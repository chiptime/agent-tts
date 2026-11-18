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

    def read_error(self, session_id: str) -> Optional[dict]:
        """The error the session's newest message ended on, or None.

        OpenCode records a failed turn (provider error, usage limit, context
        overflow) as an assistant message whose ``$.error`` is set and that
        usually carries no text part, so the agent simply stops with nothing
        new to read. Only the NEWEST message counts: any later message means
        the session moved on. A user abort (``MessageAbortedError``) is a
        deliberate stop, not a failure, and is ignored.
        """
        if not session_id or not OPENCODE_SESSION_RE.match(session_id):
            return None
        if not os.path.isfile(self.db_path):
            return None
        sql = """
        SELECT json_extract(data, '$.role'),
               json_extract(data, '$.error.name'),
               json_extract(data, '$.error.data.message')
        FROM message
        WHERE session_id = ?
        ORDER BY time_created DESC, id DESC
        LIMIT 1
        """
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                row = conn.execute(sql, (session_id,)).fetchone()
        except (sqlite3.Error, OSError):
            return None
        if not row or row[0] != "assistant" or not row[1]:
            return None
        name = str(row[1])
        if name == "MessageAbortedError":
            return None
        message = row[2] if isinstance(row[2], str) and row[2].strip() else name
        return {"name": name, "message": message.strip()}

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

    def read_error(self, session_id: str) -> Optional[dict]:
        """The API error the session's newest turn event ended on, or None.

        Claude Code records a failed request as a synthetic assistant event
        flagged ``isApiErrorMessage`` whose text block holds the error
        ("API Error: …") and whose ``error`` names its class. Only the
        newest user/assistant event counts: anything later means the
        session moved on. Bookkeeping events (system, summaries) are skipped.
        """
        if not session_id or not CLAUDE_SESSION_RE.match(session_id):
            return None
        matches = sorted(glob.glob(os.path.join(self.root, "*", f"{session_id}.jsonl")))
        if not matches:
            return None
        for raw_line in _reverse_tail_lines(matches[0], self.scan_cap):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") not in ("user", "assistant"):
                continue
            if event.get("type") != "assistant" or event.get("isApiErrorMessage") is not True:
                return None
            name = event.get("error") if isinstance(event.get("error"), str) else ""
            message = _claude_event_text(event) or name or "API Error"
            return {"name": name or "APIError", "message": message}
        return None

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

    def _transcript_path(self, session_id: str) -> Optional[str]:
        if not session_id or not ANTIGRAVITY_SESSION_RE.match(session_id):
            return None
        base_dir = Path(self.root) / session_id / ".system_generated" / "logs"
        for candidate in (base_dir / "transcript_full.jsonl", base_dir / "transcript.jsonl"):
            if candidate.is_file():
                return str(candidate)
        return None

    def read_error(self, session_id: str) -> Optional[dict]:
        """The error the session's newest step ended on, or None.

        Antigravity logs a failed model call as an ``ERROR_MESSAGE`` step
        (``"API error (attempt 8): RESOURCE_EXHAUSTED …"``). Retries log one
        per attempt, so only the NEWEST conversation step counts: a later
        user input, planner response or tool step means it moved on.
        Bookkeeping steps (system messages, checkpoints) are skipped.
        """
        path = self._transcript_path(session_id)
        if path is None:
            return None
        for raw_line in _reverse_tail_lines(path, self.scan_cap):
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
            if event_type not in ("USER_INPUT", "PLANNER_RESPONSE", "GENERIC", "ERROR_MESSAGE"):
                continue
            if event_type != "ERROR_MESSAGE":
                return None
            error = event.get("error")
            message = error.strip() if isinstance(error, str) and error.strip() else "error"
            return {"name": "ERROR_MESSAGE", "message": message}
        return None

    def read(self, session_id: str, n_turns: int = 10, skip_newest: int = 0) -> List[Turn]:
        path = self._transcript_path(session_id)
        if path is None:
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


#: herdr states in which an agent is still alive; an error never applies.
LIVE_STATUSES = ("working", "blocked")


def read_session_error(agent: str, status: str, session_id: str) -> Optional[dict]:
    """``{name, message}`` when a pane's session stopped on an error, else None.

    The one decision site shared by /herd and the watcher. A pane that is
    working or blocked is alive, so it never reports an error; only
    connectors offering ``read_error`` (OpenCode, Claude Code, pi/omp and
    Antigravity) can report one.
    Never raises: any failure reads as "no error".
    """
    if not session_id or (status or "").strip().lower() in LIVE_STATUSES:
        return None
    try:
        reader = connector_for(agent, session_id)
        read_error = getattr(reader, "read_error", None)
        error = read_error(session_id) if read_error else None
    except Exception:  # noqa: BLE001 — a per-pane failure must not spread
        return None
    if not error:
        return None
    return {"name": str(error.get("name") or ""), "message": str(error.get("message") or "")}


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


# ── resume enumeration (parity-f7c S3, doc 03 §3) ────────────────────
#
# The resume sheet needs the conversations each transcript store can
# name, WITH the folder they ran in — "reopen in its folder" is only
# possible when the store records a cwd. Enumeration walks the SAME
# roots/env vars the registry readers use (the reader instances carry
# them), so overrides keep applying. Rows without a cwd are skipped:
# never offer a conversation you cannot reopen.


#: How many recent conversations /sessions/recent serves, at most.
RESUME_SESSIONS_CAP = 10

#: Raw label budget before the caller's mask-and-clip (bounded, not display).
_RESUME_LABEL_CHARS = 200

#: How much of a transcript head the cwd lookups scan (claude writes a
#: ``cwd`` on every user event; codex/pi on their first header/meta line).
_HEAD_SCAN_BYTES = 256 * 1024


def _reader_for(harness: Optional[str], registry=None):
    """The registry's canonical reader for ``harness`` (aliases resolve)."""
    reg = registry or _build_registry()
    return reg.resolve(harness)


def _mtime_iso(path: str) -> Optional[str]:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        return None


def _newest_files(patterns, cap: int) -> List[str]:
    """Up to ``cap`` existing files across glob patterns, newest first."""
    matches: List[str] = []
    for pattern in patterns:
        # recursive so codex's dated ``**/rollout-*.jsonl`` tree matches.
        matches.extend(
            path for path in glob.glob(pattern, recursive=True) if os.path.isfile(path)
        )
    try:
        matches.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        return []
    return matches[:cap]


def _iter_head_json(path: str):
    """Yields parsed JSON objects from the first head bytes of ``path``."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(_HEAD_SCAN_BYTES)
    except OSError:
        return
    for line in head.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event


def _session_label(reader, session_id: str) -> str:
    """The session's title, or the newest turn's text as the label.

    Raw text — masking is the caller's outbound duty; this only bounds
    the size so a transcript turn cannot ride whole into a payload."""
    try:
        title = reader.read_title(session_id) if reader is not None else None
    except Exception:  # noqa: BLE001 — a broken store is an empty label
        title = None
    if isinstance(title, str) and title.strip():
        return title.strip()[:_RESUME_LABEL_CHARS]
    try:
        turns = reader.read(session_id, 1) if reader is not None else []
    except Exception:  # noqa: BLE001
        turns = []
    if turns:
        return (turns[-1].text or "").strip()[:_RESUME_LABEL_CHARS]
    return ""


def _rows_opencode(reader, cap: int) -> List[dict]:
    rows: List[dict] = []
    db_path = getattr(reader, "db_path", "")
    if not db_path or not os.path.isfile(db_path):
        return rows
    sql = (
        "SELECT id, title, directory, time_updated FROM session "
        "WHERE directory IS NOT NULL AND directory != '' "
        "ORDER BY time_updated DESC LIMIT ?"
    )
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            fetched = conn.execute(sql, (cap,)).fetchall()
    except (sqlite3.Error, OSError):
        return rows
    for session_id, title, directory, _updated in fetched:
        if not isinstance(session_id, str) or not OPENCODE_SESSION_RE.match(session_id):
            continue
        if not isinstance(directory, str) or not directory.strip():
            continue
        label = title.strip() if isinstance(title, str) and title.strip() else ""
        rows.append({
            "session_id": session_id,
            "cwd": directory,
            "title": label[:_RESUME_LABEL_CHARS],
            "last_ts": None,  # time_updated types vary by build; no guessing
        })
    return rows


def _claude_session_cwd(path: str) -> Optional[str]:
    """The session's cwd, from its first event that carries one.

    Verified live: every user event in a Claude Code JSONL transcript
    records the ``cwd`` it ran in — the munged project dir name is NOT
    decoded (lossy), the field is read instead."""
    for event in _iter_head_json(path):
        cwd = event.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()
    return None


def _rows_claude(reader, cap: int) -> List[dict]:
    root = getattr(reader, "root", "")
    if not root:
        return []
    rows: List[dict] = []
    for path in _newest_files([os.path.join(root, "*", "*.jsonl")], cap * 3):
        session_id = os.path.splitext(os.path.basename(path))[0]
        if not CLAUDE_SESSION_RE.match(session_id):
            continue
        cwd = _claude_session_cwd(path)
        if not cwd:
            continue  # a session without a folder cannot be reopened
        rows.append({
            "session_id": session_id,
            "cwd": cwd,
            "title": _session_label(reader, session_id),
            "last_ts": _mtime_iso(path),
        })
        if len(rows) >= cap:
            break
    return rows


def _codex_session_meta(path: str) -> Optional[dict]:
    """The rollout's ``session_meta`` payload, or None.

    Verified live: the payload carries the session id (``session_id``)
    and the ``cwd`` the rollout ran in."""
    for event in _iter_head_json(path):
        if event.get("type") != "session_meta":
            continue
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else None
    return None


def _rows_codex(reader, cap: int) -> List[dict]:
    from .transcript_readers.codex import CODEX_SESSION_RE

    root = getattr(reader, "root", "")
    if not root:
        return []
    rows: List[dict] = []
    pattern = os.path.join(root, "**", "rollout-*.jsonl")
    for path in _newest_files([pattern], cap * 3):
        meta = _codex_session_meta(path)
        if meta is None:
            continue
        session_id = meta.get("session_id") or meta.get("id")
        cwd = meta.get("cwd")
        if not isinstance(session_id, str) or not CODEX_SESSION_RE.match(session_id):
            continue
        if not isinstance(cwd, str) or not cwd.strip():
            continue
        rows.append({
            "session_id": session_id,
            "cwd": cwd.strip(),
            "title": _session_label(reader, session_id),
            "last_ts": _mtime_iso(path),
        })
        if len(rows) >= cap:
            break
    return rows


def _rows_pi(reader, cap: int) -> List[dict]:
    from .transcript_readers.pi import PI_SESSION_RE

    roots = getattr(reader, "roots", [])
    if not roots:
        return []
    rows: List[dict] = []
    for path in _newest_files(
        [os.path.join(root, "*", "*.jsonl") for root in roots], cap * 3
    ):
        header = next(_iter_head_json(path), None)
        if not isinstance(header, dict) or header.get("type") != "session":
            continue
        session_id = header.get("id")
        cwd = header.get("cwd")
        if not isinstance(session_id, str) or not PI_SESSION_RE.fullmatch(session_id):
            continue
        if not isinstance(cwd, str) or not cwd.strip():
            continue
        rows.append({
            "session_id": session_id,
            "cwd": cwd.strip(),
            "title": _session_label(reader, session_id),
            "last_ts": _mtime_iso(path),
        })
        if len(rows) >= cap:
            break
    return rows


#: harness → its recent-session enumerator. Only the resume-ENABLED
#: harnesses have one; a harness here is store-knowledge, NOT resume
#: permission (the verified argv table lives in tools.py).
_RESUME_ROW_READERS = {
    "claude": _rows_claude,
    "codex": _rows_codex,
    "opencode": _rows_opencode,
    "pi": _rows_pi,
}


def recent_sessions(
    harness: Optional[str], cap: int = RESUME_SESSIONS_CAP, registry=None
) -> List[dict]:
    """Recent conversations of ``harness`` the stores can name and place.

    Rows are ``{session_id, cwd, title, last_ts}`` — raw label text
    (masking is the outbound caller's duty), a cwd the store itself
    recorded (rows without one are skipped), newest first, capped. A
    harness without an enumerator (or a broken store) answers ``[]``,
    never an error: the sheet just shows nothing to resume."""
    name = (harness or "").strip().lower()
    enumerator = _RESUME_ROW_READERS.get(name)
    if enumerator is None:
        return []
    reader = _reader_for(name, registry)
    if reader is None:
        return []
    try:
        return enumerator(reader, max(1, min(cap, 50)))
    except Exception:  # noqa: BLE001 — a broken store is an empty list
        return []


def resume_target(
    harness: Optional[str], session_id: str, registry=None
) -> Optional[dict]:
    """One session's ``{cwd, title}``, or None when the store names none.

    The session id is shape-checked per harness BEFORE it ever touches a
    path or query. The cwd comes from the store itself (claude's event
    ``cwd``, codex's ``session_meta``, opencode's ``directory``, pi's
    session header) — when the store does not record one, ``cwd`` is
    None and the caller must refuse rather than guess a folder."""
    name = (harness or "").strip().lower()
    clean_sid = (session_id or "").strip()
    if not name or not clean_sid:
        return None
    reader = _reader_for(name, registry)
    if reader is None:
        return None
    try:
        if name == "opencode":
            if not OPENCODE_SESSION_RE.match(clean_sid):
                return None
            return _resume_target_opencode(reader, clean_sid)
        if name == "claude":
            if not CLAUDE_SESSION_RE.match(clean_sid):
                return None
            root = getattr(reader, "root", "")
            matches = [
                path
                for path in glob.glob(os.path.join(root, "*", f"{clean_sid}.jsonl"))
                if os.path.isfile(path)
            ]
            if not matches:
                return None
            path = max(matches, key=os.path.getmtime)
            return {
                "cwd": _claude_session_cwd(path),
                "title": _session_label(reader, clean_sid),
            }
        if name == "codex":
            from .transcript_readers.codex import CODEX_SESSION_RE

            if not CODEX_SESSION_RE.match(clean_sid):
                return None
            root = getattr(reader, "root", "")
            matches = [
                path
                for path in glob.glob(
                    os.path.join(root, "**", f"rollout-*{clean_sid}*.jsonl"),
                    recursive=True,
                )
                if os.path.isfile(path)
            ]
            if not matches:
                return None
            meta = _codex_session_meta(max(matches, key=os.path.getmtime))
            if meta is None:
                return None
            session_id = meta.get("session_id") or meta.get("id")
            if not isinstance(session_id, str):
                return None
            cwd = meta.get("cwd")
            return {
                "cwd": cwd.strip() if isinstance(cwd, str) and cwd.strip() else None,
                "title": _session_label(reader, clean_sid),
            }
        if name == "pi":
            from .transcript_readers.pi import PI_SESSION_RE

            match = PI_SESSION_RE.search(clean_sid)
            if not match:
                return None
            uuid_str = match.group(0)
            for root in getattr(reader, "roots", []):
                matches = sorted(
                    glob.glob(os.path.join(root, "*", f"*_{uuid_str}.jsonl"))
                )
                for path in matches:
                    if not os.path.isfile(path):
                        continue
                    header = next(_iter_head_json(path), None)
                    if not isinstance(header, dict):
                        continue
                    cwd = header.get("cwd")
                    return {
                        "cwd": cwd.strip() if isinstance(cwd, str) else None,
                        "title": _session_label(reader, uuid_str),
                    }
            return None
    except Exception:  # noqa: BLE001 — a broken store answers "unknown"
        return None
    return None


def _resume_target_opencode(reader, session_id: str) -> Optional[dict]:
    db_path = getattr(reader, "db_path", "")
    if not db_path or not os.path.isfile(db_path):
        return None
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute(
                "SELECT directory, title FROM session WHERE id = ?", (session_id,)
            ).fetchone()
    except (sqlite3.Error, OSError):
        return None
    if row is None:
        return None
    directory, title = row
    return {
        "cwd": directory.strip() if isinstance(directory, str) and directory.strip() else None,
        "title": title.strip() if isinstance(title, str) and title.strip() else "",
    }
