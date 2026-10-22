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


def _is_default_bg(bg: Optional[str]) -> bool:
    """Returns True if the background colour is absent or near-black terminal chrome."""
    if not bg:
        return True
    return bg.lower() in ("#000000", "#0a0a0a", "#141414", "#0f141c", "#1e1e1e", "#181825")


def _apply_sgr(params: str, fg, bg, bold, rev=False):
    """Applies one SGR parameter list and returns the new style state."""
    try:
        codes = [int(part) if part else 0 for part in params.split(";")] if params else [0]
    except ValueError:
        return fg, bg, bold, rev
    i = 0
    while i < len(codes):
        code = codes[i]
        if code == 0:
            fg, bg, bold, rev = None, None, False, False
        elif code == 1:
            bold = True
        elif code == 7:
            rev = True
        elif code == 22:
            bold = False
        elif code == 27:
            rev = False
        elif 30 <= code <= 37:
            fg = _hex_colour(_ANSI16_RGB[code - 30])
        elif code == 39:
            fg = None
        elif 40 <= code <= 47:
            bg = _hex_colour(_ANSI16_RGB[code - 40])
        elif code == 49:
            bg = None
        elif 90 <= code <= 97:
            fg = _hex_colour(_ANSI16_RGB[code - 90 + 8])
        elif 100 <= code <= 107:
            bg = _hex_colour(_ANSI16_RGB[code - 100 + 8])
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
    return fg, bg, bold, rev


def ansi_runs(text: Optional[str]) -> list:
    """Splits ANSI-styled terminal text into per-line runs of ``{t, fg?, bg?, b?, rev?}``.

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
    rev = False

    def style_keys() -> dict:
        style = {}
        if fg:
            style["fg"] = fg
        if bg:
            style["bg"] = bg
        if bold:
            style["b"] = True
        if rev:
            style["rev"] = True
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
                fg, bg, bold, rev = _apply_sgr(source[i + 2 : j], fg, bg, bold, rev)
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


_DIALOG_OPTION = re.compile(r"^(\d{1,2})[\.\)]\s+(.+)$")
_DIALOG_GUTTER = re.compile(r"^[\u2503\u2502\u2506\u2507\u250a\u250b|\u2551]\s*")
_DIALOG_HINT = re.compile(r"(select|submit|dismiss|confirm)", re.IGNORECASE)
_DIALOG_CURSOR = re.compile(r"^([❯>●*→›»►▸■✓✔]|\(\s*[•*xX✓✔]\s*\)|\[\s*[•*xX✓✔]\s*\])\s*")
_HEADER_IGNORE = re.compile(r"^(?:→\s*Asked|Thought\s*[·•]|△\s*Permission|[─━═]{3,}|\*{3,})", re.IGNORECASE)
_PERMISSION_BUTTON = re.compile(
    r"\b(allow once|allow always|always allow|allow|reject|confirm|cancel|deny|yes|no)\b",
    re.IGNORECASE,
)


def _clean_dialog_line(raw: str) -> str:
    line = raw.strip()
    line = _DIALOG_GUTTER.sub("", line, count=1).strip()
    # Terminal side columns (e.g. status/path in adjacent panes) are separated
    # by wide whitespace runs (6+ spaces), while button bars use 1-4 spaces.
    return re.split(r"\s{6,}", line)[0].strip()


def _extract_question(lines: list[str], first_option_idx: int) -> str:
    question_lines: list[str] = []
    i = first_option_idx - 1
    while i >= 0 and len(question_lines) < 5:
        line = lines[i]
        if not line:
            if question_lines:
                break
            i -= 1
            continue
        line_no_cursor = _DIALOG_CURSOR.sub("", line, count=1).strip()
        if _DIALOG_OPTION.match(line_no_cursor) or _DIALOG_HINT.search(line) or _HEADER_IGNORE.search(line):
            break
        # If we already have lines collected and the line directly above ends with
        # sentence-ending punctuation (. or !), it is part of a previous statement, not this question.
        if question_lines and (line.endswith(".") or line.endswith("!") or line.endswith("...")):
            break
        question_lines.insert(0, line)
        if line.startswith("¿") and question_lines[-1].endswith("?"):
            break
        i -= 1
    return " ".join(question_lines).strip()


def parse_dialog(text, screen_runs=None):
    """Recovers a question or permission dialog's structure from pane text, or None.

    The screen carries the question, the options with their descriptions, the
    key hint, and which option is selected (from cursor characters or
    screen_runs style). This shapes them so the PWA can paint readable HTML
    with active option highlighting instead of a terminal mirror.
    """
    lines = [_clean_dialog_line(raw) for raw in (text or "").splitlines()]
    option_at = []
    hint = ""
    hint_at = -1
    for i, line in enumerate(lines):
        cursor_match = _DIALOG_CURSOR.match(line)
        line_no_cursor = _DIALOG_CURSOR.sub("", line, count=1).strip() if cursor_match else line
        match = _DIALOG_OPTION.match(line_no_cursor)
        if match:
            has_cursor = bool(cursor_match)
            option_at.append((i, int(match.group(1)), match.group(2).strip(), has_cursor))
        elif not hint and line and _DIALOG_HINT.search(line):
            hint = line
            hint_at = i

    if len(option_at) >= 2:
        question = _extract_question(lines, option_at[0][0])
        if not question:
            return None

        options = []
        for pos, (index, number, title, has_cursor) in enumerate(option_at):
            end = option_at[pos + 1][0] if pos + 1 < len(option_at) else len(lines)
            description = []
            for j in range(index + 1, end):
                line = lines[j]
                if not line:
                    continue
                line_clean = _DIALOG_CURSOR.sub("", line, count=1).strip()
                if j == hint_at or _DIALOG_HINT.search(line) or _DIALOG_OPTION.match(line_clean):
                    break
                description.append(line)

            selected = has_cursor or bool(
                re.search(r"(\[✓\]|\[x\]|\(selected\)|\(active\)|✓)", title, re.IGNORECASE)
            )
            options.append(
                {
                    "n": number,
                    "title": title,
                    "description": " ".join(description),
                    "selected": selected,
                }
            )

        # If no option was marked by cursor/title, inspect screen_runs if provided
        if not any(opt.get("selected") for opt in options):
            if screen_runs:
                for pos, (index, _, _, _) in enumerate(option_at):
                    if index < len(screen_runs):
                        runs = screen_runs[index]
                        if any(r.get("rev") or (r.get("bg") and not _is_default_bg(r.get("bg"))) for r in runs):
                            options[pos]["selected"] = True
                            break
            # Fall back to option 0 if nothing indicated otherwise
            if not any(opt.get("selected") for opt in options) and options:
                options[0]["selected"] = True

        return {"question": question, "options": options, "hint": hint, "layout": "list"}

    # Check for horizontal button-style permission prompts (e.g. OpenCode: Allow once   Allow always   Reject)
    button_idx = -1
    buttons: list[str] = []
    for idx, line in enumerate(lines):
        m = list(_PERMISSION_BUTTON.finditer(line))
        if len(m) >= 2:
            button_idx = idx
            buttons = [match.group(0).strip() for match in m]
            break

    if button_idx >= 0 and len(buttons) >= 2:
        header = ""
        action = ""
        detail_lines: list[str] = []
        for i in range(button_idx - 1, -1, -1):
            l = lines[i]
            if not l:
                continue
            if "permission required" in l.lower():
                header = l.lstrip("△ ").strip()
                break
            if l.startswith("→ ") or l.startswith("✱ ") or l.startswith("# "):
                action = l.lstrip("→ ✱#").strip()
            elif not action:
                detail_lines.insert(0, l)

        q = f"{header}: {action}" if header and action else (header or action or "Permission required")
        detail = " ".join(detail_lines).strip()

        # Check hint below button line if present
        hint = ""
        for i in range(button_idx + 1, min(len(lines), button_idx + 3)):
            if _DIALOG_HINT.search(lines[i]):
                hint = lines[i]
                break

        selected_idx = 0
        if screen_runs and button_idx < len(screen_runs):
            btn_runs = screen_runs[button_idx]
            for i, btn in enumerate(buttons):
                for r in btn_runs:
                    if btn.lower() in (r.get("t") or "").lower():
                        if r.get("rev") or (r.get("bg") and not _is_default_bg(r.get("bg"))):
                            selected_idx = i
                            break

        options = [
            {"n": i + 1, "title": btn, "description": "", "selected": (i == selected_idx)}
            for i, btn in enumerate(buttons)
        ]
        res = {"question": q, "options": options, "hint": hint, "layout": "buttons"}
        if detail:
            res["detail"] = detail
        return res

    return None
