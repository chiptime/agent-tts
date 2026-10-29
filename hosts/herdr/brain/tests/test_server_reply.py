"""POST /pane/reply: HTTP contract of the user-typed direct reply.

S12 extends the contract additively: every reply is VERIFIED (screen
read before and after) and the response carries a ``receipt`` with the
closed outcome set. The legacy mapping is pinned here — ``delivered``
stays ``200 {ok: true, pane_id}`` (plus the receipt), ``blocked`` 409,
``unknown_pane`` 404, ``error`` 502 — and the new surfaces (steer,
typed drafts, stop) get their own rows.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings


def _receipt(outcome, detail="", text_hint=""):
    return {"outcome": outcome, "detail": detail, "text_hint": text_hint}


class FakeTools:
    outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
        "detail": "", "receipt": _receipt("effective"),
    }
    replies: list = []
    stop_results: list = []
    follow_results: list = []

    def __init__(self, cfg):
        self.last_active = None

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        type(self).replies.append((pane_id, text, steer))
        return dict(type(self).outcome, pane_id=pane_id)

    def stop_pane(self, pane_id):
        type(self).stop_results.append(pane_id)
        result = type(self).stop_outcome
        return dict(result, pane_id=pane_id)

    stop_outcome = {"outcome": "stopped", "keys": ["esc"], "detail": ""}

    def submit_typed_text(self, pane_id, text_hint):
        type(self).follow_results.append(("submit", pane_id, text_hint))
        return dict(type(self).follow_outcome)

    def discard_typed_text(self, pane_id, agent_kind, text_hint):
        type(self).follow_results.append(("discard", pane_id, text_hint))
        return dict(type(self).follow_outcome)

    follow_outcome = {"outcome": "sent", "detail": "enviado con Intro"}


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    FakeTools.replies = []
    FakeTools.stop_results = []
    FakeTools.follow_results = []
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
        "detail": "", "receipt": _receipt("effective"),
    }
    FakeTools.stop_outcome = {"outcome": "stopped", "keys": ["esc"], "detail": ""}
    FakeTools.follow_outcome = {"outcome": "sent", "detail": "enviado con Intro"}
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
    return TestClient(server_module.create_app(settings=cfg))


def test_delivered_reply_returns_ok_and_forwards_the_exact_text(client):
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes, go ahead"})

    assert resp.status_code == 200
    body = resp.json()
    # Legacy fields, unchanged: the additive S12 change is the receipt.
    assert body["ok"] is True
    assert body["pane_id"] == "w1:p2"
    receipt = body["receipt"]
    assert receipt["id"].startswith("rct-")
    assert receipt["outcome"] == "effective"
    assert receipt["pane_id"] == "w1:p2"
    assert FakeTools.replies == [("w1:p2", "yes, go ahead", False)]


@pytest.mark.parametrize(
    "outcome,status",
    [("unknown_pane", 404), ("blocked", 409), ("error", 502)],
)
def test_failure_outcomes_map_to_http_errors(client, outcome, status):
    FakeTools.outcome = {
        "outcome": outcome, "pane_id": "w1:p2", "status": "", "detail": "boom",
        "receipt": _receipt("refused" if outcome == "blocked" else "unknown"),
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "yes"})

    assert resp.status_code == status
    assert resp.json()["detail"]


def test_unknown_pane_reuses_the_store_detail(client):
    FakeTools.outcome = {
        "outcome": "unknown_pane", "pane_id": "w1:p2", "status": "", "detail": "pane not found",
        "receipt": None,
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
        "receipt": _receipt("refused", "el agente rechazó el texto"),
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


# ── S12: the receipt protocol ─────────────────────────────────────────────────


def test_typed_not_submitted_is_a_200_that_tells_the_truth(client):
    """The blind send is over: a draft the terminal never took is NOT
    ok — the phone keeps the receipt card with Submit/Discard."""
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "",
        "receipt": _receipt(
            "typed_not_submitted",
            "el texto quedó escrito en la línea de entrada sin enviarse",
            text_hint="sigue adelante",
        ),
    }

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue adelante"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["receipt"]["outcome"] == "typed_not_submitted"
    assert body["receipt"]["text_hint"] == "sigue adelante"


def test_receipts_listing_is_newest_first_and_filters_by_pane(client):
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "one"})
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "two"})

    resp = client.get("/pane/receipts", params={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pane_id"] == "w1:p2"
    assert [r["outcome"] for r in body["receipts"]] == ["effective", "effective"]
    assert len(body["receipts"]) == 2
    other = client.get("/pane/receipts", params={"pane_id": "w9:p9"}).json()
    assert other["receipts"] == []


def test_submit_finishes_a_typed_receipt_with_enter_only(client):
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue adelante"})
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="sigue"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "sent", "detail": "enviado con Intro"}
    resp = client.post(f"/pane/receipt/{receipt_id}/submit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["receipt"]["outcome"] == "sent"
    # The follow-up carries NO text: only the receipt's masked tail.
    assert FakeTools.follow_results[-1] == ("submit", "w1:p2", "sigue")
    # The store keeps the resolved state.
    stored = client.get("/pane/receipts").json()["receipts"][0]
    assert stored["outcome"] == "sent"


def test_discard_clears_a_typed_receipt(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="sigue"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "sigue"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "discarded", "detail": "entrada descartada"}
    resp = client.post(f"/pane/receipt/{receipt_id}/discard")

    assert resp.status_code == 200
    assert resp.json()["receipt"]["outcome"] == "discarded"
    assert FakeTools.follow_results[-1][0] == "discard"


def test_submit_and_discard_refuse_anything_not_typed(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "done",
        "detail": "", "receipt": _receipt("effective"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "done"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    for action in ("submit", "discard"):
        resp = client.post(f"/pane/receipt/{receipt_id}/{action}")
        assert resp.status_code == 409
        assert resp.json()["detail"]

    assert client.post("/pane/receipt/rct-missing/submit").status_code == 404
    assert client.post("/pane/receipt/rct-missing/discard").status_code == 404
    assert FakeTools.follow_results == []


def test_followup_key_failure_is_502_and_leaves_the_receipt_alone(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="x"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "x"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "error", "detail": "mux refused"}
    resp = client.post(f"/pane/receipt/{receipt_id}/submit")

    assert resp.status_code == 502
    stored = client.get("/pane/receipts").json()["receipts"][0]
    assert stored["outcome"] == "typed_not_submitted"


def test_surviving_echo_keeps_the_receipt_re_offerable(client):
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="x"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "x"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]

    FakeTools.follow_outcome = {"outcome": "typed_not_submitted", "detail": "sigue"}
    resp = client.post(f"/pane/receipt/{receipt_id}/submit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["receipt"]["outcome"] == "typed_not_submitted"


# ── S12: steer ────────────────────────────────────────────────────────────────


def test_steer_reaches_the_tools_layer_as_a_flag(client):
    resp = client.post(
        "/pane/reply", json={"pane_id": "w1:p2", "text": "keep going", "steer": True}
    )

    assert resp.status_code == 200
    assert FakeTools.replies == [("w1:p2", "keep going", True)]


def test_steer_refusal_is_a_422_that_names_the_alternative(client):
    FakeTools.outcome = {
        "outcome": "unsupported_steer", "pane_id": "w1:p2", "status": "",
        "detail": "a running turn of a shell agent cannot be steered",
        "receipt": None,
    }

    resp = client.post(
        "/pane/reply", json={"pane_id": "w1:p2", "text": "keep going", "steer": True}
    )

    assert resp.status_code == 422
    assert "inyectar" in resp.json()["detail"] or "turno" in resp.json()["detail"]


# ── S12: stop ─────────────────────────────────────────────────────────────────


def test_stop_interrupts_with_the_recipe_and_audits(client):
    resp = client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "pane_id": "w1:p2", "keys": ["esc"]}
    assert FakeTools.stop_results == ["w1:p2"]


@pytest.mark.parametrize(
    "outcome,status",
    [("unknown_pane", 404), ("unsupported_harness", 422), ("error", 502)],
)
def test_stop_failures_map_to_http_errors(client, outcome, status):
    FakeTools.stop_outcome = {
        "outcome": outcome, "keys": [], "detail": "because",
    }

    resp = client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert resp.status_code == status
    assert resp.json()["detail"]


def test_stop_requires_the_exact_pane_id(client):
    assert client.post("/pane/stop", json={}).status_code == 422
    assert client.post("/pane/stop", json={"pane_id": ""}).status_code == 422
    assert FakeTools.stop_results == []


# ── audit ─────────────────────────────────────────────────────────────────────


def test_every_write_action_lands_in_the_audit_log(client, tmp_path):
    # The settings fixture points state_dir at tmp_path/state.
    audit_file = tmp_path / "state" / "audit.log"
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "one"})
    FakeTools.outcome = {
        "outcome": "delivered", "pane_id": "w1:p2", "status": "timeout",
        "detail": "", "receipt": _receipt("typed_not_submitted", text_hint="two"),
    }
    client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "two"})
    receipt_id = client.get("/pane/receipts").json()["receipts"][0]["id"]
    client.post(f"/pane/receipt/{receipt_id}/discard")
    client.post("/pane/stop", json={"pane_id": "w1:p2"})

    assert audit_file.exists()
    actions = [
        json.loads(line)["action"] for line in audit_file.read_text().splitlines()
    ]
    assert "pane.reply" in actions
    assert "pane.receipt.discard" in actions
    assert "pane.stop" in actions
