"""FR-13/G-12: shared call context and desktop/mobile gate handover.

The shared-context seam is one narrow pair of endpoints plus the
existing stores: ``GET /call/context`` hands every client the ONE call
identity (the default session — same namespace /call-history and the
boot ring seed already use), and ``POST /call/context`` publishes the
selected pane (last writer wins, revision is monotonic). Everything
else here proves the handover S14 rule against the REAL app: a gate
recovered by a second client carries no playback authority, so a
spoken «adelante» is clarified (re-explained) and only the restate's
own receipt can confirm; rejects leave actions and history untouched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.history import default_history_path
from herdr_brain.memory import DEFAULT_SESSION
from herdr_brain.server import create_app
from tests.conftest import SETTINGS_KWARGS


class FakeTTS:
    def __call__(self, settings, text, out_path):
        out_path.write_bytes(b"ID3-fake")
        return out_path


class FakeGatedLLM:
    """Opens a real gate in the attached store when asked the trigger."""

    def __init__(self, trigger="gated", answer="He preparado el resumen del panel."):
        self.trigger = trigger
        self.answer = answer
        self.calls: list[str] = []
        self.approval_store = None

    def attach_store(self, store):
        pass

    def attach_approval_store(self, store):
        self.approval_store = store

    def ask(self, text, session_id=None, pane_id=None, **kwargs):
        self.calls.append(text)
        approval = None
        if self.trigger in text and self.approval_store is not None:
            approval = self.approval_store.propose(
                session_id or DEFAULT_SESSION,
                text="envía el resumen al agente",
                timeout_ms=300_000,
                pane_id="w1:p9",
                agent="opencode",
            )
        return {
            "answer": self.answer,
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": session_id or DEFAULT_SESSION,
            "approval": approval,
        }


class FakeReplayTools:
    def __init__(self, settings, herdr=None):
        self.dispatches: list[str] = []

    def resolve_target(self, pane_id=None):
        from herdr_brain.herdr import AgentInfo

        return AgentInfo(
            pane_id=pane_id or "w1:p9", agent="opencode", status="working",
            session_kind="id", session_value="ses_x", cwd="/repo",
            title="OpenCode", focused=True,
        )

    def dispatch(self, name, arguments, target=None):
        self.dispatches.append(name)
        return '{"ok": true, "status": "done", "pane_id": "w1:p9", "delivered": true}'


def make_settings(audio_dir) -> Settings:
    return Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})


@pytest.fixture
def audio_dir(tmp_path):
    return tmp_path / "audio"


def make_client(audio_dir, monkeypatch, llm=None):
    import herdr_brain.server as server_module

    monkeypatch.setattr(server_module, "BrainTools", FakeReplayTools)
    app = create_app(
        settings=make_settings(audio_dir),
        llm_factory=lambda _cfg, _tools: llm or FakeGatedLLM(),
        tts_renderer=FakeTTS(),
    )
    return TestClient(app)


def shared_call_id(client: TestClient) -> str:
    body = client.get("/call/context").json()
    return body["call_id"]


def gated_ask(client: TestClient, speech_id: str, text="gated question") -> dict:
    return client.post("/ask", json={
        "text": text,
        "session_id": shared_call_id(client),
        "speech_request_id": speech_id,
        "speech_cancel_token": "token-" + speech_id,
    })


class TestCallContextEndpoint:
    def test_default_context_is_the_shared_default_call(self, audio_dir, monkeypatch):
        client = make_client(audio_dir, monkeypatch)
        resp = client.get("/call/context")
        assert resp.status_code == 200
        assert resp.json() == {
            "call_id": DEFAULT_SESSION, "pane_id": None, "revision": 0,
            "locale": "es",
        }

    def test_select_pane_publishes_and_bumps_revision(self, audio_dir, monkeypatch):
        client = make_client(audio_dir, monkeypatch)
        first = client.post("/call/context", json={"pane_id": "w1:p9"})
        assert first.status_code == 200
        assert first.json() == {
            "call_id": DEFAULT_SESSION, "pane_id": "w1:p9", "revision": 1,
            "locale": "es",
        }
        # A second client reads the same selection; its own later write
        # wins (last writer) and the revision only ever grows.
        assert client.get("/call/context").json()["pane_id"] == "w1:p9"
        second = client.post("/call/context", json={"pane_id": "w2:p1"})
        assert second.json()["revision"] == 2
        assert second.json()["pane_id"] == "w2:p1"
        assert client.get("/call/context").json()["revision"] == 2

    def test_select_validates_and_clears_pane_ids(self, audio_dir, monkeypatch):
        client = make_client(audio_dir, monkeypatch)
        client.post("/call/context", json={"pane_id": "w1:p9"})
        assert client.post("/call/context", json={"pane_id": 7}).status_code == 422
        assert client.post("/call/context", json={"pane_id": "x" * 200}).status_code == 422
        assert client.post("/call/context", json={"pane_id": "w1:p9\nrm"}).status_code == 422
        # An explicit empty selection clears the shared pane.
        cleared = client.post("/call/context", json={"pane_id": "   "})
        assert cleared.json() == {
            "call_id": DEFAULT_SESSION, "pane_id": None, "revision": 2,
            "locale": "es",
        }

    def test_shared_call_id_is_the_default_session_namespace(self, audio_dir, monkeypatch):
        client = make_client(audio_dir, monkeypatch)
        assert shared_call_id(client) == DEFAULT_SESSION
        # A client that omits its session id lands in the SAME namespace
        # the shared call id names — one call, one gate namespace.
        assert client.get("/approval/current").json() == {"approval": None}


class TestRegistryFullProtectedTurn:
    """VS1.1 admission parity inside the S13/B2 guard: a decision turn
    that cannot register a speech job (registry full) keeps the exact
    ordinary text-only degraded contract — no render, no rebinding, no
    supersede — while the gate stays protected."""

    def test_registry_full_decision_turn_is_text_only_degraded(
        self, audio_dir, monkeypatch
    ):
        import herdr_brain.server as server_module
        from fastapi.testclient import TestClient

        monkeypatch.setattr(server_module, "BrainTools", FakeReplayTools)
        renders: list[str] = []

        def counting_tts(settings, text, out_path):
            renders.append(text)
            out_path.write_bytes(b"ID3-fake")
            return out_path

        llm = FakeGatedLLM()
        app = server_module.create_app(
            settings=make_settings(audio_dir),
            llm_factory=lambda _cfg, _tools: llm,
            tts_renderer=counting_tts,
        )
        client = TestClient(app)
        assert gated_ask(client, "spk-a0001").status_code == 200
        gate = client.app.state.approval_store.current("default")
        registry = client.app.state.speech_jobs
        for index in range(registry._max_jobs):
            registry.register(f"busy-{index:04d}", "busy-token", "default")
        renders.clear()
        resp = client.post("/ask", json={
            "text": "adelante", "session_id": "default", "conversation": True,
            "speech_request_id": "spk-b0001", "speech_cancel_token": "token-b0001",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["speech"] == {"status": "degraded", "reason": "registry-full"}
        assert body["audio_url"] is None
        assert renders == []  # no uncancellable unadmitted render
        assert "Antes de confirmar" in body["answer"]  # text restate still honest
        # Gate protected and untouched: same gate, still proposed, and
        # the ORIGINAL explanation binding survives — degraded turns
        # grant no new authority.
        after = client.app.state.approval_store.current("default")
        assert after.gate_id == gate.gate_id and after.state == "proposed"
        assert after.explained_speech_id == gate.explained_speech_id
        assert len(llm.calls) == 1
        assert client.app.state.speech_jobs.get("spk-b0001") is None


class TestAskVoiceDecisionGuard:
    """S13/B2 SERVER enforcement (not client suppression): a voice
    decision racing /ask after another client opened a gate re-explains
    that gate instead of superseding it; deliberate new work keeps the
    exact legacy supersede."""

    def test_voice_decision_ask_reexplains_and_never_supersedes(
        self, audio_dir, monkeypatch
    ):
        client, llm = self._gated(audio_dir, monkeypatch)
        assert gated_ask(client, "spk-a0001").status_code == 200
        gate_id = client.get("/approval/current", params={"session_id": "default"}).json()[
            "approval"
        ]["gate_id"]
        # The stale-context client speaks «adelante» into /ask.
        resp = client.post("/ask", json={
            "text": "adelante", "session_id": "default",
            "speech_request_id": "spk-b0001", "speech_cancel_token": "token-b0001",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "Antes de confirmar" in body["answer"]
        assert body["approval"]["gate_id"] == gate_id  # SAME gate, still live
        assert body["speech"]["status"] == "complete"
        gate = client.app.state.approval_store.current("default")
        assert gate is not None and gate.state == "proposed"
        assert gate.explained_speech_id == "spk-b0001"
        # The model never ran a turn for the decision token, nothing
        # executed, and the restate entered no call-history records.
        assert len(llm.calls) == 1
        turns = client.get("/call-history").json()["turns"]
        assert [t["role"] for t in turns] == ["user", "assistant"]
        # The restate's OWN receipt confirms — exactly once (S14).
        confirm = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "sí", "speech_request_id": "spk-b0001",
        })
        assert confirm.status_code == 200 and confirm.json()["decision"] == "approve"
        assert client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "sí", "speech_request_id": "spk-b0001",
        }).status_code == 404

    def test_deliberate_new_work_ask_still_supersedes(self, audio_dir, monkeypatch):
        client, llm = self._gated(audio_dir, monkeypatch)
        assert gated_ask(client, "spk-a0001").status_code == 200
        resp = client.post("/ask", json={"text": "cómo va la fase dos", "session_id": "default"})
        assert resp.status_code == 200
        assert resp.json()["approval"] is None
        assert client.get("/approval/current", params={"session_id": "default"}).json() == {
            "approval": None
        }
        assert len(llm.calls) == 2  # the question ran a real model turn

    def test_decision_token_without_live_gate_is_an_ordinary_turn(
        self, audio_dir, monkeypatch
    ):
        client, llm = self._gated(audio_dir, monkeypatch)
        resp = client.post("/ask", json={"text": "adelante", "session_id": "default"})
        assert resp.status_code == 200
        assert resp.json()["approval"] is None
        assert len(llm.calls) == 1

    def test_guard_is_atomic_against_interleaved_asks(self, audio_dir, monkeypatch):
        """begin_turn decides under the store lock: a decision turn
        either finds the live gate (protected, returned) or a legacy
        new-work turn has already atomically superseded it (None) — and
        the legacy path itself returns None, never a retired gate."""
        client, llm = self._gated(audio_dir, monkeypatch)
        assert gated_ask(client, "spk-a0001").status_code == 200
        store = client.app.state.approval_store
        guard_won = store.begin_turn("default", True)
        assert guard_won is not None and guard_won.state == "proposed"
        assert store.begin_turn("default", False) is None
        assert store.current("default") is None
        # With nothing live, neither flavor reports a protected gate.
        assert store.begin_turn("default", True) is None

    def _gated(self, audio_dir, monkeypatch):
        llm = FakeGatedLLM()
        return make_client(audio_dir, monkeypatch, llm=llm), llm


class TestSharedGateHandover:
    def _gated_call(self, audio_dir, monkeypatch, llm=None):
        llm = llm or FakeGatedLLM()
        client = make_client(audio_dir, monkeypatch, llm=llm)
        return client, llm

    def test_second_client_recovers_the_gate_under_the_shared_session(
        self, audio_dir, monkeypatch
    ):
        client, _ = self._gated_call(audio_dir, monkeypatch)
        session = shared_call_id(client)
        assert gated_ask(client, "spk-a0001").status_code == 200
        # The other device boots: same shared id, gate visible.
        recovered = client.get("/approval/current", params={"session_id": session})
        approval = recovered.json()["approval"]
        assert approval is not None and approval["gate_id"]
        # The recovered payload carries NO playback authority: the
        # explanation speech identity never leaves the owning client.
        assert "speech" not in approval
        assert "explained" not in approval
        assert not any("speech" in k or "receipt" in k for k in approval)

    def test_recovered_client_is_clarified_not_approved(self, audio_dir, monkeypatch):
        client, llm = self._gated_call(audio_dir, monkeypatch)
        session = shared_call_id(client)
        gated_ask(client, "spk-a0001")
        gate_id = client.get("/approval/current", params={"session_id": session}).json()[
            "approval"
        ]["gate_id"]
        # Second client says «adelante» with NO receipt (it never played
        # the explanation): the server re-explains instead of executing.
        outcome = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "adelante", "speech_request_id": None,
        })
        assert outcome.status_code == 200
        body = outcome.json()
        assert body["decision"] == "clarify"
        assert "Antes de confirmar" in body["answer"]
        assert body["explain_speech_id"]
        # Nothing executed and the gate is still live for a real answer.
        assert client.app.state.approval_store.current(session).state == "proposed"

    def test_clarify_receipt_confirms_and_executes_exactly_once(
        self, audio_dir, monkeypatch
    ):
        client, llm = self._gated_call(audio_dir, monkeypatch)
        session = shared_call_id(client)
        gated_ask(client, "spk-a0001")
        gate_id = client.get("/approval/current", params={"session_id": session}).json()[
            "approval"
        ]["gate_id"]
        clarify = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "adelante", "speech_request_id": None,
        }).json()
        # The second client played the restate (its queue drained), so it
        # presents the restate's own receipt.
        confirm = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "sí", "speech_request_id": clarify["explain_speech_id"],
        })
        assert confirm.status_code == 200
        assert confirm.json()["decision"] == "approve"
        replay = client.app.state.approval_store
        assert replay.current(session) is None
        # Approve-once: every later touch is a 404, never a re-execution.
        again = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "sí", "speech_request_id": clarify["explain_speech_id"],
        })
        assert again.status_code == 404

    def test_revised_gate_needs_a_fresh_explanation(self, audio_dir, monkeypatch):
        client, llm = self._gated_call(audio_dir, monkeypatch)
        session = shared_call_id(client)
        gated_ask(client, "spk-a0001")
        gate_id = client.get("/approval/current", params={"session_id": session}).json()[
            "approval"
        ]["gate_id"]
        # A handover edit revises the frozen action: the ORIGINAL owner's
        # drained explanation is now stale authority.
        patched = client.patch(f"/approval/{gate_id}", json={"text": "envía solo el diff"})
        assert patched.status_code == 200
        stale = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "adelante", "speech_request_id": "spk-a0001",
        })
        body = stale.json()
        assert body["decision"] == "clarify"
        assert "envía solo el diff" in body["answer"]
        assert client.app.state.approval_store.current(session).state == "proposed"

    def test_rejected_handover_leaves_actions_and_history_untouched(
        self, audio_dir, monkeypatch
    ):
        client, llm = self._gated_call(audio_dir, monkeypatch)
        session = shared_call_id(client)
        gated_ask(client, "spk-a0001")
        gate_id = client.get("/approval/current", params={"session_id": session}).json()[
            "approval"
        ]["gate_id"]
        reject = client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "no", "speech_request_id": None,
        })
        assert reject.status_code == 200
        assert reject.json()["decision"] == "reject"
        # No execution, no pending gate, transcript keeps exactly the
        # /ask turn pair, and the dead gate is untouchable.
        assert client.get("/approval/current", params={"session_id": session}).json() == {
            "approval": None
        }
        turns = client.get("/call-history").json()["turns"]
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert client.post(f"/approval/{gate_id}/resolve", json={
            "utterance": "adelante", "speech_request_id": None,
        }).status_code == 404
