"""Resident STT worker: private Unix-socket server + bounded client.

Transport contract STT-v1 (independent of the frozen playback IPC v2 —
that channel, its socket and its locks are never touched here):

* POSIX AF_UNIX socket only. Native Windows transport is a typed
  ``TransportUnsupportedError`` (WSL2 is the supported Windows path).
* One length-prefixed frame per request and per reply, JSON payload::

      +----------------+---------------+--------------------------+
      | MAGIC "ASTT"   | VERSION = 0x01| LENGTH (4B, big-endian)  |
      +----------------+---------------+--------------------------+
      | PAYLOAD (LENGTH bytes, UTF-8 JSON object)                    |
      +---------------------------------------------------------------+

* Ops: ``status`` (readiness), ``transcribe`` (bounded base64 audio) and
  ``shutdown`` (explicit owner stop). No client autostart, ever.
* Replies are ``{"ok": true, ...}`` or ``{"ok": false, "error": {"kind":
  ..., "message": ...}}`` with kinds ``busy`` / ``model_unavailable`` /
  ``invalid_request`` / ``worker_unavailable``. Errors never carry raw
  host tracebacks or audio content.

Endpoint safety model:

* Default endpoint: application-owned mode-0700 runtime directory under a
  validated (owner == euid, not group/world-writable) ``XDG_RUNTIME_DIR``,
  falling back to a private uid-specific directory under the temp root.
  Unsafe/symlinked/foreign-owned existing directories are rejected.
* The socket file is created 0600; a start() lock (flock on
  ``<socket>.lock``) elects a single owner per endpoint.
* Occupied addresses fail with an actionable typed error — live OR stale.
  Nothing is ever unlinked on startup; teardown removes ONLY the socket
  inode this process bound (a replacement file survives untouched).
"""

from __future__ import annotations

import base64
import errno
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
from typing import Optional

try:  # POSIX-only; the Windows path raises a typed transport error instead
    import fcntl
except ImportError:  # pragma: no cover - Windows lacks fcntl
    fcntl = None

from agent_tts.stt.transcriber import (
    InvalidConfigError,
    MissingExtraError,
    ModelUnavailableError,
    SttError,
    SttSettings,
    Transcriber,
)

# --- Framing constants (single home for every transport literal) ---------
FRAME_MAGIC = b"ASTT"
FRAME_VERSION = 1
FRAME_HEADER_SIZE = 4 + 1 + 4
# Hard bound for ONE frame payload, either direction. The raw-audio cap
# base64-encodes to 4/3 * 24 MiB = 32 MiB, and the JSON envelope adds a
# bounded header of its own; 48 MiB reserves that overhead so a maximum-
# legal audio request always fits (and an over-cap one is still rejected
# from the header long before any allocation scales with it).
MAX_PAYLOAD = 48 * 1024 * 1024
MAX_AUDIO_BYTES = 24 * 1024 * 1024
READ_CHUNK = 64 * 1024
# Server-side deadlines: header bounds a dead connection, body bounds a
# stalled-but-incomplete frame.
HEADER_TIMEOUT_SEC = 2.0
BODY_TIMEOUT_SEC = 30.0
# Client deadlines: connect is short; reads default generously because a
# real transcription legitimately takes seconds.
CLIENT_CONNECT_TIMEOUT_SEC = 2.0
DEFAULT_READ_TIMEOUT_SEC = 120.0
# Concurrency bounds: one inference at a time (a whisper model is not
# thread-safe), bounded connection threads, modest accept backlog.
MAX_CONNECTIONS = 8
LISTEN_BACKLOG = 16
DEFAULT_SHUTDOWN_GRACE_SEC = 2.0

DEFAULT_SOCKET_NAME = "stt.sock"
ENV_SOCKET = "AGENT_TTS_STT_SOCKET"
ENV_RUNTIME_DIR = "AGENT_TTS_STT_RUNTIME_DIR"
RUNTIME_DIR_NAME = "agent-tts-stt"

SUFFIX_WHITELIST = frozenset({".webm", ".mp3", ".wav", ".m4a", ".ogg", ".flac"})

_WSL_HINT = (
    "the STT worker transport requires a POSIX Unix socket; native Windows "
    "is unsupported in U1 — run it inside WSL2"
)


class FrameError(SttError):
    """Framing STT-v1 violations (bad header, oversize, mid-frame loss)."""


class FrameTooLargeError(FrameError):
    kind = "invalid_request"


class ProtocolMismatchError(FrameError):
    kind = "protocol_mismatch"


class UnsafeEndpointError(SttError):
    kind = "endpoint_unsafe"


class TransportUnsupportedError(SttError):
    kind = "transport_unsupported"
    exit_code = 7


class WorkerUnavailableError(SttError):
    kind = "worker_unavailable"
    exit_code = 5


class BusyError(SttError):
    kind = "busy"
    exit_code = 3


class InvalidRequestError(SttError):
    kind = "invalid_request"


class AddressInUseError(SttError):
    kind = "address_in_use"


_REPLY_ERROR_CLASSES = {
    BusyError.kind: BusyError,
    ModelUnavailableError.kind: ModelUnavailableError,
    InvalidRequestError.kind: InvalidRequestError,
    WorkerUnavailableError.kind: WorkerUnavailableError,
    MissingExtraError.kind: MissingExtraError,
    InvalidConfigError.kind: InvalidConfigError,
}


def _is_windows() -> bool:
    return sys.platform == "win32"


def _euid() -> int:
    return os.geteuid()


# ---------------------------------------------------------------------------
# Frame codec
# ---------------------------------------------------------------------------


def encode_frame(payload) -> bytes:
    """Builds one STT-v1 frame; oversize payloads fail locally, pre-wire."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(data) > MAX_PAYLOAD:
        raise FrameTooLargeError(
            f"frame too large (payload of {len(data)} bytes exceeds "
            f"the {MAX_PAYLOAD}-byte cap)"
        )
    return FRAME_MAGIC + bytes([FRAME_VERSION]) + len(data).to_bytes(4, "big") + data


def _recv_exact(conn: socket.socket, count: int) -> bytes:
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
    """Reads one STT-v1 frame; validates the whole header pre-body."""
    header = _recv_exact(conn, FRAME_HEADER_SIZE)
    if header[:4] != FRAME_MAGIC:
        raise ProtocolMismatchError(
            f"STT protocol mismatch: expected magic {FRAME_MAGIC!r}, got {header[:4]!r}"
        )
    if header[4] != FRAME_VERSION:
        raise ProtocolMismatchError(
            f"STT protocol mismatch: expected version {FRAME_VERSION}, "
            f"got {header[4]}"
        )
    length = int.from_bytes(header[5:9], "big")
    if length > MAX_PAYLOAD:
        raise FrameTooLargeError(
            f"frame too large (announced {length} bytes over the "
            f"{MAX_PAYLOAD}-byte cap); rejected before reading the body"
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
        return b"".join(chunks).decode("utf-8")
    finally:
        if body_timeout_sec is not None and saved_timeout != body_timeout_sec:
            conn.settimeout(saved_timeout)


def send_frame(conn: socket.socket, payload) -> None:
    conn.sendall(encode_frame(payload))


# ---------------------------------------------------------------------------
# Endpoint resolution and safety
# ---------------------------------------------------------------------------


def _is_safe_xdg_dir(path: str) -> bool:
    """XDG_RUNTIME_DIR validity: real dir, owned by us, not group/world-writable."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(st.st_mode)
        and not stat.S_ISLNK(st.st_mode)
        and st.st_uid == _euid()
        and (st.st_mode & 0o022) == 0
    )


def stt_runtime_dir(env: Optional[dict] = None) -> str:
    """Application-owned runtime dir: explicit override, valid XDG, or a
    private uid-specific directory under the platform temp root."""
    env = os.environ if env is None else env
    override = env.get(ENV_RUNTIME_DIR)
    if override:
        return override
    xdg = env.get("XDG_RUNTIME_DIR")
    if xdg and _is_safe_xdg_dir(xdg):
        return os.path.join(xdg, RUNTIME_DIR_NAME)
    return os.path.join(tempfile.gettempdir(), f"{RUNTIME_DIR_NAME}-{_euid()}")


def ensure_runtime_dir(path: str) -> str:
    """Creates/validates the runtime dir: real dir (no symlink), owned by
    this uid, mode 0700 exactly. Anything else is a typed refusal."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)  # beat umask explicitly
        return path
    problems = []
    if stat.S_ISLNK(st.st_mode):
        problems.append("it is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        problems.append("it is not a directory")
    if st.st_uid != _euid():
        problems.append(f"it is owned by uid {st.st_uid}, not this process")
    if st.st_mode & 0o077:
        problems.append(f"mode {stat.S_IMODE(st.st_mode):o} is more permissive than 0700")
    if problems:
        raise UnsafeEndpointError(
            f"refusing unsafe STT runtime directory {path}: " + "; ".join(problems)
        )
    return path


def default_socket_path(env: Optional[dict] = None) -> str:
    """Client-side default endpoint (no directories are created here)."""
    env = os.environ if env is None else env
    explicit = env.get(ENV_SOCKET)
    if explicit:
        return explicit
    return os.path.join(stt_runtime_dir(env), DEFAULT_SOCKET_NAME)


def resolve_serving_socket(
    explicit: Optional[str] = None,
    runtime_dir: Optional[str] = None,
    env: Optional[dict] = None,
) -> str:
    """Server-side endpoint resolution, hardened for the default path.

    An explicit ``--socket``/``AGENT_TTS_STT_SOCKET`` target is trusted as
    operator intent (only the socket file itself is safety-checked); every
    derived endpoint lands in a freshly validated 0700 runtime dir.
    """
    env = os.environ if env is None else env
    if explicit:
        return explicit
    if runtime_dir:
        return os.path.join(ensure_runtime_dir(runtime_dir), DEFAULT_SOCKET_NAME)
    if env.get(ENV_SOCKET):
        return env[ENV_SOCKET]
    return os.path.join(ensure_runtime_dir(stt_runtime_dir(env)), DEFAULT_SOCKET_NAME)


def validate_socket_target(path: str) -> None:
    """Client-side pre-connect check: reject symlinks and non-sockets."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise WorkerUnavailableError(
            f"no STT socket at {path}; start the worker with `agent-tts-stt serve`"
        ) from None
    if stat.S_ISLNK(st.st_mode):
        raise UnsafeEndpointError(f"refusing symlinked STT socket target {path}")
    if not stat.S_ISSOCK(st.st_mode):
        raise UnsafeEndpointError(f"{path} exists but is not a socket")


def validate_parent_dir(path: str) -> None:
    """Owner-only parent policy for EXPLICIT operator-chosen endpoints.

    The directory holding the socket must be a real directory (symlinked
    parents resolve to their target), owned by this uid, and not
    group/world-writable — a world-writable parent (e.g. sticky /tmp)
    lets any local user interpose socket entries. Derived endpoints get
    the stronger guarantee for free: their socket lives inside the
    hardened 0700 runtime dir this package creates.
    """
    parent = os.path.dirname(os.path.abspath(path))
    try:
        st = os.stat(parent)
    except OSError as exc:
        raise UnsafeEndpointError(
            f"STT socket parent directory {parent} is unusable: {exc}"
        ) from exc
    problems = []
    if not stat.S_ISDIR(st.st_mode):
        problems.append("it is not a directory")
    if st.st_uid != _euid():
        problems.append(f"it is owned by uid {st.st_uid}, not this process")
    if st.st_mode & 0o022:
        problems.append(
            f"mode {stat.S_IMODE(st.st_mode):o} is group/world-writable"
        )
    if problems:
        raise UnsafeEndpointError(
            f"refusing explicit STT socket {path}: unsafe parent directory "
            f"{parent}: " + "; ".join(problems)
        )


def _probe_live(path: str) -> bool:
    """True when a server answers connections on the existing socket path."""
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(CLIENT_CONNECT_TIMEOUT_SEC)
        probe.connect(path)
        probe.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class SttWorker:
    """Resident STT server: keeps the model loaded between requests.

    The transcriber (and its injected fake loader in tests) is owned by
    this process only; shutdown honors this worker's own state and never
    claims to cancel a native inference call that is still running.
    """

    def __init__(self, transcriber: Transcriber, socket_path: Optional[str] = None):
        self.transcriber = transcriber
        self.socket_path = socket_path
        self._settings: SttSettings = getattr(transcriber, "_settings", SttSettings())
        self._server_sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._warmup_thread: Optional[threading.Thread] = None
        self._conn_threads: list[threading.Thread] = []
        self._conn_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._done_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._report: Optional[dict] = None
        self._bound_inode: Optional[int] = None
        self._bound_dev: Optional[int] = None
        self._lock_handle = None
        self._grace = DEFAULT_SHUTDOWN_GRACE_SEC

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Binds the endpoint and starts serving in a background thread.

        Failure-safe: any startup error (unsafe endpoint, occupied
        address, failed bind such as an overlong AF_UNIX path) releases
        every resource this attempt acquired — election lock, sockets —
        so a failed start never leaves a phantom owner behind.
        """
        if _is_windows():
            raise TransportUnsupportedError(_WSL_HINT)
        if self._running:
            return
        explicit = self.socket_path is not None or os.environ.get(ENV_SOCKET)
        if self.socket_path is None:
            self.socket_path = resolve_serving_socket()
        if explicit:
            validate_parent_dir(self.socket_path)
        self._check_socket_path(self.socket_path)
        self._acquire_lock(self.socket_path + ".lock")
        try:
            self._bind(self.socket_path)
            self._running = True
            self._accept_thread = threading.Thread(
                target=self._accept_loop, name="stt-worker-accept", daemon=True
            )
            self._accept_thread.start()
        except BaseException:
            self._running = False
            if self._server_sock is not None:
                try:
                    self._server_sock.close()
                except OSError:
                    pass
                self._server_sock = None
            self._release_lock()
            raise
        # Health driver (F2): production serve never calls warmup() by
        # hand; the worker itself moves loading -> ready/unavailable. The
        # task is REGISTERED so a hung startup is counted in shutdown
        # accounting — never silently dropped from the clean/hung report.
        self._warmup_thread = threading.Thread(
            target=self._auto_warmup, name="stt-warmup", daemon=True
        )
        self._warmup_thread.start()

    def serve(self) -> dict:
        """start() + block until shutdown; returns the honest exit report."""
        self.start()
        self._done_event.wait()
        return self._report or {"clean": True, "hung": 0}

    def request_stop(self) -> None:
        """Asks the accept loop to wind down (safe from any thread)."""
        self._stop_event.set()
        with self._stop_lock:
            if self._server_sock is not None:
                try:
                    self._server_sock.close()
                except OSError:
                    pass

    def stop(self, grace: Optional[float] = None) -> dict:
        """Stops the worker and waits a bounded grace for in-flight work."""
        self._grace = grace if grace is not None else DEFAULT_SHUTDOWN_GRACE_SEC
        self.request_stop()
        deadline = time.monotonic() + self._grace + 10.0
        while not self._done_event.wait(0.05):
            if time.monotonic() > deadline:
                return {"clean": False, "hung": max(1, len(self._live_conns()))}
        return self._report or {"clean": False, "hung": 1}

    def wait_stopped(self, timeout: Optional[float] = None) -> dict:
        if not self._done_event.wait(timeout):
            raise TimeoutError("worker did not finish shutting down")
        assert self._report is not None
        return self._report

    def join_workers(self, timeout: float = 5.0) -> None:
        """Joins lingering connection threads (cleanup aid for tests/hosts)."""
        deadline = time.monotonic() + timeout
        for th in list(self._conn_threads):
            if th is threading.current_thread():
                continue
            th.join(timeout=max(0.0, deadline - time.monotonic()))

    # -- internals ----------------------------------------------------------

    def _live_conns(self) -> list:
        with self._conn_lock:
            return [th for th in self._conn_threads if th.is_alive()]

    def _prune_conns(self) -> None:
        """Registry stays bounded: finished connection threads are dropped
        so repeated requests never accumulate thread history."""
        with self._conn_lock:
            self._conn_threads = [th for th in self._conn_threads if th.is_alive()]

    def _auto_warmup(self) -> None:
        try:
            self.transcriber.maybe_warmup()
        except Exception:  # noqa: BLE001 — warmup records its own state
            pass

    def _check_socket_path(self, path: str) -> None:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(st.st_mode):
            raise UnsafeEndpointError(f"refusing symlinked socket path {path}")
        if not stat.S_ISSOCK(st.st_mode):
            raise UnsafeEndpointError(
                f"{path} exists and is not a socket; remove it or choose another endpoint"
            )
        if _probe_live(path):
            raise AddressInUseError(
                f"STT socket {path} is served by a live worker; stop it first "
                "(send the shutdown op over the socket) or choose another endpoint"
            )
        raise AddressInUseError(
            f"STT socket {path} is occupied by a stale socket left by an "
            f"unclean exit; this worker never unlinks foreign files — remove "
            f"it manually (`rm {path}`) and start again"
        )

    def _acquire_lock(self, lock_path: str) -> None:
        if fcntl is None:  # pragma: no cover - guarded by the platform check
            raise TransportUnsupportedError(_WSL_HINT)
        try:
            handle = open(lock_path, "a+")
        except OSError as exc:
            raise UnsafeEndpointError(
                f"cannot open STT election lock {lock_path}: {exc}"
            ) from exc
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AddressInUseError(
                f"another live STT worker holds the lock at {lock_path}"
            ) from None
        self._lock_handle = handle

    def _bind(self, path: str) -> None:
        """Binds with race-free creation permissions: umask 0177 makes the
        kernel create the socket 0600, so no name-based chmod window
        exists between bind and verify. The result is then verified
        (socket, not a symlink, mode 0600, this uid) and its device+inode
        identity recorded; a failed verification closes the socket WITHOUT
        unlinking — the path entry may not be ours anymore.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)  # dedicated worker process; see contract
        try:
            sock.bind(path)
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            if exc.errno == errno.EADDRINUSE:
                raise AddressInUseError(
                    f"STT socket {path} lost the bind race (address in use)"
                ) from exc
            raise UnsafeEndpointError(
                f"cannot bind STT socket {path}: {exc}"
            ) from exc
        finally:
            os.umask(old_umask)
        try:
            st = os.lstat(path)
            if (
                stat.S_ISLNK(st.st_mode)
                or not stat.S_ISSOCK(st.st_mode)
                or (st.st_mode & 0o077)
                or st.st_uid != _euid()
            ):
                raise UnsafeEndpointError(
                    f"socket {path} failed the post-bind safety verification "
                    f"(mode {stat.S_IMODE(st.st_mode):o}, uid {st.st_uid})"
                )
            self._bound_inode = st.st_ino
            self._bound_dev = st.st_dev
            sock.listen(LISTEN_BACKLOG)
            sock.settimeout(0.5)
            self._server_sock = sock
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            raise UnsafeEndpointError(
                f"cannot verify STT socket {path} after bind: {exc}"
            ) from exc
        except BaseException:
            try:
                sock.close()
            except OSError:
                pass
            raise

    def _accept_loop(self) -> None:
        try:
            while self._running and not self._stop_event.is_set():
                self._prune_conns()
                try:
                    conn, _ = self._server_sock.accept()
                except (socket.timeout, TimeoutError):
                    continue
                except OSError:
                    break
                if len(self._live_conns()) >= MAX_CONNECTIONS:
                    self._reject_busy(conn)
                    continue
                thread = threading.Thread(
                    target=self._serve_connection, args=(conn,), daemon=True
                )
                with self._conn_lock:
                    self._conn_threads.append(thread)
                thread.start()
        finally:
            self._finalize()

    def _reject_busy(self, conn: socket.socket) -> None:
        try:
            send_frame(
                conn,
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "kind": "busy",
                            "message": "too many concurrent STT connections",
                        },
                    }
                ),
            )
        except Exception:  # noqa: BLE001 — best-effort rejection
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _serve_connection(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(HEADER_TIMEOUT_SEC)
            try:
                raw = read_frame(conn, body_timeout_sec=BODY_TIMEOUT_SEC)
            except FrameTooLargeError as exc:
                self._reply_error(conn, InvalidRequestError(str(exc)))
                return
            except ProtocolMismatchError:
                return  # nothing parseable to answer; just close
            reply = self._dispatch(raw)
            send_frame(conn, json.dumps(reply, ensure_ascii=False))
        except OSError:
            pass
        except Exception as exc:  # noqa: BLE001 — typed reply, never a traceback
            self._reply_error(
                conn, SttError(f"internal worker error ({type(exc).__name__})")
            )
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _reply_error(conn: socket.socket, exc: SttError) -> None:
        try:
            send_frame(
                conn,
                json.dumps(
                    {"ok": False, "error": {"kind": exc.kind, "message": str(exc)}}
                ),
            )
        except Exception:  # noqa: BLE001 — best effort
            pass

    def _dispatch(self, raw: str) -> dict:
        try:
            request = json.loads(raw)
        except ValueError:
            return self._error(InvalidRequestError("request is not valid JSON"))
        if not isinstance(request, dict):
            return self._error(
                InvalidRequestError("request must be a JSON object")
            )
        version = request.get("v")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != FRAME_VERSION
        ):
            return self._error(
                InvalidRequestError(
                    f"unsupported or missing protocol version "
                    f"(expected integer v={FRAME_VERSION})"
                )
            )
        op = request.get("op")
        try:
            if op == "status":
                payload = {
                    "ok": True,
                    "v": FRAME_VERSION,
                    "state": self.transcriber.state,
                    "model": self._settings.model,
                    "busy": self._infer_lock.locked(),
                    "pid": os.getpid(),
                }
                error = self.transcriber.error()
                if payload["state"] == "unavailable" and error:
                    payload["error"] = error
                return payload
            if op == "shutdown":
                self.request_stop()
                return {"ok": True, "shutting_down": True}
            if op == "transcribe":
                return self._transcribe(request)
            return self._error(InvalidRequestError(f"unknown op {op!r}"))
        except (TypeError, ValueError) as exc:
            # malformed field types (unhashable/odd shapes) stay typed
            return self._error(InvalidRequestError(f"malformed request: {exc}"))

    @staticmethod
    def _error(exc: SttError) -> dict:
        return {"ok": False, "error": {"kind": exc.kind, "message": str(exc)}}

    def _transcribe(self, request: dict) -> dict:
        audio_b64 = request.get("audio_b64")
        if not isinstance(audio_b64, str):
            return self._error(
                InvalidRequestError("audio_b64 must be a base64 string")
            )
        suffix = request.get("suffix", ".webm")
        if not isinstance(suffix, str) or suffix not in SUFFIX_WHITELIST:
            return self._error(
                InvalidRequestError(
                    f"suffix {suffix!r} not allowed "
                    f"(one of {', '.join(sorted(SUFFIX_WHITELIST))})"
                )
            )
        try:
            data = base64.b64decode(audio_b64, validate=True)
        except Exception:  # noqa: BLE001 — binascii.Error/ValueError
            return self._error(InvalidRequestError("audio_b64 is not valid base64"))
        if not data:
            return self._error(InvalidRequestError("audio payload is empty"))
        if len(data) > MAX_AUDIO_BYTES:
            return self._error(
                InvalidRequestError(
                    f"audio of {len(data)} bytes exceeds the "
                    f"{MAX_AUDIO_BYTES}-byte cap"
                )
            )
        if not self._infer_lock.acquire(blocking=False):
            return self._error(
                BusyError("another transcription is in flight; retry shortly")
            )
        try:
            text = self.transcriber.transcribe_bytes(data, suffix=suffix)
        except SttError as exc:
            # typed errors keep their kind (model_unavailable,
            # missing_extra, invalid_request, ...) across the wire
            return self._error(exc)
        except Exception as exc:  # noqa: BLE001 — typed reply, no traceback
            return self._error(
                SttError(f"transcription failed ({type(exc).__name__})")
            )
        finally:
            self._infer_lock.release()
        return {"ok": True, "text": text}

    def _finalize(self) -> None:
        self._running = False
        with self._stop_lock:
            if self._server_sock is not None:
                try:
                    self._server_sock.close()
                except OSError:
                    pass
                self._server_sock = None
        grace = getattr(self, "_grace", DEFAULT_SHUTDOWN_GRACE_SEC)
        deadline = time.monotonic() + grace
        for th in self._live_conns():
            if th is threading.current_thread():
                continue
            th.join(timeout=max(0.0, deadline - time.monotonic()))
        hung = len(self._live_conns())
        # The registered warmup task shares the same bounded accounting:
        # a stuck loader must surface as unfinished startup work, not a
        # false clean=True/hung=0.
        warm = self._warmup_thread
        if warm is not None and warm.is_alive() and warm is not threading.current_thread():
            warm.join(timeout=max(0.0, deadline - time.monotonic()))
            if warm.is_alive():
                hung += 1
        self._remove_owned_socket(self.socket_path)
        self._release_lock()
        self._report = {"clean": hung == 0, "hung": hung}
        self._done_event.set()

    def _remove_owned_socket(self, path: Optional[str]) -> None:
        """Unlinks the socket ONLY when the path still holds the exact
        socket inode this process bound.

        No-kill guarantees: symlinks and non-sockets are never unlinked;
        the (stat, unlink) pair is not atomic on Linux, so a same-uid
        actor racing the teardown window could still swap the entry after
        the check — that residual interposition is out of scope (the
        parent directory is owner-only writable; cross-user swaps are
        impossible, and the election lock already covers cooperating
        peers). Cross-user safety and inode-replacement detection are
        what this guard actually provides.
        """
        if not path or self._bound_inode is None or self._bound_dev is None:
            return
        try:
            st = os.lstat(path)
        except OSError:
            return
        if (
            stat.S_ISSOCK(st.st_mode)
            and not stat.S_ISLNK(st.st_mode)
            and (st.st_dev, st.st_ino) == (self._bound_dev, self._bound_inode)
        ):
            try:
                os.unlink(path)
            except OSError:
                pass

    def _release_lock(self) -> None:
        if self._lock_handle is not None:
            try:
                self._lock_handle.close()
            except OSError:
                pass
            self._lock_handle = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def stt_request(
    request: dict,
    socket_path: Optional[str] = None,
    timeout: Optional[float] = None,
    env: Optional[dict] = None,
) -> dict:
    """One request/reply exchange with the resident worker.

    Injects the protocol version when absent, validates the target before
    connecting, and turns error replies into typed exceptions.
    """
    if _is_windows():
        raise TransportUnsupportedError(_WSL_HINT)
    path = socket_path or default_socket_path(env)
    validate_socket_target(path)
    payload = dict(request)
    payload.setdefault("v", FRAME_VERSION)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(CLIENT_CONNECT_TIMEOUT_SEC)
    try:
        try:
            client.connect(path)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            raise WorkerUnavailableError(
                f"cannot reach the STT worker at {path} ({exc}); "
                "start it with `agent-tts-stt serve`"
            ) from exc
        client.settimeout(timeout if timeout is not None else DEFAULT_READ_TIMEOUT_SEC)
        send_frame(client, json.dumps(payload, ensure_ascii=False))
        reply = json.loads(read_frame(client))
        if not isinstance(reply, dict):
            raise ProtocolMismatchError("reply is not a JSON object")
        if reply.get("ok") is False:
            error = reply.get("error") or {}
            kind, message = error.get("kind", "stt_error"), error.get("message", "")
            exc_class = _REPLY_ERROR_CLASSES.get(kind, SttError)
            raise exc_class(message)
        return reply
    finally:
        try:
            client.close()
        except OSError:
            pass
