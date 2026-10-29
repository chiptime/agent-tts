"""Encrypted Web Push sender (RFC 8291/8292) with an injectable transport.

Every test decrypts what the sender produced, using a fixed receiver
keypair — the round trip is the proof, the transport spy is the
contract. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os

import pytest

from herdr_brain.webpush import crypto, sender as sender_mod
from herdr_brain.webpush.sender import PushSender, topic_sendable
from herdr_brain.webpush.store import Subscription
from herdr_brain.webpush.vapid import generate_keys


class FakeTransport:
    def __init__(self, status=201):
        self.requests = []
        self.status = status

    def __call__(self, request):
        self.requests.append(request)
        return type("Resp", (), {"status": self.status})()


def _subscription(endpoint="https://push.example/sub/1"):
    return Subscription(
        endpoint=endpoint,
        p256dh=crypto.b64url_encode(crypto.public_from_private(0x1234567)),
        auth=crypto.b64url_encode(os.urandom(16)),
        created_at=0.0,
    )


def _sender(transport):
    return PushSender(keys=generate_keys(subject="mailto:ops@example.net"), transport=transport)


def _decrypt_body(body: bytes, receiver_private: int, auth_secret: bytes) -> bytes:
    """Standalone RFC 8291 receiver, as a browser push service would do."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    key_id = body[21 : 21 + idlen]
    sealed = body[21 + idlen :]
    assert rs == 4096
    server_public = key_id
    ikm = crypto.ecdh_p256(receiver_private, server_public) + auth_secret
    info = b"WebPush: info\x00" + crypto.public_from_private(receiver_private) + server_public
    prk = crypto.hkdf_sha256(ikm, salt, info, 32)
    cek = crypto.hkdf_sha256(prk, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = crypto.hkdf_sha256(prk, salt, b"Content-Encoding: nonce\x00", 12)
    content = crypto.aes128gcm_decrypt(cek, nonce, sealed)
    delim = content.rfind(b"\x02")
    assert delim >= 0
    assert set(content[delim + 1 :]) <= {0}
    return content[:delim]


# ── Topic: Apple-safe collapse keys ──────────────────────────────────────────


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("", False),
        ("a", False),  # len 1 ≡ 1 mod 4
        ("abcde", False),  # len 5 ≡ 1 mod 4
        ("brain-herd", True),  # len 10
        ("brain-cache", True),  # len 11
        ("A" * 32, True),
        ("A" * 33, False),
        ("bad topic!", False),  # space is outside base64url
        ("bad+topic", False),
        ("bad/topic", False),
        ("bad=topic", False),
    ],
)
def test_topic_sendable(topic, expected):
    assert topic_sendable(topic) is expected


def test_sender_rejects_unsendable_topic():
    transport = FakeTransport()
    sender = _sender(transport)
    with pytest.raises(ValueError):
        sender.send(_subscription(), {"v": 1}, topic="a", now=0.0)
    assert transport.requests == []


# ── the wire request ─────────────────────────────────────────────────────────


def test_request_shape_and_headers():
    transport = FakeTransport()
    sender = _sender(transport)
    status = sender.send(
        _subscription(),
        {"v": 1, "kind": "test", "title": "prueba", "body": "cuerpo"},
        topic="brain-test",
        ttl=60,
        urgency="high",
        now=1_800_000_000.0,
    )
    assert status == 201
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://push.example/sub/1"
    headers = req["headers"]
    assert headers["TTL"] == "60"
    assert headers["Urgency"] == "high"
    assert headers["Topic"] == "brain-test"
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Content-Type"] == "application/octet-stream"
    auth = headers["Authorization"]
    assert auth.startswith("vapid t=")
    assert ", k=" in auth
    token = auth[len("vapid t=") :].split(",")[0]
    header_b64, claims_b64, sig_b64 = token.split(".")
    claims = json.loads(crypto.b64url_decode(claims_b64))
    assert claims["aud"] == "https://push.example"
    assert claims["sub"] == "mailto:ops@example.net"


def test_body_round_trips_through_receiver_key():
    receiver_priv, receiver_pub = crypto.generate_keypair()
    auth_secret = os.urandom(16)
    sub = Subscription(
        endpoint="https://push.example/sub/9",
        p256dh=crypto.b64url_encode(receiver_pub),
        auth=crypto.b64url_encode(auth_secret),
        created_at=0.0,
    )
    transport = FakeTransport()
    sender = _sender(transport)
    payload = {
        "v": 1,
        "kind": "blocked",
        "pane_id": "p1",
        "title": "agente bloqueado",
        "body": "¿Proceder con el despliegue?",
        "tag": "brain-herd",
    }
    sender.send(sub, payload, topic="brain-herd", now=0.0)
    body = transport.requests[0]["body"]
    assert isinstance(body, bytes)
    assert len(body) <= 4096  # push services reject bigger requests
    plain = _decrypt_body(body, receiver_priv, auth_secret)
    assert json.loads(plain) == payload


def test_seeded_secrets_are_masked_before_encryption(monkeypatch):
    monkeypatch.setenv("HERDR_BRAIN_SECRET_MASKING", "on")
    receiver_priv, receiver_pub = crypto.generate_keypair()
    auth_secret = os.urandom(16)
    sub = Subscription(
        endpoint="https://push.example/sub/9",
        p256dh=crypto.b64url_encode(receiver_pub),
        auth=crypto.b64url_encode(auth_secret),
        created_at=0.0,
    )
    transport = FakeTransport()
    sender = _sender(transport)
    sender.send(
        sub,
        {
            "v": 1,
            "kind": "blocked",
            "title": "agente bloqueado",
            "body": "la clave sk-ant-abcdefghijklmnopqrstuvwxyz quedó en pantalla",
            "tag": "brain-herd",
        },
        topic="brain-herd",
        now=0.0,
    )
    plain = _decrypt_body(transport.requests[0]["body"], receiver_priv, auth_secret)
    text = plain.decode("utf-8")
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in text
    assert "[masked" in text


def test_oversized_payload_is_clipped_not_dropped():
    receiver_priv, receiver_pub = crypto.generate_keypair()
    auth_secret = os.urandom(16)
    sub = Subscription(
        endpoint="https://push.example/sub/9",
        p256dh=crypto.b64url_encode(receiver_pub),
        auth=crypto.b64url_encode(auth_secret),
        created_at=0.0,
    )
    transport = FakeTransport()
    sender = _sender(transport)
    huge = "x" * 8000
    sender.send(sub, {"v": 1, "title": "aviso", "body": huge}, topic="brain-herd", now=0.0)
    body = transport.requests[0]["body"]
    assert len(body) <= 4096
    payload = json.loads(_decrypt_body(body, receiver_priv, auth_secret))
    assert len(payload["body"]) < 8000
    assert payload["body"].endswith("…") or payload["body"].endswith("...")


def test_transport_error_maps_to_status_zero():
    def broken(request):
        raise OSError("no route to host")

    sender = PushSender(keys=generate_keys(subject="mailto:x@example.net"), transport=broken)
    status = sender.send(_subscription(), {"v": 1}, topic="brain-herd", now=0.0)
    assert status == 0
