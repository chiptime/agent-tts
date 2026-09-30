"""Engram evidence provider: memory observations from Engram's local
SQLite store (FR-05, FR-11, FR-17, FR-20, FR-22, FR-25, FR-31, FR-38,
FR-39, FR-40, FR-41; PRD decisions D03, D06, D08, D09).

Inert library (D09): nothing here is wired into the server, the LLM
loop, the tool surface, or configuration. Stdlib only; both clocks are
injected — a monotonic float clock inside ``Deadline`` and an aware-UTC
wall clock for ``observed_at``.

Access mechanism (assumption A-2, verified): Engram persists to a local
SQLite database, default ``~/.engram/engram.db`` (expanded from the
user's home; an explicit ``db_path`` overrides, and NO environment
variable is consulted). The connection is always opened READ-ONLY via
a ``file:`` URI with ``mode=ro`` — read-only by construction (FR-38/
FR-39): no subprocess, no CLI, no writes, nothing outside the db file.
The ``sessions`` table is deliberately NOT read (keep-scope-minimal).

Timestamp convention (DOCUMENTED ASSUMPTION): Engram stores naive TEXT
stamps like ``2026-09-30 18:35:59`` and writes UTC server-side, so this
adapter attaches ``timezone.utc`` to naive stamps by convention. If
Engram ever stores non-UTC stamps, period classification here is WRONG
— the rule is documented, not guessed silently. These are row values,
NOT file mtimes; FR-10's mtime-substitution prohibition does not even
arise (an unparseable stamp becomes ``None``, never a substitution).

Honesty rules (same contract as the evidence core and T3b/T3c):

- A MISSING db file or a zero-byte db is ``SOURCE_ABSENT`` with zero
  sources: "no Engram memory" is a valid empty result, never a failure
  (FR-11, FR-22). The same applies to ``collect`` against a project
  with no rows at all (an unknown project is absence, not failure).
- ``sqlite3.Error`` / ``OSError`` (unreadable, corrupt, locked) is
  ``COVERAGE_FAILED`` with the error detail — never OK-empty, never
  raised through the acquisition boundary (FR-20).
- ``deadline`` is checked BEFORE the db is opened, BETWEEN projects in
  inventory, and BEFORE a race re-read; expiry yields
  ``COVERAGE_FAILED`` whose detail mentions the budget, and NEVER a
  partial source list (FR-17, D06).

Project enumeration rule (chosen, documented): ``inventory`` lists
DISTINCT non-empty ``project`` values among observations, INCLUDING
projects whose rows are all soft-deleted — such a project still exists
as a source so its deletions remain detectable in revision tokens
(FR-31); a project only stops existing when Engram hard-deletes its
last row. A project name is a NAME, not a path (it may contain ``/``):
``project_filter`` here is EXACT name match only — the T3a
``_cwd_matches`` under-path rule deliberately does NOT apply — and an
empty/None filter means all projects.

Revision tokens (D08, FR-31): a project's token is a sha256-16 digest
over its FULL row skeleton ``[(id, updated_at, deleted_at), ...]``
ordered by id — every row of the project, active AND soft-deleted, but
never row content. This strengthens the suggested max-aggregate
composition (``[active_count, deleted_count, max(id), digest of
max(updated_at), max(created_at)]``): a max-aggregate CANNOT see an
edit whose ``updated_at`` bump stays below the project maximum, which
would silently violate "changes on update". The skeleton digest is
stable when nothing changed, changes on insert (new id), on ANY row
edit (its ``updated_at`` moves), on soft delete (``deleted_at`` flips),
and even on a hard purge of deleted rows; ``observed_at`` is
deliberately NOT an input — a later observation of identical content is
not a revision change.

Truncation visibility (FR-21 "no silent truncation", bounded reads):
``collect`` without a ``period`` returns the project's NEWEST
``max_items`` observations (default 200) presented chronologically —
the population count is reported as ``stats.items_read`` so the cap is
visible as ``items_read > items_returned`` (T3b semantics via stats).
Independently, each item's CONTENT is excerpted to ``max_chars``
(default 4000) with an explicit ``... [truncated]`` marker — the TITLE
is always full; ``stats.truncated_chars`` counts the characters dropped
from content excerpts. The excerpt bound applies in BOTH modes (it is
a per-item memory bound); the item-count cap applies only to
period-less reads because the interval already bounds those, exactly
as in T3b/T3c. Nothing is ever cut mid-truth without its marker.

``stats.skipped`` mirrors T3b/T3c's ``unknown_timestamps`` visibility
pattern: it counts rows whose ``created_at`` could not be parsed as
UTC-aware — excluded from PERIOD reads (trust is not inferred), kept in
period-less reads with ``timestamp=None``, counted either way.

Revision race (PRD Concurrency; D08, FR-31): ``collect`` computes the
project token before and after the read; on a change it re-reads ONCE
within the remaining deadline, and reports ``COVERAGE_FAILED`` if the
token changed again or the deadline expired — unverifiable data is
never served, and there is no retry loop.

Untrusted content (D07/D09; FR-25, FR-40): observation titles and
content are DATA, never instructions. They are carried verbatim into
``EvidenceItem.text``; this module performs no content filtering and
no instruction interpretation — isolation is the consolidation layer's
duty, with provenance preserved.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .evidence import (
    CoverageResult,
    CoverageStatus,
    Deadline,
    EvidenceItem,
    InventoryResult,
    Source,
    SourceKind,
    _utc_now,
    manifest_entries,
)
from .periods import Period, Verdict, classify_timestamp
from .reportstore import ManifestEntry

#: Default store location; expanded from the user's home. No env var.
DEFAULT_ENGRAM_DB = "~/.engram/engram.db"

#: Item-count cap for period-less collect reads; visible via stats.
DEFAULT_MAX_ITEMS = 200

#: Per-item content excerpt cap; the truncation marker makes cuts visible.
DEFAULT_MAX_CHARS = 4000

#: Appended to excerpted content so a cut can never pass unnoticed.
_TRUNCATION_MARKER = "... [truncated]"

# Distinct non-empty project names (NULL and '' are not projects).
_PROJECTS_SQL = (
    "SELECT DISTINCT project FROM observations"
    " WHERE project IS NOT NULL AND project != ''"
)
# One project's full row skeleton for revision tokens: every row, active
# AND soft-deleted, ordered by id — additions, edits, soft deletes, and
# hard purges all move this (see module docstring for why max-aggregates
# were rejected).
_SKELETON_SQL = (
    "SELECT id, updated_at, deleted_at FROM observations"
    " WHERE project = ? ORDER BY id ASC"
)
# Non-deleted population size: the items_read denominator that keeps the
# period-less item cap visible instead of silent.
_ACTIVE_COUNT_SQL = (
    "SELECT COUNT(*) FROM observations WHERE project = ? AND deleted_at IS NULL"
)
# Period reads scan the project's full active history oldest-first.
_ROWS_ASC_SQL = (
    "SELECT id, title, content, created_at FROM observations"
    " WHERE project = ? AND deleted_at IS NULL ORDER BY created_at ASC, id ASC"
)
# Period-less bounded reads take the NEWEST max_items rows...
_ROWS_DESC_SQL = (
    "SELECT id, title, content, created_at FROM observations"
    " WHERE project = ? AND deleted_at IS NULL"
    " ORDER BY created_at DESC, id DESC LIMIT ?"
)


@dataclass(frozen=True)
class EngramCollectStats:
    """Visibility into one ``collect`` read (no silent truncation, FR-21).

    ``items_read`` counts the project's NON-DELETED observations (the
    population being scoped; larger than ``items_returned`` when the
    period excluded rows or the ``max_items`` cap dropped the oldest
    ones); ``items_returned`` counts the items actually returned;
    ``truncated_chars`` counts characters dropped from content excerpts
    (the ``... [truncated]`` marker's own length is not "dropped");
    ``skipped`` counts rows whose ``created_at`` could not be parsed as
    UTC-aware — excluded from period reads, counted either way
    (T3b/T3c ``unknown_timestamps`` pattern).
    """

    items_read: int
    items_returned: int
    truncated_chars: int
    skipped: int


@dataclass(frozen=True)
class EngramCoverageResult(CoverageResult):
    """``CoverageResult`` extension carrying ``EngramCollectStats`` on
    success.

    Every OK ``collect`` from this provider is an
    ``EngramCoverageResult`` with ``stats`` set; failures stay plain
    ``CoverageResult`` so the base contract (and its validation) is
    unchanged.
    """

    stats: Optional[EngramCollectStats] = None


def _stamp_to_utc(value: object) -> Optional[datetime]:
    """Parses an Engram naive-TEXT ``created_at`` into an aware UTC
    datetime.

    Documented convention: Engram writes UTC, so a NAIVE stamp is read
    as UTC by attaching ``timezone.utc``; a stamp already carrying an
    explicit offset (any spelling, trailing ``Z`` included) is
    normalized to UTC. Anything unprovable — missing, non-string,
    unparseable — becomes ``None``: an unknown time, never a guessed
    zone and never a substituted one (FR-10; see the module docstring
    for the non-UTC caveat).
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
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cell(value: object) -> Optional[str]:
    """Normalizes one skeleton cell for hashing: ``None`` stays ``None``
    (distinct from any string), anything else becomes its string form so
    the payload shape cannot drift with column affinity quirks."""
    if value is None:
        return None
    return str(value)


def _project_token(skeleton: list) -> str:
    """Deterministic sha256-16 digest of a project's row skeleton.

    Same convention as the other providers: inputs JSON-encoded
    unambiguously before hashing; stable while the population is
    unchanged, changed by any insert/edit/soft-delete/purge (see the
    module docstring for the full composition rationale). ``id`` is an
    input so a purge-then-reinsert with identical stamps cannot collide.
    The empty skeleton (a project with no rows at all) still digests —
    callers treat it as absence before ever hashing it.
    """
    payload = json.dumps(
        [[_cell(row[0]), _cell(row[1]), _cell(row[2])] for row in skeleton]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class EngramEvidenceProvider:
    """Evidence provider over ALL STORED Engram observations, grouped by
    project.

    ``inventory`` enumerates every DISTINCT non-empty ``project`` as a
    ``Source`` (soft-deleted-only projects included — see the module
    docstring): ``source_id`` is ``engram:<project>`` (names may contain
    ``/``; it is a name, not a path), ``locator`` is
    ``<db_path>::<project>``, ``project`` is the project name kept
    AS-IS, ``revision_token`` is the row-skeleton digest, and
    ``title``/``state`` are ``None`` — a memory project carries no
    open/closed status snapshot to report truthfully. ``project_filter``
    is EXACT name match only (empty/None = all projects).

    ``collect`` reads one project's NON-deleted observations oldest-first
    (period reads: full active history; period-less reads: the newest
    ``max_items`` presented chronologically) into ``role="observation"``
    items whose ``text`` is ``title + "\\n" + content-or-""`` with the
    content excerpted to ``max_chars`` behind a visible marker. The
    project name is ``source.project``; a project with no rows at all is
    ``SOURCE_ABSENT``. Mid-read revision changes trigger exactly one
    re-read within the remaining deadline; a second change or an expired
    deadline is ``COVERAGE_FAILED`` — unverifiable data is never served.
    """

    kind: SourceKind = "engram"

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        clock=None,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        if (
            not isinstance(max_items, int)
            or isinstance(max_items, bool)
            or max_items < 1
        ):
            raise ValueError("max_items must be an integer >= 1")
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars < 1
        ):
            raise ValueError("max_chars must be an integer >= 1")
        self.db_path = str(Path(db_path or DEFAULT_ENGRAM_DB).expanduser())
        self.max_items = max_items
        self.max_chars = max_chars
        self._clock = clock if clock is not None else _utc_now

    # ------------------------------------------------------------------
    # internals (``_read_rows`` is the seam the race tests exercise)

    def _readable_path(self) -> Optional[str]:
        """The path when a non-empty db file exists, else None.

        A missing file or a zero-byte db contains no observations —
        SOURCE_ABSENT, the honest empty (FR-11/FR-22).
        """
        try:
            if os.path.isfile(self.db_path) and os.path.getsize(self.db_path) > 0:
                return self.db_path
        except OSError:
            pass  # vanished/unreadable between the two calls: no store
        return None

    def _connect(self) -> sqlite3.Connection:
        """Opens the store READ-ONLY via a ``file:`` URI (FR-38/FR-39):
        the entire access surface is this one connection mode."""
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _project_skeleton(self, conn: sqlite3.Connection, project: str) -> list:
        """The project's full ``(id, updated_at, deleted_at)`` skeleton,
        ordered by id. An EMPTY list means the project has no rows at
        all (never existed, or fully purged mid-read): absence."""
        return conn.execute(_SKELETON_SQL, (project,)).fetchall()

    def _read_rows(self, conn: sqlite3.Connection, project: str, newest_limit):
        """Reads the project's non-deleted observations; returns
        ``(rows, total_active)``.

        ``newest_limit=None`` (period reads) scans the FULL active
        history oldest-first. Otherwise the NEWEST ``newest_limit`` rows
        are selected (bounded period-less read) and returned
        chronologically. ``total_active`` — the COUNT of all non-deleted
        rows — is always the true population, so the cap stays visible
        as ``items_read > items_returned`` instead of silently unknown.
        """
        total = conn.execute(_ACTIVE_COUNT_SQL, (project,)).fetchone()[0]
        if newest_limit is None:
            rows = conn.execute(_ROWS_ASC_SQL, (project,)).fetchall()
        else:
            rows = list(
                reversed(conn.execute(_ROWS_DESC_SQL, (project, newest_limit)).fetchall())
            )
        return rows, total

    def _build_coverage(
        self,
        source: Source,
        rows: list,
        total_active: int,
        period: Optional[Period],
    ) -> EngramCoverageResult:
        """Shapes read rows into items + stats (scoping, excerpting)."""
        skipped = 0
        parsed = []
        for row_id, title, content, created_at in rows:
            stamp = _stamp_to_utc(created_at)
            if stamp is None:
                skipped += 1
            parsed.append((row_id, title, content, stamp))
        if period is None:
            kept = parsed  # already the capped newest selection
        else:
            # IN over the half-open bounds; UNKNOWN (unprovable) is
            # excluded — classify_timestamp itself decides, never this
            # module, and never with a substituted time.
            kept = [
                row
                for row in parsed
                if classify_timestamp(row[3], period) is Verdict.IN
            ]
        items = []
        truncated_chars = 0
        for row_id, title, content, stamp in kept:
            # Title always full; content (None included) becomes "".
            title_text = title if isinstance(title, str) else ""
            content_text = content if isinstance(content, str) else ""
            if len(content_text) > self.max_chars:
                truncated_chars += len(content_text) - self.max_chars
                content_text = content_text[: self.max_chars] + _TRUNCATION_MARKER
            items.append(
                EvidenceItem(
                    source_id=source.source_id,
                    kind=self.kind,
                    timestamp=stamp,
                    role="observation",
                    # Untrusted DATA, carried verbatim (FR-25/FR-40).
                    text=title_text + "\n" + content_text,
                    message_id=str(row_id),
                )
            )
        stats = EngramCollectStats(
            items_read=total_active,
            items_returned=len(items),
            truncated_chars=truncated_chars,
            skipped=skipped,
        )
        return EngramCoverageResult(
            source=source, status=CoverageStatus.OK, items=tuple(items), stats=stats
        )

    # ------------------------------------------------------------------
    # EvidenceProvider protocol

    def inventory(
        self,
        project_filter: Optional[str] = None,
        deadline: Optional[Deadline] = None,
    ) -> InventoryResult:
        """Lists every stored project as one Source (see class docstring
        for the soft-deleted-only rule and the EXACT-match filter)."""
        if deadline is not None and deadline.expired():
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before scanning engram projects",
            )
        if self._readable_path() is None:
            return InventoryResult(CoverageStatus.SOURCE_ABSENT)
        observed_at = self._clock()
        sources: list = []
        try:
            conn = self._connect()
            try:
                names = [row[0] for row in conn.execute(_PROJECTS_SQL)]
                for name in sorted(names):  # deterministic listing order
                    if deadline is not None and deadline.expired():
                        return InventoryResult(
                            CoverageStatus.COVERAGE_FAILED,
                            error_detail="budget exceeded while scanning engram"
                            " projects; no partial source list",
                        )
                    if project_filter not in (None, "") and name != project_filter:
                        continue  # EXACT name match only, never under-path
                    skeleton = self._project_skeleton(conn, name)
                    if not skeleton:
                        # Rows vanished between DISTINCT and the skeleton
                        # query (concurrent purge): not part of the
                        # observed snapshot — skip, never raise.
                        continue
                    sources.append(
                        Source(
                            source_id=f"engram:{name}",
                            kind=self.kind,
                            project=name,
                            locator=f"{self.db_path}::{name}",
                            revision_token=_project_token(skeleton),
                            observed_at=observed_at,
                            title=None,  # a project carries no title snapshot
                            state=None,  # ...and no open/closed state snapshot
                        )
                    )
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"engram db unreadable: {exc}",
            )
        return InventoryResult(CoverageStatus.OK, tuple(sources))

    def collect(
        self,
        source: Source,
        period: Optional[Period] = None,
        deadline: Optional[Deadline] = None,
    ) -> CoverageResult:
        """Reads one project's observations, bounded by ``period`` or
        ``max_items``/``max_chars``.

        The project name is ``source.project``. Mid-read revision
        changes trigger exactly one re-read within the remaining
        deadline; a second change or an expired deadline is
        COVERAGE_FAILED — unverifiable data is never served.
        """
        if deadline is not None and deadline.expired():
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before reading"
                f" engram project {source.source_id}",
            )
        if self._readable_path() is None:
            return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
        project = source.project
        newest_limit = None if period is not None else self.max_items
        try:
            conn = self._connect()
            try:
                skeleton = self._project_skeleton(conn, project)
                if not skeleton:
                    return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
                for attempt in (1, 2):
                    token_before = _project_token(skeleton)
                    rows, total_active = self._read_rows(
                        conn, project, newest_limit
                    )
                    skeleton = self._project_skeleton(conn, project)
                    if not skeleton:
                        # Every row vanished mid-read: provably absent now.
                        return CoverageResult(source, CoverageStatus.SOURCE_ABSENT)
                    if token_before == _project_token(skeleton):
                        return self._build_coverage(
                            source, rows, total_active, period
                        )
                    if attempt == 2:
                        return CoverageResult(
                            source,
                            CoverageStatus.COVERAGE_FAILED,
                            error_detail="engram project"
                            f" {source.source_id} revision changed again during"
                            " re-read; refusing to serve unverifiable data",
                        )
                    if deadline is not None and deadline.expired():
                        return CoverageResult(
                            source,
                            CoverageStatus.COVERAGE_FAILED,
                            error_detail="budget exceeded before re-reading"
                            f" engram project {source.source_id}",
                        )
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail=f"engram db unreadable: {exc}",
            )
        return CoverageResult(  # pragma: no cover - loop always returns
            source,
            CoverageStatus.COVERAGE_FAILED,
            error_detail=f"engram project {source.source_id} read did not conclude",
        )


def manifest_from_inventory(result: InventoryResult) -> list[ManifestEntry]:
    """Builds report-manifest entries from an inventory result by reusing
    the core ``evidence.manifest_entries`` mapping (one rule for every
    provider kind; deterministic: same sources in, same entries out)."""
    return manifest_entries(result.sources)
