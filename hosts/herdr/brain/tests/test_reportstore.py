"""Unit tests for the persistent report store (FR-27, FR-33, FR-34; D08).

Deterministic harness per the PRD Requirement-to-Test Matrix rows
"Persistent context and retention" and "Revision race and cancellation":
a fake injectable clock drives every retention/expiry boundary, threads
drive the revision races, and direct sqlite reads verify on-disk state
(the same convention as test_history.py).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from herdr_brain.periods import period_this_week, period_today
from herdr_brain.reportstore import (
    REPORTS_DB_FILENAME,
    MalformedReportInput,
    ManifestEntry,
    NormalizedInterval,
    Reference,
    ReportKey,
    ReportRecord,
    ReportStore,
    ReportStoreError,
    StaleBuildError,
    default_reportstore_path,
)

UTC = timezone.utc
MADRID = ZoneInfo("Europe/Madrid")


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def fake_clock(start=None):
    """Injectable store clock: call it for now, .advance(**timedelta kwargs)."""
    holder = {"now": start or utc(2026, 9, 30, 10, 0, 0)}

    def clock():
        return holder["now"]

    def advance(**kwargs):
        holder["now"] = holder["now"] + timedelta(**kwargs)

    clock.advance = advance
    return clock


PERIOD = period_today(utc(2026, 9, 30, 12), MADRID)
OTHER_PERIOD = period_this_week(utc(2026, 9, 30, 12), MADRID)


def make_key(context="call-1", scope="global", period=None, timezone_name=None):
    period = period or PERIOD
    return ReportKey(
        main_call_context_id=context,
        scope=scope,
        interval_key=period.key,
        timezone=timezone_name or period.zone_name,
    )


REFERENCES = [
    {
        "source_id": "opencode:ses_abc",
        "kind": "transcript",
        "locator": "~/.local/share/opencode/storage/ses_abc/session.jsonl",
        "revision_token": "rev-0001",
        "retrieved_at": "2026-09-30T09:59:01+00:00",
    },
    {
        "source_id": "engram:herdr-brain",
        "kind": "memory",
        "locator": "engram://observations/herdr-brain",
        "revision_token": "rev-77",
        "retrieved_at": "2026-09-30T09:59:44+00:00",
    },
]

MANIFEST = [
    {
        "source_id": "opencode:ses_abc",
        "revision_token": "rev-0001",
        "state": "open; pane w1:p9 status=working",
    },
    {
        "source_id": "engram:herdr-brain",
        "revision_token": "rev-77",
        "state": "queried ok",
    },
]


def make_store(tmp_path, clock=None, **kwargs):
    return ReportStore(tmp_path / "reports.db", clock=clock or fake_clock(), **kwargs)


def publish(store, key, body="consolidated brief", references=REFERENCES,
            source_manifest=MANIFEST):
    handle = store.begin_build(key)
    record = store.publish(
        handle, body=body, references=references, source_manifest=source_manifest
    )
    return handle, record


def fetch_rows(db_path):
    """Direct sqlite read of the raw table (on-disk truth, not the API)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM reports ORDER BY id")]
    finally:
        conn.close()


class TestSchemaAndOpen:
    def test_creates_parent_dirs_and_database_file(self, tmp_path):
        db = tmp_path / "nested" / "deeper" / REPORTS_DB_FILENAME
        ReportStore(db, clock=fake_clock())
        assert db.exists()

    def test_pragma_user_version_is_set(self, tmp_path):
        store = make_store(tmp_path)
        conn = sqlite3.connect(store.path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        finally:
            conn.close()

    def test_newer_schema_version_is_rejected(self, tmp_path):
        store = make_store(tmp_path)
        conn = sqlite3.connect(store.path)
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()
        with pytest.raises(ReportStoreError):
            ReportStore(store.path, clock=fake_clock())

    def test_reopen_is_idempotent(self, tmp_path):
        store = make_store(tmp_path)
        _, record = publish(store, make_key())
        reopened = ReportStore(store.path, clock=fake_clock())
        assert reopened.get_current(make_key()).report_id == record.report_id


class TestRoundTrip:
    def test_publish_roundtrips_every_field(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        key = make_key()
        handle, record = publish(store, key)
        assert store.get_current(key) == ReportRecord(
            report_id=handle.report_id,
            main_call_context_id="call-1",
            scope="global",
            normalized_interval=NormalizedInterval(
                key=PERIOD.key,
                start=PERIOD.start,
                end=PERIOD.end,
                zone_name=PERIOD.zone_name,
            ),
            timezone="Europe/Madrid",
            status="published",
            body="consolidated brief",
            references=(
                Reference(
                    source_id="opencode:ses_abc",
                    kind="transcript",
                    locator="~/.local/share/opencode/storage/ses_abc/session.jsonl",
                    revision_token="rev-0001",
                    retrieved_at="2026-09-30T09:59:01+00:00",
                ),
                Reference(
                    source_id="engram:herdr-brain",
                    kind="memory",
                    locator="engram://observations/herdr-brain",
                    revision_token="rev-77",
                    retrieved_at="2026-09-30T09:59:44+00:00",
                ),
            ),
            source_manifest=(
                ManifestEntry(
                    source_id="opencode:ses_abc",
                    revision_token="rev-0001",
                    state="open; pane w1:p9 status=working",
                ),
                ManifestEntry(
                    source_id="engram:herdr-brain",
                    revision_token="rev-77",
                    state="queried ok",
                ),
            ),
            created_at=clock(),
            retention_expires_at=clock() + timedelta(hours=24),
            superseded_by=None,
        )

    def test_records_and_items_are_frozen(self, tmp_path):
        store = make_store(tmp_path)
        _, record = publish(store, make_key())
        with pytest.raises(FrozenInstanceError):
            record.status = "expired"
        with pytest.raises(FrozenInstanceError):
            record.references[0].source_id = "x"

    def test_building_row_is_never_returned_by_reads(self, tmp_path):
        store = make_store(tmp_path)
        store.begin_build(make_key())
        assert store.get_current(make_key()) is None
        assert store.get_latest(make_key()) is None

    def test_sequences_are_strictly_increasing(self, tmp_path):
        store = make_store(tmp_path)
        first = store.begin_build(make_key())
        second = store.begin_build(make_key())
        third = store.begin_build(make_key(context="other"))
        assert first.sequence < second.sequence < third.sequence


class TestKeyIsolation:
    """D08: no cross-call leakage across any key component."""

    def test_different_call_context_is_isolated(self, tmp_path):
        store = make_store(tmp_path)
        base = make_key()
        publish(store, base)
        assert store.get_current(make_key(context="call-2")) is None
        assert store.get_current(base).body == "consolidated brief"

    def test_different_scope_is_isolated(self, tmp_path):
        store = make_store(tmp_path)
        base = make_key()
        publish(store, base)
        assert store.get_current(make_key(scope="projects:herdr")) is None
        assert store.get_current(base).body == "consolidated brief"

    def test_different_interval_is_isolated(self, tmp_path):
        store = make_store(tmp_path)
        base = make_key()
        publish(store, base)
        assert store.get_current(make_key(period=OTHER_PERIOD)) is None
        assert store.get_current(base).body == "consolidated brief"

    def test_different_timezone_is_isolated(self, tmp_path):
        store = make_store(tmp_path)
        base = make_key()
        publish(store, base)
        # Same interval key, distinct timezone column: a different key.
        assert store.get_current(make_key(timezone_name="America/New_York")) is None
        assert store.get_current(base).body == "consolidated brief"


class TestRetention:
    """Retention is a retrieval horizon, not a staleness TTL (D08)."""

    def test_boundary_exactly_at_expiry_is_expired(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        key = make_key()
        publish(store, key)
        clock.advance(hours=23, minutes=59, seconds=59, microseconds=999999)
        assert store.get_current(key) is not None
        assert store.get_latest(key) is not None
        clock.advance(microseconds=1)  # exactly at the horizon
        assert store.get_current(key) is None
        assert store.get_latest(key) is None

    def test_retention_horizon_is_configurable(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(
            tmp_path / REPORTS_DB_FILENAME, clock=clock, retention=timedelta(hours=1)
        )
        key = make_key()
        publish(store, key)
        clock.advance(minutes=59, seconds=59, microseconds=999999)
        assert store.get_current(key) is not None
        clock.advance(microseconds=1)
        assert store.get_current(key) is None


class TestSupersede:
    def test_each_publish_supersedes_the_previous_chain(self, tmp_path):
        store = make_store(tmp_path)
        key = make_key()
        _, first = publish(store, key, body="r1")
        _, second = publish(store, key, body="r2")
        _, third = publish(store, key, body="r3")
        assert store.get_current(key).report_id == third.report_id
        rows = {row["report_id"]: row for row in fetch_rows(store.path)}
        assert rows[first.report_id]["superseded_by"] == second.report_id
        assert rows[second.report_id]["superseded_by"] == third.report_id
        assert rows[third.report_id]["superseded_by"] is None
        assert rows[third.report_id]["status"] == "published"


class TestAtomicPublish:
    def test_publish_failure_mid_transaction_leaves_previous_report(
        self, tmp_path, monkeypatch
    ):
        store = make_store(tmp_path)
        key = make_key()
        _, first = publish(store, key, body="first")
        handle = store.begin_build(key)

        class ExplodingConnection:
            """sqlite3 connection double: UPDATE reports statements fail."""

            def __init__(self, real):
                self._real = real

            def execute(self, sql, parameters=()):
                if sql.startswith("UPDATE reports"):
                    raise sqlite3.OperationalError(
                        "injected mid-transaction failure"
                    )
                return self._real.execute(sql, parameters)

            def __getattr__(self, name):
                return getattr(self._real, name)

        real_connect = sqlite3.connect

        def exploding_connect(*args, **kwargs):
            return ExplodingConnection(real_connect(*args, **kwargs))

        monkeypatch.setattr(sqlite3, "connect", exploding_connect)
        with pytest.raises(sqlite3.OperationalError):
            store.publish(handle, body="second", references=[], source_manifest=[])
        monkeypatch.undo()

        current = store.get_current(key)
        assert current.body == "first"
        assert current.report_id == first.report_id
        # The rollback left the building row intact: the handle is usable.
        retry = store.publish(handle, body="second", references=[], source_manifest=[])
        assert store.get_current(key).report_id == retry.report_id


class TestPurge:
    def test_bounded_by_limit_and_returns_count(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        for i in range(5):
            publish(store, make_key(context=f"call-{i}"))
        clock.advance(hours=25)
        assert store.purge_expired(limit=2) == 2
        assert store.purge_expired(limit=2) == 2
        assert store.purge_expired(limit=2) == 1
        assert store.purge_expired(limit=2) == 0
        assert fetch_rows(store.path) == []

    def test_does_not_touch_unexpired_latest(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        old_key = make_key(context="old")
        fresh_key = make_key(context="fresh")
        publish(store, old_key)
        clock.advance(hours=1)
        publish(store, fresh_key)  # expires one hour later than old_key's
        clock.advance(hours=23)  # old exactly at horizon; fresh still inside
        assert store.purge_expired() == 1
        assert store.get_current(fresh_key).main_call_context_id == "fresh"
        assert store.get_current(old_key) is None

    def test_removes_superseded_revisions_past_the_horizon(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        key = make_key()
        _, first = publish(store, key, body="r1")
        clock.advance(hours=1)
        _, second = publish(store, key, body="r2")
        clock.advance(hours=23, microseconds=1)  # r1 past horizon; r2 not
        assert store.purge_expired() == 1
        assert [row["report_id"] for row in fetch_rows(store.path)] == [
            second.report_id
        ]
        assert store.get_current(key).report_id == second.report_id


class TestMalformedInput:
    def test_non_string_body_rejected(self, tmp_path):
        store = make_store(tmp_path)
        handle = store.begin_build(make_key())
        with pytest.raises(MalformedReportInput):
            store.publish(handle, body=42, references=[], source_manifest=[])

    @pytest.mark.parametrize(
        "bad_reference",
        [
            "not a mapping",
            {"source_id": "s"},
            {**REFERENCES[0], "locator": 3},
            {**REFERENCES[0], "surprise": "x"},
        ],
    )
    def test_malformed_reference_rejected(self, tmp_path, bad_reference):
        store = make_store(tmp_path)
        handle = store.begin_build(make_key())
        with pytest.raises(MalformedReportInput):
            store.publish(
                handle, body="b", references=[bad_reference], source_manifest=[]
            )

    @pytest.mark.parametrize(
        "bad_entry",
        [
            "not a mapping",
            {"source_id": "s", "revision_token": "t"},
            {**MANIFEST[0], "state": None},
            {**MANIFEST[0], "extra": "x"},
        ],
    )
    def test_malformed_manifest_entry_rejected(self, tmp_path, bad_entry):
        store = make_store(tmp_path)
        handle = store.begin_build(make_key())
        with pytest.raises(MalformedReportInput):
            store.publish(
                handle, body="b", references=[], source_manifest=[bad_entry]
            )

    @pytest.mark.parametrize(
        "interval_key",
        [
            "",
            "no pipes",
            "today|2026-09-30|fake|Z",  # naive bounds: not Period.key material
            "a|b|c|d|e",
            "today|not-a-date|2026-09-30T10:00:00+00:00|Europe/Madrid",
        ],
    )
    def test_malformed_interval_key_rejected_at_begin_build(
        self, tmp_path, interval_key
    ):
        store = make_store(tmp_path)
        key = ReportKey("call-1", "global", interval_key, "Europe/Madrid")
        with pytest.raises(MalformedReportInput):
            store.begin_build(key)

    def test_empty_key_component_rejected(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(MalformedReportInput):
            store.begin_build(ReportKey("", "global", PERIOD.key, "Europe/Madrid"))

    def test_rejected_publish_keeps_the_handle_usable(self, tmp_path):
        store = make_store(tmp_path)
        handle = store.begin_build(make_key())
        with pytest.raises(MalformedReportInput):
            store.publish(handle, body="b", references=["bad"], source_manifest=[])
        record = store.publish(handle, body="fixed", references=[], source_manifest=[])
        assert store.get_current(make_key()).report_id == record.report_id


class TestDefaultPath:
    def test_reports_db_lives_beside_the_audio_dir(self, tmp_path):
        from herdr_brain.config import Settings
        from tests.conftest import SETTINGS_KWARGS

        cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})
        assert default_reportstore_path(cfg) == tmp_path / REPORTS_DB_FILENAME


def rows_for_key(rows, key):
    return [
        row
        for row in rows
        if (
            row["main_call_context_id"],
            row["scope"],
            row["interval_key"],
            row["timezone"],
        )
        == (
            key.main_call_context_id,
            key.scope,
            key.interval_key,
            key.timezone,
        )
    ]


class TestRevisionRaces:
    """FR-33 / PRD "Revision race and cancellation": a cancelled or
    superseded request must never overwrite a newer report."""

    def test_cancel_discards_build_and_publish_is_rejected(self, tmp_path):
        store = make_store(tmp_path)
        key = make_key()
        handle = store.begin_build(key)
        store.cancel(handle)
        with pytest.raises(StaleBuildError):
            store.publish(handle, body="b", references=[], source_manifest=[])
        assert store.get_current(key) is None
        assert [row for row in fetch_rows(store.path) if row["status"] == "building"] == []

    def test_publish_rejects_older_sequence_regardless_of_call_order(self, tmp_path):
        store = make_store(tmp_path)
        key = make_key()
        first = store.begin_build(key)
        second = store.begin_build(key)
        third = store.begin_build(key)
        newest = store.publish(
            third, body="r3", references=[], source_manifest=[]
        )
        with pytest.raises(StaleBuildError):
            store.publish(second, body="r2", references=[], source_manifest=[])
        with pytest.raises(StaleBuildError):
            store.publish(first, body="r1", references=[], source_manifest=[])
        assert store.get_current(key).report_id == newest.report_id
        # A still-newer build remains welcome after the rejections.
        fourth = store.publish(
            store.begin_build(key), body="r4", references=[], source_manifest=[]
        )
        assert store.get_current(key).report_id == fourth.report_id

    def test_newer_publish_wins_even_when_called_second(self, tmp_path):
        store = make_store(tmp_path)
        key = make_key()
        older = store.begin_build(key)
        newer = store.begin_build(key)
        first = store.publish(older, body="r1", references=[], source_manifest=[])
        second = store.publish(newer, body="r2", references=[], source_manifest=[])
        assert store.get_current(key).report_id == second.report_id
        rows = {row["report_id"]: row for row in fetch_rows(store.path)}
        assert rows[first.report_id]["superseded_by"] == second.report_id
        assert rows[second.report_id]["superseded_by"] is None

    def test_double_publish_of_the_same_handle_is_rejected(self, tmp_path):
        store = make_store(tmp_path)
        handle, record = publish(store, make_key())
        with pytest.raises(StaleBuildError):
            store.publish(handle, body="again", references=[], source_manifest=[])
        assert store.get_current(make_key()).report_id == record.report_id


class TestRefreshFailed:
    """FR-33/D08: after a failed refresh the old report is never current."""

    def test_failed_refresh_is_not_current_but_stays_retrievable(self, tmp_path):
        store = make_store(tmp_path)
        key = make_key()
        _, record = publish(store, key)
        store.mark_refresh_failed(key)
        assert store.get_current(key) is None
        latest = store.get_latest(key)
        assert latest is not None
        assert latest.report_id == record.report_id
        assert latest.status == "refresh_failed"

    def test_expired_refresh_failed_is_excluded_from_get_latest(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        key = make_key()
        publish(store, key)
        store.mark_refresh_failed(key)
        clock.advance(hours=23, minutes=59, seconds=59, microseconds=999999)
        assert store.get_latest(key) is not None
        clock.advance(microseconds=1)  # exactly at the horizon
        assert store.get_latest(key) is None

    def test_mark_refresh_failed_without_published_report_is_a_noop(self, tmp_path):
        store = make_store(tmp_path)
        store.mark_refresh_failed(make_key())
        assert store.get_current(make_key()) is None
        assert fetch_rows(store.path) == []


class TestRestart:
    """PRD matrix row "Restart": reopen restores unexpired reports and
    discards mid-build leftovers."""

    def test_reopen_restores_unexpired_published_and_refresh_failed(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        key_a = make_key(context="a")
        key_b = make_key(context="b")
        _, record_a = publish(store, key_a, body="A")
        _, record_b = publish(store, key_b, body="B")
        store.mark_refresh_failed(key_b)

        reopened = ReportStore(store.path, clock=clock)
        current = reopened.get_current(key_a)
        assert current.body == "A"
        assert current.status == "published"
        assert current.report_id == record_a.report_id
        latest_b = reopened.get_latest(key_b)
        assert latest_b.status == "refresh_failed"
        assert latest_b.report_id == record_b.report_id
        assert reopened.get_current(key_b) is None

    def test_reopen_discards_leftover_building_rows(self, tmp_path):
        clock = fake_clock()
        store = ReportStore(tmp_path / REPORTS_DB_FILENAME, clock=clock)
        key = make_key()
        _, kept = publish(store, key, body="kept")
        leftover = store.begin_build(key)  # process "crashes" mid-build here

        reopened = ReportStore(store.path, clock=clock)
        with pytest.raises(StaleBuildError):
            reopened.publish(
                leftover, body="ghost", references=[], source_manifest=[]
            )
        assert reopened.get_current(key).report_id == kept.report_id
        assert [row for row in fetch_rows(store.path)
                if row["status"] == "building"] == []


class TestConcurrency:
    def test_same_key_parallel_publishes_yield_single_latest(self, tmp_path):
        store = make_store(tmp_path)
        key = make_key()

        # Deterministic core: an older handle must lose to an already
        # published newer revision even when published from another thread.
        older = store.begin_build(key)
        newer = store.begin_build(key)
        outcomes = {}

        def publish_newer():
            outcomes["newer"] = store.publish(
                newer, body="newer", references=[], source_manifest=[]
            ).report_id

        def publish_older():
            try:
                outcomes["older"] = store.publish(
                    older, body="older", references=[], source_manifest=[]
                ).report_id
            except StaleBuildError:
                outcomes["older"] = "stale"

        first_thread = threading.Thread(target=publish_newer)
        first_thread.start()
        first_thread.join()
        second_thread = threading.Thread(target=publish_older)
        second_thread.start()
        second_thread.join()
        assert outcomes["older"] == "stale"
        assert store.get_current(key).report_id == outcomes["newer"]

        # Storm: N threads race begin/publish on the same key; the highest
        # successfully published sequence must end up the single latest.
        workers = 8
        barrier = threading.Barrier(workers)
        storm = []

        def storm_worker(index):
            handle = store.begin_build(key)
            barrier.wait()
            try:
                record = store.publish(
                    handle, body=f"storm-{index}", references=[], source_manifest=[]
                )
                storm.append((handle.sequence, record.report_id, "ok"))
            except StaleBuildError:
                storm.append((handle.sequence, None, "stale"))

        threads = [
            threading.Thread(target=storm_worker, args=(i,)) for i in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        published = [(seq, rid) for seq, rid, state in storm if state == "ok"]
        assert published, "at least one storm publish must succeed"
        winner_sequence, winner_id = max(published)
        current = store.get_current(key)
        assert current.report_id == winner_id
        rows = rows_for_key(fetch_rows(store.path), key)
        live = [
            row
            for row in rows
            if row["superseded_by"] is None and row["status"] != "building"
        ]
        assert len(live) == 1
        assert live[0]["report_id"] == winner_id
        assert all(row["status"] != "building" for row in rows)
        for _sequence, report_id in published:
            if report_id != winner_id:
                superseded = next(
                    row for row in rows if row["report_id"] == report_id
                )
                assert superseded["superseded_by"] is not None

    def test_different_keys_threaded_do_not_interfere(self, tmp_path):
        store = make_store(tmp_path)
        key_a = make_key(context="call-a")
        key_b = make_key(context="call-b")

        def worker(key, prefix):
            for i in range(4):
                store.publish(
                    store.begin_build(key),
                    body=f"{prefix}-{i}",
                    references=[],
                    source_manifest=[],
                )

        threads = [
            threading.Thread(target=worker, args=(key_a, "a")),
            threading.Thread(target=worker, args=(key_b, "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert store.get_current(key_a).body == "a-3"
        assert store.get_current(key_b).body == "b-3"
        assert len(rows_for_key(fetch_rows(store.path), key_a)) == 4
        assert len(rows_for_key(fetch_rows(store.path), key_b)) == 4
