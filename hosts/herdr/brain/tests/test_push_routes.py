"""HTTP wiring of Web Push (S5): routes, gate classification, audit.

The service core is covered by test_webpush_*; these pins hold the
SERVER contract: ``GET /push/key`` reads the public key, the three POST
routes are write-classified (device gate + audit) with the device
attributed from the access middleware, and a corrupt subscription store
answers 500 without resetting a single byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings
from herdr_brain.webpush import WebPushService
from herdr_brain.webpush.vapid import generate_keys, save_keys

from tests.conftest import SETTINGS_KWARGS
from tests.test_pairing_api import LOOPBACK

NOT_PAIRED = "dispositivo no emparejado"


def _valid_subscription_keys() -> dict:
    """A browser-shaped keys object: a REAL P-256 point (the sender
    validates before encrypting) and 16 random auth bytes."""
    import os

    from herdr_brain.webpush import crypto

    _private, public = crypto.generate_keypair()
    return {
        "p256dh": crypto.b64url_encode(public),
        "auth": crypto.b64url_encode(os.urandom(16)),
    }


SUBSCRIPTION = {
    "endpoint": "https://push.example.invalid/f/first",
    "keys": _valid_subscription_keys(),
}


class FakeTransport:
    """Records push requests; never touches a network."""

    def __init__(self, status=201):
        self.status = status
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return type("Response", (), {"status": self.status})()


def make_push_service(state_dir: Path, transport=None) -> WebPushService:
    save_keys(state_dir, generate_keys())
    # The same audit trail create_app writes to (<state_dir>/audit.log):
    # the service owns its own audit lines, so the injected double must
    # hold the same file the production wiring hands it.
    from herdr_brain.audit import AuditLog

    return WebPushService(
        state_dir, transport=transport, audit_log=AuditLog(state_dir / "audit.log")
    )


def make_client(state_dir, push_service=None, **app_overrides) -> TestClient:
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
    }
    app = server_module.create_app(
        settings=Settings(**kwargs),
        push_service=push_service,
        **app_overrides,
    )
    return TestClient(app, base_url=LOOPBACK)


def pair_device(client, state_dir, label="phone") -> str:
    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    status, outcome = store.claim(minted["code"], label)
    assert status == "ok", outcome
    return outcome["token"]


def read_audit(state_dir: Path) -> list:
    path = state_dir / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.fixture
def state_dir(tmp_path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def transport():
    return FakeTransport()


class TestKey:
    def test_key_reads_the_public_key_when_configured(self, state_dir, transport):
        push = make_push_service(state_dir, transport)
        client = make_client(state_dir, push_service=push)
        resp = client.get("/push/key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["public_key"] == push.public_key()

    def test_key_is_available_without_any_operator_setup(self, state_dir):
        client = make_client(state_dir)
        resp = client.get("/push/key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert isinstance(body["public_key"], str) and body["public_key"]
        assert (state_dir / "vapid.json").exists()


class TestSubscribe:
    def test_subscribe_persists_and_audits_with_the_device_label(
        self, state_dir, transport
    ):
        push = make_push_service(state_dir, transport)
        client = make_client(state_dir, push_service=push)
        token = pair_device(client, state_dir)

        resp = client.post(
            "/push/subscribe",
            json=SUBSCRIPTION,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["endpoint"] == SUBSCRIPTION["endpoint"]
        assert body["device"] == "phone"

        lines = read_audit(state_dir)
        assert any(
            line["action"] == "push.subscribe"
            and line["device"] == "phone"
            and line["detail"]["endpoint"] == SUBSCRIPTION["endpoint"]
            for line in lines
        )

    def test_subscribe_without_bearer_is_refused_once_paired(self, state_dir):
        push = make_push_service(state_dir)
        client = make_client(state_dir, push_service=push)
        pair_device(client, state_dir)

        resp = client.post("/push/subscribe", json=SUBSCRIPTION)
        assert resp.status_code == 403
        assert resp.json()["detail"] == NOT_PAIRED
        # nothing was stored
        assert not (state_dir / "push-subscriptions.json").exists()

    def test_subscribe_rejects_a_bad_payload_with_422(self, state_dir):
        push = make_push_service(state_dir)
        client = make_client(state_dir, push_service=push)
        resp = client.post(
            "/push/subscribe",
            json={"endpoint": "https://push.example.invalid/x", "keys": {}},
        )
        assert resp.status_code == 422

    def test_subscribe_on_a_corrupt_store_is_a_500_and_bytes_stay_identical(
        self, state_dir
    ):
        state_dir.mkdir(parents=True, exist_ok=True)
        corrupt = b"{ this is not the subscriptions file"
        (state_dir / "push-subscriptions.json").write_bytes(corrupt)
        push = WebPushService(state_dir)  # loads the corrupt store

        client = make_client(state_dir, push_service=push)
        resp = client.post("/push/subscribe", json=SUBSCRIPTION)
        assert resp.status_code == 500

        assert (
            state_dir / "push-subscriptions.json"
        ).read_bytes() == corrupt  # never reset


class TestUnsubscribe:
    def test_unsubscribe_removes_and_audits(self, state_dir, transport):
        push = make_push_service(state_dir, transport)
        client = make_client(state_dir, push_service=push)
        token = pair_device(client, state_dir)
        client.post(
            "/push/subscribe",
            json=SUBSCRIPTION,
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = client.post(
            "/push/unsubscribe",
            json={"endpoint": SUBSCRIPTION["endpoint"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert any(
            line["action"] == "push.unsubscribe" and line["device"] == "phone"
            for line in read_audit(state_dir)
        )

    def test_unsubscribe_of_an_unknown_endpoint_is_idempotent(self, state_dir):
        push = make_push_service(state_dir)
        client = make_client(state_dir, push_service=push)
        resp = client.post(
            "/push/unsubscribe", json={"endpoint": "https://push.example.invalid/gone"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}

    def test_unsubscribe_without_bearer_is_refused_once_paired(self, state_dir):
        push = make_push_service(state_dir)
        client = make_client(state_dir, push_service=push)
        pair_device(client, state_dir)
        resp = client.post(
            "/push/unsubscribe", json={"endpoint": "https://push.example.invalid/x"}
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == NOT_PAIRED


class TestTestRoute:
    def test_test_sends_one_notification_per_device_and_audits(
        self, state_dir, transport
    ):
        push = make_push_service(state_dir, transport)
        client = make_client(state_dir, push_service=push)
        token = pair_device(client, state_dir)
        client.post(
            "/push/subscribe",
            json=SUBSCRIPTION,
            headers={"Authorization": f"Bearer {token}"},
        )
        transport.requests.clear()

        resp = client.post(
            "/push/test", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"sent": 1}
        assert len(transport.requests) == 1
        assert transport.requests[0]["url"] == SUBSCRIPTION["endpoint"]
        assert any(
            line["action"] == "push.test" and line["device"] == "phone"
            for line in read_audit(state_dir)
        )

    def test_test_without_subscriptions_sends_nothing(self, state_dir, transport):
        push = make_push_service(state_dir, transport)
        client = make_client(state_dir, push_service=push)
        resp = client.post("/push/test")
        assert resp.status_code == 200
        assert resp.json() == {"sent": 0}
        assert transport.requests == []

    def test_test_without_bearer_is_refused_once_paired(self, state_dir):
        push = make_push_service(state_dir)
        client = make_client(state_dir, push_service=push)
        pair_device(client, state_dir)
        assert client.post("/push/test").status_code == 403
