"""Control-channel ownership tests (AT-09): election, theft prevention, crash recovery.

The control channel (IPC socket + lock/pid markers) must be earned, never
stolen: a player that starts while another one is playing may not remove the
live player's socket, expose its own channel, or clobber its marker files.
"""

import os
import time
from unittest import mock

import pytest

import agent_tts.ipc as ipc
from agent_tts import audio


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """Isolates the transient channel files into a tmp dir.

    Teardown runs the production cleanup so a winning test process never
    leaks the ownership lock into later tests.
    """
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


def _wait_for_reply(reply: str, socket_path: str, timeout_sec: float = 5.0) -> str:
    """Polls the channel until the expected reply arrives (or the deadline)."""
    deadline = time.monotonic() + timeout_sec
    last = None
    while time.monotonic() < deadline:
        last = ipc.send_ipc_command("status", socket_path=socket_path)
        if last == reply:
            return reply
        time.sleep(0.05)
    return last


def test_second_player_never_steals_the_live_channel(channel):
    """US-AT-09-1 regression: player B must never rob the channel of a live A.

    Scenario of the bug: A plays and binds the IPC socket; B starts and,
    today, unconditionally removes A's socket and binds its own — control
    commands then reach B while A keeps playing, permanently uncontrollable.
    """
    # Player A: playing and bound to the channel.
    audio._write_player_locks()
    a = ipc.IPCServer(command_handler=lambda cmd: "status=playing owner=A", socket_path=channel["sock"])
    a.start()
    assert _wait_for_reply("status=playing owner=A", channel["sock"]) == "status=playing owner=A"

    # Player B starts while A is still playing.
    audio._write_player_locks()
    b = ipc.IPCServer(command_handler=lambda cmd: "status=playing owner=B", socket_path=channel["sock"])
    b.start()

    try:
        # Control commands keep reaching A until the end...
        for _ in range(3):
            assert ipc.send_ipc_command("status", socket_path=channel["sock"]) == "status=playing owner=A"
            time.sleep(0.02)
        # ...and B never exposed the channel while A lives.
        assert b.server_sock is None
    finally:
        a.stop()
        b.stop()
        audio.cleanup_locks()
