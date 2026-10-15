"""POST /pane/reply: HTTP contract of the user-typed direct reply."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings


class FakeTools:
    outcome = {"outcome": "delivered", "pane_id": "w1:p2", "status": "done", "detail": ""}
    replies: list = []

    def __init__(self, cfg):
        self.last_active = None

    def reply_to_pane(self, pane_id, text):
        type(self).replies.append((pane_id, text))
        return dict(type(self).outcome, pane_id=pane_id)


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    FakeTools.replies = []
    FakeTools.outcome = {"outcome": "delivered", "pane_id": "w1:p2", "status": "done", "detail": ""}
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
    return TestClient(server_module.create_app(settings=cfg))


def test_delivered_reply_returns_ok_and_forwards_the_exact_text(client):
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes, go ahead"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "pane_id": "w1:p2"}
    assert FakeTools.replies == [("w1:p2", "yes, go ahead")]


@pytest.mark.parametrize(
    "outcome,status",
    [("unknown_pane", 404), ("blocked", 409), ("error", 502)],
)
def test_failure_outcomes_map_to_http_errors(client, outcome, status):
    FakeTools.outcome = {"outcome": outcome, "pane_id": "w1:p2", "status": "", "detail": "boom"}

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.status_code == status
    assert resp.json()["detail"]


def test_unknown_pane_reuses_the_store_detail(client):
    FakeTools.outcome = {
        "outcome": "unknown_pane", "pane_id": "w1:p2", "status": "", "detail": "pane not found",
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.json()["detail"] == "pane not found"


def test_blocked_pane_points_at_the_dialog_not_at_the_agent(client):
    """herdr refused before writing input, so the message must not imply
    the agent rejected the text — the dialog is answered with keys."""
    FakeTools.outcome = {
        "outcome": "blocked",
        "pane_id": "w1:p2",
        "status": "blocked",
        "detail": "the agent rejected the reply",
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "diálogo" in detail
    assert "teclas" in detail
    assert "rejected" not in detail


@pytest.mark.parametrize(
    "body",
    [
        {"text": "yes"},
        {"pane_id": "w1:p2"},
        {"pane_id": "", "text": "yes"},
        {"pane_id": "w1:p2", "text": ""},
        {"pane_id": "w1:p2", "text": "x" * 8_001},
    ],
)
def test_invalid_bodies_are_422_and_nothing_is_sent(client, body):
    resp = client.post("/pane/reply", json=body)

    assert resp.status_code == 422
    assert FakeTools.replies == []


def test_whitespace_only_text_is_rejected_before_sending(client):
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "   \n "})

    assert resp.status_code == 422
    assert FakeTools.replies == []
