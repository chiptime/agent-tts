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
#: multi-question call (answering may advance to the next step);
#: ``model_picker`` is a /model selector — walk-class ALWAYS, because
#: in these lists a digit confirms and rewrites the default model
#: (ADR 0009): the only recipe is the arrows plus Enter.
KINDS = ("select", "multiselect", "confirm", "permission", "wizard_step", "model_picker")


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
    #: True on the row whose digit FOCUSES a text field instead of
    #: committing an answer (``Type something.``). Its ``keys`` are the
    #: digit alone — Enter would submit before any text is typed — and
    #: the answer travels as ``{option, text}`` through the typing
    #: recipe, never as a plain digit commit.
    free_text: bool = False
    #: model_picker only (opencode): the row the picker itself marks
    #: with a ``●`` prefix — the CURRENT model. Additive on purpose:
    #: rows of every other kind (and picker rows without a mark) keep
    #: today's shape; the PWA may badge it, nothing may tap it.
    current: bool = False
    #: model_picker only (opencode): the row's clean MODEL name, apart
    #: from the provider the screen prints after it in the same text
    #: run. opencode's own Search matches the model name only, so this
    #: — never the whole label — is what the search-first recipe types.
    model: str = ""
    #: model_picker only (opencode): the provider (the label's trailing
    #: provider name, or the group header the row sits under). Display
    #: only; empty when unknown.
    provider: str = ""


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
    #: The SAME marker parsed for the PWA stepper (S4): current step
    #: (1-based) and total. ``None`` unless ``step`` is a well-formed
    #: ``N/M`` — anything unparsable keeps today's payload (no
    #: ``wizard`` key) and renders as a plain dialog.
    step_number: Optional[int] = None
    step_total: Optional[int] = None
    #: Walk-class facts: the screen's own arrows, its commit key, and
    #: which option carries the pointer.
    nav_keys: Tuple[str, ...] = ()
    commit_keys: Tuple[str, ...] = ()
    selected: Optional[int] = None
    #: OpenCode tab-strip metadata (T4b): the question-chip labels the
    #: pinned grammar read, ending in the ``Confirm``/``Submit`` chip.
    #: The choices choreography derives its commit from them (``tab``
    #: reaches the last chip only when it is one tab away); empty on
    #: every other dialog, and never sent to the PWA.
    tab_strip: Tuple[str, ...] = ()
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
            # Additive on purpose (S4): the stepper metadata rides along
            # ONLY when the marker parsed, so every payload without it
            # keeps its exact previous shape.
            **(
                {"wizard": {"step": self.step_number, "steps": self.step_total}}
                if self.step_number is not None and self.step_total is not None
                else {}
            ),
            "hint": self.hint,
            "fingerprint": self.fingerprint,
            "recipe": "keys" if any(option.keys for option in self.options) else "walk",
            "options": [
                {
                    "label": option.label,
                    "description": option.description,
                    "checked": option.checked,
                    # Additive on purpose (S3): the flag rides along
                    # ONLY on the rows that carry it, so every payload
                    # without a free-text row keeps its exact shape.
                    **({"free_text": True} if option.free_text else {}),
                    # Additive on purpose (opencode picker): the ● row
                    # rides along ONLY when the picker marked it.
                    **({"current": True} if option.current else {}),
                    # Additive on purpose (opencode picker): the clean
                    # model name and provider ride along ONLY on rows
                    # that carry them.
                    **({"model": option.model} if option.model else {}),
                    **({"provider": option.provider} if option.provider else {}),
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
