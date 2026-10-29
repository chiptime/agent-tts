"""Web Push crypto primitives against published vectors (S5).

``webpush/crypto.py`` adapts the ``cryptography`` library to the wire
shapes Web Push uses: P-256 (ECDH + deterministic ECDSA), HKDF-SHA256 and
AES-128-GCM. These tests pin each primitive to a published external vector
— NIST GCM, RFC 5869, RFC 6979 appendix A.2.5 and the widely-reproduced
NIST P-256 test point — so the adapter (key encodings, raw ``r || s``
signatures, tag handling) is checked against independent values.
"""

from __future__ import annotations

import os
import secrets as pysecrets

import pytest

from herdr_brain.webpush import crypto


def _hex(h: str) -> bytes:
    return bytes.fromhex(h)


# ── AES-128-GCM: NIST GCM spec test cases 1 and 2 ────────────────────────────


def test_gcm_encrypts_empty_plaintext_nist_tc1():
    ct = crypto.aes128gcm_encrypt(_hex("00" * 16), _hex("00" * 12), b"")
    assert ct == _hex("58e2fccefa7e3061367f1d57a4e7455a")


def test_gcm_encrypts_one_block_nist_tc2():
    ct = crypto.aes128gcm_encrypt(
        _hex("00" * 16), _hex("00" * 12), _hex("00" * 16)
    )
    assert ct == _hex("0388dace60b6a392f328c2b971b2fe78" "ab6e47d42cec13bdf53a67b21257bddf")


def test_gcm_round_trip_and_tamper_detection():
    key = os.urandom(16)
    iv = os.urandom(12)
    sealed = crypto.aes128gcm_encrypt(key, iv, "pregunta bloqueante".encode(), aad=b"hdr")
    assert crypto.aes128gcm_decrypt(key, iv, sealed, aad=b"hdr") == "pregunta bloqueante".encode()
    bad = bytearray(sealed)
    bad[0] ^= 1
    with pytest.raises(crypto.DecryptionError):
        crypto.aes128gcm_decrypt(key, iv, bytes(bad), aad=b"hdr")
    with pytest.raises(crypto.DecryptionError):
        crypto.aes128gcm_decrypt(key, iv, sealed, aad=b"other")


# ── HKDF-SHA256: RFC 5869 test case 1 ────────────────────────────────────────


def test_hkdf_rfc5869_case_1():
    okm = crypto.hkdf_sha256(
        ikm=_hex("0b" * 22),
        salt=_hex("000102030405060708090a0b0c"),
        info=_hex("f0f1f2f3f4f5f6f7f8f9"),
        length=42,
    )
    assert okm == _hex(
        "3cb25f25faacd57a90434f64d0362f2a"
        "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
        "34007208d5b887185865"
    )


# ── P-256: public derivation, ECDH symmetry, RFC 6979 A.2.5 ─────────────────

#: NIST P-256 test point: private x below, public (x, y) — the pair
#: published with RFC 6979 appendix A.2.5 and reproduced in NIST CMVP
#: documentation. External truth for the whole point-math stack.
RFC6979_PRIV = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
RFC6979_PUB_X = 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
RFC6979_PUB_Y = 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299


def _uncompressed(x: int, y: int) -> bytes:
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def test_public_key_derivation_matches_known_point():
    pub = crypto.public_from_private(RFC6979_PRIV)
    assert pub == _uncompressed(RFC6979_PUB_X, RFC6979_PUB_Y)


def test_ecdh_is_symmetric_and_thirty_two_bytes():
    a_priv = RFC6979_PRIV
    b_priv = pysecrets.randbelow(crypto.P256_N - 1) + 1
    a_pub = crypto.public_from_private(a_priv)
    b_pub = crypto.public_from_private(b_priv)
    shared_ab = crypto.ecdh_p256(a_priv, b_pub)
    shared_ba = crypto.ecdh_p256(b_priv, a_pub)
    assert shared_ab == shared_ba
    assert len(shared_ab) == 32


def test_ecdsa_sign_is_rfc6979_deterministic_sample_vector():
    sig = crypto.ecdsa_sign_p256_sha256(RFC6979_PRIV, b"sample")
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    assert r == 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
    assert s == 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8


def test_ecdsa_verify_accepts_and_rejects():
    pub = crypto.public_from_private(RFC6979_PRIV)
    sig = crypto.ecdsa_sign_p256_sha256(RFC6979_PRIV, b"sample")
    assert crypto.ecdsa_verify(pub, b"sample", sig)
    assert not crypto.ecdsa_verify(pub, b"other", sig)
    flipped = bytearray(sig)
    flipped[10] ^= 0xFF
    assert not crypto.ecdsa_verify(pub, b"sample", bytes(flipped))


def test_generated_keypairs_sign_and_verify():
    priv, pub = crypto.generate_keypair()
    sig = crypto.ecdsa_sign_p256_sha256(priv, b"payload")
    assert crypto.ecdsa_verify(pub, b"payload", sig)


# ── base64url helpers ────────────────────────────────────────────────────────


def test_b64url_round_trip_and_padding_tolerance():
    raw = os.urandom(65)
    enc = crypto.b64url_encode(raw)
    assert "=" not in enc and "+" not in enc and "/" not in enc
    assert crypto.b64url_decode(enc) == raw
    assert crypto.b64url_decode(enc + "==") == raw  # dotenv files pad
