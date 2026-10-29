"""Per-harness dialog recognizers.

Each adapter reads ONE harness's dialog shapes and nothing else: it
declares the footer phrases that pin a recipe, which rows become
options, and which states are unread. Every adapter returns ``None``
outside its own corpus — fail-closed is the contract.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import DialogOption, StructuredDialog, fingerprint_region, unread
from ..numbered import (
    collect_numbered_block,
    parse_option_row,
    question_above,
    split_checked,
    step_marker_above,
    strip_gutter,
)

#: Rows that are frame, not content: a bare dialog gutter or a box
#: border. The footer never lives on one of these.
_FRAME_ROW = re.compile(
    r"^[\u2500\u2501\u2502\u2503\u2550\u2551"
    r"\u250c\u2510\u2514\u2518\u251c\u2524\u252c\u2534\u252c\u253c"
    r"┃│├┤╭╮╰╯╫]*$"
)


class AdapterBase:
    """The shared skeleton: find the numbered block, hand it to the
    harness's own classifier."""

    agent = ""

    #: Footer phrase → kind. Ordered: the first phrase found wins.
    FOOTER_KINDS: tuple = ()

    def detect(self, lines: List[str], styled: Optional[list]) -> Optional[StructuredDialog]:
        raise NotImplementedError  # pragma: no cover - per-harness


def footer_line(lines: List[str]) -> str:
    """The last non-blank content row: a dialog's footer is the tail of
    the buffer. Bare gutters and box borders are frame, not content —
    the hint row sits INSIDE the frame."""
    for line in reversed(lines):
        text = line.strip()
        if not text:
            continue
        if _FRAME_ROW.match(text):
            continue
        return text
    return ""


def region_lines(lines: List[str], start: int) -> List[str]:
    """The dialog's region: from its first own row to the end.

    The start is anchored by the adapter (question or block top); the
    fingerprint covers everything below it, pointer included.
    """
    return lines[start:]


#: How many rows ABOVE the first option the region reaches. A dialog's
#: subject — the command, the diff, the path it is about — sits above
#: its options, and two same-shaped prompts over different subjects are
#: DIFFERENT dialogs. Erring wide costs a harmless refresh; erring
#: narrow would let a stale tap approve the wrong thing.
DIALOG_REGION_LOOKBACK = 8


def dialog_region(lines: List[str], first_option: int) -> List[str]:
    """The bounded region a dialog's fingerprint is taken over."""
    return lines[max(0, first_option - 1 - DIALOG_REGION_LOOKBACK) :]
