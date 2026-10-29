"""The exact crypto primitives Web Push needs, over the stdlib only.

The brain venv carries no crypto library (no ``cryptography``, no
``pywebpush``), and the package may not grow dependencies on its own.
Web Push needs exactly four primitives — P-256 point math (ECDH +
deterministic ECDSA per RFC 6979), HKDF-SHA256 (RFC 5869) and
AES-128-GCM (NIST SP 800-38D) — all small, all pinned to published
vectors by ``tests/test_webpush_crypto.py`` so correctness never rests
on this file verifying itself.

Throughput is a non-issue here: a push notification encrypts a few
hundred bytes and signs one JWT per send, milliseconds of pure Python.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Tuple


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


# ── NIST P-256 (SEC 2 curve secp256r1) ───────────────────────────────────────

#: Curve short-domain parameters.
P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
P256_A = P256_P - 3
P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
P256_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
P256_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5

#: The neutral element in Jacobian coordinates (Z = 0).
_INF = (1, 1, 0)


def _inv(value: int, modulus: int) -> int:
    return pow(value, -1, modulus)


def _jacobian_double(pt: Tuple[int, int, int]) -> Tuple[int, int, int]:
    x1, y1, z1 = pt
    if z1 == 0 or y1 == 0:
        return _INF
    # dbl-2001-b (standard Jacobian doubling formulas)
    ysq = (y1 * y1) % P256_P
    s = (4 * x1 * ysq) % P256_P
    m = (3 * x1 * x1 + P256_A * pow(z1, 4, P256_P)) % P256_P
    x3 = (m * m - 2 * s) % P256_P
    y3 = (m * (s - x3) - 8 * ysq * ysq) % P256_P
    z3 = (2 * y1 * z1) % P256_P
    return (x3, y3, z3)


def _jacobian_add(
    p1: Tuple[int, int, int], p2: Tuple[int, int, int]
) -> Tuple[int, int, int]:
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    if z1 == 0:
        return p2
    if z2 == 0:
        return p1
    z1sq = (z1 * z1) % P256_P
    z2sq = (z2 * z2) % P256_P
    u1 = (x1 * z2sq) % P256_P
    u2 = (x2 * z1sq) % P256_P
    s1 = (y1 * z2sq * z2) % P256_P
    s2 = (y2 * z1sq * z1) % P256_P
    if u1 == u2:
        if s1 != s2:
            return _INF
        return _jacobian_double(p1)
    h = (u2 - u1) % P256_P
    r = (s2 - s1) % P256_P
    hsq = (h * h) % P256_P
    hcu = (hsq * h) % P256_P
    u1hsq = (u1 * hsq) % P256_P
    x3 = (r * r - hcu - 2 * u1hsq) % P256_P
    y3 = (r * (u1hsq - x3) - s1 * hcu) % P256_P
    z3 = (h * z1 * z2) % P256_P
    return (x3, y3, z3)


def _to_affine(pt: Tuple[int, int, int]) -> Tuple[int, int]:
    x, y, z = pt
    if z == 0:
        raise ValueError("point at infinity has no affine form")
    zinv = _inv(z, P256_P)
    zinv2 = (zinv * zinv) % P256_P
    return ((x * zinv2) % P256_P, (y * zinv2 * zinv) % P256_P)


def _scalar_mult(
    scalar: int, point: Tuple[int, int, int]
) -> Tuple[int, int, int]:
    result = _INF
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _jacobian_add(result, addend)
        addend = _jacobian_double(addend)
        scalar >>= 1
    return result


def _is_on_curve(x: int, y: int) -> bool:
    return (y * y - (x * x * x + P256_A * x + P256_B)) % P256_P == 0


def _decode_public_point(raw: bytes) -> Tuple[int, int]:
    """Parses a 65-byte uncompressed point, rejecting off-curve input."""
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("public key must be a 65-byte uncompressed point")
    x = int.from_bytes(raw[1:33], "big")
    y = int.from_bytes(raw[33:65], "big")
    if not _is_on_curve(x, y):
        raise ValueError("public key is not a point on the P-256 curve")
    return x, y


def public_from_private(private: int) -> bytes:
    """Derives the 65-byte uncompressed public key of ``private``."""
    if not 1 <= private < P256_N:
        raise ValueError("private scalar out of range")
    affine = _to_affine(_scalar_mult(private, (P256_GX, P256_GY, 1)))
    return b"\x04" + affine[0].to_bytes(32, "big") + affine[1].to_bytes(32, "big")


def generate_keypair() -> Tuple[int, bytes]:
    """A fresh P-256 keypair: ``(private scalar, 65-byte public point)``."""
    private = secrets.randbelow(P256_N - 1) + 1
    return private, public_from_private(private)


def ecdh_p256(private: int, peer_public: bytes) -> bytes:
    """The 32-byte x-coordinate of the ECDH shared point."""
    x, y = _decode_public_point(peer_public)
    shared = _to_affine(_scalar_mult(private, (x, y, 1)))
    return shared[0].to_bytes(32, "big")


# ── ECDSA over P-256 with SHA-256, deterministic per RFC 6979 ────────────────


def _bits2int(data: bytes) -> int:
    value = int.from_bytes(data, "big")
    excess = len(data) * 8 - 256
    return value >> excess if excess > 0 else value


def _rfc6979_k(private: int, h1: bytes) -> int:
    x = private.to_bytes(32, "big")
    h_bin = _bits2int(h1) % P256_N
    h_val = h_bin.to_bytes(32, "big")
    v = b"\x01" * 32
    k = b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + h_val, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h_val, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        t = b""
        while len(t) < 32:
            v = hmac.new(k, v, hashlib.sha256).digest()
            t += v
        candidate = _bits2int(t[:32])
        if 1 <= candidate < P256_N:
            return candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def ecdsa_sign_p256_sha256(private: int, data: bytes) -> bytes:
    """Signs ``data``; returns the 64-byte raw ``r || s`` form."""
    if not 1 <= private < P256_N:
        raise ValueError("private scalar out of range")
    h1 = hashlib.sha256(data).digest()
    e = _bits2int(h1) % P256_N
    while True:
        k = _rfc6979_k(private, h1)
        rx, _ry = _to_affine(_scalar_mult(k, (P256_GX, P256_GY, 1)))
        r = rx % P256_N
        if r == 0:
            raise RuntimeError("degenerate nonce")  # unreachable in practice
        s = (_inv(k, P256_N) * (e + r * private)) % P256_N
        if s != 0:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def ecdsa_verify(public: bytes, data: bytes, signature: bytes) -> bool:
    """Verifies a raw ``r || s`` signature against ``data``."""
    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < P256_N and 1 <= s < P256_N):
        return False
    qx, qy = _decode_public_point(public)
    e = _bits2int(hashlib.sha256(data).digest()) % P256_N
    w = _inv(s, P256_N)
    u1 = (e * w) % P256_N
    u2 = (r * w) % P256_N
    point = _jacobian_add(
        _scalar_mult(u1, (P256_GX, P256_GY, 1)), _scalar_mult(u2, (qx, qy, 1))
    )
    if point[2] == 0:
        return False
    return _to_affine(point)[0] % P256_N == r


# ── HKDF-SHA256 (RFC 5869) ───────────────────────────────────────────────────


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt or b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(
            prk, block + info + bytes([counter]), hashlib.sha256
        ).digest()
        okm += block
        counter += 1
    return okm[:length]


# ── AES-128-GCM (NIST SP 800-38D) ────────────────────────────────────────────


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _build_sbox() -> list:
    """The AES S-box, computed (inverse in GF(2^8) plus affine map)."""
    exp = [0] * 255
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value ^= _xtime(value)  # multiply by the generator 0x03
    sbox = [0] * 256
    for i in range(256):
        inv = 0 if i == 0 else exp[(255 - log[i]) % 255]
        res = 0
        for bit in range(5):
            rotated = ((inv << bit) | (inv >> (8 - bit))) & 0xFF if bit else inv
            res ^= rotated
        sbox[i] = res ^ 0x63
    return sbox


_SBOX = _build_sbox()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key: bytes) -> list:
    """The 11 round keys; ``round_keys[rnd][c]`` is word ``4*rnd + c``."""
    words = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 4 - 1]
        words.append([words[i - 4][j] ^ temp[j] for j in range(4)])
    return [[words[4 * rnd + c] for c in range(4)] for rnd in range(11)]


def _encrypt_block(state: list, round_keys: list) -> bytes:
    def add_round_key(s, rnd):
        for c in range(4):
            for r in range(4):
                s[c][r] ^= round_keys[rnd][c][r]

    def sub_bytes(s):
        for c in range(4):
            for r in range(4):
                s[c][r] = _SBOX[s[c][r]]

    def shift_rows(s):
        # Row r lives across columns; rotate it left by r positions.
        for r in range(1, 4):
            row = [s[c][r] for c in range(4)]
            row = row[r:] + row[:r]
            for c in range(4):
                s[c][r] = row[c]

    def mix_columns(s):
        for c in range(4):
            a = s[c]
            t = a[0] ^ a[1] ^ a[2] ^ a[3]
            u = a[0]
            s[c][0] ^= t ^ _xtime(a[0] ^ a[1])
            s[c][1] ^= t ^ _xtime(a[1] ^ a[2])
            s[c][2] ^= t ^ _xtime(a[2] ^ a[3])
            s[c][3] ^= t ^ _xtime(a[3] ^ u)

    state = [list(col) for col in state]
    add_round_key(state, 0)
    for rnd in range(1, 10):
        sub_bytes(state)
        shift_rows(state)
        mix_columns(state)
        add_round_key(state, rnd)
    sub_bytes(state)
    shift_rows(state)
    add_round_key(state, 10)
    return bytes(state[c][r] for c in range(4) for r in range(4))


def _aes128_block(key: bytes, block: bytes) -> bytes:
    if len(block) != 16:
        raise ValueError("AES block must be 16 bytes")
    state = [list(block[4 * i : 4 * i + 4]) for i in range(4)]
    return _encrypt_block(state, _expand_key(key))


def _gf128_mul(x: int, y: int) -> int:
    """Multiplication in GF(2^128) with the GCM polynomial."""
    r = 0xE1 << 120
    z = 0
    v = x
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            z ^= v
        v = (v >> 1) ^ r if v & 1 else v >> 1
    return z


def _ghash(h: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    h_int = int.from_bytes(h, "big")
    y = 0

    def feed(data: bytes):
        nonlocal y
        for i in range(0, len(data), 16):
            block = data[i : i + 16].ljust(16, b"\x00")
            y = _gf128_mul(y ^ int.from_bytes(block, "big"), h_int)

    feed(aad)
    feed(ciphertext)
    lengths = ((len(aad) * 8) << 64) | (len(ciphertext) * 8)
    y = _gf128_mul(y ^ lengths, h_int)
    return y.to_bytes(16, "big")


def _increment_counter(j0: bytes) -> bytes:
    counter = int.from_bytes(j0, "big") + 1
    return counter.to_bytes(16, "big")


def aes128gcm_encrypt(
    key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b""
) -> bytes:
    """Returns ``ciphertext || tag`` (the GCM "sealed" form)."""
    if len(key) != 16:
        raise ValueError("AES-128 key must be 16 bytes")
    if len(iv) != 12:
        raise ValueError("GCM IV must be 12 bytes")
    h = _aes128_block(key, b"\x00" * 16)
    j0 = iv + b"\x00\x00\x00\x01"
    ciphertext = bytearray()
    counter = j0
    for i in range(0, len(plaintext), 16):
        counter = _increment_counter(counter)
        stream = _aes128_block(key, counter)
        chunk = plaintext[i : i + 16]
        ciphertext += bytes(
            c ^ s for c, s in zip(chunk, stream)
        )
    tag_mask = _aes128_block(key, j0)
    ghash = _ghash(h, aad, bytes(ciphertext))
    tag = bytes(m ^ g for m, g in zip(tag_mask, ghash))
    return bytes(ciphertext) + tag


def aes128gcm_decrypt(
    key: bytes, iv: bytes, sealed: bytes, aad: bytes = b""
) -> bytes:
    """Opens ``ciphertext || tag``; raises on any authentication failure."""
    if len(sealed) < 16:
        raise DecryptionError("sealed data shorter than a tag")
    ciphertext, tag = sealed[:-16], sealed[-16:]
    h = _aes128_block(key, b"\x00" * 16)
    j0 = iv + b"\x00\x00\x00\x01"
    expected_mask = _aes128_block(key, j0)
    ghash = _ghash(h, aad, ciphertext)
    expected = bytes(m ^ g for m, g in zip(expected_mask, ghash))
    if not hmac.compare_digest(expected, tag):
        raise DecryptionError("authentication failed")
    plaintext = bytearray()
    counter = j0
    for i in range(0, len(ciphertext), 16):
        counter = _increment_counter(counter)
        stream = _aes128_block(key, counter)
        plaintext += bytes(
            c ^ s for c, s in zip(ciphertext[i : i + 16], stream)
        )
    return bytes(plaintext)


def random_salt(length: int = 16) -> bytes:
    return os.urandom(length)
