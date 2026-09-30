"""Unit tests for the persistent followup context store (FR-07,
FR-23, FR-24, FR-25; D03 last sentence, D07).

Deterministic harness per the PRD Requirement-to-Test Matrix rows
"Followup isolation" and "Persistent context and retention": a fake
injectable clock drives every retention/expiry boundary, threads drive
the concurrency smoke, and direct sqlite reads verify on-disk state
(the same convention as test_reportstore.py).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone

import pytest

from herdr_brain.followup import (
    FOLLOWUP_DB_FILENAME,
    FollowupContext,
    FollowupStore,
    FollowupStoreError,
    MalformedFollowupInput,
    default_followup_path,
    fingerprint_from_text,
)

UTC = timezone.utc

#: FR-24 by construction: no field or column may reference the Herdr
#: selected pane / session / selection surface in any spelling.
FORBIDDEN_FIELD_TOKENS = (
    "pane",
    "session",
    "selection",
    "screen",
    "window",
    "focus",
    "agent",
    "target",
)

EXPECTED_FIELDS = {
    "context_id",
    "anchor_report_id",
    "topic_fingerprint",
    "created_at",
    "last_used_at",
}


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


def make_store(tmp_path, clock=None, **kwargs):
    return FollowupStore(
        tmp_path / FOLLOWUP_DB_FILENAME, clock=clock or fake_clock(), **kwargs
    )


def fetch_rows(db_path):
    """Direct sqlite read of the raw table (on-disk truth, not the API)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM followups")]
    finally:
        conn.close()


def anchor(store, context="call-1", report="rep-1", fingerprint="topic-a"):
    return store.anchor(context, report, fingerprint)


class TestFollowupContext:
    """The record itself: shape, frozenness, FR-24 field isolation."""

    def test_has_exactly_the_prd_schema_fields(self):
        names = {field.name for field in fields(FollowupContext)}
        assert names == EXPECTED_FIELDS

    def test_no_pane_session_selection_field_in_any_spelling(self):
        for field in fields(FollowupContext):
            for token in FORBIDDEN_FIELD_TOKENS:
                assert token not in field.name, (
                    f"field {field.name!r} leaks pane/selection surface"
                    f" (token {token!r}, FR-24)"
                )

    def test_record_is_frozen(self):
        ctx = FollowupContext(
            "call-1", "rep-1", "topic-a", utc(2026, 9, 30), utc(2026, 9, 30)
        )
        with pytest.raises(FrozenInstanceError):
            ctx.context_id = "other"


class TestFingerprint:
    """fingerprint_from_text: deterministic normalization ONLY (the
    semantic half of topic detection belongs to T9, never here)."""

    def test_deterministic_across_calls(self):
        assert fingerprint_from_text(
            "What about the blockers?"
        ) == fingerprint_from_text("What about the blockers?")

    def test_case_and_whitespace_insensitive(self):
        noisy = "  Blockers \n in   the  HERDR  project\t"
        clean = "blockers in the herdr project"
        assert fingerprint_from_text(noisy) == fingerprint_from_text(clean)

    def test_real_change_yields_a_different_fingerprint(self):
        assert fingerprint_from_text(
            "blockers in the herdr project"
        ) != fingerprint_from_text("current status of the herdr project")

    def test_shape_is_sixteen_hex_chars(self):
        value = fingerprint_from_text("anything")
        assert len(value) == 16
        int(value, 16)  # hexadecimal, fail loud if not

    def test_non_string_rejected(self):
        with pytest.raises(TypeError):
            fingerprint_from_text(42)


class TestSchemaAndOpen:
    """Open conventions mirrored from test_reportstore.py."""

    def test_creates_parent_dirs_and_database_file(self, tmp_path):
        db = tmp_path / "nested" / "deeper" / FOLLOWUP_DB_FILENAME
        FollowupStore(db, clock=fake_clock())
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
        with pytest.raises(FollowupStoreError):
            FollowupStore(store.path, clock=fake_clock())

    def test_reopen_is_idempotent(self, tmp_path):
        store = make_store(tmp_path)
        record = anchor(store)
        reopened = FollowupStore(store.path, clock=fake_clock())
        assert reopened.get("call-1") == record


class TestAnchorRoundTrip:
    def test_anchor_roundtrips_every_field(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        record = store.anchor("call-1", "rep-1", "topic-a")
        assert record == FollowupContext(
            context_id="call-1",
            anchor_report_id="rep-1",
            topic_fingerprint="topic-a",
            created_at=clock(),
            last_used_at=clock(),
        )
        assert store.get("call-1") == record

    def test_stored_timestamps_are_timezone_aware_utc(self, tmp_path):
        store = make_store(tmp_path)
        record = anchor(store)
        assert record.created_at.tzinfo is not None
        assert record.created_at.utcoffset() == timedelta(0)
        assert record.last_used_at.tzinfo is not None

    @pytest.mark.parametrize(
        "bad_context,bad_report,bad_fingerprint",
        [
            ("", "rep-1", "topic-a"),
            ("   ", "rep-1", "topic-a"),
            (None, "rep-1", "topic-a"),
            ("call-1", "", "topic-a"),
            ("call-1", 7, "topic-a"),
            ("call-1", "rep-1", ""),
            ("call-1", "rep-1", b"topic-a"),
        ],
    )
    def test_malformed_anchor_input_rejected(
        self, tmp_path, bad_context, bad_report, bad_fingerprint
    ):
        store = make_store(tmp_path)
        with pytest.raises(MalformedFollowupInput):
            store.anchor(bad_context, bad_report, bad_fingerprint)
        assert fetch_rows(store.path) == []

    def test_naive_clock_result_rejected(self, tmp_path):
        store = FollowupStore(
            tmp_path / FOLLOWUP_DB_FILENAME,
            clock=lambda: datetime(2026, 9, 30, 10, 0, 0),  # naive: invalid
        )
        with pytest.raises(FollowupStoreError):
            store.anchor("call-1", "rep-1", "topic-a")

    def test_non_positive_retention_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            make_store(tmp_path, retention=timedelta(0))
        with pytest.raises(ValueError):
            make_store(tmp_path, retention=-timedelta(hours=1))


class TestContextIsolation:
    """Per-context isolation: two contexts never see each other's
    anchors (PRD matrix row "Persistent context and retention")."""

    def test_different_contexts_are_isolated(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store, context="call-1", report="rep-1", fingerprint="topic-a")
        anchor(store, context="call-2", report="rep-2", fingerprint="topic-b")
        assert store.get("call-1").anchor_report_id == "rep-1"
        assert store.get("call-2").anchor_report_id == "rep-2"
        assert store.get("call-3") is None

    def test_expiring_one_context_leaves_the_other_untouched(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store, context="call-1", report="rep-1")
        anchor(store, context="call-2", report="rep-2")
        store.expire("call-1")
        assert store.get("call-1") is None
        assert store.get("call-2").anchor_report_id == "rep-2"


class TestUpsert:
    """One row per context; a newer anchor always replaces the previous
    one (PRD Concurrency: a cancelled/superseded anchor never survives)."""

    def test_reanchor_replaces_the_previous_anchor_atomically(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store, report="rep-1", fingerprint="topic-a")
        clock.advance(hours=1)
        second = anchor(store, report="rep-2", fingerprint="topic-b")
        rows = fetch_rows(store.path)
        assert len(rows) == 1  # upsert: exactly one row per context
        assert rows[0]["anchor_report_id"] == "rep-2"
        assert rows[0]["topic_fingerprint"] == "topic-b"
        assert store.get("call-1") == second
        assert second.created_at == clock()
        assert second.last_used_at == clock()

    def test_reanchor_after_retention_expiry_starts_fresh(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(
            tmp_path / FOLLOWUP_DB_FILENAME, clock=clock, retention=timedelta(hours=1)
        )
        anchor(store, report="rep-1")
        clock.advance(hours=2)  # the stale row is past retention on disk
        fresh = anchor(store, report="rep-2")
        assert store.get("call-1") == fresh
        assert len(fetch_rows(store.path)) == 1


class TestTouch:
    """touch bumps last_used_at (D07: followups retain context across
    turns) but NEVER extends the retention horizon."""

    def test_touch_bumps_last_used_at_only(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        record = anchor(store)
        clock.advance(minutes=30)
        store.touch("call-1")
        touched = store.get("call-1")
        assert touched.last_used_at == clock()
        assert touched.created_at == record.created_at
        assert touched.anchor_report_id == record.anchor_report_id

    def test_touch_never_extends_life(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store)
        clock.advance(hours=12)
        store.touch("call-1")
        clock.advance(hours=12)  # exactly at the 24h horizon
        assert store.get("call-1") is None

    def test_touch_without_active_context_is_a_noop(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store)
        clock.advance(hours=25)
        store.touch("call-1")  # past retention: nothing active to retain
        assert store.get("call-1") is None
        rows = fetch_rows(store.path)
        assert rows[0]["last_used_at"] == rows[0]["created_at"]


class TestExpire:
    """Explicit return-to-normal / topic-change expiry (FR-23)."""

    def test_expire_removes_the_row_and_get_returns_none(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store)
        store.expire("call-1")
        assert store.get("call-1") is None
        assert fetch_rows(store.path) == []

    def test_reanchor_after_expire_works_fresh(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store, report="rep-1", fingerprint="topic-a")
        store.expire("call-1")
        clock.advance(minutes=5)
        fresh = anchor(store, report="rep-2", fingerprint="topic-b")
        assert store.get("call-1") == fresh
        assert fresh.created_at == clock()

    def test_expire_unknown_context_is_a_noop(self, tmp_path):
        store = make_store(tmp_path)
        store.expire("never-anchored")
        assert fetch_rows(store.path) == []


class TestRetention:
    """Retention mirrors the report store (default 24h): an anchor
    never outlives its report's horizon. Expiry is clock()-derived."""

    def test_boundary_exactly_at_and_just_before_expiry(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store)
        clock.advance(hours=23, minutes=59, seconds=59, microseconds=999999)
        assert store.get("call-1") is not None  # just before the horizon
        clock.advance(microseconds=1)  # exactly at the horizon
        assert store.get("call-1") is None

    def test_retention_horizon_is_configurable(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(
            tmp_path / FOLLOWUP_DB_FILENAME, clock=clock, retention=timedelta(hours=1)
        )
        anchor(store)
        clock.advance(minutes=59, seconds=59, microseconds=999999)
        assert store.get("call-1") is not None
        clock.advance(microseconds=1)
        assert store.get("call-1") is None


class TestPurge:
    def test_bounded_by_limit_and_returns_count(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        for i in range(5):
            anchor(store, context=f"call-{i}")
        clock.advance(hours=25)
        assert store.purge_expired(limit=2) == 2
        assert store.purge_expired(limit=2) == 2
        assert store.purge_expired(limit=2) == 1
        assert store.purge_expired(limit=2) == 0
        assert fetch_rows(store.path) == []

    def test_does_not_touch_unexpired_rows(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store, context="old")
        clock.advance(hours=1)
        anchor(store, context="fresh")  # one hour younger
        clock.advance(hours=23)  # old exactly at horizon; fresh still inside
        assert store.purge_expired() == 1
        assert store.get("old") is None
        assert store.get("fresh").context_id == "fresh"


class TestRestart:
    """PRD matrix row "Persistent context and retention": reopen
    restores unexpired anchors; expired-at-open rows are filtered on
    read (the documented restart policy)."""

    def test_reopen_restores_unexpired_and_filters_expired(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store, context="old", report="rep-old")
        clock.advance(hours=1)
        anchor(store, context="fresh", report="rep-fresh")
        clock.advance(hours=23)  # old at horizon; fresh has one hour left

        reopened = FollowupStore(store.path, clock=clock)
        assert reopened.get("old") is None
        fresh = reopened.get("fresh")
        assert fresh.anchor_report_id == "rep-fresh"
        assert fresh.created_at == utc(2026, 9, 30, 11, 0, 0)

    def test_expired_at_open_rows_remain_purgeable(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        anchor(store)
        clock.advance(hours=25)
        reopened = FollowupStore(store.path, clock=clock)
        assert reopened.get("call-1") is None  # filtered, not resurrected
        assert reopened.purge_expired() == 1
        assert fetch_rows(store.path) == []


class TestMatchesTopic:
    """Exact-equality fingerprint comparison; None means "no active
    followup" (fingerprint SEMANTICS stay in T9)."""

    def test_true_for_identical_fingerprint(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store, fingerprint="topic-a")
        assert store.matches_topic("call-1", "topic-a") is True

    def test_false_for_different_fingerprint(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store, fingerprint="topic-a")
        assert store.matches_topic("call-1", "topic-b") is False

    def test_none_without_active_context(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        assert store.matches_topic("call-1", "topic-a") is None  # never anchored
        anchor(store, fingerprint="topic-a")
        store.expire("call-1")
        assert store.matches_topic("call-1", "topic-a") is None  # expired
        anchor(store, fingerprint="topic-a")
        clock.advance(hours=25)
        assert store.matches_topic("call-1", "topic-a") is None  # past retention

    def test_comparison_is_exact_string_equality(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store, fingerprint="Topic-A")
        assert store.matches_topic("call-1", "topic-a") is False  # no normalizing


class TestMalformedRows:
    """A corrupt stored row fails loud, never silently degrades."""

    @pytest.mark.parametrize("column", ["created_at", "last_used_at"])
    def test_corrupt_timestamp_fails_loud_on_get(self, tmp_path, column):
        store = make_store(tmp_path)
        anchor(store)
        conn = sqlite3.connect(store.path)
        try:
            conn.execute(f"UPDATE followups SET {column} = 'not-a-date'")
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(FollowupStoreError):
            store.get("call-1")


class TestConcurrency:
    """Thread-safety smoke: one lock, one connection per call (the
    reportstore/HistoryStore convention)."""

    def test_two_threads_anchoring_different_contexts(self, tmp_path):
        clock = fake_clock()
        store = FollowupStore(tmp_path / FOLLOWUP_DB_FILENAME, clock=clock)
        rounds = 25
        errors = []

        def worker(context, report):
            try:
                for i in range(rounds):
                    store.anchor(context, f"{report}-{i}", f"topic-{i}")
                    store.get(context)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("call-a", "rep-a")),
            threading.Thread(target=worker, args=("call-b", "rep-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        rows = {row["context_id"]: row for row in fetch_rows(store.path)}
        assert set(rows) == {"call-a", "call-b"}  # exactly one row per context
        assert store.get("call-a").anchor_report_id == f"rep-a-{rounds - 1}"
        assert store.get("call-b").anchor_report_id == f"rep-b-{rounds - 1}"


class TestPaneIsolation:
    """FR-24 by construction: the persisted schema and records carry no
    pane/session/selection references at all."""

    def test_persisted_schema_contains_no_pane_references(self, tmp_path):
        store = make_store(tmp_path)
        anchor(store)
        conn = sqlite3.connect(store.path)
        conn.row_factory = sqlite3.Row
        try:
            schema_sql = " ".join(
                row[0]
                for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                )
            ).lower()
            row = dict(conn.execute("SELECT * FROM followups").fetchone())
        finally:
            conn.close()
        assert set(row) == EXPECTED_FIELDS
        for token in FORBIDDEN_FIELD_TOKENS:
            assert token not in schema_sql, (
                f"schema leaks pane/selection surface (token {token!r}, FR-24)"
            )
            for column in row:
                assert token not in column


class TestDefaultPath:
    def test_followup_db_lives_beside_the_audio_dir(self, tmp_path):
        from herdr_brain.config import Settings
        from tests.conftest import SETTINGS_KWARGS

        cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})
        assert default_followup_path(cfg) == tmp_path / FOLLOWUP_DB_FILENAME
