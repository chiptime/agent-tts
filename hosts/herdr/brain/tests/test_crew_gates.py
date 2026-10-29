"""Gate ownership across the crew (S7, doc 10 risk 4): an approval
gate lives on the host that owns the agent. A lead NEVER resolves a
remote gate locally — it forwards the decision and returns the
owner's answer, and under concurrent resolves from several clients
exactly ONE apply.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from herdr_brain.crew.client import CrewHttpResponse
from herdr_brain.crew.gates import GateIndex, GateRouter
from herdr_brain.crew.roster import CrewRoster

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
LEAD_PEM = (FIXTURES / "lead.pem").read_text(encoding="utf-8")
LEAD_DER = (FIXTURES / "lead.der").read_bytes()
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
PEER_DER = (FIXTURES / "peer.der").read_bytes()


class MiniGateStore:
    """The owner's once-only resolve — the ApprovalGateStore discipline
    in miniature: one lock, terminal transition, approve exactly once."""

    def __init__(self, *gate_ids: str) -> None:
        self._lock = threading.Lock()
        self._gates = set(gate_ids)
        self.resolves = 0

    def resolve(self, gate_id: str, decision: str):
        with self._lock:
            if gate_id not in self._gates:
                return 404, {"detail": "gate dead"}
            self._gates.discard(gate_id)
            self.resolves += 1
            return 200, {"gate_id": gate_id, "state": decision}


# ── local gates resolve locally ──────────────────────────────────────────────


def test_local_gate_resolves_through_the_local_store() -> None:
    store = MiniGateStore("g-local")
    router = GateRouter(
        this_host="host-a",
        local_resolve=store.resolve,
        forward=lambda peer, gate_id, decision: pytest.fail("no forwarding expected"),
    )

    first = router.resolve("g-local", "approve")
    second = router.resolve("g-local", "approve")
    assert first.applied is True and first.status == 200
    assert second.applied is False and second.status == 404
    assert store.resolves == 1


def test_indexed_remote_gate_forwards_and_never_touches_local() -> None:
    owner_store = MiniGateStore("g-remote")
    local_calls: list[tuple] = []

    def local_resolve(gate_id, decision):
        local_calls.append((gate_id, decision))
        return 404, {"detail": "gate dead"}

    forwarded: list[tuple] = []

    def forward(peer, gate_id, decision):
        forwarded.append((peer, gate_id, decision))
        return owner_store.resolve(gate_id, decision)

    index = GateIndex()
    index.record("g-remote", "host-b")
    router = GateRouter(
        this_host="host-a", local_resolve=local_resolve, forward=forward
    )

    result = router.resolve("g-remote", "approve", index=index)
    assert result.applied is True
    assert result.forwarded is True and result.owner == "host-b"
    assert forwarded == [("host-b", "g-remote", "approve")]
    assert local_calls == []  # the lead never resolved it locally
    assert owner_store.resolves == 1


def test_unknown_owner_is_not_resolved_anywhere() -> None:
    # a gate that is neither ours nor a known member's is dead — the
    # lead must not "helpfully" resolve it into its own store
    store = MiniGateStore()
    index = GateIndex()
    index.record("g-x", "host-ghost")  # not this host, not enrolled
    router = GateRouter(
        this_host="host-a",
        local_resolve=store.resolve,
        forward=lambda *a: pytest.fail("must not forward to a stranger"),
        known_members=lambda: {"host-a"},  # host-ghost is no member
    )
    result = router.resolve("g-x", "approve", index=index)
    assert result.applied is False
    assert store.resolves == 0


# ── the two-fake-host race: concurrent resolves, exactly one applies ─────────


class ServiceTransport:
    """An in-process wire: the lead's client speaks to the peer's real
    CrewService. No sockets — the transport hands the request over and
    "presents" the lead's client certificate DER the TLS layer would
    have carried."""

    def __init__(self, service, presented_der: bytes) -> None:
        self.service = service
        self.presented_der = presented_der
        self.served: list[dict] = []

    def request(self, method, url, *, headers, json_body=None, timeout_s=None):
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(url)
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        self.served.append({"method": method, "path": parsed.path})
        status, payload = self.service.handle(
            method,
            parsed.path,
            query,
            json_body if isinstance(json_body, dict) else None,
            lambda name: headers.get(name.lower()),
            self.presented_der,
        )
        return CrewHttpResponse(status, payload)


def build_two_hosts(tmp_path: Path, gate_ids: tuple[str, ...]):
    """Host A (lead) with a GateRouter, host B (peer) with a real crew
    surface owning the gates. Returns (router, b_store, a_transport)."""
    from herdr_brain.crew.client import CrewClient
    from herdr_brain.crew.service import CrewService

    now = lambda: 1_000.0  # noqa: E731 — fixed clock for both hosts

    # host B (peer): owns the agent and the gate, pins host A's cert
    b_roster = CrewRoster.load(tmp_path / "b-state", now=now)
    b_roster.join_as_peer(
        "host-b", "host-a", "https://a.example:8747", LEAD_PEM, "crew-secret-1", now=1.0
    )
    b_roster.persist()
    b_store = MiniGateStore(*gate_ids)
    b_service = CrewService(
        roster_loader=lambda: b_roster,
        handlers={
            "approval_action": lambda gate_id, decision: b_store.resolve(gate_id, decision),
        },
        now=now,
    )

    # host A (lead): pins host B's cert, talks over the in-process wire
    a_roster = CrewRoster.load(tmp_path / "a-state", now=now)
    a_roster.init_lead("host-a", "crew-secret-1", now=1.0)
    a_roster.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1.0)
    a_roster.persist()

    a_transport = ServiceTransport(b_service, presented_der=LEAD_DER)
    client = CrewClient(
        name="host-b",
        url="https://b.example:8747",
        cert_pem=PEER_PEM,
        secret_provider=lambda: "crew-secret-1",
        transport=a_transport,
        now=now,
    )

    def forward(peer: str, gate_id: str, decision: str):
        assert peer == "host-b"
        response = client.approval_resolve(gate_id, decision)
        return response.status, response.body

    router = GateRouter(
        this_host="host-a",
        local_resolve=lambda gate_id, decision: (404, {"detail": "gate dead"}),
        forward=forward,
        known_members=lambda: {"host-a", "host-b"},
    )
    return router, b_store, a_transport


def test_concurrent_resolves_apply_exactly_once(tmp_path: Path) -> None:
    router, b_store, _ = build_two_hosts(tmp_path, ("g-race",))
    index = GateIndex()
    index.record("g-race", "host-b")

    barrier = threading.Barrier(8)

    def attempt(_i: int):
        barrier.wait()  # every resolve lands as simultaneously as we can make it
        return router.resolve("g-race", "approve", index=index)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    applied = [r for r in results if r.applied]
    assert len(applied) == 1  # EXACTLY one decision took effect
    assert b_store.resolves == 1
    for result in results:
        assert result.forwarded is True  # every attempt went to the owner
        assert result.status in (200, 404)  # answers came from the owner
    # a follow-up resolve on the dead gate is refused by the owner
    again = router.resolve("g-race", "approve", index=index)
    assert again.applied is False and again.status == 404
