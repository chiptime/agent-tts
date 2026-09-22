"""IPC transport for interactive agent-tts playback controls.

POSIX keeps the original AF_UNIX socket behavior byte-identical. Windows
(sys.platform == "win32") uses TCP on 127.0.0.1 with an ephemeral port
persisted to the ``agent-tts-ipc.port`` marker file next to the lock/pid
files; the client picks the transport by which marker/socket file exists.
"""

import os
import socket
import sys
import threading
from typing import Callable, Optional

from agent_tts.constants import IPC_PORT_FILE, IPC_SOCKET

CLIENT_TIMEOUT_SEC = 1.0
# Upper bound for a single reply. The server answers with one line, so the
# client keeps reading until the newline (or EOF/cap) instead of trusting a
# single recv() — long status payloads must not be truncated mid-field.
MAX_REPLY_BYTES = 8192


def _is_windows() -> bool:
    return sys.platform == "win32"


def server_socket(socket_path: str = IPC_SOCKET) -> socket.socket:
    """Creates the bound-but-not-listening IPC server socket for the current platform."""
    if _is_windows():
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
    if os.path.exists(socket_path):
        try:
            os.remove(socket_path)
        except OSError:
            pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(socket_path)
    return sock


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
        """Binds to socket and starts background listener thread."""
        try:
            self.server_sock = server_socket(self.socket_path)
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

            try:
                conn.settimeout(1.0)
                data = conn.recv(1024).decode("utf-8", errors="ignore").strip()
                if data:
                    reply = self.command_handler(data)
                    conn.sendall(f"{reply}\n".encode("utf-8"))
                conn.close()
            except Exception:
                pass

    def stop(self) -> None:
        """Stops listener and removes transport marker files."""
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
        for path in self._cleanup_paths():
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _cleanup_paths(self):
        if _is_windows():
            return [IPC_PORT_FILE]
        return [self.socket_path]
