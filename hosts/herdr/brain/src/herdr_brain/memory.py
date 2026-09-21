"""In-process conversation memory for the brain.

One ring buffer of recent messages per client session id. The system message
is rebuilt per request (with live context) and never stored here; only real
user/assistant turns live in the ring. Thread-safe: a single user is the
norm, but the code must not corrupt state if requests race.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

MAX_MESSAGES = 16
MAX_MESSAGE_CHARS = 4_000
DEFAULT_SESSION = "default"


@dataclass(frozen=True)
class Message:
    """One stored conversation turn."""

    role: str
    content: str


def clip_content(text: str) -> str:
    """Bounds a message's stored length to keep the token budget finite."""
    text = text or ""
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - 3] + "..."


class ConversationStore:
    """Thread-safe, bounded conversation history per session id."""

    def __init__(self, max_messages: int = MAX_MESSAGES):
        self._max_messages = max_messages
        self._lock = threading.Lock()
        self._sessions: dict = {}

    @staticmethod
    def normalize(session_id: str | None) -> str:
        """Falls back to the default session when no id is given."""
        return (session_id or "").strip() or DEFAULT_SESSION

    def history(self, session_id: str | None) -> list:
        """Returns a copy of the stored turns, oldest first."""
        key = self.normalize(session_id)
        with self._lock:
            ring = self._sessions.get(key)
            return list(ring) if ring else []

    def append(self, session_id: str | None, role: str, content: str) -> None:
        """Stores one turn, clipped and evicting the oldest when full."""
        key = self.normalize(session_id)
        with self._lock:
            ring = self._sessions.setdefault(key, deque(maxlen=self._max_messages))
            ring.append(Message(role=role, content=clip_content(content)))

    def reset(self, session_id: str | None) -> None:
        """Drops the whole conversation for one session."""
        key = self.normalize(session_id)
        with self._lock:
            self._sessions.pop(key, None)
