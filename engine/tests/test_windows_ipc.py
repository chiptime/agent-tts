"""Windows IPC portability and channel lifecycle tests.

Verifies:
1. AGENT_TTS_PORT_FILE environment variable override in constants.
2. Windows TCP server_socket preserves live marker (no second server overwrites).
3. Windows TCP server_socket with require_ownership=False reclaims stale/dead markers.
4. IPCServer.stop() only removes endpoint actually bound and owned by this server.
5. _pid_alive on Windows does not terminate inspected processes (safe Win32 query).
"""

import os
import socket
import sys

from agent_tts import constants, daemon, ipc, ownership
from agent_tts.ipc import IPCServer, server_socket


def test_agent_tts_port_file_env_override(monkeypatch):
    """Rule 1: AGENT_TTS_PORT_FILE environment variable overrides IPC_PORT_FILE."""
    custom = "/custom/path/agent-tts-ipc.port"
    monkeypatch.setenv("AGENT_TTS_PORT_FILE", custom)
    import importlib
    importlib.reload(constants)
    try:
        assert constants.IPC_PORT_FILE == custom
    finally:
        monkeypatch.delenv("AGENT_TTS_PORT_FILE", raising=False)
        importlib.reload(constants)


def test_windows_server_socket_never_overwrites_live_marker(tmp_path, monkeypatch):
    """Rule 2: Windows TCP server_socket returns None if a live server already owns the marker."""
    port_marker = str(tmp_path / "agent-tts-ipc.port")
    monkeypatch.setattr(ipc, "_is_windows", lambda: True)
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", port_marker)

    # First server: a real IPC server with an accept loop (a raw listen
    # socket has a backlog of one, so a second probe could not connect and
    # an unresponsive probe is exactly what "reclaim a wedged owner" wants).
    server1 = IPCServer(command_handler=lambda cmd: "pong", require_ownership=False)
    server1.start()
    assert server1.server_sock is not None

    try:
        # A second server (even with require_ownership=False or same process)
        # must NOT overwrite the live marker
        server2 = server_socket(require_ownership=False)
        assert server2 is None

        # Even if this process owns the channel, a second server must not
        # steal a live server's marker.
        monkeypatch.setattr(ownership, "owns_channel", lambda: True)
        monkeypatch.setattr(ipc, "owns_channel", lambda: True)
        server3 = server_socket(require_ownership=True)
        assert server3 is None

        # Verify the marker still points to server1
        with open(port_marker) as f:
            port = int(f.read().strip())
        assert port == server1.server_sock.getsockname()[1]
    finally:
        server1.stop()
        if os.path.exists(port_marker):
            os.remove(port_marker)


def test_windows_server_socket_reclaims_stale_marker(tmp_path, monkeypatch):
    """Rule 3: Windows TCP server_socket with require_ownership=False succeeds if marker is stale."""
    port_marker = str(tmp_path / "agent-tts-ipc.port")
    monkeypatch.setattr(ipc, "_is_windows", lambda: True)
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", port_marker)

    # Write a stale marker pointing to a closed port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()

    with open(port_marker, "w") as f:
        f.write(str(dead_port))

    # Binding with require_ownership=False on a stale marker must succeed
    server = server_socket(require_ownership=False)
    assert server is not None
    try:
        with open(port_marker) as f:
            new_port = int(f.read().strip())
        assert new_port == server.getsockname()[1]
        assert new_port != dead_port
    finally:
        server.close()
        if os.path.exists(port_marker):
            os.remove(port_marker)


def test_windows_ipc_server_failed_start_stop_leaves_winner_marker(tmp_path, monkeypatch):
    """Rule 4: IPCServer.stop() on a server that failed to start does not remove the live marker."""
    port_marker = str(tmp_path / "agent-tts-ipc.port")
    monkeypatch.setattr(ipc, "_is_windows", lambda: True)
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", port_marker)

    winner = IPCServer(command_handler=lambda cmd: "pong", require_ownership=False)
    winner.start()
    assert winner.server_sock is not None
    assert os.path.exists(port_marker)

    try:
        # Loser server tries to start, fails
        loser = IPCServer(command_handler=lambda cmd: "pong2", require_ownership=False)
        loser.start()
        # Even if process owns channel locks:
        monkeypatch.setattr(ownership, "owns_channel", lambda: True)
        monkeypatch.setattr(ipc, "owns_channel", lambda: True)

        # Loser calls stop(): winner's marker must NOT be removed
        loser.stop()
        assert os.path.exists(port_marker)
    finally:
        winner.stop()


def test_windows_pid_alive_safe_query(monkeypatch):
    """Rule 5: _pid_alive uses safe non-terminating query on Windows."""
    # Test current process is alive on POSIX
    assert daemon._pid_alive(os.getpid()) is True

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(daemon, "_is_windows", lambda: True, raising=False)

    # Test invalid / non-positive pid on Windows
    assert daemon._pid_alive(0) is False
    assert daemon._pid_alive(-1) is False

    # Mock win32 seam to verify HANDLE width and CloseHandle calls
    closed_handles = []

    class DummyKernel32:
        def OpenProcess(self, dwDesiredAccess, bInheritHandle, dwProcessId):
            if dwProcessId == 12345:
                return 0x7FFFFFFF  # large handle width (64-bit safe)
            elif dwProcessId == 99999:
                return 0  # does not exist
            elif dwProcessId == 1:
                return 0  # access denied
            return 0

        def GetLastError(self):
            return 5  # ERROR_ACCESS_DENIED

        def WaitForSingleObject(self, hHandle, dwMilliseconds):
            if hHandle == 0x7FFFFFFF:
                return 0x00000102  # WAIT_TIMEOUT -> still active
            return 0  # WAIT_OBJECT_0 -> terminated

        def CloseHandle(self, hHandle):
            closed_handles.append(hHandle)
            return 1

    import ctypes
    fake_ctypes = DummyKernel32()
    monkeypatch.setattr(ctypes, "windll", type("Windll", (), {"kernel32": fake_ctypes})(), raising=False)

    # Calling _pid_alive on active PID 12345 returns True and closes handle
    assert daemon._pid_alive(12345) is True
    assert 0x7FFFFFFF in closed_handles

    # Calling on access-denied PID 1 returns True (conservative liveness)
    assert daemon._pid_alive(1) is True
