"""Where a brain-message address lives: this host or a crew member.

A remote pane is addressed ``<host>:<pane>#<token>`` — the same
``<host>:<pane>`` routing form the crew uses for every other pane
action (``crew.aggregate.split_pane_id``), plus the session token.

The routing rule mirrors the server's ``crew_remote_peer``:

- a bare id, or any id with no crew configured, keeps its single-host
  meaning (the local surface answers it, unknown panes included);
- a host-shaped id that names a real local pane stays local — local
  ids can look host-shaped (``w1:p9``) and the prefix is never stripped;
- the own member name as prefix is local;
- a lead routes an enrolled peer directly; a peer reaches the lead
  directly and relays every other host through it (peers only hold
  the lead in their roster);
- a lead refuses any other host prefix: nothing is sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..crew.aggregate import split_pane_id
from .errors import WalkyRefusal
from .identity import parse_address

LOCAL = "local"
REMOTE = "remote"


@dataclass(frozen=True)
class Route:
    kind: str  # LOCAL | REMOTE
    host: Optional[str]  # owner member name (remote only)
    via: Optional[str]  # crew member to call: the owner on a lead, the lead on a peer
    pane_id: str  # the pane id on its owner host
    token: str


def qualify(host: Optional[str], pane_id: str) -> str:
    """A host-qualified key: local keys stay bare, remote ones never collide."""
    return f"{host}:{pane_id}" if host else pane_id


def _crew_active(roster) -> bool:
    if roster is None:
        return False
    has_crew = getattr(roster, "has_crew", None)
    return bool(has_crew()) if callable(has_crew) else bool(has_crew)


def route_address(address: str, *, roster, local_pane_ids: Iterable[str]) -> Route:
    pane_part, token = parse_address(address)
    local = Route(kind=LOCAL, host=None, via=None, pane_id=pane_part, token=token)
    host, rest = split_pane_id(pane_part)
    if host is None or not _crew_active(roster):
        return local
    if pane_part in set(local_pane_ids):
        return local
    if host == roster.self_name:
        return Route(kind=LOCAL, host=None, via=None, pane_id=rest, token=token)

    peers = roster.peers if isinstance(roster.peers, dict) else {}
    if roster.role == "lead" and host in peers:
        return Route(kind=REMOTE, host=host, via=host, pane_id=rest, token=token)
    if roster.role == "peer" and roster.lead_name:
        return Route(kind=REMOTE, host=host, via=roster.lead_name, pane_id=rest, token=token)
    raise WalkyRefusal(
        f"'{host}' is not a crew host: the address is neither local nor "
        "routable; nothing was sent. Get live addresses from: herdr-brain msg who"
    )
