"""The neutral structured model every harness dialog is lifted into.

A dialog on a terminal is pixels; what the PWA and the voice path need
is a QUESTION, the OPTIONS with the exact keys that choose them, and an
identity the race guard can compare. This module is that shape, plus
the fingerprint that makes it comparable — nothing here knows any
harness.

Two recipe classes exist, and the difference is load-bearing:

- ``keys`` — the dialog itself prints the number. The recipe is
  pointer-independent (``("3",)`` or ``("3", "enter")``) and can be
  sent as one guarded sequence.
- the WALK — the screen prints no key per option. The model then
  carries the arrows and the commit key the screen's own footer named
  (``nav_keys``/``commit_keys``) plus where the pointer stands
  (``selected``), and the answer is the guard's choreography: one
  arrow, a re-read, only then the commit. Options then carry EMPTY
  ``keys`` — synthesising a digit the screen never printed is how a
  keystroke lands where nobody offered it.

The UNREAD state (``unread=True``) is a positive claim that this is the
harness's dialog but a state no button may drive: a notes field that
owns the keyboard, a parked card that swallows digits. It is never a
guess — it is the honest "cannot read this dialog" the PWA pairs with
the manual keypad.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional, Tuple

#: The dialog kinds the neutral model carries. ``select`` answers one
#: option; ``multiselect`` toggles several and then submits;
#: ``confirm`` and ``permission`` are one-question dialogs whose answer
#: is a yes/no/trust decision; ``wizard_step`` is one question of a
#: multi-question call (answering may advance to the next step).
KINDS = ("select", "multiselect", "confirm", "permission", "wizard_step")


@dataclass(frozen=True)
class DialogOption:
    """One answer the dialog itself offered.

    ``keys`` is the exact recipe that selects AND commits this option,
    taken from the numbering the screen printed — empty when the
    dialog is walk-class (the recipe is then the model's arrows plus a
    verified landing, not a static sequence).
    """

    label: str
    keys: Tuple[str, ...] = ()
    description: str = ""
    #: Multiselect only: the checkbox state the terminal shows. ``None``
    #: on rows that are not checkboxes (e.g. the submit row).
    checked: Optional[bool] = None


@dataclass(frozen=True)
class StructuredDialog:
    """What one harness's recognizer read off the screen."""

    agent: str
    kind: str
    question: str
    options: Tuple[DialogOption, ...] = ()
    #: Digest of the region this model came from, pointer included. The
    #: race guard compares it: non-empty, and it changes when the
    #: region's text changes.
    fingerprint: str = ""
    #: The footer hint row, verbatim (display only).
    hint: str = ""
    #: Wizard/wizard-step marker as the screen prints it ("1/2").
    step: str = ""
    #: Walk-class facts: the screen's own arrows, its commit key, and
    #: which option carries the pointer.
    nav_keys: Tuple[str, ...] = ()
    commit_keys: Tuple[str, ...] = ()
    selected: Optional[int] = None
    #: The explicit cannot-read state (no options, no keys, ever).
    unread: bool = False
    #: Why it cannot be read, when it cannot.
    detail: str = ""

    def payload(self) -> dict:
        """The /view shape: everything the PWA renders, nothing it may
        fire without the server re-reading the pane.

        Recipes stay server-side on purpose — the tap names an option,
        and the answer endpoint derives the keys from a FRESH read, so
        a stale render never carries executable material.
        """
        if self.unread:
            return {
                "unread": True,
                "agent": self.agent,
                "detail": self.detail,
            }
        return {
            "unread": False,
            "agent": self.agent,
            "kind": self.kind,
            "question": self.question,
            "step": self.step,
            "hint": self.hint,
            "fingerprint": self.fingerprint,
            "recipe": "keys" if any(option.keys for option in self.options) else "walk",
            "options": [
                {
                    "label": option.label,
                    "description": option.description,
                    "checked": option.checked,
                }
                for option in self.options
            ],
        }

    def as_guard_model(self):
        """The guard's :class:`~herdr_brain.dialog.DialogModel` twin.

        ``signature`` is the fingerprint, so the guard's strict
        equality IS the fingerprint race check; per-option ``keys`` ride
        along so a keys-class answer needs no walk at all.
        """
        from ..dialog import DialogAction, DialogModel

        return DialogModel(
            title=self.question,
            actions=tuple(
                DialogAction(label=option.label, keys=option.keys)
                for option in self.options
            ),
            nav_keys=self.nav_keys,
            commit_keys=self.commit_keys,
            selected=self.selected,
            signature=self.fingerprint,
        )


def fingerprint_region(lines) -> str:
    """A digest of the dialog's region — non-empty, text-sensitive.

    The pointer glyph is KEPT: a tap on a render whose pointer moved
    must be refused (refresh), not answered from stale coordinates.
    """
    material = "\n".join(line.rstrip() for line in lines).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def unread(agent: str, detail: str, lines) -> StructuredDialog:
    """The explicit cannot-read state for a harness's own dialog."""
    return StructuredDialog(
        agent=agent,
        kind="",
        question="",
        fingerprint=fingerprint_region(lines),
        unread=True,
        detail=detail,
    )
