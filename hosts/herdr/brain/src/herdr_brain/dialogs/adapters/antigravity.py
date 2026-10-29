"""Antigravity (agy): numbered prompts with a pointed list.

The shape this recognizer claims: numbered options under a ``❯``
pointer with a footer naming ``enter select``/``enter to select``. No
digit behavior is verified for this harness, so NO digit is ever
synthesised — the recipe is the pointer walk the footer itself names
(``up``/``down`` + ``enter``), exactly like the pointed trust lists of
its family.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region
from ..numbered import collect_numbered_block, question_above
from . import AdapterBase, dialog_region, footer_line

#: The footer words of this family's pointed list. ``esc cancel`` (in
#: this harness's clipped dialect) is part of the claim so another
#: harness's lookalike footer stays foreign.
_SELECT_FOOTER = re.compile(
    r"\benter(?:\s+to)?\s+select\b.*\besc(?:\s+to)?\s+cancel\b", re.IGNORECASE
)
_POINTER = re.compile(r"^\s*[❯›>]\s*\d{1,2}[\.\s]")


class AntigravityAdapter(AdapterBase):
    agent = "antigravity"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        if not _SELECT_FOOTER.search(footer):
            return None
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block
        question = question_above(lines, first)
        if not question:
            return None
        return StructuredDialog(
            agent=self.agent,
            kind="select",
            question=question,
            options=tuple(
                DialogOption(label=label) for _number, label, _description, _p in rows
            ),
            fingerprint=fingerprint_region(dialog_region(lines, first)),
            hint=footer,
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=next(
                (position for position, row in enumerate(rows) if row[3]), None
            ),
        )
