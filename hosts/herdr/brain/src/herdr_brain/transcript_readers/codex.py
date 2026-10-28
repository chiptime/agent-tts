"""Codex connector: rollout JSONL transcripts under ``$CODEX_ROOT``.

On-disk shape (learned from real rollouts, kept here so the parser is
readable against its source):

    <root>/YYYY/MM/DD/rollout-<ISO-ts>-<session-uuid>.jsonl
    {"timestamp":"…","type":"session_meta","payload":{"id":"<uuid>",…}}
    {"timestamp":"…","type":"response_item","payload":{"type":"message",
     "role":"user"|"assistant","content":[{"type":"input_text"|"output_text",
     "text":"…"}]}}
    {"timestamp":"…","type":"event_msg","payload":{…}}

Every turn is written TWICE — once as ``response_item`` (the API-shaped
record) and once as ``event_msg`` (the UI event stream) — so only
``response_item`` rows are read; the event stream would double-book each
turn. Reasoning rows, tool calls and the ``developer`` role are not
conversation turns and are skipped.

A rewind writes a NEW segment file (``rollout-…-<uuid>_<segment>.jsonl``)
for the same session id; this connector reads the newest file of the
session — the segment that holds the conversation as it stands now.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import List, Optional

from .base import Turn, iso_to_utc, parse_json_line, reverse_tail_lines, SCAN_CAP_BYTES

DEFAULT_CODEX_ROOT = "~/.codex/sessions"
ENV_CODEX_ROOT = "CODEX_ROOT"

CODEX_SESSION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _block_text(content) -> Optional[str]:
    """Flattens a content list of ``{type, text}`` blocks into text."""
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    pieces = [
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    text = "\n".join(piece for piece in pieces if piece)
    return text or None


class CodexTranscript:
    """Read-only connector over Codex rollout transcripts."""

    name = "codex"

    def __init__(self, root: Optional[str] = None, scan_cap: int = SCAN_CAP_BYTES):
        resolved = root or os.environ.get(ENV_CODEX_ROOT) or DEFAULT_CODEX_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def _newest_file(self, session_id: str) -> Optional[str]:
        """Newest rollout file naming this session (segments included)."""
        pattern = os.path.join(self.root, "**", f"rollout-*{session_id}*.jsonl")
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            return None
        return max(matches, key=lambda p: (os.path.getmtime(p), p) if os.path.exists(p) else (0, p))

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not CODEX_SESSION_RE.match(session_id):
            return []
        path = self._newest_file(session_id)
        if path is None:
            return []

        turns: List[Turn] = []
        skipped = 0
        for raw_line in reverse_tail_lines(path, self.scan_cap):
            if len(turns) >= n_turns:
                break
            event = parse_json_line(raw_line)
            if event is None or event.get("type") != "response_item":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _block_text(payload.get("content"))
            if not text:
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(
                Turn(role=role, text=text, created_at=iso_to_utc(event.get("timestamp")))
            )
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None
