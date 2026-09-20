"""GLM tool-calling loop: turns a user question into a voice-first answer.

LLM access goes through the OpenAI-compatible client with ``GLM_API_KEY`` /
``GLM_BASE_URL`` / ``GLM_MODEL`` environment variables — no key ever enters
the repository.

System prompt policy (baked in):
- Questions about state/history/summary -> answer from read_transcript
  (preferred) or read_screen; never send anything to the agent session.
- New work or actions -> forward via send_to_session, then report the result.
- Voice-first answers: at most three short, speakable sentences, no markdown
  dumps; offer detail on request.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import Settings
from .tools import BrainTools, TOOLS_SCHEMA

SYSTEM_PROMPT = """You are herdr-brain, the voice assistant for a developer's AI coding agents managed by Herdr. Exactly ONE agent pane is active; your tools operate on it.

Routing policy:
- Questions about state, history, summaries, or doubts about what happened: answer yourself using read_transcript (preferred) or read_screen. NEVER send anything to the agent session for these.
- New work or actions the user wants done: forward them with send_to_session, wait for completion, then report the result. Do not try to do the agent's work yourself.
- If unsure whether something is a question or a task, read the transcript first, then decide.

Answer style (voice-first):
- At most 3 short sentences, speakable, no markdown, no code blocks, no bullet lists, no file dumps.
- Lead with the direct answer; offer more detail only if the user asks.
- The user may speak Spanish or English; reply in the language they used.
- When you forwarded work, say plainly whether the agent finished and what it reported."""


class BrainLLMError(RuntimeError):
    """Raised when the LLM cannot produce a final answer."""


class BrainLLM:
    """Runs one user question through the tool-calling loop."""

    def __init__(self, settings: Settings, tools: BrainTools, client: Optional[Any] = None):
        self._settings = settings
        self._tools = tools
        self._client = client
        if self._client is None:
            if not settings.glm_api_key:
                raise BrainLLMError(
                    "GLM_API_KEY is not set. Add it to ~/.dotfiles/shell/private-env.sh "
                    "(outside this repo) and source your shell again."
                )
            self._client = OpenAI(api_key=settings.glm_api_key, base_url=settings.glm_base_url)

    def ask(self, question: str) -> Dict[str, Optional[str]]:
        """Returns ``{"answer", "pane_id", "agent"}`` for one user question."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
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

        active = self._tools.last_active
        return {
            "answer": answer,
            "pane_id": active.pane_id if active else None,
            "agent": active.agent if active else None,
        }

    def _invoke(self, call: Any) -> str:
        """Dispatches one tool call, turning every failure into a string."""
        try:
            arguments = json.loads(call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return f"error: invalid tool arguments: {exc}"
        return self._tools.dispatch(call.function.name, arguments)
