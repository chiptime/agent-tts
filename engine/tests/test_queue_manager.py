"""QueueManager core tests (AT-08, BLOQUE 1.3 hito Cola).

The manager is exercised entirely at its seams with a fake clock and a
scripted runner: playback "runs" until the test finalizes it, so every
scheduling decision (priority order, policy semantics, coalescing
windows, watchdog kills) is observed deterministically without a single
real sleep. The RS-5 watchdog is driven through the same injectable
clock: tests advance time and run one supervision tick explicitly.

The concurrency invariant under test (RF-AT-08-6): at most ONE runner
call may be "in flight" without a finalize between them, no matter how
many IPC threads enqueue at once.
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional

import pytest

from agent_tts.audio import AudioSession
from agent_tts.queue_manager import (
    DEFAULT_COALESCE_WINDOW_SEC,
    DEFAULT_WEDGED_TIMEOUT_SEC,
    AudioSessionHandle,
    FailedItemView,
    PlaybackOutcome,
    Policy,
    Priority,
    QueueItem,
    QueueManager,
    QueueSnapshot,
)


# --- Test doubles ---------------------------------------------------------------


class FakeClock:
    """Deterministic monotonic clock the tests advance by hand."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeHandle:
    """SessionHandle double: a token stream the test controls.

    ``wait_stopped`` mirrors the real seam's contract: by default it
    reports the session stopped immediately; with ``block_on_wait`` the
    call parks on ``wait_release`` (the test plays the still-unwinding
    playback thread) and reports expiry when the release never comes.
    """

    def __init__(self, paused: bool = False):
        self.token = 0
        self.paused = paused
        self.terminate_calls = 0
        self.wait_calls = 0
        self.block_on_wait = False
        self.wait_started = threading.Event()
        self.wait_release = threading.Event()

    def progress_token(self):
        return self.token

    def is_paused(self) -> bool:
        return self.paused

    def terminate(self) -> None:
        self.terminate_calls += 1

    def wait_stopped(self, timeout: float) -> bool:
        self.wait_calls += 1
        if not self.block_on_wait:
            return True
        self.wait_started.set()
        self.wait_release.wait(timeout)
        return self.wait_release.is_set()


class ScriptedRunner:
    """Runner double: records dispatches; playback ends only when the test says.

    ``finish(item_id, outcome)`` finalizes a dispatched item from the TEST
    thread; the manager then dispatches the next pending item synchronously
    inside that same call, which makes every ordering assertion exact.
    """

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.lock = threading.Lock()
        self.dispatched: List[QueueItem] = []
        self.dispatch_clock: Dict[int, float] = {}
        self.handles: Dict[int, FakeHandle] = {}
        self.in_flight = 0
        self.max_concurrent = 0
        self._finishers: Dict[int, Callable] = {}

    def __call__(self, item: QueueItem, on_finished) -> Optional[FakeHandle]:
        with self.lock:
            self.in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self.in_flight)
            self.dispatched.append(item)
            self.dispatch_clock[item.id] = self.clock()
        handle = FakeHandle()
        self.handles[item.id] = handle
        self._finishers[item.id] = on_finished
        return handle

    def finish(self, item_id: int, outcome: PlaybackOutcome, error: Optional[str] = None) -> None:
        on_finished = self._finishers.pop(item_id)
        on_finished(outcome, error)


class AutoCompletingRunner:
    """Runner double for stress tests: every playback ends immediately.

    Completing inside the runner call exercises the synchronous
    completion path (finish before the handle is even registered).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.started: List[int] = []
        self.in_flight = 0
        self.max_concurrent = 0

    def __call__(self, item: QueueItem, on_finished) -> FakeHandle:
        with self.lock:
            self.in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self.in_flight)
            self.started.append(item.id)
        on_finished(PlaybackOutcome.COMPLETED)
        with self.lock:
            self.in_flight -= 1
        return FakeHandle()


def make_manager(runner, clock=None, **kwargs) -> QueueManager:
    """Manager with the supervisor thread disabled (unit-test default)."""
    clock = clock or FakeClock()
    manager = QueueManager(
        runner,
        clock=clock,
        supervisor_interval_sec=None,  # tests drive check_watchdog by hand
        **kwargs,
    )
    return manager


def ids_of(items) -> List[int]:
    return [item.id for item in items]


# --- Construction defaults ------------------------------------------------------


def test_defaults_match_the_prd():
    assert DEFAULT_COALESCE_WINDOW_SEC == 5.0
    assert DEFAULT_WEDGED_TIMEOUT_SEC == 30.0


def test_priority_ordering_is_blocked_over_done_over_working():
    assert Priority.BLOCKED > Priority.DONE > Priority.WORKING


def test_priority_labels_round_trip():
    assert Priority.from_label("blocked") is Priority.BLOCKED
    assert Priority.from_label("DONE") is Priority.DONE
    assert Priority.from_label(" Working ") is Priority.WORKING
    with pytest.raises(ValueError):
        Priority.from_label("urgent")


def test_policy_values_are_the_wire_strings():
    assert Policy("queue") is Policy.QUEUE
    assert Policy("preempt") is Policy.PREEMPT
    assert Policy("coalesce") is Policy.COALESCE


def test_empty_manager_snapshot_is_idle():
    manager = make_manager(ScriptedRunner(FakeClock()))
    snap = manager.snapshot()
    assert isinstance(snap, QueueSnapshot)
    assert snap.queue_len == 0
    assert snap.pending == ()
    assert snap.active is None
    assert snap.failed == ()
    assert snap.last_error is None
    assert snap.completed_count == 0


# --- Dispatch: priority order, fairness, immediacy (RNF-AT-08-1, RF-AT-08-6) -----


def test_dispatches_highest_priority_first_and_fifo_within_a_priority():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE, payload="gate")
    a = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE, payload="a")
    b = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE, payload="b")
    c = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE, payload="c")
    d = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.QUEUE, payload="d")

    runner.finish(gate, PlaybackOutcome.COMPLETED)
    runner.finish(d, PlaybackOutcome.COMPLETED)
    runner.finish(b, PlaybackOutcome.COMPLETED)
    runner.finish(a, PlaybackOutcome.COMPLETED)
    runner.finish(c, PlaybackOutcome.COMPLETED)

    assert [item.payload for item in runner.dispatched] == ["gate", "d", "b", "a", "c"]
    assert ids_of(runner.dispatched) == [gate, d, b, a, c]


def test_finalize_dispatches_next_item_immediately_no_clock_advance():
    """RNF-AT-08-1 machinery: dispatch is event-driven, zero added delay."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    first = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    second = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)

    clock.advance(12.5)  # the active item finishes at t=12.5
    runner.finish(first, PlaybackOutcome.COMPLETED)

    # The next item reached the speaker at the SAME clock reading: the
    # finalize callback dispatches inline, no polling sleep in between.
    assert runner.dispatch_clock[second] == 12.5


def test_enqueue_during_active_never_starts_a_second_session():
    """RF-AT-08-6: the queue serializes; the single active slot is exclusive."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    for _ in range(3):
        manager.enqueue(priority=Priority.BLOCKED, policy=Policy.QUEUE)

    assert len(runner.dispatched) == 1
    snap = manager.snapshot()
    assert snap.queue_len == 3
    assert snap.active is not None
    assert snap.active.id == active

    runner.finish(active, PlaybackOutcome.COMPLETED)
    assert len(runner.dispatched) == 2  # exactly one more, not three


def test_snapshot_pending_lists_dispatch_order_with_priorities():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    w2 = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    d1 = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    b1 = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.QUEUE)

    pending = manager.snapshot().pending
    assert ids_of(pending) == [b1, d1, w2]
    assert [view.priority for view in pending] == [
        Priority.BLOCKED,
        Priority.DONE,
        Priority.WORKING,
    ]


# --- Policy: preempt ------------------------------------------------------------


def test_preempt_from_strictly_higher_priority_cancels_active():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    incoming = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    # The active item was terminated and marked interrupted...
    assert runner.handles[active].terminate_calls == 1
    snap = manager.snapshot()
    assert snap.interrupted_count == 1
    assert snap.failed == ()  # interrupted is NOT a failure
    # ...its announcement is lost (preempt semantics): it never re-queues.
    assert snap.queue_len == 0

    # The preempting item owns the speaker immediately.
    assert snap.active is not None and snap.active.id == incoming
    assert ids_of(runner.dispatched) == [active, incoming]


def test_preempted_item_late_finalize_is_ignored():
    """The play thread may report its stop after preemption: id-guarded."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    incoming = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    # The cancelled playback thread finally observes the stop.
    runner.finish(active, PlaybackOutcome.STOPPED)

    snap = manager.snapshot()
    assert snap.completed_count == 0  # the late report changed nothing
    assert snap.active is not None and snap.active.id == incoming
    assert len(runner.dispatched) == 2  # no spurious extra dispatch


def test_preempt_from_equal_priority_degrades_to_queue():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    incoming = manager.enqueue(priority=Priority.DONE, policy=Policy.PREEMPT)

    assert runner.handles[active].terminate_calls == 0
    snap = manager.snapshot()
    assert snap.active is not None and snap.active.id == active
    assert snap.queue_len == 1 and snap.pending[0].id == incoming

    runner.finish(active, PlaybackOutcome.COMPLETED)
    assert ids_of(runner.dispatched) == [active, incoming]


def test_preempt_from_lower_priority_degrades_to_queue():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.QUEUE)
    manager.enqueue(priority=Priority.DONE, policy=Policy.PREEMPT)

    assert runner.handles[active].terminate_calls == 0
    assert manager.snapshot().queue_len == 1


def test_queue_policy_never_interrupts_even_from_higher_priority():
    """Policy is per event: a blocked item with policy=queue waits its turn."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    manager.enqueue(priority=Priority.BLOCKED, policy=Policy.QUEUE)

    assert runner.handles[active].terminate_calls == 0
    assert manager.snapshot().queue_len == 1


def test_preempt_when_idle_dispatches_without_side_effects():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    item = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    snap = manager.snapshot()
    assert len(runner.dispatched) == 1
    assert snap.active is not None and snap.active.id == item
    assert snap.interrupted_count == 0


def test_preempt_during_dispatch_setup_degrades_to_queue():
    """A preempt that arrives before the active handle exists cannot cancel
    anything (there is no handle to terminate), so it must queue instead of
    risking an unstoppable overlap (RF-AT-08-6)."""
    clock = FakeClock()
    enqueued_from_runner = {}

    def reentrant_runner(item, on_finished):
        handle = FakeHandle()
        if item.payload == "first":
            # An IPC thread enqueues a strictly-higher preempt while the
            # dispatch of "first" has not produced its handle yet.
            enqueued_from_runner["id"] = manager.enqueue(
                priority=Priority.BLOCKED, policy=Policy.PREEMPT, payload="preemptor"
            )
        return handle

    manager = make_manager(reentrant_runner, clock)
    first = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE, payload="first")
    preemptor = enqueued_from_runner["id"]

    snap = manager.snapshot()
    assert snap.active is not None and snap.active.id == first  # not cancelled
    assert snap.queue_len == 1 and snap.pending[0].id == preemptor
    assert snap.interrupted_count == 0


# --- Termination wait (R1-01, RF-AT-08-6): dispatch waits out the cancelled session ---


def test_preempt_waits_for_the_terminated_session_before_dispatching_next():
    """terminate() only ASKS the session to stop; the device closes on the
    playback thread. The next item must not dispatch until that thread
    has actually ended (no-overlap includes the preempted interval)."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    handle = runner.handles[active]
    handle.block_on_wait = True  # the cancelled playback is still unwinding

    preempted = {}
    done = threading.Event()

    def preempt():
        preempted["id"] = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    thread = threading.Thread(target=preempt)
    thread.start()
    assert handle.wait_started.wait(timeout=2.0), "manager never reached wait_stopped"
    # While the terminated session is still unwinding, nothing dispatches.
    assert ids_of(runner.dispatched) == [active]
    handle.wait_release.set()  # the playback thread finally exited
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert ids_of(runner.dispatched) == [active, preempted["id"]]
    assert handle.terminate_calls == 1  # the cut itself is unchanged


def test_watchdog_waits_for_the_terminated_session_before_dispatching_next():
    """The watchdog path keeps the same discipline: the replacement item
    dispatches only after the wedged session's bounded wait resolved."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, wedged_timeout_sec=30.0)

    wedged = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    handle = runner.handles[wedged]
    handle.block_on_wait = True
    next_item = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    clock.advance(30.1)

    verdict = {}

    def tick():
        verdict["wedged"] = manager.check_watchdog()

    thread = threading.Thread(target=tick)
    thread.start()
    assert handle.wait_started.wait(timeout=2.0), "watchdog never reached wait_stopped"
    assert ids_of(runner.dispatched) == [wedged]  # replacement held back
    handle.wait_release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert verdict["wedged"] is True
    assert ids_of(runner.dispatched) == [wedged, next_item]


def test_termination_wait_expiry_still_dispatches(capsys):
    """RS-5: a terminated session that never stops must not wedge the
    queue — the bounded wait expires, a residual is traced, dispatch proceeds."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, termination_wait_sec=0.05)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    handle = runner.handles[active]
    handle.block_on_wait = True  # never stops: the wait must expire
    incoming = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    assert ids_of(runner.dispatched) == [active, incoming]
    assert handle.wait_calls == 1
    assert "did not stop" in capsys.readouterr().err


def test_termination_wait_is_skipped_for_handles_without_the_seam():
    """Legacy handles (no wait_stopped) keep working: nothing to wait out."""

    class LegacyHandle:
        def __init__(self):
            self.terminate_calls = 0

        def progress_token(self):
            return 0

        def is_paused(self):
            return False

        def terminate(self):
            self.terminate_calls += 1

    clock = FakeClock()
    handles = {}

    def legacy_runner(item, on_finished):
        handles[item.id] = LegacyHandle()
        return handles[item.id]

    manager = make_manager(legacy_runner, clock)
    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    incoming = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    assert handles[active].terminate_calls == 1
    assert list(handles) == [active, incoming]  # both dispatched, in order


# --- Finalize hook (R1-02): manager-side ends reach the host ---------------------------


def _hook_recorder() -> tuple:
    reported = []
    return reported, lambda item, outcome, error: reported.append((item, outcome, error))


def test_finalize_hook_reports_watchdog_wedge():
    """The wedge verdict must reach the host even though the worker never
    reports anything (R1-02)."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    reported, hook = _hook_recorder()
    manager = make_manager(runner, clock, wedged_timeout_sec=30.0, finalize_hook=hook)

    wedged = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    runner.handles[wedged].token = 7
    clock.advance(30.1)

    assert manager.check_watchdog() is True
    assert len(reported) == 1
    item, outcome, error = reported[0]
    assert item.id == wedged
    assert outcome is PlaybackOutcome.FAILED
    assert "no playback progress" in error


def test_finalize_hook_reports_preempt_interrupt():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    reported, hook = _hook_recorder()
    manager = make_manager(runner, clock, finalize_hook=hook)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    assert [(item.id, outcome, error) for item, outcome, error in reported] == [
        (active, PlaybackOutcome.STOPPED, None)
    ]


def test_finalize_hook_reports_shutdown_interrupt():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    reported, hook = _hook_recorder()
    manager = make_manager(runner, clock, finalize_hook=hook)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    manager.shutdown()

    assert [(item.id, outcome, error) for item, outcome, error in reported] == [
        (active, PlaybackOutcome.STOPPED, None)
    ]


def test_finalize_hook_is_silent_on_natural_finishes():
    """Outcomes the runner itself reported flow through the runner's own
    path (its worker releases its waiter) — the hook must stay silent so
    the two paths cannot double-report."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    reported, hook = _hook_recorder()
    manager = make_manager(runner, clock, finalize_hook=hook)

    first = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    failing = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    runner.finish(first, PlaybackOutcome.COMPLETED)
    runner.finish(failing, PlaybackOutcome.FAILED, error="device vanished")

    assert reported == []


def test_finalize_hook_reports_runner_crash():
    clock = FakeClock()
    reported, hook = _hook_recorder()

    def crashing_runner(item, on_finished):
        if item.payload == "bad":
            raise RuntimeError("cannot start playback")
        return FakeHandle()

    manager = make_manager(crashing_runner, clock, finalize_hook=hook)
    bad = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE, payload="bad")

    assert [(item.id, outcome, error) for item, outcome, error in reported] == [
        (bad, PlaybackOutcome.FAILED, "runner error: cannot start playback")
    ]


def test_faulty_finalize_hook_never_wedges_scheduling():
    clock = FakeClock()
    runner = ScriptedRunner(clock)

    def exploding_hook(item, outcome, error):
        raise RuntimeError("hook exploded")

    manager = make_manager(runner, clock, finalize_hook=exploding_hook)

    active = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    incoming = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.PREEMPT)

    snap = manager.snapshot()
    assert snap.interrupted_count == 1
    assert ids_of(runner.dispatched) == [active, incoming]  # scheduling survived


# --- Policy: coalesce ------------------------------------------------------------


def _coalesce_event(manager, identifier, event_type="jobs finished", priority=Priority.DONE):
    return manager.enqueue(
        priority=priority,
        policy=Policy.COALESCE,
        event_type=event_type,
        identifiers=[identifier],
    )


def test_coalescible_items_merge_within_the_window_and_are_visible_before_sounding():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    merged = _coalesce_event(manager, "a")
    clock.advance(1.0)
    _coalesce_event(manager, "b")  # joins the open window

    snap = manager.snapshot()
    assert snap.queue_len == 1  # ONE merged pending item
    view = snap.pending[0]
    assert view.id == merged
    assert view.coalesced == 2
    assert view.priority is Priority.DONE
    assert view.identifiers == ("a", "b")
    assert view.announcement == "2 jobs finished: a, b"

    runner.finish(gate, PlaybackOutcome.COMPLETED)
    active = manager.snapshot().active
    assert active is not None and active.id == merged and active.coalesced == 2


def test_coalesce_window_closes_exactly_at_the_boundary():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, coalesce_window_sec=5.0)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "a")  # opens the window at t=0
    clock.advance(4.99)
    _coalesce_event(manager, "b")  # still inside: merges
    assert manager.snapshot().queue_len == 1

    clock.advance(0.01)  # t=5.0 exactly: the window opened at t=0 is closed
    _coalesce_event(manager, "c")  # opens the NEXT window
    snap = manager.snapshot()
    assert snap.queue_len == 2
    assert [view.coalesced for view in snap.pending] == [2, 1]


def test_different_event_types_never_merge():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "a", event_type="jobs finished")
    _coalesce_event(manager, "x", event_type="agents blocked")

    snap = manager.snapshot()
    assert snap.queue_len == 2
    assert {view.event_type for view in snap.pending} == {
        "jobs finished",
        "agents blocked",
    }


def test_merged_item_keeps_the_window_owners_priority():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "a", priority=Priority.DONE)
    _coalesce_event(manager, "b", priority=Priority.BLOCKED)  # same type, merges

    snap = manager.snapshot()
    assert snap.queue_len == 1
    assert snap.pending[0].priority is Priority.DONE  # the owner's priority


def test_coalesce_item_dispatches_without_waiting_for_the_window():
    """The window bounds MERGING only; it never delays dispatch (RNF-AT-08-1)."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    item = _coalesce_event(manager, "a")

    assert len(runner.dispatched) == 1  # went straight to the speaker
    snap = manager.snapshot()
    assert snap.active is not None and snap.active.coalesced == 1


def test_coalescing_stops_once_the_merged_item_reaches_the_speaker():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    merged = _coalesce_event(manager, "a")
    _coalesce_event(manager, "b")

    runner.finish(gate, PlaybackOutcome.COMPLETED)  # merged item now active
    _coalesce_event(manager, "c")  # same type, same wall time: NO merge

    snap = manager.snapshot()
    assert snap.active is not None and snap.active.id == merged
    assert snap.queue_len == 1
    assert snap.pending[0].coalesced == 1
    assert snap.pending[0].identifiers == ("c",)


def test_announcement_lists_at_most_three_identifiers_and_sums_the_rest():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "a")
    _coalesce_event(manager, "b")
    _coalesce_event(manager, "c")
    _coalesce_event(manager, "d")
    _coalesce_event(manager, "e")

    view = manager.snapshot().pending[0]
    assert view.coalesced == 5
    assert view.identifiers == ("a", "b", "c", "d", "e")
    assert view.announcement == "5 jobs finished: a, b, c and 2 more"


def test_single_coalesce_item_has_no_synthesized_announcement():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "a")

    assert manager.snapshot().pending[0].announcement == ""


def test_enqueue_returns_the_surviving_item_id_when_merging():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    first = _coalesce_event(manager, "a")
    second = _coalesce_event(manager, "b")

    assert second == first  # both events are announced by the same queue item
    assert manager.snapshot().queue_len == 1


# --- RS-5 watchdog: born-in liveness --------------------------------------------


def test_wedged_session_is_terminated_marked_failed_and_replaced():
    """RS-5: a session that stops making progress can never wedge the queue."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, wedged_timeout_sec=30.0)

    wedged = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    runner.handles[wedged].token = 7  # one progress report, then silence
    next_item = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)

    clock.advance(29.9)
    assert manager.check_watchdog() is False  # still inside the budget
    clock.advance(0.2)  # t=30.1: no progress for longer than the timeout

    assert manager.check_watchdog() is True
    assert runner.handles[wedged].terminate_calls == 1

    snap = manager.snapshot()
    assert snap.failed_count == 1
    assert snap.wedged_count == 1
    assert len(snap.failed) == 1
    failure = snap.failed[0]
    assert isinstance(failure, FailedItemView)
    assert failure.id == wedged
    assert failure.wedged is True
    assert "no playback progress" in failure.error
    # The queue moved on: the next item owns the speaker now.
    assert snap.active is not None and snap.active.id == next_item
    assert ids_of(runner.dispatched) == [wedged, next_item]


def test_progressing_session_is_never_killed():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, wedged_timeout_sec=30.0)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    for _ in range(5):
        clock.advance(20.0)
        runner.handles[active].token += 1  # playback advanced
        assert manager.check_watchdog() is False

    snap = manager.snapshot()
    assert snap.active is not None and snap.active.id == active
    assert runner.handles[active].terminate_calls == 0
    assert snap.failed == ()


def test_paused_session_is_never_killed_and_resume_gets_a_fresh_budget():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, wedged_timeout_sec=30.0)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    handle = runner.handles[active]
    handle.paused = True

    clock.advance(10_000.0)  # paused for hours: the watchdog stays suspended
    assert manager.check_watchdog() is False
    assert handle.terminate_calls == 0

    handle.paused = False  # user resumes: the budget restarts
    clock.advance(29.0)
    assert manager.check_watchdog() is False
    clock.advance(1.5)  # no progress since resume beyond the timeout
    assert manager.check_watchdog() is True
    assert handle.terminate_calls == 1


def test_watchdog_termination_then_late_natural_finish_is_ignored():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, wedged_timeout_sec=30.0)

    wedged = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    clock.advance(30.1)
    assert manager.check_watchdog() is True

    # The terminated playback thread surfaces its stop long after.
    runner.finish(wedged, PlaybackOutcome.COMPLETED)

    snap = manager.snapshot()
    assert snap.completed_count == 0  # the wedge verdict stands
    assert snap.failed_count == 1
    assert snap.active is not None and snap.active.id != wedged


def test_watchdog_timeout_is_configurable():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock, wedged_timeout_sec=0.5)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    clock.advance(0.49)
    assert manager.check_watchdog() is False
    clock.advance(0.02)
    assert manager.check_watchdog() is True


def test_watchdog_without_handle_waits_for_one():
    """A runner that has not (yet) produced a handle cannot be supervised;
    the watchdog skips it instead of killing blindly."""
    clock = FakeClock()

    def handleless_runner(item, on_finished):
        return None

    manager = make_manager(handleless_runner, clock, wedged_timeout_sec=1.0)
    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    clock.advance(100.0)
    assert manager.check_watchdog() is False
    assert manager.snapshot().active is not None


def test_failed_playback_is_visible_in_the_snapshot():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    failing = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    runner.finish(failing, PlaybackOutcome.FAILED, error="device vanished")

    snap = manager.snapshot()
    assert snap.failed_count == 1
    assert snap.last_error == "device vanished"
    assert snap.failed[0].error == "device vanished"
    assert snap.active is None  # failure freed the speaker


def test_runner_crash_marks_item_failed_and_queue_moves_on():
    """A runner that raises must not wedge the queue (RS-5 spirit)."""
    clock = FakeClock()
    calls = []

    def crashing_runner(item, on_finished):
        calls.append(item.payload)
        if item.payload == "bad":
            raise RuntimeError("cannot start playback")
        return FakeHandle()

    manager = make_manager(crashing_runner, clock)
    manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE, payload="bad")
    good = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE, payload="good")

    snap = manager.snapshot()
    assert snap.failed_count == 1
    assert "runner error" in snap.last_error
    assert snap.active is not None and snap.active.id == good
    assert calls == ["bad", "good"]


def test_stopped_playback_counts_as_completed():
    """A user stop is a normal end (status=stopped), not a failure."""
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    stopped = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)
    runner.finish(stopped, PlaybackOutcome.STOPPED)

    snap = manager.snapshot()
    assert snap.completed_count == 1
    assert snap.failed_count == 0
    assert snap.active is None


def test_manager_returns_to_idle_when_the_queue_empties():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    first = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    second = manager.enqueue(priority=Priority.BLOCKED, policy=Policy.QUEUE)
    runner.finish(first, PlaybackOutcome.COMPLETED)
    runner.finish(second, PlaybackOutcome.COMPLETED)

    snap = manager.snapshot()
    assert snap.queue_len == 0
    assert snap.active is None
    assert snap.completed_count == 2


# --- Lifecycle ------------------------------------------------------------------


def test_enqueue_after_shutdown_raises():
    manager = make_manager(ScriptedRunner(FakeClock()))
    manager.shutdown()
    with pytest.raises(RuntimeError):
        manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE)


def test_shutdown_is_idempotent_and_terminates_the_active_item():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    active = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    manager.shutdown()
    manager.shutdown()  # idempotent

    snap = manager.snapshot()
    assert snap.active is None
    assert snap.interrupted_count == 1  # teardown interrupts, never fails
    assert runner.handles[active].terminate_calls == 1


def test_default_construction_runs_the_supervisor_and_shutdown_stops_it():
    runner = ScriptedRunner(FakeClock())
    manager = QueueManager(runner)  # real clock, default supervisor
    try:
        assert manager._supervisor_thread is not None
        assert manager._supervisor_thread.is_alive()
    finally:
        manager.shutdown()
    assert not manager._supervisor_thread.is_alive()


# --- Concurrency (RF-AT-08-6 under multi-threaded IPC dispatch) -------------------


def test_concurrent_enqueues_never_overlap_and_lose_nothing():
    """Stress: many IPC threads enqueue while the queue drains itself.

    Asserts the three invariants that matter: no playback overlap
    (max one runner call in flight without a finalize), no lost items
    (every enqueue accounted for), and an idle manager at the end.
    """
    runner = AutoCompletingRunner()
    manager = make_manager(runner, FakeClock())

    threads = []
    per_thread = 25
    num_threads = 8

    def producer(thread_index):
        for i in range(per_thread):
            priority = [Priority.WORKING, Priority.DONE, Priority.BLOCKED][
                (thread_index + i) % 3
            ]
            manager.enqueue(priority=priority, policy=Policy.QUEUE, payload=i)

    for index in range(num_threads):
        threads.append(threading.Thread(target=producer, args=(index,)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snap = manager.snapshot()
        if snap.completed_count == per_thread * num_threads:
            break
        time.sleep(0.005)

    snap = manager.snapshot()
    assert snap.completed_count == per_thread * num_threads
    assert snap.queue_len == 0
    assert snap.active is None
    assert snap.failed_count == 0
    assert runner.max_concurrent == 1  # RF-AT-08-6: never two sessions


# --- Snapshot serialization (consumed by the IPC status command in T3) ------------


def test_snapshot_as_dict_is_json_ready():
    clock = FakeClock()
    runner = ScriptedRunner(clock)
    manager = make_manager(runner, clock)

    gate = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "a")
    _coalesce_event(manager, "b")
    failing = manager.enqueue(priority=Priority.DONE, policy=Policy.QUEUE, payload="boom")
    runner.finish(gate, PlaybackOutcome.COMPLETED)  # merged coalesce item next
    merged_next = runner.dispatched[-1].id
    runner.finish(merged_next, PlaybackOutcome.COMPLETED)  # failing item next
    runner.finish(failing, PlaybackOutcome.FAILED, error="kaput")

    payload = manager.snapshot().as_dict()
    encoded = json.dumps(payload)  # primitives only
    assert "queue_len" in encoded
    assert payload["queue_len"] == 0
    assert payload["last_error"] == "kaput"
    assert payload["failed"][0]["wedged"] is False
    assert payload["completed_count"] == 2

    # The merged coalesce item is visible while it waits: re-run the
    # merge shape and inspect the pending dict serialization.
    gate2 = manager.enqueue(priority=Priority.WORKING, policy=Policy.QUEUE)
    _coalesce_event(manager, "x")
    _coalesce_event(manager, "y")
    pending_payload = manager.snapshot().as_dict()
    assert pending_payload["queue_len"] == 1
    assert pending_payload["pending"][0]["coalesced"] == 2
    assert pending_payload["pending"][0]["priority"] == "done"
    json.dumps(pending_payload)


# --- AudioSessionHandle: the concrete adapter over BLOQUE 1.2 sessions -------------


def test_audio_session_handle_tracks_real_session_progress_and_pause():
    session = AudioSession(label="real")
    handle = AudioSessionHandle(session)

    before = handle.progress_token()
    session.current_frame += 24000  # one second of PCM advanced
    assert handle.progress_token() != before

    assert handle.is_paused() is False
    session.state["status"] = "paused"
    assert handle.is_paused() is True

    handle.terminate()
    assert session.state["stop"] is True
    assert session.state["status"] == "stopped"


def test_audio_session_handle_counts_streaming_growth_as_progress():
    """While streaming, appended PCM moves total_frames: that is progress
    even before the position starts moving (synthesis is not a wedge)."""
    session = AudioSession(label="stream")
    handle = AudioSessionHandle(session)

    before = handle.progress_token()
    session.state["producing"] = True
    session.total_frames += 48000
    assert handle.progress_token() != before


def test_audio_session_handle_is_a_session_handle_double_drop_in():
    """The adapter satisfies the same seam the fakes in this file use."""
    handle = AudioSessionHandle(AudioSession(label="seam"))
    assert callable(handle.progress_token)
    assert callable(handle.is_paused)
    assert callable(handle.terminate)
    assert callable(handle.wait_stopped)


def test_audio_session_handle_wait_stopped_reports_worker_liveness():
    """wait_stopped joins the playback worker: True once it exited, False
    while it is still unwinding within the bound (R1-01)."""
    session = AudioSession(label="wait")
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()
    handle = AudioSessionHandle(session, playback_thread=worker)

    assert handle.wait_stopped(0.05) is False  # still unwinding: expiry

    release.set()
    worker.join(timeout=2.0)
    assert handle.wait_stopped(2.0) is True


def test_audio_session_handle_wait_stopped_without_a_worker_returns_true():
    """No playback thread to wait out (bare adapter use): nothing to do."""
    handle = AudioSessionHandle(AudioSession(label="bare"))
    assert handle.wait_stopped(1.0) is True
