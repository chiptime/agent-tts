"""ANSI screen runs: faithful style, never interpretation.

The brain used to read the terminal with ``--format text``, which throws away
the only signal that says WHICH option of a permission dialog is highlighted.
These runs carry the colour and weight through to the PWA so the operator can
see the real dialog instead of plain words. Nothing here decides what a key
would do — it only reproduces what the terminal already rendered.
"""

from __future__ import annotations

from herdr_brain.view import ansi_runs


def test_plain_text_is_one_codeless_run():
    assert ansi_runs("hola\nmundo") == [
        [{"t": "hola"}],
        [{"t": "mundo"}],
    ]


def test_bold_and_reset():
    assert ansi_runs("\x1b[1mA\x1b[0mB") == [[{"t": "A", "b": True}, {"t": "B"}]]


def test_truecolor_foreground_and_background():
    line = ansi_runs("\x1b[38;2;255;0;0m\x1b[48;2;0;0;10mX\x1b[0m")[0]

    assert line == [{"t": "X", "fg": "#ff0000", "bg": "#00000a"}]


def test_several_codes_in_one_sequence():
    line = ansi_runs("\x1b[0;1;38;2;1;2;3mZ")[0]

    assert line == [{"t": "Z", "fg": "#010203", "b": True}]


def test_256_colour_is_resolved_not_inherited():
    """An extended colour must not silently keep the previous one."""
    line = ansi_runs("\x1b[38;2;9;9;9m\x1b[38;5;196mA")[0]

    assert line[0]["fg"] == "#ff0000"


def test_default_colours_clear_the_previous():
    line = ansi_runs("\x1b[38;2;1;2;3m\x1b[39mA")[0]

    assert "fg" not in line[0]


def test_non_sgr_escapes_never_leak_into_the_text():
    assert ansi_runs("a\x1b[2Kb\x1b[1;5Hc") == [[{"t": "abc"}]]


def test_carriage_returns_are_normalised():
    assert ansi_runs("a\r\nb\rc") == [[{"t": "a"}], [{"t": "bc"}]]


def test_trailing_style_does_not_create_an_empty_run():
    assert ansi_runs("\x1b[1mA\x1b[0m") == [[{"t": "A", "b": True}]]


def test_standard_ansi_foreground_and_background():
    line = ansi_runs("\x1b[33m\x1b[42mHello\x1b[0m")[0]
    assert line == [{"t": "Hello", "fg": "#cdcd00", "bg": "#00cd00"}]


def test_bright_ansi_foreground_and_background():
    line = ansi_runs("\x1b[91m\x1b[104mHello\x1b[0m")[0]
    assert line == [{"t": "Hello", "fg": "#ff0000", "bg": "#5c5cff"}]


def test_reverse_video():
    line = ansi_runs("\x1b[7mSelected\x1b[27m Normal")[0]
    assert line == [{"t": "Selected", "rev": True}, {"t": " Normal"}]
