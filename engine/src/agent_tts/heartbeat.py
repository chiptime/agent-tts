"""Daemon heartbeat dead man's switch over ntfy (HT-18 / S2).

The watcher of the fleet cannot be the process that dies with it. Instead of
running a second supervisor that can die too, the daemon keeps ONE scheduled
ntfy message permanently in the future: it publishes every interval with
``In: <window>`` and a stable sequence ID, and the ntfy server (>= v2.18)
replaces the previously scheduled message each time. While the daemon beats,
the message never reaches the phone; when it stops beating (crash, OOM, host
offline), the last scheduled message is delivered by the server itself.

Design rules:

* Opt-in (``TTS_HEARTBEAT=off`` is the default): a disabled daemon creates no
  thread, publishes nothing, and its behavior is byte-identical to before.
* One publish per interval; the identity is ``heartbeat-<sanitized-hostname>``
  so a daemon restart resumes the same schedule.
* Capability gate before arming: on a server that does not support updating
  scheduled notifications, a naive loop would create a NEW scheduled message
  every interval and the "alert" would sound every window (spam). The first
  publish is therefore also the probe: a non-2xx answer disarms the loop with
  one log line instead of entering the repeating loop.
* Publication failures are isolated: the loop logs the transition once and
  keeps beating on the next interval. The loop holds no daemon lock and never
  touches audio or IPC.

Configuration (all read from the environment, like the rest of the daemon):

* ``TTS_HEARTBEAT`` — ``off`` (default) or ``on``.
* ``TTS_HEARTBEAT_INTERVAL_S`` — seconds between publishes (default 60).
* ``TTS_HEARTBEAT_WINDOW_S`` — scheduled delivery delay (default 300);
  must be at least ``3 * interval`` so a couple of missed beats do not page.
* ``TTS_HEARTBEAT_PRIORITY`` — ``high`` (4, default) or ``urgent`` (5), plus
  the ntfy names ``min``/``low``/``default``; numeric 1-5 also works.
* ``TTS_HEARTBEAT_SERVER`` / ``TTS_HEARTBEAT_TOPIC`` — required when on.

No new dependencies: the publish helper is stdlib ``urllib.request``.
"""

from __future__ import annotations

import math
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

ENV_HEARTBEAT = "TTS_HEARTBEAT"
ENV_HEARTBEAT_INTERVAL = "TTS_HEARTBEAT_INTERVAL_S"
ENV_HEARTBEAT_WINDOW = "TTS_HEARTBEAT_WINDOW_S"
ENV_HEARTBEAT_PRIORITY = "TTS_HEARTBEAT_PRIORITY"
ENV_HEARTBEAT_SERVER = "TTS_HEARTBEAT_SERVER"
ENV_HEARTBEAT_TOPIC = "TTS_HEARTBEAT_TOPIC"

DEFAULT_INTERVAL_S = 60.0
DEFAULT_WINDOW_S = 300.0
DEFAULT_PRIORITY = "high"

# A hung publish may not hang daemon shutdown forever: the request times out
# and the thread (a daemon thread) is joined with a slightly larger budget.
HTTP_TIMEOUT_SEC = 5.0
JOIN_TIMEOUT_SEC = 6.0

THREAD_NAME = "agent-tts-heartbeat"

# ntfy sequence IDs and topics share the same charset; the sequence ID is
# capped at 64 characters (RF-1).
SEQUENCE_MAX_LEN = 64
_INVALID_SEQUENCE_RUN = re.compile(r"[^\-_A-Za-z0-9]+")
_TOPIC_RE = re.compile(r"^[-_A-Za-z0-9]{1,64}$")

_PRIORITY_LEVELS = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5, "max": 5}
_LEVEL_NAMES = {1: "min", 2: "low", 3: "default", 4: "high", 5: "urgent"}


@dataclass(frozen=True)
class HeartbeatConfig:
    """Validated heartbeat settings (present only when the switch is armed)."""

    server: str
    topic: str
    interval_s: float
    window_s: float
    priority: str
    priority_level: int
    seq_id: str
    alert_body: str


@dataclass(frozen=True)
class HeartbeatSettings:
    """Result of reading ``TTS_HEARTBEAT*`` from an environment mapping.

    ``enabled`` records the user's intent (``TTS_HEARTBEAT=on``); ``config``
    is present only when every value validated, and ``error`` explains the
    refusal otherwise (invalid window, missing server/topic, bad priority).
    """

    enabled: bool
    config: Optional[HeartbeatConfig] = None
    error: Optional[str] = None


# --- configuration -----------------------------------------------------------


def _format_seconds(value: float) -> str:
    """Single-line human duration: ``300s``, ``60s``, ``0.5s``."""
    if value == int(value):
        return f"{int(value)}s"
    return f"{value:g}s"


def _positive_seconds(
    values: Mapping[str, str], name: str, default: float
) -> "tuple[Optional[float], Optional[str]]":
    raw = (values.get(name) or "").strip()
    if not raw:
        return default, None
    try:
        value = float(raw)
    except ValueError:
        return None, f"{name}={raw!r} must be a positive number of seconds"
    if not math.isfinite(value) or value <= 0:
        return None, f"{name}={raw!r} must be a positive number of seconds"
    return value, None


def _parse_priority(
    raw: Optional[str],
) -> "tuple[Optional[str], Optional[int], Optional[str]]":
    value = (raw or DEFAULT_PRIORITY).strip().lower()
    if not value:
        value = DEFAULT_PRIORITY
    if value in _PRIORITY_LEVELS:
        return value, _PRIORITY_LEVELS[value], None
    if value.isdigit() and 1 <= int(value) <= 5:
        level = int(value)
        return _LEVEL_NAMES[level], level, None
    return (
        None,
        None,
        f"{ENV_HEARTBEAT_PRIORITY}={raw!r} must be one of "
        "min, low, default, high, urgent (or 1-5)",
    )


def sanitize_hostname(hostname: str) -> str:
    """Keeps only the ntfy sequence charset from a hostname (RF-1).

    Runs of any other character (spaces, dots, slashes, punctuation) collapse
    to one ``-``; leading/trailing dashes are trimmed so the sequence reads
    cleanly. An empty result means "no usable hostname" (caller substitutes
    ``unknown``).
    """
    return _INVALID_SEQUENCE_RUN.sub("-", hostname or "").strip("-")


def heartbeat_seq_id(hostname: Optional[str] = None) -> str:
    """Stable sequence ID ``heartbeat-<sanitized-hostname>`` (RF-1).

    One identity per host, independent of daemon restarts; capped at the ntfy
    maximum of 64 characters.
    """
    host = socket.gethostname() if hostname is None else hostname
    sequence = f"heartbeat-{sanitize_hostname(host) or 'unknown'}"
    return sequence[:SEQUENCE_MAX_LEN]


def format_window_header(window_s: float) -> str:
    """``In:`` header value: whole minutes read best (``5m``), else seconds."""
    if window_s >= 60 and window_s % 60 == 0:
        return f"{int(window_s // 60)}m"
    return _format_seconds(window_s)


def _alert_body(hostname: str, window_s: float) -> str:
    return (
        f"agent-tts daemon on {hostname} silent for {_format_seconds(window_s)} "
        "— daemon down or host unreachable"
    )


def load_heartbeat_settings(
    env: Optional[Mapping[str, str]] = None,
    hostname: Optional[str] = None,
) -> HeartbeatSettings:
    """Reads and validates ``TTS_HEARTBEAT*`` (RF-2, RF-3 config side).

    ``off``/unset returns ``enabled=False`` with no config and no error; the
    daemon then runs exactly as before. ``on`` with any invalid value returns
    ``enabled=True, config=None`` plus an error naming the offending
    variable(s) — the caller logs one line and does not arm.
    """
    values = os.environ if env is None else env
    raw_mode = (values.get(ENV_HEARTBEAT) or "").strip().lower()
    if raw_mode in ("", "off", "0", "false", "no"):
        return HeartbeatSettings(enabled=False)
    if raw_mode not in ("on", "1", "true", "yes"):
        return HeartbeatSettings(
            enabled=True,
            error=f"{ENV_HEARTBEAT}={raw_mode!r} is invalid; expected 'on' or 'off'",
        )

    interval_s, error = _positive_seconds(
        values, ENV_HEARTBEAT_INTERVAL, DEFAULT_INTERVAL_S
    )
    if error is not None:
        return HeartbeatSettings(enabled=True, error=error)
    window_s, error = _positive_seconds(values, ENV_HEARTBEAT_WINDOW, DEFAULT_WINDOW_S)
    if error is not None:
        return HeartbeatSettings(enabled=True, error=error)

    if window_s < 3 * interval_s:
        return HeartbeatSettings(
            enabled=True,
            error=(
                f"{ENV_HEARTBEAT_WINDOW}={_format_seconds(window_s)} must be at "
                f"least 3 * {ENV_HEARTBEAT_INTERVAL}={_format_seconds(interval_s)}"
            ),
        )

    server = (values.get(ENV_HEARTBEAT_SERVER) or "").strip().rstrip("/")
    topic = (values.get(ENV_HEARTBEAT_TOPIC) or "").strip()
    missing = [
        name
        for name, value in ((ENV_HEARTBEAT_SERVER, server), (ENV_HEARTBEAT_TOPIC, topic))
        if not value
    ]
    if missing:
        return HeartbeatSettings(
            enabled=True,
            error=(
                f"{' and '.join(missing)} required when {ENV_HEARTBEAT}=on"
            ),
        )

    parsed = urllib.parse.urlsplit(server)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return HeartbeatSettings(
            enabled=True,
            error=f"{ENV_HEARTBEAT_SERVER}={server!r} must be an http(s) URL",
        )
    if not _TOPIC_RE.match(topic):
        return HeartbeatSettings(
            enabled=True,
            error=(
                f"{ENV_HEARTBEAT_TOPIC}={topic!r} must be 1-64 characters from "
                "-_A-Za-z0-9"
            ),
        )

    priority, priority_level, error = _parse_priority(
        values.get(ENV_HEARTBEAT_PRIORITY)
    )
    if error is not None:
        return HeartbeatSettings(enabled=True, error=error)

    host = socket.gethostname() if hostname is None else hostname
    return HeartbeatSettings(
        enabled=True,
        config=HeartbeatConfig(
            server=server,
            topic=topic,
            interval_s=interval_s,
            window_s=window_s,
            priority=priority,
            priority_level=priority_level,
            seq_id=heartbeat_seq_id(host),
            alert_body=_alert_body(host, window_s),
        ),
    )


# --- HTTP publish (stdlib only, RNF-1) ---------------------------------------


class PublishError(Exception):
    """Base class for one failed heartbeat publish."""


class PublishRejected(PublishError):
    """The server answered with a non-2xx status.

    ``transient`` separates rate limiting / server-side errors (retry on the
    next interval) from a rejection of the sequence-scheduled form itself
    (the capability gate refuses to arm).
    """

    def __init__(self, status: int, detail: str = ""):
        self.status = status
        self.transient = status in (408, 429) or 500 <= status < 600
        super().__init__(f"HTTP {status}" + (f": {detail}" if detail else ""))


class PublishUnavailable(PublishError):
    """Network-level failure (DNS, connection, timeout): retry later."""


def publish_heartbeat(
    config: HeartbeatConfig, timeout: float = HTTP_TIMEOUT_SEC
) -> None:
    """Publishes one scheduled heartbeat with an ``In: <window>`` delay.

    A same-sequence publish replaces the pending scheduled message on the
    server (ntfy >= v2.18): that replacement IS the dead man's switch. Raises
    ``PublishRejected``/``PublishUnavailable`` so the loop can tell a
    capability refusal from a transient network failure.
    """
    url = f"{config.server}/{config.topic}/{config.seq_id}"
    request = urllib.request.Request(
        url, data=config.alert_body.encode("utf-8"), method="POST"
    )
    request.add_header("In", format_window_header(config.window_s))
    request.add_header("Priority", str(config.priority_level))
    request.add_header("Content-Type", "text/plain; charset=utf-8")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
    except urllib.error.HTTPError as exc:
        raise PublishRejected(int(exc.code), str(exc.reason)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PublishUnavailable(str(exc)) from exc
    if status is not None and not 200 <= int(status) < 300:
        raise PublishRejected(int(status))


# --- the loop (RF-1, RF-3, RF-5) --------------------------------------------


def _log_stderr(message: str) -> None:
    print(message, file=sys.stderr)


class Heartbeat:
    """Dedicated heartbeat thread; one owner per daemon process.

    Lifecycle: ``start()`` spawns a daemon thread that publishes the first
    beat immediately (that beat is also the capability gate), then one beat
    per interval until ``stop()``. ``stop()`` sets the thread's own Event and
    joins; the loop never holds a daemon lock and never raises into the
    caller (RF-5), so a failing ntfy server can only cost log lines.
    """

    def __init__(
        self,
        config: HeartbeatConfig,
        publish: Optional[Callable[[HeartbeatConfig], None]] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self._publish = publish if publish is not None else publish_heartbeat
        self._clock = clock
        self._log = log if log is not None else _log_stderr
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = "configured"
        self._next_beat_at: Optional[float] = None
        self._failed = False

    @property
    def thread(self) -> Optional[threading.Thread]:
        return self._thread

    @property
    def state(self) -> str:
        """``configured``/``probing``/``on``/``unsupported``/``stopped``."""
        return self._state

    @property
    def next_beat_in_sec(self) -> Optional[int]:
        """Whole seconds until the next publish (None when not beating)."""
        if self._state != "on" or self._next_beat_at is None:
            return None
        return max(0, int(round(self._next_beat_at - self._clock())))

    def status_fields(self) -> str:
        """Whitespace-free kv fields for ``agent-tts status`` (RF-6)."""
        interval = _format_seconds(self.config.interval_s)
        window = _format_seconds(self.config.window_s)
        if self._state == "on":
            return (
                f"heartbeat=on interval={interval} window={window} "
                f"next_beat={self.next_beat_in_sec}s"
            )
        if self._state == "probing":
            return f"heartbeat=probing interval={interval} window={window}"
        return f"heartbeat={self._state}"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=THREAD_NAME, daemon=True
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> bool:
        """Signals the loop and joins it; True when the thread is gone."""
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(JOIN_TIMEOUT_SEC if timeout is None else timeout)
        return not thread.is_alive()

    # -- thread body ---------------------------------------------------------

    def _run(self) -> None:
        try:
            self._gate()
            if self._state == "unsupported":
                return
            while not self._stop.is_set():
                self._next_beat_at = self._clock() + self.config.interval_s
                if self._stop.wait(self.config.interval_s):
                    break
                self._beat()
        finally:
            # "unsupported" is the truthful terminal state (the server
            # refused and `status` must keep saying so); everything else
            # ended because stop() asked it to.
            if self._state != "unsupported":
                self._state = "stopped"

    def _gate(self) -> None:
        """First publish = capability probe (RF-3).

        A concrete non-2xx refusal means the server does not accept the
        sequence-scheduled form: arming would schedule a fresh message every
        interval (spam), so the loop logs once and exits. A network failure
        proves nothing about the server, so the loop stays alive and retries
        on the next interval.
        """
        self._state = "probing"
        try:
            self._publish(self.config)
        except PublishRejected as exc:
            if exc.transient:
                self._note_failure(exc)
                return
            self._state = "unsupported"
            self._log(
                "agent-tts: heartbeat not armed: ntfy server rejected the "
                f"scheduled sequence publish ({exc}); updating scheduled "
                "notifications needs ntfy v2.18 or newer"
            )
        except Exception as exc:  # noqa: BLE001 - never raise into the daemon
            self._note_failure(exc)
        else:
            self._mark_success()

    def _beat(self) -> None:
        try:
            self._publish(self.config)
        except Exception as exc:  # noqa: BLE001 - one failed beat never kills the loop
            self._note_failure(exc)
        else:
            self._mark_success()

    def _note_failure(self, exc: BaseException) -> None:
        if not self._failed:
            self._failed = True
            self._log(
                "agent-tts: heartbeat publish failed "
                f"({exc}); retrying in {_format_seconds(self.config.interval_s)}"
            )

    def _mark_success(self) -> None:
        if self._failed:
            self._log("agent-tts: heartbeat publish recovered")
        self._failed = False
        # Schedule before publishing the state so a concurrent `status`
        # read never sees "on" with an unknown next beat.
        self._next_beat_at = self._clock() + self.config.interval_s
        self._state = "on"
