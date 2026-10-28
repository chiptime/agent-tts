"""zellij adapter: a raw mirror, everything else refused.

The zellij CLI surface this port relies on is exactly two verbs:
listing panes and dumping the visible screen (styled or plain). Logical
keys, prompt submission, tab creation and every agent surface have no
shape this port could pin through that surface, so the adapter REFUSES
them with ``unsupported`` instead of approximating — a wrong keystroke
into a live terminal is silent and hard to undo.

zellij keeps each verb's life inside one session, so the adapter
declares ``spaces="one"``; and because its CLI announces no structure
changes, topology is bounded polling, not push.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, List, Optional

from ...config import MAX_SCREEN_LINES
from ...herdr import AgentInfo, HerdrError, pick_active
from ..capabilities import Capabilities, MuxProbe
from ..refusal import MuxRefusal

Runner = Callable[..., subprocess.CompletedProcess]

_EVIDENCE = {
    "pane_grid": "list-panes --all --json pinned by tests/fixtures/mux/zellij/list-panes.json",
    "ansi_screen": "action dump-screen [--ansi] pinned by tests/fixtures/mux/zellij/dump-screen.*",
}


def _exit_reason(detail: str) -> str:
    """Maps a zellij failure text onto the refusal vocabulary."""
    text = detail.lower()
    if "no active session" in text or "error connecting" in text:
        return "unreachable"
    return "refused"


class ZellijAdapter:
    """zellij behind the port: grid + styled screen, writes refused."""

    def __init__(
        self,
        bin_path: str = "zellij",
        runner: Optional[Runner] = None,
        screen_lines: int = MAX_SCREEN_LINES,
    ) -> None:
        self._bin_path = bin_path
        self._run: Runner = runner if runner is not None else subprocess.run
        self._screen_lines = screen_lines

    @property
    def bin_path(self) -> str:
        return self._bin_path

    def capabilities(self) -> Capabilities:
        return Capabilities(
            supported=frozenset(_EVIDENCE),
            evidence=_EVIDENCE,
            facts={
                # The CLI announces no structure changes: consumers must
                # re-list within a bounded window (~12s).
                "topology_latency": "bounded:12000",
                "spaces": "one",  # each verb lives in a single session
            },
        )

    def probe(self) -> MuxProbe:
        try:
            proc = self._run(
                [self._bin_path, "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return MuxProbe(False, f"zellij did not answer: {exc}")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return MuxProbe(False, f"zellij --version exited with {proc.returncode}: {detail}")
        version = (proc.stdout or "").strip().split()[-1] if proc.stdout.strip() else ""
        return MuxProbe(True, "zellij CLI answered --version", version)

    # -- internals -----------------------------------------------------------

    def _run_cli(self, args: List[str], capability: str, timeout_s: float = 15.0):
        cmd = [self._bin_path, *args]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise MuxRefusal(
                "unreachable", capability,
                f"zellij timed out after {timeout_s}s: {' '.join(args)}",
            ) from exc
        except OSError as exc:
            raise MuxRefusal(
                "unreachable", capability, f"failed to execute zellij CLI: {exc}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            reason = _exit_reason(detail)
            raise MuxRefusal(
                reason, capability, f"zellij exited with {proc.returncode}: {detail}"
            )
        return proc

    # -- the verified surface --------------------------------------------------

    def list_agents(self) -> List[AgentInfo]:
        proc = self._run_cli(["list-panes", "--all", "--json"], "pane_grid")
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as exc:
            excerpt = raw[:200]
            raise HerdrError(f"zellij list-panes returned unparseable output: {excerpt!r}") from exc
        if not isinstance(entries, list):
            excerpt = raw[:200]
            raise HerdrError(f"zellij list-panes returned unparseable output: {excerpt!r}")
        agents: List[AgentInfo] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            agents.append(
                AgentInfo(
                    pane_id=str(entry.get("id", "")),
                    agent="shell",
                    status="unknown",
                    session_kind="",
                    session_value="",
                    cwd=str(entry.get("cwd") or ""),
                    title=str(entry.get("title") or ""),
                    focused=bool(entry.get("is_focused", False)),
                )
            )
        return agents

    def active_agent(self) -> Optional[AgentInfo]:
        return pick_active(self.list_agents())

    def read_screen(
        self,
        pane_id: str,
        n_lines: Optional[int] = None,
        source: str = "visible",
        as_ansi: bool = False,
    ) -> str:
        if source != "visible":
            raise MuxRefusal(
                "unsupported", "scrollback",
                "zellij's verified dump-screen reads the visible screen; "
                "the scrollback surface is unverified, so it is refused "
                "rather than approximated",
            )
        requested = n_lines if n_lines and n_lines > 0 else self._screen_lines
        clamped = min(requested, MAX_SCREEN_LINES)
        args = ["action", "dump-screen", "--pane-id", pane_id]
        if as_ansi:
            args.append("--ansi")
        proc = self._run_cli(args, "ansi_screen")
        lines = proc.stdout.splitlines()
        return "\n".join(lines[-clamped:])

    # -- refused surfaces --------------------------------------------------------

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        raise MuxRefusal(
            "unsupported", "keys",
            "zellij's verified CLI surface writes characters, not logical "
            "key names; keys are refused rather than approximated",
        )

    def pane_run(self, pane_id: str, command: str) -> None:
        raise MuxRefusal(
            "unsupported", "keys",
            "running a line needs a verified Enter; zellij's verified "
            "surface cannot submit one",
        )

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        raise MuxRefusal(
            "unsupported", "prompt_submit",
            "prompt submission needs a verified submit; zellij's verified "
            "surface cannot deliver one",
        )

    def create_tab(self, label: str, cwd: Optional[str] = None) -> dict:
        raise MuxRefusal(
            "unsupported", "tabs",
            "tab creation is outside zellij's verified surface in this port",
        )

    def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        timeout_ms: Optional[int] = None,
    ) -> str:
        raise MuxRefusal(
            "unsupported", "agent_start",
            "zellij panes are plain shells: the brain cannot manage agent "
            "lifecycles it cannot detect",
        )

    def agent_wait(self, pane_id: str, until: str = "idle", timeout_ms: int = 15_000) -> None:
        raise MuxRefusal(
            "unsupported", "agent_detection",
            "zellij exposes no agent state; every pane is reported as a "
            "shell with status 'unknown'",
        )
