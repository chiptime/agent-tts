"""IPC transport for interactive agent-tts playback controls.

POSIX keeps the original AF_UNIX socket behavior byte-identical. Windows
(sys.platform == "win32") uses TCP on 127.0.0.1 with an ephemeral port
persisted to the ``agent-tts-ipc.port`` marker file next to the lock/pid
files; the client picks the transport by which marker/socket file exists.

The channel is owned, never stolen (RF-AT-09-2): an existing transport that
answers connections belongs to a live server and is left alone; an orphaned
one is detected by connection failure and reclaimed only by the process
that won the ownership election (see agent_tts.ownership).
"""

import errno
import os
import socket
import sys
import threading
from typing import Callable, Optional

from agent_tts.constants import IPC_PORT_FILE, IPC_SOCKET
from agent_tts.ownership import owns_channel

CLIENT_TIMEOUT_SEC = 1.0
# Upper bound for a single reply. The server answers with one line, so the
# client keeps reading until the newline (or EOF/cap) instead of trusting a
# single recv() — long status payloads must not be truncated mid-field.
MAX_REPLY_BYTES = 8192
# Server-side mirror of MAX_REPLY_BYTES (RF-AT-09-4): commands are one line
# too, so the server reads until the newline (or cap) — a future one-line
# JSON command payload must not be truncated by a single recv() either.
MAX_COMMAND_BYTES = 8192


def _is_windows() -> bool:
    return sys.platform == "win32"


def server_socket(socket_path: str = IPC_SOCKET) -> Optional[socket.socket]:
    """Creates the bound IPC server socket for the current platform.

    Never steals a live channel (RF-AT-09-2): a socket path that answers
    connections belongs to a live server and is left untouched; an orphaned
    path (connection refused) is reclaimed only by the flock-elected owner.
    On Windows the port marker is the channel: a non-owner never overwrites
    a marker it does not own (RF-AT-09-5). Returns None — no channel — when
    this process may not expose the transport.
    """
    if _is_windows():
        if os.path.exists(IPC_PORT_FILE) and not owns_channel():
            return None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            with open(IPC_PORT_FILE, "w") as f:
                f.write(str(port))
        except OSError:
            pass
        return sock
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(socket_path)
        return sock
    except OSError as exc:
        try:
            sock.close()
        except OSError:
            pass
        if exc.errno != errno.EADDRINUSE:
            # Only this branch warns: any other errno (permissions, missing
            # parent directory, path too long, read-only fs) silently kills
            # interactive control, and the user must know why. The remaining
            # None returns (live channel, lost election, Windows non-owner)
            # are normal, expected operation and stay quiet.
            print(
                f"ipc: cannot bind control socket {socket_path}: {exc}; "
                "interactive control unavailable",
                file=sys.stderr,
            )
            return None
        if _channel_is_live(socket_path):
            # Live server on this path: the channel is never stolen.
            return None
        if not owns_channel():
            # Orphaned path, but the election was lost: no reclaim either.
            return None
        retry = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.remove(socket_path)
            retry.bind(socket_path)
            return retry
        except OSError as exc:
            try:
                retry.close()
            except OSError:
                pass
            # Warn for the same reason as the non-EADDRINUSE bind failure
            # above: this process won the election, so a silent None here
            # leaves the channel dead with no explanation. The OSError may
            # come from the remove (orphan still on disk) or from the rebind
            # (path already cleared and left unusable), so the wording must
            # not claim the file's fate — only that the reclaim failed,
            # which is true in both cases.
            print(
                f"ipc: cannot reclaim orphaned control socket {socket_path}: {exc}; "
                "interactive control unavailable",
                file=sys.stderr,
            )
            return None


def _channel_is_live(socket_path: str) -> bool:
    """Returns True when a server answers on the existing socket path.

    Orphan detection for RF-AT-09-2: a connect that is refused means no
    process serves the path anymore, so the file is a leftover, not a
    channel. A successful probe connects and closes without sending data.
    """
    try:
        probe = connect_to_server(socket_path)
    except Exception:
        return False
    if probe is None:
        return False
    try:
        probe.close()
    except OSError:
        pass
    return True


def connect_to_server(socket_path: str = IPC_SOCKET) -> Optional[socket.socket]:
    """Connects to the IPC server, selecting the transport by marker/socket file existence.

    Returns a connected socket, or None when no server is reachable.
    """
    if _is_windows():
        if not os.path.exists(IPC_PORT_FILE):
            return None
        try:
            with open(IPC_PORT_FILE) as f:
                port = int(f.read().strip())
        except (OSError, ValueError):
            return None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CLIENT_TIMEOUT_SEC)
        sock.connect(("127.0.0.1", port))
        return sock
    if not os.path.exists(socket_path):
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(CLIENT_TIMEOUT_SEC)
    sock.connect(socket_path)
    return sock


def ipc_reply_json(reply: str) -> str:
    """Converts one engine IPC reply into a single-line JSON object.

    The reply wire format is `key=value` tokens whose values never contain
    spaces, optionally followed by ONE free-text field (`text=...`) that
    keeps its spaces. Unknown shapes degrade to {"raw": reply} — this never
    raises, so hosts can switch to it without new failure modes.
    """
    import json

    obj: dict = {}
    rest = reply
    if reply.startswith("text="):
        obj["text"] = reply[len("text="):]
        rest = ""
    elif " text=" in reply:
        rest, text = reply.split(" text=", 1)
        obj["text"] = text
    for token in rest.split():
        if "=" in token:
            key, value = token.split("=", 1)
            obj[key] = value
    if not obj:
        obj = {"raw": reply}
    return json.dumps(obj, ensure_ascii=False)


def send_ipc_command(command: str, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends an IPC command to the currently running audio player."""
    try:
        client = connect_to_server(socket_path)
    except Exception:
        return None
    if client is None:
        return None
    try:
        client.sendall(f"{command.strip()}\n".encode("utf-8"))
        chunks = []
        received = 0
        try:
            while received < MAX_REPLY_BYTES:
                chunk = client.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if b"\n" in chunk:
                    break
        except Exception:
            # Fall through with whatever arrived before the failure; only an
            # empty exchange degrades to None (same as the old single recv).
            pass
        if not chunks:
            return None
        return b"".join(chunks).decode("utf-8", errors="ignore").strip()
    except Exception:
        return None
    finally:
        try:
            client.close()
        except OSError:
            pass


def _recv_command(conn: socket.socket) -> str:
    """Reads one newline-terminated command from the connection.

    Symmetric to the client's reply loop: keeps recv()ing until the newline
    (or the MAX_COMMAND_BYTES cap / EOF), so a command larger than one
    packet arrives complete instead of truncated (RF-AT-09-4).
    """
    chunks = []
    received = 0
    while received < MAX_COMMAND_BYTES:
        chunk = conn.recv(1024)
        if not chunk:
            break
        chunks.append(chunk)
        received += len(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).decode("utf-8", errors="ignore").strip()


class IPCServer:
    """Threaded IPC server (AF_UNIX on POSIX, TCP loopback on Windows) for controlling playback sessions."""

    def __init__(
        self,
        command_handler: Callable[[str], str],
        socket_path: str = IPC_SOCKET,
    ):
        self.command_handler = command_handler
        self.socket_path = socket_path
        self.server_sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Binds to socket and starts background listener thread.

        A process that may not expose the channel (lost the ownership
        election, or a live server owns the transport) simply runs without
        IPC: start() leaves the server unset instead of stealing anything.
        """
        try:
            self.server_sock = server_socket(self.socket_path)
            if self.server_sock is None:
                return
            self.server_sock.listen(5)
            self.server_sock.settimeout(0.5)
            self._running = True

            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
        except Exception:
            self.server_sock = None

    def _listen_loop(self) -> None:
        while self._running and self.server_sock:
            try:
                conn, _ = self.server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            # One thread per connection: a command handler that blocks for a
            # long time (the daemon's play, which lives until playback ends)
            # must not starve concurrent short-lived control commands
            # (status/pause/stop) arriving on their own connections.
            threading.Thread(target=self._serve_connection, args=(conn,), daemon=True).start()

    def _serve_connection(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(1.0)
            data = _recv_command(conn)
            if data:
                reply = self.command_handler(data)
                conn.sendall(f"{reply}\n".encode("utf-8"))
            conn.close()
        except Exception:
            pass

    def stop(self) -> None:
        """Stops listener and removes the transport markers this process owns."""
        self._running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
        if self.thread:
            self.thread.join(timeout=0.3)
            self.thread = None
        # Ownership-aware transport cleanup (RF-AT-09-3): only the elected
        # owner removes the marker; a losing process leaves the winner's
        # transport in place.
        owned = owns_channel()
        for path in self._cleanup_paths():
            if owned and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _cleanup_paths(self):
        if _is_windows():
            return [IPC_PORT_FILE]
        return [self.socket_path]
