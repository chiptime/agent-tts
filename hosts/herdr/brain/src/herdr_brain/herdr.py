"""Wrapper over the verified ``herdr`` CLI surface.

The brain shells out to the CLI (never the raw socket: raw JSON field names
are unverified). Verified flags (from ``--help``):

- ``herdr agent list``
- ``herdr agent read <TARGET> [--source visible|recent|recent-unwrapped|detection]
  [--lines N] [--format text|ansi] [--ansi]``
- ``herdr agent prompt <TARGET> <TEXT> [--wait]
  [--until idle|working|blocked|done|unknown] [--timeout MS]``

Safety: ``send_prompt`` is the ONLY write path and it targets real live agent
sessions owned by the user. All other calls are read-only.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

from .config import MAX_BACKLOG_LINES, MAX_SCREEN_LINES, Settings

Runner = Callable[..., subprocess.CompletedProcess]

# Well-known failure statuses reported by `herdr agent prompt`. They travel
# in the CLI's JSON error envelope on STDERR with a non-zero exit (verified
# against herdr 0.9.1), NOT as a status on stdout.
_PROMPT_BLOCKED = "agent_blocked"
_PROMPT_STALLED = "agent_prompt_stalled"
_PROMPT_TIMEOUT = "timeout"

# Those codes describe a SUBMISSION OUTCOME, not a failed invocation: the
# prompt was either refused before any input (blocked) or already written to
# the pane (stalled / timeout). Mapping them to a status keeps the exit code
# from being read as the verdict, which would turn a delivered prompt into a
# failure and invite a resend that duplicates it.
_PROMPT_OUTCOME_CODES = {
    _PROMPT_BLOCKED: "blocked",
    _PROMPT_STALLED: "stalled",
    _PROMPT_TIMEOUT: "timeout",
}

#: The ONLY keys the brain will forward to an agent. A permission dialog is
#: answered by navigating (arrows/tab), confirming (enter), dismissing
#: (esc) or picking a NUMBERED option directly (digits 0-9: the dialogs
#: that print numbers commit on them — everything else is still refused).
#: The free-text recipe (S3) adds EXACTLY two chords, ``ctrl+u`` and
#: ``ctrl+k``: they clear the field the recipe is about to type into
#: (herdr accepts ctrl+letter spellings). Free TEXT itself never rides
#: this surface — it goes through :meth:`HerdrClient.send_text` with its
#: own sanitization. herdr validates keys server-side too; this list is
#: the brain's own guard so nothing outside it can ever reach the CLI.
#: Verified against herdr 0.9.1 (``esc`` is the canonical Escape name).
SEND_KEY_ALLOWLIST = (
    "up", "down", "left", "right", "enter", "esc", "tab",
    "ctrl+u", "ctrl+k",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
)

#: A dialog is answered by a short, deliberate sequence. Anything longer is a
#: caller bug, not a keystroke.
MAX_SEND_KEYS = 8

# The key dialect a dialog may be driven with. Probed live against herdr
# 0.9.1: these names are accepted, while `arrowleft`, `leftarrow` and
# `ArrowLeft` come back as `invalid_key` with nothing written. The set is
# closed on purpose — a name that is not here never reaches a live terminal,
# and `sendKeys` argv is one door into a running TUI. Digits 0-9 join the
# dialect for the dialogs that print numbered options (a digit is ONE key,
# never a multi-digit number: the tenth option stays out of reach).
DIALOG_KEYS = frozenset({"enter", "esc", "left", "right", "up", "down", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"})


def is_valid_key(key: str) -> bool:
    """Whether herdr's grammar accepts `key` (the verified subset)."""
    if key in DIALOG_KEYS:
        return True
    # `ctrl+c` is the spelling herdr's own documentation uses: a modifier and
    # exactly one lower-case letter.
    if key.startswith("ctrl+") and len(key) == 6:
        return key[5].isalpha() and key[5].islower()
    return False


class HerdrError(RuntimeError):
    """Raised when a herdr CLI invocation fails."""


@dataclass(frozen=True)
class AgentInfo:
    """One agent pane as reported by ``herdr agent list``."""

    pane_id: str
    agent: str
    status: str
    session_kind: str
    session_value: str
    cwd: str
    title: str
    focused: bool


@dataclass(frozen=True)
class WorkspaceInfo:
    """One workspace as reported by ``herdr workspace list``.

    The CLI's workspace_list carries NO cwd (probed live, parity-f7c
    S1b): a space's folder is derived by the caller from its live
    agent panes (``AgentInfo.pane_id`` prefixes with the workspace
    id, e.g. pane ``w11:p2`` ↔ workspace ``w11``).
    """

    workspace_id: str
    label: str
    number: int
    focused: bool


@dataclass(frozen=True)
class PaneInfo:
    """One pane as reported by ``herdr pane list``.

    Probed live (parity-f7c T5b): EVERY pane carries a cwd — plain
    shell panes too, not just agent panes — which is what lets a
    workspace with no running agent still resolve its folder. Only
    the fields the cwd derivation needs are kept.
    """

    pane_id: str
    cwd: str
    focused: bool


def parse_workspace_list(raw: str) -> List[WorkspaceInfo]:
    """Parses NDJSON output of ``herdr workspace list``.

    Same discipline as :func:`parse_agent_list`: returns every entry
    whose ``result.workspaces`` array is present, tolerates leading
    noise lines that are not JSON, and preserves the CLI's own order
    (the caller owns the sort).
    """
    workspaces: List[WorkspaceInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = payload.get("result") if isinstance(payload, dict) else None
        entries = result.get("workspaces") if isinstance(result, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                number = int(entry.get("number", 0) or 0)
            except (TypeError, ValueError):
                number = 0
            workspaces.append(
                WorkspaceInfo(
                    workspace_id=str(entry.get("workspace_id", "")),
                    label=str(entry.get("label", "")),
                    number=number,
                    focused=bool(entry.get("focused", False)),
                )
            )
    return workspaces


def parse_pane_list(raw: str) -> List[PaneInfo]:
    """Parses NDJSON output of ``herdr pane list``.

    Same discipline as :func:`parse_agent_list`: returns every entry
    whose ``result.panes`` array is present, tolerates leading noise
    lines that are not JSON, and preserves the CLI's own order (the
    caller owns any ranking).
    """
    panes: List[PaneInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = payload.get("result") if isinstance(payload, dict) else None
        entries = result.get("panes") if isinstance(result, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            panes.append(
                PaneInfo(
                    pane_id=str(entry.get("pane_id", "")),
                    cwd=str(entry.get("cwd", "")),
                    focused=bool(entry.get("focused", False)),
                )
            )
    return panes


def parse_agent_list(raw: str) -> List[AgentInfo]:
    """Parses NDJSON output of ``herdr agent list`` into AgentInfo entries.

    Returns every entry whose ``result.agents`` array is present; tolerates
    leading noise lines that are not JSON.
    """
    agents: List[AgentInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = payload.get("result") if isinstance(payload, dict) else None
        entries = result.get("agents") if isinstance(result, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            session = entry.get("agent_session") or {}
            agents.append(
                AgentInfo(
                    pane_id=str(entry.get("pane_id", "")),
                    agent=str(entry.get("agent", "")),
                    status=str(entry.get("agent_status", "unknown")),
                    session_kind=str(session.get("kind", "")),
                    session_value=str(session.get("value", "")),
                    cwd=str(entry.get("cwd", "")),
                    title=str(entry.get("terminal_title_stripped", "")),
                    focused=bool(entry.get("focused", False)),
                )
            )
    return agents


def parse_success_result(raw: str) -> dict:
    """Parses the ``result`` object of a CLI success envelope.

    ``herdr`` prints the API success envelope as JSON lines (``{"id": ...,
    "result": {...}}``); like :func:`parse_agent_list` this tolerates
    leading noise lines that are not JSON. Raises :class:`HerdrError`
    when no parseable ``result`` object is found.
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            return result
    excerpt = raw.strip()[:200]
    raise HerdrError(f"herdr returned unparseable output: {excerpt!r}")


def pick_active(agents: List[AgentInfo]) -> Optional[AgentInfo]:
    """Selects the active pane: focused first, then working, then the first."""
    if not agents:
        return None
    for candidate in agents:
        if candidate.focused:
            return candidate
    for candidate in agents:
        if candidate.status == "working":
            return candidate
    return agents[0]


def sanitize_prompt_text(text: str) -> str:
    """Collapses newlines to spaces.

    A literal ``\\n`` in sent text is a real Enter in the target pane; voice
    input and LLM answers must never submit multi-line prompts by accident.
    """
    return " ".join(text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").split())


class HerdrClient:
    """Thin subprocess wrapper over the herdr CLI."""

    def __init__(self, settings: Settings, runner: Optional[Runner] = None):
        self._settings = settings
        self._run: Runner = runner if runner is not None else subprocess.run

    # -- internals ---------------------------------------------------------

    def _run_cli(
        self, args: List[str], timeout_s: float, allow_failure: bool = False
    ) -> subprocess.CompletedProcess:
        cmd = [self._settings.herdr_bin, *args]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise HerdrError(f"herdr timed out after {timeout_s}s: {' '.join(args)}") from exc
        except OSError as exc:
            raise HerdrError(f"failed to execute herdr CLI: {exc}") from exc
        if proc.returncode != 0 and not allow_failure:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise HerdrError(f"herdr exited with {proc.returncode}: {detail}")
        return proc

    # -- read-only operations ----------------------------------------------

    def list_agents(self) -> List[AgentInfo]:
        proc = self._run_cli(["agent", "list"], timeout_s=15)
        return parse_agent_list(proc.stdout)

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """Every workspace, sorted by its ``number``.

        Verified CLI shape (probed live, parity-f7c S1b):
        ``herdr workspace list`` prints one NDJSON success envelope
        whose ``result`` is the ``workspace_list`` variant. Includes
        workspaces with NO live pane — hiding them is a caller
        decision, never this listing's.
        """
        proc = self._run_cli(["workspace", "list"], timeout_s=15)
        return sorted(parse_workspace_list(proc.stdout), key=lambda w: w.number)

    def list_panes(self) -> List[PaneInfo]:
        """Every live pane — agent or not (probed live, parity-f7c T5b).

        ``herdr pane list`` answers one NDJSON success envelope whose
        ``result`` is the ``pane_list`` variant; each pane row carries
        its own cwd, so this is the folder source for workspaces with
        no running agent.
        """
        proc = self._run_cli(["pane", "list"], timeout_s=15)
        return parse_pane_list(proc.stdout)

    def focus_workspace(self, workspace_id: str) -> str:
        """Focuses a herdr workspace and returns what the CLI printed.

        Argv shape fixture-derived from ``herdr workspace focus --help``
        (pinned at tests/fixtures/herdr-workspace-focus-help.txt, this
        host's CLI): ONE positional ``<workspace_id>``, no flags. The
        SUCCESS OUTPUT is UNVERIFIED-live — focusing changes the
        operator's real active workspace, so no test or probe may run
        it — which is why this returns the CLI's stdout verbatim
        instead of parsing an envelope nobody has seen; only the exit
        code carries meaning (non-zero raises :class:`HerdrError` with
        the stderr detail, like every other command).
        """
        clean = (workspace_id or "").strip()
        if not clean:
            raise HerdrError("refusing to focus an empty workspace id")
        proc = self._run_cli(["workspace", "focus", clean], timeout_s=15)
        return (proc.stdout or "").strip()

    def active_agent(self) -> Optional[AgentInfo]:
        return pick_active(self.list_agents())

    def read_screen(
        self,
        pane_id: str,
        n_lines: Optional[int] = None,
        source: str = "visible",
        as_ansi: bool = False,

    ) -> str:
        """Reads terminal output of a pane (read-only).

        ``source`` is a verified CLI flag: ``visible`` glances at the current
        viewport (clamped to 60 lines to avoid scrolling the operator's real
        screen); ``recent`` reads the scrollback backlog for explicit
        full-text reads (clamped to 120 lines).

        ``as_ansi`` keeps the styling. That matters because "which option of a
        dialog is selected" exists ONLY as colour/attribute — reading the
        plain text throws that signal away. Callers that only need words (the
        model) keep the default.

        """
        requested = n_lines if n_lines and n_lines > 0 else self._settings.screen_lines
        cap = MAX_BACKLOG_LINES if source == "recent" else MAX_SCREEN_LINES
        clamped = min(requested, cap)
        proc = self._run_cli(
            [
                "agent", "read", pane_id,
                "--source", source,
                "--lines", str(clamped),
                "--format", "ansi" if as_ansi else "text",
            ],
            timeout_s=15,
        )
        return proc.stdout

    # -- write path ----------------------------------------------------------

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        """Sends logical key presses to an agent (the DIALOG surface).

        ``herdr agent prompt`` refuses an agent already waiting at an
        approval or question dialog (``agent_blocked``, before any input is
        written), so that dialog is answered with keys — the surface
        ``herdr --skill`` documents as ``agent send-keys <target> <keys...>``,
        with herdr validating every key before writing any bytes.

        Each key MUST come from :data:`SEND_KEY_ALLOWLIST`; a key outside it
        is refused BEFORE the CLI runs. Returns ``{ok, error}`` and never
        raises on a CLI failure.
        """
        unsupported = sorted({key for key in keys if key not in SEND_KEY_ALLOWLIST})
        if unsupported:
            raise HerdrError(
                f"unsupported key(s): {', '.join(unsupported)} "
                f"(allowed: {', '.join(SEND_KEY_ALLOWLIST)})"
            )
        if not keys:
            raise HerdrError("refusing to send an empty key sequence")
        if len(keys) > MAX_SEND_KEYS:
            raise HerdrError(
                f"too many keys: {len(keys)} (max {MAX_SEND_KEYS})"
            )
        try:
            self._run_cli(["agent", "send-keys", pane_id, *keys], timeout_s=15)
        except HerdrError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": ""}

    def send_text(self, pane_id: str, text: str) -> None:
        """Types literal text into a pane, WITHOUT Enter.

        Verified CLI shape: ``herdr pane send-text <PANE_ID> <TEXT>`` —
        "Send literal text to a pane". This is the typing half of the
        free-text dialog recipe (S3): the recipe selects the row with
        its digit, clears the field with ``ctrl+u``/``ctrl+k`` and only
        then types, re-reads the screen and commits with Enter itself —
        so the text must arrive as literal keystrokes, never with a
        trailing submit. The text is sanitized exactly like a prompt
        (:func:`sanitize_prompt_text`): a literal newline would submit
        the field early. Raises :class:`HerdrError` on any CLI failure.
        """
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send empty text")
        self._run_cli(["pane", "send-text", pane_id, clean], timeout_s=15)

    def create_tab(
        self,
        label: str,
        cwd: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict:
        """Creates a new tab and returns ``{"tab_id", "pane_id"}``.

        Verified CLI shape: ``herdr tab create --label <TEXT>
        [--cwd <PATH>] [--workspace <WORKSPACE_ID>]`` prints the API
        success envelope whose ``result`` is the ``tab_created``
        variant (pinned via ``herdr api schema --json``):
        ``result.tab.tab_id`` (TabInfo) and ``result.root_pane.pane_id``
        (PaneInfo), both required keys of that variant. Should the CLI
        ever print a different envelope, :func:`parse_success_result`
        raises HerdrError instead of returning half-parsed ids.
        ``--workspace`` targets the tab INSIDE that workspace (probed
        live, parity-f7c S1b); without it the CLI keeps its current-
        workspace behavior.
        """
        args = ["tab", "create", "--label", label]
        if cwd:
            args.extend(["--cwd", cwd])
        if workspace_id:
            args.extend(["--workspace", workspace_id])
        proc = self._run_cli(args, timeout_s=15)
        result = parse_success_result(proc.stdout)
        tab = result.get("tab") if isinstance(result.get("tab"), dict) else {}
        pane = result.get("root_pane") if isinstance(result.get("root_pane"), dict) else {}
        tab_id = str(tab.get("tab_id", ""))
        pane_id = str(pane.get("pane_id", ""))
        if not tab_id or not pane_id:
            excerpt = proc.stdout.strip()[:200]
            raise HerdrError(f"tab create output missing tab_id/root_pane: {excerpt!r}")
        return {"tab_id": tab_id, "pane_id": pane_id}

    def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        timeout_ms: Optional[int] = None,
    ) -> str:
        """Starts an agent in an existing pane and returns its pane id.

        Verified CLI shape: ``herdr agent start <NAME> --kind <KIND> --pane
        <ID> --timeout <MS>`` (readiness wait; default 30000 ms, max
        300000 ms). The success ``result`` is the ``agent_started``
        variant carrying the AgentInfo under ``result.agent``; its
        ``pane_id`` is returned (falling back to the requested pane, which
        the CLI contract guarantees to be the same).
        """
        effective_ms = timeout_ms if timeout_ms and timeout_ms > 0 else 30_000
        cli_args = [
            "agent", "start", name,
            "--kind", kind,
            "--pane", pane_id,
            "--timeout", str(effective_ms),
        ]
        proc = self._run_cli(cli_args, timeout_s=effective_ms / 1000 + 30)
        result = parse_success_result(proc.stdout)
        agent = result.get("agent")
        if not isinstance(agent, dict):
            excerpt = proc.stdout.strip()[:200]
            raise HerdrError(f"agent start output missing agent info: {excerpt!r}")
        return str(agent.get("pane_id", "")) or pane_id

    def pane_run(self, pane_id: str, command: str) -> None:
        """Types ``command`` + Enter into a pane's interactive shell.

        Verified CLI shape: ``herdr pane run <PANE_ID> <COMMAND>...`` —
        "sends text and Enter in one call". The command is the SHELL line
        the pane must execute, passed as one argv element so any shell
        quoting inside it (e.g. ``oa '/my repo'``) reaches the pane
        verbatim. Fire-and-confirm: the CLI returns once the text is
        submitted, without waiting for the shell to finish; a non-zero
        exit raises :class:`HerdrError`.
        """
        clean = (command or "").strip()
        if not clean:
            raise HerdrError("refusing to run an empty command")
        self._run_cli(["pane", "run", pane_id, clean], timeout_s=15)

    def agent_wait(
        self, pane_id: str, until: str = "idle", timeout_ms: int = 15_000
    ) -> None:
        """Waits until an agent reaches a state (or fails after a timeout).

        Verified CLI shape (``herdr agent wait --help``):
        ``herdr agent wait <TARGET> [--until <STATUS>]... [--timeout <MS>]``
        with statuses ``idle|working|blocked|done|unknown``; the target
        accepts pane ids just like ``agent prompt``. Non-zero exit (state
        not reached within the timeout) raises :class:`HerdrError`.
        """
        self._run_cli(
            ["agent", "wait", pane_id, "--until", until, "--timeout", str(timeout_ms)],
            timeout_s=timeout_ms / 1000 + 30,
        )

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        """Submits a prompt and waits for completion.

        Uses the verified flags: ``--wait --until done --timeout <ms>``.
        Returns a dict with ``ok``, ``status`` and ``output``, where status
        is one of ``done`` | ``blocked`` | ``stalled`` | ``timeout``.

        A submission outcome is NOT a failed invocation. herdr reports
        ``agent_blocked`` / ``agent_prompt_stalled`` / ``timeout`` as a JSON
        error envelope on stderr with a non-zero exit; in the last two cases
        the prompt was already written to the pane, so only the completion
        wait expired. Raising on the exit code would make a delivered prompt
        look like a failure. Any OTHER non-zero exit still raises.
        """
        clean = sanitize_prompt_text(text)
        if not clean:
            raise HerdrError("refusing to send an empty prompt")
        effective_ms = timeout_ms if timeout_ms and timeout_ms > 0 else self._settings.prompt_timeout_ms
        proc = self._run_cli(
            [
                "agent", "prompt", pane_id, clean,
                "--wait",
                "--until", "done",
                "--timeout", str(effective_ms),
            ],
            timeout_s=effective_ms / 1000 + 30,
            allow_failure=True,
        )
        output = (proc.stdout or "").strip()
        if proc.returncode != 0:
            code = _cli_error_code(proc.stderr, proc.stdout)
            status = _PROMPT_OUTCOME_CODES.get(code)
            if status is None:
                detail = (proc.stderr or proc.stdout or "").strip()
                raise HerdrError(f"herdr exited with {proc.returncode}: {detail}")
            return {"ok": False, "status": status, "output": output}
        status = _prompt_status_from_output(output, default="done")
        ok = status == "done"
        return {"ok": ok, "status": status, "output": output}


def _cli_error_code(stderr: str, stdout: str = "") -> str:
    """Extracts ``error.code`` from the CLI's JSON error envelope.

    Every herdr failure prints ``{"error": {"code", "message"}, "id"}`` to
    STDERR and exits non-zero — verified against herdr 0.9.1. Returns ``""``
    when no envelope can be read (caller falls back to raising).
    """
    for raw in (stderr or "", stdout or ""):
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                return error["code"]
    return ""


def _prompt_status_from_output(output: str, default: str) -> str:
    """Best-effort extraction of the submission outcome from CLI output."""
    if _PROMPT_BLOCKED in output:
        return "blocked"
    if _PROMPT_STALLED in output:
        return "stalled"
    if _PROMPT_TIMEOUT in output:
        return "timeout"
    return default
