"""Guarded keystrokes: what has to be true BEFORE a dialog is answered.

A keystroke is not a request — it is written straight into whatever holds the
terminal's focus. So the question is never "did the dialog look like this a
moment ago", but "is it still THAT dialog, right now". This module answers
only that question, and knows nothing about any agent's dialog grammar: the
detector is handed in, and the model it derives is what gets compared.

The design is taken from the harness guard of Panel, a Herdr plugin already
installed on this machine (bridge on 127.0.0.1:8787), re-expressed here in
brain's own terms. Its findings are the load-bearing part:

- ``pane.read.revision`` is a stub upstream — always 0, verified live
  2026-07-05 — so a revision gate cannot protect a live terminal. Equality of
  the derived dialog, highlight included and carried by its ``signature``, is
  the check that can.
- a COMMITTING key must clear strict equality; a NAVIGATION key only needs
  the weaker identity comparison, because the highlight moving is the
  expected result of the previous arrow, not evidence of drift.
- a dialog that vanished resolves to ``drifted``, never to a retryable
  ``timeout``: repeating the key blindly would land on whatever replaced it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

#: Bounded verification polling between choreography steps. A TUI redraws
#: well under a second; ~3s is the window before the caller refreshes.
POLL_ATTEMPTS = 8
POLL_DELAY_MS = 350


@dataclass(frozen=True)
class DialogAction:
    """One action the dialog itself named.

    ``keys`` are ALWAYS the keys the screen's own footer advertised, never a
    digit or a shortcut inferred from the option's position: synthesising a
    key is how a menu gets driven by something the dialog never promised.
    """

    label: str
    keys: Tuple[str, ...]
    cancel: bool = False


@dataclass(frozen=True)
class DialogModel:
    """What is on screen, as a detector understood it."""

    title: str
    actions: Tuple[DialogAction, ...]
    #: The arrow keys the screen advertised, empty when it showed none.
    nav_keys: Tuple[str, ...]
    #: A digest of the region this model came from, HIGHLIGHT INCLUDED. The
    #: guard compares it, so it must be non-empty and must change whenever
    #: that region's text changes.
    signature: str
    #: Which action carries the highlight, when the screen shows one. Kept OUT
    #: of the identity comparison on purpose: the highlight moving is what the
    #: previous arrow was for, so `selected` belongs to the signature, not to
    #: "is this still the same dialog".
    selected: Optional[int] = None
    #: The keys the screen's own footer named for committing, e.g. ("enter",).
    commit_keys: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardOutcome:
    """The verdict of {@link entry_guard}.

    ``ok`` with the freshly derived model, or ``reason`` of ``changed`` (the
    screen moved underfoot — nothing may be sent) or ``error`` (the read
    itself failed). A failure is never a pass.
    """

    ok: bool
    model: Optional[DialogModel] = None
    reason: str = ""
    detail: str = ""


Reader = Callable[[str, int], str]
Detect = Callable[[Optional[str]], Optional[DialogModel]]
Sleep = Callable[[float], None]


def default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def same_dialog(a: DialogModel, b: DialogModel) -> bool:
    """Same dialog, same pixels: the check a COMMITTING key must clear."""
    return a.signature == b.signature and same_dialog_identity(a, b)


def same_dialog_identity(a: DialogModel, b: DialogModel) -> bool:
    """Same dialog SCREEN, ignoring the highlight.

    The weaker check the non-committal arrows use: the highlight moving is
    what the previous arrow was for, so comparing the signature there would
    fail every second press.
    """
    return (
        a.title == b.title
        and a.nav_keys == b.nav_keys
        and len(a.actions) == len(b.actions)
        and all(
            left.label == right.label and left.keys == right.keys
            for left, right in zip(a.actions, b.actions)
        )
    )


def read_dialog(
    reader: Reader, pane_id: str, lines: int, detect: Detect
) -> Optional[DialogModel]:
    """One FRESH read, re-derived. ``None`` when no dialog is on screen."""
    return detect(reader(pane_id, lines))


def entry_guard(
    reader: Reader, pane_id: str, lines: int, tapped: DialogModel, detect: Detect
) -> GuardOutcome:
    """May a key be sent for what the user tapped?

    The read is fresh and the comparison is against ``tapped`` — the model
    derived from the render the user actually acted on — so a screen that has
    moved on since that render is refused rather than typed into.
    """
    try:
        fresh = read_dialog(reader, pane_id, lines, detect)
    except Exception as exc:  # noqa: BLE001 — a failed read is never a pass
        return GuardOutcome(ok=False, reason="error", detail=str(exc))
    if fresh is None or not same_dialog(fresh, tapped):
        return GuardOutcome(ok=False, reason="changed")
    return GuardOutcome(ok=True, model=fresh)


def poll_until(
    reader: Reader,
    pane_id: str,
    lines: int,
    tapped: DialogModel,
    detect: Detect,
    *,
    accept: Callable[[DialogModel], bool],
    identity: Callable[[DialogModel, DialogModel], bool] = same_dialog_identity,
    attempts: int = POLL_ATTEMPTS,
    sleep: Optional[Sleep] = None,
) -> str:
    """Waits, bounded, for the screen to show what the last key was for.

    Three-valued, because the caller must tell "our dialog is still there but
    the awaited state never arrived" (retrying is safe) apart from "a
    different dialog is up now" (nothing more may be sent):

    - ``"ok"``      — ``accept`` passed on a fresh derivation.
    - ``"drifted"`` — the identity changed, or every read found NO dialog
                      (the agent resumed): no further key may be sent.
    - ``"timeout"`` — our dialog is still on screen and the awaited state
                      never came.
    """
    pause = sleep or default_sleep
    saw_dialog = False
    for _ in range(attempts):
        pause(POLL_DELAY_MS / 1000)
        try:
            fresh = read_dialog(reader, pane_id, lines, detect)
        except Exception:  # noqa: BLE001 — a transient read failure is not drift
            continue
        if fresh is None:
            # A redraw can briefly hide the dialog's tail: keep polling. Only
            # an all-null run means it truly went away.
            continue
        saw_dialog = True
        if accept(fresh):
            return "ok"
        if not identity(fresh, tapped):
            return "drifted"
    # Never seeing it is drift, not a retryable timeout: a blind repeat would
    # hit whatever replaced it.
    return "timeout" if saw_dialog else "drifted"
