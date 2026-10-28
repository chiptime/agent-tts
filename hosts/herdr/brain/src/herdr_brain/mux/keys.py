"""Neutral key orthography shared by the mux adapters.

Every multiplexer spells chords its own way, so the port speaks ONE
canonical spelling and each adapter translates to its own. Canonical
form: lower-case names joined with ``+``, modifiers first — ``ctrl+c``,
``shift+tab``, ``f7``.

The dialog allowlist is the brain's own guard, inherited from
:data:`herdr_brain.herdr.SEND_KEY_ALLOWLIST` and expressed in neutral
spelling: adapters validate against it BEFORE spawning the multiplexer,
so a name outside it never reaches a live terminal.
"""

from __future__ import annotations

import re

from ..herdr import SEND_KEY_ALLOWLIST

#: Keys a dialog may be driven with, in neutral spelling. One guard for
#: every adapter: the set is closed on purpose.
SENDABLE_KEYS = frozenset(SEND_KEY_ALLOWLIST)

_BASE_NAMES = {
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "enter": "enter",
    "return": "enter",
    "esc": "esc",
    "escape": "esc",
    "tab": "tab",
    "btab": "shift+tab",
    "backtab": "shift+tab",
    "delete": "delete",
    "del": "delete",
    "backspace": "backspace",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "page_up": "pageup",
    "page-up": "pageup",
    "pagedown": "pagedown",
    "page_down": "pagedown",
    "page-down": "pagedown",
    "space": "space",
    "spc": "space",
}

_MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "c": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "meta": "meta",
    "cmd": "meta",
    "super": "meta",
    "command": "meta",
}

_SPLIT = re.compile(r"\s*[-+]\s*")
_SINGLE = re.compile(r"^[a-z0-9]$")
_FUNCTION = re.compile(r"^f([1-9]|1[0-2])$")


def normalize_key(raw: str) -> str:
    """Normalizes one key spelling to the canonical neutral form.

    Accepts case differences, ``-``/``+`` separators and the documented
    aliases (``Escape``→``esc``, ``C-c``→``ctrl+c``, ``BTab``→
    ``shift+tab``). Anything unrecognized raises :class:`ValueError` — a
    spelling the orthography does not know never travels further.
    """
    text = (raw or "").strip().lower()
    if not text:
        raise ValueError("unrecognized key spelling: ''")
    # A full-name alias is checked BEFORE splitting: names like
    # "page-down" contain the chord separator themselves.
    if text in _BASE_NAMES:
        return _BASE_NAMES[text]
    tokens = _SPLIT.split(text)
    if any(token == "" for token in tokens):
        raise ValueError(f"unrecognized key spelling: {raw!r}")
    *mods_raw, base = tokens
    mods = []
    for token in mods_raw:
        canonical = _MODIFIERS.get(token)
        if canonical is None:
            raise ValueError(f"unrecognized key spelling: {raw!r}")
        if canonical not in mods:
            mods.append(canonical)
    if base in _BASE_NAMES:
        canonical_base = _BASE_NAMES[base]
    elif _SINGLE.match(base) or _FUNCTION.match(base):
        canonical_base = base
    else:
        raise ValueError(f"unrecognized key spelling: {raw!r}")
    if not mods:
        return canonical_base
    return "+".join([*mods, canonical_base])


def is_sendable_key(raw: str) -> bool:
    """Whether `raw` normalizes onto the dialog allowlist; never raises."""
    try:
        return normalize_key(raw) in SENDABLE_KEYS
    except ValueError:
        return False


#: Neutral → tmux key-table spellings (``enter``→``Enter``, ``esc``→
#: ``Escape``, ``shift+tab``→``BTab``).
_TMUX_NAMES = {
    "enter": "Enter",
    "esc": "Escape",
    "tab": "Tab",
    "shift+tab": "BTab",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "delete": "Delete",
    "backspace": "BSpace",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "space": "Space",
}

_TMUX_MODIFIERS = {"ctrl": "C", "shift": "S", "alt": "M", "meta": "M"}


def tmux_key(neutral: str) -> str:
    """Translates one canonical key to tmux's key-table spelling.

    Only spellings with a verified tmux equivalent translate; chords over
    named keys, digits under ctrl, and stacked modifiers raise
    :class:`ValueError` rather than approximating.
    """
    if neutral in _TMUX_NAMES:
        return _TMUX_NAMES[neutral]
    if _FUNCTION.match(neutral):
        return neutral.upper()  # f7 -> F7
    if _SINGLE.match(neutral):
        return neutral  # a literal character types itself
    parts = neutral.split("+")
    base, mods = parts[-1], parts[:-1]
    if (
        len(mods) == 1
        and base.isalpha()
        and _SINGLE.match(base)
        and mods[0] in _TMUX_MODIFIERS
    ):
        return f"{_TMUX_MODIFIERS[mods[0]]}-{base}"
    raise ValueError(f"no tmux spelling for key: {neutral!r}")
