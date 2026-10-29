"""Web Push backend core (S5): VAPID, subscriptions, encrypted sends.

A self-contained package — no third-party crypto — exposing one facade,
:class:`~herdr_brain.webpush.service.WebPushService`. Route wiring, the
service worker and the CLI live outside this package by design.
"""

from __future__ import annotations

from .errors import StoreError
from .service import WebPushService

__all__ = ["WebPushService", "StoreError"]
