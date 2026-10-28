"""Cursor-paginated full history for one pane.

The conversation window (/conversation) answers "the newest 20 turns";
this module answers "the WHOLE transcript, one bounded page at a time".
Each page is a window of turns counted from the newest: the cursor is
an opaque token carrying how many turns the client has already seen, so
the next page starts exactly at the boundary turn — no skips, no
duplicates while the transcript is at rest. Full turn text travels
unclipped (the 2.000-char clip is an LLM-budget concern, not a reading
one).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import List, Optional

from .transcripts import Turn

#: Page size the PWA history view asks for first (matches its initial
#: render window, doc 05 §Diseño propuesto).
HISTORY_DEFAULT_LIMIT = 60
#: Hard page ceiling — one request never materialises an unbounded page.
HISTORY_MAX_LIMIT = 200


def clamp_limit(raw: int) -> int:
    """Page size bounded to [1, HISTORY_MAX_LIMIT]."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return HISTORY_DEFAULT_LIMIT
    return max(1, min(value, HISTORY_MAX_LIMIT))


def encode_cursor(offset: int) -> str:
    """Opaque cursor: turns already served, newest-first."""
    payload = json.dumps({"v": 1, "o": max(0, int(offset))}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(token: Optional[str]) -> int:
    """Turn offset carried by ``token``; 0 for absent or malformed tokens.

    A malformed cursor degrades to the newest page rather than erroring:
    the miss costs one re-render, never a dead history view.
    """
    if not token:
        return 0
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return 0
    offset = payload.get("o")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return 0
    return offset


def _turn_dict(turn: Turn) -> dict:
    return {
        "role": turn.role,
        "text": turn.text,
        "created_at": turn.created_at.isoformat() if turn.created_at else None,
    }


def read_history_page(
    reader: Optional[object],
    session_id: Optional[str],
    cursor: Optional[str] = None,
    limit: int = HISTORY_DEFAULT_LIMIT,
) -> dict:
    """One page of the transcript served newest-first by cursor.

    ``reader`` is a registry connector (or None: an agent with no
    readable history — an ordinary ``no-session``, never an error). The
    page itself arrives in chronological order so a client prepends it
    under what it already holds. One extra turn is read past the page to
    answer ``has_more`` without a second pass.
    """
    bounded = clamp_limit(limit)
    if reader is None or not session_id:
        return {
            "turns": [],
            "reason": "no-session",
            "cursor": None,
            "has_more": False,
            "limit": bounded,
        }

    offset = decode_cursor(cursor)
    turns: List[Turn] = reader.read(session_id, bounded + 1, skip_newest=offset)
    has_more = len(turns) > bounded
    # The connector returns the window in chronological order, so the
    # NEWEST ``bounded`` turns are the tail: slicing the head would hand
    # the client the window's oldest turn AND re-serve it on the next
    # page (the probe read is one turn longer for exactly this reason).
    page = turns[-bounded:] if has_more else turns
    return {
        "turns": [_turn_dict(turn) for turn in page],
        "reason": None,
        "cursor": encode_cursor(offset + len(page)) if has_more else None,
        "has_more": has_more,
        "limit": bounded,
    }
