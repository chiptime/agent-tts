"""The bridge from the ANSI pill detector to the structured model.

The opencode permission modal keeps its own dedicated detector
(:mod:`herdr_brain.dialog_opencode`): the selected option is an inked
pill that exists only as colour, so the model it derives is the
authoritative one. This module re-expresses that model as a
:class:`~herdr_brain.dialogs.model.StructuredDialog` — a walk recipe
(the pill row's own footer named left/right and enter) — without
touching the detector itself.
"""

from __future__ import annotations

from typing import List, Optional

from ..dialog_opencode import detect_dialog_from_text
from .model import DialogOption, StructuredDialog


def pill_as_structured(agent: str, lines: List[str]) -> Optional[StructuredDialog]:
    """The pill dialog as a structured walk-recipe dialog, or None."""
    screen = "\n".join(lines)
    model = detect_dialog_from_text(screen)
    if model is None:
        return None
    return StructuredDialog(
        agent=agent,
        kind="permission",
        question=model.title,
        options=tuple(
            DialogOption(label=action.label, keys=()) for action in model.actions
        ),
        fingerprint=model.signature,
        hint="",
        nav_keys=tuple(model.nav_keys),
        commit_keys=tuple(model.commit_keys),
        selected=model.selected,
    )
