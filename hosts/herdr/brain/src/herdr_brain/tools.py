"""Brain tool layer: the functions the LLM may call, as OpenAI tool schemas.

Fase 1 scope is the ACTIVE pane only. ``send_to_session`` is the only write
path and forwards to the agent through ``herdr agent prompt --wait``; every
other tool is read-only.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional

from .config import MAX_SCREEN_LINES, Settings
from .herdr import AgentInfo, HerdrClient, HerdrError, pick_active
from .memory import clip_content
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
CONVERSATION_WINDOW = 20
SCREEN_FULL_LINES = 120


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

    def resolve_target(self, pane_id: Optional[str] = None) -> Optional[AgentInfo]:
        """Pane a request operates on: the explicit pane_id, else the focused.

        An unknown pane_id falls back to the focused pane so a stale selection
        never bricks the brain.
        """
        try:
            agents = self._herdr.list_agents()
        except HerdrError:
            return None
        if pane_id:
            for agent in agents:
                if agent.pane_id == pane_id:
                    return self._track(agent)
        return self._track(pick_active(agents))

    def _resolve(self, target: Optional[AgentInfo]) -> Optional[AgentInfo]:
        """Explicit target if given, otherwise the focused pane."""
        if target is not None:
            return self._track(target)
        try:
            return self._track(self._herdr.active_agent())
        except HerdrError:
            return None

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

    def conversation(self, pane_id: Optional[str] = None) -> dict:
        """Full-text recent conversation for one pane (reading view).

        Last ``CONVERSATION_WINDOW`` (20) turns, unclipped — the reading
        view shows every message complete (user request). The transcript
        readers have no cursor, so there is no older-page fetch: the window
        is documented, not paged.
        """
        target = self.resolve_target(pane_id)
        if target is None:
            return {
                "pane_id": pane_id, "agent": None, "session_id": None,
                "turns": [], "window": CONVERSATION_WINDOW,
            }
        turns = []
        if target.session_value:
            try:
                raw = read_turns(target.agent, target.session_value, CONVERSATION_WINDOW)
            except Exception:  # noqa: BLE001 — reading must degrade gracefully
                raw = None
            if raw:
                # Coalesce consecutive same-role messages: OpenCode stores
                # one assistant turn as SEVERAL messages (one per tool-loop
                # step), so the reading view would render every narration
                # fragment as its own turn — fragments typically end in ":"
                # right before a tool call.
                for turn in raw:
                    if turns and turns[-1]["role"] == turn.role:
                        turns[-1]["text"] += "\n\n" + turn.text
                    else:
                        turns.append({"role": turn.role, "text": turn.text})
        return {
            "pane_id": target.pane_id,
            "agent": target.agent,
            "session_id": target.session_value or None,
            "turns": turns,
            "window": CONVERSATION_WINDOW,
        }

    def screen_full(self, pane_id: Optional[str] = None) -> dict:
        """Last ~120 scrollback lines of one pane (reading view).

        Alt-screen TUI agents report empty scrollback, so an empty ``recent``
        read falls back to the visible viewport (60-line clamp).
        """
        target = self.resolve_target(pane_id)
        if target is None:
            return {"pane_id": pane_id, "agent": None, "screen": None}
        try:
            text = self._herdr.read_screen(
                target.pane_id, SCREEN_FULL_LINES, source="recent"
            )
        except HerdrError:
            text = None
        if not text or not text.strip():
            try:
                text = self._herdr.read_screen(target.pane_id, MAX_SCREEN_LINES)
            except HerdrError:
                text = None
        return {
            "pane_id": target.pane_id,
            "agent": target.agent,
            "screen": text if text and text.strip() else None,
        }

    def agent_view(self, pane_id: Optional[str] = None) -> dict:
        """Composes everything GET /view needs, never raising.

        Superset of the status payload: recent transcript turns, the visible
        screen tail, and the heuristic pending-action detection — all for the
        requested pane (default: focused).
        """
        target = self.resolve_target(pane_id)

        transcript: Optional[list] = None
        screen: Optional[str] = None
        if target is not None:
            transcript = self.transcript_tail(target)
            screen = self.screen_tail(target)

        pending = detect_pending(screen or "", target.status if target else "")
        return {
            "status": status_payload(target),
            "transcript": transcript,
            "screen": screen,
            "pending": {
                "detected": pending.detected,
                "kind": pending.kind,
                "excerpt": pending.excerpt,
            },
        }

    def get_status(self, target: Optional[AgentInfo] = None) -> str:
        """JSON status of the target pane (default: focused)."""
        active = self._resolve(target)
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

    def read_transcript(self, n_turns: int = 10, target: Optional[AgentInfo] = None) -> str:
        """Recent user/assistant turns for the target's session.

        Preferred path for state/history questions. Falls back to a screen
        read when the transcript connector cannot serve the session.
        """
        active = self._resolve(target)
        if active is None:
            return "error: no active agent pane"
        if active.session_value:
            text = read_transcript(active.agent, active.session_value, n_turns)
            if text:
                return text
        screen = self._screen_or_error(active)
        return "[transcript unavailable — showing terminal screen instead]\n" + screen

    def read_screen(self, n_lines: int = 40, target: Optional[AgentInfo] = None) -> str:
        """Visible terminal text of the target pane (read-only fallback)."""
        active = self._resolve(target)
        if active is None:
            return "error: no active agent pane"
        return self._screen_or_error(active, n_lines)

    def send_to_session(
        self,
        text: str,
        timeout_ms: Optional[int] = None,
        target: Optional[AgentInfo] = None,
    ) -> str:
        """Forwards a prompt to the target agent and waits for completion.

        The ONLY write path in the whole brain. Returns the completion status
        plus a short output excerpt.
        """
        active = self._resolve(target)
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

    def dispatch(
        self, name: str, arguments: Dict, target: Optional[AgentInfo] = None
    ) -> str:
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
                return handler(
                    str(arguments.get("text", "")),
                    arguments.get("timeout_ms"),
                    target,
                )
            if name == "read_transcript":
                return handler(int(arguments.get("n_turns", 10)), target)
            if name == "read_screen":
                return handler(int(arguments.get("n_lines", 40)), target)
            return handler(target)
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
