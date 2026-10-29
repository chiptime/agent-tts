"""Meta Muse: approval, questions with digit-jump semantics, trust.

Shapes this recognizer claims (pinned by synthesized fixtures in the
layouts Muse draws). Muse differs from its family in three measured
ways, and each one shapes a recipe:

- command approval — ``› 1. Allow this stage once (y)`` rows, NO
  footer. Probed: the digit ALONE commits. Recipe ``(digit,)``. The
  persistent ``Always allow …`` row is never a button.
- single-select question — numbered rows under a footer
  ``Enter to select · ↑/↓ to move …``. Probed: the digit MOVES the
  pointer (a no-op when it already stands there) and Enter submits.
  Recipe ``(digit, enter)``.
- multi-select question — checkbox rows plus the numbered
  ``N. Submit answer (M checked)`` row and the footer
  ``Enter to toggle``. Toggle recipe ``(digit, enter)``; the submit
  row's own digit + Enter advances the set. Checkbox state comes from
  the glyph the terminal shows.
- trust — ``> 1  Trust and continue`` (two spaces, no dot) under the
  footer ``Use Up/Down or 1/2, then Enter. Esc quits.``. Probed: the
  digit alone submits. Recipe ``(digit,)``.

An open ``Note (optional):`` row owns the keyboard — every digit
becomes text — so that state is UNREAD.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import (
    collect_numbered_block,
    question_above,
    split_checked,
    strip_decoration,
    strip_gutter,
)
from . import AdapterBase, dialog_region, footer_line

#: Muse's own footer words: ``to move`` and the notes hint are Muse's
#: phrasing (the family lookalike says ``to navigate``), and they pin
#: the digit-jump semantics the recipe depends on.
_SELECT_FOOTER = re.compile(r"\benter to select\b.*\bto move\b", re.IGNORECASE)
_TOGGLE_FOOTER = re.compile(r"\benter to toggle\b", re.IGNORECASE)
_TRUST_FOOTER = re.compile(r"use up/down or \d/\d", re.IGNORECASE)
_NOTES_ROW = re.compile(r"note \(optional\)", re.IGNORECASE)
_APPROVAL_QUESTION = re.compile(r"would you like to", re.IGNORECASE)
_PERSISTENT = re.compile(r"^always allow", re.IGNORECASE)
_SUBMIT_ROW = re.compile(r"^submit answer \(\d+ checked\)$", re.IGNORECASE)


class MuseAdapter(AdapterBase):
    agent = "muse"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        if any(_NOTES_ROW.search(line) for line in lines):
            return unread(self.agent, "the note row owns the keyboard", lines)

        footer = footer_line(lines)
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block
        question = question_above(lines, first)
        if not question:
            return None
        region = dialog_region(lines, first)

        if _TOGGLE_FOOTER.search(footer):
            return self._multiselect(footer, question, rows, region)
        if _SELECT_FOOTER.search(footer):
            return StructuredDialog(
                agent=self.agent,
                kind="select",
                question=question,
                options=tuple(
                    DialogOption(label=strip_decoration(label), keys=(number, "enter"))
                    for number, label, _description, _pointed in rows
                ),
                fingerprint=fingerprint_region(region),
                hint=footer,
                selected=next(
                    (position for position, row in enumerate(rows) if row[3]), None
                ),
            )
        if _TRUST_FOOTER.search(footer):
            return StructuredDialog(
                agent=self.agent,
                kind="confirm",
                question=question,
                options=tuple(
                    DialogOption(label=strip_decoration(label), keys=(number,))
                    for number, label, _description, _pointed in rows
                ),
                fingerprint=fingerprint_region(region),
                hint=footer,
                selected=next(
                    (position for position, row in enumerate(rows) if row[3]), None
                ),
            )
        # The command approval has NO footer of its own: its last option
        # row is the tail, and its question sits above the subject.
        approval_line = next(
            (
                strip_gutter(line).strip()
                for line in lines[:first]
                if _APPROVAL_QUESTION.search(line)
            ),
            "",
        )
        if approval_line:
            keep = [
                (number, label, pointed)
                for number, label, _description, pointed in rows
                if not _PERSISTENT.match(strip_decoration(label))
            ]
            if len(keep) != 2:
                return None
            return StructuredDialog(
                agent=self.agent,
                kind="permission",
                question=approval_line,
                options=tuple(
                    DialogOption(label=strip_decoration(label), keys=(number,))
                    for number, label, _pointed in keep
                ),
                fingerprint=fingerprint_region(region),
                hint="",
                selected=next(
                    (position for position, row in enumerate(rows) if row[3]), None
                ),
            )
        return None

    def _multiselect(self, footer, question, rows, region) -> StructuredDialog:
        options: List[DialogOption] = []
        for number, label, _description, _pointed in rows:
            clean, checked = split_checked(label)
            clean = clean.strip()
            if _SUBMIT_ROW.match(clean):
                options.append(DialogOption(label=clean, keys=(number, "enter")))
            else:
                options.append(
                    DialogOption(label=clean, keys=(number, "enter"), checked=checked)
                )
        return StructuredDialog(
            agent=self.agent,
            kind="multiselect",
            question=question,
            options=tuple(options),
            fingerprint=fingerprint_region(region),
            hint=footer,
            selected=next(
                (position for position, row in enumerate(rows) if row[3]), None
            ),
        )
