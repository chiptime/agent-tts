"""Muse connector: ``session.jsonl`` under ``$MUSE_ROOT``.

On-disk shape (pinned by the fixture under
``tests/fixtures/transcripts/muse``, synthesized from the documented
envelope):

    <root>/YYYY/MM/DD/<session-uuid>/session.jsonl
    {"schema_version":1,"id":"…","stream":{"kind":"session","id":"…"},
     "sequence":N,"recorded_at":<microseconds>,"record_type":"event",
     "durability":"durable","payload_type":"…","payload":{…}}

The rows that carry the transcript:

- ``runtime.user_intent.accepted`` — the operator's message, from
  ``payload.model_messages[].content[]`` text blocks (NOT ``run/
  started``'s prompt: a mid-run steer never starts a run).
- ``run/assistant_message_committed`` — the assistant's reply text
  (NOT the ``_delta`` stream rows).

``recorded_at`` is MICROSECONDS since the epoch. Slash commands
(``command.invoked``) and every structural row are skipped.
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .base import Turn, parse_json_line, reverse_tail_lines, SCAN_CAP_BYTES

DEFAULT_MUSE_ROOT = "~/.local/share/muse/sessions"
ENV_MUSE_ROOT = "MUSE_ROOT"

#: A Muse session is a uuid-shaped folder name under date buckets.
MUSE_SESSION_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")

USER_INTENT = "runtime.user_intent.accepted"
ASSISTANT_COMMIT = "run/assistant_message_committed"


def _us_to_utc(value: object) -> Optional[datetime]:
    """Parses ``recorded_at`` (epoch microseconds)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1_000_000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class MuseTranscript:
    """Read-only connector over Muse session logs."""

    name = "muse"

    def __init__(self, root: Optional[str] = None, scan_cap: int = SCAN_CAP_BYTES):
        resolved = root or os.environ.get(ENV_MUSE_ROOT) or DEFAULT_MUSE_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def _log_for(self, session_id: str) -> Optional[str]:
        pattern = os.path.join(self.root, "**", session_id, "session.jsonl")
        matches = [path for path in glob.glob(pattern, recursive=True) if os.path.isfile(path)]
        if not matches:
            return None
        return max(matches, key=os.path.getmtime)

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not MUSE_SESSION_RE.match(session_id):
            return []
        path = self._log_for(session_id)
        if path is None:
            return []

        turns: List[Turn] = []
        skipped = 0
        for raw_line in reverse_tail_lines(path, self.scan_cap):
            if len(turns) >= n_turns:
                break
            event = parse_json_line(raw_line)
            if event is None:
                continue
            payload_type = event.get("payload_type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload_type == USER_INTENT:
                text = _intent_text(payload)
                role = "user"
            elif payload_type == ASSISTANT_COMMIT:
                text = payload.get("text")
                text = text.strip() if isinstance(text, str) else ""
                role = "assistant"
            else:
                continue
            if not text:
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(
                Turn(role=role, text=text, created_at=_us_to_utc(event.get("recorded_at")))
            )
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None


def _intent_text(payload: dict) -> str:
    """The operator's words from an accepted intent."""
    pieces: List[str] = []
    messages = payload.get("model_messages")
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                pieces.append(block["text"].strip())
    return "\n".join(piece for piece in pieces if piece)
