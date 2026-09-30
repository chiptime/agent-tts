"""Claude Code and Antigravity evidence providers: historical
conversations from local JSONL transcript stores (FR-03, FR-05, FR-11,
FR-17, FR-20, FR-22, FR-26, FR-28, FR-31, FR-35, FR-40; PRD decisions
D02, D03, D06, D08, D09).

Inert library (D09): nothing here is wired into the server, the LLM
loop, the tool surface, or configuration. Stdlib only; both clocks are
injected — a monotonic float clock inside ``Deadline`` and an aware-UTC
wall clock for ``observed_at``. ``transcripts`` is imported for its
PUBLIC constants only and is never modified.

Store locations mirror ``transcripts.ClaudeTranscript`` /
``transcripts.AntigravityTranscript`` exactly: explicit root, then
``$CLAUDE_PROJECTS_ROOT`` / ``$ANTIGRAVITY_ROOT``, then the imported
defaults; the Antigravity ``brain``-subdir rule (a root whose ``brain``
child is a directory resolves to that child) is replicated verbatim.
Claude conversations are enumerated one level deep
(``<root>/<project-dir>/<session>.jsonl``, each JSONL file = one
conversation, matching the reader's glob); Antigravity conversations are
``<root>/<session>/.system_generated/logs/transcript_full.jsonl`` with
``transcript.jsonl`` as the fallback, matching the reader's preference.

Honesty rules (same contract as the evidence core and T3b):

- A MISSING root, an empty root, or a root with no conversations is
  ``SOURCE_ABSENT`` with zero sources: "no Claude/Antigravity history"
  is a valid empty result, never a failure (FR-11, FR-22). The same
  applies to ``collect`` against a root or conversation file that does
  not exist (a nonexistent conversation id is absence, not failure).
- WHOLE-STORE failure rule (the sqlite analog from T3b: a store-level
  error fails the inventory): any ``OSError`` other than a path simply
  vanishing mid-scan (permission denied, I/O error) during inventory
  fails the ENTIRE inventory with ``COVERAGE_FAILED`` and no partial
  source list — a silently skipped conversation would be a silent hole
  in the source manifest (FR-41). A path that vanished between listing
  and stat is NOT part of the observed snapshot and is skipped, mirroring
  T3b's vanished-row rule. In ``collect``, a wholly unreadable file is
  ``COVERAGE_FAILED``; a file deleted mid-call is ``SOURCE_ABSENT``.
- ``deadline`` is checked BEFORE the store is opened, BETWEEN
  conversations in inventory, and BEFORE a race re-read; expiry yields
  ``COVERAGE_FAILED`` whose detail mentions the budget, and never a
  partial source list or a truncated success (FR-17, D06).

Claude project mapping (chosen rule, documented): Claude Code stores
each conversation under a directory name MUNGED from its cwd (path
separators and other non-path characters become ``-``, so
``/home/bruno/alpha`` is ``-home-bruno-alpha``). The munge is lossy —
an original ``-`` is indistinguishable from a ``/`` — so the inverse is
a documented heuristic, not a restoration: a name starting with ``-``
maps to ``/`` + the remainder with every ``-`` replaced by ``/``; any
other name is not derivable and is kept VERBATIM (normalization to a
project name stays a later, explicit concern). ``project_filter`` then
uses the SAME ``_cwd_matches`` rule as the other providers (exact match
or under-path component; ``/repo`` never matches ``/repo2``).

Antigravity project identity (product decision 2026-09-30): the
transcript logs themselves carry no cwd, but the Antigravity CLI's
``~/.gemini/antigravity-cli/conversation_summaries.db`` does — each row
maps ``conversation_id`` to ``workspace_uris``, a JSON array of
``file://`` URIs. The Antigravity provider reads that db READ-ONLY (a
``file:`` URI with ``mode=ro``, the same discipline as
``evidence_engram``) during inventory and maps each conversation to a
project path with these documented rules:

- Only ``file://`` entries are considered; the scheme is stripped
  (percent-decoding included) to a filesystem path.
- A conversation with MULTIPLE workspaces is ambiguous; the list is
  sorted lexicographically (code-point order) and the FIRST path wins
  — deterministic, never insertion order, never "newest".
- Non-file, unparsable, or non-list ``workspace_uris`` values skip
  project mapping for that row (the conversation keeps the legacy
  ``""``); a row's ``title`` maps independently of workspace
  parsability.
- No row for a conversation, or no db FILE at all (older installs),
  keeps the legacy behavior: ``project=""``, ``title=None``. Absence
  is valid, never a failure (FR-11).
- An UNREADABLE or corrupt db (``sqlite3.Error``) fails the WHOLE
  inventory with ``COVERAGE_FAILED`` naming the db: identity claims
  would otherwise be silently wrong (FR-11 spirit — absence vs failed
  coverage stay distinct).
- The budget deadline is checked BEFORE the db is opened (the shared
  inventory's entry check); expiry never opens it.

With real paths mapped, ``project_filter`` semantics upgrade from the
legacy "never matches" to the SAME ``_cwd_matches`` rule as every other
provider (exact match or under-path component); ``summaries_db_path=None``
disables the mapping entirely and keeps the legacy byte-compatible
behavior.

Timestamps (FR-10): message times come ONLY from the event fields —
``event["timestamp"]`` (Claude) / ``event["created_at"]`` (Antigravity)
— parsed as ISO 8601 into aware UTC datetimes. Missing, non-string,
unparseable, or NAIVE values become ``None`` (a zone is never guessed)
and are excluded from period reads while staying visible in
``CollectStats.unknown_timestamps``. A file mtime is NEVER used as a
message time; it appears only inside the revision token.

Ordering and truncation (FR-21 "no silent truncation", bounded reads):
``collect`` without a ``period`` returns the conversation's NEWEST
``max_turns`` text turns in chronological order (oldest -> newest),
mirroring T3b's newest-tail semantics — natural for append-only JSONL.
The tail is read with a chunked REVERSE scan capped at ``scan_cap``
bytes (default 8 MB, the transcript readers' memory bound, replicated
here because their helper is module-private), so an unbounded
conversation is never loaded whole. ``truncated`` is True whenever the
scan did not reach the start of the file — either the ``max_turns`` cap
stopped it early or the byte cap ended the window — which is
conservative: unscanned older lines MIGHT hold no further text turns,
but that cannot be proven without scanning, and T3b's
``items_read > items_returned`` formula is unavailable to a bounded
tail scan (it does not know the total). Accordingly ``items_read``
counts text turns found within the scanned window. Period-bounded reads
use a full FORWARD scan (one line at a time — memory-bounded per line)
and are NOT turn-capped: the interval already bounds them and the
deadline bounds the work, exactly as in T3b.

Malformed lines never crash a read: a line that is not valid JSON, or
not a JSON object, is skipped and counted in
``CollectStats.skipped_malformed``; blank lines are padding, not
events, and are not counted. Events that parse but carry no extractable
user/assistant text (other types, empty text) are filtered silently,
mirroring the transcript readers' Turn extraction.

Revision race (PRD Concurrency; D08, FR-31): a conversation's revision
token digests ``os.stat`` ``(mtime_ns, size)`` — deterministic, stable
while the file is unchanged, changed by any append (or touch).
``collect`` computes the token before and after the read; on a change
it re-reads ONCE within the remaining deadline, and reports
``COVERAGE_FAILED`` if the token changed again or the deadline expired
— unverifiable data is never served, and there is no retry loop.

Untrusted content (D07/D09): ``EvidenceItem.text`` is raw DATA quoting
what the agents wrote. It must never be interpreted as instructions;
  this module performs no content filtering — isolation is the
  consolidation layer's duty, with provenance preserved (FR-40).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat as stat_module
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import unquote, urlparse

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
from .transcripts import DEFAULT_ANTIGRAVITY_ROOT, DEFAULT_CLAUDE_ROOT

#: Turn cap for period-less collect reads; overflow is flagged, never silent.
DEFAULT_MAX_TURNS = 200

#: Verified location of the Antigravity CLI's conversation summaries
#: db (the store that knows each conversation's workspaces and title).
#: The provider opens it READ-ONLY during inventory; see the module
#: docstring for the mapping rules.
DEFAULT_ANTIGRAVITY_SUMMARIES_DB = (
    "~/.gemini/antigravity-cli/conversation_summaries.db"
)

#: Reverse tail-scan window for period-less reads. The transcript
#: readers keep the same 8 MB bound as a PRIVATE constant; the VALUE is
#: replicated here (importing the private name would couple this module
#: to ``transcripts`` internals the task forbids touching).
DEFAULT_SCAN_CAP_BYTES = 8 * 1024 * 1024

# Chunk size of the reverse scan, mirroring the readers' approach.
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class CollectStats:
    """Visibility into one ``collect`` read (no silent truncation, FR-21).

    ``items_read`` counts the text turns found within the scanned window
    (for a capped tail read that is the window, not the whole file);
    ``items_returned`` counts those actually included (period exclusions
    or the ``max_turns`` cap explain any difference); ``unknown_timestamps``
    counts turns whose event time could not be proven (missing/unparseable/
    naive) — excluded from period reads, counted either way;
    ``skipped_malformed`` counts lines skipped as malformed JSONL (invalid
    JSON or a non-object value; blank lines are padding and do not count);
    ``truncated`` is True only when the tail scan did not reach the start
    of the file (turn cap or byte cap) — see the module docstring for the
    conservative rule.
    """

    items_read: int
    items_returned: int
    unknown_timestamps: int
    skipped_malformed: int
    truncated: bool


@dataclass(frozen=True)
class ClaudeCoverageResult(CoverageResult):
    """``CoverageResult`` extension carrying ``CollectStats`` on success.

    Every OK ``collect`` from ``ClaudeEvidenceProvider`` is a
    ``ClaudeCoverageResult`` with ``stats`` set; failures stay plain
    ``CoverageResult`` so the base contract (and its validation) is
    unchanged.
    """

    stats: Optional[CollectStats] = None


@dataclass(frozen=True)
class AntigravityCoverageResult(CoverageResult):
    """``CoverageResult`` extension carrying ``CollectStats`` on success.

    Every OK ``collect`` from ``AntigravityEvidenceProvider`` is an
    ``AntigravityCoverageResult`` with ``stats`` set; failures stay plain
    ``CoverageResult`` so the base contract (and its validation) is
    unchanged.
    """

    stats: Optional[CollectStats] = None


class _RawTurn(NamedTuple):
    """One parsed text turn before scoping (period filter / cap)."""

    message_id: Optional[str]
    timestamp: Optional[datetime]
    role: str
    text: str


class _ScanOutcome(NamedTuple):
    """Result of one conversation file scan."""

    records: list  # chronological _RawTurn list
    skipped_malformed: int
    truncated: bool


def _iso_to_utc(value: object) -> Optional[datetime]:
    """Parses an ISO 8601 event timestamp into an aware UTC datetime.

    Accepts trailing-``Z`` and explicit-offset spellings (normalized to
    the same instant). Anything unprovable — missing, non-string,
    unparseable, or NAIVE (no zone) — becomes ``None``: a zone is never
    guessed (FR-10), and a file mtime is never substituted.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _stat_token(path: str) -> str:
    """Deterministic digest of ``os.stat`` ``(mtime_ns, size)``.

    Same convention as the other providers: inputs JSON-encoded
    unambiguously, sha256, 16 hex chars. Stable while the file is
    unchanged; changed by any append (size) or touch (mtime_ns).
    ``OSError`` propagates to the caller's failure handling.
    """
    info = os.stat(path)
    payload = json.dumps([info.st_mtime_ns, info.st_size])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _claude_project_from_munged(name: str) -> str:
    """Maps a munged Claude project directory name back to a path.

    Claude Code munges the conversation cwd into the directory name by
    replacing path separators (and other non-path characters) with
    ``-``: ``/home/bruno/alpha`` becomes ``-home-bruno-alpha``. The
    munge is LOSSY (an original ``-`` is indistinguishable from a
    ``/``), so this inverse is a documented heuristic: a name starting
    with ``-`` maps to ``/`` + the remainder with every remaining
    ``-`` replaced by ``/``; any other name is not derivable and is
    kept VERBATIM. Ambiguity is accepted deliberately — normalization
    to a true project identity is a later, explicit concern.
    """
    if name.startswith("-"):
        return "/" + name[1:].replace("-", "/")
    return name


def _project_from_workspace_uris(workspace_uris: object) -> Optional[str]:
    """Maps a summaries-db ``workspace_uris`` cell to ONE project path.

    Documented rule (product decision 2026-09-30): the cell must be a
    JSON array; only ``file://`` entries are kept, the scheme is
    stripped (percent-decoding included); the surviving paths are
    sorted lexicographically (code-point order) and the FIRST wins —
    multiple workspaces are ambiguous, sorted-first is deterministic,
    never insertion order. Non-file, unparsable, or non-list cells (or
    cells with no surviving path) return ``None``: the conversation
    keeps the legacy ``""`` project.
    """
    if not isinstance(workspace_uris, str) or not workspace_uris.strip():
        return None
    try:
        entries = json.loads(workspace_uris)
    except ValueError:
        return None
    if not isinstance(entries, list):
        return None
    paths = []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        parsed = urlparse(entry)
        if parsed.scheme != "file":
            continue  # non-file schemes never carry a workspace path
        path = unquote(parsed.path)
        if path:
            paths.append(path)
    if not paths:
        return None
    return sorted(paths)[0]


def _claude_event_text(event: dict) -> Optional[str]:
    """Extracts joined text blocks from a Claude user/assistant event.

    Replicates ``transcripts._claude_event_text``'s rule (same store,
    same parsing) instead of importing it: that helper is module-private
    and ``transcripts`` must stay untouched.
    """
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    blocks = [
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(block for block in blocks if block)
    return text or None


def _antigravity_user_text(content) -> Optional[str]:
    """Extracts user prompt text from an Antigravity USER_INPUT step.

    Replicates ``transcripts._antigravity_user_text``'s rule (same
    store, same parsing) instead of importing it: that helper is
    module-private and ``transcripts`` must stay untouched. Strips
    ``<USER_REQUEST>`` tags if present, or metadata blocks
    (``<ADDITIONAL_METADATA>``, ``<USER_SETTINGS_CHANGE>``) otherwise.
    """
    if not isinstance(content, str):
        return None

    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, re.DOTALL)
    if match:
        text = match.group(1).strip()
        return text or None
    cleaned = re.sub(
        r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", content, flags=re.DOTALL
    )
    cleaned = re.sub(
        r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL
    )
    cleaned = cleaned.strip()
    return cleaned or None


def _entry_is_dir(entry) -> bool:
    """``DirEntry`` directory check that PROPAGATES ``OSError``.

    The convenience ``entry.is_dir()`` swallows errors, which would turn
    an unreadable directory into a silently skipped conversation — a
    hole in the manifest (FR-41). A path that merely VANISHED mid-scan
    (``FileNotFoundError``) is not part of the observed snapshot and is
    skipped, mirroring T3b's vanished-row rule.
    """
    try:
        info = entry.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat_module.S_ISDIR(info.st_mode)


def _entry_is_regular(entry) -> bool:
    """``DirEntry`` regular-file check that PROPAGATES ``OSError``.

    Same whole-store rule as ``_entry_is_dir``; a file that merely
    vanished mid-scan (``FileNotFoundError``) is skipped instead.
    """
    try:
        info = entry.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat_module.S_ISREG(info.st_mode)


def _is_regular_file(path: str) -> bool:
    """``os.stat`` regular-file check that PROPAGATES ``OSError``.

    Same whole-store rule as ``_entry_is_dir``: vanishing is absence,
    every other stat error (permissions, I/O) fails the scan.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return False
    return stat_module.S_ISREG(info.st_mode)


class _ReverseTailScan:
    """Pull-based newest-first line reader capped to ``cap_bytes`` from EOF.

    Replicates the transcript readers' chunked reverse scan (same chunk
    size and byte-cap approach as ``transcripts._reverse_tail_lines``,
    replicated because that helper is module-private) so a period-less
    read never loads an unbounded conversation into memory. The consumer
    pulls lines with ``next_line()`` (``None`` when the capped window is
    exhausted) and can call ``has_lines()`` afterwards to learn whether
    unscanned data remains; ``hit_cap`` records that the BYTE cap (not
    EOF) ended the window. ``OSError`` propagates: an unreadable file is
    a coverage failure, never an empty read.
    """

    def __init__(self, path: str, cap_bytes: int) -> None:
        self._cap = cap_bytes
        self._fh = open(path, "rb")  # OSError propagates (unreadable file)
        self._position = os.fstat(self._fh.fileno()).st_size
        self._scanned = 0
        self._remainder = b""
        self._lines: deque = deque()
        self.hit_cap = False

    def has_lines(self) -> bool:
        """True when a buffered line or unscanned in-cap bytes remain."""
        if self._lines:
            return True
        if self._position <= 0:
            return False
        if self._scanned >= self._cap:
            self.hit_cap = True
            return False
        return True

    def next_line(self):
        """Next line (bytes) newest-first, or ``None`` when exhausted."""
        while not self._lines and self.has_lines():
            self._fill()
        if self._lines:
            return self._lines.popleft()
        return None

    def _fill(self) -> None:
        step = min(_CHUNK_SIZE, self._position, self._cap - self._scanned)
        self._position -= step
        self._scanned += step
        self._fh.seek(self._position)
        chunk = self._fh.read(step) + self._remainder
        lines = chunk.split(b"\n")
        # The oldest fragment may be completed by the next-older chunk;
        # at the file start it is a complete line and stays.
        self._remainder = lines.pop(0) if self._position > 0 else b""
        # The chunk as a whole is OLDER than everything already yielded,
        # so its lines (newest of the chunk first) go to the right end.
        self._lines.extend(reversed(lines))

    def close(self) -> None:
        self._fh.close()


class _JsonlEvidenceProvider:
    """Shared machinery for the JSONL-backed transcript providers.

    Subclasses supply ``kind``, ``_coverage_class``,
    ``_timestamp_key``, ``_message_id_keys``, ``_iter_conversations()``
    and ``_extract_turn(event)``. Everything else — inventory semantics
    (whole-store failure rule, budget checks, ``_cwd_matches`` filtering),
    the stat-token race protocol, the two scan modes, and the
    stats/manifest plumbing — is identical for both providers by
    construction, so their honesty semantics can never drift apart.
    """

    kind: SourceKind
    _coverage_class: type
    _timestamp_key: str
    _message_id_keys: tuple

    def __init__(
        self,
        root: str,
        *,
        clock=None,
        max_turns: int = DEFAULT_MAX_TURNS,
        scan_cap: int = DEFAULT_SCAN_CAP_BYTES,
    ) -> None:
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
            raise ValueError("max_turns must be an integer >= 1")
        if not isinstance(scan_cap, int) or isinstance(scan_cap, bool) or scan_cap < 1:
            raise ValueError("scan_cap must be an integer >= 1")
        self.root = root
        self.max_turns = max_turns
        self.scan_cap = scan_cap
        self._clock = clock if clock is not None else _utc_now

    # -- subclass hooks ------------------------------------------------

    def _iter_conversations(self):
        """Yields (session_id, project, rel_locator, abs_path, title)
        tuples: ``title`` is a display snapshot when the store carries
        one (the Antigravity summaries db), else ``None``.

        Deterministic order (sorted by rel_locator) is applied by the
        inventory; ``OSError`` other than vanishing paths propagates per
        the whole-store rule.
        """

    def _extract_turn(self, event: dict):
        """(role, text) when the event is a text-bearing turn, else None."""

    # -- shared event plumbing ------------------------------------------

    def _event_message_id(self, event: dict) -> Optional[str]:
        """First non-empty string among the provider's id candidate keys.

        The transcript stores have no uniform message-id field, so each
        provider documents its candidates (Claude: ``uuid`` then ``id``;
        Antigravity: ``id`` then ``event_id``); an event carrying none
        yields ``None`` — provenance still holds via source + locator.
        """
        for key in self._message_id_keys:
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _parse_event(self, event: dict) -> Optional[_RawTurn]:
        extracted = self._extract_turn(event)
        if extracted is None:
            return None
        role, text = extracted
        return _RawTurn(
            message_id=self._event_message_id(event),
            timestamp=_iso_to_utc(event.get(self._timestamp_key)),
            role=role,
            text=text,
        )

    # -- reads (``_read_records`` is the seam the race tests exercise) ---

    def _read_records(self, path: str, tail_limit: Optional[int]) -> _ScanOutcome:
        """Parses a conversation's JSONL into chronological text turns.

        ``tail_limit=None`` -> full forward scan (period reads: one line
        at a time, not turn-capped). Otherwise a reverse tail scan that
        collects the NEWEST ``tail_limit`` turns within ``scan_cap``
        bytes (period-less reads: bounded memory, T3b newest-tail
        semantics).
        """
        if tail_limit is None:
            return self._forward_scan(path)
        return self._reverse_tail_scan(path, tail_limit)

    def _scan_lines(self, lines, tail_limit: Optional[int] = None) -> tuple:
        """Shared line -> records loop; returns (records, skipped).

        Blank lines are padding (not malformed); a line that is invalid
        JSON or a non-object JSON value counts as skipped-malformed;
        events without extractable text are filtered silently (the
        transcript readers' Turn semantics). ``tail_limit`` stops the
        collection once that many text turns have been gathered.
        """
        records: list = []
        skipped = 0
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:  # JSONDecodeError / undecodable bytes
                skipped += 1
                continue
            if not isinstance(event, dict):
                skipped += 1  # a JSON scalar is not a JSONL event object
                continue
            record = self._parse_event(event)
            if record is not None:
                records.append(record)
                if tail_limit is not None and len(records) >= tail_limit:
                    break
        return records, skipped

    def _forward_scan(self, path: str) -> _ScanOutcome:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            # One line at a time: the interval bounds the kept set, the
            # deadline bounds the work (T3b rule for period reads).
            records, skipped = self._scan_lines(fh)
        return _ScanOutcome(records, skipped_malformed=skipped, truncated=False)

    def _reverse_tail_scan(self, path: str, tail_limit: int) -> _ScanOutcome:
        scan = _ReverseTailScan(path, self.scan_cap)
        try:
            # Pull newest-first until the turn cap; the sentinel-iterator
            # keeps the shared line loop and the early stop in one place.
            records, skipped = self._scan_lines(
                iter(scan.next_line, None), tail_limit=tail_limit
            )
        finally:
            scan.close()
        # Conservative truncation flag: anything unscanned (early stop
        # or byte cap) MIGHT hold more text turns — see module docstring.
        truncated = scan.has_lines() or scan.hit_cap
        records.reverse()  # collected newest-first -> chronological
        return _ScanOutcome(records, skipped_malformed=skipped, truncated=truncated)

    def _build_coverage(
        self, source: Source, outcome: _ScanOutcome, period: Optional[Period]
    ):
        records = outcome.records
        unknown = sum(1 for record in records if record.timestamp is None)
        if period is None:
            kept = records  # already the capped newest tail, chronological
            truncated = outcome.truncated
        else:
            # IN over the half-open bounds; UNKNOWN (unprovable) is
            # excluded — classify_timestamp itself decides, never this
            # module, and never with a substituted time.
            kept = [
                record
                for record in records
                if classify_timestamp(record.timestamp, period) is Verdict.IN
            ]
            truncated = False  # interval-bounded reads are not capped
        items = tuple(
            EvidenceItem(
                source_id=source.source_id,
                kind=self.kind,
                timestamp=record.timestamp,
                role=record.role,
                text=record.text,  # untrusted DATA (FR-40)
                message_id=record.message_id,
            )
            for record in kept
        )
        stats = CollectStats(
            items_read=len(records),
            items_returned=len(items),
            unknown_timestamps=unknown,
            skipped_malformed=outcome.skipped_malformed,
            truncated=truncated,
        )
        return self._coverage_class(
            source=source, status=CoverageStatus.OK, items=items, stats=stats
        )

    # -- EvidenceProvider protocol ---------------------------------------

    def inventory(
        self,
        project_filter: Optional[str] = None,
        deadline: Optional[Deadline] = None,
    ) -> InventoryResult:
        """Lists ALL stored conversations as Sources (see class docs)."""
        if deadline is not None and deadline.expired():
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"budget exceeded before scanning {self.kind}"
                " transcripts",
            )
        if not os.path.isdir(self.root):
            return InventoryResult(CoverageStatus.SOURCE_ABSENT)
        observed_at = self._clock()
        sources: list = []
        try:
            conversations = sorted(
                self._iter_conversations(), key=lambda item: item[2]
            )
            for session_id, project, rel_locator, abs_path, title in conversations:
                if deadline is not None and deadline.expired():
                    return InventoryResult(
                        CoverageStatus.COVERAGE_FAILED,
                        error_detail="budget exceeded while scanning"
                        f" {self.kind} transcripts; no partial source list",
                    )
                if not _cwd_matches(project, project_filter):
                    continue
                try:
                    token = _stat_token(abs_path)
                except FileNotFoundError:
                    # Vanished between listing and stat: not part of the
                    # observed snapshot — skip, never raise (T3b rule).
                    continue
                sources.append(
                    Source(
                        source_id=f"{self.kind}:{session_id}",
                        kind=self.kind,
                        project=project,
                        locator=rel_locator,
                        revision_token=token,
                        observed_at=observed_at,
                        title=title,
                        state=None,  # stored chats carry no open/closed snapshot
                    )
                )
        except OSError as exc:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"{self.kind} transcript store unreadable: {exc}",
            )
        if not conversations:
            # No conversations at all: the honest empty (FR-11/FR-22).
            return InventoryResult(CoverageStatus.SOURCE_ABSENT)
        return InventoryResult(CoverageStatus.OK, tuple(sources))

    def collect(
        self,
        source: Source,
        period: Optional[Period] = None,
        deadline: Optional[Deadline] = None,
    ) -> CoverageResult:
        """Reads one conversation, bounded by ``period`` or ``max_turns``.

        The conversation file is ``<root>/<source.locator>``. Mid-read
        revision changes trigger exactly one re-read within the
        remaining deadline; a second change or an expired deadline is
        COVERAGE_FAILED — unverifiable data is never served.
        """
        if deadline is not None and deadline.expired():
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before reading"
                f" {self.kind} transcript {source.source_id}",
            )
        if not os.path.isdir(self.root):
            return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
        path = os.path.join(self.root, source.locator)
        if not os.path.isfile(path):
            return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
        try:
            return self._collect_file(source, path, period, deadline)
        except FileNotFoundError:
            # Deleted between the existence check and the open: absent.
            return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
        except OSError as exc:
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"{self.kind} transcript unreadable: {exc}",
            )

    def _collect_file(
        self,
        source: Source,
        path: str,
        period: Optional[Period],
        deadline: Optional[Deadline],
    ) -> CoverageResult:
        tail_limit = None if period is not None else self.max_turns
        token_before = _stat_token(path)
        outcome = self._read_records(path, tail_limit)
        token_after = _stat_token(path)
        if token_after != token_before:
            if deadline is not None and deadline.expired():
                return CoverageResult(
                    source,
                    CoverageStatus.COVERAGE_FAILED,
                    error_detail="budget exceeded before re-reading"
                    f" {self.kind} transcript {source.source_id}",
                )
            outcome = self._read_records(path, tail_limit)
            token_final = _stat_token(path)
            if token_final != token_after:
                return CoverageResult(
                    source,
                    CoverageStatus.COVERAGE_FAILED,
                    error_detail=f"{self.kind} transcript"
                    f" {source.source_id} revision changed again during"
                    " re-read; refusing to serve unverifiable data",
                )
        return self._build_coverage(source, outcome, period)


class ClaudeEvidenceProvider(_JsonlEvidenceProvider):
    """Evidence provider over ALL STORED Claude Code conversations.

    ``inventory`` enumerates every ``<root>/<project-dir>/<session>.jsonl``
    file (one level deep, mirroring ``ClaudeTranscript``'s glob; each
    JSONL file is one conversation) as a ``Source``: ``source_id`` is
    ``claude:<session-id-from-filename>``, ``project`` is the munged
    directory name mapped back to a path when derivable (see
    ``_claude_project_from_munged`` for the documented lossy heuristic;
    undashed names stay verbatim), ``locator`` is the file path RELATIVE
    to the root, ``revision_token`` digests ``os.stat`` (mtime_ns, size),
    and ``title``/``state`` are ``None`` — a stored conversation carries
    no open/closed status snapshot to report truthfully.

    ``project_filter`` uses the SAME ``_cwd_matches`` rule as every other
    provider (exact match or under-path component), reused by import so
    it stays single-source.
    """

    kind: SourceKind = "claude"
    _coverage_class = ClaudeCoverageResult
    _timestamp_key = "timestamp"
    _message_id_keys = ("uuid", "id")

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        clock=None,
        max_turns: int = DEFAULT_MAX_TURNS,
        scan_cap: int = DEFAULT_SCAN_CAP_BYTES,
    ) -> None:
        resolved = root or os.environ.get("CLAUDE_PROJECTS_ROOT") or DEFAULT_CLAUDE_ROOT
        super().__init__(
            str(Path(resolved).expanduser()),
            clock=clock,
            max_turns=max_turns,
            scan_cap=scan_cap,
        )

    def _iter_conversations(self):
        with os.scandir(self.root) as projects:
            for project_entry in projects:
                if not _entry_is_dir(project_entry):
                    continue
                with os.scandir(project_entry.path) as files:
                    for file_entry in files:
                        if not file_entry.name.endswith(".jsonl"):
                            continue
                        session_id = file_entry.name[: -len(".jsonl")]
                        if not session_id:
                            continue  # a bare ".jsonl" is not a conversation
                        if not _entry_is_regular(file_entry):
                            continue
                        yield (
                            session_id,
                            _claude_project_from_munged(project_entry.name),
                            os.path.join(project_entry.name, file_entry.name),
                            file_entry.path,
                            None,  # Claude stores carry no title snapshot
                        )

    def _extract_turn(self, event: dict):
        kind = event.get("type")
        if kind not in ("user", "assistant"):
            return None
        text = _claude_event_text(event)
        if not text:
            return None
        return (str(kind), text)


class AntigravityEvidenceProvider(_JsonlEvidenceProvider):
    """Evidence provider over ALL STORED Antigravity conversations.

    ``inventory`` enumerates every session directory under the root that
    holds ``.system_generated/logs/transcript_full.jsonl`` (falling back
    to ``transcript.jsonl``, mirroring ``AntigravityTranscript``'s file
    preference; a session directory without either is not a conversation)
    as a ``Source``: ``source_id`` is ``antigravity:<session-id>`` and
    ``locator``/``revision_token``/``state`` follow the Claude provider's
    rules. Enumeration is structural; the session-id SHAPE sniffing
    belongs to the transcript router, not this provider.

    PROJECT IDENTITY and TITLE (product decision 2026-09-30): when
    ``summaries_db_path`` resolves (default: the Antigravity CLI's
    ``~/.gemini/antigravity-cli/conversation_summaries.db``), inventory
    opens it READ-ONLY (``file:`` URI, ``mode=ro``) and maps each
    conversation to the SORTED-FIRST ``file://`` workspace path and to
    the row ``title`` when present — see the module docstring for the
    full documented rule set (skips, absence, corrupt-db failure,
    deadline-before-open). ``summaries_db_path=None`` disables the
    mapping entirely: every project is the legacy placeholder ``""``
    (matching ONLY an absent ``project_filter``) and titles stay
    ``None``. With real paths mapped, ``project_filter`` rides the same
    ``_cwd_matches`` rule as every other provider.

    ``collect`` reads the file recorded in the locator (the snapshot the
    revision token covers), unlike the interactive reader which
    re-resolves the file preference per read.
    """

    kind: SourceKind = "antigravity"
    _coverage_class = AntigravityCoverageResult
    _timestamp_key = "created_at"
    _message_id_keys = ("id", "event_id")

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        clock=None,
        max_turns: int = DEFAULT_MAX_TURNS,
        scan_cap: int = DEFAULT_SCAN_CAP_BYTES,
        summaries_db_path: Optional[str] = DEFAULT_ANTIGRAVITY_SUMMARIES_DB,
    ) -> None:
        if summaries_db_path is not None and (
            not isinstance(summaries_db_path, str)
            or not summaries_db_path.strip()
        ):
            raise ValueError(
                "summaries_db_path must be None (disabled) or a non-empty"
                " path string"
            )
        resolved = root or os.environ.get("ANTIGRAVITY_ROOT") or DEFAULT_ANTIGRAVITY_ROOT
        expanded = Path(resolved).expanduser()
        # Same brain-subdir rule as transcripts.AntigravityTranscript.
        if (expanded / "brain").is_dir():
            expanded = expanded / "brain"
        super().__init__(
            str(expanded),
            clock=clock,
            max_turns=max_turns,
            scan_cap=scan_cap,
        )
        self.summaries_db_path = (
            None
            if summaries_db_path is None
            else str(Path(summaries_db_path).expanduser())
        )

    # -- summaries identity (module docstring documents the rules) ----

    def _load_identity_map(self) -> tuple[dict, dict]:
        """Reads the summaries db into ``(projects, titles)``, both
        keyed by conversation id.

        Disabled (``summaries_db_path=None``) or missing db FILE ->
        empty maps: valid absence, never a failure (FR-11; older
        installs). A corrupt/unreadable db raises ``sqlite3.Error``
        to the ``inventory`` override, which fails the WHOLE inventory
        — identity claims would otherwise be silently wrong.
        """
        if self.summaries_db_path is None:
            return {}, {}
        if not os.path.isfile(self.summaries_db_path):
            return {}, {}
        uri = f"{Path(self.summaries_db_path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)  # read-only by construction
        try:
            rows = conn.execute(
                "SELECT conversation_id, title, workspace_uris"
                " FROM conversation_summaries"
            ).fetchall()
        finally:
            conn.close()
        projects: dict = {}
        titles: dict = {}
        for conversation_id, title, workspace_uris in rows:
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            if isinstance(title, str) and title.strip():
                titles[conversation_id] = title.strip()
            project = _project_from_workspace_uris(workspace_uris)
            if project is not None:
                projects[conversation_id] = project
        return projects, titles

    def inventory(
        self,
        project_filter: Optional[str] = None,
        deadline: Optional[Deadline] = None,
    ) -> InventoryResult:
        """Lists ALL stored conversations as Sources (see class docs).

        The shared implementation scans the transcript root; this
        override additionally converts a ``sqlite3.Error`` from the
        summaries db (opened inside the scan, AFTER the shared entry
        deadline check) into a whole-inventory COVERAGE_FAILED naming
        the db — the whole-store honesty rule applied to the identity
        source.
        """
        try:
            return super().inventory(
                project_filter=project_filter, deadline=deadline
            )
        except sqlite3.Error as exc:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail=(
                    f"antigravity summaries db unreadable"
                    f" ({self.summaries_db_path}): {exc}"
                ),
            )

    def _iter_conversations(self):
        projects, titles = self._load_identity_map()
        with os.scandir(self.root) as sessions:
            for session_entry in sessions:
                if not _entry_is_dir(session_entry):
                    continue
                session_id = session_entry.name
                logs = os.path.join(session_entry.path, ".system_generated", "logs")
                for name in ("transcript_full.jsonl", "transcript.jsonl"):
                    path = os.path.join(logs, name)
                    if _is_regular_file(path):
                        yield (
                            session_id,
                            projects.get(session_id, ""),
                            os.path.join(session_id, ".system_generated", "logs", name),
                            path,
                            titles.get(session_id),
                        )
                        break

    def _extract_turn(self, event: dict):
        event_type = event.get("type")
        if event_type == "USER_INPUT":
            text = _antigravity_user_text(event.get("content"))
            if text:
                return ("user", text)
            return None
        if event_type == "PLANNER_RESPONSE":
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                return ("assistant", content.strip())
        return None


def manifest_from_inventory(result: InventoryResult) -> list[ManifestEntry]:
    """Builds report-manifest entries from an inventory result of EITHER
    JSONL provider by reusing the core ``evidence.manifest_entries``
    mapping (one rule for every provider kind; deterministic: same
    sources in, same entries out)."""
    return manifest_entries(result.sources)

