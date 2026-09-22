"""Unit tests for TTS rendering. The engine subprocess is always mocked."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from herdr_brain.config import Settings, load_settings
from herdr_brain.tts import (
    TTS_BACKEND_MISSING,
    TTS_BACKEND_MIN_VERSION,
    TTS_BACKEND_NAME,
    TTS_BACKEND_OK,
    TTSError,
    new_audio_path,
    render_mp3,
    sanitize_for_speech,
    tts_backend_status,
)


class FakeRunner:
    def __init__(self, returncode: int = 0, stderr: str = "", touch: bool = True):
        self.returncode = returncode
        self.stderr = stderr
        self.touch = touch
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        if self.touch:
            out = Path(cmd[cmd.index("--output") + 1])
            out.write_bytes(b"ID3fake-mp3")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr=self.stderr)


class TestRenderMp3:
    def test_builds_verified_invocation(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner()
        out = tmp_path / "a.mp3"
        render_mp3(settings, "hello world", out, runner=runner)
        assert runner.calls[0]["cmd"] == [
            "/tmp/herdr-brain-test-tts/venv/bin/python",
            "/tmp/herdr-brain-test-tts/engine/tts_engine.py",
            "hello world",
            "--voice", "elvira",
            "--rate", "+0%",
            "--max-chars", "4000",
            "--output", str(out),
            "--no-play",
        ]

    def test_full_answer_reaches_engine_untouched(self, settings: Settings, tmp_path: Path):
        """Regression: answers must render in full -- no --max-chars clipping,
        no --tldr digest. The engine truncates at --max-chars, which cut
        readings mid-sentence when the default was 300."""
        long_answer = ("Este es un informe largo con muchos detalles. " * 20).strip()  # >1000 chars
        runner = FakeRunner()
        render_mp3(settings, long_answer, tmp_path / "a.mp3", runner=runner)
        cmd = runner.calls[0]["cmd"]
        # The whole answer is argv (only whitespace-sanitized), so the engine
        # receives every word regardless of how --max-chars is set.
        assert cmd[2] == " ".join(long_answer.split())
        assert "--tldr" not in cmd
        assert str(settings.tts_max_chars) in cmd

    def test_includes_extra_provider_flags(self, settings: Settings):
        extra = Settings(**{**settings.__dict__, "tts_extra_args": ("--tldr",)})
        runner = FakeRunner()
        render_mp3(extra, "hi", Path("/tmp/x.mp3"), runner=runner)
        assert "--tldr" in runner.calls[0]["cmd"]

    def test_sanitizes_newlines_before_engine(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner()
        render_mp3(settings, "one\ntwo  three\n", tmp_path / "a.mp3", runner=runner)
        assert runner.calls[0]["cmd"][2] == "one two three"

    def test_empty_text_raises_without_running(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner()
        with pytest.raises(TTSError, match="empty"):
            render_mp3(settings, "  \n\t ", tmp_path / "a.mp3", runner=runner)
        assert runner.calls == []

    def test_engine_failure_raises(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner(returncode=1, stderr="piper missing")
        with pytest.raises(TTSError, match="piper missing"):
            render_mp3(settings, "hi", tmp_path / "a.mp3", runner=runner)

    def test_missing_output_file_raises(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner(touch=False)
        with pytest.raises(TTSError, match="no audio"):
            render_mp3(settings, "hi", tmp_path / "a.mp3", runner=runner)

    def test_timeout_raises(self, settings: Settings, tmp_path: Path):
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        with pytest.raises(TTSError, match="timed out"):
            render_mp3(settings, "hi", tmp_path / "a.mp3", runner=runner)


class TestAudioPaths:
    def test_new_audio_path_unique_and_created(self, settings: Settings, tmp_path: Path):
        custom = Settings(**{**settings.__dict__, "audio_dir": tmp_path / "audio"})
        one = new_audio_path(custom)
        two = new_audio_path(custom)
        assert one.parent == tmp_path / "audio"
        assert one != two
        assert one.suffix == ".mp3"
        assert one.parent.is_dir()


class TestSanitize:
    def test_collapses_all_whitespace(self):
        assert sanitize_for_speech("a\nb\r\nc\td  e") == "a b c d e"


class TestBackendContract:
    """The herdr-tts dependency is an explicit contract, fail-soft."""

    def _cfg(self, settings: Settings, **overrides) -> Settings:
        return Settings(**{**settings.__dict__, **overrides})

    def test_status_ok_with_stubs(self, settings: Settings):
        status, detail = tts_backend_status(settings)
        assert status == TTS_BACKEND_OK
        assert TTS_BACKEND_NAME in detail

    def test_status_missing_names_both_absent_pieces(self, settings: Settings, tmp_path):
        cfg = self._cfg(
            settings,
            tts_venv=tmp_path / "nowhere",
            tts_python=tmp_path / "nowhere/venv/bin/python",
            tts_engine=tmp_path / "nowhere/lib/tts_engine.py",
        )
        status, detail = tts_backend_status(cfg)
        assert status == TTS_BACKEND_MISSING
        assert TTS_BACKEND_NAME in detail
        assert "venv python" in detail and "engine entry" in detail
        assert TTS_BACKEND_MIN_VERSION in detail  # the explicit versioned contract

    def test_status_missing_engine_only(self, settings: Settings, tmp_path):
        cfg = self._cfg(settings, tts_engine=tmp_path / "nope.py")
        status, detail = tts_backend_status(cfg)
        assert status == TTS_BACKEND_MISSING
        assert "engine entry" in detail and "venv python" not in detail

    def test_render_raises_contract_error_without_running(self, settings: Settings, tmp_path):
        cfg = self._cfg(
            settings,
            tts_python=tmp_path / "nowhere/bin/python",
            tts_engine=tmp_path / "nowhere/lib/tts_engine.py",
        )
        runner = FakeRunner()
        with pytest.raises(TTSError, match=TTS_BACKEND_NAME):
            render_mp3(cfg, "hi", tmp_path / "a.mp3", runner=runner)
        assert runner.calls == []  # the contract check precedes any subprocess


class TestTtsVenvConfig:
    """HERDR_TTS_VENV is the contract root; the python derives from it."""

    def test_venv_default_derives_python(self):
        cfg = load_settings(env={})
        assert cfg.tts_venv.as_posix().endswith("herdr-tts/venv")
        assert cfg.tts_python == cfg.tts_venv / "bin" / "python"

    def test_venv_override_moves_derived_python(self):
        cfg = load_settings(env={"HERDR_TTS_VENV": "/custom/venv"})
        assert cfg.tts_venv.as_posix() == "/custom/venv"
        assert cfg.tts_python.as_posix() == "/custom/venv/bin/python"

    def test_explicit_python_still_wins(self):
        cfg = load_settings(env={"HERDR_TTS_VENV": "/custom/venv", "HERDR_TTS_PYTHON": "/other/python"})
        assert cfg.tts_python.as_posix() == "/other/python"
