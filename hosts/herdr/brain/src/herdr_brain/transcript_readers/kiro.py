"""Kiro V3 connector: ``messages.jsonl`` under ``$KIRO_ROOT``.

On-disk shape (pinned by the fixture under
``tests/fixtures/transcripts/kiro``, synthesized from the documented
row grammar):

    <root>/<bucket>/<session-id>/messages.jsonl
    {"id":"row-1","timestamp":"…","payload":{"type":"user","content":"…"}}
    {"id":"row-2","timestamp":"…","payload":{"type":"assistant",
     "operationType":"Say","content":"…"}}

Read rows: ``user`` (its ``content``; a row carrying
``_meta.kiro.syntheticUserMessageReason`` is the agent's own steering
hand-off, not the operator) and ``assistant`` with
``operationType`` ``Say`` or ``Summary``. Tool calls, tool results,
sub-agent cards and the lifecycle rows are not conversation turns.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import List, Optional

from .base import Turn, iso_to_utc, parse_json_line, reverse_tail_lines, SCAN_CAP_BYTES

DEFAULT_KIRO_ROOT = "~/.kiro/sessions"
ENV_KIRO_ROOT = "KIRO_ROOT"

#: A session id becomes a path component, so only a plain name is
#: accepted.
KIRO_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class KiroTranscript:
    """Read-only connector over Kiro V3 session messages."""

    name = "kiro"

    def __init__(self, root: Optional[str] = None, scan_cap: int = SCAN_CAP_BYTES):
        resolved = root or os.environ.get(ENV_KIRO_ROOT) or DEFAULT_KIRO_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def _log_for(self, session_id: str) -> Optional[str]:
        pattern = os.path.join(self.root, "**", session_id, "messages.jsonl")
        matches = [path for path in glob.glob(pattern, recursive=True) if os.path.isfile(path)]
        if not matches:
            return None
        return max(matches, key=os.path.getmtime)

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not KIRO_SESSION_RE.match(session_id):
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
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            stamp = iso_to_utc(event.get("timestamp"))
            if kind == "user":
                meta = payload.get("_meta")
                synthetic = (
                    isinstance(meta, dict)
                    and isinstance(meta.get("kiro"), dict)
                    and "syntheticUserMessageReason" in meta["kiro"]
                )
                content = payload.get("content")
                if synthetic or not isinstance(content, str) or not content.strip():
                    continue
                text = content.strip()
            elif kind == "assistant":
                operation = payload.get("operationType")
                content = payload.get("content")
                if operation not in ("Say", "Summary"):
                    continue
                if not isinstance(content, str) or not content.strip():
                    continue
                text = content.strip()
            else:
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(Turn(role="user" if kind == "user" else "assistant", text=text, created_at=stamp))
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None
