"""herdr adapter: composition, not a rewrite.

Every call delegates verbatim to the verified :class:`HerdrClient` the
rest of the brain already uses — the client keeps its "CLI, never the
raw socket" rule and this adapter adds only the port's surface
(capabilities, probe). With this adapter selected, observed behavior is
identical to pre-port brain, which makes it the specification the other
adapters are measured against.
"""

from __future__ import annotations

import subprocess
from typing import List, Optional

from ...herdr import AgentInfo, HerdrClient
from ..capabilities import Capabilities, MuxProbe

#: Every cell herdr has, each backed by the verified CLI surface pinned
#: by the existing herdr suites (the recorded evidence for this matrix).
_EVIDENCE = {
    "pane_grid": "herdr agent list NDJSON envelope (tests/test_herdr.py)",
    "ansi_screen": "herdr agent read --format ansi (tests/test_herdr.py)",
    "scrollback": "herdr agent read --source recent, capped backlog (tests/test_herdr.py)",
    "prompt_submit": "herdr agent prompt --wait --until done (tests/test_herdr.py)",
    "keys": "herdr agent send-keys behind the dialog allowlist (tests/test_herdr.py)",
    "tabs": "herdr tab create envelope with tab_id/root_pane (tests/test_herdr_client.py)",
    "agent_start": "herdr agent start --kind --pane --timeout (tests/test_herdr_client.py)",
    "agent_detection": "agent_status per pane in agent list output (tests/test_herdr.py)",
    "session_ref": "agent_session kind/value in agent list output (tests/test_herdr.py)",
}


class HerdrMuxAdapter:
    """The herdr multiplexer as a port adapter (full matrix)."""

    def __init__(self, client: HerdrClient) -> None:
        self._client = client

    def capabilities(self) -> Capabilities:
        return Capabilities(
            supported=frozenset(_EVIDENCE),
            evidence=_EVIDENCE,
            facts={
                # The daemon pushes structure events; the server already
                # rides them through /events.
                "topology_latency": "push",
                "spaces": "many",
            },
        )

    def probe(self) -> MuxProbe:
        # Composition reaches into the client's runner and settings: the
        # client exposes no public injection surface and the port must
        # not re-wrap the CLI to ask one question.
        client = self._client
        try:
            proc = client._run(
                [client._settings.herdr_bin, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return MuxProbe(False, f"herdr did not answer: {exc}")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return MuxProbe(False, f"herdr --help exited with {proc.returncode}: {detail}")
        return MuxProbe(True, "herdr CLI answered --help")

    # -- verbatim delegation ------------------------------------------------

    def list_agents(self) -> List[AgentInfo]:
        return self._client.list_agents()

    def active_agent(self) -> Optional[AgentInfo]:
        return self._client.active_agent()

    def read_screen(
        self,
        pane_id: str,
        n_lines: Optional[int] = None,
        source: str = "visible",
        as_ansi: bool = False,
    ) -> str:
        return self._client.read_screen(
            pane_id, n_lines=n_lines, source=source, as_ansi=as_ansi
        )

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        return self._client.send_keys(pane_id, keys)

    def pane_run(self, pane_id: str, command: str) -> None:
        return self._client.pane_run(pane_id, command)

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        return self._client.send_prompt(pane_id, text, timeout_ms=timeout_ms)

    def create_tab(self, label: str, cwd: Optional[str] = None) -> dict:
        return self._client.create_tab(label, cwd=cwd)

    def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        timeout_ms: Optional[int] = None,
    ) -> str:
        return self._client.start_agent(name, kind, pane_id, timeout_ms=timeout_ms)

    def agent_wait(self, pane_id: str, until: str = "idle", timeout_ms: int = 15_000) -> None:
        return self._client.agent_wait(pane_id, until=until, timeout_ms=timeout_ms)
