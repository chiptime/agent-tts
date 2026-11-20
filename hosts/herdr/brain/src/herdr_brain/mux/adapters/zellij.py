"""zellij adapter: grid, screen dump, verified submit — the rest refused.

The zellij CLI surface this port relies on is three verbs: listing
panes, dumping the visible screen (styled or plain), and (f7f S2)
submitting one prompt line — ``write-chars`` types the sanitized text
as ONE argv element, ``write 10`` submits it (Enter as the LF byte),
both targeting the pane exactly like ``dump-screen`` does. The submit
is verified by re-reading with ``dump-screen`` before and after and
classifying with the SAME receipt rules herdr rides. UNVERIFIED live:
zellij is not installed on this host (f7f D2) — the write-path CLI
shapes are derived and pinned by fixtures; re-pin against a live
session before trusting them.

Logical keys, bare text typing, tab creation and every agent surface
still have no shape this port could pin through that surface, so the
adapter REFUSES them with ``unsupported`` instead of approximating — a
wrong keystroke into a live terminal is silent and hard to undo.

zellij keeps each verb's life inside one session, so the adapter
declares ``spaces="one"``; and because its CLI announces no structure
changes, topology is bounded polling, not push.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, List, Optional

from ...config import MAX_SCREEN_LINES
from ...herdr import AgentInfo, HerdrError, pick_active, sanitize_prompt_text
from ...send_receipts import classify_send, text_echoed_on_input
from ..capabilities import Capabilities, MuxProbe
from ..refusal import MuxRefusal

Runner = Callable[..., subprocess.CompletedProcess]

#: How many immediate re-reads back the receipt after a submit. No
#: sleeps here: the route's own verified poll adds the time-spaced
#: evidence; these reads are the first snapshot.
RECEIPT_AFTER_READS = 2

_EVIDENCE = {
    "pane_grid": "list-panes --all --json pinned by tests/fixtures/mux/zellij/list-panes.json",
    "ansi_screen": "action dump-screen [--ansi] pinned by tests/fixtures/mux/zellij/dump-screen.*",
    "prompt_submit": (
        "action write-chars + action write 10 (Enter) verified by dump-screen "
        "re-reads (tests/test_mux_zellij.py; CLI-shape derived, UNVERIFIED "
        "live — zellij not installed, f7f D2)"
    ),
}


def _exit_reason(detail: str) -> str:
    """Maps a zellij failure text onto the refusal vocabulary."""
    text = detail.lower()
    if "no active session" in text or "error connecting" in text:
        return "unreachable"
    return "refused"


class ZellijAdapter:
    """zellij behind the port: grid + styled screen + verified submit."""

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

    def _verified_read(self, pane_id: str) -> Optional[str]:
        """One plain-text screen read; None on failure (evidence, not truth)."""
        try:
            return self.read_screen(pane_id)
        except HerdrError:
            return None

    def _verified_poll_reads(
        self, pane_id: str, before: Optional[str], sent_text: str
    ) -> List[Optional[str]]:
        """Immediate bounded re-reads after a submit (no sleeps).

        Stops early once the evidence is decisive — the body moved and
        the text left the input line — mirroring the tools layer's poll
        rule on the adapter's first snapshot.
        """
        afters: List[Optional[str]] = []
        for _ in range(RECEIPT_AFTER_READS):
            after = self._verified_read(pane_id)
            afters.append(after)
            if (
                after is not None
                and after != before
                and not text_echoed_on_input(after, sent_text)
            ):
                break  # the body moved and the text left the input line
        return afters

    def _submit_line(self, pane_id: str, text: str) -> None:
        """Types one literal line, then submits it with Enter.

        The text rides as ONE argv element — no shell sits between the
        brain and the CLI, so quotes, ``$()`` and backticks stay
        literal — and Enter is the byte shape ``write 10`` (LF). Both
        calls target the pane exactly like ``dump-screen`` does
        (``--pane-id``); UNVERIFIED live (see module docstring).
        """
        self._run_cli(
            ["action", "write-chars", "--pane-id", pane_id, text], "prompt_submit"
        )
        self._run_cli(["action", "write", "--pane-id", pane_id, "10"], "prompt_submit")

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
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send an empty prompt")
        # f7f S2: zellij becomes a WRITE surface. The dump-screen
        # re-read before and after the submit feeds the SAME classifier
        # herdr rides, under a neutral claim: zellij vouches the WRITE
        # only, so an unreadable screen stays honestly `unknown` and
        # `effective` is never claimed (no agent-turn state exists
        # through this surface).
        before = self._verified_read(pane_id)
        self._submit_line(pane_id, clean)
        afters = self._verified_poll_reads(pane_id, before, clean)
        outcome, detail = classify_send(before, afters, clean, "unknown")
        return {"ok": outcome in ("effective", "sent"), "status": outcome, "output": detail}

    def send_text(self, pane_id: str, text: str) -> None:
        raise MuxRefusal(
            "unsupported", "send_text",
            "bare text typing needs a verified typing surface; zellij's "
            "verified surface cannot deliver one",
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
