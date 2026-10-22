"""Live agent view pieces: pending-action detection and text truncation.

The pending detector is EXPLICITLY HEURISTIC. It pattern-matches the tail of
the visible terminal screen plus the agent status reported by Herdr, so it
can false-positive on ordinary output (e.g. a file named ``error.log``) and
miss unusual prompts. Treat ``pending`` as a hint to show the user, never as
ground truth about what the agent wants.
"""

from __future__ import annotations

import re

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


_DIALOG_OPTION = re.compile(r"^(?:[❯>»›]\s*)?(\d{1,2})\.\s+(.+)$")
_DIALOG_SELECTION_MARKER = "❯>»›"
_DIALOG_GUTTER = re.compile(r"^[\u2503\u2502\u2506\u2507\u250a\u250b|\u2551]\s*")
_DIALOG_HINT = re.compile(r"(select|submit|dismiss|confirm)", re.IGNORECASE)


def _clean_dialog_line(raw: str) -> str:
    line = raw.strip()
    line = _DIALOG_GUTTER.sub("", line, count=1).strip()
    # Terminal columns are separated by wide space runs: the pane's width
    # appends unrelated side content (paths, status) to dialog lines.
    return re.split(r"\s{3,}", line)[0].strip()


def parse_dialog(text):
    """Recovers a question tool's structure from pane text, or None.

    The screen already carries the question, the numbered options with their
    descriptions and the key hint; this shapes them so the PWA can paint
    readable HTML instead of a terminal mirror. A leading selection marker
    (``❯``, ``>``, ``»``, ``›``) on an option line is presentation, not
    content: it is stripped from the label and recorded as that option's
    ``selected`` flag (plus the dialog-level ``selected`` number) so the
    spoken choices and the voice-answer path know the CURRENT highlight
    without ever letting the marker leak into a spoken label. It claims
    nothing beyond what the surface actually shows.
    """
    lines = [_clean_dialog_line(raw) for raw in (text or "").splitlines()]
    option_at = []
    selected = None
    hint = ""
    hint_at = -1
    for i, line in enumerate(lines):
        match = _DIALOG_OPTION.match(line)
        if match:
            number = int(match.group(1))
            marked = line[0] in _DIALOG_SELECTION_MARKER
            if marked and selected is None:
                selected = number
            option_at.append((i, number, match.group(2).strip(), marked))
        elif not hint and line and _DIALOG_HINT.search(line):
            hint = line
            hint_at = i
    if len(option_at) < 2:
        return None

    # The question: the nearest non-empty line(s) directly above the first
    # option, stopping at the first gap once something was collected.
    question_lines = []
    i = option_at[0][0] - 1
    while i >= 0 and len(question_lines) < 2:
        line = lines[i]
        if not line:
            if question_lines:
                break
            i -= 1
            continue
        if _DIALOG_OPTION.match(line) or _DIALOG_HINT.search(line):
            break
        question_lines.insert(0, line)
        i -= 1
    if not question_lines:
        return None

    options = []
    for pos, (index, number, title, marked) in enumerate(option_at):
        end = option_at[pos + 1][0] if pos + 1 < len(option_at) else len(lines)
        description = []
        for j in range(index + 1, end):
            line = lines[j]
            if not line:
                continue
            if j == hint_at or _DIALOG_HINT.search(line) or _DIALOG_OPTION.match(line):
                break
            description.append(line)
        options.append(
            {
                "n": number,
                "title": title,
                "description": " ".join(description),
                "selected": marked,
            }
        )

    return {
        "question": " ".join(question_lines),
        "options": options,
        "hint": hint,
        "selected": selected,
    }


def speakable_dialog(dialog, agent=None) -> str:
    """Renders a parsed dialog as ONE concise spoken prompt (S8/FR-08).

    FAITHFUL, never interpretive: the actual question and every option
    label verbatim — nothing invented, no business choice omitted — so a
    user who cannot see the screen still knows exactly what the choice
    is. Option DESCRIPTIONS are deliberately not read out (concise by
    design); the labels carry the semantics. Example:

        "opencode pregunta: ¿Corremos los tests? Opción 1: Sí. Opción 2: No."
    """
    if not dialog or not dialog.get("options"):
        return ""
    who = f"{agent} pregunta:" if agent else "El agente pregunta:"
    question = " ".join((dialog.get("question") or "").split())
    parts = [f"{who} {question}".strip()]
    for option in dialog["options"]:
        number = option.get("n")
        title = " ".join(str(option.get("title") or "").split())
        parts.append(f"Opción {number}: {title}.")
    return " ".join(part for part in parts if part)


def dialog_fingerprint(dialog) -> str:
    """Stable identity of a dialog's business content (S9 changed-guard).

    The question, every numbered option label AND its description, in
    order: descriptions carry meaning-bearing content (destinations,
    scopes, environments), so a dialog whose description changed is a
    DIFFERENT action and prior spoken authority must not survive the
    change. Presentation-only drift — the ``selected`` highlight marker
    or ANSI styling — never reaches the flattened parse text and so
    cannot perturb the identity. Whitespace is normalized so terminal
    wrapping noise alone does not invalidate an explanation.
    """
    if not dialog:
        return ""
    parts = [" ".join((dialog.get("question") or "").split())]
    for option in dialog.get("options") or []:
        label = " ".join(str(option.get("title") or "").split())
        description = " ".join(str(option.get("description") or "").split())
        parts.append(f"{option.get('n')}. {label}\x1e{description}")
    return "\x1f".join(parts)


def selection_from_runs(runs, dialog) -> Optional[int]:
    """The dialog's selected option number derived from ANSI backgrounds.

    The styled read preserves per-run ``bg``; a question tool marks the
    highlighted option by painting its line's background. Exactly one
    option line carrying a background means a KNOWN selection; zero (no
    styling) or several (ambiguous painting) mean unknown — the caller
    must then refuse rather than guess. A textual ``❯`` marker already
    recorded by ``parse_dialog`` always wins over styling.
    """
    if not runs or not dialog:
        return dialog.get("selected") if dialog else None
    if dialog.get("selected") is not None:
        return dialog["selected"]
    numbers = {option.get("n") for option in dialog.get("options") or []}
    highlighted = set()
    for line in runs:
        if not any(run.get("bg") for run in line):
            continue
        flat = _clean_dialog_line("".join(run.get("t", "") for run in line))
        match = _DIALOG_OPTION.match(flat)
        if match and int(match.group(1)) in numbers:
            highlighted.add(int(match.group(1)))
    if len(highlighted) == 1:
        return next(iter(highlighted))
    return None
