"""Append-only JSONL audit trail of write-level actions (Panel parity
F1, doc 02). Port of ``panel:bridge/audit.ts``.

Every write the brain accepts can type into a real terminal or resolve a
frozen gate, so who-did-what-when is recorded as one JSONL line per
action at ``<state_dir>/audit.log`` (created 0600 — it may echo reply
text). The line format is a pure, tested function with a frozen field
order (ts, action, pane_id?, session_id?, device?, detail) so lines are
grep/diff-friendly; optional attribution is omitted (not null) when
absent. The writer serializes appends behind a lock, caps the file at
5 MB with exactly one rotated generation, and an audit failure NEVER
fails the user action it records.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

logger = logging.getLogger("herdr_brain.audit")

# Cap on any single string value written into a line — a 2 000-char
# reply becomes a 120-char preview (exact Panel parity, L4).
MAX_STR = 120

# How much of a value's CONTENT the trail keeps. "preview" (default): a
# bounded, single-line preview. "none": every string inside ``detail``
# becomes ⟨redacted⟩ unless its key names an action parameter
# (METADATA_KEYS). "none" exists for deployments that want the trail
# but not the message bodies: booleans, numbers and the whole envelope
# (ts, action, pane_id, session_id, device) are untouched either way.
DEFAULT_AUDIT_CONTENT = "preview"
AUDIT_CONTENTS = ("preview", "none")

AuditContent = str  # one of AUDIT_CONTENTS; kept plain for Settings.
AuditValue = Union[None, bool, int, float, str, list, dict]

# The metadata allowlist for "none" mode, matched by key name at ANY
# depth inside ``detail``. A string survives only if its key names an
# action PARAMETER — an id, an enum-ish outcome, a revision, a reply
# word from a closed set, an utterance length. Anything
# screen- or operator-originated (``text``, ``label``) must not be
# listed. The direction of the default is the whole feature: a detail
# field added later under a new name redacts until someone
# deliberately adds it here. Members of an array inherit the array's
# own key ("keys" carries "enter", not a body).
METADATA_KEYS = frozenset({
    "decision",
    "outcome",
    "revision",
    "keys",
    "reason",
    "count",
    "gate_id",
    "tool",
    "reply",
    "request_id",
    "chars",
    "spoken",
})

# A constant, never "⟨n chars⟩". An exact length is itself content — a
# 9-character redaction next to a password prompt narrows the secret —
# and "was anything sent" is already answered by the entry existing.
REDACTED = "⟨redacted⟩"

_NEWLINES = re.compile(r"[\r\n]+")

# Sentinel distinguishing "drop this value/key" from a legitimate None.
_DROP = object()


def _sanitize(value: object, content: str, allowed: bool) -> object:
    """Recursive sanitize worker.

    Collapses newlines and truncates long strings so every value is a
    single-line, bounded preview; recurses into arrays/objects.
    ``allowed`` carries the enclosing key's verdict against
    METADATA_KEYS: an object re-decides per key, an array hands its own
    verdict to every member. It is False at the root, so a ``detail``
    that is somehow a bare string redacts rather than passing through
    unnamed.

    Values that are not JSON-representable (callables, arbitrary
    objects) are DROPPED — in both modes. Copying one through means the
    later json.dumps could call a smuggled ``to_json``/``default``
    hook, re-injecting its return value past the preview cap and past
    every redaction decision made here. In arrays a dropped member
    renders as None (what JSON does with a hole); in objects the key is
    omitted entirely.
    """
    if callable(value):
        return _DROP
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        # NaN/Infinity render as invalid JSON; keep the line parseable.
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        if content == "none" and not allowed:
            return REDACTED
        one_line = _NEWLINES.sub(" ", value)
        if len(one_line) > MAX_STR:
            return one_line[:MAX_STR] + "…"
        return one_line
    if isinstance(value, list):
        members = []
        for member in value:
            cleaned = _sanitize(member, content, allowed)
            members.append(None if cleaned is _DROP else cleaned)
        return members
    if isinstance(value, dict):
        out: dict = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue  # defensive: non-string keys never enter the line
            cleaned = _sanitize(item, content, key in METADATA_KEYS)
            if cleaned is not _DROP:
                out[key] = cleaned
        return out
    return _DROP


def sanitize(value: object, content: str = DEFAULT_AUDIT_CONTENT) -> AuditValue:
    """Truncated, newline-safe, (optionally) redacted JSON value."""
    cleaned = _sanitize(value, content, allowed=False)
    return None if cleaned is _DROP else cleaned


def format_audit_line(entry: dict, now: float, content: str = DEFAULT_AUDIT_CONTENT) -> str:
    """Render one entry to a single JSONL line (no trailing newline).

    Pure — ``now`` (epoch seconds) is injected so tests are
    deterministic. Stable field order (ts, action, pane_id?, session_id?,
    device?, detail); optional attribution is omitted (not null) when
    absent, exactly as Panel omits absent attributes. ``ts`` is ISO
    8601 UTC.
    """
    line: dict = {
        "ts": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "action": entry["action"],
    }
    if entry.get("pane_id") is not None:
        line["pane_id"] = entry["pane_id"]
    if entry.get("session_id") is not None:
        line["session_id"] = entry["session_id"]
    if entry.get("device") is not None:
        line["device"] = entry["device"]
    line["detail"] = sanitize(entry.get("detail") or {}, content)
    return json.dumps(line, ensure_ascii=False, separators=(",", ":"))


# Cap on the live ``audit.log`` before it rotates. Not an env var and
# not a config key: a knob whose only wrong setting is "unbounded" is
# not a choice worth offering (Panel's rule, kept).
AUDIT_MAX_BYTES = 5 * 1024 * 1024


class AuditLog:
    """The write side of the audit trail.

    ``record()`` is fire-and-forget: it formats the line and appends it,
    swallowing any failure (format or disk) so a full disk or a bad
    entry can never break the user action it was auditing. Appends are
    serialized behind a lock because the size is tracked in memory
    (seeded by one stat, incremented per line) — interleaved writes
    would make that counter fiction; the lock is the Python equivalent
    of Panel's chained promises.
    """

    def __init__(
        self,
        path: Union[str, Path],
        content: str = DEFAULT_AUDIT_CONTENT,
        clock: Optional[Callable[[], float]] = None,
        max_bytes: int = AUDIT_MAX_BYTES,
    ):
        self._path = Path(path)
        self._content = content
        self._clock = clock or time.time
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        # None ⇒ unknown (start of process, or a failed rotation left
        # the size in doubt): re-stat on the next write.
        self._bytes: Optional[int] = None

    def record(self, entry: dict) -> None:
        """Append one line for ``entry``; never raises."""
        try:
            line = format_audit_line(entry, self._clock(), self._content)
        except Exception as exc:  # noqa: BLE001 — the action must not fail
            logger.warning(
                "audit could not format %s: %s", entry.get("action"), exc
            )
            return
        try:
            self._append(line + "\n")
        except Exception as exc:  # noqa: BLE001 — the action must not fail
            logger.warning("audit write failed: %s", exc)

    def _append(self, line: str) -> None:
        with self._lock:
            if self._bytes is None:
                try:
                    self._bytes = os.stat(self._path).st_size
                except OSError:
                    self._bytes = 0  # missing or unreadable — write, don't lose
            if self._bytes >= self._max_bytes:
                try:
                    os.replace(self._path, str(self._path) + ".1")
                    self._bytes = 0
                except OSError as exc:
                    self._bytes = None  # size in doubt: re-stat next line
                    logger.warning(
                        "audit could not rotate %s: %s", self._path, exc
                    )
                    # ⛔ A failed rotation still appends. An oversized
                    # trail beats a missing line.
            payload = line.encode("utf-8")
            # 0700 dir / 0600 file: the trail may echo reply text. Both
            # modes carry no group/other bits, so the umask can only
            # make them more restrictive.
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(
                self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
            if self._bytes is not None:
                self._bytes += len(payload)
