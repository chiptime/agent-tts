"""In-process conversation memory for the brain.

One ring buffer of recent messages per client session id. The system message
is rebuilt per request (with live context) and never stored here; only real
user/assistant turns live in the ring. Thread-safe: a single user is the
norm, but the code must not corrupt state if requests race.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import dataclass

MAX_MESSAGES = 16
MAX_MESSAGE_CHARS = 4_000
DEFAULT_SESSION = "default"


@dataclass(frozen=True)
class Message:
    """One stored conversation turn.

    ``seq`` is the message's store-wide identity: unique per append,
    never reused (not by eviction, reset or re-seed), so a revoke
    receipt can only ever name the exact messages one turn wrote.
    """

    role: str
    content: str
    seq: int = 0


def clip_content(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Bounds a text's length to the given budget (default: LLM prompt)."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class StagedTurn:
    """One turn's ring writes, reserved but NOT landed (S7/L34).

    A turn that can still be rejected stages its messages here:
    identities are reserved the moment each message is staged, but the
    shared ring is touched only by :meth:`commit` — one lock-held,
    atomic landing, so bounded eviction happens there and only there.
    :meth:`abort` discards the staging and the ring stays EXACTLY as it
    was: a rejected turn can never evict an earlier message, not even
    at full capacity. Reserved identities are simply never used after
    an abort — they are never reused (the counter is store-wide), so an
    aborted receipt can never alias a later message. Not thread-safe by
    itself: one staging belongs to one turn, exactly like the turn's
    other local state.
    """

    def __init__(self, store: "ConversationStore", session_key: str):
        self._store = store
        self._key = session_key
        self._messages: list[Message] = []
        self._settled = False

    @property
    def session_id(self) -> str:
        """The normalized session this staging will land in."""
        return self._key

    @property
    def seqs(self) -> tuple[int, ...]:
        """The reserved identities, in staging order."""
        return tuple(message.seq for message in self._messages)

    def append(self, role: str, content: str) -> Message:
        """Reserves one message's identity; lands nothing shared."""
        if self._settled:
            raise RuntimeError("staged turn already settled")
        message = Message(
            role=role, content=content or "", seq=self._store._reserve_seq()
        )
        self._messages.append(message)
        return message

    def commit(self) -> list[Message]:
        """Atomically lands every staged message in the session ring.

        Bounded eviction (deque maxlen) happens HERE, once, under the
        store lock — a full ring evicts its oldest messages only for a
        turn that is actually being accepted. Returns the committed
        messages; committing an already-settled staging raises.
        """
        if self._settled:
            raise RuntimeError("staged turn already settled")
        self._settled = True
        return self._store._land(self._key, self._messages)

    def abort(self) -> None:
        """Discards the staging; the shared ring keeps every message it
        had — nothing this turn staged was ever visible."""
        self._settled = True


class ConversationStore:
    """Thread-safe, bounded conversation history per session id."""

    def __init__(self, max_messages: int = MAX_MESSAGES):
        self._max_messages = max_messages
        self._lock = threading.Lock()
        self._sessions: dict = {}
        # One counter for the WHOLE store: identities stay unique across
        # sessions and across resets, so a receipt survives a concurrent
        # reset/re-seed without ever pointing at a newer message.
        self._seq = itertools.count(1)

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

    def stage_turn(self, session_id: str | None) -> StagedTurn:
        """Opens a two-phase ring commit for ONE (rejectable) turn.

        The staging reserves identities as messages are added and lands
        them all atomically at :meth:`StagedTurn.commit` — see
        :class:`StagedTurn`. Plain :meth:`append` stays the classic
        immediate path for callers whose turns cannot be rejected.
        """
        return StagedTurn(self, self.normalize(session_id))

    def _reserve_seq(self) -> int:
        # itertools.count.__next__ is atomic; identities never repeat.
        return next(self._seq)

    def _land(self, key: str, messages: list[Message]) -> list[Message]:
        with self._lock:
            ring = self._sessions.setdefault(key, deque(maxlen=self._max_messages))
            for message in messages:
                ring.append(message)
            return list(messages)

    def append(self, session_id: str | None, role: str, content: str) -> Message:
        """Stores one turn in full, evicting the oldest when the ring is
        full. Returns the stored :class:`Message` — its ``seq`` is the
        identity a later :meth:`revoke` names."""
        key = self.normalize(session_id)
        with self._lock:
            ring = self._sessions.setdefault(key, deque(maxlen=self._max_messages))
            message = Message(role=role, content=content or "", seq=next(self._seq))
            ring.append(message)
            return message

    def revoke(self, session_id: str | None, seqs) -> int:
        """Removes EXACTLY the still-present messages whose ``seq`` is
        named — the identity-safe unwind of one cancelled turn's ring
        commit (S7). Never a text match (identical questions stay
        theirs), never another turn's messages (identities are unique
        per append and per session scoping), and unknown or
        already-evicted identities are quietly ignored. Returns how
        many messages were actually removed."""
        drop = {int(seq) for seq in seqs}
        if not drop:
            return 0
        key = self.normalize(session_id)
        with self._lock:
            ring = self._sessions.get(key)
            if not ring:
                return 0
            kept = deque(
                (m for m in ring if m.seq not in drop), maxlen=self._max_messages
            )
            removed = len(ring) - len(kept)
            if removed:
                self._sessions[key] = kept
            return removed

    def reset(self, session_id: str | None) -> None:
        """Drops the whole conversation for one session."""
        key = self.normalize(session_id)
        with self._lock:
            self._sessions.pop(key, None)
