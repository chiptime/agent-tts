"""Winhost protocol tests: server, client transport, and RemoteAudioSession.

All networking runs over loopback TCP with ephemeral ports; the audio device
is replaced by a fake device factory that eagerly drains the callback
generator and records the rendered chunks.
"""

import socket
import threading
import time

import pytest

import agent_tts.winhost_client as wc
from agent_tts.winhost import WinhostServer
from agent_tts.winhost_client import (
    RemoteAudioSession,
    WinhostUnavailable,
    open_stream,
    send_control,
)


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
                 highlight=False, autoscroll=False, bionic=False, zen=False, env=None):
        self.calls = []
        self.state = {"stop": False}
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
    first = open_stream(24000, 1, 2)
    first.send_pcm(deterministic_pcm(4096))
    assert wait_for(lambda: server._active is not None)
    send_control("pause")  # keeps the first generator alive in silence
    time.sleep(0.2)

    pcm2 = deterministic_pcm(600)
    second = open_stream(16000, 2, 2)
    second.send_pcm(pcm2)
    second.finish()

    assert server.last_header["rate"] == 16000
    assert rendered_audio(sink).endswith(pcm2)
    first.abort()


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
