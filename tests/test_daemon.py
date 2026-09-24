"""Daemon core tests (AT-04): commands, dispatch, provider cache, lifecycle.

The daemon is exercised in-process against an isolated channel (same
pattern as the ownership suite): the dispatch, the play pipeline, and the
orderly shutdown all run through the production code paths. Playback is
faked at the session boundary (AudioSession.play) and at the synthesis
boundary (a stub engine factory), so no audio device or network provider
is needed.
"""

import json
import threading
import time
from unittest import mock

import pytest

import agent_tts.daemon as daemon_mod
import agent_tts.ipc as ipc
from agent_tts import audio
from agent_tts.audio import AudioSession
from agent_tts.daemon import Daemon, ProviderCache, _inject_daemon_fields


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """Isolates the transient channel files into a tmp dir (ownership-suite pattern)."""
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


class StubEngine:
    """Minimal provider stand-in: records calls, returns non-empty audio."""

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, voice=None, rate=None, volume=None, pitch=None, stop_checker=None):
        self.calls.append({"text": text, "voice": voice})
        return b"stub-audio"


def _fake_play(decoded_to_stop: bool = True):
    """Returns an AudioSession.play replacement that blocks until stopped."""

    def play(self, decoded):
        self.state["status"] = "playing"
        self.state["playing"] = True
        deadline = time.monotonic() + 5.0
        while not self.state["stop"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.state["status"] = "stopped"

    return play


def _wait_for_ping(channel, timeout_sec: float = 5.0) -> str:
    deadline = time.monotonic() + timeout_sec
    last = None
    while time.monotonic() < deadline:
        last = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        if last and last.startswith("pong"):
            return last
        time.sleep(0.02)
    return last


def _start_daemon(channel, monkeypatch, target_env=None, **daemon_kwargs) -> Daemon:
    """Builds and runs an in-process daemon with stubbed synthesis/playback."""
    import array
    import types

    import agent_tts.cli as cli_mod

    if target_env is not None:
        monkeypatch.setenv("AGENT_TTS_PLAYBACK", target_env)

    class FakeDecoded:
        sample_rate = 24000
        nchannels = 1
        duration = 0.01
        samples = array.array("h", [0] * 240)

    # cli.miniaudio.decode is faked: the stub engine's bytes are not real
    # audio, and the real session.play is faked one layer above anyway.
    monkeypatch.setattr(cli_mod, "miniaudio", types.SimpleNamespace(decode=lambda data: FakeDecoded()))

    stub_engine = StubEngine()
    cache = ProviderCache(factory=lambda **kwargs: stub_engine)
    d = Daemon(socket_path=channel["sock"], provider_cache=cache, **daemon_kwargs)
    monkeypatch.setattr(AudioSession, "play", _fake_play())
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    reply = _wait_for_ping(channel)
    assert reply and reply.startswith("pong"), f"daemon never answered ping: {reply!r}"
    d._test_thread = thread
    d._test_engine = stub_engine
    return d


def _stop_daemon(d: Daemon) -> None:
    d.request_shutdown()
    thread = getattr(d, "_test_thread", None)
    if thread is not None:
        thread.join(timeout=5.0)


def _play_async(d: Daemon, payload: dict):
    """Runs handle_command('play ...') on a thread; returns (reply_box, thread)."""
    box = []

    def run():
        box.append(d.handle_command(f"play {json.dumps(payload)}"))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return box, t


def _wait_session_registered(d: Daemon, timeout_sec: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with d._lock:
            if d.active_session is not None:
                return
        time.sleep(0.02)


# --- ping (RF-AT-04-3) ---------------------------------------------------------------


def test_ping_answers_version_and_uptime(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        reply = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        from agent_tts import __version__

        assert reply.startswith("pong ")
        assert f"version={__version__}" in reply
        assert "uptime=" in reply
    finally:
        _stop_daemon(d)


def test_ping_uptime_grows(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        first = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        time.sleep(1.2)
        second = ipc.send_ipc_command("ping", socket_path=channel["sock"])
        up1 = int(first.split("uptime=")[1])
        up2 = int(second.split("uptime=")[1])
        assert up2 > up1
    finally:
        _stop_daemon(d)


# --- status (US-AT-04-3) -------------------------------------------------------------


def test_idle_status_reports_idle_uptime_provider_and_target(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        reply = ipc.send_ipc_command("status", socket_path=channel["sock"])
        assert reply.startswith("status=idle ")
        assert "uptime=" in reply
        assert "provider=none" in reply  # nothing synthesized yet
        assert "playback=local" in reply
    finally:
        _stop_daemon(d)


def test_status_during_playback_extends_session_status_with_uptime(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        box, play_thread = _play_async(d, {"text": "hello world", "voice": "elvira"})
        _wait_session_registered(d)

        reply = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            reply = ipc.send_ipc_command("status", socket_path=channel["sock"])
            if reply and "status=playing" in reply:
                break
            time.sleep(0.02)
        # Daemon fields extend the session payload without reshaping it:
        # legacy prefix intact, uptime injected before the free-text field.
        assert reply and reply.startswith("status=playing")
        assert "provider=edge" in reply  # session metadata still present
        assert "uptime=" in reply
        if " text=" in reply:
            head, text_value = reply.split(" text=", 1)
            assert "uptime=" in head  # injected before the free-text field

        # End the playback: the blocking play replies done.
        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        play_thread.join(timeout=5.0)
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


# --- play (RF-AT-04-3, RNF-AT-04-5) ---------------------------------------------------


def test_play_runs_pipeline_and_replies_done(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        reply = d.handle_command(
            "play " + json.dumps({"text": "hola mundo", "voice": "elvira", "rate": "+20%"})
        )
        assert reply == "status=done"
        # The cached engine served the synthesis with the payload options.
        assert d._test_engine.calls == [{"text": "hola mundo", "voice": "elvira"}]
        # After playback the daemon is idle again (session unregistered).
        with d._lock:
            assert d.active_session is None
    finally:
        _stop_daemon(d)


def test_play_error_replies_err_and_keeps_daemon_alive(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        # Broken JSON payload: explicit ERR, daemon still answers pings.
        assert d.handle_command("play {not json").startswith("ERR: invalid play payload")
        # Missing text and file.
        assert d.handle_command("play {}").startswith("ERR: play requires")
        assert ipc.send_ipc_command("ping", socket_path=channel["sock"]).startswith("pong")
    finally:
        _stop_daemon(d)


def test_play_failure_replies_err(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)

    def failing_speech(*args, **kwargs):
        raise RuntimeError("synthesis exploded")

    with mock.patch.object(d._cli, "_play_speech", side_effect=failing_speech):
        reply = d.handle_command("play " + json.dumps({"text": "boom"}))
    assert reply == "ERR: synthesis exploded"
    with d._lock:
        assert d.active_session is None
        assert d._inflight == 0
    _stop_daemon(d)


def test_play_no_play_mode_synthesizes_without_session(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    builds = []
    real_build = d._cli._build_playback_session

    def recording_build(playback, label, **kwargs):
        builds.append((playback, label))
        return real_build(playback, label, **kwargs)

    with mock.patch.object(d._cli, "_build_playback_session", side_effect=recording_build):
        reply = d.handle_command(
            "play " + json.dumps({"text": "solo síntesis", "no_play": True, "provider": "edge"})
        )
    assert reply == "status=done"
    assert builds == []  # no playback session is built in no-play mode
    assert d._test_engine.calls and d._test_engine.calls[0]["text"] == "solo síntesis"
    _stop_daemon(d)


def test_play_with_file_plays_through_session(channel, monkeypatch, tmp_path):
    import array
    import struct
    import wave

    wav_path = tmp_path / "clip.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(struct.pack("<h", 0) * 2400)  # 0.1 s silence

    d = _start_daemon(channel, monkeypatch)
    played = {}

    def recording_play(self, decoded):
        played["frames"] = len(decoded.samples)
        played["sample_rate"] = decoded.sample_rate
        played["nchannels"] = decoded.nchannels
        self.state["status"] = "playing"

    monkeypatch.setattr(AudioSession, "play", recording_play)
    try:
        reply = d.handle_command("play " + json.dumps({"file": str(wav_path)}))
        assert reply == "status=done"
        # miniaudio.decode converts to its standard format (44.1 kHz
        # stereo): assert the 0.1 s duration survived, not a frame count.
        duration = played["frames"] / float(played["sample_rate"] * played["nchannels"])
        assert duration == pytest.approx(0.1, abs=0.01)
    finally:
        monkeypatch.setattr(AudioSession, "play", _fake_play())
        _stop_daemon(d)


# --- RF-AT-04-6: playback target at startup and per request --------------------------


def test_playback_target_resolved_at_startup_from_env(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch, target_env="winhost")
    builds = []

    def fake_build(playback, label, **kwargs):
        builds.append(playback)
        session = AudioSession(label=label)
        return session

    with mock.patch.object(d._cli, "_build_playback_session", side_effect=fake_build):
        # No explicit target in the payload: the startup target applies.
        d.handle_command("play " + json.dumps({"text": "uno"}))
        assert builds == ["winhost"]
        # An event can force the target (RF-AT-04-6).
        d.handle_command("play " + json.dumps({"text": "dos", "playback": "local"}))
        assert builds == ["winhost", "local"]
    _stop_daemon(d)


def test_invalid_requested_target_fails_open_to_local(channel, monkeypatch, capsys):
    d = Daemon(socket_path=channel["sock"])
    assert d._request_target("bogus-target") == "local"
    assert "falling back to local playback" in capsys.readouterr().err


# --- Existing commands over the active session (RF-AT-04-2) ---------------------------


def test_existing_commands_delegate_to_active_session(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        box, play_thread = _play_async(d, {"text": "texto largo"})
        _wait_session_registered(d)
        # pause delegates and returns the session status (plus daemon uptime).
        reply = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            reply = ipc.send_ipc_command("pause", socket_path=channel["sock"])
            if reply and "status=paused" in reply:
                break
            time.sleep(0.02)
        assert reply and "status=paused" in reply and "uptime=" in reply

        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        play_thread.join(timeout=5.0)
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


def test_idle_commands_without_session_answer_clearly(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        sock = channel["sock"]
        assert ipc.send_ipc_command("pause", socket_path=sock) == "ERR: no active playback session"
        assert ipc.send_ipc_command("seek +10", socket_path=sock) == "ERR: no active playback session"
        # stop is idempotent silence: the user's goal already holds.
        assert ipc.send_ipc_command("stop", socket_path=sock) == "status=stopped"
    finally:
        _stop_daemon(d)


# --- shutdown (RF-AT-04-3) and channel cleanup (RF-AT-04-1) ---------------------------


def test_shutdown_command_stops_daemon_and_frees_channel(channel, monkeypatch):
    import os

    d = _start_daemon(channel, monkeypatch)
    assert os.path.exists(channel["sock"])
    assert os.path.exists(channel["pid"])
    with open(channel["pid"]) as f:
        assert f.read() == str(__import__("os").getpid())  # PID_FILE: informational marker (RF-AT-04-1)

    reply = ipc.send_ipc_command("shutdown", socket_path=channel["sock"])
    assert reply and reply.startswith("ok")

    d._test_thread.join(timeout=5.0)
    # Orderly exit frees the channel: socket and pid markers are gone.
    assert not os.path.exists(channel["sock"])
    assert not os.path.exists(channel["pid"])
    assert not os.path.exists(channel["lock"])


# --- Provider cache (RNF-AT-04-5) -----------------------------------------------------


def test_provider_cache_reuses_instance_per_configuration():
    built = []

    def factory(**kwargs):
        engine = StubEngine()
        built.append(kwargs)
        return engine

    cache = ProviderCache(factory=factory)
    a = cache.get(provider_name="edge")
    b = cache.get(provider_name="edge")
    assert a is b
    assert built == [{"provider_name": "edge", "openai_key": None, "openai_base_url": None,
                      "openai_model": None, "eleven_key": None, "eleven_model": None,
                      "piper_model": None}]

    # A different configuration gets its own instance without evicting.
    c = cache.get(provider_name="openai", openai_key="sk-x")
    assert c is not a
    assert cache.last_name == "openai"
    assert cache.get(provider_name="edge") is a


def test_daemon_reuses_warm_engine_across_plays(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        d.handle_command("play " + json.dumps({"text": "primera"}))
        d.handle_command("play " + json.dumps({"text": "segunda"}))
        # One stub engine instance served both plays (cache hit).
        assert len(d.providers._instances) == 1
        assert [c["text"] for c in d._test_engine.calls] == ["primera", "segunda"]
    finally:
        _stop_daemon(d)


# --- Reply field injection -------------------------------------------------------------


def test_inject_daemon_fields_keeps_free_text_field_last():
    base = "status=playing pos=1.00 total=9.00 text=Hola mundo con espacios"
    out = _inject_daemon_fields(base, " uptime=42")
    assert out == "status=playing pos=1.00 total=9.00 uptime=42 text=Hola mundo con espacios"


def test_inject_daemon_fields_appends_without_free_text():
    assert _inject_daemon_fields("status=stopped", " uptime=7") == "status=stopped uptime=7"
