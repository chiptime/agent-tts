"""Addressing, token pinning and sender identity for brain messages.

Every case runs against a fake mux (recorded AgentInfo rows) — no test
ever talks to a live pane.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.walky import WalkyRefusal, WalkyService

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

    def list_agents(self):
        if self.fail_list:
            raise RuntimeError("mux unreachable")
        return list(self.panes)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return self.screens.get(pane_id, "")

    def pane_run(self, pane_id, command):
        self.typed.append((pane_id, command))


def pane(pid, token, status="idle", harness="opencode", title="pane title"):
    return AgentInfo(
        pane_id=pid,
        agent=harness,
        status=status,
        session_kind="id" if token else "",
        session_value=token,
        cwd="/repo",
        title=title,
        focused=False,
    )


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
    svc = WalkyService(
        state_dir=tmp_path, mux=mux, clock=clock or Clock(), id_factory=ids()
    )
    return svc, mux


PANE_ENV = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1"}


# -- who ----------------------------------------------------------------------


def test_who_lists_panes_with_a_session_and_marks_you(tmp_path):
    svc, _ = build(
        tmp_path,
        [
            pane("w1:p1", "ses_a", status="idle", harness="opencode", title="fix auth"),
            pane("w2:p2", "ses_b", status="working", harness="claude", title="refactor"),
            pane("w3:p3", "", status="idle"),  # no agent session: not addressable
        ],
    )
    rows = svc.who(me_pane="w1:p1")
    assert [(r.address, r.harness, r.status, r.label, r.you) for r in rows] == [
        ("w1:p1#ses_a", "opencode", "idle", "fix auth", True),
        ("w2:p2#ses_b", "claude", "working", "refactor", False),
    ]


def test_who_without_a_calling_pane_marks_nobody(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    rows = svc.who(me_pane=None)
    assert [r.you for r in rows] == [False]


# -- sender identity ----------------------------------------------------------


def test_sender_resolved_from_pane_env(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a", harness="opencode", title="fix auth")])
    sender = svc.resolve_sender(PANE_ENV)
    assert sender.pane_id == "w1:p1"
    assert sender.address == "w1:p1#ses_a"
    assert sender.harness == "opencode"
    assert sender.label == "fix auth"


def test_sender_refused_outside_a_pane(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    with pytest.raises(WalkyRefusal) as exc:
        svc.resolve_sender({})
    assert "not inside a herdr pane" in str(exc.value).lower()


def test_sender_refused_when_pane_not_listed(tmp_path):
    svc, _ = build(tmp_path, [pane("w2:p2", "ses_b")])
    with pytest.raises(WalkyRefusal) as exc:
        svc.resolve_sender(PANE_ENV)
    assert "w1:p1" in str(exc.value)


# -- addressing ---------------------------------------------------------------


def test_send_requires_token_in_address(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_b")])
    sender = svc.resolve_sender(PANE_ENV)
    with pytest.raises(WalkyRefusal) as exc:
        svc.send(sender, "w2:p2", "hi")
    assert "who" in str(exc.value)  # the error points at the address source


def test_send_refuses_unknown_pane(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    sender = svc.resolve_sender(PANE_ENV)
    with pytest.raises(WalkyRefusal) as exc:
        svc.send(sender, "wX:pZ#ses_nope", "hi")
    assert "unknown pane" in str(exc.value).lower()


def test_send_refuses_replaced_session(tmp_path):
    svc, mux = build(tmp_path, [pane("w1:p1", "ses_a"), pane("w2:p2", "ses_old")])
    sender = svc.resolve_sender(PANE_ENV)
    mux.panes[1] = pane("w2:p2", "ses_new")  # the pane was replaced
    with pytest.raises(WalkyRefusal) as exc:
        svc.send(sender, "w2:p2#ses_old", "hi")
    assert "replaced" in str(exc.value).lower()


def test_send_refuses_self_send(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    sender = svc.resolve_sender(PANE_ENV)
    with pytest.raises(WalkyRefusal) as exc:
        svc.send(sender, "w1:p1#ses_a", "note to self")
    assert "yourself" in str(exc.value).lower()


def test_refused_send_writes_no_state(tmp_path):
    svc, _ = build(tmp_path, [pane("w1:p1", "ses_a")])
    sender = svc.resolve_sender(PANE_ENV)
    with pytest.raises(WalkyRefusal):
        svc.send(sender, "wX:pZ#ses_nope", "hi")
    assert not (tmp_path / "messages").exists()
