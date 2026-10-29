"""Watcher hook wiring for Web Push and brain messages (S5/S13).

The watcher's poll is the one existing timer; the notifier is an
OPTIONAL callback so the watcher's own tests keep constructing it bare.
Contract pinned here:

- one ``tick(panes=...)`` per successful poll, with a
  ``(pane_id, label, last_activity)`` row per live pane;
- ``transition(...)`` exactly once per ANNOUNCED transition (same
  decision site as the SSE hub — debounced repeats stay silent) with the
  blocking question the digest already extracted;
- ``handled(...)`` when a pane LEAVES blocked/done (state change, not
  announcement: it also fires for a debounced or vanished pane, because
  the push slot must close either way);
- a raising notifier never breaks polling: the announcements still
  publish and the poll still returns them.
"""

from __future__ import annotations

import pytest

from herdr_brain.watcher import AgentWatcher

from tests.test_watcher import FakeClock, fake_tts, make_agent


class RecordingNotifier:
    """Test double for the server's fan-out adapter."""

    def __init__(self, fail_in=None):
        self.transitions = []
        self.handled_panes = []
        self.ticks = []
        self.fail_in = fail_in or set()

    def transition(self, pane_id, label, status, text=None, question=None,
                   gate_id=None):
        if "transition" in self.fail_in:
            raise RuntimeError("push backend down")
        self.transitions.append(
            {
                "pane_id": pane_id,
                "label": label,
                "status": status,
                "text": text,
                "question": question,
                "gate_id": gate_id,
            }
        )

    def handled(self, pane_id, label=None):
        if "handled" in self.fail_in:
            raise RuntimeError("push backend down")
        self.handled_panes.append(pane_id)

    def tick(self, panes=()):
        if "tick" in self.fail_in:
            raise RuntimeError("push backend down")
        self.ticks.append(list(panes))


def make_watcher(settings, stub, notifier, clock=None):
    return AgentWatcher(
        settings,
        herdr=stub,
        tts_renderer=fake_tts(),
        clock=clock or FakeClock(),
        notifier=notifier,
    )


@pytest.fixture
def notifier():
    return RecordingNotifier()


class TestTick:
    def test_one_tick_per_poll_with_live_pane_rows(self, settings, make_stub, notifier):
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier)
        watcher.poll_once()  # baseline
        assert len(notifier.ticks) == 1
        rows = notifier.ticks[0]
        assert len(rows) == 1
        pane_id, label, last_activity = rows[0]
        assert pane_id == "w1:p1"
        assert label == "opencode repo"
        assert last_activity > 0

    def test_working_pane_refreshes_its_last_activity(self, settings, make_stub, notifier):
        clock = FakeClock()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier, clock=clock)
        watcher.poll_once()
        first = notifier.ticks[0][0][2]
        clock.advance(12.0)
        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        second = notifier.ticks[1][0][2]
        assert second > first

    def test_idle_pane_freezes_its_last_activity(self, settings, make_stub, notifier):
        clock = FakeClock()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier, clock=clock)
        watcher.poll_once()
        working_stamp = notifier.ticks[0][0][2]
        clock.advance(20.0)
        stub._agents = [make_agent(status="idle")]
        watcher.poll_once()
        # The working→idle CHANGE is activity (the agent worked until
        # now) — the stamp moves to the change, then FREEZES.
        idle_stamp = notifier.ticks[1][0][2]
        assert idle_stamp == working_stamp + 20.0
        clock.advance(30.0)
        stub._agents = [make_agent(status="idle")]
        watcher.poll_once()
        assert notifier.ticks[2][0][2] == idle_stamp

    def test_no_tick_when_the_agent_list_is_unreachable(self, settings, make_stub, notifier):
        class DeadStub:
            def list_agents(self):
                raise RuntimeError("herdr down")

        watcher = make_watcher(settings, DeadStub(), notifier)
        assert watcher.poll_once() == []
        assert notifier.ticks == []


class TestTransitions:
    def test_blocked_transition_carries_the_blocking_question(self, settings, make_stub, notifier):
        stub = make_stub(
            agents=[make_agent(status="working")],
            screen="¿Seguimos con el despliegue? (y/n)",
        )
        watcher = make_watcher(settings, stub, notifier)
        watcher.poll_once()  # baseline

        stub._agents = [make_agent(status="blocked")]
        produced = watcher.poll_once()

        assert len(produced) == 1
        assert len(notifier.transitions) == 1
        call = notifier.transitions[0]
        assert call["pane_id"] == "w1:p1"
        assert call["status"] == "blocked"
        assert call["text"] == produced[0]["text"]
        # The blocking question is the digest's pending excerpt, not the
        # prefixed announcement text.
        assert call["question"]
        assert call["question"] in call["text"]
        assert call["gate_id"] is None

    def test_done_transition_has_text_and_no_question(self, settings, make_stub, notifier):
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier)
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        watcher.poll_once()

        call = notifier.transitions[0]
        assert call["status"] == "done"
        assert call["question"] is None
        assert call["text"]

    def test_debounced_repeat_stays_silent(self, settings, make_stub, notifier):
        clock = FakeClock()
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier, clock=clock)
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        watcher.poll_once()
        assert len(notifier.transitions) == 1

        # leave and re-enter inside the debounce window: no new announce
        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        clock.advance(10.0)
        stub._agents = [make_agent(status="done")]
        watcher.poll_once()
        assert len(notifier.transitions) == 1

    def test_handled_when_the_pane_leaves_blocked(self, settings, make_stub, notifier):
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier)
        watcher.poll_once()

        stub._agents = [make_agent(status="blocked")]
        watcher.poll_once()
        assert notifier.handled_panes == []

        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        assert notifier.handled_panes == ["w1:p1"]

    def test_handled_when_a_blocked_pane_closes(self, settings, make_stub, notifier):
        stub = make_stub(agents=[make_agent(status="blocked")])
        watcher = make_watcher(settings, stub, notifier)
        watcher.poll_once()  # baseline on a blocked pane

        stub._agents = []  # the pane vanished while needing attention
        watcher.poll_once()
        assert notifier.handled_panes == ["w1:p1"]


class TestResilience:
    @pytest.mark.parametrize("fail_in", [{"transition"}, {"handled"}, {"tick"}])
    def test_a_raising_notifier_never_breaks_polling(
        self, settings, make_stub, fail_in
    ):
        notifier = RecordingNotifier(fail_in=fail_in)
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = make_watcher(settings, stub, notifier)
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        produced = watcher.poll_once()
        assert len(produced) == 1

        stub._agents = [make_agent(status="working")]
        produced = watcher.poll_once()
        assert produced == []  # polling kept cycling: this is a fresh baseline

    def test_watcher_without_a_notifier_behaves_as_before(self, settings, make_stub):
        stub = make_stub(agents=[make_agent(status="working")])
        watcher = AgentWatcher(
            settings, herdr=stub, tts_renderer=fake_tts(), clock=FakeClock()
        )
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1
