"""Live agent view pieces: pending-action detection and text truncation.

The pending detector is EXPLICITLY HEURISTIC. It pattern-matches the tail of
the visible terminal screen plus the agent status reported by Herdr, so it
can false-positive on ordinary output (e.g. a file named ``error.log``) and
miss unusual prompts. Treat ``pending`` as a hint to show the user, never as
ground truth about what the agent wants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Tail sizes used by GET /view and the LLM live-context hint.
TRANSCRIPT_TAIL_TURNS = 3
SCREEN_TAIL_LINES = 12
VIEW_TEXT_TRUNCATE = 300
MAX_EXCERPT_CHARS = 200

# Substrings that suggest the agent is waiting for a permission decision.
PERMISSION_MARKERS = (
    "y/n",
    "yes/no",
    "sí/no",
    "si/no",
    "allow",
    "deny",
    "permit",
    "permission",
    "approve",
    "confirm",
)
# Substrings that suggest the agent hit an error.
ERROR_MARKERS = ("error", "failed", "traceback")


@dataclass(frozen=True)
class PendingAction:
    """Heuristic verdict about an unresolved prompt on the agent's screen."""

    detected: bool
    kind: Optional[str]  # "question" | "permission" | "error" | None
    excerpt: Optional[str]


def truncate_text(text: str, limit: int) -> str:
    """Caps text at ``limit`` chars with an ellipsis marker when cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def detect_pending(screen_text: Optional[str], agent_status: str = "") -> PendingAction:
    """Scans the screen tail bottom-up for an unresolved prompt.

    Rules:
    - A line matching permission markers wins with kind ``permission``;
      otherwise error markers give ``error``; otherwise a line ending in
      ``?`` (or "press enter") gives ``question``. The first match from the
      bottom becomes the excerpt.
    - ``detected`` is true when a line matched OR the agent status is
      ``blocked``. If only the status fired, the excerpt falls back to the
      last non-empty screen line and kind stays ``None`` (unknown ask).
    """
    lines = [ln.strip() for ln in (screen_text or "").splitlines() if ln.strip()]
    excerpt: Optional[str] = None
    kind: Optional[str] = None

    for line in reversed(lines):
        low = line.lower()
        permission = any(marker in low for marker in PERMISSION_MARKERS)
        error = any(marker in low for marker in ERROR_MARKERS)
        question = line.endswith("?") or "press enter" in low
        if permission or error or question:
            excerpt = truncate_text(line, MAX_EXCERPT_CHARS)
            kind = "permission" if permission else ("error" if error else "question")
            break

    blocked = (agent_status or "").strip().lower() == "blocked"
    detected = kind is not None or blocked
    if detected and excerpt is None and lines:
        excerpt = truncate_text(lines[-1], MAX_EXCERPT_CHARS)

    return PendingAction(
        detected=detected,
        kind=kind if detected else None,
        excerpt=excerpt if detected else None,
    )
