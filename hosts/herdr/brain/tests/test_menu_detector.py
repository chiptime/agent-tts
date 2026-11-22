"""The generic menu detector: the LAST-RESORT claim for a modal no
specific grammar owns.

Every screen here is derived from the reference product's REAL capture
corpus (ANSI stripped, interior bytes untouched — see
``tests/fixtures/dialogs/provenance.md``). The claim is anchored to the
buffer tail: the footer must be the last non-blank row, and only the
keys THAT footer printed become buttons — never a digit (ADR 0009).
Anything the grammar cannot confidently read (no title under the
region top, an esc-only hint without the modal edge, a live composer
at the tail, a footer that belongs to a known family) stays RAW.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.dialogs.registry import recognize

FIXTURES = Path(__file__).parent / "fixtures" / "dialogs"

MENU_CORPUS = (
    "claude-menu-effort-slider.txt",
    "claude-menu-effort-slider-wrapped.txt",
    "claude-menu-config-panel.txt",
    "claude-menu-rewind.txt",
    "claude-menu-resume-picker.txt",
)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def labels(model):
    return [option.label for option in model.options]


def recipes(model):
    return [option.keys for option in model.options]


@pytest.mark.parametrize("name", MENU_CORPUS)
def test_a_real_menu_panel_is_claimed_as_menu(name):
    model = recognize("claude", fixture(name))

    assert model is not None, f"{name} was declined"
    assert model.kind == "menu"
    # The footer named every key: never a digit (ADR 0009).
    for option in model.options:
        for key in option.keys:
            assert not key.isdigit(), f"{name} emitted digit key {key!r}"
    assert model.fingerprint


def test_every_menu_screen_has_a_text_sensitive_fingerprint():
    base = recognize("claude", fixture("claude-menu-effort-slider.txt"))
    mutated = fixture("claude-menu-effort-slider.txt").replace("Effort", "Effoft")

    fresh = recognize("claude", mutated)

    assert base.fingerprint
    assert fresh is not None
    assert fresh.fingerprint != base.fingerprint


def test_the_effort_slider_names_only_its_footer_keys():
    model = recognize("claude", fixture("claude-menu-effort-slider.txt"))

    # "s for this session only" and "←/→ to adjust" name keys the guarded
    # dialect refused or the arrow grammar skipped; the honest claim
    # keeps exactly the hints that map.
    assert labels(model) == ["Confirm", "Cancel"]
    assert recipes(model) == [("enter",), ("esc",)]
    assert model.hint == (
        "←/→ to adjust · Enter to confirm · s for this session only · Esc to cancel"
    )


def test_the_wrapped_effort_footer_joins_before_parsing():
    # At 40 columns the terminal wrapped the footer onto two rows; the
    # group must be joined FIRST, or the hints above the wrap are lost.
    model = recognize("claude", fixture("claude-menu-effort-slider-wrapped.txt"))

    assert model is not None
    assert model.hint == (
        "←/→ to adjust · Enter to confirm · s for this session only · Esc to cancel"
    )
    assert recipes(model) == [("enter",), ("esc",)]


def test_the_config_panel_keeps_only_the_sendable_hints():
    model = recognize("claude", fixture("claude-menu-config-panel.txt"))

    # "Enter/↓ to select" names a COMPOUND key no dialect carries and
    # "Type to filter" is no key hint: those segments are skipped, the
    # rest stays honest.
    assert model.kind == "menu"
    assert labels(model) == ["Tabs", "Clear"]
    assert recipes(model) == [("up",), ("esc",)]


def test_the_resume_picker_offers_only_its_safe_keys():
    model = recognize("claude", fixture("claude-menu-resume-picker.txt"))

    # The capture's footer names ctrl+a (archive, destructive), ctrl+b,
    # ctrl+r, space (preview) and "Type to search": NOT ONE of them is a
    # guarded dialog recipe key, so the ONLY button is the way out.
    assert model.kind == "menu"
    assert labels(model) == ["Cancel"]
    assert recipes(model) == [("esc",)]


def test_an_esc_only_footer_without_the_modal_edge_stays_raw():
    # The /status screen ends in ONE hint and has no ▔ edge: a single
    # "Esc to cancel" is too little to claim a screen.
    assert recognize("claude", fixture("claude-menu-status-screen.txt")) is None


def test_a_live_composer_never_becomes_a_menu():
    # A prompt screen whose statusline hint row runs to the tail — the
    # input box's rule / ❯ / rule sandwich sits right under it.
    assert recognize("claude", fixture("claude-live-composer.txt")) is None


def test_a_neutral_buffer_still_declines():
    assert recognize("claude", fixture("neutral-terminal.txt")) is None


def test_the_specific_families_still_win_over_the_last_resort():
    # The /model list and every other claimed family keep their own
    # grammar: the generic menu never steals a claimed screen.
    model = recognize("claude", fixture("claude-model-picker.txt"))
    assert model.kind == "model_picker"


@pytest.mark.parametrize(
    "name",
    (
        "muse-multi.txt",
        "muse-notes-open.txt",
        "kiro-approval.txt",
        "kiro-trust.txt",
        "pi-ask.txt",
        "antigravity-select.txt",
        "codex-ask.txt",
        "grok-ask.txt",
    ),
)
def test_a_foreign_harness_footer_is_never_a_claude_menu(name):
    # Cross-harness ·-separated hint rows belong to their own adapters:
    # the last-resort claim dies when the region is not claude's shape.
    assert (
        recognize("claude", fixture(name)) is None
    ), f"claude claimed the foreign screen {name}"


class TestGuardBlocksADriftedMenu:
    """S2: options are driven only via the existing guard — a strict
    signature re-read before every commit."""

    def _guard(self):
        from herdr_brain import dialog as dialog_guard

        return dialog_guard

    def test_a_fresh_read_passes_and_a_drift_is_refused(self):
        dialog_guard = self._guard()
        screen = fixture("claude-menu-effort-slider.txt")

        def detect(text):
            model = recognize("claude", text or "")
            if model is None or model.unread:
                return None
            return model.as_guard_model()

        tapped = detect(screen)
        assert tapped is not None

        # Same bytes: the strict signature check passes.
        guard = dialog_guard.entry_guard(
            lambda _pane, _lines: screen, "pane", 40, tapped, detect
        )
        assert guard.ok

        drifted = screen.replace("Esc to cancel", "Esc to cancell")
        guard = dialog_guard.entry_guard(
            lambda _pane, _lines: drifted, "pane", 40, tapped, detect
        )
        assert not guard.ok
        assert guard.reason == "changed"


class TestModelOnlyFacts:
    """Q2: the leftRight affordance rides the model, never a button."""

    def test_a_body_leftRight_row_is_carried_without_buttons(self):
        # The row is the REAL body row Claude's v2300 /model picker
        # paints (ref menu.test.ts: «the ◐ Medium effort ←/→ to adjust
        # row names Left/Right AND carries the value they act on»);
        # the shape here is the minimal region that reaches the scan.
        screen = "\n".join(
            [
                "▔" * 40,
                "Select model",
                "◐ Medium effort ←/→ to adjust",
                "  Enter to apply · Esc to cancel",
            ]
        )

        model = recognize("claude", screen)

        assert model is not None and model.kind == "menu"
        payload = model.payload()
        assert payload["adjust"] == {
            "verb": "adjust",
            "label": "◐ Medium effort",
        }  # no "values": the printed scale stays the dedicated effort
        # grammar's read (report gap for a later unit)
        # The adjust affordance NEVER becomes a button.
        assert recipes(model) == [("enter",), ("esc",)]
        assert labels(model) == ["Apply", "Cancel"]

    def test_menu_titles_are_the_screens_own_headings(self):
        expected = {
            "claude-menu-effort-slider.txt": "Effort",
            "claude-menu-config-panel.txt": "Settings  Status   Config   Usage   Stats",
            "claude-menu-rewind.txt": "Rewind",
            "claude-menu-resume-picker.txt": "Resume session",
        }
        for name, title in expected.items():
            model = recognize("claude", fixture(name))
            assert model is not None
            assert model.question == title, name
