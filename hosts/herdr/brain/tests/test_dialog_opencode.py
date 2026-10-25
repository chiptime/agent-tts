"""The opencode permission dialog, detected from a REAL captured screen.

The fixture is a live capture (`herdr agent read --ansi`) of the dialog an
opencode agent raised when it tried to read a file outside its directory, so
the escape codes — and therefore the highlight — are the ones the TUI really
emits, not a reconstruction.

Detection is fail-closed on purpose: without styling there is no highlight to
see, and a dialog whose selected option cannot be read has no signature, so
the guard could not tell a fresh render from a stale one. Refusing is the
only honest answer there.
"""

from __future__ import annotations

import re
from pathlib import Path

from herdr_brain.dialog import DialogModel, same_dialog_identity
from herdr_brain.dialog_opencode import detect_dialog_from_text

FIXTURE = Path(__file__).parent / "fixtures" / "opencode-permission-dialog.ansi"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def captured() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_detects_the_three_options_the_dialog_offers():
    model = detect_dialog_from_text(captured())

    assert model is not None
    assert [action.label for action in model.actions] == [
        "Allow once",
        "Allow always",
        "Reject",
    ]


def test_reads_which_option_is_selected_from_the_styling():
    """The amber pill is the ONLY place the selection exists."""
    model = detect_dialog_from_text(captured())

    assert model is not None
    assert model.selected == 0  # "Allow once" carries the highlight at rest


def test_takes_the_keys_from_the_screens_own_footer():
    """`⇆ select   enter confirm`: never synthesised, never assumed."""
    model = detect_dialog_from_text(captured())

    assert model is not None
    assert model.nav_keys == ("left", "right")
    assert model.commit_keys == ("enter",)


def test_keeps_the_dialog_title_for_the_phone():
    model = detect_dialog_from_text(captured())

    assert model is not None
    assert "Permission required" in model.title


def test_the_signature_moves_when_the_highlight_moves():
    """A moved highlight MUST change the signature — it is the load-bearing
    check — while the identity deliberately stays the same, because that is
    exactly what the arrow that moved it produced."""
    moved = (
        captured()
        # the pill's own background, gone EVERYWHERE it marked the selection
        # (its padding runs included, or the old pill would still be there)…
        .replace("48;2;245;167;66", "48;2;30;30;30")
        # …and now marking a different option.
        .replace("48;2;30;30;30mReject", "48;2;245;167;66mReject")
    )
    original = detect_dialog_from_text(captured())
    shifted = detect_dialog_from_text(moved)

    assert original is not None and shifted is not None
    assert shifted.selected == 2
    assert shifted.signature != original.signature
    assert same_dialog_identity(original, shifted) is True


def test_an_ordinary_screen_is_not_a_dialog():
    assert detect_dialog_from_text("$ ls -la\ntotal 0\ndrwxr-xr-x 2\n") is None


def test_the_plain_text_screen_is_refused_not_guessed():
    """Stripping the escape codes destroys the highlight: the detector must
    refuse rather than return a model whose signature cannot be trusted."""
    plain = ANSI.sub("", captured())

    assert "Allow once" in plain  # the labels are there...
    assert detect_dialog_from_text(plain) is None  # ...and it still refuses


def test_a_styled_screen_without_the_footer_is_not_a_dialog():
    """The keys come from the footer; no footer, no keys, no model."""
    without_footer = re.sub(r"select|confirm|⇆ ", "", captured())

    assert detect_dialog_from_text(without_footer) is None


def test_returns_a_dialog_model_the_guard_can_compare():
    model = detect_dialog_from_text(captured())

    assert isinstance(model, DialogModel)
    assert model.signature
