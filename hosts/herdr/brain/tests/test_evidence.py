"""Unit tests for the evidence core: coverage statuses, value types with
provenance, budget deadlines, and the Herdr session provider (FR-03, FR-04,
FR-05, FR-11, FR-12, FR-17, FR-20, FR-22, FR-40, FR-41).

Every clock is injected (fake monotonic floats for Deadline, a fixed aware
UTC instant for the provider); no test ever sleeps or reads the real clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.periods import Verdict, classify_timestamp, explicit_period
from herdr_brain.reportstore import ManifestEntry

from herdr_brain.evidence import (
    ACTIVE_STATUSES,
    BudgetExceeded,
    CoverageResult,
    CoverageStatus,
    Deadline,
    EvidenceItem,
    EvidenceProvider,
    InventoryResult,
    Source,
    HerdrSessionProvider,
    manifest_entries,
)

UTC = timezone.utc
OBSERVED_AT = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Monotonic-style float clock advanced only by the test."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def agent(**overrides) -> AgentInfo:
    """AgentInfo with stable defaults; any field overridable."""
    fields = dict(
        pane_id="w1:p9",
        agent="opencode",
        status="working",
        session_kind="id",
        session_value="ses_test0000session",
        cwd="/repo",
        title="OpenCode",
        focused=True,
    )
    fields.update(overrides)
    return AgentInfo(**fields)


def source(**overrides) -> Source:
    """Source with stable defaults; any field overridable."""
    fields = dict(
        source_id="herdr:w1:p9",
        kind="herdr_session",
        project="/repo",
        locator="w1:p9",
        revision_token="tok-1",
        observed_at=OBSERVED_AT,
    )
    fields.update(overrides)
    return Source(**fields)


class TestCoverageStatus:
    def test_members_are_pairwise_distinct(self):
        values = [status.value for status in CoverageStatus]
        assert len(set(values)) == len(values)

    def test_ok_source_absent_and_failed_are_three_different_outcomes(self):
        assert CoverageStatus.OK != CoverageStatus.SOURCE_ABSENT
        assert CoverageStatus.SOURCE_ABSENT != CoverageStatus.COVERAGE_FAILED
        assert CoverageStatus.OK != CoverageStatus.COVERAGE_FAILED


class TestCoverageResultValidation:
    def test_coverage_failed_requires_non_empty_error_detail(self):
        with pytest.raises(ValueError):
            CoverageResult(source(), CoverageStatus.COVERAGE_FAILED)
        with pytest.raises(ValueError):
            CoverageResult(source(), CoverageStatus.COVERAGE_FAILED, error_detail="   ")

    def test_ok_and_source_absent_accept_missing_error_detail(self):
        ok = CoverageResult(source(), CoverageStatus.OK)
        absent = CoverageResult(source(), CoverageStatus.SOURCE_ABSENT)
        assert ok.error_detail is None
        assert absent.error_detail is None

    def test_items_default_to_empty_tuple(self):
        result = CoverageResult(source(), CoverageStatus.OK)
        assert result.items == ()
        assert isinstance(result.items, tuple)

    def test_items_must_be_a_tuple(self):
        with pytest.raises(ValueError):
            CoverageResult(source(), CoverageStatus.OK, items=[])


class TestInventoryResult:
    def test_failed_inventory_requires_error_detail(self):
        with pytest.raises(ValueError):
            InventoryResult(CoverageStatus.COVERAGE_FAILED)

    def test_ok_inventory_with_zero_sources_is_valid(self):
        result = InventoryResult(CoverageStatus.OK)
        assert result.status is CoverageStatus.OK
        assert result.sources == ()


class TestSource:
    def test_observed_at_must_be_aware(self):
        with pytest.raises(ValueError):
            source(observed_at=datetime(2026, 9, 30, 12, 0, 0))

    def test_carries_provenance_fields(self):
        src = source()
        assert src.source_id == "herdr:w1:p9"
        assert src.kind == "herdr_session"
        assert src.locator == "w1:p9"
        assert src.title is None
        assert src.state is None


class TestEvidenceItem:
    def test_unknown_timestamp_stays_none(self):
        item = EvidenceItem(
            source_id="herdr:w1:p9",
            kind="herdr_session",
            timestamp=None,
            role="user",
            text="whatever",
        )
        assert item.timestamp is None

    def test_timestamp_when_present_must_be_aware(self):
        with pytest.raises(ValueError):
            EvidenceItem(
                source_id="herdr:w1:p9",
                kind="herdr_session",
                timestamp=datetime(2026, 9, 30, 12, 0, 0),
                role="user",
                text="whatever",
            )

    def test_none_timestamp_is_unknown_never_out(self):
        # FR-10: unknown must never be replaced (e.g. by an mtime) to force
        # a verdict; it classifies as UNKNOWN against any period.
        item = EvidenceItem(
            source_id="herdr:w1:p9",
            kind="herdr_session",
            timestamp=None,
            role="user",
            text="whatever",
        )
        period = explicit_period(
            datetime(2026, 9, 1, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
            zone=ZoneInfo("UTC"),
        )
        assert classify_timestamp(item.timestamp, period) is Verdict.UNKNOWN

    def test_no_mtime_field_exists(self):
        # The API offers no mtime hook at all, so no caller can substitute
        # file metadata for a message timestamp (FR-10).
        assert "mtime" not in EvidenceItem.__dataclass_fields__
        assert "mtime" not in Source.__dataclass_fields__


class TestDeadline:
    def test_none_deadline_is_unlimited(self):
        clock = FakeClock(start=100.0)
        deadline = Deadline.from_remaining(clock, None)
        assert deadline.remaining() is None
        assert deadline.expired() is False
        deadline.check()  # never raises

    def test_remaining_before_expiry(self):
        clock = FakeClock(start=0.0)
        deadline = Deadline.from_remaining(clock, 10.0)
        assert deadline.remaining() == 10.0
        clock.now = 4.0
        assert deadline.remaining() == 6.0
        assert deadline.expired() is False

    def test_expired_at_and_past_the_deadline(self):
        clock = FakeClock(start=0.0)
        deadline = Deadline.from_remaining(clock, 10.0)
        clock.now = 10.0
        assert deadline.expired() is True
        assert deadline.remaining() <= 0
        clock.now = 11.5
        assert deadline.expired() is True

    def test_check_raises_budget_exceeded_only_when_expired(self):
        clock = FakeClock(start=0.0)
        deadline = Deadline.from_remaining(clock, 5.0)
        deadline.check()
        clock.now = 5.0
        with pytest.raises(BudgetExceeded):
            deadline.check()

    def test_absolute_deadline_constructor(self):
        clock = FakeClock(start=42.0)
        deadline = Deadline(clock, at=50.0)
        assert deadline.remaining() == 8.0
        clock.now = 51.0
        assert deadline.expired() is True


class FailingListClient:
    """Client double whose list_agents always fails (Herdr boundary)."""

    def list_agents(self):
        raise HerdrError("herdr exploded")


class CountingClient:
    """Wraps a client and counts list_agents calls."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.list_calls = 0

    def list_agents(self):
        self.list_calls += 1
        return self.inner.list_agents()


def make_provider(client) -> HerdrSessionProvider:
    return HerdrSessionProvider(client, clock=lambda: OBSERVED_AT)


class TestHerdrSessionProviderInventory:
    def test_every_open_status_is_reported_truthfully(self, make_stub):
        # FR-04: all open sessions are returned regardless of status, the
        # status is reported verbatim via Source.state, and nothing infers
        # "unfinished" merely because a session is open.
        statuses = ["working", "idle", "waiting", "blocked", "done", "unknown"]
        stub = make_stub(agents=[agent(pane_id=f"w1:p{i}", status=s)
                                 for i, s in enumerate(statuses)])
        result = make_provider(stub).inventory()
        assert result.status is CoverageStatus.OK
        assert sorted(src.state for src in result.sources) == sorted(statuses)

    def test_empty_agent_list_is_ok_with_zero_sources(self, make_stub):
        # FR-11/FR-22: no open sessions is a valid empty result, not a
        # failure and not absence-of-coverage.
        stub = make_stub(agents=[])
        result = make_provider(stub).inventory()
        assert result.status is CoverageStatus.OK
        assert result.sources == ()

    def test_herdr_error_is_coverage_failed_never_raised(self):
        result = make_provider(FailingListClient()).inventory()
        assert result.status is CoverageStatus.COVERAGE_FAILED
        assert result.sources == ()
        assert "herdr exploded" in result.error_detail

    def test_source_ids_are_namespaced_and_unique_per_pane(self, make_stub):
        stub = make_stub(agents=[agent(pane_id="w1:p1"), agent(pane_id="w1:p2")])
        result = make_provider(stub).inventory()
        ids = [src.source_id for src in result.sources]
        assert ids == ["herdr:w1:p1", "herdr:w1:p2"]
        assert len(set(ids)) == len(ids)

    def test_project_is_the_cwd_kept_as_is(self, make_stub):
        stub = make_stub(agents=[agent(cwd="/some/deep/path")])
        result = make_provider(stub).inventory()
        assert result.sources[0].project == "/some/deep/path"
        assert result.sources[0].locator == "w1:p9"

    def test_observed_at_comes_from_the_injected_clock(self, make_stub):
        result = make_provider(make_stub()).inventory()
        assert all(src.observed_at == OBSERVED_AT for src in result.sources)


class TestRevisionToken:
    def test_changes_when_status_changes(self, make_stub):
        base = make_provider(make_stub(agents=[agent()])).inventory().sources[0]
        changed = make_provider(
            make_stub(agents=[agent(status="idle")])
        ).inventory().sources[0]
        assert base.revision_token != changed.revision_token

    def test_changes_when_session_value_changes(self, make_stub):
        base = make_provider(make_stub(agents=[agent()])).inventory().sources[0]
        changed = make_provider(
            make_stub(agents=[agent(session_value="ses_other00000value")])
        ).inventory().sources[0]
        assert base.revision_token != changed.revision_token

    def test_changes_when_focused_changes(self, make_stub):
        base = make_provider(
            make_stub(agents=[agent(focused=True)])
        ).inventory().sources[0]
        changed = make_provider(
            make_stub(agents=[agent(focused=False)])
        ).inventory().sources[0]
        assert base.revision_token != changed.revision_token

    def test_changes_when_title_changes(self, make_stub):
        base = make_provider(
            make_stub(agents=[agent(title="Before")])
        ).inventory().sources[0]
        changed = make_provider(
            make_stub(agents=[agent(title="After")])
        ).inventory().sources[0]
        assert base.revision_token != changed.revision_token

    def test_stable_when_nothing_changes(self, make_stub):
        first = make_provider(make_stub()).inventory().sources[0]
        second = make_provider(make_stub()).inventory().sources[0]
        assert first.revision_token == second.revision_token


class TestActiveOnly:
    def test_constant_is_working_blocked_waiting(self):
        assert ACTIVE_STATUSES == frozenset({"working", "blocked", "waiting"})

    def test_active_only_keeps_active_statuses_only(self, make_stub):
        stub = make_stub(
            agents=[
                agent(pane_id="w1:p0", status="working"),
                agent(pane_id="w1:p1", status="idle"),
                agent(pane_id="w1:p2", status="waiting"),
                agent(pane_id="w1:p3", status="blocked"),
                agent(pane_id="w1:p4", status="done"),
                agent(pane_id="w1:p5", status="unknown"),
            ]
        )
        result = make_provider(stub).inventory(active_only=True)
        assert result.status is CoverageStatus.OK
        assert sorted(src.state for src in result.sources) == [
            "blocked", "waiting", "working",
        ]

    def test_default_inventory_keeps_inactive_statuses_too(self, make_stub):
        # FR-12: unqualified current progress includes ALL open sessions
        # including old last activity (idle/done/unknown stay listed).
        stub = make_stub(agents=[agent(status="idle"), agent(status="done")])
        result = make_provider(stub).inventory()
        assert len(result.sources) == 2


class TestProjectFilter:
    def test_exact_cwd_matches(self, make_stub):
        stub = make_stub(
            agents=[agent(pane_id="w1:p0", cwd="/repo"),
                    agent(pane_id="w1:p1", cwd="/repo/sub"),
                    agent(pane_id="w1:p2", cwd="/other")]
        )
        result = make_provider(stub).inventory(project_filter="/repo/sub")
        assert [src.locator for src in result.sources] == ["w1:p1"]

    def test_cwd_under_the_given_path_matches(self, make_stub):
        stub = make_stub(
            agents=[agent(pane_id="w1:p0", cwd="/repo"),
                    agent(pane_id="w1:p1", cwd="/repo/sub/deep"),
                    agent(pane_id="w1:p2", cwd="/repo2")]
        )
        result = make_provider(stub).inventory(project_filter="/repo")
        assert [src.locator for src in result.sources] == ["w1:p0", "w1:p1"]

    def test_non_match_is_ok_with_zero_sources(self, make_stub):
        stub = make_stub(agents=[agent(cwd="/repo")])
        result = make_provider(stub).inventory(project_filter="/elsewhere")
        assert result.status is CoverageStatus.OK
        assert result.sources == ()


class TestHerdrSessionProviderCollect:
    def test_collect_returns_status_snapshot_item(self, make_stub):
        stub = make_stub(agents=[agent()])
        provider = make_provider(stub)
        src = provider.inventory().sources[0]
        result = provider.collect(src)
        assert result.status is CoverageStatus.OK
        assert len(result.items) == 1
        item = result.items[0]
        assert item.role == "status"
        assert item.source_id == src.source_id
        assert item.kind == "herdr_session"
        assert item.message_id is None
        assert item.timestamp is not None
        assert item.timestamp.tzinfo is not None
        assert item.timestamp.utcoffset().total_seconds() == 0
        assert "working" in item.text
        assert "/repo" in item.text
        assert "OpenCode" in item.text

    def test_collect_uses_agent_name_when_title_empty(self, make_stub):
        stub = make_stub(agents=[agent(agent="claude", title="")])
        provider = make_provider(stub)
        result = provider.collect(provider.inventory().sources[0])
        assert "claude working" in result.items[0].text

    def test_collect_accepts_a_period_without_filtering(self, make_stub):
        # Herdr panes carry no message history: the period parameter is
        # part of the provider protocol and is deliberately ignored here;
        # historical evidence comes from transcript providers (later slices).
        stub = make_stub(agents=[agent()])
        provider = make_provider(stub)
        src = provider.inventory().sources[0]
        period = explicit_period(
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2020, 2, 1, tzinfo=UTC),
            zone=ZoneInfo("UTC"),
        )
        result = provider.collect(src, period=period)
        assert result.status is CoverageStatus.OK
        assert len(result.items) == 1


class TestDeadlineEnforcement:
    def test_expired_deadline_fails_inventory_before_listing(self, make_stub):
        clock = FakeClock(start=0.0)
        counting = CountingClient(make_stub(agents=[agent()]))
        provider = HerdrSessionProvider(counting, clock=lambda: OBSERVED_AT)
        deadline = Deadline.from_remaining(clock, 0.0)
        result = provider.inventory(deadline=deadline)
        assert result.status is CoverageStatus.COVERAGE_FAILED
        assert result.sources == ()
        assert "budget" in result.error_detail.lower()
        assert counting.list_calls == 0  # checked BEFORE the read

    def test_expired_deadline_fails_collect_before_reading(self, make_stub):
        clock = FakeClock(start=100.0)
        provider = HerdrSessionProvider(make_stub(), clock=lambda: OBSERVED_AT)
        src = provider.inventory().sources[0]
        deadline = Deadline.from_remaining(clock, 10.0)
        clock.now = 110.0
        result = provider.collect(src, deadline=deadline)
        assert result.status is CoverageStatus.COVERAGE_FAILED
        assert result.items == ()
        assert "budget" in result.error_detail.lower()

    def test_unexpired_deadline_lets_inventory_proceed(self, make_stub):
        clock = FakeClock(start=0.0)
        provider = HerdrSessionProvider(make_stub(), clock=lambda: OBSERVED_AT)
        deadline = Deadline.from_remaining(clock, 10.0)
        result = provider.inventory(deadline=deadline)
        assert result.status is CoverageStatus.OK


class TestManifestEntries:
    def test_shape_and_typing(self, make_stub):
        stub = make_stub(agents=[agent()])
        sources = make_provider(stub).inventory().sources
        entries = manifest_entries(sources)
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, ManifestEntry)
        assert entry.source_id == sources[0].source_id
        assert entry.revision_token == sources[0].revision_token
        assert entry.state == sources[0].state

    def test_state_defaults_to_empty_string(self):
        entries = manifest_entries([source()])
        assert entries[0].state == ""

    def test_deterministic(self, make_stub):
        sources = make_provider(make_stub()).inventory().sources
        assert manifest_entries(sources) == manifest_entries(sources)


class TestProviderProtocol:
    def test_herdr_session_provider_satisfies_protocol(self, make_stub):
        provider = make_provider(make_stub())
        assert isinstance(provider, EvidenceProvider)
        assert provider.kind == "herdr_session"
