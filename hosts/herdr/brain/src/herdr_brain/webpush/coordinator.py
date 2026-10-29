"""Notification coordinator: one slot per herd, retracted when handled.

Decision logic only — an injectable clock and an injectable sink; the
service wires the sink to encrypted fan-out. The cycle a notification
lives through (doc 08):

- **Coalesce**: transitions arriving inside a short window become ONE
  summary per kind (``blocked`` and ``done`` never share a title), the
  agent named when alone, counted when several.
- **Cancel**: a pane handled inside the window never reaches the phone.
- **Retract**: once delivered, a handled pane retracts — the slot
  closes (``kind: cleared``) or is replaced by a summary of whoever
  still needs the operator.

Copy is Spanish and lives here, next to the decisions that use it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

#: Collapse key for the herd slot (10 chars: base64url-safe, and a
#: length Apple's base64 decoding accepts — see sender.topic_sendable).
HERD_TAG = "brain-herd"
#: Collapse key for cache warnings: latest warning replaces the last.
CACHE_TAG = "brain-cache"
#: Collapse key for transport tests.
TEST_TAG = "brain-test"

#: Payload contract version.
PAYLOAD_VERSION = 1

#: Statuses the coordinator reacts to (the watcher's announcement set).
HANDLED_STATUSES = ("blocked", "done")


@dataclass
class _Transition:
    pane_id: str
    label: str
    status: str
    text: Optional[str] = None
    question: Optional[str] = None
    gate_id: Optional[str] = None


def _gate_actions(gate_id: str) -> List[dict]:
    """Buttons a live gate adds; the gate id rides inside each action."""
    return [
        {"action": "approve", "title": "Aprobar", "gate": gate_id},
        {"action": "reject", "title": "Rechazar", "gate": gate_id},
    ]


def _summary_payload(transition: _Transition) -> dict:
    """The single-agent notification: named, with the blocking question."""
    payload: dict = {
        "v": PAYLOAD_VERSION,
        "kind": transition.status,
        "pane_id": transition.pane_id,
        "title": None,  # filled by copy below
        "body": None,
        "tag": HERD_TAG,
    }
    if transition.status == "blocked":
        payload["title"] = f"{transition.label} está bloqueado"
        payload["body"] = transition.question or transition.text or "Necesita tu decisión."
        if transition.question:
            payload["question"] = transition.question
        if transition.gate_id:
            payload["actions"] = _gate_actions(transition.gate_id)
    else:
        payload["title"] = f"{transition.label} terminó"
        payload["body"] = transition.text or "Tarea terminada."
    return payload


def _plural_payload(status: str, transitions: List[_Transition]) -> dict:
    """The multi-agent summary: counted title, agents listed in the body."""
    labels = [t.label for t in transitions]
    count = len(transitions)
    if status == "blocked":
        title = f"{count} agentes bloqueados"
        body = "Te necesitan: " + ", ".join(labels)
    else:
        title = f"{count} agentes terminaron"
        body = "Terminaron: " + ", ".join(labels)
    return {
        "v": PAYLOAD_VERSION,
        "kind": status,
        "pane_id": None,
        "title": title,
        "body": body,
        "tag": HERD_TAG,
    }


def _cleared_payload(transition: _Transition) -> dict:
    return {
        "v": PAYLOAD_VERSION,
        "kind": "cleared",
        "pane_id": transition.pane_id,
        "title": "Resuelto",
        "body": f"{transition.label} ya no necesita tu atención.",
        "tag": HERD_TAG,
    }


Sink = Callable[[dict, str], None]


class NotificationCoordinator:
    """Owns the pending→outstanding lifecycle; emits (payload, topic)."""

    def __init__(
        self,
        window_s: float = 2.0,
        clock: Callable[[], float] = time.time,
        sink: Sink = lambda payload, topic: None,
    ):
        self._window_s = window_s
        self._clock = clock
        self._sink = sink
        self._pending: Dict[str, _Transition] = {}
        self._window_start: Optional[float] = None
        self._outstanding: Dict[str, Dict[str, _Transition]] = {
            status: {} for status in HANDLED_STATUSES
        }

    # -- intake -------------------------------------------------------------

    def transition(
        self,
        pane_id: str,
        label: str,
        status: str,
        text: Optional[str] = None,
        question: Optional[str] = None,
        gate_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """Queues a watcher transition; the first one opens the window."""
        if status not in HANDLED_STATUSES:
            return
        current = self._clock() if now is None else now
        self._pending[pane_id] = _Transition(
            pane_id=pane_id,
            label=label,
            status=status,
            text=text,
            question=question,
            gate_id=gate_id,
        )
        if self._window_start is None:
            self._window_start = current

    def handled(
        self, pane_id: str, label: Optional[str] = None, now: Optional[float] = None
    ) -> None:
        """A pane stopped needing the operator — cancel or retract."""
        pending = self._pending.pop(pane_id, None)
        if pending is not None:
            if not self._pending:
                self._window_start = None
            return  # cancelled inside the window: the phone never knew
        for status, panes in self._outstanding.items():
            transition = panes.pop(pane_id, None)
            if transition is None:
                continue
            if panes:
                # Others still need attention: replace, don't close.
                self._sink(
                    _plural_payload(status, list(panes.values())),
                    HERD_TAG,
                )
            else:
                self._sink(_cleared_payload(transition), HERD_TAG)
            return

    # -- flushing -----------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> List[dict]:
        """Sends every window that has elapsed; returns what went out."""
        if not self._pending:
            return []
        current = self._clock() if now is None else now
        if self._window_start is None or current - self._window_start < self._window_s:
            return []
        sent: List[dict] = []
        by_status: Dict[str, List[_Transition]] = {}
        for transition in self._pending.values():
            by_status.setdefault(transition.status, []).append(transition)
        for status, transitions in by_status.items():
            if len(transitions) == 1:
                payload = _summary_payload(transitions[0])
            else:
                payload = _plural_payload(status, transitions)
            self._outstanding[status].update(
                {t.pane_id: t for t in transitions}
            )
            self._sink(payload, HERD_TAG)
            sent.append(payload)
        self._pending.clear()
        self._window_start = None
        return sent
