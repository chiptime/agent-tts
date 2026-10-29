"""Brain messages: short messages between agent sessions, and from the
operator, delivered by typing into the receiving agent's pane.

The package behind ``herdr-brain msg`` (spec S13, brain's own design):

- :mod:`service` — the state machine: send/reply hold a message while
  the receiver is busy or drafting, type one header line when idle,
  give up to the inbox after 60 minutes; anti-loop limits and audit.
- :mod:`store` — the private, atomic message ledger.
- :mod:`identity` — addresses (``<pane>#<session-token>``) and sender
  resolution from the pane environment herdr injects.
- :mod:`delivery` — the typed header lines and the hold gate
  (status + draft heuristic over the visible screen).
- :mod:`cli` — the ``herdr-brain msg`` verbs and exit contract.

Tests inject a fake mux and clock; nothing here talks to a live pane.
"""

from __future__ import annotations

from .errors import MessageNotFound, StoreError, WalkyRefusal
from .identity import AgentSender, WhoRow
from .service import (
    HOLD_MAX_S,
    MAX_BODY_CHARS,
    MAX_HOPS,
    PAIR_MAX,
    SENDER_MAX,
    WINDOW_S,
    WalkyService,
)
from .store import Message

__all__ = [
    "AgentSender",
    "HOLD_MAX_S",
    "MAX_BODY_CHARS",
    "MAX_HOPS",
    "Message",
    "MessageNotFound",
    "PAIR_MAX",
    "SENDER_MAX",
    "StoreError",
    "WalkyRefusal",
    "WalkyService",
    "WhoRow",
    "WINDOW_S",
    "cli",
]
