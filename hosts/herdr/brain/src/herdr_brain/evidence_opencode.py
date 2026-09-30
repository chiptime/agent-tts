"""OpenCode evidence provider: historical conversations from OpenCode's
local SQLite store (FR-03, FR-05, FR-11, FR-17, FR-20, FR-22, FR-26,
FR-28, FR-31, FR-35, FR-40; PRD decisions D02, D03, D06, D08, D09).

Inert library (D09): nothing here is wired into the server, the LLM
loop, the tool surface, or configuration. Stdlib only; both clocks are
injected — a monotonic float clock inside ``Deadline`` and an aware-UTC
wall clock for ``observed_at``.

Store location: the db path resolves exactly like
``transcripts.OpencodeTranscript`` (explicit path, then ``$OPENCODE_DB``,
then the ``~/.local/share/opencode/opencode.db`` default). The public
constant ``DEFAULT_OPENCODE_DB`` is imported from ``transcripts`` so the
default stays single-source; the two-line resolution itself is
replicated here because reusing it would mean instantiating a reader
this provider does not need. The connection is always opened read-only
via a ``file:`` URI with ``mode=ro`` — read-only by construction
(FR-38), and every inventory is a full historical scan, not a listing
of open sessions.

Honesty rules (same contract as the evidence core):

- A MISSING db file, an empty root, or a zero-byte store is
  ``SOURCE_ABSENT`` with zero sources: "no OpenCode history" is a valid
  empty result, never a failure (FR-11, FR-22).
- ``sqlite3.Error`` / ``OSError`` (unreadable, corrupt, locked) is
  ``COVERAGE_FAILED`` with the error detail — never OK, never raised
  through the acquisition boundary (FR-20).
- ``deadline`` is checked BEFORE the db is opened and BETWEEN session
  rows while scanning; expiry yields ``COVERAGE_FAILED`` whose detail
  mentions the budget, and NEVER a partial source list (FR-17, D06).

Timestamps (FR-10): message times come ONLY from the store's
``message.time_created`` epoch-ms column, converted to aware UTC
datetimes. Unconvertible values (wrong type, out of range) become
``None``. A file mtime is never consulted and never substitutes for a
message time.

Period filtering: with a ``period``, a message is included only when
``periods.classify_timestamp`` says ``IN`` over the half-open bounds
(exact start IN, exact end OUT). A message whose timestamp cannot be
proven in-range (``None``/UNKNOWN) is EXCLUDED — trust is not inferred
— and the exclusion is made visible through
``CollectStats.unknown_timestamps``.

Truncation design (FR-21 "no silent truncation", bounded reads):
``collect`` without a ``period`` returns the conversation's NEWEST
``max_turns`` messages (default 200, chronological order preserved).
Overflow is never silent: every OK ``collect`` result is an
``OpencodeCoverageResult`` — a ``CoverageResult`` extension carrying
``CollectStats`` (``items_read``, ``items_returned``,
``unknown_timestamps``, ``truncated``). That single design was chosen
over raising (which would violate the results-not-exceptions provider
contract) and over a separate wrapper type (which would break
``CoverageResult`` substitutability); failures stay plain
``CoverageResult``. Period-bounded reads are NOT capped: the interval
already bounds them and the ``deadline`` bounds the work.

Revision race (PRD Concurrency; D08, FR-31): a session's revision token
digests (session id, ``session.time_updated``, last message id by
``time_created``/``id``, message count) — it changes whenever messages
are added or the session is updated and is stable otherwise;
``observed_at`` is deliberately NOT part of it. ``collect`` computes
the token before and after the read; on a change it re-reads ONCE
within the remaining deadline, and reports ``COVERAGE_FAILED`` if the
token changed again or the deadline expired — unverifiable data is
never served, and there is no retry loop.

Untrusted content (D07/D09): ``EvidenceItem.text`` is raw DATA quoting
what the agents wrote. It must never be interpreted as instructions;
this module performs no content filtering — isolation is the
consolidation layer's duty, with provenance preserved (FR-40).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

from .evidence import (
    CoverageResult,
    CoverageStatus,
    Deadline,
    EvidenceItem,
    InventoryResult,
    Source,
    SourceKind,
    _cwd_matches,
    _utc_now,
    manifest_entries,
)
from .periods import Period, Verdict, classify_timestamp
from .reportstore import ManifestEntry
from .transcripts import DEFAULT_OPENCODE_DB

#: Cap for period-less collect reads; overflow is flagged, never silent.
DEFAULT_MAX_TURNS = 200

_SESSIONS_SQL = (
    "SELECT id, directory, title, time_updated FROM session ORDER BY id ASC"
)
_SESSION_SQL = "SELECT id, directory, title, time_updated FROM session WHERE id = ?"
_MESSAGE_COUNT_SQL = "SELECT COUNT(*) FROM message WHERE session_id = ?"
_LAST_MESSAGE_SQL = (
    "SELECT id FROM message WHERE session_id = ?"
    " ORDER BY time_created DESC, id DESC LIMIT 1"
)
# Oldest-first full-history read; each message's text parts stay in
# stored order (the mirror of transcripts.OpencodeTranscript's
# newest-first tail scan, reversed because this reader wants history).
_MESSAGES_SQL = """
    SELECT m.id, m.time_created, json_extract(m.data, '$.role'), p.data
    FROM message AS m
    JOIN part AS p ON p.message_id = m.id
    WHERE m.session_id = ?
      AND json_extract(p.data, '$.type') = 'text'
    ORDER BY m.time_created ASC, m.id ASC, p.time_created ASC, p.id ASC
"""


@dataclass(frozen=True)
class CollectStats:
    """Visibility into one ``collect`` read (no silent truncation, FR-21).

    ``items_read`` counts the user/assistant messages with text found in
    the store; ``items_returned`` counts those actually included (period
    exclusions or the ``max_turns`` cap explain any difference);
    ``unknown_timestamps`` counts messages whose stored time could not
    be converted — excluded from period reads, counted either way;
    ``truncated`` is True only when the cap dropped messages.
    """

    items_read: int
    items_returned: int
    unknown_timestamps: int
    truncated: bool


@dataclass(frozen=True)
class OpencodeCoverageResult(CoverageResult):
    """``CoverageResult`` extension carrying ``CollectStats`` on success.

    Every OK ``collect`` from this provider is an ``OpencodeCoverageResult``
    with ``stats`` set; failures stay plain ``CoverageResult`` so the
    base contract (and its validation) is unchanged.
    """

    stats: Optional[CollectStats] = None


class _RawMessage(NamedTuple):
    """One stored message after role/text filtering, before scoping."""

    message_id: Optional[str]
    timestamp: Optional[datetime]
    role: str
    text: str


def _epoch_ms_to_utc(value: object) -> Optional[datetime]:
    """Converts the store's epoch-ms ``time_created`` to an aware UTC
    datetime; anything unconvertible (None, non-int, out of range)
    becomes ``None`` — an unknown time, never a substituted one."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _part_text(data: object) -> Optional[str]:
    """Extracts text from one OpenCode ``part`` JSON payload.

    Replicates ``transcripts._opencode_part_text``'s rule (same store,
    same parsing) instead of importing it: that helper is module-private
    and ``transcripts`` must stay untouched.
    """
    try:
        text = json.loads(data).get("text")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _revision_digest(session_id: str, state: tuple) -> str:
    """Deterministic digest of the content-defining session fields.

    Same convention as the evidence core: inputs JSON-encoded
    unambiguously, sha256, 16 hex chars. ``observed_at`` is not an input
    — a later observation of identical content is not a revision change.
    """
    time_updated, last_message_id, message_count = state
    payload = json.dumps([session_id, str(time_updated), last_message_id, message_count])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class OpencodeEvidenceProvider:
    """Evidence provider over ALL STORED OpenCode conversations.

    ``inventory`` enumerates every session in the store (historical, not
    only open ones) as a ``Source``: ``source_id`` is
    ``opencode:<session_id>``, ``project`` is the session's stored
    ``directory`` kept AS-IS (normalization to a project name is a
    later, explicit concern), ``locator`` is the session id, ``title``
    comes from the session row (blank reported as ``None``), and
    ``state`` is ``None`` — a stored conversation carries no
    open/closed status snapshot to report truthfully.

    ``project_filter`` uses the SAME rule as the Herdr session provider:
    a session matches when its directory EQUALS the filter or lives
    UNDER it as a subdirectory path component (``/repo`` matches
    ``/repo`` and ``/repo/sub`` but never ``/repo2``). The rule is
    reused by importing the core's ``_cwd_matches`` so it stays
    single-source.
    """

    kind: SourceKind = "opencode"

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        clock=None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
            raise ValueError("max_turns must be an integer >= 1")
        resolved = db_path or os.environ.get("OPENCODE_DB") or DEFAULT_OPENCODE_DB
        self.db_path = str(Path(resolved).expanduser())
        self.max_turns = max_turns
        self._clock = clock if clock is not None else _utc_now

    # ------------------------------------------------------------------
    # internals (also the seams the race tests exercise)

    def _store_readable_path(self) -> Optional[str]:
        """The path when a non-empty store file exists, else None.

        A missing file, an empty root, or a zero-byte db contains no
        conversations — SOURCE_ABSENT, the honest empty (FR-11/FR-22).
        """
        try:
            if os.path.isfile(self.db_path) and os.path.getsize(self.db_path) > 0:
                return self.db_path
        except OSError:
            pass  # vanished/unreadable between the two calls: no store
        return None

    def _connect(self) -> sqlite3.Connection:
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _session_state(self, conn: sqlite3.Connection, session_id: str) -> Optional[tuple]:
        """(time_updated, last_message_id, message_count), or None when
        the session row does not exist (or vanished mid-read)."""
        row = conn.execute(_SESSION_SQL, (session_id,)).fetchone()
        if row is None:
            return None
        count = conn.execute(_MESSAGE_COUNT_SQL, (session_id,)).fetchone()[0]
        last = conn.execute(_LAST_MESSAGE_SQL, (session_id,)).fetchone()
        return (row[3], last[0] if last else None, count)

    def _read_messages(self, conn: sqlite3.Connection, session_id: str) -> list:
        """Reads the conversation oldest-first; only user/assistant
        messages with extractable text survive (tool parts and other
        roles are dropped, mirroring the transcript reader). Multi-part
        messages keep all their text parts joined with newlines."""
        rows = conn.execute(_MESSAGES_SQL, (session_id,)).fetchall()
        order: list[str] = []
        roles: dict = {}
        stamps: dict = {}
        parts: dict = {}
        for message_id, time_created, role, part_data in rows:
            if role not in ("user", "assistant"):
                continue
            text = _part_text(part_data)
            if not text:
                continue
            if message_id not in parts:
                parts[message_id] = []
                roles[message_id] = role
                stamps[message_id] = _epoch_ms_to_utc(time_created)
                order.append(message_id)
            parts[message_id].append(text)
        return [
            _RawMessage(
                message_id=mid,
                timestamp=stamps[mid],
                role=roles[mid],
                text="\n".join(parts[mid]),
            )
            for mid in order
        ]

    def _build_coverage(
        self, source: Source, messages: list, period: Optional[Period]
    ) -> OpencodeCoverageResult:
        unknown = sum(1 for message in messages if message.timestamp is None)
        if period is None:
            truncated = len(messages) > self.max_turns
            kept = messages[-self.max_turns :] if truncated else messages
        else:
            # IN over the half-open bounds; UNKNOWN (unprovable) is
            # excluded — classify_timestamp itself decides, never this
            # module, and never with a substituted time.
            kept = [
                message
                for message in messages
                if classify_timestamp(message.timestamp, period) is Verdict.IN
            ]
            truncated = False  # interval-bounded reads are not capped
        items = tuple(
            EvidenceItem(
                source_id=source.source_id,
                kind=self.kind,
                timestamp=message.timestamp,
                role=message.role,
                text=message.text,  # untrusted DATA (FR-40)
                message_id=message.message_id,
            )
            for message in kept
        )
        stats = CollectStats(
            items_read=len(messages),
            items_returned=len(items),
            unknown_timestamps=unknown,
            truncated=truncated,
        )
        return OpencodeCoverageResult(
            source=source, status=CoverageStatus.OK, items=items, stats=stats
        )

    # ------------------------------------------------------------------
    # EvidenceProvider protocol

    def inventory(
        self,
        project_filter: Optional[str] = None,
        deadline: Optional[Deadline] = None,
    ) -> InventoryResult:
        """Lists ALL stored sessions as Sources (see class docstring)."""
        if deadline is not None and deadline.expired():
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before scanning opencode sessions",
            )
        if self._store_readable_path() is None:
            return InventoryResult(CoverageStatus.SOURCE_ABSENT)
        observed_at = self._clock()
        sources = []
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(_SESSIONS_SQL)
                for session_id, directory, title, _time_updated in cursor:
                    if deadline is not None and deadline.expired():
                        return InventoryResult(
                            CoverageStatus.COVERAGE_FAILED,
                            error_detail="budget exceeded while scanning opencode"
                            " sessions; no partial source list",
                        )
                    if not _cwd_matches(directory or "", project_filter):
                        continue
                    state = self._session_state(conn, session_id)
                    if state is None:
                        # The row vanished between the scan cursor and the
                        # token query (concurrent delete): it is not part of
                        # the observed snapshot — skip, never raise.
                        continue
                    sources.append(
                        Source(
                            source_id=f"opencode:{session_id}",
                            kind=self.kind,
                            project=directory or "",
                            locator=session_id,
                            revision_token=_revision_digest(session_id, state),
                            observed_at=observed_at,
                            title=title if isinstance(title, str) and title.strip() else None,
                            state=None,
                        )
                    )
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"opencode db unreadable: {exc}",
            )
        return InventoryResult(CoverageStatus.OK, tuple(sources))

    def collect(
        self,
        source: Source,
        period: Optional[Period] = None,
        deadline: Optional[Deadline] = None,
    ) -> CoverageResult:
        """Reads one conversation, bounded by ``period`` or ``max_turns``.

        The session id is ``source.locator``. Mid-read revision changes
        trigger exactly one re-read within the remaining deadline; a
        second change or an expired deadline is COVERAGE_FAILED —
        unverifiable data is never served.
        """
        if deadline is not None and deadline.expired():
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before reading opencode session"
                f" {source.source_id}",
            )
        if self._store_readable_path() is None:
            return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
        session_id = source.locator
        try:
            conn = self._connect()
            try:
                state = self._session_state(conn, session_id)
                if state is None:
                    return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
                for attempt in (1, 2):
                    token_before = _revision_digest(session_id, state)
                    messages = self._read_messages(conn, session_id)
                    state = self._session_state(conn, session_id)
                    if state is None:
                        # The session vanished mid-read: provably absent now.
                        return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
                    if token_before == _revision_digest(session_id, state):
                        return self._build_coverage(source, messages, period)
                    if attempt == 2:
                        return CoverageResult(
                            source,
                            CoverageStatus.COVERAGE_FAILED,
                            error_detail="opencode session"
                            f" {source.source_id} revision changed again during"
                            " re-read; refusing to serve unverifiable data",
                        )
                    if deadline is not None and deadline.expired():
                        return CoverageResult(
                            source,
                            CoverageStatus.COVERAGE_FAILED,
                            error_detail="budget exceeded before re-reading"
                            f" opencode session {source.source_id}",
                        )
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"opencode db unreadable: {exc}",
            )
        return CoverageResult(  # pragma: no cover - loop always returns
            source,
            CoverageStatus.COVERAGE_FAILED,
            error_detail=f"opencode session {source.source_id} read did not conclude",
        )


def manifest_from_inventory(result: InventoryResult) -> list[ManifestEntry]:
    """Builds report-manifest entries from an inventory result by reusing
    the core ``evidence.manifest_entries`` mapping (one rule for every
    provider kind; deterministic: same sources in, same entries out)."""
    return manifest_entries(result.sources)
