"""pi / Oh My Pi connector: session JSONL under one or more roots.

On-disk shape (learned from real sessions, kept here so the parser is
readable against its source):

    <root>/--<munged-cwd>--/<ISO-ts>_<session-uuid>.jsonl
    {"type":"session","version":3,"id":"<uuid>","cwd":"…"}   ← header
    {"type":"message","id":"…","timestamp":"…","message":{
       "role":"user"|"assistant"|"toolResult",
       "content":[{"type":"text"|"thinking"|"toolCall",…}]}}
    {"type":"model_change"|"thinking_level_change",…}        ← bookkeeping

Only ``text`` blocks of ``user``/``assistant`` messages render; pi's
``thinking`` blocks carry real text but are not the conversation, and a
``toolResult`` is its own row, not a turn.

One harness, two homes: Oh My Pi reports itself as ``omp`` and writes
pi's format under its own ``~/.omp/agent/sessions``. The root is
therefore a LIST (``PI_ROOTS``, colon-separated) — the first root
holding the session wins, and session ids are globally unique, so that
is a lookup, not a guess.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import List, Optional

from .base import Turn, iso_to_utc, parse_json_line, reverse_tail_lines, SCAN_CAP_BYTES

DEFAULT_PI_ROOTS = (
    "~/.pi/agent/sessions",
    "~/.gentle-shell/agent/sessions",
    "~/.omp/agent/sessions",
)
ENV_PI_ROOTS = "PI_ROOTS"

PI_SESSION_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _text_blocks(content) -> Optional[str]:
    """Joins the ``text`` blocks of a pi content list."""
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    pieces = [
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    text = "\n".join(piece for piece in pieces if piece)
    return text or None


def _parse_roots(raw: str) -> List[str]:
    """Splits a ``PI_ROOTS`` value into non-empty, stripped roots."""
    return [part.strip() for part in raw.split(":") if part.strip()]


class PiTranscript:
    """Read-only connector over pi (and Oh My Pi) session transcripts."""

    name = "pi"

    def __init__(
        self, roots: Optional[List[str]] = None, scan_cap: int = SCAN_CAP_BYTES
    ):
        if roots is None:
            env = os.environ.get(ENV_PI_ROOTS, "").strip()
            raw_roots = _parse_roots(env) if env else list(DEFAULT_PI_ROOTS)
        else:
            raw_roots = list(roots)
        self.roots = [str(Path(root).expanduser()) for root in raw_roots]
        self.scan_cap = scan_cap

    def _session_file(self, session_id: str) -> Optional[str]:
        if not session_id:
            return None
        expanded = os.path.expanduser(session_id)
        if os.path.isfile(expanded) and expanded.endswith(".jsonl"):
            return expanded
        match = PI_SESSION_RE.search(session_id)
        if not match:
            return None
        uuid_str = match.group(0)
        for root in self.roots:
            matches = sorted(glob.glob(os.path.join(root, "*", f"*_{uuid_str}.jsonl")))
            if matches:
                return matches[0]
        return None

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id:
            return []
        path = self._session_file(session_id)
        if path is None:
            return []

        turns: List[Turn] = []
        skipped = 0
        for raw_line in reverse_tail_lines(path, self.scan_cap):
            if len(turns) >= n_turns:
                break
            event = parse_json_line(raw_line)
            if event is None or event.get("type") != "message":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _text_blocks(message.get("content"))
            if not text:
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(
                Turn(role=role, text=text, created_at=iso_to_utc(event.get("timestamp")))
            )
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None
