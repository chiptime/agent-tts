"""HTTP surface tests for call-history persistence (/ask, /reset, boot)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.history import HistoryStore, default_history_path
from herdr_brain.llm import BrainLLMError
from herdr_brain.server import CALL_HISTORY_TURNS, create_app
from tests.conftest import SETTINGS_KWARGS


class FakeLLM:
    """Records the conversation ring as the real loop would see it."""

    def __init__(self, answer="Estás en la fase 2 del brain; todo verde."):
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
        self.ring_snapshots.append(
            list(self.attached_store.history(session_id))
            if self.attached_store is not None
            else []
        )
        return {
            "answer": self.answer,
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or "default",
            "approval": None,
        }


class FailingLLM(FakeLLM):
    def ask(self, text, session_id=None, pane_id=None):
        raise BrainLLMError("backend down")


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


class TestCallHistoryEndpoint:
    def test_empty_history_on_clean_boot(self, audio_dir):
        resp = make_client(audio_dir).get("/call-history")
        assert resp.status_code == 200
        assert resp.json() == {"turns": []}

    def test_serving_cap_returns_last_200_oldest_first(self, audio_dir):
        store = HistoryStore(default_history_path(make_settings(audio_dir)))
        for i in range(CALL_HISTORY_TURNS + 5):
            store.append("user", f"t{i}")
        resp = make_client(audio_dir).get("/call-history")
        turns = resp.json()["turns"]
        assert len(turns) == CALL_HISTORY_TURNS
        assert turns[0]["text"] == "t5"
        assert turns[-1]["text"] == f"t{CALL_HISTORY_TURNS + 4}"


class TestAskPersistence:
    def test_ask_roundtrip_writes_exactly_two_records(self, audio_dir):
        client = make_client(audio_dir)
        resp = client.post("/ask", json={"text": "cómo va todo"})
        assert resp.status_code == 200
        turns = client.get("/call-history").json()["turns"]
        assert len(turns) == 2
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert [t["text"] for t in turns] == [
            "cómo va todo",
            "Estás en la fase 2 del brain; todo verde.",
        ]
        assert all(set(t) == {"ts", "role", "text"} for t in turns)
        assert all(isinstance(t["ts"], str) for t in turns)

    def test_repeated_asks_append_never_replace(self, audio_dir):
        client = make_client(audio_dir)
        client.post("/ask", json={"text": "uno"})
        client.post("/ask", json={"text": "dos"})
        turns = client.get("/call-history").json()["turns"]
        assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant"]
        assert [t["text"] for t in turns] == [
            "uno",
            "Estás en la fase 2 del brain; todo verde.",
            "dos",
            "Estás en la fase 2 del brain; todo verde.",
        ]

    def test_failed_ask_persists_nothing(self, audio_dir):
        client = make_client(audio_dir, llm=FailingLLM())
        assert client.post("/ask", json={"text": "hola"}).status_code == 503
        assert client.get("/call-history").json()["turns"] == []

    def test_ask_reset_flag_clears_ring_but_not_history(self, audio_dir):
        client = make_client(audio_dir, llm=FakeLLM())
        client.post("/ask", json={"text": "uno"})
        client.post("/ask", json={"text": "dos", "reset": True})
        # The ring restarted mid-call, but the persisted transcript keeps
        # every turn — only POST /reset drops the call history.
        turns = client.get("/call-history").json()["turns"]
        assert [t["text"] for t in turns] == [
            "uno", "Estás en la fase 2 del brain; todo verde.",
            "dos", "Estás en la fase 2 del brain; todo verde.",
        ]


class TestResetClearsHistory:
    def test_reset_drops_the_persisted_transcript(self, audio_dir):
        client = make_client(audio_dir)
        client.post("/ask", json={"text": "uno"})
        client.post("/ask", json={"text": "dos"})
        resp = client.post("/reset", json={})
        assert resp.status_code == 200
        assert client.get("/call-history").json()["turns"] == []

    def test_reset_on_empty_history_is_a_no_op(self, audio_dir):
        client = make_client(audio_dir)
        assert client.post("/reset", json={}).status_code == 200
        assert client.get("/call-history").json()["turns"] == []


class TestBootSeed:
    def test_restart_restores_ring_from_history(self, audio_dir):
        first = make_client(audio_dir)
        for text in ("uno", "dos", "tres"):
            first.post("/ask", json={"text": text})
        # A "service restart": fresh app over the same state dir.
        llm = FakeLLM()
        second = make_client(audio_dir, llm=llm)
        second.post("/ask", json={"text": "cuatro"})
        seeded = llm.ring_snapshots[0]
        assert [(m.role, m.content) for m in seeded] == [
            ("user", "uno"),
            ("assistant", "Estás en la fase 2 del brain; todo verde."),
            ("user", "dos"),
            ("assistant", "Estás en la fase 2 del brain; todo verde."),
            ("user", "tres"),
            ("assistant", "Estás en la fase 2 del brain; todo verde."),
        ]

    def test_seed_is_capped_at_the_ring_size(self, audio_dir):
        store = HistoryStore(default_history_path(make_settings(audio_dir)))
        for i in range(30):
            store.append("user", f"t{i}")
        llm = FakeLLM()
        client = make_client(audio_dir, llm=llm)
        client.post("/ask", json={"text": "nueva"})
        seeded = llm.ring_snapshots[0]
        assert len(seeded) == 16
        assert seeded[0].content == "t14"
        assert seeded[-1].content == "t29"


class TestApprovalReplayDoesNotDoubleRecord:
    def test_approve_replay_adds_no_history_records(
        self, audio_dir, monkeypatch
    ):
        import herdr_brain.server as server_module
        from herdr_brain.herdr import AgentInfo

        tool_result = (
            '{"ok": true, "status": "done", "pane_id": "w7:p4", '
            '"delivered": true, "output_excerpt": "tests green"}'
        )

        class FakeReplayTools:
            def __init__(self, settings, herdr=None):
                self.dispatches: list = []

            def resolve_target(self, pane_id=None):
                return AgentInfo(
                    pane_id=pane_id or "w1:p9", agent="opencode", status="working",
                    session_kind="id", session_value="ses_x", cwd="/repo",
                    title="OpenCode", focused=True,
                )

            def dispatch(self, name, arguments, target=None):
                self.dispatches.append(name)
                return tool_result

        monkeypatch.setattr(server_module, "BrainTools", FakeReplayTools)
        llm = FakeLLM()
        app = create_app(
            settings=make_settings(audio_dir),
            llm_factory=lambda _cfg, _tools: llm,
            tts_renderer=FakeTTS(),
        )
        client = TestClient(app)

        client.post("/ask", json={"text": "corre los tests"})
        gate = app.state.approval_store.propose(
            "default", text="corre los tests", timeout_ms=300000,
            pane_id="w7:p4", agent="opencode",
        )
        resp = client.post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 200
        # Sanity: the replay DID run a second llm.ask (its report turn)…
        assert len(llm.calls) == 2
        assert "Report the outcome" in llm.calls[1]
        # …but the persisted call history still holds exactly the /ask
        # turn — no duplicated and no synthetic report records.
        turns = client.get("/call-history").json()["turns"]
        assert [t["text"] for t in turns] == [
            "corre los tests",
            "Estás en la fase 2 del brain; todo verde.",
        ]
