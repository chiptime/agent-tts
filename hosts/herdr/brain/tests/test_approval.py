"""Unit tests for the approval gate store (lazy expiry, approve-once)."""

from __future__ import annotations

import threading

from herdr_brain.approval import (
    APPROVED,
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REPROMPT,
    EXPIRED,
    PROPOSED,
    REJECTED,
    SUPERSEDED,
    ApprovalGateStore,
    SEND_TO_SESSION,
)
from herdr_brain.config import DEFAULT_APPROVAL_TIMEOUT_S, load_settings


class FakeClock:
    """Deterministic clock: time passes only when the test advances it."""

    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestApprovalGateStore:
    def test_propose_freezes_proposed_gate(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose(
            "s1",
            "arregla el bug del login",
            timeout_ms=300_000,
            pane_id="3",
            agent="opencode",
        )
        assert gate.state == PROPOSED
        assert gate.session_id == "s1"
        assert gate.tool == SEND_TO_SESSION
        assert gate.reprompt_count == 0
        assert gate.action.text == "arregla el bug del login"
        assert gate.action.timeout_ms == 300_000
        assert gate.action.pane_id == "3"
        assert gate.action.agent == "opencode"

    def test_gate_id_is_short_hex(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        assert len(gate.gate_id) == 12
        int(gate.gate_id, 16)  # must be hex

    def test_propose_normalizes_session_id(self):
        store = ApprovalGateStore(clock=FakeClock())
        assert store.propose(None, "one").session_id == "default"
        assert store.propose("", "two").session_id == "default"
        assert store.propose("  s1  ", "three").session_id == "s1"
        assert store.normalize(None) == "default"

    def test_get_returns_gate_by_id(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        assert store.get(gate.gate_id).gate_id == gate.gate_id

    def test_get_unknown_gate_returns_none(self):
        store = ApprovalGateStore(clock=FakeClock())
        assert store.get("nope") is None

    def test_current_tracks_live_gate(self):
        store = ApprovalGateStore(clock=FakeClock())
        assert store.current("s1") is None
        gate = store.propose("s1", "hola")
        assert store.current("s1").gate_id == gate.gate_id
        assert store.current(None) is None  # sessions are isolated

    def test_resolve_approves_once(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        approved, applied = store.resolve(gate.gate_id, DECISION_APPROVE)
        assert approved.state == APPROVED
        assert applied is True
        # Second approve must not report as executed again.
        again, applied_again = store.resolve(gate.gate_id, DECISION_APPROVE)
        assert again.state == APPROVED
        assert applied_again is False
        assert again.reprompt_count == 0
        # Reject after approve is equally inert.
        rejected, applied_reject = store.resolve(gate.gate_id, DECISION_REJECT)
        assert rejected.state == APPROVED
        assert applied_reject is False

    def test_resolve_rejects_once(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        rejected, applied = store.resolve(gate.gate_id, DECISION_REJECT)
        assert rejected.state == REJECTED
        assert applied is True
        approved, applied_late = store.resolve(gate.gate_id, DECISION_APPROVE)
        assert approved.state == REJECTED
        assert applied_late is False
        assert store.current("s1") is None

    def test_resolve_unknown_gate(self):
        store = ApprovalGateStore(clock=FakeClock())
        assert store.resolve("nope", DECISION_APPROVE) == (None, False)

    def test_resolve_invalid_decision_raises(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        try:
            store.resolve(gate.gate_id, "maybe")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert store.get(gate.gate_id).state == PROPOSED  # unchanged

    def test_reprompt_increments_and_keeps_proposed(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        first, applied = store.resolve(gate.gate_id, DECISION_REPROMPT)
        assert first.state == PROPOSED
        assert first.reprompt_count == 1
        assert applied is True
        second, _ = store.resolve(gate.gate_id, DECISION_REPROMPT)
        assert second.reprompt_count == 2
        assert second.state == PROPOSED

    def test_reprompt_on_terminal_gate_is_noop(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        store.resolve(gate.gate_id, DECISION_APPROVE)
        snapshot, applied = store.resolve(gate.gate_id, DECISION_REPROMPT)
        assert snapshot.state == APPROVED
        assert snapshot.reprompt_count == 0
        assert applied is False

    def test_lazy_expiry_on_get(self):
        clock = FakeClock()
        store = ApprovalGateStore(timeout_s=60, clock=clock)
        gate = store.propose("s1", "hola")
        clock.advance(60)
        assert store.get(gate.gate_id).state == PROPOSED  # boundary: still live
        clock.advance(0.001)
        assert store.get(gate.gate_id).state == EXPIRED

    def test_lazy_expiry_honors_configured_timeout(self):
        clock = FakeClock()
        store = ApprovalGateStore(timeout_s=5, clock=clock)
        gate = store.propose("s1", "hola")
        clock.advance(5.5)
        assert store.current("s1") is None
        assert store.get(gate.gate_id).state == EXPIRED

    def test_expired_gate_cannot_be_approved(self):
        clock = FakeClock()
        store = ApprovalGateStore(timeout_s=60, clock=clock)
        gate = store.propose("s1", "hola")
        clock.advance(61)
        snapshot, applied = store.resolve(gate.gate_id, DECISION_APPROVE)
        assert snapshot.state == EXPIRED
        assert applied is False  # safe direction: never execute

    def test_patch_resets_created_at_and_updates_text(self):
        clock = FakeClock()
        store = ApprovalGateStore(timeout_s=60, clock=clock)
        gate = store.propose("s1", "texto mal escuchado")
        clock.advance(50)
        patched = store.patch(gate.gate_id, "texto corregido")
        assert patched.action.text == "texto corregido"
        assert patched.created_at == clock.now  # timer restarted at patch time
        # 50 s already passed since the original propose; only 10 more since
        # the patch — the window must count from the patch, not the propose.
        clock.advance(59)
        assert store.get(gate.gate_id).state == PROPOSED
        clock.advance(2)
        assert store.get(gate.gate_id).state == EXPIRED

    def test_patch_on_approved_gate_returns_none(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        store.resolve(gate.gate_id, DECISION_APPROVE)
        assert store.patch(gate.gate_id, "otra cosa") is None
        assert store.get(gate.gate_id).action.text == "hola"

    def test_patch_on_expired_gate_returns_none(self):
        clock = FakeClock()
        store = ApprovalGateStore(timeout_s=60, clock=clock)
        gate = store.propose("s1", "hola")
        clock.advance(61)
        assert store.patch(gate.gate_id, "tarde") is None
        assert store.get(gate.gate_id).action.text == "hola"

    def test_patch_unknown_gate_returns_none(self):
        store = ApprovalGateStore(clock=FakeClock())
        assert store.patch("nope", "hola") is None

    def test_new_propose_supersedes_live_gate(self):
        store = ApprovalGateStore(clock=FakeClock())
        first = store.propose("s1", "uno")
        second = store.propose("s1", "dos")
        assert store.get(first.gate_id).state == SUPERSEDED
        assert store.current("s1").gate_id == second.gate_id
        # A superseded gate can no longer be approved.
        snapshot, applied = store.resolve(first.gate_id, DECISION_APPROVE)
        assert snapshot.state == SUPERSEDED
        assert applied is False

    def test_propose_after_window_elapsed_expires_old_gate(self):
        clock = FakeClock()
        store = ApprovalGateStore(timeout_s=60, clock=clock)
        first = store.propose("s1", "uno")
        clock.advance(61)
        second = store.propose("s1", "dos")
        assert store.get(first.gate_id).state == EXPIRED  # lazy check wins
        assert store.current("s1").gate_id == second.gate_id

    def test_one_live_gate_per_session_across_proposes(self):
        store = ApprovalGateStore(clock=FakeClock())
        gates = [store.propose("s1", f"msg-{i}") for i in range(3)]
        live = [g for g in gates if store.get(g.gate_id).state == PROPOSED]
        assert len(live) == 1
        assert live[0].gate_id == gates[-1].gate_id

    def test_supersede_marks_live_gate_terminal(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        superseded = store.supersede("s1")
        assert superseded.state == SUPERSEDED
        assert store.current("s1") is None
        assert store.supersede("s1") is None  # idempotent: nothing live

    def test_sessions_are_isolated(self):
        store = ApprovalGateStore(clock=FakeClock())
        g1 = store.propose("s1", "uno")
        g2 = store.propose("s2", "dos")
        store.supersede("s1")
        assert store.get(g1.gate_id).state == SUPERSEDED
        assert store.current("s2").gate_id == g2.gate_id

    def test_concurrent_proposes_leave_single_live_gate(self):
        store = ApprovalGateStore(timeout_s=600, clock=FakeClock())
        proposed: list = []

        def spam(n):
            for i in range(n):
                proposed.append(store.propose("s1", f"t{i}"))

        threads = [threading.Thread(target=spam, args=(25,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        states = [store.get(g.gate_id).state for g in proposed]
        assert len(proposed) == 100
        assert states.count(PROPOSED) == 1
        assert set(states) - {PROPOSED} == {SUPERSEDED}


class TestApprovalTimeoutConfig:
    def test_default_timeout(self):
        assert load_settings({}).approval_timeout_s == DEFAULT_APPROVAL_TIMEOUT_S

    def test_env_override(self):
        env = {"HERDR_BRAIN_APPROVAL_TIMEOUT_S": "5"}
        assert load_settings(env).approval_timeout_s == 5
