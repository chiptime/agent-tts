"""Notification coordinator: coalescing, cancellation, retraction (S5).

Pure decision logic with an injectable clock and sink — no transport,
no store. The window: transitions pile up for a few seconds, then ONE
summary per kind goes out; a pane handled inside the window never
reaches the phone; handled after delivery retracts (a ``cleared`` slot
or a replacing summary when others still need the operator).
"""

from __future__ import annotations

import pytest

from herdr_brain.webpush.coordinator import NotificationCoordinator

WINDOW = 2.0


@pytest.fixture()
def captured():
    events = []

    def sink(payload, topic):
        events.append({"payload": payload, "topic": topic})

    return events, sink


def _tick_at(clock):
    return lambda now: clock.__setitem__("now", now)


def test_single_blocked_agent_is_named_with_question(captured):
    events, sink = captured
    clock = {"now": 100.0}
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: clock["now"], sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿Desplegar a producción?", now=100.0)
    coord.tick(now=100.0 + WINDOW + 0.1)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["v"] == 1
    assert payload["kind"] == "blocked"
    assert payload["pane_id"] == "p1"
    assert "claude-1" in payload["title"]
    assert payload["question"] == "¿Desplegar a producción?"
    assert payload["body"] == "¿Desplegar a producción?"
    assert payload["tag"] == "brain-herd"
    assert events[0]["topic"] == "brain-herd"


def test_two_blocked_agents_coalesce_into_plural(captured):
    events, sink = captured
    clock = {"now": 100.0}
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: clock["now"], sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", now=100.0)
    coord.transition("p2", "codex-2", "blocked", question="¿B?", now=100.5)
    coord.tick(now=100.0 + WINDOW + 0.1)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["kind"] == "blocked"
    assert payload["pane_id"] is None
    assert "2" in payload["title"]
    assert "claude-1" in payload["body"] and "codex-2" in payload["body"]
    assert "question" not in payload  # plural summaries carry no single question


def test_handled_inside_window_cancels_the_notification(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", now=100.0)
    coord.handled("p1", "claude-1", now=100.5)
    coord.tick(now=100.0 + WINDOW + 1.0)
    assert events == []


def test_handled_after_delivery_retracts_with_cleared(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", now=100.0)
    coord.tick(now=100.0 + WINDOW + 0.1)
    coord.handled("p1", "claude-1", now=105.0)
    assert len(events) == 2
    cleared = events[1]["payload"]
    assert cleared["kind"] == "cleared"
    assert cleared["pane_id"] == "p1"
    assert cleared["tag"] == "brain-herd"
    assert events[1]["topic"] == "brain-herd"


def test_partial_retraction_replaces_the_summary(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", now=100.0)
    coord.transition("p2", "codex-2", "blocked", question="¿B?", now=100.5)
    coord.tick(now=100.0 + WINDOW + 0.1)
    coord.handled("p1", "claude-1", now=105.0)
    assert len(events) == 2
    replacement = events[1]["payload"]
    assert replacement["kind"] == "blocked"
    assert "codex-2" in replacement["body"]
    assert "claude-1" not in replacement["body"]
    # The last pane handled closes the slot entirely.
    coord.handled("p2", "codex-2", now=106.0)
    assert events[2]["payload"]["kind"] == "cleared"


def test_done_and_blocked_never_share_one_summary(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", now=100.0)
    coord.transition("p2", "codex-2", "done", text="lista la prueba", now=100.5)
    coord.tick(now=100.0 + WINDOW + 0.1)
    kinds = {e["payload"]["kind"] for e in events}
    assert kinds == {"blocked", "done"}
    done_payload = next(e["payload"] for e in events if e["payload"]["kind"] == "done")
    assert "codex-2" in done_payload["title"]
    assert done_payload["body"] == "lista la prueba"


def test_gate_adds_approve_reject_actions(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", gate_id="g-7", now=100.0)
    coord.tick(now=100.0 + WINDOW + 0.1)
    actions = events[0]["payload"]["actions"]
    assert [a["action"] for a in actions] == ["approve", "reject"]
    assert all(a["gate"] == "g-7" for a in actions)
    assert actions[0]["title"] == "Aprobar"
    assert actions[1]["title"] == "Rechazar"


def test_new_transition_after_flush_reopens_window(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.transition("p1", "claude-1", "blocked", question="¿A?", now=100.0)
    coord.tick(now=100.0 + WINDOW + 0.1)
    assert len(events) == 1
    coord.transition("p2", "codex-2", "done", text="fin", now=104.0)
    coord.tick(now=104.0 + WINDOW + 0.1)
    assert len(events) == 2


def test_tick_without_pending_sends_nothing(captured):
    events, sink = captured
    coord = NotificationCoordinator(window_s=WINDOW, clock=lambda: 100.0, sink=sink)
    coord.tick(now=200.0)
    assert events == []
