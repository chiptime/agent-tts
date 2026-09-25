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

# Well-known failure statuses reported by `herdr agent prompt`.
_PROMPT_BLOCKED = "agent_blocked"
_PROMPT_STALLED = "agent_prompt_stalled"
_PROMPT_TIMEOUT = "timeout"


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

    def _run_cli(self, args: List[str], timeout_s: float) -> subprocess.CompletedProcess:
        cmd = [self._settings.herdr_bin, *args]
        try:
            proc = self._run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise HerdrError(f"herdr timed out after {timeout_s}s: {' '.join(args)}") from exc
        except OSError as exc:
            raise HerdrError(f"failed to execute herdr CLI: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise HerdrError(f"herdr exited with {proc.returncode}: {detail}")
        return proc

    # -- read-only operations ----------------------------------------------

    def list_agents(self) -> List[AgentInfo]:
        proc = self._run_cli(["agent", "list"], timeout_s=15)
        return parse_agent_list(proc.stdout)

    def active_agent(self) -> Optional[AgentInfo]:
        return pick_active(self.list_agents())

    def read_screen(
        self, pane_id: str, n_lines: Optional[int] = None, source: str = "visible"
    ) -> str:
        """Reads terminal output of a pane (read-only).

        ``source`` is a verified CLI flag: ``visible`` glances at the current
        viewport (clamped to 60 lines to avoid scrolling the operator's real
        screen); ``recent`` reads the scrollback backlog for explicit
        full-text reads (clamped to 120 lines).
        """
        requested = n_lines if n_lines and n_lines > 0 else self._settings.screen_lines
        cap = MAX_BACKLOG_LINES if source == "recent" else MAX_SCREEN_LINES
        clamped = min(requested, cap)
        proc = self._run_cli(
            [
                "agent", "read", pane_id,
                "--source", source,
                "--lines", str(clamped),
                "--format", "text",
            ],
            timeout_s=15,
        )
        return proc.stdout

    # -- write path ----------------------------------------------------------

    def create_tab(self, label: str, cwd: Optional[str] = None) -> dict:
        """Creates a new tab and returns ``{"tab_id", "pane_id"}``.

        Verified CLI shape: ``herdr tab create --label <TEXT> [--cwd <PATH>]``
        prints the API success envelope whose ``result`` is the
        ``tab_created`` variant (pinned via ``herdr api schema --json``):
        ``result.tab.tab_id`` (TabInfo) and ``result.root_pane.pane_id``
        (PaneInfo), both required keys of that variant. Should the CLI ever
        print a different envelope, :func:`parse_success_result` raises
        HerdrError instead of returning half-parsed ids.
        """
        args = ["tab", "create", "--label", label]
        if cwd:
            args.extend(["--cwd", cwd])
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
        args: Optional[List[str]] = None,
    ) -> str:
        """Starts an agent in an existing pane and returns its pane id.

        Verified CLI shape: ``herdr agent start <NAME> --kind <KIND> --pane
        <ID> --timeout <MS>`` (readiness wait; default 30000 ms, max
        300000 ms). The success ``result`` is the ``agent_started``
        variant carrying the AgentInfo under ``result.agent``; its
        ``pane_id`` is returned (falling back to the requested pane, which
        the CLI contract guarantees to be the same).

        ``args`` (pinned via ``--help``) is appended after a trailing
        ``--``: the CLI composes ``<kind's canonical executable> *args``,
        e.g. ``args=["attach", url]`` for kind opencode runs
        ``opencode attach <url>``. Empty/None omits the ``--`` entirely,
        keeping the invocation byte-identical to the pre-args form.
        """
        effective_ms = timeout_ms if timeout_ms and timeout_ms > 0 else 30_000
        cli_args = [
            "agent", "start", name,
            "--kind", kind,
            "--pane", pane_id,
            "--timeout", str(effective_ms),
        ]
        if args:
            cli_args.extend(["--", *args])
        proc = self._run_cli(cli_args, timeout_s=effective_ms / 1000 + 30)
        result = parse_success_result(proc.stdout)
        agent = result.get("agent")
        if not isinstance(agent, dict):
            excerpt = proc.stdout.strip()[:200]
            raise HerdrError(f"agent start output missing agent info: {excerpt!r}")
        return str(agent.get("pane_id", "")) or pane_id

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        """Submits a prompt and waits for completion.

        Uses the verified flags: ``--wait --until done --timeout <ms>``.
        Returns a dict with ``ok``, ``status`` and ``output``.
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
        )
        output = (proc.stdout or "").strip()
        status = _prompt_status_from_output(output, default="done")
        ok = proc.returncode == 0 and status == "done"
        return {"ok": ok, "status": status, "output": output}


def _prompt_status_from_output(output: str, default: str) -> str:
    """Best-effort extraction of the submission outcome from CLI output."""
    if _PROMPT_BLOCKED in output:
        return "blocked"
    if _PROMPT_STALLED in output:
        return "stalled"
    if _PROMPT_TIMEOUT in output:
        return "timeout"
    return default
