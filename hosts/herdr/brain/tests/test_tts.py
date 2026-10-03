"""Unit tests for TTS rendering through the herdr-tts CLI surface.

Contract v1 only: no venv paths, no engine entrypoints. The subprocess is
either mocked (FakeRunner, for argv assertions) or the session stub bin
(conftest) for the real probe path.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import pytest

from herdr_brain import tts as tts_mod
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
    render_mp3_cancellable,
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


# --- Cancellable render (VS1.2) ---------------------------------------
#
# Fakes follow the job seam: runner(cmd, ...) -> Popen-like child. All
# timing is a FakeClock (sleep advances time); all signals land on
# patched os.killpg/os.kill recorders, so every test PROVES the only
# signalled target is the job's own process group.


class FakeClock:
    """Deterministic time for the poll loop: sleep() just advances time."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeChild:
    """Popen double: finishes on script, or dies only when signalled."""

    def __init__(
        self,
        pid: int,
        finish_after_polls: Optional[int] = None,
        on_poll: Optional[Callable[["FakeChild"], None]] = None,
    ):
        self.pid = pid
        self.returncode: Optional[int] = None
        self.finish_after_polls = finish_after_polls
        self.on_poll = on_poll
        self.poll_count = 0
        self.wait_calls: list = []

    def poll(self) -> Optional[int]:
        self.poll_count += 1
        if self.on_poll is not None:
            self.on_poll(self)
        if (
            self.returncode is None
            and self.finish_after_polls is not None
            and self.poll_count >= self.finish_after_polls
        ):
            self.returncode = 0
        return self.returncode

    def wait(self, timeout=None) -> Optional[int]:
        self.wait_calls.append(timeout)
        return self.returncode

    def terminate(self) -> None:  # non-POSIX fallback path only
        self.returncode = -15

    def kill(self) -> None:  # non-POSIX fallback path only
        self.returncode = -9


class FakeJobRunner:
    """Injectable job factory: records argv, writes the partial artifact,
    and spawns the scripted children in order."""

    def __init__(self, *children: FakeChild, partial: bytes = b"ID3-partial"):
        self.scripted = list(children)
        self.partial = partial
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        child = self.scripted.pop(0)
        out = Path(cmd[cmd.index("--render-text") + 1])
        out.write_bytes(self.partial)
        child.out_path = out
        return child


class SignalBook:
    """Records EVERY process signal (killpg AND kill) with timestamps.

    Effects: SIGKILL always slays the registered fake job's group;
    SIGTERM too, unless the pgid is opted into ``ignore_term``.
    """

    def __init__(self, clock: Optional[FakeClock] = None):
        self.clock = clock
        self.children: dict = {}
        self.ignore_term: set = set()
        self.killpg: list = []  # (pgid, signame, t)
        self.kill: list = []  # (pid,  signame, t)

    def register(self, child: FakeChild) -> FakeChild:
        self.children[child.pid] = child
        return child

    def _t(self) -> float:
        return self.clock.now if self.clock is not None else time.monotonic()

    def arm(self, monkeypatch) -> None:
        def fake_killpg(pgid, sig):
            self.killpg.append((pgid, signal.Signals(sig).name, self._t()))
            child = self.children.get(pgid)
            if child is None or child.returncode is not None:
                return
            if sig == signal.SIGKILL:
                child.returncode = -9
            elif sig == signal.SIGTERM and pgid not in self.ignore_term:
                child.returncode = -15

        def fake_kill(pid, sig):
            self.kill.append((pid, signal.Signals(sig).name, self._t()))

        monkeypatch.setattr(os, "killpg", fake_killpg)
        monkeypatch.setattr(os, "kill", fake_kill)


class TestRenderMp3Cancellable:
    """Cancellable render: own-pgid teardown, never a foreign process."""

    def _cfg(self, settings: Settings, **overrides) -> Settings:
        return Settings(**{**settings.__dict__, **overrides})

    def test_render_cancellable_terminates_pgid_and_deletes_partial(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        clock = FakeClock()
        monkeypatch.setattr(tts_mod, "time", clock)
        cancel = threading.Event()
        child = FakeChild(4242)
        child.on_poll = lambda c: cancel.set()  # operator bails after first poll
        book = SignalBook(clock)
        runner = FakeJobRunner(book.register(child))
        book.arm(monkeypatch)
        out = tmp_path / "ann-cancelled.mp3"

        with pytest.raises(TTSError, match="cancelled"):
            render_mp3_cancellable(settings, "hello", out, cancel, runner=runner)

        assert [(p, s) for p, s, _ in book.killpg] == [(4242, "SIGTERM")]
        assert not out.exists()  # partial artifact removed
        assert child.wait_calls  # the job was reaped

    def test_cancel_never_touches_llm_or_approval_action(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        """An llm call and an approval action share this machine (pids 7777 /
        8888): cancelling a render must produce ZERO signals beyond the
        job's own process group."""
        clock = FakeClock()
        monkeypatch.setattr(tts_mod, "time", clock)
        llm_pid, approval_pid = 7777, 8888
        cancel = threading.Event()
        child = FakeChild(4242)
        child.on_poll = lambda c: cancel.set()
        book = SignalBook(clock)
        runner = FakeJobRunner(book.register(child))
        book.arm(monkeypatch)

        with pytest.raises(TTSError, match="cancelled"):
            render_mp3_cancellable(
                settings, "hi", tmp_path / "a.mp3", cancel, runner=runner
            )

        assert book.killpg and all(p == 4242 for p, _, _ in book.killpg)
        assert book.kill == []  # no pid-level kill of anything, ever
        assert llm_pid not in [p for p, _, _ in book.killpg]
        assert approval_pid not in [p for p, _, _ in book.killpg]

    def test_no_foreign_pgid_signalled(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        """Two concurrent jobs A(111) and B(222): cancelling A never signals
        B's group, and B still completes normally."""
        job_a = FakeChild(111)
        job_b = FakeChild(222, finish_after_polls=3)
        book = SignalBook()  # real clock: A's thread really overlaps B
        runner = FakeJobRunner(book.register(job_a), book.register(job_b))
        book.arm(monkeypatch)
        cancel_a = threading.Event()
        out_a = tmp_path / "a.mp3"
        out_b = tmp_path / "b.mp3"
        outcome: dict = {}

        def run_a():
            try:
                render_mp3_cancellable(settings, "a", out_a, cancel_a, runner=runner)
            except TTSError as exc:
                outcome["a"] = str(exc)

        thread = threading.Thread(target=run_a)
        thread.start()
        while not runner.calls:  # wait until job A is actually launched
            time.sleep(0.01)
        cancel_a.set()  # cancel A while B renders below
        render_mp3_cancellable(
            settings, "b", out_b, threading.Event(), runner=runner
        )
        thread.join(timeout=5)

        assert outcome["a"] == "cancelled"
        assert not out_a.exists()  # A's partial removed
        assert out_b.is_file()  # B's artifact kept
        assert job_b.returncode == 0 and job_b.poll_count >= 3
        assert book.killpg and all(p == 111 for p, _, _ in book.killpg)
        assert book.kill == []

    def test_render_cancellable_success_writes_file(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        clock = FakeClock()
        monkeypatch.setattr(tts_mod, "time", clock)
        book = SignalBook(clock)
        child = FakeChild(4242)
        child.returncode = 0  # born finished: instant clean render
        runner = FakeJobRunner(book.register(child))
        book.arm(monkeypatch)
        out = tmp_path / "a.mp3"

        result = render_mp3_cancellable(
            settings, "hello", out, threading.Event(), runner=runner
        )

        assert result is None
        assert out.is_file() and out.stat().st_size > 0  # artifact kept
        assert book.killpg == [] and book.kill == []  # no signals ever
        assert runner.calls[0]["cmd"] == [
            str(settings.tts_bin),
            "--render-text",
            str(out),
            "hello",
            "--voice", "elvira",
            "--rate", "+0%",
        ]  # same v1 CLI shape as render_mp3

    def test_render_cancellable_timeout_raises(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        clock = FakeClock()
        monkeypatch.setattr(tts_mod, "time", clock)
        book = SignalBook(clock)
        child = FakeChild(4242)  # never finishes, never cancelled
        runner = FakeJobRunner(book.register(child))
        book.arm(monkeypatch)
        cfg = self._cfg(settings, tts_timeout_s=1)
        out = tmp_path / "a.mp3"

        with pytest.raises(TTSError, match="timed out"):
            render_mp3_cancellable(
                cfg, "hi", out, threading.Event(), runner=runner
            )

        assert [(p, s) for p, s, _ in book.killpg] == [
            (4242, "SIGTERM")
        ]  # cleanup attempted on the job's own group
        assert not out.exists()
        assert child.wait_calls

    def test_grace_then_sigkill(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        clock = FakeClock()
        monkeypatch.setattr(tts_mod, "time", clock)
        monkeypatch.setattr(tts_mod, "SPEECH_CANCEL_TERM_S", 0.3)
        cancel = threading.Event()
        child = FakeChild(4242)
        child.on_poll = lambda c: cancel.set()
        book = SignalBook(clock)
        book.ignore_term = {4242}  # a job that ignores SIGTERM
        runner = FakeJobRunner(book.register(child))
        book.arm(monkeypatch)
        out = tmp_path / "a.mp3"

        with pytest.raises(TTSError, match="cancelled"):
            render_mp3_cancellable(settings, "hi", out, cancel, runner=runner)

        assert [(p, s) for p, s, _ in book.killpg] == [
            (4242, "SIGTERM"),
            (4242, "SIGKILL"),
        ]
        term_t, kill_t = book.killpg[0][2], book.killpg[1][2]
        assert kill_t - term_t >= 0.3  # KILL only after the grace elapsed
        assert child.wait_calls  # reaped after KILL
        assert not out.exists()

    def test_render_cancellable_nonzero_exit_raises(
        self, settings: Settings, tmp_path: Path, monkeypatch
    ):
        clock = FakeClock()
        monkeypatch.setattr(tts_mod, "time", clock)
        child = FakeChild(4242)
        child.returncode = 3
        runner = FakeJobRunner(child)
        with pytest.raises(TTSError, match="exited with 3"):
            render_mp3_cancellable(
                settings, "hi", tmp_path / "a.mp3", threading.Event(), runner=runner
            )

    def test_render_cancellable_empty_text_raises(
        self, settings: Settings, tmp_path: Path
    ):
        runner = FakeJobRunner(FakeChild(4242))
        with pytest.raises(TTSError, match="empty"):
            render_mp3_cancellable(
                settings, "  ", tmp_path / "a.mp3", threading.Event(), runner=runner
            )
        assert runner.calls == []


class TestRenderMp3CancellableRealProcess:
    """Real processes, no fakes: the default _PopenRunner, its cleanup, and
    the own-process-group teardown against a genuine child AND grandchild."""

    def _cfg(self, settings: Settings, tmp_path: Path, body: str, **over) -> Settings:
        script = tmp_path / "fake-herdr-tts"
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(0o755)
        return Settings(**{**settings.__dict__, "tts_bin": script, **over})

    def test_real_child_renders_the_file(self, settings: Settings, tmp_path: Path):
        cfg = self._cfg(settings, tmp_path, 'printf ID3 > "$2"\n')
        out = tmp_path / "a.mp3"
        render_mp3_cancellable(cfg, "hola", out, threading.Event())
        assert out.read_bytes() == b"ID3"

    def test_real_child_failure_carries_stderr_detail(self, settings: Settings, tmp_path: Path):
        cfg = self._cfg(settings, tmp_path, "echo boom >&2\nexit 3\n")
        with pytest.raises(TTSError, match="exited with 3: boom"):
            render_mp3_cancellable(cfg, "hola", tmp_path / "a.mp3", threading.Event())

    def test_real_child_that_writes_nothing_is_an_error(self, settings: Settings, tmp_path: Path):
        cfg = self._cfg(settings, tmp_path, "exit 0\n")
        with pytest.raises(TTSError, match="produced no audio"):
            render_mp3_cancellable(cfg, "hola", tmp_path / "a.mp3", threading.Event())

    def test_unlaunchable_binary_is_a_typed_error(self, settings: Settings, tmp_path: Path):
        cfg = Settings(**{**settings.__dict__, "tts_bin": tmp_path / "missing-binary"})
        with pytest.raises(TTSError, match="cannot execute"):
            render_mp3_cancellable(cfg, "hola", tmp_path / "a.mp3", threading.Event())

    def test_real_cancel_takes_down_the_child_and_its_grandchild(
        self, settings: Settings, tmp_path: Path
    ):
        # sh (the job) spawns a sleeper (grandchild) in the same process group.
        cfg = self._cfg(
            settings, tmp_path,
            'printf partial > "$2"\necho $$ > "$2.pid"\nsleep 30 &\necho $! > "$2.gpid"\nwait\n',
        )
        out = tmp_path / "a.mp3"
        cancel = threading.Event()

        def cancel_once_running():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if Path(f"{out}.gpid").exists() and Path(f"{out}.gpid").read_text().strip():
                    cancel.set()
                    return
                time.sleep(0.02)

        helper = threading.Thread(target=cancel_once_running)
        helper.start()
        with pytest.raises(TTSError, match="cancelled"):
            render_mp3_cancellable(cfg, "hola", out, cancel)
        helper.join()

        assert not out.exists()  # the partial artifact is gone
        pids = [int(Path(f"{out}.{ext}").read_text()) for ext in ("pid", "gpid")]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = []
            for pid in pids:
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except ProcessLookupError:
                    pass
            if not alive:
                break
            time.sleep(0.05)
        assert alive == [], f"processes of the cancelled job survived: {alive}"

    def test_signal_job_ignores_an_already_gone_group(self):
        child = subprocess.Popen(["true"], start_new_session=True)
        child.wait()
        tts_mod._signal_job(child, signal.SIGTERM)  # must not raise

    def test_signal_job_without_setsid_signals_only_the_child(self, monkeypatch):
        calls = []

        class Child:
            pid = 1

            def terminate(self):
                calls.append("terminate")

            def kill(self):
                calls.append("kill")

        monkeypatch.setattr(tts_mod, "_HAS_SETSID", False)
        monkeypatch.setattr(os, "killpg", lambda *a: pytest.fail("a foreign group was signalled"))
        tts_mod._signal_job(Child(), signal.SIGKILL)
        tts_mod._signal_job(Child(), signal.SIGTERM)
        assert calls == ["kill", "terminate"]

    def test_discard_partial_tolerates_a_missing_file(self, tmp_path: Path):
        tts_mod._discard_partial(tmp_path / "never-existed.mp3")  # must not raise

    def test_reader_render_oserror_raises_reader_error(self, settings: Settings, tmp_path: Path):
        def broken_runner(cmd, **kwargs):
            raise OSError("exec format error")

        with pytest.raises(ReaderError, match="cannot execute reader CLI"):
            render_html(settings, "hola", tmp_path, runner=broken_runner)
