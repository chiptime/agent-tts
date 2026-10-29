"""HTTP wiring of brain messages (S13): operator send + pane inbox.

The state machine is covered by test_walky_*; these pins hold the
SERVER contract: ``POST /msg/send`` is a write (device gate + audit via
the service), refuses with the walky text as a 422, and
``GET /msg/inbox?pane_id=`` reads (and marks read) one pane's inbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings
from herdr_brain.walky import WalkyService

from tests.conftest import SETTINGS_KWARGS, ACTIVE_AGENT
from tests.test_pairing_api import LOOPBACK

NOT_PAIRED = "dispositivo no emparejado"

ADDRESS = f"{ACTIVE_AGENT.pane_id}#{ACTIVE_AGENT.session_value}"


class StubMux:
    """The only surface the routes drive: list_agents, read_screen, pane_run."""

    def __init__(self, agents):
        self._agents = agents
        self.run_lines = []

    def list_agents(self):
        return list(self._agents)

    def read_screen(self, pane_id, n_lines=None):
        return ""

    def pane_run(self, pane_id, line):
        self.run_lines.append((pane_id, line))


def idle_agent():
    from herdr_brain.herdr import AgentInfo
    from tests.conftest import ACTIVE_AGENT as base

    return AgentInfo(
        pane_id=base.pane_id,
        agent=base.agent,
        status="idle",
        session_kind=base.session_kind,
        session_value=base.session_value,
        cwd=base.cwd,
        title=base.title,
        focused=True,
    )


def make_service(state_dir: Path, mux) -> WalkyService:
    return WalkyService(state_dir, mux=mux)


def make_client(state_dir, msg_service) -> TestClient:
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
    }
    app = server_module.create_app(
        settings=Settings(**kwargs), msg_service=msg_service
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


class TestSend:
    def test_operator_send_delivers_and_audits_the_device(self, state_dir):
        mux = StubMux([idle_agent()])
        client = make_client(state_dir, make_service(state_dir, mux))
        token = pair_device(client, state_dir)

        resp = client.post(
            "/msg/send",
            json={"address": ADDRESS, "body": "¿Cómo va el despliegue?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "typed"
        assert body["id"]
        assert len(mux.run_lines) == 1
        assert mux.run_lines[0][0] == ACTIVE_AGENT.pane_id

        lines = read_audit(state_dir)
        assert any(
            line["action"] == "msg.send"
            and line["device"] == "phone"
            and line["pane_id"] == ACTIVE_AGENT.pane_id
            for line in lines
        )

    def test_send_without_bearer_is_refused_once_paired(self, state_dir):
        mux = StubMux([idle_agent()])
        client = make_client(state_dir, make_service(state_dir, mux))
        pair_device(client, state_dir)

        resp = client.post("/msg/send", json={"address": ADDRESS, "body": "hola"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == NOT_PAIRED

    def test_a_refusal_answers_422_with_its_text(self, state_dir):
        mux = StubMux([idle_agent()])
        client = make_client(state_dir, make_service(state_dir, mux))

        resp = client.post(
            "/msg/send", json={"address": "w9:p9#deadbeef", "body": "hola"}
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "unknown pane" in detail

    def test_a_bad_address_shape_is_a_422(self, state_dir):
        client = make_client(state_dir, make_service(state_dir, StubMux([])))
        resp = client.post("/msg/send", json={"address": "no-hash", "body": "hola"})
        assert resp.status_code == 422

    def test_an_empty_body_is_a_422(self, state_dir):
        client = make_client(state_dir, make_service(state_dir, StubMux([])))
        resp = client.post("/msg/send", json={"address": ADDRESS, "body": "  "})
        assert resp.status_code == 422


class TestInbox:
    def test_inbox_lists_the_pane_messages_and_marks_them_read(self, state_dir):
        mux = StubMux([idle_agent()])
        service = make_service(state_dir, mux)
        client = make_client(state_dir, service)

        # A held message lands in the inbox: the receiver stays busy.
        busy_mux = StubMux([])
        service.operator_send("phone", ADDRESS, "mensaje en espera")
        sent = client.post("/msg/send", json={"address": ADDRESS, "body": "segundo"})
        assert sent.status_code == 200

        resp = client.get("/msg/inbox", params={"pane_id": ACTIVE_AGENT.pane_id})
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert len(messages) == 2
        assert all(m["to_pane"] == ACTIVE_AGENT.pane_id for m in messages)

        # The inbox read marked them: a second call is empty.
        assert client.get(
            "/msg/inbox", params={"pane_id": ACTIVE_AGENT.pane_id}
        ).json()["messages"] == []

    def test_inbox_of_an_unknown_pane_is_empty_not_an_error(self, state_dir):
        client = make_client(state_dir, make_service(state_dir, StubMux([])))
        resp = client.get("/msg/inbox", params={"pane_id": "w1:none"})
        assert resp.status_code == 200
        assert resp.json()["messages"] == []
