"""pi / Oh My Pi: the Ask box.

The shape this recognizer claims (pinned by a synthesized fixture in
the layout pi draws for its ``ask`` tool):

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


class PiAdapter(AdapterBase):
    agent = "pi"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        texts = [line.rstrip() for line in lines]
        if not any(_TITLE.match(text) for text in texts):
            return None
        footer = footer_line(texts)
        cleaned = _bare(footer)
        if any(pattern.search(cleaned) for pattern in _MULTI_FOOTERS):
            return unread(self.agent, "multi-select Enter semantics differ by version", lines)
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
