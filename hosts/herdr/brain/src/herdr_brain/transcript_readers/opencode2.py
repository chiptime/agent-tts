"""OpenCode 2 connector: the ``session_v2`` store in ``opencode.db``.

On-disk shape (pinned by the fixture under
``tests/fixtures/transcripts/opencode2``):

    session_v2(id TEXT PK, parent_id, title, …,
               time_created INT ms, time_updated INT ms)
    session_message(id TEXT PK, session_id, type, seq INT,
                    time_created INT ms, time_updated INT ms, data TEXT json)

The row's ``type`` IS the role (``user``, ``assistant``) or a session
event (``compaction``, ``synthetic``, ``system``, …) — only the roles
are turns. The turn's text is INLINE in ``data``:

- user: ``{"time":{…},"text":"…"}``
- assistant: ``{"time":{…},…,"content":[{"type":"text","text":"…"},
  {"type":"reasoning",…},{"type":"tool",…}]}`` — only the ``text``
  items are speech.

Same discipline as the V1 connector: the database path is fixed,
opened READONLY, queried with bound parameters only, and the session
id is shape-checked before it touches a query. The same physical
database also holds credential tables — this connector names
``session_v2`` and ``session_message`` and nothing else.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .base import Turn

DEFAULT_OPENCODE2_DB = "~/.local/share/opencode/opencode.db"
ENV_OPENCODE2_DB = "OPENCODE2_DB"

#: OpenCode session ids: ``ses_`` + base62-ish tail (unchanged by V2).
OPENCODE2_SESSION_RE = re.compile(r"^ses_[A-Za-z0-9_-]{6,}$")


def _ms_to_utc(value: object) -> Optional[datetime]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class Opencode2Transcript:
    """Read-only connector over OpenCode 2's ``session_v2`` store."""

    name = "opencode2"

    def __init__(self, db_path: Optional[str] = None):
        resolved = db_path or os.environ.get(ENV_OPENCODE2_DB) or DEFAULT_OPENCODE2_DB
        self.db_path = str(Path(resolved).expanduser())

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not OPENCODE2_SESSION_RE.match(session_id):
            return []
        if not os.path.isfile(self.db_path):
            return []

        sql = (
            "SELECT type, data, time_created FROM session_message "
            "WHERE session_id = ? AND type IN ('user', 'assistant') "
            "ORDER BY time_created DESC, seq DESC"
        )
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                rows = conn.execute(sql, (session_id,)).fetchall()
        except (sqlite3.Error, OSError):
            return []

        turns: List[Turn] = []
        skipped = 0
        for kind, data, time_created in rows:
            if len(turns) >= n_turns:
                break
            try:
                payload = json.loads(data) if isinstance(data, str) else {}
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            text = _turn_text(kind, payload)
            if not text:
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(
                Turn(role=kind, text=text, created_at=_ms_to_utc(time_created))
            )
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        if not session_id or not OPENCODE2_SESSION_RE.match(session_id):
            return None
        if not os.path.isfile(self.db_path):
            return None
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                row = conn.execute(
                    "SELECT title FROM session_v2 WHERE id = ?", (session_id,)
                ).fetchone()
        except (sqlite3.Error, OSError):
            return None
        if row and isinstance(row[0], str) and row[0].strip():
            return row[0].strip()
        return None


def _turn_text(kind: str, payload: dict) -> str:
    """The spoken text of one V2 row: ``data.text`` or the ``text``
    items of ``data.content``."""
    if kind == "user":
        text = payload.get("text")
        return text.strip() if isinstance(text, str) else ""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    pieces = [
        block.get("text", "").strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(piece for piece in pieces if piece)
