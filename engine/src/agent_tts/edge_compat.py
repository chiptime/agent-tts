"""Compatibility shim for edge-tts 7.2.8 against Microsoft's Oct 2026 change.

Since October 2026 ``speech.platform.bing.com`` answers the synthesis
WebSocket with 403. Microsoft Edge now synthesizes through
``api.msedgeservices.com``, which accepts the legacy token as
``Ocp-Apim-Subscription-Key`` (upstream fix: rany2/edge-tts PR #491).
It also makes the server-``Date`` parser locale independent.

Lives outside ``providers/`` on purpose (that tree is guarded against
edits). Idempotent and fail-open: any problem leaves edge-tts untouched.
Remove once the pinned edge-tts includes the upstream fix.
"""

from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Optional

_NEW_WSS = "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"


def _parse_rfc2616_date(date: str) -> Optional[float]:
    try:
        parsed = parsedate_to_datetime(date)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def apply() -> bool:
    """Patches edge_tts in-process; returns True when applied."""
    try:
        from edge_tts import communicate, constants, drm
    except Exception:
        return False
    if "msedgeservices" in getattr(constants, "WSS_URL", ""):
        return False  # upstream already fixed
    url = f"{_NEW_WSS}?Ocp-Apim-Subscription-Key={constants.TRUSTED_CLIENT_TOKEN}"
    constants.WSS_URL = url
    communicate.WSS_URL = url
    drm.DRM.parse_rfc2616_date = staticmethod(_parse_rfc2616_date)
    return True
