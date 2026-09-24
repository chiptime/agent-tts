"""IPC transport for interactive agent-tts playback controls.

Wire format — framing v2 (BLOQUE 1.3, decision D1): every command and
every reply is ONE length-prefixed frame, identical shape in both
directions::

    +----------------+-----------------+------------------------+
    | MAGIC "ATTS"   | VERSION = 0x02  | LENGTH (4B, big-endian)|
    +----------------+-----------------+------------------------+
    | PAYLOAD (LENGTH bytes, UTF-8 command/reply text)             |
    +---------------------------------------------------------------+

The header (FRAME_HEADER_SIZE bytes) is fully validated — magic,
version, and LENGTH against MAX_PAYLOAD — BEFORE any body byte is
read, so an oversized frame is rejected from the header alone and a
slow sender can never be truncated mid-payload. This consciously
breaks the BLOQUE 1.1/1.2 newline framing (an approved exception to
RF-AT-04-2 taken before the IPC contract freeze): the only consumers
are this repo's CLI client and daemon, which ship together.

Protocol mismatch (a stale daemon still speaking v1): the v2 ping
frame contains no newline byte, so a v1 daemon never finds a line to
answer and stays silent — the client's health probe classifies it as
wedged and the existing kill-and-respawn machinery (RF-AT-04-8, see
agent_tts.daemon) replaces it. Bytes that DO arrive but are not a v2
frame raise ProtocolMismatchError with a restart hint.

POSIX keeps the original AF_UNIX socket behavior. Windows
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
# --- Framing v2 constants (the single home for every buffer/size literal) ---------
# Frame header: 4-byte magic + 1-byte version + 4-byte big-endian length.
FRAME_MAGIC = b"ATTS"
FRAME_VERSION = 2
FRAME_HEADER_SIZE = 4 + 1 + 4
# Hard bound for ONE frame payload, either direction (the documented
# 16 MiB cap is kept from the line-protocol era). Oversize frames are
# rejected from the header before any body byte is read.
MAX_PAYLOAD = 16 * 1024 * 1024
# Body reads ask for READ_CHUNK bytes per recv() (RI-1: the old
# 1024-byte chunks meant ~16k syscalls and ~3x peak memory per 16 MiB
# payload; 64 KiB cuts that to ~256 syscalls).
READ_CHUNK = 64 * 1024
# Header phase: bounds a connection that connects and sends nothing.
IPC_HEADER_TIMEOUT_SEC = 1.0
# Body phase: per-recv idle bound while reassembling an announced
# payload. Any sender making progress at least this often completes
# whenever it finishes (A1'': a mid-payload pause no longer truncates);
# a genuinely stalled sender errors cleanly after this deadline.
FRAME_BODY_TIMEOUT_SEC = 30.0

_RESTART_HINT = (
    "the running daemon/library speaks an older control protocol; "
    "restart it (e.g. agent-tts --ipc-cmd shutdown) or rerun your "
    "command so a current daemon is auto-respawned"
)


class FrameError(Exception):
    """Base for framing v2 violations (bad header, oversize, mid-frame loss)."""


class CommandTooLargeError(FrameError):
    """A frame announced more than MAX_PAYLOAD bytes (rejected pre-body)."""


class ProtocolMismatchError(FrameError):
    """The peer's bytes are not framing v2 (bad magic or version)."""


def encode_frame(payload) -> bytes:
    """Builds one framing v2 message: header + payload.

    Accepts str (UTF-8 encoded) or bytes. Raises CommandTooLargeError
    when the payload exceeds MAX_PAYLOAD, BEFORE anything touches the
    wire — callers can therefore fail fast locally instead of pushing
    bytes a conforming server must reject (A2''=B1'').
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(data) > MAX_PAYLOAD:
        raise CommandTooLargeError(
            f"command too large (payload of {len(data)} bytes exceeds "
            f"the {MAX_PAYLOAD}-byte cap)"
        )
    header = FRAME_MAGIC + bytes([FRAME_VERSION]) + len(data).to_bytes(4, "big")
    return header + data


def recv_exact(conn: socket.socket, count: int) -> bytes:
    """Reads exactly ``count`` bytes; ConnectionResetError on early EOF."""
    parts = []
    remaining = count
    while remaining > 0:
        chunk = conn.recv(remaining)
        if not chunk:
            raise ConnectionResetError("connection closed before the frame completed")
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def read_frame(conn: socket.socket, *, body_timeout_sec: Optional[float] = None) -> str:
    """Reads one framing v2 message and returns the decoded payload text.

    Validates the whole header BEFORE reading any body byte: bad
    magic/version raises ProtocolMismatchError (actionable restart
    hint), an oversize length raises CommandTooLargeError without
    attempting to buffer or drain the announced body (A2''=B1''), and
    the body is then reassembled with READ_CHUNK-sized reads up to the
    exact announced length (A1'': truncation is impossible by
    construction). ``body_timeout_sec`` optionally applies a per-recv
    idle bound for the body phase only (the socket's own timeout is
    restored afterwards).
    """
    header = recv_exact(conn, FRAME_HEADER_SIZE)
    magic, version = header[:4], header[4]
    if magic != FRAME_MAGIC:
        raise ProtocolMismatchError(
            f"IPC protocol mismatch: expected magic {FRAME_MAGIC!r}, got "
            f"{magic!r}; {_RESTART_HINT}"
        )
    if version != FRAME_VERSION:
        raise ProtocolMismatchError(
            f"IPC protocol mismatch: expected version {FRAME_VERSION}, got "
            f"{version}; {_RESTART_HINT}"
        )
    length = int.from_bytes(header[5:9], "big")
    if length > MAX_PAYLOAD:
        raise CommandTooLargeError(
            f"command too large (frame announces {length} bytes, over "
            f"the {MAX_PAYLOAD}-byte cap)"
        )
    saved_timeout = conn.gettimeout()
    if body_timeout_sec is not None and saved_timeout != body_timeout_sec:
        conn.settimeout(body_timeout_sec)
    try:
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = conn.recv(min(READ_CHUNK, remaining))
            if not chunk:
                raise ConnectionResetError("connection closed mid-frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks).decode("utf-8", errors="ignore").strip()
    finally:
        if body_timeout_sec is not None and saved_timeout != body_timeout_sec:
            conn.settimeout(saved_timeout)


def send_frame(conn: socket.socket, payload) -> None:
    """Sends one framing v2 message (locally capped before any write)."""
    conn.sendall(encode_frame(payload))


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
    spaces, followed by ONE free-text field that keeps its spaces:
    ``text=...`` (payload prose) or ``error=...`` (the typed error's
    message, always final — A3). Unknown shapes degrade to
    {"raw": reply} — this never raises, so hosts can switch to it
    without new failure modes.
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
    elif reply.startswith("error="):
        obj["error"] = reply[len("error="):]
        rest = ""
    elif " error=" in reply:
        rest, error = reply.split(" error=", 1)
        obj["error"] = error
    for token in rest.split():
        if "=" in token:
            key, value = token.split("=", 1)
            obj[key] = value
    if not obj:
        obj = {"raw": reply}
    return json.dumps(obj, ensure_ascii=False)


def send_ipc_command(command: str, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends one framed IPC command and returns the framed reply text.

    Returns None when no server is reachable or the exchange fails
    (parity with the line-protocol era); the payload is validated
    against MAX_PAYLOAD locally before any wire write.
    """
    try:
        client = connect_to_server(socket_path)
    except Exception:
        return None
    if client is None:
        return None
    try:
        send_frame(client, command.strip())
        return read_frame(client)
    except Exception:
        return None
    finally:
        try:
            client.close()
        except OSError:
            pass


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
            conn.settimeout(IPC_HEADER_TIMEOUT_SEC)
            try:
                # Header phase under the short deadline; body phase under
                # the generous per-recv FRAME_BODY_TIMEOUT_SEC so a slow
                # but progressing sender always completes (A1'').
                data = read_frame(conn, body_timeout_sec=FRAME_BODY_TIMEOUT_SEC)
            except CommandTooLargeError as e:
                # A2''=B1'': the oversize length is rejected from the
                # header alone — the announced body is never read or
                # drained (the old bounded drain degraded to a mid-drain
                # connection loss for >2x-cap payloads). In-repo clients
                # validate the cap locally before writing, so a conforming
                # sender is never mid-sendall here; it simply reads this
                # typed error reply (CONF-1).
                try:
                    send_frame(conn, f"ERR: {e}")
                except OSError:
                    pass
                return
            except ProtocolMismatchError:
                # The peer does not speak framing v2 (a pre-BLOQUE 1.3
                # client): nothing we could send would parse, so the
                # connection just closes.
                return
            if data:
                reply = self.command_handler(data)
                send_frame(conn, reply)
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
