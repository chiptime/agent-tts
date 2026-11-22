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


#: The guarded DIALOG-recipe dialect (T10/S3): the raw allowlist plus
#: the ONE lowercase letters a dialog's own footer named. Only the
#: recipe surface (`send_recipe_keys`) validates against this — the
#: plain keys surface keeps SENDABLE_KEYS, which is what keeps the raw
#: `POST /pane/keys` route letter-free.
_RECIPE_LETTER = re.compile(r"[a-z]")


def is_recipe_key(raw: str) -> bool:
    """Whether `raw` is a guarded dialog-recipe key; never raises.

    Same closed shape as :func:`is_sendable_key` with the narrow
    letters channel: a single lowercase letter (case-sensitive, so an
    uppercase token stays prose) or anything the plain allowlist
    already admits. Digits ride the plain allowlist; a digit is never
    minted here because the menu grammar never emits one.
    """
    return is_sendable_key(raw) or bool(_RECIPE_LETTER.fullmatch(raw or ""))


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


#: Neutral → zellij send-keys spellings (probed LIVE on zellij 0.45.1,
#: parity wave 2 S6): named keys are capitalized and chords are
#: SPACE-separated — ``Ctrl u``, never ``Ctrl+u`` (the plus spelling
#: exits 2 with ``Invalid key``).
_ZELLIJ_NAMES = {
    "enter": "Enter",
    "esc": "Esc",
    "tab": "Tab",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}

_ZELLIJ_MODIFIERS = {"ctrl": "Ctrl"}


def zellij_key(neutral: str) -> str:
    """Translates one canonical key to zellij's send-keys spelling.

    Only spellings with a LIVE-verified 0.45.1 equivalent translate:
    the named keys above, single literal characters, and single-``ctrl``
    chords over one alphabetic key. Everything else raises
    :class:`ValueError` rather than approximating — the dialog
    allowlist never asks for more.
    """
    if neutral in _ZELLIJ_NAMES:
        return _ZELLIJ_NAMES[neutral]
    if _SINGLE.match(neutral):
        return neutral  # a literal character types itself
    parts = neutral.split("+")
    base, mods = parts[-1], parts[:-1]
    if (
        len(mods) == 1
        and base.isalpha()
        and _SINGLE.match(base)
        and mods[0] in _ZELLIJ_MODIFIERS
    ):
        return f"{_ZELLIJ_MODIFIERS[mods[0]]} {base}"
    raise ValueError(f"no zellij spelling for key: {neutral!r}")


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
