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
# Hard bound for a single command line (server side of RF-AT-09-4's line
# framing). Control commands are tiny, but a delegated ``play <json>``
# carries the full speech text in one line: the bound is sized for payloads
# of at least 1 MB of text plus JSON-escaping headroom (every control char
# can grow to six bytes as ``\uXXXX``). A line longer than the bound is
# rejected with an explicit ``ERR: command too large`` reply instead of
# being silently truncated into a broken JSON parse.
MAX_COMMAND_BYTES = 16 * 1024 * 1024
# Fallback deadline for the over-cap probe when the connection carries no
# timeout of its own (a blocking socket): the probe must always be bounded.
OVERCAP_PROBE_TIMEOUT_SEC = 1.0


class CommandTooLargeError(Exception):
    """A command line exceeded MAX_COMMAND_BYTES without a newline."""


def _probe_past_cap(conn: socket.socket) -> bool:
    """True when the sender pushes past MAX_COMMAND_BYTES.

    Bounded blocking read under the connection's own timeout: a sender
    that delivered exactly MAX_COMMAND_BYTES bytes and stopped keeps the
    BLOQUE 1.1 behavior (the recv times out, the payload is served at the
    cap), while a sender that pushes past the cap — even one that pauses
    at the cap and continues later — is detected as long as its next byte
    lands within the deadline. This replaces the old instantaneous
    non-blocking probe, which only saw bytes ALREADY buffered at the
    probe instant and accepted a truncated prefix from any sender that
    paused at exactly the cap (RC-1).

    The socket's timeout survives the probe (RS-3): no setblocking()
    dance, so a stalled peer can never turn later sends into an
    unbounded block. A blocking socket gets the fixed fallback deadline
    and its blocking mode restored.
    """
    timeout = conn.gettimeout()
    if timeout is None:
        conn.settimeout(OVERCAP_PROBE_TIMEOUT_SEC)
    try:
        return bool(conn.recv(1))
    except socket.timeout:
        return False
    except OSError:
        # Peer vanished mid-probe: no further byte can arrive; treat as
        # stopped-at-cap so the buffered payload is served as before.
        return False
    finally:
        if timeout is None:
            conn.settimeout(None)


def _discard_line_remainder(conn: socket.socket) -> None:
    """Discards the rest of an oversized line, bounded by cap and timeout.

    Keeps recv()ing (and throwing away) bytes until the newline, EOF, an
    additional MAX_COMMAND_BYTES bound, or a receive timeout — whichever
    comes first — so an over-cap sender can finish its sendall and read
    the explicit error reply instead of dying on a broken pipe (RS-4).
    """
    discarded = 0
    while discarded < MAX_COMMAND_BYTES:
        try:
            chunk = conn.recv(65536)
        except socket.timeout:
            return
        except OSError:
            return
        if not chunk:
            return
        discarded += len(chunk)
        if b"\n" in chunk:
            return


def _is_windows() -> bool:
    return sys.platform == "win32"


def server_socket(
    socket_path: str = IPC_SOCKET,
    require_ownership: bool = False,
) -> Optional[socket.socket]:
    """Creates the bound IPC server socket for the current platform.

    Never steals a live channel (RF-AT-09-2): a socket path that answers
    connections belongs to a live server and is left untouched; an orphaned
    path (connection refused) is reclaimed only by the flock-elected owner.
    On Windows the port marker is the channel: a non-owner never overwrites
    a marker it does not own (RF-AT-09-5). Returns None — no channel — when
    this process may not expose the transport.

    With ``require_ownership`` the election gates every path, a free one
    included (US-AT-09-1): a process that has not won the ownership lock
    never binds first, so it cannot expose the channel while the elected
    owner is still alive but unbound.
    """
    if _is_windows():
        if not owns_channel() and (require_ownership or os.path.exists(IPC_PORT_FILE)):
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
    if require_ownership and not owns_channel():
        # Lost (or never entered) the election: a free path is not ours to
        # take. The live-channel and orphan-reclaim branches below already
        # refuse non-owners; this closes the free-path gap so the loser can
        # never expose the channel before the elected owner binds.
        return None
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
    (or EOF), so a command larger than one packet arrives complete instead
    of truncated (RF-AT-09-4). The only bound is the MAX_COMMAND_BYTES hard
    cap. A line that crosses the cap with no newline is rejected
    DETERMINISTICALLY (RC-1): bytes already read past the cap reject
    immediately, and a line sitting exactly at the cap rejects unless the
    sender provably stopped there (the bounded probe waits out the
    connection timeout with no further byte) — oversized input must fail
    with a clear error, never a silent mid-JSON truncation (CONF-1).
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
    data = b"".join(chunks)
    if b"\n" not in data:
        if received > MAX_COMMAND_BYTES:
            # A single recv already crossed the cap: over-cap by evidence
            # in hand, no probe needed.
            raise CommandTooLargeError(
                f"command too large (over {MAX_COMMAND_BYTES} bytes without a newline)"
            )
        if received == MAX_COMMAND_BYTES and _probe_past_cap(conn):
            raise CommandTooLargeError(
                f"command too large (over {MAX_COMMAND_BYTES} bytes without a newline)"
            )
    return data.decode("utf-8", errors="ignore").strip()


class IPCServer:
    """Threaded IPC server (AF_UNIX on POSIX, TCP loopback on Windows) for controlling playback sessions.

    The channel is owned by default (US-AT-09-1): the server binds only
    after winning the ownership election, so a losing player never exposes
    the control channel — free path included. Embedded and direct users
    that manage the channel themselves opt out explicitly with
    ``require_ownership=False``.
    """

    def __init__(
        self,
        command_handler: Callable[[str], str],
        socket_path: str = IPC_SOCKET,
        require_ownership: bool = True,
    ):
        self.command_handler = command_handler
        self.socket_path = socket_path
        self.require_ownership = require_ownership
        self.server_sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Binds to socket and starts background listener thread.

        A process that may not expose the channel (lost the ownership
        election, or a live server owns the transport) simply runs without
        IPC: start() leaves the server unset instead of stealing anything.
        Ownership is required by default — the election also gates a free
        path — while ``require_ownership=False`` keeps direct, election-less
        usage working.
        """
        try:
            self.server_sock = server_socket(
                self.socket_path, require_ownership=self.require_ownership
            )
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
            try:
                data = _recv_command(conn)
            except CommandTooLargeError as e:
                # Oversized input gets an explicit error reply, never a
                # silent truncation (CONF-1). Drain the remainder of the
                # line FIRST (bounded): a client still inside sendall
                # would otherwise hit a broken pipe when this connection
                # goes away and never see the error (RS-4).
                _discard_line_remainder(conn)
                conn.sendall(f"ERR: {e}\n".encode("utf-8"))
                return
            if data:
                reply = self.command_handler(data)
                conn.sendall(f"{reply}\n".encode("utf-8"))
        except Exception:
            pass
        finally:
            # Every exit path closes the connection explicitly: an
            # abandoned socket object is only reclaimed by a gc cycle
            # (the handler's traceback keeps its frame alive), which a
            # peer blocked in sendall never triggers.
            try:
                conn.close()
            except OSError:
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
