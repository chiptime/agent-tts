"""Background watcher announcing agent state transitions over SSE.

Design (deliberate, fase 2b): transition-triggered announcements built from
templates plus the agent's digest (no LLM call: instant and free). When an
agent's status transitions INTO "done" or "blocked", the event is spoken AND
shipped as text in the same SSE payload — the announcement audio is rendered
from the exact same digest string, so audio and visual always carry identical
content (product decision 2026-09-25). Transitions into "idle" are
intentionally skipped in v1 to keep the channel quiet.

Behavioral guarantees:
- Baseline snapshot on first poll: pre-existing states are never announced
  (booting with 8 running agents must stay silent).
- Debounce: the same pane+status is not re-announced within the debounce
  window (60 s by default).
- The hub is a bounded fan-out: events with zero subscribers are dropped;
  a full subscriber queue drops its oldest event. The watcher never blocks
  on consumers and survives with zero clients.
- TTS is best-effort: a rendering failure still publishes the text event
  with ``audio_url: null``.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .config import Settings
from .herdr import AgentInfo, HerdrClient, HerdrError
from .transcripts import read_turns
from .tts import new_audio_path
from .view import SCREEN_TAIL_LINES, detect_pending

POLL_INTERVAL_S = 4.0
DEBOUNCE_S = 60.0
ANNOUNCE_STATUSES = ("done", "blocked")
ANNOUNCEMENT_PREFIX = "ann-"
ANNOUNCEMENT_MAX_AGE_S = 3600
FALLBACK_DETAIL = "sin detalle disponible"
LABEL_MAX_CHARS = 48

Queue = queue.Queue


class AnnouncementHub:
    """Thread-safe fan-out of announcements to SSE subscribers."""

    def __init__(self, max_queue: int = 8):
        self._max_queue = max_queue
        self._lock = threading.Lock()
        self._subscribers: Dict[int, Queue] = {}
        self._next_id = 0

    def subscribe(self) -> Tuple[int, Queue]:
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            sub: Queue = Queue(maxsize=self._max_queue)
            self._subscribers[sub_id] = sub
            return sub_id, sub

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def publish(self, announcement: dict) -> None:
        """Delivers to every subscriber, dropping their oldest on overflow."""
        with self._lock:
            subscribers = list(self._subscribers.values())
        for sub in subscribers:
            try:
                sub.put_nowait(announcement)
            except queue.Full:
                try:
                    sub.get_nowait()
                    sub.put_nowait(announcement)
                except (queue.Empty, queue.Full):
                    pass


def clip_detail(text: Optional[str], cap: int) -> Optional[str]:
    """Longest speakable prefix of ``text`` within ``cap`` chars.

    Prefers cutting at the last sentence end that fits inside the cap;
    hard-cuts (with an ellipsis) only when a single sentence exceeds it.
    """
    if not text:
        return None
    flat = " ".join(text.split())
    if not flat:
        return None
    if len(flat) <= cap:
        return flat
    head = flat[:cap]
    cut = head.rfind(". ")
    if cut == -1 and head.endswith("."):
        cut = len(head) - 1
    if cut != -1:
        return head[: cut + 1]
    return head[: cap - 3].rstrip() + "..."


def agent_label(agent: AgentInfo) -> str:
    """Speakable label: agent kind plus short title or cwd basename."""
    base = Path(agent.cwd).name if agent.cwd else ""
    tail = base or (agent.title or "")
    label = f"{agent.agent} {tail}".strip()
    if len(label) > LABEL_MAX_CHARS:
        label = label[: LABEL_MAX_CHARS - 3].rstrip() + "..."
    return label or agent.agent


class AgentWatcher:
    """Polls the agent list and announces transitions into done/blocked."""

    def __init__(
        self,
        settings: Settings,
        herdr: Optional[HerdrClient] = None,
        tts_renderer: Optional[Callable] = None,
        hub: Optional[AnnouncementHub] = None,
        clock: Callable[[], float] = time.monotonic,
        poll_interval_s: float = POLL_INTERVAL_S,
        debounce_s: float = DEBOUNCE_S,
    ):
        self._settings = settings
        self._herdr = herdr or HerdrClient(settings)
        self._tts = tts_renderer
        self.hub = hub or AnnouncementHub()
        self._clock = clock
        self._poll_interval = poll_interval_s
        self._debounce = debounce_s
        self._baseline: Optional[Dict[str, str]] = None
        self._last_announced: Dict[Tuple[str, str], float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="herdr-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — the watcher must never die
                continue

    # -- core logic ------------------------------------------------------------

    def poll_once(self) -> List[dict]:
        """One diff cycle; returns the announcements produced (empty at baseline)."""
        try:
            agents = self._herdr.list_agents()
        except Exception:  # noqa: BLE001 — a broken CLI must stay silent
            return []
        current = {agent.pane_id: agent for agent in agents}

        if self._baseline is None:
            self._baseline = {pid: agent.status for pid, agent in current.items()}
            return []

        produced: List[dict] = []
        for pane_id, agent in current.items():
            if agent.status not in ANNOUNCE_STATUSES:
                continue
            if self._baseline.get(pane_id) == agent.status:
                continue
            key = (pane_id, agent.status)
            now_ts = self._clock()
            if now_ts - self._last_announced.get(key, float("-inf")) < self._debounce:
                continue
            self._last_announced[key] = now_ts
            announcement = self._build_announcement(agent)
            self.hub.publish(announcement)
            produced.append(announcement)

        self._baseline = {pid: agent.status for pid, agent in current.items()}
        self._cleanup_old_audio()
        return produced

    # -- digest building ---------------------------------------------------

    def _build_announcement(self, agent: AgentInfo) -> dict:
        label = agent_label(agent)
        # Audio/visual parity (product decision 2026-09-25): the SAME digest
        # string is the SSE text payload and the spoken announcement, so
        # what you hear is exactly what you read. The replay button (🔊
        # Escuchar) re-synthesizes this same string through POST /tts.
        if agent.status == "blocked":
            text = f"{label} necesita tu atención: {self._blocked_detail(agent)}"
        else:
            text = f"{label} terminó: {self._done_detail(agent)}"

        audio_url: Optional[str] = None
        try:
            out_path = new_audio_path(self._settings, prefix=ANNOUNCEMENT_PREFIX)
            self._tts(self._settings, text, out_path)
            audio_url = f"/audio/{out_path.name}"
        except Exception:  # noqa: BLE001 — text must never block on TTS
            audio_url = None

        return {
            "type": "transition",
            "pane_id": agent.pane_id,
            "agent": agent.agent,
            "status": agent.status,
            "label": label,
            "text": text,
            "audio_url": audio_url,
            # VS1.4 (additive): brain-minted speech_request_id — the public
            # handle for this announcement's speech. Deliberately NOT wired
            # to the speech registry here: announcements stay best-effort
            # and fire-and-forget in v1; the cancel-vs-announce interplay
            # lands in VS1.8.
            "speech_request_id": f"ann-{uuid.uuid4()}",
        }

    def _done_detail(self, agent: AgentInfo) -> str:
        cap = self._settings.announce_max_chars
        if agent.session_value:
            try:
                turns = read_turns(agent.agent, agent.session_value, 1)
            except Exception:  # noqa: BLE001
                turns = None
            if turns:
                sentence = clip_detail(turns[-1].text, cap)
                if sentence:
                    return sentence
        screen = self._screen(agent)
        first_line = screen.splitlines()[0].strip() if screen else None
        return clip_detail(first_line, cap) or FALLBACK_DETAIL

    def _blocked_detail(self, agent: AgentInfo) -> str:
        cap = self._settings.announce_max_chars
        screen = self._screen(agent)
        if screen:
            pending = detect_pending(screen, agent.status)
            if pending.detected and pending.excerpt:
                return clip_detail(pending.excerpt, cap)
            first_line = next(
                (ln.strip() for ln in screen.splitlines() if ln.strip()), None
            )
            if first_line:
                return clip_detail(first_line, cap)
        return FALLBACK_DETAIL

    def _screen(self, agent: AgentInfo) -> Optional[str]:
        try:
            return self._herdr.read_screen(agent.pane_id, SCREEN_TAIL_LINES)
        except Exception:  # noqa: BLE001
            return None

    # -- housekeeping ---------------------------------------------------------

    def _cleanup_old_audio(self) -> None:
        """Deletes announcement mp3s older than ~1h (chat audio untouched)."""
        try:
            audio_dir = self._settings.audio_dir
            if not audio_dir.is_dir():
                return
            now_ts = time.time()
            for path in audio_dir.glob(f"{ANNOUNCEMENT_PREFIX}*.mp3"):
                try:
                    if now_ts - path.stat().st_mtime > ANNOUNCEMENT_MAX_AGE_S:
                        path.unlink()
                except OSError:
                    continue
        except Exception:  # noqa: BLE001 — housekeeping must never kill the loop
            return
