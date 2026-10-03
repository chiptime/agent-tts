"""Remaining daemon.py edges for the D4 module floor (voice-stack VS1.8).

Pure validation helpers, typed error replies, teardown strands, pid
classification and client failure paths — all hermetic (no audio, no spawned
daemons; ensure_daemon is driven with patched internals).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

import agent_tts.daemon as daemon_mod
from agent_tts.daemon import (
    Daemon,
    _chain_payload_error,
    _env_float,
    _pid_alive,
    _session_env,
    delegate_enqueue,
    ensure_daemon,
    probe_daemon,
    send_enqueue,
    send_play,
)
from agent_tts.ipc import encode_frame



def _accept_close_server(path):
    """Accepts connections and closes them immediately (rude peer)."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path); srv.listen(2); srv.settimeout(5)

    def _run():
        try:
            while True:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    return
                conn.close()
        finally:
            try: srv.close()
            except OSError: pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _reply_server(path, reply):
    """Accepts, reads one frame, answers a fixed reply (or nothing)."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path); srv.listen(2); srv.settimeout(5)

    def _run():
        try:
            while True:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    return
                with conn:
                    conn.settimeout(2)
                    try:
                        conn.recv(65536)
                        conn.sendall(encode_frame(reply))  # even "": a real empty frame
                    except OSError:
                        pass
        finally:
            try: srv.close()
            except OSError: pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


# -- pure helpers --------------------------------------------------------------


def test_env_float_invalid_and_negative_keep_the_default(monkeypatch, capsys):
    monkeypatch.setenv("VS8_BAD", "not-a-number")
    monkeypatch.setenv("VS8_NEG", "-3")
    assert _env_float("VS8_BAD", 7.5) == 7.5
    assert _env_float("VS8_NEG", 7.5) == 7.5
    assert _env_float("VS8_UNSET", 7.5) == 7.5
    assert capsys.readouterr().err.count("Ignoring") == 2


def test_chain_payload_error_matrix():
    assert _chain_payload_error({"text": "a", "chain": ["f"]}).startswith("play accepts one of")
    assert _chain_payload_error({"chain": ["f"], "chain_gap_ms": "x"}) .startswith("chain_gap_ms")
    assert _chain_payload_error({"chain": ["f"], "chain_gap_ms": -1}).startswith("chain_gap_ms")
    assert _chain_payload_error({"chain": ["f"], "no_play": True}) == (
        "no_play cannot combine with chain (a chain owns no synthesis)")
    assert _chain_payload_error({"chain": ["f"]}) is None


def test_session_env_overlay_branches(monkeypatch):
    monkeypatch.delenv("AGENT_TTS_WINHOST_HOST", raising=False)
    monkeypatch.delenv("AGENT_TTS_WINHOST_PORT", raising=False)
    assert _session_env({}) is None
    assert _session_env({"winhost_host": "h"})["AGENT_TTS_WINHOST_HOST"] == "h"
    assert _session_env({"winhost_port": 9999})["AGENT_TTS_WINHOST_PORT"] == "9999"
    both = _session_env({"winhost_host": "h", "winhost_port": "1"})
    assert both["AGENT_TTS_WINHOST_HOST"] == "h" and both["AGENT_TTS_WINHOST_PORT"] == "1"


# -- typed replies ------------------------------------------------------------


def _idle_daemon() -> Daemon:
    return Daemon(socket_path="unused.sock")


def test_handle_command_typed_errors_without_a_session():
    daemon = _idle_daemon()
    assert daemon.handle_command("") == "ok=false error=empty command"
    assert daemon.handle_command("   ") == "ok=false error=empty command"
    assert daemon.handle_command("pause") == "ok=false error=no active playback session"


def test_play_payload_validation_errors():
    daemon = _idle_daemon()
    assert daemon.handle_command("play {not json").startswith("ok=false error=invalid play payload")
    assert daemon.handle_command("play [1]").startswith("ok=false error=invalid play payload")
    assert daemon.handle_command("play {}").startswith("ok=false error=play requires")
    assert daemon.handle_command("play " + json.dumps({"chain": ["f"], "text": "a"})).startswith(
        "ok=false error=play accepts one of")


def test_enqueue_payload_validation_errors():
    daemon = _idle_daemon()
    assert daemon.handle_command("enqueue {bad").startswith("ok=false error=invalid enqueue payload")
    assert daemon.handle_command(
        "enqueue " + json.dumps({"text": "x", "identifiers": "A"})
    ).startswith("ok=false error=identifiers must be a list")
    assert daemon.handle_command(
        "enqueue " + json.dumps({"text": "x", "no_play": True})
    ).startswith("ok=false error=enqueue does not support no_play")
    assert daemon.handle_command(
        "enqueue " + json.dumps({"chain": ["f"], "text": "a"})
    ).startswith("ok=false error=play accepts one of")
    daemon._shutting_down = True
    assert daemon.handle_command("enqueue " + json.dumps({"text": "x"})) == (
        "ok=false error=daemon shutting down")


# -- teardown strands -------------------------------------------------------------


class _RaisingSession:
    def stop(self):
        raise RuntimeError("session refuses to die")


class _RecordingWaiter:
    def __init__(self):
        self.released = []

    def release(self, outcome, error):
        self.released.append((outcome, error))


def test_shutdown_strands_waiters_and_survives_raising_sessions():
    daemon = _idle_daemon()
    waiter = _RecordingWaiter()
    daemon._sessions.add(_RaisingSession())
    daemon._queue_waiters[77] = waiter
    try:
        daemon._shutdown()
    finally:
        daemon._queue_waiters.clear()
    assert waiter.released and "shutting down" in waiter.released[0][1]


# -- pid + probe classification ----------------------------------------------------


def test_pid_alive_matrix():
    assert _pid_alive(os.getpid()) is True
    gone = subprocess.Popen(["true"]); gone.wait()
    assert _pid_alive(gone.pid) is False
    assert _pid_alive(1) is True  # alive, merely not ours to signal


def test_probe_daemon_empty_reply_is_wedged(tmp_path):
    path = str(tmp_path / "empty.sock")
    thread = _reply_server(path, "")
    try:
        assert probe_daemon(timeout_sec=1.0, socket_path=path)[0] == "wedged"
    finally:
        thread.join(timeout=2)


def test_send_play_and_enqueue_read_failures_return_none(tmp_path):
    path = str(tmp_path / "rude.sock")
    thread = _accept_close_server(path)
    try:
        assert send_play({"text": "x"}, socket_path=path) is None
        assert send_enqueue({"text": "x"}, socket_path=path) is None
    finally:
        thread.join(timeout=2)


def test_delegate_enqueue_handles_keyboard_interrupt(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_mod, "ensure_daemon", lambda socket_path=None: "pong")

    def interrupted(_payload, socket_path=None):
        raise KeyboardInterrupt()

    monkeypatch.setattr(daemon_mod, "send_enqueue", interrupted)
    with pytest.raises(KeyboardInterrupt):
        delegate_enqueue({"text": "x"}, socket_path=str(tmp_path / "dead.sock"))


# -- ensure_daemon decisions (patched internals, no real spawn) ---------------------


def test_ensure_daemon_ok_wedged_kill_and_spawn_paths(monkeypatch, tmp_path):
    sock = str(tmp_path / "ensure.sock")
    calls = {"killed": 0, "spawned": 0}

    # deterministic script: wedged -> (kill ok) -> spawn -> ok
    script = [("wedged", None), ("ok", "pong")]
    def scripted_probe(_timeout, socket_path):
        return script.pop(0) if script else ("ok", "pong")

    monkeypatch.setattr(daemon_mod, "probe_daemon", scripted_probe)
    monkeypatch.setattr(daemon_mod, "_kill_wedged_daemon", lambda: calls.__setitem__("killed", calls["killed"] + 1) or True)
    monkeypatch.setattr(daemon_mod, "_spawn_daemon",
                        lambda idle_timeout_sec, socket_path=None: calls.__setitem__("spawned", calls["spawned"] + 1))
    assert ensure_daemon(socket_path=sock) == "pong"
    assert calls["killed"] == 1 and calls["spawned"] == 1

    # unreachable -> spawn -> ok
    script2 = [("unreachable", None), ("ok", "pong")]
    monkeypatch.setattr(daemon_mod, "probe_daemon",
                        lambda _timeout, socket_path: script2.pop(0) if script2 else ("ok", "pong"))
    assert ensure_daemon(socket_path=sock) == "pong"
    assert calls["spawned"] == 2

    # kill fails -> still spawns and verifies
    script3 = [("wedged", None), ("ok", "pong")]
    monkeypatch.setattr(daemon_mod, "probe_daemon",
                        lambda _timeout, socket_path: script3.pop(0) if script3 else ("ok", "pong"))
    monkeypatch.setattr(daemon_mod, "_kill_wedged_daemon", lambda: False)
    assert ensure_daemon(socket_path=sock) == "pong"
    assert calls["spawned"] == 3


def test_ensure_daemon_raises_when_it_never_comes_up(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_mod, "probe_daemon", lambda _timeout, socket_path: ("unreachable", None))
    monkeypatch.setattr(daemon_mod, "_kill_wedged_daemon", lambda: False)
    monkeypatch.setattr(daemon_mod, "_spawn_daemon", lambda idle_timeout_sec, socket_path=None: None)
    with pytest.raises(daemon_mod.DaemonUnavailableError):
        ensure_daemon(autostart_timeout_sec=0.05, socket_path=str(tmp_path / "never.sock"))


class _ErrSession:
    """Active-session double whose replies need the typed ERR: mapping."""

    def handle_ipc_command(self, cmd):
        return "ERR: seek rejected"


def test_session_err_reply_is_mapped_to_the_typed_schema():
    daemon = _idle_daemon()
    daemon.active_session = _ErrSession()
    try:
        assert daemon.handle_command("seek +5") == "ok=false error=seek rejected"
    finally:
        daemon.active_session = None


def test_signal_handlers_install_in_the_main_thread():
    import signal

    daemon = _idle_daemon()
    daemon._install_signal_handlers()
    try:
        # installed above; restore pytest's expected defaults afterwards
        pass
    finally:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def test_signal_handlers_without_sigterm_constant(monkeypatch):
    import signal

    monkeypatch.delattr(signal, "SIGTERM", raising=False)
    daemon = _idle_daemon()
    daemon._install_signal_handlers()  # must survive a platform without SIGTERM
    monkeypatch.undo()
    signal.signal(signal.SIGINT, signal.default_int_handler)


def test_kill_wedged_daemon_refuses_a_foreign_process(tmp_path, monkeypatch):
    import agent_tts.audio as audio_mod

    foreign = subprocess.Popen(["sleep", "30"])
    try:
        pid_file = tmp_path / "pid"
        pid_file.write_text(str(foreign.pid))
        monkeypatch.setattr(audio_mod, "PID_FILE", str(pid_file))
        assert daemon_mod._kill_wedged_daemon() is False  # never kills a non-agent process
        assert _pid_alive(foreign.pid) is True
    finally:
        foreign.kill()
        foreign.wait()
