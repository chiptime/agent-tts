"""Report consolidation: per-project bundles, the structured brief the
model fills, deterministic grounding validation, and the two render
artifacts (PRD decisions D05, D06, D07; FR-13..16, FR-19..22, FR-25 of
``docs/prds/herdr-brain-on-demand-context.md``; Query FSM states
CONSOLIDATE/PUBLISH/RENDER).

ARCHITECTURAL SPLIT (critical): natural-language SUMMARY generation is
the LLM's job (later tasks). THIS module is the deterministic
consolidation layer — it organizes evidence into per-project bundles,
defines the structured ``BriefDocument`` the model must fill, VALIDATES
that structure against the evidence, and renders the user-facing
artifacts. No LLM calls happen here; Python never guesses semantics —
it only checks structure and provenance.

UNTRUSTED DATA (D07/D09, FR-25): every ``EvidenceItem.text``,
``Source.state``, and model-authored brief string is DATA, never an
instruction. The renderers escape nothing into instructions: they
interpolate prose into plain text and never interpret it. Source-derived
content is presented as guidance (evidence quotes with provenance), and
nothing in this module executes or relays embedded commands.

COMPLETION GROUNDING PHILOSOPHY (D05/D06, FR-15, FR-20, FR-22): the
MODEL owns semantics (what advanced, what is pending, what blocks);
PYTHON owns provenance (which sources exist, which were read, which
failed, which claims cite real evidence). ``validate_brief`` therefore
checks structural grounding only — one section per scaffold project,
citations resolve to real source_ids, no-work claims only over bundles
with no evidence, a visible incompleteness note when coverage failed —
and deliberately never judges whether the prose itself is true.

HONESTY RULES implemented here:

- Empty vs failed (FR-11/FR-22, D06): a project with sources but zero
  in-interval items is a VALID no-work bundle (``empty_reason =
  'no_evidence'``); a project whose only coverage is SOURCE_ABSENT is
  ``'source_absent'``; a COVERAGE_FAILED source never becomes a bundle
  — it lands in ``coverage_failures`` and flips ``complete`` to False,
  because an honest report cannot claim completeness (FR-20).
- References (FR-14): the SPOKEN artifact never carries reference
  ids/locators/urls because ``render_spoken`` only emits the model's
  prose fields — by construction. ``render_screen`` adds the per-project
  References block. The contract is additionally testable through
  ``assert_no_references``.
- Undated items (FR-10 legacy): items with ``timestamp`` None are
  INCLUDED in bundles (period filtering already happened in providers)
  and made visible through the per-bundle ``undated_items`` count —
  visibility, never exclusion, and never a fabricated date.

Inert library: stdlib only; nothing here is wired into the server, the
LLM loop, the tool surface, or configuration. Every function is pure
over its arguments; no clock, no I/O, no LLM.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .evidence import (
    CoverageResult,
    CoverageStatus,
    EvidenceItem,
    InventoryResult,
    Source,
    _cwd_matches,
)
from .reportstore import Reference

#: Display label for the documented "" (empty-string project) bucket —
#: Antigravity sources carry "" as their project. The bucket KEY stays
#: "" so it round-trips with filters and source data; only DISPLAY uses
#: this label.
UNKNOWN_PROJECT_LABEL = "(unknown project)"

#: The only values ``ProjectBundle.empty_reason`` may carry.
EMPTY_REASONS = frozenset({"no_evidence", "source_absent"})

# Sentinel that sorts undated items after every dated one.
_UNDATED_SENTINEL = datetime.max.replace(tzinfo=timezone.utc)


def _require_str(value: object, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} must be a non-empty string")


def _require_tuple(value: object, label: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{label} must be a tuple")


def display_project(project: str) -> str:
    """Display name for a project key: the key itself, except the
    documented "" bucket which renders as ``UNKNOWN_PROJECT_LABEL``."""
    _require_str(project, "project", allow_empty=True)
    return UNKNOWN_PROJECT_LABEL if project == "" else project


def build_references(sources: Iterable[Source]) -> list[Reference]:
    """Builds the reference list for a set of sources (FR-14, FR-40).

    Each ``Source`` maps to one ``reportstore.Reference`` with
    ``retrieved_at`` = the source's ``observed_at`` instant as an ISO
    8601 string (the declared observation cutoff, never a freshness
    promise). Deterministic: output is sorted by ``source_id``,
    regardless of input order.
    """
    return [
        Reference(
            source_id=src.source_id,
            kind=src.kind,
            locator=src.locator,
            revision_token=src.revision_token,
            retrieved_at=src.observed_at.isoformat(),
        )
        for src in sorted(sources, key=lambda s: s.source_id)
    ]


@dataclass(frozen=True)
class ProjectBundle:
    """The deterministic per-project consolidation of evidence (FR-13).

    Attributes:
        project: the group key — ``Source.project`` verbatim ("" is the
            documented unknown-project bucket; display it through
            ``display_project``).
        sources: the project's sources, sorted by ``source_id``.
        status_snapshots: ``(source_id, state)`` pairs for sources that
            carry a state, recorded VERBATIM (FR-04: an open session's
            status is truth, never an "unfinished" inference), sorted by
            ``source_id``. Sources without a state have no entry.
        items: the project's ``EvidenceItem``s, deterministically sorted
            (dated items by timestamp, undated last, then source_id and
            role). Period filtering already happened in providers.
        references: the project's ``Reference``s (``build_references``
            order: by ``source_id``).
        empty_reason: ``None`` when the bundle carries items; otherwise
            ``'source_absent'`` when the project's only coverage is
            SOURCE_ABSENT, or ``'no_evidence'`` when sources exist but
            produced zero in-interval items — a VALID no-work result
            (FR-22), never a failure.
        undated_items: how many ``items`` have ``timestamp`` None —
            visibility for FR-10-style unknowns, never exclusion.
    """

    project: str
    sources: tuple[Source, ...]
    status_snapshots: tuple[tuple[str, str], ...]
    items: tuple[EvidenceItem, ...]
    references: tuple[Reference, ...]
    empty_reason: Optional[str]
    undated_items: int

    def __post_init__(self) -> None:
        _require_str(self.project, "project", allow_empty=True)
        _require_tuple(self.sources, "sources")
        _require_tuple(self.status_snapshots, "status_snapshots")
        _require_tuple(self.items, "items")
        _require_tuple(self.references, "references")
        if self.empty_reason is not None and self.empty_reason not in EMPTY_REASONS:
            raise ValueError(
                f"empty_reason must be one of {sorted(EMPTY_REASONS)} or None,"
                f" got {self.empty_reason!r}"
            )
        for entry in self.status_snapshots:
            if (
                not isinstance(entry, tuple)
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not isinstance(entry[1], str)
            ):
                raise ValueError(
                    f"status_snapshots entries must be (source_id, state) string"
                    f" pairs, got {entry!r}"
                )
        if not isinstance(self.undated_items, int) or self.undated_items < 0:
            raise ValueError("undated_items must be a non-negative int")


@dataclass(frozen=True)
class ReportScaffold:
    """The consolidation input for the model: per-project bundles plus
    the honest failure ledger (FR-20).

    ``complete`` is False exactly when ``coverage_failures`` is
    non-empty (enforced in ``__post_init__``): a report built over a
    failed source can never claim completeness. COVERAGE_FAILED results
    are deliberately NOT bundles — their projects appear only through
    the failure list.
    """

    bundles: tuple[ProjectBundle, ...]
    coverage_failures: tuple[tuple[str, str], ...]

    #: False when any coverage failed (derived from and required to
    #: match ``coverage_failures`` emptiness).
    complete: bool

    def __post_init__(self) -> None:
        _require_tuple(self.bundles, "bundles")
        _require_tuple(self.coverage_failures, "coverage_failures")
        for entry in self.coverage_failures:
            if (
                not isinstance(entry, tuple)
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not entry[0].strip()
                or not isinstance(entry[1], str)
                or not entry[1].strip()
            ):
                raise ValueError(
                    "coverage_failures entries must be non-empty"
                    f" (source_id|provider_kind, detail) string pairs, got {entry!r}"
                )
        if not isinstance(self.complete, bool):
            raise ValueError("complete must be a bool")
        if self.complete != (not self.coverage_failures):
            raise ValueError(
                "complete must equal 'no coverage failures': an honest report"
                " cannot claim completeness over failed coverage"
            )


def _item_sort_key(entry: EvidenceItem) -> tuple:
    return (
        entry.timestamp is None,
        entry.timestamp if entry.timestamp is not None else _UNDATED_SENTINEL,
        entry.source_id,
        entry.role,
    )


def build_scaffold(
    provider_results: Mapping[str, InventoryResult],
    collect_results: Iterable[CoverageResult],
    *,
    project_filter: Optional[str] = None,
) -> ReportScaffold:
    """Consolidates inventory + collect outcomes into per-project
    bundles and the coverage-failure ledger (FR-13, FR-20, FR-22).

    Deterministic grouping rules:

    - Sources enter bundles from NON-failed inventory results and from
      non-failed collect results (``CoverageResult.source``), grouped by
      ``Source.project`` kept verbatim; the "" bucket is documented
      (``UNKNOWN_PROJECT_LABEL`` is display-only). A source whose
      collect COVERAGE_FAILED is excluded from every bundle — its
      project surfaces only through ``coverage_failures`` — so a failed
      source with no other evidence produces NO bundle for its project.
    - Bundles sort by project name ("" first); bundle sources,
      status snapshots, and references sort by ``source_id``; items sort
      by (timestamp, undated last, source_id, role).
    - ``empty_reason``: ``'source_absent'`` only when EVERY collect
      result covering the project's sources is SOURCE_ABSENT (at least
      one exists); otherwise zero items means ``'no_evidence'``
      (sources present, validly nothing in interval — includes
      discovered-but-uncollected sources); items present means None.
    - COVERAGE_FAILED inventories land in ``coverage_failures`` keyed by
      their provider kind (the mapping key, sorted for determinism);
      COVERAGE_FAILED collects land there keyed by ``source_id`` (input
      order). Their items are ignored: a failed read is not evidence.
    - ``project_filter`` narrows the report scope (FR-03) with the SAME
      ``_cwd_matches`` rule as the whole evidence layer: a source
      matches when its project EQUALS the filter or lives UNDER it as a
      subdirectory path; "" (unknown project) matches only an absent
      filter. Filtered-out sources and their items simply do not appear.

    Provenance integrity: an OK collect result whose items reference a
    different ``source_id`` than the result's own source raises
    ``ValueError`` — items must remain attributable to a real, read
    source (FR-15/FR-40 grounding starts here).
    """
    failures: list[tuple[str, str]] = []
    for kind in sorted(provider_results):
        inventory = provider_results[kind]
        if inventory.status is CoverageStatus.COVERAGE_FAILED:
            failures.append((kind, inventory.error_detail or "coverage failed"))

    failed_source_ids: set[str] = set()
    for result in collect_results:
        if result.status is CoverageStatus.COVERAGE_FAILED:
            failed_source_ids.add(result.source.source_id)
            failures.append((result.source.source_id, result.error_detail or "coverage failed"))

    sources_by_project: dict[str, dict[str, Source]] = {}
    # Per project: one bool per non-failed collect result covering one of
    # the project's sources (True = SOURCE_ABSENT). "Only coverage is
    # absent" means every one of them is True.
    absent_by_project: dict[str, list[bool]] = {}
    items_by_project: dict[str, list[EvidenceItem]] = {}

    def _admit(src: Source) -> None:
        if src.source_id in failed_source_ids:
            return
        if not _cwd_matches(src.project, project_filter):
            return
        bucket = sources_by_project.setdefault(src.project, {})
        bucket.setdefault(src.source_id, src)

    for kind in sorted(provider_results):
        inventory = provider_results[kind]
        if inventory.status is not CoverageStatus.COVERAGE_FAILED:
            for src in inventory.sources:
                _admit(src)

    for result in collect_results:
        if result.status is CoverageStatus.COVERAGE_FAILED:
            continue
        _admit(result.source)
        if (
            result.source.source_id
            not in sources_by_project.get(result.source.project, {})
        ):
            # The source was filtered out of this report's scope; its
            # coverage flags must not leak into a project we never show.
            continue
        is_absent = result.status is CoverageStatus.SOURCE_ABSENT
        absent_by_project.setdefault(result.source.project, []).append(is_absent)
        if is_absent:
            continue
        for entry in result.items:
            if entry.source_id != result.source.source_id:
                raise ValueError(
                    f"item claims source {entry.source_id!r} inside a collect"
                    f" result for {result.source.source_id!r}: items must be"
                    " attributable to the source they were read from"
                )
        items_by_project.setdefault(result.source.project, []).extend(result.items)

    bundles: list[ProjectBundle] = []
    for project in sorted(sources_by_project):
        sources = tuple(
            sorted(sources_by_project[project].values(), key=lambda s: s.source_id)
        )
        snapshots = tuple(
            (src.source_id, src.state)
            for src in sources
            if src.state is not None
        )
        items = tuple(sorted(items_by_project.get(project, ()), key=_item_sort_key))
        undated = sum(1 for entry in items if entry.timestamp is None)
        if items:
            empty_reason: Optional[str] = None
        elif absent_by_project.get(project):
            # Only when EVERY collect result covering this project is
            # SOURCE_ABSENT (mixed absent/OK means: readable sources,
            # validly nothing in interval).
            empty_reason = (
                "source_absent"
                if all(absent_by_project[project])
                else "no_evidence"
            )
        else:
            empty_reason = "no_evidence"
        bundles.append(
            ProjectBundle(
                project=project,
                sources=sources,
                status_snapshots=snapshots,
                items=items,
                references=tuple(build_references(sources)),
                empty_reason=empty_reason,
                undated_items=undated,
            )
        )

    return ReportScaffold(
        bundles=tuple(bundles),
        coverage_failures=tuple(failures),
        complete=not failures,
    )


#: Substring whose presence in ``BriefDocument.generated_note``
#: satisfies the incompleteness-visibility check (case-insensitive).
#: The check is deliberately LOOSE — a substring, not a schema — and
#: ``build_incompleteness_note`` produces a canonical note that
#: satisfies it by construction.
INCOMPLETENESS_MARKER = "incomplete"


@dataclass(frozen=True)
class BriefSection:
    """What the MODEL fills for one project (FR-13, FR-15).

    Python never writes these strings — the LLM does (T7/T9) — and
    never judges their truth. ``validate_brief`` only checks their
    structural grounding: citations must resolve to real scaffold
    source_ids, ``no_work`` must not contradict items, and conflict
    notes must cite a source. All prose is untrusted data (D07).
    """

    project: str
    advances: tuple[str, ...]
    pending: tuple[str, ...]
    blockers: tuple[str, ...]
    no_work: bool
    conflict_notes: tuple[str, ...]
    citations: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_str(self.project, "project", allow_empty=True)
        for name in ("advances", "pending", "blockers", "conflict_notes", "citations"):
            value = getattr(self, name)
            _require_tuple(value, name)
            for entry in value:
                _require_str(entry, f"{name} entry")
        if not isinstance(self.no_work, bool):
            raise ValueError("no_work must be a bool")


@dataclass(frozen=True)
class BriefDocument:
    """The structured brief document the model fills (FR-13, FR-20).

    ``generated_note`` is document-level prose (may be empty); when the
    scaffold is incomplete it MUST carry the ``INCOMPLETENESS_MARKER``
    so partial coverage is never presented as complete (FR-20).
    ``interval_label``/``timezone_label`` are display strings supplied
    by the caller from the resolved ``Period`` (T1) — never re-derived
    here.
    """

    context_id: str
    interval_label: str
    timezone_label: str
    sections: tuple[BriefSection, ...]
    generated_note: str = ""

    def __post_init__(self) -> None:
        _require_str(self.context_id, "context_id")
        _require_str(self.interval_label, "interval_label")
        _require_str(self.timezone_label, "timezone_label")
        _require_str(self.generated_note, "generated_note", allow_empty=True)
        _require_tuple(self.sections, "sections")
        for entry in self.sections:
            if not isinstance(entry, BriefSection):
                raise ValueError(
                    f"sections entries must be BriefSection instances,"
                    f" got {type(entry).__name__}"
                )


@dataclass(frozen=True)
class BriefValidation:
    """Outcome of ``validate_brief``: structural grounding errors only.

    ``ok`` is derived (``not errors``) so the two can never disagree.
    """

    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_tuple(self.errors, "errors")

    @property
    def ok(self) -> bool:
        return not self.errors


def build_incompleteness_note(
    coverage_failures: Iterable[tuple[str, str]],
) -> str:
    """Canonical spoken-safe incompleteness note (FR-20).

    Mentions the failure COUNT and the marker word but NEVER a
    source_id or provider key — those would leak references into the
    spoken artifact (FR-14); the screen renderer carries the details.
    """
    count = len(tuple(coverage_failures))
    noun = "source or provider" if count == 1 else "sources or providers"
    return (
        f"Report incomplete: {count} {noun} could not be covered."
        " Details are available on screen."
    )


def validate_brief(doc: BriefDocument, scaffold: ReportScaffold) -> BriefValidation:
    """Deterministic grounding checks of a filled brief (FR-15, FR-20).

    Checks, each independent and testable:

    - SECTION COVERAGE: every scaffold project has EXACTLY one section
      (missing -> error; duplicate -> error); a section for a project
      not in the scaffold is an error.
    - CITATION GROUNDING: every citation must be a real ``source_id``
      in the scaffold (scaffold-WIDE, not per-section — documented
      looseness: a claim may lean on any read source).
    - NO-WORK GROUNDING: ``no_work=True`` is allowed ONLY for bundles
      with ``empty_reason`` set or zero items; a no-work claim over
      actual evidence is an error (FR-15).
    - INCOMPLETENESS VISIBILITY (chosen mechanism, documented): when
      ``scaffold.complete`` is False, ``doc.generated_note`` MUST
      contain the ``INCOMPLETENESS_MARKER`` substring (case-insensitive).
      The check is intentionally a loose substring; the canonical
      ``build_incompleteness_note`` satisfies it by construction.
    - CONFLICT CITATION: each conflict note is free text but MUST
      mention at least one valid source_id (substring check — loose by
      design; FR-16 conflicts are surfaced WITH source).

    Everything else (whether prose is TRUE) is the model's semantics —
    deliberately out of scope for deterministic Python.
    """
    errors: list[str] = []
    bundle_by_project = {bundle.project: bundle for bundle in scaffold.bundles}
    section_counts: dict[str, int] = {}
    for sec in doc.sections:
        section_counts[sec.project] = section_counts.get(sec.project, 0) + 1

    for bundle in scaffold.bundles:
        count = section_counts.get(bundle.project, 0)
        if count == 0:
            errors.append(
                f"missing brief section for project {bundle.project!r}"
            )
        elif count > 1:
            errors.append(
                f"project {bundle.project!r} must have exactly one brief"
                f" section, found {count}"
            )
    for project in sorted(section_counts):
        if project not in bundle_by_project:
            errors.append(
                f"brief section for project {project!r} is not in the scaffold"
            )

    valid_source_ids = {
        src.source_id for bundle in scaffold.bundles for src in bundle.sources
    }

    for sec in doc.sections:
        for citation in sec.citations:
            if citation not in valid_source_ids:
                errors.append(
                    f"citation {citation!r} in section {sec.project!r} is not"
                    " a source_id in the scaffold"
                )
        bundle = bundle_by_project.get(sec.project)
        if (
            bundle is not None
            and sec.no_work
            and bundle.empty_reason is None
            and bundle.items
        ):
            errors.append(
                f"no_work claim in section {sec.project!r} contradicts"
                f" {len(bundle.items)} evidence item(s)"
            )
        for note in sec.conflict_notes:
            if not any(source_id in note for source_id in valid_source_ids):
                errors.append(
                    f"conflict note in section {sec.project!r} mentions no"
                    " scaffold source_id"
                )

    if (
        not scaffold.complete
        and INCOMPLETENESS_MARKER not in doc.generated_note.lower()
    ):
        errors.append(
            "scaffold coverage is incomplete; generated_note must carry the"
            f" {INCOMPLETENESS_MARKER!r} marker (FR-20)"
        )

    return BriefValidation(tuple(errors))


#: Spoken phrasing for a valid no-work result (FR-22): explicitly a
#: "no work RECORDED" statement, never a failure word.
NO_WORK_PHRASE = "No work recorded for this project in the requested period."


def assert_no_references(text: str, source_ids: Iterable[str]) -> bool:
    """FR-14 contract hook: True when ``text`` mentions NONE of the
    given source_ids.

    Substring-based (loose by design — source_ids are opaque namespaced
    strings, and a substring hit is always a leak). The SPOKEN renderer
    satisfies it by construction because it only ever emits the model's
    prose fields; callers/tests use this hook to verify the contract.
    """
    return not any(source_id in text for source_id in source_ids)


def _label_line(doc: BriefDocument) -> str:
    return f"Interval: {doc.interval_label} | Timezone: {doc.timezone_label}"


def _section_body(section: BriefSection) -> list[str]:
    """The shared per-section lines both artifacts render (FR-13)."""
    lines = [f"Project: {display_project(section.project)}"]
    if section.no_work:
        lines.append(NO_WORK_PHRASE)
    for label, entries in (
        ("Advances", section.advances),
        ("Pending", section.pending),
        ("Blockers", section.blockers),
    ):
        if entries:
            lines.append(f"{label}:")
            lines.extend(f"- {entry}" for entry in entries)
    return lines


def render_spoken(doc: BriefDocument) -> str:
    """The SPOKEN artifact: prose only, never references (FR-14).

    Emits the model's prose fields and nothing else: the note, each
    section's project display name, the no-work phrase, and
    advances/pending/blockers. Citations are validation metadata and
    conflict notes are SCREEN-ONLY (validation requires them to cite a
    source_id, and speaking one would violate FR-14; the model surfaces
    conflict semantics in its own prose). The renderer never strips or
    rewrites the model's strings — it simply has no code path that
    emits ids, locators, or urls. Ends with the interval/timezone line.
    """
    blocks: list[str] = []
    if doc.generated_note:
        blocks.append(doc.generated_note)
    for section in doc.sections:
        blocks.append("\n".join(_section_body(section)))
    blocks.append(_label_line(doc))
    return "\n\n".join(blocks)


def render_screen(doc: BriefDocument, scaffold: ReportScaffold) -> str:
    """The SCREEN artifact: the spoken content PLUS references (FR-14)
    and, when ``scaffold.complete`` is False, the coverage-failure
    ledger (FR-20: partial coverage is visible, never silent).

    Per section it adds the conflict notes and the project's References
    block (source_id, kind, locator, retrieved_at — FR-14). A section
    with no matching scaffold bundle renders without a references
    block; publication gating is ``validate_brief``'s job, not the
    renderer's. Ends with the interval/timezone line.
    """
    bundle_by_project = {bundle.project: bundle for bundle in scaffold.bundles}
    blocks: list[str] = []
    if doc.generated_note:
        blocks.append(doc.generated_note)
    for section in doc.sections:
        lines = _section_body(section)
        if section.conflict_notes:
            lines.append("Conflict notes:")
            lines.extend(f"- {note}" for note in section.conflict_notes)
        bundle = bundle_by_project.get(section.project)
        if bundle is not None and bundle.references:
            lines.append("References:")
            lines.extend(
                f"- {ref.source_id} ({ref.kind}),"
                f" locator: {ref.locator}, retrieved: {ref.retrieved_at}"
                for ref in bundle.references
            )
        blocks.append("\n".join(lines))
    if not scaffold.complete:
        count = len(scaffold.coverage_failures)
        noun = "source or provider" if count == 1 else "sources or providers"
        lines = [
            f"Coverage incomplete: {count} {noun} could not be covered"
            " (this report is not complete):"
        ]
        lines.extend(
            f"- {key}: {detail}" for key, detail in scaffold.coverage_failures
        )
        blocks.append("\n".join(lines))
    blocks.append(_label_line(doc))
    return "\n\n".join(blocks)
