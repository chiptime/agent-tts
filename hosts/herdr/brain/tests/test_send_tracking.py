"""Send tracking (parity-f7g): every PWA send reaches a visible outcome.

The tracker is a bounded in-memory registry keyed by receipt id, fed by
the watcher's existing poll (never a second herdr timer). These tests
drive it through its public surface — ``start`` / ``observe`` /
``snapshot`` — with injected clocks, fake harness signals and, for the
opencode seam (S2), a real temp-sqlite store. No pane, no sleeps.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.send_tracking import (
    NO_START_S,
    SSE_EVENT_TYPE,
    STALLED_S,
    TERMINAL_STATES,
    TRACK_CAP_S,
    TRACKED_OUTCOMES,
    SendTracker,
    default_harness_signals,
)


class FakeClock:
    """Wall-clock double: the tracker compares against store epochs."""

    def __init__(self, t=1_732_000_000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_agent(pane="w1:p1", status="working", value="ses_track000001",
               agent="opencode"):
    return AgentInfo(
        pane_id=pane, agent=agent, status=status, session_kind="id",
        session_value=value, cwd="/repo", title="T", focused=True,
    )


class FakeSignals:
    """Harness-signals double: one canned read result."""

    def __init__(self, activity_at=None, error=None, assistant_after=False):
        self.activity_at = activity_at
        self.error = error
        self.assistant_after = assistant_after
        self.calls = []

    def read(self, session_id, since_s):
        self.calls.append((session_id, since_s))
        return {
            "activity_at": self.activity_at,
            "error": self.error,
            "assistant_after": self.assistant_after,
        }


def make_tracker(clock=None, signals=None):
    clock = clock or FakeClock()
    events: list = []
    pushes: list = []
    tracker = SendTracker(
        clock=clock,
        publish=events.append,
        notify=lambda pane_id, label, state, error=None: pushes.append(
            {"pane_id": pane_id, "label": label, "state": state, "error": error}
        ),
        harness_signals=lambda kind: signals,
    )
    tracker.events = events
    tracker.pushes = pushes
    return tracker


# ── S1: states, supersession, terminal set ────────────────────────────────────


class TestStart:
    def test_sent_outcome_starts_in_sent_and_publishes_it(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        assert tracker.snapshot("rct-1")["state"] == "sent"
        assert tracker.events == [{
            "type": SSE_EVENT_TYPE,
            "receipt_id": "rct-1",
            "pane_id": "w1:p1",
            "seq": 1,
            "state": "sent",
            "elapsed_s": 0.0,
        }]

    def test_unknown_outcome_also_starts_a_tracker(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "unknown")
        assert tracker.snapshot("rct-1")["state"] == "sent"

    def test_effective_outcome_is_born_terminal_done(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "effective")
        snap = tracker.snapshot("rct-1")
        assert snap["state"] == "done"
        assert tracker.events[-1]["state"] == "done"
        assert not tracker.has_active()

    def test_tracked_outcomes_are_exactly_sent_effective_unknown(self):
        assert TRACKED_OUTCOMES == ("sent", "effective", "unknown")

    def test_snapshot_carries_elapsed_and_optional_fields(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent", sent_at=clock.t - 10.0)
        snap = tracker.snapshot("rct-1")
        assert snap["elapsed_s"] == 10.0
        assert snap["sent_at"] == clock.t - 10.0
        assert snap["stalled"] is False
        assert "last_activity_at" not in snap
        assert "error" not in snap

    def test_snapshot_of_an_unknown_receipt_is_none(self):
        tracker = make_tracker()
        assert tracker.snapshot("rct-missing") is None


class TestSupersession:
    def test_a_newer_send_to_the_same_pane_supersedes_the_older_tracker(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.start("rct-2", "w1:p1", "sent")
        assert tracker.snapshot("rct-1")["state"] == "superseded"
        assert tracker.snapshot("rct-2")["state"] == "sent"
        assert [e["state"] for e in tracker.events] == [
            "sent", "superseded", "sent",
        ]

    def test_a_terminal_tracker_is_never_superseded(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "effective")  # born done
        tracker.start("rct-2", "w1:p1", "sent")
        assert tracker.snapshot("rct-1")["state"] == "done"
        assert tracker.snapshot("rct-2")["state"] == "sent"

    def test_different_panes_do_not_supersede_each_other(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.start("rct-2", "w1:p9", "sent")
        assert tracker.snapshot("rct-1")["state"] == "sent"
        assert tracker.snapshot("rct-2")["state"] == "sent"


class TestStateMachine:
    def test_working_seen_then_sustained_reaches_started_then_working(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["state"] == "started"
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["state"] == "working"
        # Sustained working: no further churn on the SSE stream.
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert [e["state"] for e in tracker.events] == ["sent", "started", "working"]

    def test_herdr_done_ends_the_tracker_done_once_work_was_seen(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"
        assert not tracker.has_active()

    def test_herdr_blocked_ends_the_tracker_blocked(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="blocked")}, {})
        assert tracker.snapshot("rct-1")["state"] == "blocked"

    def test_watcher_derived_error_ends_the_tracker_error_without_push(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe(
            {"w1:p1": make_agent(status="error")},
            {"w1:p1": {"name": "APIError", "message": "limit reached"}},
        )
        snap = tracker.snapshot("rct-1")
        assert snap["state"] == "error"
        assert snap["error"] == {"name": "APIError", "message": "limit reached"}
        # The watcher's announcement (SSE + herd push) already covers a
        # herdr-derived error: the tracker never double-notifies (S7).
        assert tracker.pushes == []

    def test_error_message_is_masked_at_the_boundary(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe(
            {"w1:p1": make_agent(status="error")},
            {"w1:p1": {
                "name": "APIError",
                "message": "token sk-ant-api03-abcdef123456 leaked",
            }},
        )
        event = tracker.events[-1]
        assert "sk-ant-api03" not in event["error"]["message"]
        assert tracker.snapshot("rct-1")["error"]["message"] == (
            event["error"]["message"]
        )

    def test_pane_absent_ends_the_tracker_pane_gone(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({}, {})
        assert tracker.snapshot("rct-1")["state"] == "pane_gone"
        assert tracker.events[-1]["state"] == "pane_gone"

    def test_idle_without_ever_working_keeps_waiting(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "sent"

    def test_idle_after_working_reads_as_done(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"

    def test_terminal_states_form_the_closed_set(self):
        assert TERMINAL_STATES == frozenset({
            "done", "blocked", "error", "no_start", "pane_gone",
            "superseded", "expired",
        })


class TestPreExistingStatus:
    """S1 correction: a status the pane ALREADY had when the send
    started is not completion evidence.

    Herdr keeps ``done`` until the pane is viewed, so a pane that never
    picked the send up still reports its pre-existing ``done`` on the
    watcher's next poll — the tracker must not complete on it. The
    pane's status is pinned at the tracker's first observation;
    completion needs work seen after the send, a status the pane
    REACHED after leaving its start status, or harness evidence.
    """

    def test_pre_existing_done_is_not_completion_then_no_start_fires(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "sent"
        clock.advance(NO_START_S)
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "no_start"
        assert [p["state"] for p in tracker.pushes] == ["no_start"]

    def test_pre_existing_idle_is_not_completion_either(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        clock.advance(NO_START_S)
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "no_start"

    def test_pre_existing_done_then_working_then_done_completes(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "sent"
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["state"] == "started"
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"
        assert not tracker.has_active()

    def test_pre_existing_idle_with_harness_evidence_completes(self):
        signals = FakeSignals(assistant_after=True)
        tracker = make_tracker(signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"

    def test_a_status_reached_after_leaving_the_start_completes(self):
        """A fast turn can skip every working poll: a pane that LEFT
        its start status and rests at done/idle completed a turn after
        the send (guards the correction against over-blocking)."""
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"

    def test_the_send_time_status_passed_in_at_start_measures_transitions(self):
        """The watcher's baseline — the status the send interrupted —
        is the accurate start status when the caller has it: a pane
        that was working at send time and done by the tracker's first
        poll completed a turn."""
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent", pane_status="working")
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"


# ── S3: thresholds ────────────────────────────────────────────────────────────


class TestNoStart:
    def test_fifteen_seconds_without_work_completes_as_no_start_with_push(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent")
        clock.advance(NO_START_S)
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "no_start"
        assert [(p["state"], p["error"]) for p in tracker.pushes] == [
            ("no_start", None),
        ]
        assert tracker.events[-1]["state"] == "no_start"

    def test_seen_working_prevents_no_start(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent")
        clock.advance(NO_START_S - 1.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        clock.advance(2 * NO_START_S)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["state"] == "working"

    def test_completion_evidence_prevents_no_start(self):
        clock = FakeClock()
        signals = FakeSignals(assistant_after=True)
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        clock.advance(NO_START_S + 5.0)
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"

    def test_no_start_waits_the_full_window(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent")
        clock.advance(NO_START_S - 0.1)
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "sent"


class TestStalled:
    def _working_tracker(self, signals, clock):
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        return tracker

    def test_three_minutes_without_store_activity_stalls_with_push(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=clock.t)
        tracker = self._working_tracker(signals, clock)
        clock.advance(STALLED_S + 1.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        snap = tracker.snapshot("rct-1")
        assert snap["state"] == "working"
        assert snap["stalled"] is True
        assert [(p["state"], p["error"]) for p in tracker.pushes] == [
            ("stalled", None),
        ]
        assert tracker.events[-1]["stalled"] is True

    def test_activity_resumes_and_clears_the_flag(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=clock.t)
        tracker = self._working_tracker(signals, clock)
        clock.advance(STALLED_S + 1.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["stalled"] is True
        signals.activity_at = clock.t  # fresh store activity lands now
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        snap = tracker.snapshot("rct-1")
        assert snap["stalled"] is False
        assert snap["state"] == "working"
        assert tracker.events[-1].get("stalled") is None  # cleared event

    def test_stalled_is_opencode_only(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock, signals=None)  # no harness seam
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        clock.advance(STALLED_S * 3)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["stalled"] is False
        assert tracker.pushes == []

    def test_no_activity_at_all_since_the_send_stalls_too(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=None)
        tracker = self._working_tracker(signals, clock)
        clock.advance(STALLED_S + 1.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["stalled"] is True


class TestHardCap:
    def test_thirty_minutes_ends_the_tracker_expired(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=clock.t)
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        # Keep it alive and active well past the cap.
        clock.advance(TRACK_CAP_S + 5.0)
        signals.activity_at = clock.t
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["state"] == "expired"
        assert not tracker.has_active()
        # The cap is terminal even for an unhealthy quiet tracker.
        assert TRACK_CAP_S == 1800.0

    def test_the_cap_outranks_stalled(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=None)
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        clock.advance(TRACK_CAP_S + 5.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert tracker.snapshot("rct-1")["state"] == "expired"


# ── S2: the opencode seam over a real store ──────────────────────────────────


def _store_db(tmp_path, session="ses_track000001"):
    db = tmp_path / "send-signals.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,"
        " time_created INT, time_updated INT, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT,"
        " session_id TEXT, time_created INT, time_updated INT, data TEXT)"
    )
    conn.commit()
    return conn, db


BASE_MS = 1_732_000_000_000


def _msg(conn, mid, session, at_ms, role, error=None, text=None):
    data = {"role": role}
    if error is not None:
        data["error"] = error
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
        (mid, session, at_ms, at_ms, json.dumps(data)),
    )


def _part(conn, pid, mid, session, at_ms, text="chunk"):
    conn.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
        (pid, mid, session, at_ms, at_ms, json.dumps({"type": "text", "text": text})),
    )


class TestOpencodeSeam:
    def test_activity_is_the_newest_row_update_after_the_send(self, tmp_path, monkeypatch):
        conn, db = _store_db(tmp_path)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        _msg(conn, "m1", "ses_track000001", BASE_MS + 1_000, "user")
        _msg(conn, "m2", "ses_track000001", BASE_MS + 60_000, "assistant")
        _part(conn, "m2-p1", "m2", "ses_track000001", BASE_MS + 90_000)
        conn.commit()
        conn.close()
        signals = default_harness_signals("opencode")
        read = signals.read("ses_track000001", since_s=BASE_MS / 1000.0)
        assert read["activity_at"] == pytest.approx((BASE_MS + 90_000) / 1000.0)

    def test_rows_before_the_send_do_not_count(self, tmp_path, monkeypatch):
        conn, db = _store_db(tmp_path)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        _msg(conn, "m1", "ses_track000001", BASE_MS - 50_000, "user")
        conn.commit()
        conn.close()
        read = default_harness_signals("opencode").read(
            "ses_track000001", since_s=BASE_MS / 1000.0
        )
        assert read["activity_at"] is None
        assert read["assistant_after"] is False

    def test_error_after_the_send_is_read_even_while_working(self, tmp_path, monkeypatch):
        conn, db = _store_db(tmp_path)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        _msg(conn, "m1", "ses_track000001", BASE_MS + 5_000, "user")
        _msg(
            conn, "m2", "ses_track000001", BASE_MS + 10_000, "assistant",
            error={"name": "APIError", "data": {"message": "usage limit reached"}},
        )
        conn.commit()
        conn.close()
        read = default_harness_signals("opencode").read(
            "ses_track000001", since_s=BASE_MS / 1000.0
        )
        assert read["error"] == {
            "name": "APIError", "message": "usage limit reached",
        }

    def test_message_aborted_error_is_filtered_like_read_session_error(self, tmp_path, monkeypatch):
        conn, db = _store_db(tmp_path)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        _msg(
            conn, "m1", "ses_track000001", BASE_MS + 5_000, "assistant",
            error={"name": "MessageAbortedError", "data": {"message": "aborted"}},
        )
        conn.commit()
        conn.close()
        read = default_harness_signals("opencode").read(
            "ses_track000001", since_s=BASE_MS / 1000.0
        )
        assert read["error"] is None

    def test_assistant_message_after_the_send_is_completion_evidence(self, tmp_path, monkeypatch):
        conn, db = _store_db(tmp_path)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        _msg(conn, "m1", "ses_track000001", BASE_MS + 1_000, "user")
        _msg(conn, "m2", "ses_track000001", BASE_MS + 2_000, "assistant")
        conn.commit()
        conn.close()
        read = default_harness_signals("opencode").read(
            "ses_track000001", since_s=BASE_MS / 1000.0
        )
        assert read["assistant_after"] is True

    def test_a_missing_store_reads_as_no_signals(self, tmp_path):
        read = default_harness_signals("opencode").read(
            "ses_track000001", since_s=BASE_MS / 1000.0
        )
        assert read == {
            "activity_at": None, "error": None, "assistant_after": False,
        }

    def test_other_harnesses_get_no_seam(self):
        assert default_harness_signals("opencode") is not None
        assert default_harness_signals("claude") is None
        assert default_harness_signals("") is None

    def test_a_store_error_while_herdr_says_working_ends_error_with_push(
        self, tmp_path, monkeypatch
    ):
        conn, db = _store_db(tmp_path)
        _msg(conn, "m1", "ses_track000001", BASE_MS + 5_000, "user")
        _msg(
            conn, "m2", "ses_track000001", BASE_MS + 10_000, "assistant",
            error={"name": "APIError", "data": {"message": "limit reached"}},
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        clock = FakeClock(BASE_MS / 1000.0 + 1.0)
        tracker = SendTracker(clock=clock)  # the default seam reads the env
        tracker.start("rct-1", "w1:p1", "sent", sent_at=BASE_MS / 1000.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        snap = tracker.snapshot("rct-1")
        assert snap["state"] == "error"
        assert snap["error"]["name"] == "APIError"

    def test_completion_at_idle_ends_done_without_ever_seeing_working(
        self, tmp_path, monkeypatch
    ):
        conn, db = _store_db(tmp_path)
        _msg(conn, "m1", "ses_track000001", BASE_MS + 1_000, "user")
        _msg(conn, "m2", "ses_track000001", BASE_MS + 2_000, "assistant")
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        clock = FakeClock(BASE_MS / 1000.0 + 3.0)
        tracker = SendTracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent", sent_at=BASE_MS / 1000.0)
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"


# ── S4: the bounded store and the SSE payload ────────────────────────────────


class TestBoundedStore:
    def test_the_registry_caps_itself_dropping_the_oldest(self):
        tracker = make_tracker()
        for i in range(70):
            tracker.start(f"rct-{i:03d}", f"w1:p{i}", "effective")
        assert len({e["receipt_id"] for e in tracker.events}) == 70
        # Every receipt still queryable within the cap, oldest gone.
        assert tracker.snapshot("rct-000") is None
        assert tracker.snapshot("rct-069")["state"] == "done"

    def test_terminal_trackers_stop_being_active(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        assert tracker.active_panes() == frozenset({"w1:p1"})
        tracker.observe({}, {})
        assert tracker.active_panes() == frozenset()
        assert not tracker.has_active()


class TestSSEPayload:
    def test_every_event_carries_the_contract_keys(self):
        clock = FakeClock()
        tracker = make_tracker(clock=clock)
        tracker.start("rct-1", "w1:p1", "sent", sent_at=clock.t - 2.5)
        event = tracker.events[0]
        assert event["type"] == SSE_EVENT_TYPE
        assert event == {
            "type": "send.track",
            "receipt_id": "rct-1",
            "pane_id": "w1:p1",
            "seq": 1,
            "state": "sent",
            "elapsed_s": 2.5,
        }

    def test_stalled_rides_as_a_flag_never_a_state_word(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=clock.t)
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        clock.advance(STALLED_S + 1.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        event = tracker.events[-1]
        assert event["state"] == "working"
        assert event["stalled"] is True

    def test_activity_carries_last_activity_at(self, tmp_path, monkeypatch):
        conn, db = _store_db(tmp_path)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        _part(conn, "p1", "m1", "ses_track000001", BASE_MS + 8_000)
        conn.commit()
        conn.close()
        clock = FakeClock(BASE_MS / 1000.0 + 1.0)
        events: list = []
        tracker = SendTracker(clock=clock, publish=events.append)
        tracker.start("rct-1", "w1:p1", "sent", sent_at=BASE_MS / 1000.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        event = events[-1]
        assert event["last_activity_at"] == pytest.approx(
            (BASE_MS + 8_000) / 1000.0
        )


class TestEventOrdering:
    """S4 correction: consumers can never end on a stale state.

    Events are computed under the registry lock in seq order, but the
    publish callback runs after it is released — a callback that blocks
    must not let a LATER state publish first (the phone would end on a
    stale card). Every payload and the receipt snapshot carry a
    per-receipt monotonically increasing ``seq``; delivery is
    order-preserving on the server and the PWA ignores lower seqs.
    """

    def test_seq_is_present_and_increasing_across_events_and_snapshots(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        seqs = [e["seq"] for e in tracker.events]
        assert seqs == [1, 2, 3]
        # The receipt track snapshot carries the same monotonic seq.
        assert tracker.snapshot("rct-1")["seq"] == seqs[-1]

    def test_a_blocked_publish_callback_cannot_reorder_events(self):
        entered = threading.Event()  # the 'sent' publish is inside its callback
        gate = threading.Event()     # ...and parked there
        published: list = []

        def publish(event):
            if event["state"] == "sent":
                entered.set()
                gate.wait(10)
            published.append(event)

        tracker = SendTracker(
            clock=FakeClock(), publish=publish,
            harness_signals=lambda kind: None,
        )
        starter = threading.Thread(
            target=lambda: tracker.start("rct-1", "w1:p1", "sent"))
        starter.start()
        assert entered.wait(5)  # the initial event is parked in publish

        observer = threading.Thread(target=lambda: tracker.observe(
            {"w1:p1": make_agent(status="working")}, {}))
        observer.start()
        time.sleep(0.3)  # the observer computed 'started'; it must queue behind
        gate.set()
        starter.join(5)
        observer.join(5)
        assert not starter.is_alive() and not observer.is_alive()

        seqs = [e["seq"] for e in published]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # strictly increasing
        assert [e["state"] for e in published] == ["sent", "started"]
        # The last published state equals the snapshot: no stale end.
        assert published[-1]["state"] == tracker.snapshot("rct-1")["state"]


# ── S7: push rules ────────────────────────────────────────────────────────────


class TestPushRules:
    def test_done_and_blocked_never_push(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.pushes == []

        tracker.start("rct-2", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="blocked")}, {})
        assert tracker.pushes == []

    def test_pane_gone_and_expired_never_push(self):
        tracker = make_tracker()
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({}, {})
        assert tracker.pushes == []

    def test_store_sourced_error_pushes_with_the_masked_message(self):
        clock = FakeClock()
        signals = FakeSignals(error={
            "name": "APIError",
            "message": "key sk-ant-api03-abcdef123456 failed",
        })
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert len(tracker.pushes) == 1
        push = tracker.pushes[0]
        assert push["state"] == "error"
        assert push["pane_id"] == "w1:p1"
        assert push["label"]  # the agent label reached the push copy
        assert "sk-ant-api03" not in push["error"]["message"]

    def test_a_stall_episode_pushes_once_not_per_tick(self):
        clock = FakeClock()
        signals = FakeSignals(activity_at=clock.t)
        tracker = make_tracker(clock=clock, signals=signals)
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        clock.advance(STALLED_S + 1.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        clock.advance(10.0)
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        assert [p["state"] for p in tracker.pushes] == ["stalled"]

    def test_a_push_hook_that_raises_never_breaks_observing(self):
        def boom(*args, **kwargs):
            raise RuntimeError("push down")

        clock = FakeClock()
        tracker = SendTracker(
            clock=clock,
            publish=lambda event: None,
            notify=boom,
            harness_signals=lambda kind: None,
        )
        tracker.start("rct-1", "w1:p1", "sent")
        clock.advance(NO_START_S)
        tracker.observe({"w1:p1": make_agent(status="idle")}, {})
        assert tracker.snapshot("rct-1")["state"] == "no_start"

    def test_a_publish_hook_that_raises_never_breaks_observing(self):
        def boom(event):
            raise RuntimeError("hub down")

        tracker = SendTracker(
            clock=FakeClock(), publish=boom,
            harness_signals=lambda kind: None,
        )
        tracker.start("rct-1", "w1:p1", "sent")
        tracker.observe({"w1:p1": make_agent(status="working")}, {})
        tracker.observe({"w1:p1": make_agent(status="done")}, {})
        assert tracker.snapshot("rct-1")["state"] == "done"
