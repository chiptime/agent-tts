"""The encrypted Web Push sender: RFC 8291 payload + RFC 8292 auth.

One method — :meth:`PushSender.send` — turns a payload dict into a
lawful Web Push request and returns the push service's status code (0
for transport failure). The HTTP transport is injectable so tests never
touch the network; the default uses stdlib ``urllib`` with a short
timeout.

Measured send options per doc 08: ``TTL: 21600`` (6 h), ``Urgency:
high`` (without it Android defers through Doze) and a ``Topic`` the
service collapses on — restricted by :func:`topic_sendable` because the
topic rides through push services that decode it as base64 and reject
lengths ≡ 1 (mod 4) with a 400.

Every string in the payload passes the host's secret mask BEFORE
encryption (S14): the cipher must never become a way to smuggle a
credential that wandered onto a screen past the masking pass.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Tuple

from ..secrets_mask import mask_text
from . import crypto
from .store import Subscription, push_service_origin
from .vapid import VapidKeys, build_vapid_jwt

logger = logging.getLogger(__name__)

#: Default time-to-live of a notification (6 h, the measured value).
DEFAULT_TTL = 21600
DEFAULT_URGENCY = "high"

#: Push services cap the whole request body; header is 87 bytes + tag.
MAX_BODY_BYTES = 4096
MAX_PLAINTEXT = 3800

#: aes128gcm record size (RFC 8188); fits any single notification.
RECORD_SIZE = 4096


def topic_sendable(topic: str) -> bool:
    """A collapse key push services accept: base64url charset, at most
    32 chars, and never a length ≡ 1 (mod 4) — that length is invalid
    base64 and some services answer 400 without delivering."""
    if not topic or len(topic) > 32:
        return False
    if len(topic) % 4 == 1:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    return set(topic) <= allowed


def _mask_value(value):
    """Masks every string in a JSON-shaped payload, keys untouched."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    return value


def _clip(payload: dict) -> dict:
    """Shrinks long text fields (after masking) so the request stays
    under the body cap; clipped fields end with an ellipsis."""
    field_cap = 1200

    def clip_str(text: str) -> str:
        return text if len(text) <= field_cap else text[: field_cap - 1] + "…"

    out = {
        k: (
            {ik: (clip_str(iv) if isinstance(iv, str) else iv) for ik, iv in v.items()}
            if isinstance(v, dict)
            else clip_str(v) if isinstance(v, str) else v
        )
        for k, v in payload.items()
    }
    while len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > MAX_PLAINTEXT:
        longest = max(
            (k for k, v in out.items() if isinstance(v, str) and len(v) > 8),
            key=lambda k: len(out[k]),
            default=None,
        )
        if longest is None:
            break
        cut = max(8, len(out[longest]) // 2)
        out[longest] = out[longest][: cut - 1] + "…"
    return out


def encrypt_payload(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    delivery_private: int,
    salt: bytes,
) -> bytes:
    """RFC 8291 ``aes128gcm`` body: header + one sealed record."""
    delivery_public = crypto.public_from_private(delivery_private)
    ikm = crypto.ecdh_p256(delivery_private, ua_public) + auth_secret
    info = b"WebPush: info\x00" + ua_public + delivery_public
    prk = crypto.hkdf_sha256(ikm, salt, info, 32)
    cek = crypto.hkdf_sha256(prk, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = crypto.hkdf_sha256(prk, salt, b"Content-Encoding: nonce\x00", 12)
    # RFC 8188 content: data || 0x02 delim || zero padding.
    content = plaintext + b"\x02"
    sealed = crypto.aes128gcm_encrypt(cek, nonce, content)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(delivery_public)])
    body = header + delivery_public + sealed
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("encrypted payload exceeds the push body cap")
    return body


def default_transport(request: dict):
    """The real HTTP hop; errors become status 0 at the caller."""
    req = urllib.request.Request(
        request["url"], data=request["body"], headers=request["headers"], method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return type("PushResponse", (), {"status": int(response.status)})()
    except urllib.error.HTTPError as exc:
        return type("PushResponse", (), {"status": int(exc.code)})()


class PushSender:
    """Builds and sends one encrypted notification per subscription."""

    def __init__(
        self,
        keys: VapidKeys,
        transport: Optional[Callable[[dict], object]] = None,
        clock: Callable[[], float] = time.time,
        delivery_key: Optional[Tuple[int, bytes]] = None,
    ):
        self._keys = keys
        self._transport = transport or default_transport
        self._clock = clock
        # The RFC 8291 sending key pair: fresh per process unless pinned
        # (tests pin it to decrypt what would otherwise be opaque bytes).
        if delivery_key is not None:
            self._delivery_private = delivery_key[0]
        else:
            self._delivery_private, _public = crypto.generate_keypair()

    def send(
        self,
        subscription: Subscription,
        payload: dict,
        topic: str,
        ttl: int = DEFAULT_TTL,
        urgency: str = DEFAULT_URGENCY,
        now: Optional[float] = None,
    ) -> int:
        """Returns the push service status (0 = transport failure)."""
        if not topic_sendable(topic):
            raise ValueError(f"topic {topic!r} is not sendable")
        masked = _clip(_mask_value(payload))
        plaintext = json.dumps(masked, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        body = encrypt_payload(
            plaintext,
            crypto.b64url_decode(subscription.p256dh),
            crypto.b64url_decode(subscription.auth),
            self._delivery_private,
            os.urandom(16),
        )
        origin = push_service_origin(subscription.endpoint)
        token = build_vapid_jwt(self._keys, origin, self._clock() if now is None else now)
        headers = {
            "TTL": str(ttl),
            "Urgency": urgency,
            "Topic": topic,
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "Authorization": f"vapid t={token}, k={self._keys.public_b64url}",
        }
        request = {
            "method": "POST",
            "url": subscription.endpoint,
            "headers": headers,
            "body": body,
        }
        try:
            response = self._transport(request)
            return int(getattr(response, "status", 0))
        except Exception as exc:  # noqa: BLE001 — network is best-effort
            logger.warning(
                "web push to %s failed without a status: %s", origin, exc
            )
            return 0
