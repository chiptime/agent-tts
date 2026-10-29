"""Grok dialogs: the radio ask card and the permission card.

Shapes this recognizer claims (pinned by synthesized fixtures in the
layouts Grok Build draws):

- ask — a bar-run card with radio rows ``1 (○) Label  description``, a
  ``z`` free-text row (NOT an option: it opens a text field) and the
  footer ``Tab:next answer │ Esc:scrollback │ Shift+x:dismiss``.
  Probed fact: the digit alone submits. Recipe ``(digit,)``.
- permission — numbered radio rows under a footer
  ``N/M:select …``. Only the one-shot ``Yes`` row and the ``No,
  reject`` row become options; the rows above them persist approval
  modes and are never buttons.

Parked cards are UNREAD: after Esc the card swallows bare digits (the
footer reads ``Tab/Space:question``), so no button may fire.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import collect_numbered_block, question_above, strip_decoration
from . import AdapterBase, dialog_region, footer_line

_ASK_FOOTER = re.compile(r"tab:\s*next answer", re.IGNORECASE)
_PARKED_FOOTER = re.compile(r"^tab/space:\s*question\s*$", re.IGNORECASE)
_PERMISSION_FOOTER = re.compile(r"^\s*\d{1,2}/\d{1,2}:\s*select\b", re.IGNORECASE)
_ONESHOT = re.compile(r"^yes$", re.IGNORECASE)
_REJECT = re.compile(r"^no, reject", re.IGNORECASE)
_BAR_ROW = re.compile(r"^\s*┃")


class GrokAdapter(AdapterBase):
    agent = "grok"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        if _PARKED_FOOTER.match(footer):
            return unread(self.agent, "a parked card swallows bare digits", lines)
        if not _BAR_ROW.match(footer) and not any(
            _BAR_ROW.match(line) for line in lines[-8:]
        ):
            return None
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block
        question = question_above(lines, first)
        if not question:
            return None
        # The filled radio ``(●)`` is decoration the row parser strips;
        # read it off the RAW block lines, in row order.
        block_lines = lines[first : last + 1]
        radio = next(
            (
                position
                for position, line in enumerate(block_lines)
                if "(●)" in line or "(*)" in line
            ),
            None,
        )
        pointed = next(
            (position for position, row in enumerate(rows) if row[3]), None
        )
        selected = radio if radio is not None else pointed

        if _ASK_FOOTER.search(footer):
            return StructuredDialog(
                agent=self.agent,
                kind="select",
                question=question,
                options=tuple(
                    DialogOption(
                        label=strip_decoration(label),
                        keys=(number,),
                        description=description,
                    )
                    for number, label, description, _pointed in rows
                ),
                fingerprint=fingerprint_region(dialog_region(lines, first)),
                hint=footer,
                selected=selected,
            )

        if _PERMISSION_FOOTER.match(footer):
            keep = [
                (position, row)
                for position, row in enumerate(rows)
                if _ONESHOT.match(strip_decoration(row[1]))
                or _REJECT.match(strip_decoration(row[1]))
            ]
            if len(keep) != 2:
                # A permission card whose row classes are not the
                # measured ones is somebody else's dialog.
                return None
            kept_positions = sorted(position for position, _row in keep)
            kept_selected = (
                kept_positions.index(selected)
                if selected is not None and selected in kept_positions
                else None
            )
            return StructuredDialog(
                agent=self.agent,
                kind="permission",
                question=question,
                options=tuple(
                    DialogOption(label=strip_decoration(label), keys=(number,))
                    for _position, (number, label, _description, _pointed) in keep
                ),
                fingerprint=fingerprint_region(dialog_region(lines, first)),
                hint=footer,
                selected=kept_selected,
            )
        return None
