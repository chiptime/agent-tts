"""OpenCode dialogs: the question bar run (1.x and 2.x).

The question tool replaces the composer with a bar-run region
(``┃``) holding the question, numbered options with description rows
under their labels, and a footer whose words pin the recipe:

- ``↑↓ select  enter submit  esc dismiss`` (single select): the digit
  alone submits, pointer-independent. Recipe ``(digit,)``.
- ``⇆ tab  ↑↓ select  enter toggle  esc dismiss`` (multi select): the
  tabbed list the reference product measured keystroke by keystroke
  on opencode 1.18.33/34 and 2.1.1 — a digit TOGGLES its own box
  (pointer-independent), Space does nothing, and the commit is the
  ``Confirm``/``Submit`` tab's own ``tab`` then ``enter``. Only that
  exactly-pinned shape upgrades to ``multiselect`` (see
  :meth:`_QuestionAdapterBase._multiselect`); every other multi-ish
  footer stays UNREAD below, exactly as before.
- the multi-QUESTION single-select footer (``⇆ tab … enter
  confirm``): the digit selects and advances — read as today's plain
  ``select``; the Confirm-tab review (``⇆ tab  enter submit``) claims
  nothing (``None``).

The 2.x generation draws the same shapes (registered as ``opencode2``;
2.1 adds a grey ``Questions`` title and spells the last tab chip
``Submit``); the permission pill dialog keeps its own dedicated
recognizer below, which re-uses the existing ANSI pill detector and
answers with the walk the pill's own footer advertises.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import split_checked, strip_gutter
from . import AdapterBase, dialog_region, footer_line

_SELECT_FOOTER = re.compile(r"↑↓\s*select.*enter\s+(?:submit|confirm)", re.IGNORECASE)
#: The multi-select footer's own words. The multi-QUESTION single
#: select footer also carries ``⇆ tab`` but commits with ``enter
#: confirm`` — the toggle verb is what marks the box card.
_MULTI_FOOTER = re.compile(r"enter\s+toggle|␣\s*toggle", re.IGNORECASE)
#: The EXACTLY pinned multi-select footer (whitespace-insensitive):
#: the reference's own footer grammar — ``enter (submit|toggle|
#: confirm) esc dismiss`` with the ``⇆ tab`` and ``↑↓ select`` hints
#: (markers.ts QUESTION_FOOTER_ROW + questionFooter's tabs/list) —
#: restricted to the one verb/tabs/list combination the reference
#: lifted as the multi-select list (question-tabs.ts: ``verb=toggle``
#: with the tab bar and the list). Every other ``enter toggle``
#: footer falls back to the unread umbrella.
_MULTI_PINNED_FOOTER = re.compile(
    r"⇆\s*tab\b.*?↑↓\s*select\b.*?enter\s+toggle\b.*?esc\s+dismiss\b",
    re.IGNORECASE,
)
_BAR = "┃"
_DESCRIPTION_INDENT = re.compile(r"^\s{4,}\S")
_NUMBERED_ROW = re.compile(r"^(?P<number>\d{1,2})\.\s+(?P<label>.+?)\s*$")
#: The last chip of the tab strip: ``Confirm`` (1.18.x) or ``Submit``
#: (opencode 2.1) — the reference's LAST_TAB_LABELS.
_LAST_TAB_CHIP = frozenset({"Confirm", "Submit"})


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
            pinned = self._multiselect(texts, footer)
            if pinned is not None:
                return pinned
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
            match = _NUMBERED_ROW.match(stripped)
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

    def _multiselect(
        self, texts: List[str], footer: str
    ) -> Optional[StructuredDialog]:
        """The tabbed multi-select list, pinned to the versions the
        reference product measured (opencode 1.18.33/34 and 2.1.1).

        Everything here is fail-closed to the unread umbrella (the
        caller) unless the screen is EXACTLY the pinned shape: the
        four-hint footer, a checkbox on every numbered row, the
        question-chip tab strip (ending ``Confirm``/``Submit``) above
        the question, and — at most, LAST — the free-text row whose
        digit opens an input and is therefore never an option.

        The recipe facts are the reference's own measurements: a digit
        TOGGLES its own box whatever the pointer is on (each option's
        ``keys``), and the commit is the Confirm tab's own ``tab`` +
        ``enter`` — no row on this screen carries it, so the model
        offers NO submit row and carries the tab chips instead
        (``tab_strip``): the choices choreography derives the commit
        from them. The pointer is painted STYLE (a colour chip),
        invisible in text: no walk is claimed. The fingerprint erases
        the box glyphs — the choreography flips them on purpose.
        """
        flat = " ".join(footer.split())
        if not _MULTI_PINNED_FOOTER.search(flat):
            return None

        footer_index = next(
            (
                index
                for index in range(len(texts) - 1, -1, -1)
                if texts[index].strip()
                and strip_gutter(texts[index]).strip() == footer
            ),
            len(texts),
        )
        boxes: List[DialogOption] = []
        free_text = False
        last_number = 0
        first_option: Optional[int] = None
        for index in range(footer_index):
            text = texts[index]
            stripped = strip_gutter(text).strip()
            if not stripped:
                continue
            match = _NUMBERED_ROW.match(stripped)
            if match is not None:
                if first_option is None:
                    first_option = index
                number = int(match.group("number"))
                if number != last_number + 1:
                    return None
                last_number = number
                raw_label = match.group("label")
                if not raw_label.startswith("["):
                    return None  # a checkbox step's row carries a box
                label, checked = split_checked(raw_label)
                if checked is None:
                    return None
                if _is_free_text(label):
                    if free_text:
                        return None  # the label may name only one row
                    free_text = True
                    continue
                if free_text:
                    return None  # nothing numbered rides below the row
                boxes.append(
                    DialogOption(
                        label=label, keys=(match.group("number"),), checked=checked
                    )
                )
            elif first_option is None:
                continue  # above the list: read by the frame walk below
            elif free_text:
                continue  # committed free text rides under its own row
            elif _DESCRIPTION_INDENT.match(text.lstrip("┃│")):
                _append_description(boxes, stripped)
            else:
                return None  # a foreign row inside the list is not this grammar
        if first_option is None or len(boxes) < 2:
            return None

        # The frame above the list: the question paragraph, then the
        # tab strip. A numbered row above is another widget's list; a
        # row outside the bar run is foreign chrome.
        question_rows: List[str] = []
        tab_chips: Optional[List[str]] = None
        index = first_option - 1
        while index >= 0 and first_option - index <= 12:
            if _BAR not in texts[index]:
                return None
            stripped = strip_gutter(texts[index]).strip()
            if not stripped:
                index -= 1
                continue
            if _NUMBERED_ROW.match(stripped) is not None:
                return None
            chips = _tab_chips(stripped)
            if chips is not None:
                tab_chips = chips
                break
            question_rows.insert(0, stripped)
            index -= 1
        if tab_chips is None or not question_rows:
            return None

        return StructuredDialog(
            agent=self.agent,
            kind="multiselect",
            question=" ".join(question_rows).strip(),
            options=tuple(boxes),
            fingerprint=fingerprint_region(
                _boxes_normalized(dialog_region(texts, first_option))
            ),
            hint=footer,
            tab_strip=tuple(tab_chips),
        )


def _is_free_text(label: str) -> bool:
    return label.strip().lower().startswith("type your own answer")


#: The Confirm/Submit tab's review footer (whitespace-insensitive):
#: the same hint family minus the list verbs — neither ``↑↓ select``
#: nor ``enter toggle`` survives on the last tab (QUESTION_NOTES.md
#: footer table; the review captures
#: ``oc--question--multi--confirm.txt`` / ``oc--v211-question--
#: multi--review.txt``). The negative half refuses every list footer
#: of the same family, so only the review claims it.
_REVIEW_PINNED_FOOTER = re.compile(
    r"⇆\s*tab\b.*?enter\s+submit\b.*?esc\s+dismiss\b",
    re.IGNORECASE,
)
_REVIEW_LIST_VERBS = re.compile(r"↑↓\s*select|enter\s+toggle", re.IGNORECASE)
#: How far above the review footer the tab strip may sit: the review
#: body is one ``Header: value`` row per question and 1.x heads it
#: with ``Review``, so two chips sit at most a handful of rows up.
_REVIEW_STRIP_LOOKBACK = 8


def confirm_review(lines: List[str]) -> Optional[List[str]]:
    """The tab chips when the screen is the Confirm/Submit tab's review
    of a tabbed question dialog, else ``None``.

    This is the LANDING the commit choreography must read back after
    ``tab`` (T4b): the review footer pins the shape, and the tab strip
    — the same chips the list carried — names WHICH dialog's review it
    is. The review body's ``Header: value`` rows are not parsed: the
    answers were already verified box by box on the list, and the
    body's own words are the terminal's retelling, not a contract.
    """
    texts = [line.rstrip() for line in lines]
    tail = [text for text in texts if text.strip()][-6:]
    if not any(_BAR in text for text in tail):
        return None
    footer = strip_gutter(footer_line(texts)).strip()
    if not footer:
        return None
    flat = " ".join(footer.split())
    if not _REVIEW_PINNED_FOOTER.search(flat):
        return None
    if _REVIEW_LIST_VERBS.search(flat):
        return None

    footer_index = next(
        (
            index
            for index in range(len(texts) - 1, -1, -1)
            if texts[index].strip()
            and strip_gutter(texts[index]).strip() == footer
        ),
        len(texts),
    )
    # Above the footer: the review body (`Header: value` rows, 1.x's
    # `Review` heading, bare rows), then the tab strip. A body row is
    # single-spaced words, so it never parses as two-or-more chips.
    for index in range(footer_index - 1, max(-1, footer_index - 1 - _REVIEW_STRIP_LOOKBACK), -1):
        chips = _tab_chips(strip_gutter(texts[index]).strip())
        if chips is not None:
            return chips
    return None


def _tab_chips(row: str) -> Optional[List[str]]:
    """The question-chip tab strip, or ``None`` when the row is not
    one: at least two chips of words one space apart, separated by
    two-or-more spaces, ending in opencode's own last chip —
    ``Confirm`` (1.18.x) or ``Submit`` (2.1). Which chip is ACTIVE is
    painted style, invisible in text, and correctly not modelled."""
    chips = [chip.strip() for chip in re.split(r"\s{2,}", row.strip())]
    if len(chips) < 2 or chips[-1] not in _LAST_TAB_CHIP:
        return None
    if any(not chip for chip in chips) or len(set(chips)) != len(chips):
        return None
    return chips


def _boxes_normalized(region: List[str]) -> List[str]:
    """The region with the checkbox glyphs erased — the answer
    choreography flips them on purpose, so the raw digest would
    falsify the race guard with the screen's own toggles (state
    transitions are verified by re-reading). The pointer is painted
    STYLE in opencode, invisible in text, so the boxes are the only
    state that moves."""
    return [re.sub(r"\[[xX✔✓]\]", "[ ]", line).rstrip() for line in region]


def _append_description(options: List[DialogOption], row: str) -> None:
    last = options[-1]
    options[-1] = DialogOption(
        label=last.label,
        keys=last.keys,
        description=(last.description + " " + row.strip()).strip(),
        checked=last.checked,
    )


# ── the /models picker (LIVE capture 2026-11-19, opencode 1.19.5) ────
#
# The composer command ``/models`` floats a borderless modal over the
# TUI: a ``Select model`` title (an ``esc`` hint at its right edge), a
# ``Search`` input, the model rows, provider group headers among them,
# and the ``Connect provider ctrl+a  Favorite ctrl+f`` footer. The
# modal has no box borders, so the underlying screen's own text bleeds
# at both margins of every row — over a LIVE chat the conversation
# text sits LEFT of every modal column and the TUI's status lists sit
# RIGHT of it (see ``opencode-models-picker-over-chat.txt``). The
# grammar therefore anchors the modal by COLUMN: the title and the
# footer pin a consistent column window, every row is read as the
# SUBSTRING inside that window, and whatever rides outside the window
# — left or right — is not the modal's. The label is the slice's
# leading text, cut at the first wide gap. Fail-closed on the full
# evidence set: title + Search + rows + the exact footer, all inside
# one column window — any other shape is somebody else's screen.
#
# Two facts shape the recipe:
#
# - the CURRENT model is the row prefixed ``●`` (two cells left of the
#   label column); it is carried as the additive ``current`` mark;
# - the POINTER is ANSI paint — invisible in the text every
#   recognizer reads — so ``selected`` is NEVER claimed, ``nav_keys``
#   stay empty (this footer names no arrows), and the only honest
#   recipe is SEARCH-FIRST: typing into the Search box filters the
#   list, and Enter commits it only once exactly one row names the
#   target. Never a digit key (ADR 0009); the typed name is literal
#   text, not a keypress.

_MODELS_TITLE = re.compile(r"(?:^|(?<=  ))Select model(?:\s|$)")
_MODELS_FOOTER = re.compile(r"Connect provider +ctrl\+a {2,}Favorite +ctrl\+f")
_MODELS_SEARCH_ROW = "Search"
#: The picker's own rows: enough to be a list, few enough to be one
#: modal and not the whole screen.
_MODELS_MAX_ROWS = 40
_MODELS_SEARCH_LOOKAHEAD = 4
_MODELS_CURRENT = "●"
_MODELS_LABEL_GAP = re.compile(r" {2,}")


def _models_label(line: str, column: int, end: int) -> str:
    """One picker row's label: the leading text of the row's slice at
    the title's column, cut at the first wide gap (the margin noise
    and the right-edge badges live behind that gap). ``end`` bounds
    the slice at the modal's column window — the screen's own text
    bleeding right of the box is not the modal's."""
    segment = line[column:end].strip()
    if not segment:
        return ""
    return _MODELS_LABEL_GAP.split(segment, 1)[0].strip()


def detect_models_picker(
    agent: str,
    lines: List[str],
    filled_search: bool = False,
    query: str = "",
) -> Optional[StructuredDialog]:
    """The opencode ``/models`` picker as a ``model_picker`` dialog.

    ``filled_search`` reads the state AFTER the choreography typed into
    the Search box: the ``Search`` placeholder is then the query, and
    with ``query`` given the search row must ECHO it — the verifiable
    signal that the filter is ours and live. The filtered list may be
    empty (the strict adapter never claims that state; the search-first
    choreography reads it to refuse with ``no-match`` instead of a
    silent ``changed``).

    Fail-closed: the full evidence set (title, search row, rows, the
    ctrl+a/ctrl+f footer, at most one ``●`` row) — all inside ONE
    column window, the title and the footer pinned at the same column
    — or ``None``.
    """
    texts = [line.rstrip() for line in lines]

    footer_at = next(
        (
            index
            for index in range(len(texts) - 1, -1, -1)
            if _MODELS_FOOTER.search(texts[index])
        ),
        None,
    )
    if footer_at is None:
        return None

    title_at = None
    for index in range(footer_at - 1, -1, -1):
        if "Select model" in texts[index] and _MODELS_TITLE.search(texts[index]):
            title_at = index
            break
    if title_at is None:
        return None
    column = texts[title_at].index("Select model")

    # The modal's column window: the box is centered, its title and
    # its footer pad to the SAME left column (both live captures pin
    # them equal). A footer floating at another column is not this
    # modal's — the screen behind it may be anything.
    footer_match = _MODELS_FOOTER.search(texts[footer_at])
    if footer_match.start() != column:
        return None
    span_end = footer_match.end()

    # The search input: the first non-blank row below the title. The
    # strict read requires the placeholder; a filled box may hold the
    # query, which must then be echoed back.
    search_at = next(
        (
            index
            for index in range(
                title_at + 1,
                min(title_at + 1 + _MODELS_SEARCH_LOOKAHEAD, footer_at),
            )
            if _models_label(texts[index], column, span_end)
        ),
        None,
    )
    if search_at is None:
        return None
    search_row = _models_label(texts[search_at], column, span_end)
    if not filled_search and search_row != _MODELS_SEARCH_ROW:
        return None
    if filled_search and query:
        echo = " ".join(query.split()).lower()
        if echo not in " ".join(
            texts[search_at][column:span_end].split()
        ).lower():
            return None

    options: List[DialogOption] = []
    current = 0
    for index in range(search_at + 1, footer_at):
        label = _models_label(texts[index], column, span_end)
        if not label:
            continue
        marked = (
            column >= 2
            and texts[index][column - 2 : column] == _MODELS_CURRENT + " "
        )
        current += 1 if marked else 0
        options.append(DialogOption(label=label, current=marked))
    if current > 1:
        return None
    if not options and not filled_search:
        return None
    if len(options) > _MODELS_MAX_ROWS:
        return None

    return StructuredDialog(
        agent=agent,
        kind="model_picker",
        question="Select model",
        options=tuple(options),
        fingerprint=fingerprint_region(texts[title_at : footer_at + 1]),
        hint=" ".join(_MODELS_FOOTER.search(texts[footer_at]).group(0).split()),
        nav_keys=(),
        commit_keys=("enter",),
        selected=None,
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
    """The 1.x question tool, plus the permission pill dialog and the
    ``/models`` picker."""

    agent = "opencode"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        question = self.detect_question(lines)
        if question is not None:
            return question
        picker = detect_models_picker(self.agent, lines)
        if picker is not None:
            return picker
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
    """The 2.x session_v2 generation draws the same question bar — and
    the same ``/models`` picker (fail-closed: a 2.x layout the grammar
    does not know claims nothing)."""

    agent = "opencode2"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        question = self.detect_question(lines)
        if question is not None:
            return question
        return detect_models_picker(self.agent, lines)
