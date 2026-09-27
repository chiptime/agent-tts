"""Antigravity CLI connector: reads the last assistant message from local JSONL transcripts.

Antigravity persists each session at
``~/.gemini/antigravity-cli/brain/<session-uuid>/.system_generated/logs/transcript.jsonl``
(the folder name IS the session id). Each JSONL line carries ``step_index``,
``source`` (``MODEL``/``USER``/``SYSTEM``), ``type``, ``status`` and optional
``content``/``thinking``/``tool_calls`` fields. The visible assistant prose
lives on ``PLANNER_RESPONSE`` lines with non-empty ``content``: ``GENERIC``
lines are tool summaries ("Created At: ...", "The output was large and was
saved to: ..."), and thinking/tool payloads ride optional fields rather than
the content, so everything else must be skipped. This schema is
UNOFFICIAL/internal and may change between Antigravity versions. Sessions can
be very large, so reading is done with a reverse tail scan capped at ~8 MB.
"""

import json
import os
import re
from typing import Optional

from agent_tts.sources.claude import _reverse_tail_lines

DEFAULT_ROOT = os.path.expanduser("~/.gemini/antigravity-cli/brain")

# Reverse-scan safety cap: never read more than the trailing 8 MB of a session.
DEFAULT_SCAN_CAP_BYTES = 8 * 1024 * 1024

_UUIDISH_RE = re.compile(r"^[\da-fA-F-]{16,}$")


class AntigravitySource:
    """Read-only connector over Antigravity CLI's JSONL session transcripts."""

    def __init__(self, root: Optional[str] = None, scan_cap: int = DEFAULT_SCAN_CAP_BYTES):
        # Injectable root (and scan cap) for tests.
        self.root = root or DEFAULT_ROOT
        self.scan_cap = scan_cap

    def read(self, session_id: str) -> Optional[str]:
        """Returns the last PLANNER_RESPONSE content (the visible assistant prose) or None."""
        if not session_id or not _UUIDISH_RE.match(session_id):
            return None

        path = os.path.join(
            self.root, session_id, ".system_generated", "logs", "transcript.jsonl"
        )
        if not os.path.isfile(path):
            return None

        for raw_line in _reverse_tail_lines(path, self.scan_cap):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "PLANNER_RESPONSE":
                continue
            content = event.get("content")
            # Thinking-only planner lines (internal reasoning, tool calls)
            # carry no user-visible content and must be skipped so the scan
            # continues backwards to the last real response.
            if isinstance(content, str) and content.strip():
                return content
        return None
