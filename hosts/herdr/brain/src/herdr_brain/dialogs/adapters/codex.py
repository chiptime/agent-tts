"""Codex dialogs: trust, exec approval, and request_user_input.

Shapes this recognizer claims (pinned by synthesized fixtures in the
layouts Codex draws):

- folder trust — ``› 1. Yes, continue`` / ``2. No, quit`` under a
  "Do you trust the contents of this directory?" question. Probed
  fact: the digit alone confirms. Recipe ``(digit,)``.
- exec approval — ``Would you like to run the following command?``
  with numbered rows. Only the ONE-SHOT row (``Yes, proceed``) and the
  reject row become options: the "don't ask again" row persists an
  approval rule, and its digit is never a button here.
- ``request_user_input`` — a ``Question 1/2 (N unanswered)`` header
  over numbered rows with a notes footer. The digit answers the
  current question (and submits the set on the last one). The
  NOTES-FOCUSED state (``› Add notes`` row, ``tab or esc to clear
  notes`` footer) is UNREAD: a digit would type into the box.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import (
    collect_numbered_block,
    question_above,
    step_marker_above,
    strip_decoration,
)
from . import AdapterBase, dialog_region, footer_line

_TRUST_QUESTION = re.compile(r"do you trust the contents of this directory", re.IGNORECASE)
_RUN_QUESTION = re.compile(r"would you like to run the following command", re.IGNORECASE)
_ASK_HEADER = re.compile(r"question\s+\d{1,2}/\d{1,2}\s+\(", re.IGNORECASE)
_ASK_FOOTER = re.compile(r"enter to submit", re.IGNORECASE)
_NOTES_FOOTER = re.compile(r"tab or esc to clear notes", re.IGNORECASE)
_NOTES_ROW = re.compile(r"^\s*[›❯>]\s*add notes\b", re.IGNORECASE)
_ONESHOT = re.compile(r"^yes, proceed", re.IGNORECASE)
_REJECT = re.compile(r"^no, and tell", re.IGNORECASE)


class CodexAdapter(AdapterBase):
    agent = "codex"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block
        above = "\n".join(lines[:first])

        if _NOTES_FOOTER.search(footer) or any(
            _NOTES_ROW.match(line) for line in lines
        ):
            return unread(self.agent, "the notes box owns the keyboard", lines)

        if _TRUST_QUESTION.search(above) and _looks_like_trust(rows):
            return self._model(
                lines, first, rows, "confirm", footer,
            )
        if _RUN_QUESTION.search(above):
            keep = [row for row in rows if _ONESHOT.match(row[1]) or _REJECT.match(row[1])]
            if len(keep) != 2:
                # An approval card whose row classes are not the
                # measured ones is somebody else's dialog.
                return None
            return self._model(lines, first, keep, "permission", footer)

        if _ASK_HEADER.search(above) and _ASK_FOOTER.search(footer):
            return self._model(
                lines, first, rows, "wizard_step", footer,
                step=_ask_step(above),
            )
        return None

    def _model(
        self, lines, first, rows, kind, footer, step=""
    ) -> StructuredDialog:
        question = question_above(lines, first)
        if not question:
            return None  # type: ignore[return-value]
        return StructuredDialog(
            agent=self.agent,
            kind=kind,
            question=question,
            options=tuple(
                DialogOption(label=label, keys=(number,), description=description)
                for number, label, description, _pointed in rows
            ),
            fingerprint=fingerprint_region(dialog_region(lines, first)),
            hint=footer,
            step=step,
            selected=next(
                (position for position, row in enumerate(rows) if row[3]), None
            ),
        )


def _looks_like_trust(rows) -> bool:
    labels = [strip_decoration(label).lower() for _n, label, _d, _p in rows]
    return len(labels) == 2 and any(l.startswith("yes") for l in labels) and any(
        l.startswith("no") for l in labels
    )


def _ask_step(above: str) -> str:
    """The step the ``Question 1/2 (…)`` header names."""
    match = re.search(r"question\s+(\d{1,2}/\d{1,2})", above, re.IGNORECASE)
    return match.group(1) if match else ""
