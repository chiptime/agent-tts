"""Pairing endpoints and enforcement through the HTTP surface
(Panel parity F1, doc 01): POST /pair, GET /devices,
POST /devices/revoke, write-gated routes acquiring a bearer
requirement exactly when the registry is non-empty, and the ntfy
action route keeping HERDR_BRAIN_APPROVAL_TOKEN alone.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings

from tests.conftest import SETTINGS_KWARGS

LOOPBACK = "http://127.0.0.1:8741"
TSNET = "https://node.tail1234.ts.net:8443"


class FakeTools:
    """BrainTools double: /pane/reply always delivers."""

    def __init__(self, cfg):
        self.last_active = None

    def reply_to_pane(self, pane_id, text):
        return {"outcome": "delivered", "pane_id": pane_id, "status": "done", "detail": ""}

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return []


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    # NOT created: PairingStore._write_atomic mkdir's it 0700 on the
    # first write, and a pre-created dir would keep the default umask.
    return tmp_path / "state"


def make_client(state_dir, **overrides) -> TestClient:
    """App over a tmp state dir with the REAL default gate on.

    The TestClient base URL is loopback, so the fail-closed defaults
    (loopback + ``*.ts.net`` hosts, Origin required on remote writes)
    are exercised exactly as production answers them. SETTINGS_KWARGS
    rides the allow-any-host opt-out for the legacy suite; these tests
    pin the gate itself, so they opt back in.
    """
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
    }
    kwargs.update(overrides)
    cfg = Settings(**kwargs)
    app = server_module.create_app(settings=cfg)
    return TestClient(app, base_url=LOOPBACK)


@pytest.fixture
def client(state_dir, monkeypatch) -> TestClient:
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    return make_client(state_dir)


def pair_device(client, state_dir, label="phone"):
    """Mints a code out-of-band (the operator's shell) and claims it.

    The real-clock store mirrors the real CLI: the SERVER's store also
    reads real time, so a fake-clock pending would read as expired."""
    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    resp = client.post("/pair", json={"code": minted["code"], "label": label})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ── AC1/S7: empty registry = today's behavior ────────────────────────────────


def test_no_registry_write_routes_behave_exactly_as_today(state_dir, monkeypatch):
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    client = make_client(state_dir)
    assert client.get("/health").status_code == 200
    assert client.get("/herd").json() == []
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "pane_id": "w1:p2"}


def test_ts_net_host_keeps_working_with_no_new_configuration(state_dir, monkeypatch):
    """S7: the phone's https://<node>.ts.net:8443 access needs no config."""
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
    }
    app = server_module.create_app(settings=Settings(**kwargs))
    client = TestClient(app, base_url=TSNET)
    assert client.get("/health").status_code == 200
    # A same-origin write passes with no bearer while nothing is paired.
    resp = client.post(
        "/pane/reply",
        json={"pane_id": "w1:p2", "text": "go"},
        headers={"Origin": TSNET},
    )
    assert resp.status_code == 200


# ── AC2: the write requirement turns on with the first pairing ───────────────


def test_write_without_bearer_is_refused_once_a_device_is_paired(client, state_dir):
    token = pair_device(client, state_dir)
    assert token

    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "dispositivo no emparejado"


def test_write_with_a_paired_device_token_passes_like_today(client, state_dir):
    token = pair_device(client, state_dir)

    resp = client.post(
        "/pane/reply",
        json={"pane_id": "w1:p2", "text": "go"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "pane_id": "w1:p2"}


def test_write_with_a_wrong_bearer_is_refused_with_the_same_text(client, state_dir):
    pair_device(client, state_dir)
    resp = client.post(
        "/pane/reply",
        json={"pane_id": "w1:p2", "text": "go"},
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "dispositivo no emparejado"


# ── AC3: reads always work; revocation lands without a restart ───────────────


def test_valid_token_read_works_and_revoked_device_keeps_read_loses_write(
    client, state_dir
):
    phone_token = pair_device(client, state_dir, label="phone")
    pair_device(client, state_dir, label="tablet")  # keeps enforcement on
    auth = {"Authorization": f"Bearer {phone_token}"}
    assert client.get("/herd", headers=auth).status_code == 200

    # The CLI (another process/store) revokes the phone; no restart.
    cli = pairing.PairingStore(state_dir)
    assert cli.revoke("phone") is True
    assert cli.enforced() is True  # the tablet still enforces

    assert client.get("/herd", headers=auth).status_code == 200  # read survives
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"}, headers=auth)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "dispositivo no emparejado"


# ── AC4: the pairing exchange ────────────────────────────────────────────────


def test_pair_exchange_is_single_use_and_bound_to_the_code(client, state_dir):
    store = pairing.PairingStore(state_dir)
    minted = store.mint()

    ok = client.post("/pair", json={"code": minted["code"].lower(), "label": "móvil"})
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {"token", "label"}
    assert body["label"] == "móvil"
    assert body["token"]

    again = client.post("/pair", json={"code": minted["code"], "label": "otro"})
    assert again.status_code == 400
    assert again.json()["detail"] == "no-pending"


def test_pair_five_wrong_codes_destroy_the_pending(client, state_dir):
    store = pairing.PairingStore(state_dir)
    store.mint()
    reasons = []
    for _ in range(5):
        resp = client.post("/pair", json={"code": "WRONGWRONG", "label": "x"})
        assert resp.status_code == 400
        reasons.append(resp.json()["detail"])
    assert reasons == ["bad-code"] * 4 + ["exhausted"]
    # The pending is destroyed with the counter: the file is gone.
    assert not (state_dir / pairing.PENDING_FILENAME).exists()
    # Even a correct code is now useless (nothing to compare against).
    resp = client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    assert resp.json()["detail"] == "no-pending"


def test_pair_expired_code_is_refused(client, state_dir):
    # A pending minted eleven minutes ago: written directly with a
    # backdated clock so the server's real clock sees it expired.
    import time as time_module

    stale = pairing.new_pending(
        "AB23CD45", time_module.time() - pairing.CODE_TTL_S - 1
    )
    state_dir.joinpath(pairing.PENDING_FILENAME).write_text(
        json.dumps(stale), encoding="utf-8"
    )
    resp = client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "expired"


def test_pair_failure_body_is_the_bare_reason_word(client, state_dir):
    pairing.PairingStore(state_dir).mint()
    resp = client.post("/pair", json={"code": "WRONGWRONG", "label": "x"})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "bad-code"}


def test_pair_without_pending(client):
    resp = client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no-pending"


def test_pair_bad_label_is_a_validation_error(client, state_dir):
    pairing.PairingStore(state_dir).mint()
    resp = client.post("/pair", json={"code": "AB23CD45", "label": ""})
    assert resp.status_code == 422


# ── GET /devices (read; no hashes on the wire) ───────────────────────────────


def test_devices_read_is_open_and_never_exposes_token_hashes(client, state_dir):
    empty = client.get("/devices")
    assert empty.status_code == 200
    assert empty.json() == {"enforced": False, "current": None, "devices": []}

    token = pair_device(client, state_dir)
    listed = client.get("/devices", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["enforced"] is True
    assert body["current"] == "phone"
    assert [d["label"] for d in body["devices"]] == ["phone"]
    assert "tokenHash" not in json.dumps(body)


# ── POST /devices/revoke (write-gated; self-revoke allowed) ──────────────────


def test_revoke_is_write_gated_and_allows_self_revocation(client, state_dir):
    token = pair_device(client, state_dir)
    auth = {"Authorization": f"Bearer {token}"}

    unauthenticated = client.post("/devices/revoke", json={"label": "phone"})
    assert unauthenticated.status_code == 403
    assert unauthenticated.json()["detail"] == "dispositivo no emparejado"

    revoked = client.post("/devices/revoke", json={"label": "phone"}, headers=auth)
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["enforced"] is False  # the last device left: requirement off
    assert body["devices"] == []
    assert body["current"] is None


def test_revoke_unknown_label_is_404(client, state_dir):
    token = pair_device(client, state_dir)
    resp = client.post(
        "/devices/revoke", json={"label": "ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── AC8: the ntfy action route keeps its own token only ─────────────────────


def test_approval_action_keeps_the_approval_token_and_refuses_device_bearers(
    state_dir, monkeypatch
):
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
        "approval_token": "ntfy-secret",
    }
    app = server_module.create_app(settings=Settings(**kwargs))
    client = TestClient(app, base_url=LOOPBACK)

    token = pair_device(client, state_dir)

    # A device bearer must NOT open the action route.
    resp = client.post(
        "/approval/gate1/action?decision=approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401

    # The approval token (query form, as ntfy sends it) still works.
    ok = client.post("/approval/gate1/action?decision=approve&token=ntfy-secret")
    assert ok.status_code == 404  # past auth; unknown gate → 404, never 401/403


def test_approval_action_without_any_token_is_still_401(state_dir):
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
        "approval_token": "ntfy-secret",
    }
    app = server_module.create_app(settings=Settings(**kwargs))
    client = TestClient(app, base_url=LOOPBACK)
    resp = client.post("/approval/gate1/action?decision=approve")
    assert resp.status_code == 401


# ── AC10 through the surface: corrupt files never authorize writes ──────────


@pytest.mark.parametrize("raw", ["{", "", '{"devices":[{"label":"incomplete"}]}'])
def test_s1_corrupt_registry_never_opens_writes(client, state_dir, raw):
    """T4 B1 (doc 01 AC10): once a device is paired, a corrupt registry
    never lifts the write requirement — enforcement is OFF only for an
    absent file or a WELL-FORMED zero-device registry. Unparsable,
    truncated or incomplete-entry files keep every write refused with
    nobody authorized, and the service stays up."""
    pair_device(client, state_dir)  # enforcement is on
    (state_dir / pairing.DEVICES_FILENAME).write_text(raw, encoding="utf-8")

    assert client.get("/health").status_code == 200  # the service stays up
    assert client.get("/devices").json()["enforced"] is True
    resp = client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "dispositivo no emparejado"
    # Even a paired device's bearer can no longer be matched: corruption
    # authorizes nobody.
    resp = client.post(
        "/pane/reply",
        json={"pane_id": "w1:p2", "text": "go"},
        headers={"Authorization": "Bearer whatever"},
    )
    assert resp.status_code == 403


def test_valid_empty_registry_keeps_today_open_behavior(client, state_dir):
    """Revoking the last device keeps today's semantics: the well-formed
    zero-device registry (what `revoke` writes) is the one corrupt shape
    that does NOT keep enforcement on."""
    token = pair_device(client, state_dir)
    assert client.post("/devices/revoke", json={"label": "phone"},
                       headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/devices").json()["enforced"] is False
    assert client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"}).status_code == 200


def test_pair_recovers_a_corrupt_registry_over_http(client, state_dir):
    """T4 B1: `pair` via the CLI must still recover — a claim rewrites a
    valid registry, so a corrupt file cannot lock the operator out."""
    pair_device(client, state_dir)
    (state_dir / pairing.DEVICES_FILENAME).write_text("{", encoding="utf-8")
    assert client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"}).status_code == 403

    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    resp = client.post("/pair", json={"code": minted["code"], "label": "tablet"})
    assert resp.status_code == 200
    auth = {"Authorization": f"Bearer {resp.json()['token']}"}
    assert client.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"},
                       headers=auth).status_code == 200


def test_corrupt_pending_file_still_refuses_cleanly(client, state_dir):
    (state_dir / pairing.PENDING_FILENAME).write_text("nope", encoding="utf-8")
    assert client.get("/health").status_code == 200
    resp = client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no-pending"


# ── file hygiene surfaced through the endpoints ──────────────────────────────


def test_pairing_files_are_owner_only_after_endpoint_claims(client, state_dir):
    pair_device(client, state_dir)
    # The credential files are owner-only, and the state dir is 0700
    # even when audio/ created it first with the default umask (T4 B5:
    # a pre-existing dir is retightened, not just created 0700).
    assert stat.S_IMODE((state_dir / pairing.DEVICES_FILENAME).stat().st_mode) == 0o600
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700


def test_state_dir_setting_respected(state_dir, tmp_path):
    custom = tmp_path / "custom-state"
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(tmp_path / "a"),
        "allow_any_host": False,
        "state_dir": str(custom),
    }
    app = server_module.create_app(settings=Settings(**kwargs))
    client = TestClient(app, base_url=LOOPBACK)
    store = pairing.PairingStore(custom)
    minted = store.mint()
    assert client.post("/pair", json={"code": minted["code"], "label": "x"}).status_code == 200
    assert (custom / pairing.DEVICES_FILENAME).is_file()
