"""The PWA-only opencode permission surface.

GET  /pane/permission        — what the viewed pane's agent is waiting for
POST /pane/permission/reply  — answer it with once|always|reject
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings
from herdr_brain.permissions import OpenCodeError, OpenCodeNotFound, PermissionRequest
from herdr_brain.tools import TOOLS_SCHEMA

PANE = {"pane_id": "w1:p2", "agent": "opencode", "session_id": "ses_abc", "cwd": "/repo"}
PENDING = [
    PermissionRequest(
        request_id="per_1",
        session_id="ses_abc",
        permission="edit",
        patterns=("src/x.py",),
        always=("src/*",),
    )
]


class FakeTools:
    pane = PANE

    def __init__(self, cfg):
        self.last_active = None

    def pane_info(self, pane_id):
        return dict(type(self).pane) if pane_id == type(self).pane["pane_id"] else None


class FakePermissions:
    pending = PENDING
    error = None
    replies = []

    def list_pending(self, session_id=None, directory=None):
        if type(self).error is not None:
            raise type(self).error
        return [p for p in type(self).pending if session_id is None or p.session_id == session_id]

    def reply(self, request_id, reply, message=None):
        if type(self).error is not None:
            raise type(self).error
        type(self).replies.append((request_id, reply))
        return True


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    FakeTools.pane = PANE
    FakePermissions.pending = PENDING
    FakePermissions.error = None
    FakePermissions.replies = []
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
    return TestClient(server_module.create_app(settings=cfg, permissions=FakePermissions()))


# -- S5: the model must never reach this ------------------------------------


def test_no_llm_tool_can_answer_a_permission():
    """S5: a model able to approve its own request defeats the gate.

    Asserted on tool NAMES, parameter names and the exact reply enum — not
    on prose, where an unrelated "once" already appears.
    """
    for tool in TOOLS_SCHEMA:
        function = tool["function"]
        assert "permission" not in function["name"]
        properties = (function.get("parameters") or {}).get("properties") or {}
        for name, prop in properties.items():
            assert "permission" not in name
            assert prop.get("enum") != ["once", "always", "reject"]


# -- GET /pane/permission ---------------------------------------------------


def test_lists_the_pending_request_of_the_viewed_pane(client):
    resp = client.get("/pane/permission", params={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    pending = resp.json()["pending"]
    assert pending["request_id"] == "per_1"
    assert pending["permission"] == "edit"
    assert pending["patterns"] == ["src/x.py"]


def test_reports_nothing_pending(client):
    FakePermissions.pending = []

    resp = client.get("/pane/permission", params={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    assert resp.json() == {"pending": None}


def test_unknown_pane_is_404_and_never_the_focused_one(client):
    resp = client.get("/pane/permission", params={"pane_id": "w9:p9"})

    assert resp.status_code == 404


def test_pane_without_a_session_reports_nothing_pending(client):
    FakeTools.pane = {**PANE, "session_id": None}

    resp = client.get("/pane/permission", params={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    assert resp.json() == {"pending": None}


def test_unreachable_opencode_is_502_not_a_silent_no(client):
    FakePermissions.error = OpenCodeError("connection refused")

    resp = client.get("/pane/permission", params={"pane_id": "w1:p2"})

    assert resp.status_code == 502
    assert resp.json()["detail"]


# -- POST /pane/permission/reply --------------------------------------------


@pytest.mark.parametrize("reply", ["once", "always", "reject"])
def test_forwards_the_exact_contract_value(client, reply):
    resp = client.post(
        "/pane/permission/reply", json={"request_id": "per_1", "reply": reply}
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert FakePermissions.replies == [("per_1", reply)]


def test_an_invented_answer_never_reaches_opencode(client):
    resp = client.post(
        "/pane/permission/reply", json={"request_id": "per_1", "reply": "yes"}
    )

    assert resp.status_code == 422
    assert FakePermissions.replies == []


def test_a_vanished_request_is_404(client):
    FakePermissions.error = OpenCodeNotFound("permission not found")

    resp = client.post(
        "/pane/permission/reply", json={"request_id": "per_1", "reply": "once"}
    )

    assert resp.status_code == 404


def test_unreachable_opencode_on_reply_is_502(client):
    FakePermissions.error = OpenCodeError("connection refused")

    resp = client.post(
        "/pane/permission/reply", json={"request_id": "per_1", "reply": "once"}
    )

    assert resp.status_code == 502
