"""Reads require the device token once pairing is enforced (S11).

The reference panel's ADR 0086 behaviour, in brain's own shape: pairing
the first device gates READS as well as writes. Exempt: /health, the
PWA shell and static assets, POST /pair (bootstrap), the ntfy action
route (keeps HERDR_BRAIN_APPROVAL_TOKEN) and the speech transport
(job-token bound). EventSource and <audio> cannot send headers, so
exactly GET /events and GET /audio/{name} also accept the credential
via a ``device_token`` query parameter — never writes. Plus the S11
hardening: per-device token expiry («dispositivo caducado») and a
/pair rate limit (429 + Retry-After).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain import pairing
from herdr_brain.config import Settings

from tests.conftest import SETTINGS_KWARGS
from tests.test_pairing_api import LOOPBACK, FakeTools

NOT_PAIRED = "dispositivo no emparejado"
EXPIRED = "dispositivo caducado"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def make_client(state_dir, monkeypatch=None, **app_overrides) -> TestClient:
    """App over a tmp state dir with the real default gate on (see
    tests/test_pairing_api.py::make_client); supports create_app seams
    (sse heartbeat/limit) for finite /events streams."""
    if monkeypatch is not None:
        monkeypatch.setattr(server_module, "BrainTools", FakeTools)
    kwargs = {
        **SETTINGS_KWARGS,
        "audio_dir": str(state_dir / "audio"),
        "state_dir": str(state_dir),
        "allow_any_host": False,
    }
    app = server_module.create_app(settings=Settings(**kwargs), **app_overrides)
    return TestClient(app, base_url=LOOPBACK)


@pytest.fixture
def client(state_dir, monkeypatch) -> TestClient:
    return make_client(state_dir, monkeypatch)


def pair_device(client, state_dir, label="phone", expires_in_s=None):
    """Enrol out-of-band (the operator's CLI) and return the token."""
    store = pairing.PairingStore(state_dir)
    minted = store.mint(expires_in_s=expires_in_s)
    status, outcome = store.claim(minted["code"], label)
    assert status == "ok", outcome
    return outcome["token"]


# ── S9/AC1 preservation: empty registry changes nothing ──────────────────────


def test_empty_registry_reads_and_writes_stay_open(state_dir, monkeypatch):
    """The read gate, like the write gate, arms only with the first
    pairing: with no registry file every sampled route behaves exactly
    as before (S9). /events needs the finite-stream client (an ungated
    SSE stream never ends)."""
    finite = make_client(state_dir, monkeypatch, sse_heartbeat_s=0.05, sse_stream_limit=1)
    with finite.stream("GET", "/events") as resp:
        assert resp.status_code == 200
    assert finite.get("/health").status_code == 200
    assert finite.get("/herd").status_code == 200
    assert finite.get("/devices").status_code == 200
    assert finite.post("/pane/reply", json={"pane_id": "w1:p2", "text": "go"}).status_code == 200
    # A stray device_token query parameter is inert with no registry.
    assert finite.get("/herd?device_token=whatever").status_code == 200


# ── S11.1: reads gated once enforced ──────────────────────────────────────────


READ_ROUTES = [
    "/herd",
    "/view",
    "/state",
    "/conversation",
    "/history",
    "/call-history",
    "/call/context",
    "/approval/current",
    "/devices",
    "/screen?pane_id=w1:p2",
]


@pytest.mark.parametrize("path", READ_ROUTES)
def test_reads_refuse_the_unpaired_client_with_the_pairing_403(client, state_dir, path):
    token = pair_device(client, state_dir)
    assert token

    resp = client.get(path)
    assert resp.status_code == 403
    assert resp.json()["detail"] == NOT_PAIRED


def test_read_with_a_valid_device_token_passes(client, state_dir):
    token = pair_device(client, state_dir)
    resp = client.get("/herd", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_devices_get_is_gated_and_the_403_is_the_unpaired_signal(client, state_dir):
    """GET /devices requires the token once enforced; the not-paired 403
    itself tells an unpaired client it is unpaired (the PWA opens the
    pairing flow on it), and a paired client still sees the registry."""
    token = pair_device(client, state_dir, label="phone")

    unpaired = client.get("/devices")
    assert unpaired.status_code == 403
    assert unpaired.json()["detail"] == NOT_PAIRED

    paired = client.get("/devices", headers={"Authorization": f"Bearer {token}"})
    assert paired.status_code == 200
    body = paired.json()
    assert body["enforced"] is True
    assert body["current"] == "phone"


def test_pwa_shell_health_and_static_assets_stay_open(client, state_dir):
    pair_device(client, state_dir)
    for path in (
        "/health",
        "/",
        "/app.js",
        "/pairing.js",
        "/sw.js",
        "/manifest.webmanifest",
        "/icon.svg",
        "/mermaid.min.js",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path


def test_ntfy_action_and_speech_routes_stay_on_their_own_credentials(
    state_dir, monkeypatch
):
    """POST /approval/{id}/action keeps HERDR_BRAIN_APPROVAL_TOKEN (a
    device bearer is not an alternative), and /speech/{id}/* stay
    job-token bound: neither adopts the device gate."""
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

    action = client.post(
        "/approval/gate1/action?decision=approve&token=ntfy-secret"
    )
    assert action.status_code == 404  # past auth and the gate; unknown gate

    nxt = client.get("/speech/job1/next")
    assert nxt.status_code in (401, 404)  # job-token bound, never 403 not-paired


def test_post_pair_stays_the_bootstrap(client, state_dir):
    pair_device(client, state_dir)
    resp = client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    # The bootstrap is not device-gated: it answers its own failure
    # vocabulary, never the pairing 403.
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no-pending"


def test_sse_stream_refuses_the_unpaired_client(state_dir, monkeypatch):
    client = make_client(state_dir, monkeypatch, sse_heartbeat_s=0.05, sse_stream_limit=2)
    pair_device(client, state_dir)
    resp = client.get("/events")
    assert resp.status_code == 403
    assert resp.json()["detail"] == NOT_PAIRED


# ── S11.2: the query token for headerless transports ─────────────────────────


def test_events_accepts_the_token_by_query_parameter(state_dir, monkeypatch):
    client = make_client(state_dir, monkeypatch, sse_heartbeat_s=0.05, sse_stream_limit=2)
    token = pair_device(client, state_dir)
    with client.stream("GET", f"/events?device_token={token}") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


def test_audio_accepts_the_token_by_query_parameter(client, state_dir):
    token = pair_device(client, state_dir)
    (state_dir / "audio").mkdir(parents=True, exist_ok=True)
    (state_dir / "audio" / "clip.mp3").write_bytes(b"ID3-stub")

    ok = client.get(f"/audio/clip.mp3?device_token={token}")
    assert ok.status_code == 200
    assert ok.content == b"ID3-stub"

    wrong = client.get("/audio/clip.mp3?device_token=not-a-token")
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == NOT_PAIRED


def test_the_query_token_is_accepted_nowhere_else(client, state_dir):
    """The parameter is a concession to EventSource and <audio> — two
    GET transports that cannot send headers — so it opens neither other
    reads nor any write."""
    token = pair_device(client, state_dir)

    read = client.get("/herd?device_token=" + token)
    assert read.status_code == 403
    assert read.json()["detail"] == NOT_PAIRED

    write = client.post(
        "/pane/reply?device_token=" + token,
        json={"pane_id": "w1:p2", "text": "go"},
    )
    assert write.status_code == 403
    assert write.json()["detail"] == NOT_PAIRED


def test_a_header_bearer_beats_the_query_token_on_the_same_route(client, state_dir):
    token = pair_device(client, state_dir)
    (state_dir / "audio").mkdir(parents=True, exist_ok=True)
    (state_dir / "audio" / "clip.mp3").write_bytes(b"ID3-stub")
    resp = client.get(
        "/audio/clip.mp3?device_token=not-a-token",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ── S11.3: per-device token expiry ────────────────────────────────────────────


def test_an_expired_token_is_refused_with_its_own_403_text(client, state_dir):
    token = pair_device(client, state_dir, expires_in_s=3600)

    # Within its lifetime the token works.
    assert client.get("/herd", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    # …then the registry says it expired.
    registry = json.loads((state_dir / pairing.DEVICES_FILENAME).read_text(encoding="utf-8"))
    registry["devices"][0]["expiresAt"] = time.time() - 1
    (state_dir / pairing.DEVICES_FILENAME).write_text(json.dumps(registry), encoding="utf-8")

    read = client.get("/herd", headers={"Authorization": f"Bearer {token}"})
    assert read.status_code == 403
    assert read.json()["detail"] == EXPIRED

    write = client.post(
        "/pane/reply", json={"pane_id": "w1:p2", "text": "go"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert write.status_code == 403
    assert write.json()["detail"] == EXPIRED


def test_expiry_refusal_distinguishes_a_wrong_token_from_an_expired_one(client, state_dir):
    pair_device(client, state_dir, expires_in_s=3600)
    # Expire the only device, then prove the texts differ by credential:
    registry = json.loads((state_dir / pairing.DEVICES_FILENAME).read_text(encoding="utf-8"))
    registry["devices"][0]["expiresAt"] = time.time() - 1
    (state_dir / pairing.DEVICES_FILENAME).write_text(json.dumps(registry), encoding="utf-8")

    wrong = client.get("/herd", headers={"Authorization": "Bearer not-a-token"})
    assert wrong.json()["detail"] == NOT_PAIRED


def test_pair_with_expires_enrols_a_device_that_runs_out(client, state_dir):
    store = pairing.PairingStore(state_dir, now=lambda: 1_000.0)
    minted = store.mint(expires_in_s=120.0)
    assert minted["expiresInS"] == 120.0
    status, outcome = store.claim(minted["code"], "phone")
    assert status == "ok"
    registry = json.loads((state_dir / pairing.DEVICES_FILENAME).read_text(encoding="utf-8"))
    assert registry["devices"][0]["expiresAt"] == pytest.approx(1_120.0)


def test_devices_wire_and_an_f1_registry_without_expiry(client, state_dir):
    """Backward compatibility (S11.3): an F1 registry entry carries no
    expiresAt and never expires; the /devices wire carries the field so
    the PWA and `devices list` can show it."""
    token = pair_device(client, state_dir)
    assert "expiresAt" not in json.loads(
        (state_dir / pairing.DEVICES_FILENAME).read_text(encoding="utf-8")
    )["devices"][0]

    body = client.get("/devices", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["devices"][0]["expiresAt"] is None
    assert client.get("/herd", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_a_corrupt_expires_at_never_extends_a_credential(client, state_dir):
    """Fail-closed like every other registry corruption: an expiresAt
    that is not a timestamp drops the entry, it does not read as
    "never expires"."""
    token = pair_device(client, state_dir)
    registry = json.loads((state_dir / pairing.DEVICES_FILENAME).read_text(encoding="utf-8"))
    registry["devices"][0]["expiresAt"] = "forever"
    (state_dir / pairing.DEVICES_FILENAME).write_text(json.dumps(registry), encoding="utf-8")

    resp = client.get("/herd", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_set_expiry_and_clear_expiry_manage_the_deadline_without_restart(
    client, state_dir
):
    token = pair_device(client, state_dir)
    cli = pairing.PairingStore(state_dir)

    assert cli.set_expiry("phone", 1800.0) is True
    body = client.get("/devices", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["devices"][0]["expiresAt"] == pytest.approx(time.time() + 1800.0, abs=5)
    assert client.get("/herd", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    assert cli.clear_expiry("phone") is True
    body = client.get("/devices", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["devices"][0]["expiresAt"] is None

    assert cli.set_expiry("ghost", 60.0) is False
    assert cli.clear_expiry("ghost") is False


# ── S11.4: the /pair rate limit ───────────────────────────────────────────────


def test_eleventh_claim_attempt_within_a_minute_is_a_429(client, state_dir):
    store = pairing.PairingStore(state_dir)
    store.mint()

    for _ in range(10):
        resp = client.post("/pair", json={"code": "WRONGWRONG", "label": "x"})
        assert resp.status_code == 400  # within the budget: the claim itself fails

    limited = client.post("/pair", json={"code": "WRONGWRONG", "label": "x"})
    assert limited.status_code == 429
    retry_after = int(limited.headers["Retry-After"])
    assert 0 < retry_after <= 60


def test_the_rate_limit_leaves_the_pairing_files_unchanged(client, state_dir):
    store = pairing.PairingStore(state_dir)
    store.mint()

    def snapshot():
        pending = state_dir / pairing.PENDING_FILENAME
        registry = state_dir / pairing.DEVICES_FILENAME
        return (
            pending.read_bytes() if pending.exists() else None,
            registry.read_bytes() if registry.exists() else None,
        )

    for _ in range(10):
        client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    # Whatever the ten in-budget attempts did to the pending is the
    # claim counter's own doing; the 429 itself must touch nothing.
    before = snapshot()

    limited = client.post("/pair", json={"code": "AB23CD45", "label": "x"})

    assert limited.status_code == 429
    assert snapshot() == before


def test_the_rate_limit_is_in_process_state_not_a_file(client, state_dir):
    """The budget lives in the app (bounded, per client address — pinned
    at the limiter level in tests/test_pairing.py), never in the pairing
    files, and a fresh app instance starts clean."""
    store = pairing.PairingStore(state_dir)
    store.mint()
    for _ in range(10):
        client.post("/pair", json={"code": "AB23CD45", "label": "x"})
    assert client.post("/pair", json={"code": "AB23CD45", "label": "x"}).status_code == 429

    second = make_client(state_dir)
    assert second.post("/pair", json={"code": "AB23CD45", "label": "x"}).status_code == 400


# ── S11.5: audit for expiry management ────────────────────────────────────────


def run_cli(argv, monkeypatch, state_dir):
    from herdr_brain import pairing_cli

    monkeypatch.setenv("HERDR_BRAIN_STATE_DIR", str(state_dir))
    return pairing_cli.main(argv)


def test_set_and_clear_expiry_write_audit_lines(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    store = pairing.PairingStore(state, now=lambda: 1_000.0)
    minted = store.mint()
    store.claim(minted["code"], "phone")

    assert run_cli(["devices", "set-expiry", "phone", "30d"], monkeypatch, state) == 0
    capsys.readouterr()
    assert run_cli(["devices", "clear-expiry", "phone"], monkeypatch, state) == 0

    lines = [
        json.loads(line)
        for line in (state / "audit.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [line["action"] for line in lines] == ["device.set_expiry", "device.clear_expiry"]
    assert lines[0]["detail"]["label"] == "phone"
    assert lines[0]["detail"]["expires_in_s"] == 30 * 24 * 3600


def test_cli_devices_list_shows_the_expiry(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    # Real clock: the CLI's `list` judges CADUCADO vs caduca against
    # wall time, so the enrolment and the deadline must be real too.
    store = pairing.PairingStore(state)
    minted = store.mint()
    store.claim(minted["code"], "phone")
    store.set_expiry("phone", 12 * 3600)

    assert run_cli(["devices", "list"], monkeypatch, state) == 0
    out = capsys.readouterr().out
    assert "phone" in out
    assert "caduca" in out


def test_cli_devices_list_keeps_the_legacy_no_expiry_stdout(
    tmp_path, monkeypatch, capsys
):
    """B7: a row without expiresAt (every pre-S11 registry, and every
    device whose deadline was cleared) keeps the exact pre-S11 stdout —
    no trailing column — so scripts pinning the old output keep
    working; stderr stays empty and the exit code stays 0."""
    state = tmp_path / "state"
    state.mkdir()
    (state / pairing.DEVICES_FILENAME).write_text(
        json.dumps({"devices": [{
            "label": "phone",
            "tokenHash": "a" * 64,
            "createdAt": 1_700_000_000,
            "lastSeenAt": 1_700_000_000,
        }]}),
        encoding="utf-8",
    )

    assert run_cli(["devices", "list"], monkeypatch, state) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "phone  created 2023-11-14T22:13:20Z  last seen 2023-11-14T22:13:20Z\n"
    )
    assert captured.err == ""


def test_cli_devices_list_shows_expiry_only_where_it_exists(
    tmp_path, monkeypatch, capsys
):
    """B7 differential: the explicit expiry controls keep functioning —
    a row WITH a deadline shows it, a no-expiry row in the SAME listing
    keeps the bare legacy line (the CLI judges caduca vs CADUCADO
    against wall time, so the stamps are real-clock)."""
    import datetime

    def iso(epoch: float) -> str:
        return (
            datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    state = tmp_path / "state"
    state.mkdir()
    now = time.time()
    (state / pairing.DEVICES_FILENAME).write_text(
        json.dumps({"devices": [
            {"label": "bounded", "tokenHash": "b" * 64, "createdAt": now,
             "lastSeenAt": now, "expiresAt": now + 3600},
            {"label": "forever", "tokenHash": "f" * 64, "createdAt": now,
             "lastSeenAt": now},
        ]}),
        encoding="utf-8",
    )

    assert run_cli(["devices", "list"], monkeypatch, state) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"bounded  created {iso(now)}  last seen {iso(now)}  caduca {iso(now + 3600)}",
        f"forever  created {iso(now)}  last seen {iso(now)}",
    ]


def test_cli_pair_expires_and_set_expiry_parse_durations(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    assert run_cli(["pair", "--expires", "12h"], monkeypatch, state) == 0
    out = capsys.readouterr().out
    assert "12h" in out or "caduca" in out
    pending = json.loads((state / pairing.PENDING_FILENAME).read_text(encoding="utf-8"))
    assert pending["expiresInS"] == 12 * 3600

    assert run_cli(["pair", "--expires", "bogus"], monkeypatch, tmp_path / "s2") == 2


# ── S11.2 (logs): the query token never reaches logs we control ──────────────


def test_the_access_log_redacts_the_query_device_token():
    from herdr_brain.server import redact_query_tokens

    line = 'GET "/events?device_token=TOPSECRET&x=1 HTTP/1.1" 200'
    redacted = redact_query_tokens(line)
    assert "TOPSECRET" not in redacted
    assert "device_token=" in redacted
    assert "x=1" in redacted
    assert redact_query_tokens("GET /health HTTP/1.1") == "GET /health HTTP/1.1"


def test_the_redaction_filter_rewrites_uvicorn_access_records():
    from herdr_brain.server import RedactQueryToken

    record = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", "GET", "/audio/x.mp3?device_token=TOP", "1.1", 200),
        exc_info=None,
    )
    assert RedactQueryToken().filter(record) is True
    assert "TOP" not in record.getMessage()
    assert "device_token=" in record.getMessage()


def test_the_redaction_covers_every_accepted_spelling():
    """B2: canonical pair-wise redaction — the key is percent-decoded
    exactly as parse_qsl decodes it, so every spelling the SERVER
    accepts (device%5Ftoken, repeated keys, the ntfy action ``token``)
    is caught, while non-accepted spellings and nonsensitive arguments
    survive byte-identical."""
    from herdr_brain.server import redact_query_tokens

    # Percent-encoded key — accepted by parse_qsl, so it must go.
    line = 'GET "/events?device%5Ftoken=SECRET1 HTTP/1.1" 200'
    redacted = redact_query_tokens(line)
    assert "SECRET1" not in redacted
    assert "device%5Ftoken=⟨redactado⟩" in redacted

    # Repetitions: every occurrence, not just the last one the server
    # would read (dict(parse_qsl) keeps the final value).
    line = "/events?device_token=A&device_token=B&x=1"
    redacted = redact_query_tokens(line)
    assert "A" not in redacted and "B" not in redacted
    assert redacted.count("device_token=⟨redactado⟩") == 2
    assert "x=1" in redacted

    # The ntfy action route's existing token query credential.
    redacted = redact_query_tokens("/approval/g1/action?decision=approve&token=ntfy-secret")
    assert "ntfy-secret" not in redacted
    assert "decision=approve" in redacted
    assert "token=⟨redactado⟩" in redacted

    # Mixed encodings in one line; other arguments untouched.
    redacted = redact_query_tokens("/events?device%5Ftoken=S1&device_token=S2&seq=7")
    assert "S1" not in redacted and "S2" not in redacted
    assert "seq=7" in redacted

    # A double-encoded key is NOT an accepted spelling (it cannot
    # authenticate), and a non-credential key named like one is data.
    untouched = "/events?device%255Ftoken=not-accepted&tokens=many"
    assert redact_query_tokens(untouched) == untouched
