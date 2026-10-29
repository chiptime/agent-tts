"""Refusal shapes for brain messages.

One exception per audience decision, mirroring the mux port's rule that
a typed "no" beats an unexplained error: :class:`WalkyRefusal` is an
expected, user-readable refusal (rate limit, unknown pane, self-send…)
and :class:`MessageNotFound` the specific "no such id" case the CLI
reports with its own exit code.
"""

from __future__ import annotations


class WalkyRefusal(Exception):
    """An expected refusal with a message the caller can print as-is."""


class MessageNotFound(WalkyRefusal):
    """No message with that id is known (or visible to this pane)."""


class StoreError(Exception):
    """The message ledger exists but cannot be trusted (corrupt/short)."""
