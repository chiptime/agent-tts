"""Independent security-access regressions for the S11 correction batch
(B2/B3/B4): percent-encoded query-token spellings must be redacted in
the REAL uvicorn access record, the /pair claim budget must key on the
transport peer a client cannot forge, and a refused (429) bootstrap
must leave every pairing file byte-identical even when it carries a
valid bearer. Synthetic data only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from uvicorn.protocols.utils import get_path_with_query_string

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings

from tests.conftest import SETTINGS_KWARGS
from tests.test_read_token_gate import pair_device


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def make_client(state_dir, monkeypatch, **app_overrides):
    """The test_pairing_api make_client shape: real default gate, tmp
    state dir, optional create_app seams for a finite /events stream."""
    monkeypatch.setattr(server_module, "BrainTools", FakePairTools)
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
    }
    app = server_module.create_app(settings=Settings(**kwargs), **app_overrides)
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://127.0.0.1:8741")


class FakePairTools:
    """BrainTools double: the /pair path needs none of its surface."""

    def __init__(self, cfg):
        self.cfg = cfg

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return []


# ── B2: accepted encoded spellings never reach the access log ────────────────


def test_an_accepted_encoded_query_token_is_redacted(
    state_dir, monkeypatch
):
    """``device%5Ftoken=`` authenticates on /events (parse_qsl decodes
    the key), so the access-log redaction must catch it too — through
    the REAL uvicorn record shape, not only the pure helper."""
    client = make_client(
        state_dir, monkeypatch, sse_heartbeat_s=0.05, sse_stream_limit=1
    )
    token = pair_device(client, state_dir)

    with client.stream("GET", "/events?device%5Ftoken=" + token) as resp:
        assert resp.status_code == 200

    scope = {"path": "/events", "query_string": ("device%5Ftoken=" + token).encode()}
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:4000", "GET", get_path_with_query_string(scope), "1.1", 200),
        None,
    )
    assert server_module.RedactQueryToken().filter(record) is True
    message = record.getMessage()
    assert token not in message
    assert "device%5Ftoken=" in message
    # No later formatting can re-leak the value either.
    assert record.args is None


# ── B3: the claim budget keys on the transport peer ──────────────────────────


def test_the_pair_budget_counts_the_tcp_peer_not_forwarded_headers(
    state_dir, monkeypatch
):
    """X-Forwarded-For is not identity: <=10 attempts from ONE TCP peer,
    then a 429, whatever forwarded headers the requests forge. (uvicorn
    with its default proxy-header trust would rewrite the ASGI client
    from exactly these headers; the production launch pinned below
    never installs that rewriting.)"""
    client = make_client(state_dir, monkeypatch)
    pairing.PairingStore(state_dir).mint()

    for i in range(10):
        resp = client.post(
            "/pair",
            json={"code": "WRONGWRONG", "label": "x"},
            headers={"X-Forwarded-For": f"192.0.2.{i}"},
        )
        assert resp.status_code == 400

    limited = client.post(
        "/pair",
        json={"code": "WRONGWRONG", "label": "x"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 60


def test_the_production_launch_disables_uvicorn_proxy_header_trust():
    """The actual entry point must not let client-controlled
    X-Forwarded-For rewrite scope['client'] (which the limiter keys
    on). Co-located reverse-proxy connections therefore share one
    transport-peer budget unless a separately trusted scheme is
    deliberately supported."""
    settings = Settings(**SETTINGS_KWARGS)
    kwargs = server_module.uvicorn_launch_kwargs("127.0.0.1", settings)
    assert kwargs["proxy_headers"] is False
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == settings.brain_port
    assert kwargs["log_config"] is None


# ── B4: a refused bootstrap touches no credential ────────────────────────────


def test_an_authenticated_refused_pair_leaves_the_registry_byte_identical(
    state_dir, monkeypatch
):
    """The 429 must fire before ANY credential touch: a valid bearer on
    a budget-exhausted /pair stamps no lastSeenAt (aged here, so a
    stamp would rewrite the bytes) and rewrites no pairing file."""
    client = make_client(state_dir, monkeypatch)
    token = pair_device(client, state_dir)
    pairing.PairingStore(state_dir).mint()

    def pending_bytes():
        path = state_dir / pairing.PENDING_FILENAME
        return path.read_bytes() if path.exists() else None

    for _ in range(10):
        client.post("/pair", json={"code": "WRONGWRONG", "label": "x"})

    registry = state_dir / pairing.DEVICES_FILENAME
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["devices"][0]["lastSeenAt"] = 1
    registry.write_text(json.dumps(data), encoding="utf-8")
    registry_before = registry.read_bytes()
    pending_before = pending_bytes()

    limited = client.post(
        "/pair",
        json={"code": "WRONGWRONG", "label": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert limited.status_code == 429
    assert registry.read_bytes() == registry_before
    assert pending_bytes() == pending_before
