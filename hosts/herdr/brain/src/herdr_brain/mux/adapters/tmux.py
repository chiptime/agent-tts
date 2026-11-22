"""tmux adapter: grid, screen reads, keys, tabs and verified prompts.

tmux exposes panes, windows and sessions, but no agent identity: every
pane is reported as a plain shell with status ``unknown``, and the agent
surfaces (detection, session reference, start, wait) are refused rather
than approximated — a guessed agent name would select the wrong grammar
and the wrong journal downstream.

Reads ride ``capture-pane`` (styled with ``-e``, history with
``-S -<n>``); writes deliver text through a PRIVATE paste buffer — the
exact bytes travel by stdin (``load-buffer <name> -``), are pasted
byte-exact into the target pane (``paste-buffer -d -S``) and the buffer
is deleted, with ``send-keys`` reduced to the ``Enter`` key — and
(parity wave 2 S5) bare typing with NO submit at all (``send_text``):
the same private-buffer pipeline with nothing after the paste. No shell
ever sits between the brain and the pane, and the text never rides argv:
tmux 3.8 drops a trailing ``;`` from ``send-keys -l`` while still
exiting 0 (live probe in ``tests/test_mux_tmux_live.py``), so argv is
treated as a surface tmux may alter, and stdin as the one it cannot.
Prompt submission is verified by re-reading the pane with
``capture-pane`` BEFORE and immediately AFTER the send and classifying
with the SAME receipt rules herdr rides
(:func:`herdr_brain.send_receipts.classify_send`). tmux vouches the
WRITE only (``send-keys`` exited 0), never completion, so the claim
handed to the classifier stays neutral: the screens decide ``sent`` /
``typed_not_submitted`` / ``unknown``, and ``effective`` (turn
completion) is never claimed — tmux exposes no agent-turn state.
"""

from __future__ import annotations

import subprocess
import uuid
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
from ...send_receipts import classify_send, text_echoed_on_input
from ..capabilities import Capabilities, MuxProbe
from ..keys import is_recipe_key, is_sendable_key, normalize_key, tmux_key
from ..refusal import MuxRefusal

Runner = Callable[..., subprocess.CompletedProcess]

#: How many immediate re-reads back the receipt after a submit. No
#: sleeps here: the route's own verified poll adds the time-spaced
#: evidence; these reads are the first snapshot.
RECEIPT_AFTER_READS = 2

_EVIDENCE = {
    "pane_grid": "list-panes -a -F grid pinned by tests/fixtures/mux/tmux/list-panes.txt",
    "ansi_screen": "capture-pane -p [-e] pinned by tests/fixtures/mux/tmux/capture-visible.*",
    "scrollback": "capture-pane -S -<n> history capture (tests/fixtures/mux/tmux/capture-recent.txt)",
    "prompt_submit": "load-buffer -> paste-buffer -d -S -> send-keys Enter "
                     "verified by capture-pane re-reads (tests/test_mux_tmux.py, "
                     "tests/test_mux_tmux_live.py)",
    "keys": "send-keys argv translated from the neutral orthography (tests/test_mux_tmux.py)",
    "tabs": "new-window -d -P -F '#{window_id} #{pane_id}' (tests/fixtures/mux/tmux/new-window.txt, tests/test_mux_tmux_live.py)",
}


def _exit_reason(detail: str) -> str:
    """Maps a tmux failure text onto the refusal vocabulary.

    S12 A2: connection-loss stories are TRANSPORT, not refusals — the
    only retry-worthy reason is ``unreachable``. tmux 3.8 carries the
    constants ``server exited``, ``server exited unexpectedly`` (both
    live-probed mid-call on an isolated ``-L`` server), ``no server
    running on %s`` and ``error connecting to %s (%s)``; the substring
    ``server exited`` covers those variants and any suffix, and
    ``lost server`` is reference-pinned (Collie's tmux NO_SERVER:
    "a server that died DURING the call is `unreachable` too — the
    transport, not a refusal"). A missing pane/window target is the
    multiplexer understanding and saying no: ``gone``. Everything
    else stays ``refused``.
    """
    text = detail.lower()
    if (
        "no server running" in text
        or "error connecting" in text
        or "lost server" in text
        or "server exited" in text
    ):
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

    #: ``tmux list-clients -F``: session name and the client's control
    #: mode, one line per attached client. ``session_attached`` counts
    #: control clients too, so this cross-check decides who is REALLY
    #: watching a session.
    LIST_CLIENTS_FORMAT = "#{session_name}|#{client_control_mode}"

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
                # fresh list-panes + list-clients pair, so topology is
                # at most two calls (~1s) stale.
                "topology_latency": "bounded:1000",
                "spaces": "many",  # one grid per tmux session
            },
        )

    def probe(self) -> MuxProbe:
        """Reachability = the SERVER answers; ``-V`` is the version source only.

        Finding A (live, tmux 3.8): ``tmux -V`` is a client-side flag —
        a wrapper pointed at a nonexistent socket still prints the
        version, so a probe built on it claims reachable with no server
        at all. The reachability fact comes from one server roundtrip
        (``list-sessions``, the cheapest command that needs the server
        to exist); ``-V`` keeps answering the version field, and an
        unreachable probe may still carry that version.
        """
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
        try:
            self._run_cli(["list-sessions"], "pane_grid", timeout_s=10)
        except HerdrError as exc:
            # The failure text rides along: "no server running" and
            # "error connecting" name the socket that refused us.
            return MuxProbe(False, f"tmux server did not answer: {exc}", version)
        detail = "tmux server answered list-sessions"
        if version:
            detail = f"{detail} (tmux {version})"
        return MuxProbe(True, detail, version)

    # -- internals -----------------------------------------------------------

    def _run_cli(
        self,
        args: List[str],
        capability: str,
        timeout_s: float = 15.0,
        input_text: Optional[str] = None,
    ):
        cmd = [self._bin_path, *args]
        try:
            proc = self._run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                input=input_text,
            )
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

    #: Private-buffer prefix: never a tmux default buffer (``0``..``9``)
    #: and never a foreign named buffer; the uuid suffix makes concurrent
    #: sends in one server collision-free.
    PASTE_BUFFER_PREFIX = "herdr-paste-"

    def _paste_text(
        self, pane_id: str, text: str, capability: str, *, submit: bool
    ) -> None:
        """Delivers one literal line via a private buffer, optionally with Enter.

        Finding C (live, tmux 3.8 isolated probe): ``send-keys -l``
        silently dropped a trailing ``;`` while still exiting 0, and the
        receipt even read ``sent`` on the altered write. The exact bytes
        therefore never ride argv — argv is a surface tmux 3.8 can alter;
        stdin is not. ``load-buffer`` pulls the text from stdin into a PRIVATE uniquely
        named buffer (never a default ``0``..``9`` buffer, never a foreign
        name), ``paste-buffer -d -S`` inserts it byte-exact into the
        TARGET pane by id (``-S`` keeps tmux from vis-escaping control
        characters, matching the raw bytes ``send-keys -l`` used to
        deliver) and ``-d`` deletes the buffer in the same operation, and
        any submit rides the SAME client invocation with tmux's ``;``
        separator — the payload text only ever rides stdin, and one
        invocation narrows the window in which another client's command
        could run between the paste and the Enter. A best-effort
        ``delete-buffer`` runs on EVERY path; honestly, if that delete
        itself fails, the payload survives under this private uuid name
        until the server exits — never under a default or foreign name.
        Only buffers this operation created are ever touched.
        """
        buffer_name = f"{self.PASTE_BUFFER_PREFIX}{uuid.uuid4().hex}"
        paste = ["paste-buffer", "-d", "-S", "-b", buffer_name, "-t", pane_id]
        if submit:
            paste.extend([";", "send-keys", "-t", pane_id, "Enter"])
        try:
            self._run_cli(
                ["load-buffer", "-b", buffer_name, "-"], capability,
                input_text=text,
            )
            self._run_cli(paste, capability)
        finally:
            self._delete_paste_buffer(buffer_name, capability)

    def _delete_paste_buffer(self, buffer_name: str, capability: str) -> None:
        """Best-effort delete of a buffer THIS call created; never raises.

        A paste that already succeeded deleted its buffer via ``-d``; the
        delete then fails harmlessly for the same uuid name. If the
        delete itself fails, the payload may persist under this private
        uuid-named buffer until the server exits — never under a tmux
        default (``0``..``9``) or a foreign actor's named buffer.
        """
        try:
            self._run_cli(["delete-buffer", "-b", buffer_name], capability)
        except HerdrError:
            pass

    def _verified_read(self, pane_id: str) -> Optional[str]:
        """One FULL plain-text grid read; None on failure (evidence, not truth).

        Finding B (live, tmux 3.8): ``read_screen`` serves the display
        budget — the last ``screen_lines`` rows — and a pane taller than
        that budget holds its prompt/echo rows at the TOP with blank
        rows below, so the tail slice saw only blanks and the receipt
        degraded to an honest-but-wrong ``unknown``. The classification
        path therefore skips the budget clamp and compares WHOLE grids;
        ``read_screen``'s public tail semantics stay the display budget
        for consumers. Plain text (no ``-e``): the classifier matches
        echo tails on unstyled lines.
        """
        try:
            proc = self._run_cli(
                ["capture-pane", "-p", "-t", pane_id], "ansi_screen"
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

    # -- reads ----------------------------------------------------------------

    def _interactive_client_sessions(self) -> set:
        """Sessions with at least one attached NON-control client.

        Finding (live, tmux 3.8): control clients (``tmux -C``) count
        into ``#{session_attached}`` — with one attached, a session
        driven purely by a program looked "on screen" with no human
        watching. ``session_control_clients`` does not exist as a
        session-level format in tmux 3.8 (rendered empty while a
        control client was attached), but the per-client
        ``#{client_control_mode}`` does: only an explicit ``1`` marks a
        control client, an empty or ``0`` mode is a real client.
        """
        proc = self._run_cli(
            ["list-clients", "-F", self.LIST_CLIENTS_FORMAT], "pane_grid"
        )
        sessions = set()
        for line in proc.stdout.splitlines():
            fields = line.split("|")
            if not fields or not fields[0]:
                continue
            if len(fields) < 2 or fields[1].strip() != "1":
                sessions.add(fields[0])
        return sessions

    def list_agents(self) -> List[AgentInfo]:
        proc = self._run_cli(["list-panes", "-a", "-F", self.LIST_FORMAT], "pane_grid")
        interactive = self._interactive_client_sessions()
        agents: List[AgentInfo] = []
        for line in proc.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) < 8:
                continue
            pane_id, session, _window, cwd, title, pane_active, window_active, _attached = (
                fields[:8]
            )
            focused = (
                pane_active == "1"
                and window_active == "1"
                and session in interactive
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

    def send_recipe_keys(self, pane_id: str, keys: List[str]) -> dict:
        """The guarded dialog-recipe surface (T10/S3): the plain allowlist
        plus the ONE lowercase letters a dialog's own footer named. A
        literal letter types itself in tmux's key table (the translation
        already maps a single-literal spelling), so nothing new reaches
        the CLI — only the validation gate widens, and only on this
        recipe method, which the guarded dialog answer alone calls."""
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
        self._paste_text(pane_id, clean, "keys", submit=True)

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send an empty prompt")
        # f7f S2: the fire-and-forget write gains its verified read-back.
        # Plain-text capture (the classifier matches echo tails on
        # unstyled lines), the SAME classifier herdr rides, and a
        # neutral claim: tmux vouches the WRITE only, so an unreadable
        # screen stays honestly `unknown` instead of upgrading to
        # `sent`, and `effective` is never claimed.
        before = self._verified_read(pane_id)
        self._paste_text(pane_id, clean, "prompt_submit", submit=True)
        afters = self._verified_poll_reads(pane_id, before, clean)
        outcome, detail = classify_send(before, afters, clean, "unknown")
        return {"ok": outcome in ("effective", "sent"), "status": outcome, "output": detail}

    def send_text(self, pane_id: str, text: str) -> None:
        """Types literal text WITHOUT Enter (parity wave 2 S5).

        The operator opened this surface deliberately: tmux's
        ``paste-buffer`` can deliver text with no submit, so refusing it
        was refusing something tmux verifiably does. It rides the SAME
        exact path the verified submit rides (private buffer, text on
        stdin, never argv — the T7 fix), minus the Enter: typing only.
        Sanitized to ONE line exactly like herdr's ``send_text`` (a
        literal newline would submit the field early), empty refused
        before any spawn, and a CLI failure raises — the dialog recipe
        re-reads the screen itself, so no receipt is fabricated here.
        """
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send empty text")
        self._paste_text(pane_id, clean, "send_text", submit=False)

    def create_tab(self, label: str, cwd: Optional[str] = None) -> dict:
        # Finding (live, tmux 3.8 isolated probe): ``new-window`` without
        # ``-d`` made the new window the session's current one — visible
        # focus theft from whoever is attached. ``-d`` creates the window
        # WITHOUT switching; id printing stays unchanged.
        args = [
            "new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}",
            "-n", label,
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
