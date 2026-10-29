"""The WebPushService facade over the whole package (S5, S9, S14).

Everything the parent wires into routes, the watcher and the service
worker goes through this one object: subscriptions (audited), the
coalescing coordinator, encrypted fan-out with store accounting, and
the prompt-cache warning tick. No keys configured ⇒ soft-off (the
server keeps running, nothing sends), per doc 08.
"""

from __future__ import annotations

import json

import pytest

from herdr_brain.audit import AuditLog
from herdr_brain.webpush import WebPushService


class FakeTransport:
    def __init__(self, status=201):
        self.requests = []
        self.status = status

    def __call__(self, request):
        self.requests.append(request)
        return type("Resp", (), {"status": self.status})()


@pytest.fixture()
def keys_env(monkeypatch):
    from herdr_brain.webpush import crypto, vapid

    private, public = crypto.generate_keypair()
    monkeypatch.setenv("HERDR_BRAIN_VAPID_PUBLIC", crypto.b64url_encode(public))
    monkeypatch.setenv("HERDR_BRAIN_VAPID_PRIVATE", crypto.b64url_encode(private.to_bytes(32, "big")))
    monkeypatch.setenv("HERDR_BRAIN_VAPID_SUBJECT", "mailto:ops@example.net")
    return private, public


@pytest.fixture()
def clock():
    state = {"now": 5_000_000.0}
    return state


def _service(tmp_path, transport, clock, keys=True, audit=None):
    import os

    env = dict(os.environ) if keys else {}
    return WebPushService(
        state_dir=tmp_path,
        transport=transport,
        clock=lambda: clock["now"],
        audit_log=audit,
        _environ=env,
        coalesce_window_s=0.0,
    )


def _subscribe(service, endpoint="https://push.example/sub/1", device="movil"):
    from herdr_brain.webpush import crypto

    _, receiver_pub = crypto.generate_keypair()
    return service.subscribe(
        {
            "endpoint": endpoint,
            "keys": {
                "p256dh": crypto.b64url_encode(receiver_pub),
                "auth": crypto.b64url_encode(b"B" * 16),
            },
        },
        device=device,
    )


def test_without_configured_keys_the_service_creates_its_own_pair(tmp_path, clock):
    service = _service(tmp_path, FakeTransport(), clock, keys=False)
    assert service.enabled is True
    assert isinstance(service.public_key(), str)
    assert (tmp_path / "vapid.json").exists()
    # A second process on the same state dir signs with the same pair.
    other = _service(tmp_path, FakeTransport(), clock, keys=False)
    assert other.public_key() == service.public_key()


def test_an_unwritable_state_dir_degrades_to_soft_off(tmp_path, clock, monkeypatch):
    from herdr_brain.webpush import vapid

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(vapid, "_create_key_file", refuse)
    service = _service(tmp_path, FakeTransport(), clock, keys=False)
    assert service.enabled is False
    assert service.public_key() is None
    # The store still works: the file is a file, not a capability.
    _subscribe(service)
    service.notify_transition("p1", "claude-1", "blocked", question="¿A?")
    service.tick()
    assert service.subscriptions() == ["https://push.example/sub/1"]


def test_subscribe_persists_and_audits(tmp_path, clock, keys_env):
    audit_path = tmp_path / "audit.log"
    audit = AuditLog(audit_path)
    service = _service(tmp_path, FakeTransport(), clock, audit=audit)
    assert service.enabled is True
    assert isinstance(service.public_key(), str)
    _subscribe(service, device="bolsillo")
    raw = json.loads((tmp_path / "push-subscriptions.json").read_text())
    assert list(raw["subscriptions"]) == ["https://push.example/sub/1"]
    lines = [json.loads(l) for l in audit_path.read_text().splitlines()]
    assert lines[-1]["action"] == "push.subscribe"
    assert lines[-1]["device"] == "bolsillo"


def test_subscribe_rejects_bad_bodies(tmp_path, clock, keys_env):
    service = _service(tmp_path, FakeTransport(), clock)
    with pytest.raises(ValueError):
        service.subscribe({"endpoint": "notaurl", "keys": {"p256dh": "A", "auth": "B"}})
    with pytest.raises(ValueError):
        service.subscribe(
            {"endpoint": "https://push.example/sub/1", "keys": {"auth": "B"}}
        )


def test_unsubscribe_audits_and_removes(tmp_path, clock, keys_env):
    audit_path = tmp_path / "audit.log"
    audit = AuditLog(audit_path)
    service = _service(tmp_path, FakeTransport(), clock, audit=audit)
    _subscribe(service)
    assert service.unsubscribe("https://push.example/sub/1") is True
    assert service.unsubscribe("https://push.example/sub/1") is False
    lines = [json.loads(l) for l in audit_path.read_text().splitlines()]
    assert [l["action"] for l in lines] == ["push.subscribe", "push.unsubscribe"]


def test_transition_reaches_every_device_encrypted(tmp_path, clock, keys_env):
    transport = FakeTransport()
    service = _service(tmp_path, transport, clock)
    _subscribe(service, endpoint="https://push.example/sub/1")
    _subscribe(service, endpoint="https://push.example/sub/2")
    service.notify_transition("p1", "claude-1", "blocked", question="¿Desplegar?")
    service.tick()
    assert len(transport.requests) == 2
    urls = {r["url"] for r in transport.requests}
    assert urls == {"https://push.example/sub/1", "https://push.example/sub/2"}
    for request in transport.requests:
        assert request["headers"]["Topic"] == "brain-herd"
        assert request["headers"]["Urgency"] == "high"
        assert request["headers"]["Content-Encoding"] == "aes128gcm"


def test_handled_retraction_flows_to_devices(tmp_path, clock, keys_env):
    transport = FakeTransport()
    service = _service(tmp_path, transport, clock)
    _subscribe(service)
    service.notify_transition("p1", "claude-1", "blocked", question="¿A?")
    service.tick()
    service.notify_handled("p1", "claude-1")
    assert len(transport.requests) == 2


def test_gone_status_prunes_device(tmp_path, clock, keys_env):
    transport = FakeTransport(status=404)
    service = _service(tmp_path, transport, clock)
    _subscribe(service)
    service.notify_transition("p1", "claude-1", "blocked", question="¿A?")
    service.tick()
    assert service.subscriptions() == []


def test_corrupt_store_sends_nothing_and_refuses_writes(tmp_path, clock, keys_env):
    (tmp_path / "push-subscriptions.json").write_bytes(b"{{{{")
    transport = FakeTransport()
    service = _service(tmp_path, transport, clock)
    service.notify_transition("p1", "claude-1", "blocked", question="¿A?")
    service.tick()
    assert transport.requests == []
    with pytest.raises(Exception):
        _subscribe(service)


def test_test_notification_round_trip(tmp_path, clock, keys_env):
    from herdr_brain.webpush import crypto

    receiver_priv, receiver_pub = crypto.generate_keypair()
    auth_secret = b"C" * 16
    transport = FakeTransport()
    service = _service(tmp_path, transport, clock)
    service.subscribe(
        {
            "endpoint": "https://push.example/sub/1",
            "keys": {
                "p256dh": crypto.b64url_encode(receiver_pub),
                "auth": crypto.b64url_encode(auth_secret),
            },
        }
    )
    result = service.test_notification()
    assert result["sent"] == 1
    assert len(transport.requests) == 1

    # Decrypt through the receiver to prove the payload contract.
    from herdr_brain.webpush.sender import encrypt_payload  # noqa: F401 — shape import

    body = transport.requests[0]["body"]
    delivery_public = body[21:86]
    assert body[20] == 65  # idlen: the uncompressed point length
    salt = body[:16]
    sealed = body[86:]
    prk = crypto.hkdf_sha256(
        crypto.ecdh_p256(receiver_priv, delivery_public) + auth_secret,
        salt,
        b"WebPush: info\x00" + crypto.public_from_private(receiver_priv) + delivery_public,
        32,
    )
    cek = crypto.hkdf_sha256(prk, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = crypto.hkdf_sha256(prk, salt, b"Content-Encoding: nonce\x00", 12)
    content = crypto.aes128gcm_decrypt(cek, nonce, sealed)
    payload = json.loads(content[: content.rfind(b"\x02")])
    assert payload["v"] == 1
    assert payload["kind"] == "test"
    assert payload["tag"] == "brain-test"


def test_tick_emits_cache_warnings(tmp_path, clock, keys_env):
    transport = FakeTransport()
    service = _service(tmp_path, transport, clock)
    _subscribe(service)
    base = clock["now"]
    last_activity = base - 300.0  # ttl default 300 → already expiring
    panes = [("p1", "claude-1", last_activity)]
    service.tick(panes=panes)
    assert len(transport.requests) == 1
    topic = transport.requests[0]["headers"]["Topic"]
    assert topic == "brain-cache"
    # Same idle period: no second warning.
    service.tick(panes=panes)
    assert len(transport.requests) == 1
