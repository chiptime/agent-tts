"""Shared parsing for dialogs whose options are NUMBERED rows.

Claude, Codex, Grok, Muse, OpenCode and Antigravity all draw some form
of ``1. Label`` (or ``1 (○) Label``, or ``1  Label``) with a pointer
glyph on the highlighted row. What differs — and what each adapter
must therefore declare — is the footer that pins the recipe: which key
commits, and whether the digit alone is enough.

Nothing here is harness-specific beyond the row grammar; the ADAPTER
owns every decision (footer phrases, which rows become options, which
recipe each row earns).
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

#: Pointer glyphs terminals draw on the highlighted row.
_POINTER = "❯›>→►▸"

#: ``❯ 1. Label`` / ``1) Label`` / ``1 (○) Label`` / ``1  Label`` — the
#: number the screen prints, the label after it. Radio/checkbox
#: decorations between the number and the label (Grok's ``(○)``,
#: Muse's boxes) stay ON the label: the multiselect adapters read the
#: box state from them, everyone else strips them
#: (:func:`strip_decoration`).
_OPTION_ROW = re.compile(
    r"^\s*(?P<pointer>[" + _POINTER + r"])?\s*"
    r"(?P<number>\d{1,2})"
    r"(?:\s*\.\s*|\s*\)\s*|\s+)"
    r"(?P<rest>.+?)"
    r"\s*$"
)

#: A description on the same row, separated by a wide gap (Codex ask,
#: Grok ask): the label is everything before the first 2+ space run.
_SAME_ROW_DESCRIPTION = re.compile(r"^(?P<label>.+?)\s{2,}(?P<description>\S.*)$")

#: The glyphs a checked/unchecked box or radio carries.
_CHECKED_GLYPHS = ("[x]", "[✔]", "[✓]", "(●)", "(*)", "◉", "☒")
_UNCHECKED_GLYPHS = ("[ ]", "(○)", "( )", "○", "☐", "○")

#: Free-text rows a digit cannot answer (it would focus/type into the
#: field). An option row carrying one of these labels is NOT an option.
FREE_TEXT_LABELS = (
    "type your own answer",
    "type something.",
    "tell claude what to change",
    "add notes",
)


def strip_gutter(line: str) -> str:
    """Removes one dialog gutter (``┃``/``│`` and friends) and trims."""
    return re.sub(r"^[\u2503\u2502\u2506\u2507\u250a\u250b|\u2551]\s*", "", line.rstrip()).rstrip()


def parse_option_row(line: str) -> Optional[Tuple[str, str, str, bool]]:
    """One numbered option row → ``(number, label, description, pointed)``.

    The description is the same-row tail behind a 2+ space gap (Codex
    ask, Grok ask); adapters that read descriptions on their own rows
    ignore it. Returns ``None`` for anything that is not a numbered
    row, and for the free-text rows above (their digit belongs to a
    field, not an answer).
    """
    text = strip_gutter(line)
    if not text:
        return None
    match = _OPTION_ROW.match(text)
    if match is None:
        return None
    number = match.group("number")
    rest = match.group("rest").strip()
    low = rest.lower()
    if any(low.startswith(marker) or low == marker.rstrip(".")
           for marker in FREE_TEXT_LABELS):
        return None
    label = rest
    description = ""
    split = _SAME_ROW_DESCRIPTION.match(rest)
    if split is not None:
        label = split.group("label").strip()
        description = split.group("description").strip()
    return number, label, description, bool(match.group("pointer"))


#: The radio/checkbox glyphs harnesses draw between the number and the
#: label.
_DECORATION = re.compile(r"^(?:\[[ xX✔✓]?\]|\([ xX*•○●]?\)|[○●☐☒◉])\s*")


def strip_decoration(label: str) -> str:
    """Removes a leading radio/checkbox glyph from an option label."""
    return _DECORATION.sub("", label.strip()).strip()


def split_checked(label: str) -> Tuple[str, Optional[bool]]:
    """Splits a checkbox glyph off a multiselect label.

    ``(label, checked)``; ``checked`` is None when the row shows no box
    (the submit row of a multiselect).
    """
    for glyph in _CHECKED_GLYPHS:
        if glyph in label:
            return label.replace(glyph, "").strip(), True
    for glyph in _UNCHECKED_GLYPHS:
        if glyph in label:
            return label.replace(glyph, "").strip(), False
    return label, None


def collect_numbered_block(
    lines: List[str],
) -> Optional[Tuple[int, int, List[Tuple[str, str, str, bool]]]]:
    """The longest run of consecutive numbered rows anchored at the tail.

    Returns ``(first_index, last_index, rows)`` where ``rows`` carries
    ``(number, label, description, pointed)`` per row, or ``None`` when
    fewer than two consecutive numbered rows exist. A gap in the
    numbering ends the block: menus renumber from 1, and a run that
    does not is another widget's text.
    """
    parsed: List[Tuple[int, Optional[Tuple[str, str, str, bool]]]] = []
    for index, line in enumerate(lines):
        row = parse_option_row(line)
        parsed.append((index, row))

    best: Optional[Tuple[int, int, List[Tuple[str, str, str, bool]]]] = None
    run_start = None
    previous_number = None
    run_rows: List[Tuple[str, str, str, bool]] = []

    def close(end_index: int) -> None:
        nonlocal best, run_start, previous_number, run_rows
        if run_start is not None and len(run_rows) >= 2:
            candidate = (run_start, end_index, list(run_rows))
            if best is None or len(run_rows) > len(best[2]):
                best = candidate
        run_start = None
        previous_number = None
        run_rows = []

    for index, row in parsed:
        if row is None:
            close(index - 1)
            continue
        number, _label, _description, _pointed = row
        if previous_number is not None and int(number) != previous_number + 1:
            close(index - 1)
        if run_start is None:
            run_start = index
            run_rows = []
        run_rows.append(row)
        previous_number = int(number)
    close(len(lines) - 1)
    return best


def question_above(lines: List[str], first_option: int, limit: int = 4) -> str:
    """The question lines directly above the option block.

    Walks up over blank lines and the dialog's own chrome (rules,
    step markers, timers), joining at most ``limit`` question rows.
    Description rows of the block are not question rows; nothing below
    the first option is ever read.
    """
    question: List[str] = []
    index = first_option - 1
    while index >= 0 and len(question) < limit:
        line = strip_gutter(lines[index]).strip()
        if not line:
            if question:
                break
            index -= 1
            continue
        if parse_option_row(lines[index]) is not None:
            break
        if _is_chrome(line):
            index -= 1
            continue
        question.insert(0, line)
        index -= 1
    return " ".join(question).strip()


_TIMER = re.compile(r"(running|—|·)\s*\(?[0-9]{1,3}[sm]\)?", re.IGNORECASE)
_STEP = re.compile(r"^\d{1,2}\s*/\s*\d{1,2}(\s+\(.*unanswered\))?$", re.IGNORECASE)
_RULE = re.compile(r"^[\u2500\u2501\u2550\-]{3,}$")
_POINTER_BARE = re.compile(r"^[" + _POINTER + r"]\s*$")


def _is_chrome(line: str) -> bool:
    """Rules, step markers, running timers — the dialog's furniture."""
    return bool(
        _RULE.match(line)
        or _STEP.match(line)
        or _TIMER.search(line)
        or _POINTER_BARE.match(line)
        or line.startswith("╭")
        or line.startswith("├")
        or line.startswith("╰")
    )


def step_marker_above(lines: List[str], first_option: int) -> str:
    """A step marker directly above the options, if any.

    Claude prints ``1/2`` on its own row; Codex prints
    ``Question 1/2 (N unanswered)``. Both name the current step of a
    multi-question call.
    """
    for index in range(first_option - 1, max(-1, first_option - 4), -1):
        line = strip_gutter(lines[index]).strip()
        if not line:
            continue
        match = re.match(r"^(\d{1,2}\s*/\s*\d{1,2})", line) or re.match(
            r"^Question\s+(\d{1,2}\s*/\s*\d{1,2})", line, re.IGNORECASE
        )
        if match:
            return match.group(1).replace(" ", "")
        break
    return ""
