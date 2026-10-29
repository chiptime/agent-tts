"""Crew messages through real dispatch/auth with an in-memory wire.

No installed crew, live service, credentials or remote network is used.
"""

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from herdr_brain.crew.client import CrewHttpResponse, CrewUnreachable
from herdr_brain.crew.roster import CrewRoster
from herdr_brain.crew.service import CrewService
from herdr_brain.crew.tls import ensure_host_certificate, pem_to_der
from herdr_brain.crew_runtime import CrewGateway
from herdr_brain.walky.federation import FederatedWalkyService
from herdr_brain.walky.errors import WalkyRefusal
from tests.test_walky_store_cli import Clock, FakeMux, env, ids, pane


@pytest.fixture
def fleet(tmp_path):
    clock = Clock()
    material = {}
    for name in ("alpha", "beta", "gamma"):
        cert, _, _ = ensure_host_certificate(tmp_path / name / "crew-tls", name)
        material[name] = cert.read_text()
    rosters, dispatch, services, muxes = {}, {}, {}, {}
    faults, calls = {}, []

    class Wire:
        def __init__(self, origin, target):
            self.origin, self.target = origin, target

        def request(self, method, url, **kwargs):
            calls.append((self.origin, self.target, url, kwargs["json_body"]))
            fault = faults.get(self.target)
            if fault == "down":
                raise CrewUnreachable(self.target, "offline")
            status, payload = dispatch[self.target].handle(
                method, urlsplit(url).path, body=kwargs["json_body"],
                headers_get=kwargs["headers"].get,
                peer_cert_der=pem_to_der(material[self.origin]),
            )
            if fault == "lost" and url.endswith("/send"):
                raise CrewUnreachable(self.target, "response lost")
            return CrewHttpResponse(status, payload)

    for name in material:
        state = tmp_path / name
        roster = CrewRoster.load(state, now=clock)
        if name == "alpha":
            roster.init_lead(name, "test-secret")
            for other in ("beta", "gamma"):
                roster.record_peer(other, f"https://{other}", material[other])
        else:
            roster.join_as_peer(
                self_name=name, lead_name="alpha", lead_url="https://alpha",
                lead_cert_pem=material["alpha"], secret="test-secret",
            )
        roster.persist()
        rosters[name] = roster
        gateway = CrewGateway(
            state, secret_provider=lambda: "test-secret",
            transport_factory=lambda target, url, cert, origin=name: Wire(origin, target),
            now=clock,
        )
        mux = FakeMux([pane("w1:p1", f"{name}-session", status="working")])
        service = FederatedWalkyService(state, mux=mux, gateway=gateway, clock=clock, id_factory=ids())
        muxes[name], services[name] = mux, service
        dispatch[name] = CrewService(lambda r=roster: r, service.crew_handlers(), now=clock)
    return services, muxes, faults, calls, clock, rosters


def sender(service):
    return service.resolve_sender({"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1"})


def test_peer_to_peer_is_relayed_and_reply_returns_to_origin(fleet):
    svc, mux, _, calls, _, _ = fleet
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "question")
    assert message.state == "held"
    incoming = svc["gamma"].inbox("w1:p1", message.id)
    assert incoming.from_address == "beta:w1:p1#beta-session"
    assert incoming.body == "question"
    assert not mux["gamma"].typed
    mux["gamma"].panes[0] = replace(mux["gamma"].panes[0], status="idle")
    svc["gamma"].tick()
    assert len(mux["gamma"].typed) == 1
    assert "from agent beta:w1:p1#beta-session" in mux["gamma"].typed[0][1]
    assert svc["beta"].status(message.id).state == "typed"
    answer = svc["gamma"].reply(sender(svc["gamma"]), message.id, "answer")
    assert svc["beta"].inbox("w1:p1", answer.id).body == "answer"
    assert any(a == "beta" and b == "alpha" for a, b, _, _ in calls)
    assert any(a == "alpha" and b == "gamma" for a, b, _, _ in calls)


def test_remote_cancel_is_owned_and_removes_from_inbox(fleet):
    svc, _, _, _, _, _ = fleet
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "cancel me")
    assert "canceled" in svc["beta"].cancel("w1:p1", message.id)
    assert svc["gamma"].inbox("w1:p1") == []
    assert svc["beta"].status(message.id).state == "canceled"


def test_unreachable_preflight_leaves_no_message_and_lost_send_is_not_retried(fleet):
    svc, mux, faults, calls, _, _ = fleet
    faults["gamma"] = "down"
    with pytest.raises(WalkyRefusal, match="nothing was sent"):
        svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "offline")
    assert svc["beta"]._outbox.all() == []
    assert svc["gamma"].inbox("w1:p1") == []
    faults["gamma"] = "lost"
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "uncertain")
    assert message.state == "uncertain"
    assert svc["gamma"].inbox("w1:p1", message.id).body == "uncertain"
    before = len([c for c in calls if c[2].endswith("/send")])
    svc["beta"].tick()
    assert len([c for c in calls if c[2].endswith("/send")]) == before
    faults["gamma"] = "down"
    assert svc["beta"].status(message.id).state == "uncertain"


def test_who_lists_all_hosts_and_unreachable_members(fleet):
    svc, _, faults, _, _, _ = fleet
    rows = svc["beta"].who("w1:p1")
    assert {r.address for r in rows} == {
        "w1:p1#beta-session", "alpha:w1:p1#alpha-session", "gamma:w1:p1#gamma-session"
    }
    faults["gamma"] = "down"
    rows = svc["beta"].who("w1:p1")
    assert any(r.status == "unreachable" and "gamma" in r.label for r in rows)


def test_forged_relay_origin_is_refused_without_writing(fleet):
    svc, _, _, _, _, _ = fleet
    handler = svc["alpha"].crew_handlers()["msg_send"]
    status, _ = handler(member="beta", payload={"_origin": "gamma", "host": "alpha"})
    assert status == 422
    assert svc["alpha"]._store.all() == []


def test_operator_remote_send_and_audit_masking(fleet):
    svc, _, _, _, _, _ = fleet
    message = svc["alpha"].operator_send("phone", "gamma:w1:p1#gamma-session", "hello")
    incoming = svc["gamma"].inbox("w1:p1", message.id)
    assert incoming.from_kind == "operator"
    assert incoming.from_device == "alpha:phone"
    with pytest.raises(WalkyRefusal, match="operator"):
        svc["gamma"].reply(sender(svc["gamma"]), incoming.id, "not a peer")


def test_remote_reply_limit_and_pair_limit_are_preserved(fleet):
    svc, _, _, _, _, _ = fleet
    current = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "start")
    for name in ("gamma", "beta", "gamma", "beta"):
        current = svc[name].reply(sender(svc[name]), current.id, "reply")
    with pytest.raises(WalkyRefusal, match="hop"):
        svc["gamma"].reply(sender(svc["gamma"]), current.id, "one too many")
    svc["gamma"].send(sender(svc["gamma"]), "beta:w1:p1#beta-session", "sixth")
    with pytest.raises(WalkyRefusal, match="pair rate"):
        svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "seventh")


def test_owner_honors_draft_session_pin_and_sixty_minute_hold(fleet):
    svc, mux, _, _, clock, _ = fleet
    with pytest.raises(WalkyRefusal, match="replaced"):
        svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#old-session", "stale")
    assert not svc["beta"]._outbox.all()
    mux["gamma"].panes[0] = replace(mux["gamma"].panes[0], status="idle")
    mux["gamma"].read_screen = lambda *a, **kw: "> half written draft"
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "wait")
    assert message.state == "held"
    assert not mux["gamma"].typed
    clock.advance(3600)
    svc["gamma"].tick()
    assert svc["beta"].status(message.id).state == "waiting_in_inbox"
    assert svc["gamma"].inbox("w1:p1", message.id).body == "wait"


def test_replayed_envelope_does_not_type_twice(fleet):
    svc, mux, _, calls, _, _ = fleet
    mux["gamma"].panes[0] = replace(mux["gamma"].panes[0], status="idle")
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "once")
    envelope = next(p for a, b, u, p in calls if a == "alpha" and b == "gamma" and u.endswith("/send"))
    result = svc["gamma"].crew_handlers()["msg_send"](member="alpha", payload=envelope)
    assert result["message"]["id"] == message.id
    assert len(mux["gamma"].typed) == 1


def test_independent_owner_views_serialize_delivery(fleet):
    from concurrent.futures import ThreadPoolExecutor

    svc, mux, _, _, clock, _ = fleet
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "once")
    other = FederatedWalkyService(svc["gamma"].gateway._state_dir, mux["gamma"],
                                  gateway=svc["gamma"].gateway, clock=clock)
    mux["gamma"].panes[0] = replace(mux["gamma"].panes[0], status="idle")
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda view: view.tick(), [svc["gamma"], other]))
    assert svc["beta"].status(message.id).state == "typed"
    assert len(mux["gamma"].typed) == 1


def test_body_sender_limits_and_masked_audit_on_all_three_hosts(fleet):
    import json

    svc, mux, _, _, _, _ = fleet
    for body in ("", "x" * 4001):
        with pytest.raises(WalkyRefusal):
            svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", body)
    assert not svc["beta"]._outbox.all()
    # Each pair is distinct, so this exercises the sender limit, not the pair limit.
    mux["gamma"].panes = [pane(f"w2:p{i}", f"s{i}", status="working") for i in range(31)]
    secret = "sk-" + "A" * 48
    for i in range(30):
        svc["beta"].send(sender(svc["beta"]), f"gamma:w2:p{i}#s{i}", f"api_key={secret}")
    with pytest.raises(WalkyRefusal, match="sender rate"):
        svc["beta"].send(sender(svc["beta"]), "gamma:w2:p30#s30", "too many")
    for service in svc.values():
        audit = (service.gateway._state_dir / "audit.log").read_text()
        assert secret not in audit
        rows = [json.loads(line) for line in audit.splitlines()]
        assert len(rows) == 30
    assert secret not in svc["gamma"].inbox("w2:p0")[0].body


def test_unauthorized_remote_cancel_does_not_change_owner_ledger(fleet):
    svc, _, _, _, _, _ = fleet
    message = svc["beta"].send(sender(svc["beta"]), "gamma:w1:p1#gamma-session", "mine")
    path = svc["gamma"].gateway._state_dir / "messages" / "ledger.json"
    before = path.read_bytes()
    result = svc["gamma"].crew_handlers()["msg_cancel"](
        member="alpha", payload={"_origin": "alpha", "id": message.id, "pane": "w1:p1"}
    )
    assert result[0] == 422
    assert path.read_bytes() == before
