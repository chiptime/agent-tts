"""Live pane mirror: the visible window as ANSI-styled runs.

herdr already delivers the rendered grid — this module converts that
styled read into per-line runs through the same ``ansi_runs`` the dialog
path uses, plus the escape-stripped plain text of the same window. It
deliberately runs NO terminal emulation: the pane IS the emulator, the
mirror only reproduces what it already drew. Line counts are clamped to
the visible-window cap so a mirror read never scrolls the operator's
real screen.
"""

from __future__ import annotations

from typing import Optional

from .config import MAX_SCREEN_LINES
from .view import ansi_runs

#: Lines a mirror read asks for when the client names none.
MIRROR_DEFAULT_LINES = MAX_SCREEN_LINES


def clamp_lines(raw: Optional[int]) -> int:
    """Mirror window bounded to [1, MAX_SCREEN_LINES]."""
    try:
        value = int(raw) if raw is not None else MIRROR_DEFAULT_LINES
    except (TypeError, ValueError):
        return MIRROR_DEFAULT_LINES
    return max(1, min(value, MAX_SCREEN_LINES))


def runs_text(runs: list) -> Optional[str]:
    """Escape-stripped plain text of a runs payload (None when empty)."""
    lines = ["".join(run.get("t", "") for run in line) for line in runs]
    text = "\n".join(lines)
    return text if text.strip() else None


def mirror_payload(
    pane_id: str,
    agent: Optional[str],
    status: Optional[str],
    styled_screen: Optional[str],
) -> dict:
    """Shapes the /pane/{id}/mirror body from one styled screen read."""
    runs = ansi_runs(styled_screen) if styled_screen else []
    return {
        "pane_id": pane_id,
        "agent": agent,
        "status": status,
        "runs": runs,
        "text": runs_text(runs),
    }
