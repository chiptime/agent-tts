#!/usr/bin/env python3
"""Host-side request identifiers, daemon passthrough and pending-announcement
arbiter (voice-stack VS1.6 + VS3.1).

VS1.6 scope: identifier helpers plus the thin passthrough that lets the host
(a) enqueue an audio file into the engine daemon carrying an identifier and
(b) cancel by identifier.

VS3.1 (milestone 3) adds the pending-announcement arbiter — class
:class:`PendingQueue` (TECHNICAL-PLAN T7, PRD 03): a bounded ACTIVE queue of
dispatchable records over a durable SQLite WAL ledger, with metadata-only
consolidation, pane-epoch expiry, honest bounds and the hard visible
``admission-blocked`` condition when the ledger/storage is exhausted. The
dispatch tick and the claim/completion mapping arrived with VS3.3 (below);
operator actions (T8) arrive with their own task.

The host talks to the engine through its public surface only: the daemon's
``enqueue <json>`` and ``cancel <json>`` IPC commands (TECHNICAL-PLAN T3).
Both senders are injectable so every behavior is testable without a daemon.

CLI (used by ``bin/herdr-tts``)::

    pending_queue.py new-id
    pending_queue.py enqueue-file FILE --id ID [--id ID ...] [--priority P]
                                    [--policy P] [--event-type T] [--label L]
    pending_queue.py cancel --id ID [--id ID ...]
    pending_queue.py admit --pane-id P --agent A --status done|blocked
                           --label L --text T [--pane-pid PID] [--audio-path PATH]
    pending_queue.py tick
    pending_queue.py completion --id ID --outcome played|completed|stopped|failed|unknown
    pending_queue.py list
    pending_queue.py status
    pending_queue.py retry ID [--actor A]
    pending_queue.py resolve ID {announced|expired} [--actor A]
    pending_queue.py regenerate ID [--actor A]

``tick`` (VS3.3, T7 LOCKED) is the dispatcher: when the engine is not busy
it claims the OLDEST dispatchable ``pending`` record FIFO by
``first_seen_ts`` — ``pending -> announcing`` with ``claimed_ts`` stamped in
the SAME transaction, BEFORE any enqueue attempt, so concurrent ticks can
never double-dispatch — then enqueues the record's audio through the daemon
surface carrying the record's own id as identifier. On a typed daemon
``ok`` the engine ``item`` is persisted as ``item_id``; on ANY enqueue
failure the claim resets (``announcing -> pending``, ``item_id`` cleared)
so no record dispatches twice — the failure is typed, never raised.
Exactly one record per tick, FIFO non-preempt ALWAYS: nothing is enqueued
while the engine sounds, and this path never asks the engine for
preemption even though it supports it. The busy check is read-only over
the engine's channel files (playing lock present AND its pid alive); this
module never writes another component's locks.

``completion --id --outcome`` (VS3.3) maps a reported playback outcome onto
the ledger by record id AND/OR engine item id: ``played``/``completed`` ->
``announced`` (terminal); ``stopped``/``failed``/``unknown`` ->
``uncertain`` (exactly-once audible is NEVER claimed — resolving
``uncertain`` is deliberate, VS3.5). Terminal states never reopen, and
reporting the state a record already has is an idempotent ``ok``.

Operator CLI (VS3.5, T8): ``list`` prints every ledger record as one
compact line (id, pane, status, state, repeat_count, first_seen);
``status`` prints the aggregate (per-state counts, ACTIVE bound usage,
ledger bytes, overflow condition); ``retry`` is the deliberate re-queue
``uncertain -> pending`` ONLY; ``resolve`` records the deliberate verdict
``announced``/``expired`` over legal FSM edges only. Every deliberate
action stamps ``resolved_by`` (``--actor``, default ``$USER`` or
``operator-cli``) and ``resolved_ts`` — resolution is attributable by
construction. Illegal or terminal-reopening attempts are typed refusals
that mutate nothing and exit non-zero.

Crash recovery (VS3.7, T7 "Crash"): constructing the queue (or an explicit
``recover()``) turns orphan ``announcing`` records — claimed by a tick
whose completion never landed because the host died between claim and
completion — into ``uncertain``. The recovery is idempotent and NEVER
auto-replays: ``uncertain`` re-enqueues only through the deliberate
retry/resolve actions above.

Regeneration after GC (VS3.8, T7 "Regeneración"): ``regenerate``
re-renders a ``pending``/``uncertain``/``evicted`` record whose audio is
gone or null FROM TEXT through an injectable renderer seam (production
default: a thin bridge into the host's local render chain — no provider
policy and no network code live in this module; the operator's explicit
provider policy governs real renders, and tests only ever inject fakes).
On success the single deliberate edge ``evicted -> pending`` makes a
displaced record dispatchable again; the brain watcher's own GC rule is
not touched here.

Exit status: 0 = ``ok=true``, 1 = ``ok=false`` (engine/daemon refusal,
including ``admission-blocked``), 2 = usage or invalid identifier. The
reply line is ``key=value`` tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Same opaque-id rule as the brain's speech_request_id (TECHNICAL-PLAN T1).
ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_INT_FIELDS = ("item", "queue_len", "removed", "active_stopped", "trimmed", "coalesced")

Sender = Callable[[str], Optional[str]]
Enqueuer = Callable[[dict], Optional[str]]


class IdentifierError(ValueError):
    """An identifier violates the opaque-id rule (or none was given)."""


class EngineUnavailable(RuntimeError):
    """The agent_tts engine could not be imported."""


def new_announcement_id() -> str:
    """Fresh ``ann-<uuid4>`` identifier for a host-minted announcement."""
    return f"ann-{uuid.uuid4()}"


def valid_identifier(value: object) -> bool:
    return isinstance(value, str) and ID_PATTERN.match(value) is not None


def _checked(identifiers: Sequence[str]) -> List[str]:
    """Validated identifiers, order preserved, duplicates dropped."""
    ids: List[str] = []
    for value in identifiers:
        if not valid_identifier(value):
            raise IdentifierError(f"invalid identifier: {value!r}")
        if value not in ids:
            ids.append(value)
    if not ids:
        raise IdentifierError("at least one identifier is required")
    return ids


def build_enqueue_payload(
    path: str,
    identifiers: Sequence[str],
    *,
    label: Optional[str] = None,
    priority: str = "working",
    policy: str = "queue",
    event_type: str = "",
) -> dict:
    """Daemon ``enqueue`` payload for one audio file carrying identifiers."""
    payload = {
        "file": os.path.abspath(path),
        "label": label or os.path.basename(path),
        "priority": priority,
        "policy": policy,
        "identifiers": _checked(identifiers),
    }
    if event_type:
        payload["event_type"] = event_type
    return payload


def build_cancel_command(identifiers: Sequence[str]) -> str:
    """Daemon ``cancel`` IPC line for the given identifiers."""
    body = {"identifiers": _checked(identifiers)}
    return "cancel " + json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def parse_reply(reply: Optional[str]) -> Dict[str, object]:
    """Typed view of a daemon reply (``ok=true item=3 queue_len=0`` ...)."""
    if reply is None:
        return {"ok": False, "error": "daemon unreachable"}
    text = reply.strip()
    if text.startswith("ERR:"):
        return {"ok": False, "error": text[4:].strip()}
    result: Dict[str, object] = {}
    if "error=" in text:
        head, _, error = text.partition("error=")
        result["error"] = error.strip()
        text = head
    for token in text.split():
        key, sep, value = token.partition("=")
        if not sep:
            continue
        if key == "ok":
            result["ok"] = value == "true"
        elif key in _INT_FIELDS and value.lstrip("-").isdigit():
            result[key] = int(value)
        else:
            result[key] = value
    result.setdefault("ok", False)
    return result


def format_reply(result: Dict[str, object]) -> str:
    """``key=value`` line for shell consumers (``ok`` first, error last)."""
    parts = [f"ok={'true' if result.get('ok') else 'false'}"]
    for key, value in result.items():
        if key not in ("ok", "error"):
            parts.append(f"{key}={value}")
    if "error" in result:
        parts.append(f"error={result['error']}")
    return " ".join(parts)


def _engine_enqueue(payload: dict) -> Optional[str]:
    try:
        import tts_engine  # noqa: F401  (sets the herdr socket/lock env first)
        from agent_tts.daemon import delegate_enqueue
    except (ImportError, SystemExit) as exc:
        raise EngineUnavailable(f"agent_tts unavailable: {exc}") from exc
    return delegate_enqueue(payload)


def _engine_send(command: str) -> Optional[str]:
    try:
        import tts_engine  # noqa: F401
        from agent_tts import send_ipc_command
    except (ImportError, SystemExit) as exc:
        raise EngineUnavailable(f"agent_tts unavailable: {exc}") from exc
    return send_ipc_command(command)


def default_renderer(text: str, out_path: str) -> str:
    """Production renderer seam for regenerate (VS3.8): a THIN bridge into
    the host's LOCAL render chain — the same engine seam segmented_render
    uses (``import tts_engine`` first so the herdr channel env defaults
    apply, then the engine's public ``synthesize``). NO provider choice and
    NO network code live in this module: the operator's explicit provider
    policy (engine env/config) governs the real render; this seam only
    moves the rendered bytes to ``out_path`` and returns it."""
    import asyncio

    import tts_engine  # noqa: F401  (sets the herdr socket/lock env first)
    from agent_tts.cli import synthesize

    asyncio.run(synthesize(text, output_file=out_path))
    return out_path


def enqueue_file(
    path: str,
    identifiers: Sequence[str],
    *,
    enqueue: Optional[Enqueuer] = None,
    **options: str,
) -> Dict[str, object]:
    """Enqueue an audio file carrying identifiers; returns the typed ack.

    A missing/empty file never reaches the daemon (nothing to play, nothing
    to cancel later).
    """
    payload = build_enqueue_payload(path, identifiers, **options)
    try:
        if os.path.getsize(path) <= 0:
            raise OSError("empty file")
    except OSError:
        return {"ok": False, "error": f"audio file missing or empty: {path}"}
    return parse_reply((enqueue or _engine_enqueue)(payload))


def cancel(
    identifiers: Sequence[str], *, send: Optional[Sender] = None
) -> Dict[str, object]:
    """Cancel by identifiers; an absent daemon is idempotent silence.

    The command never spawns a daemon: with none running there is nothing
    queued or playing to cancel, which is exactly ``removed=0``.
    """
    command = build_cancel_command(identifiers)
    reply = (send or _engine_send)(command)
    if reply is None:
        return {"ok": True, "removed": 0, "active_stopped": 0}
    return parse_reply(reply)


# -- dispatcher busy check (VS3.3) -------------------------------------------------
#
# Channel paths of the engine's playback channel. lib/tts_engine.py sets the
# same env names to these defaults before importing agent_tts, so reading the
# env (with the identical fallback) IS reading the constants where they live.
# The dispatcher only ever READS these files: writing another component's
# locks is forbidden (T7) — staleness is the engine's own discipline.

DEFAULT_LOCK_PATH = "/tmp/herdr-tts-playing.lock"
DEFAULT_PID_PATH = "/tmp/herdr-tts-current.pid"

PlayingCheck = Callable[[], bool]


def _pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` existence probe — no signal is ever delivered."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # a live process this user does not own
    except OSError:
        return False
    return True


def engine_is_playing(lock_path: Optional[str] = None,
                      pid_path: Optional[str] = None) -> bool:
    """Read-only busy check over the engine's channel files: busy iff the
    playing lock exists AND the pid it names is alive. A lock without a
    live, readable owner (missing pid file, garbage pid, dead pid) is NOT
    busy — proving busy needs both, and this side never cleans up another
    component's stale locks."""
    lock = Path(lock_path or os.environ.get("AGENT_TTS_LOCK_FILE") or DEFAULT_LOCK_PATH)
    pid_file = Path(pid_path or os.environ.get("AGENT_TTS_PID_FILE") or DEFAULT_PID_PATH)
    try:
        if not lock.exists():
            return False
        raw = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    try:
        pid = int(raw)
    except ValueError:
        return False
    return _pid_alive(pid)


# -- pending queue (VS3.1) --------------------------------------------------------

# Event statuses the brain emits for ambient transitions (PRD 03).
VALID_STATUSES = ("done", "blocked")

# Playback outcomes reportable through ``completion`` (VS3.3): the Bash site
# reports played|failed; the engine's PlaybackOutcome vocabulary adds
# completed|stopped, and ``unknown`` covers outcomes that could not be
# classified. Only played/completed prove audible completion; everything
# else is honestly uncertain — exactly-once audible is never claimed here.
COMPLETION_OUTCOMES = ("played", "completed", "stopped", "failed", "unknown")
ANNOUNCED_OUTCOMES = frozenset({"played", "completed"})
UNCERTAIN_OUTCOMES = frozenset({"stopped", "failed", "unknown"})

# Deliberate resolution targets for the operator CLI (VS3.5, T8): an honest
# terminal verdict for a record nobody can prove audible anymore.
RESOLVE_TARGETS = ("announced", "expired")

# States whose records are regenerable from text after audio GC (VS3.8):
# ``evicted`` is displaced-but-kept, ``pending``/``uncertain`` may carry a
# null or reaped ``audio_path``. announced/expired/cancelled records never
# re-render — their audio going away is not a loss.
REGEN_STATES = ("pending", "uncertain", "evicted")

# FSM states (PRD 03 §2C). Repetition metadata (repeat_count/last_seen_ts) is
# record DATA, never a state change.
PENDING_STATES = (
    "pending",      # admitted, waiting for a free playback turn
    "announcing",   # claimed for dispatch (VS3.3: claimed_ts/item_id)
    "announced",    # terminal: playback completion confirmed
    "uncertain",    # crash/orphan outcome — only deliberate action resolves it
    "expired",      # terminal: pane epoch obsoleted (visible, not reproducible)
    "evicted",      # terminal: displaced from the ACTIVE queue; ledger keeps it
    "cancelled",    # terminal: explicit cancel applied to THIS record
)
ACTIVE_STATES = ("pending", "announcing")
NON_TERMINAL_STATES = ("pending", "announcing", "uncertain")
TERMINAL_STATES = ("announced", "expired", "evicted", "cancelled")

# Explicit FSM edges. Terminal states have NO outgoing edges: they never
# reopen (PRD 03). pending -> announcing is the dispatch claim; announcing ->
# pending is the enqueue-failure claim reset (T7); uncertain -> {pending,
# announced, expired} are the deliberate resolving actions (PRD 03, T8). The
# ONE deliberate edge out of a terminal is evicted -> pending (VS3.8): a
# displaced record whose audio was re-rendered by regenerate() becomes
# dispatchable again — mark() still refuses it because its terminal guard
# runs before the edge check; regenerate() is the only traversal.
_TRANSITIONS = {
    "pending": frozenset({"announcing", "expired", "evicted", "cancelled"}),
    "announcing": frozenset({"pending", "announced", "uncertain", "cancelled"}),
    "uncertain": frozenset({"pending", "announced", "expired"}),
    "announced": frozenset(),
    "expired": frozenset(),
    "evicted": frozenset({"pending"}),
    "cancelled": frozenset(),
}

# Bounds are constructor parameters first (T11); the env vars tune production
# deployments without code changes and never affect tests that pass values.
DEFAULT_MAX_RECORDS = 256                    # PENDING_MAX_RECORDS
DEFAULT_MAX_DB_BYTES = 8 * 1024 * 1024       # PENDING_MAX_DB_BYTES (8 MiB)
DEFAULT_CONSOLIDATION_WINDOW_S = 60.0        # CONSOLIDATION_WINDOW_S
DEFAULT_DB_PATH = "~/.local/state/herdr-tts/pending.db"
OVERFLOW_SIDECAR_NAME = "pending-overflow.json"
DEFAULT_MAX_OVERFLOW_EVENTS = 32             # bounded emergency envelope

# The persisted text is bounded exactly like the announcement the brain would
# speak (watcher.py announce_max_chars default / clip_detail).
DEFAULT_TEXT_MAX_CHARS = 300
LABEL_MAX_CHARS = 120

OVERFLOW_DECLARATION = (
    "Emergency envelope: durable storage for the pending ledger was exhausted "
    "and the incoming event was NOT accepted. Recoverable detail CANNOT be "
    "promised when a durable write is impossible: this bounded envelope keeps "
    "only the exact reason, attribution and aggregate counts. No existing "
    "record was deleted or completed to make room, and playback is never "
    "muted by admission backpressure."
)

# Conservative write-size heuristics for the ledger byte-budget pre-check:
# SQLite grows the file in pages (4 KiB by default); the row estimate covers
# the payload, the metadata estimate a repeat_count/last_seen rewrite.
_PAGE_MARGIN_BYTES = 4096
_ROW_OVERHEAD_BYTES = 384
_METADATA_WRITE_BYTES = 512

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_records (
    id               TEXT PRIMARY KEY,
    epoch            TEXT NOT NULL,
    pane_id          TEXT NOT NULL,
    agent            TEXT NOT NULL,
    status           TEXT NOT NULL,
    label            TEXT NOT NULL,
    text             TEXT NOT NULL,
    audio_path       TEXT,
    first_seen_ts    REAL NOT NULL,
    last_seen_ts     REAL NOT NULL,
    repeat_count     INTEGER NOT NULL,
    state            TEXT NOT NULL,
    announce_seq     INTEGER NOT NULL,
    epoch_unverified INTEGER NOT NULL DEFAULT 0,
    pane_pid         INTEGER,
    claimed_ts       REAL,
    item_id          TEXT,
    resolved_by      TEXT,
    resolved_ts      REAL
)
"""
_SCHEMA_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_pending_key ON pending_records"
    " (pane_id, status, epoch, announce_seq DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pending_state ON pending_records"
    " (state, first_seen_ts)",
)


def _env_number(name: str, default, cast) -> float:
    """Env-tunable bound with an honest fallback: garbage warns on stderr and
    keeps the documented default (a typo must not crash the watcher loop)."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        print(f"pending_queue: invalid {name}={raw!r}, keeping default {default}", file=sys.stderr)
        return default


def _sanitize_text(text: str, cap: int) -> str:
    """Announce-grade bound for the persisted copy of the event text.

    Mirrors the brain watcher's ``clip_detail``: flatten all whitespace,
    prefer cutting at the last sentence end that fits inside ``cap`` and
    hard-cut with an ellipsis only when a single sentence exceeds it. The
    record never stores more than the announcement would speak.
    """
    if not text:
        return ""
    flat = " ".join(text.split())
    if not flat:
        return ""
    if len(flat) <= cap:
        return flat
    head = flat[:cap]
    cut = head.rfind(". ")
    if cut == -1 and head.endswith("."):
        cut = len(head) - 1
    if cut != -1:
        return head[: cut + 1]
    return head[: cap - 3].rstrip() + "..."


def _sanitize_label(label: str) -> str:
    """Whitespace-flattened label hard-capped to ``LABEL_MAX_CHARS``."""
    flat = " ".join(label.split())
    if len(flat) <= LABEL_MAX_CHARS:
        return flat
    return flat[: LABEL_MAX_CHARS - 3].rstrip() + "..."


def default_actor() -> str:
    """Operator attribution for deliberate actions (VS3.5): an explicit
    ``--actor`` wins (the CLI passes it), otherwise the environment's
    ``USER``, otherwise the honest literal fallback."""
    return os.environ.get("USER") or "operator-cli"


def _usable_audio(path: Optional[str]) -> bool:
    """Dispatchability rule shared by the claim scan and regenerate: an
    audio path is usable iff a non-empty file sits on disk."""
    if not path:
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _empty_envelope() -> Dict[str, object]:
    return {
        "condition": "admission-blocked",
        "declaration": OVERFLOW_DECLARATION,
        "aggregate": {"total": 0, "first_ts": None, "last_ts": None},
        "events": [],
    }


def _row_to_record(row: sqlite3.Row) -> Dict[str, object]:
    """Public dict view of one persisted record (bools restored)."""
    return {
        "id": row["id"],
        "epoch": row["epoch"],
        "pane_id": row["pane_id"],
        "agent": row["agent"],
        "status": row["status"],
        "label": row["label"],
        "text": row["text"],
        "audio_path": row["audio_path"],       # None = regenerable from text
        "first_seen_ts": row["first_seen_ts"],
        "last_seen_ts": row["last_seen_ts"],
        "repeat_count": row["repeat_count"],
        "state": row["state"],
        "announce_seq": row["announce_seq"],
        "epoch_unverified": bool(row["epoch_unverified"]),
        "pane_pid": row["pane_pid"],
        # Dispatcher bookkeeping (VS3.3): claimed_ts at dispatch, item_id
        # from the daemon's typed ok (both NULL until the first claim).
        "claimed_ts": row["claimed_ts"],
        "item_id": row["item_id"],
        # Deliberate-action attribution (VS3.5): who resolved/regenerated
        # and when (NULL until a deliberate action or crash recovery).
        "resolved_by": row["resolved_by"],
        "resolved_ts": row["resolved_ts"],
    }


def _placeholders(values: Sequence[str]) -> str:
    return ", ".join("?" for _ in values)


class PendingQueue:
    """Host pending-announcement arbiter: bounded ACTIVE queue + durable ledger.

    Two levels (TECHNICAL-PLAN T7, LOCKED): the ACTIVE queue holds dispatchable
    records (``pending``/``announcing``) and is bounded by ``max_records``;
    when full the OLDEST active record is displaced to ``evicted`` and REMAINS
    in the ledger (displacing is not losing). The LEDGER is durable evidence of
    everything ever admitted, bounded by ``max_db_bytes``; ledger or storage
    exhaustion is the hard visible condition ``admission-blocked``: the
    incoming event is NOT accepted and a bounded emergency envelope (sidecar
    ``pending-overflow.json`` + aggregate counter) records the exact reason and
    attribution, explicitly declaring that recoverable detail cannot be
    promised when a durable write is impossible. Nothing is ever silently
    lost or faked, nothing is deleted or completed to make room, and this
    module never touches playback (backpressure never mutes the PC).

    Concurrency model: a lock plus connection-per-call — the same pattern as
    the brain's ``HistoryStore``. Connections are opened and closed inside the
    lock and never cross threads, so ``check_same_thread`` keeps its default;
    WAL journal mode lives in the database file itself, so separate processes
    (the Bash watcher spawns one per invocation) share the same WAL database.
    Every write path runs inside a ``BEGIN IMMEDIATE`` transaction so the
    read-modify-write of admit/consolidation/sequencing is atomic against
    other writers.

    Epoch model: a record's epoch derives from the tmux pane pid observed with
    the event (``pid-<pid>``). A verified pid change on the same pane_id means
    a new epoch: NON-TERMINAL records of that pane's old verified epochs become
    ``expired`` (visible, not reproducible); terminal records are never
    touched. Without a pid the admit proceeds with ``epoch_unverified=true``
    (an ``epoch_hint`` string may still label the epoch) and NO invalidation
    is invented — unverified-epoch records are never expired by a later
    verified epoch, because without a pid a pane restart cannot be proven.

    ``announce_seq`` is monotone per (pane_id, status) — the speakable ordinal
    of that pane's done/blocked announcements across epochs — so a pane
    restart does not reset the numbering (T7: "announce_seq siguiente de la
    misma clave"; the consolidation key adds the epoch, the sequence key does
    not).
    """

    def __init__(
        self,
        db_path=None,
        *,
        max_records=None,
        max_db_bytes=None,
        consolidation_window_s=None,
        clock=time.time,
        max_overflow_events=DEFAULT_MAX_OVERFLOW_EVENTS,
        text_max_chars=DEFAULT_TEXT_MAX_CHARS,
    ):
        if db_path is None:
            db_path = os.path.expanduser(DEFAULT_DB_PATH)
        self._db_file = Path(os.path.expanduser(str(db_path)))
        self._max_records = self._resolved(max_records, "max_records", DEFAULT_MAX_RECORDS, "PENDING_MAX_RECORDS", int)
        self._max_db_bytes = self._resolved(max_db_bytes, "max_db_bytes", DEFAULT_MAX_DB_BYTES, "PENDING_MAX_DB_BYTES", int)
        self._consolidation_window_s = self._resolved(
            consolidation_window_s, "consolidation_window_s", DEFAULT_CONSOLIDATION_WINDOW_S,
            "PENDING_CONSOLIDATION_WINDOW_S", float,
        )
        self._clock = clock
        self._max_overflow_events = int(max_overflow_events)
        self._text_max_chars = int(text_max_chars)
        self._lock = threading.Lock()
        self._mem_entries: List[Dict[str, object]] = []
        self._sidecar_error: Optional[str] = None
        self._ensure_schema()
        # Crash recovery (VS3.7): orphan claims become visible+uncertain at
        # every construction — idempotent, never auto-replaying.
        self.recover()

    # -- configuration surface ---------------------------------------------------

    @staticmethod
    def _resolved(value, name, default, env, cast) -> float:
        """Explicit constructor value wins; otherwise the env-tunable default.
        Negative bounds are a wiring bug and raise immediately."""
        if value is None:
            value = _env_number(env, default, cast)
        value = cast(value)
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value!r}")
        return value

    @property
    def db_path(self) -> Path:
        return self._db_file

    @property
    def max_records(self) -> int:
        return self._max_records

    @property
    def max_db_bytes(self) -> int:
        return self._max_db_bytes

    @property
    def consolidation_window_s(self) -> float:
        return self._consolidation_window_s

    # -- public API ----------------------------------------------------------------

    def admit(self, event: Dict[str, object]) -> Optional[Dict[str, object]]:
        """Admit one ambient transition event into the pending queue.

        Returns the fresh or consolidated record, or ``None`` when the ledger
        byte budget (or the storage itself) refused the write — the hard
        visible ``admission-blocked`` condition: the incoming event is NOT
        accepted and an emergency envelope records reason + attribution.
        Malformed events raise :class:`ValueError` (a wiring bug must stay
        visible, never a fake accept). Busy-checking is NOT this module's
        concern (the Bash watcher owns ``is_playing``): admit always accepts
        into pending. This method never deletes or completes existing records
        to make room and never touches playback.
        """
        (pane_id, agent, status, label, text,
         pane_pid, epoch_hint, audio_path) = self._validated(event)
        now = self._clock()
        epoch, unverified = self._derive_epoch(pane_pid, epoch_hint)
        label = _sanitize_label(label)
        text = _sanitize_text(text, self._text_max_chars)
        with self._lock:
            try:
                conn = self._connect()
            except (sqlite3.Error, OSError) as exc:
                return self._refuse(f"ledger-unopenable: {exc}", pane_id, agent, status, label, now)
            try:
                conn.execute("BEGIN IMMEDIATE")
                latest = self._latest_of_key(conn, pane_id, status, epoch)
                consolidates = (
                    latest is not None
                    and (now - latest["last_seen_ts"]) <= self._consolidation_window_s
                )
                estimate = (
                    _METADATA_WRITE_BYTES if consolidates
                    else self._estimate_row_bytes(pane_id, agent, label, text)
                )
                if self._db_bytes() + estimate > self._max_db_bytes:
                    stored = self._db_bytes()
                    conn.execute("ROLLBACK")
                    reason = (f"ledger-byte-budget-exceeded: {stored} bytes stored"
                              f" + ~{estimate} needed > {self._max_db_bytes} budget")
                    return self._refuse(reason, pane_id, agent, status, label, now)
                if consolidates:
                    # Same key within the window: metadata only — NO new
                    # record, NO state change, whatever the state is.
                    conn.execute(
                        "UPDATE pending_records SET repeat_count = repeat_count + 1,"
                        " last_seen_ts = ? WHERE id = ?",
                        (now, latest["id"]),
                    )
                    record_id = latest["id"]
                else:
                    if not unverified:
                        # Verified pid change => new epoch: old-epoch
                        # non-terminal records of this pane expire (visible,
                        # not reproducible). Terminal records never move.
                        conn.execute(
                            "UPDATE pending_records SET state = 'expired'"
                            f" WHERE pane_id = ? AND epoch <> ? AND epoch_unverified = 0"
                            f" AND state IN ({_placeholders(NON_TERMINAL_STATES)})",
                            (pane_id, epoch, *NON_TERMINAL_STATES),
                        )
                    record_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO pending_records (id, epoch, pane_id, agent, status,"
                        " label, text, audio_path, first_seen_ts, last_seen_ts, repeat_count,"
                        " state, announce_seq, epoch_unverified, pane_pid, claimed_ts, item_id)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'pending', ?, ?, ?, NULL, NULL)",
                        (record_id, epoch, pane_id, agent, status, label, text, audio_path,
                         now, now, self._next_seq(conn, pane_id, status),
                         int(unverified), pane_pid),
                    )
                    self._displace_overflow(conn)
                conn.execute("COMMIT")
                return self._record(conn, record_id)
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                return self._refuse(f"ledger-write-failed: {exc}", pane_id, agent, status, label, now)
            finally:
                conn.close()

    def get(self, record_id: str) -> Optional[Dict[str, object]]:
        """One record by id, or ``None`` when unknown."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM pending_records WHERE id = ?", (record_id,)
                ).fetchone()
                return _row_to_record(row) if row is not None else None
            finally:
                conn.close()

    def list_active(self) -> List[Dict[str, object]]:
        """Dispatchable records (``pending``/``announcing``), FIFO by first_seen."""
        return self._list(f"state IN ({_placeholders(ACTIVE_STATES)})", ACTIVE_STATES)

    def list_all(self) -> List[Dict[str, object]]:
        """The whole ledger — every state, ``evicted``/``expired`` included."""
        return self._list("1 = 1", ())

    def mark(self, record_id: str, state: str) -> Dict[str, object]:
        """Explicit FSM transition with a typed result — never a fake success.

        ``{"ok": True, "record": ...}`` or ``{"ok": False, "error": ...,
        "from": ..., "to": ...}``: unknown ids/states, self-transitions,
        illegal edges and ANY transition out of a terminal state are refused
        (terminals never reopen, PRD 03). Storage errors propagate — this is
        an operator/dispatcher action, not the watcher's backpressure path.
        """
        if state not in PENDING_STATES:
            return {"ok": False, "error": f"unknown state {state!r} (not in PENDING_STATES)",
                    "record_id": record_id, "from": None, "to": state}
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT state FROM pending_records WHERE id = ?", (record_id,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "error": f"unknown record id {record_id!r}",
                            "record_id": record_id, "from": None, "to": state}
                current = row[0]
                if current in TERMINAL_STATES:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "error": f"terminal state {current!r} never reopens",
                            "record_id": record_id, "from": current, "to": state}
                if state == current:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "error": f"record already in state {current!r};"
                            " mark expects a transition",
                            "record_id": record_id, "from": current, "to": state}
                if state not in _TRANSITIONS[current]:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "error": f"FSM forbids {current!r} -> {state!r}",
                            "record_id": record_id, "from": current, "to": state}
                conn.execute("UPDATE pending_records SET state = ? WHERE id = ?",
                             (state, record_id))
                conn.execute("COMMIT")
                return {"ok": True, "record": self._record(conn, record_id)}
            finally:
                conn.close()

    def dispatch_tick(self, *, is_playing: Optional[PlayingCheck] = None,
                      enqueue: Optional[Enqueuer] = None) -> Dict[str, object]:
        """One FIFO dispatch attempt (VS3.3, T7 LOCKED claim-before-enqueue).

        When ``is_playing`` (default: the read-only engine busy check) says
        the engine is free, the OLDEST dispatchable ``pending`` record is
        claimed — ``pending -> announcing`` with ``claimed_ts`` stamped
        INSIDE the same transaction, BEFORE any enqueue attempt, so
        concurrent ticks can never double-dispatch — and then enqueued
        through the engine daemon surface carrying the record's own id as
        identifier. A typed daemon ``ok`` persists the engine ``item`` as
        ``item_id``; ANY enqueue failure (typed refusal, unreachable daemon
        or a raising seam) resets the claim (``announcing -> pending``,
        ``item_id`` cleared) and surfaces the failure typed. Exactly one
        record per tick, FIFO non-preempt ALWAYS: nothing is enqueued while
        the engine sounds and this path never asks the engine for
        preemption even though it supports it.

        The tick never raises: every failure lands typed on the result —
        ``{"ok": True, "dispatched": 0|1}`` plus ``claimed``/``item`` when a
        record moved; ``ok=False`` with ``error`` when a dispatch attempt
        failed (the claim was already reset, so nothing dispatches twice).
        Records whose ``audio_path`` is missing or empty on disk are not
        dispatchable and stay ``pending`` (their re-rendering is a later
        milestone's concern, never a fake enqueue).
        """
        try:
            return self._dispatch_tick(is_playing or engine_is_playing, enqueue)
        except Exception as exc:  # the tick must never break the watcher loop
            return {"ok": False, "dispatched": 0, "error": f"tick-failed: {exc}"}

    def report_completion(self, ref: str, outcome: str) -> Dict[str, object]:
        """Map a reported playback outcome onto the ledger (VS3.3).

        ``ref`` is a record id AND/OR the engine item id its dispatch
        persisted. ``played``/``completed`` -> ``announced`` (terminal);
        ``stopped``/``failed``/``unknown`` -> ``uncertain`` — exactly-once
        audible is NEVER claimed here; resolving ``uncertain`` is deliberate
        (VS3.5, T8). Terminal states never reopen; reporting the state the
        record already has is an idempotent ``ok``; a ``pending`` record
        (never dispatched through the claim) is refused typed — the FSM has
        no ``pending -> announced`` shortcut. A ``ref`` matching nothing is
        ``ok=true matched=none``: immediate-announce playback outside the
        pending queue legitimately reports outcomes too.
        """
        if outcome in ANNOUNCED_OUTCOMES:
            target = "announced"
        elif outcome in UNCERTAIN_OUTCOMES:
            target = "uncertain"
        else:
            return {"ok": False, "id": ref, "outcome": outcome,
                    "error": f"unknown outcome {outcome!r}"}
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM pending_records WHERE id = ?", (ref,)
                ).fetchone()
                matched = "id"
                if row is None:
                    row = conn.execute(
                        "SELECT * FROM pending_records WHERE item_id = ?"
                        " ORDER BY first_seen_ts DESC LIMIT 1",
                        (ref,),
                    ).fetchone()
                    matched = "item"
                if row is None:
                    return {"ok": True, "id": ref, "outcome": outcome,
                            "matched": "none"}
                record = _row_to_record(row)
                current = record["state"]
                if current == target:
                    return {"ok": True, "id": ref, "outcome": outcome,
                            "matched": matched, "record": record["id"],
                            "state": current}
                if current in TERMINAL_STATES:
                    return {"ok": False, "id": ref, "outcome": outcome,
                            "matched": matched, "record": record["id"],
                            "from": current, "to": target,
                            "error": f"terminal state {current!r} never reopens"}
                if target not in _TRANSITIONS[current]:
                    return {"ok": False, "id": ref, "outcome": outcome,
                            "matched": matched, "record": record["id"],
                            "from": current, "to": target,
                            "error": f"FSM forbids {current!r} -> {target!r}"}
                # Guarded write: another process may have resolved the record
                # between the read above and this transaction — a stale view
                # must never reopen or downgrade what it decided.
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE pending_records SET state = ? WHERE id = ? AND state = ?",
                    (target, record["id"], current),
                )
                if cursor.rowcount != 1:
                    conn.execute("ROLLBACK")
                    fresh = conn.execute(
                        "SELECT state FROM pending_records WHERE id = ?",
                        (record["id"],),
                    ).fetchone()
                    return {"ok": False, "id": ref, "outcome": outcome,
                            "matched": matched, "record": record["id"],
                            "error": "record changed under the completion;"
                                     f" now {fresh[0] if fresh else 'gone'}"}
                conn.execute("COMMIT")
                return {"ok": True, "id": ref, "outcome": outcome,
                        "matched": matched, "record": record["id"],
                        "state": target}
            finally:
                conn.close()

    def recover(self) -> Dict[str, object]:
        """Crash recovery (VS3.7, T7 "Crash"): every orphan ``announcing``
        record — claimed by a dispatch tick whose completion never landed
        because the host died between claim and completion — becomes
        ``uncertain``.

        Idempotent and visible: a second run recovers nothing (no
        ``announcing`` records remain) and NEVER auto-replays — ``uncertain``
        never re-enqueues by itself; resolution is ONLY the deliberate
        VS3.5 actions (retry/resolve). ``item_id``/``claimed_ts`` are kept
        as evidence so a late engine completion can still map onto the
        record (uncertain -> announced is a legal completion edge). The
        recovery itself is attributable: rows are stamped
        ``resolved_by="crash-recovery"`` with ``resolved_ts``.

        Called automatically at construction; safe to call explicitly. The
        concurrency consequence is honest: constructing a queue while
        ANOTHER process is mid-dispatch flips that in-flight claim to
        uncertain (exactly-once is never claimed here); a completion that
        lands afterwards self-heals it to ``announced``, and a lost race
        costs at most the item_id bookkeeping — never a second dispatch.
        """
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                orphans = conn.execute(
                    "SELECT COUNT(*) FROM pending_records WHERE state = 'announcing'"
                ).fetchone()[0]
                if not orphans:
                    return {"ok": True, "recovered": 0}
                conn.execute("BEGIN IMMEDIATE")
                # State-guarded inside the write transaction: only claims
                # that are STILL announcing when the lock is held move.
                cursor = conn.execute(
                    "UPDATE pending_records SET state = 'uncertain',"
                    " resolved_by = 'crash-recovery', resolved_ts = ?"
                    " WHERE state = 'announcing'",
                    (now,),
                )
                recovered = cursor.rowcount
                conn.execute("COMMIT")
                return {"ok": True, "recovered": recovered}
            finally:
                conn.close()

    def retry(self, record_id: str, *, actor: Optional[str] = None) -> Dict[str, object]:
        """Deliberate re-queue of an ``uncertain`` record (VS3.5, T8).

        The ONLY edge is ``uncertain -> pending`` (the deliberate-resolve
        edge, PRD 03): the record becomes dispatchable again for the FIFO
        dispatcher. Anything else is a typed refusal that mutates nothing —
        terminals never reopen, a non-uncertain record is not retryable,
        and an unknown id is honest about it. ``item_id`` is cleared (a
        stale engine item must not map a second dispatch); ``claimed_ts``
        keeps the attempt as evidence. The action stamps
        ``resolved_by``/``resolved_ts`` — resolution is attributable by
        construction. Storage errors propagate: this is an operator
        action, not the watcher's backpressure path.
        """
        return self._deliberate(record_id, "pending", actor,
                                only_from=("uncertain",), clear_item=True)

    def resolve(self, record_id: str, target: str, *,
                actor: Optional[str] = None) -> Dict[str, object]:
        """Deliberate resolution (VS3.5, T8): record the honest verdict.

        ``target`` is ``announced`` or ``expired``, reachable ONLY over
        legal FSM edges — ``uncertain/announcing -> announced`` (it did
        play / the operator vouches it played) and
        ``pending/uncertain -> expired`` (obsolete, never to be played).
        Terminal-reopening attempts, illegal edges and unknown ids are
        typed refusals that mutate nothing. The action stamps
        ``resolved_by``/``resolved_ts``.
        """
        if target not in RESOLVE_TARGETS:
            return {"ok": False, "id": record_id, "from": None, "to": target,
                    "error": f"unknown resolve target {target!r}"
                             f" (not in {RESOLVE_TARGETS})"}
        return self._deliberate(record_id, target, actor)

    def _deliberate(self, record_id: str, target: str,
                    actor: Optional[str], *, only_from: Optional[Tuple[str, ...]] = None,
                    clear_item: bool = False) -> Dict[str, object]:
        """Shared engine of the deliberate operator transitions. The read
        and the guarded UPDATE share one ``BEGIN IMMEDIATE`` transaction
        (like mark), so the observed state cannot change under the write."""
        now = self._clock()
        who = actor or default_actor()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT state FROM pending_records WHERE id = ?", (record_id,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "id": record_id, "from": None, "to": target,
                            "error": f"unknown record id {record_id!r}"}
                current = row[0]
                if current in TERMINAL_STATES:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "id": record_id, "from": current, "to": target,
                            "error": f"terminal state {current!r} never reopens"}
                if only_from is not None and current not in only_from:
                    conn.execute("ROLLBACK")
                    wanted = " or ".join(repr(s) for s in only_from)
                    return {"ok": False, "id": record_id, "from": current, "to": target,
                            "error": f"retry only resolves {wanted} records;"
                                     f" this one is {current!r}"}
                if target not in _TRANSITIONS[current]:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "id": record_id, "from": current, "to": target,
                            "error": f"FSM forbids {current!r} -> {target!r}"}
                conn.execute(
                    "UPDATE pending_records SET state = ?, resolved_by = ?, resolved_ts = ?"
                    + (", item_id = NULL" if clear_item else "")
                    + " WHERE id = ?",
                    (target, who, now, record_id),
                )
                conn.execute("COMMIT")
                return {"ok": True, "id": record_id, "from": current, "to": target,
                        "resolved_by": who, "resolved_ts": now,
                        "record": self._record(conn, record_id)}
            finally:
                conn.close()

    def regenerate(self, record_id: str, *, renderer: Optional[Callable[[str, str], str]] = None,
                   actor: Optional[str] = None) -> Dict[str, object]:
        """Re-render a record's audio FROM TEXT after GC (VS3.8, T7
        "Regeneración").

        Eligible: ``pending``/``uncertain``/``evicted`` records whose stored
        ``audio_path`` is gone or null (evicted = displaced-but-kept; its
        audio is the GC's to reap; a null audio was never rendered). The
        audio is re-rendered through the INJECTABLE ``renderer(text,
        out_path)`` seam — production default is a thin bridge into the
        host's local render chain (:func:`default_renderer`); no provider
        policy and no network code live in THIS module, and tests only ever
        inject fakes. The regenerated file lands at a deterministic path
        next to the ledger (same record -> same path, so repeated
        regenerations overwrite instead of accumulating).

        On success ``audio_path`` points at the regenerated file and the
        action stamps ``resolved_by``/``resolved_ts``. Regenerate does NOT
        resolve the record: the state stays what it was — EXCEPT the single
        deliberate edge ``evicted -> pending``, which makes a displaced
        record dispatchable again the moment its audio exists (the only
        FSM edge out of a terminal, traversable only here, after a
        successful re-render). ACTIVE-bound discipline stays with admit:
        if regeneration pushes active usage over ``max_records``, the next
        admit displaces the oldest again. The brain watcher's own GC rule
        is NOT touched by this module.
        """
        now = self._clock()
        who = actor or default_actor()
        make_audio = renderer or default_renderer
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM pending_records WHERE id = ?", (record_id,)
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return {"ok": False, "id": record_id,
                    "error": f"unknown record id {record_id!r}"}
        record = _row_to_record(row)
        current = record["state"]
        if current not in REGEN_STATES:
            return {"ok": False, "id": record_id, "state": current,
                    "error": f"state {current!r} is not regenerable"
                             " (audio-GC recovery only)"}
        if _usable_audio(record["audio_path"]):
            return {"ok": False, "id": record_id, "state": current,
                    "error": f"audio already present: {record['audio_path']}"}
        # The render runs OUTSIDE the lock (it is slow) against a
        # deterministic target; the ledger write below re-proves the state.
        target_path = str(self._regen_audio_path(record_id))
        try:
            written = make_audio(record["text"], target_path)
        except Exception as exc:  # a raising seam is a typed failure too
            return {"ok": False, "id": record_id,
                    "error": f"renderer failed: {exc}"}
        if not written:  # a falsy return is a typed failure, never a fake path
            return {"ok": False, "id": record_id,
                    "error": "renderer returned no audio path"}
        if not _usable_audio(str(written)):
            return {"ok": False, "id": record_id,
                    "error": f"renderer wrote no usable audio: {written}"}
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                updated = conn.execute(
                    "UPDATE pending_records SET audio_path = ?, resolved_by = ?,"
                    " resolved_ts = ? WHERE id = ? AND state = ?",
                    (str(written), who, now, record_id, current),
                )
                if updated.rowcount != 1:
                    # Another writer moved the record between the render
                    # and this transaction: never overwrite its decision.
                    conn.execute("ROLLBACK")
                    fresh = conn.execute(
                        "SELECT state FROM pending_records WHERE id = ?",
                        (record_id,),
                    ).fetchone()
                    return {"ok": False, "id": record_id,
                            "error": "record changed under the regeneration;"
                                     f" now {fresh[0] if fresh else 'gone'}"}
                new_state = current
                if current == "evicted":
                    # The single deliberate edge out of a terminal (VS3.8):
                    # displaced audio reborn — dispatchable again.
                    conn.execute(
                        "UPDATE pending_records SET state = 'pending' WHERE id = ?",
                        (record_id,),
                    )
                    new_state = "pending"
                conn.execute("COMMIT")
                return {"ok": True, "id": record_id, "from": current,
                        "state": new_state, "audio_path": str(written),
                        "resolved_by": who, "resolved_ts": now}
            finally:
                conn.close()

    def _regen_audio_path(self, record_id: str) -> Path:
        """Deterministic regenerated-audio location NEXT TO THE LEDGER: the
        state dir is this module's own domain (the brain watcher's GC owns
        the audio dir), and same record -> same path means repeated
        regenerations overwrite in place instead of accumulating files."""
        regen_dir = self._db_file.parent / "pending-audio"
        regen_dir.mkdir(parents=True, exist_ok=True)
        return regen_dir / f"{record_id}.mp3"

    def status_summary(self) -> Dict[str, object]:
        """Aggregate operator view (feeds the ``status`` CLI, VS3.5):
        per-state counts, ACTIVE-bound usage, ledger bytes and the
        overflow condition — one honest picture, nothing hidden."""
        counts: Dict[str, object] = {state: 0 for state in PENDING_STATES}
        for record in self.list_all():
            if record["state"] in counts:
                counts[record["state"]] += 1
        active = counts["pending"] + counts["announcing"]
        overflow = self.overflow_status()
        summary: Dict[str, object] = {"ok": True}
        summary.update(counts)
        summary["active"] = f"{active}/{self._max_records}"
        summary["ledger_bytes"] = self._db_bytes()
        summary["overflow"] = overflow["condition"]
        summary["overflow_total"] = overflow["total"]
        return summary

    def overflow_status(self) -> Dict[str, object]:
        """Aggregate view of the emergency envelope (feeds the PWA/operator).

        ``total`` counts every refused admission; ``events`` is the bounded
        tail with reason + attribution. ``sidecar_error`` is set when the
        durable envelope could not be written or read — the counts then come
        from this process's memory and are still reported, never hidden.
        """
        with self._lock:
            data = self._flush_overflow()
            aggregate = data["aggregate"]
            total = aggregate["total"]
            return {
                "condition": "admission-blocked" if total else "ok",
                "total": total,
                "first_overflow_ts": aggregate["first_ts"],
                "last_overflow_ts": aggregate["last_ts"],
                "events": data["events"][-self._max_overflow_events:],
                "sidecar": str(self._sidecar_path()),
                "sidecar_error": self._sidecar_error,
            }

    # -- internals (callers hold the lock where writes happen) ---------------------

    def _ensure_schema(self) -> None:
        parent = self._db_file.parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            for statement in (_SCHEMA, *_SCHEMA_INDEXES):
                conn.execute(statement)
            # Ledger upgrade (VS3.5): pre-VS3.5 databases gain the operator
            # attribution columns in place — CREATE IF NOT EXISTS cannot
            # evolve an existing table, and the ledger is never rebuilt.
            present = {row[1] for row in conn.execute("PRAGMA table_info(pending_records)")}
            for column, decl in (("resolved_by", "TEXT"), ("resolved_ts", "REAL")):
                if column not in present:
                    conn.execute(f"ALTER TABLE pending_records ADD COLUMN {column} {decl}")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_file), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _sidecar_path(self) -> Path:
        return self._db_file.parent / OVERFLOW_SIDECAR_NAME

    def _db_bytes(self) -> int:
        """Storage actually consumed by the ledger: main db + WAL. The ``-shm``
        side file is transient shared memory, not durable storage."""
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += os.path.getsize(str(self._db_file) + suffix)
            except OSError:
                pass
        return total

    @staticmethod
    def _validated(event) -> Tuple[str, str, str, str, str, Optional[int], Optional[str], Optional[str]]:
        if not isinstance(event, dict):
            raise ValueError(f"event must be a dict, got {type(event).__name__}")
        fields: Dict[str, str] = {}
        for name in ("pane_id", "agent", "label", "text"):
            value = event.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"event field {name!r} must be a non-empty string, got {value!r}")
            fields[name] = value
        status = event.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"event status must be one of {VALID_STATUSES}, got {status!r}")
        pane_pid = event.get("pane_pid")
        if pane_pid is not None:
            pane_pid = int(pane_pid)
        epoch_hint = event.get("epoch_hint")
        if epoch_hint is not None and not isinstance(epoch_hint, str):
            raise ValueError(f"event field 'epoch_hint' must be a string, got {epoch_hint!r}")
        audio_path = event.get("audio_path")
        if audio_path is not None and not isinstance(audio_path, str):
            raise ValueError(f"event field 'audio_path' must be a string, got {audio_path!r}")
        return (fields["pane_id"], fields["agent"], status, fields["label"],
                fields["text"], pane_pid, epoch_hint, audio_path)

    @staticmethod
    def _derive_epoch(pane_pid, epoch_hint) -> Tuple[str, bool]:
        """Observed pane pid wins; a hint only labels an unverified epoch."""
        if pane_pid is not None:
            return f"pid-{pane_pid}", False
        if epoch_hint:
            return epoch_hint, True
        return "unverified", True

    def _estimate_row_bytes(self, pane_id: str, agent: str, label: str, text: str) -> int:
        payload = (len(text.encode("utf-8")) + len(label.encode("utf-8"))
                   + len(pane_id.encode("utf-8")) + len(agent.encode("utf-8")))
        return _PAGE_MARGIN_BYTES + _ROW_OVERHEAD_BYTES + payload

    @staticmethod
    def _latest_of_key(conn: sqlite3.Connection, pane_id: str, status: str, epoch: str):
        return conn.execute(
            "SELECT * FROM pending_records WHERE pane_id = ? AND status = ? AND epoch = ?"
            " ORDER BY announce_seq DESC LIMIT 1",
            (pane_id, status, epoch),
        ).fetchone()

    @staticmethod
    def _next_seq(conn: sqlite3.Connection, pane_id: str, status: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(announce_seq), 0) FROM pending_records"
            " WHERE pane_id = ? AND status = ?",
            (pane_id, status),
        ).fetchone()
        return int(row[0]) + 1

    def _displace_overflow(self, conn: sqlite3.Connection) -> None:
        """Shrink the ACTIVE queue to ``max_records`` by displacing its OLDEST
        records to ``evicted``. Displacing is not losing: the ledger row (text
        + attribution) stays intact and regenerable."""
        active = conn.execute(
            f"SELECT id FROM pending_records WHERE state IN ({_placeholders(ACTIVE_STATES)})"
            " ORDER BY first_seen_ts ASC, rowid ASC",
            ACTIVE_STATES,
        ).fetchall()
        for row in active[: max(0, len(active) - self._max_records)]:
            conn.execute("UPDATE pending_records SET state = 'evicted' WHERE id = ?", (row[0],))

    # -- dispatcher internals (VS3.3) ----------------------------------------------

    def _dispatch_tick(self, is_playing: PlayingCheck,
                       enqueue: Optional[Enqueuer]) -> Dict[str, object]:
        now = self._clock()
        try:
            busy = bool(is_playing())
        except Exception as exc:
            return {"ok": False, "dispatched": 0,
                    "error": f"is-playing check failed: {exc}"}
        if busy:
            # FIFO non-preempt ALWAYS: never enqueue over sounding audio.
            return {"ok": True, "dispatched": 0, "busy": "true"}
        record = self._claim_next_pending(now)
        if record is None:
            return {"ok": True, "dispatched": 0}
        try:
            result = enqueue_file(record["audio_path"], [record["id"]],
                                  enqueue=enqueue, label=record["label"])
        except Exception as exc:  # a raising seam is an enqueue failure too
            result = {"ok": False, "error": f"enqueue raised: {exc}"}
        if result.get("ok"):
            # The dispatch is real regardless of bookkeeping: report it and
            # only annotate a persist failure — retrying would double-play.
            persist_error = self._record_dispatched(record["id"], result.get("item"), now)
            ack: Dict[str, object] = {"ok": True, "dispatched": 1,
                                      "claimed": record["id"]}
            if result.get("item") is not None:
                ack["item"] = result["item"]
            if persist_error is not None:
                ack["item_id_persist_error"] = persist_error
            return ack
        # Enqueue failed in any way: reset the claim BEFORE reporting, so the
        # record is retryable and nothing dispatches twice.
        self._reset_claim(record["id"], now)
        error = result.get("error")
        return {"ok": False, "dispatched": 0, "claimed": record["id"],
                "reset": "pending",
                "error": str(error) if error is not None else "enqueue refused"}

    def _claim_next_pending(self, now: float) -> Optional[Dict[str, object]]:
        """Claim (``pending -> announcing``, ``claimed_ts`` stamped) the OLDEST
        dispatchable pending record, FIFO by ``first_seen_ts``. Dispatchable
        means a non-empty ``audio_path`` file on disk: a record without usable
        audio is skipped (stays pending), never fake-enqueued. The candidate
        scan and the state-guarded UPDATE share one ``BEGIN IMMEDIATE``
        transaction, so concurrent ticks — cross-process included — can never
        claim the same record (SQLite serializes the writers; the ``state =
        'pending'`` predicate re-proves the claim inside the lock)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT * FROM pending_records WHERE state = 'pending'"
                    " AND audio_path IS NOT NULL"
                    " ORDER BY first_seen_ts ASC, rowid ASC"
                ).fetchall()
                for row in rows:
                    if not _usable_audio(row["audio_path"]):
                        continue
                    conn.execute(
                        "UPDATE pending_records SET state = 'announcing',"
                        " claimed_ts = ? WHERE id = ? AND state = 'pending'",
                        (now, row["id"]),
                    )
                    conn.execute("COMMIT")
                    claimed = dict(_row_to_record(row))
                    claimed["state"] = "announcing"
                    claimed["claimed_ts"] = now
                    return claimed
                conn.execute("ROLLBACK")  # read-only walk: nothing claimed
                return None
            finally:
                conn.close()

    def _record_dispatched(self, record_id: str, item_id, now: float) -> Optional[str]:
        """Persist the engine item id on the claimed record. Returns ``None``
        on success or the typed reason the bookkeeping failed — the dispatch
        already happened, so the caller reports success and never retries."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE pending_records SET item_id = ?"
                    " WHERE id = ? AND state = 'announcing'",
                    (None if item_id is None else str(item_id), record_id),
                )
                conn.execute("COMMIT")
                return None
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                return f"item-id persist failed: {exc}"
            finally:
                conn.close()

    def _reset_claim(self, record_id: str, now: float) -> None:
        """Enqueue-failure claim reset (``announcing -> pending``): the FSM
        edge exists precisely for this (T7). ``item_id`` is cleared;
        ``claimed_ts`` keeps the attempt as evidence. A reset that itself
        fails propagates (dispatch_tick types it) and leaves the record
        visibly ``announcing`` for deliberate resolution (VS3.5)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE pending_records SET state = 'pending', item_id = NULL,"
                    " claimed_ts = ? WHERE id = ? AND state = 'announcing'",
                    (now, record_id),
                )
                conn.execute("COMMIT")
            finally:
                conn.close()

    def _record(self, conn: sqlite3.Connection, record_id: str) -> Optional[Dict[str, object]]:
        row = conn.execute("SELECT * FROM pending_records WHERE id = ?", (record_id,)).fetchone()
        return _row_to_record(row) if row is not None else None

    def _list(self, where: str, params: Sequence[str]) -> List[Dict[str, object]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT * FROM pending_records WHERE {where}"
                    " ORDER BY first_seen_ts ASC, rowid ASC",
                    params,
                ).fetchall()
                return [_row_to_record(row) for row in rows]
            finally:
                conn.close()

    def _refuse(self, reason: str, pane_id: str, agent: str, status: str,
                label: str, now: float) -> None:
        """Record one admission-blocked refusal; always returns ``None``.

        The envelope write is best-effort and never raises — a broken sidecar
        must not mask the refusal itself (the entry also lives in memory for
        :meth:`overflow_status`).
        """
        self._mem_entries.append({
            "ts": now, "reason": reason, "pane_id": pane_id,
            "agent": agent, "status": status, "label": label,
        })
        self._flush_overflow()
        return None

    def _load_envelope(self) -> Dict[str, object]:
        path = self._sidecar_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _empty_envelope()
        except (OSError, ValueError) as exc:
            self._sidecar_error = f"sidecar unreadable, aggregate restarted: {exc}"
            return _empty_envelope()
        if (isinstance(raw, dict) and isinstance(raw.get("aggregate"), dict)
                and isinstance(raw["aggregate"].get("total"), int)
                and isinstance(raw.get("events"), list)):
            raw.setdefault("condition", "admission-blocked")
            raw.setdefault("declaration", OVERFLOW_DECLARATION)
            return raw
        self._sidecar_error = "sidecar shape invalid, aggregate restarted"
        return _empty_envelope()

    def _write_envelope(self, data: Dict[str, object]) -> None:
        path = self._sidecar_path()
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def _flush_overflow(self) -> Dict[str, object]:
        """Merge unflushed refusals into the durable envelope (best effort).

        Returns the reportable envelope: file contents merged with any entries
        that could not be made durable — those stay in memory and are retried
        on the next refusal/status call. An unreadable/invalid sidecar starts
        a fresh aggregate (flagged via ``sidecar_error``): a corrupt file is
        not trusted as evidence.
        """
        data = self._load_envelope()
        if not self._mem_entries:
            return data
        aggregate = data["aggregate"]
        aggregate["total"] += len(self._mem_entries)
        if aggregate["first_ts"] is None:
            aggregate["first_ts"] = self._mem_entries[0]["ts"]
        aggregate["last_ts"] = self._mem_entries[-1]["ts"]
        data["events"] = (data["events"] + self._mem_entries)[-self._max_overflow_events:]
        try:
            self._write_envelope(data)
            self._mem_entries = []
            self._sidecar_error = None
        except OSError as exc:
            self._sidecar_error = f"sidecar not writable: {exc}"
        return data


def _emit(result: Dict[str, object]) -> int:
    line = format_reply(result)
    if result.get("ok"):
        print(line)
        return 0
    print(line, file=sys.stderr)
    return 1


def _admit_cli(args, queue_factory) -> int:
    """``admit`` arm: one event in, one typed line out (thin VS3.2 wiring)."""
    event: Dict[str, object] = {
        "pane_id": args.pane_id,
        "agent": args.agent,
        "status": args.status,
        "label": args.label,
        "text": args.text,
    }
    if args.pane_pid is not None:
        event["pane_pid"] = args.pane_pid
    if args.audio_path is not None:
        event["audio_path"] = args.audio_path
    try:
        queue = (queue_factory or PendingQueue)()
        record = queue.admit(event)
    except ValueError as exc:  # wiring bug: visible, never a fake accept
        print(f"ok=false error={exc}", file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError) as exc:  # ledger/storage unusable
        return _emit({"ok": False, "reason": "admission-blocked",
                      "error": f"ledger-unopenable: {exc}"})
    if record is None:
        return _emit({"ok": False, "reason": "admission-blocked"})
    return _emit({"ok": True, "id": record["id"], "state": record["state"],
                  "repeat_count": record["repeat_count"]})


def _tick_cli(*, queue_factory=None, is_playing=None, enqueue=None) -> int:
    """``tick`` arm: one FIFO dispatch attempt through the queue — typed
    on stdout/stderr, never raising into the caller (VS3.3)."""
    try:
        queue = (queue_factory or PendingQueue)()
    except (sqlite3.Error, OSError) as exc:  # ledger/storage unusable
        return _emit({"ok": False, "dispatched": 0,
                      "error": f"ledger-unopenable: {exc}"})
    return _emit(queue.dispatch_tick(is_playing=is_playing, enqueue=enqueue))


def _valid_ref(ref: str) -> bool:
    """A completion ref is a record identifier OR an engine item id (a
    digits-only string — the daemon's queue ids are small ints)."""
    return valid_identifier(ref) or ref.isdigit()


def _completion_cli(args, *, queue_factory=None) -> int:
    """``completion`` arm: map the reported outcome onto the ledger (VS3.3)."""
    if not _valid_ref(args.id):
        print(f"ok=false error=invalid identifier: {args.id!r}", file=sys.stderr)
        return 2
    try:
        queue = (queue_factory or PendingQueue)()
    except (sqlite3.Error, OSError) as exc:  # ledger/storage unusable
        return _emit({"ok": False, "error": f"ledger-unopenable: {exc}"})
    return _emit(queue.report_completion(args.id, args.outcome))


def _open_queue(queue_factory) -> Optional["PendingQueue"]:
    """Queue for an operator arm, or the typed ledger-unopenable refusal."""
    try:
        return (queue_factory or PendingQueue)()
    except (sqlite3.Error, OSError) as exc:  # ledger/storage unusable
        _emit({"ok": False, "error": f"ledger-unopenable: {exc}"})
        return None


def _list_cli(args, *, queue_factory=None) -> int:
    """``list`` arm (VS3.5): every ledger record, one compact line each —
    id, pane, status, state, repeat_count, first_seen, FIFO by first_seen."""
    queue = _open_queue(queue_factory)
    if queue is None:
        return 1
    records = queue.list_all()
    print(f"ok=true count={len(records)}")
    for record in records:
        print(f"id={record['id']} pane={record['pane_id']}"
              f" status={record['status']} state={record['state']}"
              f" repeat_count={record['repeat_count']}"
              f" first_seen={record['first_seen_ts']}")
    return 0


def _status_cli(args, *, queue_factory=None) -> int:
    """``status`` arm (VS3.5): the aggregate — per-state counts, ACTIVE
    bound usage, ledger bytes, overflow condition."""
    queue = _open_queue(queue_factory)
    if queue is None:
        return 1
    return _emit(queue.status_summary())


def _retry_cli(args, *, queue_factory=None) -> int:
    """``retry`` arm (VS3.5): the deliberate re-queue uncertain -> pending."""
    if not _valid_ref(args.id):
        print(f"ok=false error=invalid identifier: {args.id!r}", file=sys.stderr)
        return 2
    queue = _open_queue(queue_factory)
    if queue is None:
        return 1
    return _emit(queue.retry(args.id, actor=args.actor))


def _resolve_cli(args, *, queue_factory=None) -> int:
    """``resolve`` arm (VS3.5): the deliberate verdict announced|expired."""
    if not _valid_ref(args.id):
        print(f"ok=false error=invalid identifier: {args.id!r}", file=sys.stderr)
        return 2
    queue = _open_queue(queue_factory)
    if queue is None:
        return 1
    return _emit(queue.resolve(args.id, args.target, actor=args.actor))


def _regenerate_cli(args, *, queue_factory=None) -> int:
    """``regenerate`` arm (VS3.8): re-render missing audio from text through
    the host's local render chain (the operator's provider policy governs
    the real render; nothing here chooses providers)."""
    if not _valid_ref(args.id):
        print(f"ok=false error=invalid identifier: {args.id!r}", file=sys.stderr)
        return 2
    queue = _open_queue(queue_factory)
    if queue is None:
        return 1
    return _emit(queue.regenerate(args.id, actor=args.actor))


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    enqueue: Optional[Enqueuer] = None,
    send: Optional[Sender] = None,
    queue_factory: Optional[Callable[[], "PendingQueue"]] = None,
    is_playing: Optional[PlayingCheck] = None,
) -> int:
    parser = argparse.ArgumentParser(prog="pending_queue.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("new-id", help="print a fresh ann-<uuid4> identifier")
    p_enq = sub.add_parser("enqueue-file", help="enqueue an audio file with identifiers")
    p_enq.add_argument("file")
    p_enq.add_argument("--id", dest="ids", action="append", default=[])
    p_enq.add_argument("--priority", default="working")
    p_enq.add_argument("--policy", default="queue")
    p_enq.add_argument("--event-type", default="")
    p_enq.add_argument("--label", default=None)
    p_can = sub.add_parser("cancel", help="cancel queued/active audio by identifier")
    p_can.add_argument("--id", dest="ids", action="append", default=[])
    p_adm = sub.add_parser("admit", help="admit one watcher transition event into"
                                         " the pending queue (busy-PC announcements)")
    p_adm.add_argument("--pane-id", required=True)
    p_adm.add_argument("--agent", required=True)
    p_adm.add_argument("--status", required=True, choices=VALID_STATUSES)
    p_adm.add_argument("--label", required=True)
    p_adm.add_argument("--text", required=True)
    p_adm.add_argument("--pane-pid", type=int, default=None)
    p_adm.add_argument("--audio-path", default=None,
                       help="path of the already-rendered announcement audio, if any")
    sub.add_parser("tick", help="dispatch tick — claim the oldest pending"
                                 " record FIFO (claim-before-enqueue) and"
                                 " enqueue it when the engine is not busy;"
                                 " FIFO non-preempt always")
    p_com = sub.add_parser("completion", help="report a playback outcome by"
                                               " record id or engine item id:"
                                               " played/completed -> announced,"
                                               " stopped/failed/unknown ->"
                                               " uncertain")
    p_com.add_argument("--id", required=True)
    p_com.add_argument("--outcome", required=True, choices=COMPLETION_OUTCOMES)
    sub.add_parser("list", help="list every ledger record, one compact line"
                                " each (id, pane, status, state,"
                                " repeat_count, first_seen)")
    sub.add_parser("status", help="aggregate: per-state counts, ACTIVE bound"
                                  " usage, ledger bytes, overflow condition")
    p_rty = sub.add_parser("retry", help="deliberate re-queue:"
                                         " uncertain -> pending ONLY")
    p_rty.add_argument("id")
    p_rty.add_argument("--actor", default=None,
                       help="attribution recorded as resolved_by"
                            " (default: $USER or operator-cli)")
    p_res = sub.add_parser("resolve", help="deliberate resolution to a"
                                           " terminal verdict, over legal"
                                           " FSM edges only")
    p_res.add_argument("id")
    p_res.add_argument("target", choices=RESOLVE_TARGETS)
    p_res.add_argument("--actor", default=None,
                       help="attribution recorded as resolved_by"
                            " (default: $USER or operator-cli)")
    p_rgn = sub.add_parser("regenerate", help="re-render a record's audio"
                                              " from text after GC"
                                              " (evicted -> pending again)")
    p_rgn.add_argument("id")
    p_rgn.add_argument("--actor", default=None,
                       help="attribution recorded as resolved_by"
                            " (default: $USER or operator-cli)")
    args = parser.parse_args(argv)

    if args.command == "new-id":
        print(new_announcement_id())
        return 0
    if args.command == "tick":
        return _tick_cli(queue_factory=queue_factory, is_playing=is_playing,
                         enqueue=enqueue)
    if args.command == "list":
        return _list_cli(args, queue_factory=queue_factory)
    if args.command == "status":
        return _status_cli(args, queue_factory=queue_factory)
    if args.command == "retry":
        return _retry_cli(args, queue_factory=queue_factory)
    if args.command == "resolve":
        return _resolve_cli(args, queue_factory=queue_factory)
    if args.command == "regenerate":
        return _regenerate_cli(args, queue_factory=queue_factory)
    try:
        if args.command == "enqueue-file":
            result = enqueue_file(
                args.file,
                args.ids,
                enqueue=enqueue,
                label=args.label,
                priority=args.priority,
                policy=args.policy,
                event_type=args.event_type,
            )
        elif args.command == "admit":
            return _admit_cli(args, queue_factory)
        elif args.command == "completion":
            return _completion_cli(args, queue_factory=queue_factory)
        else:
            result = cancel(args.ids, send=send)
    except IdentifierError as exc:
        print(f"ok=false error={exc}", file=sys.stderr)
        return 2
    except EngineUnavailable as exc:
        print(f"ok=false error={exc}", file=sys.stderr)
        return 1
    return _emit(result)


if __name__ == "__main__":
    sys.exit(main())
