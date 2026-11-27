"""Antigravity (agy): pointed lists and the /model picker.

Two shapes this recognizer claims (fail-closed everywhere else):

- numbered options under a ``❯`` pointer with a footer naming
  ``enter select``/``enter to select``. No digit behavior is verified
  for this harness, so NO digit is ever synthesised — the recipe is the
  pointer walk the footer itself names (``up``/``down`` + ``enter``),
  exactly like the pointed trust lists of its family.
- the ``/model`` picker (POST-09, LIVE capture 2026-11-26 of agy
  1.5.18): the ``Switch Model`` panel — a ``Search:`` row with its own
  underline, unnumbered family rows whose pointer ``>`` sits in column
  0, one ``(current)`` mark, the Effort slider, and the single
  ``Keyboard:`` footer naming ``↑/↓ Navigate``, ``←/→ Effort``,
  ``enter Select`` and ``esc Go Back``. Walk-class ALWAYS (ADR 0009):
  the rows print no key, so the recipe is arrows plus Enter, never a
  digit. The quota statusline painted BELOW the footer churns on every
  read, so the fingerprint is bounded at the footer — the statusline
  never destabilizes the picker's identity, and the ``←/→ Effort``
  hint is recorded nowhere: effort is out of scope for this unit.
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

#: The picker's single footer row (real capture, 2026-11-26). EVERY
#: segment is part of the claim: another agy build's wording is another
#: version's shape.
_PICKER_FOOTER = re.compile(
    r"^Keyboard: ↑/↓ Navigate\s+←/→ Effort\s+enter Select\s+esc Go Back$"
)
_PICKER_TITLE = "Switch Model"
#: How many rows above the footer the picker's own panel may reach:
#: wide enough for a wrapped Effort description or a longer family
#: list, narrow enough that a second echo above stays outside.
_PICKER_WINDOW = 40
_SEARCH_ROW = re.compile(r"^\s*Search:\s*$")
_SEARCH_RULE = re.compile(r"^\s*─+\s*$")
_EFFORT_ROW = re.compile(r"^\s*Effort\s+.*[◂▸]")
#: One family row: the pointer glyph (``>``) or two spaces, then the
#: name; a wide-gap `` (current)`` suffix marks the current model and
#: is NOT part of the label.
_PICKER_ROW = re.compile(
    r"^(?P<pointer>>| ) (?P<label>\S.*?)(?: {2,}(?P<current>\(current\)))?$"
)


class AntigravityAdapter(AdapterBase):
    agent = "antigravity"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        picker = self._detect_model_picker(lines)
        if picker is not None:
            return picker

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

    def _detect_model_picker(self, lines: List[str]) -> Optional[StructuredDialog]:
        """The ``/model`` picker, pinned by the LIVE capture.

        The evidence, all required: the exact Keyboard footer; ONE
        ``Switch Model`` title within the bounded window above it; the
        ``Search:`` row with its underline rule directly below; a clean
        contiguous block of family rows (wide-gap ``(current)`` on at
        most one, pointer on exactly one, no repeats); exactly one
        Effort row closing the panel. Anything else is another
        version's screen and stays unclaimed.
        """
        texts = [line.rstrip() for line in lines]
        footer_at = max(
            (
                index
                for index, text in enumerate(texts)
                if _PICKER_FOOTER.match(text.strip())
            ),
            default=-1,
        )
        if footer_at < 0:
            return None
        start = max(0, footer_at + 1 - _PICKER_WINDOW)
        window = texts[start : footer_at + 1]

        titles = [
            index
            for index, text in enumerate(window)
            if text.strip() == _PICKER_TITLE
        ]
        if len(titles) != 1:
            return None
        title_at = titles[0]

        search_at = next(
            (
                index
                for index in range(title_at + 1, len(window))
                if _SEARCH_ROW.match(window[index])
            ),
            None,
        )
        if search_at is None or search_at + 1 >= len(window):
            return None
        if not _SEARCH_RULE.match(window[search_at + 1].strip()):
            return None

        effort_at = [
            index
            for index in range(search_at + 2, len(window))
            if _EFFORT_ROW.match(window[index])
        ]
        if len(effort_at) != 1:
            return None
        effort_at = effort_at[0]

        # The rows: the contiguous non-blank run between the search
        # rule and the Effort slider. A blank gap or a stranger row
        # inside is a screen this read does not understand.
        block = [
            index
            for index in range(search_at + 2, effort_at)
            if window[index].strip()
        ]
        if len(block) < 2 or block[-1] - block[0] + 1 != len(block):
            return None
        rows = []
        selected: Optional[int] = None
        current_seen = False
        for index in block:
            match = _PICKER_ROW.match(window[index])
            if match is None:
                return None
            label = match.group("label").strip()
            is_current = match.group("current") is not None
            if not label:
                return None
            if match.group("pointer") == ">":
                if selected is not None:
                    return None  # two pointers is not one list
                selected = len(rows)
            if is_current:
                if current_seen:
                    return None
                current_seen = True
            rows.append((label, is_current))
        if len({label for label, _current in rows}) != len(rows):
            return None
        if selected is None:
            # A frame without the pointer is not a live list: without a
            # `selected` the walk recipe is unverifiable, so the read
            # claims nothing (fail-closed, never half a dialog).
            return None

        title_abs = footer_at - (len(window) - 1 - title_at)
        return StructuredDialog(
            agent=self.agent,
            kind="model_picker",
            question=_PICKER_TITLE,
            options=tuple(
                DialogOption(label=label, current=is_current)
                for label, is_current in rows
            ),
            # Bounded at the footer: the quota statusline below it
            # churns every read and must never move this identity.
            fingerprint=fingerprint_region(texts[title_abs : footer_at + 1]),
            hint=texts[footer_at].strip(),
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=selected,
        )
