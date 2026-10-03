"""Priority playback queue manager (AT-08, BLOQUE 1.3 hito Cola).

The QueueManager lives INSIDE the daemon process, above the playback
session (the mount point BLOQUE 1.2 reserved in ``Daemon.active_session``):
it serializes playback into a single active slot (RF-AT-08-6), dispatches
by priority, applies the per-event policy, and supervises liveness so a
wedged session can never block the queue forever (RS-5).

Scheduling model
----------------
Priorities are event-derived and totally ordered: ``blocked > done >
working``. On every finalize of the active item (completed, stopped,
failed, watchdog-terminated — or interrupted by preemption) the next
pending item is dispatched event-driven: the finalize callback dispatches
inline on the finishing thread, so the added latency is zero by design
(RNF-AT-08-1's < 50 ms budget is consumed elsewhere, never here). Within
one priority level, dispatch is FIFO by arrival (fairness: same-priority
events are served in the order they were enqueued).

Policies (RF-AT-08-2, D4)
-------------------------
- ``queue``: waits its turn. A plain play NEVER rejects with busy
  (D4): everything enters the queue, priority decides the speaker.
- ``preempt``: only effective when the incoming priority is STRICTLY
  higher than the active item's; equal or lower degrades to ``queue``.
  A preempt cancels the active item: it is terminated and recorded as
  INTERRUPTED, its announcement is lost (that IS preempt semantics —
  it is not re-queued), and dispatch then follows normal priority
  order (an even higher pending item may reach the speaker first).
  A preempt that arrives while the active dispatch has not yet
  produced its session handle cannot cancel anything (nothing to
  terminate) and degrades to ``queue`` — RF-AT-08-6 outweighs it.
- ``coalesce``: coalescible items with the same event type merge into
  ONE pending announcement while the window is open. See
  ``QueueManager`` docstring for the exact window rule.

Liveness watchdog (RS-5)
------------------------
Every dispatch registers a :class:`SessionHandle` exposing a progress
token. If the token does not change for ``wedged_timeout_sec`` of
contiguous, non-paused time, the session is declared wedged: it is
terminated, its item is marked FAILED (visible in the snapshot with
``wedged=True``), and the next item dispatches. Paused sessions suspend
the watchdog — observing a paused session refreshes the liveness
baseline, so a resumed session always gets a full fresh budget. A
handle whose ``progress_token`` raises counts as silent progress
(fail-closed: liveness wins). Note for adapters: a silent synthesis
phase produces no tokens, so ``wedged_timeout_sec`` must exceed the
worst-case silent interval your playback path can incur.

Termination wait (RF-AT-08-6, R1-01)
-------------------------------------
``terminate()`` only ASKS a session to stop: flags are set, but the
audio device closes asynchronously on the playback thread. After a
preempt or a watchdog kill, the manager therefore waits (BOUNDED, via
the handle's ``wait_stopped(timeout)``) for the terminated session to
actually end before dispatching the next item — the no-overlap
invariant now covers the preempted interval too. The whole
cut-wait-dispatch section runs under the dispatch mutex so a racing
dispatcher cannot start the next item between the clear and the
session's real end. The wait is bounded by ``termination_wait_sec``:
on expiry the manager traces one residual line and proceeds anyway —
an unstoppable session must never wedge the queue (RS-5 wins over
RF-AT-08-6 there). Handles without ``wait_stopped`` (legacy seams)
skip the wait entirely.

All time comes from an injectable monotonic clock; the manager itself
never sleeps. Timeouts are constructor arguments, never module state.

Locking model
--------------
One non-reentrant ``threading.Lock`` guards all mutable state (pending
heap, active entry, counters, failure history, closing flag). The lock
is NEVER held while calling into user code (``runner``, ``terminate``,
``progress_token``, ``is_paused``, ``wait_stopped``). The runner call is
additionally serialized by a dispatch RLock — the preempt and watchdog
cut sections take the same RLock around terminate/wait/dispatch, so no
dispatcher can slip between the active item's clear and its actual end
(and while a handle is active the mutex is free: runner launches only
happen with the slot empty, and they must return promptly by contract).
A thread-local guard flattens re-entrant dispatch chains (a runner whose
playback completes inside its own dispatch call cannot grow the stack).
Finalization is id-guarded by item id: when the watchdog, a preempt, and
the playback thread's natural finish race, exactly the first one wins
and the late reports are ignored.
"""

from __future__ import annotations

import heapq
import itertools
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Callable, Deque, List, Optional, Sequence, Tuple
from typing import Protocol, runtime_checkable

__all__ = [
    "DEFAULT_COALESCE_WINDOW_SEC",
    "DEFAULT_WEDGED_TIMEOUT_SEC",
    "DEFAULT_SUPERVISOR_INTERVAL_SEC",
    "DEFAULT_TERMINATION_WAIT_SEC",
    "DEFAULT_FAILED_HISTORY_LIMIT",
    "ANNOUNCE_MAX_IDENTIFIERS",
    "ActiveItemView",
    "AudioSessionHandle",
    "FailedItemView",
    "FinalizeHook",
    "FinishCallback",
    "PendingItemView",
    "PlaybackOutcome",
    "Policy",
    "Priority",
    "QueueItem",
    "QueueManager",
    "QueueSnapshot",
    "Runner",
    "SessionHandle",
]

DEFAULT_COALESCE_WINDOW_SEC = 5.0
DEFAULT_WEDGED_TIMEOUT_SEC = 30.0
DEFAULT_SUPERVISOR_INTERVAL_SEC = 1.0
# Bounded wait for a terminated session's playback thread to actually
# end before the next dispatch (R1-01). The common healthy unwind is a
# few frame polls (tens of ms); 0.5 s covers slow device teardown while
# keeping a preempting enqueue's reply well under the 1 s IPC client
# timeout even in the worst (expiry) case.
DEFAULT_TERMINATION_WAIT_SEC = 0.5
DEFAULT_FAILED_HISTORY_LIMIT = 20
ANNOUNCE_MAX_IDENTIFIERS = 3


class Priority(IntEnum):
    """Event-derived announcement priority: blocked > done > working."""

    WORKING = 1
    DONE = 2
    BLOCKED = 3

    @classmethod
    def from_label(cls, label: str) -> "Priority":
        """Resolves a wire label ("blocked"/"done"/"working"), case-insensitive."""
        normalized = str(label).strip().lower()
        for member in cls:
            if member.name.lower() == normalized:
                return member
        raise ValueError(f"unknown priority label: {label!r}")


class Policy(str, Enum):
    """Per-event dispatch policy (RF-AT-08-2). Values are the wire strings."""

    QUEUE = "queue"
    PREEMPT = "preempt"
    COALESCE = "coalesce"


class PlaybackOutcome(str, Enum):
    """How a dispatched playback ended, as reported by the runner."""

    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


FinishCallback = Callable[[PlaybackOutcome, Optional[str]], None]

# Host notification for manager-initiated finalizations (R1-02): called
# with the finalized item, the coherent PlaybackOutcome for the host's
# client (FAILED + error for wedges and runner crashes, STOPPED for
# preempt/shutdown interrupts), and the error text when any. Never
# called for outcomes the runner itself reported via its FinishCallback.
FinalizeHook = Callable[["QueueItem", PlaybackOutcome, Optional[str]], None]


@runtime_checkable
class SessionHandle(Protocol):
    """Minimal liveness seam over an active playback session (RS-5).

    ``progress_token`` returns any value that changes when playback
    advances (position, total frames, status...). ``terminate`` asks the
    session to stop now; it must be safe to call from any thread and
    must not raise for a session that already ended. ``wait_stopped``
    blocks (never longer than ``timeout`` seconds) until the session's
    playback has ACTUALLY ended — device closed, worker exited — and
    returns whether it did; the manager calls it only after
    ``terminate()`` to keep the terminated session from overlapping the
    next dispatch (R1-01). Handles without the method (legacy seams)
    simply skip the wait.
    """

    def progress_token(self) -> Any: ...

    def is_paused(self) -> bool: ...

    def terminate(self) -> None: ...

    def wait_stopped(self, timeout: float) -> bool: ...


Runner = Callable[["QueueItem", FinishCallback], Optional[SessionHandle]]


class AudioSessionHandle:
    """SessionHandle over a BLOQUE 1.2 AudioSession-like object.

    The token combines every state signal the session already exposes:
    playback status, the PCM frame cursor, the buffered total (streaming
    appends grow it before the cursor moves), and the producing flag.
    No new session-side API is introduced — the seam rides on what 1.2
    already maintains.

    ``playback_thread`` is the daemon's queue-playback worker running
    ``session.play(...)``: the worker's ``finally`` is the point where
    the session is unmounted and the device closed, so joining it IS
    waiting for the playback to have truly ended (any target type —
    local, remote, powershell — plays inside that one worker).
    """

    def __init__(self, session, playback_thread=None):
        self._session = session
        self._playback_thread = playback_thread

    def progress_token(self):
        session = self._session
        state = session.state
        return (
            state.get("status"),
            session.current_frame,
            session.total_frames,
            bool(state.get("producing")),
        )

    def is_paused(self) -> bool:
        return self._session.state.get("status") == "paused"

    def terminate(self) -> None:
        # A queue-side termination (preempt or watchdog) must CUT, not
        # drain: targets that distinguish a user stop from a natural end
        # (wsl-ps plays out its current group on drain) hard-stop only
        # when the stop flag is already set — without this, preempting a
        # wsl-ps announcement waits out the whole playing group.
        self._session.state["stop"] = True
        self._session.stop()

    def wait_stopped(self, timeout: float) -> bool:
        thread = self._playback_thread
        if thread is None:
            return True  # no worker to wait out: nothing pending to unwind
        thread.join(timeout)
        return not thread.is_alive()


def build_announcement(count: int, event_type: str, identifiers: Sequence[str]) -> str:
    """Synthesizes the coalesced announcement text (RF-AT-08-3).

    Lists at most three identifiers and summarizes the rest:
    ``"5 jobs finished: a, b, c and 2 more"``. With no identifiers the
    announcement is just the count plus the event type. A single
    (non-merged) event has no synthesized text — its own payload speaks.
    """
    if count <= 1:
        return ""
    ids = [str(identifier) for identifier in identifiers]
    if not ids:
        return f"{count} {event_type}"
    shown = ", ".join(ids[:ANNOUNCE_MAX_IDENTIFIERS])
    rest = len(ids) - ANNOUNCE_MAX_IDENTIFIERS
    suffix = f" and {rest} more" if rest > 0 else ""
    return f"{count} {event_type}: {shown}{suffix}"


@dataclass
class QueueItem:
    """One queued playback request.

    ``payload`` is opaque to the manager — the runner consumes it to
    dispatch playback (for a coalesced item it is the WINDOW OWNER's
    payload; the synthesized ``announcement`` replaces the individual
    ones). Runners must treat every field as read-only.
    """

    id: int
    priority: Priority
    policy: Policy
    seq: int
    enqueued_at: float
    window_opened_at: float
    payload: Any = None
    event_type: str = ""
    identifiers: Tuple[str, ...] = ()
    coalesced: int = 1
    status: str = "pending"  # informational: pending/active/completed/failed/interrupted

    @property
    def announcement(self) -> str:
        return build_announcement(self.coalesced, self.event_type, self.identifiers)


# --- Immutable snapshot (pure data for the IPC status command, T3) -----------------


@dataclass(frozen=True)
class PendingItemView:
    id: int
    priority: Priority
    policy: Policy
    event_type: str
    identifiers: Tuple[str, ...]
    coalesced: int
    enqueued_at: float
    announcement: str


@dataclass(frozen=True)
class ActiveItemView:
    id: int
    priority: Priority
    policy: Policy
    event_type: str
    coalesced: int
    started_at: float


@dataclass(frozen=True)
class FailedItemView:
    id: int
    priority: Priority
    event_type: str
    error: str
    failed_at: float
    wedged: bool


@dataclass(frozen=True)
class QueueSnapshot:
    """Immutable point-in-time view of the queue, JSON-ready via as_dict."""

    queue_len: int
    pending: Tuple[PendingItemView, ...]  # in dispatch order
    active: Optional[ActiveItemView]
    failed: Tuple[FailedItemView, ...]  # bounded history, oldest first
    last_error: Optional[str]
    completed_count: int
    failed_count: int
    interrupted_count: int
    wedged_count: int

    def as_dict(self) -> dict:
        return {
            "queue_len": self.queue_len,
            "pending": [
                {
                    "id": view.id,
                    "priority": view.priority.name.lower(),
                    "policy": view.policy.value,
                    "event_type": view.event_type,
                    "identifiers": list(view.identifiers),
                    "coalesced": view.coalesced,
                    "enqueued_at": view.enqueued_at,
                    "announcement": view.announcement,
                }
                for view in self.pending
            ],
            "active": None
            if self.active is None
            else {
                "id": self.active.id,
                "priority": self.active.priority.name.lower(),
                "policy": self.active.policy.value,
                "event_type": self.active.event_type,
                "coalesced": self.active.coalesced,
                "started_at": self.active.started_at,
            },
            "failed": [
                {
                    "id": view.id,
                    "priority": view.priority.name.lower(),
                    "event_type": view.event_type,
                    "error": view.error,
                    "failed_at": view.failed_at,
                    "wedged": view.wedged,
                }
                for view in self.failed
            ],
            "last_error": self.last_error,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "interrupted_count": self.interrupted_count,
            "wedged_count": self.wedged_count,
        }


# --- The manager ------------------------------------------------------------------

_NO_SESSION = object()  # progress_token() sentinel for "handle cannot report"
_UNBASED = object()  # last_token before the first observation: silence counts from dispatch


@dataclass
class _ActiveEntry:
    """Manager-side bookkeeping for the item owning the speaker."""

    item: QueueItem
    started_at: float
    handle: Optional[SessionHandle] = None
    last_token: Any = _UNBASED  # sentinel: the first observation only baselines
    last_progress_at: float = 0.0


class QueueManager:
    """Single-active-slot playback scheduler with born-in supervision.

    Coalescing window rule (deterministic): the FIRST pending coalesce
    item of an event type OPENS a window anchored at its enqueue time.
    A later coalesce item with the SAME event type merges into it while
    ``now - window_opened_at < coalesce_window_sec`` (the window never
    re-anchors on a merge). Once the window closes — or the merged item
    reaches the speaker — the next coalesce item of that event type
    opens a fresh window. The window bounds MERGING only: it never
    delays dispatch (an idle manager dispatches a coalesce item
    immediately with ``coalesced=1``). A merged item keeps the window
    owner's priority and FIFO position; identifiers accumulate in
    arrival order and the announcement lists up to three plus a count.

    Runner contract: ``runner(item, on_finished)`` must START playback
    asynchronously and return promptly (a SessionHandle, or None to run
    unsupervised); it must not block until playback ends. When playback
    truly ends it must call ``on_finished(outcome, error)`` exactly
    once, from any thread. Late or duplicate reports are ignored
    (id-guarded), so the watchdog, preemption, and natural finish can
    race safely — the first finalization wins.

    Finalize hook (R1-02): ``finalize_hook(item, outcome, error)`` is
    invoked — never under the lock, never blocking scheduling — for
    every MANAGER-initiated finalization: watchdog wedges (FAILED +
    the watchdog error), preempt and shutdown interrupts (STOPPED), and
    runner crashes (FAILED + the runner error). It exists so a host
    can react to ends the playback worker will never report itself
    (a wedged worker never runs its own reporting); natural outcomes
    flow exclusively through the runner's ``on_finished`` and never
    through the hook, so the two paths cannot double-report.
    """

    def __init__(
        self,
        runner: Runner,
        *,
        clock: Callable[[], float] = time.monotonic,
        coalesce_window_sec: float = DEFAULT_COALESCE_WINDOW_SEC,
        wedged_timeout_sec: float = DEFAULT_WEDGED_TIMEOUT_SEC,
        supervisor_interval_sec: Optional[float] = DEFAULT_SUPERVISOR_INTERVAL_SEC,
        failed_history_limit: int = DEFAULT_FAILED_HISTORY_LIMIT,
        termination_wait_sec: float = DEFAULT_TERMINATION_WAIT_SEC,
        finalize_hook: Optional[FinalizeHook] = None,
    ):
        self._runner = runner
        self._clock = clock
        self._coalesce_window_sec = coalesce_window_sec
        self._wedged_timeout_sec = wedged_timeout_sec
        self._supervisor_interval_sec = supervisor_interval_sec
        self._termination_wait_sec = termination_wait_sec
        self._finalize_hook = finalize_hook
        # Guards: _lock for all state below; _dispatch_mutex serializes
        # runner calls (RF-AT-08-6 even under racing finalize paths);
        # _dispatching (thread-local) flattens re-entrant dispatch.
        self._lock = threading.Lock()
        self._dispatch_mutex = threading.RLock()
        self._dispatching = threading.local()
        self._pending: List[Tuple[int, int, QueueItem]] = []  # heap: (-prio, seq)
        self._active: Optional[_ActiveEntry] = None
        self._failed: Deque[FailedItemView] = deque(maxlen=failed_history_limit)
        self._last_error: Optional[str] = None
        self._completed_count = 0
        self._failed_count = 0
        self._interrupted_count = 0
        self._wedged_count = 0
        self._closing = False
        self._ids = itertools.count(1)
        self._seq = itertools.count(1)
        self._supervisor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        if supervisor_interval_sec is not None:
            self._start_supervisor()

    # --- Supervisor (RS-5) ---------------------------------------------------

    def _start_supervisor(self) -> None:
        self._supervisor_thread = threading.Thread(
            target=self._supervise,
            name="agent-tts-queue-watchdog",
            daemon=True,
        )
        self._supervisor_thread.start()

    def _supervise(self) -> None:
        interval = self._supervisor_interval_sec
        while not self._stop_event.wait(interval):
            try:
                self.check_watchdog()
            except Exception:  # supervision must never kill the supervisor
                pass

    def check_watchdog(self) -> bool:
        """One supervision tick; True iff a wedged session was terminated.

        Public on purpose: deterministic tests (and alternative hosts)
        drive it by hand with an injected clock instead of the default
        supervisor thread.
        """
        now = self._clock()
        with self._lock:
            entry = self._active
            if entry is None or entry.handle is None:
                return False  # nothing dispatchable to supervise
            handle = entry.handle
            try:
                paused = bool(handle.is_paused())
            except Exception:
                paused = False
            if paused:
                # Paused sessions suspend the watchdog; observing the
                # pause refreshes the baseline so a resumed session
                # always starts with a full fresh budget.
                entry.last_progress_at = now
                return False
            try:
                token = handle.progress_token()
            except Exception:
                token = _NO_SESSION  # a handle that cannot report is silent
            if token != entry.last_token:
                was_unbased = entry.last_token is _UNBASED
                entry.last_token = token
                if not was_unbased:
                    # Observed live progress: the silent clock restarts.
                    entry.last_progress_at = now
                    return False
                # First observation: baseline the token only — silence
                # is still measured from dispatch, so a session already
                # silent past the timeout dies on this very first tick
                # (no free pass from a late supervisor start).
            silent_for = now - entry.last_progress_at
            if silent_for <= self._wedged_timeout_sec:
                return False
        # Wedged verdict (RS-5). The cut, the bounded wait for the
        # terminated session to actually end, and the next dispatch run
        # as ONE serialized section: a racing dispatcher must not start
        # the next item while the wedged session may still hold the
        # device (RF-AT-08-6 covers the terminated interval too).
        with self._dispatch_mutex:
            with self._lock:
                if self._active is not entry:
                    # A natural finish or preempt won the race while we
                    # reached for the mutex: the verdict is moot.
                    return False
                error = (
                    f"watchdog: no playback progress for {silent_for:.1f}s "
                    f"(wedged timeout {self._wedged_timeout_sec:.1f}s)"
                )
                # The verdict wins; the late natural finish (if any) is
                # ignored by the id guard.
                self._finalize_active_locked(entry.item.id, failed=True, error=error, wedged=True)
            # Manager-verdict precedence (R2-01): the verdict is final at
            # this point, so the host hook runs BEFORE terminate() —
            # never merely before the bounded wait. terminate() is what
            # wakes a stop-responsive worker, and that worker's finally
            # releases its waiter with STOPPED and pops the dispatched
            # record; if the hook ran after the cut (or only after the
            # wait), that release would win and the delayed hook would
            # no-op, answering the blocked client status=stopped instead
            # of the watchdog failure. Firing first makes the worker's
            # own later release the designed idempotent no-op (first
            # release wins). The PREEMPT path keeps its hook-after-wait
            # order: its STOPPED mapping is the correct verdict there.
            self._notify_finalize(entry.item, PlaybackOutcome.FAILED, error)
            try:
                handle.terminate()
            except Exception:
                pass
            # R1-01 invariant unchanged: the device wait (join the
            # worker before dispatching next) still runs under the
            # dispatch mutex.
            self._wait_termination(entry.item.id, handle)
            self._dispatch_next()
        return True

    # --- Public API ----------------------------------------------------------

    def enqueue(
        self,
        *,
        priority: Priority,
        policy: Policy,
        payload: Any = None,
        event_type: str = "",
        identifiers: Optional[Sequence[str]] = None,
    ) -> int:
        """Adds one playback request; returns the announcing item's id.

        There is no busy rejection (D4): with the speaker taken, the
        item queues; ``policy`` decides whether it may preempt or
        coalesce. The returned id identifies the queue item that will
        actually announce the event — for a merge, that is the window
        owner (the merged-away event has no item of its own).
        """
        if not isinstance(priority, Priority):
            priority = Priority(priority)
        if not isinstance(policy, Policy):
            policy = Policy(policy)
        now = self._clock()
        if policy is Policy.PREEMPT:
            # A preempting enqueue may have to cut the active item: the
            # decision, the cut, the bounded wait for the terminated
            # session to actually end, and the next dispatch run as ONE
            # serialized section — a racing dispatcher must not start
            # the next item between the clear and the session's real
            # end (RF-AT-08-6 covers the preempted interval too).
            with self._dispatch_mutex:
                with self._lock:
                    if self._closing:
                        raise RuntimeError("queue manager is shut down")
                    item_id = self._insert_locked(priority, policy, payload, event_type, identifiers, now)
                    cancelled = self._maybe_preempt_locked(priority)
                if cancelled is not None:
                    self._cut_active(*cancelled)
                self._dispatch_next()
            return item_id
        with self._lock:
            if self._closing:
                raise RuntimeError("queue manager is shut down")
            item_id = self._insert_locked(priority, policy, payload, event_type, identifiers, now)
        self._dispatch_next()
        return item_id

    def cancel_by_identifiers(self, identifiers: Sequence[str]) -> Tuple[int, int, bool]:
        """Targeted cancel: drops/stops ONLY what intersects ``identifiers``.

        Returns ``(removed, trimmed, active_stopped)``:

        * a pending item whose identifiers intersect the request is
          removed (``removed``) — unless it is a merged coalesce item
          (``coalesced > 1``), which represents several events: only the
          matching identifiers are dropped from it (``trimmed``) and the
          other events keep their announcement;
        * the active item is stopped through the SAME cut the preempt
          path uses (terminate -> bounded wait -> STOPPED) when its
          identifiers intersect and it already owns a terminable handle;
        * an empty request, or identifiers nothing carries, changes
          nothing (idempotent). Items without identifiers never match.

        Never touches the global queue policy and never signals anything
        that does not intersect.
        """
        wanted = {str(i) for i in identifiers}
        if not wanted:
            return 0, 0, False
        with self._dispatch_mutex:
            cut: Optional[Tuple[Any, QueueItem]] = None
            removed = 0
            trimmed = 0
            with self._lock:
                if self._closing:
                    return 0, 0, False
                kept: List[Tuple[int, int, QueueItem]] = []
                for entry in self._pending:
                    item = entry[2]
                    hits = [i for i in item.identifiers if i in wanted]
                    if not hits:
                        kept.append(entry)
                    elif item.coalesced > 1 and len(hits) < len(item.identifiers):
                        item.identifiers = tuple(i for i in item.identifiers if i not in wanted)
                        item.coalesced = max(1, item.coalesced - len(hits))
                        trimmed += 1
                        kept.append(entry)
                    else:
                        removed += 1
                if removed:
                    heapq.heapify(kept)
                    self._pending[:] = kept
                active = self._active
                if (
                    active is not None
                    and active.handle is not None
                    and wanted.intersection(active.item.identifiers)
                ):
                    self._interrupt_active_locked(active)
                    cut = (active.handle, active.item)
            if cut is not None:
                self._cut_active(*cut)
            if removed or cut is not None:
                self._dispatch_next()
        return removed, trimmed, cut is not None

    def _cut_active(self, handle: Any, item: QueueItem) -> None:
        """Terminates an interrupted active session (preempt and cancel).

        Runs OUTSIDE ``_lock`` and under the dispatch mutex: terminate,
        bounded wait for the device to truly release, then the host hook
        (a cut is a normal STOPPED end, not a failure).
        """
        try:
            handle.terminate()
        except Exception:
            pass
        self._wait_termination(item.id, handle)
        self._notify_finalize(item, PlaybackOutcome.STOPPED, None)

    def snapshot(self) -> QueueSnapshot:
        """Immutable point-in-time view (thread-safe, never blocks playback)."""
        with self._lock:
            pending = tuple(
                PendingItemView(
                    id=item.id,
                    priority=item.priority,
                    policy=item.policy,
                    event_type=item.event_type,
                    identifiers=item.identifiers,
                    coalesced=item.coalesced,
                    enqueued_at=item.enqueued_at,
                    announcement=item.announcement,
                )
                for _, _, item in sorted(self._pending)
            )
            active = None
            if self._active is not None:
                entry = self._active
                active = ActiveItemView(
                    id=entry.item.id,
                    priority=entry.item.priority,
                    policy=entry.item.policy,
                    event_type=entry.item.event_type,
                    coalesced=entry.item.coalesced,
                    started_at=entry.started_at,
                )
            return QueueSnapshot(
                queue_len=len(pending),
                pending=pending,
                active=active,
                failed=tuple(self._failed),
                last_error=self._last_error,
                completed_count=self._completed_count,
                failed_count=self._failed_count,
                interrupted_count=self._interrupted_count,
                wedged_count=self._wedged_count,
            )

    def shutdown(self) -> None:
        """Stops supervision and interrupts the active item; idempotent.

        Teardown interrupts (the item did not fail, the daemon is going
        away) and performs no further dispatches.
        """
        with self._lock:
            if self._closing:
                return
            self._closing = True
            entry = self._active
            if entry is not None:
                self._interrupt_active_locked(entry)
        if entry is not None:
            if entry.handle is not None:
                try:
                    entry.handle.terminate()
                except Exception:
                    pass
            # No termination wait here: no further dispatch follows a
            # shutdown, and the daemon's own teardown stops the session.
            self._notify_finalize(entry.item, PlaybackOutcome.STOPPED, None)
        self._stop_event.set()
        thread = self._supervisor_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=(self._supervisor_interval_sec or 0.0) + 5.0)

    # --- Internals: insertion, preemption, coalescing --------------------------

    def _insert_locked(
        self,
        priority: Priority,
        policy: Policy,
        payload: Any,
        event_type: str,
        identifiers: Optional[Sequence[str]],
        now: float,
    ) -> int:
        if policy is Policy.COALESCE and (target := self._open_coalesce_window_locked(event_type, now)) is not None:
            target.identifiers = target.identifiers + tuple(identifiers or ())
            target.coalesced += 1
            return target.id
        item = QueueItem(
            id=next(self._ids),
            priority=priority,
            policy=policy,
            seq=next(self._seq),
            enqueued_at=now,
            window_opened_at=now,
            payload=payload,
            event_type=event_type,
            identifiers=tuple(identifiers or ()),
        )
        heapq.heappush(self._pending, (-int(priority), item.seq, item))
        return item.id

    def _open_coalesce_window_locked(self, event_type: str, now: float) -> Optional[QueueItem]:
        """Finds the pending coalesce item whose window is still open.

        At most one open window can exist per event type: the first
        item opens it, arrivals inside it merge, and once closed the
        next arrival opens a fresh one.
        """
        for _, _, item in self._pending:
            if (
                item.policy is Policy.COALESCE
                and item.event_type == event_type
                and now - item.window_opened_at < self._coalesce_window_sec
            ):
                return item
        return None

    def _maybe_preempt_locked(self, incoming: Priority) -> Optional[Tuple[Any, QueueItem]]:
        """Cancels the active item if the preempt is strictly higher.

        Returns the cancelled ``(session handle, item)`` (to be
        terminated OUTSIDE the lock), or None when the preempt degrades
        to queue: equal or lower priority, no active item, or an active
        dispatch that has not yet produced its handle (nothing
        terminable — RF-AT-08-6).
        """
        entry = self._active
        if entry is None or entry.handle is None:
            return None
        if incoming <= entry.item.priority:
            return None  # equal or lower: policy degrades to queue (RF-AT-08-2)
        self._interrupt_active_locked(entry)
        return entry.handle, entry.item

    def _interrupt_active_locked(self, entry: _ActiveEntry) -> None:
        entry.item.status = "interrupted"
        self._interrupted_count += 1
        self._active = None

    # --- Internals: dispatch and finalization ----------------------------------

    def _pop_next_locked(self) -> Optional[QueueItem]:
        if self._closing or self._active is not None or not self._pending:
            return None
        _, _, item = heapq.heappop(self._pending)
        return item

    def _notify_finalize(
        self, item: QueueItem, outcome: PlaybackOutcome, error: Optional[str]
    ) -> None:
        """Forwards a manager-initiated finalization to the host hook.

        Fired for watchdog wedges (FAILED + the watchdog error), preempt
        and shutdown interrupts (STOPPED — a cut is a normal end, not a
        failure), and runner crashes (FAILED + the runner error). NEVER
        fired for outcomes the runner itself reported through
        ``on_finished``: that path already owns its host-side reporting.
        Called outside the lock; a faulty hook must not affect
        scheduling.
        """
        if self._finalize_hook is None:
            return
        try:
            self._finalize_hook(item, outcome, error)
        except Exception:
            pass

    def _wait_termination(self, item_id: int, handle: Any) -> None:
        """Bounded wait for a TERMINATED session's playback to truly end.

        ``terminate()`` only asks; the device closes asynchronously on
        the playback thread. Called only on the preempt and watchdog
        cut paths, always under the dispatch mutex, always AFTER
        ``terminate()`` and BEFORE the next dispatch (R1-01: the
        no-overlap invariant covers the preempted interval). Expiry
        never wedges the daemon (RS-5): one residual is traced to
        stderr and the caller proceeds. Handles without a
        ``wait_stopped`` seam (legacy adapters) skip the wait.
        """
        wait = getattr(handle, "wait_stopped", None)
        if not callable(wait):
            return
        try:
            stopped = bool(wait(self._termination_wait_sec))
        except Exception:
            return  # a handle that cannot be waited on cannot be waited on
        if not stopped:
            print(
                f"agent-tts-queue: terminated session item={item_id} did not stop "
                f"within {self._termination_wait_sec:.2f}s; dispatching anyway "
                f"(documented residual, RS-5)",
                file=sys.stderr,
            )

    def _dispatch_next(self) -> None:
        """Event-driven dispatch: the caller's thread starts playback NOW.

        The RLock serializes runner calls (RF-AT-08-6); the thread-local
        guard keeps synchronous-completion chains flat instead of
        recursing one stack frame per item.
        """
        if getattr(self._dispatching, "on", False):
            return  # the loop up-stack will observe our finalize and continue
        with self._dispatch_mutex:
            self._dispatching.on = True
            try:
                while not self._closing:
                    with self._lock:
                        item = self._pop_next_locked()
                    if item is None:
                        return
                    self._start_item(item)
            finally:
                self._dispatching.on = False

    def _start_item(self, item: QueueItem) -> None:
        now = self._clock()
        with self._lock:
            item.status = "active"
            self._active = _ActiveEntry(
                item=item,
                started_at=now,
                last_progress_at=now,
            )
        handle: Optional[SessionHandle] = None
        try:
            handle = self._runner(item, self._on_finished(item.id))
        except Exception as e:
            with self._lock:
                self._finalize_active_locked(
                    item.id, failed=True, error=f"runner error: {e}", wedged=False
                )
            self._notify_finalize(item, PlaybackOutcome.FAILED, f"runner error: {e}")
            return
        with self._lock:
            entry = self._active
            if entry is not None and entry.item.id == item.id:
                # Finalize already ran (synchronous playback): skip.
                entry.handle = handle
                entry.last_token = _UNBASED  # first observation only baselines
                # A slow runner launch must not eat into the wedged
                # budget: the supervision clock starts at registration.
                entry.last_progress_at = self._clock()

    def _on_finished(self, item_id: int) -> FinishCallback:
        def on_finished(outcome: PlaybackOutcome, error: Optional[str] = None) -> None:
            with self._lock:
                entry = self._active
                if entry is None or entry.item.id != item_id:
                    return  # late/duplicate report: the first finalization won
                if outcome is PlaybackOutcome.FAILED:
                    self._finalize_active_locked(item_id, failed=True, error=error or "playback failed", wedged=False)
                else:  # completed or stopped: both are normal ends
                    self._finalize_active_locked(item_id, failed=False)
            self._dispatch_next()

        return on_finished

    def _finalize_active_locked(
        self,
        item_id: int,
        *,
        failed: bool,
        error: Optional[str] = None,
        wedged: bool = False,
    ) -> None:
        """Records the end of the active item. Caller holds _lock."""
        entry = self._active
        if entry is None or entry.item.id != item_id:
            return
        item = entry.item
        self._active = None
        if failed:
            item.status = "failed"
            self._failed_count += 1
            if wedged:
                self._wedged_count += 1
            self._last_error = error
            self._failed.append(
                FailedItemView(
                    id=item.id,
                    priority=item.priority,
                    event_type=item.event_type,
                    error=error or "playback failed",
                    failed_at=self._clock(),
                    wedged=wedged,
                )
            )
        else:
            item.status = "completed"
            self._completed_count += 1
