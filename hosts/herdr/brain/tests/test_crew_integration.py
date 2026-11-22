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
from typing import Optional

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
        self.steer_flags: list = []
        self.submits: list = []
        self.discards: list = []
        #: canned verified_reply result when a steer reaches the tools
        #: (None = the pane steers like any other send)
        self.steer_reply: Optional[dict] = None
        #: canned receipt fields attached to every verified send (the
        #: evidence the REAL owner's verified path returns)
        self.verified_receipt: Optional[dict] = None
        self.submit_outcome = "sent"
        self.discard_outcome = "discarded"

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
        self.steer_flags.append(steer)
        if steer and self.steer_reply is not None:
            return self.steer_reply
        result = self.reply_to_pane(pane_id, text)
        if self.verified_receipt is not None:
            return {**result, "receipt": dict(self.verified_receipt)}
        return result

    def submit_typed_text(self, pane_id, text_hint, line_hint=""):
        self.submits.append((pane_id, text_hint, line_hint))
        return {"outcome": self.submit_outcome, "pane_id": pane_id, "detail": ""}

    def discard_typed_text(self, pane_id, agent_kind, text_hint, line_hint=""):
        self.discards.append((pane_id, agent_kind, text_hint, line_hint))
        return {"outcome": self.discard_outcome, "pane_id": pane_id, "detail": ""}

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


#: The canonical local steer refusal (the exact /pane/reply 422 detail a
#: non-steerable LOCAL pane gets — a remote pane must get the same bytes).
STEER_REFUSAL = (
    "Ese agente no admite inyectar mensajes en un turno en "
    "curso; envía la respuesta normal y espera a que termine."
)


class OwnerWire:
    """The doubled wire for S1: the lead's REAL CrewClient speaks to the
    REAL owner dispatcher — CrewService over the peer app's own handlers —
    with only the socket replaced. Both wire factors travel for real: the
    bearer the client itself attached, and the pinned peer certificate
    DER the owner's roster enrolled."""

    def __init__(self, owner_state: Path, owner_handlers: dict):
        from herdr_brain.crew.service import CrewService

        self.requests: list = []
        self._service = CrewService(
            roster_loader=lambda: CrewRoster.load(owner_state, now=lambda: 1_000.0),
            handlers=owner_handlers,
            now=lambda: 1_000.0,
        )

    def request(self, method, url, *, headers, json_body=None, timeout_s=None):
        from urllib.parse import parse_qsl, unquote, urlparse

        parsed = urlparse(url)
        self.requests.append({"method": method, "url": url, "body": json_body})
        status, payload = self._service.handle(
            method, unquote(parsed.path),
            query=dict(parse_qsl(parsed.query)),
            body=json_body,
            headers_get=headers.get,
            peer_cert_der=ssl.PEM_cert_to_DER_cert(LEAD_PEM),
        )
        return CrewHttpResponse(status, payload)


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


# ── lead: remote steer and remote receipts (S1, parity wave 2) ──────────────


class TestRemoteSteerAndReceipts:
    """Steer travels the crew wire end to end (never silently degrading
    to an ordinary reply), a remote send leaves a LEAD-side receipt with
    an id and a tracker, and Submit/Discard reach the owner where the
    draft lives. Every test drives the real lead HTTP route; the wire is
    doubled, and the owner side is the real handler surface."""

    def _lead_client(self, tmp_path, monkeypatch, transport):
        state = tmp_path / "state"
        write_lead_roster(state)
        return make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
        ), state

    def _loopback(self, tmp_path, monkeypatch, tools=None):
        """A lead brain whose host-b transport forwards into a real peer
        brain's crew dispatcher (both sides share the tools double)."""
        tools = tools or CrewTools()
        owner_state = tmp_path / "owner-state"
        roster = CrewRoster.load(owner_state, now=lambda: 1_000.0)
        roster.join_as_peer(
            "host-b", "host-a", "https://a.example:8747", LEAD_PEM,
            "crew-secret-1", now=1.0,
        )
        roster.persist()
        monkeypatch.setattr(server_module, "BrainTools", lambda cfg: tools)
        owner_app = create_app(settings=Settings(**{
            **SETTINGS_KWARGS,
            "state_dir": str(owner_state), "audio_dir": str(tmp_path / "audio"),
        }), crew=CrewGateway(
            owner_state, secret_provider=lambda: "crew-secret-1",
            transport_factory=lambda *a: StubTransport(), now=lambda: 1_000.0,
        ))
        owner_handlers = owner_app.state.crew_handlers
        lead_state = tmp_path / "state"
        write_lead_roster(lead_state)
        client = make_client(tmp_path, monkeypatch, crew=make_gateway(
            lead_state, {"host-b": OwnerWire(owner_state, owner_handlers)},
        ))
        return client, tools, lead_state

    def _audit_lines(self, state: Path) -> list:
        path = state / "audit.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_steer_rides_the_wire_into_the_owner_handler(
        self, tmp_path, monkeypatch,
    ):
        client, tools, _ = self._loopback(tmp_path, monkeypatch)
        resp = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p9", "text": "corta por ahí", "steer": True,
        })

        assert resp.status_code == 200
        # the owner handler ran through the REAL dispatcher with the flag
        assert tools.steer_flags == [True]
        assert tools.replies == [("w1:p9", "corta por ahí")]

    def test_steer_wire_body_is_explicit_and_ordinary_body_is_untouched(
        self, tmp_path, monkeypatch,
    ):
        transport = StubTransport(responses={
            "/reply": (200, {"ok": True, "pane_id": "w1:p1"}),
        })
        client, _ = self._lead_client(tmp_path, monkeypatch, transport)

        client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p1", "text": "corta", "steer": True,
        })
        client.post("/pane/reply", json={"pane_id": "host-b:w1:p1", "text": "sigue"})

        # steer is explicit on the wire; the plain reply stays byte for byte
        assert transport.requests[0]["body"] == {"text": "corta", "steer": True}
        assert transport.requests[1]["body"] == {"text": "sigue"}

    def test_old_owner_steer_refusal_is_the_canonical_422(
        self, tmp_path, monkeypatch,
    ):
        transport = StubTransport(responses={
            "/reply": (400, {"detail": (
                "bad request: _h_reply() got an unexpected keyword "
                "argument 'steer'"
            )}),
        })
        client, _ = self._lead_client(tmp_path, monkeypatch, transport)

        resp = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p1", "text": "corta", "steer": True,
        })

        # fail closed: the SAME refusal a local non-steerable pane gets,
        # never a silent ordinary send
        assert resp.status_code == 422
        assert resp.json()["detail"] == STEER_REFUSAL

    def test_old_owner_ordinary_reply_keeps_working_unchanged(
        self, tmp_path, monkeypatch,
    ):
        transport = StubTransport(responses={
            "/reply": (200, {"ok": True, "pane_id": "w1:p1"}),
        })
        client, _ = self._lead_client(tmp_path, monkeypatch, transport)

        resp = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p1", "text": "sigue",
        })

        # the plain reply to the SAME old owner: byte-identical wire,
        # the answer this route has always given
        assert resp.status_code == 200
        assert transport.requests[0]["body"] == {"text": "sigue"}
        assert resp.json() == {"ok": True, "pane_id": "w1:p1"}

    def test_owner_unsupported_steer_is_422_never_an_ordinary_send(
        self, tmp_path, monkeypatch,
    ):
        tools = CrewTools()
        tools.steer_reply = {
            "outcome": "unsupported_steer", "pane_id": "w1:p9",
            "detail": (
                "a running turn of a claude agent cannot be steered; "
                "send a normal reply instead"
            ),
        }
        client, tools, _ = self._loopback(tmp_path, monkeypatch, tools)

        resp = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p9", "text": "corta", "steer": True,
        })

        assert resp.status_code == 422
        assert resp.json()["detail"] == STEER_REFUSAL
        # exactly ONE owner call — never retried as a plain reply
        assert tools.steer_flags == [True]

    def test_crew_send_returns_a_lead_receipt_the_pwa_can_follow(
        self, tmp_path, monkeypatch,
    ):
        transport = StubTransport(responses={
            "/reply": (200, {"ok": False, "pane_id": "w1:p1", "receipt": {
                "outcome": "typed_not_submitted",
                "detail": "el texto quedó escrito",
                "text_hint": "…sigue", "line_hint": "│ sigue",
            }}),
        })
        client, state = self._lead_client(tmp_path, monkeypatch, transport)

        resp = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p1", "text": "sigue",
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["pane_id"] == "host-b:w1:p1"
        receipt = body["receipt"]
        assert receipt["id"]  # a lead-side id — Submit/Discard become possible
        assert receipt["outcome"] == "typed_not_submitted"
        assert receipt["pane_id"] == "host-b:w1:p1"
        listed = client.get(
            "/pane/receipts", params={"pane_id": "host-b:w1:p1"}
        ).json()["receipts"]
        assert [r["id"] for r in listed] == [receipt["id"]]
        assert listed[0]["text_hint"] == "…sigue"
        # the audit carries the REAL outcome, never a hardcoded delivered
        forwarded = [
            line for line in self._audit_lines(state)
            if line["action"] == "pane.reply" and line["detail"].get("crew")
        ]
        assert forwarded[0]["detail"]["outcome"] == "typed_not_submitted"
        assert forwarded[0]["detail"]["receipt"]["id"] == receipt["id"]

    def test_tracked_crew_send_starts_a_lead_tracker(
        self, tmp_path, monkeypatch,
    ):
        transport = StubTransport(responses={
            "/reply": (200, {"ok": True, "pane_id": "w1:p1", "receipt": {
                "outcome": "sent", "detail": "", "text_hint": "…sigue",
            }}),
        })
        client, _ = self._lead_client(tmp_path, monkeypatch, transport)

        resp = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p1", "text": "sigue",
        })

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        receipt = resp.json()["receipt"]
        assert receipt["outcome"] == "sent"
        listed = client.get(
            "/pane/receipts", params={"pane_id": "host-b:w1:p1"}
        ).json()["receipts"]
        # parity-f7g: the tracked send carries its follow state
        assert "track" in listed[0]

    def test_crew_receipt_submit_forwards_to_the_owner_where_the_draft_lives(
        self, tmp_path, monkeypatch,
    ):
        tools = CrewTools()
        tools.verified_receipt = {
            "outcome": "typed_not_submitted",
            "detail": "el texto quedó escrito",
            "text_hint": "«sigue»", "line_hint": "│ sigue",
        }
        client, tools, _ = self._loopback(tmp_path, monkeypatch, tools)
        reply = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p9", "text": "sigue",
        })
        receipt = reply.json()["receipt"]

        resp = client.post(f"/pane/receipt/{receipt['id']}/submit")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["receipt"]["outcome"] == "sent"
        # the REAL owner handler keyed the draft with the bound hints
        assert tools.submits == [("w1:p9", "«sigue»", "│ sigue")]
        # the draft is finished: a second submit finds nothing actionable
        resp = client.post(f"/pane/receipt/{receipt['id']}/submit")
        assert resp.status_code == 409

    def test_crew_receipt_discard_forwards_to_the_owner(
        self, tmp_path, monkeypatch,
    ):
        tools = CrewTools()
        tools.verified_receipt = {
            "outcome": "typed_not_submitted",
            "detail": "el texto quedó escrito",
            "text_hint": "«sigue»", "line_hint": "│ sigue",
        }
        client, tools, _ = self._loopback(tmp_path, monkeypatch, tools)
        reply = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p9", "text": "sigue",
        })
        receipt = reply.json()["receipt"]

        resp = client.post(f"/pane/receipt/{receipt['id']}/discard")

        assert resp.status_code == 200
        assert resp.json()["receipt"]["outcome"] == "discarded"
        assert tools.discards == [("w1:p9", "", "«sigue»", "│ sigue")]

    def test_crew_submit_against_an_owner_without_followups_is_a_clear_502(
        self, tmp_path, monkeypatch,
    ):
        # an owner predating the receipt routes: /reply works, the
        # follow-up route does not exist (its dispatcher answers 404)
        transport = StubTransport(responses={
            "/reply": (200, {"ok": False, "pane_id": "w1:p1", "receipt": {
                "outcome": "typed_not_submitted", "detail": "el texto quedó escrito",
                "text_hint": "…sigue", "line_hint": "│ sigue",
            }}),
        })
        client, state = self._lead_client(tmp_path, monkeypatch, transport)
        reply = client.post("/pane/reply", json={
            "pane_id": "host-b:w1:p1", "text": "sigue",
        })
        receipt = reply.json()["receipt"]

        resp = client.post(f"/pane/receipt/{receipt['id']}/submit")

        assert resp.status_code == 502
        assert resp.json()["detail"]
        # the receipt is untouched: the card stays re-offerable, honest
        listed = client.get(
            "/pane/receipts", params={"pane_id": "host-b:w1:p1"}
        ).json()["receipts"]
        assert listed[0]["outcome"] == "typed_not_submitted"


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
                 dialog_answer=None, steer_reply=None):
        super().__init__()
        self.reply_outcome = reply_outcome
        self.keys_outcome = keys_outcome
        self.dialog_answer = dialog_answer
        self.steer_reply = steer_reply
        self.dialog_calls: list = []

    def reply_to_pane(self, pane_id, text):
        self.replies.append((pane_id, text))
        return {"outcome": self.reply_outcome, "pane_id": pane_id, "detail": ""}

    def verified_reply_to_pane(self, pane_id, text, steer=False):
        self.steer_flags.append(steer)
        if steer and self.steer_reply is not None:
            return self.steer_reply
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

    def test_reply_steer_reaches_the_tools_layer_and_audits_the_flag(
        self, tmp_path, monkeypatch,
    ):
        tools = PeerAuditTools()
        app, state = self._peer_app(tmp_path, monkeypatch, tools)
        result = app.state.crew_handlers["reply"]("w1:p9", "corta", steer=True)

        assert result == (200, {"ok": True, "pane_id": "w1:p9"})
        assert tools.steer_flags == [True]
        lines = self._lines(state)
        assert lines[0]["detail"] == {
            "outcome": "delivered", "text": "corta", "crew": True, "steer": True,
        }

    def test_reply_unsupported_steer_maps_to_a_422_refusal(
        self, tmp_path, monkeypatch,
    ):
        tools = PeerAuditTools(steer_reply={
            "outcome": "unsupported_steer", "pane_id": "w1:p9",
            "detail": "a running turn of a claude agent cannot be steered",
        })
        app, state = self._peer_app(tmp_path, monkeypatch, tools)
        result = app.state.crew_handlers["reply"]("w1:p9", "corta", steer=True)

        # the owner's own verdict travels as its own status — the lead
        # translates it into the canonical refusal its operator speaks
        assert result == (422, {"detail": "unsupported_steer"})
        lines = self._lines(state)
        assert lines[0]["detail"]["outcome"] == "unsupported_steer"
        assert lines[0]["detail"]["steer"] is True

    def test_reply_rejects_a_non_boolean_steer_before_the_tools(
        self, tmp_path, monkeypatch,
    ):
        tools = PeerAuditTools()
        app, state = self._peer_app(tmp_path, monkeypatch, tools)
        result = app.state.crew_handlers["reply"]("w1:p9", "corta", steer="sí")

        assert result == (400, {"detail": "steer must be a boolean"})
        assert tools.replies == []
        assert self._lines(state) == []

    def test_receipt_followup_handlers_key_the_bound_line_and_audit(
        self, tmp_path, monkeypatch,
    ):
        tools = PeerAuditTools()
        app, state = self._peer_app(tmp_path, monkeypatch, tools)

        result = app.state.crew_handlers["receipt_submit"](
            "w1:p9", "«sigue»", "│ sigue",
        )
        assert result == (200, {"outcome": "sent", "detail": ""})
        assert tools.submits == [("w1:p9", "«sigue»", "│ sigue")]

        result = app.state.crew_handlers["receipt_discard"](
            "w1:p9", "«sigue»", "│ sigue",
        )
        assert result == (200, {"outcome": "discarded", "detail": ""})
        assert tools.discards == [("w1:p9", "", "«sigue»", "│ sigue")]

        actions = [line["action"] for line in self._lines(state)]
        assert actions == ["pane.receipt.submit", "pane.receipt.discard"]
        assert self._lines(state)[0]["detail"] == {
            "outcome": "sent", "crew": True,
        }

    def test_receipt_followup_failure_keeps_the_owner_honest(
        self, tmp_path, monkeypatch,
    ):
        tools = PeerAuditTools()
        tools.submit_outcome = "error"
        app, state = self._peer_app(tmp_path, monkeypatch, tools)

        result = app.state.crew_handlers["receipt_submit"]("w1:p9", "hint", "")
        assert result == (502, {"detail": "submit failed"})
        lines = self._lines(state)
        assert lines[0]["detail"]["outcome"] == "error"

    def test_receipt_followup_blank_hint_is_admission_refused(
        self, tmp_path, monkeypatch,
    ):
        tools = PeerAuditTools()
        app, state = self._peer_app(tmp_path, monkeypatch, tools)

        result = app.state.crew_handlers["receipt_submit"]("w1:p9", "   ", "")
        assert result == (400, {"detail": "text_hint is required"})
        assert tools.submits == []
        assert self._lines(state) == []


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


class TestRemoteGateForwardFailureAudit:
    """T3-fu2/U7: the ONE forward failure mode that returns no answer —
    a transport raise from the owner — records the same failed-attempt
    line every crew write uses (crew.call, op=gate_forward) and maps to
    the same 503 crew_call does. Refusals that RETURN (the raw
    approval_resolve contract: a 404 for a dead gate is an ANSWER) keep
    their single existing approval.forward line — recording crew.call
    for them would duplicate on the same trail."""

    def _lead_with_gate(self, tmp_path, monkeypatch, transport):
        state = tmp_path / "state"
        write_lead_roster(state)
        client = make_client(
            tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
        )
        return client, transport

    def _lines(self, tmp_path) -> list:
        path = tmp_path / "state" / "audit.log"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]

    def _learn_remote_gate(self, client):
        # The herd/approval-current probe teaches the index the owner.
        approval = client.get("/approval/current").json()["approval"]
        assert approval is not None and approval["host"] == "host-b"

    def test_unreachable_forward_records_one_crew_call_line(
        self, tmp_path, monkeypatch,
    ):
        class FailAction(StubTransport):
            def request(self, method, url, **kwargs):
                if method == "POST" and "/action" in url:
                    raise OSError("peer is down")
                return super().request(method, url, **kwargs)

        client, _ = self._lead_with_gate(
            tmp_path, monkeypatch,
            FailAction(responses={
                "/crew/v1/approval/current": (200, {"approval": {
                    "gate_id": "g-remote-1", "state": "proposed",
                    "question": "¿Envío?",
                }}),
            }),
        )
        self._learn_remote_gate(client)

        resp = client.post("/approval/g-remote-1/approve")

        assert resp.status_code == 503
        lines = self._lines(tmp_path)
        assert len(lines) == 1
        line = lines[0]
        assert line["action"] == "crew.call"
        assert line["detail"] == {
            "peer": "host-b", "op": "gate_forward", "outcome": "unreachable",
            "gate_id": "g-remote-1", "decision": "approve",
        }
        # No transport text; no approval.forward line for this attempt.
        assert "peer is down" not in json.dumps(line)

    def test_unreachable_reject_route_records_the_decision(
        self, tmp_path, monkeypatch,
    ):
        class FailAction(StubTransport):
            def request(self, method, url, **kwargs):
                if method == "POST" and "/action" in url:
                    raise OSError("peer is down")
                return super().request(method, url, **kwargs)

        client, _ = self._lead_with_gate(
            tmp_path, monkeypatch,
            FailAction(responses={
                "/crew/v1/approval/current": (200, {"approval": {
                    "gate_id": "g-remote-2", "state": "proposed",
                    "question": "¿Envío?",
                }}),
            }),
        )
        self._learn_remote_gate(client)

        resp = client.post("/approval/g-remote-2/reject")

        assert resp.status_code == 503
        lines = self._lines(tmp_path)
        assert len(lines) == 1
        assert lines[0]["detail"]["decision"] == "reject"

    def test_refused_forward_keeps_its_single_existing_line(
        self, tmp_path, monkeypatch,
    ):
        client, _ = self._lead_with_gate(
            tmp_path, monkeypatch,
            StubTransport(responses={
                "/crew/v1/approval/current": (200, {"approval": {
                    "gate_id": "g-remote-3", "state": "proposed",
                    "question": "¿Envío?",
                }}),
                "/crew/v1/approval/g-remote-3/action": (
                    404, {"detail": "gate dead"}
                ),
            }),
        )
        self._learn_remote_gate(client)

        resp = client.post("/approval/g-remote-3/approve")

        assert resp.status_code == 404
        lines = self._lines(tmp_path)
        # Exactly ONE line: the route's own approval.forward. A crew.call
        # line here would duplicate the same trail entry.
        assert len(lines) == 1
        assert lines[0]["action"] == "approval.forward"
        assert "crew.call" not in json.dumps(lines)


def test_ntfy_forward_failure_is_audited_and_warns_without_transport_text(
    tmp_path, monkeypatch, caplog,
):
    class FailAction(StubTransport):
        def request(self, method, url, **kwargs):
            if method == "POST" and "/action" in url:
                raise OSError("peer is down SECRET_TRANSPORT")
            return super().request(method, url, **kwargs)

    state = tmp_path / "state"
    write_lead_roster(state)
    transport = FailAction(responses={
        "/crew/v1/approval/current": (200, {"approval": {
            "gate_id": "g-probe", "state": "proposed", "question": "hello",
        }}),
    })
    client = make_client(
        tmp_path, monkeypatch, crew=make_gateway(state, {"host-b": transport})
    )
    assert client.get("/approval/current").json()["approval"]["host"] == "host-b"
    caplog.clear()

    response = client.post("/approval/g-probe/action", content="approve")

    assert response.status_code == 503
    lines = [
        json.loads(line)
        for line in (state / "audit.log").read_text().splitlines()
    ]
    assert len(lines) == 1
    assert lines[0]["action"] == "crew.call"
    assert "device" not in lines[0]
    assert lines[0]["detail"] == {
        "peer": "host-b", "op": "gate_forward", "outcome": "unreachable",
        "gate_id": "g-probe", "decision": "approve",
    }
    assert "SECRET_TRANSPORT" not in json.dumps(lines)
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("crew call failed:")
    ]
    assert warnings == [
        "crew call failed: peer=host-b op=gate_forward "
        "outcome=unreachable status=None"
    ]
