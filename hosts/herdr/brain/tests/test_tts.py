"""Unit tests for TTS rendering. The engine subprocess is always mocked."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from herdr_brain.config import Settings
from herdr_brain.tts import TTSError, new_audio_path, render_mp3, sanitize_for_speech


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
            "/venv/bin/python",
            "/engine/tts_engine.py",
            "hello world",
            "--voice", "elvira",
            "--rate", "+0%",
            "--max-chars", "300",
            "--output", str(out),
            "--no-play",
        ]

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
