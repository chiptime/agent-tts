"""Verified pane sends (S12): receipts over a closed outcome set.

A blind ``send_prompt`` claim is not evidence. This module is the brain's
own read-back discipline for every reply typed into a real terminal:

- read the pane screen BEFORE sending;
- send, then read it again on a bounded short poll;
- classify what the terminal actually did into the CLOSED set
  :data:`OUTCOMES` — nothing outside it ever reaches the PWA.

The classifier is a pure function over evidence (screens + the mux's own
claim), so every row of the outcome table is testable without a pane.
The receipt store is bounded and in-memory by design: a receipt is UI
state for the phone, not a log (the audit trail owns who-did-what).

Also here: the per-harness capability tables the stop/steer routes gate
on. Chat-TUI harnesses get an ``esc`` interrupt recipe and steer
support; everything else is refused — sending an interrupt key to a
harness the brain cannot reason about is exactly the mistake the tables
exist to prevent.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Iterable, List, Optional, Tuple

from .secrets_mask import mask_text

#: The CLOSED outcome set (S12). ``discarded`` closes over the discard
#: action on a ``typed_not_submitted`` receipt; every other value is
#: classified from send evidence.
OUTCOMES = (
    "effective",
    "sent",
    "typed_not_submitted",
    "discarded",
    "refused",
    "unknown",
)

#: Mux prompt statuses that mean "nothing was written" — herdr refuses
#: an agent waiting at a dialog BEFORE writing any input.
REFUSED_SEND_STATUSES = frozenset({"blocked"})

#: How many chars of the sent text the input-line echo check needs. The
#: tail is the stable part (TUIs may ellipsize the head of a long draft).
ECHO_TAIL_CHARS = 40

# ── per-harness capability tables (S12 stop/steer) ────────────────────────────
#
# Chat-TUI harnesses queue a message typed while the agent works, and
# answer Esc as "interrupt the running turn" — both verified behaviors
# of the claude/codex/opencode family. A harness NOT in a table gets the
# safe default: refusal for the interrupt (a stray key into an unknown
# TUI is unguessable), steer refusal likewise.

STEER_HARNESSES = frozenset({"claude", "opencode", "codex"})

INTERRUPT_RECIPES: dict = {
    "claude": ("esc",),
    "opencode": ("esc",),
    "codex": ("esc",),
}

#: Clearing a typed draft: Esc never types a character and never
#: executes anything — on chat TUIs it drops the draft, on a plain
#: shell it is a no-op. Safe as the DEFAULT for every harness.
DISCARD_RECIPES: dict = {
    "claude": ("esc",),
    "opencode": ("esc",),
    "codex": ("esc",),
}


def steer_supported(agent_kind: str) -> bool:
    """Whether a message may be injected into this harness's RUNNING turn."""
    return (agent_kind or "") in STEER_HARNESSES


def interrupt_recipe(agent_kind: str) -> Optional[Tuple[str, ...]]:
    """The interrupt key recipe, or None when the harness is unknown
    (the caller refuses — never guess an interrupt into a stranger)."""
    return INTERRUPT_RECIPES.get(agent_kind or "")


def discard_recipe(agent_kind: str) -> Tuple[str, ...]:
    """The recipe that clears a typed draft; ``esc`` for every harness."""
    return DISCARD_RECIPES.get(agent_kind or "", ("esc",))


# ── input-line evidence ───────────────────────────────────────────────────────


def input_line(screen: str) -> str:
    """The screen's last non-empty line — where a TUI paints its input box."""
    for line in reversed((screen or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


def echo_tail(text: str) -> str:
    """The short tail of the sent text the echo check looks for."""
    return (text or "").strip()[-ECHO_TAIL_CHARS:]


def text_echoed_on_input(screen: str, text: str) -> bool:
    """Whether the sent text still sits on the screen's input line."""
    tail = echo_tail(text)
    return bool(tail) and tail in input_line(screen)


def _body_lines(screen: str) -> List[str]:
    """Everything ABOVE the input line — the transcript the agent moves."""
    lines = [line.strip() for line in (screen or "").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    if lines:
        lines.pop()  # the input line itself
    return lines


# ── the classifier ────────────────────────────────────────────────────────────


def classify_send(
    before: Optional[str],
    afters: List[Optional[str]],
    sent_text: str,
    send_status: str,
) -> Tuple[str, str]:
    """One outcome from the evidence, in the closed set.

    - ``blocked`` (herdr refused before writing) → ``refused``.
    - ``done`` (the mux waited for completion) → ``effective``.
    - The sent text still on the input line with a FROZEN body →
      ``typed_not_submitted`` — typed, but the terminal never took it.
    - The mux claims the prompt was written (``stalled``/``timeout``,
      the wait expired) → ``sent`` — submitted, unconfirmed.
    - Fire-and-forget sends fall back to the screen: a body that moved
      is ``sent``; no evidence at all is ``unknown`` (never a guess).
    """
    if send_status in REFUSED_SEND_STATUSES:
        return "refused", "el agente rechazó el texto antes de escribir nada"
    if send_status == "done":
        return "effective", "el agente terminó el turno"
    last = afters[-1] if afters else None
    if last is None:
        if send_status in ("stalled", "timeout"):
            return "sent", "enviado sin confirmación de pantalla"
        return "unknown", "no se pudo leer la pantalla del agente"
    echo_now = text_echoed_on_input(last, sent_text)
    echo_before = text_echoed_on_input(before or "", sent_text)
    body_moved = _body_lines(last) != _body_lines(before or "")
    if echo_now and not echo_before and not body_moved:
        return (
            "typed_not_submitted",
            "el texto quedó escrito en la línea de entrada sin enviarse",
        )
    if send_status in ("stalled", "timeout"):
        return "sent", "enviado; el agente sigue trabajando"
    if body_moved:
        return "sent", "enviado; la pantalla del agente avanzó"
    if echo_now and echo_before:
        return "unknown", "la pantalla ya mostraba ese texto antes del envío"
    return "unknown", "sin evidencia suficiente para clasificar el envío"


def classify_followup(
    afters: List[Optional[str]],
    text_hint: str,
    resolved_outcome: str,
) -> Tuple[str, str]:
    """Classifies a submit/discard follow-up on a typed receipt.

    The only question is whether the echo left the input line: gone →
    ``resolved_outcome`` (``sent`` for submit, ``discarded`` for
    discard); still there → ``typed_not_submitted`` (the receipt stays
    re-offerable); unreadable → ``unknown``.
    """
    last = afters[-1] if afters else None
    if last is None:
        return "unknown", "no se pudo leer la pantalla del agente"
    if text_echoed_on_input(last, text_hint):
        return (
            "typed_not_submitted",
            "el texto sigue en la línea de entrada",
        )
    if resolved_outcome == "discarded":
        return "discarded", "entrada descartada"
    return "sent", "enviado con Intro"


# ── the bounded receipt store ─────────────────────────────────────────────────

#: Receipts are phone-side UI state: recent ones matter, old ones never
#: will. One bounded deque, oldest dropped, no disk.
MAX_RECEIPTS = 64


class SendReceiptStore:
    """Thread-safe, bounded, in-memory receipts (newest first)."""

    def __init__(
        self,
        max_receipts: int = MAX_RECEIPTS,
        clock: Optional[Callable[[], float]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._max = max_receipts
        self._clock = clock or time.time
        self._ids = id_factory or (lambda: f"rct-{uuid.uuid4().hex[:10]}")
        self._lock = threading.Lock()
        self._receipts: List[dict] = []

    def record(
        self,
        pane_id: str,
        outcome: str,
        detail: str = "",
        text_hint: str = "",
    ) -> dict:
        """Appends one receipt (masked: a detail may echo screen text)."""
        receipt = {
            "id": self._ids(),
            "pane_id": pane_id,
            "outcome": outcome,
            "detail": mask_text(detail or ""),
            "text_hint": mask_text((text_hint or "")[-ECHO_TAIL_CHARS:]),
            "created_at": self._clock(),
            "resolved_at": None,
        }
        with self._lock:
            self._receipts.append(receipt)
            if len(self._receipts) > self._max:
                self._receipts = self._receipts[-self._max:]
        return dict(receipt)

    def get(self, receipt_id: str) -> Optional[dict]:
        with self._lock:
            receipt = next((r for r in self._receipts if r["id"] == receipt_id), None)
            return dict(receipt) if receipt else None

    def resolve(self, receipt_id: str, outcome: str, detail: str = "") -> Optional[dict]:
        """Flips one receipt's outcome (submit/discard), once."""
        with self._lock:
            receipt = next((r for r in self._receipts if r["id"] == receipt_id), None)
            if receipt is None:
                return None
            receipt["outcome"] = outcome
            receipt["detail"] = mask_text(detail or receipt["detail"])
            receipt["resolved_at"] = self._clock()
            return dict(receipt)

    def list(self, pane_id: Optional[str] = None) -> List[dict]:
        """Newest first, optionally filtered to one pane."""
        with self._lock:
            receipts = [dict(r) for r in reversed(self._receipts)]
        if pane_id:
            receipts = [r for r in receipts if r["pane_id"] == pane_id]
        return receipts
