"""Public CLI/PWA wiring uses the same owner-delivery service."""

import io

from fastapi.testclient import TestClient

import herdr_brain.server as server
from herdr_brain.config import Settings
from herdr_brain.walky import cli
from tests.conftest import SETTINGS_KWARGS
from tests.test_msg_routes import pair_device
from tests.test_pairing_api import LOOPBACK
from tests.test_walky_federation import fleet
from tests.test_walky_store_cli import env


def test_cli_factory_discovers_and_sends_to_remote_host(fleet):
    svc, mux, _, _, clock, _ = fleet
    state = svc["beta"].gateway._state_dir
    output, error = io.StringIO(), io.StringIO()
    assert cli.main(
        ["send", "gamma:w1:p1#gamma-session", "from CLI"],
        env=env("w1:p1", state), mux=mux["beta"], clock=clock,
        gateway=svc["beta"].gateway, stdout=output, stderr=error,
    ) == 0
    assert "held" in output.getvalue()
    assert not error.getvalue()
    assert svc["gamma"].inbox("w1:p1")[0].body == "from CLI"


def test_pwa_factory_uses_crew_service_and_keeps_pairing_gate(fleet, monkeypatch):
    svc, mux, _, _, _, _ = fleet
    state = svc["alpha"].gateway._state_dir
    monkeypatch.setattr(server, "create_mux", lambda cfg: mux["alpha"])
    app = server.create_app(settings=Settings(**{
        **SETTINGS_KWARGS, "state_dir": str(state), "audio_dir": str(state / "audio"),
    }), crew=svc["alpha"].gateway)
    client = TestClient(app, base_url=LOOPBACK)
    token = pair_device(client, state)
    payload = {"address": "gamma:w1:p1#gamma-session", "body": "from PWA"}
    assert client.post("/msg/send", json=payload).status_code == 403
    response = client.post("/msg/send", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "held"
    assert svc["gamma"].inbox("w1:p1")[0].body == "from PWA"
    assert set(app.state.crew_handlers).issuperset({"msg_send", "msg_status", "msg_cancel", "msg_who"})
