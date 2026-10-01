"""Unit tests for freshness validation (T5; FR-26, FR-28..FR-33, FR-41;
PRD decision D08 in ``docs/prds/herdr-brain-on-demand-context.md``).

Deterministic harness per the PRD Requirement-to-Test Matrix rows
"Freshness: no-change", "Freshness: deletions", "Time rollover", and
"Timeout/incomplete": every clock is injected (a fake store clock for
retention boundaries, fake monotonic clocks for deadlines), provider
inventories are hand-built ``InventoryResult`` values, and the stored
side is a REAL ``ReportStore`` on a tmp database so the eligibility
paths (published / refresh_failed / expired) are exercised exactly as
production reaches them. No test ever sleeps or reads the real clock.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from herdr_brain.evidence import CoverageStatus, Deadline, InventoryResult, Source
from herdr_brain.periods import period_this_week, period_today
from herdr_brain.reportstore import ManifestEntry, ReportKey, ReportStore

from herdr_brain.freshness import (
    FreshnessChecker,
    FreshnessConfigError,
    FreshnessOutcome,
    ManifestDiff,
    Rebuild,
    Reuse,
    Unable,
    diff_manifests,
    plan_rebuild,
)

UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


class FakeMono:
    """Monotonic float clock advanced only by the test."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def entry(source_id, token, state=""):
    return ManifestEntry(source_id=source_id, revision_token=token, state=state)


class TestManifestDiff:
    def test_addition(self):
        diff = diff_manifests([entry("a", "t1")], [entry("a", "t1"), entry("b", "t2")])
        assert diff.added == (entry("b", "t2"),)
        assert diff.removed == ()
        assert diff.changed == ()
        assert diff.unchanged == (entry("a", "t1"),)

    def test_removal(self):
        diff = diff_manifests(
            [entry("a", "t1"), entry("b", "t2")], [entry("a", "t1")]
        )
        assert diff.removed == (entry("b", "t2"),)
        assert diff.added == () and diff.changed == ()
        assert diff.unchanged == (entry("a", "t1"),)

    def test_token_change(self):
        diff = diff_manifests([entry("a", "t1")], [entry("a", "t2")])
        assert diff.changed == (entry("a", "t2"),)  # current side carried
        assert diff.added == () and diff.removed == () and diff.unchanged == ()

    def test_unchanged(self):
        diff = diff_manifests(
            [entry("a", "t1"), entry("b", "t2")],
            [entry("b", "t2"), entry("a", "t1")],
        )
        assert diff == ManifestDiff((), (), (), (entry("a", "t1"), entry("b", "t2")))

    def test_disjoint(self):
        diff = diff_manifests([entry("a", "t1")], [entry("b", "t2")])
        assert diff.added == (entry("b", "t2"),)
        assert diff.removed == (entry("a", "t1"),)
        assert diff.changed == () and diff.unchanged == ()

    def test_empty_empty(self):
        diff = diff_manifests([], [])
        assert diff == ManifestDiff((), (), (), ())
        assert diff.any_change() is False

    def test_mapping_and_object_inputs_equivalent(self):
        from_objects = diff_manifests(
            [entry("a", "t1", state="open")],
            [entry("a", "t2"), entry("b", "t9")],
        )
        from_mappings = diff_manifests(
            [{"source_id": "a", "revision_token": "t1", "state": "open"}],
            [
                {"source_id": "a", "revision_token": "t2", "state": ""},
                {"source_id": "b", "revision_token": "t9"},  # state optional
            ],
        )
        assert from_objects == from_mappings

    def test_determinism_regardless_of_input_order(self):
        stored = [entry("b", "t2"), entry("a", "t1"), entry("c", "t3")]
        current = [
            entry("c", "t3x"),
            entry("d", "t4"),
            entry("a", "t1"),
            entry("b", "t2x"),
        ]
        one = diff_manifests(stored, current)
        two = diff_manifests(list(reversed(stored)), list(reversed(current)))
        assert one == two
        assert [e.source_id for e in one.added] == ["d"]
        assert [e.source_id for e in one.removed] == []
        assert [e.source_id for e in one.changed] == ["b", "c"]
        assert [e.source_id for e in one.unchanged] == ["a"]

    def test_state_only_difference_is_unchanged(self):
        """FR-31: state strings are informational; only the token signals."""
        diff = diff_manifests(
            [entry("a", "t1", state="working")], [entry("a", "t1", state="idle")]
        )
        assert diff.unchanged == (entry("a", "t1", state="idle"),)
        assert diff.any_change() is False

    def test_any_change_true_for_each_bucket(self):
        assert diff_manifests([], [entry("a", "t1")]).any_change()
        assert diff_manifests([entry("a", "t1")], []).any_change()
        assert diff_manifests([entry("a", "t1")], [entry("a", "t2")]).any_change()


class TestManifestMalformed:
    """Fail loud, never silently ignore a row (a dropped row would
    fabricate or HIDE a change). The store itself always yields real
    ``ManifestEntry`` objects; mapping rows are the hand-built path."""

    def test_mapping_missing_token_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests([{"source_id": "a", "state": "open"}], [])

    def test_mapping_missing_source_id_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests([], [{"revision_token": "t1"}])

    def test_mapping_non_string_token_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests([{"source_id": "a", "revision_token": 7, "state": ""}], [])

    def test_mapping_none_state_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests(
                [{"source_id": "a", "revision_token": "t", "state": None}], []
            )

    def test_mapping_unexpected_key_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests(
                [{"source_id": "a", "revision_token": "t", "state": "", "bogus": 1}],
                [],
            )

    def test_non_mapping_non_entry_item_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests(["opencode:ses_1"], [])

    def test_duplicate_source_id_within_one_side_fails_loud(self):
        with pytest.raises(FreshnessConfigError):
            diff_manifests([entry("a", "t1"), entry("a", "t2")], [])


class TestPlanRebuild:
    def test_full_current_source_set_with_full_scan(self):
        diff = ManifestDiff(
            added=(entry("c", "t3"),),
            removed=(entry("d", "t4"),),
            changed=(entry("e", "t5"),),
            unchanged=(entry("b", "t2"), entry("a", "t1")),
        )
        plan = plan_rebuild(diff)
        # FR-30/FR-32: everything CURRENT is read (removed excluded).
        assert plan.sources_to_read == ("a", "b", "c", "e")
        assert plan.full_scan is True
        assert "full-scan" in plan.reason

    def test_empty_diff_still_reads_every_current_source(self):
        diff = ManifestDiff((), (), (), (entry("b", "t2"), entry("a", "t1")))
        assert plan_rebuild(diff).sources_to_read == ("a", "b")

    def test_deadline_is_carried_not_enforced(self):
        deadline = Deadline.from_remaining(FakeMono(), 30.0)
        plan = plan_rebuild(diff_manifests([], [entry("a", "t1")]), deadline=deadline)
        assert plan.deadline is deadline
        assert plan.sources_to_read == ("a",)

    def test_none_deadline_is_allowed(self):
        plan = plan_rebuild(diff_manifests([], []))
        assert plan.deadline is None
        assert plan.sources_to_read == ()


# -- FreshnessChecker fixtures (store conventions from test_reportstore) ---


def fake_clock(start=None):
    """Injectable store clock: call it for now, .advance(**td kwargs)."""
    holder = {"now": start or NOW}

    def clock():
        return holder["now"]

    def advance(**kwargs):
        holder["now"] = holder["now"] + timedelta(**kwargs)

    clock.advance = advance
    return clock


PERIOD = period_today(NOW, MADRID)
WEEK = period_this_week(NOW, MADRID)
KINDS = ("opencode", "engram")

STORED_MANIFEST = [
    {"source_id": "opencode:ses_1", "revision_token": "rev-1", "state": "open"},
    {"source_id": "engram:proj", "revision_token": "rev-9", "state": ""},
]


def make_key(context="call-1", scope="global", period=None):
    period = period or PERIOD
    return ReportKey(
        main_call_context_id=context,
        scope=scope,
        interval_key=period.key,
        timezone=period.zone_name,
    )


def src(source_id, token):
    kind = source_id.split(":", 1)[0]
    return Source(
        source_id=source_id,
        kind=kind,
        project="proj",
        locator=f"loc:{source_id}",
        revision_token=token,
        observed_at=NOW,
    )


def ok(*sources):
    return InventoryResult(CoverageStatus.OK, sources=tuple(sources))


def absent():
    return InventoryResult(CoverageStatus.SOURCE_ABSENT)


def failed(detail="provider exploded"):
    return InventoryResult(CoverageStatus.COVERAGE_FAILED, error_detail=detail)


def matching_results():
    """Inventories exactly matching STORED_MANIFEST."""
    return {
        "opencode": ok(src("opencode:ses_1", "rev-1")),
        "engram": ok(src("engram:proj", "rev-9")),
    }


@pytest.fixture
def clock():
    return fake_clock()


@pytest.fixture
def store(tmp_path, clock):
    return ReportStore(tmp_path / "reports.db", clock=clock)


@pytest.fixture
def checker(store):
    return FreshnessChecker(store, configured_kinds=KINDS)


def publish_stored(store, key=None, manifest=None):
    key = key or make_key()
    handle = store.begin_build(key)
    record = store.publish(
        handle,
        body="consolidated brief",
        references=[],
        source_manifest=manifest if manifest is not None else STORED_MANIFEST,
    )
    return key, record


class TestSealedFamily:
    def test_variants_subclass_the_base(self):
        for variant in (Reuse, Rebuild, Unable):
            assert issubclass(variant, FreshnessOutcome)

    def test_outcomes_are_frozen(self):
        with pytest.raises(FrozenInstanceError):
            Reuse(report=None).report = None  # type: ignore[misc]


class TestAuthorityStep:
    def test_unknown_provider_kind_raises(self, checker):
        results = dict(matching_results(), claude=ok())
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, results)

    def test_missing_configured_kind_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, {"opencode": ok()})

    def test_source_kind_outside_configured_raises(self, checker):
        rogue = Source(
            source_id="claude:ses_9",
            kind="claude",
            project="proj",
            locator="loc",
            revision_token="r",
            observed_at=NOW,
        )
        results = {"opencode": ok(), "engram": ok(rogue)}
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, results)

    def test_authority_precedes_inventory_health(self, checker):
        results = {"opencode": failed(), "engram": ok(), "claude": absent()}
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, results)

    def test_non_inventory_result_value_raises(self, checker):
        results = {"opencode": "not-a-result", "engram": ok()}
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, results)

    def test_non_mapping_results_raise(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, [ok()])

    def test_blank_interval_key_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), "  ", matching_results())

    def test_constructor_rejects_empty_kinds(self, store):
        with pytest.raises(FreshnessConfigError):
            FreshnessChecker(store, configured_kinds=())

    def test_constructor_rejects_duplicate_kinds(self, store):
        with pytest.raises(FreshnessConfigError):
            FreshnessChecker(store, configured_kinds=("opencode", "opencode"))


class TestInventoryHealthStep:
    def test_coverage_failed_is_unable_and_never_reuses(self, checker, store):
        """Everything else matches the stored snapshot; the failed kind
        alone makes reuse unverifiable and rebuild dishonest (FR-20)."""
        key, _ = publish_stored(store)
        results = {
            "opencode": ok(src("opencode:ses_1", "rev-1")),
            "engram": failed("engram db locked"),
        }
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Unable)
        assert outcome.failed_providers == (("engram", "engram db locked"),)
        assert not isinstance(outcome, Reuse)  # NEVER the stored report

    def test_multiple_failures_are_all_reported_sorted(self, checker):
        results = {"opencode": failed("a"), "engram": failed("b")}
        outcome = checker.evaluate(make_key(), PERIOD.key, results)
        assert isinstance(outcome, Unable)
        assert outcome.failed_providers == (("engram", "b"), ("opencode", "a"))

    def test_health_precedes_stored_report_step(self, checker):
        # No stored report AND a failed provider: Unable wins, not Rebuild.
        outcome = checker.evaluate(
            make_key(), PERIOD.key, {"opencode": failed(), "engram": ok()}
        )
        assert isinstance(outcome, Unable)


class TestStoredReportStep:
    def test_no_stored_report_rebuilds(self, checker, store):
        outcome = checker.evaluate(make_key(), PERIOD.key, matching_results())
        assert isinstance(outcome, Rebuild)
        assert "no stored report" in outcome.reason
        # Diff against the empty set: everything current is new.
        assert [e.source_id for e in outcome.diff.added] == [
            "engram:proj",
            "opencode:ses_1",
        ]
        assert outcome.diff.unchanged == () and outcome.diff.removed == ()

    def test_expired_stored_report_rebuilds(self, checker, store, clock):
        key, _ = publish_stored(store)
        clock.advance(hours=25)  # past the 24h retention horizon
        assert store.get_current(key) is None  # expiry removes eligibility
        outcome = checker.evaluate(key, PERIOD.key, matching_results())
        assert isinstance(outcome, Rebuild)
        assert "no stored report" in outcome.reason

    def test_refresh_failed_stored_report_rebuilds(self, checker, store):
        key, _ = publish_stored(store)
        store.mark_refresh_failed(key)
        # Documents the path: refresh_failed is hidden from get_current
        # (never served as current, FR-33) but visible via get_latest.
        assert store.get_current(key) is None
        assert store.get_latest(key).status == "refresh_failed"
        outcome = checker.evaluate(key, PERIOD.key, matching_results())
        assert isinstance(outcome, Rebuild)
        assert "refresh_failed" in outcome.reason

    def test_interval_movement_rebuilds_even_with_identical_manifests(
        self, checker, store
    ):
        key, _ = publish_stored(store)  # stored under PERIOD
        outcome = checker.evaluate(key, WEEK.key, matching_results())  # same sources
        assert isinstance(outcome, Rebuild)
        assert outcome.diff.any_change() is False  # manifests identical
        assert "interval" in outcome.reason  # the window moved (D08)
        assert outcome.full_scan is True


class TestManifestStep:
    def test_identical_everything_reuses(self, checker, store):
        key, record = publish_stored(store)
        outcome = checker.evaluate(key, PERIOD.key, matching_results())
        assert isinstance(outcome, Reuse)
        assert outcome.report.report_id == record.report_id

    def test_single_token_change_rebuilds_and_reason_names_it(
        self, checker, store
    ):
        key, _ = publish_stored(store)
        results = {
            "opencode": ok(src("opencode:ses_1", "rev-2")),
            "engram": ok(src("engram:proj", "rev-9")),
        }
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Rebuild)
        assert [e.source_id for e in outcome.diff.changed] == ["opencode:ses_1"]
        assert "opencode:ses_1" in outcome.reason and "changed" in outcome.reason

    def test_source_added_rebuilds(self, checker, store):
        key, _ = publish_stored(store)
        results = {
            "opencode": ok(src("opencode:ses_1", "rev-1"), src("opencode:ses_2", "rev-2")),
            "engram": ok(src("engram:proj", "rev-9")),
        }
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Rebuild)
        assert [e.source_id for e in outcome.diff.added] == ["opencode:ses_2"]
        assert "added" in outcome.reason

    def test_source_removed_rebuilds(self, checker, store):
        # Provider healthy but reports fewer sources: ses_1 is gone.
        key, _ = publish_stored(store)
        results = {"opencode": ok(), "engram": ok(src("engram:proj", "rev-9"))}
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Rebuild)
        assert [e.source_id for e in outcome.diff.removed] == ["opencode:ses_1"]
        assert "removed" in outcome.reason

    def test_absent_provider_with_stored_sources_rebuilds_via_removed(
        self, checker, store
    ):
        """opencode previously present, now its storage is gone: the
        provider reports SOURCE_ABSENT (valid empty), and the stored
        sources land in removed -> REBUILD."""
        key, _ = publish_stored(store)
        results = {"opencode": absent(), "engram": ok(src("engram:proj", "rev-9"))}
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Rebuild)
        assert [e.source_id for e in outcome.diff.removed] == ["opencode:ses_1"]

    def test_absent_provider_never_present_plus_identical_rest_reuses(
        self, checker, store
    ):
        stored = [
            m for m in STORED_MANIFEST if not m["source_id"].startswith("opencode")
        ]
        key, record = publish_stored(store, manifest=stored)
        results = {"opencode": absent(), "engram": ok(src("engram:proj", "rev-9"))}
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Reuse)
        assert outcome.report.report_id == record.report_id

    def test_rebuild_diff_feeds_full_scan_plan(self, checker, store):
        key, _ = publish_stored(store)
        results = {
            "opencode": ok(src("opencode:ses_1", "rev-2"), src("opencode:ses_2", "rev-2")),
            "engram": ok(src("engram:proj", "rev-9")),
        }
        outcome = checker.evaluate(key, PERIOD.key, results)
        assert isinstance(outcome, Rebuild)
        plan = plan_rebuild(
            outcome.diff, deadline=Deadline.from_remaining(FakeMono(), 60.0)
        )
        assert plan.sources_to_read == (
            "engram:proj",
            "opencode:ses_1",
            "opencode:ses_2",
        )
        assert plan.full_scan is True


class TestExpectedKinds:
    """The per-evaluation IN-SCOPE kind subset (D02: a global
    current-progress query checks only the herdr provider). Honest
    scoping, never a weakened authority gate: the subset must be
    non-empty and within the configured maximum authority, results
    must cover it EXACTLY, and out-of-scope kinds — configured or not
    — are rejected rather than reported as absent (FR-41 stays
    intact)."""

    def test_explicit_subset_reuses_matching_stored_report(self, checker, store):
        key, record = publish_stored(
            store,
            manifest=[
                {"source_id": "opencode:ses_1", "revision_token": "rev-1", "state": ""}
            ],
        )
        results = {"opencode": ok(src("opencode:ses_1", "rev-1"))}
        outcome = checker.evaluate(
            key, PERIOD.key, results, expected_kinds=("opencode",)
        )
        assert isinstance(outcome, Reuse)
        assert outcome.report.report_id == record.report_id

    def test_subset_rebuild_detects_removal_of_out_of_scope_sources(
        self, checker, store
    ):
        """A stored snapshot that still contains out-of-scope sources
        (a pre-scope mixed report) is honestly rebuilt: those sources
        land in ``removed`` and can never be silently reused."""
        key, _ = publish_stored(store)  # opencode:ses_1 + engram:proj
        results = {"opencode": ok(src("opencode:ses_1", "rev-1"))}
        outcome = checker.evaluate(
            key, PERIOD.key, results, expected_kinds=("opencode",)
        )
        assert isinstance(outcome, Rebuild)
        assert [e.source_id for e in outcome.diff.removed] == ["engram:proj"]
        assert "removed" in outcome.reason

    def test_missing_in_scope_kind_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(),
                PERIOD.key,
                {"opencode": ok()},
                expected_kinds=("opencode", "engram"),
            )

    def test_out_of_scope_but_configured_kind_in_results_raises(self, checker):
        results = matching_results()  # opencode + engram
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(), PERIOD.key, results, expected_kinds=("opencode",)
            )

    def test_subset_outside_configured_authority_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(),
                PERIOD.key,
                {"opencode": ok()},
                expected_kinds=("opencode", "claude"),
            )

    def test_empty_subset_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(), PERIOD.key, matching_results(), expected_kinds=()
            )

    def test_duplicate_subset_kinds_raise(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(),
                PERIOD.key,
                matching_results(),
                expected_kinds=("opencode", "opencode"),
            )

    def test_non_string_subset_entry_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(), PERIOD.key, matching_results(), expected_kinds=(7,)
            )

    def test_non_iterable_subset_raises(self, checker):
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(), PERIOD.key, matching_results(), expected_kinds=42
            )

    def test_source_kind_outside_the_subset_raises(self, checker):
        # The engram kind is CONFIGURED but out of scope for this
        # evaluation: a source of that kind under an in-scope result
        # must fail loudly (scope is explicit, never inferred from
        # source ids).
        results = {"opencode": ok(src("engram:proj", "rev-9"))}
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(
                make_key(), PERIOD.key, results, expected_kinds=("opencode",)
            )

    def test_subset_failed_provider_is_unable(self, checker, store):
        key, _ = publish_stored(store)
        results = {"opencode": failed("db locked")}
        outcome = checker.evaluate(
            key, PERIOD.key, results, expected_kinds=("opencode",)
        )
        assert isinstance(outcome, Unable)
        assert outcome.failed_providers == (("opencode", "db locked"),)

    def test_default_stays_the_exact_configured_set(self, checker):
        # No expected_kinds: the historical default — every configured
        # kind must be present (FR-41 unchanged).
        with pytest.raises(FreshnessConfigError):
            checker.evaluate(make_key(), PERIOD.key, {"opencode": ok()})
