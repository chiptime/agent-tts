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
``ping`` answers version and uptime, ``shutdown`` terminates the daemon.
``play`` replies when the playback ends (status=done / status=stopped /
ERR: ...) so a delegating client keeps the classic CLI's blocking
semantics — the reply is served on its own connection thread while
concurrent control commands keep flowing.

Provider instances stay alive between requests (RNF-AT-04-5): today
get_provider() builds an instance per call — kokoro reloads its ONNX
session per instance — so the daemon keeps a cache keyed by provider
configuration and pays the cold load once per configuration.
"""

import argparse
import asyncio
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
from agent_tts.constants import (
    DAEMON_LOG_FILE,
    DAEMON_START_TIMEOUT_SEC,
    DEFAULT_AUTOSTART_IDLE_TIMEOUT_SEC,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    ENV_IDLE_TIMEOUT,
    IPC_SOCKET,
    PING_TIMEOUT_SEC,
)
from agent_tts.ipc import IPCServer, connect_to_server, send_ipc_command
from agent_tts.ownership import owns_channel
from agent_tts.playback_target import InvalidPlaybackTarget, resolve_target
from agent_tts.providers import TTSProvider, get_provider


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


class Daemon:
    """Long-lived owner of the control channel and of playback state.

    ``active_session`` is the queue mount point required by the BLOQUE 1.3
    plan: the queue manager will dispatch into sessions through this
    attribute; in this block it simply holds the one in-flight playback.
    """

    POLL_INTERVAL_SEC = 0.1
    DRAIN_TIMEOUT_SEC = 5.0

    def __init__(
        self,
        socket_path: str = IPC_SOCKET,
        idle_timeout_sec: Optional[float] = None,
        implicit: bool = False,
        provider_cache: Optional[ProviderCache] = None,
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
        self._last_request = time.time()
        self._inflight = 0
        self._stop = threading.Event()
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
        """Orderly teardown: stop playback, drain requests, free the channel."""
        with self._lock:
            session = self.active_session
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass
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
        """Serves one IPC command line (daemon commands + session commands)."""
        parts = cmd.strip().split(maxsplit=1)
        if not parts:
            return "ERR: empty command"
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        self._touch()  # any request resets the idle clock (RF-AT-04-7)

        if action == "ping":
            return f"pong version={__version__} uptime={self.uptime_sec():.0f}"

        if action == "shutdown":
            self.request_shutdown()
            return "ok shutting_down=true"

        if action == "play":
            return self._handle_play(rest)

        with self._lock:
            session = self.active_session

        if session is None:
            if action == "status":
                provider = self.providers.last_name or "none"
                return (
                    f"status=idle uptime={self.uptime_sec():.0f} "
                    f"provider={provider} playback={self.startup_target}"
                )
            if action == "stop":
                # Idempotent silence: nothing is playing, the user's goal
                # ("be quiet") already holds.
                return "status=stopped"
            return "ERR: no active playback session"

        reply = session.handle_ipc_command(cmd)
        if reply.startswith("status="):
            # US-AT-04-3: daemon fields extend every status-bearing reply
            # (status/pause/resume/seek embed the session status); queue
            # fields arrive with RF-AT-08-5 in BLOQUE 1.3.
            reply = _inject_daemon_fields(reply, f" uptime={self.uptime_sec():.0f}")
        return reply

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

    def _handle_play(self, payload_str: str) -> str:
        """Runs one synthesis+playback; replies when it ends.

        Executed on the connection's own thread: blocking here is the
        intended blocking semantics of a delegated play, while control
        commands keep being served on their own connections.
        """
        cli = self._cli
        if cli is None:
            from agent_tts import cli as _cli_module

            self._cli = cli = _cli_module
        try:
            payload = json.loads(payload_str) if payload_str.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except ValueError as e:
            return f"ERR: invalid play payload: {e}"

        text = (payload.get("text") or "").strip()
        file_path = payload.get("file") or ""
        if not text and not file_path:
            return "ERR: play requires text or file"
        no_play = bool(payload.get("no_play"))
        target = self._request_target(payload.get("playback") or "")
        label = payload.get("label") or (
            f"{len(text)} chars" if text else os.path.basename(file_path)
        )

        session = None
        if not no_play:
            try:
                session = cli._build_playback_session(
                    target,
                    label,
                    auto_rewind_sec=float(payload.get("auto_rewind_sec", 2.0)),
                    highlight=bool(payload.get("highlight")),
                    autoscroll=bool(payload.get("autoscroll")),
                    bionic=bool(payload.get("bionic")),
                    zen=bool(payload.get("zen")),
                )
            except SystemExit as e:
                # _build_playback_session exits(1) with its own message when
                # the requested target is unavailable; surface it as an ERR
                # reply instead of killing the daemon.
                return f"ERR: playback target unavailable (exit {e.code})"

        with self._lock:
            self.active_session = session  # queue mount point (BLOQUE 1.3)
            self._inflight += 1
        try:
            if file_path:
                self._play_file(session, file_path)
            else:
                asyncio.run(
                    cli._play_speech(
                        session,
                        text,
                        voice=payload.get("voice") or DEFAULT_VOICE,
                        rate=payload.get("rate") or DEFAULT_RATE,
                        volume=payload.get("volume") or "+0%",
                        pitch=payload.get("pitch") or "+0Hz",
                        output_file=payload.get("output_file") or None,
                        no_play=no_play,
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
            stopped = session is not None and bool(session.state.get("stop"))
            return "status=stopped" if stopped else "status=done"
        except Exception as e:
            print(f"Playback error: {e}", file=sys.stderr)
            return f"ERR: {e}"
        finally:
            self._touch()  # the idle clock starts when playback ends
            with self._lock:
                if self.active_session is session:
                    self.active_session = None
                self._inflight -= 1
            if session is not None:
                try:
                    session.stop()
                except Exception:
                    pass

    @staticmethod
    def _play_file(session, file_path: str) -> None:
        """Decodes an audio file and plays it through the active session."""
        with open(file_path, "rb") as f:
            data = f.read()
        session.play(miniaudio.decode(data))


def run_daemon(
    socket_path: str = IPC_SOCKET,
    idle_timeout_sec: Optional[float] = None,
    implicit: bool = False,
) -> int:
    """Entry point for a daemon process; returns the exit code."""
    return Daemon(
        socket_path=socket_path,
        idle_timeout_sec=idle_timeout_sec,
        implicit=implicit,
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
      (RF-AT-04-8: kill-and-respawn candidate);
    - "foreign": a live non-daemon owner answered something else (a
      library-embedded speak() or an older playback process).
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
        client.sendall(b"ping\n")
        chunks = []
        received = 0
        try:
            while received < 8192:
                chunk = client.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if b"\n" in chunk:
                    break
        except (socket_mod.timeout, TimeoutError, OSError):
            return ("wedged", None)
        reply = b"".join(chunks).decode("utf-8", errors="ignore").strip()
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
    """SIGKILLs the wedged daemon identified by PID_FILE (RF-AT-04-8).

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
        os.kill(pid, signal.SIGKILL)
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
    AGENT_TTS_PLAYBACK and the socket/lock overrides travel through the
    inherited environment (RF-AT-04-6 startup resolution).
    """
    command = [sys.executable, "-m", "agent_tts.daemon", "--implicit"]
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

    Unlike send_ipc_command, the read has no timeout: a delegated play
    blocks until playback ends, mirroring the classic CLI's semantics.
    Returns None when the daemon closed the connection mid-playback.
    """
    line = "play " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        client = connect_to_server(socket_path)
    except Exception:
        return None
    if client is None:
        return None
    try:
        client.settimeout(None)  # blocking: the reply comes when playback ends
        client.sendall(f"{line.strip()}\n".encode("utf-8"))
        chunks = []
        received = 0
        try:
            while received < 8192:
                chunk = client.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if b"\n" in chunk:
                    break
        except Exception:
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


def send_control_command(command: str, socket_path: str = IPC_SOCKET) -> Optional[str]:
    """Sends a control command with the daemon health handshake (RF-AT-04-8).

    Control commands never auto-start a daemon: with nothing playing
    there is nothing to control, and the legacy "no active playback"
    error keeps its exact text. A wedged daemon is killed and respawned
    first — the user must never lose voice control; a live non-daemon
    owner is addressed directly (library-embedded speak stays servable).
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
        "--implicit",
        action="store_true",
        help="Mark as auto-started by a client: an election loss exits silently",
    )
    args = parser.parse_args(argv)
    return run_daemon(
        idle_timeout_sec=args.idle_timeout,
        implicit=args.implicit,
    )


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
