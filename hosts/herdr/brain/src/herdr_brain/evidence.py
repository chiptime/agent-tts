"""Evidence core: source inventory, coverage outcomes, and budgeted
acquisition for on-demand context (FR-03, FR-04, FR-05, FR-11, FR-12,
FR-17, FR-20, FR-22, FR-40, FR-41; PRD decisions D02, D04, D06, D09).

Inert library: nothing here is wired into the server, the LLM loop, the
tool surface, or configuration. Stdlib only; ``now'' is always injected
(clock callables), never read implicitly.

Honesty rules implemented here:

- Source ABSENCE (the source does not exist; a valid empty result,
  FR-11/FR-22) and COVERAGE FAILURE (unreadable, corrupt, locked, timed
  out, or over budget, FR-11/FR-20) are two different ``CoverageStatus``
  values that can never be conflated: an exhaustively covered empty scope
  is OK (possibly with zero items), never a failure, and a failure always
  carries a non-empty ``error_detail``.
- A ``Deadline`` bounds acquisition (FR-17, D06). Providers MUST check it
  before each source read and report COVERAGE_FAILED (error detail
  mentioning the budget) instead of returning partial data. The clock is
  injectable so tests never sleep. A ``None`` deadline is unlimited.
- Every ``Source``/``EvidenceItem`` carries kind + source_id + locator so
  later layers can cite provenance (FR-40).
- ``EvidenceItem.timestamp`` is an aware UTC datetime or None when the
  time is genuinely unknown — it is NEVER substituted (e.g. with a file
  mtime) to force a verdict (FR-10); the dataclass has no mtime field at
  all.

Untrusted content (D07/D09): ``EvidenceItem.text`` and ``Source.state``
are DATA. They may quote agent output or terminal titles verbatim, and
must never be interpreted as instructions. This module adds no
instruction-following logic and no content filtering — isolation is the
consolidation layer's duty, with provenance preserved.

Revision semantics (D08, FR-31): a ``Source.revision_token`` is a
deterministic digest of the fields that define the source's current
content; it changes whenever any of them changes and is stable otherwise.
If a revision changes DURING acquisition, the caller must re-read within
the remaining budget or report unable-to-complete — this module only
makes the change detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, Protocol, runtime_checkable

from .herdr import AgentInfo, HerdrError
from .periods import Period
from .reportstore import ManifestEntry

SourceKind = Literal["herdr_session", "opencode", "claude", "antigravity", "engram"]

# Monotonic-style clock for deadlines: returns fractional seconds.
MonotonicClock = Callable[[], float]
# Wall clock for observation stamps: returns aware UTC datetimes.
UtcClock = Callable[[], datetime]


def _utc_now() -> datetime:
    """Default wall clock (aware UTC); tests inject their own."""
    return datetime.now(timezone.utc)


class CoverageStatus(Enum):
    """Outcome of checking or reading a source (FR-11, FR-22).

    The three values are deliberately pairwise distinct so absence can
    never be reported as failure (or as success-with-data):
    """

    #: Source was checked/read; zero items is a valid, complete result.
    OK = "ok"
    #: Source does not exist; also a valid empty outcome (no open
    #: sessions is OK for an inventory, a missing store is not a failure).
    SOURCE_ABSENT = "source_absent"
    #: Source could not be covered: unreadable, corrupt, locked, timed
    #: out, or budget exceeded. Always carries an error detail (FR-20).
    COVERAGE_FAILED = "coverage_failed"


class BudgetExceeded(RuntimeError):
    """The acquisition deadline passed (FR-17). Providers normally turn
    this into a COVERAGE_FAILED result; ``Deadline.check()`` raises it for
    callers that prefer exceptions at loop boundaries."""


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")


def _require_str(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


@dataclass(frozen=True)
class Source:
    """A discovered evidence source with provenance (FR-40).

    Attributes:
        source_id: stable, namespaced identifier (e.g. ``herdr:<pane>``).
        kind: one of the ``SourceKind`` literals.
        project: scope key as observed (for Herdr sessions, the pane cwd
            kept as-is; normalization to a project NAME is a later,
            explicit concern — never guessed here).
        locator: how to reach the source again (pane id, path, session id).
        revision_token: deterministic digest of the content-defining
            fields; changes when any of them changes (FR-31).
        observed_at: aware UTC instant of the observation behind this
            Source. Evidence corresponds to a recorded snapshot at a
            declared cutoff, not to a promise of continuous freshness.
        title: optional display title.
        state: free-form status snapshot (open/working/idle/blocked...)
            used later for report manifests; DATA, never instructions.
    """

    source_id: str
    kind: SourceKind
    project: str
    locator: str
    revision_token: str
    observed_at: datetime
    title: Optional[str] = None
    state: Optional[str] = None

    def __post_init__(self) -> None:
        _require_str(self.source_id, "source_id")
        _require_str(self.kind, "kind")
        _require_str(self.locator, "locator")
        _require_str(self.revision_token, "revision_token")
        _require_aware(self.observed_at, "observed_at")


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence with provenance (FR-40).

    ``timestamp`` is an aware UTC datetime, or None when the time is
    genuinely unknown — never a substitution such as a file mtime
    (FR-10). ``text`` is untrusted DATA and must never be executed or
    relayed as instructions (D07/D09).
    """

    source_id: str
    kind: SourceKind
    timestamp: Optional[datetime]
    role: str
    text: str
    message_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require_str(self.source_id, "source_id")
        _require_str(self.kind, "kind")
        _require_str(self.role, "role")
        if self.timestamp is not None:
            _require_aware(self.timestamp, "timestamp")


@dataclass(frozen=True)
class CoverageResult:
    """Outcome of collecting one source (FR-11, FR-20, FR-22).

    ``items`` is always a tuple; OK may legitimately carry zero items.
    COVERAGE_FAILED is impossible without a non-empty ``error_detail``
    so a failure can always be explained.
    """

    source: Source
    status: CoverageStatus
    items: tuple[EvidenceItem, ...] = ()
    error_detail: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            raise ValueError("items must be a tuple")
        if self.status is CoverageStatus.COVERAGE_FAILED:
            if not (self.error_detail and self.error_detail.strip()):
                raise ValueError(
                    "COVERAGE_FAILED requires a non-empty error_detail"
                )


@dataclass(frozen=True)
class InventoryResult:
    """Outcome of listing a provider's sources.

    A failed inventory (e.g. a Herdr CLI error) is COVERAGE_FAILED with
    an error detail; an empty-but-successful inventory (no open sessions)
    is OK with zero sources. The two are never conflated (FR-11).
    """

    status: CoverageStatus
    sources: tuple[Source, ...] = ()
    error_detail: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.sources, tuple):
            raise ValueError("sources must be a tuple")
        if self.status is CoverageStatus.COVERAGE_FAILED:
            if not (self.error_detail and self.error_detail.strip()):
                raise ValueError(
                    "COVERAGE_FAILED requires a non-empty error_detail"
                )


class Deadline:
    """Injectable acquisition budget over a monotonic clock (FR-17).

    Built either from an absolute monotonic instant or from remaining
    seconds; ``clock`` is supplied by the caller so tests never sleep.
    ``at=None`` (or ``from_remaining(clock, None)``) means unlimited.
    The deadline is expired when ``clock() >= at``: at the deadline
    instant no time remains. ``remaining()`` is None when unlimited.
    """

    def __init__(self, clock: MonotonicClock, at: Optional[float] = None) -> None:
        self._clock = clock
        self._at = at

    @classmethod
    def from_remaining(
        cls, clock: MonotonicClock, seconds: Optional[float]
    ) -> "Deadline":
        """Deadline located ``seconds`` from the clock's current reading."""
        if seconds is None:
            return cls(clock, None)
        return cls(clock, clock() + seconds)

    @property
    def at(self) -> Optional[float]:
        """Absolute monotonic instant, or None when unlimited."""
        return self._at

    def remaining(self) -> Optional[float]:
        """Seconds left, or None when there is no deadline."""
        if self._at is None:
            return None
        return self._at - self._clock()

    def expired(self) -> bool:
        return self._at is not None and self._clock() >= self._at

    def check(self) -> None:
        """Raises ``BudgetExceeded`` when past the deadline."""
        if self.expired():
            raise BudgetExceeded(f"budget exceeded: deadline {self._at} reached")


@runtime_checkable
class EvidenceProvider(Protocol):
    """The read-only adapter contract every evidence source implements.

    Providers never mutate anything (FR-38) and report coverage outcomes
    instead of raising through the acquisition boundary: a failed source
    becomes a COVERAGE_FAILED result so the caller can decide between
    retry-within-budget and unable-to-complete (FR-20).
    """

    kind: SourceKind

    def inventory(
        self,
        project_filter: Optional[str] = None,
        deadline: Optional[Deadline] = None,
    ) -> InventoryResult:
        """Lists the provider's sources; empty-but-OK means none exist."""

    def collect(
        self,
        source: Source,
        period: Optional[Period] = None,
        deadline: Optional[Deadline] = None,
    ) -> CoverageResult:
        """Reads one source; period bounds evidence when the source has
        history (ignored by snapshot-only providers)."""


# Statuses that count as ACTIVE for date-scoped current progress (FR-12,
# D04). working/blocked/waiting are in; idle/done/unknown are not — an
# open session is never inferred "unfinished" (FR-04), it simply is not
# part of the active-only view. This set is a POLICY RULE the
# orchestration layer may revisit; it lives here as one named constant
# so there is exactly one place to change it.
ACTIVE_STATUSES = frozenset({"working", "blocked", "waiting"})


def _revision_token(info: AgentInfo) -> str:
    """Deterministic digest of the fields that define a session's current
    content (status, session id, focus, title).

    Any field change yields a different token; nothing changing yields
    the same token across calls, providers, and process restarts (the
    inputs are JSON-encoded unambiguously before hashing). observed_at is
    deliberately NOT part of the token: a re-observation at a later time
    with identical content is not a revision change (FR-31).
    """
    payload = json.dumps(
        [info.status, info.session_value, bool(info.focused), info.title]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cwd_matches(cwd: str, project_filter: Optional[str]) -> bool:
    """project_filter matching rule: a session matches when its cwd EQUALS
    the filter or lives UNDER it as a subdirectory path component.
    ``/repo`` matches ``/repo`` and ``/repo/sub`` but never ``/repo2``."""
    if project_filter is None:
        return True
    if cwd == project_filter:
        return True
    prefix = project_filter if project_filter.endswith(os.sep) else project_filter + os.sep
    return cwd.startswith(prefix)


class HerdrSessionProvider:
    """Evidence provider over ALL open Herdr agent sessions (FR-04, D02).

    ``inventory`` returns every open session regardless of status —
    working, idle, waiting, blocked, done, unknown — reporting the status
    truthfully via ``Source.state``; "open" never means "unfinished".
    ``Source.project`` is the pane cwd KEPT AS-IS: normalization to a
    project name is a later, explicit concern (this provider never
    guesses one). ``Source.locator`` is the pane id and ``source_id`` is
    the namespaced ``herdr:<pane_id>``.

    FR-12 scoping: the DEFAULT inventory is the unqualified one (all open
    sessions, including old last activity). Date-scoped current progress
    must instead ask for ``active_only=True``, which keeps only
    ``ACTIVE_STATUSES`` sessions.

    Failure honesty (FR-11/FR-20): a ``HerdrError`` from the CLI becomes
    an InventoryResult with COVERAGE_FAILED and the error text — never an
    exception, never an empty-OK. An expired deadline is detected BEFORE
    listing and also yields COVERAGE_FAILED (error detail mentions the
    budget) rather than partial data.

    Herdr panes carry no message history: ``collect`` returns a single
    status-snapshot item whose timestamp is the observation instant.
    Historical evidence comes from transcript providers (later slices);
    the ``period`` argument is accepted for protocol compatibility and
    deliberately ignored here.
    """

    kind: SourceKind = "herdr_session"

    def __init__(self, client, *, clock: Optional[UtcClock] = None) -> None:
        self._client = client
        self._clock: UtcClock = clock if clock is not None else _utc_now

    def _source_from_agent(self, info: AgentInfo, observed_at: datetime) -> Source:
        return Source(
            source_id=f"herdr:{info.pane_id}",
            kind=self.kind,
            project=info.cwd,
            locator=info.pane_id,
            revision_token=_revision_token(info),
            observed_at=observed_at,
            title=info.title or info.agent or None,
            state=info.status,
        )

    def inventory(
        self,
        project_filter: Optional[str] = None,
        deadline: Optional[Deadline] = None,
        *,
        active_only: bool = False,
    ) -> InventoryResult:
        """Lists open sessions as Sources (see class docstring for the
        status-truthfulness and FR-12 scoping rules)."""
        if deadline is not None and deadline.expired():
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before listing herdr sessions",
            )
        try:
            agents = self._client.list_agents()
        except HerdrError as exc:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED, error_detail=str(exc)
            )
        observed_at = self._clock()
        sources = tuple(
            self._source_from_agent(info, observed_at)
            for info in agents
            if (not active_only or info.status in ACTIVE_STATUSES)
            and _cwd_matches(info.cwd, project_filter)
        )
        return InventoryResult(CoverageStatus.OK, sources)

    def collect(
        self,
        source: Source,
        period: Optional[Period] = None,
        deadline: Optional[Deadline] = None,
    ) -> CoverageResult:
        """Returns OK with one status-snapshot EvidenceItem (role
        ``status``) describing the session at observation time."""
        if deadline is not None and deadline.expired():
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail="budget exceeded before reading herdr session"
                f" {source.source_id}",
            )
        observed_at = self._clock()
        item = EvidenceItem(
            source_id=source.source_id,
            kind=self.kind,
            timestamp=observed_at,
            role="status",
            text=f"{source.title or source.kind} {source.state} in {source.project}",
        )
        return CoverageResult(source, CoverageStatus.OK, (item,))


def manifest_entries(sources: Iterable[Source]) -> list:
    """Builds report-manifest entries from Sources (for T5 freshness
    comparison).

    Each Source maps to one ``reportstore.ManifestEntry``
    (``source_id``, ``revision_token``, ``state``); a missing ``state``
    becomes the empty string because the manifest schema requires a
    string. Deterministic: same sources in, same entries out.
    """
    return [
        ManifestEntry(
            source_id=src.source_id,
            revision_token=src.revision_token,
            state=src.state or "",
        )
        for src in sources
    ]
