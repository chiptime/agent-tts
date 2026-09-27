"""In-process approval gates for the mutating tool boundary.

When the tool loop wants to execute a mutating tool (``send_to_session``
or ``create_session``), it freezes the exact dispatch arguments into an
ApprovalGate instead of running them; execution happens only after the
user approves (PRD-action-approval-gate). Thread-safe like
ConversationStore: a single user is the norm, but racing requests must
never corrupt state. State is in-process only — a server restart drops
every gate (no persistence, same as conversation memory).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace

from .config import DEFAULT_APPROVAL_TIMEOUT_S

DEFAULT_SESSION = "default"
SEND_TO_SESSION = "send_to_session"
CREATE_SESSION = "create_session"
GATED_TOOLS = frozenset({SEND_TO_SESSION, CREATE_SESSION})

# Gate states: proposed -> approved | rejected | expired | superseded.
PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
SUPERSEDED = "superseded"
TERMINAL_STATES = frozenset({APPROVED, REJECTED, EXPIRED, SUPERSEDED})

# Resolve decisions accepted by ApprovalGateStore.resolve().
DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISION_REPROMPT = "reprompt"
DECISIONS = frozenset({DECISION_APPROVE, DECISION_REJECT, DECISION_REPROMPT})


@dataclass(frozen=True)
class GateAction:
    """FROZEN exact dispatch arguments captured at propose time.

    An approval replays the identical call, so nothing here may ever be
    edited in place — ``patch`` swaps the whole action (new text only).

    The send fields (``text``/``timeout_ms``/``pane_id``/``agent``) are
    ``send_to_session``'s exact dispatch args; the create fields
    (``agent_kind``/``title``/``task``/``cwd``) are ``create_session``'s,
    and stay ``None``/empty on send gates so the send payload is
    unchanged.
    """

    text: str
    timeout_ms: int | None
    pane_id: str | None
    agent: str | None
    agent_kind: str | None = None
    title: str | None = None
    task: str | None = None
    cwd: str = ""


@dataclass(frozen=True)
class ApprovalGate:
    """One pending action awaiting explicit user approval."""

    gate_id: str
    session_id: str
    state: str
    created_at: float  # epoch seconds; reset on text revision
    tool: str
    action: GateAction
    reprompt_count: int


class ApprovalGateStore:
    """Thread-safe approval gates with lazy expiry, one live gate per session.

    Expiry is lazy: gates older than ``timeout_s`` flip to ``expired`` when
    touched (get/current/resolve/patch/propose/supersede) — there is no
    background timer thread. Terminal states are immutable; resolving an
    already-approved gate is a no-op so approval executes exactly once.
    """

    def __init__(self, timeout_s: int = DEFAULT_APPROVAL_TIMEOUT_S, clock=None):
        self._timeout_s = timeout_s
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._gates: dict[str, ApprovalGate] = {}
        self._live: dict[str, str] = {}  # session key -> live gate_id

    @staticmethod
    def normalize(session_id: str | None) -> str:
        """Falls back to the default session when no id is given."""
        return (session_id or "").strip() or DEFAULT_SESSION

    def propose(
        self,
        session_id: str | None,
        text: str,
        timeout_ms: int | None = None,
        pane_id: str | None = None,
        agent: str | None = None,
        tool: str = SEND_TO_SESSION,
        agent_kind: str | None = None,
        title: str | None = None,
        task: str | None = None,
        cwd: str = "",
    ) -> ApprovalGate:
        """Freezes a new proposed gate, superseding any live one first.

        A still-live previous gate becomes ``superseded``; one whose window
        already elapsed becomes ``expired`` (lazy check wins — supersede
        only applies to gates that were still answerable). Create gates
        pass their args through ``agent_kind``/``title``/``task``/``cwd``
        with ``tool=CREATE_SESSION``.
        """
        key = self.normalize(session_id)
        with self._lock:
            live_id = self._live.get(key)
            if live_id is not None:
                previous = self._gates.get(live_id)
                if previous is not None:
                    previous = self._expire_if_due(previous)
                    if previous.state == PROPOSED:
                        self._retire(previous, SUPERSEDED)
            gate_id = uuid.uuid4().hex[:12]
            while gate_id in self._gates:  # paranoia: never overwrite
                gate_id = uuid.uuid4().hex[:12]
            gate = ApprovalGate(
                gate_id=gate_id,
                session_id=key,
                state=PROPOSED,
                created_at=self._clock(),
                tool=tool,
                action=GateAction(
                    text=text or "",
                    timeout_ms=timeout_ms,
                    pane_id=pane_id,
                    agent=agent,
                    agent_kind=agent_kind,
                    title=title,
                    task=task,
                    cwd=cwd,
                ),
                reprompt_count=0,
            )
            self._gates[gate_id] = gate
            self._live[key] = gate_id
            return gate

    def get(self, gate_id: str) -> ApprovalGate | None:
        """Returns the gate snapshot by id (any state), lazily expiring it."""
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                return None
            return self._expire_if_due(gate)

    def current(self, session_id: str | None) -> ApprovalGate | None:
        """Returns the session's live (proposed) gate, or None."""
        key = self.normalize(session_id)
        with self._lock:
            gate = self._live_gate(key)
            return gate if gate is not None and gate.state == PROPOSED else None

    def resolve(self, gate_id: str, decision: str) -> tuple[ApprovalGate | None, bool]:
        """Applies a user decision to a gate.

        Returns ``(gate, applied)``: ``applied`` is True only when this call
        actually changed the gate, so an approve that lands after expiry or
        a second approve never reports as executed again (approve-once).
        ``reprompt`` keeps the gate proposed and increments the budget.
        """
        if decision not in DECISIONS:
            raise ValueError(f"unknown approval decision: {decision!r}")
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                return None, False
            gate = self._expire_if_due(gate)
            if gate.state != PROPOSED:
                return gate, False  # terminal states are immutable
            if decision == DECISION_REPROMPT:
                updated = replace(gate, reprompt_count=gate.reprompt_count + 1)
                self._gates[gate_id] = updated
                return updated, True
            state = APPROVED if decision == DECISION_APPROVE else REJECTED
            return self._retire(gate, state), True

    def patch(self, gate_id: str, text: str) -> ApprovalGate | None:
        """Edits the frozen editable field of a still-live gate and
        restarts its timer.

        The editable field follows the tool: ``text`` for send gates,
        ``task`` for create gates — the PATCH wire body stays ``text``
        either way. Returns the updated gate, or None when the id is
        unknown or the gate is no longer proposed (terminal or lazily
        expired) — a dead gate must never come back to life through an
        edit.
        """
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                return None
            gate = self._expire_if_due(gate)
            if gate.state != PROPOSED:
                return None
            field = "task" if gate.tool == CREATE_SESSION else "text"
            updated = replace(
                gate,
                created_at=self._clock(),
                action=replace(gate.action, **{field: text or ""}),
            )
            self._gates[gate_id] = updated
            return updated

    def supersede(self, session_id: str | None) -> ApprovalGate | None:
        """Terminal-supersedes the session's live gate, if any (new /ask)."""
        key = self.normalize(session_id)
        with self._lock:
            gate = self._live_gate(key)
            if gate is None or gate.state != PROPOSED:
                return gate
            return self._retire(gate, SUPERSEDED)

    # All helpers below assume self._lock is held by the caller.

    def _live_gate(self, key: str) -> ApprovalGate | None:
        gate_id = self._live.get(key)
        if gate_id is None:
            return None
        gate = self._gates.get(gate_id)
        if gate is None:
            self._live.pop(key, None)
            return None
        return self._expire_if_due(gate)

    def _expire_if_due(self, gate: ApprovalGate) -> ApprovalGate:
        if gate.state == PROPOSED and self._clock() - gate.created_at > self._timeout_s:
            return self._retire(gate, EXPIRED)
        return gate

    def _retire(self, gate: ApprovalGate, state: str) -> ApprovalGate:
        """Moves a gate to a terminal state and clears the live slot."""
        retired = replace(gate, state=state)
        self._gates[gate.gate_id] = retired
        if self._live.get(gate.session_id) == gate.gate_id:
            self._live.pop(gate.session_id, None)
        return retired
