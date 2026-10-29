"""Two-factor authentication for a crew request (doc 10 risk 2).

Both factors are REQUIRED and independent:

1. **Certificate pin** — the TLS peer certificate's DER bytes hash to
   the SHA-256 pin of an ENROLLED member. The pin is the identity: the
   certificates are long-lived self-signed ones, and the expiry date
   is explicitly not the trust boundary.
2. **Crew secret** — ``Authorization: Bearer <secret>`` matches the
   roster's current secret hash, or the previous one inside the
   rotation grace window (``hmac.compare_digest`` both times).

Every failure — either factor missing, wrong, or the roster unreadable
— produces the SAME refusal: status 401 with one fixed body, and not a
byte of difference to tell which factor failed. Callers must answer
with ``refusal_response()`` verbatim; a detail-rich 401 here would be
an oracle over the credential space.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Optional

from ..pairing import bearer_token, hashes_equal

#: The one refusal every crew auth failure returns. Identical on
#: purpose: no oracle about which factor failed (task S7).
UNAUTHORIZED_STATUS = 401
UNAUTHORIZED_BODY = {"detail": "unauthorized"}


def cert_sha256_pin(peer_cert_der: Optional[bytes]) -> Optional[str]:
    """The SHA-256 pin of a peer certificate's DER bytes, or None when
    there is nothing to pin (no client certificate was presented)."""
    if not isinstance(peer_cert_der, (bytes, bytearray)) or not peer_cert_der:
        return None
    return hashlib.sha256(bytes(peer_cert_der)).hexdigest()


def refusal_response() -> tuple[int, dict]:
    """The uniform crew refusal, ready to return up the stack."""
    return UNAUTHORIZED_STATUS, UNAUTHORIZED_BODY


def member_for_pin(pin: Optional[str], roster) -> Optional[str]:
    """The enrolled member whose pinned certificate hash equals ``pin``,
    or None. Every enrolled pin is compared in constant time; the pin
    itself is public material (a fingerprint), the MATCH is the trust."""
    if pin is None or roster is None:
        return None
    peers = getattr(roster, "peers", None)
    if not isinstance(peers, dict):
        return None
    member: Optional[str] = None
    for name, entry in peers.items():
        pinned = entry.get("cert_sha256_pin", "") if isinstance(entry, dict) else ""
        if isinstance(pinned, str) and hashes_equal(pinned, pin):
            member = name
    return member


def check_crew_auth(
    headers_get: Callable[[str], Optional[str]],
    peer_cert_der: Optional[bytes],
    roster,
    now: Optional[float] = None,
) -> Optional[str]:
    """Both factors, or nobody.

    Returns the authenticated member's name, or None. Both factors are
    evaluated unconditionally and combined at the end — the control
    flow never short-circuits on a factor, so timing does not disclose
    which one failed either.
    """
    # factor 1: the certificate pin must identify an enrolled member
    pin = cert_sha256_pin(peer_cert_der)
    member = member_for_pin(pin, roster)
    # factor 2: the bearer secret must match current or grace-window
    token = bearer_token(headers_get)
    secret_ok = False
    if token and roster is not None:
        try:
            secret_ok = roster.secret_matches(token, now=now)
        except Exception:
            secret_ok = False  # a roster that cannot answer authorizes nobody
    if member is not None and secret_ok:
        return member
    return None
