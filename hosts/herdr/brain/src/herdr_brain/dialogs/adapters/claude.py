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
- ``model_picker`` (parity-f7d S2) — the ``/model`` family, pinned by
  real reference captures (fixtures/dialogs/provenance.md):

  - the ``Select model`` list: numbered rows under the footer
    ``Enter to set as default · s to use this session only · Esc to
    cancel``. The rows ARE numbered, but a digit CONFIRMS and
    rewrites the default model (ADR 0009), so the options carry NO
    keys — the recipe is the footer's own arrows plus Enter, walked
    from the ``❯`` pointer.
  - the ``Switch model?`` confirmation Claude draws after the picker
    when the conversation is cached: a modal under the ``▔`` edge
    (NOT a ``─`` rule), rows ``1. Yes, switch to <model>`` /
    ``2. No, go back`` and NO key-hint footer. Same walk recipe; the
    list wraps, so both ends move.

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
    parse_option_row,
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


#: The ``/model`` list's own footer words (the real capture's, verbatim
#: in the phrases that pin the family): ``Enter`` sets the DEFAULT and
#: ``s`` is the session-only verb — neither word appears in any other
#: Claude footer this recognizer knows.
_MODEL_LIST_FOOTER = re.compile(
    r"\benter to set as default\b.*\bs to use this session only\b"
    r".*\besc to cancel\b",
    re.IGNORECASE,
)

#: The confirmation's own title and row shapes. The list WRAPS, and the
#: modal edge is ``▔`` — a ``─`` rule is another widget.
_SWITCH_TITLE = "Switch model?"
_SWITCH_YES_PREFIX = "Yes, switch to "
_SWITCH_NO_LABEL = "No, go back"

#: How many rows above row 1 the ``▔`` modal edge may sit (prose
#: paragraphs plus wrapped label rows; a bound, not a measurement).
_SWITCH_EDGE_LOOKBACK = 10

#: How many continuation rows row 1's label may wrap onto.
_MAX_WRAPPED_ROWS = 3


class ClaudeAdapter(AdapterBase):
    agent = "claude"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        footer = footer_line(lines)
        block = collect_numbered_block(lines)
        if block is None:
            return None
        first, last, rows = block

        picker = self._model_picker(lines, footer, block)
        if picker is not None:
            return picker

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


    def _model_picker(self, lines, footer, block) -> Optional[StructuredDialog]:
        """The ``/model`` family (parity-f7d S2): the ``Select model``
        list and the ``Switch model?`` confirmation.

        Both are walk-class ALWAYS: the rows may be numbered, but a
        digit confirms and rewrites the default model (ADR 0009), so
        the options carry no keys and the model carries the screen's
        own arrows plus Enter.
        """
        if _MODEL_LIST_FOOTER.search(footer):
            first, _last, rows = block
            # The title row sits above the description paragraph and
            # the block; anything else above the rows is not this list.
            if not any(
                line.strip() == "Select model"
                for line in lines[max(0, first - 6) : first]
            ):
                return None
            return StructuredDialog(
                agent=self.agent,
                kind="model_picker",
                question="Select model",
                options=tuple(
                    DialogOption(label=label, description=description)
                    for _number, label, description, _pointed in rows
                ),
                fingerprint=fingerprint_region(dialog_region(lines, first)),
                hint=footer,
                nav_keys=("up", "down"),
                commit_keys=("enter",),
                selected=next(
                    (position for position, row in enumerate(rows) if row[3]), None
                ),
            )
        return self._switch_confirmation(lines)

    def _switch_confirmation(self, lines) -> Optional[StructuredDialog]:
        """The ``Switch model?`` modal under the ``▔`` edge.

        Plain-text port of the reference grammar: the tail is the
        numbered run ``1. Yes, switch to <model>`` / ``2. No, go
        back`` with nothing under it (no footer), the region's top is
        the MODAL EDGE (``▔``, not ``─``), the first non-blank row
        under it is exactly the title, and exactly one ``❯`` points at
        one of the two rows. Row 1's label may wrap onto indented
        continuation rows.
        """
        texts = [line.rstrip() for line in lines]

        # 1. The tail: the last non-blank row is the plain row 2.
        last = len(texts) - 1
        while last >= 0 and not texts[last].strip():
            last -= 1
        if last < 0:
            return None
        two = parse_option_row(texts[last])
        if two is None or two[0] != "2" or two[1] != _SWITCH_NO_LABEL:
            return None

        # 2. Row 1 above it, possibly wrapped onto continuation rows.
        one_at = -1
        for index in range(last - 1, max(-1, last - 1 - _MAX_WRAPPED_ROWS), -1):
            text = texts[index]
            if not text.strip():
                return None
            if parse_option_row(text) is not None:
                one_at = index
                break
        if one_at < 0:
            return None
        one = parse_option_row(texts[one_at])
        if one is None or one[0] != "1":
            return None
        wrapped = " ".join(
            text.strip() for text in texts[one_at + 1 : last]
        ).strip()
        yes_label = " ".join(part for part in (one[1], wrapped) if part)
        if not yes_label.startswith(_SWITCH_YES_PREFIX) or yes_label == _SWITCH_YES_PREFIX:
            return None

        # 3. The region's top is the MODAL EDGE, and the title is the
        #    first non-blank row under it, exactly.
        edge_at = -1
        for index in range(one_at - 1, max(-1, one_at - 1 - _SWITCH_EDGE_LOOKBACK), -1):
            text = texts[index].strip()
            if not text:
                continue
            if text.startswith("▔"):
                edge_at = index
                break
        if edge_at < 0:
            return None
        # The first non-blank row UNDER the edge is the title, exactly.
        probe = edge_at + 1
        while probe < one_at and not texts[probe].strip():
            probe += 1
        if probe >= one_at or texts[probe].strip() != _SWITCH_TITLE:
            return None

        # 4. Exactly one pointer, on row 1 or row 2.
        pointers = sum(
            text.count("❯") for text in texts[edge_at : last + 1]
        )
        if pointers != 1 or one[3] == two[3]:
            return None

        return StructuredDialog(
            agent=self.agent,
            kind="model_picker",
            question=_SWITCH_TITLE,
            options=(
                DialogOption(label=yes_label),
                DialogOption(label=_SWITCH_NO_LABEL),
            ),
            fingerprint=fingerprint_region(dialog_region(lines, one_at)),
            hint="",
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=0 if one[3] else 1,
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
