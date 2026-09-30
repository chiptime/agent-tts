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
    READER_HTML_FLAG,
    READER_MAP_FLAG,
    TTS_BACKEND_MISSING,
    TTS_BACKEND_NAME,
    TTS_BACKEND_OK,
    TTSError,
    ReaderError,
    new_audio_path,
    render_html,
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


class FakeHtmlRunner:
    """Recorder for --render-html: captures argv + input document bytes and
    materializes both renderer outputs (html + sidecar) like the stub CLI."""

    def __init__(
        self,
        returncode: int = 0,
        write_outputs: bool = True,
        html_bytes: bytes = b"",
        sidecar_text: str = '{"version": 1, "stub": true}',
    ):
        self.returncode = returncode
        self.write_outputs = write_outputs
        self.html_bytes = html_bytes
        self.sidecar_text = sidecar_text
        self.calls: list = []
        self.input_docs: list[str] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        in_path, html_path, map_path = Path(cmd[2]), Path(cmd[3]), Path(cmd[5])
        self.input_docs.append(in_path.read_text(encoding="utf-8"))
        if self.write_outputs:
            html_path.write_bytes(self.html_bytes or b"<p>hola</p>")
            map_path.write_text(self.sidecar_text, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr="")


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


class TestRenderHtml:
    """render_html: the reader CLI boundary (design Decision 2, 8).

    argv carries ONLY server-generated paths — the transcript travels as
    file content, never as arguments; one TemporaryDirectory per
    invocation is cleaned on every outcome.
    """

    def test_argv_matches_cli_surface(self, settings: Settings, tmp_path: Path):
        runner = FakeHtmlRunner()
        html, _ = render_html(settings, "hola", tmp_path, runner=runner)
        call = runner.calls[0]
        cmd = call["cmd"]
        assert isinstance(cmd, list)          # list argv, never a shell string
        assert len(cmd) == 6                  # exactly six tokens
        assert cmd[0] == str(settings.tts_bin)
        assert cmd[1] == READER_HTML_FLAG     # --render-html
        assert cmd[3].endswith(".html")
        assert cmd[4] == READER_MAP_FLAG      # --map
        assert cmd[5].endswith(".json")
        assert not call.get("shell")          # shell=False
        assert call.get("timeout") == settings.reader_timeout_s
        assert html == "<p>hola</p>"
        assert runner.input_docs == ["hola"]

    def test_text_never_enters_argv(self, settings: Settings, tmp_path: Path):
        hostile = "--voice x --render-text /etc/passwd"
        runner = FakeHtmlRunner()
        render_html(settings, hostile, tmp_path, runner=runner)
        cmd = runner.calls[0]["cmd"]
        assert hostile not in cmd             # never an argument
        assert "--voice" not in cmd and "--render-text" not in cmd
        assert runner.input_docs == [hostile]  # but whole, as file content

    def test_render_uses_list_argv_no_shell(self, settings: Settings, tmp_path: Path):
        metachars = "back`tick` $(rm -rf /); | pipe & amp"
        runner = FakeHtmlRunner()
        render_html(settings, metachars, tmp_path, runner=runner)
        cmd = runner.calls[0]["cmd"]
        assert isinstance(cmd, list) and not runner.calls[0].get("shell")
        assert "$(rm -rf /)" not in " ".join(cmd[1:])  # no shell material
        assert runner.input_docs == [metachars]        # content, verbatim

    def test_whole_document_preserves_leading_heading(
        self, settings: Settings, tmp_path: Path
    ):
        document = "# Resultado\n\ncuerpo del informe"
        runner = FakeHtmlRunner()
        render_html(settings, document, tmp_path, runner=runner)
        assert runner.input_docs == [document]  # no extraction, no sanitize

    @pytest.mark.parametrize("code", [1, 2, 3])
    def test_exit_1_2_3_raise_reader_error(
        self, settings: Settings, tmp_path: Path, code: int
    ):
        runner = FakeHtmlRunner(returncode=code)
        with pytest.raises(ReaderError):
            render_html(settings, "hola", tmp_path, runner=runner)

    def test_missing_binary_raises(self, settings: Settings, tmp_path: Path):
        cfg = Settings(**{**settings.__dict__, "tts_bin": tmp_path / "nowhere/herdr-tts"})
        with pytest.raises(ReaderError):
            render_html(cfg, "hola", tmp_path)  # real subprocess.run path

    def test_timeout_terminates_and_nulls_pair(
        self, settings: Settings, tmp_path: Path
    ):
        def hanging_runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, settings.reader_timeout_s)

        with pytest.raises(ReaderError, match="timed out"):
            render_html(settings, "hola", tmp_path, runner=hanging_runner)
        assert list(tmp_path.iterdir()) == []   # temp tree removed on timeout

    def test_tempdir_removed_on_every_outcome(
        self, settings: Settings, tmp_path: Path
    ):
        # Success: clean.
        render_html(settings, "hola", tmp_path, runner=FakeHtmlRunner())
        assert list(tmp_path.iterdir()) == []
        # Failure (exit 1): clean.
        with pytest.raises(ReaderError):
            render_html(settings, "hola", tmp_path, runner=FakeHtmlRunner(returncode=1))
        assert list(tmp_path.iterdir()) == []
        # Missing outputs: clean.
        with pytest.raises(ReaderError):
            render_html(
                settings, "hola", tmp_path, runner=FakeHtmlRunner(write_outputs=False)
            )
        assert list(tmp_path.iterdir()) == []

    def test_undecodable_output_is_failsoft(
        self, settings: Settings, tmp_path: Path
    ):
        runner = FakeHtmlRunner(html_bytes=b"<p>\xff\xfe\xfd</p>")
        with pytest.raises(ReaderError):
            render_html(settings, "hola", tmp_path, runner=runner)


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
        assert cfg.tts_home.as_posix().endswith("tts-plugin")
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
