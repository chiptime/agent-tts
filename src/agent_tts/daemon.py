"""Persistent playback daemon: vía única with transparent auto-start (AT-04).

The CLI is always a client (RF-AT-04-5): every invocation either reaches
this daemon over the control channel or transparently starts it and
delegates. There is no classic second execution path.

The daemon owns the control channel for its whole life (ownership per
BLOQUE 1.1: atomic flock election; PID_FILE stays an informational marker
written only by the owner) and it is the owner of playback state from day
one: ``Daemon.active_session`` is the mount point where the BLOQUE 1.3
queue manager will sit, above the per-playback session.

Commands (RF-AT-04-2/RF-AT-04-3): the existing playback commands
(status/pause/resume/stop/seek/phrase navigation) are served over the
active session with zero protocol changes; ``play <json-payload>`` runs
one synthesis+playback (text + synthesis options in one JSON line),
``enqueue <json-payload>`` is the queue-aware variant (priority/policy/
event_type/identifiers fields alongside the play payload), ``ping``
answers version and uptime, ``shutdown`` terminates the daemon. Play
payloads may carry a ``chain`` of files instead of text/file
(RF-AT-08-4, see ``_chain_payload_error``): the chain plays as ONE
queued session over the assembled continuous stream.

Reply schema (A3, freeze-critical — see README "Wire format"): every
daemon-level ERROR is ``ok=false error=<free text>`` with ``error=`` as
the final, space-bearing field; acks carry ``ok=true`` (``ok=true
shutting_down=true``, ``ok=true item=<id> queue_len=<n>``); successful
playback replies keep their classic prefixes (``status=done`` /
``status=stopped`` / the session status line). Only the transport layer
keeps the legacy ``ERR:`` prefix (framing errors raised before dispatch,
see agent_tts.ipc).

Since BLOQUE 1.3 (AT-08, D4) every audible playback rides the priority
queue (``agent_tts.queue_manager``): a plain ``play`` maps to
``enqueue(priority=working, policy=queue)`` — it NEVER rejects with busy,
it waits its turn — and ``play`` keeps its blocking reply semantics (the
delegating client hears ``status=done``/``status=stopped`` when ITS item
finishes). Synthesis-only (``no_play``) requests own no audio and still
run directly, displacing nothing.

The runner adapter (``Daemon._queue_runner``) starts the real playback
session over the active-session slot asynchronously and returns the
liveness handle; the playback worker owns session teardown and reports
exactly one outcome through the manager's finish callback. Synthesis
liveness (silent non-streaming synthesis emits no progress tokens) is
handled by SIZING, not milestones: the wedged timeout is configurable
(``--wedged-timeout`` / AGENT_TTS_WEDGED_TIMEOUT) and must exceed the
worst-case silent synthesis interval — see the queue manager's adapter
note and the README queue section.

Provider instances stay alive between requests (RNF-AT-04-5): today
get_provider() builds an instance per call — kokoro reloads its ONNX
session per instance — so the daemon keeps a cache keyed by provider
configuration and pays the cold load once per configuration.
"""

import argparse
import asyncio
import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

import miniaudio

from agent_tts import __version__
from agent_tts import audio as audio_mod
from agent_tts.audio import _write_player_locks, cleanup_locks
from agent_tts.chain import ChainError, assemble_chain_files
from agent_tts.constants import (
    DAEMON_LOG_FILE,
    DAEMON_START_TIMEOUT_SEC,
    DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    ENV_COALESCE_WINDOW,
    ENV_IDLE_TIMEOUT,
    ENV_WEDGED_TIMEOUT,
    IPC_SOCKET,
    PING_TIMEOUT_SEC,
)
from agent_tts.ipc import (
    IPCServer,
    CommandTooLargeError,
    ProtocolMismatchError,
    connect_to_server,
    encode_frame,
    read_frame,
    send_frame,
    send_ipc_command,
)
from agent_tts.ownership import owns_channel
from agent_tts.playback_target import InvalidPlaybackTarget, resolve_target
from agent_tts.providers import TTSProvider, get_provider
from agent_tts.queue_manager import (
    DEFAULT_COALESCE_WINDOW_SEC,
    DEFAULT_WEDGED_TIMEOUT_SEC,
    AudioSessionHandle,
    PlaybackOutcome,
    Policy,
    Priority,
    QueueManager,
)


def autostart_idle_timeout_sec() -> Optional[float]:
    """Default idle timeout for implicitly auto-started daemons (RF-AT-04-7).

    AGENT_TTS_IDLE_TIMEOUT (seconds) overrides the 30-minute default; 0
    disables the idle exit. Invalid or negative values warn once and keep
    the default. Explicit starts (--serve/--foreground) pass no timeout at
    all unless the user asks for one.
    """
    raw = os.environ.get(ENV_IDLE_TIMEOUT, "")
    if not raw:
        return DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        print(
            f"Ignoring invalid {ENV_IDLE_TIMEOUT}={raw!r}; using the "
            f"{DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC:.0f}s default",
            file=sys.stderr,
        )
        return DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC
    if value < 0:
        print(
            f"Ignoring negative {ENV_IDLE_TIMEOUT}={raw!r}; using the "
            f"{DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC:.0f}s default",
            file=sys.stderr,
        )
        return DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC
    return value or None  # 0 disables the idle exit


def _env_float(name: str, default: float) -> float:
    """Reads a positive float override from the environment (queue config).

    Invalid or negative values warn once and keep the default — the same
    fail-open policy as the idle timeout.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        print(f"Ignoring invalid {name}={raw!r}; using the {default}s default", file=sys.stderr)
        return default
    if value < 0:
        print(f"Ignoring negative {name}={raw!r}; using the {default}s default", file=sys.stderr)
        return default
    return value


# Whitespace inside the compact queue JSON is encoded as \\uXXXX escapes so
# the whole ``queue=...`` value stays ONE whitespace-free kv token: legacy
# key=value parsers (and ipc.ipc_reply_json) keep splitting on spaces while
# any JSON consumer restores the original strings (freeze-critical rule).
_JSON_WS_ESCAPES = ((" ", "\\u0020"), ("\t", "\\u0009"), ("\n", "\\u000A"), ("\r", "\\u000D"))


def encode_queue_fields(snapshot_dict: dict) -> str:
    """Renders QueueSnapshot.as_dict() as the ``queue_len``/``queue`` kv fields.

    The JSON is compact (no separators whitespace), ensure_ascii, and with
    every residual whitespace byte escaped per _JSON_WS_ESCAPES — all real
    whitespace lives inside string values, so none survives literally.
    """
    text = json.dumps(snapshot_dict, ensure_ascii=True, separators=(",", ":"))
    for char, escape in _JSON_WS_ESCAPES:
        text = text.replace(char, escape)
    return f"queue_len={snapshot_dict['queue_len']} queue={text}"


class ProviderCache:
    """Keeps provider instances alive between daemon requests (RNF-AT-04-5).

    Cache key: provider name plus the provider-construction options. Two
    requests with the same configuration reuse the same instance (warm
    kokoro ONNX session, no per-call construction); a different
    configuration gets its own instance without evicting the previous one.
    """

    def __init__(self, factory: Callable[..., TTSProvider] = get_provider):
        self._factory = factory
        self._lock = threading.Lock()
        self._instances: dict = {}
        self.last_name = ""

    def get(
        self,
        provider_name: str = "edge",
        openai_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_model: Optional[str] = None,
        eleven_key: Optional[str] = None,
        eleven_model: Optional[str] = None,
        piper_model: Optional[str] = None,
    ) -> TTSProvider:
        name = (provider_name or "edge").lower().strip()
        key = (
            name,
            openai_key or "",
            openai_base_url or "",
            openai_model or "",
            eleven_key or "",
            eleven_model or "",
            piper_model or "",
        )
        with self._lock:
            instance = self._instances.get(key)
            if instance is None:
                instance = self._factory(
                    provider_name=name,
                    openai_key=openai_key,
                    openai_base_url=openai_base_url,
                    openai_model=openai_model,
                    eleven_key=eleven_key,
                    eleven_model=eleven_model,
                    piper_model=piper_model,
                )
                self._instances[key] = instance
            self.last_name = name
            return instance


def _queue_trace(message: str) -> None:
    """One queue lifecycle trace line on stderr (T5 measurement seam).

    ``dispatch`` prints when an item leaves pending (runner invocation =
    dispatch start); ``finalize`` prints when its audio path has fully
    ended, immediately before the queue finalizes the item. The gap
    between one item's finalize and the next item's dispatch is the
    RNF-AT-08-1 dispatch latency, observable for a real daemon
    subprocess from its stderr. Timestamps are CLOCK_MONOTONIC
    (comparable across processes on Linux).

    ``chain-item`` (T6) prints when a chain's playback cursor crosses a
    chain item's chain-global start (item 0 included: chain playback
    began) — the RNF-AT-08-1 chain-application seam: the wall-clock
    delta between consecutive crossings minus the audio time between
    them (item duration + gap) is the scheduling hole the chain must
    not have.
    """
    print(f"agent-tts-queue: {message}", file=sys.stderr, flush=True)


# Chain boundary watcher cadence: fine enough that one poll interval
# never dominates the 50 ms RNF-AT-08-1 chain budget.
CHAIN_TRACE_POLL_SEC = 0.005


def _inject_daemon_fields(reply: str, fields: str) -> str:
    """Inserts daemon-level kv fields into a session status reply.

    The free-text ``text=`` field must stay last (its value keeps spaces),
    so daemon fields are inserted before it when present, appended
    otherwise. Existing clients parsing the legacy prefix are unaffected.
    """
    if " text=" in reply:
        head, tail = reply.split(" text=", 1)
        return f"{head}{fields} text={tail}"
    return reply + fields


def _resolve_or_local(value: str, env=None) -> str:
    """Resolves a playback target, failing open to "local" with one warning."""
    try:
        return resolve_target(value, env if env is not None else os.environ)
    except InvalidPlaybackTarget as e:
        print(f"Playback target invalid ({e}); falling back to local playback", file=sys.stderr)
        return "local"


def _chain_payload_error(payload: dict) -> Optional[str]:
    """Typed-error text for an invalid chain payload shape, or None when valid.

    The chain wire fields (freeze-critical, documented in README next to
    the enqueue schema): ``chain`` = non-empty list of file path strings,
    mutually exclusive with ``text``/``file``; ``chain_gap_ms`` = a
    non-negative number of milliseconds of inter-item silence (default
    0). A chain is replayed audio, never synthesis: ``no_play`` cannot
    combine with it.
    """
    chain = payload.get("chain")
    if chain is None:
        return None
    if not isinstance(chain, list) or not chain or not all(
        isinstance(path, str) and path.strip() for path in chain
    ):
        return "chain must be a list of file paths"
    if (payload.get("text") or "").strip() or payload.get("file"):
        return "play accepts one of text, file, or chain, not a combination"
    gap = payload.get("chain_gap_ms", 0)
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or gap < 0:
        return f"chain_gap_ms must be a non-negative number of milliseconds: {gap!r}"
    if payload.get("no_play"):
        return "no_play cannot combine with chain (a chain owns no synthesis)"
    return None


def _session_env(payload: dict) -> Optional[dict]:
    """Per-request env overlay carrying the client's winhost endpoint.

    ``--winhost-host`` / ``--winhost-port`` (and the client's
    AGENT_TTS_WINHOST_* environment) resolve in the CLIENT process; the
    play payload carries them so the daemon-side session reaches the
    endpoint the delegating client asked for instead of resolving from
    the daemon's inherited environment. Absent values mean no overlay:
    the daemon's environment resolves as before.
    """
    host = str(payload.get("winhost_host") or "").strip()
    port = payload.get("winhost_port")
    if not host and port in (None, ""):
        return None
    env = dict(os.environ)
    if host:
        env["AGENT_TTS_WINHOST_HOST"] = host
    if port not in (None, ""):
        env["AGENT_TTS_WINHOST_PORT"] = str(port)
    return env


class _QueueWaiter:
    """One blocking play's wait for its queue item's outcome (D4).

    The play handler registers a waiter under a token before enqueueing;
    the runner adapter pops it when the item dispatches and the playback
    worker releases it with the final outcome. First release wins —
    shutdown's bulk refusal of never-dispatched items cannot overwrite an
    outcome a dispatched item already reported.
    """

    __slots__ = ("event", "outcome", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.outcome: Optional[PlaybackOutcome] = None
        self.error: Optional[str] = None

    def release(self, outcome: PlaybackOutcome, error: Optional[str] = None) -> None:
        if self.event.is_set():
            return  # the first finalization wins
        self.outcome = outcome
        self.error = error
        self.event.set()


class Daemon:
    """Long-lived owner of the control channel and of playback state.

    ``active_session`` is the queue mount point required by the BLOQUE 1.3
    plan: the queue manager dispatches into sessions through this
    attribute — the manager serializes every audible playback into this
    one slot (RF-AT-08-6: no two sessions ever overlap), and shutdown
    stops every tracked session (``_sessions``), not just the active one.
    """

    POLL_INTERVAL_SEC = 0.1
    DRAIN_TIMEOUT_SEC = 5.0

    # Payload key carrying the internal play-waiter token from _handle_play
    # to the runner adapter. Not part of the wire contract: clients never
    # set it, and the runner strips it before building the session.
    WAITER_KEY = "_waiter"

    def __init__(
        self,
        socket_path: str = IPC_SOCKET,
        idle_timeout_sec: Optional[float] = None,
        implicit: bool = False,
        provider_cache: Optional[ProviderCache] = None,
        coalesce_window_sec: Optional[float] = None,
        wedged_timeout_sec: Optional[float] = None,
    ):
        self.socket_path = socket_path
        self.idle_timeout_sec = idle_timeout_sec if idle_timeout_sec else None
        self.implicit = implicit
        self.providers = provider_cache or ProviderCache()
        self.started_at = time.time()
        # Startup playback target (RF-AT-04-6): resolved once from the
        # daemon's environment; a play payload can force a target per event.
        self.startup_target = _resolve_or_local(None)
        self._lock = threading.Lock()
        self.active_session = None
        # Every in-flight playback session (a superset of active_session:
        # the queue keeps exactly one active, but a session mid-teardown or
        # a queued dispatch race can briefly widen the set). Shutdown stops
        # them ALL, not just the active one, so no audio outlives the daemon.
        self._sessions = set()
        self._last_request = time.time()
        self._inflight = 0
        self._stop = threading.Event()
        # Queue mount point (BLOQUE 1.3 / AT-08): the manager sits above the
        # active-session slot with born-in liveness supervision (RS-5: a
        # wedged session auto-resolves). Every audible play and enqueue
        # routes through it (D4); the runner adapter starts the real
        # playback session asynchronously and reports the outcome exactly
        # once. Configuration: coalesce window and wedged timeout come from
        # the constructor flags, then the AGENT_TTS_* env overrides, then
        # the manager defaults (5 s / 30 s).
        self.queue_manager = QueueManager(
            runner=self._queue_runner,
            coalesce_window_sec=(
                _env_float(ENV_COALESCE_WINDOW, DEFAULT_COALESCE_WINDOW_SEC)
                if coalesce_window_sec is None
                else coalesce_window_sec
            ),
            wedged_timeout_sec=(
                _env_float(ENV_WEDGED_TIMEOUT, DEFAULT_WEDGED_TIMEOUT_SEC)
                if wedged_timeout_sec is None
                else wedged_timeout_sec
            ),
        )
        # Blocking plays wait for their queue item's outcome on these
        # records (token -> _QueueWaiter). The runner POPS the record when
        # its item dispatches; whatever is still registered at shutdown
        # belongs to a never-dispatched item and is refused deterministically.
        self._queue_waiters: dict = {}
        self._waiter_tokens = itertools.count(1)
        # Set under _lock at the top of _shutdown: once teardown has
        # started, new play registrations are refused with a
        # deterministic error so no audible session can register after
        # the stop-snapshot and escape the stop (RS-1).
        self._shutting_down = False
        self.ipc_server: Optional[IPCServer] = None
        self._cli = None  # set by run(); lazily imported otherwise

    # --- Request clock (RF-AT-04-7) -------------------------------------------

    def _touch(self) -> None:
        self._last_request = time.time()

    def uptime_sec(self) -> float:
        return time.time() - self.started_at

    def _idle_expired(self) -> bool:
        if self.idle_timeout_sec is None:
            return False
        with self._lock:
            busy = self.active_session is not None or self._inflight > 0
        return (not busy) and (time.time() - self._last_request) >= self.idle_timeout_sec

    # --- Lifecycle ------------------------------------------------------------

    def request_shutdown(self) -> None:
        self._stop.set()

    def _install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT trigger the orderly shutdown path (RF-AT-04-1).

        Must be called AFTER the cli import in run(): importing cli
        registers cli's own signal handlers at import time, and the
        daemon's must be the ones that stay installed.
        """

        def _handler(signum, frame):
            self.request_shutdown()

        try:
            signal.signal(signal.SIGINT, _handler)
            _sigterm = getattr(signal, "SIGTERM", None)
            if _sigterm is not None:  # SIGTERM is not delivered on Windows
                signal.signal(_sigterm, _handler)
        except ValueError:
            # signal only works in the main thread: a daemon embedded in a
            # thread (tests) keeps the default handler; the production
            # daemon always runs in its process's main thread.
            pass

    def run(self) -> int:
        """Elects, exposes the channel, and serves until stop/idle exit.

        Returns the process exit code. An implicit daemon (client
        auto-start) that loses the ownership election exits 0 silently:
        the race had a winner and it is serving. An explicit start that
        cannot own the channel is a hard error.
        """
        # Import cli BEFORE installing our signal handlers (see
        # _install_signal_handlers) and before any play can arrive; the
        # daemon pays the import cost once at startup.
        from agent_tts import cli

        self._cli = cli

        _write_player_locks()  # election + informational PID_FILE (RF-AT-04-1)
        if not owns_channel():
            if not self.implicit:
                print(
                    "agent-tts: another process owns the control channel; daemon not started",
                    file=sys.stderr,
                )
                return 1
            return 0

        self.ipc_server = IPCServer(command_handler=self.handle_command, socket_path=self.socket_path)
        self.ipc_server.start()
        if self.ipc_server.server_sock is None:
            print(
                "agent-tts: daemon could not expose the control channel",
                file=sys.stderr,
            )
            cleanup_locks()
            return 1

        self._install_signal_handlers()
        try:
            while not self._stop.is_set():
                if self._idle_expired():
                    break  # orderly idle exit (RF-AT-04-7)
                self._stop.wait(self.POLL_INTERVAL_SEC)
        finally:
            self._shutdown()
        return 0

    def _shutdown(self) -> None:
        """Orderly teardown: stop playback, drain requests, free the channel.

        New play registrations are refused from the moment this starts
        (the flag and the session snapshot share the lock): a play that
        registers after the snapshot would otherwise run through the
        drain without ever receiving stop (RS-1).
        """
        with self._lock:
            self._shutting_down = True
            sessions = list(self._sessions)
        for session in sessions:
            try:
                session.stop()
            except Exception:
                pass
        self.queue_manager.shutdown()  # stops supervision with the daemon
        # Waiters still registered belong to plays that never dispatched
        # (their items were dropped by the closing queue): refuse them
        # deterministically instead of letting their handlers hang through
        # the drain (RS-1 parity — a play that never ran is a refusal, not
        # a silent loss). Dispatched items release their own waiters.
        with self._lock:
            stranded = list(self._queue_waiters.values())
            self._queue_waiters.clear()
        for waiter in stranded:
            waiter.release(PlaybackOutcome.FAILED, "daemon shutting down")
        # Let in-flight handlers (a play mid-playback) observe the stop and
        # send their final reply before the channel disappears.
        deadline = time.time() + self.DRAIN_TIMEOUT_SEC
        while time.time() < deadline:
            with self._lock:
                if self._inflight == 0:
                    break
            time.sleep(0.02)
        if self.ipc_server is not None:
            self.ipc_server.stop()  # removes the transport marker when owner
        cleanup_locks()  # removes socket/pid/lock when owner, releases flock

    # --- IPC dispatch ---------------------------------------------------------

    def handle_command(self, cmd: str) -> str:
        """Serves one IPC command line (daemon commands + session commands).

        Reply schema (A3, freeze-critical): daemon-level errors are
        ``ok=false error=<free text, final field>``; acks carry
        ``ok=true``; success payloads keep their classic prefixes
        (``pong``, ``status=...``, ``ok=true item=...``).
        """
        parts = cmd.strip().split(maxsplit=1)
        if not parts:
            return "ok=false error=empty command"
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        self._touch()  # any request resets the idle clock (RF-AT-04-7)

        if action == "ping":
            return f"pong version={__version__} uptime={self.uptime_sec():.0f}"

        if action == "shutdown":
            self.request_shutdown()
            return "ok=true shutting_down=true"

        if action == "play":
            return self._handle_play(rest)

        if action == "enqueue":
            return self._handle_enqueue(rest)

        with self._lock:
            session = self.active_session

        if session is None:
            if action == "status":
                provider = self.providers.last_name or "none"
                return (
                    f"status=idle uptime={self.uptime_sec():.0f} "
                    f"provider={provider} playback={self.startup_target} "
                    f"{self._queue_status_fields()}"
                )
            if action == "stop":
                # Idempotent silence: nothing is playing, the user's goal
                # ("be quiet") already holds.
                return "status=stopped"
            # Typed error (A3): a control command against an idle daemon is
            # an explicit ok=false reply, never payload-shaped text.
            return "ok=false error=no active playback session"

        reply = session.handle_ipc_command(cmd)
        if reply.startswith("status="):
            # US-AT-04-3: daemon fields extend every status-bearing reply
            # (status/pause/resume/seek embed the session status); the
            # queue fields ride along since RF-AT-08-5.
            reply = _inject_daemon_fields(
                reply,
                f" uptime={self.uptime_sec():.0f} {self._queue_status_fields()}",
            )
        elif reply.startswith("ERR: "):
            # Session-level errors adopt the typed schema at the daemon
            # dispatch boundary (A3): the legacy in-process sessions keep
            # their own ERR: strings, the daemon never leaks them.
            reply = "ok=false error=" + reply[len("ERR: "):]
        return reply

    def _queue_status_fields(self) -> str:
        """Queue snapshot rendered as the queue_len/queue status fields."""
        return encode_queue_fields(self.queue_manager.snapshot().as_dict())

    # --- play (RF-AT-04-3, RF-AT-04-6) -----------------------------------------

    def _request_target(self, requested: str) -> str:
        """Per-request playback target: an explicit value forces it (RF-AT-04-6)."""
        if requested:
            return _resolve_or_local(requested)
        return self.startup_target

    def _build_engine(self, payload: dict) -> TTSProvider:
        """Builds (or reuses) the provider for this request (RNF-AT-04-5)."""
        return self.providers.get(
            provider_name=payload.get("provider") or "edge",
            openai_key=payload.get("openai_key") or None,
            openai_base_url=payload.get("openai_base_url") or None,
            openai_model=payload.get("openai_model") or None,
            eleven_key=payload.get("eleven_key") or None,
            eleven_model=payload.get("eleven_model") or None,
            piper_model=payload.get("piper_model") or None,
        )

    def _ensure_cli(self):
        """Returns the cli module, importing it once (daemon pays it at startup)."""
        if self._cli is None:
            from agent_tts import cli as _cli_module

            self._cli = _cli_module
        return self._cli

    def _handle_play(self, payload_str: str) -> str:
        """One delegated play: queued playback or direct synthesis.

        Audible plays ride the priority queue as ``working``/``queue``
        (D4: a plain play NEVER rejects with busy — it waits its turn)
        and keep the classic blocking reply: the handler sleeps until ITS
        item finalizes and answers ``status=done`` / ``status=stopped`` /
        ``ok=false error=...``. A synthesis-only (``no_play``) request
        owns no audio, displaces nothing, and runs directly on this
        connection thread.
        """
        cli = self._ensure_cli()
        try:
            payload = json.loads(payload_str) if payload_str.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except ValueError as e:
            return f"ok=false error=invalid play payload: {e}"

        text = (payload.get("text") or "").strip()
        file_path = payload.get("file") or ""
        if not text and not file_path and payload.get("chain") is None:
            return "ok=false error=play requires text, file, or chain"
        chain_error = _chain_payload_error(payload)
        if chain_error is not None:
            return f"ok=false error={chain_error}"
        no_play = bool(payload.get("no_play"))
        if no_play:
            return self._run_direct_synthesis(cli, payload)

        waiter = _QueueWaiter()
        with self._lock:
            if self._shutting_down:
                return "ok=false error=daemon shutting down"
            token = next(self._waiter_tokens)
            self._queue_waiters[token] = waiter
            self._inflight += 1
        try:
            try:
                self.queue_manager.enqueue(
                    priority=Priority.WORKING,
                    policy=Policy.QUEUE,
                    payload={**payload, self.WAITER_KEY: token},
                )
            except RuntimeError as e:
                # Manager closed (shutdown won the race): typed refusal.
                return f"ok=false error={e}"
            waiter.event.wait()  # blocking semantics: reply when OUR item ends
            if waiter.outcome is PlaybackOutcome.FAILED:
                return f"ok=false error={waiter.error}"
            if waiter.outcome is PlaybackOutcome.STOPPED:
                return "status=stopped"
            return "status=done"
        finally:
            self._touch()  # the idle clock starts when playback ends
            with self._lock:
                self._queue_waiters.pop(token, None)
                self._inflight -= 1

    def _run_direct_synthesis(self, cli, payload: dict) -> str:
        """Synthesis-only (no_play) request: runs the pipeline, no session.

        Claims no audio and touches neither the queue nor active_session;
        failures reply with the typed error shape.
        """
        with self._lock:
            if self._shutting_down:
                return "ok=false error=daemon shutting down"
            self._inflight += 1
        try:
            asyncio.run(
                cli._play_speech(
                    None,
                    (payload.get("text") or "").strip(),
                    voice=payload.get("voice") or DEFAULT_VOICE,
                    rate=payload.get("rate") or DEFAULT_RATE,
                    volume=payload.get("volume") or "+0%",
                    pitch=payload.get("pitch") or "+0Hz",
                    output_file=payload.get("output_file") or None,
                    no_play=True,
                    provider=payload.get("provider") or "edge",
                    openai_key=payload.get("openai_key") or None,
                    openai_base_url=payload.get("openai_base_url") or None,
                    openai_model=payload.get("openai_model") or None,
                    eleven_key=payload.get("eleven_key") or None,
                    eleven_model=payload.get("eleven_model") or None,
                    piper_model=payload.get("piper_model") or None,
                    auto_lang=bool(payload.get("auto_lang")),
                    podcast=bool(payload.get("podcast")),
                    podcast_title=payload.get("podcast_title") or "",
                    stream=payload.get("stream") or "auto",
                    persist_name=payload.get("persist_name") or None,
                    engine=self._build_engine(payload),
                )
            )
            return "status=done"
        except Exception as e:
            print(f"Playback error: {e}", file=sys.stderr)
            return f"ok=false error={e}"
        finally:
            self._touch()
            with self._lock:
                self._inflight -= 1

    def _handle_enqueue(self, payload_str: str) -> str:
        """Queue-aware enqueue (RF-AT-08-1/2, US-AT-08-4): accepts and returns.

        The payload is the play JSON plus queue-control fields —
        ``priority`` (blocked|done|working, default working), ``policy``
        (preempt|queue|coalesce, default queue), and the optional
        ``event_type``/``identifiers`` carried by coalesce announcements.
        Unknown labels are typed errors (ok=false). The reply is
        immediate: ``ok=true item=<id> queue_len=<n>`` (plus
        ``coalesced=<n>`` when the event merged into an open window) —
        playback runs daemon-owned, the client does not wait for it.
        """
        try:
            envelope = json.loads(payload_str) if payload_str.strip() else {}
            if not isinstance(envelope, dict):
                raise ValueError("payload must be a JSON object")
        except ValueError as e:
            return f"ok=false error=invalid enqueue payload: {e}"

        payload = dict(envelope)
        priority_label = payload.pop("priority", "working")
        policy_label = payload.pop("policy", "queue")
        event_type = str(payload.pop("event_type", "") or "")
        identifiers = payload.pop("identifiers", None) or []
        if not isinstance(identifiers, list) or not all(
            isinstance(i, (str, int, float)) for i in identifiers
        ):
            return "ok=false error=identifiers must be a list of strings"

        try:
            priority = Priority.from_label(priority_label)
        except ValueError as e:
            return f"ok=false error={e}"
        try:
            policy = Policy(str(policy_label).strip().lower())
        except ValueError:
            return f"ok=false error=unknown policy label: {policy_label!r}"

        if payload.get("no_play"):
            return "ok=false error=enqueue does not support no_play (use play)"
        chain_error = _chain_payload_error(payload)
        if chain_error is not None:
            return f"ok=false error={chain_error}"
        if (
            not (payload.get("text") or "").strip()
            and not payload.get("file")
            and payload.get("chain") is None
        ):
            return "ok=false error=play requires text, file, or chain"

        with self._lock:
            if self._shutting_down:
                return "ok=false error=daemon shutting down"
        try:
            item_id = self.queue_manager.enqueue(
                priority=priority,
                policy=policy,
                payload=payload,
                event_type=event_type,
                identifiers=[str(i) for i in identifiers],
            )
        except RuntimeError as e:
            return f"ok=false error={e}"

        # Position visibility: pending count after insertion, and the
        # merge count for coalesce items that joined an open window.
        snapshot = self.queue_manager.snapshot()
        coalesced = 1
        for view in snapshot.pending:
            if view.id == item_id:
                coalesced = view.coalesced
                break
        else:
            if snapshot.active is not None and snapshot.active.id == item_id:
                coalesced = snapshot.active.coalesced
        reply = f"ok=true item={item_id} queue_len={snapshot.queue_len}"
        if coalesced > 1:
            reply += f" coalesced={coalesced}"
        return reply

    # --- Queue runner adapter (BLOQUE 1.3 T3) ---------------------------------

    def _queue_runner(self, item, on_finished):
        """Real runner for QueueManager: starts playback, returns the handle.

        Contract (see QueueManager): start asynchronously, return the
        liveness SessionHandle promptly, call ``on_finished(outcome,
        error)`` exactly once when playback truly ends — from the worker
        thread this spawns. Liveness rides the existing session state
        (status/current_frame/total_frames/producing) through
        AudioSessionHandle.

        Synthesis liveness (documented choice): silent non-streaming
        synthesis emits NO progress tokens, so the wedged timeout must be
        SIZED to exceed the worst-case silent synthesis interval — there
        are no synthetic milestones from this adapter. The daemon exposes
        ``--wedged-timeout`` / AGENT_TTS_WEDGED_TIMEOUT for exactly that;
        the 30 s default comfortably covers typical synthesis, and a
        session that flips to ``playing`` resets the silent clock through
        the status token change.
        """
        label = str((item.payload or {}).get("label") or item.event_type or "")
        _queue_trace(
            f"dispatch item={item.id} label={label!r} "
            f"prio={item.priority.name.lower()} coalesced={item.coalesced} "
            f"t={time.monotonic():.9f}"
        )
        payload = dict(item.payload or {})
        waiter = None
        token = payload.pop(self.WAITER_KEY, None)
        if token is not None:
            with self._lock:
                waiter = self._queue_waiters.pop(token, None)
        if item.coalesced > 1 and item.announcement:
            # RF-AT-08-3: the merged item speaks ONE synthesized summary
            # (count + up to three identifiers); the window owner's own
            # payload is replaced, not concatenated.
            payload = {**payload, "text": item.announcement, "file": ""}

        try:
            session = self._build_queue_session(payload)
        except Exception as e:
            if waiter is not None:
                waiter.release(PlaybackOutcome.FAILED, str(e))
            raise  # the manager records the item as FAILED (A5 visibility)

        with self._lock:
            if self._shutting_down:
                # RS-1: never register a session into a shutdown that
                # already snapshotted — the play is a typed refusal.
                if waiter is not None:
                    waiter.release(PlaybackOutcome.FAILED, "daemon shutting down")
                raise RuntimeError("daemon shutting down")
            self.active_session = session  # queue mount point (BLOQUE 1.3)
            self._sessions.add(session)

        cli = self._ensure_cli()

        def worker() -> None:
            outcome = PlaybackOutcome.COMPLETED
            error: Optional[str] = None
            try:
                if payload.get("chain"):
                    self._play_chain(
                        session, payload["chain"], payload.get("chain_gap_ms", 0), item.id
                    )
                elif payload.get("file"):
                    self._play_file(session, payload["file"])
                else:
                    asyncio.run(
                        cli._play_speech(
                            session,
                            (payload.get("text") or "").strip(),
                            voice=payload.get("voice") or DEFAULT_VOICE,
                            rate=payload.get("rate") or DEFAULT_RATE,
                            volume=payload.get("volume") or "+0%",
                            pitch=payload.get("pitch") or "+0Hz",
                            output_file=payload.get("output_file") or None,
                            no_play=False,
                            provider=payload.get("provider") or "edge",
                            openai_key=payload.get("openai_key") or None,
                            openai_base_url=payload.get("openai_base_url") or None,
                            openai_model=payload.get("openai_model") or None,
                            eleven_key=payload.get("eleven_key") or None,
                            eleven_model=payload.get("eleven_model") or None,
                            piper_model=payload.get("piper_model") or None,
                            auto_lang=bool(payload.get("auto_lang")),
                            podcast=bool(payload.get("podcast")),
                            podcast_title=payload.get("podcast_title") or "",
                            stream=payload.get("stream") or "auto",
                            persist_name=payload.get("persist_name") or None,
                            engine=self._build_engine(payload),
                        )
                    )
                if bool(session.state.get("stop")):
                    outcome = PlaybackOutcome.STOPPED
            except Exception as e:
                print(f"Playback error: {e}", file=sys.stderr)
                outcome = PlaybackOutcome.FAILED
                error = str(e)
            finally:
                self._touch()  # the idle clock starts when playback ends
                with self._lock:
                    self._sessions.discard(session)
                    if self.active_session is session:
                        self.active_session = None
                try:
                    session.stop()
                except Exception:
                    pass
                # Order is load-bearing (RF-AT-08-6): the session is dead
                # and unmounted BEFORE the queue finalizes this item, so
                # the next dispatch finds a free active-session slot and
                # playback never overlaps.
                _queue_trace(
                    f"finalize item={item.id} outcome={outcome.value} "
                    f"t={time.monotonic():.9f}"
                )
                try:
                    on_finished(outcome, error)
                except Exception:
                    pass
                if waiter is not None:
                    waiter.release(outcome, error)

        threading.Thread(
            target=worker, name="agent-tts-queue-playback", daemon=True
        ).start()
        return AudioSessionHandle(session)

    def _build_queue_session(self, payload: dict):
        """Builds the playback session for a dispatched queue item.

        Mirrors the request semantics of the pre-queue play path: session
        metadata from the request, per-request target, winhost endpoint
        overlay, and the replay local-fallback contract (B6) — a file or
        chain play whose remote target is unavailable degrades to local
        with one warning, a text play surfaces the unavailability as a
        typed error.
        """
        cli = self._ensure_cli()
        text = (payload.get("text") or "").strip()
        file_path = payload.get("file") or ""
        chain_files = payload.get("chain") or []
        target = self._request_target(payload.get("playback") or "")
        if payload.get("label"):
            label = payload["label"]
        elif chain_files:
            # One readable identity for the whole chain: the first file
            # plus how many more follow it.
            label = f"chain: {os.path.basename(chain_files[0])}"
            if len(chain_files) > 1:
                label += f" +{len(chain_files) - 1} more"
        else:
            label = f"{len(text)} chars" if text else os.path.basename(file_path)
        session_kwargs = dict(
            auto_rewind_sec=float(payload.get("auto_rewind_sec", 2.0)),
            highlight=bool(payload.get("highlight")),
            autoscroll=bool(payload.get("autoscroll")),
            bionic=bool(payload.get("bionic")),
            zen=bool(payload.get("zen")),
            # The session's status metadata mirrors the request
            # (same defaults _play_speech applies to synthesis),
            # and remote targets resolve the winhost endpoint
            # against the client's per-request overlay.
            provider=payload.get("provider") or "edge",
            voice=payload.get("voice") or DEFAULT_VOICE,
            env=_session_env(payload),
        )
        try:
            return cli._build_playback_session(target, label, **session_kwargs)
        except SystemExit as e:
            if not file_path and not chain_files:
                # Speak-path contract: _build_playback_session exits(1)
                # with its own message when the requested target is
                # unavailable; surface it as a typed error instead of
                # killing the daemon.
                raise RuntimeError(f"playback target unavailable (exit {e.code})") from e
            # Replay contract (legacy play_mp3_file, README replay
            # section): an unavailable remote target degrades to the
            # local device with one warning, not a hard error. A chain
            # is replayed audio and follows the same contract.
            print(
                f"Playback target '{target}' unavailable; falling back to local playback",
                file=sys.stderr,
            )
            return cli._build_playback_session("local", label, **session_kwargs)

    @staticmethod
    def _play_file(session, file_path: str) -> None:
        """Decodes an audio file and plays it through the active session."""
        with open(file_path, "rb") as f:
            data = f.read()
        session.play(miniaudio.decode(data))

    def _play_chain(self, session, files: list, gap_ms, item_id: int) -> None:
        """Plays the chain as ONE session over the assembled stream (RF-AT-08-4).

        The files decode into one continuous PCM stream with the
        configurable inter-item silence (agent_tts.chain); the session's
        boundary map becomes the COMBINED chain-global map before play
        begins, so every control (seek/pause/phrase navigation) mounted
        on the session addresses the whole chain from the first frame.
        One session, one play() call: gapless by construction, and a
        queue-side terminate (stop flag) cuts mid-stream so the files
        after the cut are never played.

        A ChainError (missing/undecodable file, format mismatch) raises
        RuntimeError: the worker marks the item FAILED with the message
        (A5 visibility).
        """
        try:
            assembled = assemble_chain_files(files, float(gap_ms or 0))
        except ChainError as e:
            raise RuntimeError(str(e)) from e
        with session.lock:
            session.boundaries = assembled.boundaries

        done = threading.Event()
        watcher = threading.Thread(
            target=self._watch_chain_boundaries,
            args=(session, assembled, item_id, done),
            name="agent-tts-chain-trace",
            daemon=True,
        )
        watcher.start()
        try:
            session.play(assembled.decoded)
        finally:
            done.set()
            watcher.join(timeout=1.0)  # flush late chain-item traces before finalize

    @staticmethod
    def _watch_chain_boundaries(session, assembled, item_id: int, done: threading.Event) -> None:
        """Traces each chain item's start as the playback cursor crosses it.

        The seam definition (RNF-AT-08-1 chain application): the trace
        timestamp of item i+1 minus the trace timestamp of item i, minus
        the audio time between their chain-global starts (item i's
        duration + the gap), is the wall-clock hole between chain items
        — the quantity the < 50 ms budget bounds. Position comes from
        the session itself (``pos_frames`` when the target exposes it,
        else the frame cursor), so the seam rides targets that report a
        live position and stays quiet on those that do not.
        """
        pending = [(item.index, item.start_sec) for item in assembled.items]
        position_of = getattr(session, "pos_frames", None)
        while pending and not done.is_set():
            state = session.state
            if state.get("stop") or state.get("status") == "stopped":
                return
            if state.get("status") == "playing":
                rate = session.sample_rate or 0
                if rate:
                    frames = position_of() if callable(position_of) else session.current_frame
                    pos = frames / float(rate)
                    while pending and pending[0][1] <= pos:
                        index, start = pending.pop(0)
                        _queue_trace(
                            f"chain-item item={item_id} index={index} "
                            f"pos={pos:.3f} t={time.monotonic():.9f}"
                        )
            time.sleep(CHAIN_TRACE_POLL_SEC)


def run_daemon(
    socket_path: str = IPC_SOCKET,
    idle_timeout_sec: Optional[float] = None,
    implicit: bool = False,
    coalesce_window_sec: Optional[float] = None,
    wedged_timeout_sec: Optional[float] = None,
) -> int:
    """Entry point for a daemon process; returns the exit code."""
    return Daemon(
        socket_path=socket_path,
        idle_timeout_sec=idle_timeout_sec,
        implicit=implicit,
        coalesce_window_sec=coalesce_window_sec,
        wedged_timeout_sec=wedged_timeout_sec,
    ).run()


# --- Client side of the vía única (RF-AT-04-5, RF-AT-04-8, RNF-AT-04-3) ---------------


class DaemonUnavailableError(RuntimeError):
    """Raised when no daemon can be reached, started, or respawned."""


def probe_daemon(
    timeout_sec: float = PING_TIMEOUT_SEC, socket_path: str = IPC_SOCKET
):
    """Classifies the control channel for the client handshake.

    Returns (status, reply) with status one of:
    - "ok": a healthy daemon answered ``pong`` within the budget;
    - "unreachable": no transport accepts connections (no daemon);
    - "wedged": the transport accepts but no ping answer arrives in time
      (RF-AT-04-8: kill-and-respawn candidate). A STALE daemon still
      speaking the pre-BLOQUE 1.3 line protocol lands here too: the v2
      ping frame carries no newline byte, so the old daemon never finds
      a line to answer — the existing kill-and-respawn machinery then
      replaces it with a current daemon (the chosen protocol-mismatch
      recovery; no new respawn path);
    - "foreign": a live owner answered bytes that are not a v2 frame or
      are not a pong (a library-embedded speak() or an older playback
      process).
    """
    import socket as socket_mod

    try:
        client = connect_to_server(socket_path)
    except Exception:
        return ("unreachable", None)
    if client is None:
        return ("unreachable", None)
    try:
        client.settimeout(timeout_sec)
        send_frame(client, "ping")
        try:
            reply = read_frame(client)
        except ProtocolMismatchError:
            # Something answered, but not in framing v2: a live foreign
            # owner, not our daemon.
            return ("foreign", None)
        except (socket_mod.timeout, TimeoutError, OSError):
            return ("wedged", None)
        if reply.startswith("pong"):
            return ("ok", reply)
        if not reply:
            return ("wedged", None)
        return ("foreign", reply)
    finally:
        try:
            client.close()
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal (different user)
    except OSError:
        return True
    return True


def _pid_looks_like_agent_tts(pid: int) -> bool:
    """Best-effort identity check before killing a wedged owner.

    POSIX: the owner's cmdline must mention agent_tts/agent-tts. When
    /proc is unavailable (non-POSIX), the check cannot run — but the
    wedge probe already proved a hung owner exists and PID_FILE was
    written by whoever owns the channel, so killing proceeds.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").lower()
    except OSError:
        return True
    return b"agent_tts" in cmdline or b"agent-tts" in cmdline


def _kill_wedged_daemon() -> bool:
    """Force-kills the wedged daemon identified by PID_FILE (RF-AT-04-8).

    The wedge probe proved the transport accepts connections but never
    answers, so the owner cannot clean up after itself. PID_FILE is the
    informational marker the owner wrote at startup; on its death the
    kernel frees the flock and the socket file becomes a harmless orphan
    that the respawned daemon reclaims after winning the election.
    Returns True when a kill was delivered.
    """
    try:
        with open(audio_mod.PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid() or not _pid_alive(pid):
        return False
    if not _pid_looks_like_agent_tts(pid):
        return False
    try:
        # SIGKILL is POSIX-only (native Windows has no such constant, and
        # the AttributeError would escape the OSError handler): fall back
        # to the classic value 9, which on Windows os.kill maps to an
        # unconditional TerminateProcess — still a forced kill.
        os.kill(pid, getattr(signal, "SIGKILL", 9))
    except OSError:
        return False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.02)
    return False


def _spawn_daemon(idle_timeout_sec: Optional[float], socket_path: str = IPC_SOCKET) -> None:
    """Starts a detached daemon inheriting this environment (RF-AT-04-5).

    Detached (new session) so the daemon outlives the client; stderr goes
    to the daemon log so respawn failures leave a trace (RNF-AT-04-3).
    The requested channel travels on the command line (--socket): an
    explicit socket_path parameter must reach the child even when it
    differs from the inherited environment's AGENT_TTS_SOCKET default
    (on Windows the value is inert — the channel there is the TCP port
    marker). AGENT_TTS_PLAYBACK and the lock/pid overrides still travel
    through the inherited environment (RF-AT-04-6 startup resolution).
    """
    command = [sys.executable, "-m", "agent_tts.daemon", "--implicit", "--socket", socket_path]
    if idle_timeout_sec is not None:
        command += ["--idle-timeout", str(idle_timeout_sec)]
    try:
        with open(DAEMON_LOG_FILE, "ab") as log:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
                env=dict(os.environ),
                close_fds=True,
            )
    except OSError as e:
        print(f"agent-tts: could not spawn the daemon: {e}", file=sys.stderr)


def ensure_daemon(
    socket_path: str = IPC_SOCKET,
    autostart_timeout_sec: float = DAEMON_START_TIMEOUT_SEC,
) -> str:
    """Vía única handshake: healthy daemon, or transparent auto-start.

    RF-AT-04-5: ping within the 200 ms budget; on no answer, start a
    daemon, verify again, and delegate. RF-AT-04-8: a wedged daemon is
    killed and respawned first. RNF-AT-04-3: persistent failure raises a
    clear, logged error — there is no classic fallback path.

    Returns the pong reply of the healthy daemon.
    """
    status, reply = probe_daemon(PING_TIMEOUT_SEC, socket_path)
    if status == "ok":
        return reply

    if status == "foreign":
        # A live non-daemon owner (library-embedded speak, older process)
        # holds the channel and exits with its playback: wait bounded, then
        # the regular auto-start applies once the channel frees.
        deadline = time.monotonic() + autostart_timeout_sec
        while time.monotonic() < deadline and status == "foreign":
            time.sleep(0.1)
            status, reply = probe_daemon(PING_TIMEOUT_SEC, socket_path)
            if status == "ok":
                return reply
        if status == "foreign":
            raise DaemonUnavailableError(
                "agent-tts: the control channel is held by a non-daemon playback; "
                "retry once it finishes"
            )

    if status == "wedged":
        # Kill-and-respawn (RF-AT-04-8): the wedged owner keeps the audio
        # device hostage; only its death frees the channel.
        _kill_wedged_daemon()

    # Unreachable (or just killed): transparent auto-start (RF-AT-04-5).
    _spawn_daemon(autostart_idle_timeout_sec(), socket_path)
    deadline = time.monotonic() + autostart_timeout_sec
    while time.monotonic() < deadline:
        status, reply = probe_daemon(PING_TIMEOUT_SEC, socket_path)
        if status == "ok":
            return reply
        time.sleep(0.05)
    raise DaemonUnavailableError(
        "agent-tts: daemon auto-start failed (no ping answer within "
        f"{autostart_timeout_sec:.0f}s; daemon log: {DAEMON_LOG_FILE})"
    )


def send_play(payload: dict, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends one play command and blocks for the final reply.

    The play line is validated against the framing payload cap LOCALLY,
    before any socket write (A2''=B1''): an oversized delegation returns
    a typed ``ERR: command too large`` reply immediately instead of
    degrading to a broken pipe or a silent mid-playback connection loss.
    The read has no timeout: a delegated play blocks until playback
    ends, mirroring the classic CLI's semantics. Returns None when the
    daemon closed the connection mid-playback.
    """
    line = "play " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        frame = encode_frame(line.strip())  # local cap check, no wire I/O
    except CommandTooLargeError as e:
        return f"ERR: {e}"
    try:
        client = connect_to_server(socket_path)
    except Exception:
        return None
    if client is None:
        return None
    try:
        client.settimeout(None)  # blocking: the reply comes when playback ends
        client.sendall(frame)
        try:
            return read_frame(client)
        except Exception:
            return None
    except Exception:
        return None
    finally:
        try:
            client.close()
        except OSError:
            pass


def _best_effort_stop(socket_path: str = IPC_SOCKET) -> None:
    """Forwards a stop after an interrupt so audio does not outlive Ctrl-C."""
    try:
        send_ipc_command("stop", socket_path=socket_path)
    except Exception:
        pass


def delegate_play(payload: dict, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Ensures a daemon and runs one play; returns the daemon's reply.

    Blocking by design (parity with the classic CLI). KeyboardInterrupt
    forwards a best-effort stop to the daemon before re-raising, matching
    the old in-process Ctrl-C semantics.
    """
    ensure_daemon(socket_path=socket_path)
    try:
        return send_play(payload, socket_path=socket_path)
    except KeyboardInterrupt:
        _best_effort_stop(socket_path)
        raise


def send_enqueue(payload: dict, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends one enqueue command; returns the immediate ack reply.

    Same local frame-cap discipline as send_play (an oversized request
    is a typed ``ERR:`` reply before any wire write). The read keeps
    the connection's standard client timeout: enqueue replies
    immediately — playback is daemon-owned — unlike the blocking play.
    """
    line = "enqueue " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        frame = encode_frame(line.strip())  # local cap check, no wire I/O
    except CommandTooLargeError as e:
        return f"ERR: {e}"
    try:
        client = connect_to_server(socket_path)
    except Exception:
        return None
    if client is None:
        return None
    try:
        client.sendall(frame)
        try:
            return read_frame(client)
        except Exception:
            return None
    except Exception:
        return None
    finally:
        try:
            client.close()
        except OSError:
            pass


def delegate_enqueue(payload: dict, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Ensures a daemon and enqueues one event; returns the ack reply.

    Fire-and-forget by design (US-AT-08-4): the daemon owns playback,
    the client never waits for it. KeyboardInterrupt does NOT forward a
    stop — an enqueued event owns no audio yet, and a stop would kill
    another event's active announcement.
    """
    ensure_daemon(socket_path=socket_path)
    return send_enqueue(payload, socket_path=socket_path)


def send_control_command(command: str, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends a control command with the daemon health handshake (RF-AT-04-8).

    Control commands never auto-start a daemon: with nothing playing
    there is nothing to control, and the typed idle error
    (``ok=false error=no active playback session``) keeps its text. A
    wedged daemon is killed and respawned first — the user must never
    lose voice control; a live non-daemon owner is addressed directly
    (library-embedded speak stays servable).
    """
    status, _ = probe_daemon(PING_TIMEOUT_SEC, socket_path)
    if status == "foreign":
        return send_ipc_command(command, socket_path)
    if status == "unreachable":
        return None
    if status == "wedged":
        try:
            ensure_daemon(socket_path=socket_path)
        except DaemonUnavailableError as e:
            print(f"agent-tts: {e}", file=sys.stderr)
            return None
    return send_ipc_command(command, socket_path=socket_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-tts-daemon",
        description="Persistent agent-tts playback daemon (vía única, AT-04)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        help="Exit orderly after this many seconds without requests "
        "(default: no timeout; the client auto-start passes 30 minutes, "
        "configurable via AGENT_TTS_IDLE_TIMEOUT)",
    )
    parser.add_argument(
        "--coalesce-window",
        type=float,
        default=None,
        metavar="SEC",
        help="Coalescing window for queue events with policy=coalesce: same "
        "event_type merges into one announcement while the window is open "
        "(default 5 s; env AGENT_TTS_COALESCE_WINDOW)",
    )
    parser.add_argument(
        "--wedged-timeout",
        type=float,
        default=None,
        metavar="SEC",
        help="Queue liveness budget: a dispatched playback making no progress "
        "for this long is terminated and the queue moves on. Silent "
        "non-streaming synthesis emits no progress, so this must exceed your "
        "worst-case synthesis time (default 30 s; env AGENT_TTS_WEDGED_TIMEOUT)",
    )
    parser.add_argument(
        "--implicit",
        action="store_true",
        help="Mark as auto-started by a client: an election loss exits silently",
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="Control channel path to serve (default: AGENT_TTS_SOCKET or the "
        "platform default; inert on Windows, where the channel is the TCP "
        "port marker)",
    )
    args = parser.parse_args(argv)
    return run_daemon(
        socket_path=args.socket or IPC_SOCKET,
        idle_timeout_sec=args.idle_timeout,
        implicit=args.implicit,
        coalesce_window_sec=args.coalesce_window,
        wedged_timeout_sec=args.wedged_timeout,
    )


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
