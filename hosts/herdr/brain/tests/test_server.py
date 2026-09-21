"""HTTP surface tests: LLM and TTS backends are faked, nothing real runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.server import create_app


class FakeLLM:
    def __init__(self, result=None):
        self.calls: list = []
        self.session_ids: list = []
        self.result = result or {
            "answer": "Estás en la fase 2 del brain; todo verde.",
            "pane_id": "w1:p9",
            "agent": "opencode",
            "session_id": "default",
        }

    def attach_store(self, store):
        self.attached_store = store

    def ask(self, text, session_id=None):
        self.calls.append(text)
        self.session_ids.append(session_id)
        return dict(self.result, session_id=session_id or "default")


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
        assert 'id="call-btn"' in resp.text
        assert 'id="new-conversation"' in resp.text
        assert 'id="agent-view"' in resp.text
        assert 'id="pending-banner"' in resp.text
        assert "/app.js" in resp.text
        assert "/manifest.webmanifest" in resp.text

    def test_static_assets_served(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        assert client.get("/app.js").status_code == 200
        assert client.get("/sw.js").status_code == 200
        assert client.get("/icon.svg").status_code == 200
        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["name"] == "herdr-brain"

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
        class FakeTools:
            def __init__(self, cfg):
                self.last_active = None

            def status_payload(self):
                from herdr_brain.tools import status_payload

                return status_payload(None)

            def agent_view(self):
                if raise_agent_view:
                    raise RuntimeError("compose blew up")
                return agent_view_result

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
        client = self._client(
            settings, audio_dir, self._tools_class(agent_view_result=result), monkeypatch
        )
        resp = client.get("/view")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

    def test_unknown_static_path_404(self, settings, audio_dir):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        client = TestClient(create_app(settings=cfg, llm_factory=lambda c, t: FakeLLM()))
        assert client.get("/nope.js").status_code == 404


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
