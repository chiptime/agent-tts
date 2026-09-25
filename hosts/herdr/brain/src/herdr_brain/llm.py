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

from .approval import ApprovalGate, ApprovalGateStore, CREATE_SESSION, SEND_TO_SESSION
from .config import Settings
from .herdr import AgentInfo, HerdrError
from .memory import ConversationStore, clip_content
from .tools import BrainTools, TOOLS_SCHEMA
from .view import PendingAction, SCREEN_TAIL_LINES, detect_pending

LOGGER = logging.getLogger("herdr_brain.tool_calls")

_MAX_LOGGED_ARG_CHARS = 80

SYSTEM_PROMPT = """You are herdr-brain, the user's hands-free voice assistant — the collie that herds their personal herd of AI coding agents. The agents run managed by Herdr on this machine; there is exactly one human user. Be conversational, warm and direct.

Capabilities:
- You can read the active agent's real transcript (recent user/assistant turns) and what its terminal shows right now, and answer from what you actually read.
- You can forward new work to the active agent and wait until it finishes, then report the outcome.
- Your answers are spoken aloud: the service renders the speech after you answer, so write clean speakable text.

Honesty rules:
- NEVER invent or guess transcript or screen content. If a read comes back empty or unreadable, or a tool fails, say in one plain sentence what happened and suggest trying again. No stack traces, no apology theater.

Pending prompts:
- When the live context shows a pending prompt, surface it to the user proactively in your answer.
- If the user tells you to answer a pending prompt (e.g. "dile que sí"), FIRST read_screen to see exactly what would be confirmed and state it, THEN send_to_session with the user's answer. Never send a blind yes to something you have not read.

Routing policy:
- Questions about state, history, summaries, or doubts about what happened: answer yourself using read_transcript (preferred — cheap and local) or read_screen. NEVER send anything to the agent session for these.
- New work or actions: forward them with send_to_session and wait for completion, then report the result. Do not do the agent's work yourself.
- Unsure whether it is a question or a task: read the transcript first, then decide.

Approval gate:
- send_to_session never executes on the spot: the send is frozen behind a user approval gate. When a send is gated, state the target and the text (verbatim if short), and wait — never narrate the send as done.

Conversation memory:
- You keep the recent conversation. Resolve follow-ups like "and what else?" against prior turns before calling tools again; re-read sources only when you truly need fresh data.

Answer style (voice-first):
- At most 3 short sentences, speakable, no markdown, no code blocks, no bullet lists, no file dumps.
- Lead with the direct answer; offer more detail only if the user asks.
- Reply in the user's language; it will usually be Spanish.

Working agreement — hold these in mind and apply them when they fit. Never recite them or turn them into a fixed template; brevity still wins:
- Keep a sense of where things stand across turns. If the user switches topics or contexts mid-conversation, briefly re-anchor which thread you are on before answering.
- If you or the agent are blocked waiting on a user decision or input to continue autonomously, say so in one plain sentence (e.g. "necesito que decidas X") — but only when it truly blocks; silence when nothing does.
- When the agent's work leaves something open or a decision is near, offer the recommended next step or a suggested reply. Skip it when it would be noise.
- These points may stretch the sentence limit slightly when they apply; keep the answer speakable above all."""


def build_live_context(
    active: Optional[AgentInfo],
    now: Optional[datetime] = None,
    pending: Optional[PendingAction] = None,
) -> str:
    """Renders the per-request live block appended to the system message."""
    # Naive datetimes are assumed local so %Z never renders empty.
    timestamp = (now or datetime.now()).astimezone().strftime("%Y-%m-%d %H:%M (%Z)")
    lines = [
        "---- LIVE CONTEXT (refreshed every request; not part of the conversation) ----",
        f"Local time: {timestamp}",
    ]
    if active is None:
        lines.append("Selected agent: none right now — no agent panes are running.")
    else:
        focused = "yes" if active.focused else "no"
        lines.extend(
            [
                f"Selected agent: {active.agent} ({active.status}) — focused: {focused}",
                f"Pane: {active.pane_id}",
                f"Session: {active.session_value or 'unknown'}",
                f"Terminal title: {active.title or 'unknown'}",
                f"Working directory: {active.cwd or 'unknown'}",
            ]
        )
        if pending is not None and pending.detected:
            lines.append(_pending_hint_line(active, pending))
    return "\n".join(lines)


_PENDING_KIND_PHRASES = {
    "permission": "requesting permission",
    "error": "reporting an error",
    "question": "asking a question",
    None: "waiting for input",
}


def _pending_hint_line(active: AgentInfo, pending: PendingAction) -> str:
    """One ATTENTION line describing the likely unresolved prompt."""
    phrase = _PENDING_KIND_PHRASES.get(pending.kind, "waiting for input")
    if (active.status or "").lower() == "blocked":
        line = f"ATTENTION: the agent is BLOCKED and appears to be {phrase}"
    else:
        line = f"ATTENTION: the agent terminal suggests a pending {pending.kind or 'prompt'}"
    if pending.excerpt:
        line += f': "{pending.excerpt}"'
    return line


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
        approval_store: Optional[ApprovalGateStore] = None,
    ):
        self._settings = settings
        self._tools = tools
        self._client = client
        self._store = store
        self._approval_store = approval_store
        self._request_target: Optional[AgentInfo] = None
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

    def attach_approval_store(self, store: ApprovalGateStore) -> None:
        """Binds the shared approval gate store (used by the HTTP server)."""
        self._approval_store = store

    def ask(
        self,
        question: str,
        session_id: Optional[str] = None,
        pane_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Returns ``{answer, pane_id, agent, session_id, approval}``.

        ``pane_id`` selects which agent the conversation targets; when
        omitted (or stale) the focused pane is used. ``approval`` is the
        frozen :class:`ApprovalGate` opened when a ``send_to_session``
        call was intercepted (None on ungated turns) — the send itself
        never executes here; only an approval replay may run it.
        """
        store = self._store or ConversationStore()
        key = ConversationStore.normalize(session_id)
        target = self._tools.resolve_target(pane_id)
        self._request_target = target

        live = self._live_context(target)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{live}"},
            # The store keeps turns in full (display reads them unclipped);
            # the LLM prompt is where the token budget is enforced.
            *[{"role": m.role, "content": clip_content(m.content)}
              for m in store.history(key)],
            {"role": "user", "content": question},
        ]

        answer: Optional[str] = None
        opened_gate: Optional[ApprovalGate] = None
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
                result, gate = self._invoke(call, session_key=key)
                if gate is not None:
                    opened_gate = gate
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
        else:
            answer = answer or ""

        if not answer:
            answer = "I could not put together an answer right now; try again or rephrase."

        store.append(key, "user", question)
        store.append(key, "assistant", answer)

        return {
            "answer": answer,
            "pane_id": target.pane_id if target else None,
            "agent": target.agent if target else None,
            "session_id": key,
            "approval": opened_gate,
        }

    def _live_context(self, target: Optional[AgentInfo]) -> str:
        """Builds the live block for the selected pane, best-effort."""
        pending = None
        if target is not None:
            screen = self._tools.screen_tail(target, SCREEN_TAIL_LINES)
            pending = detect_pending(screen or "", target.status)
        return build_live_context(target, pending=pending)

    def _invoke(self, call: Any, session_key: str) -> tuple[str, Optional[ApprovalGate]]:
        """Dispatches one tool call, turning every failure into a string.

        Mutating tools (``send_to_session``, ``create_session``) are NEVER
        dispatched: each is frozen into an approval gate instead (AC1 —
        no path executes them without an approved gate). Returns
        ``(tool_message, gate_or_None)``.
        """
        try:
            arguments = json.loads(call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return f"error: invalid tool arguments: {exc}", None
        LOGGER.info("tool call name=%s args=%s", call.function.name, summarize_tool_args(arguments))
        if call.function.name == SEND_TO_SESSION:
            return self._gate_send(arguments, session_key)
        if call.function.name == CREATE_SESSION:
            return self._gate_create(arguments, session_key)
        return (
            self._tools.dispatch(
                call.function.name, arguments, target=self._request_target
            ),
            None,
        )

    def _gate_send(
        self, arguments: Dict[str, Any], session_key: str
    ) -> tuple[str, Optional[ApprovalGate]]:
        """Freezes the EXACT dispatch args into a gate instead of sending.

        The frozen ``text``/``timeout_ms`` mirror what dispatch would have
        passed, and the pane is the already-resolved request target, so an
        approval can replay the identical call. With no resolved target
        the send could never run — mirror the tool's own error instead of
        opening a gate. With no store wired the send is still blocked:
        fail safe, never execute.
        """
        target = self._request_target
        if target is None:
            return "error: no active agent pane", None
        if self._approval_store is None:
            return (
                "error: send_to_session is blocked pending user approval, but "
                "no approval gate store is wired; nothing was sent",
                None,
            )
        gate = self._approval_store.propose(
            session_key,
            text=str(arguments.get("text", "")),
            timeout_ms=arguments.get("timeout_ms"),
            pane_id=target.pane_id,
            agent=target.agent,
        )
        LOGGER.info(
            "approval gate opened gate_id=%s pane_id=%s agent=%s",
            gate.gate_id,
            target.pane_id,
            target.agent,
        )
        return (
            f"pending user approval (gate {gate.gate_id}): the prompt was NOT "
            "sent. State the target and the exact text, then wait for the "
            "user's decision; never say it was sent.",
            gate,
        )

    def _gate_create(
        self, arguments: Dict[str, Any], session_key: str
    ) -> tuple[str, Optional[ApprovalGate]]:
        """Freezes the EXACT create_session args into a gate instead of
        creating the panel.

        Mirrors :meth:`_gate_send`: the frozen ``agent_kind``/``title``/
        ``task``/``cwd`` are exactly what dispatch would have received,
        so an approval replays the identical call. No target pane is
        needed — the whole point is a NEW panel — so the only failure
        modes are missing kind/title (mirroring the tool's own
        validation) and a missing store, where creation is still
        blocked: fail safe, never execute.
        """
        agent_kind = str(arguments.get("agent_kind", "")).strip()
        title = str(arguments.get("title", "")).strip()
        task = str(arguments.get("task", "") or "")
        cwd = str(arguments.get("cwd", "") or "")
        if not agent_kind or not title:
            return "error: agent_kind and title are required", None
        if self._approval_store is None:
            return (
                "error: create_session is blocked pending user approval, but "
                "no approval gate store is wired; nothing was created",
                None,
            )
        gate = self._approval_store.propose(
            session_key,
            text="",
            agent=agent_kind,
            tool=CREATE_SESSION,
            agent_kind=agent_kind,
            title=title,
            task=task,
            cwd=cwd,
        )
        LOGGER.info(
            "approval gate opened gate_id=%s tool=create_session kind=%s title=%s",
            gate.gate_id,
            agent_kind,
            title,
        )
        return (
            f"pending user approval (gate {gate.gate_id}): the panel was NOT "
            "created. State the agent kind and the title, then wait for the "
            "user's decision; never say it was created.",
            gate,
        )
