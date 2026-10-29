"""Addresses and sender identity for brain messages.

An address is ``<pane_id>#<token>``: the pane id locates the pane, the
token (the pane's live agent-session value) pins WHICH conversation the
sender meant, so a pane whose agent was replaced refuses messages
addressed to the old session instead of silently delivering them to a
stranger.

The sender is resolved from the pane environment herdr injects into
every managed pane (``HERDR_ENV=1`` and ``HERDR_PANE_ID`` — the surface
``herdr --skill`` documents). A caller that is not inside a pane is
refused with a clear message: the operator's path is the voice/PWA
route, not this CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

from ..herdr import AgentInfo
from .errors import WalkyRefusal

#: Env keys herdr injects into every managed pane (herdr --skill).
ENV_PANE = "HERDR_PANE_ID"
ENV_FLAG = "HERDR_ENV"


def format_address(pane_id: str, token: str) -> str:
    return f"{pane_id}#{token}"


def parse_address(address: str) -> Tuple[str, str]:
    """Splits ``<pane>#<token>``; refuses anything without both halves."""
    if "#" not in address:
        raise WalkyRefusal(
            f"address {address!r} is incomplete: it must be <pane>#<token>. "
            "Get full addresses from: herdr-brain msg who"
        )
    pane_id, _, token = address.rpartition("#")
    pane_id = pane_id.strip()
    token = token.strip()
    if not pane_id or not token:
        raise WalkyRefusal(
            f"address {address!r} is incomplete: it must be <pane>#<token>. "
            "Get full addresses from: herdr-brain msg who"
        )
    return pane_id, token


@dataclass(frozen=True)
class AgentSender:
    """The agent calling the CLI, resolved from its pane."""

    pane_id: str
    address: str
    harness: str
    label: str


@dataclass(frozen=True)
class WhoRow:
    """One reachable pane as ``who`` prints it."""

    address: str
    pane_id: str
    harness: str
    status: str
    label: str
    you: bool = False


def find_pane(panes: List[AgentInfo], pane_id: str) -> Optional[AgentInfo]:
    return next((p for p in panes if p.pane_id == pane_id), None)


def who_rows(panes: List[AgentInfo], me_pane: Optional[str]) -> List[WhoRow]:
    """Addressable panes only: a pane needs a live agent session."""
    rows = [
        WhoRow(
            address=format_address(p.pane_id, p.session_value),
            pane_id=p.pane_id,
            harness=p.agent,
            status=p.status,
            label=p.title,
            you=bool(me_pane and p.pane_id == me_pane),
        )
        for p in sorted(panes, key=lambda a: a.pane_id)
        if p.session_value
    ]
    return rows


def resolve_sender(env: Mapping[str, str], panes: List[AgentInfo]) -> AgentSender:
    """Builds the caller's identity from the pane env plus the pane list."""
    if str(env.get(ENV_FLAG, "")) != "1" or not str(env.get(ENV_PANE, "")).strip():
        raise WalkyRefusal(
            "you are not inside a herdr pane: brain messages run from an "
            "agent pane (the operator sends through the brain's voice/PWA route)"
        )
    pane_id = str(env[ENV_PANE]).strip()
    pane = find_pane(panes, pane_id)
    if pane is None:
        raise WalkyRefusal(
            f"your pane {pane_id} is not visible to the multiplexer; "
            "messages can only be sent from a live pane"
        )
    if not pane.session_value:
        raise WalkyRefusal(
            f"your pane {pane_id} has no agent session yet; "
            "brain messages need a running agent session"
        )
    return AgentSender(
        pane_id=pane_id,
        address=format_address(pane_id, pane.session_value),
        harness=pane.agent,
        label=pane.title,
    )
