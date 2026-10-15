"""The opencode permission dialog, read off the screen it draws.

An opencode agent that needs permission draws a modal whose option row is one
terminal line holding three things: the options, a wide gap, and the key
hints the dialog prints for itself. Observed live (a real capture, kept as a
fixture) the row is::

    ┃  Allow once  Allow always  Reject                  ctrl+f fullscreen  ⇆ select  enter confirm

with the SELECTED option drawn as an inked pill — dark text on the accent
colour — and the others plain grey. That pill is the only place the selection
exists: the highlight lives in the escape codes and nowhere else, which is
why a plain-text read is refused rather than guessed.

Three layout facts this module leans on, all of them from the capture:

- options are separated by 2-3 spaces; the hint block sits after an 18-space
  gap, so a run of more than `_MAX_OPTION_GAP` spaces ends the option group;
- the option group shares ONE background, and it is the background just
  outside the pill — that is what keeps the status bubble on the same line
  (a different background) from being read as an option;
- the hints are (key, verb) pairs, and the verbs are the dialog's own words:
  keys are taken from what the screen printed, never synthesised.
"""

from __future__ import annotations

import hashlib
import re
from typing import List, NamedTuple, Optional, Sequence

from .dialog import DialogAction, DialogModel
from .herdr import is_valid_key

#: A styled run of text: colour codes are "r,g,b" strings, None = default.
class StyledRun(NamedTuple):
    fg: Optional[str]
    bg: Optional[str]
    text: str


_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_OTHER_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

#: Spaces inside the option group are 2-3 wide; the hint block is separated by
#: a much wider run. Anything longer than this ends the group.
_MAX_OPTION_GAP = 8

#: The glyph the dialog draws its box with; the box is how the heading is
#: told apart from whatever scrolled above it.
_BORDER = "┃"

#: What the screen's own arrow affordance means. An unlisted glyph yields no
#: navigation keys at all: guessing which arrows a dialog means is how a
#: keystroke ends up somewhere it was never offered.
_NAV_GLYPHS = {
    "⇆": ("left", "right"),
    "⇄": ("left", "right"),
    "↔": ("left", "right"),
    "⇅": ("up", "down"),
    "↕": ("up", "down"),
}


def parse_styled(text: str) -> List[StyledRun]:
    """Splits a styled screen into runs of (fg, bg, text)."""
    runs: List[StyledRun] = []
    fg: Optional[str] = None
    bg: Optional[str] = None
    for token in _SGR.split(text):
        if token and set(token) <= set("0123456789;"):
            codes = [code for code in token.split(";") if code]
            if "0" in codes:
                fg = bg = None
            for position, code in enumerate(codes):
                if position + 4 < len(codes) and codes[position + 1] == "2":
                    colour = ",".join(codes[position + 2 : position + 5])
                    if code == "38":
                        fg = colour
                    elif code == "48":
                        bg = colour
        elif token:
            cleaned = _OTHER_CSI.sub("", token)
            if cleaned:
                runs.append(StyledRun(fg, bg, cleaned))
    return runs


def detect_dialog_from_text(text: str) -> Optional[DialogModel]:
    """Convenience wrapper for callers holding the raw styled screen."""
    return detect_dialog(parse_styled(text))


def detect_dialog(runs: Sequence[StyledRun]) -> Optional[DialogModel]:
    """The dialog on this screen, or ``None`` — never a guess.

    Refuses when: nothing is styled (the highlight is invisible, so no
    signature could be trusted), no option group with a selected pill is
    found, or the dialog named no usable keys.
    """
    lines = _lines(runs)
    if not any(run.bg for line in lines for run in line):
        return None
    for index, line in enumerate(lines):
        found = _option_group(line)
        if found is None:
            continue
        group, selected_run = found
        labels = [run for run in group if not _is_space(run)]
        if len(labels) < 2:
            continue
        hints = _hint_keys(line[line.index(group[-1]) + 1 :])
        nav_keys = hints.get("select", ())
        commit_keys = hints.get("confirm", ())
        if not nav_keys and not commit_keys:
            return None
        selected = next(i for i, run in enumerate(labels) if run is selected_run)
        title = _title(lines[:index])
        return DialogModel(
            title=title,
            actions=tuple(
                DialogAction(label=run.text.strip(), keys=()) for run in labels
            ),
            nav_keys=nav_keys,
            commit_keys=commit_keys,
            selected=selected,
            signature=_signature(title, labels, selected, hints),
        )
    return None


# -- internals --------------------------------------------------------------


def _lines(runs: Sequence[StyledRun]) -> List[List[StyledRun]]:
    lines: List[List[StyledRun]] = [[]]
    for run in runs:
        parts = run.text.split("\n")
        for position, part in enumerate(parts):
            if position:
                lines.append([])
            if part:
                lines[-1].append(StyledRun(run.fg, run.bg, part))
    return [line for line in lines if line]


def _is_space(run: StyledRun) -> bool:
    return not run.text.strip()


def _luma(colour: Optional[str]) -> Optional[int]:
    if not colour:
        return None
    try:
        return sum(int(part) for part in colour.split(","))
    except ValueError:
        return None


def _inked(run: StyledRun) -> int:
    """How pill-like a run is: a light background under dark text.

    The dialog draws the selected option that way, and nothing else on the
    line comes close, so the maximum is the selection.
    """
    background = _luma(run.bg)
    foreground = _luma(run.fg)
    if background is None:
        return -1
    return background - (foreground if foreground is not None else background)


def _option_group(line: Sequence[StyledRun]):
    """The option group and the selected run inside it, or ``None``."""
    candidates = [
        (position, run)
        for position, run in enumerate(line)
        if not _is_space(run) and _inked(run) > 0
    ]
    if not candidates:
        return None
    position, selected = max(candidates, key=lambda pair: _inked(pair[1]))

    pill_bg = selected.bg
    low = high = position
    while low - 1 >= 0 and line[low - 1].bg == pill_bg:
        low -= 1
    while high + 1 < len(line) and line[high + 1].bg == pill_bg:
        high += 1

    row_bg = line[low - 1].bg if low - 1 >= 0 else None
    if row_bg is None:
        return None

    walk = high + 1
    while walk < len(line):
        run = line[walk]
        if run.bg != row_bg:
            break
        if _is_space(run):
            if len(run.text) > _MAX_OPTION_GAP:
                break
        elif not _is_space(line[walk - 1]):
            break  # a key+verb hint pair, not an option
        walk += 1
    high = walk - 1

    walk = low - 1
    while walk >= 0:
        run = line[walk]
        if run.bg != row_bg:
            break
        if _is_space(run):
            if len(run.text) > _MAX_OPTION_GAP:
                break
        elif walk + 1 < len(line) and not _is_space(line[walk + 1]):
            break
        walk -= 1
    low = walk + 1

    group = list(line[low : high + 1])
    if selected not in group:
        return None
    return group, selected


def _hint_keys(runs: Sequence[StyledRun]) -> dict:
    """The keys the footer named, by the verb it named them for."""
    found: dict = {}
    for position, run in enumerate(runs):
        verb = run.text.strip()
        if verb not in ("select", "confirm") or position == 0:
            continue
        token = runs[position - 1].text.strip()
        if verb == "select":
            keys = _NAV_GLYPHS.get(token, ())
            if keys:
                found["select"] = keys
        elif is_valid_key(token):
            found["confirm"] = (token,)
    return found


def _title(above: Sequence[Sequence[StyledRun]]) -> str:
    """The dialog's own heading: the top informative lines INSIDE its box.

    Walking up from the option row while the lines still carry the box
    border, then keeping the ones FARTHEST up. Nearest-to-the-options is
    wrong: there sit the pattern list and the working directory, which are
    the dialog's evidence, not its name.
    """
    interior: List[str] = []
    for line in reversed(above):
        texts = [run.text.strip() for run in line if not _is_space(run)]
        if not texts or texts[0] != _BORDER:
            break
        rest = " ".join(texts[1:]).strip()
        if rest:
            interior.append(rest)
    return " — ".join(reversed(interior[-2:]))


def _signature(
    title: str, labels: Sequence[StyledRun], selected: int, hints: dict
) -> str:
    """A digest of the dialog WITH its highlight marked.

    This is the guard's load-bearing check: herdr's pane revision is a stub
    upstream, so a moved highlight has to change these bytes or a stale tap
    would be indistinguishable from a fresh one.
    """
    marked = [
        (">" if position == selected else "") + run.text.strip()
        for position, run in enumerate(labels)
    ]
    material = "|".join(
        [title, *marked, *sorted(f"{verb}:{','.join(keys)}" for verb, keys in hints.items())]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
