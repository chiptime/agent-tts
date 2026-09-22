"""Unit tests for TTS rendering through the herdr-tts CLI surface.

Contract v1 only: no venv paths, no engine entrypoints. The subprocess is
either mocked (FakeRunner, for argv assertions) or the session stub bin
(conftest) for the real probe path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from herdr_brain.config import Settings, load_settings
from herdr_brain.tts import (
    TTS_BACKEND_MISSING,
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
            out = Path(cmd[cmd.index("--render-text") + 1])
            out.write_bytes(b"ID3fake-mp3")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr=self.stderr)


class TestRenderMp3:
    def test_invokes_the_surface_contract(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner()
        out = tmp_path / "a.mp3"
        render_mp3(settings, "hello world", out, runner=runner)
        assert runner.calls[0]["cmd"] == [
            str(settings.tts_bin),
            "--render-text",
            str(out),
            "hello world",
            "--voice", "elvira",
            "--rate", "+0%",
        ]

    def test_full_answer_reaches_the_surface_untouched(self, settings: Settings, tmp_path: Path):
        """The whole answer is argv (whitespace-sanitized only): no digest,
        no truncation — length capping is herdr-tts's own concern now."""
        long_answer = ("Este es un informe largo con muchos detalles. " * 20).strip()
        runner = FakeRunner()
        render_mp3(settings, long_answer, tmp_path / "a.mp3", runner=runner)
        assert runner.calls[0]["cmd"][3] == " ".join(long_answer.split())

    def test_sanitizes_newlines_before_surface(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner()
        render_mp3(settings, "one\ntwo  three\n", tmp_path / "a.mp3", runner=runner)
        assert runner.calls[0]["cmd"][3] == "one two three"

    def test_empty_text_raises_without_running(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner()
        with pytest.raises(TTSError, match="empty"):
            render_mp3(settings, "  \n\t ", tmp_path / "a.mp3", runner=runner)
        assert runner.calls == []

    def test_render_failure_raises(self, settings: Settings, tmp_path: Path):
        runner = FakeRunner(returncode=1, stderr="synthesis failed")
        with pytest.raises(TTSError, match="synthesis failed"):
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

    def test_unexecutable_cli_names_the_contract(self, settings: Settings, tmp_path: Path):
        """No runner injected and the CLI path is bogus: the real exec fails
        and the error names the surface contract."""
        cfg = Settings(**{**settings.__dict__, "tts_bin": tmp_path / "nowhere/herdr-tts"})
        with pytest.raises(TTSError, match=TTS_BACKEND_NAME):
            render_mp3(cfg, "hi", tmp_path / "a.mp3")


class TestBackendContract:
    """The herdr-tts surface contract is explicit and fail-soft."""

    def _cfg(self, settings: Settings, **overrides) -> Settings:
        return Settings(**{**settings.__dict__, **overrides})

    def test_status_ok_via_stub_surface(self, settings: Settings):
        status, detail = tts_backend_status(settings)  # real subprocess on the stub bin
        assert status == TTS_BACKEND_OK
        assert TTS_BACKEND_NAME in detail
        assert "surface contract v1" in detail

    def test_status_missing_when_cli_absent(self, settings: Settings, tmp_path):
        cfg = self._cfg(settings, tts_home=tmp_path / "nowhere", tts_bin=tmp_path / "nowhere/bin/herdr-tts")
        status, detail = tts_backend_status(cfg)
        assert status == TTS_BACKEND_MISSING
        assert TTS_BACKEND_NAME in detail
        assert "--contract-version" in detail  # names the exact gate command

    def _probe_runner(self, returncode=0, stdout="1"):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

        return runner, calls

    def test_status_rejects_older_surface_version(self, settings: Settings):
        runner, calls = self._probe_runner(stdout="0")
        status, detail = tts_backend_status(settings, runner=runner)
        assert status == TTS_BACKEND_MISSING
        assert "version 0" in detail
        assert calls[0][-1] == "--contract-version"

    def test_status_rejects_garbage_version(self, settings: Settings):
        runner, _ = self._probe_runner(stdout="not-a-number")
        status, detail = tts_backend_status(settings, runner=runner)
        assert status == TTS_BACKEND_MISSING
        assert "not-a-number" in detail

    def test_status_rejects_probe_failure(self, settings: Settings):
        def boom(cmd, **kwargs):
            raise RuntimeError("exec format error")

        status, detail = tts_backend_status(settings, runner=boom)
        assert status == TTS_BACKEND_MISSING
        assert "probe failed" in detail

    def test_status_rejects_nonzero_exit(self, settings: Settings):
        runner, _ = self._probe_runner(returncode=127)
        status, detail = tts_backend_status(settings, runner=runner)
        assert status == TTS_BACKEND_MISSING
        assert "127" in detail


class TestTtsHomeConfig:
    """HERDR_TTS_HOME names the repo root; the surface CLI derives from it."""

    def test_default_home_derives_bin(self):
        cfg = load_settings(env={})
        assert cfg.tts_home.as_posix().endswith("herdr-tts")
        assert cfg.tts_bin == cfg.tts_home / "bin/herdr-tts"

    def test_home_override_moves_derived_bin(self):
        cfg = load_settings(env={"HERDR_TTS_HOME": "/custom/herdr-tts"})
        assert cfg.tts_home.as_posix() == "/custom/herdr-tts"
        assert cfg.tts_bin.as_posix() == "/custom/herdr-tts/bin/herdr-tts"


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
