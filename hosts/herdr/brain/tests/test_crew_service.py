"""The peer-side crew surface: a framework-free dispatcher for
``/crew/v1/*`` that the FastAPI router will wrap. Enrolment rides the
join token; every other route sits behind the two-factor check; a
corrupt roster fails CLOSED; the 401 is one constant shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.crew.auth import UNAUTHORIZED_BODY, UNAUTHORIZED_STATUS
from herdr_brain.crew.enrol import JoinTokens
from herdr_brain.crew.roster import CrewRoster, CrewRosterCorrupt
from herdr_brain.crew.service import CrewService

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
PEER_DER = (FIXTURES / "peer.der").read_bytes()
LEAD_DER = (FIXTURES / "lead.der").read_bytes()
SECRET = "crew-secret-1"


def headers(secret: str | None) -> dict:
    return {"authorization": f"Bearer {secret}"} if secret else {}


def getter(h: dict):
    return lambda name: h.get(name.lower())


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def roster(state_dir: Path) -> CrewRoster:
    state = CrewRoster.load(state_dir, now=lambda: 1_000.0)
    state.init_lead("host-a", SECRET, now=1_000.0)
    state.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1_000.0)
    state.persist()
    return state


def make_service(roster_state: CrewRoster, handlers=None, join_tokens=None) -> CrewService:
    return CrewService(
        roster_loader=lambda: roster_state,
        handlers=handlers or {},
        join_tokens=join_tokens,
        now=lambda: 1_000.0,
    )


# ── the two-factor gate over every crew route ────────────────────────────────


def test_authorized_read_reaches_the_handler(roster: CrewRoster) -> None:
    seen = {}

    def herd():
        seen["called"] = True
        return [{"pane_id": "p-1", "status": "idle"}]

    service = make_service(roster, handlers={"herd": herd})
    status, payload = service.handle(
        "GET", "/crew/v1/herd", None, None, getter(headers(SECRET)), PEER_DER
    )
    assert status == 200
    assert payload == [{"pane_id": "p-1", "status": "idle"}]
    assert seen.get("called") is True


def test_unauthorized_matrix_is_one_constant_401(roster: CrewRoster) -> None:
    service = make_service(roster, handlers={"herd": lambda: []})
    cases = [
        (headers("wrong-secret"), PEER_DER),   # secret wrong
        ({}, PEER_DER),                        # secret missing
        (headers(SECRET), LEAD_DER),           # certificate wrong
        (headers(SECRET), None),               # certificate missing
        ({}, None),                            # both missing
        ({"authorization": f"Basic {SECRET}"}, PEER_DER),  # wrong scheme
    ]
    answers = set()
    for h, der in cases:
        answer = service.handle(
            "GET", "/crew/v1/herd", None, None, getter(h), der
        )
        answers.add(repr(answer))
    assert answers == {repr((UNAUTHORIZED_STATUS, UNAUTHORIZED_BODY))}
    assert UNAUTHORIZED_BODY == {"detail": "unauthorized"}


def test_corrupt_roster_fails_closed_with_the_same_401(tmp_path: Path) -> None:
    def broken_loader():
        raise CrewRosterCorrupt("unreadable")

    service = CrewService(roster_loader=broken_loader, handlers={"herd": lambda: []})
    status, payload = service.handle(
        "GET", "/crew/v1/herd", None, None, getter(headers(SECRET)), PEER_DER
    )
    assert (status, payload) == (UNAUTHORIZED_STATUS, UNAUTHORIZED_BODY)


def test_unknown_route_and_method_are_404_405(roster: CrewRoster) -> None:
    service = make_service(roster, handlers={"herd": lambda: []})
    status, _ = service.handle(
        "GET", "/crew/v1/nope", None, None, getter(headers(SECRET)), PEER_DER
    )
    assert status == 404
    status, _ = service.handle(
        "DELETE", "/crew/v1/herd", None, None, getter(headers(SECRET)), PEER_DER
    )
    assert status == 405


def test_handler_crash_is_a_500_not_a_stack_trace(roster: CrewRoster) -> None:
    def explode():
        raise RuntimeError("boom")

    service = make_service(roster, handlers={"herd": explode})
    status, payload = service.handle(
        "GET", "/crew/v1/herd", None, None, getter(headers(SECRET)), PEER_DER
    )
    assert status == 500
    assert payload == {"detail": "error"}  # the crash text never travels


# ── routing into handlers ────────────────────────────────────────────────────


def test_pane_routes_deliver_pane_id_and_body(roster: CrewRoster) -> None:
    seen = {}

    def reply(pane_id, text):
        seen["reply"] = (pane_id, text)
        return {"outcome": "delivered"}

    def view(pane_id):
        seen["view"] = pane_id
        return {"pane_id": pane_id}

    def history(pane_id, **query):
        seen["history"] = (pane_id, query)
        return {"turns": []}

    def mirror(pane_id, **query):
        seen["mirror"] = (pane_id, query)
        return {"lines": []}

    service = make_service(
        roster,
        handlers={"reply": reply, "view": view, "history": history, "mirror": mirror},
    )
    auth = (getter(headers(SECRET)), PEER_DER)

    status, _ = service.handle("GET", "/crew/v1/view", {"pane_id": "p-1"}, None, *auth)
    assert status == 200 and seen["view"] == "p-1"
    status, _ = service.handle(
        "GET", "/crew/v1/history", {"pane_id": "p-1", "before": "7"}, None, *auth
    )
    assert status == 200 and seen["history"] == ("p-1", {"before": "7"})
    status, _ = service.handle(
        "GET", "/crew/v1/pane/p-2/mirror", {"lines": "10"}, None, *auth
    )
    assert status == 200 and seen["mirror"] == ("p-2", {"lines": "10"})
    status, _ = service.handle(
        "POST", "/crew/v1/pane/p-2/reply", None, {"text": "hi"}, *auth
    )
    assert status == 200 and seen["reply"] == ("p-2", "hi")


def test_approval_routes_reach_the_owner_handlers(roster: CrewRoster) -> None:
    seen = {}

    def approval_current(pane_id=None, **query):
        seen["current"] = pane_id
        return {"gate_id": "g1", "state": "proposed"}

    def approval_action(gate_id, decision):
        seen["action"] = (gate_id, decision)
        return 404, {"detail": "gate dead"}

    service = make_service(
        roster, handlers={"approval_current": approval_current, "approval_action": approval_action}
    )
    auth = (getter(headers(SECRET)), PEER_DER)

    status, payload = service.handle(
        "GET", "/crew/v1/approval/current", {"pane_id": "p-1"}, None, *auth
    )
    assert status == 200 and payload["gate_id"] == "g1"
    status, payload = service.handle(
        "POST", "/crew/v1/approval/g1/action", None, {"decision": "approve"}, *auth
    )
    assert status == 404  # the owner's dead-gate verdict travels as-is
    assert seen["action"] == ("g1", "approve")


def test_writes_require_the_decision_body_field(roster: CrewRoster) -> None:
    service = make_service(
        roster, handlers={"approval_action": lambda **kw: pytest.fail("never reached")}
    )
    status, payload = service.handle(
        "POST", "/crew/v1/approval/g1/action", None, {}, getter(headers(SECRET)), PEER_DER
    )
    assert status == 400 and "decision" in payload["detail"]


# ── enrolment over the service ───────────────────────────────────────────────


def test_enrol_route_consumes_token_and_records_peer(tmp_path: Path, roster: CrewRoster) -> None:
    jar = JoinTokens(tmp_path / "tokens", now=lambda: 1_000.0)
    token = jar.mint()
    service = make_service(roster, join_tokens=jar)

    body = {"name": "host-c", "url": "https://c.example:8747", "cert_pem": PEER_PEM}
    status, payload = service.handle(
        "POST", "/crew/v1/enrol", None, body, getter({"authorization": f"Bearer {token}"}), None
    )
    assert status == 200 and payload["member"] == "host-c"
    assert "host-c" in roster.peers  # recorded on the lead

    # a replay of the same token enrols nobody, with the SAME 401
    replay = service.handle(
        "POST", "/crew/v1/enrol", None, body, getter({"authorization": f"Bearer {token}"}), None
    )
    assert replay == (UNAUTHORIZED_STATUS, UNAUTHORIZED_BODY)


def test_enrol_bad_payload_is_400_not_a_burned_token(
    tmp_path: Path, roster: CrewRoster
) -> None:
    jar = JoinTokens(tmp_path / "tokens", now=lambda: 1_000.0)
    token = jar.mint()
    service = make_service(roster, join_tokens=jar)

    bad = {"name": "host-c", "url": "http://insecure.example", "cert_pem": PEER_PEM}
    status, payload = service.handle(
        "POST", "/crew/v1/enrol", None, bad, getter({"authorization": f"Bearer {token}"}), None
    )
    assert status == 400
    # the token survives the refused payload and a fixed one enrols
    good = {"name": "host-c", "url": "https://c.example:8747", "cert_pem": PEER_PEM}
    status2, payload2 = service.handle(
        "POST", "/crew/v1/enrol", None, good, getter({"authorization": f"Bearer {token}"}), None
    )
    assert status2 == 200 and payload2["member"] == "host-c"


def test_enrol_is_the_only_route_without_two_factors(roster: CrewRoster) -> None:
    # ...because the join token IS its authentication
    service = make_service(roster, handlers={"herd": lambda: []})
    status, payload = service.handle(
        "GET", "/crew/v1/herd", None, None, getter({}), None
    )
    assert (status, payload) == (UNAUTHORIZED_STATUS, UNAUTHORIZED_BODY)
