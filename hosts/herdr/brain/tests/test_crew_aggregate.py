"""Herd aggregation and pane routing across the crew (S7): remote
panes are tagged with their host, local panes stay byte-compatible
with the single-host herd (host omitted), duplicates collapse, and a
peer that cannot be reached is marked explicitly instead of silently
disappearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.crew.aggregate import (
    merge_herds,
    owner_host_for_pane,
    remote_pane_id,
    split_pane_id,
)
from herdr_brain.crew.roster import CrewRoster

FIXTURES = Path(__file__).parent / "fixtures" / "crew"


def pane(pane_id: str, status: str = "idle") -> dict:
    return {"pane_id": pane_id, "status": status, "last_turn": None}


# ── tagging ──────────────────────────────────────────────────────────────────


def test_local_panes_carry_no_host_tag() -> None:
    merged = merge_herds([pane("p-1")], [])
    assert merged["panes"] == [pane("p-1")]  # byte-compatible, no host key
    assert merged["peers"] == []


def test_remote_panes_are_tagged_with_their_host() -> None:
    merged = merge_herds(
        [pane("p-1")],
        [("host-b", [pane("p-9")])],
    )
    by_id = {p["pane_id"]: p for p in merged["panes"]}
    assert "host" not in by_id["p-1"]
    assert by_id["p-9"]["host"] == "host-b"


# ── dedupe ──────────────────────────────────────────────────────────────────


def test_same_bare_id_on_two_hosts_is_two_panes() -> None:
    # pane ids are per-host namespaces: "p-1" here and "p-1" on a peer
    # are TWO panes, told apart by the host tag (local keeps the
    # single-host shape — that is the whole point of the tag)
    merged = merge_herds(
        [pane("p-1", "idle")],
        [("host-b", [pane("p-1", "blocked")])],
    )
    assert len(merged["panes"]) == 2
    by_key = {(p.get("host"), p["pane_id"]): p for p in merged["panes"]}
    assert by_key[(None, "p-1")]["status"] == "idle"
    assert by_key[("host-b", "p-1")]["status"] == "blocked"


def test_duplicate_remote_panes_collapse_first_wins() -> None:
    merged = merge_herds(
        [],
        [("host-b", [pane("p-2", "idle")]), ("host-b", [pane("p-2", "done")])],
    )
    assert len(merged["panes"]) == 1
    assert merged["panes"][0]["status"] == "idle"


# ── unreachable peers are explicit ───────────────────────────────────────────


def test_unreachable_peer_is_marked_not_omitted() -> None:
    merged = merge_herds(
        [pane("p-1")],
        [("host-b", None), ("host-c", [pane("p-3")])],
    )
    states = {p["name"]: p["state"] for p in merged["peers"]}
    assert states == {"host-b": "unreachable", "host-c": "fresh"}
    ids = {p["pane_id"] for p in merged["panes"]}
    assert ids == {"p-1", "p-3"}  # host-b contributed nothing, silently


# ── pane id routing ──────────────────────────────────────────────────────────


def test_split_pane_id_routes_qualified_ids() -> None:
    assert split_pane_id("host-b:p-2") == ("host-b", "p-2")
    assert split_pane_id("p-2") == (None, "p-2")  # bare id: local pane
    # an id whose prefix is not a member-shaped name stays whole/local
    assert split_pane_id("not a host:p-2") == (None, "not a host:p-2")


def test_remote_pane_id_round_trips() -> None:
    qualified = remote_pane_id("host-b", "p-2")
    assert qualified == "host-b:p-2"
    assert split_pane_id(qualified) == ("host-b", "p-2")


def test_owner_host_for_pane(tmp_path: Path) -> None:
    roster = CrewRoster.load(tmp_path / "state", now=lambda: 1.0)
    roster.init_lead("host-a", "crew-secret-1", now=1.0)
    roster.record_peer(
        "host-b",
        "https://b.example:8747",
        (FIXTURES / "peer.pem").read_text(encoding="utf-8"),
        now=1.0,
    )

    assert owner_host_for_pane("p-1", roster) == "local"
    assert owner_host_for_pane("host-b:p-1", roster) == "host-b"
    assert owner_host_for_pane("host-zzz:p-1", roster) is None  # unknown host
