"""Independent Windows IPC probes retained as cross-platform regressions."""

import ctypes
import hashlib
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_tts import daemon, ipc, ownership

from ipc_test_support import assert_channel_closed


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def tcp(tmp_path, monkeypatch):
    marker = tmp_path / "channel.port"
    monkeypatch.setattr(ipc, "_is_windows", lambda: True)
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", str(marker))
    monkeypatch.setattr(ownership, "IPC_PORT_FILE", str(marker))
    monkeypatch.setenv("AGENT_TTS_PORT_FILE", str(marker))
    ownership.release_channel_ownership()
    yield marker
    ownership.release_channel_ownership()


def test_owner_gate_and_failed_stop_preserve_marker_bytes(tcp, tmp_path):
    tcp.write_bytes(b"invalid marker")
    before = digest(tcp)
    loser = ipc.IPCServer(lambda _: "loser")
    loser.start()
    assert loser.server_sock is None
    loser.stop()
    assert digest(tcp) == before

    assert ownership.acquire_channel_ownership(str(tmp_path / "owner.lock"))
    winner = ipc.IPCServer(lambda _: "pong")
    winner.start()
    try:
        assert ipc.send_ipc_command("ping") == "pong"
        before = digest(tcp)
        second = ipc.IPCServer(lambda _: "stolen")
        second.start()
        assert second.server_sock is None
        second.stop()
        assert digest(tcp) == before
        assert ipc.send_ipc_command("ping") == "pong"
    finally:
        winner.stop()
    assert_channel_closed(str(tcp))

    successor = ipc.IPCServer(lambda _: "next")
    successor.start()
    try:
        before = digest(tcp)
        winner.stop()
        assert digest(tcp) == before
        assert ipc.send_ipc_command("ping") == "next"
    finally:
        successor.stop()


@pytest.mark.parametrize("payload", [b"", b"invalid", b"0"])
def test_explicit_unowned_stale_reclaim(tcp, payload):
    tcp.write_bytes(payload)
    server = ipc.server_socket(require_ownership=False)
    assert server is not None
    try:
        server.listen(8)
        assert int(tcp.read_text()) == server.getsockname()[1]
        before = digest(tcp)
        assert ipc.server_socket(require_ownership=False) is None
        assert digest(tcp) == before
    finally:
        server.close()


@pytest.mark.parametrize(
    "wait, code, expected",
    [(258, 0, True), (0, 259, False), (0xFFFFFFFF, 259, True), (0xFFFFFFFF, 0, False)],
)
def test_win32_query_handle_width_and_closure(monkeypatch, wait, code, expected):
    handle = 0x1234567887654321
    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=handle),
        WaitForSingleObject=Mock(return_value=wait),
        CloseHandle=Mock(return_value=1),
    )

    def exit_code(open_handle, pointer):
        pointer._obj.value = code
        return 1

    kernel.GetExitCodeProcess = Mock(side_effect=exit_code)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    kill = Mock(side_effect=AssertionError("must not signal inspected PID"))
    monkeypatch.setattr(os, "kill", kill)

    assert daemon._pid_alive_win32(123) is expected
    assert kernel.OpenProcess.restype is ctypes.c_void_p
    kernel.CloseHandle.assert_called_once_with(handle)
    kill.assert_not_called()


@pytest.mark.parametrize("error, expected", [(5, True), (87, False)])
def test_win32_missing_and_denied(monkeypatch, error, expected):
    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=0),
        GetLastError=Mock(return_value=error),
        WaitForSingleObject=Mock(),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    assert daemon._pid_alive_win32(123) is expected
    kernel.CloseHandle.assert_not_called()


def test_win32_exception_still_closes(monkeypatch):
    handle = 0x1234567887654321
    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=handle),
        WaitForSingleObject=Mock(side_effect=OSError("query failed")),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    with pytest.raises(OSError, match="query failed"):
        daemon._pid_alive_win32(123)
    kernel.CloseHandle.assert_called_once_with(handle)


def test_port_override_inherited_without_global_write(tcp):
    result = subprocess.run(
        [sys.executable, "-c", "from agent_tts.constants import IPC_PORT_FILE; print(IPC_PORT_FILE)"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == str(tcp) + "\n"
    assert_channel_closed(str(tcp))


def test_interprocess_owner_and_marker_are_preserved(tcp, tmp_path):
    lock = str(tmp_path / "child.lock")
    code = """import sys
from agent_tts import ipc, ownership
ipc._is_windows = lambda: True
ipc.IPC_PORT_FILE = sys.argv[2]
ownership.IPC_PORT_FILE = sys.argv[2]
assert ownership.acquire_channel_ownership(sys.argv[1])
server = ipc.IPCServer(lambda _: 'child')
server.start()
assert server.server_sock is not None
print('READY', flush=True)
sys.stdin.read(1)
server.stop()
ownership.release_channel_ownership()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, lock, str(tcp)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline() == "READY\n"
        assert not ownership.acquire_channel_ownership(lock)
        before = digest(tcp)
        loser = ipc.IPCServer(lambda _: "loser")
        loser.start()
        assert loser.server_sock is None
        loser.stop()
        assert digest(tcp) == before
        assert ipc.send_ipc_command("ping") == "child"
    finally:
        _, error = child.communicate(input="q", timeout=10)
    assert child.returncode == 0, error
    assert_channel_closed(str(tcp))


@pytest.mark.parametrize(
    "image, expected",
    [("python.exe", True), ("PythonW.EXE", True), ("sleep.exe", False), (None, False)],
)
def test_win32_daemon_identity_is_required_before_a_kill(monkeypatch, image, expected):
    """A wedged-owner kill needs a verified identity; unknown means no."""
    monkeypatch.setattr(daemon, "_is_windows", lambda: True)
    monkeypatch.setattr(daemon, "_windows_pid_image_name", lambda pid: image)
    assert daemon._pid_looks_like_agent_tts(4242) is expected


def test_posix_unknown_identity_refuses_a_kill(monkeypatch):
    """No /proc entry (recycled or unreadable PID) is not "ours"."""
    monkeypatch.setattr(daemon, "_is_windows", lambda: False)
    assert daemon._pid_looks_like_agent_tts(4_000_000) is False
