"""Targeted cancel by identifiers (voice-stack VS1.5, TECHNICAL-PLAN T3).

The daemon's ``cancel <json>`` command drops/stops ONLY the queue items that
carry a requested identifier. ``stop`` stays the global command and queue
policies are untouched. Hermetic: a scripted runner and a fake clock stand in
for real playback, so every ordering assertion is exact.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import pytest

from agent_tts.daemon import Daemon
from agent_tts.queue_manager import PlaybackOutcome, QueueItem, QueueManager


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeHandle:
    """SessionHandle double: records terminate/wait and never wedges."""

    def __init__(self) -> None:
        self.terminate_calls = 0
        self.wait_calls = 0

    def progress_token(self):
        return 0

    def is_paused(self) -> bool:
        return False

    def terminate(self) -> None:
        self.terminate_calls += 1

    def wait_stopped(self, timeout: float) -> bool:
        self.wait_calls += 1
        return True


class ScriptedRunner:
    """Playback ends only when the test calls ``finish``."""

    def __init__(self, *, give_handle: bool = True) -> None:
        self.give_handle = give_handle
        self.dispatched: List[QueueItem] = []
        self.handles: Dict[int, FakeHandle] = {}
        self._finishers: Dict[int, object] = {}

    def __call__(self, item: QueueItem, on_finished) -> Optional[FakeHandle]:
        self.dispatched.append(item)
        self._finishers[item.id] = on_finished
        if not self.give_handle:
            return None
        handle = FakeHandle()
        self.handles[item.id] = handle
        return handle

    def finish(self, item_id: int, outcome: PlaybackOutcome = PlaybackOutcome.COMPLETED) -> None:
        self._finishers.pop(item_id)(outcome, None)

    @property
    def dispatched_ids(self) -> List[int]:
        return [item.id for item in self.dispatched]


def _build(runner: ScriptedRunner) -> Daemon:
    daemon = Daemon(socket_path="unused.sock")
    daemon.queue_manager.shutdown()
    daemon.queue_manager = QueueManager(
        runner, clock=FakeClock(), supervisor_interval_sec=None
    )
    return daemon


@pytest.fixture
def runner() -> ScriptedRunner:
    return ScriptedRunner()


@pytest.fixture
def daemon(runner):
    d = _build(runner)
    yield d
    d.queue_manager.shutdown()


def _enqueue(d: Daemon, identifiers=None, **extra) -> int:
    payload = {"text": "hola", **extra}
    if identifiers is not None:
        payload["identifiers"] = identifiers
    reply = d.handle_command("enqueue " + json.dumps(payload))
    assert reply.startswith("ok=true item="), reply
    return int(reply.split("item=")[1].split()[0])


def _cancel(d: Daemon, identifiers) -> str:
    return d.handle_command("cancel " + json.dumps({"identifiers": identifiers}))


def test_cancel_removes_pending_and_matching_active_only(daemon, runner):
    active = _enqueue(daemon, ["A"])
    pending_a = _enqueue(daemon, ["A"])
    pending_b = _enqueue(daemon, ["B"])
    pending_c = _enqueue(daemon, ["C"])
    assert runner.dispatched_ids == [active]

    reply = _cancel(daemon, ["A"])

    assert reply == "ok=true removed=1 active_stopped=1"
    # The matching active item was cut through the preempt path...
    assert runner.handles[active].terminate_calls == 1
    assert runner.handles[active].wait_calls == 1
    # ...the matching pending item is gone, the foreign ones are intact and
    # the speaker moved on to the next foreign item.
    snap = daemon.queue_manager.snapshot()
    assert snap.active is not None and snap.active.id == pending_b
    assert [view.id for view in snap.pending] == [pending_c]
    assert pending_a not in runner.dispatched_ids
    # Foreign items finish normally (never interrupted by the cancel).
    runner.finish(pending_b)
    runner.finish(pending_c)
    assert runner.dispatched_ids == [active, pending_b, pending_c]
    assert runner.handles[pending_b].terminate_calls == 0
    assert runner.handles[pending_c].terminate_calls == 0
    assert daemon.queue_manager.snapshot().completed_count == 2


def test_cancel_no_match_idempotent(daemon, runner):
    active = _enqueue(daemon, ["A"])
    pending = _enqueue(daemon, ["B"])

    first = _cancel(daemon, ["unknown"])
    second = _cancel(daemon, ["unknown"])

    assert first == second == "ok=true removed=0 active_stopped=0"
    snap = daemon.queue_manager.snapshot()
    assert snap.active is not None and snap.active.id == active
    assert [view.id for view in snap.pending] == [pending]
    assert runner.handles[active].terminate_calls == 0


def test_cancel_empty_identifiers_noop(daemon, runner):
    active = _enqueue(daemon, ["A"])

    assert _cancel(daemon, []) == "ok=true removed=0 active_stopped=0"
    assert runner.handles[active].terminate_calls == 0
    assert daemon.queue_manager.snapshot().active.id == active


@pytest.mark.parametrize(
    "command, fragment",
    [
        ("cancel {not json", "invalid cancel payload"),
        ("cancel", "cancel requires"),
        ("cancel {}", "cancel requires"),
        ("cancel []", "cancel requires"),
        ('cancel {"identifiers": "A"}', "identifiers must be a list"),
        ('cancel {"identifiers": [true]}', "identifiers must be a list"),
        ('cancel {"identifiers": [["A"]]}', "identifiers must be a list"),
    ],
)
def test_cancel_malformed_payload_typed_error(daemon, command, fragment):
    reply = daemon.handle_command(command)
    assert reply.startswith("ok=false error="), reply
    assert fragment in reply


def test_cancel_while_shutting_down_is_a_typed_error(daemon):
    daemon._shutting_down = True
    assert _cancel(daemon, ["A"]) == "ok=false error=daemon shutting down"


def test_active_without_identifiers_not_stopped(daemon, runner):
    active = _enqueue(daemon)  # carries no identifiers: can never match
    assert _cancel(daemon, ["A"]) == "ok=true removed=0 active_stopped=0"
    assert runner.handles[active].terminate_calls == 0


def test_active_without_handle_is_not_cut():
    """A dispatch that produced no terminable handle cannot be cut (as preempt)."""
    runner = ScriptedRunner(give_handle=False)
    d = _build(runner)
    try:
        active = _enqueue(d, ["A"])
        assert _cancel(d, ["A"]) == "ok=true removed=0 active_stopped=0"
        assert d.queue_manager.snapshot().active.id == active
    finally:
        d.queue_manager.shutdown()


def test_merged_coalesce_item_is_trimmed_not_dropped(daemon, runner):
    """Cancelling one event must not silently lose the OTHER merged events."""
    _enqueue(daemon, ["busy"])  # owns the speaker so the next two queue
    coalesce = dict(priority="done", policy="coalesce", event_type="jobs finished")
    first = _enqueue(daemon, ["A"], **coalesce)
    merged = _enqueue(daemon, ["B"], **coalesce)
    assert merged == first  # B merged into A's window
    assert daemon.queue_manager.snapshot().pending[0].coalesced == 2

    assert _cancel(daemon, ["A"]) == "ok=true removed=0 active_stopped=0 trimmed=1"
    view = daemon.queue_manager.snapshot().pending[0]
    assert view.identifiers == ("B",)
    assert view.coalesced == 1

    # With a single event left the item is an ordinary request: now it goes.
    assert _cancel(daemon, ["B"]) == "ok=true removed=1 active_stopped=0"
    assert daemon.queue_manager.snapshot().queue_len == 0


def test_global_stop_semantics_untouched(daemon, runner):
    """``stop`` is still the global command; cancel neither replaces nor alters it."""
    active = _enqueue(daemon, ["A"])
    pending = _enqueue(daemon, ["B"])

    # No daemon session is attached to the scripted queue: stop keeps its
    # documented idle behaviour and does not touch the queue.
    assert daemon.handle_command("stop") == "status=stopped"
    snap = daemon.queue_manager.snapshot()
    assert snap.active.id == active
    assert [view.id for view in snap.pending] == [pending]
    assert runner.handles[active].terminate_calls == 0


def test_preempt_still_cuts_active_through_the_shared_path(daemon, runner):
    """The extracted cut is the one preempt uses: behaviour must be unchanged."""
    active = _enqueue(daemon, ["A"], priority="done", policy="queue")
    preempting = _enqueue(daemon, ["B"], priority="blocked", policy="preempt")

    assert runner.handles[active].terminate_calls == 1
    assert runner.handles[active].wait_calls == 1
    assert daemon.queue_manager.snapshot().active.id == preempting
