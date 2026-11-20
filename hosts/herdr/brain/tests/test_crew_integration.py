"""Crew integration (S7, doc 10): the daemon-side wiring over the core.

Three invariants pinned here through the REAL app:

* SOLO (the default) is byte-identical: no gateway, no listener, the
  exact single-host route bodies every installed PWA already consumes.
* A LEAD routes: /herd merges the peers (host tags, unreachable peers
  explicit), pane ops on ``<host>:<pane>`` ids proxy through
  CrewClient, and an unknown host prefix is an error — never a local
  fallback that would strip the prefix.
* Remote gates forward to their owner through GateRouter; the local
  store never holds them.

No network: every peer speaks through an injected in-process transport
(the crew README's testing stance).
"""

from __future__ import annotations

import json
import ssl
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import herdr_brain.server as server_module
from herdr_brain.config import Settings
from herdr_brain.crew.client import CrewHttpResponse
from herdr_brain.crew.roster import CrewRoster
from herdr_brain.crew_runtime import (
    DEFAULT_CREW_PORT,
    CrewGateway,
    listener_kwargs,
    listener_should_start,
)
from herdr_brain.server import create_app

from tests.conftest import SETTINGS_KWARGS

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
LEAD_PEM = (FIXTURES / "lead.pem").read_text(encoding="utf-8")


class StubTransport:
    """In-process wire: canned responses per path, everything recorded."""

    def __init__(self, responses=None, fail_all=False):
        self.responses = responses or {}
        self.fail_all = fail_all
        self.requests: list[dict] = []

    def request(self, method, url, *, headers, json_body=None, timeout_s=None):
        from urllib.parse import urlparse

        self.requests.append({
            "method": method,
            "url": url,
            "body": json_body,
            "bearer": headers.get("authorization", ""),
        })
        if self.fail_all:
            raise OSError("peer is down")
        for prefix, response in self.responses.items():
            if prefix in url:
                return CrewHttpResponse(*response)
        return CrewHttpResponse(404, {"detail": "not found"})


class CrewTools:
    """Local tools double: the local herd plus recorded writes."""

    def __init__(self):
        self.key_sends: list = []
        self.replies: list = []

    def status_payload(self):
        return {"active": False}

    def herd(self):
        return [{"pane_id": "w1:p9", "agent": "opencode", "status": "working"}]

    def pane_info(self, pane_id):
        if pane_id == "w1:p9":
            return {"pane_id": "w1:p9", "agent": "opencode",
                    "session_id": None, "cwd": None}
        return None

    def agent_view(self, pane_id=None):
        return {"local": True, "pane_id": pane_id}

    def reply_to_pane(self, pane_id, text):
        self.replies.append((pane_id, text))
        return {"outcome": "delivered", "pane_id": pane_id, "detail": ""}

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        return self.reply_to_pane(pane_id, text)

    def send_keys_to_pane(self, pane_id, keys):
        self.key_sends.append((pane_id, keys))
        return {"outcome": "sent", "pane_id": pane_id, "detail": ""}


def write_lead_roster(state_dir: Path, peers=("host-b",)) -> None:
    roster = CrewRoster.load(state_dir, now=lambda: 1_000.0)
    roster.init_lead("host-a", "crew-secret-1", now=1.0)
    for peer in peers:
        roster.record_peer(peer, f"https://{peer}.example:8747", PEER_PEM, now=1.0)
    roster.persist()
    (state_dir / "crew-secret").write_text("crew-secret-1\n")


def make_client(tmp_path, monkeypatch, *, crew=None, tools=None) -> TestClient:
    tools = tools or CrewTools()
    monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
    settings = Settings(**{
        **SETTINGS_KWARGS,
        "state_dir": str(tmp_path / "state"),
        "audio_dir": str(tmp_path / "audio"),
    })
    app = create_app(settings=settings, crew=crew)
    return TestClient(app)


def make_gateway(state_dir: Path, transports: dict) -> CrewGateway:
    def factory(peer, url, cert_pem):
        return transports[peer]

    return CrewGateway(
        state_dir, secret_provider=lambda: "crew-secret-1", transport_factory=factory,
        now=lambda: 1_000.0,
    )


# ── solo: zero crew, byte-identical routes ──────────────────────────────────


class TestSoloMode:
    def test_no_roster_means_no_gateway_and_no_listener(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch)
        assert client.app.state.crew is None
        assert getattr(client.app.state, "crew_app", None) is None

    def test_solo_routes_keep_their_exact_bodies(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch)
        # /herd is the plain list, byte-identical to the pre-crew shape.
        assert client.get("/herd").json() == [
            {"pane_id": "w1:p9", "agent": "opencode", "status": "working"}
        ]
        assert client.get("/view", params={"pane_id": "w1:p9"}).json() == {
            "local": True, "pane_id": "w1:p9"
        }
        reply = client.post("/pane/reply", json={"pane_id": "w1:p9", "text": "hola"})
        assert reply.status_code == 200
        assert reply.json() == {"ok": True, "pane_id": "w1:p9"}
        assert client.get("/approval/current").json() == {"approval": None}

    def test_solo_never_builds_listener_kwargs(self, tmp_path):
        roster = CrewRoster.load(tmp_path)  # absent file → solo
        assert listener_should_start(roster) is False
        assert listener_kwargs(roster, tmp_path) is None

    def test_crew_settings_default_without_effect(self):
        settings = Settings(**SETTINGS_KWARGS)
        assert settings.crew_port == DEFAULT_CREW_PORT == 8747


# ── lead: the merged herd ───────────────────────────────────────────────────


class TestLeadHerd:
    def test_remote_panes_gain_host_and_unreachable_peers_are_explicit(
        self, tmp_path, monkeypatch
    ):
        state = tmp_path / "state"
        write_lead_roster(state, peers=("host-b", "host-dead"))
        transport = StubTransport(responses={
            "/crew/v1/herd": (200, [
                {"pane_id": "w1:p1", "agent": "claude", "status": "blocked"},
            ]),
        })
        dead = StubTransport(fail_all=True)
        client = make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {
                "host-b": transport, "host-dead": dead,
            })
        )
        body = client.get("/herd").json()
        # Local first, single-host shape preserved; remote tagged.
        assert body["panes"][0] == {
            "pane_id": "w1:p9", "agent": "opencode", "status": "working"
        }
        assert body["panes"][1]["host"] == "host-b"
        assert body["panes"][1]["pane_id"] == "w1:p1"
        # The dead peer is NAMED, never silently gone.
        assert {"name": "host-b", "state": "fresh"} in body["peers"]
        assert {"name": "host-dead", "state": "unreachable"} in body["peers"]

    def test_peer_failure_never_500s_the_herd(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        write_lead_roster(state, peers=("host-b",))
        client = make_client(
            tmp_path, monkeypatch,
            crew=make_gateway(state, {"host-b": StubTransport(fail_all=True)}),
        )
        assert client.get("/herd").status_code == 200


# ── lead: pane ops route through the crew client ────────────────────────────


class TestRemotePaneOps:
    def _lead_client(self, tmp_path, monkeypatch, transport):
        state = tmp_path / "state"
        write_lead_roster(state)
        return make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
        )

    def test_view_proxies_and_strips_the_host_prefix(self, tmp_path, monkeypatch):
        transport = StubTransport(responses={
            "/crew/v1/view": (200, {"status": {"active": True}, "remote": True}),
        })
        client = self._lead_client(tmp_path, monkeypatch, transport)
        resp = client.get("/view", params={"pane_id": "host-b:w1:p1"})
        assert resp.status_code == 200
        assert resp.json()["remote"] is True
        # The wire pane id is the peer's LOCAL id — no host prefix.
        assert transport.requests[0]["url"].endswith(
            "/crew/v1/view?pane_id=w1%3Ap1"
        )

    def test_reply_proxies_the_write(self, tmp_path, monkeypatch):
        transport = StubTransport(responses={
            "/reply": (200, {"ok": True, "pane_id": "w1:p1"}),
        })
        client = self._lead_client(tmp_path, monkeypatch, transport)
        resp = client.post(
            "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
        )
        assert resp.status_code == 200
        assert transport.requests[0]["body"] == {"text": "sigue"}
        audit = [
            json.loads(line)
            for line in (tmp_path / "state" / "audit.log").read_text().splitlines()
        ]
        forwarded = [
            line for line in audit
            if line["action"] == "pane.reply" and line["detail"].get("crew")
        ]
        assert forwarded and forwarded[0]["pane_id"] == "host-b:w1:p1"

    def test_keys_proxies_with_the_allowlist_shape(self, tmp_path, monkeypatch):
        transport = StubTransport(responses={
            "/keys": (200, {"ok": True, "pane_id": "w1:p1"}),
        })
        client = self._lead_client(tmp_path, monkeypatch, transport)
        resp = client.post(
            "/pane/keys", json={"pane_id": "host-b:w1:p1", "keys": ["enter"]}
        )
        assert resp.status_code == 200
        assert transport.requests[0]["body"] == {"keys": ["enter"]}

    def test_peer_refusal_status_travels_back(self, tmp_path, monkeypatch):
        transport = StubTransport(responses={
            "/reply": (409, {"detail": "agent_blocked"}),
        })
        client = self._lead_client(tmp_path, monkeypatch, transport)
        resp = client.post(
            "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "agent_blocked"

    def test_unreachable_peer_is_a_503(self, tmp_path, monkeypatch):
        client = self._lead_client(tmp_path, monkeypatch, StubTransport(fail_all=True))
        resp = client.post(
            "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
        )
        assert resp.status_code == 503

    def test_unknown_host_is_an_error_never_a_local_fallback(
        self, tmp_path, monkeypatch
    ):
        tools = CrewTools()
        state = tmp_path / "state"
        write_lead_roster(state)
        client = make_client(
            tmp_path, monkeypatch, tools=tools,
            crew=make_gateway(state, {"host-b": StubTransport()}),
        )
        resp = client.post(
            "/pane/reply", json={"pane_id": "ghost:w1:p1", "text": "hola"}
        )
        assert resp.status_code == 422
        assert "ghost" in resp.json()["detail"]
        # Nothing was written locally either: the prefix was never
        # stripped onto a local pane.
        assert tools.replies == []

    def test_host_shaped_local_id_stays_local(self, tmp_path, monkeypatch):
        tools = CrewTools()
        state = tmp_path / "state"
        write_lead_roster(state)
        transport = StubTransport()
        client = make_client(
            tmp_path, monkeypatch, tools=tools,
            crew=make_gateway(state, {"host-b": transport}),
        )
        # "w1" looks like a member name but is not enrolled; the full id
        # IS a real local pane → the local code path serves it.
        resp = client.post("/pane/reply", json={"pane_id": "w1:p9", "text": "hola"})
        assert resp.status_code == 200
        assert tools.replies == [("w1:p9", "hola")]
        assert transport.requests == []


# ── lead: remote gates ──────────────────────────────────────────────────────


class TestRemoteGates:
    def _lead_with_gate(self, tmp_path, monkeypatch, action_response=(200, {"ok": True})):
        state = tmp_path / "state"
        write_lead_roster(state)
        transport = StubTransport(responses={
            "/crew/v1/approval/current": (200, {"approval": {
                "gate_id": "g-remote-1", "state": "proposed", "question": "¿Envío?",
            }}),
            "/crew/v1/approval/g-remote-1/action": action_response,
        })
        client = make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
        )
        return client, transport

    def test_current_merges_the_remote_gate_with_its_host(
        self, tmp_path, monkeypatch
    ):
        client, _ = self._lead_with_gate(tmp_path, monkeypatch)
        approval = client.get("/approval/current").json()["approval"]
        assert approval is not None
        assert approval["gate_id"] == "g-remote-1"
        assert approval["host"] == "host-b"

    def test_approve_forwards_and_never_touches_the_local_store(
        self, tmp_path, monkeypatch
    ):
        client, transport = self._lead_with_gate(tmp_path, monkeypatch)
        client.get("/approval/current")  # learns + indexes the remote gate
        resp = client.post("/approval/g-remote-1/approve")
        assert resp.status_code == 200
        forwarded = [
            r for r in transport.requests
            if r["method"] == "POST" and "action" in r["url"]
        ]
        assert forwarded, "the decision reached the owner"
        # The local store never held the gate.
        assert client.app.state.approval_store.get("g-remote-1") is None
        audit = (tmp_path / "state" / "audit.log").read_text()
        assert "approval.forward" in audit

    def test_owners_404_is_the_answer(self, tmp_path, monkeypatch):
        client, _ = self._lead_with_gate(
            tmp_path, monkeypatch, action_response=(404, {"detail": "gate dead"})
        )
        client.get("/approval/current")
        resp = client.post("/approval/g-remote-1/approve")
        assert resp.status_code == 404

    def test_local_gates_still_resolve_locally_with_crew_active(
        self, tmp_path, monkeypatch
    ):
        client, _ = self._lead_with_gate(tmp_path, monkeypatch)
        # An id the index never saw: local handling, byte-identical 404.
        resp = client.post("/approval/purely-local/approve")
        assert resp.status_code == 404
        assert resp.json()["detail"] == (
            "approval gate not found or no longer active"
        )


# ── the peer-side surface ───────────────────────────────────────────────────


class TestPeerSurface:
    def _peer_client(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        roster = CrewRoster.load(state, now=lambda: 1_000.0)
        roster.join_as_peer(
            "host-b", "host-a", "https://a.example:8747", LEAD_PEM,
            "crew-secret-1", now=1.0,
        )
        roster.persist()
        tools = CrewTools()
        monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
        settings = Settings(**{
            **SETTINGS_KWARGS,
            "state_dir": str(state), "audio_dir": str(tmp_path / "audio"),
        })
        gateway = CrewGateway(
            state, secret_provider=lambda: "crew-secret-1",
            transport_factory=lambda *a: StubTransport(), now=lambda: 1_000.0,
        )
        app = create_app(settings=settings, crew=gateway)
        return TestClient(app), tools

    def test_peer_builds_the_crew_surface_and_handlers(self, tmp_path, monkeypatch):
        client, tools = self._peer_client(tmp_path, monkeypatch)
        assert client.app.state.crew is not None
        assert client.app.state.crew_app is not None
        handlers = client.app.state.crew_handlers
        # The handlers map straight onto the local machinery.
        assert handlers["herd"]() == tools.herd()
        outcome = handlers["reply"]("w1:p9", "sigue")
        assert outcome == (200, {"ok": True, "pane_id": "w1:p9"})
        assert tools.replies == [("w1:p9", "sigue")]

    def test_crew_app_answers_the_uniform_401_without_factors(
        self, tmp_path, monkeypatch
    ):
        from fastapi.testclient import TestClient as TC

        client, _ = self._peer_client(tmp_path, monkeypatch)
        crew_client = TC(client.app.state.crew_app)
        resp = crew_client.get("/crew/v1/herd")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "unauthorized"}

    def test_enrol_route_exists_without_the_two_factors(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient as TC

        client, _ = self._peer_client(tmp_path, monkeypatch)
        crew_client = TC(client.app.state.crew_app)
        # A malformed enrol payload is a 400/401-UNIFORM answer, never a
        # 404: the route exists and only enrolment may answer it.
        resp = crew_client.post(
            "/crew/v1/enrol",
            json={"name": "", "url": "", "cert_pem": ""},
        )
        assert resp.status_code in (400, 401)
        assert crew_client.get("/crew/v1/no-such-route").status_code == 401


# ── the listener decision ───────────────────────────────────────────────────


class TestListenerDecision:
    def test_peer_requires_the_client_certificate(self, tmp_path):
        state = tmp_path / "state"
        roster = CrewRoster.load(state, now=lambda: 1_000.0)
        roster.join_as_peer(
            "host-b", "host-a", "https://a.example:8747", LEAD_PEM,
            "crew-secret-1", now=1.0,
        )
        roster.persist()
        # Materialize the lead's enrolled PEM so a CA bundle exists.
        certs = state / "crew-certs"
        certs.mkdir(parents=True, exist_ok=True)
        (certs / "host-a.pem").write_text(LEAD_PEM)
        loaded = CrewRoster.load(state)
        assert listener_should_start(loaded) is True
        kwargs = listener_kwargs(loaded, state, host="127.0.0.1", port=8747)
        assert kwargs is not None
        assert kwargs["port"] == 8747
        assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        assert kwargs["ssl_ca_certs"].endswith("ca-bundle.pem")

    def test_lead_keeps_the_enrolment_window_open(self, tmp_path):
        state = tmp_path / "state"
        write_lead_roster(state, peers=())
        loaded = CrewRoster.load(state)
        kwargs = listener_kwargs(loaded, state)
        assert kwargs is not None
        assert kwargs["ssl_cert_reqs"] == ssl.CERT_OPTIONAL

    def test_listener_never_starts_for_a_solo_roster(self, tmp_path):
        roster = CrewRoster.load(tmp_path / "empty-state")
        assert listener_kwargs(roster, tmp_path / "empty-state") is None


# ── outgoing crew calls present this host's crew certificate ────────────────


class TestOutgoingClientCertificate:
    def _capture(self, monkeypatch) -> list:
        import herdr_brain.crew.client as client_module

        calls: list = []

        def fake_context(cert_pem, client_cert=None, client_key=None):
            calls.append((client_cert, client_key))
            return object()

        monkeypatch.setattr(client_module, "pinned_client_context", fake_context)
        monkeypatch.setattr(client_module, "UrllibTransport", lambda context: object())
        return calls

    def test_client_for_loads_the_host_certificate_when_present(self, tmp_path, monkeypatch):
        calls = self._capture(monkeypatch)
        state = tmp_path / "state"
        state.mkdir()
        write_lead_roster(state)
        tls_dir = state / "crew-tls"
        tls_dir.mkdir()
        (tls_dir / "crew.crt").write_text("cert")
        (tls_dir / "crew.key").write_text("key")

        CrewGateway(state, secret_provider=lambda: "crew-secret-1").client_for("host-b")

        assert calls == [(tls_dir / "crew.crt", tls_dir / "crew.key")]

    def test_client_for_without_host_certificate_keeps_the_pin_only(self, tmp_path, monkeypatch):
        calls = self._capture(monkeypatch)
        state = tmp_path / "state"
        state.mkdir()
        write_lead_roster(state)

        CrewGateway(state, secret_provider=lambda: "crew-secret-1").client_for("host-b")

        assert calls == [(None, None)]  # honest: the peer answers the uniform 401


# ── peer audit trail (T3/S3): the owner brain records its own writes ────────


class PeerAuditTools(CrewTools):
    """CrewTools with the dialog surface and switchable write outcomes, so
    the handler-level audit policy can be pinned for every tool outcome
    the (status, payload) mapping knows."""

    def __init__(self, reply_outcome="delivered", keys_outcome="sent",
                 dialog_answer=None):
        super().__init__()
        self.reply_outcome = reply_outcome
        self.keys_outcome = keys_outcome
        self.dialog_answer = dialog_answer
        self.dialog_calls: list = []

    def reply_to_pane(self, pane_id, text):
        self.replies.append((pane_id, text))
        return {"outcome": self.reply_outcome, "pane_id": pane_id, "detail": ""}

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        return self.reply_to_pane(pane_id, text)

    def send_keys_to_pane(self, pane_id, keys):
        self.key_sends.append((pane_id, keys))
        return {"outcome": self.keys_outcome, "pane_id": pane_id, "detail": ""}

    def answer_structured_dialog(self, pane_id, fingerprint, option=None,
                                 choices=None, text=None, model_name=None,
                                 provider_name=None):
        self.dialog_calls.append({
            "pane_id": pane_id, "fingerprint": fingerprint, "option": option,
            "choices": choices, "text": text,
        })
        # Mirrors tools.answer_structured_dialog: refusals carry a reason,
        # answers carry the resolved label.
        result = dict(self.dialog_answer) if self.dialog_answer else {
            "outcome": "answered", "label": "Sí",
        }
        result.update({"pane_id": pane_id, "option": option, "choices": choices})
        return result


class TestPeerAuditTrail:
    """S3 on the peer surface: every crew write handler records exactly one
    host-local audit line, refusals included, with `crew` marking the
    channel and no `device` attribute (the crew link carries no paired
    device bearer — the same convention _h_approval_action already uses).
    Handler-level admission validation (blank text, empty keys) mirrors the
    HTTP routes' pydantic body validation and records nothing."""

    def _peer_app(self, tmp_path, monkeypatch, tools):
        state = tmp_path / "state"
        roster = CrewRoster.load(state, now=lambda: 1_000.0)
        roster.join_as_peer(
            "host-b", "host-a", "https://a.example:8747", LEAD_PEM,
            "crew-secret-1", now=1.0,
        )
        roster.persist()
        monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
        settings = Settings(**{
            **SETTINGS_KWARGS,
            "state_dir": str(state), "audio_dir": str(tmp_path / "audio"),
        })
        gateway = CrewGateway(
            state, secret_provider=lambda: "crew-secret-1",
            transport_factory=lambda *a: StubTransport(), now=lambda: 1_000.0,
        )
        return create_app(settings=settings, crew=gateway), state

    def _lines(self, state: Path) -> list:
        path = state / "audit.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_reply_records_one_crew_line_without_device(self, tmp_path, monkeypatch):
        app, state = self._peer_app(tmp_path, monkeypatch, PeerAuditTools())
        outcome = app.state.crew_handlers["reply"]("w1:p9", "sigue")

        assert outcome == (200, {"ok": True, "pane_id": "w1:p9"})
        lines = self._lines(state)
        assert len(lines) == 1
        line = lines[0]
        assert line["action"] == "pane.reply"
        assert line["pane_id"] == "w1:p9"
        assert "device" not in line  # crew link carries no device identity
        assert line["detail"] == {"outcome": "delivered", "text": "sigue", "crew": True}

    @pytest.mark.parametrize("outcome,status", [
        ("unknown_pane", 404), ("blocked", 409), ("error", 502),
    ])
    def test_reply_refusals_record_their_outcome(
        self, tmp_path, monkeypatch, outcome, status
    ):
        app, state = self._peer_app(
            tmp_path, monkeypatch, PeerAuditTools(reply_outcome=outcome)
        )
        result = app.state.crew_handlers["reply"]("w1:p9", "intenta igual")

        assert result == (status, {"detail": result[1]["detail"]})
        lines = self._lines(state)
        assert len(lines) == 1
        assert lines[0]["action"] == "pane.reply"
        assert lines[0]["detail"] == {
            "outcome": outcome, "text": "intenta igual", "crew": True,
        }

    def test_blank_reply_records_nothing(self, tmp_path, monkeypatch):
        """Admission failure: mirrors the HTTP route's body validation, which
        is unaudited today — the failed-attempt policy starts at the tool."""
        app, state = self._peer_app(tmp_path, monkeypatch, PeerAuditTools())
        assert app.state.crew_handlers["reply"]("w1:p9", "   ") == (
            400, {"detail": "text is required"},
        )
        assert self._lines(state) == []

    def test_keys_records_one_crew_line(self, tmp_path, monkeypatch):
        app, state = self._peer_app(tmp_path, monkeypatch, PeerAuditTools())
        result = app.state.crew_handlers["keys"]("w1:p9", ["enter"])

        assert result == {"ok": True, "pane_id": "w1:p9"}
        lines = self._lines(state)
        assert len(lines) == 1
        assert lines[0]["action"] == "pane.keys"
        assert lines[0]["detail"] == {
            "outcome": "sent", "keys": ["enter"], "crew": True,
        }

    def test_keys_failure_records_its_outcome(self, tmp_path, monkeypatch):
        app, state = self._peer_app(
            tmp_path, monkeypatch, PeerAuditTools(keys_outcome="error")
        )
        result = app.state.crew_handlers["keys"]("w1:p9", ["enter"])

        assert result == (502, {"detail": "send-keys failed"})
        lines = self._lines(state)
        assert len(lines) == 1
        assert lines[0]["detail"]["outcome"] == "error"

    def test_empty_keys_record_nothing(self, tmp_path, monkeypatch):
        app, state = self._peer_app(tmp_path, monkeypatch, PeerAuditTools())
        assert app.state.crew_handlers["keys"]("w1:p9", []) == (
            400, {"detail": "keys must be a non-empty array"},
        )
        assert self._lines(state) == []

    def test_dialog_answer_records_the_answered_shape(self, tmp_path, monkeypatch):
        app, state = self._peer_app(tmp_path, monkeypatch, PeerAuditTools())
        result = app.state.crew_handlers["dialog_answer"](
            "w1:p9", fingerprint="fp1", option=0,
        )

        assert result["ok"] is True
        assert result["label"] == "Sí"
        lines = self._lines(state)
        assert len(lines) == 1
        assert lines[0]["action"] == "pane.dialog.answer"
        assert lines[0]["detail"] == {
            "outcome": "answered", "reason": "", "option": 0,
            "choices": None, "fingerprint": "fp1", "crew": True,
        }

    def test_dialog_answer_refusal_records_its_reason(
        self, tmp_path, monkeypatch,
    ):
        app, state = self._peer_app(
            tmp_path, monkeypatch,
            PeerAuditTools(dialog_answer={"outcome": "refused", "reason": "changed"}),
        )
        result = app.state.crew_handlers["dialog_answer"](
            "w1:p9", fingerprint="fp1", option=0,
        )

        assert result == (409, {"detail": "changed"})
        lines = self._lines(state)
        assert len(lines) == 1
        assert lines[0]["detail"]["outcome"] == "refused"
        assert lines[0]["detail"]["reason"] == "changed"


class TestLeadCrewCallFailureAudit:
    """T3-fu/U6: a failed proxied WRITE attempt records one `crew.call`
    line on the lead (S3's failed-attempt policy), while successes keep
    belonging to the routes' own crew:true lines and READ proxies record
    nothing. The refusal detail is the peer's own HTTP error detail —
    the same string the route already surfaces; unreachable failures
    carry no transport text in the trail."""

    def _lead_client(self, tmp_path, monkeypatch, transport):
        state = tmp_path / "state"
        write_lead_roster(state)
        return make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
        )

    def _lines(self, tmp_path) -> list:
        path = tmp_path / "state" / "audit.log"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]

    def test_unreachable_reply_records_one_crew_call_line(
        self, tmp_path, monkeypatch,
    ):
        client = self._lead_client(tmp_path, monkeypatch, StubTransport(fail_all=True))
        resp = client.post(
            "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
        )

        assert resp.status_code == 503
        lines = self._lines(tmp_path)
        assert len(lines) == 1
        line = lines[0]
        assert line["action"] == "crew.call"
        assert line["detail"]["peer"] == "host-b"
        assert line["detail"]["op"] == "reply"
        assert line["detail"]["outcome"] == "unreachable"
        # No transport error text in the trail; no pane.reply line either.
        assert "detail" not in line["detail"]

    def test_refused_reply_records_the_peer_status_and_detail(
        self, tmp_path, monkeypatch,
    ):
        client = self._lead_client(
            tmp_path, monkeypatch,
            StubTransport(responses={"/reply": (404, {"detail": "pane not found"})}),
        )
        resp = client.post(
            "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
        )

        assert resp.status_code == 404
        lines = self._lines(tmp_path)
        assert len(lines) == 1
        detail = lines[0]["detail"]
        assert detail["peer"] == "host-b"
        assert detail["op"] == "reply"
        assert detail["outcome"] == "refused"
        assert detail["status"] == 404
        assert detail["detail"] == "pane not found"

    def test_failed_keys_and_dialog_proxies_record_their_op(
        self, tmp_path, monkeypatch,
    ):
        client = self._lead_client(tmp_path, monkeypatch, StubTransport(fail_all=True))
        keys = client.post(
            "/pane/keys", json={"pane_id": "host-b:w1:p1", "keys": ["enter"]}
        )
        dialog = client.post(
            "/pane/dialog/answer",
            json={"pane_id": "host-b:w1:p1", "fingerprint": "fp1", "option": 0},
        )
        assert keys.status_code == 503
        assert dialog.status_code == 503

        lines = self._lines(tmp_path)
        assert [line["detail"]["op"] for line in lines] == ["keys", "dialog_answer"]
        assert all(line["action"] == "crew.call" for line in lines)
        assert all(line["detail"]["outcome"] == "unreachable" for line in lines)

    def test_failed_read_proxies_record_nothing(self, tmp_path, monkeypatch):
        client = self._lead_client(tmp_path, monkeypatch, StubTransport(fail_all=True))
        resp = client.get("/view", params={"pane_id": "host-b:w1:p1"})

        assert resp.status_code == 503
        assert self._lines(tmp_path) == []  # reads are not write actions

    def test_none_mode_keeps_peer_and_op_but_redacts_the_peer_detail(
        self, tmp_path, monkeypatch,
    ):
        state = tmp_path / "state"
        write_lead_roster(state)
        monkeypatch.setattr(server_module, "BrainTools", lambda cfg: CrewTools())
        settings = Settings(**{
            **SETTINGS_KWARGS, "state_dir": str(state),
            "audio_dir": str(tmp_path / "audio"), "audit_content": "none",
        })
        gateway = make_gateway(state, {"host-b": StubTransport(
            responses={"/reply": (404, {"detail": "pane not found"})}
        )})
        app = create_app(settings=settings, crew=gateway)
        client = TestClient(app)

        assert client.post(
            "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
        ).status_code == 404
        lines = self._lines(tmp_path)
        assert len(lines) == 1
        detail = lines[0]["detail"]
        assert detail["peer"] == "host-b"        # attribution metadata
        assert detail["op"] == "reply"           # operation enum metadata
        assert detail["outcome"] == "refused"
        assert detail["detail"] == "⟨redacted⟩"  # the peer message is content


class TestCrewCallFailureOpsWarning:
    """T3-fu/U6 (verification fix): the ops logger line is bounded and
    metadata-only for EVERY failed write attempt — no transport text,
    no tokens. The 503 response body already owns the full diagnostic."""

    def _lead_client(self, tmp_path, monkeypatch, transport):
        state = tmp_path / "state"
        write_lead_roster(state)
        return make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
        )

    def _warnings(self, caplog) -> list:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == "herdr_brain.server"
            and record.getMessage().startswith("crew call failed:")
        ]

    def test_unreachable_warning_excludes_transport_text(
        self, tmp_path, monkeypatch, caplog,
    ):
        client = self._lead_client(tmp_path, monkeypatch, StubTransport(fail_all=True))
        with caplog.at_level("WARNING", logger="herdr_brain.server"):
            response = client.post(
                "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
            )
        assert response.status_code == 503
        assert self._warnings(caplog) == [
            "crew call failed: peer=host-b op=reply outcome=unreachable status=None"
        ]
        assert "peer is down" not in self._warnings(caplog)[0]

    def test_refused_warning_is_metadata_only(
        self, tmp_path, monkeypatch, caplog,
    ):
        client = self._lead_client(
            tmp_path, monkeypatch,
            StubTransport(responses={"/reply": (404, {"detail": "pane not found"})}),
        )
        with caplog.at_level("WARNING", logger="herdr_brain.server"):
            response = client.post(
                "/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"}
            )
        assert response.status_code == 404
        assert self._warnings(caplog) == [
            "crew call failed: peer=host-b op=reply outcome=refused status=404"
        ]

    def test_failed_reads_never_warn_or_record(
        self, tmp_path, monkeypatch, caplog,
    ):
        client = self._lead_client(tmp_path, monkeypatch, StubTransport(fail_all=True))
        with caplog.at_level("WARNING", logger="herdr_brain.server"):
            response = client.get("/view", params={"pane_id": "host-b:w1:p1"})
        assert response.status_code == 503
        assert self._warnings(caplog) == []
        assert not (tmp_path / "state" / "audit.log").exists()
