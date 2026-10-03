"""Server-side edges of the speech wiring (voice-stack VS1.8).

Pins the terminal phases and default-path behavior the happy-path suites do
not reach: an LLM failure must free the id, a render failure is 'failed'
(never 'cancelled'), the production renderer path (no injected seam) works
against a real subprocess, and startup helpers degrade instead of crashing.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain import server as server_mod
from herdr_brain.config import Settings
from herdr_brain.llm import BrainLLMError
from herdr_brain.server import create_app
from herdr_brain.speech import (
    PHASE_COMPLETE,
    PHASE_FAILED,
    SEGMENTED_UNAVAILABLE,
    SpeechRegistry,
)
from herdr_brain.tts import TTSError
from herdr_brain.watcher import AgentWatcher
from tests.conftest import SETTINGS_KWARGS, StubHerdr
from tests.test_speech_cancel import (
    ANSWER,
    SPEECH_ID,
    FakeLLM,
    FakeTTS,
    make_client,
    speech_body,
)


class FailingLLM(FakeLLM):
    def ask(self, text, session_id=None, pane_id=None):
        raise BrainLLMError("llm down")


class EmptyAnswerLLM(FakeLLM):
    def ask(self, text, session_id=None, pane_id=None):
        result = super().ask(text, session_id=session_id, pane_id=pane_id)
        return {**result, "answer": ""}


class BrokenTTS:
    def __call__(self, settings, text, out_path: Path) -> Path:
        raise TTSError("synthesis exploded")


@pytest.fixture
def audio_dir(tmp_path) -> Path:
    return tmp_path / "audio"


def test_llm_failure_marks_the_job_failed_and_frees_the_id(audio_dir):
    registry = SpeechRegistry()
    client = make_client(FailingLLM(), FakeTTS(), registry, audio_dir)

    resp = client.post("/ask", json=speech_body())

    assert resp.status_code == 503
    assert registry.get(SPEECH_ID).phase == PHASE_FAILED
    registry.register(SPEECH_ID, "another-token", "s1")  # terminal: the id is reusable


def test_render_failure_without_cancel_is_failed_not_cancelled(audio_dir):
    registry = SpeechRegistry()
    client = make_client(FakeLLM(), BrokenTTS(), registry, audio_dir)

    resp = client.post("/ask", json=speech_body())

    assert resp.status_code == 200
    assert resp.json()["answer"] == ANSWER  # the text survives a dead voice
    # v1-only host: legacy path + the contract's visible degraded marker
    # (contracts/tts-brain-v2.md).
    assert resp.json()["speech"] == {
        "id": SPEECH_ID,
        "status": PHASE_FAILED,
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    assert registry.get(SPEECH_ID).phase == PHASE_FAILED


def test_default_renderer_path_is_cancellable_and_really_renders(tmp_path, audio_dir):
    """No injected seam: an identified turn uses render_mp3_cancellable for real."""
    script = tmp_path / "herdr-tts"
    script.write_text('#!/bin/sh\nprintf ID3 > "$2"\n')
    script.chmod(0o755)
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir), "tts_bin": str(script)})
    registry = SpeechRegistry()
    client = TestClient(create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: FakeLLM(),
        speech_registry=registry,
        watcher=AgentWatcher(cfg, herdr=StubHerdr(), tts_renderer=None),
        daemon_probe=lambda: "up",
    ))

    resp = client.post("/ask", json=speech_body())

    body = resp.json()
    assert resp.status_code == 200
    # The per-test stub has no --contract-capabilities → v1-only host →
    # legacy cancellable render + the visible degraded marker.
    assert body["audio_url"] and body["speech"] == {
        "id": SPEECH_ID,
        "status": PHASE_COMPLETE,
        "degraded": SEGMENTED_UNAVAILABLE,
    }
    served = client.get(body["audio_url"])
    assert served.status_code == 200 and served.content == b"ID3"


def test_empty_answer_produces_no_audio(audio_dir):
    tts = FakeTTS()
    client = make_client(EmptyAnswerLLM(), tts, SpeechRegistry(), audio_dir)

    resp = client.post("/ask", json={"text": "hola", "session_id": "s1"})

    assert resp.status_code == 200
    assert not resp.json().get("audio_url")
    assert tts.calls == []  # nothing to say: the renderer is never invoked


@pytest.fixture
def fresh_version_cache():
    server_mod.resolve_version.cache_clear()
    yield
    server_mod.resolve_version.cache_clear()


def test_resolve_version_degrades_to_dev(monkeypatch, fresh_version_cache):
    monkeypatch.setattr(
        server_mod.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=""),
    )
    assert server_mod.resolve_version() == "dev"  # git answered with failure

    server_mod.resolve_version.cache_clear()

    def no_git(*_args, **_kwargs):
        raise OSError("git not installed")

    monkeypatch.setattr(server_mod.subprocess, "run", no_git)
    assert server_mod.resolve_version() == "dev"  # git missing entirely


def test_create_app_without_settings_loads_them(monkeypatch, audio_dir):
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})
    monkeypatch.setattr("herdr_brain.config.load_settings", lambda: cfg)
    app = create_app(
        llm_factory=lambda _cfg, _tools: FakeLLM(),
        tts_renderer=FakeTTS(),
        watcher=AgentWatcher(cfg, herdr=StubHerdr(), tts_renderer=None),
        daemon_probe=lambda: "up",
    )
    assert TestClient(app).get("/health").status_code == 200
