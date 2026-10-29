"""Cursor CLI connector: agent transcripts under ``$CURSOR_ROOT``.

On-disk shape (the layout the Cursor CLI writes; the fixture under
``tests/fixtures/transcripts/cursor`` pins the row grammar this parser
reads — synthesized pending a live capture, so the reader fails closed
to an empty page rather than guessing a different envelope):

    <root>/projects/<encoded-cwd>/agent-transcripts/<sid>/<sid>.jsonl
    {"timestamp":"…","role":"user"|"assistant","text":"…"}

One JSON object per line; every other row kind is skipped.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import List, Optional

from .base import Turn, iso_to_utc, parse_json_line, reverse_tail_lines, SCAN_CAP_BYTES

DEFAULT_CURSOR_ROOT = "~/.cursor/projects"
ENV_CURSOR_ROOT = "CURSOR_ROOT"

#: A Cursor session id is a plain path component: only a safe name is
#: ever joined onto a root.
CURSOR_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CursorTranscript:
    """Read-only connector over Cursor CLI agent transcripts."""

    name = "cursor"

    def __init__(self, root: Optional[str] = None, scan_cap: int = SCAN_CAP_BYTES):
        resolved = root or os.environ.get(ENV_CURSOR_ROOT) or DEFAULT_CURSOR_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not CURSOR_SESSION_RE.match(session_id):
            return []
        pattern = os.path.join(
            self.root, "projects", "*", "agent-transcripts", session_id, f"{session_id}.jsonl"
        )
        matches = [path for path in glob.glob(pattern) if os.path.isfile(path)]
        if not matches:
            return []
        path = max(matches, key=os.path.getmtime)

        turns: List[Turn] = []
        skipped = 0
        for raw_line in reverse_tail_lines(path, self.scan_cap):
            if len(turns) >= n_turns:
                break
            event = parse_json_line(raw_line)
            if event is None:
                continue
            role = event.get("role")
            if role not in ("user", "assistant"):
                continue
            text = event.get("text") if isinstance(event.get("text"), str) else ""
            if not text.strip():
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(Turn(role=role, text=text.strip(), created_at=iso_to_utc(event.get("timestamp"))))
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None
