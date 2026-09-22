"""Orchestrator for the agent transcript connector layer (Vision B).

Contract: hosts (shell wrappers, agent integrations, ...) pass an agent identity and a
session id; the engine owns all transcript/source knowledge and resolves the
last assistant message itself. If no connector matches, callers keep their
terminal-scrollback fallback text untouched.

Sniffing: ``ses_*`` ids route straight to OpenCode; UUID-like ids belong to
several tools (Claude Code, Codex, Antigravity) whose local stores are
disjoint, so each candidate adapter is tried in turn and the first readable
message wins. Aider has no session store at all: its adapter is
explicit-name-only and reads the project's markdown chat history, treating
the session id as a pass-through label.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Session id shapes used for sniffing when no (or an unknown) agent name is given.
OPENCODE_SESSION_RE = re.compile(r"^ses_[A-Za-z0-9_-]{6,}$")
CLAUDE_SESSION_RE = re.compile(r"^[\da-fA-F-]{16,}$")


@dataclass
class SourceResult:
    """A transcript message resolved by a connector adapter.

    Attributes:
        text: The message text, ready for the speech cleaning pipeline.
        source: Identifier of the adapter that resolved it (e.g. "opencode").
    """

    text: str
    source: str


def read_last_agent_message(
    agent: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[SourceResult]:
    """Resolve the last assistant message of an agent session.

    Routes by ``agent`` name to the matching connector adapter. When the agent
    is unknown or omitted, the session id shape is sniffed (``ses_*`` ->
    OpenCode; UUID-like -> Claude Code, then Codex, then Antigravity, keeping
    the first hit). Returns ``None`` when nothing matches or the adapter finds
    no readable message, signaling the caller to fall back to whatever text it
    already has (typically terminal scrollback).
    """
    if not session_id:
        return None

    for name, adapter in _adapter_candidates(agent, session_id):
        text = adapter.read(session_id)
        if text:
            return SourceResult(text=text, source=name)
    return None


def _adapter_candidates(
    agent: Optional[str], session_id: str
) -> List[Tuple[str, object]]:
    """Returns the ``(name, adapter)`` candidates to try, in order."""
    from agent_tts.sources.opencode import OpencodeSource
    from agent_tts.sources.claude import ClaudeSource
    from agent_tts.sources.antigravity import AntigravitySource
    from agent_tts.sources.codex import CodexSource
    from agent_tts.sources.aider import AiderSource

    registry = {
        "opencode": ("opencode", OpencodeSource),
        "claude": ("claude", ClaudeSource),
        "antigravity": ("antigravity", AntigravitySource),
        "agy": ("antigravity", AntigravitySource),
        "codex": ("codex", CodexSource),
        "aider": ("aider", AiderSource),
    }

    key = (agent or "").strip().lower()
    if key in registry:
        name, adapter_cls = registry[key]
        return [(name, adapter_cls())]

    # Unknown or missing agent name: sniff by session id shape.
    if OPENCODE_SESSION_RE.match(session_id):
        return [("opencode", OpencodeSource())]
    if CLAUDE_SESSION_RE.match(session_id):
        # Claude Code, Codex and Antigravity all key sessions by UUID-like
        # ids; their local stores are disjoint, so try each in turn and keep
        # the first adapter that resolves a readable message.
        return [
            ("claude", ClaudeSource()),
            ("codex", CodexSource()),
            ("antigravity", AntigravitySource()),
        ]
    return []
