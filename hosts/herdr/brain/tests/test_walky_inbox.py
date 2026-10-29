"""Inbox read/mark, replies and cancel semantics."""

from __future__ import annotations

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.walky import MessageNotFound, WalkyRefusal, WalkyService


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
        self.typed: list = []

    def list_agents(self):
        return list(self.panes)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return ""

    def pane_run(self, pane_id, command):
        self.typed.append((pane_id, command))


def pane(pid, token, status="idle", harness="opencode", title="pane title"):
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
    mux = FakeMux(panes)
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=clock or Clock(), id_factory=ids())
    return svc, mux


def env(pid):
    return {"HERDR_ENV": "1", "HERDR_PANE_ID": pid}


# -- inbox ----------------------------------------------------------------------


def test_inbox_lists_unread_then_marks_read(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    alice = svc.resolve_sender(env("w1:p1"))
    svc.send(alice, "w2:p2#ses_b", "first")
    svc.send(alice, "w2:p2#ses_b", "second")

    listed = svc.inbox("w2:p2")
    assert [m.body for m in listed] == ["first", "second"]
    assert svc.inbox("w2:p2") == []  # both marked read by the listing


def test_inbox_single_id_reads_full_body(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    alice = svc.resolve_sender(env("w1:p1"))
    svc.send(alice, "w2:p2#ses_b", "the full\nbody")
    msg = svc.inbox("w2:p2", "bm_0000000001")
    assert msg.body == "the full\nbody"
    assert svc.inbox("w2:p2") == []


def test_inbox_only_shows_my_pane(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    alice = svc.resolve_sender(env("w1:p1"))
    svc.send(alice, "w2:p2#ses_b", "for bob")
    assert svc.inbox("w1:p1") == []
    with pytest.raises(MessageNotFound):
        svc.inbox("w1:p1", "bm_0000000001")


def test_status_of_unknown_id_raises(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    with pytest.raises(MessageNotFound):
        svc.status("bm_deadbeef00")


# -- replies ---------------------------------------------------------------------


def test_reply_goes_to_the_sender_and_carries_the_chain(tmp_path):
    svc, mux = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    alice = svc.resolve_sender(env("w1:p1"))
    bob = svc.resolve_sender(env("w2:p2"))
    original = svc.send(alice, "w2:p2#ses_b", "question for bob")

    answer = svc.reply(bob, original.id, "here is the answer")
    assert answer.hops == 1
    assert answer.to_pane == "w1:p1"
    assert svc.status(answer.id).state == "typed"
    assert mux.typed[1][0] == "w1:p1"


def test_reply_only_to_messages_addressed_to_me(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    alice = svc.resolve_sender(env("w1:p1"))
    bob = svc.resolve_sender(env("w2:p2"))
    original = svc.send(alice, "w2:p2#ses_b", "for bob only")
    with pytest.raises(WalkyRefusal) as exc:
        svc.reply(alice, original.id, "alice butting in")
    assert "your inbox" in str(exc.value).lower()


def test_reply_to_operator_message_is_refused(tmp_path):
    svc, _ = build(tmp_path, [pane("w2:p2", "ses_b")])
    svc.operator_send("phone-altan", "w2:p2#ses_b", "from the operator")
    bob = svc.resolve_sender(env("w2:p2"))
    with pytest.raises(WalkyRefusal) as exc:
        svc.reply(bob, "bm_0000000001", "trying to answer")
    assert "operator" in str(exc.value).lower()


# -- cancel ----------------------------------------------------------------------


def test_cancel_own_held_message(tmp_path):
    clock = Clock()
    svc, mux = build(
        tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working")], clock=clock
    )
    alice = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(alice, "w2:p2#ses_b", "take this back")
    outcome = svc.cancel("w1:p1", msg.id)
    assert "canceled" in outcome.lower()

    mux.panes[1] = pane("w2:p2", "ses_b", status="idle")
    clock.advance(10)
    svc.tick()
    assert svc.status(msg.id).state == "canceled"
    assert mux.typed == []


def test_cancel_after_typing_is_refused(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    alice = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(alice, "w2:p2#ses_b", "already delivered")
    with pytest.raises(WalkyRefusal) as exc:
        svc.cancel("w1:p1", msg.id)
    assert "already typed" in str(exc.value).lower()


def test_cancel_only_your_own_message(tmp_path):
    svc, _ = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b", status="working"), pane("w3:p3", "ses_c")],
    )
    alice = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(alice, "w2:p2#ses_b", "not yours")
    with pytest.raises(WalkyRefusal) as exc:
        svc.cancel("w3:p3", msg.id)
    assert "your own" in str(exc.value).lower()


def test_cancel_unknown_id_raises(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    with pytest.raises(MessageNotFound):
        svc.cancel("w1:p1", "bm_deadbeef00")
