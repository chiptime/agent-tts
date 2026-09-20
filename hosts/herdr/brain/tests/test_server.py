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
        self.result = result or {
            "answer": "Estás en la fase 2 del brain; todo verde.",
            "pane_id": "w1:p9",
            "agent": "opencode",
        }

    def ask(self, text):
        self.calls.append(text)
        return dict(self.result)


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

            def active_status(self):
                self.last_active = active_agent
                return active_agent

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

            def active_status(self):
                return None

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


class TestTts:
    def test_plain_tts(self, client_factory, audio_dir):
        resp = client_factory().post("/tts", json={"text": "echo local del PWA"})
        assert resp.status_code == 200
        audio_url = resp.json()["audio_url"]
        assert (audio_dir / Path(audio_url).name).is_file()

    def test_tts_failure_is_502(self, client_factory):
        resp = client_factory(tts=FakeTTS(fail=True)).post("/tts", json={"text": "hola"})
        assert resp.status_code == 502


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
