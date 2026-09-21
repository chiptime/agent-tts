"""GLM tool-calling loop: turns a user question into a voice-first answer.

LLM access goes through the OpenAI-compatible client with ``GLM_API_KEY`` /
``GLM_BASE_URL`` / ``GLM_MODEL`` environment variables — no key ever enters
the repository.

The harness per request:
- System message = static identity/policy text + a LIVE CONTEXT block built
  from the current active agent pane (agent kind, status, title, cwd, pane
  id, session id) and the local time. The live block is refreshed on every
  request and is never stored in conversation history.
- Conversation memory: per-session ring of recent user/assistant turns so
  follow-ups like "y ¿qué más?" resolve without re-reading everything.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import Settings
from .herdr import AgentInfo, HerdrError
from .memory import ConversationStore
from .tools import BrainTools, TOOLS_SCHEMA

LOGGER = logging.getLogger("herdr_brain.tool_calls")

_MAX_LOGGED_ARG_CHARS = 80

SYSTEM_PROMPT = """You are herdr-brain, the user's hands-free voice assistant — the collie that herds their personal herd of AI coding agents. The agents run managed by Herdr on this machine; there is exactly one human user. Be conversational, warm and direct.

Capabilities:
- You can read the active agent's real transcript (recent user/assistant turns) and what its terminal shows right now, and answer from what you actually read.
- You can forward new work to the active agent and wait until it finishes, then report the outcome.
- Your answers are spoken aloud: the service renders the speech after you answer, so write clean speakable text.

Honesty rules:
- NEVER invent or guess transcript or screen content. If a read comes back empty or unreadable, or a tool fails, say in one plain sentence what happened and suggest trying again. No stack traces, no apology theater.

Routing policy:
- Questions about state, history, summaries, or doubts about what happened: answer yourself using read_transcript (preferred — cheap and local) or read_screen. NEVER send anything to the agent session for these.
- New work or actions: forward them with send_to_session and wait for completion, then report the result. Do not do the agent's work yourself.
- Unsure whether it is a question or a task: read the transcript first, then decide.

Conversation memory:
- You keep the recent conversation. Resolve follow-ups like "and what else?" against prior turns before calling tools again; re-read sources only when you truly need fresh data.

Answer style (voice-first):
- At most 3 short sentences, speakable, no markdown, no code blocks, no bullet lists, no file dumps.
- Lead with the direct answer; offer more detail only if the user asks.
- Reply in the user's language; it will usually be Spanish."""


def build_live_context(
    active: Optional[AgentInfo], now: Optional[datetime] = None
) -> str:
    """Renders the per-request live block appended to the system message."""
    # Naive datetimes are assumed local so %Z never renders empty.
    timestamp = (now or datetime.now()).astimezone().strftime("%Y-%m-%d %H:%M (%Z)")
    lines = [
        "---- LIVE CONTEXT (refreshed every request; not part of the conversation) ----",
        f"Local time: {timestamp}",
    ]
    if active is None:
        lines.append("Active agent: none right now — no agent panes are running.")
    else:
        lines.extend(
            [
                f"Active agent: {active.agent} ({active.status})",
                f"Pane: {active.pane_id}",
                f"Session: {active.session_value or 'unknown'}",
                f"Terminal title: {active.title or 'unknown'}",
                f"Working directory: {active.cwd or 'unknown'}",
            ]
        )
    return "\n".join(lines)


class BrainLLMError(RuntimeError):
    """Raised when the LLM cannot produce a final answer."""


def summarize_tool_args(args: Dict[str, Any]) -> str:
    """Renders tool arguments for the audit log without full payloads."""
    parts = []
    for key in sorted(args):
        value = args[key]
        rendered = str(value)
        if isinstance(value, str) and len(rendered) > _MAX_LOGGED_ARG_CHARS:
            rendered = rendered[: _MAX_LOGGED_ARG_CHARS - 3] + "..."
        parts.append(f"{key}={rendered!r}" if isinstance(value, str) else f"{key}={rendered}")
    return " ".join(parts) if parts else "-"


class BrainLLM:
    """Runs one user question through the tool-calling loop with memory."""

    def __init__(
        self,
        settings: Settings,
        tools: BrainTools,
        client: Optional[Any] = None,
        store: Optional[ConversationStore] = None,
    ):
        self._settings = settings
        self._tools = tools
        self._client = client
        self._store = store
        if self._client is None:
            if not settings.glm_api_key:
                raise BrainLLMError(
                    "GLM_API_KEY is not set. Add it to ~/.dotfiles/shell/private-env.sh "
                    "(outside this repo) and source your shell again."
                )
            self._client = OpenAI(api_key=settings.glm_api_key, base_url=settings.glm_base_url)

    def attach_store(self, store: ConversationStore) -> None:
        """Binds a shared conversation store (used by the HTTP server)."""
        self._store = store

    def ask(self, question: str, session_id: Optional[str] = None) -> Dict[str, Optional[str]]:
        """Returns ``{answer, pane_id, agent, session_id}`` for one question."""
        store = self._store or ConversationStore()
        key = ConversationStore.normalize(session_id)

        live = self._live_context()
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{live}"},
            *[{"role": m.role, "content": m.content} for m in store.history(key)],
            {"role": "user", "content": question},
        ]

        answer: Optional[str] = None
        for _ in range(self._settings.max_tool_rounds + 1):
            response = self._client.chat.completions.create(
                model=self._settings.glm_model,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
            )
            choice = response.choices[0].message
            if not getattr(choice, "tool_calls", None):
                answer = (choice.content or "").strip()
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in choice.tool_calls
                    ],
                }
            )
            for call in choice.tool_calls:
                result = self._invoke(call)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
        else:
            answer = answer or ""

        if not answer:
            answer = "I could not put together an answer right now; try again or rephrase."

        store.append(key, "user", question)
        store.append(key, "assistant", answer)

        active = self._tools.last_active
        return {
            "answer": answer,
            "pane_id": active.pane_id if active else None,
            "agent": active.agent if active else None,
            "session_id": key,
        }

    def _live_context(self) -> str:
        """Builds the live block from the current active pane, best-effort."""
        try:
            active = self._tools.active_status()
        except HerdrError:
            active = None
        return build_live_context(active)

    def _invoke(self, call: Any) -> str:
        """Dispatches one tool call, turning every failure into a string."""
        try:
            arguments = json.loads(call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return f"error: invalid tool arguments: {exc}"
        LOGGER.info("tool call name=%s args=%s", call.function.name, summarize_tool_args(arguments))
        return self._tools.dispatch(call.function.name, arguments)
