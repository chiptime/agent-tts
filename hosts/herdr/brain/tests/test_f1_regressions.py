"""T5 correction-batch regressions (T4 verifier blockers B1-B5 + S7 recheck).

One home for the behaviors an independent verifier broke on 43a96a2,
each pinned through the same public surface the verifier used: the HTTP
API, the real `python -m herdr_brain.pairing_cli` entry point, and the
state files on disk. The unit-level stories live next to their modules
(test_pairing.py, test_access_gate.py, test_audit.py); this file proves
the composed guarantees the verifier actually probed.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings

from tests.conftest import SETTINGS_KWARGS
from tests.test_pairing_api import (
    LOOPBACK,
    TSNET,
    FakeTools,
    make_client,
    pair_device,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def client(state_dir: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    return make_client(state_dir)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── B2/S2: POST /pair follows the write Origin/CSRF rule ─────────────────────


def test_s2_pair_requires_origin_from_remote_host(client, state_dir):
    """A valid code claimed from `Host: <node>.ts.net:8443` with NO
    Origin must not mint a token: /pair hands out write credentials, so
    it takes the write-level Host/Origin gate (still no device bearer —
    the bootstrap cannot require what it exists to mint)."""
    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    pending_before = _digest(state_dir / pairing.PENDING_FILENAME)

    resp = client.post(
        "/pair",
        json={"code": minted["code"], "label": "phone"},
        headers={"Host": "node.tail1234.ts.net:8443"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "origin requerido"
    # The pairing files are untouched: no attempt consumed, no registry.
    assert _digest(state_dir / pairing.PENDING_FILENAME) == pending_before
    assert not (state_dir / pairing.DEVICES_FILENAME).exists()

    # The same claim from the phone's real shape — same-origin Origin —
    # still works (browsers always send Origin on POST).
    ok = client.post(
        "/pair",
        json={"code": minted["code"], "label": "phone"},
        headers={"Host": "node.tail1234.ts.net:8443", "Origin": TSNET},
    )
    assert ok.status_code == 200
    assert ok.json()["label"] == "phone"


# ── B5/S1+S5: the state dir is 0700 even when audio/ nested first ───────────


def test_s1_s5_nested_state_directory_is_private(client, state_dir):
    """audio_dir=<state_dir>/audio created first leaves the state dir at
    the default umask (0775); the first pairing write must retighten it
    to 0700 with 0600 files (B5)."""
    audio = state_dir / "audio"
    audio.mkdir(parents=True)
    state_dir.chmod(0o775)

    pair_device(client, state_dir)

    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_dir / pairing.DEVICES_FILENAME).stat().st_mode) == 0o600


# ── B4/S1: the real CLI rejects an invalid explicit label ───────────────────


@pytest.mark.parametrize("label", ["", "x" * 49])
def test_s1_cli_rejects_invalid_explicit_label(label, tmp_path, monkeypatch):
    """`python -m herdr_brain.pairing_cli pair --label <invalid>` exits
    non-zero with a clear stderr message and writes nothing (B4)."""
    state = tmp_path / "state"
    env = {**os.environ, "HERDR_BRAIN_STATE_DIR": str(state)}
    # The suite's env may point the module elsewhere; the child process
    # must resolve the same herdr_brain package this test imports.
    package_root = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, "-m", "herdr_brain.pairing_cli", "pair", "--label", label],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
    )

    assert proc.returncode != 0
    assert "--label" in proc.stderr
    assert not state.exists()  # nothing written — not even the state dir


def test_s1_cli_accepts_a_valid_explicit_label(tmp_path):
    """The flip side of B4: a usable --label still pins the claim."""
    state = tmp_path / "state"
    env = {**os.environ, "HERDR_BRAIN_STATE_DIR": str(state)}
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, "-m", "herdr_brain.pairing_cli", "pair", "--label", "móvil"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
    )

    assert proc.returncode == 0
    pending = state / pairing.PENDING_FILENAME
    assert pending.is_file()
    import json

    assert json.loads(pending.read_text(encoding="utf-8"))["label"] == "móvil"


# ── S1/S3: every write route refuses cleanly while paired, touching nothing ─


WRITE_ROUTES = [
    ("POST", "/pane/reply"),
    ("POST", "/pane/keys"),
    ("POST", "/pane/permission/reply"),
    ("POST", "/approval/g1/approve"),
    ("POST", "/approval/g1/reject"),
    ("POST", "/approval/g1/resolve"),
    ("PATCH", "/approval/g1"),
    ("POST", "/devices/revoke"),
    ("POST", "/ask"),
    ("POST", "/transcribe"),
    ("POST", "/reset"),
]


@pytest.mark.parametrize(("method", "path"), WRITE_ROUTES)
def test_s1_every_write_route_refuses_not_paired_and_touches_nothing(
    client, state_dir, method, path
):
    """With a device paired, every write route (doc 01 §4) answers 403
    «dispositivo no emparejado» to an unpaired caller and leaves the
    state files byte-identical — the refusal happens in the middleware,
    before any handler or audit line runs."""
    pair_device(client, state_dir)
    registry_before = _digest(state_dir / pairing.DEVICES_FILENAME)
    audit_before = (
        _digest(state_dir / "audit.log")
        if (state_dir / "audit.log").exists()
        else None
    )

    resp = client.request(method, path, json={})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "dispositivo no emparejado"
    assert _digest(state_dir / pairing.DEVICES_FILENAME) == registry_before
    assert (
        _digest(state_dir / "audit.log") if (state_dir / "audit.log").exists() else None
    ) == audit_before


# ── S7 recheck (amended): ntfy action from ts.net WITHOUT Origin ────────────


def test_s7_ntfy_action_from_ts_net_host_without_origin_still_works(state_dir):
    """`POST /approval/{id}/action` keeps HERDR_BRAIN_APPROVAL_TOKEN
    alone: ntfy sends no Origin and no device bearer, so the amended S7
    exempts it from the write Origin rule — it must reach the handler
    (404 for an unknown gate), never the access gate's 403."""
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
        "approval_token": "ntfy-secret",
    }
    app = server_module.create_app(settings=Settings(**kwargs))
    client = TestClient(app, base_url=TSNET)

    # Enforcement on — but enrolled out-of-band (the operator's CLI),
    # because POST /pair from this Host without an Origin is itself the
    # B2 refusal.
    store = pairing.PairingStore(state_dir)
    minted = store.mint()
    assert store.claim(minted["code"], "phone")[0] == "ok"

    resp = client.post(
        "/approval/gate1/action?decision=approve&token=ntfy-secret",
        headers={"Host": "node.tail1234.ts.net:8443"},
    )
    assert resp.status_code == 404  # past the gate; unknown gate → 404
