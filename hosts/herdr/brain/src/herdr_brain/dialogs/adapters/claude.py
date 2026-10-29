"""Claude Code dialogs: numbered prompts whose footer names the recipe.

The shapes this recognizer claims (each pinned by a synthesized
fixture in the same layout Claude draws):

- ``select`` — AskUserQuestion: options ``❯ 1. Label``, footer
  ``Enter to select · ↑/↓ to navigate · Esc to cancel``. The digit
  moves the pointer; Enter commits. Recipe: ``(digit, enter)``.
- ``permission`` — the tool permission dialog: footer
  ``Tab to amend …`` (or a bare ``Esc to cancel`` under a
  "Do you want to" question). The digit alone commits.
- ``trust`` — the folder-trust prompt: ``Enter to confirm`` footer
  under a "Do you trust the files in this folder?" question. The digit
  alone commits.
- wizard steps — a ``1/2`` step marker above the question makes the
  same select shape a ``wizard_step`` (answering may advance).

A pointer sitting on a free-text row (``Type something.``) means the
field owns the keyboard and every digit becomes text: UNREAD, never
buttons with a lying recipe.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import (
    collect_numbered_block,
    question_above,
    step_marker_above,
)
from . import AdapterBase, dialog_region, footer_line

#: Claude's own footer words. ``Esc to cancel`` is the discriminator
#: that keeps this from claiming another harness's lookalike footer
#: (Muse's select footer ends ``Esc to interrupt`` and its digits move
#: a pointer under a live notes row).
_SELECT_FOOTER = re.compile(r"\benter to select\b.*\besc to cancel\b", re.IGNORECASE)
_PERMISSION_FOOTER = re.compile(r"\btab to amend\b", re.IGNORECASE)
_BARE_ESC_FOOTER = re.compile(r"^\s*esc to cancel\s*$", re.IGNORECASE)
_TRUST_FOOTER = re.compile(r"\benter to confirm\b", re.IGNORECASE)
_PERMISSION_QUESTION = re.compile(r"\bdo you want to\b", re.IGNORECASE)
_TRUST_QUESTION = re.compile(r"\bdo you trust\b", re.IGNORECASE)


class ClaudeAdapter(AdapterBase):
    agent = "claude"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block

        if _SELECT_FOOTER.search(footer):
            kind = "select"
            suffix = ("enter",)
        elif _PERMISSION_FOOTER.search(footer) or (
            _BARE_ESC_FOOTER.match(footer)
            and _PERMISSION_QUESTION.search("\n".join(lines[:first]))
        ):
            kind = "permission"
            suffix = ()
        elif _TRUST_FOOTER.search(footer) and _TRUST_QUESTION.search(
            "\n".join(lines[:first])
        ):
            kind = "confirm"
            suffix = ()
        else:
            return None

        question = question_above(lines, first)
        if not question:
            return None
        step = step_marker_above(lines, first)
        return StructuredDialog(
            agent=self.agent,
            kind="wizard_step" if (step and kind == "select") else kind,
            question=question,
            options=tuple(
                DialogOption(
                    label=label,
                    keys=(number, *suffix),
                )
                for number, label, _description, _pointed in rows
            ),
            fingerprint=fingerprint_region(dialog_region(lines, first)),
            hint=footer,
            step=step,
            selected=next(
                (position for position, row in enumerate(rows) if row[3]), None
            ),
        )
