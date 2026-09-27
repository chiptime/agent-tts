"""Concurrent IPC connection tests: a blocking command must not starve others.

The daemon's play command lives on its connection until playback ends
(RF-AT-04-3), while status/pause/stop keep arriving on their own
short-lived connections (RF-AT-04-2). The listener therefore serves each
connection on its own thread.
"""

import threading
import time

from agent_tts.ipc import IPCServer, send_ipc_command


def test_status_is_answered_while_another_command_blocks(tmp_path):
    """A long-running handler on one connection must not delay other commands."""
    sock_file = str(tmp_path / "player.sock")

    blocked = threading.Event()
    release = threading.Event()

    def handler(cmd: str) -> str:
        if cmd == "block":
            blocked.set()
            release.wait(timeout=10.0)
            return "status=unblocked"
        return "status=playing"

    # Opt out of the ownership default: this suite exercises raw per-connection
    # concurrency on a private temp path, no election involved.
    server = IPCServer(command_handler=handler, socket_path=sock_file, require_ownership=False)
    server.start()
    try:
        # A first client sends a command whose handler blocks.
        blocker_reply = []
        blocker_errors = []

        def run_blocker():
            try:
                blocker_reply.append(send_ipc_command("block", socket_path=sock_file))
            except Exception as e:  # pragma: no cover - failure path only
                blocker_errors.append(e)

        blocker = threading.Thread(target=run_blocker, daemon=True)
        blocker.start()
        assert blocked.wait(timeout=5.0), "blocking command never reached its handler"

        # While that handler is stuck, a status on a NEW connection is
        # still served promptly.
        deadline = time.monotonic() + 5.0
        reply = None
        while time.monotonic() < deadline:
            reply = send_ipc_command("status", socket_path=sock_file)
            if reply == "status=playing":
                break
            time.sleep(0.02)
        assert reply == "status=playing"

        release.set()
        blocker.join(timeout=5.0)
        assert not blocker_errors
        assert blocker_reply == ["status=unblocked"]
    finally:
        release.set()
        server.stop()
