"""The shared menu derivation helpers — the harness-agnostic half of
the generic modal contract.

A «menu» is the last-resort dialog: a full-screen picker no specific
grammar owns (Claude's ``/model`` and its kin), driven ENTIRELY by the
keys the screen printed in its own ``·``-separated footer. The
invariants these helpers encode, which the conformance tests re-check:

- ONLY KEYS THE SCREEN NAMED. Every emitted key comes from a
  ``<key> to <verb>`` footer hint; the only keys added are the ARROWS
  the screen advertised. Nothing is inferred.
- NO DIGITS, EVER (.adr/0009). In the ``/model`` picker a digit confirms
  AND rewrites the user's default — an unrecoverable side effect on a
  screen whose semantics we do not know. A digit is a valid recipe key
  elsewhere, which is exactly why the ban lives in :func:`menu_key_for`
  rather than at the send layer.
- DIALECT-CLOSED. A footer key the guarded answer path cannot send
  (``shift+tab``, ``ctrl+r``) yields ``None``: the footer segment is
  skipped, honestly, instead of minting a button that would be refused
  at send time.
- LAST RESORT, TAIL-ANCHORED. The footer must be the tail of the
  buffer; a picker that scrolled up (real output below it) does not
  match.

Pure functions over strings; imports nothing harness-shaped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

from ..herdr import DIALOG_LETTER_KEYS, SEND_KEY_ALLOWLIST

#: The footer's segment separator: a middle dot with space on both
#: sides. Anchored on the spaces so a dot inside a value ("Haiku 4.5 ·
#: Fastest") in a BODY row can't be mistaken for a footer separator —
#: only the footer line is ever split, and its hints are always spaced.
SEGMENT_SPLIT = re.compile(r"\s+·\s+")

#: One hint segment: ``<key token> to <verb phrase>``. Non-greedy on the
#: key so "Enter to set as default" yields "Enter" / "set as default"
#: rather than swallowing the first "to".
HINT = re.compile(r"^(.+?)\s+to\s+(.+)$", re.IGNORECASE)

#: An ``<value> ←/→ to <verb>`` row. Group 1 is everything left of the
#: arrows — the value being adjusted; group 2 is the verb. Used by the
#: (model-only) leftRight affordance the screen advertises.
MENU_ARROW_ROW = re.compile(r"^(.*?)\s*←/→\s+to\s+(.+)$")

#: The SHAPE of one hint segment, looser than ``HINT`` on purpose: it
#: accepts ``<key> for <phrase>`` as well (the effort footer's "s for
#: this session only"). This never BUILDS an action —
#: :func:`parse_key_hint_footer` keeps its narrower ``to`` grammar — it
#: only answers "is every part of this joined text hint text?".
HINT_FORM = re.compile(r"^(.+?)\s+(?:to|for)\s+(.+)$", re.IGNORECASE)

#: How many rows a wrapped key-hint footer may span. Three is what the
#: real captures need (a footer runs onto three rows at 40 columns);
#: a bound, not "keep going while it parses" — every extra row is one
#: more chance for a body row to read like a hint.
MAX_FOOTER_ROWS = 3


@dataclass(frozen=True)
class MenuAction:
    """One footer-named action, up-levelled into a tappable button."""

    #: The footer's own verb phrase, sentence-capitalised.
    label: str
    #: The keys to send — always exactly the key the footer named.
    keys: Tuple[str, ...]
    #: The Esc segment: renders as the de-emphasised control rather than
    #: a peer action.
    cancel: bool = False


#: The keys the guarded dialog-answer path can physically send: the
#: brain's own verified allowlist plus the lowercase letters the narrow
#: dialog recipe admits (never the raw-keys route — that gate keeps
#: SEND_KEY_ALLOWLIST alone).
_SENDABLE_RECIPE_KEYS = frozenset(SEND_KEY_ALLOWLIST) | DIALOG_LETTER_KEYS


def menu_key_for(token: str) -> Optional[str]:
    """Map a footer's key TOKEN to a guarded recipe key, or ``None``.

    Grammar: ``enter`` · ``esc``/``escape`` · ``tab`` · ``shift+tab`` ·
    a bare lowercase letter (case-SENSITIVE: "S" and "s" are different
    keystrokes and an uppercase token is prose) · the arrows ·
    ``ctrl+<letter>``. NOT digits — see the module docstring; a digit
    would be a valid recipe key, which is exactly why the ban has to
    live here rather than being left to the key validator. The mapped
    key must also be in the dialect the guarded path can send.
    """
    raw = token.strip()
    lower = raw.lower()
    if lower == "enter":
        key: Optional[str] = "enter"
    elif lower in ("esc", "escape"):
        key = "esc"
    elif lower == "tab":
        key = "tab"
    elif lower == "shift+tab":
        key = "shift+tab"
    elif raw == "↑":
        key = "up"
    elif raw == "↓":
        key = "down"
    elif raw == "←":
        key = "left"
    elif raw == "→":
        key = "right"
    elif re.fullmatch(r"[a-z]", raw):
        key = raw
    else:
        ctrl = re.fullmatch(r"ctrl\+([a-z])", lower)
        key = f"ctrl+{ctrl[1]}" if ctrl else None
    if key is None:
        return None
    return key if key in _SENDABLE_RECIPE_KEYS else None


def capitalize_menu_label(phrase: str) -> str:
    """Sentence-capitalise a footer verb phrase for a button label."""
    trimmed = phrase.strip()
    if not trimmed:
        return trimmed
    return trimmed[0].upper() + trimmed[1:]


def parse_key_hint_footer(text: str) -> List[MenuAction]:
    """Parse a KEY-HINT FOOTER into its actions.

    Returns ``[]`` when the line isn't one: fewer than two
    ``·``-separated segments, or no segment whose key token maps to a
    sendable key. A segment that parses as a hint but names a key we
    can't send is SKIPPED (no button) rather than failing the whole
    footer — the remaining hints stay honest.
    """
    segments = SEGMENT_SPLIT.split(text.strip())
    if len(segments) < 2:
        return []
    actions: List[MenuAction] = []
    for segment in segments:
        match = HINT.match(segment.strip())
        if not match:
            continue
        key = menu_key_for(match[1])
        if key is None:
            continue
        actions.append(MenuAction(label=capitalize_menu_label(match[2]), keys=(key,)))
    # The Esc segment is the footer's way OUT, not a peer action: it is
    # flagged so the renderer may de-emphasise it.
    return [
        replace(action, cancel=True) if action.keys[0] == "esc" else action
        for action in actions
    ]


def parse_single_hint(text: str) -> Optional[Tuple[str, str]]:
    """A footer that is ONE ``<key> to <verb>`` hint.

    ``("esc", "cancel")`` for ``Esc to cancel``. ``None`` when the
    footer has more than one segment, is not a hint, or names no
    sendable key — :func:`parse_key_hint_footer` refuses a single
    segment on purpose; this is for a caller that has other evidence
    the screen is a modal (the ``▔`` edge).
    """
    footer = text.strip()
    if len(SEGMENT_SPLIT.split(footer)) != 1:
        return None
    match = HINT.match(footer)
    if match is None:
        return None
    key = menu_key_for(match[1])
    if key is None:
        return None
    return key, match[2]


@dataclass(frozen=True)
class KeyHintFooter:
    """A key-hint footer, read as the rows the terminal wrapped it onto."""

    #: The group's rows, trimmed and joined with one space.
    text: str
    #: The group's FIRST row, and its last. Both indices into the read.
    start_line: int
    end_line: int
    #: What :func:`parse_key_hint_footer` made of ``text``. Never empty.
    actions: List[MenuAction]


def read_key_hint_footer(
    texts: List[str], max_rows: int = MAX_FOOTER_ROWS
) -> Optional[KeyHintFooter]:
    """Read the key-hint footer at the tail of ``texts``.

    The group is the last ``k`` NON-BLANK rows, contiguous (a blank row
    ends it), with ``k`` at most ``max_rows``. A ``k`` is accepted when
    all of these hold:

    - every row carries the SAME left indent, because a wrapped footer
      is one block the renderer drew at one indent. This keeps ordinary
      agent output that scrolled in below a dialog out of the group: it
      is written at the transcript's indent, not the dialog's. ONE
      exception, for the wrap the TERMINAL did rather than the
      renderer: a row at indent 0 is still part of the block when the
      row above it could not have held that row's first word
      (:func:`_soft_wrapped_at0`, with ``width`` the longest row on the
      screen — a dialog's own rules run the full width, so that IS the
      column count);
    - every ``·``-separated segment of the joined text is hint text
      (:data:`HINT_FORM`), so a group holding an option row, a label
      row or a rule is refused whole rather than parsed in part;
    - :func:`parse_key_hint_footer` gets at least one action out of the
      joined text.

    The LARGEST accepted ``k`` wins: smallest-first would stop too
    early and read a true footer's tail as the whole of it. Returns
    ``None`` when no group qualifies.
    """
    end = len(texts) - 1
    while end >= 0 and not texts[end].strip():
        end -= 1
    if end < 0:
        return None

    # The pane's column count, read off the screen rather than passed
    # in: a dialog draws its own rules edge to edge, so the longest row
    # IS the width. Only the soft-wrap exception below uses it.
    width = max(len(text) for text in texts)

    best: Optional[KeyHintFooter] = None
    for k in range(1, max_rows + 1):
        start = end - k + 1
        if start < 0:
            break
        if not texts[start].strip():
            break  # a blank row ends the group; no larger k is contiguous
        rows = texts[start : end + 1]
        if not _rows_are_one_block(rows, width):
            continue
        text = " ".join(row.strip() for row in rows)
        segments = SEGMENT_SPLIT.split(text)
        if not all(HINT_FORM.match(segment.strip()) for segment in segments):
            continue
        actions = parse_key_hint_footer(text)
        if not actions:
            continue
        best = KeyHintFooter(
            text=text, start_line=start, end_line=end, actions=actions
        )
    return best


def _rows_are_one_block(rows: List[str], width: int) -> bool:
    """Whether ``rows`` read as ONE block the renderer drew: every row at
    the first row's indent, save a row at indent 0 the TERMINAL wrapped
    there (:func:`_soft_wrapped_at0`)."""
    indent = _indent_of(rows[0])
    for previous, row in zip(rows, rows[1:]):
        if _indent_of(row) == indent:
            continue
        if _indent_of(row) != 0:
            return False
        if not _soft_wrapped_at0(previous, row, width):
            return False
    return True


def _soft_wrapped_at0(previous: str, continuation: str, width: int) -> bool:
    """Whether ``continuation`` sits at column 0 because ``previous`` had
    no room for its first word. Trailing spaces on ``previous`` are the
    grid's padding, not text the cursor passed, so they are trimmed
    first; the ``+ 1`` is the space that would have separated the two."""
    match = re.search(r"\S", continuation)
    if match is None:
        return False
    first = continuation[match.start() :].split()[0]
    return len(previous.rstrip()) + 1 + len(first) > width


def _indent_of(text: str) -> int:
    """A row's left indent, in characters (comparable among group rows)."""
    return len(text) - len(text.lstrip())
