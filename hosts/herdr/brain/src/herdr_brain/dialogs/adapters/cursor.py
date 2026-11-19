"""Cursor CLI: the two captured dialogs are claimed UNREAD, deliberately.

The Cursor CLI replaces its input box with dialogs. Two of their real
shapes are pinned by the reference corpus's captures (Cursor Agent
2026.10.14 — see tests/fixtures/dialogs/provenance.md):

- the shell-command approval (``Run this command?`` with rows named by
  their own hint keys: ``→ Run (once) (y)``, ``Add Shell(...) to
  allowlist? (tab)``, ``Run Everything (shift+tab)``, ``Skip & tell …
  (esc or n)``);
- the agent question box (``Question 1 of 1`` with ``› [ ]`` checkbox
  rows and the footer ``↑/↓ option · ←/→ question · Space select ·
  Enter next/submit · Esc to skip``).

Both are claimed as the harness's OWN dialog — the PWA shows the
explicit cannot-read card with the manual keypad — and never as
buttons: no answer recipe was ever measured on them. The reference
product's lab found a probe character and a Backspace inert on the
approval (its cursor profile's probe note; its CHANGELOG: the dialogs
"read as unreadable"), and the keys the approval itself names — ``y``,
``tab``, ``shift+tab`` — sit outside brain's verified key dialect,
while the question box's Enter is "next/submit" by its own footer,
semantics no capture pinned. Fail-closed until a recipe is verified.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..model import StructuredDialog, unread
from ..numbered import strip_gutter
from . import AdapterBase

# ── the shell-command approval (the tail of the pane) ────────────────

_SKIP_ROW = re.compile(
    r"^\s+Skip & tell the agent what to do instead \(esc or n\)\s*$"
)
_RUN_ROW = re.compile(r"^\s*→ Run \(once\) \(y\)\s*$")
_ALLOWLIST_ROW = re.compile(r"^\s+Add Shell\(.+\) to allowlist\? \(tab\)\s*$")
_EVERYTHING_ROW = re.compile(r"^\s+Run Everything \(shift\+tab\)\s*$")
_NOT_ALLOWLISTED = re.compile(r"^\s*Not in allowlist: \S.*$")
_RUN_QUESTION = re.compile(r"^\s*Run this command\?\s*$")

# ── the agent question box ───────────────────────────────────────────

_BOX_TOP = re.compile(r"^\s*┌+\u2500*┐\s*$")
_BOX_BOTTOM = re.compile(r"^\s*└+\u2500*┘\s*$")
#: The box's own footer, verbatim: every word of it is the pinned shape.
_QUESTION_FOOTER = (
    "↑/↓ option · ←/→ question · Space select · Enter next/submit · Esc to skip"
)
_QUESTION_MARKER = re.compile(r"^Question \d+ of \d+$")
_CHECKBOX_ROW = re.compile(r"^\s*[›]?\s*\[[ xX]\]\s+\S")
_MAX_BOX_ROWS = 40

#: Why neither dialog is tappable, in one line each.
_SHELL_DETAIL = (
    "cursor's approval answers only with its own hints (y, tab, shift+tab), "
    "which no verified recipe covers"
)
_QUESTION_DETAIL = (
    "cursor's question box needs Space and its Enter is next-or-submit; "
    "no capture pinned the answer semantics"
)


def _bare(row: str) -> str:
    """A box row without its walls: the box may sit past the pane's
    first column, so leading spaces go before the gutter."""
    return strip_gutter(row.strip()).rstrip("│┃").strip()


def _last_content(lines: List[str]) -> int:
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip():
            return index
    return -1


class CursorAdapter(AdapterBase):
    agent = "cursor"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        if self._permission_shell(lines):
            return unread(self.agent, _SHELL_DETAIL, lines)
        if self._question_box(lines):
            return unread(self.agent, _QUESTION_DETAIL, lines)
        return None

    def _permission_shell(self, lines: List[str]) -> bool:
        """The approval's own five rows, tail-anchored and verbatim:
        any other row order or wording is not this dialog."""
        end = _last_content(lines)
        if end < 5:
            return False
        rows = lines[end - 5 : end + 1]
        return (
            _RUN_QUESTION.match(rows[0]) is not None
            and _NOT_ALLOWLISTED.match(rows[1]) is not None
            and _RUN_ROW.match(rows[2]) is not None
            and _ALLOWLIST_ROW.match(rows[3]) is not None
            and _EVERYTHING_ROW.match(rows[4]) is not None
            and _SKIP_ROW.match(rows[5]) is not None
        )

    def _question_box(self, lines: List[str]) -> bool:
        end = _last_content(lines)
        if end < 1 or _BOX_BOTTOM.match(lines[end]) is None:
            return False
        if _bare(lines[end - 1]) != _QUESTION_FOOTER:
            return False
        top = next(
            (
                index
                for index in range(end - 1, max(-1, end - _MAX_BOX_ROWS), -1)
                if _BOX_TOP.match(lines[index])
            ),
            None,
        )
        if top is None:
            return False
        inner = [_bare(row) for row in lines[top + 1 : end - 1]]
        has_marker = any(_QUESTION_MARKER.match(row) for row in inner)
        checkboxes = sum(1 for row in inner if _CHECKBOX_ROW.match(row))
        return has_marker and checkboxes >= 2
