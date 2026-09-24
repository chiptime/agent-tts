"""Client-side vía única tests (RF-AT-04-5, RF-AT-04-8, RNF-AT-04-3, US-AT-04-2).

The CLI is always a client: probe with the 200 ms gate, transparent
auto-start, kill-and-respawn of a wedged daemon, clear errors when
nothing works, and observable parity with or without a running daemon.
All scenarios run against an isolated channel; the suite never touches
the real system channel nor spawns real detached daemons.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

import agent_tts.daemon as daemon_mod
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.constants import PING_TIMEOUT_SEC
from agent_tts.daemon import (
    Daemon,
    DaemonUnavailableError,
    ProviderCache,
    delegate_play,
    ensure_daemon,
    probe_daemon,
    send_control_command,
)


@pytest.fixture
def channel(tmp_path, monkeypatch):
    paths = {
        "lock": str(tmp_path / "playing.lock"),
        "pid": str(tmp_path / "current.pid"),
        "sock": str(tmp_path / "player.sock"),
    }
    monkeypatch.setattr(audio, "LOCK_FILE", paths["lock"])
    monkeypatch.setattr(audio, "PID_FILE", paths["pid"])
    monkeypatch.setattr(audio, "IPC_SOCKET", paths["sock"])
    yield paths
    audio.cleanup_locks()


class _StubEngine:
    async def synthesize(self, text, voice=None, **kwargs):
        return b"stub"


def _fake_play(self, decoded):
    self.state["status"] = "playing"
    self.state["playing"] = True
    deadline = time.monotonic() + 5.0
    while not self.state["stop"] and time.monotonic() < deadline:
        time.sleep(0.02)
    self.state["status"] = "stopped"


def _patch_playback_internals(monkeypatch):
    """Stubs synthesis-side decode and device-side play (same as daemon tests)."""
    import array
    import types

    import agent_tts.cli as cli_mod

    class FakeDecoded:
        sample_rate = 24000
        nchannels = 1
        duration = 0.01
        samples = array.array("h", [0] * 240)

    monkeypatch.setattr(cli_mod, "miniaudio", types.SimpleNamespace(decode=lambda data: FakeDecoded()))
    monkeypatch.setattr(AudioSession, "play", _fake_play)


def _start_inprocess_daemon(channel, monkeypatch, **kwargs) -> Daemon:
    _patch_playback_internals(monkeypatch)
    d = Daemon(
        socket_path=channel["sock"],
        provider_cache=ProviderCache(factory=lambda **kw: _StubEngine()),
        **kwargs,
    )
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    d._test_thread = thread
    deadline = time.monotonic() + 5.0
    reply = None
    while time.monotonic() < deadline:
        reply = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        if reply and reply.startswith("pong"):
            break
        time.sleep(0.02)
    assert reply and reply.startswith("pong"), f"daemon never answered ping: {reply!r}"
    return d


def _stop_inprocess_daemon(d: Daemon) -> None:
    d.request_shutdown()
    getattr(d, "_test_thread", None) and d._test_thread.join(timeout=5.0)


def _fake_spawn(channel, monkeypatch):
    """Replaces _spawn_daemon with an in-process starter; returns (calls, daemons)."""
    calls = []
    daemons = []

    def spawn(idle_timeout_sec, socket_path=None):
        calls.append(idle_timeout_sec)
        daemons.append(_start_inprocess_daemon(channel, monkeypatch, idle_timeout_sec=idle_timeout_sec))

    monkeypatch.setattr(daemon_mod, "_spawn_daemon", spawn)
    return calls, daemons


def _stop_all(daemons):
    for d in daemons:
        _stop_inprocess_daemon(d)
    audio.cleanup_locks()


# --- Probe classification -------------------------------------------------------------


def test_ping_gate_is_200_ms():
    assert PING_TIMEOUT_SEC == 0.2


def test_probe_unreachable_when_no_channel(channel):
    status, reply = probe_daemon(PING_TIMEOUT_SEC, channel["sock"])
    assert status == "unreachable"
    assert reply is None


def test_probe_ok_with_healthy_daemon(channel, monkeypatch):
    d = _start_inprocess_daemon(channel, monkeypatch)
    try:
        status, reply = probe_daemon(PING_TIMEOUT_SEC, channel["sock"])
        assert status == "ok"
        assert reply.startswith("pong")
    finally:
        _stop_inprocess_daemon(d)


def test_probe_foreign_with_non_daemon_owner(channel):
    audio._write_player_locks()
    server = ipc.IPCServer(command_handler=lambda cmd: "status=playing", socket_path=channel["sock"])
    server.start()
    try:
        status, reply = probe_daemon(PING_TIMEOUT_SEC, channel["sock"])
        assert status == "foreign"
        assert reply == "status=playing"
    finally:
        server.stop()
        audio.cleanup_locks()


_WEDGED_CHILD = (
    "import sys\n"
    "import socket\n"
    "import time\n"
    "import agent_tts.audio as audio\n"
    "audio._write_player_locks()\n"
    "srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "srv.bind(sys.argv[1])\n"
    "srv.listen(5)\n"
    "print('READY', flush=True)\n"
    "while True:\n"
    "    try:\n"
    "        conn, _ = srv.accept()\n"
    "    except OSError:\n"
    "        break\n"
    "    time.sleep(60)\n"  # the wedge: never read, never reply
)


def _child_env(channel):
    env = dict(os.environ)
    env["AGENT_TTS_LOCK_FILE"] = channel["lock"]
    env["AGENT_TTS_PID_FILE"] = channel["pid"]
    env["AGENT_TTS_SOCKET"] = channel["sock"]
    return env


def _start_wedged_child(channel):
    proc = subprocess.Popen(
        [sys.executable, "-c", _WEDGED_CHILD, channel["sock"]],
        env=_child_env(channel),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"wedged child died: {proc.stderr.read()}")
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            probe.connect(channel["sock"])
            probe.close()
            return proc
        except OSError:
            time.sleep(0.05)
    proc.kill()
    raise AssertionError("wedged child never exposed the socket")


def test_probe_wedged_when_channel_alive_but_silent(channel):
    proc = _start_wedged_child(channel)
    try:
        started = time.monotonic()
        status, reply = probe_daemon(PING_TIMEOUT_SEC, channel["sock"])
        elapsed = time.monotonic() - started
        # The 200 ms gate is respected: silent-but-alive classifies as
        # wedged quickly, it does not hang for the default client timeout.
        assert status == "wedged"
        assert reply is None
        assert elapsed < 1.5
    finally:
        proc.kill()
        proc.wait(timeout=10)


# --- ensure_daemon: transparent auto-start (RF-AT-04-5) --------------------------------


def test_ensure_daemon_returns_fast_when_healthy(channel, monkeypatch):
    d = _start_inprocess_daemon(channel, monkeypatch)
    spawn_calls, spawned = _fake_spawn(channel, monkeypatch)  # must NOT be used
    try:
        reply = ensure_daemon(socket_path=channel["sock"])
        assert reply.startswith("pong")
        assert spawn_calls == []
    finally:
        _stop_all(spawned)
        _stop_inprocess_daemon(d)


def test_ensure_daemon_auto_starts_and_passes_default_idle_timeout(channel, monkeypatch):
    spawn_calls, spawned = _fake_spawn(channel, monkeypatch)
    try:
        reply = ensure_daemon(socket_path=channel["sock"])
        assert reply.startswith("pong")
        # The implicit daemon carries the 30-minute idle policy (RF-AT-04-7).
        assert spawn_calls == [1800.0]
        # The spawned daemon really serves a play (US-AT-04-2 delegation).
        assert delegate_play({"text": "hola"}, socket_path=channel["sock"]) == "status=done"
    finally:
        _stop_all(spawned)


def test_ensure_daemon_env_override_reaches_autostart(channel, monkeypatch):
    monkeypatch.setenv("AGENT_TTS_IDLE_TIMEOUT", "7")
    spawn_calls, spawned = _fake_spawn(channel, monkeypatch)
    try:
        assert ensure_daemon(socket_path=channel["sock"]).startswith("pong")
        assert spawn_calls == [7.0]
    finally:
        _stop_all(spawned)


def test_ensure_daemon_fails_with_clear_error_when_start_fails(channel, monkeypatch):
    monkeypatch.setattr(
        daemon_mod, "_spawn_daemon", lambda *a, **kw: None  # spawn succeeds, daemon never comes
    )
    with pytest.raises(DaemonUnavailableError) as excinfo:
        ensure_daemon(socket_path=channel["sock"], autostart_timeout_sec=0.5)
    # RNF-AT-04-3: clear and logged error naming where to look.
    assert "auto-start failed" in str(excinfo.value)
    assert "agent-tts-daemon.log" in str(excinfo.value)


def test_ensure_daemon_foreign_owner_is_a_clear_error(channel):
    audio._write_player_locks()
    server = ipc.IPCServer(command_handler=lambda cmd: "status=playing", socket_path=channel["sock"])
    server.start()
    try:
        with pytest.raises(DaemonUnavailableError) as excinfo:
            ensure_daemon(socket_path=channel["sock"], autostart_timeout_sec=0.3)
        assert "non-daemon playback" in str(excinfo.value)
    finally:
        server.stop()
        audio.cleanup_locks()


# --- Kill-and-respawn (RF-AT-04-8, RNF-AT-04-3) ----------------------------------------


def test_wedged_daemon_is_killed_and_respawned_before_delegating(channel, monkeypatch):
    proc = _start_wedged_child(channel)
    try:
        assert probe_daemon(PING_TIMEOUT_SEC, channel["sock"]) == ("wedged", None)

        spawn_calls, spawned = _fake_spawn(channel, monkeypatch)
        reply = ensure_daemon(socket_path=channel["sock"], autostart_timeout_sec=8.0)

        assert reply.startswith("pong")
        assert proc.poll() is not None  # the wedged owner was SIGKILLed
        assert spawn_calls == [1800.0]
        # The respawned daemon really serves plays: the user is not left
        # without voice (RNF-AT-04-3).
        assert delegate_play({"text": "recovered"}, socket_path=channel["sock"]) == "status=done"
    finally:
        _stop_all(spawned)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_kill_uses_pid_file_and_spares_unrelated_processes(channel, monkeypatch, tmp_path):
    # PID_FILE missing: no kill attempt, no crash.
    assert daemon_mod._kill_wedged_daemon() is False
    # PID_FILE pointing at a live non-agent process: spared by the cmdline check.
    with open(channel["pid"], "w") as f:
        f.write(str(os.getpid()))  # the test process: alive, cmdline has pytest, not agent_tts
    monkeypatch.setattr(daemon_mod, "_pid_looks_like_agent_tts", lambda pid: False)
    assert daemon_mod._kill_wedged_daemon() is False


# --- Control commands ------------------------------------------------------------------


def test_control_command_served_by_daemon(channel, monkeypatch):
    d = _start_inprocess_daemon(channel, monkeypatch)
    try:
        reply = send_control_command("status", socket_path=channel["sock"])
        assert reply and reply.startswith("status=idle")
    finally:
        _stop_inprocess_daemon(d)


def test_control_command_without_daemon_returns_none(channel):
    assert send_control_command("status", socket_path=channel["sock"]) is None


def test_control_command_reaches_foreign_owner_directly(channel):
    audio._write_player_locks()
    server = ipc.IPCServer(
        command_handler=lambda cmd: "status=playing owner=classic", socket_path=channel["sock"]
    )
    server.start()
    try:
        assert (
            send_control_command("status", socket_path=channel["sock"])
            == "status=playing owner=classic"
        )
    finally:
        server.stop()
        audio.cleanup_locks()


def test_control_command_recovers_from_wedged_daemon(channel, monkeypatch):
    proc = _start_wedged_child(channel)
    try:
        _, spawned = _fake_spawn(channel, monkeypatch)
        reply = send_control_command("status", socket_path=channel["sock"])
        assert reply is not None and reply.startswith("status=idle")  # respawned daemon answered
        assert proc.poll() is not None
    finally:
        _stop_all(spawned)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# --- delegate_play and Ctrl-C parity ----------------------------------------------------


def test_delegate_play_blocks_until_playback_ends(channel, monkeypatch):
    d = _start_inprocess_daemon(channel, monkeypatch)
    try:
        # A play whose playback is stopped via IPC replies status=stopped.
        def stop_soon():
            time.sleep(0.3)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with d._lock:
                    if d.active_session is not None:
                        break
                time.sleep(0.02)
            ipc.send_ipc_command("stop", socket_path=channel["sock"])

        stopper = threading.Thread(target=stop_soon, daemon=True)
        stopper.start()
        reply = delegate_play({"text": "bloqueante"}, socket_path=channel["sock"])
        assert reply == "status=stopped"
        stopper.join(timeout=5.0)
    finally:
        _stop_inprocess_daemon(d)


def test_delegate_play_interrupt_forwards_stop(channel, monkeypatch):
    d = _start_inprocess_daemon(channel, monkeypatch)
    try:
        def raising_send(payload, socket_path=None):
            raise KeyboardInterrupt

        with mock.patch.object(daemon_mod, "send_play", side_effect=raising_send):
            with mock.patch.object(daemon_mod, "send_ipc_command") as stop_recorder:
                with pytest.raises(KeyboardInterrupt):
                    delegate_play({"text": "x"}, socket_path=channel["sock"])
                stop_recorder.assert_called_once_with("stop", socket_path=channel["sock"])
    finally:
        _stop_inprocess_daemon(d)


# --- US-AT-04-2: observable parity with and without a daemon ---------------------------


def test_same_invocation_behaves_identically_with_and_without_daemon(channel, monkeypatch):
    payload = {"text": "paridad", "voice": "elvira"}

    # With a daemon already running.
    d = _start_inprocess_daemon(channel, monkeypatch)
    try:
        with_daemon = delegate_play(payload, socket_path=channel["sock"])
    finally:
        _stop_inprocess_daemon(d)
    assert with_daemon == "status=done"
    audio.cleanup_locks()  # the in-process daemon released on its way down

    # Without a daemon: killed above; the next invocation transparently
    # auto-starts one and behaves identically.
    spawn_calls, spawned = _fake_spawn(channel, monkeypatch)
    try:
        without_daemon = delegate_play(payload, socket_path=channel["sock"])
        assert without_daemon == with_daemon
        assert spawn_calls == [1800.0]
    finally:
        _stop_all(spawned)


# --- CLI wiring --------------------------------------------------------------------------


def _run_cli(monkeypatch, argv):
    from agent_tts import cli

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    return excinfo.value.code


def test_cli_serve_flag_runs_daemon(monkeypatch):
    recorded = {}
    with mock.patch.object(
        daemon_mod, "run_daemon", side_effect=lambda **kw: recorded.update(kw) or 0
    ):
        code = _run_cli(monkeypatch, ["cli.py", "--serve"])
    assert code == 0
    assert recorded["idle_timeout_sec"] is None  # explicit start: no timeout (RF-AT-04-7)


def test_cli_serve_flag_with_idle_timeout(monkeypatch):
    recorded = {}
    with mock.patch.object(
        daemon_mod, "run_daemon", side_effect=lambda **kw: recorded.update(kw) or 0
    ):
        _run_cli(monkeypatch, ["cli.py", "--foreground", "--idle-timeout", "5"])
    assert recorded["idle_timeout_sec"] == 5.0


def test_cli_text_delegates_and_exits_zero(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod, "delegate_play", return_value="status=done"
    ) as delegate:
        code = _run_cli(monkeypatch, ["cli.py", "hola", "--voice", "alvaro"])
    assert code == 0
    payload = delegate.call_args.args[0]
    assert payload["text"] == "hola"
    assert payload["voice"] == "alvaro"
    assert payload["playback"] is None  # absent: the daemon's startup env decides
    assert capsys.readouterr().out == ""  # success stays silent (classic parity)


def test_cli_payload_carries_winhost_endpoint(monkeypatch):
    """SOS-1/B5: --winhost-host/--winhost-port cross the IPC boundary."""
    with mock.patch.object(
        daemon_mod, "delegate_play", return_value="status=done"
    ) as delegate:
        _run_cli(
            monkeypatch,
            ["cli.py", "hola", "--winhost-host", "192.0.2.10", "--winhost-port", "7799"],
        )
    payload = delegate.call_args.args[0]
    assert payload["winhost_host"] == "192.0.2.10"
    assert payload["winhost_port"] == "7799"


def test_cli_payload_winhost_endpoint_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_TTS_WINHOST_HOST", "10.0.0.8")
    monkeypatch.setenv("AGENT_TTS_WINHOST_PORT", "7718")
    with mock.patch.object(
        daemon_mod, "delegate_play", return_value="status=done"
    ) as delegate:
        _run_cli(monkeypatch, ["cli.py", "hola"])
    payload = delegate.call_args.args[0]
    assert payload["winhost_host"] == "10.0.0.8"
    assert payload["winhost_port"] == "7718"


def test_cli_err_reply_exits_one_with_stderr(monkeypatch, capsys):
    with mock.patch.object(
        daemon_mod, "delegate_play", return_value="ERR: playback target unavailable (exit 1)"
    ):
        code = _run_cli(monkeypatch, ["cli.py", "hola"])
    assert code == 1
    assert "ERR: playback target unavailable" in capsys.readouterr().err


def test_cli_daemon_unavailable_exits_one_with_clear_error(monkeypatch, capsys):
    def unavailable(payload, socket_path=None):
        raise DaemonUnavailableError("agent-tts: daemon auto-start failed (no ping answer)")

    with mock.patch.object(daemon_mod, "delegate_play", side_effect=unavailable):
        code = _run_cli(monkeypatch, ["cli.py", "hola"])
    assert code == 1
    assert "auto-start failed" in capsys.readouterr().err


def test_cli_ipc_cmd_without_daemon_keeps_legacy_error(monkeypatch, capsys):
    monkeypatch.setattr(
        daemon_mod, "send_control_command", lambda cmd, socket_path=None: None
    )
    code = _run_cli(monkeypatch, ["cli.py", "--ipc-cmd", "status"])
    assert code == 1
    assert "No active audio playback session found" in capsys.readouterr().err
