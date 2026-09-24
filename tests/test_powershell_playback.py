"""PowerShell (wsl-ps) playback tests: encoded command, framing, lazy spawn, gate logic.

The persistent-session tests run against a FakePopen that simulates the
PowerShell lifecycle: alive until stdin closes (then exits with
``eof_exit_code``) or killed; ``hangs_on_close`` simulates a busy process
that survives the hard-stop grace period.
"""


import io
import os
import struct
import subprocess
import threading

import pytest

import agent_tts.powershell_playback as psp
from agent_tts import playback_target as pt
from agent_tts.constants import DEFAULT_VOICE
from agent_tts.wav import pcm_to_wav


class FakeStdin(io.BytesIO):
    """BytesIO stdin that records close() calls and stays readable after close."""

    def __init__(self):
        super().__init__()
        self.close_count = 0
        self.written = bytearray()

    def write(self, data):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        self.written += data
        return super().write(data)

    def close(self):
        self.close_count += 1
        super().close()


class FakePopen:
    """Stand-in for subprocess.Popen simulating the persistent PowerShell process."""

    instances = []

    def __init__(self, argv, stdin=None, stdout=None, stderr=None):
        self.argv = argv
        self.stdin = FakeStdin()
        self.stdout = stdout  # as passed by the session (subprocess.DEVNULL)
        self.stderr = io.BytesIO(b"")  # readable pipe contents
        self.stderr_arg = stderr  # as passed by the session (subprocess.PIPE)
        self.exit_code = None  # None = still running
        self.eof_exit_code = 0  # exit code once stdin closes (script exits on EOF)
        self.kill_count = 0
        self.hangs_on_close = False  # ignores stdin close until killed
        FakePopen.instances.append(self)

    def poll(self):
        return self.exit_code

    def wait(self, timeout=None):
        if self.exit_code is None and self.stdin.close_count and not self.hangs_on_close:
            self.exit_code = self.eof_exit_code
        if self.exit_code is None:
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
            raise AssertionError("fake process never exits")
        return self.exit_code

    def kill(self):
        self.kill_count += 1
        self.hangs_on_close = False
        self.exit_code = -9


@pytest.fixture
def fake_popen(monkeypatch):
    FakePopen.instances = []
    monkeypatch.setattr(psp.subprocess, "Popen", FakePopen)
    return FakePopen


def make_decoded(frames, sample_rate=24000, nchannels=1, sample_width=2):
    """Builds a stand-in decoded segment with the requested PCM shape."""
    import array

    class FakeDecoded:
        pass

    decoded = FakeDecoded()
    decoded.sample_rate = sample_rate
    decoded.nchannels = nchannels
    decoded.sample_width = sample_width
    decoded.samples = array.array("h", [((i * 37) % 20000) - 10000 for i in range(frames * nchannels)])
    return decoded


def framed(wav: bytes) -> bytes:
    """The wire format for one group: little-endian u64 length prefix + wav."""
    return struct.pack("<Q", len(wav)) + wav


def wav_of(decoded) -> bytes:
    return pcm_to_wav(
        decoded.samples.tobytes(),
        decoded.sample_rate,
        decoded.nchannels,
        getattr(decoded, "sample_width", 2),
    )


# -- process shape -----------------------------------------------------------


def test_argv_shape_and_script(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]
    assert len(proc.argv) == 6
    assert proc.argv[:5] == [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]
    script = proc.argv[5]
    assert "ReadExact" in script
    assert "BitConverter" in script
    assert "PlaySync()" in script
    # -Command (not -EncodedCommand): real WSL interop hardware silently
    # terminates EncodedCommand processes at random moments.
    # Quote-free, newline-free: safe through the argv -> Windows command line
    # handoff.
    assert '"' not in script
    assert "\n" not in script
    # stdout is dropped; stderr is captured for error reporting.
    assert proc.stdout == subprocess.DEVNULL
    assert proc.stderr_arg == subprocess.PIPE


def test_lazy_spawn_on_first_group(fake_popen):
    session = psp.PowershellSession()
    assert FakePopen.instances == []  # construction spawns nothing
    session.append_pcm(make_decoded(10))  # default 24000Hz/1ch/16bit matches
    assert len(FakePopen.instances) == 1  # first group spawns exactly one process
    session.append_pcm(make_decoded(8))
    assert len(FakePopen.instances) == 1  # later groups reuse the same process


# -- framing ------------------------------------------------------------------


def test_framing_two_groups_in_order(fake_popen):
    session = psp.PowershellSession()
    g1 = make_decoded(10, 24000, 1)
    g2 = make_decoded(8, 24000, 1)
    session.prepare_pcm(g1)
    session.append_pcm(g2)
    expected = framed(wav_of(g1)) + framed(wav_of(g2))
    assert bytes(FakePopen.instances[0].stdin.written) == expected


def test_mismatched_append_dropped(fake_popen, capsys):
    session = psp.PowershellSession()
    g1 = make_decoded(10, 24000, 1)
    session.prepare_pcm(g1)
    assert session.append_pcm(make_decoded(5, 48000, 1)) is False
    assert session.append_pcm(make_decoded(5, 24000, 2)) is False
    assert "mismatched format" in capsys.readouterr().err
    # Only the matching first group ever reached the process.
    assert bytes(FakePopen.instances[0].stdin.written) == framed(wav_of(g1))


def test_play_after_prepare_does_not_rewrite_first_group(fake_popen):
    session = psp.PowershellSession()
    g1 = make_decoded(10)
    session.prepare_pcm(g1)
    session.play(g1)  # streaming path: first group already written by prepare
    assert bytes(FakePopen.instances[0].stdin.written) == framed(wav_of(g1))


def test_play_one_shot_writes_group_and_drains(fake_popen):
    session = psp.PowershellSession()
    g1 = make_decoded(10, 24000, 1)
    session.play(g1)  # no prepare_pcm: one-shot path
    proc = FakePopen.instances[0]
    assert bytes(proc.stdin.written) == framed(wav_of(g1))
    assert proc.stdin.close_count >= 1  # EOF drain: playback completes before returning
    assert session.state["status"] == "stopped"


# -- pause gate -----------------------------------------------------------------


def test_pause_gate_blocks_writer_until_resume(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    session.pause()
    done = threading.Event()
    result = {}

    def writer():
        try:
            result["ok"] = session.append_pcm(make_decoded(8))
        except Exception as e:  # pragma: no cover - failure reporting
            result["error"] = e
        finally:
            done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    assert not done.wait(timeout=0.2)  # writes block while paused
    session.resume()
    assert done.wait(timeout=2.0)
    assert result.get("ok") is True
    thread.join(timeout=1.0)


def test_ipc_pause_resume_toggle_and_stop(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    session.state["status"] = "playing"

    assert session.handle_ipc_command("pause").startswith("status=paused")
    assert not session._gate.is_set()
    assert session.handle_ipc_command("resume").startswith("status=playing")
    assert session._gate.is_set()
    assert session.handle_ipc_command("toggle-pause").startswith("status=paused")
    assert session.handle_ipc_command("toggle_pause").startswith("status=playing")
    assert session._gate.is_set()
    assert session.handle_ipc_command("seek +10").startswith("ERR:")
    assert session.handle_ipc_command("stop") == "status=stopped"
    assert session.state["stop"] is True


def test_ipc_status_reports_requested_provider_and_voice(monkeypatch):
    """RS-2 (SOS-1/B5): wsl-ps status carries the request's engine metadata."""
    for var in ("AGENT_TTS_PROVIDER", "AGENT_TTS_VOICE", "TTS_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    session = psp.PowershellSession(provider="piper", voice="es-ES-AlvaroNeural")
    session.state["status"] = "playing"

    status = session.handle_ipc_command("status")
    assert " provider=piper" in status
    assert " voice=es-ES-AlvaroNeural" in status

    # Without explicit args the same resolution as the local session
    # applies (env fallbacks, then engine defaults).
    default_session = psp.PowershellSession()
    assert default_session.provider == "edge"
    assert default_session.voice == DEFAULT_VOICE
    default_status = default_session.handle_ipc_command("status")
    assert " provider=edge" in default_status
    assert f" voice={DEFAULT_VOICE}" in default_status

    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_TTS_PROVIDER", "kokoro")
    monkeypatch.setenv("AGENT_TTS_VOICE", "ef_dora")
    from_env = psp.PowershellSession()
    assert from_env.provider == "kokoro"
    assert from_env.voice == "ef_dora"


# -- stop / finish lifecycle -----------------------------------------------------


def test_natural_stop_drains_instead_of_killing(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]
    session.stop()  # state["stop"] not set: natural end of run
    assert proc.stdin.close_count >= 1
    assert proc.kill_count == 0
    assert session.state["status"] == "stopped"


def test_explicit_stop_closes_stdin_kills_and_unblocks_writer(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    session.pause()
    done = threading.Event()

    def writer():
        try:
            session.append_pcm(make_decoded(8))
        except RuntimeError:
            pass  # hard stop invalidates the pending write
        finally:
            done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    assert not done.wait(timeout=0.2)
    proc = FakePopen.instances[0]
    proc.hangs_on_close = True  # survives the grace period, forces the kill path
    session.state["stop"] = True  # explicit user stop -> hard stop path
    session.stop()
    assert done.wait(timeout=2.0)  # the gated writer was unblocked
    thread.join(timeout=1.0)
    assert proc.stdin.close_count >= 1
    assert proc.kill_count == 1


def test_finish_nonzero_exit_raises_with_stderr_tail(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]
    proc.eof_exit_code = 3
    proc.stderr = io.BytesIO(b"boom happened\r\n")
    with pytest.raises(RuntimeError, match=r"PowerShell playback failed \(exit code 3\): boom happened"):
        session.finish()
    assert proc.stdin.close_count >= 1


def test_write_after_process_death_raises_english_error(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]
    proc.exit_code = 1  # the process died after the first group
    proc.stderr = io.BytesIO(b"script error\r\n")
    with pytest.raises(RuntimeError, match=r"PowerShell playback failed \(exit code 1\): script error"):
        session.append_pcm(make_decoded(8))
    with pytest.raises(RuntimeError):
        session.append_pcm(make_decoded(8))  # session stays dead: fails fast


def test_keyboard_interrupt_during_write_hard_stops(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]

    class InterruptingStdin(FakeStdin):
        def write(self, data):
            raise KeyboardInterrupt()

    proc.stdin = InterruptingStdin()
    proc.hangs_on_close = True  # survives the grace period, forces the kill path
    with pytest.raises(KeyboardInterrupt):
        session.append_pcm(make_decoded(8))
    assert proc.kill_count == 1


def test_context_manager_drains_on_clean_exit(fake_popen):
    with psp.PowershellSession() as session:
        session.prepare_pcm(make_decoded(10))
        proc = FakePopen.instances[0]
    assert proc.stdin.close_count >= 1
    assert proc.kill_count == 0


def test_context_manager_hard_stops_on_error(fake_popen):
    holder = {}
    try:
        with psp.PowershellSession() as session:
            session.prepare_pcm(make_decoded(10))
            holder["proc"] = FakePopen.instances[0]
            holder["proc"].hangs_on_close = True  # forces the kill path
            raise ValueError("boom")
    except ValueError:
        pass
    assert holder["proc"].kill_count == 1


def test_finish_waits_for_inflight_write_before_closing_stdin(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]
    release_write = threading.Event()
    order = []

    class SlowStdin(FakeStdin):
        def write(self, data):
            release_write.wait(timeout=2.0)
            result = super().write(data)
            order.append("write")
            return result

    proc.stdin = SlowStdin()
    writer_done = threading.Event()

    def writer():
        try:
            session.append_pcm(make_decoded(8))
        except RuntimeError:  # pragma: no cover - not expected here
            pass
        finally:
            writer_done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    finish_done = threading.Event()

    def closer():
        session.finish()
        order.append("close")
        finish_done.set()

    closer_thread = threading.Thread(target=closer)
    closer_thread.start()
    release_write.set()
    assert writer_done.wait(timeout=2.0)
    assert finish_done.wait(timeout=2.0)
    thread.join(timeout=1.0)
    closer_thread.join(timeout=1.0)
    assert order == ["write", "close"]  # stdin closes only after the write lands
    assert proc.stdin.close_count >= 1
    assert proc.kill_count == 0


def test_exit_error_skips_clixml_markup(fake_popen):
    session = psp.PowershellSession()
    session.prepare_pcm(make_decoded(10))
    proc = FakePopen.instances[0]
    proc.eof_exit_code = 2
    proc.stderr = io.BytesIO(b"#< CLIXML\r\n<Objs Version=\"1.1.0.1\">\r\nreal error text\r\n</Objs>\r\n")
    with pytest.raises(RuntimeError, match=r"exit code 2\): real error text"):
        session.finish()


def test_availability_gate(monkeypatch):
    # No powershell.exe on PATH -> unavailable, even with WSL env markers.
    monkeypatch.setattr(psp.shutil, "which", lambda name: None)
    assert psp.is_wsl_ps_available(env={"WSL_DISTRO_NAME": "Ubuntu"}) is False

    # powershell.exe + WSL_DISTRO_NAME -> available.
    monkeypatch.setattr(
        psp.shutil,
        "which",
        lambda name: "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    )
    assert psp.is_wsl_ps_available(env={"WSL_DISTRO_NAME": "Ubuntu"}) is True

    # Without the env marker, /proc/version must mention microsoft.
    monkeypatch.setattr(pt, "_proc_version_text", lambda: "Linux version 5.15.0 (generic)")
    assert psp.is_wsl_ps_available(env={}) is False
    monkeypatch.setattr(
        pt,
        "_proc_version_text",
        lambda: "Linux version 5.15.90.1-microsoft-standard-WSL2",
    )
    assert psp.is_wsl_ps_available(env={}) is True


# -- live integration (real powershell.exe via WSL interop) -----------------------


@pytest.mark.skipif(
    not psp.is_wsl_ps_available(),
    reason="real powershell.exe on PATH + WSL interop required",
)
def test_real_persistent_session_plays_tiny_wav():
    session = psp.PowershellSession()
    decoded = make_decoded(int(24000 * 0.05), 24000, 1)  # ~50 ms of audio
    session.play(decoded)  # returns after the group played and the script exited
    session.stop()
