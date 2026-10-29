"""Shared vocabulary of the transcript connectors.

One ``Turn`` shape, one timestamp dialect family and one capped reverse
scan — every harness module builds on these so a page read by /history
and a tail read by /conversation agree on what a turn IS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

# Reverse-scan safety cap shared by every JSONL-style connector: never
# read more than the trailing 8 MB of a session.
SCAN_CAP_BYTES = 8 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Turn:
    """One user or assistant message from a transcript."""

    role: str
    text: str
    created_at: Optional[datetime] = None


def iso_to_utc(value: object) -> Optional[datetime]:
    """Parses an ISO 8601 event timestamp into an aware UTC datetime.

    A naive value is assumed UTC; an unparseable or missing one yields
    ``None`` — an unknown time is never guessed (a file mtime is not an
    event time).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def epoch_s_to_utc(value: object) -> Optional[datetime]:
    """Parses an epoch-seconds timestamp (Hermes' ``messages.timestamp``).

    ``bool`` is rejected explicitly: it is an ``int`` subclass.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def reverse_tail_lines(path: str, cap_bytes: int) -> Iterator[bytes]:
    """Yields complete lines of a file newest-first, capped to ``cap_bytes``.

    Chunked reverse scan so a caller collecting the newest N turns never
    loads (or even seeks past) the head of a multi-megabyte session.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return

    with open(path, "rb") as fh:
        position = size
        scanned = 0
        remainder = b""
        while position > 0 and scanned < cap_bytes:
            step = min(_CHUNK_SIZE, position, cap_bytes - scanned)
            position -= step
            scanned += step
            fh.seek(position)
            chunk = fh.read(step) + remainder
            lines = chunk.split(b"\n")
            remainder = lines.pop(0) if position > 0 else b""
            for line in reversed(lines):
                yield line


def parse_json_line(raw: bytes) -> Optional[dict]:
    """One JSONL line as a dict, or None for blank/unparseable/scalar lines."""
    line = raw.strip()
    if not line:
        return None
    import json

    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return event if isinstance(event, dict) else None
