"""Neutral key orthography for the mux port.

Pins the canonical spelling, the dialog allowlist guard and the tmux
translation table. Safety: nothing here touches a terminal; adapters must
validate against this orthography BEFORE spawning any multiplexer.
"""

from __future__ import annotations

import pytest

from herdr_brain.herdr import SEND_KEY_ALLOWLIST
from herdr_brain.mux.keys import (
    SENDABLE_KEYS,
    is_sendable_key,
    normalize_key,
    tmux_key,
)

ALIAS_CASES = [
    # canonical spellings survive untouched
    ("enter", "enter"),
    ("esc", "esc"),
    ("up", "up"),
    ("down", "down"),
    ("left", "left"),
    ("right", "right"),
    ("tab", "tab"),
    ("ctrl+c", "ctrl+c"),
    ("shift+tab", "shift+tab"),
    ("f7", "f7"),
    # case is irrelevant
    ("Enter", "enter"),
    ("ENTER", "enter"),
    ("Escape", "esc"),
    ("ESC", "esc"),
    ("Up", "up"),
    ("Down", "down"),
    ("Right", "right"),
    ("F7", "f7"),
    ("F12", "f12"),
    # documented aliases fold onto the canonical name
    ("return", "enter"),
    ("DEL", "delete"),
    ("del", "delete"),
    ("backspace", "backspace"),
    (" space ", "space"),
    ("spc", "space"),
    ("page_up", "pageup"),
    ("page-down", "pagedown"),
    # chords: both separators, every modifier alias
    ("C-c", "ctrl+c"),
    ("CTRL+C", "ctrl+c"),
    ("Control-c", "ctrl+c"),
    ("ctrl-c", "ctrl+c"),
    ("Shift-Tab", "shift+tab"),
    ("BTab", "shift+tab"),
    ("backtab", "shift+tab"),
    ("alt+x", "alt+x"),
    ("option+f", "alt+f"),
    ("meta+a", "meta+a"),
    ("cmd+a", "meta+a"),
    ("super+a", "meta+a"),
]


@pytest.mark.parametrize("raw,expected", ALIAS_CASES)
def test_normalize_key_aliases(raw: str, expected: str):
    assert normalize_key(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        # spellings herdr's own probes rejected: never silently accepted here
        "arrowleft",
        "leftarrow",
        # unknown words and malformed chords
        "hello",
        "ctrl++",
        "meta+shift",
        "ctrl+notakey",
        "f13",
        "f0",
    ],
)
def test_normalize_key_rejects_unknown(raw: str):
    with pytest.raises(ValueError, match="unrecognized key spelling"):
        normalize_key(raw)


def test_sendable_keys_mirror_the_dialog_allowlist():
    # Same closed set herdr guards with, in neutral spelling: one guard,
    # three multiplexers.
    assert SENDABLE_KEYS == frozenset(SEND_KEY_ALLOWLIST)


@pytest.mark.parametrize("key", sorted(SEND_KEY_ALLOWLIST))
def test_dialog_keys_are_sendable(key: str):
    assert is_sendable_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "ctrl+c",  # a real chord, but dialogs are navigated, not interrupted
        "shift+tab",
        "0",
        "f7",
        "alt+x",
        "delete",
        "bogus-key",
        "",
    ],
)
def test_non_dialog_keys_are_not_sendable(key: str):
    assert is_sendable_key(key) is False


def test_alias_spellings_are_sendable():
    # The orthography normalizes FIRST, so "Escape" reaches the pane as esc.
    assert is_sendable_key("Escape") is True
    assert is_sendable_key("UP") is True


TMUX_CASES = [
    ("enter", "Enter"),
    ("esc", "Escape"),
    ("tab", "Tab"),
    ("shift+tab", "BTab"),
    ("up", "Up"),
    ("down", "Down"),
    ("left", "Left"),
    ("right", "Right"),
    ("delete", "Delete"),
    ("backspace", "BSpace"),
    ("home", "Home"),
    ("end", "End"),
    ("pageup", "PageUp"),
    ("pagedown", "PageDown"),
    ("space", "Space"),
    ("f7", "F7"),
    ("ctrl+c", "C-c"),
    ("a", "a"),  # a literal character types itself
]


@pytest.mark.parametrize("neutral,expected", TMUX_CASES)
def test_tmux_translation(neutral: str, expected: str):
    assert tmux_key(neutral) == expected


@pytest.mark.parametrize(
    "neutral",
    [
        "ctrl+tab",  # chord onto a named key: no verified tmux spelling
        "ctrl+f7",
        "ctrl+1",  # ctrl over a digit
        "meta+shift+x",  # stacked modifiers: refused, not approximated
    ],
)
def test_tmux_translation_refuses_unverified_spellings(neutral: str):
    with pytest.raises(ValueError, match="no tmux spelling"):
        tmux_key(neutral)
