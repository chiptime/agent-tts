"""Herd aggregation and pane routing across the crew (S7).

The merged herd keeps the single-host byte shape for LOCAL panes
(``host`` omitted — installed PWAs and the voice flow must not see a
difference), tags remote panes with their host, collapses duplicates
with the local truth winning, and names every unreachable peer
explicitly instead of letting a dead host vanish its agents silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .roster import NAME_RE

#: Explicit reachability verdicts for the merged herd's peers.
STATE_FRESH = "fresh"
STATE_UNREACHABLE = "unreachable"


@dataclass
class PeerHerd:
    """One peer's contribution to a merge: its herd, or None when the
    peer could not be reached at all."""

    name: str
    herd: Optional[list]


def merge_herds(
    local_herd: Optional[list],
    peer_herds: Iterable,
) -> dict:
    """Merge the local herd with the peers' herds.

    Returns ``{"panes": [...], "peers": [{"name", "state"}]}``. Local
    panes keep their exact shape (no ``host`` key); remote panes gain
    ``host: <peer name>``. Duplicates collapse on
    ``(host, pane_id)`` with the FIRST occurrence winning — local
    entries are merged first, so the local truth beats a peer's echo.
    """
    panes: list[dict] = []
    seen: set = set()
    peers: list[dict] = []

    def take(pane: object, host: Optional[str]) -> None:
        if not isinstance(pane, dict):
            return
        entry = {k: v for k, v in pane.items() if k != "host"}
        if host is not None:
            entry["host"] = host
        key = (host, entry.get("pane_id"))
        if key in seen:
            return
        seen.add(key)
        panes.append(entry)

    for pane in local_herd or []:
        take(pane, None)

    for contribution in peer_herds:
        if isinstance(contribution, PeerHerd):
            name, herd = contribution.name, contribution.herd
        else:
            name, herd = contribution  # plain (name, herd) tuples
        if herd is None:
            peers.append({"name": name, "state": STATE_UNREACHABLE})
            continue
        peers.append({"name": name, "state": STATE_FRESH})
        for pane in herd:
            take(pane, name)

    return {"panes": panes, "peers": peers}


def split_pane_id(pane_id: object) -> tuple[Optional[str], str]:
    """Split ``<host>:<pane>`` into ``(host, pane_id)``.

    A bare id is local: ``(None, pane_id)``. A prefix that is not a
    member-shaped name is not a host at all — the whole id stays local
    (pane ids are free-form; only a well-formed host prefix routes).
    """
    if not isinstance(pane_id, str) or ":" not in pane_id:
        return None, pane_id if isinstance(pane_id, str) else ""
    host, _, rest = pane_id.partition(":")
    if not NAME_RE.match(host) or not rest:
        return None, pane_id
    return host, rest


def remote_pane_id(host: str, pane_id: str) -> str:
    """Qualify a pane id with its owning host (the routing form)."""
    return f"{host}:{pane_id}"


def owner_host_for_pane(pane_id: object, roster) -> Optional[str]:
    """Where a pane id lives: ``"local"``, the owning peer's name, or
    None when the host prefix names no enrolled member (unroutable —
    never silently treated as local)."""
    host, _ = split_pane_id(pane_id)
    if host is None:
        return "local"
    peers = getattr(roster, "peers", None)
    if isinstance(peers, dict) and host in peers:
        return host
    return None
