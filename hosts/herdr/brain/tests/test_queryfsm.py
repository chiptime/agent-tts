"""Tests for the query FSM engine (T7; FR-01..03, FR-17, FR-19..22, FR-26,
FR-28..33, FR-37; PRD decisions D02, D04, D06, D08 in ``docs/prds/
herdr-brain-on-demand-context.md``).

Deterministic harness per the PRD Requirement-to-Test Matrix rows
"False routing", "Timeout/incomplete", "Volume", "Empty vs failed",
"Ref isolation", and "Revision race and cancellation": every clock is
injected (a fake wall clock for periods/store, a fake monotonic clock
advanced only by test code or by injected fakes mid-flow), providers
are fakes with the real ``EvidenceProvider`` method shapes, the store
and freshness checker are the REAL implementations on a tmp database,
and the summarizer is an injected fake (the real LLM summarizer lands
in T9). No test ever sleeps, reads the real clock, or touches the
network.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from herdr_brain.evidence import (
    CoverageResult,
    CoverageStatus,
    EvidenceItem,
    InventoryResult,
    Source,
)
from herdr_brain.periods import TimezoneUnavailable
from herdr_brain.reportstore import ReportKey, ReportStore

from herdr_brain.queryfsm import (
    DEFAULT_BUDGET_SECONDS,
    DEFAULT_NARROW_CHAR_THRESHOLD,
    DEFAULT_NARROW_ITEM_THRESHOLD,
    UNQUALIFIED_INTERVAL_KEY,
    UNQUALIFIED_TIMEZONE,
    EngineDeps,
    EngineResult,
    ExplicitPeriod,
    NaturalPeriod,
    QueryIntent,
    brief_from_json,
    brief_to_json,
    interval_label,
    timezone_label,
)

UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 30, 13, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Shared deterministic fakes (method shapes mirror the real classes)
# ---------------------------------------------------------------------------


class FakeMono:
    """Monotonic float clock advanced only by the test or by fakes."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePaneClient:
    """Spy for the Herdr selected-session client: the engine must NEVER
    send to a pane (FR-01/02, D07) — any send_prompt call is a routing
    violation, asserted by construction in the supplementary suite."""

    def __init__(self) -> None:
        self.send_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def send_prompt(self, pane_id, text, timeout_ms=None):
        self.send_calls.append(
            {"pane_id": pane_id, "text": text, "timeout_ms": timeout_ms}
        )
        return {"ok": True, "status": "done", "output": ""}

    def create_session(self, *args, **kwargs):
        self.create_calls.append({"args": args, "kwargs": kwargs})
        return {"ok": True}


def make_source(
    source_id: str, kind: str = "opencode", **overrides
) -> Source:
    fields = dict(
        source_id=source_id,
        kind=kind,
        project=overrides.pop("project", "/repo"),
        locator=overrides.pop("locator", f"{source_id}/session"),
        revision_token=overrides.pop("revision_token", "tok-1"),
        observed_at=overrides.pop("observed_at", NOW),
        title=overrides.pop("title", None),
        state=overrides.pop("state", None),
    )
    fields.update(overrides)
    return Source(**fields)


def make_item(source_id: str, kind: str = "opencode", text="Did work", **overrides):
    fields = dict(
        source_id=source_id,
        kind=kind,
        timestamp=NOW,
        role="assistant",
        text=text,
    )
    fields.update(overrides)
    return EvidenceItem(**fields)


class FakeProvider:
    """Evidence-provider fake with the real method shapes.

    Records every inventory/collect call (including the FR-12
    ``active_only`` flag, which only the herdr provider supports) and
    can be scripted to fail or to advance the monotonic clock mid-flow
    (budget tests)."""

    def __init__(
        self,
        kind: str,
        sources=(),
        items_by_source=None,
        *,
        inventory_status=CoverageStatus.OK,
        fail_collect_source_id=None,
        mono=None,
        advance_on_inventory=0.0,
    ) -> None:
        self.kind = kind
        self.sources = tuple(sources)
        self.items_by_source = dict(items_by_source or {})
        self.inventory_status = inventory_status
        self.fail_collect_source_id = fail_collect_source_id
        self._mono = mono
        self._advance_on_inventory = advance_on_inventory
        self.inventory_calls: list[dict] = []
        self.collect_calls: list[dict] = []

    def inventory(
        self, project_filter=None, deadline=None, **kwargs
    ) -> InventoryResult:
        self.inventory_calls.append(
            {
                "project_filter": project_filter,
                "deadline": deadline,
                "active_only": kwargs.get("active_only", False),
            }
        )
        if self._mono is not None and self._advance_on_inventory:
            self._mono.advance(self._advance_on_inventory)
        if self.inventory_status is CoverageStatus.COVERAGE_FAILED:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED, error_detail="provider down"
            )
        # Faithful to the real providers: inventory narrows by the
        # same cwd-matching rule the evidence layer documents.
        from herdr_brain.evidence import _cwd_matches

        sources = tuple(
            s for s in self.sources if _cwd_matches(s.project, project_filter)
        )
        return InventoryResult(CoverageStatus.OK, sources)

    def collect(self, source, period=None, deadline=None) -> CoverageResult:
        self.collect_calls.append(
            {"source": source, "period": period, "deadline": deadline}
        )
        if source.source_id == self.fail_collect_source_id:
            return CoverageResult(
                source,
                CoverageStatus.COVERAGE_FAILED,
                error_detail="source unreadable",
            )
        items = self.items_by_source.get(source.source_id, ())
        status = CoverageStatus.OK if items else CoverageStatus.SOURCE_ABSENT
        if source.state == "always-absent":
            status = CoverageStatus.SOURCE_ABSENT
        return CoverageResult(source, status, tuple(items))


class RecordingSummarizer:
    """Injected consolidation callable (the real LLM one lands in T9).

    Follows the documented convention: labels come from the engine's
    ``interval_label``/``timezone_label`` helpers so the reuse path and
    the rebuild path always agree."""

    def __init__(self, *, leak_reference=False, raise_exc=None) -> None:
        self.calls: list[tuple] = []
        self.leak_reference = leak_reference
        self.raise_exc = raise_exc
        self.mono = None
        self.advance = 0.0

    def __call__(self, scaffold, period):
        from herdr_brain.report import BriefDocument, BriefSection

        self.calls.append((scaffold, period))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.mono is not None and self.advance:
            self.mono.now += self.advance
        sections = []
        for bundle in scaffold.bundles:
            leak = ""
            if self.leak_reference and bundle.sources:
                leak = f" (see {bundle.sources[0].source_id})"
            sections.append(
                BriefSection(
                    project=bundle.project,
                    advances=(f"Advanced the work{leak}",) if bundle.items else (),
                    pending=(),
                    blockers=(),
                    no_work=not bundle.items,
                    conflict_notes=(),
                    citations=tuple(s.source_id for s in bundle.sources),
                )
            )
        return BriefDocument(
            context_id="main-call",
            interval_label=interval_label(period),
            timezone_label=timezone_label(period),
            sections=tuple(sections),
            generated_note="",
        )


def build_harness(tmp_path, providers, *, summarizer=None, mono=None, budget=60.0,
                  narrow_items=4000, narrow_chars=500_000, zone_resolver=None,
                  store=None, clock=None, max_span=None):
    """Assembles real store + real checker + fake providers into an engine."""
    from herdr_brain.freshness import FreshnessChecker
    from herdr_brain.queryfsm import QueryEngine

    mono = mono if mono is not None else FakeMono()
    store = store if store is not None else ReportStore(
        tmp_path / "reports.db", clock=clock or (lambda: NOW)
    )
    checker = FreshnessChecker(store, configured_kinds=tuple(sorted(providers)))
    deps = EngineDeps(
        providers=providers,
        store=store,
        checker=checker,
        summarizer=summarizer if summarizer is not None else RecordingSummarizer(),
        clock=clock or (lambda: NOW),
        monotonic=mono,
        budget_seconds=budget,
        narrow_item_threshold=narrow_items,
        narrow_char_threshold=narrow_chars,
        **({"zone_resolver": zone_resolver} if zone_resolver else {}),
        **({"max_span": max_span} if max_span is not None else {}),
    )
    return QueryEngine(deps), deps, store, mono


def global_key(context: str, scope: str = "all") -> ReportKey:
    """The unqualified-global store key (sentinel interval + zone)."""
    return ReportKey(
        main_call_context_id=context,
        scope=scope,
        interval_key=UNQUALIFIED_INTERVAL_KEY,
        timezone=UNQUALIFIED_TIMEZONE,
    )


# ---------------------------------------------------------------------------
# Cycle 1: intent construction, deps validation, labels, body serialization
# ---------------------------------------------------------------------------


class TestQueryIntent:
    def test_frozen(self):
        intent = QueryIntent(
            kind="global",
            raw_text="What am I working on?",
            projects=(),
            period_spec=None,
            timezone_candidates=("Europe/Madrid",),
        )
        with pytest.raises(FrozenInstanceError):
            intent.raw_text = "changed"

    def test_unqualified_global_is_valid(self):
        intent = QueryIntent(
            kind="global",
            raw_text="current state",
            projects=(),
            period_spec=None,
            timezone_candidates=(),
        )
        assert intent.kind == "global"
        assert intent.period_spec is None

    def test_historical_requires_a_period_spec(self):
        with pytest.raises(ValueError, match="period_spec=None is only valid"):
            QueryIntent(
                kind="historical",
                raw_text="what did I do",
                projects=(),
                period_spec=None,
                timezone_candidates=("Europe/Madrid",),
            )

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValueError, match="kind"):
            QueryIntent(
                kind="chatty",
                raw_text="hi",
                projects=(),
                period_spec=NaturalPeriod(kind="today"),
                timezone_candidates=(),
            )

    def test_multi_project_scope_rejected_as_unsupported(self):
        with pytest.raises(ValueError, match="at most one project"):
            QueryIntent(
                kind="global",
                raw_text="a and b",
                projects=("/repo/a", "/repo/b"),
                period_spec=NaturalPeriod(kind="today"),
                timezone_candidates=("Europe/Madrid",),
            )

    def test_projects_must_be_a_tuple_of_nonempty_strings(self):
        for bad in (["/repo"], ("/repo", ""), (1,)):
            with pytest.raises(ValueError):
                QueryIntent(
                    kind="global",
                    raw_text="x",
                    projects=bad,
                    period_spec=None,
                    timezone_candidates=(),
                )

    def test_timezone_candidates_must_be_a_tuple_of_strings(self):
        with pytest.raises(ValueError):
            QueryIntent(
                kind="global",
                raw_text="x",
                projects=(),
                period_spec=None,
                timezone_candidates=(1, 2),
            )

    def test_focus_with_a_period_spec_is_constructible(self):
        intent = QueryIntent(
            kind="focus",
            raw_text="what's it doing?",
            projects=(),
            period_spec=NaturalPeriod(kind="today"),
            timezone_candidates=(),
        )
        assert intent.kind == "focus"

    def test_focus_without_a_period_spec_is_structurally_invalid(self):
        # Task rule: period_spec=None is valid ONLY for kind="global".
        with pytest.raises(ValueError, match="period_spec=None"):
            QueryIntent(
                kind="focus",
                raw_text="what's it doing?",
                projects=(),
                period_spec=None,
                timezone_candidates=(),
            )


class TestPeriodSpecs:
    def test_natural_period_validates_kind(self):
        with pytest.raises(ValueError, match="today"):
            NaturalPeriod(kind="this_month")

    def test_natural_period_frozen_and_carries_kind(self):
        spec = NaturalPeriod(kind="last_7_days")
        assert spec.kind == "last_7_days"
        with pytest.raises(FrozenInstanceError):
            spec.kind = "today"

    def test_explicit_period_accepts_dates_and_datetimes(self):
        spec = ExplicitPeriod(
            start=datetime(2026, 9, 28, tzinfo=UTC),
            end=datetime(2026, 9, 30, tzinfo=UTC),
        )
        assert spec.start.year == 2026
        date_spec = ExplicitPeriod(start=datetime(2026, 9, 28).date(),
                                   end=datetime(2026, 9, 30).date())
        assert date_spec.start is not None

    def test_explicit_period_rejects_other_types(self):
        with pytest.raises(ValueError):
            ExplicitPeriod(start="2026-09-28", end="2026-09-30")

    def test_bad_period_spec_type_rejected_on_intent(self):
        with pytest.raises(ValueError, match="period_spec"):
            QueryIntent(
                kind="global",
                raw_text="x",
                projects=(),
                period_spec="today",
                timezone_candidates=(),
            )


class TestEngineDeps:
    def _providers(self):
        return {"opencode": FakeProvider("opencode")}

    def _kwargs(self, **overrides):
        from herdr_brain.freshness import FreshnessChecker

        base = dict(
            providers=self._providers(),
            store=object(),
            checker=object(),
            summarizer=lambda scaffold, period: None,
            clock=lambda: NOW,
            monotonic=lambda: 0.0,
        )
        base.update(overrides)
        return base

    def test_defaults_are_the_documented_provisional_values(self):
        deps = EngineDeps(**self._kwargs())
        assert deps.budget_seconds == DEFAULT_BUDGET_SECONDS == 60.0
        assert deps.narrow_item_threshold == DEFAULT_NARROW_ITEM_THRESHOLD
        assert deps.narrow_item_threshold == 4000
        assert deps.narrow_char_threshold == DEFAULT_NARROW_CHAR_THRESHOLD
        assert deps.narrow_char_threshold == 500_000

    def test_default_zone_resolver_wraps_periods_resolve_timezone(self):
        deps = EngineDeps(**self._kwargs())
        assert deps.zone_resolver(("Europe/Madrid",)) is not None
        assert deps.zone_resolver(("Europe/Madrid",)).key == "Europe/Madrid"
        with pytest.raises(TimezoneUnavailable):
            deps.zone_resolver(())

    def test_empty_providers_rejected(self):
        with pytest.raises(ValueError, match="providers"):
            EngineDeps(**self._kwargs(providers={}))

    def test_nonpositive_budget_rejected(self):
        for bad in (0.0, -1.0):
            with pytest.raises(ValueError, match="budget"):
                EngineDeps(**self._kwargs(budget_seconds=bad))

    def test_thresholds_must_be_positive_ints(self):
        for bad in (0, -3, 1.5, True):
            with pytest.raises(ValueError, match="narrow_item_threshold"):
                EngineDeps(**self._kwargs(narrow_item_threshold=bad))
            with pytest.raises(ValueError, match="narrow_char_threshold"):
                EngineDeps(**self._kwargs(narrow_char_threshold=bad))

    def test_uncallable_injected_callables_rejected(self):
        for field in ("summarizer", "clock", "monotonic", "zone_resolver"):
            with pytest.raises(ValueError, match=field):
                EngineDeps(**self._kwargs(**{field: "not callable"}))

    def test_frozen(self):
        deps = EngineDeps(**self._kwargs())
        with pytest.raises(FrozenInstanceError):
            deps.budget_seconds = 1.0


class TestLabels:
    def test_unqualified_labels(self):
        assert interval_label(None) == "current work"
        assert timezone_label(None) == UNQUALIFIED_TIMEZONE == "unqualified"

    def test_natural_labels(self):
        from herdr_brain.periods import (
            period_last_7_days,
            period_this_week,
            period_today,
        )

        assert interval_label(period_today(NOW, MADRID)) == "today"
        assert interval_label(period_this_week(NOW, MADRID)) == "this week"
        assert interval_label(period_last_7_days(NOW, MADRID)) == "the last 7 days"

    def test_explicit_label_uses_local_wall_clock(self):
        from herdr_brain.periods import explicit_period

        period = explicit_period(
            datetime(2026, 9, 28, 9, 30), datetime(2026, 9, 30, 18, 0), MADRID
        )
        assert interval_label(period) == "2026-09-28 09:30 to 2026-09-30 18:00"
        assert timezone_label(period) == "Europe/Madrid"

    def test_labels_deterministic_for_identical_periods(self):
        from herdr_brain.periods import period_today

        one = period_today(NOW, MADRID)
        two = period_today(NOW, MADRID)
        assert interval_label(one) == interval_label(two)


class TestBriefJson:
    def _doc(self):
        from herdr_brain.report import BriefDocument, BriefSection

        return BriefDocument(
            context_id="main-call",
            interval_label="today",
            timezone_label="Europe/Madrid",
            sections=(
                BriefSection(
                    project="/repo",
                    advances=("a", "b"),
                    pending=(),
                    blockers=("c",),
                    no_work=False,
                    conflict_notes=("x conflicts",),
                    citations=("opencode:/repo/a",),
                ),
            ),
            generated_note="note with ünicode",
        )

    def test_round_trip_is_identity(self):
        doc = self._doc()
        assert brief_from_json(brief_to_json(doc)) == doc

    def test_output_is_sorted_key_deterministic_json(self):
        payload = json.loads(brief_to_json(self._doc()))
        assert list(sorted(payload)) == list(payload.keys())
        assert brief_to_json(self._doc()) == brief_to_json(self._doc())

    def test_malformed_json_raises_value_error(self):
        for bad in ("not json", "{}", '{"sections": []}', "[]"):
            with pytest.raises(ValueError):
                brief_from_json(bad)

    def test_extra_or_missing_fields_raise(self):
        payload = json.loads(brief_to_json(self._doc()))
        payload["unexpected"] = 1
        with pytest.raises(ValueError):
            brief_from_json(json.dumps(payload))
        del payload["generated_note"]
        with pytest.raises(ValueError):
            brief_from_json(json.dumps(payload))


# ---------------------------------------------------------------------------
# Cycle 2: focus false-routing guard (FR-01/FR-02)
# ---------------------------------------------------------------------------


class TestFocusRouting:
    def _focus_intent(self):
        return QueryIntent(
            kind="focus",
            raw_text="what's it doing?",
            projects=(),
            period_spec=NaturalPeriod(kind="today"),
            timezone_candidates=("Europe/Madrid",),
        )

    def test_focus_returns_focus_rejected_without_touching_anything(self, tmp_path):
        from herdr_brain.queryfsm import QueryEngine

        provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(self._focus_intent(), "call-1")

        assert result.state == "focus_rejected"
        assert result.spoken == "" and result.screen == ""
        assert result.report_id is None
        assert result.references == ()
        assert "conversation loop" in result.detail
        # Zero side effects: no inventory, no collect, no store rows.
        assert provider.inventory_calls == []
        assert provider.collect_calls == []
        assert store.get_current(global_key("call-1")) is None

    def test_focus_guard_fires_before_any_deadline_or_zone_work(self, tmp_path):
        from herdr_brain.queryfsm import QueryEngine

        # A zero budget would expire everything instantly for non-focus
        # queries; focus must return before any of that machinery.
        provider = FakeProvider("opencode")
        mono = FakeMono()
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, mono=mono, budget=0.0001
        )
        result = engine.handle(self._focus_intent(), "call-1")
        assert result.state == "focus_rejected"
        assert mono.now == 0.0  # monotonic clock never even read


# ---------------------------------------------------------------------------
# Cycle 3: the mainline lifecycle (render/publish/reuse/inventory scoping)
# ---------------------------------------------------------------------------


def today_intent(raw="What did I do today?", kind="historical", projects=()):
    return QueryIntent(
        kind=kind,
        raw_text=raw,
        projects=projects,
        period_spec=NaturalPeriod(kind="today"),
        timezone_candidates=("Europe/Madrid",),
    )


def unqualified_intent(raw="What am I working on?"):
    return QueryIntent(
        kind="global",
        raw_text=raw,
        projects=(),
        period_spec=None,
        timezone_candidates=("Europe/Madrid",),
    )


def today_key(context: str, scope: str = "all") -> ReportKey:
    from herdr_brain.periods import period_today

    return ReportKey(
        main_call_context_id=context,
        scope=scope,
        interval_key=period_today(NOW, MADRID).key,
        timezone="Europe/Madrid",
    )


class TestHappyPath:
    def _provider(self):
        src = make_source("opencode:/repo/a")
        return FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )

    def test_rebuild_renders_and_publishes_once(self, tmp_path):
        provider = self._provider()
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")

        assert result.state == "rendered"
        assert result.report_id is not None
        assert result.interval_label == "today"
        assert result.timezone_label == "Europe/Madrid"
        assert "published" in result.detail
        # Spoken is prose only; screen carries the references (FR-14).
        assert "opencode:/repo/a" not in result.spoken
        assert "opencode:/repo/a" in result.screen
        assert [r.source_id for r in result.references] == ["opencode:/repo/a"]
        # Store: exactly one published revision for the key.
        stored = store.get_current(today_key("call-1"))
        assert stored is not None and stored.status == "published"
        assert stored.report_id == result.report_id
        assert len(provider.collect_calls) == 1

    def test_second_identical_call_reuses_without_collecting(self, tmp_path):
        provider = self._provider()
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        first = engine.handle(today_intent(), "call-1")
        collects_before = len(provider.collect_calls)
        inventories_before = len(provider.inventory_calls)

        second = engine.handle(today_intent(), "call-1")

        assert second.state == "rendered"
        assert second.report_id == first.report_id
        assert second.spoken == first.spoken
        assert second.screen == first.screen
        assert "reuse-after-check" in second.detail
        # Reuse-after-check: inventories ran again, collects did not.
        assert len(provider.inventory_calls) == inventories_before + 1
        assert len(provider.collect_calls) == collects_before

    def test_any_token_change_forces_full_rebuild(self, tmp_path):
        provider = self._provider()
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        first = engine.handle(today_intent(), "call-1")
        # Same source id, NEW revision token: the only change signal (FR-31).
        provider.sources = (
            make_source("opencode:/repo/a", revision_token="tok-2"),
        )
        provider.items_by_source = {"opencode:/repo/a": (make_item("opencode:/repo/a"),)}

        second = engine.handle(today_intent(), "call-1")

        assert second.state == "rendered"
        assert second.report_id != first.report_id
        assert "rebuild" in second.detail.lower()
        assert len(provider.collect_calls) == 2  # full re-read, not delta
        latest = store.get_current(today_key("call-1"))
        assert latest.report_id == second.report_id

    def test_keys_isolate_contexts(self, tmp_path):
        provider = self._provider()
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        engine.handle(today_intent(), "call-1")
        engine.handle(today_intent(), "call-2")
        assert store.get_current(today_key("call-1")) is not None
        assert store.get_current(today_key("call-2")) is not None
        assert (
            store.get_current(today_key("call-1")).report_id
            != store.get_current(today_key("call-2")).report_id
        )


class TestInventoryScoping:
    """FR-12/D04: unqualified current progress includes ALL open
    sessions; date-scoped current progress uses ACTIVE inventory only."""

    def _herdr_provider(self):
        active = make_source(
            "herdr:w1:p1",
            kind="herdr_session",
            project="/repo",
            locator="w1:p1",
            revision_token="t-active",
            state="working",
        )
        idle = make_source(
            "herdr:w1:p2",
            kind="herdr_session",
            project="/repo",
            locator="w1:p2",
            revision_token="t-idle",
            state="idle",
        )
        items = {
            "herdr:w1:p1": (make_item("herdr:w1:p1", kind="herdr_session", text="working on X"),),
            "herdr:w1:p2": (make_item("herdr:w1:p2", kind="herdr_session", text="idle since morning"),),
        }
        provider = FakeProvider("herdr_session", (active, idle), items)
        # A faithful fake applies the flag like the real provider does.
        original_inventory = provider.inventory

        def inventory(project_filter=None, deadline=None, **kwargs):
            result = original_inventory(project_filter, deadline, **kwargs)
            if kwargs.get("active_only"):
                from herdr_brain.evidence import ACTIVE_STATUSES

                result = InventoryResult(
                    result.status,
                    tuple(s for s in result.sources if s.state in ACTIVE_STATUSES),
                )
            return result

        provider.inventory = inventory
        return provider

    def test_unqualified_global_keeps_idle_sessions(self, tmp_path):
        provider = self._herdr_provider()
        engine, deps, store, mono = build_harness(
            tmp_path, {"herdr_session": provider}
        )
        result = engine.handle(unqualified_intent(), "call-1")

        assert result.state == "rendered"
        assert provider.inventory_calls[0]["active_only"] is False
        assert len(provider.collect_calls) == 2  # active AND idle read
        stored = store.get_current(global_key("call-1"))
        assert stored is not None
        assert result.timezone_label == "unqualified"
        assert result.interval_label == "current work"

    def test_date_scoped_global_filters_to_active_inventory(self, tmp_path):
        provider = self._herdr_provider()
        engine, deps, store, mono = build_harness(
            tmp_path, {"herdr_session": provider}
        )
        result = engine.handle(today_intent(kind="global", raw="state of my work today"), "call-1")

        assert result.state == "rendered"
        assert provider.inventory_calls[0]["active_only"] is True
        collected = {c["source"].source_id for c in provider.collect_calls}
        assert collected == {"herdr:w1:p1"}  # idle session not acquired
        # The manifest reflects the active-only view.
        stored = store.get_current(today_key("call-1"))
        assert [e.source_id for e in stored.source_manifest] == ["herdr:w1:p1"]

    def test_historical_never_restricts_by_activity(self, tmp_path):
        provider = self._herdr_provider()
        engine, deps, store, mono = build_harness(
            tmp_path, {"herdr_session": provider}
        )
        engine.handle(today_intent(kind="historical"), "call-1")
        assert provider.inventory_calls[0]["active_only"] is False


class TestEmptyScope:
    """FR-22: an exhaustively covered empty scope is a valid no-work
    result, never a failure."""

    def test_no_evidence_scope_renders_no_work_brief(self, tmp_path):
        src = make_source("opencode:/repo/empty")
        provider = FakeProvider("opencode", (src,))  # zero items -> OK-empty
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")

        assert result.state == "rendered"
        from herdr_brain.report import NO_WORK_PHRASE

        assert NO_WORK_PHRASE in result.spoken

    def test_all_absent_scope_is_also_a_valid_no_work_brief(self, tmp_path):
        src = make_source("opencode:/repo/gone", state="always-absent")
        provider = FakeProvider("opencode", (src,))
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "rendered"
        from herdr_brain.report import NO_WORK_PHRASE

        assert NO_WORK_PHRASE in result.spoken

    def test_zero_providers_inventory_ok_is_not_failure(self, tmp_path):
        # A provider whose inventory is OK with zero sources (nothing
        # open): valid empty scope, rendered (FR-11/FR-22).
        provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "rendered"
        assert result.spoken.strip()


# ---------------------------------------------------------------------------
# Cycle 4: failure semantics, budget, terminal states, races
# ---------------------------------------------------------------------------


class TestInventoryFailure:
    def test_inventory_failure_is_unable_and_demotes_prior_report(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        first = engine.handle(today_intent(), "call-1")
        assert first.state == "rendered"
        provider.inventory_status = CoverageStatus.COVERAGE_FAILED

        second = engine.handle(today_intent(), "call-1")

        assert second.state == "unable_to_complete"
        assert "provider down" in second.detail
        # FR-33: the prior report is demoted, never served as current.
        assert store.get_current(today_key("call-1")) is None
        latest = store.get_latest(today_key("call-1"))
        assert latest is not None and latest.status == "refresh_failed"
        assert latest.report_id == first.report_id

    def test_inventory_failure_without_prior_report_publishes_nothing(self, tmp_path):
        provider = FakeProvider("opencode", inventory_status=CoverageStatus.COVERAGE_FAILED)
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert store.get_latest(today_key("call-1")) is None


class TestCollectFailure:
    def test_collect_failure_mid_acquire_is_unable_no_partial(self, tmp_path):
        good = make_source("opencode:/repo/a")
        bad = make_source("opencode:/repo/b")
        provider = FakeProvider(
            "opencode",
            (good, bad),
            {good.source_id: (make_item(good.source_id),)},
            fail_collect_source_id=bad.source_id,
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")

        assert result.state == "unable_to_complete"
        assert "opencode:/repo/b" in result.detail  # names the source (FR-20)
        assert store.get_current(today_key("call-1")) is None  # no partial

    def test_collect_failure_after_publish_demotes_prior_report(self, tmp_path):
        good = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (good,), {good.source_id: (make_item(good.source_id),)}
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        first = engine.handle(today_intent(), "call-1")
        # Change the token (rebuild trigger) and fail the read.
        provider.sources = (make_source("opencode:/repo/a", revision_token="tok-2"),)
        provider.fail_collect_source_id = "opencode:/repo/a"

        second = engine.handle(today_intent(), "call-1")

        assert second.state == "unable_to_complete"
        assert store.get_current(today_key("call-1")) is None
        latest = store.get_latest(today_key("call-1"))
        assert latest.status == "refresh_failed"
        assert latest.report_id == first.report_id


class TestConsolidationFailures:
    def test_validation_failure_is_unable_and_publishes_nothing(self, tmp_path):
        from herdr_brain.report import BriefDocument, BriefSection

        def bad_summarizer(scaffold, period):
            return BriefDocument(
                context_id="main-call",
                interval_label=interval_label(period),
                timezone_label=timezone_label(period),
                sections=(
                    BriefSection(
                        project="/repo",
                        advances=("claims evidence",),
                        pending=(),
                        blockers=(),
                        no_work=False,
                        conflict_notes=(),
                        citations=("bogus:source",),  # ungrounded (FR-15)
                    ),
                ),
            )

        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, summarizer=bad_summarizer
        )
        result = engine.handle(today_intent(), "call-1")

        assert result.state == "unable_to_complete"
        assert "bogus:source" in result.detail
        assert store.get_current(today_key("call-1")) is None

    def test_summarizer_raise_is_unable(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(
            tmp_path,
            {"opencode": provider},
            summarizer=RecordingSummarizer(raise_exc=RuntimeError("LLM down")),
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "LLM down" in result.detail
        assert store.get_current(today_key("call-1")) is None

    def test_spoken_reference_leak_never_publishes(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(
            tmp_path,
            {"opencode": provider},
            summarizer=RecordingSummarizer(leak_reference=True),
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "leaked" in result.detail
        assert store.get_current(today_key("call-1")) is None


class TestBudget:
    def _provider(self):
        src = make_source("opencode:/repo/a")
        return FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )

    def test_expired_before_freshness(self, tmp_path):
        def zone_then_expire(candidates):
            mono.advance(10_000.0)
            return MADRID

        provider = self._provider()
        mono = FakeMono()
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, mono=mono, zone_resolver=zone_then_expire
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "freshness" in result.detail
        assert provider.inventory_calls == []  # never got there
        assert store.get_current(today_key("call-1")) is None

    def test_expired_before_acquire(self, tmp_path):
        provider = self._provider()
        mono = FakeMono()
        provider_with_clock = FakeProvider(
            "opencode",
            provider.sources,
            provider.items_by_source,
            mono=mono,
            advance_on_inventory=10_000.0,
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider_with_clock}, mono=mono
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "acquire" in result.detail
        assert provider_with_clock.collect_calls == []
        assert store.get_current(today_key("call-1")) is None

    def test_expired_mid_acquire(self, tmp_path):
        a = make_source("opencode:/repo/a")
        b = make_source("opencode:/repo/b")
        mono = FakeMono()

        class AdvanceOnFirstCollect(FakeProvider):
            def collect(self, source, period=None, deadline=None):
                if source.source_id == "opencode:/repo/a":
                    mono.advance(10_000.0)
                return super().collect(source, period, deadline)

        provider = AdvanceOnFirstCollect(
            "opencode", (a, b), {a.source_id: (make_item(a.source_id),)}
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, mono=mono
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "acquire" in result.detail
        assert len(provider.collect_calls) == 1  # stopped before source b
        assert store.get_current(today_key("call-1")) is None

    def test_expired_before_publish(self, tmp_path):
        provider = self._provider()
        mono = FakeMono()
        summarizer = RecordingSummarizer()
        summarizer.mono = mono
        summarizer.advance = 10_000.0  # budget dies during consolidation
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, mono=mono, summarizer=summarizer
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "publish" in result.detail
        assert store.get_current(today_key("call-1")) is None  # never partial


class TestTerminalStates:
    def test_tz_unavailable_asks_tz_and_touches_nothing(self, tmp_path):
        provider = self_provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        intent = QueryIntent(
            kind="historical",
            raw_text="what did I do today?",
            projects=(),
            period_spec=NaturalPeriod(kind="today"),
            timezone_candidates=("Not/AZone", "Also/Bad"),
        )
        result = engine.handle(intent, "call-1")

        assert result.state == "ask_tz"
        assert "timezone" in result.spoken.lower()
        assert provider.inventory_calls == []
        assert store.get_latest(today_key("call-1")) is None

    def test_explicit_period_error_clarifies_with_reason(self, tmp_path):
        provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        intent = QueryIntent(
            kind="historical",
            raw_text="september 32nd please",
            projects=(),
            period_spec=ExplicitPeriod(
                start=datetime(2026, 9, 30, tzinfo=UTC),
                end=datetime(2026, 9, 28, tzinfo=UTC),  # start >= end
            ),
            timezone_candidates=("Europe/Madrid",),
        )
        result = engine.handle(intent, "call-1")

        assert result.state == "clarify"
        assert "start must be before end" in result.spoken
        assert "start must be before end" in result.detail
        assert provider.inventory_calls == []
        assert result.timezone_label == "Europe/Madrid"  # zone was resolved

    def test_explicit_future_bounds_clarify(self, tmp_path):
        provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        intent = QueryIntent(
            kind="historical",
            raw_text="next year",
            projects=(),
            period_spec=ExplicitPeriod(
                start=NOW,
                end=NOW + timedelta(days=400),
            ),
            timezone_candidates=("Europe/Madrid",),
        )
        result = engine.handle(intent, "call-1")
        assert result.state == "clarify"
        assert "future" in result.detail


class TestExplicitMaxSpan:
    """Product decision 2026-09-30: EXPLICIT periods may span at most
    ``EngineDeps.max_span`` (default 60 days). A wider span raises
    ``PeriodError`` inside resolution and surfaces the EXISTING
    user-facing ``clarify`` state with the reason (FR-08/FR-37; D04
    "explicit periods stay bounded"). Natural periods are inherently
    bounded by their own resolution (today / this week / last 7 days)
    and must never hit the cap."""

    def wide_intent(self, days: int) -> QueryIntent:
        return QueryIntent(
            kind="historical",
            raw_text=f"the last {days} days please",
            projects=(),
            period_spec=ExplicitPeriod(start=NOW - timedelta(days=days), end=NOW),
            timezone_candidates=("Europe/Madrid",),
        )

    def test_sixty_one_day_explicit_span_clarifies_with_reason(self, tmp_path):
        provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(self.wide_intent(61), "call-1")
        assert result.state == "clarify"  # user-facing outcome, not the raise
        assert "max_span" in result.detail  # the reason rides the detail
        assert "period" in result.spoken.lower()
        assert provider.inventory_calls == []  # rejected before any acquisition

    def test_exactly_sixty_day_explicit_span_proceeds(self, tmp_path):
        provider = FakeProvider(
            "opencode",
            sources=(make_source("opencode:s1"),),
            items_by_source={
                "opencode:s1": [
                    # Mid-period timestamp: the exact end instant is OUT
                    # of the half-open interval.
                    make_item("opencode:s1", timestamp=NOW - timedelta(days=30))
                ]
            },
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(self.wide_intent(60), "call-1")
        assert result.state == "rendered"
        assert result.report_id

    def test_smaller_max_span_clarifies_wider_explicit_period(self, tmp_path):
        provider = FakeProvider("opencode")
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, max_span=timedelta(days=7)
        )
        result = engine.handle(self.wide_intent(30), "call-1")
        assert result.state == "clarify"
        assert "max_span" in result.detail

    def test_natural_periods_bypass_a_tiny_max_span(self, tmp_path):
        # last_7_days spans 7 days; a 1-second cap must not affect it
        # because natural kinds never ride validate_bounds.
        provider = FakeProvider(
            "opencode",
            sources=(make_source("opencode:s1"),),
            items_by_source={
                "opencode:s1": [
                    make_item("opencode:s1", timestamp=NOW - timedelta(hours=2))
                ]
            },
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, max_span=timedelta(seconds=1)
        )
        intent = QueryIntent(
            kind="historical",
            raw_text="what did I do this week?",
            projects=(),
            period_spec=NaturalPeriod(kind="this_week"),
            timezone_candidates=("Europe/Madrid",),
        )
        result = engine.handle(intent, "call-1")
        assert result.state == "rendered"

    def test_deps_default_max_span_is_sixty_days(self, tmp_path):
        _, deps, _, _ = build_harness(tmp_path, {"opencode": FakeProvider("opencode")})
        assert deps.max_span == timedelta(days=60)

    def test_deps_reject_nonpositive_or_non_timedelta_max_span(self, tmp_path):
        for bad in (timedelta(0), timedelta(days=-1), 60, "60d"):
            with pytest.raises(ValueError, match="max_span"):
                build_harness(
                    tmp_path, {"opencode": FakeProvider("opencode")}, max_span=bad
                )


class TestVolume:
    def test_over_item_threshold_asks_to_narrow(self, tmp_path):
        sources = tuple(
            make_source(f"opencode:/repo/{i}") for i in range(5)
        )
        items = {
            s.source_id: (make_item(s.source_id, text="x" * 10),)
            for s in sources
        }
        provider = FakeProvider("opencode", sources, items)
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, narrow_items=4
        )
        result = engine.handle(today_intent(), "call-1")

        assert result.state == "narrow_ask"
        assert "5 items" in result.spoken  # mentions the counts
        assert "narrow" in result.spoken.lower()
        assert "50 characters" in result.spoken
        assert store.get_current(today_key("call-1")) is None  # no publish
        assert store.get_latest(today_key("call-1")) is None

    def test_over_char_threshold_asks_to_narrow(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode",
            (src,),
            {src.source_id: (make_item(src.source_id, text="y" * 200),)},
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, narrow_chars=100
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "narrow_ask"
        assert "200 characters" in result.spoken

    def test_at_threshold_is_not_over(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode",
            (src,),
            {src.source_id: (make_item(src.source_id, text="z" * 100),)},
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, narrow_items=1, narrow_chars=100
        )
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "rendered"  # exactly at the limit passes


class TestStaleAndCorrupt:
    def test_stale_build_error_is_unable_without_retry(self, tmp_path):
        from herdr_brain.reportstore import BuildHandle

        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        real_store = ReportStore(tmp_path / "reports.db", clock=lambda: NOW)

        class RacingStore:
            """Delegates to the real store but simulates a newer
            concurrent revision winning between begin_build and the
            engine's publish."""

            def __init__(self) -> None:
                self.engine_publishes: list[str] = []
                self._raced = False

            def get_current(self, key):
                return real_store.get_current(key)

            def get_latest(self, key):
                return real_store.get_latest(key)

            def mark_refresh_failed(self, key):
                return real_store.mark_refresh_failed(key)

            def begin_build(self, key):
                handle = real_store.begin_build(key)
                if not self._raced:
                    self._raced = True
                    winner = real_store.begin_build(key)
                    real_store.publish(
                        winner,
                        body='{"version":1,"brief":{},"screen":""}',
                        references=[],
                        source_manifest=[],
                    )
                return handle

            def publish(self, handle, *, body, references, source_manifest):
                self.engine_publishes.append(handle.report_id)
                return real_store.publish(
                    handle,
                    body=body,
                    references=references,
                    source_manifest=source_manifest,
                )

        racing = RacingStore()
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": provider}, store=racing
        )
        result = engine.handle(today_intent(), "call-1")

        assert result.state == "unable_to_complete"
        assert "superseded" in result.detail
        # Exactly one publish attempt on the stale handle: no retry.
        assert racing.engine_publishes == [racing.engine_publishes[0]]
        assert len(racing.engine_publishes) == 1

    def test_corrupt_stored_body_is_unable_and_demoted(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        first = engine.handle(today_intent(), "call-1")
        assert first.state == "rendered"
        # Tamper with the stored body out-of-band.
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE reports SET body = 'not json' WHERE report_id = ?",
                (first.report_id,),
            )

        second = engine.handle(today_intent(), "call-1")
        assert second.state == "unable_to_complete"
        assert "body" in second.detail
        assert store.get_current(today_key("call-1")) is None  # demoted


# ---------------------------------------------------------------------------
# Cycle 5: supplementary coverage (spy client, routing matrix,
# multi-provider union, project scoping, shared deadline)
# ---------------------------------------------------------------------------


class TestPaneIsolation:
    def test_engine_cannot_send_to_any_pane_by_construction(self, tmp_path):
        """FR-24/D07: the engine API holds no send path at all. The
        spy client sits inside a herdr-shaped provider exactly like
        production wiring; a full happy query plus every terminal
        state must leave it untouched."""
        spy = FakePaneClient()

        class ProviderWithClient(FakeProvider):
            client = spy  # production wiring hands providers a client

        src = make_source("herdr:w1:p1", kind="herdr_session", state="working")
        provider = ProviderWithClient(
            "herdr_session", (src,),
            {src.source_id: (make_item(src.source_id, kind="herdr_session"),)},
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"herdr_session": provider}
        )
        engine.handle(unqualified_intent(), "call-1")
        engine.handle(
            QueryIntent(
                kind="focus",
                raw_text="status",
                projects=(),
                period_spec=NaturalPeriod(kind="today"),
                timezone_candidates=("Europe/Madrid",),
            ),
            "call-1",
        )
        assert spy.send_calls == []
        assert spy.create_calls == []


class TestRoutingMatrix:
    """The deterministic false-routing core (FR-01..03)."""

    def test_matrix_focus_global_historical(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})

        focus = engine.handle(
            QueryIntent(
                kind="focus",
                raw_text="what's it doing?",
                projects=(),
                period_spec=NaturalPeriod(kind="today"),
                timezone_candidates=("Europe/Madrid",),
            ),
            "call-f",
        )
        assert focus.state == "focus_rejected"

        global_scoped = engine.handle(
            today_intent(kind="global", raw="today's state of my work"), "call-g"
        )
        assert global_scoped.state == "rendered"

        global_unqualified = engine.handle(unqualified_intent(), "call-gu")
        assert global_unqualified.state == "rendered"

        historical = engine.handle(today_intent(kind="historical"), "call-h")
        assert historical.state == "rendered"

        # Only the three non-focus queries touched the provider.
        assert len(provider.inventory_calls) == 3

    def test_historical_without_period_never_reaches_the_engine(self):
        # Structural guard: classification must give historical queries
        # a period spec; None is a construction error, not a runtime
        # state (keeps the engine's state machine sealed).
        with pytest.raises(ValueError):
            QueryIntent(
                kind="historical",
                raw_text="stuff",
                projects=(),
                period_spec=None,
                timezone_candidates=("Europe/Madrid",),
            )


class TestMultiProviderUnion:
    def test_all_configured_providers_inventory_collect_and_manifest(self, tmp_path):
        oc = make_source("opencode:/repo/a")
        hd = make_source(
            "herdr:w1:p1", kind="herdr_session", state="working"
        )
        opencode = FakeProvider(
            "opencode", (oc,), {oc.source_id: (make_item(oc.source_id),)}
        )
        herdr = FakeProvider(
            "herdr_session",
            (hd,),
            {hd.source_id: (make_item(hd.source_id, kind="herdr_session"),)},
        )
        engine, deps, store, mono = build_harness(
            tmp_path, {"opencode": opencode, "herdr_session": herdr}
        )
        result = engine.handle(today_intent(kind="historical"), "call-1")

        assert result.state == "rendered"
        assert len(opencode.inventory_calls) == 1
        assert len(herdr.inventory_calls) == 1
        assert len(opencode.collect_calls) == 1
        assert len(herdr.collect_calls) == 1
        stored = store.get_current(today_key("call-1"))
        assert sorted(e.source_id for e in stored.source_manifest) == [
            "herdr:w1:p1",
            "opencode:/repo/a",
        ]

    def test_every_call_shares_one_deadline_object(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        engine.handle(today_intent(), "call-1")
        inventory_deadline = provider.inventory_calls[0]["deadline"]
        collect_deadline = provider.collect_calls[0]["deadline"]
        assert inventory_deadline is collect_deadline
        assert inventory_deadline.at == pytest.approx(60.0)

    def test_engine_result_is_frozen(self):
        result = EngineResult(
            state="unable_to_complete",
            spoken="x",
            screen="x",
            report_id=None,
            interval_label="",
            timezone_label="",
            detail="d",
        )
        with pytest.raises(FrozenInstanceError):
            result.state = "rendered"


class TestProjectScoping:
    def test_single_project_narrows_scope_and_key(self, tmp_path):
        in_scope = make_source("opencode:/repo/a", project="/repo/a")
        out_scope = make_source("opencode:/other/b", project="/other/b")
        provider = FakeProvider(
            "opencode",
            (in_scope, out_scope),
            {s.source_id: (make_item(s.source_id),) for s in (in_scope, out_scope)},
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        intent = QueryIntent(
            kind="historical",
            raw_text="what did we do in repo a",
            projects=("/repo/a",),
            period_spec=NaturalPeriod(kind="today"),
            timezone_candidates=("Europe/Madrid",),
        )
        result = engine.handle(intent, "call-1")

        assert result.state == "rendered"
        assert provider.inventory_calls[0]["project_filter"] == "/repo/a"
        stored = store.get_current(today_key("call-1", scope="/repo/a"))
        assert stored is not None  # scope-isolated key (D08)
        assert [e.source_id for e in stored.source_manifest] == ["opencode:/repo/a"]

    def test_spoken_artifacts_never_carry_source_ids(self, tmp_path):
        src = make_source("opencode:/repo/a")
        provider = FakeProvider(
            "opencode", (src,), {src.source_id: (make_item(src.source_id),)}
        )
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")
        assert "opencode:/repo/a" not in result.spoken
        assert "opencode:/repo/a" in result.screen

    def test_unable_spoken_is_generic_never_names_sources(self, tmp_path):
        bad = make_source("opencode:/repo/b")
        provider = FakeProvider("opencode", (bad,), fail_collect_source_id=bad.source_id)
        engine, deps, store, mono = build_harness(tmp_path, {"opencode": provider})
        result = engine.handle(today_intent(), "call-1")
        assert result.state == "unable_to_complete"
        assert "opencode:/repo/b" in result.detail  # diagnostics carry it
        assert "opencode:/repo/b" not in result.spoken  # spoken never does
