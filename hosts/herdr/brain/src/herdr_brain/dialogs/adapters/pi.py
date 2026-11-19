"""pi / Oh My Pi: the Ask box, and omp's compact Switch Model box.

The shapes this recognizer claims:

- the Ask box (pinned by a synthesized fixture in the layout pi draws
  for its ``ask`` tool):

::

    ╭─ Ask ──────────────╮
    │ Pick a color       │
    ├────────────────────┤
    │ ❯ ○ Red            │
    │   ○ Green          │
    │   ○ Other …        │
    ├────────────────────┤
    │ Enter select · n note · ↑/↓ move · Esc cancel
    ╰────────────────────╯

pi prints NO digit: the only recipe is the screen's own arrows plus
Enter, walked from the pointed row — so the options carry no keys and
the model carries the walk facts (``nav_keys``/``commit_keys``/
``selected``). The multi-select box is UNREAD: Enter's meaning there
differs across pi versions, and no recipe is inferred.

- the omp ``/switch`` compact model picker (parity-f7d S2), pinned by
  the REAL 18.4 reference capture (fixtures/dialogs/provenance.md) —
  omp is built on pi's TUI, so the ``omp`` agent alias reaches this
  recognizer too. A boxed list of ``provider/id`` rows with badge
  columns, a search row, two detail rows that follow the pointer, and
  the exact session footer. Model rows are unnumbered — walk-class by
  construction (arrows plus Enter, never a digit: in this list a digit
  would confirm and rewrite the default model, ADR 0009). A row omp
  shortened with ``…`` has no full id on screen: the whole box
  declines rather than guess where a walk would land.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import strip_gutter
from . import AdapterBase, dialog_region, footer_line


def _bare(row: str) -> str:
    """A pi row without its box walls: gutter left, ``│`` right."""
    return strip_gutter(row).rstrip("│┃").strip()

_TITLE = re.compile(r"^╭─ Ask ─")
_DIVIDER = re.compile(r"^├[─┼]*┤?\s*$|^├─*[┼┤]?\s*$")
_OPTION = re.compile(r"^\s*(?P<pointer>[❯›>])?\s*(?P<radio>[○●]|◉?)\s*(?P<label>\S.*)$")
_SELECT_FOOTERS = (
    re.compile(r"⏎\s*select", re.IGNORECASE),
    re.compile(r"\benter\s+select\b", re.IGNORECASE),
)
_MULTI_FOOTERS = (
    re.compile(r"space/?enter\s+toggle", re.IGNORECASE),
    re.compile(r"␣\s*toggle", re.IGNORECASE),
)

# ── the omp /switch compact model picker (parity-f7d S2) ────────────
#
# A plain-text port of the reference grammar (omp 18.4, SWITCH_NOTES):
# the box title, the status sentence, the search row, a 5–60 row list
# window, a blank, two detail rows about the POINTED model, the exact
# session footer and the bottom border. Every piece is required.

_SW_TITLE = re.compile(r"^╭─ Switch Model ─+╮$")
_SW_STATUS = re.compile(r"^│ {2}Session-only switch — role models stay unchanged\s*│$")
_SW_SEARCH = re.compile(r"^│ {2}🔍 >(?: (.*?))?\s*│$")
_SW_BOTTOM = re.compile(r"^╰─+╯$")
_SW_BLANK = re.compile(r"^│\s*│$")
_SW_FOOTER = (
    "↑/↓ models · ⏎ use for this session · type to search · "
    "@ quick roles · ⎋ close · Alt+P task model"
)
_SW_MIN_WINDOW = 5
_SW_MAX_WINDOW = 60
_SW_POINTER = "❯"
_SW_SCROLL_CELLS = frozenset("█│")

#: One model row's content after the pointer column: the full
#: ``provider/id``, `` ●`` on the current model, `` ⦸ context>N`` on an
#: over-context one, then the badge columns.
_SW_MODEL_ROW = re.compile(r"^(\S+/\S+)( ●)?( ⦸ context>\S+)?(?: +(\S.*))?$")
_SW_RULE = re.compile(r"^ {2}─+$")

#: The badge columns (one per kind, the unicode preset), each a cell
#: padded on the left and separated by two-or-more spaces.
_SW_BADGE_CELLS = (
    re.compile(r"^🧠 \d+$"),
    re.compile(r"^(?:\d+(?:\.\d+)?s )?~?\d+(?:\.\d+)?t/s$"),
    re.compile(r"^\d+(?:\.\d+)?[kmbt]? ◫$"),
    re.compile(r"^(?:\$[\d.?]+/[\d.?]+(?: [\d.?]+×)?|[\d.?]+×|free|included|varies|unknown)$"),
)

#: The second detail row: status chips, or the over-context warning.
_SW_CHIP = re.compile(r"^[●○] \S(?:[^·]*\S)?$")
_SW_OVER_CONTEXT = re.compile(r"^⦸ context \S+ exceeds \S+ limit")


class PiAdapter(AdapterBase):
    agent = "pi"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        texts = [line.rstrip() for line in lines]
        if any(_TITLE.match(text) for text in texts):
            ask = self._ask_box(texts)
            if ask is not None:
                return ask
        return detect_switch_box(self.agent, texts)

    def _ask_box(self, texts: List[str]) -> Optional[StructuredDialog]:
        footer = footer_line(texts)
        cleaned = _bare(footer)
        if any(pattern.search(cleaned) for pattern in _MULTI_FOOTERS):
            return unread(self.agent, "multi-select Enter semantics differ by version", texts)
        if not any(pattern.search(cleaned) for pattern in _SELECT_FOOTERS):
            return None

        # The body: rows between the divider under the question and the
        # divider over the footer.
        try:
            dividers = [i for i, text in enumerate(texts) if _DIVIDER.match(text.strip())]
            body_start = dividers[0] + 1
            body_end = dividers[-1]
        except IndexError:
            return None
        options: List[DialogOption] = []
        selected: Optional[int] = None
        for text in texts[body_start:body_end]:
            row = _bare(text)
            match = _OPTION.match(row)
            if match is None:
                continue
            label = match.group("label").strip()
            if not label:
                continue
            if match.group("pointer"):
                selected = len(options)
            options.append(DialogOption(label=label))
        if len(options) < 2 or selected is None:
            return None
        question = _question_between(texts, dividers[0])
        if not question:
            return None
        return StructuredDialog(
            agent=self.agent,
            kind="select",
            question=question,
            options=tuple(options),
            fingerprint=fingerprint_region(dialog_region(texts, body_start)),
            hint=cleaned,
            nav_keys=("up", "down"),
            commit_keys=("enter",),
            selected=selected,
        )


def _question_between(texts: List[str], divider: int) -> str:
    """The question rows between the title and the first divider."""
    question: List[str] = []
    for text in reversed(texts[:divider]):
        row = _bare(text)
        if not row:
            continue
        if _TITLE.match(row):
            break
        question.insert(0, row)
    return " ".join(question).strip()


def detect_switch_box(agent: str, texts: List[str]) -> Optional[StructuredDialog]:
    """The omp ``/switch`` compact model picker (parity-f7d S2).

    Plain-text port of the reference grammar, fail-closed on every
    piece: the bottom border closes the tail, the footer above it is
    the exact session footer, a blank and two detail rows (the second
    must agree with the pointed row's marks), the box title with its
    status and search rows, and a 5–60 row window of model rows, the
    rule omp draws between the recents and the rest, and blank padding.
    A row omp shortened with ``…`` declines the whole box: its id is
    not on screen, so no walk could be verified. The rows are
    unnumbered — the recipe is the pointer walk with arrows and Enter,
    never a digit.
    """
    end = len(texts) - 1
    while end >= 0 and not texts[end].strip():
        end -= 1
    if end < 0 or not _SW_BOTTOM.match(texts[end]):
        return None
    footer_text = _boxed(texts[end - 1])
    if footer_text is None or footer_text.strip() != _SW_FOOTER:
        return None
    detail_facts = texts[end - 3]
    detail_chips = texts[end - 2]
    if not _SW_BLANK.match(texts[end - 4] or ""):
        return None
    if not re.match(r"^│ {3}\S", detail_facts or ""):
        return None

    # The title, nearest above the window's fixed rows.
    title_at = next(
        (
            index
            for index in range(end - 5, max(-1, end - 5 - (_SW_MAX_WINDOW + 12)), -1)
            if _SW_TITLE.match(texts[index])
        ),
        None,
    )
    if title_at is None:
        return None
    if not _SW_STATUS.match(texts[title_at + 1] or ""):
        return None
    if _SW_SEARCH.match(texts[title_at + 2] or "") is None:
        return None
    if not _SW_BLANK.match(texts[title_at + 3] or ""):
        return None

    window_start = title_at + 4
    window_end = end - 5  # inclusive
    rows_count = window_end - window_start + 1
    if not _SW_MIN_WINDOW <= rows_count <= _SW_MAX_WINDOW:
        return None
    rows = _read_switch_window(texts[window_start : window_end + 1])
    if rows is None:
        return None
    pointed = [row for row in rows if row["pointed"]]
    if len(pointed) != 1:
        return None
    if len([row for row in rows if row["current"]]) > 1:
        return None
    if len([row for row in rows if row["rule"]]) > 1:
        return None
    # The chips row must agree with the pointed row's marks (the
    # warning replaces the chips; a shortened row lost its marks).
    chips = _read_switch_chips(detail_chips)
    if chips is None:
        return None
    pointed_row = pointed[0]
    if chips["over_context"] != pointed_row["over_context"]:
        return None
    if not chips["over_context"] and chips["current"] != pointed_row["current"]:
        return None

    # The walk index counts MODEL rows only: omp's arrows skip the
    # rule row (a disabled item), exactly as the options list does.
    selected = sum(1 for row in rows[: rows.index(pointed_row)] if not row["rule"])
    return StructuredDialog(
        agent=agent,
        kind="model_picker",
        question="\n".join(
            [
                "Switch Model",
                _boxed(texts[title_at + 1]) or "",
                _boxed(texts[title_at + 2]) or "",
            ]
        ),
        options=tuple(
            DialogOption(
                label=row["id"],
                description=" · ".join(row["badges"]),
            )
            for row in rows
            if not row["rule"]
        ),
        fingerprint=fingerprint_region(texts[title_at : end + 1]),
        hint=_SW_FOOTER,
        nav_keys=("up", "down"),
        commit_keys=("enter",),
        selected=selected,
    )


def _boxed(row: str) -> Optional[str]:
    """A boxed row's inner text: between ``│ `` and the closing wall."""
    if not row.startswith("│ ") or not row.endswith("│"):
        return None
    return row[2:-1]


def _read_switch_window(rows: List[str]) -> Optional[List[dict]]:
    """The window's rows: model rows, at most one rule, then — only
    when no row carries a scrollbar cell — blank padding. A blank row
    between two content rows, a scrollbar on some rows and not others,
    or a row of any other shape is not this layout."""
    read: List[dict] = []
    scrollbar = None
    padding = False
    for row in rows:
        if _SW_BLANK.match(row):
            padding = True
            continue
        if padding:
            return None
        if not row.startswith("│ ") or not row.endswith(" │"):
            return None
        inner = row[2:-2]
        if not inner:
            return None
        cell = inner[-1]
        has_bar = cell in _SW_SCROLL_CELLS
        if not has_bar and cell != " ":
            return None
        if scrollbar is None:
            scrollbar = has_bar
        if scrollbar != has_bar:
            return None
        content = inner[:-1].rstrip()
        if _SW_RULE.match(content):
            read.append({"rule": True, "pointed": False, "id": "", "current": False,
                         "over_context": False, "badges": []})
            continue
        model = _read_switch_model_row(content)
        if model is None:
            return None
        read.append(model)
    if scrollbar is True and padding:
        return None
    if scrollbar is True and not any(
        row.rstrip().endswith("█ │") for row in rows
    ):
        return None
    return read


def _read_switch_model_row(content: str) -> Optional[dict]:
    """One model row's content (the pointer column on), or None."""
    head = re.match(r"^([❯ ]) (\S.*)$", content)
    if head is None:
        return None
    pointed = head.group(1) == _SW_POINTER
    body = head.group(2)
    # omp shortened the row to fit the pane: its id is not all on
    # screen, so the whole box declines rather than guess the walk.
    if "…" in body:
        return None
    match = _SW_MODEL_ROW.match(body)
    if match is None:
        return None
    badges = (
        re.split(r"\s{2,}", match.group(4).strip()) if match.group(4) else []
    )
    if not all(
        any(pattern.match(cell) for pattern in _SW_BADGE_CELLS)
        for cell in badges
    ):
        return None
    return {
        "rule": False,
        "pointed": pointed,
        "id": match.group(1),
        "current": match.group(2) is not None,
        "over_context": match.group(3) is not None,
        "badges": badges,
    }


def _read_switch_chips(row: str) -> Optional[dict]:
    """The second detail row read for what it says about the pointed
    model, or None when it is a row this grammar does not know."""
    if _SW_BLANK.match(row):
        return {"current": False, "over_context": False}
    inner = _boxed(row)
    if inner is None:
        inner = row[3:] if row.startswith("│ ") else row
    text = inner.strip()
    if _SW_OVER_CONTEXT.match(text):
        return {"current": False, "over_context": True}
    chips = text.split(" · ")
    if not all(_SW_CHIP.match(chip) for chip in chips):
        return None
    return {"current": chips[0] == "● current", "over_context": False}
