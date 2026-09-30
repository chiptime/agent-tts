"""Freshness validation for on-demand context reports (PRD decision
D08; FR-26, FR-28..FR-33, FR-41 of ``docs/prds/
herdr-brain-on-demand-context.md``).

REUSE PRECONDITION (D08, one sentence): a stored report may be served
again only when the current source set AND every source's revision
token AND the normalized query interval are identical to the stored
snapshot, AND only after this freshness check itself succeeded.
RETENTION IS NOT A STALENESS TTL: the report store's 24h horizon only
bounds retrievability (FR-27) — EVERY qualifying query still performs
this check before answering (FR-26, FR-28), so an unexpired published
report is never implicitly fresh.

Inert library: stdlib only; nothing here is wired into the server, the
LLM loop, the tool surface, or configuration. All decision logic is
PURE with respect to the store — evaluation only reads
(``get_current``/``get_latest``) and never mutates; publication,
``mark_refresh_failed``, and cleanup stay with the caller.

Manifest comparison: ``diff_manifests`` compares a stored manifest
against the current one with SET semantics on ``source_id`` and TOKEN
EQUALITY AS THE ONLY CHANGE SIGNAL (FR-31) — see ``ManifestDiff``.
``plan_rebuild`` turns a diff into the read plan for the rebuild
phase (FR-30/FR-32: delta-only answers are forbidden, so the plan is
always the FULL current source set, bounded by the deadline at read
time — providers enforce it).

``FreshnessChecker.evaluate`` returns a SEALED outcome family chosen
deliberately as frozen-dataclass SUBCLASSES (not one tagged union
dataclass): each variant carries exactly the fields it needs and
callers dispatch with ``isinstance``. ``Reuse`` (serve the stored
report), ``Rebuild`` (full-scan rebuild), ``Unable`` (report
unable-to-complete). Python cannot enforce sealing; the family is
closed by convention — do not add variants without updating every
caller's dispatch.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Optional

from .evidence import CoverageStatus, Deadline, InventoryResult, manifest_entries
from .reportstore import ManifestEntry, ReportKey, ReportRecord, ReportStore

_MANIFEST_FIELDS = frozenset({"source_id", "revision_token", "state"})
_REQUIRED_MANIFEST_FIELDS = ("source_id", "revision_token")


class FreshnessConfigError(ValueError):
    """Fail-loud freshness problem; scope is never silently narrowed.

    Raised for authority violations (a provider result for a kind
    outside ``configured_kinds``, a configured kind missing from the
    results — i.e. a silently skipped source, FR-41 — or a source whose
    kind is not configured), checker construction misuse, and MALFORMED
    manifest rows (an entry that is neither a ``ManifestEntry`` nor a
    mapping of the manifest fields). A malformed or partial snapshot
    cannot be diffed honestly, so it is never silently ignored, never
    truncated to its valid prefix: it raises.
    """


def _require_str(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FreshnessConfigError(
            f"{label} must be a non-empty string, got {value!r}"
        )


def _normalize_manifest_entry(item: object, label: str) -> ManifestEntry:
    """Normalizes one manifest-row input into a ``ManifestEntry``.

    Accepts ``ManifestEntry`` instances or mappings carrying the
    manifest fields. Mappings may omit the informational ``state``
    (it defaults to the empty string) but MUST carry ``source_id`` and
    ``revision_token`` as non-empty strings, MUST NOT carry any other
    key (typo guard, the same rule the report store applies on write),
    and every present value MUST be a string. Anything else raises
    ``FreshnessConfigError``: a malformed row is NEVER silently
    dropped, because a dropped row would fabricate an addition/removal
    pair or, worse, hide a change (fail loud).
    """
    if isinstance(item, ManifestEntry):
        source_id = item.source_id
        token = item.revision_token
        state = item.state
    elif isinstance(item, Mapping):
        extra = sorted({str(key) for key in item} - _MANIFEST_FIELDS)
        if extra:
            raise FreshnessConfigError(
                f"{label} entry has unexpected field(s) {extra};"
                f" expected a subset of {sorted(_MANIFEST_FIELDS)}"
            )
        missing = [name for name in _REQUIRED_MANIFEST_FIELDS if name not in item]
        if missing:
            raise FreshnessConfigError(
                f"{label} entry is missing required field(s) {missing}:"
                f" {dict(item)!r}"
            )
        source_id = item["source_id"]
        token = item["revision_token"]
        state = item.get("state", "")
    else:
        raise FreshnessConfigError(
            f"{label} entries must be ManifestEntry instances or mappings,"
            f" got {type(item).__name__}: {item!r}"
        )
    _require_str(source_id, f"{label} entry source_id")
    _require_str(token, f"{label} entry revision_token")
    if not isinstance(state, str):
        raise FreshnessConfigError(
            f"{label} entry state must be a str, got {type(state).__name__}"
        )
    return ManifestEntry(source_id=source_id, revision_token=token, state=state)


def _index_entries(items: Iterable[object], label: str) -> dict[str, ManifestEntry]:
    """Normalizes and indexes one manifest side by ``source_id``.

    A duplicate ``source_id`` within ONE side raises: manifests are
    sets, and a duplicate is a provider bug that must not be flattened
    into a silent last-wins.
    """
    indexed: dict[str, ManifestEntry] = {}
    for item in items:
        manifest_entry = _normalize_manifest_entry(item, label)
        if manifest_entry.source_id in indexed:
            raise FreshnessConfigError(
                f"{label} manifest has duplicate source_id"
                f" {manifest_entry.source_id!r}: manifests are sets"
            )
        indexed[manifest_entry.source_id] = manifest_entry
    return indexed


@dataclass(frozen=True)
class ManifestDiff:
    """Result of comparing a stored manifest with the current one.

    Buckets, each a tuple deterministically sorted by ``source_id``:
    ``added``/``changed``/``unchanged`` carry CURRENT-side entries and
    ``removed`` carries STORED-side entries — together the first three
    are exactly the current source set a rebuild must read.

    TOKEN EQUALITY IS THE ONLY CHANGE SIGNAL (FR-31): a source is
    ``changed`` iff its ``revision_token`` differs. ``state`` strings
    are informational snapshots only. This is the deliberate contract
    with the providers: ANY change that must be detected — a closure, a
    status change (working/idle/blocked), a source modification, an
    Engram edit or deletion — ALREADY produces a new revision token
    (see ``evidence`` provider docstrings), so comparing states here
    would create a second, weaker signal and let token-only-conformant
    providers be misjudged as unchanged. A status-only change reaches
    this diff exclusively through its token bump.
    """

    added: tuple[ManifestEntry, ...]
    removed: tuple[ManifestEntry, ...]
    changed: tuple[ManifestEntry, ...]
    unchanged: tuple[ManifestEntry, ...]

    def any_change(self) -> bool:
        """True when any addition, removal, or token change exists."""
        return bool(self.added or self.removed or self.changed)


def diff_manifests(
    stored: Iterable[object], current: Iterable[object]
) -> ManifestDiff:
    """Set-diff of two source manifests keyed by ``source_id``.

    Inputs may be ``ManifestEntry`` objects or mappings with those
    fields (normalized by ``_normalize_manifest_entry``: malformed rows
    raise ``FreshnessConfigError`` instead of being skipped). A source
    present on both sides lands in ``changed`` when its
    ``revision_token`` differs and in ``unchanged`` otherwise — never
    both; output ordering is deterministic (sorted by ``source_id``)
    regardless of input order.
    """
    stored_map = _index_entries(stored, "stored")
    current_map = _index_entries(current, "current")
    added: list[ManifestEntry] = []
    removed: list[ManifestEntry] = []
    changed: list[ManifestEntry] = []
    unchanged: list[ManifestEntry] = []
    for source_id in sorted(set(stored_map) | set(current_map)):
        was = stored_map.get(source_id)
        now = current_map.get(source_id)
        if was is None:
            added.append(now)
        elif now is None:
            removed.append(was)
        elif was.revision_token != now.revision_token:
            changed.append(now)
        else:
            unchanged.append(now)
    return ManifestDiff(
        added=tuple(added),
        removed=tuple(removed),
        changed=tuple(changed),
        unchanged=tuple(unchanged),
    )


@dataclass(frozen=True)
class RebuildPlan:
    """What the acquisition phase must read after a rebuild decision.

    ``sources_to_read`` is ALWAYS the full current source set (the ids
    from ``added`` + ``changed`` + ``unchanged``), sorted: FR-30/FR-32
    forbid delta-only answers — the delta is fetched to know WHAT
    changed, but the REPORT is rebuilt in full, so the read set is
    everything currently in scope. ``full_scan`` is a field, not an
    assumption, so the acquisition layer reads the mode explicitly; no
    code path in this module ever produces False. ``deadline`` is
    carried THROUGH for the read phase: this pure helper never enforces
    it — providers bound every read against it and report
    COVERAGE_FAILED when it expires (FR-17).
    """

    sources_to_read: tuple[str, ...]
    reason: str
    full_scan: bool = True
    deadline: Optional[Deadline] = None


def plan_rebuild(
    diff: ManifestDiff, *, deadline: Optional[Deadline] = None
) -> RebuildPlan:
    """Builds the bounded read plan for a rebuild from a manifest diff.

    Delta-only answers are forbidden (FR-30/FR-32), so the plan is
    always the FULL current source set — including sources whose token
    did not change — never just ``added``/``changed``. The plan's
    ``reason`` describes the READ SET (its size and the diff shape);
    the ``Rebuild`` outcome's reason describes the TRIGGER (why the
    rebuild started). They are intentionally separate strings.
    """
    current = sorted(
        manifest_entry.source_id
        for bucket in (diff.added, diff.changed, diff.unchanged)
        for manifest_entry in bucket
    )
    reason = (
        f"bounded full-scan rebuild: read all {len(current)} current sources"
        f" ({len(diff.added)} added, {len(diff.removed)} removed,"
        f" {len(diff.changed)} changed vs the stored manifest)"
    )
    return RebuildPlan(
        sources_to_read=tuple(current),
        reason=reason,
        full_scan=True,
        deadline=deadline,
    )


@dataclass(frozen=True)
class FreshnessOutcome:
    """Base of the SEALED result family returned by ``evaluate``.

    DESIGN CHOICE — a subclass family of frozen dataclasses (not one
    tagged dataclass): each variant carries exactly the fields it
    needs, and callers dispatch with ``isinstance``. The base class is
    not meant to be instantiated; the family is closed by convention
    (see module docstring).
    """


@dataclass(frozen=True)
class Reuse(FreshnessOutcome):
    """Every check passed — identical source set, identical revision
    tokens, identical normalized interval — so the stored report may
    be served as-is, but only BECAUSE the check succeeded (D08,
    FR-29); retention alone never implies this."""

    report: ReportRecord


@dataclass(frozen=True)
class Rebuild(FreshnessOutcome):
    """The stored snapshot cannot be reused: acquire and republish.

    ``full_scan`` is ALWAYS True in this design and MUST stay True:
    providers expose FULL inventories, never deltas, so a
    deletion/update delta is never available to read — FR-32's
    bounded full scan is the PERMANENT rebuild mode, and append-only
    deltas are never trusted for completeness (D08). The window-
    movement and no-stored-report paths read the full current set too
    (``plan_rebuild`` turns ``diff`` into that read set).
    """

    diff: ManifestDiff
    reason: str
    full_scan: bool = True


@dataclass(frozen=True)
class Unable(FreshnessOutcome):
    """At least one configured provider's inventory COVERAGE_FAILED
    (FR-20/FR-41): a source that cannot be checked makes reuse
    unverifiable AND rebuild dishonest, so the caller MUST report
    unable-to-complete — it NEVER falls back to serving the stored
    report. ``failed_providers`` carries ``(kind, error_detail)``
    pairs, sorted by kind, for the user-facing explanation."""

    failed_providers: tuple[tuple[str, str], ...]


def _change_reason(diff: ManifestDiff) -> str:
    """Enumerates counts AND source ids for each non-empty bucket, so
    a rebuild reason always names what changed (FR-31 visibility)."""
    parts: list[str] = []
    for label, bucket in (
        ("added", diff.added),
        ("removed", diff.removed),
        ("changed", diff.changed),
    ):
        if bucket:
            ids = ", ".join(manifest_entry.source_id for manifest_entry in bucket)
            parts.append(f"{len(bucket)} {label} ({ids})")
    return "source manifest changed: " + "; ".join(parts)


class FreshnessChecker:
    """Pre-answer freshness gate (D08; FR-26, FR-28..FR-33, FR-41).

    ``configured_kinds`` is the authority set: every evaluation MUST
    cover exactly these kinds — an unknown kind in the results, a
    configured kind missing from them, or a source of an unconfigured
    kind is a config bug that raises ``FreshnessConfigError`` instead
    of silently narrowing scope (FR-41).

    ``evaluate`` never mutates the store: it only reads
    ``get_current``/``get_latest``; publication, ``mark_refresh_failed``
    and cleanup remain the caller's. Pipeline order (documented, each
    step independently testable):

    a. AUTHORITY — input validation + the FR-41 gate above; raises.
    b. INVENTORY HEALTH — any COVERAGE_FAILED inventory -> ``Unable``
       (SOURCE_ABSENT and OK are healthy; absence contributes zero
       sources by contract).
    c. STORED REPORT — no current-eligible stored report (never
       published, expired retention, superseded, or refresh_failed —
       the store hides all of these behind ``get_current`` -> None) ->
       ``Rebuild``; a present report whose interval key differs from
       the query's -> ``Rebuild`` for window movement EVEN IF the
       manifests are identical (D08 moving windows; the store being
       keyed by interval usually turns a moved window into "no stored
       report", and this explicit comparison never trusts that
       coupling).
    d. MANIFEST — the union of sources across healthy results (built
       with ``evidence.manifest_entries``) is diffed against
       ``stored.source_manifest``; any addition, removal, or token
       change -> ``Rebuild`` with the diff and a reason enumerating
       counts; an empty diff -> ``Reuse``. Stored sources of a kind
       whose provider now reports absence simply land in ``removed``.
    """

    def __init__(self, store: ReportStore, *, configured_kinds: tuple[str, ...]) -> None:
        try:
            kinds = tuple(configured_kinds)
        except TypeError:
            raise FreshnessConfigError(
                "configured_kinds must be an iterable of strings,"
                f" got {type(configured_kinds).__name__}"
            ) from None
        if not kinds:
            raise FreshnessConfigError(
                "configured_kinds must not be empty: a freshness check over"
                " zero providers would vacuously pass (FR-41)"
            )
        for kind in kinds:
            _require_str(kind, "configured kind")
        duplicates = sorted({k for k in kinds if kinds.count(k) > 1})
        if duplicates:
            raise FreshnessConfigError(f"duplicate configured kinds: {duplicates}")
        self._store = store
        self._configured_kinds: tuple[str, ...] = kinds
        self._configured = frozenset(kinds)

    @property
    def configured_kinds(self) -> tuple[str, ...]:
        """The authority set this checker enforces (FR-41)."""
        return self._configured_kinds

    def evaluate(
        self,
        key: ReportKey,
        interval_key: str,
        provider_results: Mapping[str, InventoryResult],
    ) -> FreshnessOutcome:
        """Runs the pipeline described in the class docstring and
        returns the sealed outcome for this key and query interval."""
        _require_str(interval_key, "interval_key")
        results = self._validate_authority(provider_results)
        failed = tuple(
            sorted(
                (kind, result.error_detail or "")
                for kind, result in results.items()
                if result.status is CoverageStatus.COVERAGE_FAILED
            )
        )
        if failed:
            return Unable(failed_providers=failed)
        union = sorted(
            (
                source for result in results.values() for source in result.sources
            ),
            key=lambda source: source.source_id,
        )
        current_entries = manifest_entries(union)
        stored = self._store.get_current(key)
        if stored is None:
            latest = self._store.get_latest(key)
            if latest is not None:
                # Visible but not eligible: only refresh_failed reaches
                # get_latest while being hidden from get_current.
                reason = (
                    "stored report is not current-eligible"
                    f" (status={latest.status!r}: a refresh_failed report"
                    " is never served as current, FR-33)"
                )
            else:
                reason = (
                    "no stored report for key (never published, expired"
                    " retention, or superseded)"
                )
            return Rebuild(diff=diff_manifests((), current_entries), reason=reason)
        if stored.normalized_interval.key != interval_key:
            return Rebuild(
                diff=diff_manifests(stored.source_manifest, current_entries),
                reason=(
                    "query interval moved since the stored report:"
                    f" stored {stored.normalized_interval.key!r},"
                    f" current {interval_key!r}"
                ),
            )
        diff = diff_manifests(stored.source_manifest, current_entries)
        if diff.any_change():
            return Rebuild(diff=diff, reason=_change_reason(diff))
        return Reuse(report=stored)

    def _validate_authority(
        self, provider_results: object
    ) -> Mapping[str, InventoryResult]:
        """Step (a): the FR-41 authority gate — raises instead of ever
        narrowing the checked scope."""
        if not isinstance(provider_results, Mapping):
            raise FreshnessConfigError(
                "provider_results must be a mapping of kind -> InventoryResult,"
                f" got {type(provider_results).__name__}"
            )
        unknown = sorted(set(provider_results) - self._configured)
        if unknown:
            raise FreshnessConfigError(
                f"provider_results contain unconfigured kinds {unknown};"
                f" configured kinds are {list(self._configured_kinds)}"
            )
        missing = sorted(self._configured - set(provider_results))
        if missing:
            raise FreshnessConfigError(
                f"configured kinds missing from provider_results: {missing};"
                " a missing kind would be a silently skipped source (FR-41)"
            )
        for kind in sorted(provider_results):
            result = provider_results[kind]
            if not isinstance(result, InventoryResult):
                raise FreshnessConfigError(
                    f"provider_results[{kind!r}] must be an InventoryResult,"
                    f" got {type(result).__name__}"
                )
        for kind in sorted(provider_results):
            for source in provider_results[kind].sources:
                if source.kind not in self._configured:
                    raise FreshnessConfigError(
                        f"source {source.source_id!r} has kind {source.kind!r}"
                        " outside the configured kinds"
                        f" {list(self._configured_kinds)}: reuse must never"
                        " reach outside configured authority (FR-41)"
                    )
        return provider_results
