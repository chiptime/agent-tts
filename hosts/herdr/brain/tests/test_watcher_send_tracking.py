"""Watcher × send tracking (parity-f7g S6): the fixes the tracker needs.

The watcher stays the ONE herdr poller: the tracker rides its cycle via
the ``send_tracker`` seam, gains the fast cadence while any tracker is
non-terminal, and the 60 s debounce no longer swallows a NEW turn's
done/error. All tests use the existing stub-herdr + FakeClock patterns
from tests/test_watcher.py.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from herdr_brain.send_tracking import SSE_EVENT_TYPE, SendTracker
from herdr_brain.watcher import (
    FAST_POLL_INTERVAL_S,
    POLL_INTERVAL_S,
    AgentWatcher,
    AnnouncementHub,
)

from tests.test_watcher import FakeClock, fake_tts, make_agent


class WallClock:
    """The tracker's own clock (wall domain, unlike the watcher's)."""

    def __init__(self, t=1_732_000_000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_tracker(events=None, pushes=None, clock=None):
    clock = clock or WallClock()
    events = events if events is not None else []
    pushes = pushes if pushes is not None else []
    tracker = SendTracker(
        clock=clock,
        publish=events.append,
        notify=lambda pane_id, label, state, error=None: pushes.append(
            {"pane_id": pane_id, "label": label, "state": state, "error": error}
        ),
    )
    return tracker


def make_watcher(settings, stub, tracker=None, notifier=None, clock=None,
                 hub=None):
    return AgentWatcher(
        settings,
        herdr=stub,
        tts_renderer=fake_tts(),
        clock=clock or FakeClock(),
        send_tracker=tracker,
        notifier=notifier,
        hub=hub,
    )


# ── the feed: the watcher's poll drives the tracker ──────────────────────────


class TestWatcherFeedsTheTracker:
    def test_a_poll_observes_the_tracker_pane(self, settings, make_stub):
        events: list = []
        tracker = make_tracker(events=events)
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, tracker=tracker)

        tracker.start("rct-1", "w1:p1", "sent")
        watcher.poll_once()  # first poll IS the baseline: still observed
        assert [e["state"] for e in events] == ["sent", "started"]
        assert tracker.snapshot("rct-1")["state"] == "started"

    def test_a_tracker_without_a_watcher_still_works_standalone(
        self, settings, make_stub
    ):
        events: list = []
        tracker = make_tracker(events=events)
        stub = make_stub(agents=[make_agent(status="done")])
        watcher = make_watcher(settings, stub, tracker=None)
        watcher.poll_once()
        tracker.start("rct-1", "w1:p1", "sent")
        assert watcher.poll_once() == []  # done→done: nothing to announce
        # No watcher feed: the tracker keeps its last state.
        assert tracker.snapshot("rct-1")["state"] == "sent"

    def test_a_broken_tracker_never_breaks_the_poll(self, settings, make_stub):
        class BrokenTracker:
            def active_panes(self):
                raise RuntimeError("tracker down")

            def observe(self, current, errors):
                raise RuntimeError("tracker down")

        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, tracker=BrokenTracker())
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1  # announcements survived


# ── S6(a): the debounce must not swallow a NEW turn's outcome ─────────────────


class TestDebounceNewTurn:
    def test_done_again_after_real_work_announces_within_the_window(
        self, settings, make_stub
    ):
        clock = FakeClock()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, clock=clock)
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1

        # A NEW turn: the pane went back to work, then finished again
        # inside the 60 s window. The completion must be announced.
        clock.advance(10)
        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1

    def test_error_again_after_real_work_announces_within_the_window(
        self, settings, make_stub, tmp_path, monkeypatch
    ):
        db = tmp_path / "s6a.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,"
            " time_created INT, data TEXT)"
        )
        conn.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT,"
            " session_id TEXT, time_created INT, data TEXT)"
        )
        conn.execute(
            "INSERT INTO message VALUES ('e1', 'ses_watch000001', 1, ?)",
            (json.dumps({
                "role": "assistant",
                "error": {"name": "APIError", "data": {"message": "boom"}},
            }),),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        clock = FakeClock()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, clock=clock)
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        watcher.poll_once()  # first done (transcript has no error yet)
        clock.advance(10)
        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        produced = watcher.poll_once()
        assert [a["status"] for a in produced] == ["error"]

    def test_flapping_without_work_stays_debounced(self, settings, make_stub):
        """PRESERVE: only a real turn resets the debounce. A pane that
        flickers done → idle → done inside the window (no work in
        between) is the flap the 60 s debounce exists for."""
        clock = FakeClock()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, clock=clock)
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1

        clock.advance(10)
        stub._agents = [make_agent(status="idle")]  # left, but no work
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]  # re-entered
        assert watcher.poll_once() == []


# ── S6(b): a vanished pane with an in-flight tracker ──────────────────────────


class TestVanishedPaneWithTracker:
    def test_tracker_ends_pane_gone_and_no_resuelto_is_pushed(
        self, settings, make_stub
    ):
        """The real S6(b) sequence: the pane sat DONE in the watcher's
        baseline (so the old code would retract it as "Resuelto"), a
        send lands between polls (tracker in flight), and the pane
        vanishes before the next one."""
        from tests.test_watcher_hooks import RecordingNotifier

        events: list = []
        tracker = make_tracker(events=events)
        notifier = RecordingNotifier()
        stub = make_stub(agents=[make_agent(status="done")])
        watcher = make_watcher(settings, stub, tracker=tracker, notifier=notifier)
        watcher.poll_once()  # baseline: done (nothing announced)

        tracker.start("rct-1", "w1:p1", "sent")  # the send lands now
        stub._agents = []  # ...and the pane dies before the next poll
        watcher.poll_once()

        assert tracker.snapshot("rct-1")["state"] == "pane_gone"
        assert events[-1]["state"] == "pane_gone"
        assert events[-1]["type"] == SSE_EVENT_TYPE
        # S6(b): no "Resuelto" retract for a pane whose send died with it.
        assert notifier.handled_panes == []

    def test_a_vanished_pane_without_a_tracker_still_retracts(
        self, settings, make_stub
    ):
        """PRESERVE: the untracked case keeps today's handled push."""
        from tests.test_watcher_hooks import RecordingNotifier

        notifier = RecordingNotifier()
        stub = make_stub(agents=[make_agent(status="blocked")])
        watcher = make_watcher(settings, stub, notifier=notifier)
        watcher.poll_once()  # baseline: blocked
        stub._agents = []
        watcher.poll_once()
        assert notifier.handled_panes == ["w1:p1"]

    def test_a_terminal_tracker_does_not_suppress_the_retract(
        self, settings, make_stub
    ):
        from tests.test_watcher_hooks import RecordingNotifier

        events: list = []
        tracker = make_tracker(events=events)
        notifier = RecordingNotifier()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, tracker=tracker, notifier=notifier)
        watcher.poll_once()

        tracker.start("rct-1", "w1:p1", "sent")
        stub._agents = [make_agent(status="done")]
        watcher.poll_once()  # tracker ends done (terminal)
        stub._agents = []
        watcher.poll_once()
        assert tracker.snapshot("rct-1")["state"] == "done"
        assert notifier.handled_panes == ["w1:p1"]


# ── S4: the fast cadence while a send is in flight ────────────────────────────


class TestFastCadence:
    def test_an_active_tracker_tightens_the_cadence_without_subscribers(
        self, settings, make_stub
    ):
        stub = make_stub(agents=[make_agent(status="idle")])
        watcher = make_watcher(settings, stub, tracker=make_tracker())
        watcher.poll_once()
        assert watcher.next_interval_s() == POLL_INTERVAL_S

        watcher._send_tracker.start("rct-1", "w1:p1", "sent")
        assert watcher.next_interval_s() == FAST_POLL_INTERVAL_S

    def test_the_cadence_relaxes_again_once_terminal(self, settings, make_stub):
        tracker = make_tracker()
        stub = make_stub(agents=[make_agent(status="done")])
        watcher = make_watcher(settings, stub, tracker=tracker)
        watcher.poll_once()
        tracker.start("rct-1", "w1:p1", "sent")
        watcher.poll_once()  # observes done → terminal
        assert watcher.next_interval_s() == POLL_INTERVAL_S


# ── S6(c): opencode errors while Herdr says working reach the tracker ─────────


class TestOpencodeErrorWhileWorking:
    def test_the_tracker_ends_error_and_pushes(self, settings, make_stub,
                                               tmp_path, monkeypatch):
        base_ms = 1_732_000_000_000
        db = tmp_path / "s6c.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,"
            " time_created INT, time_updated INT, data TEXT)"
        )
        conn.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT,"
            " session_id TEXT, time_created INT, time_updated INT, data TEXT)"
        )
        conn.execute(
            "INSERT INTO message VALUES ('e1', 'ses_watch000001', ?, ?, ?)",
            (base_ms + 5_000, base_ms + 5_000,
             json.dumps({
                 "role": "assistant",
                 "error": {"name": "APIError", "data": {"message": "limit"}},
             })),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        wall = WallClock(base_ms / 1000.0 + 1.0)
        events: list = []
        pushes: list = []
        tracker = make_tracker(events=events, pushes=pushes, clock=wall)
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, tracker=tracker)
        watcher.poll_once()

        tracker.start("rct-1", "w1:p1", "sent", sent_at=base_ms / 1000.0)
        watcher.poll_once()  # herdr still says working

        snap = tracker.snapshot("rct-1")
        assert snap["state"] == "error"
        assert snap["error"]["name"] == "APIError"
        assert [p["state"] for p in pushes] == ["error"]
        # The watcher itself stayed quiet: working is not announceable.
        assert events[-1]["state"] == "error"
        assert events[-1]["type"] == SSE_EVENT_TYPE
