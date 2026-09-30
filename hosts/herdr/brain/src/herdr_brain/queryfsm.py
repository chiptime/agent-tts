"""Query FSM engine: the deterministic runtime behind the PRD's Query
FSM table (``docs/prds/herdr-brain-on-demand-context.md``; FR-01..03,
FR-17, FR-19..22, FR-26, FR-28..33, FR-37; decisions D02, D04, D06,
D08).

ARCHITECTURAL POSITION: the LLM/tool layer (T9) classifies a voice
query into a structured ``QueryIntent`` and supplies the main-call
context id; THIS module is the deterministic engine that then enforces
the product invariants end to end:

- ONE wall-clock budget (default 60s) from acceptance to publication,
  never reset per step (FR-17, D06): a ``Deadline`` is created from
  the injected monotonic clock at entry and every phase checks it;
  expiry anywhere -> ``unable_to_complete`` naming the phase, NEVER a
  partial render (FR-19).
- FOCUS IS NOT THIS ENGINE'S JOB (FR-01/FR-02, D01): focus queries
  are served by the existing selected-session conversation loop, so
  ``handle`` returns a ``focus_rejected`` routing signal WITHOUT
  touching providers or the store — the engine must never acquire for
  them (false-routing guard).
- Periods resolve through ``periods`` in the USER's timezone
  (FR-08/FR-37): natural kinds via ``period_*``; explicit bounds via
  ``explicit_period`` + ``validate_bounds`` (span capped at
  ``EngineDeps.max_span``, default 60 days — natural kinds are
  inherently bounded and unaffected); a ``TimezoneUnavailable``
  becomes the terminal ``ask_tz`` state (never a guess, FR-09) and a
  ``PeriodError`` becomes ``clarify`` (bounded explicit periods).
- Freshness FIRST (FR-26/FR-28): every query runs each configured
  provider's inventory under the SAME ``Deadline``, assembles the
  provider results, and delegates to ``FreshnessChecker.evaluate``.
  ``Reuse`` serves the stored report only after that successful check;
  ``Unable`` demotes a prior published report via
  ``mark_refresh_failed`` (FR-33: a stale report is never current
  after a failed refresh) and reports ``unable_to_complete``.
- Acquisition is all-or-nothing (FR-20): ``plan_rebuild`` (always a
  bounded FULL scan, FR-30/FR-32) drives per-source
  ``provider.collect`` under the remaining deadline; ANY
  COVERAGE_FAILED source aborts to ``unable_to_complete`` naming the
  source. Absent/empty sources are NOT failures (FR-22).
- Volume honesty (FR-21): item-count and total-character thresholds
  (both PROVISIONAL values owned by T9/product — parameters, never
  inline magic constants) trip a deterministic ``narrow_ask`` that
  mentions the counts. The engine NEVER truncates anything itself.
- FR-12 inventory scoping (D04): a ``global`` intent WITH a period
  spec is date-scoped current progress and restricts the herdr
  provider to ``inventory(active_only=True)`` (ONLY the herdr
  provider has this flag); an unqualified ``global`` intent (period
  None) keeps the default inventory over ALL open sessions including
  idle ones. ``historical`` intents never restrict by activity.
- Consolidation via an INJECTED summarizer callable (tests inject a
  fake; the real LLM summarizer lands in T9): the engine builds the
  ``ReportScaffold``, calls ``summarizer(scaffold, period)``,
  validates the result with ``validate_brief`` (ungrounded briefs are
  never published, FR-15), and double-checks the spoken artifact with
  ``assert_no_references`` before publishing (FR-14).
- Publication is atomic through ``ReportStore``: ``begin_build`` ->
  ``publish`` with body = the serialized ``BriefDocument`` plus the
  captured screen artifact, references built from the scaffold, and
  the manifest from the freshness step. A ``StaleBuildError`` means a
  superseded request: ``unable_to_complete``, never retried (PRD
  Concurrency).

The engine NEVER touches the Herdr selected session: it holds only
providers (read-only adapters), the store, and injected callables —
nothing in this module can send to a pane, asserted by construction in
tests with a spy client.

STORED BODY FORMAT (decision): ``body`` is a JSON object
``{"version": 1, "brief": <BriefDocument fields>, "screen": str}``.
The brief half round-trips through ``brief_to_json``/``brief_from_json``
so the REUSE path re-renders the spoken artifact from the stored
document (``render_spoken`` is deterministic) and serves the captured
``screen`` verbatim — the screen needs the scaffold (per-project
references, coverage ledger), which is not stored, so it is captured
at publish time instead of being rebuilt from lossy parts.

Determinism: stdlib only; no ``datetime.now``/``time.sleep``/network/
subprocess anywhere; every clock is injected through ``EngineDeps``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Optional, Union
from zoneinfo import ZoneInfo

from .evidence import (
    CoverageStatus,
    Deadline,
    InventoryResult,
    Source,
    manifest_entries,
)
from .freshness import Rebuild, Reuse, Unable, plan_rebuild
from .periods import (
    Period,
    PeriodError,
    TimezoneUnavailable,
    explicit_period,
    period_last_7_days,
    period_this_week,
    period_today,
    resolve_timezone,
    validate_bounds,
)
from .report import (
    BriefDocument,
    BriefSection,
    ReportScaffold,
    assert_no_references,
    build_references,
    build_scaffold,
    render_screen,
    render_spoken,
    validate_brief,
)
from .reportstore import ReportKey, StaleBuildError

__all__ = [
    "DEFAULT_BUDGET_SECONDS",
    "DEFAULT_MAX_SPAN",
    "DEFAULT_NARROW_CHAR_THRESHOLD",
    "DEFAULT_NARROW_ITEM_THRESHOLD",
    "EngineDeps",
    "EngineResult",
    "ExplicitPeriod",
    "HERDR_SESSION_KIND",
    "NaturalPeriod",
    "QueryEngine",
    "QueryIntent",
    "UNQUALIFIED_INTERVAL_KEY",
    "UNQUALIFIED_TIMEZONE",
    "brief_from_json",
    "brief_to_json",
    "interval_label",
    "timezone_label",
]

#: The one and only provider kind that supports ``active_only`` on
#: inventory (FR-12). Only the herdr session provider has the flag;
#: the engine never passes it to any other provider.
HERDR_SESSION_KIND = "herdr_session"

#: Budget default (FR-17, D06): one 60-second wall-clock envelope from
#: acceptance to publication; audio rendering is outside it.
DEFAULT_BUDGET_SECONDS = 60.0

#: PROVISIONAL volume-guidance defaults (FR-21). The VALUES belong to
#: T9/product tuning, not to this engine: they are exposed as
#: ``EngineDeps`` parameters so wiring can change them without touching
#: engine logic, and they are never inlined as magic constants.
DEFAULT_NARROW_ITEM_THRESHOLD = 4000
DEFAULT_NARROW_CHAR_THRESHOLD = 500_000

#: Maximum span for EXPLICIT periods (product decision 2026-09-30):
#: explicit bounds may cover at most 60 days; a wider span raises
#: ``PeriodError`` and surfaces the existing ``clarify`` state (FR-08/
#: FR-37; D04 "explicit periods stay bounded"). Natural periods are
#: inherently bounded by their own resolution (today / this week /
#: last 7 days) and never hit this cap.
DEFAULT_MAX_SPAN = timedelta(days=60)

#: Stable interval key for unqualified current progress (period None).
#: The report store requires a parseable ``Period.key``-shaped string
#: (``kind|start|end|zone``), so the sentinel uses the epoch as a
#: degenerate zero-width instant: unqualified current progress is a
#: SNAPSHOT whose content is fully captured by the manifest revision
#: tokens, so reuse-after-check is keyed on the manifest alone and two
#: unqualified queries never differ by "window movement".
UNQUALIFIED_INTERVAL_KEY = (
    "unqualified|1970-01-01T00:00:00+00:00|1970-01-01T00:00:00+00:00|unqualified"
)

#: Report-key timezone component for unqualified queries: no timezone
#: is guessed or required because no time math is involved (FR-09's
#: ask applies to period resolution, not to snapshots).
UNQUALIFIED_TIMEZONE = "unqualified"

QueryKind = Literal["focus", "global", "historical"]
EngineState = Literal[
    "rendered",
    "unable_to_complete",
    "narrow_ask",
    "ask_tz",
    "clarify",
    "focus_rejected",
]

_NATURAL_KINDS = ("today", "this_week", "last_7_days")
_NATURAL_INTERVAL_LABELS = {
    "today": "today",
    "this_week": "this week",
    "last_7_days": "the last 7 days",
}
_UNQUALIFIED_INTERVAL_LABEL = "current work"

# Deterministic terminal-state phrasing (never contains references).
SPOKEN_UNABLE = (
    "I could not complete the report. Nothing partial will be presented;"
    " please try again or narrow the request."
)
SPOKEN_ASK_TZ = (
    "I need your timezone to answer time-scoped questions."
    " Which timezone should I use?"
)


def _require_str(value: object, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a non-empty string")


def _require_str_tuple(value: object, label: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{label} must be a tuple")
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"{label} entries must be non-empty strings")


@dataclass(frozen=True)
class NaturalPeriod:
    """Period spec for a natural language kind (FR-08, D04).

    The engine resolves it through ``periods.period_*`` in the USER's
    timezone at query time; the spec itself carries no instants.
    """

    kind: Literal["today", "this_week", "last_7_days"]

    def __post_init__(self) -> None:
        if self.kind not in _NATURAL_KINDS:
            raise ValueError(
                f"kind must be one of {_NATURAL_KINDS}, got {self.kind!r}"
            )


@dataclass(frozen=True)
class ExplicitPeriod:
    """Period spec with explicit bounds (FR-08/FR-37: bounded exactly,
    never clamped).

    Accepts aware datetimes (exact instants) or naive datetimes/dates
    (interpreted in the resolved user zone). Validation happens at
    resolution time via ``periods.explicit_period`` +
    ``validate_bounds``; a ``PeriodError`` becomes a ``clarify``
    outcome with the reason. The span is capped at
    ``EngineDeps.max_span`` (default 60 days) — natural period specs
    never carry this concern because their kinds are inherently
    bounded.
    """

    start: Union[datetime, date]
    end: Union[datetime, date]

    def __post_init__(self) -> None:
        for name in ("start", "end"):
            if not isinstance(getattr(self, name), (datetime, date)):
                raise ValueError(
                    f"{name} must be a datetime or date, got"
                    f" {type(getattr(self, name)).__name__}"
                )


PeriodSpec = Union[NaturalPeriod, ExplicitPeriod, None]


@dataclass(frozen=True)
class QueryIntent:
    """The classified query the LLM/tool layer (T9) hands the engine.

    Attributes:
        kind: ``focus`` (served by the existing conversation loop and
            REJECTED by this engine), ``global`` (current progress —
            unqualified or date-scoped), or ``historical``.
        raw_text: the original utterance, diagnostics only.
        projects: scope tuple; empty means ALL projects (FR-03).
            At most ONE project is supported per query (providers and
            the scaffold take a single filter); a multi-project tuple
            is a structural error T9 must split into per-project
            queries.
        period_spec: ``NaturalPeriod``, ``ExplicitPeriod``, or None.
            None is valid ONLY for ``kind="global"`` (unqualified
            current progress, D04/FR-12).
        timezone_candidates: ordered IANA candidates validated by
            ``periods.resolve_timezone``; empty means "cannot detect"
            and yields ``ask_tz`` for period-scoped queries.
    """

    kind: QueryKind
    raw_text: str
    projects: tuple[str, ...]
    period_spec: PeriodSpec
    timezone_candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in ("focus", "global", "historical"):
            raise ValueError(
                f"kind must be 'focus', 'global', or 'historical', got {self.kind!r}"
            )
        _require_str(self.raw_text, "raw_text")
        _require_str_tuple(self.projects, "projects")
        if len(self.projects) > 1:
            raise ValueError(
                "projects must carry at most one project per query (single"
                " filter per provider/scaffold); split multi-project queries"
                " into one engine call per project (T9 concern)"
            )
        _require_str_tuple(self.timezone_candidates, "timezone_candidates")
        if not isinstance(
            self.period_spec, (NaturalPeriod, ExplicitPeriod)
        ) and self.period_spec is not None:
            raise ValueError(
                "period_spec must be a NaturalPeriod, an ExplicitPeriod, or"
                f" None, got {type(self.period_spec).__name__}"
            )
        if self.period_spec is None and self.kind != "global":
            raise ValueError(
                "period_spec=None is only valid for kind='global' (unqualified"
                " current progress, D04/FR-12); historical queries need one"
            )


@dataclass(frozen=True)
class EngineResult:
    """The engine's terminal outcome for one handled query.

    ``detail`` is a diagnostic string that is NEVER spoken; ``spoken``
    and ``screen`` are the render-ready artifacts (for every state but
    ``focus_rejected``, which is a routing signal for the caller with
    empty artifacts by design). ``references`` feeds the on-screen
    reference surface (T10). ``report_id`` is set only when a stored
    report backs the result (fresh publish or reuse-after-check).
    """

    state: EngineState
    spoken: str
    screen: str
    report_id: Optional[str]
    interval_label: str
    timezone_label: str
    detail: str
    references: tuple[Reference, ...] = ()


def _default_zone_resolver(candidates: tuple[str, ...]) -> ZoneInfo:
    """Wraps ``periods.resolve_timezone`` (never guesses; raises
    ``TimezoneUnavailable`` when no candidate resolves)."""
    return resolve_timezone(candidates)


@dataclass(frozen=True)
class EngineDeps:
    """Everything the engine needs, fully injected (no implicit
    clocks, no hidden providers). Tests use fakes with the same method
    shapes; production (T9) passes the real classes plus the real LLM
    summarizer.

    Attributes:
        providers: kind -> evidence provider (read-only adapters).
        store: the persistent report store.
        checker: the freshness checker over the same store.
        summarizer: consolidation callable
            ``(ReportScaffold, Period|None) -> BriefDocument``. MUST
            fill ``interval_label``/``timezone_label`` from this
            module's helpers so the reuse and rebuild paths agree, and
            MUST carry the incompleteness note when the scaffold is
            incomplete (``validate_brief`` enforces it).
        zone_resolver: candidates -> ``ZoneInfo``; raises
            ``TimezoneUnavailable`` (default wraps
            ``periods.resolve_timezone``).
        clock: wall clock returning aware UTC datetimes.
        monotonic: monotonic clock returning fractional seconds.
        budget_seconds: the single wall-clock envelope (FR-17).
        narrow_item_threshold / narrow_char_threshold: PROVISIONAL
            volume guidance (FR-21) — values owned by T9/product.
        max_span: the span cap for EXPLICIT periods (default 60 days,
            product decision 2026-09-30). Passed to
            ``periods.validate_bounds`` on the explicit resolution
            path only; natural periods are inherently bounded (at most
            a week back) and are never compared against it.
    """

    providers: Mapping[str, object]
    store: object
    checker: object
    summarizer: Callable[[ReportScaffold, Optional[Period]], BriefDocument]
    clock: Callable[[], datetime]
    monotonic: Callable[[], float]
    budget_seconds: float = DEFAULT_BUDGET_SECONDS
    narrow_item_threshold: int = DEFAULT_NARROW_ITEM_THRESHOLD
    narrow_char_threshold: int = DEFAULT_NARROW_CHAR_THRESHOLD
    zone_resolver: Callable[[tuple[str, ...]], ZoneInfo] = _default_zone_resolver
    max_span: timedelta = DEFAULT_MAX_SPAN

    def __post_init__(self) -> None:
        if not isinstance(self.providers, Mapping) or not self.providers:
            raise ValueError(
                "providers must be a non-empty Mapping of kind -> provider"
            )
        for name in ("summarizer", "clock", "monotonic", "zone_resolver"):
            if not callable(getattr(self, name)):
                raise ValueError(f"{name} must be callable")
        if not isinstance(self.budget_seconds, (int, float)) or isinstance(
            self.budget_seconds, bool
        ):
            raise ValueError("budget_seconds must be a number")
        if math.isnan(self.budget_seconds) or self.budget_seconds <= 0:
            raise ValueError("budget_seconds must be positive")
        for name in ("narrow_item_threshold", "narrow_char_threshold"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be an int >= 1")
        if not isinstance(self.max_span, timedelta) or self.max_span <= timedelta(0):
            raise ValueError("max_span must be a positive timedelta")
        configured = getattr(self.checker, "configured_kinds", None)
        if configured is not None and set(configured) != set(self.providers):
            raise ValueError(
                "checker configured_kinds must match the providers mapping:"
                f" checker has {sorted(configured)}, providers has"
                f" {sorted(self.providers)} (FR-41 authority)"
            )


# ---------------------------------------------------------------------------
# Display labels (deterministic; shared by engine and summarizers)
# ---------------------------------------------------------------------------


def interval_label(period: Optional[Period]) -> str:
    """Deterministic spoken-safe interval label for a resolved Period
    (or None for unqualified current progress)."""
    if period is None:
        return _UNQUALIFIED_INTERVAL_LABEL
    natural = _NATURAL_INTERVAL_LABELS.get(period.kind)
    if natural is not None:
        return natural
    return (
        f"{period.local_start:%Y-%m-%d %H:%M} to"
        f" {period.local_end:%Y-%m-%d %H:%M}"
    )


def timezone_label(period: Optional[Period]) -> str:
    """Deterministic timezone label: the resolved IANA zone name, or
    the unqualified sentinel when no period (and thus no zone) exists."""
    if period is None:
        return UNQUALIFIED_TIMEZONE
    return period.zone_name


# ---------------------------------------------------------------------------
# BriefDocument serialization (stored-body format)
# ---------------------------------------------------------------------------

_BRIEF_FIELDS = (
    "context_id",
    "interval_label",
    "timezone_label",
    "sections",
    "generated_note",
)
_SECTION_TUPLE_FIELDS = (
    "advances",
    "pending",
    "blockers",
    "conflict_notes",
    "citations",
)
_SECTION_FIELDS = ("project", "no_work") + _SECTION_TUPLE_FIELDS
_BODY_VERSION = 1


def brief_to_json(doc: BriefDocument) -> str:
    """Serializes a ``BriefDocument`` to deterministic JSON (sorted
    keys; sections keep their order). Round-trips through
    ``brief_from_json`` with equality."""
    payload = {
        "context_id": doc.context_id,
        "interval_label": doc.interval_label,
        "timezone_label": doc.timezone_label,
        "sections": [
            {"project": section.project, "no_work": section.no_work}
            | {name: list(getattr(section, name)) for name in _SECTION_TUPLE_FIELDS}
            for section in doc.sections
        ],
        "generated_note": doc.generated_note,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _brief_from_payload(payload: object) -> BriefDocument:
    """Validates a decoded payload against the exact brief schema and
    builds the frozen document (extra/missing fields fail loudly)."""
    if not isinstance(payload, dict):
        raise ValueError(f"brief payload must be a JSON object, got {type(payload).__name__}")
    if set(payload) != set(_BRIEF_FIELDS):
        raise ValueError(
            f"brief payload fields must be exactly {list(_BRIEF_FIELDS)},"
            f" got {sorted(payload)}"
        )
    sections = payload["sections"]
    if not isinstance(sections, list):
        raise ValueError("brief payload sections must be a list")
    built = []
    for raw in sections:
        if not isinstance(raw, dict) or set(raw) != set(_SECTION_FIELDS):
            raise ValueError(
                f"brief section fields must be exactly {list(_SECTION_FIELDS)},"
                f" got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
            )
        built.append(
            BriefSection(
                project=raw["project"],
                advances=tuple(raw["advances"]),
                pending=tuple(raw["pending"]),
                blockers=tuple(raw["blockers"]),
                no_work=raw["no_work"],
                conflict_notes=tuple(raw["conflict_notes"]),
                citations=tuple(raw["citations"]),
            )
        )
    return BriefDocument(
        context_id=payload["context_id"],
        interval_label=payload["interval_label"],
        timezone_label=payload["timezone_label"],
        sections=tuple(built),
        generated_note=payload["generated_note"],
    )


def brief_from_json(raw: str) -> BriefDocument:
    """Rebuilds a ``BriefDocument`` from ``brief_to_json`` output.

    Raises ``ValueError`` for anything malformed (bad JSON, wrong
    shape, extra/missing fields, non-string values) — a corrupt stored
    body must fail loudly, never silently degrade into a wrong report.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stored brief is not valid JSON: {exc}") from exc
    return _brief_from_payload(payload)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def _encode_body(doc: BriefDocument, screen: str) -> str:
    """The stored report body: versioned wrapper of the serialized
    brief plus the captured screen artifact (see module docstring)."""
    return json.dumps(
        {
            "version": _BODY_VERSION,
            "brief": json.loads(brief_to_json(doc)),
            "screen": screen,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _decode_body(raw: str) -> tuple[BriefDocument, str]:
    """Parses the stored body; ``ValueError`` on any mismatch."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stored report body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "brief",
        "screen",
    }:
        raise ValueError(
            "stored report body must be a {version, brief, screen} object"
        )
    if payload["version"] != _BODY_VERSION:
        raise ValueError(
            f"stored report body version {payload['version']!r} is not"
            f" supported (expected {_BODY_VERSION})"
        )
    if not isinstance(payload["screen"], str):
        raise ValueError("stored report body screen must be a string")
    return _brief_from_payload(payload["brief"]), payload["screen"]


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class QueryEngine:
    """Deterministic executor of classified global/historical queries.

    ``handle`` is the ONLY entry point. Each numbered phase matches the
    PRD's Query FSM table and checks the shared budget deadline
    (FR-17); expiry anywhere is ``unable_to_complete`` naming the
    phase, never a partial render (FR-19):

    a. ACCEPT — focus intents return ``focus_rejected`` immediately,
       touching nothing (no clock reads, no providers, no store).
    b. RESOLVE_PERIOD — zone from ``zone_resolver``
       (TimezoneUnavailable -> ``ask_tz``, never a guess, FR-09);
       natural kinds via ``periods.period_*``; explicit bounds via
       ``explicit_period`` + ``validate_bounds`` (PeriodError ->
       ``clarify`` with the reason, FR-08/FR-37); unqualified global
       keeps period None with the sentinel key components.
    c. FRESHNESS — every configured provider's inventory under ONE
       ``Deadline``; ``checker.evaluate`` decides Reuse (serve the
       stored report, re-rendered from the stored body) / Rebuild /
       Unable (demote a prior published report via
       ``mark_refresh_failed``, FR-33, then unable-to-complete).
    d. ACQUIRE — ``plan_rebuild`` (always a bounded FULL scan,
       FR-30/FR-32) -> per-source ``provider.collect`` under the
       remaining deadline; ANY COVERAGE_FAILED source aborts naming
       the source (FR-20); absent/empty is NOT failure (FR-22).
    e. VOLUME — item and character totals over the provisional
       thresholds -> deterministic ``narrow_ask`` mentioning the
       counts; nothing is truncated, nothing published (FR-21).
    f. CONSOLIDATE — ``build_scaffold``; ``summarizer(scaffold,
       period)``; ``validate_brief``; a failed or ungrounded brief is
       never published (FR-15).
    g. PUBLISH + RENDER — the FR-14 spoken-leak double-check runs
       BEFORE publication, then atomic ``begin_build``/``publish``
       (body = serialized brief + captured screen; references from
       the scaffold; manifest from the freshness union). A
       ``StaleBuildError`` means a superseded request:
       unable-to-complete, never retried.
    """

    def __init__(self, deps: EngineDeps) -> None:
        if not isinstance(deps, EngineDeps):
            raise ValueError(f"deps must be an EngineDeps, got {type(deps).__name__}")
        self._deps = deps

    @property
    def deps(self) -> EngineDeps:
        return self._deps

    def handle(self, intent: QueryIntent, main_call_context_id: str) -> EngineResult:
        """Runs the FSM for one classified query."""
        if not isinstance(intent, QueryIntent):
            raise ValueError(
                f"intent must be a QueryIntent, got {type(intent).__name__}"
            )
        _require_str(main_call_context_id, "main_call_context_id")

        # (a) ACCEPT — focus is a false-routing guard, not an outcome:
        # the existing conversation loop serves it (FR-01/FR-02).
        if intent.kind == "focus":
            return EngineResult(
                state="focus_rejected",
                spoken="",
                screen="",
                report_id=None,
                interval_label="",
                timezone_label="",
                detail=(
                    "focus queries are served by the existing selected-session"
                    " conversation loop (FR-01/FR-02); the engine never"
                    " acquires for them"
                ),
            )

        deps = self._deps
        deadline = Deadline(
            deps.monotonic, at=deps.monotonic() + deps.budget_seconds
        )
        labels = _Labels()
        expired = self._budget_expired(deadline, "accept", labels)
        if expired is not None:
            return expired

        # (b) RESOLVE_PERIOD — TimezoneUnavailable -> ask_tz (never a
        # guess, FR-09); PeriodError -> clarify with the reason
        # (bounded explicit periods, FR-08/FR-37).
        try:
            period = self._resolve_period(intent, labels)
        except TimezoneUnavailable as exc:
            return _terminal(
                "ask_tz",
                SPOKEN_ASK_TZ,
                SPOKEN_ASK_TZ,
                f"timezone unavailable (never guessed, FR-09): {exc}",
                labels,
            )
        except PeriodError as exc:
            spoken = (
                f"The requested period is invalid: {exc} Please restate the"
                " period."
            )
            return _terminal("clarify", spoken, spoken, str(exc), labels)
        labels.period = period
        expired = self._budget_expired(deadline, "freshness", labels)
        if expired is not None:
            return expired
        key = self._report_key(intent, period, main_call_context_id)

        # (c) FRESHNESS
        provider_results, source_owner = self._run_inventories(
            intent, period, deadline
        )
        outcome = deps.checker.evaluate(
            key,
            period.key if period is not None else UNQUALIFIED_INTERVAL_KEY,
            provider_results,
        )
        expired = self._budget_expired(deadline, "acquire", labels)
        if expired is not None:
            return expired

        if isinstance(outcome, Reuse):
            return self._serve_reused(outcome, key, labels)
        if isinstance(outcome, Unable):
            # A failed inventory makes reuse unverifiable AND rebuild
            # dishonest: unable-to-complete, never a stored fallback.
            detail = "freshness check failed (FR-20): " + "; ".join(
                f"provider {kind!r}: {error}"
                for kind, error in outcome.failed_providers
            )
            self._mark_refresh_failed(key)
            return _terminal(
                "unable_to_complete", SPOKEN_UNABLE, SPOKEN_UNABLE, detail, labels
            )
        assert isinstance(outcome, Rebuild)

        # (d) ACQUIRE — always a bounded full scan (FR-30/FR-32); ANY
        # COVERAGE_FAILED source aborts (FR-20); absent/empty is NOT
        # failure (FR-22).
        plan = plan_rebuild(outcome.diff, deadline=deadline)
        collect_results = []
        for source_id in plan.sources_to_read:
            expired = self._budget_expired(deadline, "acquire", labels)
            if expired is not None:
                return expired
            provider, source = source_owner[source_id]
            result = provider.collect(source, period, deadline)
            if result.status is CoverageStatus.COVERAGE_FAILED:
                detail = (
                    f"source {source.source_id} coverage failed (FR-20):"
                    f" {result.error_detail}; nothing published"
                )
                self._mark_refresh_failed(key)
                return _terminal(
                    "unable_to_complete", SPOKEN_UNABLE, SPOKEN_UNABLE, detail, labels
                )
            collect_results.append(result)
        # A deadline that died DURING the last read is still an
        # acquire-phase death (named honestly), never a partial
        # consolidation over possibly-stale reads.
        expired = self._budget_expired(deadline, "acquire", labels)
        if expired is not None:
            return expired

        # (e) VOLUME — advisory thresholds, never truncation (FR-21).
        total_items = sum(len(result.items) for result in collect_results)
        total_chars = sum(
            len(item.text) for result in collect_results for item in result.items
        )
        if (
            total_items > deps.narrow_item_threshold
            or total_chars > deps.narrow_char_threshold
        ):
            spoken = (
                f"This request covers too much material to summarize honestly:"
                f" {total_items} items and {total_chars} characters of source"
                " text. Please narrow the dates or projects."
            )
            screen = (
                f"{spoken}\n(Limits: {deps.narrow_item_threshold} items,"
                f" {deps.narrow_char_threshold} characters. Nothing was"
                " truncated.)"
            )
            detail = (
                f"volume over threshold: {total_items} items (limit"
                f" {deps.narrow_item_threshold}), {total_chars} chars (limit"
                f" {deps.narrow_char_threshold}); nothing published, nothing"
                " truncated (FR-21)"
            )
            return _terminal("narrow_ask", spoken, screen, detail, labels)

        # (f) CONSOLIDATE — validate BEFORE any publication; a failed
        # or ungrounded brief is never published (FR-15).
        expired = self._budget_expired(deadline, "consolidate", labels)
        if expired is not None:
            return expired
        project_filter = intent.projects[0] if intent.projects else None
        scaffold = build_scaffold(
            provider_results, collect_results, project_filter=project_filter
        )
        try:
            doc = deps.summarizer(scaffold, period)
        except Exception as exc:  # the summarizer is an injected boundary
            return _terminal(
                "unable_to_complete",
                SPOKEN_UNABLE,
                SPOKEN_UNABLE,
                f"summarizer failed: {type(exc).__name__}: {exc}; nothing"
                " published",
                labels,
            )
        validation = validate_brief(doc, scaffold)
        if not validation.ok:
            detail = (
                "brief validation failed (FR-15; never publish ungrounded): "
                + "; ".join(validation.errors)
                + "; nothing published"
            )
            return _terminal(
                "unable_to_complete", SPOKEN_UNABLE, SPOKEN_UNABLE, detail, labels
            )

        # (g) PUBLISH + RENDER — the FR-14 leak double-check runs BEFORE
        # any publication so a leaking spoken artifact never lands in
        # the store.
        spoken = render_spoken(doc)
        screen = render_screen(doc, scaffold)
        source_ids = [
            source.source_id
            for bundle in scaffold.bundles
            for source in bundle.sources
        ]
        if not assert_no_references(spoken, source_ids):
            self._mark_refresh_failed(key)
            return _terminal(
                "unable_to_complete",
                SPOKEN_UNABLE,
                SPOKEN_UNABLE,
                "spoken artifact leaked a reference (FR-14); nothing"
                " published",
                labels,
            )
        expired = self._budget_expired(deadline, "publish", labels)
        if expired is not None:
            return expired
        current_manifest = manifest_entries(
            source
            for result in provider_results.values()
            for source in result.sources
        )
        references = build_references(
            source for bundle in scaffold.bundles for source in bundle.sources
        )
        handle = deps.store.begin_build(key)
        try:
            published = deps.store.publish(
                handle,
                body=_encode_body(doc, screen),
                references=references,
                source_manifest=current_manifest,
            )
        except StaleBuildError as exc:
            # A superseded request never overwrites a newer report and
            # is never retried (PRD Concurrency).
            return _terminal(
                "unable_to_complete",
                SPOKEN_UNABLE,
                SPOKEN_UNABLE,
                f"publish rejected — superseded by a newer report, not"
                f" retried: {exc}",
                labels,
            )
        return EngineResult(
            state="rendered",
            spoken=spoken,
            screen=screen,
            report_id=published.report_id,
            interval_label=interval_label(period),
            timezone_label=timezone_label(period),
            detail=(
                f"published {published.report_id} after rebuild"
                f" ({outcome.reason}); {plan.reason}"
            ),
            references=tuple(published.references),
        )

    # -- internals ---------------------------------------------------------

    def _resolve_period(
        self, intent: QueryIntent, labels: "_Labels"
    ) -> Optional[Period]:
        """Phase (b): zone + period resolution (FR-08/FR-09/FR-37).

        Unqualified current progress (period None) needs NO timezone:
        no time math is involved, so none is guessed or required.
        """
        deps = self._deps
        if intent.period_spec is None:
            labels.zone_name = UNQUALIFIED_TIMEZONE
            labels.unqualified = True
            return None
        zone = deps.zone_resolver(intent.timezone_candidates)
        labels.zone_name = zone.key
        spec = intent.period_spec
        if isinstance(spec, NaturalPeriod):
            resolvers = {
                "today": period_today,
                "this_week": period_this_week,
                "last_7_days": period_last_7_days,
            }
            return resolvers[spec.kind](deps.clock(), zone)
        assert isinstance(spec, ExplicitPeriod)
        period = explicit_period(spec.start, spec.end, zone)
        # Server-side bound validation (FR-37): explicit periods stay
        # bounded — no future ends, span capped at deps.max_span
        # (default 60 days, product decision 2026-09-30). Natural
        # periods never reach this check: their kinds are inherently
        # bounded by their own resolution.
        validate_bounds(
            period.start, period.end, now=deps.clock(), max_span=deps.max_span
        )
        return period

    def _report_key(
        self, intent: QueryIntent, period: Optional[Period], context_id: str
    ) -> ReportKey:
        """The D08 isolation key: context + scope + interval + timezone."""
        scope = intent.projects[0] if intent.projects else "all"
        if period is None:
            return ReportKey(
                main_call_context_id=context_id,
                scope=scope,
                interval_key=UNQUALIFIED_INTERVAL_KEY,
                timezone=UNQUALIFIED_TIMEZONE,
            )
        return ReportKey(
            main_call_context_id=context_id,
            scope=scope,
            interval_key=period.key,
            timezone=period.zone_name,
        )

    def _run_inventories(
        self, intent: QueryIntent, period: Optional[Period], deadline: Deadline
    ) -> tuple[dict[str, InventoryResult], dict[str, tuple[object, Source]]]:
        """Phase (c) inventory half: every configured provider, the SAME
        Deadline object, deterministic kind order.

        FR-12: a date-scoped ``global`` query (period is not None and
        kind is ``global``) restricts the herdr provider to
        ``active_only=True`` — ONLY that provider has the flag; every
        other provider and every other intent kind keeps the default
        inventory over ALL open sessions.
        """
        deps = self._deps
        date_scoped_current = period is not None and intent.kind == "global"
        project_filter = intent.projects[0] if intent.projects else None
        results: dict[str, InventoryResult] = {}
        owner: dict[str, tuple[object, Source]] = {}
        for kind in sorted(deps.providers):
            provider = deps.providers[kind]
            if kind == HERDR_SESSION_KIND:
                result = provider.inventory(
                    project_filter=project_filter,
                    deadline=deadline,
                    active_only=date_scoped_current,
                )
            else:
                result = provider.inventory(
                    project_filter=project_filter, deadline=deadline
                )
            results[kind] = result
            for source in result.sources:
                owner[source.source_id] = (provider, source)
        return results, owner

    def _serve_reused(
        self, outcome: Reuse, key: ReportKey, labels: "_Labels"
    ) -> EngineResult:
        """Phase (c) REUSE: serve the stored report only after the
        successful check (D08/FR-29). A body that cannot be decoded or
        a spoken artifact that leaks a reference demotes the stored
        report via ``mark_refresh_failed`` (FR-33) — a corrupt report
        is never served, never silently skipped."""
        stored = outcome.report
        source_ids = [entry.source_id for entry in stored.source_manifest]
        try:
            doc, screen = _decode_body(stored.body)
        except ValueError as exc:
            self._mark_refresh_failed(key)
            return _terminal(
                "unable_to_complete",
                SPOKEN_UNABLE,
                SPOKEN_UNABLE,
                f"stored report body is unusable, demoted via"
                f" mark_refresh_failed (FR-33): {exc}",
                labels,
            )
        spoken = render_spoken(doc)
        if not assert_no_references(spoken, source_ids):
            self._mark_refresh_failed(key)
            return _terminal(
                "unable_to_complete",
                SPOKEN_UNABLE,
                SPOKEN_UNABLE,
                "stored report spoken artifact leaks a reference (FR-14);"
                " demoted via mark_refresh_failed",
                labels,
            )
        return EngineResult(
            state="rendered",
            spoken=spoken,
            screen=screen,
            report_id=stored.report_id,
            interval_label=labels.interval,
            timezone_label=labels.timezone,
            detail=(
                f"served stored report {stored.report_id} after a successful"
                " freshness check (reuse-after-check, FR-29)"
            ),
            references=tuple(stored.references),
        )

    def _mark_refresh_failed(self, key: ReportKey) -> None:
        """FR-33: demote a prior published report so a stale one is
        never served as current after a failed refresh. No-op when no
        live published revision exists."""
        if self._deps.store.get_current(key) is not None:
            self._deps.store.mark_refresh_failed(key)

    def _budget_expired(
        self, deadline: Deadline, phase: str, labels: "_Labels"
    ) -> Optional[EngineResult]:
        """The FR-17 invariant check: expiry anywhere is
        unable-to-complete naming the phase, never a partial render."""
        if not deadline.expired():
            return None
        return _terminal(
            "unable_to_complete",
            SPOKEN_UNABLE,
            SPOKEN_UNABLE,
            f"budget expired before the {phase} phase (FR-17); nothing"
            " published",
            labels,
        )


class _Labels:
    """Mutable carry for the labels known so far: result fields must
    reflect exactly what was resolved when a terminal state fires
    (unknown zone -> empty, unqualified global -> sentinel, resolved
    period -> its labels)."""

    def __init__(self) -> None:
        self.zone_name: Optional[str] = None
        self.period: Optional[Period] = None
        self.unqualified = False

    @property
    def interval(self) -> str:
        if self.period is not None:
            return interval_label(self.period)
        return _UNQUALIFIED_INTERVAL_LABEL if self.unqualified else ""

    @property
    def timezone(self) -> str:
        return self.zone_name if self.zone_name is not None else ""


def _terminal(
    state: EngineState,
    spoken: str,
    screen: str,
    detail: str,
    labels: "_Labels",
) -> EngineResult:
    """Builds a terminal (non-rendered) result from the known labels."""
    return EngineResult(
        state=state,
        spoken=spoken,
        screen=screen,
        report_id=None,
        interval_label=labels.interval,
        timezone_label=labels.timezone,
        detail=detail,
    )
