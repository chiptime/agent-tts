"""Control-channel ownership tests (AT-09): election, theft prevention, crash recovery.

The control channel (IPC socket + lock/pid markers) must be earned, never
stolen: a player that starts while another one is playing may not remove the
live player's socket, expose its own channel, or clobber its marker files.
"""

import os
import signal
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


# --- Crash recovery (US-AT-09-2, RNF-AT-09-3) ---------------------------------------


def test_owner_killed_with_sigkill_leaves_channel_reclaimable(channel):
    """US-AT-09-2/RNF-AT-09-3: a SIGKILLed owner frees the channel by itself.

    The kernel drops the flock when the owner dies, so the next startup
    wins the election and reclaims the orphaned socket with no manual
    intervention — the stale socket file is never removed by hand.
    """
    child_code = (
        "import sys\n"
        "import time\n"
        "import agent_tts.audio as audio\n"
        "import agent_tts.ipc as ipc\n"
        "audio._write_player_locks()\n"
        "server = ipc.IPCServer(\n"
        "    command_handler=lambda cmd: 'status=playing owner=child',\n"
        "    socket_path=sys.argv[1],\n"
        ")\n"
        "server.start()\n"
        "print('READY', flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code, channel["sock"]],
        env=_child_env(channel),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # The child owns the channel and serves commands.
        assert _wait_for_reply("status=playing owner=child", channel["sock"]) == (
            "status=playing owner=child"
        )
        assert os.path.exists(channel["sock"])

        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    # The owner died without cleanup: the socket file remains, orphaned.
    assert os.path.exists(channel["sock"])

    # Next startup wins the election (the kernel already dropped the flock)
    # and reclaims the channel; no test removes the stale socket by hand.
    audio._write_player_locks()
    assert ownership.owns_channel()
    successor = ipc.IPCServer(
        command_handler=lambda cmd: "status=playing owner=successor",
        socket_path=channel["sock"],
    )
    successor.start()
    try:
        assert successor.server_sock is not None
        assert _wait_for_reply("status=playing owner=successor", channel["sock"]) == (
            "status=playing owner=successor"
        )
    finally:
        successor.stop()
        audio.cleanup_locks()


# --- Transport parity contract (RF-AT-09-5) -----------------------------------------

_OWNER_CHILD = (
    "import sys\n"
    "import time\n"
    "import agent_tts.audio as audio\n"
    "import agent_tts.ipc as ipc\n"
    "sock, marker, transport = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "if transport == 'tcp':\n"
    "    ipc._is_windows = lambda: True\n"
    "    ipc.IPC_PORT_FILE = marker\n"
    "audio._write_player_locks()\n"
    "server = ipc.IPCServer(\n"
    "    command_handler=lambda cmd: 'status=playing owner=A',\n"
    "    socket_path=sock,\n"
    ")\n"
    "server.start()\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n"
)


@pytest.mark.parametrize("transport", ["unix", "tcp"])
def test_ownership_election_contract_over_both_transports(
    transport, channel, tmp_path, monkeypatch
):
    """RF-AT-09-5: the same election scenario over AF_UNIX and TCP loopback.

    Windows has no AF_UNIX, so its transport is TCP + IPC_PORT_FILE; this
    runs the identical owner/loser/successor scenario through both transport
    branches (the TCP side reuses the existing _is_windows branch) to prove
    the ownership semantics do not diverge per transport.
    """
    marker = str(tmp_path / "ipc.port")
    if transport == "tcp":
        monkeypatch.setattr(ipc, "_is_windows", lambda: True)
        monkeypatch.setattr(ipc, "IPC_PORT_FILE", marker)

    proc = subprocess.Popen(
        [sys.executable, "-c", _OWNER_CHILD, channel["sock"], marker, transport],
        env=_child_env(channel),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Player A owns the channel and serves over this transport.
        assert _wait_for_reply("status=playing owner=A", channel["sock"]) == (
            "status=playing owner=A"
        )

        # Player B starts while A lives: loses the election, never exposes
        # the channel, and A keeps answering.
        audio._write_player_locks()
        assert not ownership.owns_channel()
        b = ipc.IPCServer(
            command_handler=lambda cmd: "status=playing owner=B", socket_path=channel["sock"]
        )
        b.start()
        try:
            assert b.server_sock is None
            assert ipc.send_ipc_command("status", socket_path=channel["sock"]) == (
                "status=playing owner=A"
            )
        finally:
            b.stop()

        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    # The successor wins the election and reclaims the channel over the
    # same transport: AF_UNIX orphan path or stale TCP port marker.
    audio._write_player_locks()
    assert ownership.owns_channel()
    successor = ipc.IPCServer(
        command_handler=lambda cmd: "status=playing owner=successor",
        socket_path=channel["sock"],
    )
    successor.start()
    try:
        assert successor.server_sock is not None
        assert _wait_for_reply("status=playing owner=successor", channel["sock"]) == (
            "status=playing owner=successor"
        )
    finally:
        successor.stop()
        audio.cleanup_locks()


# --- Bind-failure diagnostics -------------------------------------------------------


def test_unexpected_bind_failure_warns_on_stderr(tmp_path, capsys):
    """A bind failure that is not EADDRINUSE must not be silent.

    EADDRINUSE is the expected contended-channel case and handled
    deliberately; any other errno (missing parent directory, permissions,
    path too long, read-only fs) means interactive control is unavailable
    for a reason the user must see. The normal None returns (live channel,
    lost election, Windows non-owner) stay quiet — one warning line only
    here.
    """
    # Missing parent directory: bind fails with ENOENT, not EADDRINUSE.
    unbindable = str(tmp_path / "no-such-dir" / "player.sock")

    assert ipc.server_socket(socket_path=unbindable) is None

    err = capsys.readouterr().err
    assert "ipc:" in err  # component-prefixed, like "winhost:" in winhost.py
    assert unbindable in err  # names what failed
    assert "No such file or directory" in err  # carries the underlying error
    assert "interactive control unavailable" in err  # states the consequence


def test_orphan_reclaim_failure_warns_on_stderr(tmp_path, monkeypatch, capsys):
    """A reclaim failure after winning the election must not be silent.

    By the retry point this process owns the channel, so a failed reclaim
    leaves it without interactive control — and when the rebind is what
    fails, the old socket file is already gone. The warning therefore
    reports the failed reclaim without claiming the file's fate: the
    OSError may come from the remove (orphan still on disk — the real
    read-only-directory case forced here) or from the rebind (path
    cleared, then left unusable).
    """
    monkeypatch.setattr(audio, "LOCK_FILE", str(tmp_path / "playing.lock"))
    monkeypatch.setattr(audio, "PID_FILE", str(tmp_path / "current.pid"))
    monkeypatch.setattr(audio, "IPC_SOCKET", str(tmp_path / "player.sock"))

    sockdir = tmp_path / "sockdir"
    sockdir.mkdir()
    orphan = str(sockdir / "player.sock")
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(orphan)
    stale.close()  # crash leftover: path exists, nobody serves it

    audio._write_player_locks()  # win the election: the reclaim is authorized
    sockdir.chmod(0o500)  # remove() inside now fails with EACCES

    try:
        assert ipc.server_socket(socket_path=orphan) is None
        assert os.path.exists(orphan)  # the remove failed: orphan survives
    finally:
        sockdir.chmod(0o700)
        audio.cleanup_locks()

    err = capsys.readouterr().err
    assert "ipc:" in err  # component-prefixed, like the bind-failure warning
    assert orphan in err  # names what failed
    assert "Permission denied" in err  # carries the underlying error
    assert "interactive control unavailable" in err  # states the consequence
