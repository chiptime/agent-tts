"""Control-channel ownership tests (AT-09): election, theft prevention, crash recovery.

The control channel (IPC socket + lock/pid markers) must be earned, never
stolen: a player that starts while another one is playing may not remove the
live player's socket, expose its own channel, or clobber its marker files.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

import agent_tts.ipc as ipc
import agent_tts.ownership as ownership
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


def _child_env(channel):
    """Subprocess env pointing the transient files at the isolated channel."""
    env = dict(os.environ)
    env["AGENT_TTS_LOCK_FILE"] = channel["lock"]
    env["AGENT_TTS_PID_FILE"] = channel["pid"]
    env["AGENT_TTS_SOCKET"] = channel["sock"]
    return env


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


# --- Election mechanics (RF-AT-09-1) ------------------------------------------------


def test_election_loser_preserves_owner_markers_and_cleans_nothing(channel):
    """RF-AT-09-1/RF-AT-09-3: a losing player owns nothing and touches nothing.

    The loser neither clobbers the winner's lock/pid markers nor removes any
    channel file on its exit path.
    """
    audio._write_player_locks()  # this process wins the election
    owner_pid = str(os.getpid())
    assert ownership.owns_channel()

    child_code = (
        "import agent_tts.audio as audio\n"
        "import agent_tts.ownership as ownership\n"
        "audio._write_player_locks()\n"
        "print('OWNS', ownership.owns_channel(), flush=True)\n"
        "audio.cleanup_locks()\n"
        "print('DONE', flush=True)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", child_code],
        env=_child_env(channel),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    # The child lost the election and its cleanup removed nothing.
    assert "OWNS False" in proc.stdout
    assert os.path.exists(channel["lock"])
    assert os.path.exists(channel["pid"])
    with open(channel["lock"]) as f:
        assert f.read() == owner_pid
    with open(channel["pid"]) as f:
        assert f.read() == owner_pid


# --- Orphan reclaim (RF-AT-09-2) ----------------------------------------------------


def test_owner_reclaims_orphan_socket_after_connection_refusal(channel):
    """RF-AT-09-2: a leftover socket nobody serves is reclaimed by the owner.

    The orphan is detected by connection failure, never by a preemptive
    remove, and only the flock-elected owner may reclaim it.
    """
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(channel["sock"])
    stale.close()  # crash leftover: path exists, nobody serves it
    assert os.path.exists(channel["sock"])

    audio._write_player_locks()
    server = ipc.IPCServer(
        command_handler=lambda cmd: "status=playing owner=A", socket_path=channel["sock"]
    )
    server.start()
    try:
        assert server.server_sock is not None
        assert _wait_for_reply("status=playing owner=A", channel["sock"]) == "status=playing owner=A"
    finally:
        server.stop()
        audio.cleanup_locks()


def test_non_owner_never_reclaims_an_orphan_socket(channel):
    """RF-AT-09-2: without winning the election, an orphan path stays untouched."""
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(channel["sock"])
    stale.close()

    server = ipc.IPCServer(
        command_handler=lambda cmd: "status=playing owner=B", socket_path=channel["sock"]
    )
    server.start()
    try:
        assert server.server_sock is None  # no election won: no channel
    finally:
        server.stop()
    assert os.path.exists(channel["sock"])  # the orphan file was left in place


# --- Ownership-aware cleanup (RF-AT-09-3) -------------------------------------------


def test_non_owner_server_stop_keeps_transport_marker(channel):
    """RF-AT-09-3: IPCServer.stop() removes the transport only for its owner."""
    server = ipc.IPCServer(command_handler=lambda cmd: "ok", socket_path=channel["sock"])
    server.start()
    assert server.server_sock is not None
    server.stop()
    # This process never won the election: the transport file stays.
    assert os.path.exists(channel["sock"])


def test_owner_cleanup_removes_channel_files(channel):
    """RNF-AT-09-1: the owner's cleanup keeps the classic file contract.

    Same files at the same paths, removed on exit exactly as before the
    ownership fix — but only by the process that owns them.
    """
    audio._write_player_locks()
    server = ipc.IPCServer(command_handler=lambda cmd: "ok", socket_path=channel["sock"])
    server.start()
    assert server.server_sock is not None
    server.stop()
    audio.cleanup_locks()
    assert not os.path.exists(channel["sock"])
    assert not os.path.exists(channel["lock"])
    assert not os.path.exists(channel["pid"])
    assert not ownership.owns_channel()
