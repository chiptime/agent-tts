"""Codex CLI connector: reads the last assistant message from rollout JSONL transcripts.

Codex persists each session at
``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<session-uuid>.jsonl``.
Lines are ``{"timestamp", "type", "payload"}`` events. Assistant prose prefers
``response_item`` payloads with ``payload.type == "message"`` and
``role == "assistant"`` (joining ``content[].text`` of ``output_text`` parts);
some Codex versions also emit assistant text through ``event_msg`` payloads
with ``payload.type == "agent_message"`` as an alternate channel, used only
when no ``response_item`` assistant message exists in the scan window.
User-role messages carry developer/environment noise (``<environment_context>``,
``<turn_aborted>`` blocks) and are ignored. The rollout schema is
UNOFFICIAL/internal and may change between Codex versions. Sessions can be
very large, so reading is done with a reverse tail scan capped at ~8 MB.
"""

import glob
import json
import os
import re
from typing import Optional

from agent_tts.sources.claude import _reverse_tail_lines

DEFAULT_SESSIONS_ROOT = os.path.expanduser("~/.codex/sessions")

# Reverse-scan safety cap: never read more than the trailing 8 MB of a session.
DEFAULT_SCAN_CAP_BYTES = 8 * 1024 * 1024

_UUIDISH_RE = re.compile(r"^[\da-fA-F-]{16,}$")


class CodexSource:
    """Read-only connector over Codex CLI's rollout JSONL transcripts."""

    def __init__(
        self, sessions_root: Optional[str] = None, scan_cap: int = DEFAULT_SCAN_CAP_BYTES
    ):
        # Injectable sessions root (and scan cap) for tests.
        self.sessions_root = sessions_root or DEFAULT_SESSIONS_ROOT
        self.scan_cap = scan_cap

    def read(self, session_id: str) -> Optional[str]:
        """Returns the last assistant message text (preferred channel first) or None."""
        if not session_id or not _UUIDISH_RE.match(session_id):
            return None

        matches = sorted(
            glob.glob(
                os.path.join(
                    self.sessions_root, "*", "*", "*", f"rollout-*{session_id}.jsonl"
                )
            )
        )
        for path in matches:
            # The glob suffix alone could match a different uuid that merely
            # ends with the requested id; anchor on the full filename tail.
            if os.path.basename(path).endswith(f"-{session_id}.jsonl"):
                return self._last_assistant_text(path)
        return None

    def _last_assistant_text(self, path: str) -> Optional[str]:
        """Scans backwards for the last assistant text across both channels.

        ``response_item`` assistant messages always win, wherever they appear
        in the scan window; the ``event_msg`` ``agent_message`` channel is
        only a fallback for when the window holds no preferred-channel hit.
        """
        fallback: Optional[str] = None
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
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            event_type = event.get("type")
            if (
                event_type == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "assistant"
            ):
                text = _output_text(payload)
                if text:
                    # Preferred channel: beats any agent_message already seen.
                    return text
            elif event_type == "event_msg" and payload.get("type") == "agent_message":
                if fallback is None:
                    message = payload.get("message")
                    # Reverse order: the first hit seen is the newest one.
                    if isinstance(message, str) and message.strip():
                        fallback = message
        return fallback


def _output_text(payload: dict) -> Optional[str]:
    """Extracts the joined output_text parts of an assistant payload; None when empty."""
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    parts = [
        str(part.get("text", "")).strip()
        for part in content
        if isinstance(part, dict) and part.get("type") == "output_text"
    ]
    text = "\n".join(part for part in parts if part)
    return text or None
