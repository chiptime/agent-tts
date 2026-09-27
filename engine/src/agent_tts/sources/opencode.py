"""OpenCode connector: reads the last assistant message from OpenCode's local SQLite transcript.

Verified against OpenCode 1.18.31 (``~/.local/share/opencode/opencode.db``):
``message(id, session_id, time_created INT, data JSON $.role)`` and
``part(id, message_id, session_id, time_created INT, data JSON $.type in
text|tool|reasoning|step-start, $.text for text parts)``. Access is strictly
read-only (SQLite URI ``mode=ro``), so it is safe while OpenCode is running.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from agent_tts.sources.base import OPENCODE_SESSION_RE

DEFAULT_DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")

# Newest assistant message of the session, then its text parts in
# chronological order. Tool/reasoning/step-start parts are filtered out.
_LAST_ASSISTANT_TEXT_SQL = """
SELECT p.data
FROM part AS p
WHERE p.message_id = (
    SELECT m.id
    FROM message AS m
    WHERE m.session_id = ?
      AND json_extract(m.data, '$.role') = 'assistant'
    ORDER BY m.time_created DESC, m.id DESC
    LIMIT 1
)
  AND json_extract(p.data, '$.type') = 'text'
ORDER BY p.time_created ASC, p.id ASC
"""


class OpencodeSource:
    """Read-only connector over OpenCode's local transcript database."""

    def __init__(self, db_path: Optional[str] = None):
        # Injectable path for tests; env override mirrors the historical host behavior.
        self.db_path = db_path or os.environ.get("OPENCODE_DB") or DEFAULT_DB_PATH

    def read(self, session_id: str) -> Optional[str]:
        """Returns the newest assistant message text (parts joined with blank lines) or None."""
        if not session_id or not OPENCODE_SESSION_RE.match(session_id):
            return None
        if not os.path.isfile(self.db_path):
            return None

        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as conn:
                rows = conn.execute(_LAST_ASSISTANT_TEXT_SQL, (session_id,)).fetchall()
        except (sqlite3.Error, OSError):
            return None

        texts = []
        for (data,) in rows:
            try:
                text = json.loads(data).get("text")
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
            if isinstance(text, str) and text.strip():
                texts.append(text)
        if not texts:
            return None
        return "\n\n".join(texts)
