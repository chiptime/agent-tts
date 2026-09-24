"""HTTP surface tests: LLM and TTS backends are faked, nothing real runs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.approval import ApprovalGate, ApprovalGateStore
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.server import APPROVAL_CLOSER, REPROMPT_LINE, approval_payload, create_app
from tests.conftest import SETTINGS_KWARGS


class FakeLLM:
    def __init__(self, result=None):
        self.calls: list = []
        self.session_ids: list = []
        self.pane_ids: list = []
        self.result = result or {
            "answer": "Estás en la fase 2 del brain; todo verde.",
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": "default",
        }

    def attach_store(self, store):
        self.attached_store = store

    def attach_approval_store(self, store):
        self.attached_approval_store = store

    def ask(self, text, session_id=None, pane_id=None):
        self.calls.append(text)
        self.session_ids.append(session_id)
        self.pane_ids.append(pane_id)
        return dict(
            self.result,
            session_id=session_id or "default",
            pane_id=pane_id or self.result["pane_id"],
        )


class FakeTTS:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list = []

    def __call__(self, settings, text, out_path: Path) -> Path:
        if self.fail:
            raise RuntimeError("engine boom")
        self.calls.append({"text": text, "out": out_path})
        out_path.write_bytes(b"ID3-fake")
        return out_path


@pytest.fixture
def audio_dir(tmp_path) -> Path:
    return tmp_path / "audio"


@pytest.fixture
def client_factory(settings, audio_dir):
    def _make(llm=None, tts=None):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        app = create_app(
            settings=cfg,
            llm_factory=lambda _cfg, _tools: llm or FakeLLM(),
            tts_renderer=tts or FakeTTS(),
        )
        return TestClient(app)

    return _make


class TestHealth:
    def test_health_ok(self, client_factory):
        resp = client_factory().get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["version"]


class TestTtsBackendContract:
    """The herdr-tts backend contract: /health field + fail-soft boot."""

    def test_health_reports_tts_ok_with_stub_backend(self, client_factory):
        # client_factory cannot inject the daemon probe; build directly so
        # the 'ok' expectation is deterministic (probe stubbed UP).
        cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": "/tmp/herdr-brain-test-audio"})
        client = TestClient(create_app(settings=cfg, daemon_probe=lambda: "up"))
        assert client.get("/health").json()["tts"] == "ok"

    def test_health_degraded_when_daemon_down(self):
        cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": "/tmp/herdr-brain-test-audio"})
        client = TestClient(create_app(settings=cfg, daemon_probe=lambda: "down"))
        assert client.get("/health").json()["tts"] == "degraded"

    def test_health_missing_contract_dominates_daemon_state(self):
        cfg = Settings(**{
            **SETTINGS_KWARGS,
            "audio_dir": "/tmp/herdr-brain-test-audio",
            "tts_home": "/nowhere/herdr-tts",
            "tts_bin": "/nowhere/herdr-tts/bin/herdr-tts",
        })
        client = TestClient(create_app(settings=cfg, daemon_probe=lambda: "up"))
        assert client.get("/health").json()["tts"] == "missing"

    def test_health_reports_tts_missing_and_server_survives(self, settings, audio_dir, monkeypatch, caplog):
        """Fail-soft: a missing surface contract must never crash the server;
        text answers keep working, only speech is degraded."""
        import logging

        from herdr_brain.tts import TTS_BACKEND_MISSING, TTS_BACKEND_NAME

        cfg = Settings(**{
            **settings.__dict__,
            "audio_dir": str(audio_dir),
            "tts_home": audio_dir / "nowhere-tts",
            "tts_bin": audio_dir / "nowhere-tts/bin/herdr-tts",
        })
        with caplog.at_level(logging.WARNING, logger="herdr_brain.server"):
            client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["tts"] == TTS_BACKEND_MISSING
        assert any(TTS_BACKEND_NAME in r.message for r in caplog.records)

        # Text answers still work; audio degrades to null.
        resp = client.post("/ask", json={"text": "hola"})
        assert resp.status_code == 200
        assert resp.json()["answer"]
        assert resp.json()["audio_url"] is None

    def test_ask_degradation_log_names_backend_contract(self, settings, audio_dir, caplog):
        """Render failure with a missing surface logs the contract, not a
        bare traceback."""
        import logging

        cfg = Settings(**{
            **settings.__dict__,
            "audio_dir": str(audio_dir),
            "tts_home": audio_dir / "nowhere-tts",
            "tts_bin": audio_dir / "nowhere-tts/bin/herdr-tts",
        })

        with caplog.at_level(logging.WARNING, logger="herdr_brain.server"):
            client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
            resp = client.post("/ask", json={"text": "hola"})
        assert resp.status_code == 200
        assert resp.json()["audio_url"] is None
        assert any("herdr-tts" in r.message and "text-only" in r.message for r in caplog.records)


class TestState:
    def test_state_active(self, settings, audio_dir, active_agent, monkeypatch):
        import herdr_brain.server as server_module

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = active_agent

            def status_payload(self):
                from herdr_brain.tools import status_payload

                self.last_active = active_agent
                return status_payload(active_agent)

        monkeypatch.setattr(server_module, "BrainTools", FakeTools)
        client = TestClient(server_module.create_app(settings=cfg))
        resp = client.get("/state")
        assert resp.status_code == 200
        assert resp.json() == {
            "active": True,
            "pane_id": active_agent.pane_id,
            "agent": active_agent.agent,
            "agent_status": active_agent.status,
            "title": active_agent.title,
            "cwd": active_agent.cwd,
            "session_id": active_agent.session_value,
        }

    def test_state_no_active_pane(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def status_payload(self):
                from herdr_brain.tools import status_payload

                return status_payload(None)

        monkeypatch.setattr(server_module, "BrainTools", FakeTools)
        client = TestClient(server_module.create_app(settings=cfg))
        resp = client.get("/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False
        assert body["pane_id"] is None

    def test_health_and_tts_work_without_llm_key(self, settings, audio_dir, monkeypatch):
        """No GLM_API_KEY: service still boots; only /ask is unavailable."""
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir), "glm_api_key": None})
        client = TestClient(create_app(settings=cfg, tts_renderer=FakeTTS()))
        assert client.get("/health").status_code == 200
        assert client.post("/tts", json={"text": "hola"}).status_code == 200
        resp = client.post("/ask", json={"text": "hola"})
        assert resp.status_code == 503
        assert "GLM_API_KEY" in resp.json()["detail"]


class TestAsk:
    def test_ask_returns_answer_and_audio(self, client_factory, audio_dir):
        llm = FakeLLM()
        tts = FakeTTS()
        resp = client_factory(llm=llm, tts=tts).post("/ask", json={"text": "en que estas?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"].startswith("Estás")
        assert body["pane_id"] == "w1:p9"
        assert body["agent"] == "opencode"
        assert body["audio_url"].startswith("/audio/")
        assert llm.calls == ["en que estas?"]
        rendered = audio_dir / Path(body["audio_url"]).name
        assert rendered.is_file()

    def test_tts_failure_still_returns_answer(self, client_factory):
        resp = client_factory(tts=FakeTTS(fail=True)).post("/ask", json={"text": "hola"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]
        assert body["audio_url"] is None

    def test_empty_text_rejected(self, client_factory):
        resp = client_factory().post("/ask", json={"text": ""})
        assert resp.status_code == 422


def make_gate(
    session_id: str = "default",
    text: str = "corre los tests",
    timeout_ms: int | None = 300000,
    pane_id: str | None = "w1:p9",
    agent: str | None = "opencode",
) -> ApprovalGate:
    """A frozen gate snapshot built through the real store API."""
    store = ApprovalGateStore(timeout_s=60)
    return store.propose(
        session_id=session_id,
        text=text,
        timeout_ms=timeout_ms,
        pane_id=pane_id,
        agent=agent,
    )


class TestApprovalGate:
    """/ask gains approval{} + the deterministic closer when a gate opens."""

    def test_gated_ask_returns_approval_field_and_closer(self, client_factory):
        gate = make_gate(text="arregla el bug del login", timeout_ms=300000)
        llm = FakeLLM(
            result={
                "answer": "Voy a enviar a opencode: arregla el bug del login",
                "pane_id": "w1:p9",
                "agent": "opencode",
                "session_id": "default",
                "approval": gate,
            }
        )
        tts = FakeTTS()
        resp = client_factory(llm=llm, tts=tts).post(
            "/ask", json={"text": "dile que arregle el login"}
        )
        assert resp.status_code == 200
        body = resp.json()
        # Deterministic closer, appended server-side after the model echo.
        assert body["answer"].startswith("Voy a enviar a opencode")
        assert body["answer"].endswith(APPROVAL_CLOSER)
        approval = body["approval"]
        assert approval["gate_id"] == gate.gate_id
        assert approval["tool"] == "send_to_session"
        assert approval["pane_id"] == "w1:p9"
        assert approval["agent"] == "opencode"
        assert approval["text"] == "arregla el bug del login"  # FULL text
        assert approval["timeout_ms"] == 300000
        assert isinstance(approval["expires_in_s"], int)
        assert 0 < approval["expires_in_s"] <= 60
        # The closer is spoken too: TTS rendered the gated answer in full.
        assert tts.calls[-1]["text"] == body["answer"]

    def test_ungated_ask_has_null_approval_and_no_closer(self, client_factory):
        resp = client_factory().post("/ask", json={"text": "en que estas?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["approval"] is None
        assert APPROVAL_CLOSER not in body["answer"]

    def test_new_ask_supersedes_the_live_gate(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        app = create_app(
            settings=cfg, llm_factory=lambda c, t: FakeLLM(), tts_renderer=FakeTTS()
        )
        gate_store = app.state.approval_store
        old_gate = gate_store.propose(
            "s1", text="old send", pane_id="w1:p9", agent="opencode"
        )
        resp = TestClient(app).post("/ask", json={"text": "otra cosa", "session_id": "s1"})
        assert resp.status_code == 200
        assert resp.json()["approval"] is None  # question-only turn, no new gate
        assert gate_store.current("s1") is None
        assert gate_store.get(old_gate.gate_id).state == "superseded"

    def test_approval_payload_counts_down_lazily(self):
        # Lazy expiry model: remaining seconds from created_at, no timers.
        store = ApprovalGateStore(timeout_s=60, clock=lambda: 1_000.0)
        gate = store.propose("s1", text="x", pane_id="p", agent="a")
        assert approval_payload(gate, 60, now=1000.0)["expires_in_s"] == 60
        assert approval_payload(gate, 60, now=1010.5)["expires_in_s"] == 50
        assert approval_payload(gate, 60, now=1065.0)["expires_in_s"] == 0

    def test_create_app_exposes_and_attaches_approval_store(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        llm = FakeLLM()
        app = create_app(
            settings=cfg, llm_factory=lambda c, t: llm, tts_renderer=FakeTTS()
        )
        assert isinstance(app.state.approval_store, ApprovalGateStore)
        TestClient(app).post("/ask", json={"text": "hola"})  # builds the LLM
        # Same instance the approval endpoints will resolve against.
        assert llm.attached_approval_store is app.state.approval_store


def make_replay_tools(
    tool_result: str = '{"ok": true, "status": "done", "output_excerpt": "tests green"}',
):
    """BrainTools double recording the approve replay path (resolve_target
    + dispatch), so tests assert the EXACT frozen args were sent."""
    created: list = []

    class _FakeReplayTools:
        def __init__(self, cfg):
            self.last_active = None
            self.dispatches: list = []
            self.resolved_panes: list = []
            created.append(self)

        def resolve_target(self, pane_id=None):
            self.resolved_panes.append(pane_id)
            return AgentInfo(
                pane_id=pane_id or "w1:p9", agent="opencode", status="working",
                session_kind="id", session_value="ses_x", cwd="/repo",
                title="OpenCode", focused=True,
            )

        def dispatch(self, name, arguments, target=None):
            self.dispatches.append(
                {"name": name, "arguments": dict(arguments), "target": target}
            )
            return tool_result

    _FakeReplayTools.created = created
    return _FakeReplayTools


def approval_app(
    settings, audio_dir, monkeypatch, llm=None, tools_cls=None, tts=None,
    approval_timeout_s=None,
):
    """create_app wired for approval-endpoint tests: fakes everywhere, an
    optional BrainTools monkeypatch (replay recording) and timeout override."""
    import herdr_brain.server as server_module

    overrides = {"audio_dir": str(audio_dir)}
    if approval_timeout_s is not None:
        overrides["approval_timeout_s"] = approval_timeout_s
    cfg = Settings(**{**settings.__dict__, **overrides})
    if tools_cls is not None:
        monkeypatch.setattr(server_module, "BrainTools", tools_cls)
    return server_module.create_app(
        settings=cfg,
        llm_factory=lambda c, t: llm or FakeLLM(),
        tts_renderer=tts or FakeTTS(),
    )


class TestApproveEndpoint:
    """/approval/{id}/approve: exact-args replay + approve-once."""

    def test_approve_replays_exact_frozen_args(self, settings, audio_dir, monkeypatch):
        tools_cls = make_replay_tools()
        llm = FakeLLM(result={
            "answer": "Listo: el agente terminó en verde.",
            "pane_id": "w7:p4", "agent": "opencode", "session_id": "s1",
        })
        tts = FakeTTS()
        app = approval_app(
            settings, audio_dir, monkeypatch, llm=llm, tools_cls=tools_cls, tts=tts
        )
        gate = app.state.approval_store.propose(
            "s1", text="corre los tests", timeout_ms=300000,
            pane_id="w7:p4", agent="opencode",
        )
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        # Same response conventions as /ask.
        assert set(body) == {"answer", "pane_id", "agent", "session_id", "audio_url", "approval"}
        assert body["answer"] == "Listo: el agente terminó en verde."
        assert body["audio_url"].startswith("/audio/")
        assert body["approval"] is None
        assert tts.calls[-1]["text"] == body["answer"]
        # THE replay: exact frozen args through the normal dispatch path.
        tools = tools_cls.created[0]
        assert tools.resolved_panes == ["w7:p4"]  # frozen pane, re-resolved
        assert len(tools.dispatches) == 1
        sent = tools.dispatches[0]
        assert sent["name"] == "send_to_session"
        assert sent["arguments"] == {"text": "corre los tests", "timeout_ms": 300000}
        assert sent["target"].pane_id == "w7:p4"
        # The model reported on the replay result, in the gate's session/pane.
        assert len(llm.calls) == 1
        assert "tests green" in llm.calls[0]
        assert llm.session_ids == ["s1"]
        assert llm.pane_ids == ["w7:p4"]

    def test_approve_executes_exactly_once(self, settings, audio_dir, monkeypatch):
        tools_cls = make_replay_tools()
        app = approval_app(settings, audio_dir, monkeypatch, tools_cls=tools_cls)
        gate = app.state.approval_store.propose(
            "s1", text="solo una vez", timeout_ms=None, pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        assert client.post(f"/approval/{gate.gate_id}/approve").status_code == 200
        assert client.post(f"/approval/{gate.gate_id}/approve").status_code == 404
        # AC6: the frozen call executed exactly once (None timeout replayed
        # as-is too — "as the model passed it").
        tools = tools_cls.created[0]
        assert tools.dispatches[0]["arguments"] == {"text": "solo una vez", "timeout_ms": None}
        assert len(tools.dispatches) == 1
        assert app.state.approval_store.get(gate.gate_id).state == "approved"

    def test_approve_on_expired_gate_404_lazy_expiry(self, settings, audio_dir, monkeypatch):
        tools_cls = make_replay_tools()
        app = approval_app(
            settings, audio_dir, monkeypatch, tools_cls=tools_cls, approval_timeout_s=0
        )
        gate = app.state.approval_store.propose(
            "s1", text="tarde", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        time.sleep(0.01)  # the 0 s window is definitively elapsed
        resp = TestClient(app).post(f"/approval/{gate.gate_id}/approve")
        assert resp.status_code == 404
        assert app.state.approval_store.get(gate.gate_id).state == "expired"
        assert tools_cls.created[0].dispatches == []  # nothing executed

    def test_unknown_gate_id_is_404_on_every_approval_endpoint(self, client_factory):
        client = client_factory()
        assert client.post("/approval/doesnotexist/approve").status_code == 404
        assert client.post("/approval/doesnotexist/reject").status_code == 404
        assert client.post("/approval/doesnotexist/resolve", json={"utterance": "sí"}).status_code == 404
        assert client.patch("/approval/doesnotexist", json={"text": "x"}).status_code == 404

    def test_superseded_gate_is_404_through_the_endpoints(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        app = create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM(), tts_renderer=FakeTTS())
        gate = app.state.approval_store.propose(
            "s1", text="old send", pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        assert client.post("/ask", json={"text": "otra cosa", "session_id": "s1"}).status_code == 200
        # The /ask supersede must hold through the endpoint surface too.
        assert client.post(f"/approval/{gate.gate_id}/approve").status_code == 404
        assert client.get("/approval/current", params={"session_id": "s1"}).json()["approval"] is None


class TestRejectEndpoint:
    """/approval/{id}/reject: silent confirmation, terminal state."""

    def test_reject_is_silent_and_terminal(self, settings, audio_dir, monkeypatch):
        tts = FakeTTS()
        app = approval_app(settings, audio_dir, monkeypatch, tts=tts)
        gate = app.state.approval_store.propose(
            "s1", text="no mandar", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        resp = client.post(f"/approval/{gate.gate_id}/reject")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "state": "rejected"}
        assert tts.calls == []  # no TTS generated server-side (PRD §5)
        assert app.state.approval_store.get(gate.gate_id).state == "rejected"
        # Terminal is final: every further op on the gate 404s.
        assert client.post(f"/approval/{gate.gate_id}/approve").status_code == 404
        assert client.post(f"/approval/{gate.gate_id}/reject").status_code == 404
        assert client.post(f"/approval/{gate.gate_id}/resolve", json={"utterance": "sí"}).status_code == 404
        assert client.patch(f"/approval/{gate.gate_id}", json={"text": "x"}).status_code == 404
        assert client.get("/approval/current", params={"session_id": "s1"}).json()["approval"] is None


class TestResolveEndpoint:
    """/approval/{id}/resolve: voice lexicon + reprompt budget."""

    def test_approve_utterance_replays_and_reports(self, settings, audio_dir, monkeypatch):
        tools_cls = make_replay_tools()
        llm = FakeLLM(result={
            "answer": "Enviado y terminó bien.",
            "pane_id": "w1:p9", "agent": "opencode", "session_id": "s1",
        })
        app = approval_app(settings, audio_dir, monkeypatch, llm=llm, tools_cls=tools_cls)
        gate = app.state.approval_store.propose(
            "s1", text="arregla el login", timeout_ms=120000, pane_id="w1:p9", agent="opencode"
        )
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/resolve", json={"utterance": "sí, envíalo"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "approve"
        assert body["answer"] == "Enviado y terminó bien."
        assert body["audio_url"].startswith("/audio/")
        assert body["approval"] is None
        sent = tools_cls.created[0].dispatches[0]
        assert sent["arguments"] == {"text": "arregla el login", "timeout_ms": 120000}
        assert app.state.approval_store.get(gate.gate_id).state == "approved"

    def test_reject_utterance_is_silent(self, settings, audio_dir, monkeypatch):
        tts = FakeTTS()
        app = approval_app(settings, audio_dir, monkeypatch, tts=tts)
        gate = app.state.approval_store.propose(
            "s1", text="para ya", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/resolve", json={"utterance": "no lo envíes"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"decision": "reject", "answer": None, "audio_url": None, "approval": None}
        assert tts.calls == []
        assert app.state.approval_store.get(gate.gate_id).state == "rejected"

    def test_unknown_reprompts_once_then_auto_rejects(self, settings, audio_dir, monkeypatch):
        tts = FakeTTS()
        app = approval_app(settings, audio_dir, monkeypatch, tts=tts)
        gate = app.state.approval_store.propose(
            "s1", text="algo", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        first = client.post(f"/approval/{gate.gate_id}/resolve", json={"utterance": "hola qué tal"})
        assert first.status_code == 200
        body = first.json()
        assert body["decision"] == "reprompt"
        assert body["answer"] == REPROMPT_LINE
        assert body["audio_url"].startswith("/audio/")
        assert tts.calls[-1]["text"] == REPROMPT_LINE
        # Card stays: the gate is live with the ambiguity budget spent.
        assert app.state.approval_store.get(gate.gate_id).reprompt_count == 1
        assert app.state.approval_store.current("s1") is not None
        # A second consecutive ambiguous utterance auto-rejects (PRD §4).
        second = client.post(
            f"/approval/{gate.gate_id}/resolve", json={"utterance": "quizás la semana que viene"}
        )
        assert second.status_code == 200
        assert second.json()["decision"] == "reject"
        assert app.state.approval_store.get(gate.gate_id).state == "rejected"
        assert app.state.approval_store.current("s1") is None
        assert len(tts.calls) == 1  # only the reprompt line was ever spoken

    def test_replace_intent_waits_for_the_client_patch(self, settings, audio_dir, monkeypatch):
        tts = FakeTTS()
        app = approval_app(settings, audio_dir, monkeypatch, tts=tts)
        gate = app.state.approval_store.propose(
            "s1", text="texto original", timeout_ms=60000, pane_id="w1:p9", agent="opencode"
        )
        resp = TestClient(app).post(
            f"/approval/{gate.gate_id}/resolve", json={"utterance": "cambia el texto"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"decision": "listen_replace", "answer": None, "audio_url": None, "approval": None}
        # The gate stays live and untouched: the replacement text is NOT in
        # this utterance — the client's ONE dictation round comes back
        # through PATCH, which restarts the timer (PRD §5).
        stored = app.state.approval_store.get(gate.gate_id)
        assert stored.state == "proposed"
        assert stored.action.text == "texto original"
        assert stored.reprompt_count == 0  # clear intent: no ambiguity budget
        assert tts.calls == []


class TestApprovalPatchEndpoint:
    """PATCH /approval/{id}: manual text edit + timer restart."""

    def test_patch_unknown_and_terminal_gates_404(self, settings, audio_dir, monkeypatch):
        app = approval_app(settings, audio_dir, monkeypatch)
        gate = app.state.approval_store.propose(
            "s1", text="x", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        assert client.patch("/approval/unknowngate", json={"text": "y"}).status_code == 404
        client.post(f"/approval/{gate.gate_id}/reject")
        assert client.patch(f"/approval/{gate.gate_id}", json={"text": "y"}).status_code == 404

    def test_patch_updates_text_and_restarts_timer(self, settings, audio_dir, monkeypatch):
        app = approval_app(settings, audio_dir, monkeypatch, approval_timeout_s=3)
        gate = app.state.approval_store.propose(
            "s1", text="original", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        time.sleep(1.1)  # burn past a second boundary of the 3 s window
        before = client.get("/approval/current", params={"session_id": "s1"}).json()["approval"]
        assert before["expires_in_s"] == 2  # 3 s window with ~1.1 s burned
        resp = client.patch(f"/approval/{gate.gate_id}", json={"text": "texto editado"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["approval"]["gate_id"] == gate.gate_id
        assert body["approval"]["text"] == "texto editado"
        assert body["approval"]["expires_in_s"] == 3  # timer restarted
        after = client.get("/approval/current", params={"session_id": "s1"}).json()["approval"]
        assert after["text"] == "texto editado"
        assert after["expires_in_s"] >= 2


class TestApprovalCurrentEndpoint:
    """GET /approval/current: reload recovery payload (T7 consumes it)."""

    def test_current_returns_live_gate_or_null(self, settings, audio_dir, monkeypatch):
        app = approval_app(settings, audio_dir, monkeypatch)
        client = TestClient(app)
        assert client.get("/approval/current", params={"session_id": "phone-1"}).json() == {"approval": None}
        gate = app.state.approval_store.propose(
            "phone-1", text="reenvía el informe", timeout_ms=90000,
            pane_id="w2:p1", agent="claude",
        )
        approval = client.get("/approval/current", params={"session_id": "phone-1"}).json()["approval"]
        assert approval["gate_id"] == gate.gate_id
        assert approval["tool"] == "send_to_session"
        assert approval["pane_id"] == "w2:p1"
        assert approval["agent"] == "claude"
        assert approval["text"] == "reenvía el informe"
        assert approval["timeout_ms"] == 90000
        assert 0 < approval["expires_in_s"] <= 60

    def test_current_uses_the_same_session_source_as_ask(self, settings, audio_dir, monkeypatch):
        app = approval_app(settings, audio_dir, monkeypatch)
        gate = app.state.approval_store.propose(
            None, text="sin sesión", timeout_ms=1000, pane_id="w1:p9", agent="opencode"
        )
        client = TestClient(app)
        # Absent session id normalizes to the default session, like /ask.
        assert client.get("/approval/current").json()["approval"]["gate_id"] == gate.gate_id
        assert client.get("/approval/current", params={"session_id": "other"}).json()["approval"] is None


class TestSessions:
    def test_ask_without_session_uses_default(self, client_factory):
        llm = FakeLLM()
        resp = client_factory(llm=llm).post("/ask", json={"text": "hola"})
        assert resp.status_code == 200
        assert llm.session_ids == [None]
        assert resp.json()["session_id"] == "default"

    def test_ask_with_session_id_echoes_it(self, client_factory):
        llm = FakeLLM()
        resp = client_factory(llm=llm).post(
            "/ask", json={"text": "hola", "session_id": "phone-abc"}
        )
        assert resp.status_code == 200
        assert llm.session_ids == ["phone-abc"]
        assert resp.json()["session_id"] == "phone-abc"

    def test_ask_with_pane_id_targets_selection(self, client_factory):
        llm = FakeLLM()
        resp = client_factory(llm=llm).post(
            "/ask", json={"text": "hola", "session_id": "s1", "pane_id": "w7:p4"}
        )
        assert resp.status_code == 200
        assert llm.pane_ids == ["w7:p4"]
        assert resp.json()["pane_id"] == "w7:p4"

    def test_ask_reset_clears_store_before_answer(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        resets: list = []

        class SpyStore:
            def __init__(self, *a, **k):
                pass

            def reset(self, session_id):
                resets.append(session_id)

            @staticmethod
            def normalize(session_id):
                return session_id or "default"

        monkeypatch.setattr(server_module, "ConversationStore", SpyStore)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        llm = FakeLLM()
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: llm))
        resp = client.post(
            "/ask", json={"text": "hola", "session_id": "s1", "reset": True}
        )
        assert resp.status_code == 200
        assert resets == ["s1"]

    def test_reset_endpoint(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        resets: list = []

        class SpyStore:
            def __init__(self, *a, **k):
                pass

            def reset(self, session_id):
                resets.append(session_id)

            @staticmethod
            def normalize(session_id):
                return session_id or "default"

        monkeypatch.setattr(server_module, "ConversationStore", SpyStore)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        resp = client.post("/reset", json={"session_id": "s1"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "session_id": "s1"}
        assert resets == ["s1"]

    def test_reset_works_keyless(self, settings, audio_dir, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        cfg = Settings(
            **{**settings.__dict__, "audio_dir": str(audio_dir), "glm_api_key": None}
        )
        client = TestClient(create_app(settings=cfg))
        resp = client.post("/reset", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "session_id": "default"}

    def test_store_shared_between_llm_and_reset(self, settings, audio_dir, monkeypatch):
        """attach_store binds the same store instance /reset uses."""
        import herdr_brain.server as server_module

        instances: list = []
        resets: list = []

        class SpyStore:
            def __init__(self, *a, **k):
                instances.append(self)

            def reset(self, session_id):
                resets.append(session_id)

            @staticmethod
            def normalize(session_id):
                return session_id or "default"

        monkeypatch.setattr(server_module, "ConversationStore", SpyStore)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        llm = FakeLLM()
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: llm))

        client.post("/ask", json={"text": "hola", "session_id": "s9"})  # builds LLM
        client.post("/reset", json={"session_id": "s9"})

        # Exactly one store exists, and it is the one the LLM holds.
        assert len(instances) == 1
        assert llm.attached_store is instances[0]
        assert resets == ["s9"]


class TestTts:
    def test_plain_tts(self, client_factory, audio_dir):
        resp = client_factory().post("/tts", json={"text": "echo local del PWA"})
        assert resp.status_code == 200
        audio_url = resp.json()["audio_url"]
        assert (audio_dir / Path(audio_url).name).is_file()

    def test_tts_failure_is_502(self, client_factory):
        resp = client_factory(tts=FakeTTS(fail=True)).post("/tts", json={"text": "hola"})
        assert resp.status_code == 502


class TestStatic:
    """The PWA is served same-origin from the brain server (zero CORS)."""

    def test_index_served_with_call_button(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        # PRD-call-drawer-redesign contract: the full-screen sheet is gone
        # (absorbed by the cockpit); the call lives in #call-drawer. The
        # Collie-style header picker (#pane-picker) + #conv-sheet own
        # conversation selection; the old #herd-strip and the cryptic
        # #new-conversation header button are absorbed by the sheet.
        for element_id in (
            "call-btn", "pause-btn", "state-pill", "pane-picker",
            "conv-sheet", "conv-list", "conv-new",
            "agent-view", "pending-banner", "herd-note",
            "call-drawer", "drawer-close", "call-timer", "d-pending",
            "conversation", "glance-turns", "glance-empty", "glance-label",
            "interim", "meter-cells", "diag-panel",
        ):
            assert f'id="{element_id}"' in html, f"missing #{element_id}"
        assert 'id="sheet"' not in html, "the sheet must stay removed (PRD FR5)"
        for removed_id in ("new-conversation", "herd-strip"):
            assert f'id="{removed_id}"' not in html, f"#{removed_id} must stay removed"
        assert "/app.js" in html
        assert "/endpointing.js" in html
        assert "/vad.js" in html
        assert "/approval.js" in html
        assert "/manifest.webmanifest" in html

    def test_view_payload_shape_for_nonfocused_panes(self, settings, make_stub, active_agent):
        """Regression (BUG 1): /view?pane_id for ANY pane carries every key
        the panel renders -- status/transcript/screen/pending all present."""
        from herdr_brain.tools import BrainTools

        other = AgentInfo(
            pane_id="w1:p2", agent="opencode", status="idle", session_kind="id",
            session_value="ses_other0000001", cwd="/other", title="Other", focused=False,
        )
        tools = BrainTools(settings, herdr=make_stub(agents=[active_agent, other]))
        view = tools.agent_view("w1:p2")
        assert set(view.keys()) == {"status", "transcript", "screen", "pending"}
        assert view["status"]["active"] is True
        assert view["status"]["pane_id"] == "w1:p2"
        assert set(view["pending"].keys()) == {"detected", "kind", "excerpt"}
        # Even on total read failure the keys exist (never an absent field).
        tools_fail = BrainTools(settings, herdr=make_stub(agents=[other], fail_screen=True))
        view_fail = tools_fail.agent_view("w1:p2")
        assert set(view_fail.keys()) == {"status", "transcript", "screen", "pending"}

    def test_index_spanish_labels(self, settings, audio_dir):
        """User-facing labels are Spanish (single Spanish-speaking owner)."""
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        html = client.get("/").text
        assert "📞 Llamar" in html
        assert "⏸ Pausa" in html
        assert "Parar audio" in html
        assert "Llamada con brain" in html
        assert "Vista del agente" in html
        assert "Enviar" in html
        assert "Escribe en su lugar…" in html

    def test_static_assets_served(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        assert client.get("/app.js").status_code == 200
        assert client.get("/sw.js").status_code == 200
        assert client.get("/icon.svg").status_code == 200
        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["name"] == "Herdr Voz"

    def test_api_routes_take_precedence_over_static_mount(self, settings, audio_dir):
        import herdr_brain.server as server_module

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def active_status(self):
                return None

        app = create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM())
        # Re-register is not possible; instead patch tools on a fresh app.
        original_tools = server_module.BrainTools
        server_module.BrainTools = FakeTools
        try:
            app = create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM())
        finally:
            server_module.BrainTools = original_tools
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert client.get("/state").status_code == 200

    def test_state_degrades_when_herdr_fails(self, settings, audio_dir, monkeypatch):
        """herdr CLI missing/failing: poll returns inactive, not a 500."""
        from herdr_brain.herdr import HerdrError

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})

        class FailingTools:
            def __init__(self, cfg):
                self.last_active = None

            def status_payload(self):
                raise HerdrError("herdr exited with 1: boom")

        import herdr_brain.server as server_module

        monkeypatch.setattr(server_module, "BrainTools", FailingTools)
        resp = TestClient(server_module.create_app(settings=cfg)).get("/state")
        assert resp.status_code == 200
        assert resp.json()["active"] is False


class TestHerdEndpoint:
    def test_herd_returns_array(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        herd = [
            {"pane_id": "w1:p1", "agent": "opencode", "agent_status": "working",
             "title": "A", "cwd": "/a", "session_id": "ses_1", "focused": True,
             "last_turn": {"role": "assistant", "text": "hi"}},
            {"pane_id": "w1:p2", "agent": "claude", "agent_status": "idle",
             "title": "B", "cwd": "/b", "session_id": None, "focused": False,
             "last_turn": None},
        ]

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def herd(self):
                return herd

        monkeypatch.setattr(server_module, "BrainTools", FakeTools)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        resp = TestClient(server_module.create_app(settings=cfg)).get("/herd")
        assert resp.status_code == 200
        assert resp.json() == herd

    def test_herd_never_500s(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        class FailingTools:
            def __init__(self, cfg):
                self.last_active = None

            def herd(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(server_module, "BrainTools", FailingTools)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        resp = TestClient(server_module.create_app(settings=cfg)).get("/herd")
        assert resp.status_code == 200
        assert resp.json() == []


class TestView:
    """GET /view: status superset with transcript, screen and pending hint."""

    @staticmethod
    def _tools_class(agent_view_result=None, raise_agent_view=False):
        views_requested: list = []

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def status_payload(self):
                from herdr_brain.tools import status_payload

                return status_payload(None)

            def agent_view(self, pane_id=None):
                views_requested.append(pane_id)
                if raise_agent_view:
                    raise RuntimeError("compose blew up")
                return agent_view_result

        FakeTools.views_requested = views_requested
        return FakeTools

    def _client(self, settings, audio_dir, tools_cls, monkeypatch):
        import herdr_brain.server as server_module

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        monkeypatch.setattr(server_module, "BrainTools", tools_cls)
        return TestClient(server_module.create_app(settings=cfg))

    def test_view_blocked_with_permission_prompt(self, settings, audio_dir, monkeypatch):
        result = {
            "status": {"active": True, "pane_id": "w1:p9", "agent": "opencode",
                       "agent_status": "blocked", "title": "OpenCode",
                       "cwd": "/repo", "session_id": "ses_x"},
            "transcript": [{"role": "assistant", "text": "Need permission to run bash."}],
            "screen": "Do you want to allow this? (y/n)",
            "pending": {"detected": True, "kind": "permission",
                        "excerpt": "Do you want to allow this? (y/n)"},
        }
        client = self._client(
            settings, audio_dir, self._tools_class(agent_view_result=result), monkeypatch
        )
        resp = client.get("/view")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"]["agent_status"] == "blocked"
        assert body["pending"]["detected"] is True
        assert body["pending"]["kind"] == "permission"
        assert "(y/n)" in body["pending"]["excerpt"]

    def test_view_never_500s_when_composition_fails(self, settings, audio_dir, monkeypatch):
        client = self._client(
            settings, audio_dir, self._tools_class(raise_agent_view=True), monkeypatch
        )
        resp = client.get("/view")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"]["active"] is False
        assert body["transcript"] is None
        assert body["screen"] is None
        assert body["pending"] == {"detected": False, "kind": None, "excerpt": None}

    def test_view_transcript_truncation(self, settings, audio_dir, monkeypatch):
        long_text = "y" * 800
        result = {
            "status": {"active": True, "pane_id": "p", "agent": "opencode",
                       "agent_status": "working", "title": "T", "cwd": "/c",
                       "session_id": "ses_x"},
            "transcript": [{"role": "assistant", "text": long_text[:297] + "..."}],
            "screen": None,
            "pending": {"detected": False, "kind": None, "excerpt": None},
        }
        client = self._client(
            settings, audio_dir, self._tools_class(agent_view_result=result), monkeypatch
        )
        body = client.get("/view").json()
        assert len(body["transcript"][0]["text"]) == 300
        assert body["transcript"][0]["text"].endswith("...")

    def test_view_route_beats_static_mount(self, settings, audio_dir, monkeypatch):
        result = {
            "status": {"active": False, "pane_id": None, "agent": None,
                       "agent_status": None, "title": None, "cwd": None,
                       "session_id": None},
            "transcript": None, "screen": None,
            "pending": {"detected": False, "kind": None, "excerpt": None},
        }
        tools_cls = self._tools_class(agent_view_result=result)
        client = self._client(settings, audio_dir, tools_cls, monkeypatch)
        resp = client.get("/view")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

    def test_view_honors_pane_id_param(self, settings, audio_dir, monkeypatch):
        result = {
            "status": {"active": True, "pane_id": "w1:p2", "agent": "opencode",
                       "agent_status": "idle", "title": "Other", "cwd": "/o",
                       "session_id": "ses_x"},
            "transcript": None, "screen": None,
            "pending": {"detected": False, "kind": None, "excerpt": None},
        }
        tools_cls = self._tools_class(agent_view_result=result)
        client = self._client(settings, audio_dir, tools_cls, monkeypatch)
        resp = client.get("/view", params={"pane_id": "w1:p2"})
        assert resp.status_code == 200
        assert resp.json()["status"]["pane_id"] == "w1:p2"
        assert tools_cls.views_requested == ["w1:p2"]

    def test_unknown_static_path_404(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        assert client.get("/nope.js").status_code == 404


class TestFullTextEndpoints:
    def test_conversation_route(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        calls: list = []
        result = {"pane_id": "w1:p2", "agent": "opencode", "session_id": "ses_x",
                  "turns": [{"role": "user", "text": "pregunta completa " + "x" * 400}],
                  "window": 20}

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def conversation(self, pane_id=None):
                calls.append(pane_id)
                return result

        monkeypatch.setattr(server_module, "BrainTools", FakeTools)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        resp = TestClient(server_module.create_app(settings=cfg)).get(
            "/conversation", params={"pane_id": "w1:p2"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["turns"][0]["text"] == result["turns"][0]["text"]
        assert calls == ["w1:p2"]

    def test_conversation_never_500s(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        class FailingTools:
            def __init__(self, cfg):
                self.last_active = None

            def conversation(self, pane_id=None):
                raise RuntimeError("boom")

        monkeypatch.setattr(server_module, "BrainTools", FailingTools)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        resp = TestClient(server_module.create_app(settings=cfg)).get("/conversation")
        assert resp.status_code == 200
        assert resp.json()["turns"] == []

    def test_screen_route(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        result = {"pane_id": "w1:p2", "agent": "opencode", "screen": "line\n" * 120}

        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def screen_full(self, pane_id=None):
                return result

        monkeypatch.setattr(server_module, "BrainTools", FakeTools)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        resp = TestClient(server_module.create_app(settings=cfg)).get(
            "/screen", params={"pane_id": "w1:p2"}
        )
        assert resp.status_code == 200
        assert resp.json()["screen"].count("line") == 120

    def test_screen_never_500s(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        class FailingTools:
            def __init__(self, cfg):
                self.last_active = None

            def screen_full(self, pane_id=None):
                raise RuntimeError("boom")

        monkeypatch.setattr(server_module, "BrainTools", FailingTools)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        resp = TestClient(server_module.create_app(settings=cfg)).get("/screen")
        assert resp.status_code == 200
        assert resp.json()["screen"] is None


class TestVersioning:
    """Kill stale-JS: versioned asset refs + no-cache headers + visible build."""

    def test_index_stamps_asset_refs_and_footer(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, version="abc1234", llm_factory=lambda c, t: FakeLLM()))
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-cache"
        assert 'src="/app.js?v=abc1234"' in resp.text
        assert 'src="/endpointing.js?v=abc1234"' in resp.text
        assert 'src="/vad.js?v=abc1234"' in resp.text
        assert 'src="/approval.js?v=abc1234"' in resp.text
        assert 'href="/manifest.webmanifest?v=abc1234"' in resp.text
        assert 'id="app-version">vabc1234<' in resp.text
        assert 'src="/app.js"></script>' not in resp.text  # no unversioned refs left

    def test_static_assets_served_no_cache(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, version="abc1234", llm_factory=lambda c, t: FakeLLM()))
        for path in ("/app.js", "/endpointing.js", "/vad.js", "/approval.js", "/sw.js"):
            resp = client.get(path)
            assert resp.status_code == 200
            assert resp.headers["cache-control"] == "no-cache"

    def test_default_version_resolves_something(self, settings, audio_dir):
        """No explicit version: git hash or 'dev' fallback — footer always present."""
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        resp = client.get("/")
        assert 'id="app-version">v' in resp.text
        assert "?v=" in resp.text


class TestEvents:
    """SSE endpoint delivery (finite streams via sse_stream_limit).

    starlette's TestClient buffers whole responses before returning, so a
    live watcher publish cannot be observed mid-stream from the test thread.
    These tests therefore pre-load the hub; watcher publish semantics are
    covered in tests/test_watcher.py, and the live pipeline is verified with
    curl -N against the running server.
    """

    @staticmethod
    def _preloaded_hub(events):
        import queue as queue_module

        class PreloadedHub:
            def __init__(self, items):
                self._q = queue_module.Queue()
                for item in items:
                    self._q.put(item)
                self.unsubscribed: list = []

            def subscribe(self):
                return 0, self._q

            def unsubscribe(self, sub_id):
                self.unsubscribed.append(sub_id)

        return PreloadedHub(events)

    def _app(self, settings, audio_dir, hub, limit):
        import herdr_brain.server as server_module
        from herdr_brain.watcher import AgentWatcher

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        watcher = AgentWatcher(cfg, tts_renderer=_write_dummy_tts)
        watcher.hub = hub
        app = server_module.create_app(
            settings=cfg, watcher=watcher, sse_heartbeat_s=0.2, sse_stream_limit=limit
        )
        return app, watcher

    def test_stream_delivers_transition_event(self, settings, audio_dir):
        announcement = {
            "type": "transition",
            "pane_id": "w1:p1",
            "agent": "opencode",
            "status": "done",
            "label": "opencode repo",
            "text": "opencode repo terminó: Todo verde",
            "audio_url": "/audio/ann-abc.mp3",
        }
        hub = self._preloaded_hub([announcement])
        app, _ = self._app(settings, audio_dir, hub, limit=1)
        client = TestClient(app)

        with client.stream("GET", "/events") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            lines = [l for l in resp.iter_lines() if l]
        assert lines == [
            ": connected",
            "data: " + json.dumps(announcement, ensure_ascii=False),
        ]

    def test_stream_heartbeat_when_quiet(self, settings, audio_dir):
        hub = self._preloaded_hub([])
        app, _ = self._app(settings, audio_dir, hub, limit=1)
        client = TestClient(app)

        with client.stream("GET", "/events") as resp:
            lines = [l for l in resp.iter_lines() if l]
        assert lines == [": connected", ": heartbeat"]

    def test_event_unsubscribes_after_stream_ends(self, settings, audio_dir):
        hub = self._preloaded_hub([])
        app, _ = self._app(settings, audio_dir, hub, limit=1)
        client = TestClient(app)
        with client.stream("GET", "/events"):
            pass  # connected + heartbeat, then clean end
        assert hub.unsubscribed == [0]

    def test_data_and_heartbeat_interleave_in_order(self, settings, audio_dir):
        a1 = {"type": "transition", "pane_id": "p", "agent": "opencode",
              "status": "done", "label": "l", "text": "t", "audio_url": None}
        hub = self._preloaded_hub([a1])
        app, _ = self._app(settings, audio_dir, hub, limit=2)
        client = TestClient(app)
        with client.stream("GET", "/events") as resp:
            lines = [l for l in resp.iter_lines() if l]
        assert lines[0] == ": connected"
        assert lines[1].startswith("data: ")
        assert lines[2] == ": heartbeat"  # queue drained -> heartbeat


def _write_dummy_tts(settings, text, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(b"ID3")
    return out_path


class TestAudioServing:
    def test_serves_rendered_file(self, client_factory):
        client = client_factory()
        audio_url = client.post("/tts", json={"text": "hola"}).json()["audio_url"]
        resp = client.get(audio_url)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/mpeg")

    def test_unknown_file_404(self, client_factory):
        assert client_factory().get("/audio/deadbeef.mp3").status_code == 404

    @pytest.mark.parametrize(
        "name",
        ["..%2F..%2Fetc%2Fpasswd", "sub%2Ffile.mp3", "bad%20name.mp3", ".hidden"],
    )
    def test_path_traversal_and_unsafe_names_404(self, client_factory, name):
        assert client_factory().get(f"/audio/{name}").status_code == 404


# -- rendered conversation endpoint (reader-html-integration) --

READER_MAP = {
    "version": 1,
    "contract": "reader-pipeline/anchors@1",
    "alignment": "exact",
    "total_sents": 1,
    "total_paras": 1,
    "engine": {"lang": "es", "max_chars": 0, "summarize": False,
               "lexicon_fp": "stub"},
    "sentences": [
        {"sent_idx": 0, "para_idx": 0, "text": "hola", "selector": "#tts-sent-0",
         "block_ids": ["b0"], "fragments": [[0, 4]], "fragment_texts": ["hola"],
         "exact": True},
    ],
}

CONVERSATION_RESULT = {
    "pane_id": "w1:p9",
    "agent": "opencode",
    "session_id": "ses_test0000session",
    "turns": [
        {"role": "user", "text": "pregunta del usuario"},
        {"role": "assistant", "text": "# Respuesta\nCuerpo del informe."},
    ],
    "window": 20,
}


class FakeReaderRenderer:
    """reader_renderer seam double: returns a valid pair, counts calls."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, settings, text, workdir):
        self.calls.append(text)
        if self.fail:
            from herdr_brain.tts import ReaderError

            raise ReaderError("fake renderer boom")
        return '<p><span class="tts-sent" data-sent-idx="0">hola</span></p>', READER_MAP


def make_conversation_tools(result=None, unresolved_panes=()):
    """BrainTools double serving a canned conversation payload."""
    requested: list = []

    class FakeTools:
        def __init__(self, cfg):
            self.last_active = None

        def conversation(self, pane_id=None):
            requested.append(pane_id)
            if result is not None and pane_id not in unresolved_panes:
                canned = dict(result)
                canned["pane_id"] = pane_id or result["pane_id"]
                return canned
            return {"pane_id": pane_id, "agent": None, "session_id": None,
                    "turns": [], "window": 20}

    FakeTools.requested = requested
    return FakeTools


class TestReaderCsp:
    """Slice B: pinned CSP on the index response ONLY (Req 12)."""

    PINNED_CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'"
    )

    def test_index_carries_exact_csp(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(
            create_app(settings=cfg, version="abc1234",
                       llm_factory=lambda c, t: FakeLLM())
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-security-policy"] == self.PINNED_CSP
        assert resp.headers["cache-control"] == "no-cache"  # unchanged

    def test_csp_absent_on_asset_routes(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(
            create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM())
        )
        for path in ("/app.js", "/approval.js", "/sw.js", "/manifest.webmanifest"):
            resp = client.get(path)
            assert resp.status_code == 200
            assert "content-security-policy" not in resp.headers
            assert resp.headers["cache-control"] == "no-cache"


class TestReaderAsset:
    """/reader.js: versioned static asset under the no-cache discipline."""

    def test_reader_js_served_no_cache(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(
            create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM())
        )
        resp = client.get("/reader.js")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/javascript")
        assert resp.headers["cache-control"] == "no-cache"
        assert "content-security-policy" not in resp.headers  # assets stay plain

    def test_reader_js_reference_is_versioned(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(
            create_app(settings=cfg, version="abc1234",
                       llm_factory=lambda c, t: FakeLLM())
        )
        html = client.get("/").text
        assert 'src="/reader.js?v=abc1234"' in html
        assert 'src="/reader.js"></script>' not in html  # no unversioned ref


class TestRenderedConversation:
    """GET /conversation/{pane_id}/rendered — contract with injected fakes."""

    def _client(self, settings, audio_dir, monkeypatch, reader=None,
                tools_result=None, unresolved_panes=()):
        import herdr_brain.server as server_module

        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        monkeypatch.setattr(
            server_module, "BrainTools",
            make_conversation_tools(tools_result, unresolved_panes),
        )
        return TestClient(
            server_module.create_app(settings=cfg, reader_renderer=reader)
        )

    def test_rendered_contract_shape(self, settings, audio_dir, monkeypatch):
        reader = FakeReaderRenderer()
        client = self._client(
            settings, audio_dir, monkeypatch,
            reader=reader, tools_result=CONVERSATION_RESULT,
        )
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"pane_id", "session_id", "turns"}
        assert body["pane_id"] == "w1:p9"
        assert body["session_id"] == "ses_test0000session"
        assert len(body["turns"]) == len(CONVERSATION_RESULT["turns"])
        for turn, source in zip(body["turns"], CONVERSATION_RESULT["turns"]):
            assert set(turn) == {"turn_id", "role", "text", "html", "map"}
            assert turn["role"] == source["role"]
            assert turn["text"] == source["text"]  # fallback always present
            assert turn["turn_id"]
            assert turn["html"] is not None and turn["map"] == READER_MAP
        assert len(reader.calls) == 2  # one render per turn, counted via seam

    def test_turns_preserve_source_order(self, settings, audio_dir, monkeypatch):
        client = self._client(
            settings, audio_dir, monkeypatch, tools_result=CONVERSATION_RESULT,
        )
        turns = client.get("/conversation/w1:p9/rendered").json()["turns"]
        texts = [t["text"] for t in turns]
        assert texts == [t["text"] for t in CONVERSATION_RESULT["turns"]]

    def test_turn_ids_stable_across_identical_requests(
        self, settings, audio_dir, monkeypatch
    ):
        client = self._client(
            settings, audio_dir, monkeypatch, tools_result=CONVERSATION_RESULT,
        )
        first = client.get("/conversation/w1:p9/rendered").json()["turns"]
        second = client.get("/conversation/w1:p9/rendered").json()["turns"]
        assert [t["turn_id"] for t in first] == [t["turn_id"] for t in second]
        # Duplicate role+text within one snapshot still cannot collide.
        assert len({t["turn_id"] for t in first}) == len(first)

    def test_empty_conversation_returns_empty_list(
        self, settings, audio_dir, monkeypatch
    ):
        empty = {**CONVERSATION_RESULT, "turns": []}
        client = self._client(settings, audio_dir, monkeypatch, tools_result=empty)
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        assert resp.json()["turns"] == []

    def test_unknown_pane_returns_404(self, settings, audio_dir, monkeypatch):
        client = self._client(
            settings, audio_dir, monkeypatch,
            tools_result=CONVERSATION_RESULT, unresolved_panes=("w9:p9",),
        )
        resp = client.get("/conversation/w9:p9/rendered")
        assert resp.status_code == 404
        assert "turns" not in resp.json()  # never a reader fallback body

    def test_no_map_url_field(self, settings, audio_dir, monkeypatch):
        client = self._client(
            settings, audio_dir, monkeypatch, tools_result=CONVERSATION_RESULT,
        )
        body = client.get("/conversation/w1:p9/rendered").json()
        assert "map_url" not in body
        for turn in body["turns"]:
            assert "map_url" not in turn

    def test_map_travels_inline_complete(self, settings, audio_dir, monkeypatch):
        client = self._client(
            settings, audio_dir, monkeypatch,
            reader=FakeReaderRenderer(), tools_result=CONVERSATION_RESULT,
        )
        turns = client.get("/conversation/w1:p9/rendered").json()["turns"]
        assert turns[1]["map"] == READER_MAP  # complete sidecar, inline

    def test_pane_id_traversal_does_not_escape(
        self, settings, audio_dir, monkeypatch
    ):
        import herdr_brain.server as server_module

        tools_cls = make_conversation_tools(CONVERSATION_RESULT)
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        monkeypatch.setattr(server_module, "BrainTools", tools_cls)
        client = TestClient(
            server_module.create_app(settings=cfg, reader_renderer=FakeReaderRenderer())
        )
        resp = client.get("/conversation/..%2F..%2Fetc%2Fpasswd/rendered")
        # Traversal-shaped ids never resolve a conversation: rejected with
        # 404 BEFORE any resolution or filesystem touch (routing decodes
        # the %2F runs into extra path segments, which match no route).
        assert resp.status_code == 404
        assert tools_cls.requested == []  # nothing reached resolution
        # The length ceiling applies like every addressed id.
        oversized = client.get(f"/conversation/{'x' * 200}/rendered")
        assert oversized.status_code == 422


class TestReaderFailSoft:
    """Fail-soft matrix driving the REAL stub binary via env switches."""

    def _client(self, settings, audio_dir, reader=None, **overrides):
        kwargs = {"audio_dir": str(audio_dir)}
        kwargs.update(overrides)
        cfg = Settings(**{**settings.__dict__, **kwargs})
        return TestClient(
            create_app(
                settings=cfg,
                llm_factory=lambda c, t: FakeLLM(),
                tts_renderer=FakeTTS(),
                reader_renderer=reader,
            )
        )

    def _conversation_tools(self, monkeypatch, turns=None):
        import herdr_brain.server as server_module

        result = {
            "pane_id": "w1:p9", "agent": "opencode",
            "session_id": "ses_x",
            "turns": turns if turns is not None else
            [{"role": "assistant", "text": "SECRETO-texto-del-turno"}],
            "window": 20,
        }
        monkeypatch.setattr(
            server_module, "BrainTools",
            make_conversation_tools(result),
        )

    def test_cache_hit_spawns_no_subprocess(
        self, settings, audio_dir, monkeypatch
    ):
        from herdr_brain.tts import render_html

        self._conversation_tools(monkeypatch)
        calls: list[str] = []

        def counting_renderer(cfg, text, workdir):
            calls.append(text)
            return render_html(cfg, text, workdir)

        client = self._client(settings, audio_dir, reader=counting_renderer)
        first = client.get("/conversation/w1:p9/rendered")
        assert first.status_code == 200
        assert first.json()["turns"][0]["html"] is not None
        spawns_after_first = len(calls)
        second = client.get("/conversation/w1:p9/rendered")
        assert second.status_code == 200
        assert second.json()["turns"][0]["html"] is not None
        assert len(calls) == spawns_after_first  # warm hit: flat counter

    def test_missing_binary_nulls_pair(self, settings, audio_dir, monkeypatch):
        self._conversation_tools(monkeypatch)
        client = self._client(
            settings, audio_dir, tts_bin=audio_dir / "nowhere/herdr-tts"
        )
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        turn = resp.json()["turns"][0]
        assert turn["html"] is None and turn["map"] is None
        assert turn["text"] == "SECRETO-texto-del-turno"

    @pytest.mark.parametrize("code", [1, 2, 3])
    def test_exit_codes_null_pair(self, settings, audio_dir, monkeypatch, code):
        self._conversation_tools(monkeypatch)
        monkeypatch.setenv("STUB_RENDER_EXIT", str(code))
        client = self._client(settings, audio_dir)
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        turn = resp.json()["turns"][0]
        assert turn["html"] is None and turn["map"] is None
        assert turn["text"]

    def test_timeout_nulls_pair(self, settings, audio_dir, monkeypatch):
        self._conversation_tools(monkeypatch)
        monkeypatch.setenv("STUB_RENDER_HANG", "5")
        client = self._client(settings, audio_dir, reader_timeout_s=1)
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        turn = resp.json()["turns"][0]
        assert turn["html"] is None and turn["map"] is None

    def test_malformed_sidecar_nulls_pair(self, settings, audio_dir, monkeypatch):
        self._conversation_tools(monkeypatch)
        monkeypatch.setenv("STUB_BAD_MAP", "1")
        client = self._client(settings, audio_dir)
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        turn = resp.json()["turns"][0]
        assert turn["html"] is None and turn["map"] is None

    def test_unwritable_cache_still_200(self, settings, audio_dir, monkeypatch):
        self._conversation_tools(monkeypatch)
        # cache_dir = audio_dir.parent/reader_cache/… → parent is a FILE:
        # mkdir fails, publish degrades, the request still answers 200.
        client = self._client(settings, audio_dir / "sentinel" / "audio")
        resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        turn = resp.json()["turns"][0]
        assert turn["html"] is not None  # memory-only serving, renderer fine

    def test_warning_omits_transcript_payload(
        self, settings, audio_dir, monkeypatch, caplog
    ):
        import logging
        import re as re_module

        self._conversation_tools(monkeypatch)
        monkeypatch.setenv("STUB_RENDER_EXIT", "1")
        client = self._client(settings, audio_dir)
        with caplog.at_level(logging.WARNING):
            resp = client.get("/conversation/w1:p9/rendered")
        assert resp.status_code == 200
        assert "SECRETO" not in caplog.text  # never the transcript payload
        assert re_module.search(r"\[[0-9a-f]{8}\]", caplog.text)  # key prefix

    def test_speech_unaffected_by_reader_failure(
        self, settings, audio_dir, monkeypatch
    ):
        self._conversation_tools(monkeypatch)
        monkeypatch.setenv("STUB_RENDER_EXIT", "1")
        client = self._client(settings, audio_dir)
        tts_resp = client.post("/tts", json={"text": "hola"})
        assert tts_resp.status_code == 200
        assert tts_resp.json()["audio_url"].startswith("/audio/")
        ask_resp = client.post("/ask", json={"text": "hola"})
        assert ask_resp.status_code == 200
        assert ask_resp.json()["answer"]
        assert ask_resp.json()["audio_url"].startswith("/audio/")
