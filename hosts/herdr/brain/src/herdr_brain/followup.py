"""SQLite-backed persistent followup context, bound to the last
successful report (``docs/prds/herdr-brain-on-demand-context.md``;
FR-07, FR-23, FR-24, FR-25; decisions D03 last sentence and D07).

ARCHITECTURAL POSITION: this module is DETERMINISTIC PERSISTENCE AND
EXPIRY ONLY. Topic-change DETECTION is semantic work that belongs to
the model/tool layer (T9): the store receives an opaque
``topic_fingerprint`` string and compares it by exact equality — it
never interprets text. ``fingerprint_from_text`` is a deterministic
normalization convenience for T9 (lowercase/strip/collapse/sha256-16);
equality of those hashes means exact normalized-text match, nothing
more.

SELECTION ISOLATION BY CONSTRUCTION (FR-24): followup context is
independent of the Herdr selected pane. The record and the table carry
NO pane/session/selection field at all — there is nothing in this
module that could mutate or even reference the selection. Anchoring
writes this store's own database and nothing else.

GUIDANCE, NEVER INSTRUCTIONS (FR-25): source-derived content reachable
through a followup (the anchored report's brief and references) is
untrusted guidance for the user, never instructions to execute; this
store persists opaque ids and fingerprints only, so it can never
become an execution path.

Lifecycle (D07, FR-23):

- ``anchor`` records or refreshes ONE row per ``context_id``: a
  re-anchor atomically REPLACES the previous anchor (upsert — the
  newer anchor always wins, so a cancelled or superseded anchor never
  survives), resetting created_at/last_used_at to now. The natural
  anchor source is ``EngineResult.report_id`` from a successful
  publish or reuse; this store does not verify that binding against
  the report store (the engine/T9 owns it).
- ``touch`` bumps ``last_used_at`` so followups retain context across
  turns (D07). It NEVER extends life: retention is measured from
  ``created_at`` (mirroring the report store's publish-anchored
  horizon), so an anchor can never outlive its report's retention.
- ``expire`` DELETEs the row (chosen over a tombstone: simplest —
  nothing downstream needs to distinguish "expired" from "never
  anchored"). After expiry ``get`` is None and re-anchoring starts
  fresh.
- Expiry is DERIVED AT READ TIME from the injected clock plus the
  store's ``retention`` (default 24h; never stored). Restart policy:
  NO open-time reconciliation pass — reopening the same path restores
  unexpired rows, and rows that aged out while the process was down
  are simply filtered on every read. ``purge_expired`` is the bounded
  physical cleanup (FR-34 pattern; same shape as the report store).

Concurrency mirrors ``ReportStore``/``HistoryStore``: one lock guards
every access, and each call opens, uses and closes its own sqlite
connection, so a connection is never shared across threads. "Now" is
injected (``clock``) and must return timezone-aware UTC datetimes; a
naive clock result raises ``FollowupStoreError``.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

FOLLOWUP_DB_FILENAME = "followup.db"
SCHEMA_VERSION = 1

_SELECT_COLUMNS = (
    "context_id, anchor_report_id, topic_fingerprint, created_at, last_used_at"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS followups (
    context_id TEXT PRIMARY KEY,
    anchor_report_id TEXT NOT NULL,
    topic_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
)
"""

_INDEX_CREATED = (
    "CREATE INDEX IF NOT EXISTS idx_followups_created ON followups (created_at)"
)


class FollowupStoreError(Exception):
    """Base class for followup store failures."""


class MalformedFollowupInput(FollowupStoreError, ValueError):
    """Malformed followup input: a context_id/report_id/fingerprint
    that is not a non-empty string (validated on write, never
    persisted)."""


@dataclass(frozen=True)
class FollowupContext:
    """One persistent followup context (PRD Data Schema).

    Deliberately carries NO pane/session/selection field: followup
    context is independent of the Herdr selected pane BY CONSTRUCTION
    (FR-24). ``anchor_report_id`` binds to the ``EngineResult.report_id``
    of the last successful report; ``topic_fingerprint`` is an opaque
    string whose SEMANTICS (how the model layer computes it) are out
    of scope here — the store only compares fingerprints by exact
    equality.
    """

    context_id: str
    anchor_report_id: str
    topic_fingerprint: str
    created_at: datetime
    last_used_at: datetime


def default_followup_path(settings) -> Path:
    """Followup database location: beside the audio dir (same state
    root the report/call-history databases use)."""
    return Path(settings.audio_dir).parent / FOLLOWUP_DB_FILENAME


def fingerprint_from_text(text: str) -> str:
    """Deterministic normalization-only topic fingerprint (for T9).

    Lowercases (Unicode casefold), strips, and collapses every
    whitespace run to a single space, then returns the first 16 hex
    characters of the SHA-256 of the normalized text. DETERMINISTIC
    ONLY: equality of two fingerprints means an exact normalized-text
    match — semantic topic-change detection (paraphrases, drift,
    "back to normal") remains the model layer's job (T9) and MUST NOT
    be inferred from this hash.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
    normalized = " ".join(text.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _iso(moment: datetime) -> str:
    """Canonical storage form: UTC ISO 8601 with FIXED six-digit
    microseconds and a literal ``+00:00`` suffix.

    Fixed width keeps lexical SQL order identical to chronological
    order even across whole-second boundaries (``isoformat`` would
    omit the fractional part exactly on boundaries), so the read-time
    cutoff comparisons stay exact.
    """
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _require_str(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MalformedFollowupInput(
            f"{label} must be a non-empty string, got {value!r}"
        )


def _row_to_context(row: tuple) -> FollowupContext:
    """Rebuilds a FollowupContext from a stored row.

    A corrupt row (unparseable timestamp, wrong type) fails loud with
    ``FollowupStoreError`` — never silently degrades into a wrong
    context.
    """
    context_id, report_id, fingerprint, created_at, last_used_at = row
    try:
        return FollowupContext(
            context_id=context_id,
            anchor_report_id=report_id,
            topic_fingerprint=fingerprint,
            created_at=datetime.fromisoformat(created_at),
            last_used_at=datetime.fromisoformat(last_used_at),
        )
    except (TypeError, ValueError) as exc:
        raise FollowupStoreError(
            f"stored followup row for context {context_id!r} is"
            f" malformed: {exc}"
        ) from exc


class FollowupStore:
    """Thread-safe sqlite store for followup contexts (D07/FR-23).

    One row per ``context_id``; upsert semantics. See the module
    docstring for the anchor/touch/expire lifecycle, the
    derived-at-read expiry, and the restart policy.
    """

    def __init__(
        self,
        path,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        retention: timedelta = timedelta(hours=24),
    ):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._clock: Callable[[], datetime] = clock or (
            lambda: datetime.now(timezone.utc)
        )
        if not isinstance(retention, timedelta) or retention <= timedelta(0):
            raise ValueError("retention must be a positive timedelta")
        self._retention = retention
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

    # -- write path ------------------------------------------------------

    def anchor(
        self, context_id: str, report_id: str, topic_fingerprint: str
    ) -> FollowupContext:
        """Records or refreshes the followup anchor for ``context_id``.

        ONE atomic upsert statement: a re-anchor REPLACES the previous
        anchor (the newer one always wins — a cancelled or superseded
        anchor never survives), resetting created_at/last_used_at to
        now so retention restarts with the newer report. Anchoring
        never touches anything but this store's own database (FR-24 by
        construction: there is no pane field to write).
        """
        _require_str(context_id, "context_id")
        _require_str(report_id, "report_id")
        _require_str(topic_fingerprint, "topic_fingerprint")
        now = self._now()
        now_iso = _iso(now)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO followups (context_id, anchor_report_id,"
                    " topic_fingerprint, created_at, last_used_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(context_id) DO UPDATE SET"
                    " anchor_report_id = excluded.anchor_report_id,"
                    " topic_fingerprint = excluded.topic_fingerprint,"
                    " created_at = excluded.created_at,"
                    " last_used_at = excluded.last_used_at",
                    (context_id, report_id, topic_fingerprint, now_iso, now_iso),
                )
                row = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM followups"
                    " WHERE context_id = ?",
                    (context_id,),
                ).fetchone()
            finally:
                conn.close()
        return _row_to_context(row)

    def touch(self, context_id: str) -> None:
        """Bumps ``last_used_at`` to now (D07: followups retain context
        across turns).

        Never extends life: retention stays anchored at ``created_at``,
        so an anchor never outlives its report's horizon. No-op when no
        ACTIVE context exists (absent or past retention) — there is
        nothing to retain.
        """
        _require_str(context_id, "context_id")
        now = self._now()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE followups SET last_used_at = ?"
                    " WHERE context_id = ? AND created_at > ?",
                    (_iso(now), context_id, _iso(now - self._retention)),
                )
            finally:
                conn.close()

    def expire(self, context_id: str) -> None:
        """Explicit return-to-normal / topic-change expiry (FR-23).

        DELETEs the row — chosen over a tombstone because it is the
        simplest shape and nothing downstream needs to distinguish
        "expired" from "never anchored". After expiry ``get`` is None
        and re-anchoring works fresh. No-op on unknown contexts.
        """
        _require_str(context_id, "context_id")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM followups WHERE context_id = ?", (context_id,)
                )
            finally:
                conn.close()

    # -- read path -------------------------------------------------------

    def get(self, context_id: str) -> Optional[FollowupContext]:
        """The active followup context, or None.

        None when the context never anchored, was explicitly expired,
        or is past retention. Expiry is derived at read time: a row is
        active iff ``created_at > clock() - retention`` — exactly at
        the horizon counts as expired, mirroring the report store.
        """
        _require_str(context_id, "context_id")
        row = self._active_row(context_id)
        return _row_to_context(row) if row is not None else None

    def matches_topic(self, context_id: str, fingerprint: str) -> Optional[bool]:
        """Exact-equality topic check against the ACTIVE context.

        True/False when an active context exists; None when it does
        not (the caller treats None as "no followup"). Fingerprint
        SEMANTICS — how the model layer (T9) decides the topic
        changed — are out of scope: this store compares opaque strings
        and never normalizes them.
        """
        _require_str(context_id, "context_id")
        _require_str(fingerprint, "fingerprint")
        row = self._active_row(context_id)
        if row is None:
            return None
        return _row_to_context(row).topic_fingerprint == fingerprint

    def purge_expired(self, *, limit: int = 100) -> int:
        """Bounded cleanup (FR-34 pattern): deletes at most ``limit``
        rows past their retention horizon, oldest first, and returns
        the deleted count.

        Unexpired rows are never touched; ``limit <= 0`` deletes
        nothing. Callers may invoke this opportunistically (after
        anchor, or at boot).
        """
        if limit <= 0:
            return 0
        cutoff = _iso(self._now() - self._retention)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM followups WHERE context_id IN ("
                    " SELECT context_id FROM followups"
                    " WHERE created_at <= ?"
                    " ORDER BY created_at, context_id LIMIT ?)",
                    (cutoff, limit),
                )
                deleted = cursor.rowcount
            finally:
                conn.close()
        return int(deleted or 0)

    # -- internals ---------------------------------------------------------

    def _active_row(self, context_id: str) -> Optional[tuple]:
        """The unexpired row for ``context_id`` or None (derived
        expiry: active iff created_at is inside the retention window)."""
        now = self._now()
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM followups"
                    " WHERE context_id = ? AND created_at > ?",
                    (context_id, _iso(now - self._retention)),
                ).fetchone()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        # Autocommit mode: every write above is ONE statement, which is
        # atomic on its own (reportstore's BEGIN IMMEDIATE pattern
        # covers its multi-statement publish; nothing here needs it).
        return sqlite3.connect(self._path, isolation_level=None)

    def _now(self) -> datetime:
        moment = self._clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise FollowupStoreError(
                "clock must return timezone-aware datetimes,"
                f" got {moment!r}"
            )
        return moment.astimezone(timezone.utc)

    def _ensure_schema(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(_SCHEMA)
            conn.execute(_INDEX_CREATED)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise FollowupStoreError(
                    f"followup database schema v{version} is newer than"
                    f" the supported v{SCHEMA_VERSION}: {self._path}"
                )
            if version < SCHEMA_VERSION:
                # Migration-on-open: v1 is the initial shape (CREATE IF
                # NOT EXISTS above); future versions migrate forward.
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # Restart: no reconciliation pass by design. Expiry is
            # derived on every read (created_at vs the current clock),
            # so rows that aged out while the process was down are
            # filtered by get/matches_topic and removed by
            # purge_expired (bounded).
        finally:
            conn.close()
