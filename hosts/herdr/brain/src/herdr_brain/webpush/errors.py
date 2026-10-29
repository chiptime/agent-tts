"""Shared exceptions for the webpush package."""

from __future__ import annotations


class StoreError(Exception):
    """The subscription store is unusable — corrupt, unreadable, or a
    write refused. Raised loudly instead of any silent reset: the file
    holds per-device delivery keys and pretending it was empty would
    kill every phone's push with no trace (risk item 1)."""
