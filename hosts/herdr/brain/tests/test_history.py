"""Unit tests for the sqlite call history store."""

from __future__ import annotations

import json
import sqlite3

from herdr_brain.history import (
    COMPACT_ABOVE,
    COMPACT_KEEP,
    HISTORY_DB_FILENAME,
    IMPORTED_SUFFIX,
    LEGACY_HISTORY_FILENAME,
    HistoryStore,
    default_history_path,
)


def count_rows(db_path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    finally:
        conn.close()


def seed_rows(store, ts_texts) -> None:
    """Seeds (ts, text) pairs as user turns with explicit timestamps."""
    conn = sqlite3.connect(store.path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO turns (ts, role, text) VALUES (?, 'user', ?)",
                list(ts_texts),
            )
    finally:
        conn.close()


def write_jsonl(path, lines) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestAppendLoad:
    def test_load_returns_empty_when_db_missing_after_clear(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.clear()
        assert store.load() == []
        assert store.load(last_n=5) == []

    def test_append_then_load_roundtrip_oldest_first(self, tmp_path):
        store = HistoryStore(tmp_path / "nested" / HISTORY_DB_FILENAME)
        store.append("user", "qué tal")
        store.append("assistant", "todo verde")
        turns = store.load()
        assert [(t["role"], t["text"]) for t in turns] == [
            ("user", "qué tal"),
            ("assistant", "todo verde"),
        ]

    def test_records_carry_iso8601_utc_timestamp(self, tmp_path):
        from datetime import datetime

        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "hola")
        ts = store.load()[0]["ts"]
        # Parses as ISO 8601 and carries an explicit UTC offset.
        parsed = datetime.fromisoformat(ts)
        assert parsed.utcoffset().total_seconds() == 0

    def test_table_shape_is_id_ts_role_text(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "hola")
        conn = sqlite3.connect(store.path)
        try:
            cursor = conn.execute("SELECT * FROM turns")
            columns = {d[0] for d in cursor.description}
            row = cursor.fetchone()
        finally:
            conn.close()
        assert columns == {"id", "ts", "role", "text"}
        assert (row[1], row[2], row[3]) == (store.load()[0]["ts"], "user", "hola")

    def test_append_empty_text_is_stored_as_empty_string(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("assistant", None)
        assert store.load()[0]["text"] == ""

    def test_load_last_n_returns_newest_tail_oldest_first(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        for i in range(6):
            store.append("user", f"t{i}")
        turns = store.load(last_n=3)
        assert [t["text"] for t in turns] == ["t3", "t4", "t5"]

    def test_load_last_n_larger_than_history_returns_all(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "only")
        assert len(store.load(last_n=100)) == 1

    def test_load_last_n_zero_returns_empty(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "only")
        assert store.load(last_n=0) == []


class TestLoadBefore:
    """Cursor pagination: strictly-older records, oldest first."""

    def _store_with(self, tmp_path, ts_texts):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        seed_rows(store, ts_texts)
        return store

    def test_none_cursor_returns_newest_page(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        for i in range(5):
            store.append("user", f"t{i}")
        turns = store.load_before(None, 3)
        assert [t["text"] for t in turns] == ["t2", "t3", "t4"]

    def test_returns_only_strictly_older_records(self, tmp_path):
        store = self._store_with(tmp_path, [
            ("2026-09-25T10:00:00+00:00", "a"),
            ("2026-09-25T10:00:01+00:00", "b"),   # equal to the cursor: excluded
            ("2026-09-25T10:00:02+00:00", "c"),
        ])
        turns = store.load_before("2026-09-25T10:00:01+00:00", 10)
        assert [t["text"] for t in turns] == ["a"]

    def test_limit_takes_the_newest_slice_of_the_older_set(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        for i in range(6):
            store.append("user", f"t{i}")
        # Cursor just above t4: older set is t0..t3, newest 2 of it → t2,t3.
        cursor = store.load()[4]["ts"]
        turns = store.load_before(cursor, 2)
        assert [t["text"] for t in turns] == ["t2", "t3"]

    def test_comparison_is_instant_based_across_offsets(self, tmp_path):
        # 09:00-02:00 is 11:00 UTC — NEWER than the 10:30 UTC cursor,
        # though lexically "09..." < "10...". String compare would lie.
        store = self._store_with(tmp_path, [
            ("2026-09-25T09:00:00-02:00", "later-in-utc"),
            ("2026-09-25T09:00:00+00:00", "truly-older"),
        ])
        turns = store.load_before("2026-09-25T10:30:00+00:00", 10)
        assert [t["text"] for t in turns] == ["truly-older"]

    def test_microsecond_precision_compares_as_instant(self, tmp_path):
        store = self._store_with(tmp_path, [
            ("2026-09-25T10:00:00+00:00", "older"),
            ("2026-09-25T10:00:00.5+00:00", "newer"),  # fractional precision
        ])
        turns = store.load_before("2026-09-25T10:00:00.25+00:00", 10)
        assert [t["text"] for t in turns] == ["older"]

    def test_unparseable_cursor_falls_back_to_string_compare(self, tmp_path):
        store = self._store_with(tmp_path, [
            ("2026-09-25T10:00:00+00:00", "a"),
            ("2026-09-25T11:00:00+00:00", "b"),
        ])
        # "not-a-ts" parses as neither; every ISO record sorts before it.
        turns = store.load_before("not-a-ts", 1)
        assert [t["text"] for t in turns] == ["b"]

    def test_empty_store_returns_empty_page(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        assert store.load_before(None, 25) == []
        assert store.load_before("2026-09-25T10:00:00+00:00", 25) == []

    def test_zero_limit_returns_empty(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "only")
        assert store.load_before(None, 0) == []


class TestClear:
    def test_clear_removes_every_record(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "uno")
        store.append("assistant", "dos")
        store.clear()
        assert store.load() == []
        assert not store.path.exists()

    def test_clear_on_missing_file_does_not_raise(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.clear()

    def test_append_after_clear_starts_fresh(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "old")
        store.clear()
        store.append("user", "new")
        assert [t["text"] for t in store.load()] == ["new"]


class TestCompaction:
    def test_load_and_compact_trims_to_last_1000(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        seed_rows(store, [(f"2026-09-25T10:00:{i % 60:02d}.{i:06d}+00:00", f"t{i}")
                          for i in range(COMPACT_ABOVE + 50)])
        turns = store.load_and_compact()
        assert len(turns) == COMPACT_KEEP
        # The kept tail is the NEWEST 1000 records, oldest first.
        assert turns[0]["text"] == f"t{COMPACT_ABOVE + 50 - COMPACT_KEEP}"
        assert turns[-1]["text"] == f"t{COMPACT_ABOVE + 49}"
        assert count_rows(store.path) == COMPACT_KEEP

    def test_load_and_compact_honors_last_n(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        seed_rows(store, [(f"2026-09-25T10:00:{i % 60:02d}.{i:06d}+00:00", f"t{i}")
                          for i in range(COMPACT_ABOVE + 10)])
        turns = store.load_and_compact(last_n=16)
        assert len(turns) == 16
        assert turns[0]["text"] == f"t{COMPACT_ABOVE + 10 - 16}"

    def test_no_delete_below_threshold(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        for i in range(50):
            store.append("user", f"t{i}")
        turns = store.load_and_compact()
        assert len(turns) == 50
        assert count_rows(store.path) == 50


class TestMigration:
    """One-time import of the legacy JSONL file into the database."""

    def _legacy_jsonl(self, tmp_path, texts):
        legacy = tmp_path / LEGACY_HISTORY_FILENAME
        lines = [
            json.dumps(
                {"ts": f"2026-09-25T10:00:{i:02d}+00:00", "role": "user", "text": t}
            )
            for i, t in enumerate(texts)
        ]
        write_jsonl(legacy, lines)
        return legacy

    def test_db_file_created_at_expected_path(self, tmp_path):
        db = tmp_path / "nested" / HISTORY_DB_FILENAME
        HistoryStore(db)
        assert db.exists()

    def test_migration_imports_jsonl_and_renames_it(self, tmp_path):
        legacy = self._legacy_jsonl(tmp_path, ["uno", "dos"])
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        assert [t["text"] for t in store.load()] == ["uno", "dos"]
        assert not legacy.exists()
        assert (tmp_path / (LEGACY_HISTORY_FILENAME + IMPORTED_SUFFIX)).exists()

    def test_second_boot_does_not_double_import(self, tmp_path):
        self._legacy_jsonl(tmp_path, ["uno", "dos"])
        first = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        second = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        assert [t["text"] for t in second.load()] == ["uno", "dos"]
        assert count_rows(first.path) == 2
        # The retired file keeps its renamed shape — nothing re-imports it.
        assert (tmp_path / (LEGACY_HISTORY_FILENAME + IMPORTED_SUFFIX)).exists()

    def test_non_empty_db_skips_import_but_still_renames(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "already-here")
        legacy = self._legacy_jsonl(tmp_path, ["from-jsonl"])
        fresh = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        assert [t["text"] for t in fresh.load()] == ["already-here"]
        assert not legacy.exists()

    def test_corrupt_lines_are_skipped_on_import(self, tmp_path):
        legacy = tmp_path / LEGACY_HISTORY_FILENAME
        write_jsonl(legacy, [
            json.dumps({"ts": "2026-09-25T10:00:00+00:00", "role": "user", "text": "uno"}),
            '{"ts": "2026-09-25T',                     # truncated trailing line
            "not json at all",                          # garbage line
            json.dumps({"ts": "x", "role": "system", "text": "bad role"}),
            json.dumps({"ts": "x", "role": "user", "text": 7}),
            json.dumps({"ts": None, "role": "user", "text": "no ts"}),
            '"just a string"',
            json.dumps({"ts": "2026-09-25T10:00:01+00:00", "role": "assistant", "text": "dos"}),
        ])
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        turns = store.load()
        assert [(t["role"], t["text"]) for t in turns] == [
            ("user", "uno"),
            ("assistant", "dos"),
        ]


class TestDefaultPath:
    def test_history_db_lives_beside_the_audio_dir(self, tmp_path):
        from herdr_brain.config import Settings
        from tests.conftest import SETTINGS_KWARGS

        cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})
        assert default_history_path(cfg) == tmp_path / HISTORY_DB_FILENAME
