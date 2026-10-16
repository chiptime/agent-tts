"""POST /pane/keys: HTTP contract of the blocked-dialog key pad."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings


class FakeTools:
    outcome = {"outcome": "sent", "pane_id": "w1:p2", "status": "", "detail": ""}
    sent: list = []

    def __init__(self, cfg):
        self.last_active = None

    def send_keys_to_pane(self, pane_id, keys):
        type(self).sent.append((pane_id, keys))
        return dict(type(self).outcome, pane_id=pane_id)


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    FakeTools.sent = []
    FakeTools.outcome = {"outcome": "sent", "pane_id": "w1:p2", "status": "", "detail": ""}
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
    return TestClient(server_module.create_app(settings=cfg))


def test_sent_keys_return_ok_and_forward_the_exact_sequence(client):
    resp = client.post("/pane/keys", json={"pane_id": "w1:p2", "keys": ["right", "enter"]})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "pane_id": "w1:p2"}
    assert FakeTools.sent == [("w1:p2", ["right", "enter"])]


@pytest.mark.parametrize(
    ("outcome", "status"),
    [("unknown_pane", 404), ("error", 502)],
)
def test_failure_outcomes_map_to_http_errors(client, outcome, status):
    FakeTools.outcome = {"outcome": outcome, "pane_id": "w1:p2", "status": "", "detail": "boom"}

    resp = client.post("/pane/keys", json={"pane_id": "w1:p2", "keys": ["enter"]})

    assert resp.status_code == status
    assert resp.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"pane_id": "w1:p2", "keys": []},
        {"pane_id": "w1:p2", "keys": ["ctrl+c"]},
        {"pane_id": "w1:p2", "keys": ["y"]},
        {"pane_id": "w1:p2", "keys": ["enter", "rm -rf /"]},
        {"pane_id": "w1:p2", "keys": ["enter"] * 50},
        {"pane_id": "", "keys": ["enter"]},
        {"pane_id": "w1:p2"},
    ],
)
def test_invalid_bodies_are_422_and_nothing_is_sent(client, body):
    resp = client.post("/pane/keys", json=body)

    assert resp.status_code == 422
    assert FakeTools.sent == []


def test_an_unknown_key_names_the_allowed_set(client):
    resp = client.post("/pane/keys", json={"pane_id": "w1:p2", "keys": ["ctrl+c"]})

    detail = str(resp.json()["detail"])
    assert "ctrl+c" in detail
    assert "enter" in detail
