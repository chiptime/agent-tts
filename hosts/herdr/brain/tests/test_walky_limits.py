"""Body size, sender/pair rate windows and reply-chain limits."""

from __future__ import annotations

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.walky import MAX_BODY_CHARS, WalkyRefusal, WalkyService


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


def build(tmp_path, panes, clock):
    mux = FakeMux(panes)
    svc = WalkyService(state_dir=tmp_path, mux=mux, clock=clock, id_factory=ids())
    return svc, mux


def env(pid):
    return {"HERDR_ENV": "1", "HERDR_PANE_ID": pid}


# -- body size ----------------------------------------------------------------


def test_body_at_limit_is_accepted(tmp_path):
    clock = Clock()
    svc, _ = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")],
        clock,
    )
    sender = svc.resolve_sender(env("w1:p1"))
    msg = svc.send(sender, "w2:p2#ses_b", "x" * MAX_BODY_CHARS)
    assert msg.state == "typed"


def test_body_over_limit_is_refused(tmp_path):
    clock = Clock()
    svc, _ = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")],
        clock,
    )
    sender = svc.resolve_sender(env("w1:p1"))
    with pytest.raises(WalkyRefusal) as exc:
        svc.send(sender, "w2:p2#ses_b", "x" * (MAX_BODY_CHARS + 1))
    assert str(MAX_BODY_CHARS) in str(exc.value)


def test_empty_body_is_refused(tmp_path):
    clock = Clock()
    svc, _ = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")],
        clock,
    )
    sender = svc.resolve_sender(env("w1:p1"))
    with pytest.raises(WalkyRefusal):
        svc.send(sender, "w2:p2#ses_b", "   ")


# -- sender window: 30 per 10 minutes ------------------------------------------


def test_sender_limit_30_per_10_minutes(tmp_path):
    clock = Clock()
    panes = [pane("w1:p1", "ses_a")] + [
        pane(f"w9:p{i}", f"ses_r{i}") for i in range(6)  # 6 receivers: pair cap never bites
    ]
    svc, mux = build(tmp_path, panes, clock)
    sender = svc.resolve_sender(env("w1:p1"))

    sent = 0
    for _round in range(5):
        for i in range(6):
            svc.send(sender, f"w9:p{i}#ses_r{i}", "hi")
            sent += 1
    assert sent == 30

    with pytest.raises(WalkyRefusal) as exc:
        svc.send(sender, "w9:p0#ses_r0", "one too many")
    assert "30" in str(exc.value)

    clock.advance(601)  # window rotated: the oldest sends left it
    msg = svc.send(sender, "w9:p0#ses_r0", "after the window")
    assert msg.state == "typed"


# -- pair window: 6 per 10 minutes, both directions ----------------------------


def test_pair_limit_counts_both_directions(tmp_path):
    clock = Clock()
    svc, mux = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")],
        clock,
    )
    alice = svc.resolve_sender(env("w1:p1"))
    bob = svc.resolve_sender(env("w2:p2"))

    for _ in range(6):
        svc.send(alice, "w2:p2#ses_b", "hi bob")
    with pytest.raises(WalkyRefusal) as exc:
        svc.send(alice, "w2:p2#ses_b", "seventh")
    assert "6" in str(exc.value)

    # The trade is capped as a pair: the reverse direction is the same pair.
    with pytest.raises(WalkyRefusal):
        svc.reply(bob, svc.inbox("w2:p2")[0].id, "hi alice")

    clock.advance(601)
    svc.send(alice, "w2:p2#ses_b", "fresh window")


def test_pair_limit_is_per_pair(tmp_path):
    clock = Clock()
    svc, _ = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b"), pane("w3:p3", "ses_c")],
        clock,
    )
    alice = svc.resolve_sender(env("w1:p1"))
    for _ in range(6):
        svc.send(alice, "w2:p2#ses_b", "hi bob")
    # A different pair is untouched.
    assert svc.send(alice, "w3:p3#ses_c", "hi cara").state == "typed"


# -- reply chains stop after 4 hops ---------------------------------------------


def test_reply_chain_stops_after_4_hops(tmp_path):
    clock = Clock()
    svc, _ = build(
        tmp_path,
        [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")],
        clock,
    )
    alice = svc.resolve_sender(env("w1:p1"))
    bob = svc.resolve_sender(env("w2:p2"))

    msg = svc.send(alice, "w2:p2#ses_b", "start")  # hop 0
    assert msg.hops == 0
    for expected_hops in (1, 2, 3, 4):
        parent = svc.inbox("w2:p2")[0] if expected_hops % 2 == 1 else svc.inbox("w1:p1")[0]
        responder = bob if expected_hops % 2 == 1 else alice
        msg = svc.reply(responder, parent.id, f"hop {expected_hops}")
        assert msg.hops == expected_hops

    parent = svc.inbox("w2:p2")[0]  # bob holds the hop-4 message
    with pytest.raises(WalkyRefusal) as exc:
        svc.reply(bob, parent.id, "hop 5")
    assert "chain" in str(exc.value).lower()

    # A fresh send is still fine: only the chain is capped.
    assert svc.send(bob, "w1:p1#ses_a", "new topic").state == "typed"
