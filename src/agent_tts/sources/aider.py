"""Aider connector: reads the last assistant turn from aider's markdown chat history.

Aider (AI pair programming CLI) appends every conversation turn to a markdown
file, by default ``.aider.chat.history.md`` in the project working directory
(user-configurable via aider's ``--chat-history-file``, mirrored here through
the ``AIDER_CHAT_HISTORY`` environment variable). There is no daemon or session
store: the whole file IS the session, so the session id handed over by the host
is a pass-through label and only its presence is validated.

Turn boundaries are ``#### User:`` / ``#### Assistant:`` headings followed by
prose that routinely contains fenced code blocks. A line that *looks* like a
turn heading inside a fence is transcript content, not a boundary, so parsing
tracks fence state (CommonMark-style ``` and ~~~ runs) instead of splitting on
headings alone. The file is scanned forwards because fence state is inherently
forward context; memory stays bounded because only the current assistant turn
is retained.
"""

import os
import re
from typing import List, Optional, Tuple

# Aider's default: the history file lives in the project working directory.
DEFAULT_HISTORY_PATH = ".aider.chat.history.md"

# Turn headings ("#### User:" / "#### Assistant:"). Lenient about trailing
# text after the colon; slash-command records ("#### /add ...") never match.
_TURN_HEADING_RE = re.compile(r"^####\s+(User|Assistant)\s*:")

# A fenced code block delimiter: 3+ backticks or tildes, optional info string.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")

_FenceState = Optional[Tuple[str, int]]


def _advance_fence(line: str, state: _FenceState) -> _FenceState:
    """Returns the fence state after consuming ``line``; None means outside.

    A fence opens on a 3+ marker run (backtick openers must not carry
    backticks in their info string) and only closes on a same-character run
    at least as long with no info string.
    """
    match = _FENCE_RE.match(line)
    if not match:
        return state
    marker, info = match.group(1), match.group(2)
    char, length = marker[0], len(marker)
    if state is None:
        if char == "`" and "`" in info:
            return None  # not a valid opener: plain text
        return (char, length)
    if char == state[0] and length >= state[1] and not info.strip():
        return None  # closing fence
    return state


class AiderSource:
    """Read-only connector over aider's markdown chat history file."""

    def __init__(self, history_path: Optional[str] = None):
        # Injectable path for tests; env override mirrors aider's
        # --chat-history-file. Default is the project working directory.
        self.history_path = (
            history_path
            or os.environ.get("AIDER_CHAT_HISTORY")
            or DEFAULT_HISTORY_PATH
        )

    def read(self, session_id: str) -> Optional[str]:
        """Returns the last assistant turn's prose (fences preserved) or None.

        Aider keeps no per-session records: the session id is a pass-through
        label from the host contract and only non-blank values are accepted.
        """
        if not session_id or not session_id.strip():
            return None
        if not os.path.isfile(self.history_path):
            return None
        return self._last_assistant_turn()

    def _last_assistant_turn(self) -> Optional[str]:
        """One forward pass keeping the most recent non-empty assistant turn.

        Every turn boundary flushes the just-closed segment, so a trailing
        user turn (or an assistant turn without prose) never displaces the
        last readable assistant message.
        """
        last_text: Optional[str] = None
        collecting = False
        body: List[str] = []
        fence: _FenceState = None

        def flush() -> None:
            nonlocal last_text
            if collecting:
                text = "".join(body).strip()
                if text:
                    last_text = text

        with open(self.history_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                fence = _advance_fence(line, fence)
                if fence is None:
                    heading = _TURN_HEADING_RE.match(line)
                    if heading:
                        flush()
                        collecting = heading.group(1) == "Assistant"
                        body = []
                        continue
                if collecting:
                    body.append(line)
        flush()
        return last_text
