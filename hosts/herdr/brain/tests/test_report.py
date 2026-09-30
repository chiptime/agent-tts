"""Unit tests for the report consolidation layer (FR-13, FR-14, FR-15,
FR-16, FR-19, FR-20, FR-22, FR-25; PRD decisions D05, D06, D07).

The consolidation layer is deterministic: these tests never call an LLM,
never read the real clock, and construct every Source/EvidenceItem/
CoverageResult/InventoryResult by hand. Rendering and validation are
pure functions of their arguments.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from herdr_brain.evidence import (
    CoverageResult,
    CoverageStatus,
    EvidenceItem,
    InventoryResult,
    Source,
)

from herdr_brain.report import (
    NO_WORK_PHRASE,
    UNKNOWN_PROJECT_LABEL,
    BriefDocument,
    BriefSection,
    BriefValidation,
    ProjectBundle,
    ReportScaffold,
    assert_no_references,
    build_incompleteness_note,
    build_references,
    build_scaffold,
    display_project,
    render_screen,
    render_spoken,
    validate_brief,
)

UTC = timezone.utc
OBSERVED_AT = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
LATER_AT = datetime(2026, 9, 30, 13, 0, 0, tzinfo=UTC)


def source(**overrides) -> Source:
    """Source with stable defaults; any field overridable."""
    fields = dict(
        source_id="opencode:/repo/a",
        kind="opencode",
        project="/repo/a",
        locator="/repo/a/session-1",
        revision_token="tok-a1",
        observed_at=OBSERVED_AT,
        title="Session A",
        state="working",
    )
    fields.update(overrides)
    return Source(**fields)


def item(**overrides) -> EvidenceItem:
    """EvidenceItem with stable defaults; any field overridable."""
    fields = dict(
        source_id="opencode:/repo/a",
        kind="opencode",
        timestamp=OBSERVED_AT,
        role="assistant",
        text="Refactored the parser",
    )
    fields.update(overrides)
    return EvidenceItem(**fields)


def ok_collect(src: Source, items=()) -> CoverageResult:
    return CoverageResult(src, CoverageStatus.OK, tuple(items))


def absent_collect(src: Source) -> CoverageResult:
    return CoverageResult(src, CoverageStatus.SOURCE_ABSENT)


def failed_collect(src: Source, detail="unreadable") -> CoverageResult:
    return CoverageResult(src, CoverageStatus.COVERAGE_FAILED, error_detail=detail)


def failed_inventory(kind: str, detail="provider down") -> InventoryResult:
    return InventoryResult(CoverageStatus.COVERAGE_FAILED, error_detail=detail)


class TestBuildReferences:
    def test_sorted_by_source_id_regardless_of_input_order(self):
        alpha = source(source_id="opencode:/repo/a")
        beta = source(source_id="herdr:w1:p2", kind="herdr_session", locator="w1:p2")
        gamma = source(source_id="claude:/repo/c", kind="claude")
        refs = build_references([gamma, alpha, beta])
        assert [r.source_id for r in refs] == [
            "claude:/repo/c",
            "herdr:w1:p2",
            "opencode:/repo/a",
        ]

    def test_reference_fields_come_from_the_source(self):
        src = source()
        ref = build_references([src])[0]
        assert ref.source_id == src.source_id
        assert ref.kind == src.kind
        assert ref.locator == src.locator
        assert ref.revision_token == src.revision_token
        # retrieved_at is the observation instant, ISO 8601 (FR-40).
        assert ref.retrieved_at == src.observed_at.isoformat()

    def test_deterministic_for_identical_inputs(self):
        srcs = [source(source_id=f"opencode:/repo/{name}") for name in ("b", "a", "c")]
        shuffled = [srcs[2], srcs[0], srcs[1]]
        assert build_references(srcs) == build_references(shuffled)

    def test_empty_input_yields_empty_list(self):
        assert build_references([]) == []


class TestScaffoldGrouping:
    def test_groups_sources_and_items_by_project(self):
        src_a = source(source_id="opencode:/repo/a", project="/repo/a")
        src_b = source(
            source_id="claude:/repo/b", kind="claude", project="/repo/b", state=None
        )
        item_a = item(source_id="opencode:/repo/a", kind="opencode")
        item_b = item(
            source_id="claude:/repo/b",
            kind="claude",
            role="user",
            text="please continue",
        )
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src_a, src_b))},
            [ok_collect(src_a, [item_a]), ok_collect(src_b, [item_b])],
        )
        assert [b.project for b in scaffold.bundles] == ["/repo/a", "/repo/b"]
        bundle_a = scaffold.bundles[0]
        assert tuple(s.source_id for s in bundle_a.sources) == ("opencode:/repo/a",)
        assert tuple(i.text for i in bundle_a.items) == ("Refactored the parser",)
        bundle_b = scaffold.bundles[1]
        assert tuple(i.text for i in bundle_b.items) == ("please continue",)

    def test_bundles_sorted_by_project_name(self):
        names = ["/zeta", "/alpha", "/mid"]
        srcs = [
            source(source_id=f"opencode:{n}", project=n) for n in names
        ]
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, tuple(srcs))},
            [absent_collect(s) for s in srcs],
        )
        assert [b.project for b in scaffold.bundles] == ["/alpha", "/mid", "/zeta"]

    def test_empty_string_project_bucket_is_kept_and_labeled(self):
        # Antigravity sources carry "" as their project: they group into
        # ONE "" bucket whose key stays "" (round-trips with filters) and
        # whose DISPLAY name is the documented label.
        src = source(source_id="antigravity:x", kind="antigravity", project="", locator="x")
        scaffold = build_scaffold(
            {"antigravity": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [item(source_id="antigravity:x", kind="antigravity")])],
        )
        assert [b.project for b in scaffold.bundles] == [""]
        assert display_project("") == UNKNOWN_PROJECT_LABEL
        assert display_project("/repo/a") == "/repo/a"

    def test_items_are_restricted_to_their_own_project(self):
        src_a = source(source_id="opencode:/repo/a", project="/repo/a")
        src_b = source(source_id="opencode:/repo/b", project="/repo/b")
        item_for_b = item(source_id="opencode:/repo/b")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src_a, src_b))},
            [
                ok_collect(src_b, [item_for_b]),
                ok_collect(src_a, []),
            ],
        )
        bundle_a = scaffold.bundles[0]
        bundle_b = scaffold.bundles[1]
        assert bundle_a.items == ()
        assert bundle_b.items == (item_for_b,)

    def test_status_snapshots_recorded_verbatim_and_sorted(self):
        # FR-04: statuses are recorded verbatim (never interpreted as
        # unfinished); entries are (source_id, state) pairs by source_id.
        src_working = source(
            source_id="herdr:w1:p1",
            kind="herdr_session",
            project="/repo/a",
            locator="w1:p1",
            state="working",
        )
        src_idle = source(
            source_id="herdr:w1:p0",
            kind="herdr_session",
            project="/repo/a",
            locator="w1:p0",
            state="idle",
        )
        scaffold = build_scaffold(
            {"herdr_session": InventoryResult(CoverageStatus.OK, (src_working, src_idle))},
            [ok_collect(src_working), ok_collect(src_idle)],
        )
        bundle = scaffold.bundles[0]
        assert bundle.status_snapshots == (
            ("herdr:w1:p0", "idle"),
            ("herdr:w1:p1", "working"),
        )

    def test_sources_without_state_have_no_status_snapshot(self):
        src = source(state=None)
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src)],
        )
        assert scaffold.bundles[0].status_snapshots == ()

    def test_references_are_built_per_bundle_sorted_by_source_id(self):
        src_b = source(source_id="opencode:/repo/b", project="/repo/shared")
        src_a = source(source_id="opencode:/repo/a", project="/repo/shared")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src_b, src_a))},
            [ok_collect(src_b), ok_collect(src_a)],
        )
        refs = scaffold.bundles[0].references
        assert [r.source_id for r in refs] == ["opencode:/repo/a", "opencode:/repo/b"]
        assert refs[0].retrieved_at == src_a.observed_at.isoformat()

    def test_sources_from_inventory_only_still_form_a_bundle(self):
        # A discovered-but-uncollected source still belongs to its
        # project bundle (with zero items); the caller is expected to
        # pass every collect result it has.
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))}, []
        )
        assert len(scaffold.bundles) == 1
        assert scaffold.bundles[0].sources == (src,)

    def test_scaffold_is_frozen(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))}, [ok_collect(src)]
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            scaffold.complete = False
        with pytest.raises(dataclasses.FrozenInstanceError):
            scaffold.bundles[0].project = "x"

    def test_bundle_shapes_are_tuples(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [item()])],
        )
        bundle = scaffold.bundles[0]
        assert isinstance(bundle.sources, tuple)
        assert isinstance(bundle.items, tuple)
        assert isinstance(bundle.status_snapshots, tuple)
        assert isinstance(bundle.references, tuple)
        assert isinstance(scaffold.bundles, tuple)
        assert isinstance(scaffold.coverage_failures, tuple)


class TestScaffoldEmptyReason:
    def test_sources_with_ok_collect_but_zero_items_is_no_evidence(self):
        # FR-22: an exhaustively covered empty scope is a VALID no-work
        # result, never a failure.
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [])],
        )
        assert scaffold.bundles[0].empty_reason == "no_evidence"

    def test_only_source_absent_coverage_is_source_absent(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [absent_collect(src)],
        )
        assert scaffold.bundles[0].empty_reason == "source_absent"

    def test_mixed_absent_and_ok_zero_items_is_no_evidence(self):
        # Mixed coverage means the sources were readable and simply had
        # nothing in interval: that is no-work, not absence.
        src_a = source(source_id="opencode:/repo/a", project="/repo/a")
        src_b = source(source_id="opencode:/repo/b", project="/repo/a")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src_a, src_b))},
            [absent_collect(src_a), ok_collect(src_b, [])],
        )
        assert scaffold.bundles[0].empty_reason == "no_evidence"

    def test_bundle_with_items_has_no_empty_reason(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [item()])],
        )
        assert scaffold.bundles[0].empty_reason is None

    def test_discovered_but_uncollected_source_is_no_evidence(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))}, []
        )
        assert scaffold.bundles[0].empty_reason == "no_evidence"


class TestScaffoldCoverageFailures:
    def test_failed_collect_lands_in_failures_not_bundles(self):
        # FR-20/FR-11: a failed read is announced, never presented as an
        # empty success; with no other evidence there is NO bundle.
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [failed_collect(src, detail="sqlite locked")],
        )
        assert scaffold.complete is False
        assert scaffold.coverage_failures == (("opencode:/repo/a", "sqlite locked"),)
        assert scaffold.bundles == ()

    def test_failed_inventory_lands_in_failures_keyed_by_kind(self):
        src = source()
        scaffold = build_scaffold(
            {
                "opencode": failed_inventory("opencode", "db gone"),
                "claude": InventoryResult(CoverageStatus.OK),
            },
            [ok_collect(src, [item()])],
        )
        assert scaffold.complete is False
        assert ("opencode", "db gone") in scaffold.coverage_failures
        assert len(scaffold.bundles) == 1  # the ok collect still bundles

    def test_complete_scaffold_when_nothing_failed(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [item()])],
        )
        assert scaffold.complete is True
        assert scaffold.coverage_failures == ()

    def test_failed_source_excluded_from_otherwise_populated_project(self):
        src_dead = source(source_id="opencode:/repo/dead", project="/repo/a")
        src_live = source(source_id="opencode:/repo/live", project="/repo/a")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src_dead, src_live))},
            [
                failed_collect(src_dead, detail="timeout"),
                ok_collect(src_live, [item(source_id="opencode:/repo/live")]),
            ],
        )
        bundle = scaffold.bundles[0]
        assert tuple(s.source_id for s in bundle.sources) == ("opencode:/repo/live",)
        assert bundle.empty_reason is None
        assert ("opencode:/repo/dead", "timeout") in scaffold.coverage_failures

    def test_items_from_failed_results_are_not_evidence(self):
        src = source()
        poisoned = item(text="phantom progress")
        result = CoverageResult(
            src, CoverageStatus.COVERAGE_FAILED, (poisoned,), error_detail="locked"
        )
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))}, [result]
        )
        assert scaffold.bundles == ()
        assert scaffold.complete is False

    def test_items_from_absent_results_are_ignored(self):
        # SOURCE_ABSENT semantically means nothing was there; attached
        # items would be a provider bug and are never shown as evidence.
        src = source()
        result = CoverageResult(src, CoverageStatus.SOURCE_ABSENT, (item(),))
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))}, [result]
        )
        assert scaffold.bundles[0].items == ()
        assert scaffold.bundles[0].empty_reason == "source_absent"

    def test_multiple_failures_keep_provider_then_collect_order(self):
        src_a = source(source_id="opencode:/repo/a", project="/repo/a")
        src_b = source(source_id="opencode:/repo/b", project="/repo/b")
        scaffold = build_scaffold(
            {"engram": failed_inventory("engram", "no observations table")},
            [failed_collect(src_b, "b dead"), failed_collect(src_a, "a dead")],
        )
        assert [entry[0] for entry in scaffold.coverage_failures] == [
            "engram",
            "opencode:/repo/b",
            "opencode:/repo/a",
        ]


class TestScaffoldUndatedItems:
    def test_undated_items_are_included_and_counted(self):
        # FR-10 visibility: unknown timestamps are never excluded (the
        # providers already period-filtered) and never dated by proxy.
        src = source()
        dated = item(text="dated work")
        undated = item(timestamp=None, text="undated work")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [undated, dated])],
        )
        bundle = scaffold.bundles[0]
        assert len(bundle.items) == 2
        assert bundle.undated_items == 1
        assert undated in bundle.items

    def test_undated_items_sort_after_dated_ones(self):
        src = source()
        late = item(timestamp=LATER_AT, text="late")
        early = item(timestamp=OBSERVED_AT, text="early")
        undated = item(timestamp=None, text="undated")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [late, undated, early])],
        )
        assert [i.text for i in scaffold.bundles[0].items] == [
            "early",
            "late",
            "undated",
        ]


class TestScaffoldProjectFilter:
    def test_filter_narrows_to_exact_and_under_path_matches(self):
        # Same matching rule as the evidence layer: exact cwd or a
        # subdirectory under it; "/repo" never matches "/repo2".
        inside = source(source_id="opencode:/repo/sub", project="/repo/sub")
        exact = source(source_id="opencode:/repo", project="/repo")
        outside = source(source_id="opencode:/repo2", project="/repo2")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (inside, exact, outside))},
            [
                ok_collect(inside, [item(source_id="opencode:/repo/sub")]),
                ok_collect(exact, [item(source_id="opencode:/repo")]),
                ok_collect(outside, [item(source_id="opencode:/repo2")]),
            ],
            project_filter="/repo",
        )
        assert [b.project for b in scaffold.bundles] == ["/repo", "/repo/sub"]

    def test_no_filter_keeps_everything(self):
        a = source(source_id="opencode:/x", project="/x")
        b = source(source_id="opencode:/y", project="/y")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (a, b))},
            [ok_collect(a), ok_collect(b)],
            project_filter=None,
        )
        assert [bnd.project for bnd in scaffold.bundles] == ["/x", "/y"]

    def test_empty_string_project_matches_only_absent_filter(self):
        # Mirrors the Antigravity rule: "" matches no concrete filter.
        unknown = source(source_id="antigravity:x", kind="antigravity", project="", locator="x")
        scaffold = build_scaffold(
            {"antigravity": InventoryResult(CoverageStatus.OK, (unknown,))},
            [ok_collect(unknown, [item(source_id="antigravity:x", kind="antigravity")])],
            project_filter="/repo",
        )
        assert scaffold.bundles == ()


class TestScaffoldProvenanceValidation:
    def test_item_attributed_to_the_wrong_source_raises(self):
        src = source()
        alien = item(source_id="opencode:/elsewhere")
        with pytest.raises(ValueError, match="attributable"):
            build_scaffold(
                {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
                [ok_collect(src, [alien])],
            )

    def test_bundle_rejects_unknown_empty_reason(self):

        with pytest.raises(ValueError, match="empty_reason"):
            ProjectBundle("", (), (), (), (), "finished", 0)

    def test_scaffold_rejects_complete_over_failures(self):

        with pytest.raises(ValueError, match="completeness"):
            ReportScaffold((), (("opencode", "down"),), complete=True)

    def test_bundle_rejects_non_tuple_fields(self):

        with pytest.raises(ValueError, match="tuple"):
            ProjectBundle("", [], (), (), (), None, 0)


def section(**overrides):
    """BriefSection with stable defaults; any field overridable."""

    fields = dict(
        project="/repo/a",
        advances=("Parser refactor landed",),
        pending=("Write migration notes",),
        blockers=("CI flakiness",),
        no_work=False,
        conflict_notes=(),
        citations=("opencode:/repo/a",),
    )
    fields.update(overrides)
    return BriefSection(**fields)


def brief_doc(sections, **overrides):

    fields = dict(
        context_id="call-1",
        interval_label="today (2026-09-30, Europe/Madrid)",
        timezone_label="Europe/Madrid",
        sections=tuple(sections),
        generated_note="",
    )
    fields.update(overrides)
    return BriefDocument(**fields)


def happy_scaffold():
    src = source()
    return build_scaffold(
        {"opencode": InventoryResult(CoverageStatus.OK, (src,))},
        [ok_collect(src, [item()])],
    )


class TestBriefShapes:
    def test_brief_section_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            section().project = "/other"

    def test_brief_document_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            brief_doc([section()]).generated_note = "x"

    def test_brief_section_rejects_non_tuple_lists(self):

        with pytest.raises(ValueError, match="tuple"):
            BriefSection("/repo/a", ["not a tuple"], (), (), False, (), ())

    def test_brief_document_rejects_non_string_labels(self):
        with pytest.raises(ValueError, match="interval_label"):
            brief_doc([], interval_label="  ")


class TestValidateBriefHappyPath:
    def test_grounded_doc_over_complete_scaffold_is_ok(self):
        scaffold = happy_scaffold()
        result = validate_brief(brief_doc([section()]), scaffold)
        assert result.ok is True
        assert result.errors == ()

    def test_empty_scaffold_with_no_sections_is_ok(self):
        result = validate_brief(brief_doc([]), build_scaffold({}, []))
        assert result.ok is True

    def test_ok_is_false_exactly_when_errors_exist(self):

        assert BriefValidation(()).ok is True
        assert BriefValidation(("something",)).ok is False


class TestValidateBriefSections:
    def test_missing_section_for_a_scaffold_project_errors(self):
        result = validate_brief(brief_doc([]), happy_scaffold())
        assert result.ok is False
        assert any("missing" in e and "/repo/a" in e for e in result.errors)

    def test_extra_section_for_unknown_project_errors(self):
        result = validate_brief(
            brief_doc([section(), section(project="/ghost")]), happy_scaffold()
        )
        assert result.ok is False
        assert any("/ghost" in e for e in result.errors)

    def test_duplicate_sections_for_one_project_error(self):
        result = validate_brief(
            brief_doc([section(), section()]), happy_scaffold()
        )
        assert result.ok is False
        assert any("duplicate" in e or "exactly one" in e for e in result.errors)

    def test_unknown_project_bucket_section_matches_empty_string_bundle(self):
        src = source(source_id="antigravity:x", kind="antigravity", project="", locator="x")
        scaffold = build_scaffold(
            {"antigravity": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [item(source_id="antigravity:x", kind="antigravity")])],
        )
        result = validate_brief(
            brief_doc([section(project="", citations=("antigravity:x",))]), scaffold
        )
        assert result.ok is True


class TestValidateBriefCitations:
    def test_unknown_citation_errors(self):
        result = validate_brief(
            brief_doc([section(citations=("opencode:/made-up",))]), happy_scaffold()
        )
        assert result.ok is False
        assert any("opencode:/made-up" in e for e in result.errors)

    def test_citation_from_another_projects_source_is_valid(self):
        # Documented looseness: citations are validated scaffold-wide,
        # not per-section — a claim may lean on any read source.
        src_a = source(source_id="opencode:/repo/a", project="/repo/a")
        src_b = source(source_id="opencode:/repo/b", project="/repo/b")
        scaffold = build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src_a, src_b))},
            [ok_collect(src_a, [item()]), ok_collect(src_b, [item(source_id="opencode:/repo/b")])],
        )
        doc = brief_doc(
            [
                section(project="/repo/a", citations=("opencode:/repo/b",)),
                section(project="/repo/b", citations=("opencode:/repo/a",)),
            ]
        )
        assert validate_brief(doc, scaffold).ok is True


class TestValidateBriefNoWork:
    def _empty_scaffold(self, reason: str):
        src = source()
        result = (
            absent_collect(src) if reason == "source_absent" else ok_collect(src, [])
        )
        return build_scaffold(
            {"opencode": InventoryResult(CoverageStatus.OK, (src,))}, [result]
        )

    def test_no_work_over_real_evidence_is_rejected(self):
        # FR-15: a no-work claim over actual evidence is a grounding
        # violation, not a style choice.
        result = validate_brief(brief_doc([section(no_work=True)]), happy_scaffold())
        assert result.ok is False
        assert any("no_work" in e or "no-work" in e for e in result.errors)

    def test_no_work_on_source_absent_bundle_is_allowed(self):
        doc = brief_doc(
            [section(no_work=True, advances=(), pending=(), blockers=(), citations=())]
        )
        assert validate_brief(doc, self._empty_scaffold("source_absent")).ok is True

    def test_no_work_on_no_evidence_bundle_is_allowed(self):
        doc = brief_doc(
            [section(no_work=True, advances=(), pending=(), blockers=(), citations=())]
        )
        assert validate_brief(doc, self._empty_scaffold("no_evidence")).ok is True

    def test_empty_reason_bundle_without_no_work_flag_still_valid(self):
        # no_work is the model's claim; the validator only forbids the
        # dishonest direction.
        result = validate_brief(
            brief_doc([section(advances=(), pending=(), blockers=(), citations=())]),
            self._empty_scaffold("no_evidence"),
        )
        assert result.ok is True


class TestValidateBriefIncompleteness:
    def test_incomplete_scaffold_without_note_is_rejected(self):
        # FR-20: never present partial coverage as complete.
        src = source()
        scaffold = build_scaffold(
            {"opencode": failed_inventory("opencode", "db gone")},
            [ok_collect(src, [item()])],
        )
        result = validate_brief(brief_doc([section()]), scaffold)
        assert result.ok is False
        assert any("incomplete" in e for e in result.errors)

    def test_incomplete_scaffold_with_note_is_accepted(self):
        src = source()
        scaffold = build_scaffold(
            {"opencode": failed_inventory("opencode", "db gone")},
            [ok_collect(src, [item()])],
        )
        doc = brief_doc(
            [section()], generated_note="Report incomplete: one provider failed."
        )
        assert validate_brief(doc, scaffold).ok is True

    def test_canonical_helper_note_satisfies_the_marker(self):

        note = build_incompleteness_note((("opencode", "db gone"),))
        assert "incomplete" in note.lower()
        assert "1 provider" in note or "1 source" in note


class TestValidateBriefConflictNotes:
    def test_conflict_note_without_any_source_id_is_rejected(self):
        doc = brief_doc(
            [section(conflict_notes=("sources disagree about everything",))]
        )
        result = validate_brief(doc, happy_scaffold())
        assert result.ok is False
        assert any("conflict" in e for e in result.errors)

    def test_conflict_note_mentioning_a_source_id_is_accepted(self):
        doc = brief_doc(
            [
                section(
                    conflict_notes=(
                        "opencode:/repo/a says done, but engram:/x says pending",
                    ),
                    citations=("opencode:/repo/a",),
                )
            ]
        )
        assert validate_brief(doc, happy_scaffold()).ok is True

    def test_errors_accumulate(self):
        result = validate_brief(
            brief_doc([section(project="/ghost", citations=("nope",))]),
            happy_scaffold(),
        )
        assert result.ok is False
        assert len(result.errors) >= 2


class TestAssertNoReferences:
    def test_clean_text_returns_true(self):

        assert assert_no_references("plain prose", ("opencode:/repo/a",)) is True

    def test_text_containing_a_source_id_returns_false(self):

        assert assert_no_references("see opencode:/repo/a", ("opencode:/repo/a",)) is False

    def test_no_source_ids_trivially_true(self):

        assert assert_no_references("anything", ()) is True


class TestRenderSpoken:
    def test_contains_section_prose(self):

        doc = brief_doc([section()])
        spoken = render_spoken(doc)
        assert "Parser refactor landed" in spoken
        assert "Write migration notes" in spoken
        assert "CI flakiness" in spoken
        assert "/repo/a" in spoken

    def test_contains_no_source_ids(self):
        # FR-14: the spoken artifact carries no reference ids. The
        # renderer never emits them; the contract is additionally
        # checked with the provided hook.

        scaffold = happy_scaffold()
        spoken = render_spoken(brief_doc([section()]))
        source_ids = [s.source_id for b in scaffold.bundles for s in b.sources]
        assert assert_no_references(spoken, source_ids) is True

    def test_no_references_block_and_no_conflict_notes(self):
        # Conflict notes must cite a source_id (validation rule), so
        # they are SCREEN-ONLY: speaking them would speak references.

        doc = brief_doc(
            [section(conflict_notes=("opencode:/repo/a vs engram:/x",))]
        )
        spoken = render_spoken(doc)
        assert "References" not in spoken
        assert "opencode:/repo/a vs" not in spoken

    def test_ends_with_interval_and_timezone_label_line(self):

        spoken = render_spoken(brief_doc([section()]))
        last_line = spoken.rstrip("\n").splitlines()[-1]
        assert "today (2026-09-30, Europe/Madrid)" in last_line
        assert "Europe/Madrid" in last_line

    def test_no_work_phrase_is_spoken(self):

        doc = brief_doc(
            [section(no_work=True, advances=(), pending=(), blockers=(), citations=())]
        )
        assert NO_WORK_PHRASE in render_spoken(doc)

    def test_unknown_project_renders_with_its_label(self):

        doc = brief_doc([section(project="", citations=())])
        spoken = render_spoken(doc)
        assert UNKNOWN_PROJECT_LABEL in spoken
        assert "Project: \n" not in spoken

    def test_generated_note_is_spoken(self):

        doc = brief_doc([section()], generated_note="Report incomplete: x failed.")
        assert "Report incomplete: x failed." in render_spoken(doc)

    def test_spoken_never_includes_citations_field(self):
        # citations are metadata for validation, not prose to speak.

        doc = brief_doc([section(citations=("opencode:/repo/a",))])
        assert "opencode:/repo/a" not in render_spoken(doc)


class TestRenderScreen:
    def test_contains_references_block_with_locator_and_retrieved_at(self):

        scaffold = happy_scaffold()
        screen = render_screen(brief_doc([section()]), scaffold)
        assert "References:" in screen
        assert "opencode:/repo/a" in screen
        assert "/repo/a/session-1" in screen  # locator (FR-14)
        assert OBSERVED_AT.isoformat() in screen  # retrieved_at (FR-14)

    def test_contains_prose_and_conflict_notes(self):

        scaffold = happy_scaffold()
        doc = brief_doc(
            [section(conflict_notes=("opencode:/repo/a disagrees with notes",))]
        )
        screen = render_screen(doc, scaffold)
        assert "Parser refactor landed" in screen
        assert "opencode:/repo/a disagrees with notes" in screen

    def test_incomplete_scaffold_shows_coverage_failures(self):

        src = source()
        scaffold = build_scaffold(
            {"opencode": failed_inventory("opencode", "db gone")},
            [ok_collect(src, [item()])],
        )
        doc = brief_doc(
            [section()], generated_note="Report incomplete: one provider failed."
        )
        screen = render_screen(doc, scaffold)
        assert "opencode" in screen and "db gone" in screen
        assert "incomplete" in screen.lower()

    def test_complete_scaffold_shows_no_failure_block(self):

        scaffold = happy_scaffold()
        screen = render_screen(brief_doc([section()]), scaffold)
        assert "could not be covered" not in screen

    def test_ends_with_interval_and_timezone_label_line(self):

        screen = render_screen(brief_doc([section()]), happy_scaffold())
        last_line = screen.rstrip("\n").splitlines()[-1]
        assert "today (2026-09-30, Europe/Madrid)" in last_line
        assert "Europe/Madrid" in last_line

    def test_unknown_project_renders_with_its_label(self):

        src = source(source_id="antigravity:x", kind="antigravity", project="", locator="x")
        scaffold = build_scaffold(
            {"antigravity": InventoryResult(CoverageStatus.OK, (src,))},
            [ok_collect(src, [item(source_id="antigravity:x", kind="antigravity")])],
        )
        doc = brief_doc([section(project="", citations=("antigravity:x",))])
        assert UNKNOWN_PROJECT_LABEL in render_screen(doc, scaffold)

    def test_section_without_a_matching_bundle_renders_without_references(self):
        # render_screen is pure: it renders the DOC; validation gates
        # publication elsewhere. An unmatched section simply has no
        # references block of its own.

        screen = render_screen(brief_doc([section(project="/ghost")]), happy_scaffold())
        assert "/ghost" in screen
        assert "References:" not in screen
