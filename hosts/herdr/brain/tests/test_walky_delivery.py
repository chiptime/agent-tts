"""The hold/deliver/timeout state machine, driven by a fake clock and mux.

Statuses under test: queued, held, typed, waiting_in_inbox, refused,
uncertain. No live pane is ever touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.mux import MuxRefusal
from herdr_brain.walky import WalkyService

FIXTURES = Path(__file__).parent / "fixtures" / "walky"


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeMux:
    def __init__(self, panes) -> None:
        self.panes = list(panes)
        self.screens: dict = {}
        self.typed: list = []
        self.fail_list = False
        self.fail_run = False
        self.unsupported_run = False

    def list_agents(self):
        if self.fail_list:
            raise RuntimeError("mux unreachable")
        return list(self.panes)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return self.screens.get(pane_id, "")

    def pane_run(self, pane_id, command):
        if self.unsupported_run:
            raise MuxRefusal("unsupported", "pane_run", "read-only multiplexer")
        if self.fail_run:
            raise HerdrError("herdr exited with 1: pane write failed")
        self.typed.append((pane_id, command))


def pane(pid, token, status="idle", harness="opencode", title="fix auth"):
    return AgentInfo(pid, harness, status, "id" if token else "", token, "/repo", title, False)


def ids(start=1):
    n = start

    def factory():
        nonlocal n
        mid = f"bm_{n:010d}"
        n += 1
        return mid

    return factory


def build(tmp_path, panes, clock=None):
    clock = clock or Clock()
    mux = FakeMux(panes)
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=clock, id_factory=ids())
    return svc, mux, clock


def env(pid):
    return {"HERDR_ENV": "1", "HERDR_PANE_ID": pid}


# -- happy path: typed header, exact text --------------------------------------


def test_idle_receiver_gets_the_exact_header_line(tmp_path):
    svc, mux, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "the body stays in the inbox")
    assert msg.state == "typed"
    assert mux.typed == [
        (
            "w2:p2",
            '[brain-msg bm_0000000001] from agent w1:p1#ses_a (opencode, fix auth): '
            'read it with: herdr-brain msg inbox bm_0000000001 · reply: '
            'herdr-brain msg reply bm_0000000001 "<text>"',
        )
    ]


def test_operator_message_header_carries_the_body(tmp_path):
    svc, mux, _ = build(tmp_path, [pane("w2:p2", "ses_b")])
    msg = svc.operator_send("phone-altan", "w2:p2#ses_b", "dinner in ten minutes")
    assert msg.state == "typed"
    assert mux.typed == [
        ("w2:p2", "[brain-msg bm_0000000001] from operator (phone-altan): dinner in ten minutes")
    ]


def test_multiline_body_types_one_line_but_stores_verbatim(tmp_path):
    svc, mux, _ = build(tmp_path, [pane("w2:p2", "ses_b")])
    msg = svc.operator_send("phone", "w2:p2#ses_b", "line one\nline two\nline three")
    assert msg.state == "typed"
    assert mux.typed[0][1].count("\n") == 0
    stored = svc.inbox("w2:p2")[0]
    assert stored.body == "line one\nline two\nline three"


def test_body_is_never_typed_for_agent_senders(tmp_path):
    svc, mux, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    sender = svc.resolve_sender(env("w1:p1"))
    svc.send(sender, "w2:p2#ses_b", "SECRET-PHRASE-NOT-FOR-SCREEN")
    assert "SECRET-PHRASE-NOT-FOR-SCREEN" not in mux.typed[0][1]


# -- hold while busy or drafting ------------------------------------------------


def test_working_receiver_holds_until_idle(tmp_path):
    svc, mux, clock = build(
        tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")]
    )
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    assert msg.state == "held"
    assert mux.typed == []

    mux.panes[1] = pane("w2:p2", "ses_b", status="idle")
    clock.advance(30)
    changed = svc.tick()
    assert [m.id for m in changed] == [msg.id]
    assert svc.status(msg.id).state == "typed"
    assert len(mux.typed) == 1


def test_blocked_receiver_holds(tmp_path):
    svc, mux, _ = build(
        tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="blocked")]
    )
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    assert msg.state == "held"
    assert mux.typed == []


def test_draft_in_the_input_box_holds_even_when_idle(tmp_path):
    svc, mux, clock = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    mux.screens["w2:p2"] = (FIXTURES / "screen-idle-draft.txt").read_text()
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    assert msg.state == "held"

    mux.screens["w2:p2"] = (FIXTURES / "screen-idle-empty.txt").read_text()
    clock.advance(5)
    svc.tick()
    assert svc.status(msg.id).state == "typed"


def test_empty_input_box_does_not_hold(tmp_path):
    svc, mux, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    mux.screens["w2:p2"] = (FIXTURES / "screen-idle-empty.txt").read_text()
    sender = svc.resolve_sender(env("w1:p1"))
    assert svc.send(sender, "w2:p2#ses_b", "ping").state == "typed"


# -- hold deadline: 60 minutes, then waiting_in_inbox -----------------------------


def test_hold_times_out_after_60_minutes(tmp_path):
    svc, mux, clock = build(
        tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")]
    )
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "read this later")
    clock.advance(3600)
    svc.tick()
    assert svc.status(msg.id).state == "waiting_in_inbox"
    assert mux.typed == []

    # Terminal state: later ticks never type it, even once idle.
    mux.panes[1] = pane("w2:p2", "ses_b", status="idle")
    clock.advance(5)
    svc.tick()
    assert svc.status(msg.id).state == "waiting_in_inbox"
    assert mux.typed == []
    assert svc.inbox("w2:p2")[0].body == "read this later"


def test_hold_just_inside_the_deadline_still_delivers(tmp_path):
    svc, mux, clock = build(
        tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")]
    )
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "still deliverable")
    clock.advance(3599)
    mux.panes[1] = pane("w2:p2", "ses_b", status="idle")
    svc.tick()
    assert svc.status(msg.id).state == "typed"


# -- refusals at delivery time ----------------------------------------------------


def test_closed_pane_is_refused_at_delivery(tmp_path):
    svc, mux, clock = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")])
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    mux.panes.pop()  # the pane closed while held
    clock.advance(10)
    svc.tick()
    assert svc.status(msg.id).state == "refused"
    assert "closed" in (svc.status(msg.id).reason or "").lower()


def test_replaced_session_is_refused_at_delivery(tmp_path):
    svc, mux, clock = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")])
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    mux.panes[1] = pane("w2:p2", "ses_new", status="idle")
    clock.advance(10)
    svc.tick()
    assert svc.status(msg.id).state == "refused"
    assert "replaced" in (svc.status(msg.id).reason or "").lower()


# -- uncertain outcomes never retype -----------------------------------------------


def test_pane_run_error_leaves_uncertain_and_never_retries(tmp_path):
    svc, mux, clock = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    mux.fail_run = True
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    assert msg.state == "uncertain"

    mux.fail_run = False
    clock.advance(10)
    svc.tick()
    assert svc.status(msg.id).state == "uncertain"
    assert mux.typed == []


def test_unsupported_write_is_refused(tmp_path):
    svc, mux, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    mux.unsupported_run = True
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    assert msg.state == "refused"


def test_mux_outage_keeps_the_message_queued(tmp_path):
    svc, mux, clock = build(
        tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")]
    )
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "ping")
    mux.fail_list = True
    mux.panes[1] = pane("w2:p2", "ses_b", status="idle")
    clock.advance(10)
    svc.tick()  # cannot even list: state must survive untouched
    assert svc.status(msg.id).state == "held"
    mux.fail_list = False
    svc.tick()
    assert svc.status(msg.id).state == "typed"


# -- tick returns the messages it advanced ----------------------------------------


def test_tick_returns_only_changed_messages(tmp_path):
    svc, mux, clock = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")])
    sender = svc.resolve_sender(env("w1:p1"))
    first = svc.send(sender, "w2:p2#ses_b", "one")
    second = svc.send(sender, "w2:p2#ses_b", "two")
    mux.panes[1] = pane("w2:p2", "ses_b", status="idle")
    clock.advance(1)
    changed = svc.tick()
    assert sorted(m.id for m in changed) == sorted([first.id, second.id])
    assert svc.tick() == []  # nothing left in flight
