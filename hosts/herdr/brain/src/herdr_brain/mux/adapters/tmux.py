"""tmux adapter: grid, screen reads, keys and tabs — no agent surface.

tmux exposes panes, windows and sessions, but no agent identity: every
pane is reported as a plain shell with status ``unknown``, and the agent
surfaces (detection, session reference, start, wait) are refused rather
than approximated — a guessed agent name would select the wrong grammar
and the wrong journal downstream.

Reads ride ``capture-pane`` (styled with ``-e``, history with
``-S -<n>``); writes ride ``send-keys`` with text passed as ONE argv
element behind ``-l``, so no shell ever sits between the brain and the
pane and metacharacters stay literal. Prompts are submitted
fire-and-forget: tmux cannot report completion, so the prompt outcome is
``unknown`` immediately after Enter.
"""

from __future__ import annotations

import subprocess
from typing import Callable, List, Optional

from ...config import MAX_BACKLOG_LINES, MAX_SCREEN_LINES
from ...herdr import (
    MAX_SEND_KEYS,
    SEND_KEY_ALLOWLIST,
    AgentInfo,
    HerdrError,
    pick_active,
    sanitize_prompt_text,
)
from ..capabilities import Capabilities, MuxProbe
from ..keys import is_sendable_key, normalize_key, tmux_key
from ..refusal import MuxRefusal

Runner = Callable[..., subprocess.CompletedProcess]

_EVIDENCE = {
    "pane_grid": "list-panes -a -F grid pinned by tests/fixtures/mux/tmux/list-panes.txt",
    "ansi_screen": "capture-pane -p [-e] pinned by tests/fixtures/mux/tmux/capture-visible.*",
    "scrollback": "capture-pane -S -<n> history capture (tests/fixtures/mux/tmux/capture-recent.txt)",
    "prompt_submit": "send-keys -l text + Enter, fire-and-forget (tests/test_mux_tmux.py)",
    "keys": "send-keys argv translated from the neutral orthography (tests/test_mux_tmux.py)",
    "tabs": "new-window -P -F '#{window_id} #{pane_id}' (tests/fixtures/mux/tmux/new-window.txt)",
}


def _exit_reason(detail: str) -> str:
    """Maps a tmux failure text onto the refusal vocabulary."""
    text = detail.lower()
    if "no server running" in text or "error connecting" in text:
        return "unreachable"
    if "can't find pane" in text or "can't find window" in text:
        return "gone"
    return "refused"


class TmuxAdapter:
    """tmux behind the port: mirror + keys + tabs, agents refused."""

    #: ``tmux list-panes -a -F`` field order: pane id, session, window,
    #: current path, title, pane-active, window-active, attached clients.
    #: Focused means on screen: the active pane of the active window of a
    #: session that has at least one attached client.
    LIST_FORMAT = (
        "#{pane_id}\t#{session_name}\t#{window_id}\t#{pane_current_path}"
        "\t#{pane_title}\t#{pane_active}\t#{window_active}\t#{session_attached}"
    )

    def __init__(
        self,
        bin_path: str = "tmux",
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
                # No watcher is armed in this port: every listing is a
                # fresh list-panes call, so topology is at most one call
                # (~1s) stale.
                "topology_latency": "bounded:1000",
                "spaces": "many",  # one grid per tmux session
            },
        )

    def probe(self) -> MuxProbe:
        try:
            proc = self._run(
                [self._bin_path, "-V"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return MuxProbe(False, f"tmux did not answer: {exc}")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return MuxProbe(False, f"tmux -V exited with {proc.returncode}: {detail}")
        version = (proc.stdout or "").strip().split()[-1] if proc.stdout.strip() else ""
        return MuxProbe(True, "tmux CLI answered -V", version)

    # -- internals -----------------------------------------------------------

    def _run_cli(self, args: List[str], capability: str, timeout_s: float = 15.0):
        cmd = [self._bin_path, *args]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise MuxRefusal(
                "unreachable", capability,
                f"tmux timed out after {timeout_s}s: {' '.join(args)}",
            ) from exc
        except OSError as exc:
            raise MuxRefusal(
                "unreachable", capability, f"failed to execute tmux CLI: {exc}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            reason = _exit_reason(detail)
            raise MuxRefusal(
                reason, capability, f"tmux exited with {proc.returncode}: {detail}"
            )
        return proc

    def _submit_line(self, pane_id: str, text: str, capability: str) -> None:
        """Types one literal line, then presses Enter (two argv-only calls)."""
        self._run_cli(["send-keys", "-t", pane_id, "-l", text], capability)
        self._run_cli(["send-keys", "-t", pane_id, "Enter"], capability)

    # -- reads ----------------------------------------------------------------

    def list_agents(self) -> List[AgentInfo]:
        proc = self._run_cli(["list-panes", "-a", "-F", self.LIST_FORMAT], "pane_grid")
        agents: List[AgentInfo] = []
        for line in proc.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) < 8:
                continue
            pane_id, _session, _window, cwd, title, pane_active, window_active, attached = (
                fields[:8]
            )
            focused = (
                pane_active == "1" and window_active == "1" and attached not in ("", "0")
            )
            agents.append(
                AgentInfo(
                    pane_id=pane_id,
                    agent="shell",
                    status="unknown",
                    session_kind="",
                    session_value="",
                    cwd=cwd,
                    title=title,
                    focused=focused,
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
        requested = n_lines if n_lines and n_lines > 0 else self._screen_lines
        cap = MAX_BACKLOG_LINES if source != "visible" else MAX_SCREEN_LINES
        clamped = min(requested, cap)
        args = ["capture-pane", "-p", "-t", pane_id]
        if source != "visible":
            args.extend(["-S", f"-{clamped}"])
        if as_ansi:
            args.append("-e")
        capability = "scrollback" if source != "visible" else "ansi_screen"
        proc = self._run_cli(args, capability)
        lines = proc.stdout.splitlines()
        return "\n".join(lines[-clamped:])

    # -- writes -----------------------------------------------------------------

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        unsupported = sorted({key for key in keys if not is_sendable_key(key)})
        if unsupported:
            raise MuxRefusal(
                "refused", "keys",
                f"unsupported key(s): {', '.join(unsupported)} "
                f"(allowed: {', '.join(SEND_KEY_ALLOWLIST)})",
            )
        if not keys:
            raise MuxRefusal("refused", "keys", "refusing to send an empty key sequence")
        if len(keys) > MAX_SEND_KEYS:
            raise MuxRefusal(
                "refused", "keys", f"too many keys: {len(keys)} (max {MAX_SEND_KEYS})"
            )
        translated = [tmux_key(normalize_key(key)) for key in keys]
        try:
            self._run_cli(["send-keys", "-t", pane_id, *translated], "keys")
        except HerdrError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": ""}

    def pane_run(self, pane_id: str, command: str) -> None:
        clean = (command or "").strip()
        if not clean:
            raise HerdrError("refusing to run an empty command")
        self._submit_line(pane_id, clean, "keys")

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send an empty prompt")
        # Fire-and-forget: tmux has no completion signal, so the honest
        # outcome is `unknown` right after Enter.
        self._submit_line(pane_id, clean, "prompt_submit")
        return {"ok": True, "status": "unknown", "output": ""}

    def create_tab(self, label: str, cwd: Optional[str] = None) -> dict:
        args = [
            "new-window", "-P", "-F", "#{window_id} #{pane_id}", "-n", label,
        ]
        if cwd:
            args.extend(["-c", cwd])
        proc = self._run_cli(args, "tabs")
        fields = proc.stdout.strip().split()
        # tmux ids carry their sigil: windows start with @, panes with %.
        if (
            len(fields) != 2
            or not fields[0].startswith("@")
            or not fields[1].startswith("%")
        ):
            excerpt = proc.stdout.strip()[:200]
            raise HerdrError(f"tmux new-window output missing window/pane id: {excerpt!r}")
        return {"tab_id": fields[0], "pane_id": fields[1]}

    def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        timeout_ms: Optional[int] = None,
    ) -> str:
        raise MuxRefusal(
            "unsupported", "agent_start",
            "tmux panes are plain shells: the brain cannot manage agent "
            "lifecycles it cannot detect",
        )

    def agent_wait(self, pane_id: str, until: str = "idle", timeout_ms: int = 15_000) -> None:
        raise MuxRefusal(
            "unsupported", "agent_detection",
            "tmux exposes no agent state; every pane is reported as a shell "
            "with status 'unknown'",
        )
