"""Liveness probe for the herdr-tts DAEMON + fail-noisy spoken warnings.

herdr-tts is the brain's PRIMARY service and both announcement channels
stay always-on BY DESIGN (fail-noisy for approval flows): PC speakers via
the herdr-tts daemon, phone via the brain's SSE. If the daemon dies, the
PC channel is dead — the brain must WARN on the surviving channel.

Chosen liveness signal (documented decision): the daemon PIDFILE plus a
process-alive and cmdline-mention check, exactly the probe herdr-tts
itself ships as ``daemon_pid()`` (bin/herdr-tts, "Daemon lifecycle"):
the daemon writes ``$$`` to the pidfile at startup (daemon_takeover) and
its EXIT trap removes it — pidfile lifecycle is daemon-lifecycle-owned.
A stale pidfile (SIGKILLed daemon) is caught by the /proc checks; the
cmdline mention of "herdr-tts" guards against PID reuse (same guard
daemon_takeover uses).

REJECTED: the playback IPC socket (/tmp/herdr-tts-player.sock). It is
NOT daemon-lifecycle-owned — herdr-tts's engine creates the IPCServer per
playback AudioSession (started on playback, stopped at session end), so
the socket exists only while audio is playing. Verified live on this
machine: daemon up for hours, no socket file. A socket handshake would
report "down" between utterances.

The probe is pure file I/O (pidfile + /proc read): nothing can block, so
the <=1.5s budget is trivially met and it is safe from any background
thread.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from .config import Settings
from .tts import new_audio_path
from .watcher import ANNOUNCEMENT_PREFIX, AnnouncementHub

DAEMON_UP = "up"
DAEMON_DOWN = "down"

DEFAULT_DAEMON_PID_FILE = "/tmp/herdr-tts-daemon.pid"

DAEMON_POLL_INTERVAL_S = 10.0
DAEMON_REANNOUNCE_S = 30 * 60.0  # while down: re-warn at most every 30 min

DAEMON_DOWN_TEXT = (
    "Atención: el backend de voz herdr-tts está caído — "
    "no habrá avisos por los altavoces del PC."
)
DAEMON_UP_TEXT = "El backend de voz herdr-tts ha vuelto."


def _proc_args(pid: int) -> Optional[List[str]]:
    """NUL-separated argv of a pid, or None when unreadable (dead/reused)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode("utf-8", errors="ignore") for part in raw.split(b"\0") if part]


def _evaluate(pid: int, args: Optional[List[str]]) -> str:
    """Pure decision: alive + cmdline mentions herdr-tts → up (repo parity:
    daemon_pid()/daemon_takeover use the same mention guard against PID
    reuse; the pidfile anchors WHICH pid we trust)."""
    if args is None:
        return DAEMON_DOWN
    if any("herdr-tts" in arg for arg in args):
        return DAEMON_UP
    return DAEMON_DOWN


def daemon_status(pid_file: Optional[str] = None) -> str:
    """Fail-soft daemon liveness: 'up' | 'down'. Never raises, never blocks."""
    path = Path(pid_file or os.environ.get("HERDR_TTS_DAEMON_PID_FILE", DEFAULT_DAEMON_PID_FILE))
    try:
        raw = path.read_text().strip()
    except OSError:
        return DAEMON_DOWN
    if not raw.isdigit():
        return DAEMON_DOWN
    pid = int(raw)
    return _evaluate(pid, _proc_args(pid))


Probe = Callable[[], str]


class DaemonWatcher:
    """Polls daemon liveness and announces channel-loss on the hub.

    Another event source into the same announcement hub the agent watcher
    uses; audio is rendered through the brain's own TTS (venv direct),
    which does NOT depend on the daemon. Transition-driven (fail-noisy,
    not spammy):

    - up → down: warn at once.
    - down → up: announce the recovery.
    - while down: re-warn at most every ``reannounce_s`` (30 min).
    - boot while down: exactly one warning on the first poll (after
      warmup — same baseline-first-poll discipline as the agent watcher).
    """

    def __init__(
        self,
        settings: Settings,
        hub: AnnouncementHub,
        tts_renderer: Optional[Callable] = None,
        probe: Optional[Probe] = None,
        clock: Callable[[], float] = time.monotonic,
        poll_interval_s: float = DAEMON_POLL_INTERVAL_S,
        reannounce_s: float = DAEMON_REANNOUNCE_S,
    ):
        self._settings = settings
        self._hub = hub
        self._tts = tts_renderer
        self._probe = probe or daemon_status
        self._clock = clock
        self._poll_interval = poll_interval_s
        self._reannounce = reannounce_s
        self._state: Optional[str] = None  # baseline not yet established
        self._last_warned: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="herdr-tts-daemon-watch", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — the watcher must never die
                continue

    # -- core logic ----------------------------------------------------------

    def poll_once(self) -> List[dict]:
        """One probe cycle; publishes and returns the announcements."""
        status = self._probe()
        previous, self._state = self._state, status
        now_ts = self._clock()

        def emit(text: str, kind: str) -> dict:
            announcement = self._announce(text, kind, now_ts)
            self._hub.publish(announcement)
            return announcement

        if previous is None:
            # Baseline poll: booting with the daemon already down warns
            # exactly once; booting healthy stays silent.
            if status == DAEMON_DOWN:
                return [emit(DAEMON_DOWN_TEXT, "warning")]
            return []

        if previous == DAEMON_UP and status == DAEMON_DOWN:
            return [emit(DAEMON_DOWN_TEXT, "warning")]
        if previous == DAEMON_DOWN and status == DAEMON_UP:
            return [emit(DAEMON_UP_TEXT, "info")]
        if status == DAEMON_DOWN:
            due = (
                self._last_warned is None
                or now_ts - self._last_warned >= self._reannounce
            )
            if due:
                return [emit(DAEMON_DOWN_TEXT, "warning")]
        return []

    # -- announcement building -------------------------------------------------

    def _announce(self, text: str, kind: str, now_ts: float) -> dict:
        self._last_warned = now_ts if kind == "warning" else None
        audio_url: Optional[str] = None
        if self._tts is not None:
            try:
                out_path = new_audio_path(self._settings, prefix=ANNOUNCEMENT_PREFIX)
                self._tts(self._settings, text, out_path)
                audio_url = f"/audio/{out_path.name}"
            except Exception:  # noqa: BLE001 — text must never block on TTS
                audio_url = None
        return {
            "type": "system",
            "kind": kind,
            "pane_id": None,
            "agent": None,
            "status": None,
            "label": "herdr-tts",
            "text": text,
            "audio_url": audio_url,
        }
