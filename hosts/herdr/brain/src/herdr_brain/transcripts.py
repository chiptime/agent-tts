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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Session id shapes (shared with the voice layer's connectors).
OPENCODE_SESSION_RE = re.compile(r"^ses_[A-Za-z0-9_-]{6,}$")
CLAUDE_SESSION_RE = re.compile(r"^[\da-fA-F-]{16,}$")

DEFAULT_OPENCODE_DB = "~/.local/share/opencode/opencode.db"
DEFAULT_CLAUDE_ROOT = "~/.claude/projects"
DEFAULT_ANTIGRAVITY_ROOT = "~/.gemini/antigravity-cli/brain"
ANTIGRAVITY_SESSION_RE = re.compile(r"^[\da-fA-F-]{16,}$")

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
    created_at: Optional[datetime] = None


def _iso_to_utc(value: object) -> Optional[datetime]:
    """Parses an ISO 8601 event timestamp into an aware UTC datetime.

    Claude Code emits ``timestamp`` and Antigravity ``created_at`` in this
    shape (``2026-10-10T15:24:00.817Z``). A naive value is assumed UTC; an
    unparseable or missing one yields ``None`` — an unknown time is never
    guessed (a file mtime is not an event time).
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


def _epoch_ms_to_utc(value: object) -> Optional[datetime]:
    """Parses OpenCode's ``time_created`` column (epoch milliseconds, UTC).

    ``bool`` is rejected explicitly: it is an ``int`` subclass, and ``True``
    would silently become 1970-01-01T00:00:00.001Z.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def turn_age_seconds(created_at: Optional[datetime], now: datetime) -> Optional[float]:
    """Seconds between a turn's timestamp and ``now``; ``None`` when unknown.

    A naive ``created_at`` is read as UTC. Clock skew between the transcript
    store and the brain clamps to ``0.0`` so the UI never says "hace -3 min".
    """
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created_at).total_seconds())


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
        SELECT m.id, json_extract(m.data, '$.role'), p.data, m.time_created
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

        # Group every text part under its message, preserving the parts'
        # chronological order: OpenCode splits long answers across several
        # text parts, and keeping only the first one made the reading view
        # arrive truncated ("ver más" had nothing more to reveal).
        order: List[str] = []
        roles: dict = {}
        parts: dict = {}
        stamps: dict = {}
        for message_id, role, data, time_created in rows:
            if role not in ("user", "assistant"):
                continue
            text = _opencode_part_text(data)
            if not text:
                continue
            if message_id not in parts:
                parts[message_id] = []
                roles[message_id] = role
                stamps[message_id] = _epoch_ms_to_utc(time_created)
                order.append(message_id)
            parts[message_id].append(text)
        turns: List[Turn] = []
        for message_id in order:
            if len(turns) >= n_turns:
                break
            turns.append(
                Turn(
                    role=roles[message_id],
                    text="\n".join(parts[message_id]),
                    created_at=stamps[message_id],
                )
            )
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        if not session_id or not OPENCODE_SESSION_RE.match(session_id):
            return None
        if not os.path.isfile(self.db_path):
            return None

        sql = "SELECT title FROM session WHERE id = ?"
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                row = conn.execute(sql, (session_id,)).fetchone()
                if row and isinstance(row[0], str) and row[0].strip():
                    return row[0].strip()
        except (sqlite3.Error, OSError):
            return None
        return None


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
                turns.append(
                    Turn(
                        role=str(kind),
                        text=text,
                        created_at=_iso_to_utc(event.get("timestamp")),
                    )
                )
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None


def _antigravity_user_text(content) -> Optional[str]:
    """Extracts user prompt text from an Antigravity USER_INPUT step.

    Strips <USER_REQUEST> tags if present, or strips metadata blocks
    (<ADDITIONAL_METADATA>, <USER_SETTINGS_CHANGE>) otherwise.
    """
    if not isinstance(content, str):
        return None
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, re.DOTALL)
    if match:
        text = match.group(1).strip()
        return text or None
    cleaned = re.sub(
        r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", content, flags=re.DOTALL
    )
    cleaned = re.sub(
        r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL
    )
    cleaned = cleaned.strip()
    return cleaned or None


class AntigravityTranscript:
    """Read-only multi-turn connector over Antigravity CLI's JSONL transcripts."""

    def __init__(
        self,
        root: Optional[str] = None,
        scan_cap: int = _CLAUDE_SCAN_CAP_BYTES,
    ):
        resolved = root or os.environ.get("ANTIGRAVITY_ROOT") or DEFAULT_ANTIGRAVITY_ROOT
        expanded = Path(resolved).expanduser()
        if (expanded / "brain").is_dir():
            self.root = str(expanded / "brain")
        else:
            self.root = str(expanded)
        self.scan_cap = scan_cap

    def read(self, session_id: str, n_turns: int = 10) -> List[Turn]:
        if not session_id or not ANTIGRAVITY_SESSION_RE.match(session_id):
            return []

        base_dir = Path(self.root) / session_id / ".system_generated" / "logs"
        full_path = base_dir / "transcript_full.jsonl"
        norm_path = base_dir / "transcript.jsonl"

        if full_path.is_file():
            path = str(full_path)
        elif norm_path.is_file():
            path = str(norm_path)
        else:
            return []

        turns: List[Turn] = []
        for raw_line in _reverse_tail_lines(path, self.scan_cap):
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

            event_type = event.get("type")
            stamp = _iso_to_utc(event.get("created_at"))
            if event_type == "USER_INPUT":
                text = _antigravity_user_text(event.get("content"))
                if text:
                    turns.append(Turn(role="user", text=text, created_at=stamp))
            elif event_type == "PLANNER_RESPONSE":
                content = event.get("content")
                if isinstance(content, str) and content.strip():
                    turns.append(
                        Turn(role="assistant", text=content.strip(), created_at=stamp)
                    )

        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        if not session_id or not ANTIGRAVITY_SESSION_RE.match(session_id):
            return None
        root_path = Path(self.root)
        candidates = [
            root_path.parent / "annotations" / f"{session_id}.pbtxt",
            root_path / "annotations" / f"{session_id}.pbtxt",
            root_path / f"{session_id}.pbtxt",
        ]
        for pbtxt_path in candidates:
            if pbtxt_path.is_file():
                try:
                    content = pbtxt_path.read_text(encoding="utf-8")
                    match = re.search(r'title:\s*"(.*?)"', content)
                    if match and match.group(1).strip():
                        return match.group(1).strip()
                    match = re.search(r'title:\s*([^\r\n]+)', content)
                    if match and match.group(1).strip():
                        return match.group(1).strip().strip('"')
                except OSError:
                    continue
        return None


def read_title(
    agent: str,
    session_id: str,
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
    antigravity: Optional[AntigravityTranscript] = None,
) -> Optional[str]:
    """Routes to the matching connector and returns the session's conversation title.

    Returns None when no connector matches, the session store is unreadable,
    or the session has no assigned title.
    """
    opencode = opencode or OpencodeTranscript()
    claude = claude or ClaudeTranscript()
    antigravity = antigravity or AntigravityTranscript()

    key = (agent or "").strip().lower()
    if key in ("opencode", "oa"):
        return opencode.read_title(session_id)
    elif key in ("antigravity", "agy"):
        return antigravity.read_title(session_id)
    elif key in ("claude", "codex"):
        return claude.read_title(session_id)
    elif OPENCODE_SESSION_RE.match(session_id):
        return opencode.read_title(session_id)
    elif ANTIGRAVITY_SESSION_RE.match(session_id):
        title = antigravity.read_title(session_id)
        if not title:
            title = claude.read_title(session_id)
        return title
    return None


def read_turns(
    agent: str,
    session_id: str,
    n_turns: int = 10,
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
    antigravity: Optional[AntigravityTranscript] = None,
) -> Optional[List[Turn]]:
    """Routes to the matching connector and returns structured turns.

    Routing mirrors the voice layer: match by agent name first, then sniff the
    session id shape. Returns ``None`` when no connector matches or nothing
    readable is found, so callers can fall back to a terminal screen read.
    """
    n_turns = max(1, min(n_turns, MAX_TURNS))
    opencode = opencode or OpencodeTranscript()
    claude = claude or ClaudeTranscript()
    antigravity = antigravity or AntigravityTranscript()

    key = (agent or "").strip().lower()
    if key in ("opencode", "oa"):
        turns = opencode.read(session_id, n_turns)
    elif key in ("antigravity", "agy"):
        turns = antigravity.read(session_id, n_turns)
    elif key in ("claude", "codex"):
        turns = claude.read(session_id, n_turns) if CLAUDE_SESSION_RE.match(session_id) else []
    elif OPENCODE_SESSION_RE.match(session_id):
        turns = opencode.read(session_id, n_turns)
    elif CLAUDE_SESSION_RE.match(session_id):
        turns = antigravity.read(session_id, n_turns)
        if not turns:
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
    antigravity: Optional[AntigravityTranscript] = None,
) -> Optional[str]:
    """Same routing as :func:`read_turns`, formatted as text for the LLM."""
    turns = read_turns(
        agent,
        session_id,
        n_turns,
        opencode=opencode,
        claude=claude,
        antigravity=antigravity,
    )
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
