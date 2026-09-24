"""Daemon idle-timeout lifecycle tests (RF-AT-04-7).

Policies under test: a daemon started with an idle timeout ends orderly
after the configured period without requests, freeing the channel per the
BLOQUE 1.1 ownership rules; requests reset the clock; an active playback
is never interrupted by the idle exit; a daemon without a timeout (the
explicit-start default) stays alive through idle windows. Both modes are
configurable via --idle-timeout / AGENT_TTS_IDLE_TIMEOUT.
"""

import json
import os
import threading
import time
from unittest import mock

import pytest

import agent_tts.daemon as daemon_mod
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.daemon import Daemon, ProviderCache, autostart_idle_timeout_sec


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


def _fake_blocking_play(self, decoded):
    self.state["status"] = "playing"
    deadline = time.monotonic() + 10.0
    while not self.state["stop"] and time.monotonic() < deadline:
        time.sleep(0.02)
    self.state["status"] = "stopped"


def _run_in_thread(daemon_obj):
    thread = threading.Thread(target=daemon_obj.run, daemon=True)
    thread.start()
    return thread


def _wait_ping(channel, timeout_sec=5.0):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        reply = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        if reply and reply.startswith("pong"):
            return reply
        time.sleep(0.02)
    return None


def _make_daemon(channel, monkeypatch, **kwargs) -> Daemon:
    import array
    import types

    import agent_tts.cli as cli_mod

    class FakeDecoded:
        sample_rate = 24000
        nchannels = 1
        duration = 0.01
        samples = array.array("h", [0] * 240)

    monkeypatch.setattr(cli_mod, "miniaudio", types.SimpleNamespace(decode=lambda data: FakeDecoded()))
    monkeypatch.setattr(AudioSession, "play", _fake_blocking_play)
    return Daemon(socket_path=channel["sock"], provider_cache=ProviderCache(factory=lambda **kw: _StubEngine()), **kwargs)


# --- Configuration defaults -----------------------------------------------------------


def test_autostart_default_is_30_minutes():
    assert autostart_idle_timeout_sec() == 1800.0


def test_autostart_timeout_env_overrides_and_zero_disables(monkeypatch):
    monkeypatch.setenv("AGENT_TTS_IDLE_TIMEOUT", "90")
    assert autostart_idle_timeout_sec() == 90.0
    monkeypatch.setenv("AGENT_TTS_IDLE_TIMEOUT", "0")
    assert autostart_idle_timeout_sec() is None


def test_autostart_timeout_invalid_values_keep_default(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_TTS_IDLE_TIMEOUT", "not-a-number")
    assert autostart_idle_timeout_sec() == 1800.0
    monkeypatch.setenv("AGENT_TTS_IDLE_TIMEOUT", "-5")
    assert autostart_idle_timeout_sec() == 1800.0
    assert capsys.readouterr().err.count("Ignoring") == 2


# --- Idle exit ------------------------------------------------------------------------


def test_idle_daemon_exits_orderly_and_frees_channel(channel, monkeypatch):
    d = _make_daemon(channel, monkeypatch, idle_timeout_sec=0.5)
    thread = _run_in_thread(d)
    try:
        assert _wait_ping(channel) is not None
        assert os.path.exists(channel["sock"])
        assert os.path.exists(channel["pid"])
    finally:
        pass
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    # Orderly idle exit frees the channel per BLOQUE 1.1 ownership rules.
    assert not os.path.exists(channel["sock"])
    assert not os.path.exists(channel["pid"])
    assert not os.path.exists(channel["lock"])


def test_requests_reset_the_idle_clock(channel, monkeypatch):
    d = _make_daemon(channel, monkeypatch, idle_timeout_sec=1.0)
    thread = _run_in_thread(d)
    assert _wait_ping(channel) is not None

    # Pings keep the daemon alive well past the nominal idle window.
    for _ in range(6):
        time.sleep(0.4)
        assert ipc.send_ipc_command("ping", socket_path=channel["sock"]).startswith("pong")
        assert thread.is_alive()

    # Stop pinging: the exit happens after the timeout counted from the
    # LAST request, not from daemon start.
    last_ping_at = time.monotonic()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert time.monotonic() - last_ping_at >= 1.0
    assert not os.path.exists(channel["sock"])


def test_active_playback_blocks_idle_exit(channel, monkeypatch):
    d = _make_daemon(channel, monkeypatch, idle_timeout_sec=0.4)
    thread = _run_in_thread(d)
    assert _wait_ping(channel) is not None

    box = []

    def run_play():
        box.append(d.handle_command("play " + json.dumps({"text": "largo"})))

    play_thread = threading.Thread(target=run_play, daemon=True)
    play_thread.start()

    # Wait until the playback session is registered, then let the nominal
    # idle window lapse twice: the daemon must stay alive and serving.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with d._lock:
            if d.active_session is not None:
                break
        time.sleep(0.02)
    time.sleep(1.0)
    assert thread.is_alive()
    assert ipc.send_ipc_command("ping", socket_path=channel["sock"]).startswith("pong")

    # Playback ends: the idle exit then takes the daemon down cleanly.
    ipc.send_ipc_command("stop", socket_path=channel["sock"])
    play_thread.join(timeout=5.0)
    assert box == ["status=stopped"]
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert not os.path.exists(channel["sock"])


def test_explicit_daemon_without_timeout_survives_idle_window(channel, monkeypatch):
    d = _make_daemon(channel, monkeypatch)  # no idle_timeout_sec: explicit start
    thread = _run_in_thread(d)
    assert _wait_ping(channel) is not None
    # An idle window that would kill a timeout-carrying daemon (0.3s class)
    # leaves the explicit daemon alive.
    time.sleep(1.2)
    assert thread.is_alive()
    assert ipc.send_ipc_command("ping", socket_path=channel["sock"]).startswith("pong")
    d.request_shutdown()
    thread.join(timeout=5.0)
    assert not os.path.exists(channel["sock"])


# --- CLI flag wiring ------------------------------------------------------------------


def test_daemon_main_wires_idle_timeout_flag():
    """--idle-timeout/--implicit map to the Daemon constructor (RF-AT-04-7)."""
    with mock.patch.object(daemon_mod, "run_daemon", return_value=0) as rd:
        assert daemon_mod.main(["--idle-timeout", "0.5"]) == 0
        assert rd.call_args.kwargs["idle_timeout_sec"] == 0.5
        assert rd.call_args.kwargs["implicit"] is False
    with mock.patch.object(daemon_mod, "run_daemon", return_value=0) as rd:
        assert daemon_mod.main(["--implicit", "--idle-timeout", "2"]) == 0
        assert rd.call_args.kwargs["implicit"] is True
