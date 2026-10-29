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
        "claude-permission.txt",
        "claude-trust.txt",
        "claude-wizard.txt",
    ),
    "codex": ("codex-trust.txt", "codex-approval.txt", "codex-ask.txt"),
    "grok": ("grok-ask.txt", "grok-permission.txt"),
    "pi": ("pi-ask.txt",),
    "opencode": ("opencode-question.txt",),
    "opencode2": ("opencode2-question.txt",),
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
