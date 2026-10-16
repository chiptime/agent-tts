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


#: The 16 base ANSI colours (xterm's defaults), indexed 0-15.
_ANSI16_RGB = (
    (0x00, 0x00, 0x00), (0xCD, 0x00, 0x00), (0x00, 0xCD, 0x00), (0xCD, 0xCD, 0x00),
    (0x00, 0x00, 0xEE), (0xCD, 0x00, 0xCD), (0x00, 0xCD, 0xCD), (0xE5, 0xE5, 0xE5),
    (0x7F, 0x7F, 0x7F), (0xFF, 0x00, 0x00), (0x00, 0xFF, 0x00), (0xFF, 0xFF, 0x00),
    (0x5C, 0x5C, 0xFF), (0xFF, 0x00, 0xFF), (0x00, 0xFF, 0xFF), (0xFF, 0xFF, 0xFF),
)


def _hex_colour(rgb) -> str:
    return "#%02x%02x%02x" % (rgb[0], rgb[1], rgb[2])


def _colour_from_256(index: int) -> Optional[str]:
    """Resolves an xterm 256-colour index, so a colour is never inherited."""
    if 0 <= index < 16:
        return _hex_colour(_ANSI16_RGB[index])
    if 16 <= index < 232:
        steps = (0, 95, 135, 175, 215, 255)
        n = index - 16
        return _hex_colour((steps[n // 36], steps[(n // 6) % 6], steps[n % 6]))
    if 232 <= index < 256:
        level = 8 + (index - 232) * 10
        return _hex_colour((level, level, level))
    return None


def _apply_sgr(params: str, fg, bg, bold):
    """Applies one SGR parameter list and returns the new style state."""
    try:
        codes = [int(part) if part else 0 for part in params.split(";")] if params else [0]
    except ValueError:
        return fg, bg, bold
    i = 0
    while i < len(codes):
        code = codes[i]
        if code == 0:
            fg, bg, bold = None, None, False
        elif code == 1:
            bold = True
        elif code == 22:
            bold = False
        elif code == 39:
            fg = None
        elif code == 49:
            bg = None
        elif code in (38, 48) and i + 1 < len(codes):
            kind = codes[i + 1]
            value = None
            if kind == 2 and i + 4 < len(codes):
                value = _hex_colour((codes[i + 2], codes[i + 3], codes[i + 4]))
                i += 4
            elif kind == 5 and i + 2 < len(codes):
                value = _colour_from_256(codes[i + 2])
                i += 2
            if code == 38:
                fg = value
            else:
                bg = value
        i += 1
    return fg, bg, bold


def ansi_runs(text: Optional[str]) -> list:
    """Splits ANSI-styled terminal text into per-line runs of ``{t, fg?, bg?, b?}``.

    FAITHFUL, never interpretive: it reproduces the colours and weight the
    terminal already rendered and decides nothing about their meaning. That is
    what lets the PWA show WHICH option of a permission dialog is highlighted
    without anyone guessing what pressing Enter would do.

    Non-SGR escapes are dropped rather than leaked into the text, and a bare
    carriage return is dropped too (true overwrite semantics are not emulated;
    ``\\r\\n`` ends a line). Adjacent runs with the same style are merged.
    """
    out: list = []
    line: list = []
    fg = bg = None
    bold = False

    def style_keys() -> dict:
        style = {}
        if fg:
            style["fg"] = fg
        if bg:
            style["bg"] = bg
        if bold:
            style["b"] = True
        return style

    def emit(chunk: str) -> None:
        if not chunk:
            return
        keys = style_keys()
        if line and {k: v for k, v in line[-1].items() if k != "t"} == keys:
            line[-1]["t"] += chunk
            return
        line.append(dict(keys, t=chunk))

    source = text or ""
    i = 0
    while i < len(source):
        char = source[i]
        if char == "\x1b" and source[i + 1 : i + 2] == "[":
            j = i + 2
            while j < len(source) and not ("@" <= source[j] <= "~"):
                j += 1
            if j >= len(source):
                break  # a truncated escape is not text
            if source[j] == "m":
                fg, bg, bold = _apply_sgr(source[i + 2 : j], fg, bg, bold)
            i = j + 1
            continue
        if char == "\r":
            i += 1
            continue
        if char == "\n":
            out.append(line)
            line = []
            i += 1
            continue
        j = i
        while j < len(source) and source[j] not in "\x1b\r\n":
            j += 1
        emit(source[i:j])
        i = j

    if line:
        out.append(line)
    return out
