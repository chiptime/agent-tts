"""Test support helpers for cross-platform IPC testing.

Provides helpers for isolating channel files across POSIX and Windows,
resolving platform-appropriate endpoints, and running portable test servers.
"""

import sys

from agent_tts import audio, constants, ipc, ownership


def channel_endpoint(channel: dict) -> str:
    """Returns the platform-correct transport endpoint file for channel assertions.

    On POSIX, the endpoint is a Unix socket (.sock).
    On Windows, the endpoint is the TCP loopback port marker (.port).
    """
    if sys.platform == "win32":
        return channel.get("port", channel.get("sock"))
    return channel["sock"]


def setup_channel_paths(tmp_path, monkeypatch) -> dict:
    """Isolates all transient channel files (lock, pid, sock, port) into tmp_path."""
    paths = {
        "lock": str(tmp_path / "playing.lock"),
        "pid": str(tmp_path / "current.pid"),
        "sock": str(tmp_path / "player.sock"),
        "port": str(tmp_path / "agent-tts-ipc.port"),
    }
    monkeypatch.setattr(audio, "LOCK_FILE", paths["lock"])
    monkeypatch.setattr(audio, "PID_FILE", paths["pid"])
    monkeypatch.setattr(audio, "IPC_SOCKET", paths["sock"])
    monkeypatch.setattr(constants, "IPC_PORT_FILE", paths["port"])
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", paths["port"])
    monkeypatch.setattr(ownership, "IPC_PORT_FILE", paths["port"])
    monkeypatch.setenv("AGENT_TTS_PORT_FILE", paths["port"])
    return paths


def assert_channel_closed(endpoint: str) -> None:
    """The endpoint no longer names a channel a client could use.

    POSIX removes the socket file; Windows cannot unlink the marker while
    the owner still holds its ownership lock (a mandatory byte-range lock
    keeps it open), so it is truncated instead. Both outcomes must leave
    nothing reachable, and POSIX must additionally leave no file behind.
    """
    from agent_tts import ipc

    assert not ipc._channel_is_live(endpoint), f"endpoint still answers: {endpoint}"
    if not ipc._is_windows():
        import os

        assert not os.path.exists(endpoint), f"endpoint file left behind: {endpoint}"
