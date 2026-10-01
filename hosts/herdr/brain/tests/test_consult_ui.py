"""Tests for T10 UI backend support (``docs/prds/
herdr-brain-on-demand-context.md``; FR-14, FR-18, FR-33): the
``consult_report`` SSE event carrying the SCREEN text, and the
``ReportStore.get_by_report_id`` read that closes the T9 followup
integration finding (restart-surviving followup summaries).

Deterministic harness (same conventions as ``test_consult``): injected
clocks, fake providers with the real method shapes, the REAL report and
followup stores on tmp databases, and the scripted fake model callable.
No network, no sleeps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from herdr_brain.config import Settings
from herdr_brain.consult import LLMSummarizer, ConsultService, intent_global
from herdr_brain.evidence import CoverageStatus
from herdr_brain.followup import FollowupStore
from herdr_brain.reportstore import (
    ManifestEntry,
    NormalizedInterval,
    Reference,
    ReportKey,
    ReportStore,
)
from herdr_brain.tools import BrainTools

UTC = timezone.utc
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
MADRID_TZ = ("Europe/Madrid",)


# ---------------------------------------------------------------------------
# Shared deterministic fakes (test_consult subset, self-contained)
# ---------------------------------------------------------------------------


class FakeMono:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class MutableClock:
    """Wall clock the test advances to cross retention horizons."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_source(source_id: str, kind: str = "opencode"):
    from herdr_brain.evidence import Source

    return Source(
        source_id=source_id,
        kind=kind,
        project="/repo",
        locator=f"{source_id}/session",
        revision_token="tok-1",
        observed_at=NOW,
        title=None,
        state=None,
    )


def make_item(source_id: str, text="Did auth work"):
    from herdr_brain.evidence import EvidenceItem

    return EvidenceItem(
        source_id=source_id,
        kind="opencode",
        timestamp=NOW,
        role="assistant",
        text=text,
    )


class FakeProvider:
    def __init__(self, kind, sources=(), items_by_source=None, *, inventory_status=None):
        self.kind = kind
        self.sources = tuple(sources)
        self.items_by_source = dict(items_by_source or {})
        self.inventory_status = (
            CoverageStatus.COVERAGE_FAILED if inventory_status else CoverageStatus.OK
        )

    def inventory(self, project_filter=None, deadline=None, **kwargs):
        from herdr_brain.evidence import InventoryResult

        if self.inventory_status is CoverageStatus.COVERAGE_FAILED:
            return InventoryResult(
                CoverageStatus.COVERAGE_FAILED, error_detail="provider down"
            )
        return InventoryResult(CoverageStatus.OK, self.sources)

    def collect(self, source, period=None, deadline=None):
        from herdr_brain.evidence import CoverageResult

        items = self.items_by_source.get(source.source_id, ())
        status = CoverageStatus.OK if items else CoverageStatus.SOURCE_ABSENT
        return CoverageResult(source, status, tuple(items))


class FaithfulModel:
    """Fake ``model_call`` answering a schema-compliant brief JSON."""

    def __call__(self, messages, *, timeout=None):
        import json
        import re

        from herdr_brain.consult import DATA_BEGIN_MARKER, DATA_END_MARKER

        user = messages[1]["content"]

        def field(name: str) -> str:
            match = re.search(rf"^{name}: (.*)$", user, re.MULTILINE)
            assert match is not None
            return match.group(1).strip()

        payload = user.split(DATA_BEGIN_MARKER, 1)[1]
        payload = payload.split(DATA_END_MARKER, 1)[0]
        scaffold = json.loads(payload)
        sections = []
        for bundle in scaffold["bundles"]:
            items = bundle["items"]
            sections.append(
                {
                    "project": bundle["project"],
                    "advances": [f"Advanced: {items[0]['text'][:60]}"] if items else [],
                    "pending": [],
                    "blockers": [],
                    "no_work": not items,
                    "conflict_notes": [],
                    "citations": [s["source_id"] for s in bundle["sources"]],
                }
            )
        return json.dumps(
            {
                "context_id": field("context_id"),
                "interval_label": field("interval_label"),
                "timezone_label": field("timezone_label"),
                "sections": sections,
                "generated_note": "",
            }
        )


def provider_service(tmp_path, settings, *, report_db=None, followup_db=None,
                     clock=None, event_sink=None):
    """A successful global-consultation harness: a Herdr session
    provider (the ONLY source global queries read, D02/FR-04 — the
    engine fails closed without it) plus the historical opencode
    provider."""
    herdr_src = make_source("herdr:w1:p1", kind="herdr_session")
    herdr = FakeProvider(
        "herdr_session",
        sources=(herdr_src,),
        items_by_source={herdr_src.source_id: [make_item(herdr_src.source_id)]},
    )
    provider = FakeProvider(
        "opencode",
        sources=(make_source("opencode:s1"),),
        items_by_source={"opencode:s1": [make_item("opencode:s1")]},
    )
    return ConsultService(
        settings=settings,
        providers={"herdr_session": herdr, "opencode": provider},
        report_db=report_db or str(tmp_path / "reports.db"),
        followup_store=FollowupStore(
            followup_db or tmp_path / "followup.db", clock=clock or (lambda: NOW)
        ),
        summarizer=LLMSummarizer(FaithfulModel()),
        clock=clock or (lambda: NOW),
        monotonic=FakeMono(),
        timezone_candidates=MADRID_TZ,
        event_sink=event_sink,
    )


# ---------------------------------------------------------------------------
# ReportStore.get_by_report_id (additive by-id diagnostics read)
# ---------------------------------------------------------------------------

INTERVAL = NormalizedInterval(
    key="global|2026-09-30T00:00:00+00:00|2026-09-30T12:00:00+00:00|Europe/Madrid",
    start=NOW - timedelta(hours=12),
    end=NOW,
    zone_name="Europe/Madrid",
)


def key_for(context_id: str) -> ReportKey:
    return ReportKey(
        main_call_context_id=context_id,
        scope="global",
        interval_key=INTERVAL.key,
        timezone="Europe/Madrid",
    )


def publish_one(store: ReportStore, context_id: str, body: str):
    handle = store.begin_build(key_for(context_id))
    return store.publish(
        handle,
        body=body,
        references=(Reference(
            source_id="opencode:s1",
            kind="opencode",
            locator="a",
            revision_token="tok-1",
            retrieved_at=NOW.isoformat(),
        ),),
        source_manifest=(ManifestEntry(
            source_id="opencode:s1", revision_token="tok-1", state="open"
        ),),
    )


class TestGetByReportId:
    def test_found_by_exact_id_across_keys(self, tmp_path):
        store = ReportStore(tmp_path / "r.db", clock=lambda: NOW)
        first = publish_one(store, "call-1", "body-one")
        second = publish_one(store, "call-2", "body-two")
        # no key argument: the id alone addresses the row
        assert store.get_by_report_id(first.report_id).body == "body-one"
        assert store.get_by_report_id(second.report_id).body == "body-two"

    def test_unknown_id_returns_none(self, tmp_path):
        store = ReportStore(tmp_path / "r.db", clock=lambda: NOW)
        publish_one(store, "call-1", "body-one")
        assert store.get_by_report_id("no-such-id") is None

    def test_expired_returns_none(self, tmp_path):
        clock = MutableClock(NOW)
        store = ReportStore(tmp_path / "r.db", clock=clock)
        record = publish_one(store, "call-1", "body-one")
        clock.now = NOW + timedelta(hours=25)  # past the 24h retention
        assert store.get_by_report_id(record.report_id) is None

    def test_refresh_failed_included_like_get_latest(self, tmp_path):
        store = ReportStore(tmp_path / "r.db", clock=lambda: NOW)
        record = publish_one(store, "call-1", "body-one")
        store.mark_refresh_failed(key_for("call-1"))
        fetched = store.get_by_report_id(record.report_id)
        assert fetched is not None, "refresh_failed stays retrievable for diagnostics"
        assert fetched.status == "refresh_failed"

    def test_building_never_returned(self, tmp_path):
        store = ReportStore(tmp_path / "r.db", clock=lambda: NOW)
        handle = store.begin_build(key_for("call-1"))
        assert store.get_by_report_id(handle.report_id) is None

    def test_superseded_revision_returned_by_exact_id(self, tmp_path):
        store = ReportStore(tmp_path / "r.db", clock=lambda: NOW)
        older = publish_one(store, "call-1", "body-one")
        newer = publish_one(store, "call-1", "body-two")
        # get_current moved on to the newer revision...
        assert store.get_current(key_for("call-1")).report_id == newer.report_id
        # ...but the by-id fetch is a diagnostics read: the exact id wins
        # even though that revision is superseded.
        fetched = store.get_by_report_id(older.report_id)
        assert fetched is not None
        assert fetched.body == "body-one"
        assert fetched.superseded_by == newer.report_id


# ---------------------------------------------------------------------------
# consult_report SSE event (FR-14/FR-18 backend half)
# ---------------------------------------------------------------------------


class TestConsultReportEvent:
    def test_sequence_start_end_report_with_payload(self, settings, tmp_path):
        events: list[dict] = []
        service = provider_service(tmp_path, settings, event_sink=events.append)
        result = service.consult(intent_global("", "", MADRID_TZ), "call-1")

        assert result.state == "rendered"
        # ORDER (product contract): consulting start -> end -> the
        # report payload LAST, so the PWA clears the indicator before
        # the panel paints.
        assert [e["type"] for e in events] == [
            "consulting",
            "consulting",
            "consult_report",
        ]
        assert events[0]["state"] == "start"
        assert events[1]["state"] == "end"
        assert events[1]["outcome"] == "rendered"
        payload = events[2]
        assert payload["screen"] == result.screen
        assert payload["interval_label"] == result.interval_label
        assert payload["timezone_label"] == result.timezone_label
        assert payload["report_id"] == result.report_id
        assert payload["context_id"] == "call-1"
        assert "References:" in payload["screen"]  # screen artifact carries them

    def test_no_report_event_on_unable(self, settings, tmp_path):
        events: list[dict] = []
        # Global queries read ONLY the herdr provider (D02/FR-04), so
        # a failing HERDR inventory is what surfaces unable here.
        herdr = FakeProvider(
            "herdr_session",
            sources=(make_source("herdr:w1:p1", kind="herdr_session"),),
            inventory_status=True,
        )
        opencode = FakeProvider("opencode")
        service = ConsultService(
            settings=settings,
            providers={"herdr_session": herdr, "opencode": opencode},
            report_db=str(tmp_path / "reports.db"),
            followup_store=FollowupStore(
                tmp_path / "followup.db", clock=lambda: NOW
            ),
            summarizer=LLMSummarizer(FaithfulModel()),
            clock=lambda: NOW,
            monotonic=FakeMono(),
            timezone_candidates=MADRID_TZ,
            event_sink=events.append,
        )
        result = service.consult(intent_global("", "", MADRID_TZ), "call-1")
        assert result.state == "unable_to_complete"
        assert [e["type"] for e in events] == ["consulting", "consulting"]
        # FR-19/FR-20: nothing report-like is ever emitted for a failure
        assert all(e["type"] != "consult_report" for e in events)


# ---------------------------------------------------------------------------
# Followup survives restarts via the store fallback (T9 integration gap)
# ---------------------------------------------------------------------------


class TestFollowupStoreFallback:
    def test_registry_miss_falls_back_to_store_after_restart(
        self, settings, tmp_path
    ):
        before = provider_service(tmp_path, settings)
        result = before.consult(intent_global("", "", MADRID_TZ), "call-1")
        assert result.state == "rendered"

        # RESTART: a brand-new service instance over the SAME store
        # paths. Its in-memory rendered registry is empty (no consult
        # ran here); only the persistent store can answer.
        after = provider_service(tmp_path, settings)
        assert after.last_result is None  # registry is genuinely empty
        restored = after.rendered_result(result.report_id)
        assert restored is not None, "store fallback serves the anchored report"
        assert restored is not result  # rebuilt from the store, not cached
        assert restored.state == "rendered"
        assert restored.screen == result.screen  # the followup summary body
        assert restored.report_id == result.report_id

    def test_restart_still_serves_the_followup_summary(
        self, settings, tmp_path, make_stub
    ):
        before = provider_service(tmp_path, settings)
        before.consult(intent_global("", "", MADRID_TZ), "call-1")
        topic = before.last_topic

        after = provider_service(tmp_path, settings)
        tools = BrainTools(settings, herdr=make_stub(), consult=after)
        out = tools.dispatch(
            "get_followup_context", {"question": topic}, context_id="call-1"
        )
        assert "topic matched" in out
        assert "ANCHORED REPORT" in out
        assert "References:" in out  # screen text restored from the store

    def test_store_miss_degrades_to_reconsult_note(
        self, settings, tmp_path, make_stub
    ):
        before = provider_service(tmp_path, settings)
        before.consult(intent_global("", "", MADRID_TZ), "call-1")
        topic = before.last_topic

        # Same followup anchor, but the report store is GONE (expired
        # row or wiped database): only the re-consult degradation may
        # surface — never an improvised summary.
        after = ConsultService(
            settings=settings,
            providers={"opencode": FakeProvider("opencode")},
            report_db=str(tmp_path / "elsewhere.db"),
            followup_store=FollowupStore(
                tmp_path / "followup.db", clock=lambda: NOW
            ),
            summarizer=LLMSummarizer(FaithfulModel()),
            clock=lambda: NOW,
            monotonic=FakeMono(),
            timezone_candidates=MADRID_TZ,
        )
        tools = BrainTools(settings, herdr=make_stub(), consult=after)
        out = tools.dispatch(
            "get_followup_context", {"question": topic}, context_id="call-1"
        )
        assert "no longer in memory" in out
        assert "Re-consult" in out
        assert "ANCHORED REPORT" not in out
