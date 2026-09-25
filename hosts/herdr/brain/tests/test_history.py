"""Unit tests for the JSONL call history store."""

from __future__ import annotations

import json

from herdr_brain.history import (
    COMPACT_ABOVE,
    COMPACT_KEEP,
    HistoryStore,
    default_history_path,
)


def read_lines(path) -> list:
    return path.read_text(encoding="utf-8").splitlines()


class TestAppendLoad:
    def test_load_returns_empty_when_file_missing(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        assert store.load() == []
        assert store.load(last_n=5) == []

    def test_append_then_load_roundtrip_oldest_first(self, tmp_path):
        store = HistoryStore(tmp_path / "nested" / "call_history.jsonl")
        store.append("user", "qué tal")
        store.append("assistant", "todo verde")
        turns = store.load()
        assert [(t["role"], t["text"]) for t in turns] == [
            ("user", "qué tal"),
            ("assistant", "todo verde"),
        ]

    def test_records_carry_iso8601_utc_timestamp(self, tmp_path):
        from datetime import datetime

        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "hola")
        ts = store.load()[0]["ts"]
        # Parses as ISO 8601 and carries an explicit UTC offset.
        parsed = datetime.fromisoformat(ts)
        assert parsed.utcoffset().total_seconds() == 0

    def test_record_keys_are_exactly_ts_role_text(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "hola")
        record = json.loads(read_lines(store.path)[0])
        assert set(record) == {"ts", "role", "text"}

    def test_append_empty_text_is_stored_as_empty_string(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("assistant", None)
        assert store.load()[0]["text"] == ""

    def test_load_last_n_returns_newest_tail_oldest_first(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        for i in range(6):
            store.append("user", f"t{i}")
        turns = store.load(last_n=3)
        assert [t["text"] for t in turns] == ["t3", "t4", "t5"]

    def test_load_last_n_larger_than_history_returns_all(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "only")
        assert len(store.load(last_n=100)) == 1

    def test_load_last_n_zero_returns_empty(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "only")
        assert store.load(last_n=0) == []


class TestLoadBefore:
    """Cursor pagination: strictly-older records, oldest first."""

    def _store_with(self, tmp_path, ts_texts):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        with store.path.open("w", encoding="utf-8") as fh:
            for ts, text in ts_texts:
                fh.write(json.dumps({"ts": ts, "role": "user", "text": text}) + "\n")
        return store

    def test_none_cursor_returns_newest_page(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
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
        store = HistoryStore(tmp_path / "call_history.jsonl")
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
        store = HistoryStore(tmp_path / "call_history.jsonl")
        assert store.load_before(None, 25) == []
        assert store.load_before("2026-09-25T10:00:00+00:00", 25) == []

    def test_zero_limit_returns_empty(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "only")
        assert store.load_before(None, 0) == []


class TestClear:
    def test_clear_removes_every_record(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "uno")
        store.append("assistant", "dos")
        store.clear()
        assert store.load() == []
        assert not store.path.exists()

    def test_clear_on_missing_file_does_not_raise(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.clear()

    def test_append_after_clear_starts_fresh(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "old")
        store.clear()
        store.append("user", "new")
        assert [t["text"] for t in store.load()] == ["new"]


class TestCorruptLines:
    def test_truncated_trailing_line_is_skipped(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "uno")
        store.append("assistant", "dos")
        # Simulate a crash mid-write: half a JSON line at the tail.
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-09-25T')
        turns = store.load()
        assert [(t["role"], t["text"]) for t in turns] == [
            ("user", "uno"),
            ("assistant", "dos"),
        ]

    def test_garbage_line_anywhere_is_skipped(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "uno")
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write("not json at all\n\n")
        store.append("assistant", "dos")
        assert len(store.load()) == 2

    def test_well_typed_but_invalid_records_are_skipped(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        store.append("user", "uno")
        bad_records = [
            '{"ts": "x", "role": "system", "text": "bad role"}',
            '{"ts": "x", "role": "user", "text": 7}',
            '{"ts": null, "role": "user", "text": "no ts"}',
            '"just a string"',
        ]
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(bad_records) + "\n")
        store.append("assistant", "dos")
        turns = store.load()
        assert [(t["role"], t["text"]) for t in turns] == [
            ("user", "uno"),
            ("assistant", "dos"),
        ]


class TestCompaction:
    def test_load_and_compact_rewrites_to_last_1000(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        for i in range(COMPACT_ABOVE + 50):
            store.append("user", f"t{i}")
        turns = store.load_and_compact()
        assert len(turns) == COMPACT_KEEP
        # The kept tail is the NEWEST 1000 records, oldest first.
        assert turns[0]["text"] == f"t{COMPACT_ABOVE + 50 - COMPACT_KEEP}"
        assert turns[-1]["text"] == f"t{COMPACT_ABOVE + 49}"
        assert len(read_lines(store.path)) == COMPACT_KEEP

    def test_load_and_compact_honors_last_n(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        for i in range(COMPACT_ABOVE + 10):
            store.append("user", f"t{i}")
        turns = store.load_and_compact(last_n=16)
        assert len(turns) == 16
        assert turns[0]["text"] == f"t{COMPACT_ABOVE + 10 - 16}"

    def test_no_rewrite_below_threshold(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        for i in range(50):
            store.append("user", f"t{i}")
        before = read_lines(store.path)
        turns = store.load_and_compact()
        assert len(turns) == 50
        assert read_lines(store.path) == before

    def test_compaction_drops_a_corrupt_trailing_line_first(self, tmp_path):
        store = HistoryStore(tmp_path / "call_history.jsonl")
        for i in range(COMPACT_ABOVE + 5):
            store.append("user", f"t{i}")
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "broken')
        turns = store.load_and_compact()
        # The corrupt line never counts nor survives the rewrite.
        assert len(turns) == COMPACT_KEEP
        assert len(read_lines(store.path)) == COMPACT_KEEP


class TestDefaultPath:
    def test_history_file_lives_beside_the_audio_dir(self, tmp_path):
        from herdr_brain.config import Settings
        from tests.conftest import SETTINGS_KWARGS

        cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})
        assert default_history_path(cfg) == tmp_path / "call_history.jsonl"
