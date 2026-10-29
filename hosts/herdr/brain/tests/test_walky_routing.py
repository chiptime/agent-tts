"""Crew routing of brain-message addresses (parity-crew-messages S2, S6).

A remote pane is ``<host>:<pane>#<token>``. Local addresses keep their
single-host meaning byte for byte; an enrolled host routes remote; a
peer relays every other host through its lead; a lead refuses a host
prefix that is neither local nor enrolled, and nothing is sent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from herdr_brain.walky.errors import WalkyRefusal
from herdr_brain.walky.routing import Route, qualify, route_address


def roster(role: str, self_name: str, lead: str, peers: tuple = ()) -> SimpleNamespace:
    members = {name: {"url": f"https://{name}:8741"} for name in peers}
    if role == "peer":
        members = {lead: {"url": f"https://{lead}:8741"}}
    return SimpleNamespace(
        role=role,
        self_name=self_name,
        lead_name=lead,
        peers=members,
        has_crew=role in ("lead", "peer"),
    )


LEAD = roster("lead", "alpha", "alpha", peers=("beta", "gamma"))
PEER = roster("peer", "beta", "alpha")


# ── local addresses never change meaning ─────────────────────────────────────


def test_bare_local_address_without_crew_is_local() -> None:
    assert route_address("wJ:p11#tok", roster=None, local_pane_ids=set()) == Route(
        kind="local", host=None, via=None, pane_id="wJ:p11", token="tok"
    )


def test_host_shaped_local_pane_stays_local_even_in_a_crew() -> None:
    # "w1" is a valid member-shaped name, but the full id is a real local pane.
    route = route_address("w1:p2#tok", roster=LEAD, local_pane_ids={"w1:p2"})
    assert route.kind == "local"
    assert route.pane_id == "w1:p2"


def test_host_shaped_address_without_crew_keeps_local_semantics() -> None:
    route = route_address("w1:p2#tok", roster=None, local_pane_ids=set())
    assert route.kind == "local"
    assert route.pane_id == "w1:p2"


def test_own_host_prefix_routes_local() -> None:
    route = route_address("alpha:wJ:p11#tok", roster=LEAD, local_pane_ids={"wJ:p11"})
    assert route == Route(kind="local", host=None, via=None, pane_id="wJ:p11", token="tok")


# ── remote routing ───────────────────────────────────────────────────────────


def test_lead_routes_an_enrolled_host_directly() -> None:
    route = route_address("beta:w1:p2#tok", roster=LEAD, local_pane_ids=set())
    assert route == Route(kind="remote", host="beta", via="beta", pane_id="w1:p2", token="tok")


def test_peer_routes_the_lead_directly() -> None:
    route = route_address("alpha:w3:p1#tok", roster=PEER, local_pane_ids=set())
    assert route == Route(kind="remote", host="alpha", via="alpha", pane_id="w3:p1", token="tok")


def test_peer_relays_another_peer_through_the_lead() -> None:
    route = route_address("gamma:w1:p2#tok", roster=PEER, local_pane_ids=set())
    assert route == Route(kind="remote", host="gamma", via="alpha", pane_id="w1:p2", token="tok")


def test_lead_refuses_an_unknown_host_and_sends_nothing() -> None:
    with pytest.raises(WalkyRefusal) as err:
        route_address("delta:w1:p2#tok", roster=LEAD, local_pane_ids=set())
    assert "'delta' is not a crew host" in str(err.value)
    assert "nothing was sent" in str(err.value)


def test_incomplete_remote_address_is_refused() -> None:
    with pytest.raises(WalkyRefusal) as err:
        route_address("beta:w1:p2", roster=LEAD, local_pane_ids=set())
    assert "<pane>#<token>" in str(err.value)


# ── host-qualified keys for the anti-loop limits ─────────────────────────────


def test_qualify_keeps_local_keys_unchanged() -> None:
    assert qualify(None, "w1:p2") == "w1:p2"


def test_qualify_prefixes_remote_keys_so_hosts_never_collide() -> None:
    assert qualify("beta", "w1:p2") == "beta:w1:p2"
    assert qualify("beta", "w1:p2") != qualify("gamma", "w1:p2")
