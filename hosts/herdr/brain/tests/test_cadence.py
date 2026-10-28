"""Adaptive cadence tests (doc 05 AC6, server side).

The watcher's single loop relaxes when nobody is watching and tightens
while a subscriber rides the SSE hub and a pane is blocked — no new timer
per client is ever created.
"""

from __future__ import annotations

import queue

import pytest

from herdr_brain.config import Settings
from herdr_brain.watcher import AgentWatcher, POLL_INTERVAL_S


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_agent(status="working", pane="w1:p1"):
    from herdr_brain.herdr import AgentInfo

    return AgentInfo(
        pane_id=pane, agent="opencode", status=status, session_kind="id",
        session_value="ses_cadence00001", cwd="/repo", title="T", focused=True,
    )


class StubHerdr:
    def __init__(self, agents):
        self._agents = agents

    def list_agents(self):
        return list(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return ""


def watcher(settings, agents, hub=None):
    stub = StubHerdr(agents)
    w = AgentWatcher(
        settings, herdr=stub, tts_renderer=None, hub=hub, clock=FakeClock()
    )
    return w


class TestHubSubscriberCount:
    def test_count_reflects_subscribe_and_unsubscribe(self):
        from herdr_brain.watcher import AnnouncementHub

        hub = AnnouncementHub()
        assert hub.subscriber_count() == 0
        sub_id, _ = hub.subscribe()
        assert hub.subscriber_count() == 1
        hub.unsubscribe(sub_id)
        assert hub.subscriber_count() == 0


class TestAdaptiveInterval:
    def test_next_interval_relaxes_without_attention(self, settings):
        w = watcher(settings, [make_agent("working")])
        w.poll_once()
        assert w.next_interval_s() == POLL_INTERVAL_S

    def test_next_interval_tightens_for_watched_blocked_pane(self, settings):
        from herdr_brain.watcher import AnnouncementHub

        hub = AnnouncementHub()
        hub.subscribe()
        w = watcher(settings, [make_agent("blocked")], hub=hub)
        w.poll_once()
        assert w.next_interval_s() < POLL_INTERVAL_S

    def test_blocked_without_subscribers_stays_relaxed(self, settings):
        from herdr_brain.watcher import AnnouncementHub

        hub = AnnouncementHub()  # no subscriber
        w = watcher(settings, [make_agent("blocked")], hub=hub)
        w.poll_once()
        assert w.next_interval_s() == POLL_INTERVAL_S

    def test_working_pane_with_subscribers_stays_relaxed(self, settings):
        from herdr_brain.watcher import AnnouncementHub

        hub = AnnouncementHub()
        hub.subscribe()
        w = watcher(settings, [make_agent("working")], hub=hub)
        w.poll_once()
        assert w.next_interval_s() == POLL_INTERVAL_S

    def test_attention_decays_after_the_window(self, settings):
        from herdr_brain.watcher import ATTENTION_WINDOW_S, AnnouncementHub

        hub = AnnouncementHub()
        hub.subscribe()
        w = watcher(settings, [make_agent("blocked")], hub=hub)
        w.poll_once()
        assert w.next_interval_s() < POLL_INTERVAL_S
        # Simulate the attention window elapsing with no further signal.
        w._last_attention = w._clock() - ATTENTION_WINDOW_S - 1
        assert w.next_interval_s() == POLL_INTERVAL_S
