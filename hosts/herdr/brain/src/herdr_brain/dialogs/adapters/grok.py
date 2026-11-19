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
- ``model_picker`` (parity-f7d S2) — the ``/model`` completion list
  Grok draws above its composer: unnumbered model rows between a
  ``─N─`` count rule and a bare ``─`` rule, over the composer holding
  ``/model`` and the ``Enter:send`` hint. Pinned by real reference
  captures (fixtures/dialogs/provenance.md). No row carries a key:
  the recipe is the pointer walk with arrows and Enter.

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

#: The completion-list family: a top rule carrying the row count
#: (``────4─``), bare bottom rule, and the composer hint that names
#: the committing key. All three are required evidence.
_PICKER_TOP = re.compile(r"^\s*─+(\d+)─$")
_PICKER_BOTTOM = re.compile(r"^\s*─+$")
_PICKER_ROW = re.compile(
    r"^\s*(?P<pointer>❯ )?(?P<label>Grok \d+(?:\.\d+)+(?: Fast)?"
    r"(?: \(current\))?)(?:\s+(?P<description>\S.*))?$"
)


class GrokAdapter(AdapterBase):
    agent = "grok"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        picker = self._model_picker(lines)
        if picker is not None:
            return picker

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

    def _model_picker(self, lines) -> Optional[StructuredDialog]:
        """The ``/model`` completion list (parity-f7d S2), pinned by the
        real reference captures.

        The evidence, all required: the composer hint row ends the
        screen and names ``Enter:send``; above the composer box a bare
        ``─`` rule closes the list; the list's own top rule carries the
        ROW COUNT (``────4─``) and it must equal the number of rows
        read; every row is a Grok model (optionally the current one)
        with its description behind a wide gap; exactly one row carries
        the pointer and no label repeats. The rows are unnumbered —
        walk-class by construction, and the recipe is arrows plus
        Enter, never a digit.
        """
        texts = [line.rstrip() for line in lines]
        nonblank = [text for text in texts if text.strip()]
        if len(nonblank) < 4 or "Enter:send" not in nonblank[-1]:
            return None

        # The list's top rule: the LAST count rule on screen (a wide
        # transcript can echo an older picker above).
        top = max(
            (
                index
                for index, text in enumerate(texts)
                if _PICKER_TOP.match(text)
            ),
            default=-1,
        )
        if top < 0:
            return None
        count = int(_PICKER_TOP.match(texts[top]).group(1))

        # The bare rule that closes the list, between the rows and the
        # composer box.
        bottom = next(
            (
                index
                for index in range(top + 1, len(texts))
                if _PICKER_BOTTOM.match(texts[index])
            ),
            None,
        )
        if bottom is None:
            return None

        rows = []
        selected: Optional[int] = None
        for index in range(top + 1, bottom):
            match = _PICKER_ROW.match(texts[index])
            if match is None:
                return None
            if match.group("pointer"):
                if selected is not None:
                    return None  # two pointers is not one list
                selected = index - (top + 1)
            rows.append((match.group("label"), match.group("description") or ""))
        if not rows or len(rows) != count or selected is None:
            return None
        if len({label for label, _description in rows}) != len(rows):
            return None
        # The composer below the list holds the /model command: its row
        # is the question, verbatim in its own words.
        command = next(
            (
                text.strip("│╭╮╰ ").strip()
                for text in texts[bottom + 1 :]
                if "/model" in text
            ),
            None,
        )
        if command is None:
            return None

        return StructuredDialog(
            agent=self.agent,
            kind="model_picker",
            question=command,
            options=tuple(
                DialogOption(label=label, description=description)
                for label, description in rows
            ),
            fingerprint=fingerprint_region(texts[top:]),
            hint=nonblank[-1].strip(),
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=selected,
        )
