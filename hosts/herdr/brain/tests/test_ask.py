"""CLI smoke tests for python -m herdr_brain.ask. Backends are faked."""

from __future__ import annotations

import json

import pytest

from herdr_brain.ask import main
from herdr_brain.llm import BrainLLMError


class FakeLLM:
    last_instance = None

    def __init__(self, settings, tools):
        self.result = {"answer": "Respuesta corta y hablada.", "pane_id": "p1", "agent": "opencode"}
        FakeLLM.last_instance = self

    def ask(self, question):
        self.question = question
        return dict(self.result)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    monkeypatch.setenv("HERDR_BRAIN_AUDIO_DIR", str(tmp_path / "audio"))
    FakeLLM.last_instance = None


class TestAskCLI:
    def test_ask_without_audio(self, capsys):
        rc = main(["dime", "en", "que", "estas", "--no-audio"], llm_factory=FakeLLM)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Respuesta corta y hablada." in out
        assert "audio:" not in out
        assert FakeLLM.last_instance.question == "dime en que estas"

    def test_ask_with_audio(self, capsys, tmp_path):
        def fake_tts(settings, text, out_path):
            out_path.write_bytes(b"ID3")
            return out_path

        rc = main(["hola"], llm_factory=FakeLLM, tts_renderer=fake_tts)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("Respuesta corta y hablada.")
        assert "audio:" in out

    def test_ask_json_with_audio(self, capsys, tmp_path):
        def fake_tts(settings, text, out_path):
            out_path.write_bytes(b"ID3")
            return out_path

        rc = main(["hola", "--json", "--no-audio"], llm_factory=FakeLLM, tts_renderer=fake_tts)
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["answer"] == "Respuesta corta y hablada."
        assert payload["audio_path"] is None

    def test_tts_failure_is_soft_warning(self, capsys, tmp_path):
        def bad_tts(settings, text, out_path):
            raise RuntimeError("no piper")

        rc = main(["hola"], llm_factory=FakeLLM, tts_renderer=bad_tts)
        assert rc == 0
        err = capsys.readouterr().err
        assert "tts rendering failed" in err

    def test_missing_key_exits_2(self, capsys, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "")

        def keyless_factory(settings, tools):
            raise BrainLLMError("GLM_API_KEY is not set.")

        rc = main(["hola"], llm_factory=keyless_factory)
        assert rc == 2
        assert "GLM_API_KEY" in capsys.readouterr().err
