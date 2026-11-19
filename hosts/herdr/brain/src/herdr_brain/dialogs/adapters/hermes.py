"""Hermes Agent (classic CLI): the dangerous-command approval panel.

The shape this recognizer claims (pinned by the REAL captures of the
reference corpus, Hermes Agent v0.22.2 classic CLI — see
tests/fixtures/dialogs/provenance.md; the Ink TUI is a different
screen and is claimed nowhere here):

::

    ╭────────────────────────────────────╮
    │ ⚠️  Dangerous Command               │
    │                                    │
    │ rm -rf /tmp/refcap-hermes-lab/...  │
    │                                    │
    │ ❯ 1. Allow once                    │
    │   2. Allow for this session        │
    │   3. Add to permanent allowlist    │
    │   4. Deny                          │
    │                                    │
    │ delete in root path                │
    ╰────────────────────────────────────╯

      💻 rm -rf /tmp/...  (  5.0s · ↓ 40 tok)     ← ticks
      ↑/↓ to select, Enter to confirm  (295s)     ← ticks

The measured recipe (the reference product's live-probed table,
``APPROVAL_NOTES.md`` lines 35-46): the digit ALONE confirms its own
row regardless of where ``❯`` stands — ``4`` confirmed Deny with the
pointer on Allow once, ``5`` only redrew the panel with the command
whole, and ``1`` (Allow once) was the live end-to-end check from the
phone. ``2`` and ``3`` are NEVER buttons: they change Hermes's
standing policy for the session or for good.

Fail-closed on every unpinned shape: a command Hermes cut with ``…``
(not whole on screen), a choice label outside the pinned set, the
clarify panel's ``Enter to lock`` footer, a panel without its box, or
the narrow (<64 column) layout no capture here pins — all decline.
The rows under the panel tick about once a second, so the fingerprint
covers the panel ALONE.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..model import DialogOption, StructuredDialog, fingerprint_region
from ..numbered import parse_option_row, strip_gutter
from . import AdapterBase

#: The approval panel's own hint. The clarify panel under the same
#: ``↑/↓ to select`` prefix says ``Enter to lock, Tab next question`` —
#: a different dialog this adapter does not pin.
_HINT = re.compile(r"^\s*↑/↓ to select, Enter to confirm\b")

#: The title row exactly as the capture prints it (⚠ + VS16).
_TITLE = re.compile(r"^⚠️?\s+Dangerous Command$")

_BOX_TOP = re.compile(r"^\s*╭+\u2500*╮\s*$")
_BOX_BOTTOM = re.compile(r"^\s*╰+\u2500*╯\s*$")

#: How far below the panel's bottom border the hint may sit: blank rows
#: and the ticking spinner row sit between them, and on a wide command
#: the spinner itself wraps onto a second row.
_HINT_SLACK = 7

#: The panel's own rows between its borders and the choice block.
_MAX_BOX_ROWS = 30

#: The pinned choice labels, and the two sequences the captures pin
#: (the reference notes: any other label refuses the whole panel, and
#: the smart-approval shrink set is a shape no capture here pins).
_ALLOW_ONCE = "Allow once"
_DENY = "Deny"
_SHOW_FULL = "Show full command"
_POLICY_LABELS = ("Allow for this session", "Add to permanent allowlist")
_PINNED_SEQUENCES = (
    (_ALLOW_ONCE, *_POLICY_LABELS, _DENY),
    (_ALLOW_ONCE, *_POLICY_LABELS, _DENY, _SHOW_FULL),
)


def _bare(row: str) -> str:
    """A panel row without its box walls: gutter left, ``│`` right."""
    return strip_gutter(row).rstrip("│┃").strip()


def _is_blank(row: str) -> bool:
    return not row


class HermesAdapter(AdapterBase):
    agent = "hermes"

    def detect(self, lines: List[str], styled) -> Optional[StructuredDialog]:
        hint_at = next(
            (
                index
                for index in range(len(lines) - 1, -1, -1)
                if _HINT.match(lines[index])
            ),
            None,
        )
        if hint_at is None:
            return None
        bottom = next(
            (
                index
                for index in range(hint_at - 1, max(-1, hint_at - _HINT_SLACK), -1)
                if _BOX_BOTTOM.match(lines[index])
            ),
            None,
        )
        if bottom is None:
            return None
        top = next(
            (
                index
                for index in range(bottom - 1, max(-1, bottom - _MAX_BOX_ROWS), -1)
                if _BOX_TOP.match(lines[index])
            ),
            None,
        )
        if top is None:
            return None
        return self._panel(lines, top, bottom, lines[hint_at].strip())

    def _panel(
        self, lines: List[str], top: int, bottom: int, hint: str
    ) -> Optional[StructuredDialog]:
        inner = [_bare(row) for row in lines[top + 1 : bottom]]

        # title, blank, command rows, blank, choice rows, blank, reason —
        # the whole interior is accounted for, in this order.
        if not inner or _TITLE.match(inner[0]) is None or not _is_blank(inner[1]):
            return None
        cursor = 2
        command_rows: List[str] = []
        while cursor < len(inner) and not _is_blank(inner[cursor]):
            # A command Hermes cut with ``…`` is not whole on screen:
            # nothing may approve what the panel does not show.
            if "…" in inner[cursor]:
                return None
            command_rows.append(inner[cursor])
            cursor += 1
        if not command_rows or cursor >= len(inner) or not _is_blank(inner[cursor]):
            return None
        cursor += 1
        choice_rows: List[Tuple[str, str, bool]] = []
        while cursor < len(inner) and not _is_blank(inner[cursor]):
            row = parse_option_row(inner[cursor])
            if row is None:
                return None
            number, label, _description, pointed = row
            choice_rows.append((number, label, pointed))
            cursor += 1
        if len(choice_rows) < 2 or cursor >= len(inner) or not _is_blank(inner[cursor]):
            return None
        cursor += 1
        reason_rows = [row for row in inner[cursor:] if not _is_blank(row)]
        if len(reason_rows) != 1 or len(reason_rows) != len(inner[cursor:]):
            return None

        # The numbering the screen prints, 1..N with no gap.
        if [number for number, _label, _pointed in choice_rows] != [
            str(position + 1) for position in range(len(choice_rows))
        ]:
            return None
        labels = tuple(label for _number, label, _pointed in choice_rows)
        if labels not in _PINNED_SEQUENCES:
            return None

        options: List[DialogOption] = []
        selected: Optional[int] = None
        for number, label, pointed in choice_rows:
            if label in _POLICY_LABELS:
                # Standing-policy rows are never buttons; a pointer on
                # one leaves no button selected.
                continue
            options.append(DialogOption(label=label, keys=(number,)))
            if pointed:
                selected = len(options) - 1

        # The terminal soft-wrapped the command mid-token: joining the
        # rows with nothing reconstitutes the text it broke.
        question = "\n".join(
            ["Dangerous Command", "".join(command_rows), reason_rows[0]]
        )
        return StructuredDialog(
            agent=self.agent,
            kind="permission",
            question=question,
            options=tuple(options),
            # The panel ALONE: every row under it ticks about once a
            # second, and the pointer sits inside it.
            fingerprint=fingerprint_region(lines[top : bottom + 1]),
            hint=hint,
            selected=selected,
        )
