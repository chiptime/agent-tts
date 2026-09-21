"""Brain tool layer: the functions the LLM may call, as OpenAI tool schemas.

Fase 1 scope is the ACTIVE pane only. ``send_to_session`` is the only write
path and forwards to the agent through ``herdr agent prompt --wait``; every
other tool is read-only.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional

from .config import Settings
from .herdr import AgentInfo, HerdrClient, HerdrError
from .transcripts import read_transcript, read_turns
from .tts import render_mp3  # noqa: F401  (re-exported for server wiring)
from .view import (
    SCREEN_TAIL_LINES,
    TRANSCRIPT_TAIL_TURNS,
    VIEW_TEXT_TRUNCATE,
    detect_pending,
    truncate_text,
)

MAX_SEND_EXCERPT = 500
HERD_LAST_TURN_CHARS = 160


def status_payload(active: Optional[AgentInfo]) -> dict:
    """Shapes the active-pane status shared by /state and /view."""
    if active is None:
        return {
            "active": False,
            "pane_id": None,
            "agent": None,
            "agent_status": None,
            "title": None,
            "cwd": None,
            "session_id": None,
        }
    return {
        "active": True,
        "pane_id": active.pane_id,
        "agent": active.agent,
        "agent_status": active.status,
        "title": active.title,
        "cwd": active.cwd,
        "session_id": active.session_value,
    }


class BrainTools:
    """Tool implementations bound to a HerdrClient and settings."""

    def __init__(self, settings: Settings, herdr: Optional[HerdrClient] = None):
        self._settings = settings
        self._herdr = herdr or HerdrClient(settings)
        self.last_active: Optional[AgentInfo] = None

    # -- tools --------------------------------------------------------------

    def active_status(self) -> Optional[AgentInfo]:
        """Fetches the active agent pane (structured) and tracks it."""
        return self._track(self._herdr.active_agent())

    def status_payload(self) -> dict:
        """Active-pane status dict, degrading to {active: false} on errors."""
        try:
            active = self._track(self._herdr.active_agent())
        except HerdrError:
            active = None
        return status_payload(active)

    def last_turn(self, agent: AgentInfo) -> Optional[dict]:
        """Latest transcript turn for one agent, or None on any failure."""
        if not agent.session_value:
            return None
        try:
            turns = read_turns(agent.agent, agent.session_value, 1)
        except Exception:  # noqa: BLE001 — per-agent failure must not spread
            return None
        if not turns:
            return None
        turn = turns[-1]
        return {"role": turn.role, "text": truncate_text(turn.text, HERD_LAST_TURN_CHARS)}

    def herd(self) -> list:
        """Every agent pane with status and its latest transcript turn.

        Never raises: a failed agent listing yields an empty herd, and a
        per-agent transcript failure yields ``last_turn: null``.
        """
        try:
            agents = self._herdr.list_agents()
        except HerdrError:
            return []
        entries = []
        for agent in agents:
            entries.append(
                {
                    "pane_id": agent.pane_id,
                    "agent": agent.agent,
                    "agent_status": agent.status,
                    "title": agent.title,
                    "cwd": agent.cwd,
                    "session_id": agent.session_value if agent.session_kind == "id" else None,
                    "focused": agent.focused,
                    "last_turn": self.last_turn(agent),
                }
            )
        return entries

    def screen_tail(
        self, active: AgentInfo, n_lines: int = SCREEN_TAIL_LINES
    ) -> Optional[str]:
        """Visible screen tail for a known pane; None on failure."""
        try:
            text = self._herdr.read_screen(active.pane_id, n_lines)
        except HerdrError:
            return None
        return text if text and text.strip() else None

    def transcript_tail(
        self, active: AgentInfo, n_turns: int = TRANSCRIPT_TAIL_TURNS
    ) -> Optional[list]:
        """Recent turns (role + truncated text) for a known pane; None if unavailable."""
        if not active.session_value:
            return None
        try:
            turns = read_turns(active.agent, active.session_value, n_turns)
        except Exception:  # noqa: BLE001 — the view must degrade gracefully
            return None
        if not turns:
            return None
        return [
            {"role": turn.role, "text": truncate_text(turn.text, VIEW_TEXT_TRUNCATE)}
            for turn in turns
        ]

    def agent_view(self) -> dict:
        """Composes everything GET /view needs, never raising.

        Superset of the status payload: recent transcript turns, the visible
        screen tail, and the heuristic pending-action detection.
        """
        try:
            active = self._track(self._herdr.active_agent())
        except HerdrError:
            active = None

        transcript: Optional[list] = None
        screen: Optional[str] = None
        if active is not None:
            transcript = self.transcript_tail(active)
            screen = self.screen_tail(active)

        pending = detect_pending(screen or "", active.status if active else "")
        return {
            "status": status_payload(active),
            "transcript": transcript,
            "screen": screen,
            "pending": {
                "detected": pending.detected,
                "kind": pending.kind,
                "excerpt": pending.excerpt,
            },
        }

    def get_status(self) -> str:
        """JSON status of the active agent pane."""
        active = self._track(self._herdr.active_agent())
        if active is None:
            return json.dumps({"active": False, "detail": "no agent panes found"})
        return json.dumps(
            {
                "active": True,
                "agent": active.agent,
                "status": active.status,
                "pane_id": active.pane_id,
                "session_id": active.session_value,
                "cwd": active.cwd,
                "title": active.title,
            },
            ensure_ascii=False,
        )

    def read_transcript(self, n_turns: int = 10) -> str:
        """Recent user/assistant turns for the active session.

        Preferred path for state/history questions. Falls back to a screen
        read when the transcript connector cannot serve the session.
        """
        active = self._track(self._herdr.active_agent())
        if active is None:
            return "error: no active agent pane"
        if active.session_value:
            text = read_transcript(active.agent, active.session_value, n_turns)
            if text:
                return text
        screen = self._screen_or_error(active)
        return "[transcript unavailable — showing terminal screen instead]\n" + screen

    def read_screen(self, n_lines: int = 40) -> str:
        """Visible terminal text of the active pane (read-only fallback)."""
        active = self._track(self._herdr.active_agent())
        if active is None:
            return "error: no active agent pane"
        return self._screen_or_error(active, n_lines)

    def send_to_session(self, text: str, timeout_ms: Optional[int] = None) -> str:
        """Forwards a prompt to the active agent and waits for completion.

        The ONLY write path in the whole brain. Returns the completion status
        plus a short output excerpt.
        """
        active = self._track(self._herdr.active_agent())
        if active is None:
            return "error: no active agent pane"
        try:
            result = self._herdr.send_prompt(active.pane_id, text, timeout_ms)
        except HerdrError as exc:
            return f"error sending prompt: {exc}"
        excerpt = (result.get("output") or "")[-MAX_SEND_EXCERPT:]
        return json.dumps(
            {
                "ok": result["ok"],
                "status": result["status"],
                "pane_id": active.pane_id,
                "output_excerpt": excerpt,
            },
            ensure_ascii=False,
        )

    # -- dispatch helpers ----------------------------------------------------

    def dispatch(self, name: str, arguments: Dict) -> str:
        """Calls a tool by name with JSON-ish arguments, returning a string."""
        handler: Optional[Callable] = {
            "get_status": self.get_status,
            "read_transcript": self.read_transcript,
            "read_screen": self.read_screen,
            "send_to_session": self.send_to_session,
        }.get(name)
        if handler is None:
            return f"error: unknown tool {name}"
        try:
            if name == "send_to_session":
                return handler(str(arguments.get("text", "")), arguments.get("timeout_ms"))
            if name == "read_transcript":
                return handler(int(arguments.get("n_turns", 10)))
            if name == "read_screen":
                return handler(int(arguments.get("n_lines", 40)))
            return handler()
        except (TypeError, ValueError) as exc:
            return f"error: invalid arguments for {name}: {exc}"

    # -- internals -------------------------------------------------------------

    def _track(self, active: Optional[AgentInfo]) -> Optional[AgentInfo]:
        if active is not None:
            self.last_active = active
        return active

    def _screen_or_error(self, active: AgentInfo, n_lines: Optional[int] = None) -> str:
        try:
            return self._herdr.read_screen(active.pane_id, n_lines)
        except HerdrError as exc:
            return f"error reading screen: {exc}"


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_status",
            "description": (
                "Rarely needed: live context (agent kind, status, terminal title, "
                "cwd, pane and session id) is already injected into your system "
                "message every turn. Use only to re-check after your own actions."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_transcript",
            "description": (
                "Cheap and local: the recent real user/assistant turns of the "
                "active agent session. Use this FIRST for anything about what "
                "happened, state, history or summaries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "n_turns": {
                        "type": "integer",
                        "description": "How many recent turns to read (default 10).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": (
                "Fallback when the transcript is unavailable: what the agent's "
                "terminal shows right now (visible lines only)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "n_lines": {
                        "type": "integer",
                        "description": "Visible lines to read, capped at 60 (default 40).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_session",
            "description": (
                "SLOW: submits the text to the active agent and WAITS for it to "
                "actually finish — seconds to minutes. Use only for real new work "
                "or actions the user asked for, never for state/history questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Single-line prompt to submit to the agent.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Optional wait timeout in milliseconds.",
                    },
                },
                "required": ["text"],
            },
        },
    },
]
