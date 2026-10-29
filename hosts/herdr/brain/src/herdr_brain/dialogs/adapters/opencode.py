"""OpenCode dialogs: the question bar run (1.x and 2.x).

The question tool replaces the composer with a bar-run region
(``┃``) holding the question, numbered options with description rows
under their labels, and a footer whose words pin the recipe:

- ``↑↓ select  enter submit  esc dismiss`` (single select): the digit
  alone submits, pointer-independent. Recipe ``(digit,)``.
- the multi-select and Confirm-tab footers (``enter toggle``,
  ``⇆ tab … enter confirm`` with a tab strip) are UNREAD for now: the
  tab strip's state changes what every digit does, and no recipe is
  inferred.

The 2.x generation draws the same shape (registered as ``opencode2``);
the permission pill dialog keeps its own dedicated recognizer below,
which re-uses the existing ANSI pill detector and answers with the
walk the pill's own footer advertises.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import strip_gutter
from . import AdapterBase, dialog_region, footer_line

_SELECT_FOOTER = re.compile(r"↑↓\s*select.*enter\s+(?:submit|confirm)", re.IGNORECASE)
#: The multi-select footer's own words. The multi-QUESTION single
#: select footer also carries ``⇆ tab`` but commits with ``enter
#: confirm`` — the toggle verb is what marks the box card.
_MULTI_FOOTER = re.compile(r"enter\s+toggle|␣\s*toggle", re.IGNORECASE)
_BAR = "┃"
_DESCRIPTION_INDENT = re.compile(r"^\s{4,}\S")


class _QuestionAdapterBase(AdapterBase):
    def detect_question(self, lines: List[str]) -> Optional[StructuredDialog]:
        texts = [line.rstrip() for line in lines]
        # The bar run is the dialog only when it owns the tail: its
        # footer must be among the last non-blank rows.
        tail = [text for text in texts if text.strip()][-6:]
        if not any(_BAR in text for text in tail):
            return None
        footer = strip_gutter(footer_line(texts)).strip()
        if not footer:
            return None

        if _MULTI_FOOTER.search(footer):
            return unread(
                self.agent, "the tab strip changes what every digit does", lines
            )
        if not _SELECT_FOOTER.search(footer):
            return None

        # Numbered options live in the bar run, above the footer; a
        # description row indents under its option and is not numbered.
        options: List[DialogOption] = []
        last_number = 0
        footer_index = next(
            (
                index
                for index in range(len(texts) - 1, -1, -1)
                if texts[index].strip() and strip_gutter(texts[index]).strip() == footer
            ),
            len(texts),
        )
        for text in texts[:footer_index]:
            stripped = strip_gutter(text).strip()
            if not stripped:
                continue
            match = re.match(r"^(?P<number>\d{1,2})\.\s+(?P<label>.+?)\s*$", stripped)
            if match is not None:
                number = int(match.group("number"))
                if number != last_number + 1:
                    return None
                last_number = number
                label = match.group("label").strip()
                if _is_free_text(label):
                    continue
                options.append(
                    DialogOption(label=label, keys=(match.group("number"),))
                )
            elif options and _DESCRIPTION_INDENT.match(
                # The indent lives between the bar gutter and the text;
                # strip_gutter would eat it, so match on the row with
                # only the gutter character removed.
                text.lstrip("┃│")
            ):
                _append_description(options, stripped)
        if len(options) < 2:
            return None
        question = _question_above(texts, options, footer)
        if not question:
            return None
        first_option = next(
            index
            for index, text in enumerate(texts)
            if strip_gutter(text).strip().startswith("1.")
        )
        return StructuredDialog(
            agent=self.agent,
            kind="select",
            question=question,
            options=tuple(options),
            fingerprint=fingerprint_region(dialog_region(texts, first_option)),
            hint=footer,
        )


def _is_free_text(label: str) -> bool:
    return label.strip().lower().startswith("type your own answer")


def _append_description(options: List[DialogOption], row: str) -> None:
    last = options[-1]
    options[-1] = DialogOption(
        label=last.label,
        keys=last.keys,
        description=(last.description + " " + row.strip()).strip(),
        checked=last.checked,
    )


def _question_above(texts: List[str], options, footer: str) -> str:
    """The bar rows above the first numbered option: the question."""
    first_number = next(
        (
            index
            for index, text in enumerate(texts)
            if re.match(r"^\s*┃\s*1\.\s", text)
        ),
        None,
    )
    if first_number is None:
        return ""
    question: List[str] = []
    for text in reversed(texts[:first_number]):
        row = strip_gutter(text).strip()
        if not row:
            continue
        if re.match(r"^\d{1,2}\.\s", row) or row == footer:
            break
        question.insert(0, row)
    return " ".join(question).strip()


class OpencodeAdapter(_QuestionAdapterBase):
    """The 1.x question tool, plus the permission pill dialog."""

    agent = "opencode"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        question = self.detect_question(lines)
        if question is not None:
            return question
        return self._detect_permission_pill(lines, styled)

    def _detect_permission_pill(
        self, lines: List[str], styled
    ) -> Optional[StructuredDialog]:
        """The modal whose selected option is an INKED pill: selection
        exists only as colour, so the styled read is required and the
        recipe is the pill row's own footer (left/right + enter)."""
        from ..opencode_pill import pill_as_structured

        return pill_as_structured(self.agent, lines)


class Opencode2Adapter(_QuestionAdapterBase):
    """The 2.x session_v2 generation draws the same question bar."""

    agent = "opencode2"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        return self.detect_question(lines)
