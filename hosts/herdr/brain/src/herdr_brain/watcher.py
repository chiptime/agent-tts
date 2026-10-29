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
- Adaptive cadence (doc 05): one loop, one decision site. The baseline
  poll runs every ``POLL_INTERVAL_S``; while an SSE subscriber was seen
  inside ``ATTENTION_WINDOW_S`` AND a pane is blocked, the next wait
  shortens to ``FAST_POLL_INTERVAL_S``. No per-client timer exists —
  attention is deduced from the hub, and it decays by itself.
- Notifier hook (S5/S13): an OPTIONAL callback object riding the same
  poll — no second timer. It hears every announced transition (with the
  blocking question the digest already extracted), every pane that
  leaves blocked/done, and one ``tick`` per cycle with each live pane's
  last-activity estimate. The watcher owns the resilience rule: a
  notifier that raises never breaks polling.
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
from .mux import create_mux
from .secrets_mask import mask_text
from .transcripts import read_turns
from .tts import new_audio_path
from .view import SCREEN_TAIL_LINES, detect_pending

POLL_INTERVAL_S = 4.0
#: Cadence floor while somebody is watching a blocked pane (doc 05): the
#: changes worth catching within seconds happen when a pane waits at a
#: dialog, so only that state earns the fast poll.
FAST_POLL_INTERVAL_S = 1.5
#: How long a watch signal keeps the fast cadence armed. An SSE
#: subscriber present at poll time refreshes it; no subscriber lets it
#: decay back to the baseline cadence within one window.
ATTENTION_WINDOW_S = 10.0
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

    def subscriber_count(self) -> int:
        """Live subscribers right now — the "somebody is watching" signal."""
        with self._lock:
            return len(self._subscribers)

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
    """Speakable label: agent kind plus short title or cwd basename.

    Masked BEFORE the length cut (S14): a secret inside the cwd or
    title must not survive the truncation as a partial prefix.
    """
    base = Path(agent.cwd).name if agent.cwd else ""
    tail = base or (agent.title or "")
    label = mask_text(f"{agent.agent} {tail}".strip())
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
        notifier=None,
    ):
        self._settings = settings
        self._herdr = herdr or create_mux(settings)
        self._tts = tts_renderer
        self.hub = hub or AnnouncementHub()
        self._clock = clock
        self._poll_interval = poll_interval_s
        self._debounce = debounce_s
        self._baseline: Optional[Dict[str, str]] = None
        self._last_announced: Dict[Tuple[str, str], float] = {}
        # Notifier hook (S5/S13): structural duck type — transition /
        # handled / tick. Optional on purpose: the watcher's own tests
        # (and any embedding) construct it bare.
        self._notifier = notifier
        # Last-activity estimate per pane (the watcher's clock domain):
        # refreshed while a pane works or changes state, frozen while it
        # idles. This is the S5 cache-warning feed — an honest proxy,
        # since the multiplexer exposes no activity timestamps.
        self._last_activity: Dict[str, float] = {}
        # Attention (doc 05 cadence): timestamp of the last poll that saw
        # a live SSE subscriber. ``-inf`` means nobody has watched since
        # the watcher was created.
        self._last_attention: float = float("-inf")
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
        # Adaptive (doc 05): the wait for the NEXT cycle is re-asked every
        # loop, so the cadence re-arms in this one place and a poll that
        # failed costs nothing — the next tick simply re-asks again.
        while not self._stop.wait(self.next_interval_s()):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — the watcher must never die
                continue

    # -- core logic ------------------------------------------------------------

    def next_interval_s(self) -> float:
        """Interval to wait before the NEXT cycle.

        Fast only while BOTH hold: attention is fresh (an SSE subscriber
        was seen inside the attention window) and a pane is blocked. Any
        other state — nobody watching, or nothing to watch closely —
        rides the baseline cadence.
        """
        fresh = self._clock() - self._last_attention <= ATTENTION_WINDOW_S
        blocked = any(
            status == "blocked" for status in (self._baseline or {}).values()
        )
        if fresh and blocked:
            return FAST_POLL_INTERVAL_S
        return self._poll_interval

    def poll_once(self) -> List[dict]:
        """One diff cycle; returns the announcements produced (empty at baseline)."""
        try:
            agents = self._herdr.list_agents()
        except Exception:  # noqa: BLE001 — a broken CLI must stay silent
            return []
        # A live SSE subscriber is somebody watching the herd right now:
        # refresh the attention window this cycle (doc 05 cadence).
        if self.hub.subscriber_count() > 0:
            self._last_attention = self._clock()
        now_ts = self._clock()
        current = {agent.pane_id: agent for agent in agents}

        # Activity estimate (S5): a working pane is active by definition,
        # and ANY state change is activity — both refresh the stamp an
        # idle pane leaves frozen.
        for pane_id, agent in current.items():
            if (
                agent.status == "working"
                or self._baseline is None
                or self._baseline.get(pane_id) != agent.status
            ):
                self._last_activity[pane_id] = now_ts
        for gone in self._last_activity.keys() - current.keys():
            self._last_activity.pop(gone, None)

        if self._baseline is None:
            self._baseline = {pid: agent.status for pid, agent in current.items()}
            self._notify_tick(current)
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
            label, text, question = self._digest(agent)
            announcement = self._build_announcement(agent, label, text)
            self.hub.publish(announcement)
            produced.append(announcement)
            # Same decision site as the SSE publish above: a debounced
            # repeat stays silent on BOTH channels. No gate id here —
            # approval gates belong to the /ask flow, not the watcher.
            self._notify(
                "transition",
                pane_id,
                label,
                agent.status,
                text=text,
                question=question,
            )

        # State-driven handled (S5 retraction): a pane that no longer
        # needs the operator closes its push slot — also when its
        # blocked transition was debounced, and also when the pane
        # vanished entirely.
        for pane_id, status in self._baseline.items():
            if status not in ANNOUNCE_STATUSES:
                continue
            if current.get(pane_id, None) is None or (
                current[pane_id].status != status
            ):
                self._notify("handled", pane_id)

        self._baseline = {pid: agent.status for pid, agent in current.items()}
        self._cleanup_old_audio()
        self._notify_tick(current)
        return produced

    # -- notifier fan-out ---------------------------------------------------

    def _notify(self, method: str, *args, **kwargs) -> None:
        """One notifier call that can never break the poll cycle."""
        if self._notifier is None:
            return
        try:
            getattr(self._notifier, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001 — resilience rule of this hook
            return

    def _notify_tick(self, current: Dict[str, AgentInfo]) -> None:
        """One per-poll beat: the live panes with their activity stamps."""
        if self._notifier is None:
            return
        panes = [
            (
                pane_id,
                agent_label(agent),
                self._last_activity.get(pane_id, self._clock()),
            )
            for pane_id, agent in current.items()
        ]
        self._notify("tick", panes=panes)

    # -- digest building ---------------------------------------------------

    def _digest(self, agent: AgentInfo) -> Tuple[str, str, Optional[str]]:
        """The announcement's three facts: speakable label, the spoken
        digest text, and the raw blocking question (blocked only — the
        pending excerpt or first screen line, WITHOUT the label prefix;
        push notifications carry it as their body)."""
        label = agent_label(agent)
        if agent.status == "blocked":
            question = self._blocked_detail(agent)
            return label, f"{label} necesita tu atención: {question}", question
        return label, f"{label} terminó: {self._done_detail(agent)}", None

    def _build_announcement(
        self,
        agent: AgentInfo,
        label: Optional[str] = None,
        text: Optional[str] = None,
    ) -> dict:
        # Audio/visual parity (product decision 2026-09-25): the SAME digest
        # string is the SSE text payload and the spoken announcement, so
        # what you hear is exactly what you read. The replay button (🔊
        # Escuchar) re-synthesizes this same string through POST /tts.
        # ``label``/``text`` arrive pre-computed from _digest so a poll
        # reads the screen ONCE per transition (the push hook reuses them).
        if label is None or text is None:
            label, text, _ = self._digest(agent)

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
                # Mask BEFORE clipping (S14): the cap could otherwise cut
                # a credential into a half the detectors no longer match.
                sentence = clip_detail(mask_text(turns[-1].text), cap)
                if sentence:
                    return sentence
        screen = self._screen(agent)
        first_line = screen.splitlines()[0].strip() if screen else None
        return clip_detail(mask_text(first_line), cap) if first_line else FALLBACK_DETAIL

    def _blocked_detail(self, agent: AgentInfo) -> str:
        cap = self._settings.announce_max_chars
        screen = self._screen(agent)
        if screen:
            pending = detect_pending(screen, agent.status)
            if pending.detected and pending.excerpt:
                return clip_detail(mask_text(pending.excerpt), cap)
            first_line = next(
                (ln.strip() for ln in screen.splitlines() if ln.strip()), None
            )
            if first_line:
                return clip_detail(mask_text(first_line), cap)
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
