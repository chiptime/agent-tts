"""Multi-turn transcript readers for the brain's ``read_transcript`` tool.

The voice layer's connectors only expose the *last* assistant message;
the brain needs recent user/assistant turns, so the same store contracts
are implemented here with multi-turn queries:

- OpenCode: read-only SQLite at ``~/.local/share/opencode/opencode.db``
  (``message``/``part`` tables, JSON ``$.role`` / ``$.type`` filters).
- Claude Code: JSONL sessions at ``~/.claude/projects/<munged-cwd>/<uuid>.jsonl``
  with a reverse tail scan capped at 8 MB.
- Antigravity: JSONL step transcripts under ``~/.gemini/antigravity-cli``.

Harness-name routing lives in :mod:`herdr_brain.transcript_readers`
(the registry); this module keeps the original three connectors, the
session-id shape fallback and the LLM-facing formatting. All access is
read-only.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .transcript_readers.base import (
    Turn,
    iso_to_utc as _iso_to_utc,
    reverse_tail_lines as _reverse_tail_lines,
)
from .transcript_readers.registry import TranscriptRegistry

# Session id shapes (shared with the voice layer's connectors).
OPENCODE_SESSION_RE = re.compile(r"^ses_[A-Za-z0-9_-]{6,}$")
CLAUDE_SESSION_RE = re.compile(r"^[\da-fA-F-]{16,}$")

DEFAULT_OPENCODE_DB = "~/.local/share/opencode/opencode.db"
DEFAULT_CLAUDE_ROOT = "~/.claude/projects"
DEFAULT_ANTIGRAVITY_ROOT = "~/.gemini/antigravity-cli/brain"
ANTIGRAVITY_SESSION_RE = re.compile(r"^[\da-fA-F-]{16,}$")

# Safety caps matching the voice layer's connectors.
_CLAUDE_SCAN_CAP_BYTES = 8 * 1024 * 1024
MAX_TURNS = 50
MAX_TURN_CHARS = 2_000


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

    name = "opencode"

    def __init__(self, db_path: Optional[str] = None):
        resolved = db_path or os.environ.get("OPENCODE_DB") or DEFAULT_OPENCODE_DB
        self.db_path = str(Path(resolved).expanduser())

    def read(self, session_id: str, n_turns: int = 10, skip_newest: int = 0) -> List[Turn]:
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
        # ``order`` is newest-first; paging skips whole MESSAGES (a turn),
        # never parts, so offset and window agree with the history cursor.
        for message_id in order[max(0, skip_newest):]:
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

    name = "claude"

    def __init__(self, root: Optional[str] = None, scan_cap: int = _CLAUDE_SCAN_CAP_BYTES):
        resolved = root or os.environ.get("CLAUDE_PROJECTS_ROOT") or DEFAULT_CLAUDE_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def read(self, session_id: str, n_turns: int = 10, skip_newest: int = 0) -> List[Turn]:
        if not session_id or not CLAUDE_SESSION_RE.match(session_id):
            return []
        matches = sorted(glob.glob(os.path.join(self.root, "*", f"{session_id}.jsonl")))
        if not matches:
            return []

        turns: List[Turn] = []
        skipped = 0
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
                if skipped < skip_newest:
                    skipped += 1
                    continue
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

    name = "antigravity"

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

    def read(self, session_id: str, n_turns: int = 10, skip_newest: int = 0) -> List[Turn]:
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
        skipped = 0
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
                    if skipped < skip_newest:
                        skipped += 1
                        continue
                    turns.append(Turn(role="user", text=text, created_at=stamp))
            elif event_type == "PLANNER_RESPONSE":
                content = event.get("content")
                if isinstance(content, str) and content.strip():
                    if skipped < skip_newest:
                        skipped += 1
                        continue
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


def connector_for(
    agent: str,
    session_id: str,
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
    antigravity: Optional[AntigravityTranscript] = None,
) -> Optional[object]:
    """The connector a pane's history is read through, or None.

    Same policy as :func:`read_turns` (registry name first, session-id
    shape second) exposed as one lookup so the history endpoint and the
    conversation window can never disagree about whose store a pane
    reads.
    """
    registry = _build_registry(opencode, claude, antigravity)
    reader = registry.resolve(agent)
    if reader is not None:
        return reader
    return _shape_fallback_reader(registry, session_id)


def _build_registry(
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
    antigravity: Optional[AntigravityTranscript] = None,
) -> "TranscriptRegistry":
    """The shared harness registry, honouring the injectable connectors.

    Readers are constructed per call (same as before the registry), so
    environment root changes keep applying without a restart. Explicit
    instances replace their defaults one-for-one — the documented seam
    the existing tests drive.
    """
    from .transcript_readers.registry import default_registry

    if opencode is None and claude is None and antigravity is None:
        return default_registry()
    registry = default_registry()
    readers = dict(registry._readers)  # noqa: SLF001 — same-package seam
    if opencode is not None:
        readers[opencode.name] = opencode
    if claude is not None:
        readers[claude.name] = claude
    if antigravity is not None:
        readers[antigravity.name] = antigravity
    return TranscriptRegistry(readers)


def _shape_fallback_reader(
    registry: "TranscriptRegistry", session_id: str
) -> Optional[object]:
    """Connector sniffed from the session id shape (registry-driven).

    Same policy as ever: ``ses_*`` is OpenCode; a UUID-ish id is read as
    Antigravity first and Claude second (both stores share the shape).
    """
    if OPENCODE_SESSION_RE.match(session_id or ""):
        return registry.resolve("opencode")
    if CLAUDE_SESSION_RE.match(session_id or ""):
        for harness in ("antigravity", "claude"):
            reader = registry.resolve(harness)
            if reader is not None and reader.read(session_id, 1):
                return reader
        return registry.resolve("antigravity")
    return None


def read_title(
    agent: str,
    session_id: str,
    opencode: Optional[OpencodeTranscript] = None,
    claude: Optional[ClaudeTranscript] = None,
    antigravity: Optional[AntigravityTranscript] = None,
) -> Optional[str]:
    """Routes to the matching connector and returns the session's conversation title.

    Name routing goes through the harness registry (aliases included);
    a UUID-shaped id whose named connector knows no title still gets the
    shape fallback. Returns None when no connector matches, the session
    store is unreadable, or the session has no assigned title.
    """
    registry = _build_registry(opencode, claude, antigravity)

    reader = registry.resolve(agent)
    if reader is not None:
        # A named harness answers with its own title support — no shape
        # sniffing behind its back (the registry is the decision site).
        return reader.read_title(session_id)
    fallback = _shape_fallback_reader(registry, session_id)
    if fallback is not None:
        return fallback.read_title(session_id)
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

    Routing mirrors the voice layer: match by harness name through the
    registry first, then sniff the session id shape. Returns ``None``
    when no connector matches or nothing readable is found, so callers
    can fall back to a terminal screen read.
    """
    n_turns = max(1, min(n_turns, MAX_TURNS))
    registry = _build_registry(opencode, claude, antigravity)

    reader = registry.resolve(agent)
    if reader is not None:
        turns = reader.read(session_id, n_turns)
        return turns or None

    fallback = _shape_fallback_reader(registry, session_id)
    if fallback is None:
        return None
    turns = fallback.read(session_id, n_turns)
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
