"""Multi-turn transcript readers for the brain's ``read_transcript`` tool.

The voice layer's connectors only expose the *last* assistant message;
the brain needs recent user/assistant turns, so the same store contracts
are implemented here with multi-turn queries:

- OpenCode: read-only SQLite at ``~/.local/share/opencode/opencode.db``
  (``message``/``part`` tables, JSON ``$.role`` / ``$.type`` filters).
- Claude Code: JSONL sessions at ``~/.claude/projects/<munged-cwd>/<uuid>.jsonl``
  with a reverse tail scan capped at 8 MB.

Session-id shapes follow the voice layer's sniffing rules: ``ses_*`` routes to
OpenCode, UUID-like ids to the Claude-style stores. All access is read-only.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Session id shapes (shared with the voice layer's connectors).
OPENCODE_SESSION_RE = re.compile(r"^ses_[A-Za-z0-9_-]{6,}$")
CLAUDE_SESSION_RE = re.compile(r"^[\da-fA-F-]{16,}$")

DEFAULT_OPENCODE_DB = "~/.local/share/opencode/opencode.db"
DEFAULT_CLAUDE_ROOT = "~/.claude/projects"

# Safety caps matching the voice layer's connectors.
_CLAUDE_SCAN_CAP_BYTES = 8 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
MAX_TURNS = 50
MAX_TURN_CHARS = 2_000


@dataclass(frozen=True)
class Turn:
    """One user or assistant message from a transcript."""

    role: str
    text: str


class TranscriptError(RuntimeError):
    """Raised internally when a transcript store cannot be read."""


class OpencodeTranscript:
    """Read-only multi-turn connector over OpenCode's local SQLite database."""

    def __init__(self, db_path: Optional[str] = None):
        resolved = db_path or os.environ.get("OPENCODE_DB") or DEFAULT_OPENCODE_DB
        self.db_path = str(Path(resolved).expanduser())

    def read(self, session_id: str, n_turns: int = 10) -> List[Turn]:
        if not session_id or not OPENCODE_SESSION_RE.match(session_id):
            return []
        if not os.path.isfile(self.db_path):
            return []

        # Newest messages first, parts of each message in chronological order.
        sql = """
        SELECT m.id, json_extract(m.data, '$.role'), p.data
        FROM message AS m
        JOIN part AS p ON p.message_id = m.id
        WHERE m.session_id = ?
          AND json_extract(p.data, '$.type') = 'text'
        ORDER BY m.time_created DESC, m.id DESC, p.time_created ASC, p.id ASC
        """
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                rows = conn.execute(sql, (session_id,)).fetchall()
        except (sqlite3.Error, OSError):
            return []

        turns: List[Turn] = []
        seen: set = set()
        for message_id, role, data in rows:
            if message_id in seen:
                continue  # part of an already-captured message
            if len(turns) >= n_turns:
                break
            if role not in ("user", "assistant"):
                continue
            text = _opencode_part_text(data)
            if not text:
                continue
            seen.add(message_id)
            turns.append(Turn(role=role, text=text))
        turns.reverse()
        return turns


class ClaudeTranscript:
    """Read-only multi-turn connector over Claude-style JSONL transcripts."""

    def __init__(self, root: Optional[str] = None, scan_cap: int = _CLAUDE_SCAN_CAP_BYTES):
        resolved = root or os.environ.get("CLAUDE_PROJECTS_ROOT") or DEFAULT_CLAUDE_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def read(self, session_id: str, n_turns: int = 10) -> List[Turn]:
        if not session_id or not CLAUDE_SESSION_RE.match(session_id):
            return []
        matches = sorted(glob.glob(os.path.join(self.root, "*", f"{session_id}.jsonl")))
        if not matches:
            return []

        turns: List[Turn] = []
        for raw_line in _reverse_tail_lines(matches[0], self.scan_cap):
            if len(turns) >= n_turns:
                break
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind not in ("user", "assistant"):
                continue
            text = _claude_event_text(event)
            if text:
                turns.append(Turn(role=str(kind), text=text))
        turns.reverse()
        return turns


def read_turns(
    agent: str,
    session_id: str,
    n_turns: int = 10,
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
) -> Optional[List[Turn]]:
    """Routes to the matching connector and returns structured turns.

    Routing mirrors the voice layer: match by agent name first, then sniff the
    session id shape. Returns ``None`` when no connector matches or nothing
    readable is found, so callers can fall back to a terminal screen read.
    """
    n_turns = max(1, min(n_turns, MAX_TURNS))
    opencode = opencode or OpencodeTranscript()
    claude = claude or ClaudeTranscript()

    key = (agent or "").strip().lower()
    if key == "opencode":
        turns = opencode.read(session_id, n_turns)
    elif key in ("claude", "codex", "antigravity", "agy"):
        # UUID-like stores are disjoint per tool; the Claude JSONL contract is
        # the only multi-turn shape implemented, others fall back to screen.
        turns = claude.read(session_id, n_turns) if CLAUDE_SESSION_RE.match(session_id) else []
    elif OPENCODE_SESSION_RE.match(session_id):
        turns = opencode.read(session_id, n_turns)
    elif CLAUDE_SESSION_RE.match(session_id):
        turns = claude.read(session_id, n_turns)
    else:
        return None

    return turns or None


def read_transcript(
    agent: str,
    session_id: str,
    n_turns: int = 10,
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
) -> Optional[str]:
    """Same routing as :func:`read_turns`, formatted as text for the LLM."""
    turns = read_turns(agent, session_id, n_turns, opencode=opencode, claude=claude)
    if not turns:
        return None
    return format_turns(turns)


def format_turns(turns: List[Turn]) -> str:
    """Renders turns as a compact, LLM-friendly transcript."""
    blocks = []
    for turn in turns:
        text = turn.text.strip()
        if len(text) > MAX_TURN_CHARS:
            text = text[:MAX_TURN_CHARS] + " […]"
        blocks.append(f"{turn.role}: {text}")
    return "\n\n".join(blocks)


def _opencode_part_text(data) -> Optional[str]:
    """Extracts text from one OpenCode ``part`` JSON payload."""
    try:
        text = json.loads(data).get("text")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _claude_event_text(event: dict) -> Optional[str]:
    """Extracts joined text blocks from a Claude user/assistant event."""
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    blocks = [
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(block for block in blocks if block)
    return text or None


def _reverse_tail_lines(path: str, cap_bytes: int):
    """Yields complete lines of a file newest-first, capped to ``cap_bytes``.

    Same chunked reverse-scan approach as the Claude-style voice connectors.
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
