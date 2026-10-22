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


class TestExplanationBinding:
    """S14: a spoken «adelante» may only authorize the gate whose
    explanation this request can prove. The store carries that proof as
    an (speech job, revision) pair, checked ATOMICALLY with the approve
    transition."""

    def test_bind_explanation_records_the_speech_job_and_revision(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        bound = store.bind_explanation(gate.gate_id, "sp-job-0001")
        assert bound.explained_speech_id == "sp-job-0001"
        assert bound.explained_revision == gate.revision
        stored = store.get(gate.gate_id)
        assert stored.explained_speech_id == "sp-job-0001"
        assert stored.explained_revision == 0

    def test_bind_explanation_rebinds_to_the_newest_explanation(self):
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        store.bind_explanation(gate.gate_id, "sp-job-0001")
        store.bind_explanation(gate.gate_id, "sp-job-0002")
        assert store.get(gate.gate_id).explained_speech_id == "sp-job-0002"

    def test_bind_explanation_ignores_unknown_and_terminal_gates(self):
        store = ApprovalGateStore(clock=FakeClock())
        assert store.bind_explanation("nope", "sp-job-0001") is None
        gate = store.propose("s1", "hola")
        store.resolve(gate.gate_id, DECISION_REJECT)
        assert store.bind_explanation(gate.gate_id, "sp-job-0001") is None
        assert store.get(gate.gate_id).explained_speech_id is None

    def test_bind_explanation_refuses_a_stale_revision(self):
        """A PATCH racing the bind must leave the gate UNBOUND: the bind
        states which revision it explained (B6/B8)."""
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        store.patch(gate.gate_id, "texto revisado")  # revision is now 1
        assert store.bind_explanation(gate.gate_id, "sp-job-0001", expected_revision=0) is None
        assert store.get(gate.gate_id).explained_speech_id is None
        assert store.bind_explanation(
            gate.gate_id, "sp-job-0001", expected_revision=1
        ).explained_revision == 1

    def test_new_gate_never_inherits_a_prior_explanation(self):
        store = ApprovalGateStore(clock=FakeClock())
        first = store.propose("s1", "uno")
        store.bind_explanation(first.gate_id, "sp-job-0001")
        second = store.propose("s1", "dos")  # supersedes the first
        assert second.explained_speech_id is None
        assert store.get(second.gate_id).explained_speech_id is None

    def test_patch_bumps_the_revision_and_voids_the_explanation(self):
        """A revised action is a DIFFERENT action: authority spoken for the
        old text must not silently approve the new one."""
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "texto original")
        store.bind_explanation(gate.gate_id, "sp-job-0001")

        patched = store.patch(gate.gate_id, "texto revisado")

        assert patched.revision == gate.revision + 1
        assert patched.explained_speech_id is None
        assert patched.explained_revision is None
        assert store.get(gate.gate_id).action.text == "texto revisado"

    def test_resolve_with_expectation_is_atomic_with_the_check(self):
        """B8: the proven (speech id, revision) pair must still be the
        gate's binding AT transition time — a revision landing between
        the caller's validation and the resolve blocks the approve."""
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "run tests")
        store.bind_explanation(gate.gate_id, "sp-job-0001")

        # The caller validated (sp-job-0001, revision 0)…
        store.patch(gate.gate_id, "delete tests")  # …then the action changed
        resolved, applied = store.resolve(
            gate.gate_id, DECISION_APPROVE, expect_explanation=("sp-job-0001", 0)
        )
        assert applied is False
        assert resolved.state == PROPOSED
        assert resolved.action.text == "delete tests"  # nothing executed

        # The pair that matches the CURRENT binding still approves…
        store.bind_explanation(gate.gate_id, "sp-job-0002")
        resolved, applied = store.resolve(
            gate.gate_id, DECISION_APPROVE, expect_explanation=("sp-job-0001", 1)
        )
        assert applied is False and resolved.state == PROPOSED
        resolved, applied = store.resolve(
            gate.gate_id, DECISION_APPROVE, expect_explanation=("sp-job-0002", 1)
        )
        assert applied is True and resolved.state == APPROVED
        assert resolved.action.text == "delete tests"  # the frozen snapshot

    def test_plain_resolve_without_expectation_still_works(self):
        """The manual (button) approve path carries no expectation: it is
        explicit direct authorization and must keep working (B6 note)."""
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        resolved, applied = store.resolve(gate.gate_id, DECISION_APPROVE)
        assert applied is True and resolved.state == APPROVED

    def test_resolve_keeps_the_binding_fields(self):
        """Approve-once semantics are untouched by the S14 fields."""
        store = ApprovalGateStore(clock=FakeClock())
        gate = store.propose("s1", "hola")
        store.bind_explanation(gate.gate_id, "sp-job-0001")
        resolved, applied = store.resolve(
            gate.gate_id, DECISION_APPROVE,
            expect_explanation=("sp-job-0001", gate.revision),
        )
        assert applied is True and resolved.state == APPROVED
        # A second approve is still a no-op.
        again, applied_again = store.resolve(gate.gate_id, DECISION_APPROVE)
        assert applied_again is False and again.state == APPROVED
