"""Winhost protocol tests: server, client transport, and RemoteAudioSession.

All networking runs over loopback TCP with ephemeral ports; the audio device
is replaced by a fake device factory that eagerly drains the callback
generator and records the rendered chunks.
"""

import socket
import json
import threading
import time

import pytest

import agent_tts.winhost_client as wc
import agent_tts.winhost as wh
from agent_tts import ducking
from agent_tts.constants import DEFAULT_VOICE
from agent_tts.winhost import WinhostServer
from agent_tts.winhost_client import (
    RemoteAudioSession,
    WinhostUnavailable,
    open_stream,
    send_control,
)

ORIGINAL_QUERY_ACTIVITY = wc.query_activity


def test_classify_active_communications_session():
    render = [{"state": 1, "system_sounds": False, "endpoint_role": "communications"}]
    assert wh.classify_sessions(render, []) == {"communication_active": True, "capture_active": False}


@pytest.mark.parametrize("render,capture,expected", [
    ([], [], (False, False)),
    ([{"state": 0, "system_sounds": False}], [], (False, False)),
    ([{"state": 2, "system_sounds": False}], [], (False, False)),
    ([{"state": 1, "system_sounds": True}], [], (False, False)),
    ([{"state": 1, "system_sounds": False, "endpoint_role": "multimedia"}], [], (False, False)),
    ([{"state": 1, "system_sounds": False, "endpoint_role": "console"}], [], (False, False)),
    ([], [{"state": 1, "system_sounds": False}], (False, True)),
    ([], [{"state": 0, "system_sounds": False}], (False, False)),
    ([], [{"state": 2, "system_sounds": False}], (False, False)),
    ([], [{"state": 1, "system_sounds": True}], (False, True)),
    ([{"state": 1, "system_sounds": False}], [{"state": 1, "system_sounds": False}], (True, True)),
    ([{"state": 0, "system_sounds": False}, {"state": 1, "system_sounds": False}], [], (True, False)),
], ids=["empty", "inactive", "expired", "system-sounds", "multimedia", "console-media",
        "capture-only", "capture-inactive", "capture-expired", "system-owned-capture", "both", "mixed"])
def test_classify_sessions_matrix(render, capture, expected):
    assert wh.classify_sessions(render, capture) == {
        "communication_active": expected[0], "capture_active": expected[1],
    }


@pytest.mark.parametrize("malformed", [
    None, "sessions", {}, [None], [{}], [{"state": 1}],
    [{"state": True, "system_sounds": False}],
    [{"state": "active", "system_sounds": False}],
    [{"state": 3, "system_sounds": False}],
    [{"state": 1, "system_sounds": "false"}],
    [{"state": 1, "system_sounds": False, "endpoint_role": "unknown"}],
    [{"state": 1, "system_sounds": False, "process_id": True}],
    [{"state": 1, "system_sounds": False, "process_id": -1}],
    [{"state": 1, "system_sounds": False, "process_id": None}],
])
@pytest.mark.parametrize("side", ["render", "capture"])
def test_classify_malformed_snapshot_fails_open(malformed, side):
    active = [{"state": 1, "system_sounds": False}]
    args = (malformed, active) if side == "render" else (active, malformed)
    assert wh.classify_sessions(*args) == {"communication_active": False, "capture_active": False}


def test_classify_checks_late_malformed_records_before_positive():
    render = [{"state": 1, "system_sounds": False}, {"state": 0}]
    assert wh.classify_sessions(render, []) == {"communication_active": False, "capture_active": False}


def test_classify_excludes_own_render_stream_without_mutating_inputs():
    render = [{"state": 1, "system_sounds": False, "process_id": 321}]
    before = [record.copy() for record in render]
    assert wh.classify_sessions(render, [], own_process_id=321)["communication_active"] is False
    assert wh.classify_sessions(render, [], own_process_id=123)["communication_active"] is True
    assert render == before


def test_classify_media_on_shared_communications_endpoint_is_heuristic():
    # The endpoint, not an unsupported per-application media-role API, is the
    # selected contract. Media routed to that same endpoint can also count.
    render = [{"state": 1, "system_sounds": False, "endpoint_role": "communications", "media_role": "music"}]
    assert wh.classify_sessions(render, [])["communication_active"] is True


@pytest.mark.parametrize("communication,capture", [(False, False), (True, False), (False, True), (True, True)])
def test_activity_query_v1_roundtrip(winserver, monkeypatch, communication, capture):
    _, host, port, _ = winserver
    patch_target(monkeypatch, host, port)
    state = {"communication_active": communication, "capture_active": capture}
    monkeypatch.setattr(wh, "detect_host_activity", lambda: state)
    assert wc.query_activity() == state


@pytest.fixture
def duck_warnings(monkeypatch):
    monkeypatch.setattr(ducking, "_warned", set())


def test_activity_reply_is_one_json_line_then_close(winserver, monkeypatch):
    _, host, port, _ = winserver
    monkeypatch.setattr(wh, "detect_host_activity", lambda: {"communication_active": True, "capture_active": False})
    with socket.create_connection((host, port), timeout=2) as sock:
        sock.sendall(b'{"v":1,"cmd":"activity"}\n')
        reply = b""
        while b"\n" not in reply:
            reply += sock.recv(4096)
        assert json.loads(reply) == {"ok": True, "communication_active": True, "capture_active": False}
        assert reply.endswith(b"\n")
        assert sock.recv(1) == b""


@pytest.mark.parametrize("reply", [
    b"garbage\n", b"[]\n", b"{}\n", b'\xff\n', b"",
    b'{"ok":true,"communication_active":true}\n',
    b'{"ok":false,"communication_active":true,"capture_active":true}\n',
    b'{"ok":true,"communication_active":"false","capture_active":true}\n',
    b'{"ok":true,"communication_active":1,"capture_active":false}\n',
    b"x" * 8193, b'{"ok":true}',
])
def test_query_activity_bad_replies_fail_open(monkeypatch, duck_warnings, capsys, reply):
    sockets = []

    class Sock:
        def __init__(self):
            self.reply = reply
            self.closed = False
            sockets.append(self)

        def sendall(self, payload):
            assert json.loads(payload) == {"v": 1, "cmd": "activity"}

        def shutdown(self, how):
            assert how == socket.SHUT_WR

        def settimeout(self, seconds):
            assert 0 < seconds <= wc.CONNECT_TIMEOUT_SEC

        def recv(self, size):
            data, self.reply = self.reply[:size], self.reply[size:]
            return data

        def close(self):
            self.closed = True

    monkeypatch.setattr(wc.socket, "create_connection", lambda *args, **kwargs: Sock())
    monkeypatch.setattr(wc, "candidate_hosts", lambda env=None: ["mock"])
    for _ in range(2):
        assert wc.query_activity(env={}) == {"communication_active": False, "capture_active": False}
    assert all(sock.closed for sock in sockets)
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.parametrize("error", [ConnectionRefusedError(), TimeoutError()])
def test_query_activity_unreachable_fails_open(monkeypatch, duck_warnings, capsys, error):
    def connect(*args, **kwargs):
        raise error

    monkeypatch.setattr(wc.socket, "create_connection", connect)
    env = {"AGENT_TTS_WINHOST_HOST": "127.0.0.1", "AGENT_TTS_WINHOST_PORT": "7717"}
    assert wc.query_activity(env) == {"communication_active": False, "capture_active": False}
    assert wc.query_activity(env) == {"communication_active": False, "capture_active": False}
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_host_detector_nonwindows_never_invokes_com(monkeypatch, duck_warnings, capsys):
    monkeypatch.setattr(wh.sys, "platform", "linux")
    monkeypatch.setattr(wh, "_enumerate_wasapi_sessions", lambda: pytest.fail("COM on Linux"))
    assert wh.detect_host_activity() == {"communication_active": False, "capture_active": False}
    assert wh.detect_host_activity() == {"communication_active": False, "capture_active": False}
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_native_adapter_guards_before_ctypes_import(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in ("ctypes", "uuid"):
            pytest.fail("native dependencies imported on non-Windows")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(wh.sys, "platform", "linux")
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(OSError, match="Windows-only"):
        wh._enumerate_wasapi_sessions()


@pytest.mark.parametrize("error", [OSError("COM unavailable"), ValueError("bad session"), RuntimeError("COM getter failed")])
def test_host_detector_windows_enumeration_errors_fail_open(monkeypatch, duck_warnings, capsys, error):
    calls = []

    def enumerate_sessions():
        calls.append(True)
        raise error

    monkeypatch.setattr(wh.sys, "platform", "win32")
    monkeypatch.setattr(wh, "_enumerate_wasapi_sessions", enumerate_sessions)
    assert wh.detect_host_activity() == {"communication_active": False, "capture_active": False}
    assert wh.detect_host_activity() == {"communication_active": False, "capture_active": False}
    assert calls == [True, True]
    assert len(capsys.readouterr().err.splitlines()) == 1


@pytest.mark.parametrize("communication,capture", [(False, False), (True, False), (False, True), (True, True)])
def test_host_detector_adapter_classifies_synthetic_sessions(monkeypatch, duck_warnings, capsys, communication, capture):
    # Only the adapter boundary is mocked: NO native COM method executes here.
    snapshot = ([{"state": int(communication), "system_sounds": False}],
                [{"state": int(capture), "system_sounds": False}])
    monkeypatch.setattr(wh.sys, "platform", "win32")
    monkeypatch.setattr(wh, "_enumerate_wasapi_sessions", lambda: snapshot)
    assert wh.detect_host_activity() == {"communication_active": communication, "capture_active": capture}
    assert capsys.readouterr().err == ""


def test_host_detector_malformed_enumeration_fails_open(monkeypatch, duck_warnings, capsys):
    monkeypatch.setattr(wh.sys, "platform", "win32")
    monkeypatch.setattr(wh, "_enumerate_wasapi_sessions", lambda: ([{"state": 1, "system_sounds": False}], [{}]))
    assert wh.detect_host_activity() == {"communication_active": False, "capture_active": False}
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_host_detector_excludes_winhost_process(monkeypatch):
    monkeypatch.setattr(wh.sys, "platform", "win32")
    monkeypatch.setattr(wh.os, "getpid", lambda: 321)
    monkeypatch.setattr(wh, "_enumerate_wasapi_sessions", lambda: (
        [{"state": 1, "system_sounds": False, "process_id": 321}], []
    ))
    assert wh.detect_host_activity() == {"communication_active": False, "capture_active": False}


def test_gain_db_v1_header_and_server_attenuation(winserver, monkeypatch):
    import struct

    server, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    raw = struct.pack("<4h", 12000, -12000, 32767, -32768)
    stream = open_stream(24000, 1, 2, gain_db=-12)
    stream.send_pcm(raw)
    stream.finish()
    assert server.last_header["v"] == 1
    assert server.last_header["gain_db"] == -12
    gain = ducking.gain_linear(-12)
    assert rendered_audio(sink) == struct.pack("<4h", *(int(sample * gain) for sample in (12000, -12000, 32767, -32768)))


def test_playstream_live_gain_release_and_pause_controls():
    import struct

    raw = struct.pack("<2h", 12000, -12000)
    header = {"rate": 24000, "channels": 1, "gain_db": -12}
    stream = wh.PlayStream(None, header, None, initial_data=raw * 3)
    generator = stream._pcm_generator()
    next(generator)
    assert generator.send(2) != raw
    stream.pause()
    assert generator.send(2) == bytes(4)
    stream.set_gain_db(0)
    stream.resume()
    assert generator.send(2) == raw
    stream.stop()
    assert generator.send(2) == b""


@pytest.mark.parametrize("invalid", ["bad", True, 12, float("nan"), float("inf"), -(10 ** 1000), None],
                         ids=["string", "bool", "amplification", "nan", "infinity", "oversized-number", "null"])
def test_invalid_gain_db_fails_open_to_unity(duck_warnings, capsys, invalid):
    raw = b"\x01\x02\x03\x04"
    stream = wh.PlayStream(None, {"rate": 24000, "channels": 1, "gain_db": invalid}, None, initial_data=raw)
    generator = stream._pcm_generator()
    next(generator)
    assert generator.send(2) == raw
    assert "gain_db" in capsys.readouterr().err


@pytest.fixture
def remote_ducking(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wc.time, "monotonic", lambda: clock[0])
    activity = [{"communication_active": True, "capture_active": False}]
    queries, headers, controls, pcm = [], [], [], []

    def query(env=None):
        queries.append(clock[0])
        return activity[-1].copy()

    class Stream:
        host = "mock-host"
        port = 7717

        def send_pcm(self, chunk):
            pcm.append(chunk)

        def finish(self, on_wait=None):
            if on_wait:
                clock[0] = 2.0
                activity.append({"communication_active": False, "capture_active": False})
                on_wait()

        def abort(self):
            pass

    def open_mock(*args, **kwargs):
        headers.append(kwargs)
        return Stream()

    monkeypatch.setattr(wc, "query_activity", query)
    monkeypatch.setattr(wc, "open_stream", open_mock)
    monkeypatch.setattr(wc, "send_control", lambda cmd, **kwargs: controls.append((cmd, kwargs)) or True)
    return clock, activity, queries, headers, controls, pcm


@pytest.mark.parametrize("target", ["winhost", "windows"])
@pytest.mark.parametrize("policy", ["duck", "pause", "ignore"])
def test_remote_policy_header_and_release(remote_ducking, target, policy):
    clock, activity, queries, headers, controls, pcm = remote_ducking
    session = RemoteAudioSession(target=target, env={f"AGENT_TTS_DUCK_POLICY_{target.upper()}": policy})
    decoded = make_decoded_pcm(10)
    session.prepare_pcm(decoded)
    session.play(decoded)
    assert pcm == [decoded.samples.tobytes()]  # attenuation is server-owned
    if policy == "duck":
        assert headers[0]["gain_db"] == -12.0
        assert ("gain", {"env": {"AGENT_TTS_WINHOST_HOST": "mock-host", "AGENT_TTS_WINHOST_PORT": "7717"},
                         "gain_db": 0.0}) in controls
        assert queries == [0, 2]
    elif policy == "pause":
        assert headers[0]["paused"] is True
        assert [cmd for cmd, _ in controls] == ["pause", "resume"]
        assert queries == [0, 2]
    else:
        assert "gain_db" not in headers[0] and "paused" not in headers[0]
        assert queries == [] and controls == []
    assert session.state["status"] == "stopped"
    clock[0] = 100
    session._check_remote_ducking()
    assert len(queries) == (0 if policy == "ignore" else 2)


def test_remote_activity_checks_only_during_live_playback(remote_ducking):
    clock, _, queries, _, _, _ = remote_ducking
    session = RemoteAudioSession(env={"AGENT_TTS_DUCK_POLICY": "duck"})
    session.prepare_pcm(make_decoded_pcm(10))
    clock[0] = 10
    session._check_remote_ducking()
    assert queries == [0]
    session.stop()
    session._check_remote_ducking()
    assert queries == [0]


def test_gain_control_roundtrip_does_not_replace_active_stream(winserver, monkeypatch):
    server, host, port, _ = winserver
    patch_target(monkeypatch, host, port)
    active = wh.PlayStream(None, {"rate": 24000, "channels": 1}, None, initial_data=b"\x01\x02" * 4)
    with server._active_lock:
        server._active = active
    assert send_control("gain", gain_db=-12)
    assert wait_for(lambda: active.gain == ducking.gain_linear(-12))
    assert send_control("gain", gain_db=0)
    assert wait_for(lambda: active.gain == 1.0)
    assert server._active is active


def test_remote_manual_pause_is_not_resumed_by_activity_release(remote_ducking):
    clock, activity, queries, _, controls, _ = remote_ducking
    session = RemoteAudioSession(env={"AGENT_TTS_DUCK_POLICY": "pause"})
    session.prepare_pcm(make_decoded_pcm(10))
    session.state["status"] = "playing"
    session._duck_running = True
    session._apply_remote_activity(activity[-1])
    assert session.state["status"] == "paused"
    session.handle_ipc_command("pause")
    activity.append({"communication_active": False, "capture_active": False})
    clock[0] = 2
    session._check_remote_ducking()
    assert session.state["status"] == "paused"
    assert [cmd for cmd, _ in controls] == ["pause"]
    assert queries == [0]


def test_remote_query_failure_releases_ducking(remote_ducking, monkeypatch, duck_warnings, capsys):
    clock, _, _, _, controls, _ = remote_ducking
    session = RemoteAudioSession(env={"AGENT_TTS_DUCK_POLICY": "duck"})
    session.prepare_pcm(make_decoded_pcm(10))
    session.state["status"] = "playing"
    session._duck_running = True
    monkeypatch.setattr(wc, "query_activity", ORIGINAL_QUERY_ACTIVITY)
    monkeypatch.setattr(wc, "candidate_hosts", lambda env=None: ["mock-host"])

    def unavailable(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(wc.socket, "create_connection", unavailable)
    for now in [2, 4]:
        clock[0] = now
        session._check_remote_ducking()
    assert session._duck_gain_db == 0.0
    assert [cmd for cmd, _ in controls] == ["gain"]
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_remote_capture_only_activity_uses_custom_attenuation(remote_ducking):
    _, activity, _, headers, _, _ = remote_ducking
    activity.append({"communication_active": False, "capture_active": True})
    session = RemoteAudioSession(env={"AGENT_TTS_DUCK_POLICY": "duck", "AGENT_TTS_DUCK_DB": "6"})
    session.prepare_pcm(make_decoded_pcm(10))
    assert headers[0]["gain_db"] == -6.0


def test_finish_wait_hook_survives_read_timeout():
    events = []

    class Sock:
        def sendall(self, payload):
            pass

        def shutdown(self, how):
            events.append("half-close")

        def settimeout(self, seconds):
            assert seconds == 0.2

        def recv(self, size):
            if events.count("check") == 1:
                raise socket.timeout()
            return b""

        def close(self):
            events.append("close")

    stream = wc.WinhostStream(Sock(), "mock", 7717, 24000, 1, 2)
    stream.finish(on_wait=lambda: events.append("check"))
    assert events == ["half-close", "check", "check", "close"]


def test_remote_activity_uses_connected_endpoint(remote_ducking, monkeypatch):
    clock, _, _, _, _, _ = remote_ducking
    environments = []

    def query(env=None):
        environments.append(env)
        return {"communication_active": False, "capture_active": False}

    monkeypatch.setattr(wc, "query_activity", query)
    env = {"AGENT_TTS_DUCK_POLICY": "duck", "AGENT_TTS_WINHOST_HOST": "requested-host"}
    session = RemoteAudioSession(env=env)
    session.prepare_pcm(make_decoded_pcm(10))
    session.state["status"] = "playing"
    session._duck_running = True
    clock[0] = 2
    session._check_remote_ducking()
    assert environments == [env, {"AGENT_TTS_WINHOST_HOST": "mock-host", "AGENT_TTS_WINHOST_PORT": "7717"}]


# -- helpers ---------------------------------------------------------------


class FakeDevice:
    """Stands in for miniaudio.PlaybackDevice: drains the generator and records output."""

    def __init__(self, rate, channels, sink):
        self.rate = rate
        self.channels = channels
        self.sink = sink
        self.started = threading.Event()
        self.stopped = False
        self.closed = False

    def start(self, gen):
        self.started.set()
        try:
            while True:
                try:
                    chunk = gen.send(1024)
                except StopIteration:
                    break
                if chunk:
                    self.sink.append(chunk)
        finally:
            self.stopped = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def make_decoded_pcm(frames, sample_rate=24000, nchannels=1, sample_width=2):
    """Builds a stand-in decoded sound whose PCM payload is deterministic and non-silent."""
    import array

    class FakeDecoded:
        pass

    decoded = FakeDecoded()
    decoded.sample_rate = sample_rate
    decoded.nchannels = nchannels
    decoded.sample_width = sample_width
    decoded.samples = array.array("h", [((i * 37) % 20000) - 10000 for i in range(frames * nchannels)])
    return decoded


def deterministic_pcm(nbytes):
    """Raw PCM-ish bytes with no zero bytes, so silence chunks are filterable."""
    return bytes((i % 251) + 1 for i in range(nbytes))


def rendered_audio(sink):
    """Joins recorded device chunks, dropping pure-silence (all zero) chunks."""
    return b"".join(chunk for chunk in sink if chunk.strip(b"\x00"))


def patch_target(monkeypatch, host, port):
    """Pins client resolution helpers to a single loopback candidate."""
    monkeypatch.setattr(wc, "candidate_hosts", lambda env=None: [host])
    monkeypatch.setattr(wc, "winhost_port", lambda env=None: port)


class FakePSSession:
    """Records the PowershellSession surface calls made by RemoteAudioSession."""

    instances = []

    def __init__(self, label="Audio", auto_rewind_sec=2.0, boundaries=None,
                 highlight=False, autoscroll=False, bionic=False, zen=False,
                 provider="", voice="", env=None):
        self.calls = []
        self.state = {"stop": False}
        self.provider = provider
        self.voice = voice
        FakePSSession.instances.append(self)

    def prepare_pcm(self, decoded):
        self.calls.append(("prepare", decoded))

    def append_pcm(self, decoded):
        self.calls.append(("append", decoded))
        return True

    def play(self, decoded):
        self.calls.append(("play", decoded))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))

    def stop(self):
        self.calls.append(("stop",))

    def finish(self):
        self.calls.append(("finish",))


class FakeAudioSession:
    """Records the local AudioSession surface calls made by RemoteAudioSession."""

    instances = []

    def __init__(self, label="Audio", auto_rewind_sec=2.0, boundaries=None,
                 highlight=False, autoscroll=False, bionic=False, zen=False):
        self.calls = []
        self.state = {"stop": False, "status": "synthesizing"}
        self.boundaries = boundaries if boundaries is not None else wc.BoundaryMap()
        FakeAudioSession.instances.append(self)

    def prepare_pcm(self, decoded):
        self.calls.append(("prepare", decoded))

    def append_pcm(self, decoded):
        self.calls.append(("append", decoded))
        return True

    def play(self, decoded):
        self.calls.append(("play", decoded))

    def handle_ipc_command(self, cmd):
        self.calls.append(("ipc", cmd))
        return "status=ok"

    def stop(self):
        self.calls.append(("stop",))


class BoomPSSession:
    """Constructing a PowerShell session for the windows target is a bug."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("PowershellSession must never be constructed for the windows target")


def wait_for(predicate, timeout=2.0):
    """Polls a condition on a background thread (server handlers run async)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def winserver():
    sink = []
    server = WinhostServer(
        host="127.0.0.1",
        port=0,
        device_factory=lambda rate, channels: FakeDevice(rate, channels, sink),
    )
    host, port = server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    yield server, host, port, sink
    server.shutdown()


# -- client transport -------------------------------------------------------


def test_open_stream_unavailable_raises(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)  # reserved port: refused
    with pytest.raises(WinhostUnavailable):
        open_stream(24000, 1, 2)


def test_finish_half_closes_and_waits_for_server_close(winserver, monkeypatch):
    _, host, port, _ = winserver
    patch_target(monkeypatch, host, port)
    pcm = deterministic_pcm(1000)
    stream = open_stream(24000, 1, 2)
    stream.send_pcm(pcm)
    stream.finish()  # Returns only after the server closed the connection.
    assert stream.sock.fileno() == -1  # closed


# -- server protocol ---------------------------------------------------------


def test_play_roundtrip_and_header(winserver, monkeypatch):
    server, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    pcm = deterministic_pcm(1000)
    stream = open_stream(24000, 1, 2)
    stream.send_pcm(pcm)
    stream.finish()
    assert server.last_header == {"v": 1, "cmd": "play", "rate": 24000, "channels": 1, "format": "s16"}
    assert rendered_audio(sink) == pcm


def test_bad_header_closes_connection_and_logs_once(winserver, capsys):
    server, host, port, _ = winserver
    sock = socket.create_connection((host, port), timeout=2)
    sock.sendall(b"this is not json\n")
    sock.shutdown(socket.SHUT_WR)
    assert sock.recv(1024) == b""  # server closed without replying
    sock.close()
    time.sleep(0.1)
    err = capsys.readouterr().err
    assert "bad header" in err
    assert server.last_header is None


def test_unknown_version_rejected(winserver, capsys):
    server, host, port, _ = winserver
    sock = socket.create_connection((host, port), timeout=2)
    sock.sendall(b'{"v":2,"cmd":"play","rate":24000,"channels":1,"format":"s16"}\n')
    sock.shutdown(socket.SHUT_WR)
    assert sock.recv(1024) == b""
    sock.close()
    time.sleep(0.1)
    assert "bad header" in capsys.readouterr().err


def test_control_before_play_is_a_noop(winserver):
    server, host, port, _ = winserver
    sock = socket.create_connection((host, port), timeout=2)
    sock.sendall(b'{"v":1,"cmd":"pause"}\n')
    sock.close()
    time.sleep(0.1)
    assert server._active is None


def test_control_before_play_then_roundtrip_still_works(winserver, monkeypatch):
    server, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    sock = socket.create_connection((host, port), timeout=2)
    sock.sendall(b'{"v":1,"cmd":"resume"}\n')
    sock.close()
    time.sleep(0.05)
    pcm = deterministic_pcm(800)
    stream = open_stream(24000, 1, 2)
    stream.send_pcm(pcm)
    stream.finish()
    assert rendered_audio(sink) == pcm


def test_new_play_preempts_active_session(winserver, monkeypatch):
    server, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    first_active = threading.Event()
    first_ready = threading.Event()
    first_paused = threading.Event()
    first_stopped = threading.Event()
    second_active = threading.Event()
    streams = []
    real_activate = server._activate
    real_control = server._apply_control

    def observed_activate(stream):
        if stream.rate == 24000:
            streams.append(stream)
            stop = stream.stop

            def observed_stop():
                stop()  # acknowledge the REAL preemption stop, not a test flag
                first_stopped.set()

            monkeypatch.setattr(stream, "stop", observed_stop)
        real_activate(stream)
        (first_active if stream.rate == 24000 else second_active).set()

    def observed_control(cmd, header=None):
        real_control(cmd, header)
        if cmd == "pause" and streams and streams[0].paused:
            first_paused.set()

    class HeldFirstDevice(FakeDevice):
        def start(self, generator):
            self.started.set()
            first_ready.set()
            # Do not busy-spin allocating silent buffers while the test waits.
            # Real PlayStream.stop wakes this gate; its guarded generator then
            # acknowledges completion so _activate can finish preempting it.
            assert first_stopped.wait(timeout=10.0), "first stream was never preempted"
            super().start(generator)

    monkeypatch.setattr(server, "_activate", observed_activate)
    monkeypatch.setattr(server, "_apply_control", observed_control)
    monkeypatch.setattr(server, "device_factory", lambda rate, channels: (
        HeldFirstDevice(rate, channels, sink) if rate == 24000 else FakeDevice(rate, channels, sink)
    ))
    first = second = None
    try:
        first = open_stream(24000, 1, 2)
        first.send_pcm(deterministic_pcm(4096))
        assert first_active.wait(timeout=10.0), "first stream was never activated"
        assert first_ready.wait(timeout=10.0), "first device never started"
        with server._active_lock:
            assert server._active is streams[0]
        assert send_control("pause")
        assert first_paused.wait(timeout=10.0), "first stream was never paused"
        assert not streams[0].stopped

        pcm2 = deterministic_pcm(600)
        second = open_stream(16000, 2, 2)
        second.sock.settimeout(10.0)
        second.send_pcm(pcm2)
        assert second_active.wait(timeout=10.0), "second stream was never activated"
        assert first_stopped.is_set()
        assert streams[0].stopped
        assert streams[0].finished.wait(timeout=10.0), "preempted stream never finished"
        second.finish()

        assert server.last_header["rate"] == 16000
        assert rendered_audio(sink).endswith(pcm2)
    finally:
        if streams:
            streams[0].stop()
        if first is not None:
            first.abort()
        if second is not None:
            second.abort()


# -- RemoteAudioSession --------------------------------------------------------


def test_remote_prepare_sets_header_then_streams_groups(winserver, monkeypatch):
    server, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    session = RemoteAudioSession()
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    assert wait_for(lambda: server.last_header is not None)
    assert server.last_header == {"v": 1, "cmd": "play", "rate": 24000, "channels": 1, "format": "s16"}

    g2 = make_decoded_pcm(60, 24000, 1)
    assert session.append_pcm(g2) is True
    session.play(g1)  # buffer already preloaded; pumps g1+g2 in order
    assert rendered_audio(sink) == g1.samples.tobytes() + g2.samples.tobytes()


def test_remote_append_mismatch_dropped_like_local(winserver, monkeypatch, capsys):
    _, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    session = RemoteAudioSession()
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    assert session.append_pcm(make_decoded_pcm(50, 48000, 1)) is False
    assert session.append_pcm(make_decoded_pcm(50, 24000, 2)) is False
    session.play(g1)
    assert rendered_audio(sink) == g1.samples.tobytes()
    assert "mismatched format" in capsys.readouterr().err


def test_remote_finish_sends_eof_before_play_returns(winserver, monkeypatch):
    _, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    session = RemoteAudioSession()
    g1 = make_decoded_pcm(80, 24000, 1)
    session.prepare_pcm(g1)
    session.play(g1)
    # finish() returned, meaning the client observed the server close after EOF.
    assert rendered_audio(sink) == g1.samples.tobytes()


def test_remote_falls_back_to_powershell_once(monkeypatch, capsys):
    patch_target(monkeypatch, "127.0.0.1", 1)  # nothing listening
    monkeypatch.setattr(wc, "is_wsl_ps_available", lambda env=None: True)
    FakePSSession.instances = []
    monkeypatch.setattr(wc, "PowershellSession", FakePSSession)
    session = RemoteAudioSession(target="winhost")
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    session.play(g1)

    err = capsys.readouterr().err
    assert err.count("falling back to wsl-ps playback") == 1
    assert len(FakePSSession.instances) == 1  # one persistent session for the run
    ps = FakePSSession.instances[0]
    assert ps.calls == [("prepare", g1), ("play", g1)]

    g2 = make_decoded_pcm(40, 24000, 1)
    assert session.append_pcm(g2) is True
    assert ps.calls[-1] == ("append", g2)


def test_remote_fallback_unavailable_raises(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "is_wsl_ps_available", lambda env=None: False)
    session = RemoteAudioSession(target="winhost")
    with pytest.raises(RuntimeError, match="wsl-ps fallback unavailable"):
        session.prepare_pcm(make_decoded_pcm(10, 24000, 1))


def test_wsl_ps_target_plays_per_group_without_connecting(monkeypatch, capsys):
    patch_target(monkeypatch, "127.0.0.1", 1)  # never contacted
    FakePSSession.instances = []
    monkeypatch.setattr(wc, "PowershellSession", FakePSSession)
    session = RemoteAudioSession(target="wsl-ps")
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    session.play(g1)
    g2 = make_decoded_pcm(40, 24000, 1)
    assert session.append_pcm(g2) is True
    ps = FakePSSession.instances[0]
    assert ps.calls == [
        ("prepare", g1),
        ("play", g1),
        ("append", g2),
    ]
    assert "falling back" not in capsys.readouterr().err


def test_remote_stop_natural_end_drains_ps_session(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)
    FakePSSession.instances = []
    monkeypatch.setattr(wc, "PowershellSession", FakePSSession)
    session = RemoteAudioSession(target="wsl-ps")
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    session.play(g1)
    session.stop()  # natural end of run: drain, don't hard-stop
    ps = FakePSSession.instances[0]
    assert ps.calls[-1] == ("finish",)


def test_remote_explicit_stop_hard_stops_ps_session(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)
    FakePSSession.instances = []
    monkeypatch.setattr(wc, "PowershellSession", FakePSSession)
    session = RemoteAudioSession(target="wsl-ps")
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    session.state["stop"] = True  # user asked to stop before cleanup
    session.stop()
    ps = FakePSSession.instances[0]
    assert ps.calls[-1] == ("stop",)
    assert ps.state["stop"] is True


def test_remote_ipc_pause_and_stop_forward_to_ps_session(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)
    FakePSSession.instances = []
    monkeypatch.setattr(wc, "PowershellSession", FakePSSession)
    session = RemoteAudioSession(target="wsl-ps")
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    session.state["status"] = "playing"

    session.handle_ipc_command("pause")
    session.handle_ipc_command("resume")
    session.handle_ipc_command("stop")
    ps = FakePSSession.instances[0]
    assert ("pause",) in ps.calls
    assert ("resume",) in ps.calls
    assert ("stop",) in ps.calls


def test_remote_ipc_status_reports_requested_provider_and_voice(monkeypatch):
    """RS-2 (SOS-1/B5): winhost status carries the request's engine metadata."""
    for var in ("AGENT_TTS_PROVIDER", "AGENT_TTS_VOICE", "TTS_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    session = RemoteAudioSession(provider="piper", voice="es-ES-AlvaroNeural")
    session.state["status"] = "playing"

    status = session.handle_ipc_command("status")
    assert " provider=piper" in status
    assert " voice=es-ES-AlvaroNeural" in status

    # Without explicit args the same resolution as the local session
    # applies (env fallbacks, then engine defaults).
    default_session = RemoteAudioSession()
    assert default_session.provider == "edge"
    assert default_session.voice == DEFAULT_VOICE
    default_status = default_session.handle_ipc_command("status")
    assert " provider=edge" in default_status
    assert f" voice={DEFAULT_VOICE}" in default_status


def test_remote_ipc_status_and_stop(winserver, monkeypatch):
    server, host, port, _ = winserver
    patch_target(monkeypatch, host, port)
    session = RemoteAudioSession()
    g1 = make_decoded_pcm(24000, 24000, 1)  # 1 second of audio
    session.prepare_pcm(g1)
    session.state["status"] = "playing"

    status = session.handle_ipc_command("status")
    assert status.startswith("status=playing")
    assert "sent_idx=-1" in status

    assert session.handle_ipc_command("toggle-pause").startswith("status=paused")
    assert session.handle_ipc_command("resume").startswith("status=playing")
    assert session.handle_ipc_command("seek +10").startswith("ERR:")
    assert session.handle_ipc_command("stop") == "status=stopped"


# -- "windows" target: winhost with local-device fallback, never PowerShell ---


def test_windows_target_streams_over_winhost_when_reachable(winserver, monkeypatch, capsys):
    server, host, port, sink = winserver
    patch_target(monkeypatch, host, port)
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    g1 = make_decoded_pcm(100, 24000, 1)
    g2 = make_decoded_pcm(60, 24000, 1)
    session.prepare_pcm(g1)
    assert session.append_pcm(g2) is True
    session.play(g1)

    assert rendered_audio(sink) == g1.samples.tobytes() + g2.samples.tobytes()
    assert "falling back" not in capsys.readouterr().err
    assert FakeAudioSession.instances == []  # local device untouched when receiver is alive


def test_windows_target_falls_back_to_local_when_unreachable(monkeypatch, capsys):
    patch_target(monkeypatch, "127.0.0.1", 1)  # nothing listening
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)  # must never fire
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    g1 = make_decoded_pcm(100, 24000, 1)
    session.prepare_pcm(g1)
    session.play(g1)

    err = capsys.readouterr().err
    assert err.count("falling back to local playback") == 1
    assert FakeAudioSession.instances, "local AudioSession must be constructed"
    local = FakeAudioSession.instances[0]
    assert local.calls == [("prepare", g1), ("play", g1)]

    # Streaming continues through the local session; mismatch rule intact.
    g2 = make_decoded_pcm(40, 24000, 1)
    assert session.append_pcm(g2) is True
    assert local.calls[-1] == ("append", g2)
    assert session.append_pcm(make_decoded_pcm(50, 48000, 1)) is False
    assert [c for c in local.calls if c[0] == "append"] == [("append", g2)]
    assert "mismatched format" in capsys.readouterr().err


def test_windows_fallback_never_spawns_powershell_across_run(monkeypatch, capsys):
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "is_wsl_ps_available", lambda env=None: True)
    FakePSSession.instances = []
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    g1 = make_decoded_pcm(80, 24000, 1)
    session.prepare_pcm(g1)
    session.play(g1)
    session.append_pcm(make_decoded_pcm(40, 24000, 1))
    session.handle_ipc_command("toggle-pause")
    session.stop()

    assert FakePSSession.instances == []  # no PowerShell process for the whole run
    assert "wsl-ps" not in capsys.readouterr().err


def test_windows_target_ipc_controls_forward_to_winhost_when_reachable(winserver, monkeypatch):
    server, host, port, _ = winserver
    patch_target(monkeypatch, host, port)
    sent = []
    monkeypatch.setattr(wc, "send_control", lambda cmd, env=None: sent.append(cmd) or True)
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    session.prepare_pcm(make_decoded_pcm(24000, 24000, 1))  # opens the transport
    session.state["status"] = "playing"

    session.handle_ipc_command("pause")
    session.handle_ipc_command("resume")
    session.handle_ipc_command("stop")
    assert sent == ["pause", "resume", "stop"]
    assert FakeAudioSession.instances == []


def test_windows_target_ipc_controls_act_on_local_session_after_fallback(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    session.prepare_pcm(make_decoded_pcm(100, 24000, 1))  # triggers local fallback
    session.state["status"] = "playing"

    session.handle_ipc_command("pause")
    session.handle_ipc_command("resume")
    session.handle_ipc_command("stop")
    local = FakeAudioSession.instances[0]
    assert ("ipc", "pause") in local.calls
    assert ("ipc", "resume") in local.calls
    assert ("ipc", "stop") in local.calls


def test_windows_target_stop_after_fallback_stops_local_session(monkeypatch):
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    session.prepare_pcm(make_decoded_pcm(100, 24000, 1))
    session.play(make_decoded_pcm(100, 24000, 1))
    session.stop()  # natural end of run: local session flagged to stop

    local = FakeAudioSession.instances[0]
    assert local.calls[-1] == ("stop",)


# -- "windows" fallback boundary estimation (document safety net) ------------


DOCUMENT_TEXT = "Primera frase del documento largo. Segunda frase del documento largo."


def test_windows_fallback_estimates_document_boundaries_when_map_empty(monkeypatch, capsys):
    """Carrier built without a map and nothing estimated upstream still serves karaoke.

    Regression for the empty-BoundaryMap fallback: the local AudioSession
    shares the carrier map, so estimating it in place at the first buffer
    load makes status/sentence/scroll-info/highlight carry real data.
    """
    patch_target(monkeypatch, "127.0.0.1", 1)  # nothing listening -> local fallback
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    session = RemoteAudioSession(target="windows", document_text=DOCUMENT_TEXT)
    session.prepare_pcm(make_decoded_pcm(48000, 24000, 1))  # 2s -> duration known
    session.state["status"] = "playing"

    assert session._local_session is not None, "local fallback session must exist"
    assert session._local_session.boundaries is session.boundaries, "map must be shared"
    assert len(session.boundaries.sentences) == 2
    assert session.boundaries.words and session.boundaries.paragraphs

    status = session.handle_ipc_command("status")
    assert "sent_idx=0" in status
    assert "Primera frase del documento largo." in status

    sentence = session.handle_ipc_command("sentence")
    assert "sent_idx=0" in sentence
    assert "text=Primera frase del documento largo." in sentence

    scroll = session.handle_ipc_command("scroll-info")
    assert "sent_idx=0" in scroll
    assert "total_sents=2" in scroll
    assert "para_idx=0" in scroll

    highlight = session.handle_ipc_command("highlight")
    assert "Primera" in highlight
    assert "\x1b[" in highlight  # SGR codes for karaoke rendering


def test_windows_fallback_estimates_document_boundaries_on_first_append(monkeypatch):
    """A stream whose first segment carries no frames estimates once duration arrives.

    Mirrors the streaming pipeline: the buffer starts empty (total 0.0, no
    estimation possible) and the map fills at the first append that makes
    the duration known.
    """
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    FakeAudioSession.instances = []
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows", document_text=DOCUMENT_TEXT)
    session.prepare_pcm(make_decoded_pcm(0, 24000, 1))  # no frames yet -> total 0.0

    assert session.boundaries.sentences == []
    local = FakeAudioSession.instances[0]
    assert local.boundaries is session.boundaries

    assert session.append_pcm(make_decoded_pcm(48000, 24000, 1)) is True
    assert len(session.boundaries.sentences) == 2
    assert len(local.boundaries.sentences) == 2, "shared map fills in place"


def test_windows_fallback_keeps_existing_boundaries_untouched(monkeypatch):
    """Per-segment merged boundaries (streaming path) are never re-estimated."""
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows", document_text=DOCUMENT_TEXT)

    merged = wc.estimate_boundaries_from_text("Frase previa del primer grupo.", 1.0)
    with session.lock:  # same in-place merge cli.py's merge_group performs
        session.boundaries.sentences.extend(merged.sentences)
        session.boundaries.words.extend(merged.words)
        session.boundaries.paragraphs.extend(merged.paragraphs)
    before = list(session.boundaries.sentences)

    session.prepare_pcm(make_decoded_pcm(48000, 24000, 1))

    assert session.boundaries.sentences == before, "existing sentences must be preserved"


def test_windows_fallback_without_document_keeps_empty_map(monkeypatch):
    """No document text (e.g. --play-file replays) -> estimation impossible, no crash."""
    patch_target(monkeypatch, "127.0.0.1", 1)
    monkeypatch.setattr(wc, "PowershellSession", BoomPSSession)
    monkeypatch.setattr(wc, "AudioSession", FakeAudioSession)
    session = RemoteAudioSession(target="windows")
    session.prepare_pcm(make_decoded_pcm(48000, 24000, 1))

    assert session.boundaries.sentences == []
