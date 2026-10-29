"""Voice stop (S12): «para al agente» proposes an approval gate.

The spoken path must not bypass the verbal binding: a stop_agent call
from the tool loop NEVER dispatches — it freezes behind the same
ApprovalGateStore a send uses, and only the approve replay runs the
interrupt. These tests pin that boundary end to end: the gate (llm
level), the replay (server level) and the access classification of the
new routes under enforcement.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.approval import ApprovalGateStore, PROPOSED
from herdr_brain.config import Settings
from herdr_brain.llm import BrainLLM
from herdr_brain.tools import BrainTools

from tests.conftest import SETTINGS_KWARGS
from tests.test_llm import make_brain, text_response, tool_call_response
from tests.test_pairing_api import LOOPBACK, FakeTools

STOP_AGENT = "stop_agent"


class FakeStopTools(FakeTools):
    """Server double: replies deliver, stops succeed, both recorded."""

    stops: list = []
    stop_outcome = {"outcome": "stopped", "keys": ["esc"], "detail": ""}

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        type(self).replies.append((pane_id, text))
        return {
            "outcome": "delivered", "pane_id": pane_id, "status": "done",
            "detail": "",
            "receipt": {"outcome": "effective", "detail": "", "text_hint": ""},
        }

    def stop_pane(self, pane_id):
        type(self).stops.append(pane_id)
        return dict(type(self).stop_outcome, pane_id=pane_id)


def _stop_client(tmp_path, monkeypatch) -> TestClient:
    FakeStopTools.replies = []
    FakeStopTools.stops = []
    monkeypatch.setattr(server_module, "BrainTools", FakeStopTools)
    cfg = Settings(**{
        **SETTINGS_KWARGS,
        "audio_dir": str(tmp_path / "audio"),
        "state_dir": str(tmp_path / "state"),
    })
    return TestClient(server_module.create_app(settings=cfg), base_url=LOOPBACK)


# ── the gate: a spoken stop never dispatches ──────────────────────────────────


def test_stop_agent_call_freezes_a_gate_and_touches_no_keys(settings, make_stub):
    stub = make_stub()
    llm, _ = make_brain(
        settings, stub,
        responses=[
            tool_call_response("c1", STOP_AGENT, {}),
            text_response("Voy a detener al agente, confirma para pararlo."),
        ],
    )
    result = llm.ask("para al agente", session_id="s1")

    # AC1 for stops: no key reached the pane; the gate holds the target.
    assert stub.key_calls == []
    assert stub.prompt_calls == []
    gate = llm._approval_store.current("s1")
    assert gate is not None and gate.state == PROPOSED
    assert gate.tool == STOP_AGENT
    assert gate.action.pane_id == "w1:p9"
    assert gate.action.text == ""  # an interrupt carries no prompt
    assert result["approval"].gate_id == gate.gate_id
    # The tool message keeps the model honest about the pending stop.
    tool_messages = [
        m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
    ]
    assert "NOT" in tool_messages[0]["content"]
    assert "never say it was stopped" in tool_messages[0]["content"]


def test_stop_agent_without_a_target_or_store_stays_blocked(settings, make_stub):
    stub = make_stub(agents=[])
    llm, _ = make_brain(
        settings, stub,
        responses=[
            tool_call_response("c1", STOP_AGENT, {}),
            text_response("no hay agente"),
        ],
    )
    llm.ask("para al agente")
    assert llm._approval_store.current("default") is None
    assert stub.key_calls == []

    orphan, _ = make_brain(
        settings, make_stub(),
        responses=[
            tool_call_response("c1", STOP_AGENT, {}),
            text_response("sin almacén"),
        ],
    )
    orphan._approval_store = None
    orphan.ask("para al agente")
    assert stub.key_calls == []


def test_the_stop_gate_is_wired_but_not_yet_advertised(settings, make_stub):
    """The voice boundary, honestly: the gate and replay are wired and
    tested, while the TOOLS_SCHEMA advertisement stays out until the
    baseline tests pinning the advertised name set (outside this
    change's surfaces) are updated with it."""
    from herdr_brain.tools import TOOLS_SCHEMA

    names = {tool["function"]["name"] for tool in TOOLS_SCHEMA}
    assert "stop_agent" not in names
    llm, _ = make_brain(settings, make_stub(), responses=[text_response("ok")])
    llm.ask("hola")
    policy = llm._client.create_kwargs[0]["messages"][0]["content"]
    assert "stop_agent" not in policy
    # But a scripted stop_agent call (what advertising will enable) is
    # gated exactly like a send — pinned by the tests above.


def test_a_stop_gate_renders_as_a_send_card_with_its_marker_text():
    from herdr_brain.server import approval_payload

    store = ApprovalGateStore()
    gate = store.propose(
        "s1", text="", pane_id="w1:p2", agent="claude", tool=STOP_AGENT
    )
    payload = approval_payload(gate, timeout_s=60, now=gate.created_at)
    # The approval flow's second variant is the SEND card: the stop gate
    # rides it with a fixed marker, so the PWA needs no new variant.
    assert payload["tool"] == STOP_AGENT
    assert payload["pane_id"] == "w1:p2"
    assert payload["text"] == "⏹ detener el turno en curso"
    assert payload["expires_in_s"] == 60


# ── the approve replay runs exactly one interrupt ─────────────────────────────


def test_approving_a_stop_gate_runs_the_interrupt_once(tmp_path, monkeypatch):
    from tests.test_server import FakeLLM

    FakeStopTools.replies = []
    FakeStopTools.stops = []
    monkeypatch.setattr(server_module, "BrainTools", FakeStopTools)
    cfg = Settings(**{
        **SETTINGS_KWARGS,
        "audio_dir": str(tmp_path / "audio"),
        "state_dir": str(tmp_path / "state"),
    })
    app = server_module.create_app(
        settings=cfg,
        llm_factory=lambda c, t: FakeLLM(result={
            "answer": "Turno detenido.",
            "pane_id": "w1:p2", "agent": "claude", "session_id": "default",
        }),
    )
    client = TestClient(app, base_url=LOOPBACK)
    gate = app.state.approval_store.propose(
        "default", text="", pane_id="w1:p2", agent="claude", tool=STOP_AGENT
    )

    resp = client.post(f"/approval/{gate.gate_id}/approve")

    assert resp.status_code == 200
    assert FakeStopTools.stops == ["w1:p2"]
    assert resp.json()["approval"] is None  # resolved


# ── access: the new routes are writes under enforcement ───────────────────────


def test_new_write_routes_refuse_the_unpaired_client(tmp_path, monkeypatch):
    client = _stop_client(tmp_path, monkeypatch)
    state_dir = tmp_path / "state"
    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    status, outcome = store.claim(minted["code"], "phone")
    assert status == "ok"

    refused = [
        client.post("/pane/stop", json={"pane_id": "w1:p2"}),
        client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"}),
        client.post("/pane/receipt/rct-x/submit"),
        client.post("/pane/receipt/rct-x/discard"),
    ]
    for resp in refused:
        assert resp.status_code == 403
        assert resp.json()["detail"] == "dispositivo no emparejado"
    assert FakeStopTools.stops == []
    assert FakeStopTools.replies == []

    token = outcome["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/pane/stop", json={"pane_id": "w1:p2"}, headers=headers
    ).status_code == 200
    assert FakeStopTools.stops == ["w1:p2"]


def test_receipts_listing_is_gated_as_a_read(tmp_path, monkeypatch):
    client = _stop_client(tmp_path, monkeypatch)
    state_dir = tmp_path / "state"
    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    _, outcome = store.claim(minted["code"], "phone")

    resp = client.get("/pane/receipts")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "dispositivo no emparejado"

    auth = {"Authorization": f"Bearer {outcome['token']}"}
    assert client.get("/pane/receipts", headers=auth).status_code == 200
