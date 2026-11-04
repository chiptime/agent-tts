"""Unit and HTTP surface tests for per-call history records and routes (T5)."""

from __future__ import annotations

import sqlite3
import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.history import (
    HISTORY_DB_FILENAME,
    HistoryStore,
    default_history_path,
)
from herdr_brain.server import CALL_HISTORY_TURNS, create_app
from tests.conftest import SETTINGS_KWARGS


class FakeLLM:
    """Simple fake LLM returning deterministic answers."""

    def __init__(self, answer="Respuesta simulada del brain"):
        self.calls: list = []
        self.ring_snapshots: list = []
        self.answer = answer
        self.attached_store = None

    def attach_store(self, store):
        self.attached_store = store

    def attach_approval_store(self, store):
        pass

    def ask(self, text, session_id=None, pane_id=None):
        self.calls.append(text)
        return {
            "answer": self.answer,
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or "default",
            "approval": None,
        }


class FakeTTS:
    def __call__(self, settings, text, out_path):
        out_path.write_bytes(b"ID3-fake")
        return out_path


@pytest.fixture
def audio_dir(tmp_path):
    return tmp_path / "audio"


def make_settings(audio_dir) -> Settings:
    return Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})


def make_client(audio_dir, llm=None):
    app = create_app(
        settings=make_settings(audio_dir),
        llm_factory=lambda _cfg, _tools: llm or FakeLLM(),
        tts_renderer=FakeTTS(),
    )
    return TestClient(app)


# ── HistoryStore per-call layer unit tests ────────────────────────────────────


class TestStoreCalls:
    def test_start_call_creates_open_call_and_returns_fresh_id(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        call_id = store.start_call()
        assert isinstance(call_id, str)
        assert len(call_id) > 0

        call = store.get_call(call_id)
        assert call is not None
        assert call["call_id"] == call_id
        assert call["status"] == "open"
        assert call["ended_at"] is None
        assert call["turn_count"] == 0
        assert call["duration_s"] is None
        assert "started_at" in call

    def test_start_call_closes_dangling_open_calls_as_interrupted(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c1 = store.start_call()
        store.append("user", "pregunta previa", call_id=c1)

        c2 = store.start_call()
        assert c1 != c2

        call1 = store.get_call(c1)
        assert call1["status"] == "interrupted"
        assert call1["ended_at"] is not None
        assert call1["turn_count"] == 1
        assert call1["duration_s"] is not None

        call2 = store.get_call(c2)
        assert call2["status"] == "open"

    def test_start_call_dangling_with_no_turns_uses_started_at_as_ended_at(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c1 = store.start_call()
        call1_initial = store.get_call(c1)
        c2 = store.start_call()

        call1 = store.get_call(c1)
        assert call1["status"] == "interrupted"
        assert call1["ended_at"] == call1_initial["started_at"]
        assert call1["turn_count"] == 0
        assert call1["duration_s"] == 0.0

    def test_end_call_finalizes_with_duration_turn_count_and_ended_status(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c = store.start_call()
        store.append("user", "pregunta", call_id=c)
        store.append("assistant", "respuesta", call_id=c)

        finalized = store.end_call(c)
        assert finalized["call_id"] == c
        assert finalized["status"] == "ended"
        assert finalized["turn_count"] == 2
        assert finalized["ended_at"] is not None
        assert finalized["duration_s"] is not None
        assert finalized["duration_s"] >= 0.0

        persisted = store.get_call(c)
        assert persisted["status"] == "ended"
        assert persisted["turn_count"] == 2

    def test_end_call_unknown_id_fails_closed(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        with pytest.raises(KeyError):
            store.end_call("call-unknown")

    def test_end_call_already_closed_fails_closed(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c = store.start_call()
        store.end_call(c)
        with pytest.raises(ValueError):
            store.end_call(c)

    def test_call_turns_unknown_id_fails_closed(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        with pytest.raises(KeyError):
            store.call_turns("call-unknown")

    def test_call_turns_masks_text(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c = store.start_call()
        store.append("user", "mi secreto es sk-abcdef12345678901234567890", call_id=c)
        turns = store.call_turns(c)
        assert len(turns) == 1
        assert "sk-abcdef" not in turns[0]["text"]
        assert "[masked]" in turns[0]["text"]

    def test_call_turns_oldest_first_and_pagination(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c = store.start_call()
        for i in range(5):
            store.append("user", f"turn-{i}", call_id=c)

        # limit takes the newest slice of older set, oldest first
        page1 = store.call_turns(c, limit=2)
        assert len(page1) == 2
        assert [t["text"] for t in page1] == ["turn-3", "turn-4"]

        # page before cursor
        page2 = store.call_turns(c, before_ts=page1[0]["ts"], limit=2)
        assert len(page2) == 2
        assert [t["text"] for t in page2] == ["turn-1", "turn-2"]

    def test_list_calls_newest_first_and_pagination(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c1 = store.start_call()
        store.end_call(c1)
        c2 = store.start_call()
        store.end_call(c2)
        c3 = store.start_call()
        store.end_call(c3)

        page1 = store.list_calls(limit=2)
        assert len(page1) == 2
        assert [c["call_id"] for c in page1] == [c3, c2]
        # Metadata only: no turn text in the list
        assert "text" not in page1[0]
        assert "turns" not in page1[0]

        cursor = page1[-1]["started_at"]
        page2 = store.list_calls(before_ts=cursor, limit=2)
        assert len(page2) == 1
        assert page2[0]["call_id"] == c1

    def test_resume_call_reopens_ended_call_with_fresh_started_at_and_raw_turns(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c1 = store.start_call()
        store.append("user", "hola", call_id=c1)
        store.append("assistant", "buenas", call_id=c1)
        store.end_call(c1)

        call, turns = store.resume_call(c1)
        assert call["call_id"] == c1
        assert call["status"] == "open"
        assert call["ended_at"] is None
        assert call["duration_s"] is None
        assert call["turn_count"] == 2
        # RAW turns (no S14 masking): they seed the conversation ring,
        # exactly like the boot-time load_and_compact path.
        assert [(t["role"], t["text"]) for t in turns] == [
            ("user", "hola"),
            ("assistant", "buenas"),
        ]

    def test_resume_call_unknown_id_fails_closed(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        with pytest.raises(KeyError):
            store.resume_call("call-unknown")

    def test_resume_call_closes_other_dangling_open_calls(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c1 = store.start_call()
        store.append("user", "colgada sin cerrar", call_id=c1)
        c2 = store.start_call()
        store.end_call(c2)

        call, _turns = store.resume_call(c2)
        assert call["status"] == "open"

        dangling = store.get_call(c1)
        assert dangling["status"] == "interrupted"
        assert dangling["ended_at"] is not None

    def test_resume_call_on_open_call_keeps_started_at(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        c = store.start_call()
        original = store.get_call(c)
        store.append("user", "en curso", call_id=c)

        call, turns = store.resume_call(c)
        assert call["status"] == "open"
        assert call["started_at"] == original["started_at"]
        assert [(t["role"], t["text"]) for t in turns] == [("user", "en curso")]

    def test_append_2_arg_legacy_leaves_call_id_null(self, tmp_path):
        store = HistoryStore(tmp_path / HISTORY_DB_FILENAME)
        store.append("user", "legacy turn")
        turns = store.load()
        assert len(turns) == 1
        assert turns[0]["text"] == "legacy turn"

        conn = sqlite3.connect(store.path)
        try:
            row = conn.execute("SELECT ts, role, text, call_id FROM turns").fetchone()
            assert row[2] == "legacy turn"
            assert row[3] is None
        finally:
            conn.close()

    def test_migration_from_legacy_schema_db(self, tmp_path):
        db_path = tmp_path / HISTORY_DB_FILENAME
        # Create DB using strictly the legacy schema (no call_id column, no calls table)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO turns (ts, role, text) VALUES ('2026-01-01T00:00:00Z', 'user', 'antiguo')")
        conn.commit()
        conn.close()

        # Open with new HistoryStore
        store = HistoryStore(db_path)
        # Verify legacy turn is still readable
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0]["text"] == "antiguo"

        # Verify call_id column was added and calls table exists
        conn = sqlite3.connect(db_path)
        try:
            turn_cols = {col[1] for col in conn.execute("PRAGMA table_info(turns)").fetchall()}
            assert "call_id" in turn_cols
            calls_cols = {col[1] for col in conn.execute("PRAGMA table_info(calls)").fetchall()}
            assert "call_id" in calls_cols
            assert "status" in calls_cols
        finally:
            conn.close()

        # Appending new call work operates smoothly
        cid = store.start_call()
        store.append("user", "nuevo", call_id=cid)
        store.end_call(cid)
        assert store.get_call(cid)["status"] == "ended"


# ── Route surface tests via TestClient ────────────────────────────────────────


class TestCallRoutes:
    def test_call_start_records_active_call_and_tags_ask_turns(self, audio_dir):
        client = make_client(audio_dir)

        # Start call
        resp_start = client.post("/call/start")
        assert resp_start.status_code == 200
        start_data = resp_start.json()
        assert "call_id" in start_data
        assert "started_at" in start_data
        call_id = start_data["call_id"]

        # Run /ask turns while call is active
        ask1 = client.post("/ask", json={"text": "primera pregunta"})
        assert ask1.status_code == 200
        ask2 = client.post("/ask", json={"text": "segunda pregunta"})
        assert ask2.status_code == 200

        # End call
        resp_end = client.post("/call/end")
        assert resp_end.status_code == 200
        end_data = resp_end.json()
        assert end_data["call_id"] == call_id
        assert end_data["status"] == "ended"
        assert end_data["turn_count"] == 4  # 2 user + 2 assistant
        assert end_data["duration_s"] is not None

        # Verify call detail page
        resp_detail = client.get(f"/calls/{call_id}")
        assert resp_detail.status_code == 200
        detail = resp_detail.json()
        assert detail["call"]["call_id"] == call_id
        assert len(detail["turns"]) == 4
        assert detail["turns"][0]["text"] == "primera pregunta"
        assert detail["turns"][0]["role"] == "user"
        assert detail["has_more"] is False

    def test_call_end_with_explicit_body_id(self, audio_dir):
        client = make_client(audio_dir)
        resp_start = client.post("/call/start")
        call_id = resp_start.json()["call_id"]

        resp_end = client.post("/call/end", json={"call_id": call_id})
        assert resp_end.status_code == 200
        assert resp_end.json()["call_id"] == call_id
        assert resp_end.json()["status"] == "ended"

    def test_call_end_fails_404_when_no_active_call(self, audio_dir):
        client = make_client(audio_dir)
        resp = client.post("/call/end")
        assert resp.status_code == 404

    def test_call_end_fails_404_when_unknown_call_id(self, audio_dir):
        client = make_client(audio_dir)
        resp = client.post("/call/end", json={"call_id": "call-nonexistent"})
        assert resp.status_code == 404

    def test_call_end_fails_409_when_already_closed(self, audio_dir):
        client = make_client(audio_dir)
        resp_start = client.post("/call/start")
        call_id = resp_start.json()["call_id"]
        client.post("/call/end")

        resp_again = client.post("/call/end", json={"call_id": call_id})
        assert resp_again.status_code == 409

    def test_get_calls_pagination_and_clamping(self, audio_dir):
        store = HistoryStore(default_history_path(make_settings(audio_dir)))
        call_ids = []
        for i in range(30):
            cid = store.start_call()
            store.end_call(cid)
            call_ids.append(cid)

        client = make_client(audio_dir)
        resp = client.get("/calls")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["calls"]) == 25
        assert body["has_more"] is True
        assert body["calls"][0]["call_id"] == call_ids[-1]

        # Clamping check: limit > CALL_HISTORY_TURNS is clamped
        resp_clamped = client.get(f"/calls?limit={CALL_HISTORY_TURNS + 50}")
        assert resp_clamped.status_code == 200
        assert len(resp_clamped.json()["calls"]) <= CALL_HISTORY_TURNS

    def test_get_call_detail_404_for_unknown_id(self, audio_dir):
        client = make_client(audio_dir)
        resp = client.get("/calls/nonexistent-call-id")
        assert resp.status_code == 404

class RingSnapshotLLM(FakeLLM):
    """FakeLLM that snapshots the conversation ring at every ask."""

    def ask(self, text, session_id=None, pane_id=None):
        self.calls.append(text)
        if self.attached_store is not None:
            key = session_id or "default"
            self.ring_snapshots.append(
                [(m.role, m.content) for m in self.attached_store.history(key)]
            )
        return super().ask(text, session_id=session_id, pane_id=pane_id)


class TestResumeRoute:
    def test_resume_reopens_call_seeds_ring_and_tags_new_turns(self, audio_dir):
        llm = RingSnapshotLLM()
        client = make_client(audio_dir, llm=llm)

        # First call: two turns, then ended.
        first = client.post("/call/start").json()["call_id"]
        client.post("/ask", json={"text": "pregunta vieja"})
        client.post("/call/end")

        # An unrelated later call pollutes the conversation ring.
        client.post("/call/start")
        client.post("/ask", json={"text": "otra llamada"})

        # Resume the FIRST call.
        resp = client.post(f"/calls/{first}/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["call_id"] == first
        assert body["status"] == "open"

        # New turns are tagged with the resumed call, not the polluter.
        client.post("/ask", json={"text": "seguimos"})
        detail = client.get(f"/calls/{first}").json()
        texts = [t["text"] for t in detail["turns"]]
        assert "pregunta vieja" in texts
        assert "seguimos" in texts
        assert "otra llamada" not in texts

        # The ring was RESEEDED from the resumed call only: the resumed
        # call's turns are the context, the polluter's turns are gone.
        resumed_ring = llm.ring_snapshots[-1]
        contents = [content for _role, content in resumed_ring]
        assert "pregunta vieja" in contents
        assert "otra llamada" not in contents
        assert "seguimos" not in contents  # the new question rides the prompt, not the ring

        # Ending the call finalizes the REOPENED record.
        end = client.post("/call/end").json()
        assert end["call_id"] == first
        assert end["status"] == "ended"
        assert end["turn_count"] == 4  # pregunta vieja + answer + seguimos + answer

    def test_resume_after_end_reports_resumed_session_duration(self, audio_dir):
        client = make_client(audio_dir)
        first = client.post("/call/start").json()["call_id"]
        client.post("/ask", json={"text": "hola"})
        ended = client.post("/call/end").json()

        resumed = client.post(f"/calls/{first}/resume").json()
        assert resumed["duration_s"] is None
        assert resumed["started_at"] >= ended["started_at"]

    def test_resume_unknown_call_404(self, audio_dir):
        client = make_client(audio_dir)
        resp = client.post("/calls/call-nonexistent/resume")
        assert resp.status_code == 404

    def test_call_history_and_boot_ring_byte_compat_with_no_call_db(self, audio_dir):
        cfg = make_settings(audio_dir)
        store = HistoryStore(default_history_path(cfg))
        # Add turns without call_id
        store.append("user", "legacy q")
        store.append("assistant", "legacy a")

        # GET /call-history still serves them identically
        client = make_client(audio_dir)
        resp = client.get("/call-history")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["turns"]) == 2
        assert body["turns"][0]["text"] == "legacy q"
        assert body["turns"][1]["text"] == "legacy a"

        # Boot seed check: ConversationStore has the records
        # Verify GET /calls list has 0 calls even though turns exist
        calls_resp = client.get("/calls")
        assert calls_resp.status_code == 200
        assert calls_resp.json() == {"calls": [], "has_more": False}
