"""Claude Code dialogs: numbered prompts whose footer names the recipe.

The shapes this recognizer claims (each pinned by a synthesized
fixture in the same layout Claude draws):

- ``select`` — AskUserQuestion: options ``❯ 1. Label``, footer
  ``Enter to select · ↑/↓ to navigate · Esc to cancel``. The digit
  moves the pointer; Enter commits. Recipe: ``(digit, enter)``. A
  free-text row (``Type something.``, S3) is KEPT as an option with
  ``free_text=True`` whose digit alone focuses the field — the answer
  is typed by the server's recipe, never committed on the digit. The
  multiselect keeps its closed grammar (checkbox rows + one submit
  row): with no real capture pinning the free-text glyph there, a
  free-text row inside it still declines.
- ``multiselect`` — AskUserQuestion with multiple selections allowed:
  checkbox rows (``Empty checkbox``/``Filled checkbox``, or the
  ``[ ]``/``[✔]`` glyph pair) plus the numbered ``Submit`` row, footer
  ``Space to select · Enter to submit · … · Esc to cancel``. The digit
  ALONE toggles its box; the pointer walks to the submit row with the
  footer's arrows and Enter commits there. The fingerprint is taken
  over the region with pointer and checkbox state NORMALIZED: the
  answer choreography flips boxes and moves the pointer on purpose, so
  the raw digest would falsify the race guard with the screen's own
  movements (state transitions are verified by re-reading).
- ``permission`` — the tool permission dialog: footer
  ``Tab to amend …`` (or a bare ``Esc to cancel`` under a
  "Do you want to" question). The digit alone commits.
- ``trust`` — the folder-trust prompt: ``Enter to confirm`` footer
  under a "Do you trust the files in this folder?" question. The digit
  alone commits.
- wizard steps — a ``1/2`` step marker above the question makes the
  same select shape a ``wizard_step`` (answering may advance).

A pointer sitting on a free-text row (``Type something.``) means the
field owns the keyboard and every digit becomes text — which is exactly
why the row is modelled ``free_text=True`` (S3): its digit recipe only
FOCUSES the field, and the typed answer rides the server's verified
typing recipe, never a digit commit.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import (
    collect_numbered_block,
    is_free_text_label,
    question_above,
    split_checked,
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

#: The multi-select footer: the SAME family rhythm as select but naming
#: space (the toggle key) and ``enter to submit`` — both phrases the
#: single-select footer never carries, so the two footers cannot be
#: confused in either direction.
_MULTI_FOOTER = re.compile(
    r"\bspace to select\b.*\benter to submit\b.*\besc to cancel\b", re.IGNORECASE
)

#: The word-form checkbox glyphs Claude's TUI draws between the number
#: and the label (the ``[ ]``/``[✔]`` glyph pair is read by
#: :func:`~herdr_brain.dialogs.numbered.split_checked`).
_CHECKED_WORD = "filled checkbox"
_UNCHECKED_WORD = "empty checkbox"

#: The one row that submits a multi-select (muse draws the same tail
#: row: ``N. Submit answer (M checked)``).
_SUBMIT_ROW = re.compile(r"^submit$", re.IGNORECASE)

#: Pointer glyphs the family draws on the highlighted row.
_POINTER_GLYPHS = "❯›>→►▸"

#: A step marker the stepper can trust (S4): ``N/M`` with a sane ratio.
#: Anything else keeps today's model (marker string, no step metadata)
#: and the PWA renders the dialog as a plain one.
_STEP_META = re.compile(r"^(\d{1,2})/(\d{1,2})$")


def _step_meta(marker: str) -> Tuple[Optional[int], Optional[int]]:
    """``"1/2"`` → ``(1, 2)``; every other shape → ``(None, None)``."""
    match = _STEP_META.match(marker)
    if not match:
        return None, None
    step, total = int(match.group(1)), int(match.group(2))
    if step < 1 or total < 1 or step > total:
        return None, None
    return step, total


class ClaudeAdapter(AdapterBase):
    agent = "claude"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block

        if _MULTI_FOOTER.search(footer):
            return self._multiselect(footer, lines, first, rows)
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
        step_number, step_total = _step_meta(step)
        # S3: the select family keeps the free-text rows of the block —
        # re-collect with them counted as rows instead of block-enders.
        # The multiselect above keeps the default (its closed checkbox
        # grammar pins nothing about a free-text row's glyph there).
        free_block = collect_numbered_block(lines, keep_free_text=True)
        if free_block is not None:
            first, _last, rows = free_block
        return StructuredDialog(
            agent=self.agent,
            kind="wizard_step" if (step and kind == "select") else kind,
            question=question,
            options=tuple(
                DialogOption(
                    label=label,
                    keys=(number,) if is_free_text_label(label) else (number, *suffix),
                    free_text=is_free_text_label(label),
                )
                for number, label, _description, _pointed in rows
            ),
            fingerprint=fingerprint_region(dialog_region(lines, first)),
            hint=footer,
            step=step,
            step_number=step_number,
            step_total=step_total,
            selected=next(
                (position for position, row in enumerate(rows) if row[3]), None
            ),
        )

    def _multiselect(
        self, footer, lines, first, rows
    ) -> Optional[StructuredDialog]:
        """The checkbox shape — fail-closed unless EVERY row is a
        checkbox or the one trailing Submit row."""
        question = question_above(lines, first)
        if not question:
            return None

        options: List[DialogOption] = []
        boxes = 0
        submit = False
        for number, label, description, _pointed in rows:
            # A wide gap after the glyph would split the label from its
            # own description: the checkbox state belongs to the whole
            # row, so the parts are read as one.
            text = f"{label} {description}".strip() if description else label
            clean, checked = _checkbox_state(text)
            if checked is None:
                if submit or not options or not _SUBMIT_ROW.match(clean):
                    # Neither a checkbox nor the single trailing submit
                    # row: another widget's shape, never a guess.
                    return None
                submit = True
                options.append(DialogOption(label=clean, keys=()))
            else:
                if submit:
                    return None  # nothing rides below the submit row
                boxes += 1
                options.append(
                    DialogOption(label=clean, keys=(number,), checked=checked)
                )
        if boxes < 2 or not submit:
            return None

        # A multi-select step of a wizard keeps its answering kind and
        # gains the marker + stepper metadata (S4): the PWA drives it
        # with the same choices path, one step at a time.
        step = step_marker_above(lines, first)
        step_number, step_total = _step_meta(step)
        return StructuredDialog(
            agent=self.agent,
            kind="multiselect",
            question=question,
            options=tuple(options),
            fingerprint=fingerprint_region(
                _state_normalized(dialog_region(lines, first))
            ),
            hint=footer,
            step=step,
            step_number=step_number,
            step_total=step_total,
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=next(
                (position for position, row in enumerate(rows) if row[3]), None
            ),
        )


def _checkbox_state(text: str) -> Tuple[str, Optional[bool]]:
    """One option row → ``(clean label, checked)``.

    ``checked`` is ``None`` when the row carries no box at all (the
    submit row, or a row this shape does not own).
    """
    low = text.lower()
    if _CHECKED_WORD in low:
        return re.sub(_CHECKED_WORD, "", text, flags=re.IGNORECASE).strip(), True
    if _UNCHECKED_WORD in low:
        return re.sub(_UNCHECKED_WORD, "", text, flags=re.IGNORECASE).strip(), False
    return split_checked(text)


def _state_normalized(lines: List[str]) -> List[str]:
    """The region with pointer and checkbox state erased.

    Only the multiselect fingerprint uses this: its answer flips boxes
    and walks the pointer BY DESIGN, so the raw digest would report the
    screen's own choreography as drift. Everything else in the region
    — question, labels, footer — still feeds the digest.
    """
    normalized = []
    for line in lines:
        text = re.sub(
            rf"\b(?:{_CHECKED_WORD}|{_UNCHECKED_WORD})\b", "#", line, flags=re.IGNORECASE
        )
        text = re.sub(r"\[[ xX✔✓]?\]", "#", text)
        text = re.sub(f"[{re.escape(_POINTER_GLYPHS)}]", "", text)
        normalized.append(re.sub(r"\s{2,}", " ", text).rstrip())
    return normalized
