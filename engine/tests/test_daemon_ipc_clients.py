"""Daemon IPC client surfaces + real queue dispatch (voice-stack VS1.8).

daemon.py became a touched module in VS1.5, so the whole file falls under the
D4 per-module floor. These tests drive the previously unexercised halves:
the CLIENT functions (send_ipc_command/send_enqueue/send_play, probe
classification, KeyboardInterrupt delegation) against a scripted Unix-socket
server speaking the real frame protocol, and the REAL queue-manager dispatch
path (_queue_runner) with a stub engine and faked AudioSession.play — no
audio device, no spawned daemons.
"""

from __future__ import annotations

import array
import json
import os
import socket
import threading
import types
from pathlib import Path

import pytest

import agent_tts.audio as audio
from agent_tts.daemon import (
    Daemon,
    _best_effort_stop,
    delegate_enqueue,
    delegate_play,
    probe_daemon,
    send_enqueue,
    send_ipc_command,
    send_play,
)
from agent_tts.ipc import encode_frame, read_frame
from agent_tts.providers.base import TTSProvider


# -- scripted frame-protocol server --------------------------------------------


class FrameServer:
    """Minimal daemon stand-in: replies come from a handler function."""

    def __init__(self, handler, socket_path):
        self.handler = handler
        self.path = socket_path
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(socket_path)
        self.server.listen(4)
        self.server.settimeout(5)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            while True:
                try:
                    conn, _ = self.server.accept()
                except socket.timeout:
                    return
                with conn:
                    conn.settimeout(5)
                    try:
                        command = read_frame(conn)
                        reply = self.handler(command)
                        if reply is not None:
                            conn.sendall(encode_frame(reply))
                    except Exception:
                        pass
        finally:
            try:
                self.server.close()
            except OSError:
                pass

    def close(self):
        try:
            self.server.close()
        except OSError:
            pass
        self.thread.join(timeout=2)
        try:
            os.unlink(self.path)
        except OSError:
            pass


@pytest.fixture
def sock_path(tmp_path) -> str:
    return str(tmp_path / "player.sock")


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """Isolates the transient channel files (ownership-suite pattern)."""
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


def serve(handler, sock_path) -> FrameServer:
    return FrameServer(handler, sock_path)


def test_send_ipc_command_round_trip_and_unreachable(sock_path):
    server = serve(lambda cmd: "pong version=x" if cmd == "ping" else "ok=true", sock_path)
    try:
        assert send_ipc_command("ping", socket_path=sock_path).startswith("pong")
        assert send_ipc_command("status", socket_path=sock_path) == "ok=true"
    finally:
        server.close()
    assert send_ipc_command("ping", socket_path=sock_path + "-dead") is None


def test_send_enqueue_and_send_play_paths(sock_path):
    def handler(command):
        if command.startswith("enqueue"):
            return "ok=true item=1 queue_len=0"
        if command.startswith("play"):
            return "status=done"
        return "err"

    server = serve(handler, sock_path)
    try:
        assert send_enqueue({"text": "hola"}, socket_path=sock_path) == "ok=true item=1 queue_len=0"
        assert send_play({"text": "hola"}, socket_path=sock_path) == "status=done"
    finally:
        server.close()
    # Dead socket: connect fails on both client paths.
    assert send_enqueue({"text": "hola"}, socket_path=sock_path + "-dead") is None
    assert send_play({"text": "hola"}, socket_path=sock_path + "-dead") is None
    # Oversized payload: typed ERR before any wire write.
    huge = send_enqueue({"text": "x" * (17 * 1024 * 1024)}, socket_path=sock_path)
    assert huge is not None and huge.startswith("ERR:")
    assert send_play({"text": "x" * (17 * 1024 * 1024)}, socket_path=sock_path).startswith("ERR:")


def test_probe_daemon_classifies_the_channel(sock_path, tmp_path):
    # ok: healthy pong
    server = serve(lambda cmd: "pong version=t", sock_path)
    try:
        assert probe_daemon(timeout_sec=1.0, socket_path=sock_path)[0] == "ok"
    finally:
        server.close()
    # unreachable: nothing listens
    assert probe_daemon(timeout_sec=0.2, socket_path=str(tmp_path / "none.sock"))[0] == "unreachable"
    # wedged: accepts, never answers
    silent_path = str(tmp_path / "silent.sock")
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.bind(silent_path)
    silent.listen(2)
    try:
        assert probe_daemon(timeout_sec=0.2, socket_path=silent_path)[0] == "wedged"
    finally:
        silent.close()
    # foreign: something answered, but not pong
    stranger = serve(lambda cmd: "who are you", str(tmp_path / "stranger.sock"))
    try:
        status, reply = probe_daemon(timeout_sec=1.0, socket_path=str(tmp_path / "stranger.sock"))
        assert status == "foreign" and reply == "who are you"
    finally:
        stranger.close()


def test_delegate_play_and_enqueue_forward_and_handle_interrupt(sock_path, monkeypatch):
    started = []
    monkeypatch.setattr("agent_tts.daemon.ensure_daemon",
                        lambda socket_path=None: started.append(socket_path) or "pong")
    server = serve(lambda cmd: "status=done" if cmd.startswith("play") else "ok=true item=2 queue_len=0",
                   sock_path)
    try:
        assert delegate_play({"text": "x"}, socket_path=sock_path) == "status=done"
        assert delegate_enqueue({"text": "x"}, socket_path=sock_path) == "ok=true item=2 queue_len=0"
    finally:
        server.close()
    assert started == [sock_path, sock_path]

    def interrupted(_payload, socket_path=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr("agent_tts.daemon.send_play", interrupted)
    with pytest.raises(KeyboardInterrupt):
        delegate_play({"text": "x"}, socket_path=sock_path + "-dead")  # best-effort stop swallows the dead path


def test_best_effort_stop_never_raises_on_a_dead_socket(sock_path):
    _best_effort_stop(socket_path=sock_path + "-dead")


# -- real queue-manager dispatch through _queue_runner ---------------------------


class _StubEngine(TTSProvider):
    async def synthesize(self, text, **_kwargs):
        return b"ID3"

    async def synthesize_stream(self, text, **_kwargs):
        yield b"ID3"


def _fake_blocking_play(self, decoded):
    self.state["status"] = "playing"
    deadline = threading.Event()
    self.state["_done"] = deadline  # kept for introspection; playback ends on stop or timeout
    import time as _time

    end = _time.monotonic() + 0.05
    while not self.state.get("stop") and _time.monotonic() < end:
        _time.sleep(0.005)
    self.state["status"] = "stopped"


def test_real_dispatch_runs_the_queue_runner_and_completes(channel, monkeypatch):
    import agent_tts.cli as cli_mod
    from agent_tts.daemon import ProviderCache

    class FakeDecoded:
        sample_rate = 24000
        nchannels = 1
        duration = 0.01
        samples = array.array("h", [0] * 240)

    monkeypatch.setattr(cli_mod, "miniaudio",
                        types.SimpleNamespace(decode=lambda data: FakeDecoded()))
    monkeypatch.setattr(audio.AudioSession, "play", _fake_blocking_play)

    daemon = Daemon(socket_path=channel["sock"],
                    provider_cache=ProviderCache(factory=lambda **kw: _StubEngine()))
    try:
        reply = daemon.handle_command("enqueue " + json.dumps({"text": "hola", "stream": "off"}))
        assert reply.startswith("ok=true item=")
        # The real dispatch loop is synchronous here: the faked playback ends
        # within ~50ms; wait for the queue to drain back to idle.
        import time as _time

        deadline = _time.monotonic() + 5
        while _time.monotonic() < deadline:
            snap = daemon.queue_manager.snapshot()
            if snap.active is None and snap.queue_len == 0:
                break
            _time.sleep(0.02)
        snap = daemon.queue_manager.snapshot()
        assert snap.active is None and snap.queue_len == 0
        assert snap.completed_count >= 1

        blocking = daemon.handle_command("play " + json.dumps({"text": "chau", "stream": "off"}))
        assert blocking.startswith("status=")

        # A preempting enqueue owns the speaker at once: its ack comes from
        # the ACTIVE snapshot branch, not the pending list.
        preempt = daemon.handle_command("enqueue " + json.dumps(
            {"text": "urgente", "priority": "blocked", "policy": "preempt", "stream": "off"}))
        assert preempt.startswith("ok=true item=")
        import time as _t

        deadline = _t.monotonic() + 5
        while _t.monotonic() < deadline and daemon.queue_manager.snapshot().active is not None:
            _t.sleep(0.02)
    finally:
        daemon._shutdown()
