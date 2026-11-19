"""Per-harness dialog recognition: structured models, recipes, fail-closed.

Every fixture under ``tests/fixtures/dialogs`` is a SYNTHESIZED screen in
the shape its harness draws (numbered rows, pointers, footers), so the
recognizer is pinned against the layout facts and nothing else. The
conformance rules here are the contract of S2:

- a registered adapter reads ITS corpus into the neutral structured
  model (kind, question, options with the exact key recipe);
- the SAME adapter declines every OTHER harness's corpus and a neutral
  terminal buffer (fail-closed: ``None``, never a guess);
- every fingerprint is non-empty and changes when the region's text
  changes;
- an adapter that knows its harness's dialog but cannot read it safely
  (a notes field owns the keyboard, a parked card swallows digits)
  answers with the explicit unread state, not with buttons.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "dialogs"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# Every (adapter, fixture) pair the corpus pins. Anything else must be
# declined by that adapter.
CORPUS = {
    "claude": (
        "claude-select.txt",
        "claude-select-freetext.txt",
        "claude-permission.txt",
        "claude-trust.txt",
        "claude-wizard.txt",
        "claude-wizard-2of2.txt",
        "claude-wizard-multi.txt",
        "claude-multi.txt",
        # parity-f7d S2: the /model picker list and the "Switch model?"
        # confirmation, both derived from real reference captures (see
        # fixtures/dialogs/provenance.md).
        "claude-model-picker.txt",
        "claude-switch-model-yes.txt",
    ),
    "codex": ("codex-trust.txt", "codex-approval.txt", "codex-ask.txt"),
    "grok": ("grok-ask.txt", "grok-permission.txt", "grok-model-picker.txt"),
    "pi": ("pi-ask.txt", "omp-switch-model.txt"),
    "opencode": (
        "opencode-question.txt",
        "opencode-multi.txt",
        # the /models picker, LIVE capture (opencode 1.19.5, see
        # fixtures/dialogs/provenance.md).
        "opencode-models-picker.txt",
    ),
    "opencode2": (
        "opencode2-question.txt",
        "opencode2-multi.txt",
        "opencode-models-picker.txt",
    ),
    "antigravity": ("antigravity-select.txt",),
    "kiro": ("kiro-approval.txt", "kiro-trust.txt"),
    "muse": (
        "muse-approval.txt",
        "muse-select.txt",
        "muse-multi.txt",
        "muse-trust.txt",
    ),
}

# (adapter, fixture) pairs that must answer UNREAD: the harness's own
# dialog, in a state whose keyboard a button cannot be trusted with.
UNREAD_CORPUS = {
    "codex": ("codex-ask-notes.txt",),
    "grok": ("grok-ask-parked.txt",),
    "pi": ("pi-ask-multi.txt",),
    "muse": ("muse-notes-open.txt",),
}

# The Cursor CLI replaces its input box with dialogs nobody has a
# verified recipe for: its adapter claims nothing (the PWA keeps the
# manual keypad) rather than guessing keys.
CURSOR_SCREEN = "cursor-approval.txt"


def detect(agent: str, screen: str):
    from herdr_brain.dialogs import recognize

    return recognize(agent, screen)


def labels(model):
    return [option.label for option in model.options]


def recipes(model):
    return [tuple(option.keys) for option in model.options]


class TestRegistry:
    def test_every_harness_resolves_by_exact_name(self):
        from herdr_brain.dialogs import registered_agents
        from herdr_brain.dialogs.registry import adapter_for

        expected = {
            "antigravity", "claude", "codex", "cursor", "grok", "kiro",
            "muse", "opencode", "opencode2", "pi",
        }
        assert set(registered_agents()) == expected
        for agent in expected:
            assert adapter_for(agent) is not None

    def test_aliases_resolve_to_the_canonical_adapter(self):
        from herdr_brain.dialogs.registry import adapter_for

        assert adapter_for("omp") is adapter_for("pi")
        assert adapter_for("agy") is adapter_for("antigravity")

    def test_an_unknown_agent_resolves_to_nothing(self):
        from herdr_brain.dialogs.registry import adapter_for

        assert adapter_for("claude-code") is None
        assert adapter_for("") is None
        assert adapter_for(None) is None


class TestClaude:
    def test_select_reads_options_with_digit_then_enter(self):
        model = detect("claude", fixture("claude-select.txt"))

        assert model.kind == "select"
        assert "Which fruit" in model.question
        assert labels(model) == ["Manzana", "Pera", "Naranja"]
        assert recipes(model) == [("1", "enter"), ("2", "enter"), ("3", "enter")]
        assert model.step == ""
        assert model.selected == 0

    def test_permission_reads_digit_alone(self):
        model = detect("claude", fixture("claude-permission.txt"))

        assert model.kind == "permission"
        assert recipes(model) == [("1",), ("2",), ("3",)]
        assert "proceed" in model.question

    def test_trust_reads_digit_alone_as_confirm(self):
        model = detect("claude", fixture("claude-trust.txt"))

        assert model.kind == "confirm"
        assert labels(model) == ["Yes, proceed", "No, exit"]
        assert recipes(model) == [("1",), ("2",)]

    def test_wizard_step_carries_the_step_marker(self):
        model = detect("claude", fixture("claude-wizard.txt"))

        assert model.kind == "wizard_step"
        assert model.step == "1/2"
        assert recipes(model) == [("1", "enter"), ("2", "enter")]

    def test_multi_select_reads_checkbox_states_and_a_walk_class_submit_row(
        self,
    ):
        model = detect("claude", fixture("claude-multi.txt"))

        assert model.kind == "multiselect"
        assert "Which toppings" in model.question
        assert labels(model) == ["Cheese", "Pepperoni", "Mushrooms", "Submit"]
        assert [option.checked for option in model.options] == [
            False, True, False, None,
        ]
        # The digit toggles its box without submitting; the submit row
        # is walk-class: the pointer walks there with the footer's own
        # arrows and Enter commits.
        assert recipes(model) == [("1",), ("2",), ("3",), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0

    def test_multi_select_fingerprint_ignores_its_own_pointer_and_boxes(self):
        """The answer choreography flips boxes and moves the pointer ON
        PURPOSE: the fingerprint must not falsify the race guard with
        the screen's own movements (state transitions are verified by
        re-reading, not by the fingerprint)."""
        screen = fixture("claude-multi.txt")
        model = detect("claude", screen)
        moved = screen.replace("❯ 1. Empty checkbox", "  1. Filled checkbox")

        other = detect("claude", moved)

        assert other is not None
        assert other.fingerprint == model.fingerprint
        assert [option.checked for option in other.options] == [
            True, True, False, None,
        ]

    def test_a_multiselect_footer_without_checkbox_rows_declines(self):
        screen = fixture("claude-multi.txt")
        stripped = screen.replace("Empty checkbox ", "").replace(
            "Filled checkbox ", ""
        )

        assert detect("claude", stripped) is None

    def test_checkbox_rows_without_a_submit_row_decline(self):
        """Without the submit row there is no verified way to commit the
        selection: fail closed rather than guessing Enter's meaning."""
        screen = fixture("claude-multi.txt").replace(
            "4. Submit", "4. Filled checkbox Confirm"
        )

        assert detect("claude", screen) is None

    def test_word_glyphs_behind_a_wide_gap_still_read_as_one_label(self):
        screen = fixture("claude-multi.txt").replace(
            "1. Empty checkbox Cheese", "1. Empty checkbox  Cheese"
        )

        model = detect("claude", screen)

        assert model is not None
        assert model.options[0].label == "Cheese"


class TestClaudeFreeText:
    """S3: a ``Type something.`` row is an OPTION that opens an editor,
    not a row to drop. Its digit FOCUSES the field — Enter would commit
    before any text is typed — so its recipe is the digit alone."""

    def test_a_free_text_row_is_kept_as_a_free_text_option(self):
        model = detect("claude", fixture("claude-select-freetext.txt"))

        assert model is not None and not model.unread
        assert model.kind == "select"
        assert labels(model) == ["Manzana", "Pera", "Type something."]
        assert [option.free_text for option in model.options] == [
            False, False, True,
        ]
        # The digit alone: an Enter in the recipe would submit the row
        # before the server's text recipe could type into it.
        assert model.options[2].keys == ("3",)
        # The payload names the row for the PWA's editor, additively:
        # rows without the field keep their exact previous shape.
        payload = model.payload()
        assert payload["options"][2]["free_text"] is True
        assert "free_text" not in payload["options"][0]

    def test_fixtures_without_a_free_text_row_change_nothing(self):
        model = detect("claude", fixture("claude-select.txt"))

        assert labels(model) == ["Manzana", "Pera", "Naranja"]
        assert [option.free_text for option in model.options] == [
            False, False, False,
        ]
        assert "free_text" not in model.payload()["options"][0]

    def test_a_neutral_buffer_still_declines(self):
        assert detect("claude", fixture("neutral-terminal.txt")) is None

    def test_the_free_text_row_stays_out_of_the_multiselect_shape(self):
        """The multiselect grammar is checkbox rows plus one trailing
        submit row; whether Claude draws the free-text row there (and
        with which glyph) is pinned by no real capture, so S3 keeps the
        closed shape: a free-text row inside it still declines."""
        screen = fixture("claude-multi.txt").replace(
            "│   4. Submit",
            "│   4. Type something.\n│   5. Submit",
        )

        assert detect("claude", screen) is None


class TestWizardStepMetadata:
    """S4: the ``N/M`` marker a wizard step already carries becomes the
    step metadata (current + total) the PWA stepper orchestrates over.

    Strictly additive and strictly parsed: only a well-formed ``N/M``
    (1-based, current ≤ total) yields ``step``/``steps``; anything else
    keeps today's exact payload — no ``wizard`` key, the marker string
    untouched — and the PWA treats the dialog as a plain one."""

    def test_step_one_of_two_parses_current_and_total(self):
        model = detect("claude", fixture("claude-wizard.txt"))

        assert model.kind == "wizard_step"
        assert model.step == "1/2"
        assert model.step_number == 1
        assert model.step_total == 2
        payload = model.payload()
        assert payload["wizard"] == {"step": 1, "steps": 2}
        # The marker string keeps today's shape for every wizard dialog.
        assert payload["step"] == "1/2"

    def test_step_two_of_two_parses_the_same_way(self):
        model = detect("claude", fixture("claude-wizard-2of2.txt"))

        assert model.kind == "wizard_step"
        assert model.step == "2/2"
        assert model.step_number == 2
        assert model.step_total == 2
        assert model.payload()["wizard"] == {"step": 2, "steps": 2}

    def test_a_multiselect_step_carries_marker_and_metadata(self):
        """A multi-select wizard step keeps its answering kind and gains
        the same metadata — the PWA's stepper drives it with the
        existing ``choices`` path."""
        model = detect("claude", fixture("claude-wizard-multi.txt"))

        assert model.kind == "multiselect"
        assert model.step == "2/2"
        assert model.step_number == 2
        assert model.step_total == 2
        assert model.payload()["wizard"] == {"step": 2, "steps": 2}

    def test_a_plain_select_and_multiselect_keep_today_payload(self):
        for name in ("claude-select.txt", "claude-multi.txt"):
            payload = detect("claude", fixture(name)).payload()

            assert "wizard" not in payload
            assert payload["step"] == ""

    def test_an_unparsable_marker_keeps_today_payload_with_no_wizard(self):
        """Detection itself only ever yields ``N/M`` markers, so the
        fail-safe is pinned at the model: a dialog whose marker does not
        parse carries NO step metadata and the PWA renders it plain."""
        from herdr_brain.dialogs.model import StructuredDialog

        raw = StructuredDialog(
            agent="claude",
            kind="wizard_step",
            question="Q",
            options=(),
            fingerprint="f",
            step="x/y",
        )
        payload = raw.payload()

        assert "wizard" not in payload
        assert payload["step"] == "x/y"

    def test_a_nonsense_ratio_declines_metadata_but_keeps_the_dialog(self):
        screen = fixture("claude-wizard.txt").replace("1/2", "3/2")

        model = detect("claude", screen)

        assert model is not None and model.kind == "wizard_step"
        assert model.step == "3/2"
        assert model.step_number is None
        assert model.step_total is None
        assert "wizard" not in model.payload()

    def test_a_neutral_buffer_still_declines(self):
        assert detect("claude", fixture("neutral-terminal.txt")) is None


class TestCodex:
    def test_trust_reads_yes_no_with_digit_alone(self):
        model = detect("codex", fixture("codex-trust.txt"))

        assert model.kind == "confirm"
        assert labels(model) == ["Yes, continue", "No, quit"]
        assert recipes(model) == [("1",), ("2",)]

    def test_approval_offers_only_one_shot_and_reject(self):
        """The persistent "don't ask again" row is never a button: its
        digit would change the session's approval rules."""
        model = detect("codex", fixture("codex-approval.txt"))

        assert model.kind == "permission"
        assert labels(model) == [
            "Yes, proceed (y)",
            "No, and tell Codex what to do differently (esc)",
        ]
        assert recipes(model) == [("1",), ("3",)]

    def test_ask_reads_step_question_and_split_descriptions(self):
        model = detect("codex", fixture("codex-ask.txt"))

        assert model.kind == "wizard_step"
        assert model.step == "1/2"
        assert labels(model) == [
            "Tabs (Recommended)", "Spaces", "None of the above",
        ]
        assert model.options[0].description == "Indent code with tab characters."
        assert recipes(model) == [("1",), ("2",), ("3",)]

    def test_ask_with_the_notes_box_open_is_unread(self):
        model = detect("codex", fixture("codex-ask-notes.txt"))

        assert model is not None and model.unread
        assert "note" in model.detail.lower()


class TestGrok:
    def test_ask_reads_radio_options_but_not_the_free_text_row(self):
        model = detect("grok", fixture("grok-ask.txt"))

        assert model.kind == "select"
        assert labels(model) == ["Red", "Green", "Blue"]
        assert model.options[0].description == "Warm red palette"
        assert recipes(model) == [("1",), ("2",), ("3",)]

    def test_ask_parked_is_unread(self):
        """A parked card swallows bare digits: no button may fire."""
        model = detect("grok", fixture("grok-ask-parked.txt"))

        assert model is not None and model.unread

    def test_permission_offers_one_shot_yes_and_reject_only(self):
        model = detect("grok", fixture("grok-permission.txt"))

        assert model.kind == "permission"
        assert labels(model) == ["Yes", "No, reject (type to add feedback)"]
        assert recipes(model) == [("3",), ("4",)]
        assert model.selected == 0  # the ● radio row among the buttons


class TestPi:
    def test_ask_box_reads_walk_recipe_options(self):
        """pi/omp prints no digit: the recipe is the screen's own arrows
        plus Enter, walked from the pointed row."""
        model = detect("pi", fixture("pi-ask.txt"))

        assert model.kind == "select"
        assert labels(model) == ["Red", "Green", "Blue", "Other (type your own)"]
        assert recipes(model) == [(), (), (), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0

    def test_multi_select_footer_is_unread(self):
        """Enter's meaning differs across pi versions on multi-select:
        no recipe is inferred."""
        model = detect("pi", fixture("pi-ask-multi.txt"))

        assert model is not None and model.unread

    def test_the_real_stock_multi_capture_stays_unclaimed(self):
        """The REAL stock pi 1.1.0 multi-select (rule-framed, footer
        ``Space to tick • Enter to confirm``, boxes ``[ ]``/``[x]``
        before the numbers — derived from the reference product's
        capture pi--v110-multi.txt) is NOT brain's pinned Ask-box
        corpus: its only named toggle is Space, which is outside the
        verified key dialect, and its frame was never pinned. Nothing
        may claim it — the fail-closed answer, exactly as today."""
        assert detect("pi", fixture("pi-multi.txt")) is None


class TestOpencode:
    def test_question_reads_digit_alone_options(self):
        model = detect("opencode", fixture("opencode-question.txt"))

        assert model.kind == "select"
        assert labels(model) == ["Red", "Green", "Blue"]
        assert model.options[0].description == "warm"
        assert recipes(model) == [("1",), ("2",), ("3",)]

    def test_opencode2_reads_the_same_shape(self):
        model = detect("opencode2", fixture("opencode2-question.txt"))

        assert model.kind == "select"
        assert labels(model) == ["Sí, con smoke tests", "No, espera"]
        assert model.options[0].description == "lanza la batería completa primero"
        assert recipes(model) == [("1",), ("2",)]

    def test_the_pill_permission_dialog_still_reads_through_the_guard_model(self):
        """The existing ANSI pill fixture (the real capture) resolves to a
        walk-recipe structured dialog with three options."""
        real = (Path(__file__).parent / "fixtures" / "opencode-permission-dialog.ansi").read_text(
            encoding="utf-8"
        )
        model = detect("opencode", real)

        assert model is not None and not model.unread
        assert model.kind == "permission"
        assert labels(model) == ["Allow once", "Allow always", "Reject"]
        assert model.nav_keys == ("left", "right")
        assert model.commit_keys == ("enter",)


class TestOpencodeMulti:
    """S2: the tabbed multi-select list, pinned by the reference
    product's REAL captures (opencode 1.18.33 measured keystroke by
    keystroke: a digit TOGGLES its own box; Space does nothing; the
    commit is the Confirm tab's own ``tab`` then ``enter``).

    ``opencode-multi.txt`` derives from the reference capture
    ``oc--question--multi--toggled.txt`` (page gutter trimmed to the
    corpus convention); ``opencode2-multi.txt`` from
    ``oc--v211-question--multi--toggled.txt`` (opencode 2.1.1: a
    ``Questions`` title, a ``Submit`` last chip, deeper descriptions).
    """

    def test_multi_reads_box_states_and_digit_toggle_recipes(self):
        model = detect("opencode", fixture("opencode-multi.txt"))

        assert model.kind == "multiselect"
        assert "Which colours?" in model.question
        # The free-text row's digit opens an input: never an option.
        assert labels(model) == ["Red", "Green", "Blue", "Yellow"]
        assert [o.description for o in model.options] == [
            "warm", "calm", "cool", "bright",
        ]
        assert [o.checked for o in model.options] == [True, False, False, False]
        # Measured: the digit alone toggles ITS OWN box, pointer or not.
        assert recipes(model) == [("1",), ("2",), ("3",), ("4",)]
        # No submit row exists on this screen and none is synthesized:
        # the commit (tab to Confirm, enter there) is not a row recipe.
        assert [o.checked for o in model.options].count(None) == 0
        assert model.nav_keys == () and model.commit_keys == ()
        # The tab strip rides along additively (T4b): the chips the
        # commit choreography derives `tab`+`enter` from.
        assert model.tab_strip == ("Colour", "Confirm")

    def test_opencode2_multi_with_the_submit_chip_reads_the_same(self):
        model = detect("opencode2", fixture("opencode2-multi.txt"))

        assert model.kind == "multiselect"
        assert model.question == "Which toppings?"
        assert labels(model) == ["Ham", "Olives", "Cheese", "Mushroom"]
        assert [o.checked for o in model.options] == [True, False, False, False]
        assert recipes(model) == [("1",), ("2",), ("3",), ("4",)]
        assert model.tab_strip == ("Toppings", "Submit")

    def test_the_confirm_review_stays_unclaimed_and_reads_its_chips(self):
        """The Confirm tab's own screen (footer ``⇆ tab  enter submit
        esc dismiss``) is not an answerable list: no recognizer claims
        it. The commit choreography verifies it through the adapter's
        review reader, which returns the tab strip it saw — the same
        chips the list carried, the identity of the landing."""
        screen = fixture("opencode-multi-confirm.txt")

        model = detect("opencode", screen)

        assert model is None
        from herdr_brain.dialogs.adapters.opencode import confirm_review

        assert confirm_review(screen.splitlines()) == ["Colour", "Confirm"]

    def test_multi_fingerprint_ignores_its_own_box_flips(self):
        """The answer choreography flips boxes ON PURPOSE: the digest is
        taken over the region with the box glyphs normalized, so the
        screen's own toggles never falsify the race guard."""
        screen = fixture("opencode-multi.txt")
        model = detect("opencode", screen)
        flipped = screen.replace("1. [✓] Red", "1. [ ] Red").replace(
            "2. [ ] Green", "2. [✓] Green"
        )

        other = detect("opencode", flipped)

        assert other is not None and not other.unread
        assert other.fingerprint == model.fingerprint
        assert [o.checked for o in other.options] == [False, True, False, False]

    def test_rows_without_boxes_under_the_multi_footer_stay_unread(self):
        screen = fixture("opencode-multi.txt").replace("[✓] ", "").replace(
            "[ ] ", ""
        )

        model = detect("opencode", screen)

        assert model is not None and model.unread

    def test_broken_numbering_under_the_multi_footer_stays_unread(self):
        screen = fixture("opencode-multi.txt").replace(
            "3. [ ] Blue", "4. [ ] Blue"
        )

        model = detect("opencode", screen)

        assert model is not None and model.unread

    def test_a_free_text_row_above_the_last_row_stays_unread(self):
        """Only the LAST numbered row may be the free-text row: a digit
        on any other row must be a box toggle, and two rows whose digit
        opens inputs cannot be told apart."""
        screen = fixture("opencode-multi.txt").replace(
            "4. [ ] Yellow", "4. [ ] Type your own answer"
        )

        model = detect("opencode", screen)

        assert model is not None and model.unread

    def test_no_tab_strip_above_the_question_stays_unread(self):
        """The tab strip (question chips ending Confirm/Submit) is part
        of the pinned frame: without it the screen is another version's
        shape, and it stays unread."""
        screen = fixture("opencode-multi.txt").replace(
            "┃   Colour   Confirm", "┃   Colour   Finish"
        )

        model = detect("opencode", screen)

        assert model is not None and model.unread

    def test_a_foreign_multi_footer_verb_stays_unread(self):
        """The open-input footer of another generation (``enter done``
        instead of ``enter toggle``) never upgrades: only the exactly
        pinned four-hint footer does."""
        screen = fixture("opencode-multi.txt").replace(
            "enter toggle  esc dismiss", "enter done  esc close"
        )

        model = detect("opencode", screen)

        assert model is None or model.unread


class TestAntigravity:
    def test_pointed_list_reads_a_walk_recipe(self):
        """No digit behavior is verified for this harness: the pointer
        walk the footer names is the only recipe offered."""
        model = detect("antigravity", fixture("antigravity-select.txt"))

        assert model.kind == "select"
        assert labels(model) == ["Staging", "Production"]
        assert recipes(model) == [(), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0


class TestKiro:
    def test_approval_panel_walks_allow_and_deny_only(self):
        """Always allow / Always deny persist trust rules: only the
        one-shot rows become options."""
        model = detect("kiro", fixture("kiro-approval.txt"))

        assert model.kind == "permission"
        assert labels(model) == ["Allow", "Deny"]
        assert recipes(model) == [(), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0

    def test_workspace_trust_walks_both_rows(self):
        model = detect("kiro", fixture("kiro-trust.txt"))

        assert model.kind == "confirm"
        assert labels(model) == ["Trust, continue", "Don't trust, exit"]
        assert model.nav_keys == ("up", "down")
        assert model.selected == 0


class TestMuse:
    def test_command_approval_reads_digit_alone(self):
        model = detect("muse", fixture("muse-approval.txt"))

        assert model.kind == "permission"
        assert labels(model) == [
            "Allow this stage once (y)",
            "Abort the entire command (esc)",
        ]
        assert recipes(model) == [("1",), ("3",)]

    def test_single_select_reads_digit_then_enter(self):
        model = detect("muse", fixture("muse-select.txt"))

        assert model.kind == "select"
        assert labels(model) == ["Red (Recommended)", "Green", "Blue", "None of the above"]
        assert recipes(model) == [
            ("1", "enter"), ("2", "enter"), ("3", "enter"), ("4", "enter"),
        ]
        assert model.selected == 0

    def test_multi_select_reads_toggle_states_and_the_submit_row(self):
        model = detect("muse", fixture("muse-multi.txt"))

        assert model.kind == "multiselect"
        assert labels(model) == [
            "Cheese (Recommended)", "Pepperoni", "Mushrooms",
            "None of the above", "Submit answer (1 checked)",
        ]
        assert [option.checked for option in model.options] == [
            False, True, False, False, None,
        ]
        # Toggle = digit jumps the pointer, Enter flips the box; the
        # submit row advances with its own digit + Enter.
        assert recipes(model) == [
            ("1", "enter"), ("2", "enter"), ("3", "enter"),
            ("4", "enter"), ("5", "enter"),
        ]

    def test_trust_reads_digit_alone(self):
        model = detect("muse", fixture("muse-trust.txt"))

        assert model.kind == "confirm"
        assert labels(model) == ["Trust and continue", "Quit"]
        assert recipes(model) == [("1",), ("2",)]

    def test_open_notes_row_is_unread(self):
        """While the note row is open it owns the keyboard: every digit
        becomes text, so no button may fire."""
        model = detect("muse", fixture("muse-notes-open.txt"))

        assert model is not None and model.unread


class TestCursor:
    def test_cursor_dialogs_are_never_claimed(self):
        """The Cursor CLI replaces its input box with dialogs whose keys
        nobody has verified here: the recognizer claims NOTHING (the
        unknown-dialog card keeps the keypad) rather than guessing."""
        model = detect("cursor", fixture(CURSOR_SCREEN))

        assert model is None


class TestModelPickerKind:
    """parity-f7d S2: the ``model_picker`` kind — the /model selectors.

    A model picker is walk-class ALWAYS: the rows may be numbered, but a
    digit CONFIRMS and rewrites the default model (ADR 0009), so no
    option ever carries a key recipe. The only recipe is the screen's
    own arrows plus Enter, walked from the pointer with a re-read after
    every key.
    """

    def test_the_kind_is_registered(self):
        from herdr_brain.dialogs.model import KINDS

        assert "model_picker" in KINDS

    def test_the_payload_rides_the_walk_recipe(self):
        model = detect("claude", fixture("claude-model-picker.txt"))

        assert model.payload()["recipe"] == "walk"


class TestClaudeModelPicker:
    def test_the_model_list_yields_model_picker(self):
        model = detect("claude", fixture("claude-model-picker.txt"))

        assert model.kind == "model_picker"
        assert labels(model) == [
            "Default (recommended) ✔",
            "Opus (1M context)",
            "Fable",
            "Sonnet",
            "Haiku",
        ]
        # NEVER a digit: the rows are numbered, but a digit confirms and
        # rewrites the default model (ADR 0009).
        assert recipes(model) == [(), (), (), (), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0

    def test_the_pointer_row_selects_the_selection(self):
        moved = detect("claude", fixture("claude-model-picker-moved.txt"))

        assert moved.selected == 2
        assert moved.fingerprint != detect(
            "claude", fixture("claude-model-picker.txt")
        ).fingerprint

    def test_a_foreign_footer_word_declines(self):
        screen = fixture("claude-model-picker.txt").replace(
            "s to use this session only", "s for this chat only"
        )

        assert detect("claude", screen) is None

    def test_the_switch_model_confirmation_is_a_model_picker(self):
        model = detect("claude", fixture("claude-switch-model-yes.txt"))

        assert model.kind == "model_picker"
        assert labels(model) == ["Yes, switch to Fable 5.1", "No, go back"]
        assert recipes(model) == [(), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0

    def test_the_confirmation_pointer_on_row_two(self):
        model = detect("claude", fixture("claude-switch-model-no.txt"))

        assert model.selected == 1

    def test_the_confirmation_without_the_modal_edge_declines(self):
        screen = fixture("claude-switch-model-yes.txt").replace("▔", "─")

        assert detect("claude", screen) is None


class TestGrokModelPicker:
    def test_the_completion_list_yields_model_picker(self):
        model = detect("grok", fixture("grok-model-picker.txt"))

        assert model.kind == "model_picker"
        assert labels(model) == [
            "Grok 4.7 (current)",
            "Grok 4.7 Fast",
            "Grok 4.6",
            "Grok 4.5",
        ]
        assert recipes(model) == [(), (), (), ()]
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        assert model.selected == 0

    def test_the_moved_pointer_row_selects_the_selection(self):
        model = detect("grok", fixture("grok-model-picker-moved.txt"))

        assert model.selected == 1

    def test_a_row_count_that_disagrees_with_the_rule_declines(self):
        screen = fixture("grok-model-picker.txt").replace("────4─", "────9─")

        assert detect("grok", screen) is None

    def test_a_hint_row_without_enter_send_declines(self):
        screen = fixture("grok-model-picker.txt").replace(
            "Enter:send", "Enter:open"
        )

        assert detect("grok", screen) is None


class TestOmpSwitchModel:
    """The compact ``/switch`` box omp draws (pi-tui), pinned by the real
    18.4 capture. Reaches the pi adapter through the ``omp`` alias."""

    def test_the_switch_box_yields_model_picker(self):
        model = detect("omp", fixture("omp-switch-model.txt"))

        assert model.kind == "model_picker"
        assert labels(model)[:4] == [
            "openrouter/google/gemini-3.8-flash",
            "openai/gpt-5.6-sol",
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-opus-5-5",
        ]
        assert recipes(model) == [()] * len(model.options)
        assert model.nav_keys == ("up", "down")
        assert model.commit_keys == ("enter",)
        # The pointer sits on the CURRENT model's row (anthropic/claude-
        # opus-5-5, fourth model row).
        assert model.selected == 3

    def test_the_pi_agent_reads_the_same_box(self):
        assert detect("pi", fixture("omp-switch-model.txt")) is not None

    def test_a_footer_word_changed_declines(self):
        screen = fixture("omp-switch-model.txt").replace(
            "type to search", "type to filter"
        )

        assert detect("omp", screen) is None

    def test_a_truncated_row_declines_the_whole_screen(self):
        """A row omp shortened with ``…`` has no full id on screen: the
        walk could not verify where it lands, so the box is not
        claimed."""
        screen = fixture("omp-switch-model.txt").replace(
            "anthropic/claude-sonnet-5-5", "anthropic/claude-sonnet-5-…"
        )

        assert detect("omp", screen) is None


class TestOpencodeModelsPicker:
    """The opencode ``/models`` picker — a LIVE capture (1.19.5, real
    pane, 2026-11-19; provenance.md). The modal floats over the TUI:
    the surrounding screen's text bleeds at both margins, the ``●``
    prefix marks the CURRENT model, and the pointer is ANSI paint —
    invisible in text, so no ``selected`` is ever claimed and the
    recipe is SEARCH-FIRST (typing into the picker's own Search box),
    never an arrow walk that could not be verified. Still never a
    digit key (ADR 0009)."""

    def test_the_models_picker_yields_model_picker(self):
        model = detect("opencode", fixture("opencode-models-picker.txt"))

        assert model.kind == "model_picker"
        assert labels(model) == [
            "GLM-5.3-Flash",
            "Qwen3.8 Flash",
            "GLM-5.3",
            "Muse Spark 1.2 Contributor",
            "Hy3",
            "LongCat-2.0",
            "GLM-5.2",
            "Kimi K2.7 Code",
            "Qwen3.7 Plus",
            "MiniMax-M3",
            "DeepSeek V4 Pro",
            "MiMo V2.5",
            "MiMo V2.5 Pro",
            # A provider group header is plain text in this TUI —
            # indistinguishable from a model row once escapes are
            # stripped, so it stays a row (the search-first verifier
            # and the name matcher keep it honest).
            "Z.AI Coding Plan",
            "GLM-5.3-Flash",
            "GLM-5.3 Highspeed",
        ]
        # Never a digit key, and no walk either: the pointer is paint.
        assert recipes(model) == [()] * len(model.options)
        assert model.nav_keys == ()
        assert model.commit_keys == ("enter",)
        assert model.selected is None

    def test_the_current_model_row_is_marked(self):
        model = detect("opencode", fixture("opencode-models-picker.txt"))

        current = [
            (index, option.label)
            for index, option in enumerate(model.options)
            if option.current
        ]
        # The ● row: the default model, duplicated as the first row of
        # its provider group. Exactly one row carries the mark.
        assert current == [(14, "GLM-5.3-Flash")]

    def test_the_payload_marks_the_current_row_additively(self):
        payload = detect(
            "opencode", fixture("opencode-models-picker.txt")
        ).payload()

        assert payload["recipe"] == "walk"
        marked = [
            option for option in payload["options"] if option.get("current")
        ]
        assert [option["label"] for option in marked] == ["GLM-5.3-Flash"]

    def test_the_opencode2_agent_reads_the_same_picker(self):
        model = detect("opencode2", fixture("opencode-models-picker.txt"))

        assert model.kind == "model_picker"
        assert model.agent == "opencode2"

    def test_a_footer_word_changed_declines(self):
        screen = fixture("opencode-models-picker.txt").replace(
            "Favorite", "Starred"
        )

        assert detect("opencode", screen) is None

    def test_a_renamed_title_declines(self):
        screen = fixture("opencode-models-picker.txt").replace(
            "Select model", "Pick a model", 1
        )

        assert detect("opencode", screen) is None

    def test_a_renamed_search_row_declines(self):
        screen = fixture("opencode-models-picker.txt").replace(
            "Search", "Filter", 1
        )

        assert detect("opencode", screen) is None

    def test_two_current_rows_decline(self):
        fixture_text = fixture("opencode-models-picker.txt")
        column = fixture_text.split("\n")[0].index("Select model")
        row = " " * column + "GLM-5.3 Highspeed"
        screen = fixture_text.replace(
            row, " " * (column - 2) + "● GLM-5.3 Highspeed"
        )

        assert detect("opencode", screen) is None

    def test_the_filled_search_box_still_reads_with_the_query(self):
        """After the choreography types into Search, the placeholder is
        gone: the strict adapter declines, but the filled-search read
        the choreography itself uses still recognizes the picker —
        only when the search row carries the typed query."""
        from herdr_brain.dialogs.adapters.opencode import detect_models_picker

        screen = fixture("opencode-models-picker.txt")
        filled = screen.replace("Search", "GLM-5.3 Highspeed", 1)

        assert detect("opencode", filled) is None
        lines = filled.splitlines()
        assert detect_models_picker("opencode", lines, filled_search=True) is not None
        assert (
            detect_models_picker(
                "opencode", lines, filled_search=True, query="GLM-5.3 Highspeed"
            ).kind
            == "model_picker"
        )
        # A query the search box does NOT echo is not our filter.
        assert (
            detect_models_picker(
                "opencode", lines, filled_search=True, query="Kimi"
            )
            is None
        )


class TestConformance:
    """The fail-closed gate: one adapter, every OTHER harness's corpus,
    plus a neutral terminal buffer — all must decline.

    Two documented family overlaps are exempt because the widgets ARE
    the same shape (identical footer phrases measured on both sides):

    - claude ↔ antigravity: the pointed numbered list with an
      ``enter select … esc cancel`` footer;
    - opencode ↔ opencode2: the same question bar run across the two
      storage generations.
    """

    FAMILY_OVERLAP = (frozenset({"claude", "antigravity"}),
                      frozenset({"opencode", "opencode2"}))

    @classmethod
    def _overlap_allows(cls, agent: str, other_agent: str) -> bool:
        return any(
            {agent, other_agent} <= group for group in cls.FAMILY_OVERLAP
        )

    @pytest.mark.parametrize(
        "agent", sorted(CORPUS) + ["cursor"],
    )
    def test_foreign_and_neutral_buffers_decline(self, agent):
        others = sorted(
            name
            for other, names in CORPUS.items()
            if other != agent and not self._overlap_allows(agent, other)
            for name in names
        )
        declined = True
        for name in others:
            model = detect(agent, fixture(name))
            if model is not None:
                declined = False
        assert declined, f"{agent} claimed a foreign dialog"
        assert detect(agent, fixture("neutral-terminal.txt")) is None

    @pytest.mark.parametrize(
        "agent,fixture_name",
        [(a, n) for a, names in CORPUS.items() for n in names],
    )
    def test_every_model_has_a_sensitive_non_empty_fingerprint(self, agent, fixture_name):
        model = detect(agent, fixture(fixture_name))

        assert model is not None and not model.unread
        assert model.fingerprint
        # Same screen, same bytes: stable.
        again = detect(agent, fixture(fixture_name))
        assert again.fingerprint == model.fingerprint
        # A different question in the same shape is a DIFFERENT dialog.
        mutated = fixture(fixture_name).replace(
            model.question.split("\n")[0][:12] or "Which",
            "Totally different question here",
            1,
        )
        other = detect(agent, mutated)
        assert other is None or other.unread or other.fingerprint != model.fingerprint

    def test_recipes_stay_inside_the_verified_key_dialect(self):
        from herdr_brain.herdr import is_valid_key

        for agent, names in CORPUS.items():
            for name in names:
                model = detect(agent, fixture(name))
                if model is None or model.unread:
                    continue
                for option in model.options:
                    assert all(is_valid_key(key) for key in option.keys), (
                        agent, name, option,
                    )
                for key in (*model.nav_keys, *model.commit_keys):
                    assert is_valid_key(key), (agent, name, key)
