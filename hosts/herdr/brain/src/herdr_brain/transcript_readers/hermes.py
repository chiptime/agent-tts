"""Hermes connector: one SQLite ``state.db`` per profile.

On-disk shape (learned from real stores, kept here so the parser is
readable against its source):

    <root>/state.db                     ← default profile
    <root>/profiles/<name>/state.db     ← each named profile

    messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
             content TEXT, …, timestamp REAL, active INTEGER,
             compacted INTEGER, display_kind TEXT)

A row renders when it is still live (``active = 1``) or kept by a
compaction (``compacted = 1``), is not hidden, and carries a plain-text
``content`` for role ``user``/``assistant``. The pane supplies the
session id; the base database is searched first, then every profile's —
profile names come from the root's own directory listing, never from a
request.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from .base import Turn, epoch_s_to_utc

DEFAULT_HERMES_ROOT = "~/.hermes"
ENV_HERMES_ROOT = "HERMES_ROOT"

HERMES_SESSION_RE = re.compile(r"^(?:\d{8}_\d{6}_[A-Za-z0-9]+|local-[a-f0-9]{64})$")

_READ_SQL = """
SELECT id, role, content, timestamp FROM messages
WHERE session_id = ?
  AND (active = 1 OR compacted = 1)
  AND (display_kind IS NULL OR display_kind != 'hidden')
  AND role IN ('user', 'assistant')
ORDER BY id DESC
LIMIT ? OFFSET ?
"""


def _candidate_dbs(root: str) -> List[str]:
    """The root's own state.db first, then each profile's."""
    base = Path(root).expanduser()
    found: List[str] = []
    own = base / "state.db"
    if own.is_file():
        found.append(str(own))
    profiles = base / "profiles"
    if profiles.is_dir():
        try:
            for child in sorted(profiles.iterdir()):
                db = child / "state.db"
                if db.is_file():
                    found.append(str(db))
        except OSError:
            pass
    return found


class HermesTranscript:
    """Read-only connector over Hermes' per-profile SQLite stores."""

    name = "hermes"

    def __init__(self, root: Optional[str] = None):
        resolved = root or os.environ.get(ENV_HERMES_ROOT) or DEFAULT_HERMES_ROOT
        self.root = str(Path(resolved).expanduser())

    def _read_rows(self, session_id: str, limit: int, offset: int) -> Optional[List[Turn]]:
        for db_path in _candidate_dbs(self.root):
            uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
            try:
                with sqlite3.connect(uri, uri=True) as conn:
                    rows = conn.execute(_READ_SQL, (session_id, limit, offset)).fetchall()
            except (sqlite3.Error, OSError):
                continue
            if not rows:
                continue
            turns = [
                Turn(role=role, text=text, created_at=epoch_s_to_utc(stamp))
                for _id, role, text, stamp in reversed(rows)
                if isinstance(text, str) and text.strip()
            ]
            if turns:
                return turns
        return None

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not HERMES_SESSION_RE.match(session_id):
            return []
        try:
            return self._read_rows(session_id, max(1, n_turns), max(0, skip_newest)) or []
        except Exception:  # noqa: BLE001 — a broken store reads as empty
            return []

    def read_title(self, session_id: str) -> Optional[str]:
        return None
