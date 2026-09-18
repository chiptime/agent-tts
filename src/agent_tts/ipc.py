"""Unix socket IPC protocol for interactive agent-tts playback controls."""

import os
import socket
import threading
from typing import Callable, Optional

from agent_tts.constants import IPC_SOCKET


def send_ipc_command(command: str, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends an IPC command to the currently running audio player."""
    if not os.path.exists(socket_path):
        return None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1.0)
        client.connect(socket_path)
        client.sendall(f"{command.strip()}\n".encode("utf-8"))
        res = client.recv(1024).decode("utf-8", errors="ignore").strip()
        client.close()
        return res
    except Exception:
        return None


class IPCServer:
    """Threaded UNIX domain socket server for controlling playback sessions."""

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
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass

        try:
            self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server_sock.bind(self.socket_path)
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
        """Stops listener and removes socket file."""
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
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
