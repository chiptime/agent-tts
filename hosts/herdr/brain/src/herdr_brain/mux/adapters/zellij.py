"""zellij adapter: grid, screen dump, verified submit, keys.

The zellij CLI surface this port relies on is four verbs, all
LIVE-PINNED against an isolated real session on zellij 0.45.1 (parity
wave 2 S6): ``action list-panes --all --json`` (the top-level
``list-panes`` does NOT exist — exit 2, unrecognized subcommand),
``action dump-screen [--ansi]``, the prompt submit (``write-chars``
types the sanitized text as ONE argv element, ``write 10`` presses
Enter as the LF byte) and ``action send-keys`` for logical keys
(chords SPACE-separated: ``Ctrl u``; the ``Ctrl+u`` spelling exits 2
with ``Invalid key``). The submit is verified by re-reading with
``dump-screen`` before and after and classifying with the SAME receipt
rules herdr rides.

Two honesty edges the live probes proved, both shaped into this
adapter:

- Plugin panes ride the same listing with ``is_plugin: true`` — and a
  plugin pane (the floating First-Run Setup Wizard) can be
  ``is_focused``. Plugins are FILTERED before parsing: they are never
  agents, never active.
- zellij fails SILENTLY on nonexistent panes: ``write-chars`` /
  ``dump-screen`` targeting an unknown pane exit 0 with no output. The
  write path never trusts exit codes; the receipt's before/after
  screen comparison keeps an unreadable or vanished pane honestly
  ``unknown``.

Tab creation and every agent surface remain outside the opened
surface, so the adapter REFUSES those with ``unsupported`` instead of
approximating — a wrong keystroke into a live terminal is silent and
hard to undo.

zellij keeps each verb's life inside one session, so the adapter
declares ``spaces="one"``; and because its CLI announces no structure
changes, topology is bounded polling, not push.
"""

from __future__ import annotations

import json
import subprocess
from typing import Callable, List, Optional

from ...config import MAX_SCREEN_LINES
from ...herdr import (
    MAX_SEND_KEYS,
    SEND_KEY_ALLOWLIST,
    AgentInfo,
    HerdrError,
    pick_active,
    sanitize_prompt_text,
)
from ...send_receipts import classify_send, text_echoed_on_input
from ..capabilities import Capabilities, MuxProbe
from ..keys import is_recipe_key, is_sendable_key, normalize_key, zellij_key
from ..refusal import MuxRefusal

Runner = Callable[..., subprocess.CompletedProcess]
_LIST_PANES = ("action", "list-panes", "--all", "--json")

#: How many immediate re-reads back the receipt after a submit. No
#: sleeps here: the route's own verified poll adds the time-spaced
#: evidence; these reads are the first snapshot.
RECEIPT_AFTER_READS = 2

_EVIDENCE = {
    "pane_grid": "action list-panes --all --json pinned by "
                 "tests/fixtures/mux/zellij/list-panes.json (captured live "
                 "on zellij 0.45.1; plugins filtered)",
    "ansi_screen": "action dump-screen [--ansi] pinned by "
                   "tests/fixtures/mux/zellij/dump-screen.* (captured live "
                   "on zellij 0.45.1)",
    "prompt_submit": (
        "action write-chars + action write 10 (Enter) verified by dump-screen "
        "re-reads (tests/test_mux_zellij.py recorded; "
        "tests/test_mux_zellij_live.py live on zellij 0.45.1)"
    ),
    "keys": (
        "action send-keys, chords space-separated (Ctrl u), translated from "
        "the neutral orthography (tests/test_mux_zellij.py; probed live on "
        "zellij 0.45.1)"
    ),
}


def _session_failure(detail: str) -> Optional[str]:
    """Recognize session diagnostics independently of the CLI exit status."""
    text = detail.lower()
    if (
        "no active session" in text
        or "no active zellij sessions" in text
        or "error connecting" in text
        or (text.startswith("session ") and "not found. the following sessions are active:" in text)
    ):
        return "unreachable"
    if text.startswith("please specify the session name to send actions to."):
        return "refused"
    return None


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
        """Reachability = live session state answers; ``--version`` is
        the version source only.

        ``zellij --version`` is a client-side flag and says nothing
        about any session. The reachability fact comes from one
        session-state roundtrip on the adapter's OWN pinned verb —
        ``action list-panes --all --json``, the same call
        ``list_agents`` rides; ``--version`` keeps answering the
        version field, and an unreachable probe may still carry that
        version. LIVE-PINNED (parity wave 2 S6): without a session the
        real 0.45.1 CLI exits 1 with ``There is no active session!``.
        """
        try:
            proc = self._run(
                [self._bin_path, "--version"], capture_output=True, text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return MuxProbe(False, f"zellij did not answer: {exc}")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return MuxProbe(
                False, f"zellij --version exited with {proc.returncode}: {detail}"
            )
        version = (proc.stdout or "").strip().split()[-1] if proc.stdout.strip() else ""
        try:
            self._run_cli(
                list(_LIST_PANES), "pane_grid",
                timeout_s=10,
            )
        except HerdrError as exc:
            return MuxProbe(
                False, f"zellij session state did not answer: {exc}", version
            )
        detail = "zellij session state answered action list-panes"
        if version:
            detail = f"{detail} (zellij {version})"
        return MuxProbe(True, detail, version)

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
        # Session selection can print a diagnostic and a session list while
        # exiting zero. Only interpret stderr on success: pane text in stdout
        # may legitimately contain the same words.
        stderr = (proc.stderr or "").strip()
        detail = stderr or ((proc.stdout or "").strip() if proc.returncode else "")
        reason = _session_failure(detail)
        if proc.returncode != 0 or reason is not None:
            raise MuxRefusal(
                reason or "refused", capability,
                f"zellij action failed (exit {proc.returncode}): {detail}",
            )
        return proc

    # -- the verified surface --------------------------------------------------

    def _verified_read(self, pane_id: str) -> Optional[str]:
        """One FULL plain-text grid read; None on failure (evidence, not truth).

        Finding B (live on tmux 3.8, mirrored here because both adapters
        share the budgeted-tail pattern): ``read_screen`` serves the
        display budget — the last ``screen_lines`` rows — and a pane
        taller than that budget holds its prompt/echo rows at the TOP
        with blank rows below, so the tail slice saw only blanks and the
        receipt degraded to an honest-but-wrong ``unknown``. The
        classification path therefore skips the budget clamp and
        compares WHOLE grids; ``read_screen``'s public tail semantics
        stay the display budget for consumers.
        """
        try:
            proc = self._run_cli(
                ["action", "dump-screen", "--pane-id", pane_id], "ansi_screen"
            )
            return "\n".join(proc.stdout.splitlines())
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

    def _write_chars(self, pane_id: str, text: str, capability: str) -> None:
        args = ["action", "write-chars", "--pane-id", pane_id]
        if text.startswith("-"):
            args.append("--")
        self._run_cli([*args, text], capability)

    def _submit_line(self, pane_id: str, text: str) -> None:
        """Types one literal line, then submits it with Enter.

        The text rides as ONE argv element — no shell sits between the
        brain and the CLI, so quotes, ``$()`` and backticks stay
        literal — and Enter is the byte shape ``write 10`` (LF). Both
        calls target the pane exactly like ``dump-screen`` does
        (``--pane-id``); LIVE-PINNED on zellij 0.45.1. Note the live
        honesty edge: an unknown pane id exits 0 silently, so callers
        who claim an outcome must ride the receipt's screen comparison,
        never the exit code.
        """
        self._write_chars(pane_id, text, "prompt_submit")
        self._run_cli(
            ["action", "write", "--pane-id", pane_id, "10"], "prompt_submit"
        )

    def list_agents(self) -> List[AgentInfo]:
        proc = self._run_cli(list(_LIST_PANES), "pane_grid")
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
            # Real 0.45.1 grids ride plugin panes too (status bar, and a
            # floating First-Run Setup Wizard that can be `is_focused`).
            # A plugin is never an agent and never the active one —
            # filtered before anything else looks at the entry.
            if entry.get("is_plugin"):
                continue
            agents.append(
                AgentInfo(
                    pane_id=str(entry.get("id", "")),
                    agent="shell",
                    status="unknown",
                    session_kind="",
                    session_value="",
                    cwd=str(entry.get("pane_cwd") or ""),
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

    # -- writes -----------------------------------------------------------------

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        """Sends logical key presses via ``action send-keys`` (S6).

        The zellij verb exists in 0.45.1 and its dialect was probed
        live: named keys are capitalized, chords are SPACE-separated.
        The allowlist guard runs BEFORE any spawn — the same closed
        set, translated through the neutral orthography — and a CLI
        failure returns ``{ok: False}`` instead of raising, exactly
        like the tmux and herdr adapters.
        """
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
        translated = [zellij_key(normalize_key(key)) for key in keys]
        try:
            self._run_cli(
                ["action", "send-keys", "--pane-id", pane_id, *translated], "keys"
            )
        except HerdrError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": ""}

    def send_recipe_keys(self, pane_id: str, keys: List[str]) -> dict:
        """The guarded dialog-recipe surface (T10/S3): the plain allowlist
        plus the ONE lowercase letters a dialog's own footer named. A
        single literal character types itself in zellij's send-keys
        spelling (the live-probed translation already maps it), so only
        the validation gate widens — and only on this method, which the
        guarded dialog answer alone calls."""
        unsupported = sorted({key for key in keys if not is_recipe_key(key)})
        if unsupported:
            raise MuxRefusal(
                "refused", "keys",
                f"unsupported key(s): {', '.join(unsupported)} "
                "(allowed: the dialog key dialect plus footer-named letters)",
            )
        if not keys:
            raise MuxRefusal("refused", "keys", "refusing to send an empty key sequence")
        if len(keys) > MAX_SEND_KEYS:
            raise MuxRefusal(
                "refused", "keys", f"too many keys: {len(keys)} (max {MAX_SEND_KEYS})"
            )
        translated = [zellij_key(normalize_key(key)) for key in keys]
        try:
            self._run_cli(
                ["action", "send-keys", "--pane-id", pane_id, *translated], "keys"
            )
        except HerdrError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": ""}

    def pane_run(self, pane_id: str, command: str) -> None:
        raise MuxRefusal(
            "unsupported", "keys", "zellij supports verified prompt submission, "
            "not pane_run through this adapter",
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
        """Types literal text WITHOUT Enter (fleet parity-w2 lead decision).

        The lead opened this surface for zellij, consistent with the
        operator's tmux S5 decision: ``write-chars`` alone delivers the
        exact bytes and NO submit ride follows. The contract mirrors
        herdr's ``send_text``: sanitized to ONE line (a literal newline
        would submit the field early), empty refused before any spawn,
        and a CLI failure raises — the dialog recipe re-reads the
        screen itself. zellij's honesty edge applies: a nonexistent
        pane answers rc 0 silently, so this call claims nothing; the
        caller's screen re-read is where a silent pane shows up.
        """
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send empty text")
        self._write_chars(pane_id, clean, "send_text")

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
