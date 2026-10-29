"""The exact crypto primitives Web Push needs, over ``cryptography``.

Web Push needs four primitives: P-256 ECDH and ECDSA (RFC 8292 VAPID
signatures, deterministic per RFC 6979), HKDF-SHA256 (RFC 5869) and
AES-128-GCM (RFC 8188/8291 payload encryption). All of them come from the
audited ``cryptography`` library; this module only adapts its objects to
the small wire-level shapes the rest of the package uses (private keys as
integers, public keys as 65-byte uncompressed points, signatures as raw
``r || s``). ``tests/test_webpush_crypto.py`` pins every primitive to a
published external vector.
"""

from __future__ import annotations

import base64
import os
from typing import Tuple

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class DecryptionError(Exception):
    """AES-GCM authentication failed (wrong key, wrong AAD, tampering)."""


# ── base64url (RFC 4648 §5, unpadded — the Web Push wire form) ───────────────


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decodes unpadded base64url; tolerates the padded dotenv form."""
    cleaned = text.strip().rstrip("=")
    padding = "=" * (-len(cleaned) % 4)
    return base64.urlsafe_b64decode(cleaned + padding)


# ── NIST P-256 ───────────────────────────────────────────────────────────────

#: Group order of P-256: the valid private scalar range is [1, P256_N).
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

_CURVE = ec.SECP256R1()


def _private_key(private: int) -> ec.EllipticCurvePrivateKey:
    if not 1 <= private < P256_N:
        raise ValueError("private scalar out of range")
    return ec.derive_private_key(private, _CURVE)


def _public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
    """Parses a 65-byte uncompressed point, rejecting off-curve input."""
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("public key must be a 65-byte uncompressed point")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, raw)
    except ValueError as exc:
        raise ValueError("public key is not a point on the P-256 curve") from exc


def _encode_public(key: ec.EllipticCurvePublicKey) -> bytes:
    numbers = key.public_numbers()
    return b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


def public_from_private(private: int) -> bytes:
    """Derives the 65-byte uncompressed public key of ``private``."""
    return _encode_public(_private_key(private).public_key())


def generate_keypair() -> Tuple[int, bytes]:
    """A fresh P-256 keypair: ``(private scalar, 65-byte public point)``."""
    key = ec.generate_private_key(_CURVE)
    return key.private_numbers().private_value, _encode_public(key.public_key())


def ecdh_p256(private: int, peer_public: bytes) -> bytes:
    """The 32-byte x-coordinate of the ECDH shared point."""
    return _private_key(private).exchange(ec.ECDH(), _public_key(peer_public))


# ── ECDSA over P-256 with SHA-256, deterministic per RFC 6979 ────────────────


def ecdsa_sign_p256_sha256(private: int, data: bytes) -> bytes:
    """Signs ``data``; returns the 64-byte raw ``r || s`` form."""
    der = _private_key(private).sign(
        data, ec.ECDSA(hashes.SHA256(), deterministic_signing=True)
    )
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def ecdsa_verify(public: bytes, data: bytes, signature: bytes) -> bool:
    """Verifies a raw ``r || s`` signature against ``data``."""
    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < P256_N and 1 <= s < P256_N):
        return False
    try:
        _public_key(public).verify(
            encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256())
        )
    except InvalidSignature:
        return False
    return True


# ── HKDF-SHA256 (RFC 5869) ───────────────────────────────────────────────────


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=salt or None, info=info
    ).derive(ikm)


# ── AES-128-GCM ──────────────────────────────────────────────────────────────


def aes128gcm_encrypt(
    key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b""
) -> bytes:
    """Returns ``ciphertext || tag`` (the GCM "sealed" form)."""
    if len(key) != 16:
        raise ValueError("AES-128 key must be 16 bytes")
    if len(iv) != 12:
        raise ValueError("GCM IV must be 12 bytes")
    return AESGCM(key).encrypt(iv, plaintext, aad or None)


def aes128gcm_decrypt(
    key: bytes, iv: bytes, sealed: bytes, aad: bytes = b""
) -> bytes:
    """Opens ``ciphertext || tag``; raises on any authentication failure."""
    if len(sealed) < 16:
        raise DecryptionError("sealed data shorter than a tag")
    try:
        return AESGCM(key).decrypt(iv, sealed, aad or None)
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("authentication failed") from exc


def random_salt(length: int = 16) -> bytes:
    return os.urandom(length)
