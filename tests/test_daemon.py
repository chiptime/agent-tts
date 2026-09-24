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


# --- CONF-1: large delegated payloads over the real IPC framing ------------------------


def test_large_play_payload_round_trips_through_the_channel(channel, monkeypatch):
    """A >= 1 MB delegated play crosses the socket complete, not truncated."""
    d = _start_daemon(channel, monkeypatch)
    try:
        from agent_tts.daemon import send_play

        big_text = "a" * (1024 * 1024)
        reply = send_play({"text": big_text, "no_play": True}, socket_path=channel["sock"])
        assert reply == "status=done"
        # The daemon synthesized the FULL text: framing did not drop bytes.
        assert d._test_engine.calls and d._test_engine.calls[0]["text"] == big_text
    finally:
        _stop_daemon(d)


def test_oversized_play_payload_beyond_hard_cap_fails_clearly(channel, monkeypatch):
    """Beyond the documented hard cap the reply is a clear error, never a
    silent truncation that surfaces as an invalid-JSON parse failure."""
    d = _start_daemon(channel, monkeypatch)
    try:
        from agent_tts.daemon import send_play

        from agent_tts.ipc import MAX_COMMAND_BYTES

        runaway = "b" * (MAX_COMMAND_BYTES + 2048)
        reply = send_play({"text": runaway, "no_play": True}, socket_path=channel["sock"])
        assert reply is not None and reply.startswith("ERR: command too large"), reply
    finally:
        _stop_daemon(d)


# --- CONF-2: concurrent play is a clear error, never a silent supersede ----------------


def test_second_concurrent_play_is_rejected_and_first_stays_controllable(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        box, play_thread = _play_async(d, {"text": "primera"})
        _wait_session_registered(d)

        # A second play while audio is active: deterministic clear error,
        # never a silent supersede (queueing arrives in BLOQUE 1.3).
        box2, play_thread2 = _play_async(d, {"text": "segunda"})
        play_thread2.join(timeout=2.0)
        assert not play_thread2.is_alive(), "second play must reply, not block or displace"
        assert box2 == ["ERR: playback already in progress"]

        # The FIRST playback is still the controllable one.
        reply = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            reply = ipc.send_ipc_command("pause", socket_path=channel["sock"])
            if reply and "status=paused" in reply:
                break
            time.sleep(0.02)
        assert reply and "status=paused" in reply

        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        play_thread.join(timeout=5.0)
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


def test_no_play_request_does_not_displace_active_playback(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        box, play_thread = _play_async(d, {"text": "audio activo"})
        _wait_session_registered(d)

        # A synthesis-only request while audio plays must not orphan the
        # active session's control: active_session is not clobbered.
        assert d.handle_command("play " + json.dumps({"text": "solo texto", "no_play": True})) == "status=done"

        # stop still controls the ORIGINAL playback; it cannot lie.
        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        play_thread.join(timeout=2.0)
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


def test_shutdown_stops_all_inflight_sessions(channel):
    """Shutdown stops every tracked in-flight session, not just active_session."""
    d = Daemon(socket_path=channel["sock"])
    stopped = []

    class FakeSession:
        def stop(self):
            stopped.append(self)

    a, b = FakeSession(), FakeSession()
    with d._lock:
        d._sessions.update([a, b])
        d.active_session = a
    d._shutdown()
    assert set(stopped) == {a, b}


def test_play_registering_during_shutdown_is_refused_not_orphaned(channel):
    """RS-1: a play registering while _shutdown drains cannot escape the stop.

    _shutdown snapshots _sessions once: a play whose session registers
    after the snapshot but before the drain check used to run to
    completion without ever receiving stop. Registration is now refused
    with a deterministic error once shutdown has started.
    """
    import types

    d = Daemon(
        socket_path=channel["sock"],
        provider_cache=ProviderCache(factory=lambda **kw: StubEngine()),
    )
    snapshot_taken = threading.Event()
    stopped = []

    class Early:
        def stop(self):
            # Signals that _shutdown has snapshotted and is stopping us.
            snapshot_taken.set()
            stopped.append(self)

    class Late:
        def __init__(self):
            self.state = {"stop": False}

        def stop(self):
            stopped.append(self)

    with d._lock:
        d._sessions.add(Early())

    late = Late()
    played = []

    def fake_build(target, label, **kw):
        # Registers the session only AFTER _shutdown has snapshotted, so
        # the registration races the drain exactly as in the defect.
        snapshot_taken.wait(timeout=5.0)
        return late

    async def fake_play_speech(session, text, **kw):
        deadline = time.monotonic() + 2.0
        while not session.state.get("stop") and time.monotonic() < deadline:
            time.sleep(0.02)
        played.append(bool(session.state.get("stop")))

    d._cli = types.SimpleNamespace(
        _build_playback_session=fake_build, _play_speech=fake_play_speech
    )

    replies = []

    def run_play():
        replies.append(d.handle_command("play " + json.dumps({"text": "tarde"})))

    play_thread = threading.Thread(target=run_play, daemon=True)
    play_thread.start()
    d._shutdown()
    play_thread.join(timeout=5.0)

    # The late play gets a deterministic refusal instead of running
    # unsupervised through the shutdown drain.
    assert replies == ["ERR: daemon shutting down"]
    assert played == []
    assert len(stopped) == 1  # only the pre-snapshot session existed


def test_shutdown_command_stops_inflight_playback(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    box, play_thread = _play_async(d, {"text": "larga"})
    _wait_session_registered(d)
    try:
        ipc.send_ipc_command("shutdown", socket_path=channel["sock"])
        d._test_thread.join(timeout=5.0)
        play_thread.join(timeout=5.0)
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


# --- SOS-1/B5: the request's provider/voice/winhost endpoint reach the session ---------


def test_play_status_reports_requested_provider_and_voice(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    try:
        box, play_thread = _play_async(
            d, {"text": "hola", "provider": "piper", "voice": "es-ES-AlvaroNeural"}
        )
        _wait_session_registered(d)
        reply = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            reply = ipc.send_ipc_command("status", socket_path=channel["sock"])
            if reply and "status=playing" in reply:
                break
            time.sleep(0.02)
        # The session metadata mirrors what the request asked for, not the
        # daemon's inherited environment/defaults.
        assert reply and "provider=piper" in reply, reply
        assert "voice=es-ES-AlvaroNeural" in reply, reply

        ipc.send_ipc_command("stop", socket_path=channel["sock"])
        play_thread.join(timeout=5.0)
        assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


@pytest.mark.parametrize("target", ["wsl-ps", "winhost"])
def test_play_status_reports_requested_provider_and_voice_for_remote_targets(
    channel, monkeypatch, target
):
    """RS-2 (SOS-1/B5): daemon status honors provider/voice for remote targets too.

    The pipeline is faked at _play_speech: what is under test is the
    session metadata the daemon constructs (through the real
    _build_playback_session) and surfaces over IPC.
    """
    import agent_tts.cli as cli_mod

    monkeypatch.setattr(cli_mod, "is_wsl_ps_available", lambda: True)

    async def fake_play_speech(session, text, **kw):
        deadline = time.monotonic() + 5.0
        while not session.state.get("stop") and time.monotonic() < deadline:
            time.sleep(0.02)

    d = _start_daemon(channel, monkeypatch)
    try:
        with mock.patch.object(d._cli, "_play_speech", fake_play_speech):
            box, play_thread = _play_async(
                d,
                {
                    "text": "remoto",
                    "playback": target,
                    "provider": "piper",
                    "voice": "es-ES-AlvaroNeural",
                },
            )
            _wait_session_registered(d)
            reply = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                reply = ipc.send_ipc_command("status", socket_path=channel["sock"])
                if reply and "status=" in reply and "ERR" not in reply:
                    break
                time.sleep(0.02)
            assert reply and "provider=piper" in reply, reply
            assert "voice=es-ES-AlvaroNeural" in reply, reply

            ipc.send_ipc_command("stop", socket_path=channel["sock"])
            play_thread.join(timeout=5.0)
            assert box == ["status=stopped"]
    finally:
        _stop_daemon(d)


def test_build_playback_session_forwards_provider_voice_to_remote_sessions(monkeypatch):
    """RS-2: the requested provider/voice reach the remote session objects."""
    import agent_tts.cli as cli_mod
    from agent_tts.powershell_playback import PowershellSession
    from agent_tts.winhost_client import RemoteAudioSession

    for var in ("AGENT_TTS_PROVIDER", "AGENT_TTS_VOICE", "TTS_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli_mod, "is_wsl_ps_available", lambda: True)

    ps = cli_mod._build_playback_session(
        "wsl-ps", "L", provider="piper", voice="es-ES-AlvaroNeural"
    )
    assert isinstance(ps, PowershellSession)
    assert ps.provider == "piper"
    assert ps.voice == "es-ES-AlvaroNeural"

    remote = cli_mod._build_playback_session(
        "winhost", "L", provider="elevenlabs", voice="rachel"
    )
    assert isinstance(remote, RemoteAudioSession)
    assert remote.provider == "elevenlabs"
    assert remote.voice == "rachel"


def test_play_winhost_endpoint_crosses_ipc_boundary(channel, monkeypatch):
    d = _start_daemon(channel, monkeypatch)
    builds = []
    real_build = d._cli._build_playback_session

    def recording_build(playback, label, **kwargs):
        builds.append((playback, kwargs))
        return real_build(playback, label, **kwargs)

    try:
        with mock.patch.object(d._cli, "_build_playback_session", side_effect=recording_build):
            d.handle_command(
                "play "
                + json.dumps(
                    {
                        "text": "remoto",
                        "playback": "winhost",
                        "winhost_host": "192.0.2.10",
                        "winhost_port": 7799,
                        "no_play": False,
                    }
                )
            )
            d.handle_command(
                "play " + json.dumps({"text": "sin endpoint", "playback": "local"})
            )
        # The daemon passed the client's winhost endpoint into session
        # construction as an env overlay the remote session resolves
        # against (candidate_hosts honors the explicit host).
        winhost_kwargs = builds[0][1]
        assert "AGENT_TTS_WINHOST_HOST" in winhost_kwargs["env"]
        assert winhost_kwargs["env"]["AGENT_TTS_WINHOST_HOST"] == "192.0.2.10"
        assert winhost_kwargs["env"]["AGENT_TTS_WINHOST_PORT"] == "7799"
        from agent_tts.playback_target import candidate_hosts

        assert candidate_hosts(winhost_kwargs["env"]) == ["192.0.2.10"]
        # Without endpoint values in the payload there is no overlay: the
        # daemon's own environment resolves as before.
        assert builds[1][1]["env"] is None
    finally:
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
