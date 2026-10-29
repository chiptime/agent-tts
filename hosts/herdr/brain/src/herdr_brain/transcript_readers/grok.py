"""Grok Build connector: ``chat_history.jsonl`` session folders.

On-disk shape (learned from real sessions, kept here so the parser is
readable against its source):

    <root>/<urlencoded-cwd>/<session-uuid>/chat_history.jsonl
    {"type":"system","content":"…"}                                ← dropped
    {"type":"user","content":[{type:"text",text:"…"}],"prompt_index":N}
    {"type":"user","content":[…],"synthetic_reason":"…"}           ← dropped
    {"type":"reasoning","summary":[…],"encrypted_content":"…"}     ← dropped
    {"type":"assistant","content":"…" | [{type:"text",…}]}
    {"type":"backend_tool_call"|"tool_result",…}                   ← dropped

User speech is wrapped in ``<user_query>…</user_query>``; the SAME
``user`` row type also carries injected plumbing (skills lists, banners)
distinguished by ``synthetic_reason`` — only a real prompt renders, or a
``prompt_index`` turn whose text survives unwrapped.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import List, Optional

from .base import Turn, iso_to_utc, parse_json_line, reverse_tail_lines, SCAN_CAP_BYTES

DEFAULT_GROK_ROOT = "~/.grok/sessions"
ENV_GROK_ROOT = "GROK_ROOT"

GROK_SESSION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)


def _content_text(content) -> Optional[str]:
    """Flattens a content list (or bare string) into text."""
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


def user_query_text(raw: str) -> Optional[str]:
    """Inner text of the first ``<user_query>`` wrapper, trimmed."""
    match = _USER_QUERY_RE.search(raw)
    if not match:
        return None
    inner = (match.group(1) or "").strip()
    return inner or None


class GrokTranscript:
    """Read-only connector over Grok Build's chat history transcripts."""

    name = "grok"

    def __init__(self, root: Optional[str] = None, scan_cap: int = SCAN_CAP_BYTES):
        resolved = root or os.environ.get(ENV_GROK_ROOT) or DEFAULT_GROK_ROOT
        self.root = str(Path(resolved).expanduser())
        self.scan_cap = scan_cap

    def _session_file(self, session_id: str) -> Optional[str]:
        matches = sorted(
            glob.glob(os.path.join(self.root, "*", session_id, "chat_history.jsonl"))
        )
        return matches[0] if matches else None

    def read(
        self, session_id: str, n_turns: int = 10, skip_newest: int = 0
    ) -> List[Turn]:
        if not session_id or not GROK_SESSION_RE.match(session_id):
            return []
        path = self._session_file(session_id)
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
            kind = event.get("type")
            if isinstance(event.get("synthetic_reason"), str):
                continue  # injected plumbing, never the human's words
            if kind == "user":
                raw = _content_text(event.get("content"))
                if raw is None:
                    continue
                spoken = user_query_text(raw)
                if spoken is None and isinstance(event.get("prompt_index"), int):
                    spoken = raw.strip() or None
                if spoken is None:
                    continue
            elif kind == "assistant":
                spoken = _content_text(event.get("content"))
                if spoken is None:
                    continue
            else:
                continue
            if skipped < skip_newest:
                skipped += 1
                continue
            turns.append(Turn(role=kind, text=spoken, created_at=None))
        turns.reverse()
        return turns

    def read_title(self, session_id: str) -> Optional[str]:
        return None
