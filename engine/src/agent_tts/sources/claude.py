"""Claude Code connector: reads the last assistant message from local JSONL transcripts.

Claude Code persists sessions at ``~/.claude/projects/<munged-cwd>/<session-uuid>.jsonl``
(the filename minus ``.jsonl`` IS the session id). Lines are JSON events;
assistant messages have ``type == "assistant"`` and ``message.content`` as a
list of blocks (``text``, ``thinking``, ``tool_use``, ...). Non-assistant
events (cost-state, system, progress) are interleaved, and sessions can be
very large, so reading is done with a reverse tail scan capped at ~8 MB.
"""

import glob
import json
import os
import re
from typing import Iterator, Optional

from agent_tts.sources.base import CLAUDE_SESSION_RE

DEFAULT_ROOT = os.path.expanduser("~/.claude/projects")

# Reverse-scan safety cap: never read more than the trailing 8 MB of a session.
DEFAULT_SCAN_CAP_BYTES = 8 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024

_UUIDISH_RE = re.compile(r"^[\da-fA-F-]{16,}$")


class ClaudeSource:
    """Read-only connector over Claude Code's JSONL session transcripts."""

    def __init__(self, root: Optional[str] = None, scan_cap: int = DEFAULT_SCAN_CAP_BYTES):
        # Injectable root (and scan cap) for tests.
        self.root = root or DEFAULT_ROOT
        self.scan_cap = scan_cap

    def read(self, session_id: str) -> Optional[str]:
        """Returns the last assistant message text (text blocks joined with newlines) or None."""
        if not session_id or not _UUIDISH_RE.match(session_id):
            return None

        matches = sorted(glob.glob(os.path.join(self.root, "*", f"{session_id}.jsonl")))
        if not matches:
            return None
        return self._last_assistant_text(matches[0])

    def _last_assistant_text(self, path: str) -> Optional[str]:
        """Scans the transcript backwards for the last assistant message with non-empty text."""
        for raw_line in _reverse_tail_lines(path, self.scan_cap):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "assistant":
                continue
            text = _assistant_text(event)
            if text:
                return text
        return None


def _assistant_text(event: dict) -> Optional[str]:
    """Extracts the joined text blocks of an assistant event; None when it has none."""
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


def _reverse_tail_lines(path: str, cap_bytes: int) -> Iterator[bytes]:
    """Yields complete lines of a (possibly huge) file, newest first.

    Seeks chunks from the end of the file and splits complete lines so the
    caller can stop as soon as the last relevant record is found without
    loading the whole session into memory. Stops after ``cap_bytes`` scanned.
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
            # At the file start every line is complete; in the middle the
            # first piece belongs to a line completed by an earlier chunk.
            remainder = lines.pop(0) if position > 0 else b""
            for line in reversed(lines):
                yield line
