"""The port every multiplexer adapter implements.

Mirrors the verified :class:`~herdr_brain.herdr.HerdrClient` surface that
callers depend on today, plus ``capabilities()`` and ``probe()``.
Adapters that lack a surface raise :class:`~herdr_brain.mux.MuxRefusal`
instead of diverging per call — a second multiplexer is a module, not a
branch in every route.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from ..herdr import AgentInfo
from .capabilities import Capabilities, MuxProbe


@runtime_checkable
class MuxAdapter(Protocol):
    """One multiplexer behind the brain's pane surface."""

    def capabilities(self) -> Capabilities:
        """The declared, probed capability matrix of this adapter."""
        ...

    def probe(self) -> MuxProbe:
        """First-hand reachability check; adds facts, never capability cells."""
        ...

    def list_agents(self) -> List[AgentInfo]:
        ...

    def active_agent(self) -> Optional[AgentInfo]:
        ...

    def read_screen(
        self,
        pane_id: str,
        n_lines: Optional[int] = None,
        source: str = "visible",
        as_ansi: bool = False,
    ) -> str:
        ...

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        ...

    def pane_run(self, pane_id: str, command: str) -> None:
        ...

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        ...

    def create_tab(self, label: str, cwd: Optional[str] = None) -> dict:
        ...

    def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        timeout_ms: Optional[int] = None,
    ) -> str:
        ...

    def agent_wait(self, pane_id: str, until: str = "idle", timeout_ms: int = 15_000) -> None:
        ...
