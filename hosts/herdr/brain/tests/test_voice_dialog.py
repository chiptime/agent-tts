"""Voice answering of terminal dialogs (S9/FR-09): lexicon, bindings and
the safe voice-to-key mapping through ``send_keys_to_pane``.

The flow under test, end to end at the tool layer: a blocked pane's
question dialog is EXPLAINED (handed to the model for speaking with a
recorded binding), a LATER turn's spoken choice is matched
deterministically against the CURRENT dialog, and only an explicit,
unambiguous, unchanged choice ever dispatches allowlisted logical keys.
Every refusal must leave the pane untouched (no keys sent).
"""

from __future__ import annotations

import json

import pytest

from herdr_brain.approval_lexicon import resolve_dialog_choice
from herdr_brain.herdr import AgentInfo
from herdr_brain.tools import BrainTools
from herdr_brain.view import ansi_runs, parse_dialog

from tests.conftest import StubHerdr

YES_NO_DIALOG = """¿Corremos los tests antes de subir?
  1. Sí
  2. No
  ↑↓ select  enter submit  esc dismiss
"""

# The same dialog with the FIRST option visibly highlighted (the ANSI
# background the styled read preserves). Positive voice paths need a
# KNOWN selection; plain dialogs are the selection-unknown refusal case.
BG = "\x1b[48;2;60;60;60m"
RST = "\x1b[0m"
YES_NO_SELECTED = YES_NO_DIALOG.replace("  1. Sí", f"  {BG}1. Sí{RST}")

AMBIGUOUS_YES_DIALOG = """¿Cuántos arreglamos?
  1. Ninguno
  2. Sí, los críticos
  3. Sí, todos
  ↑↓ select  enter submit  esc dismiss
""".replace("  1. Ninguno", f"  {BG}1. Ninguno{RST}")

NINE_OPTION_DIALOG = ("¿Cuál?\n" + "\n".join(
    f"  {BG if n == 1 else ''}{n}. Opción número {n}{RST if n == 1 else ''}"
    for n in range(1, 10)
) + "\n  ↑↓ select  enter submit\n")


BLOCKED_AGENT = AgentInfo(
    pane_id="w1:p9",
    agent="opencode",
    status="blocked",
    session_kind="id",
    session_value="ses_dialog0000001",
    cwd="/repo",
    title="OpenCode",
    focused=True,
)


class DialogStub(StubHerdr):
    """StubHerdr with a blocked agent whose screen IS a question dialog.

    The styled (as_ansi) read returns the dialog text; herdr's real styled
    read carries SGR codes, but ``ansi_runs`` strips them, so plain text is
    the same parse input the production path feeds ``parse_dialog``.
    """

    def __init__(self, screen=None, agents=None):
        # Default: the dialog with a KNOWN highlight (option 1 painted),
        # the realistic precondition for any positive voice answer.
        super().__init__(
            screen=YES_NO_SELECTED if screen is None else screen,
            agents=[BLOCKED_AGENT] if agents is None else agents,
        )
        self._dialog_screen = screen if screen is not None else YES_NO_SELECTED

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        self.screen_calls.append(
            {"pane_id": pane_id, "n_lines": n_lines, "source": source, "as_ansi": as_ansi}
        )
        if as_ansi:
            return self._dialog_screen
        return "screen tail"


@pytest.fixture
def tools(settings):
    stub = DialogStub()
    built = BrainTools(settings, herdr=stub)
    return built, stub


def explain_and_advance_turn(tools_obj, target=None):
    """One explained turn: records the binding, then moves to a LATER turn
    (the user's answer always comes after the turn that spoke the dialog)."""
    tools_obj.begin_turn()
    dialog = tools_obj.explain_dialog(target or BLOCKED_AGENT)
    tools_obj.begin_turn()
    return dialog


class TestDialogChoiceLexicon:
    """resolve_dialog_choice: deterministic utterance → choice mapping."""

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            # The PRD's exact voice examples (S9).
            ("dile que sí", ("boolean", "yes")),
            ("selecciona la opción 1", ("option", 1)),
            # Spanish variants, accents and case are normalized away.
            ("Dile que sí", ("boolean", "yes")),
            ("dile que no", ("boolean", "no")),
            ("selecciona la opcion 2", ("option", 2)),
            ("elige la 3", ("option", 3)),
            ("escoge la opción 1", ("option", 1)),
            ("marca la 2", ("option", 2)),
            ("pulsa 1", ("option", 1)),
            ("sí", ("boolean", "yes")),
            ("que sí", ("boolean", "yes")),
            ("que no", ("boolean", "no")),
            ("no", ("boolean", "no")),
            ("contesta que no", ("boolean", "no")),
            ("responde sí", ("boolean", "yes")),
            # English mirrors.
            ("yes", ("boolean", "yes")),
            ("say yes", ("boolean", "yes")),
            ("tell him no", ("boolean", "no")),
            ("option 2", ("option", 2)),
            ("choose number 1", ("option", 1)),
        ],
    )
    def test_explicit_choices_resolve(self, utterance, expected):
        assert resolve_dialog_choice(utterance) == expected

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "hola",
            "no sé",
            "¿qué opción me recomiendas?",
            "baja el volumen 50",           # a digit without choice context
            "versión 2 del informe",
            "opción 0",                      # options are 1-based
            "selecciona la opción 1 por favor porque yo quiero la primera",
            # B3: negated, multiple and self-corrected numbers select nothing.
            "no selecciones la opción 1",
            "opción 1 o 2",
            "selecciona 2, no, mejor 1",
            "ni la 1 ni la 2",
        ],
    )
    def test_vague_or_long_utterances_are_not_choices(self, utterance):
        assert resolve_dialog_choice(utterance) is None


class TestExplainDialog:
    def test_first_sight_returns_the_dialog_for_speaking(self, tools):
        built, _ = tools
        built.begin_turn()
        dialog = built.explain_dialog(BLOCKED_AGENT)
        # Production flattens the styled read through ansi_runs first.
        flat = "\n".join(
            "".join(run["t"] for run in line) for line in ansi_runs(YES_NO_SELECTED)
        )
        assert dialog == dict(parse_dialog(flat), selected=1)

    def test_same_dialog_later_turns_need_no_repeating(self, tools):
        built, _ = tools
        explain_and_advance_turn(built)
        # The dialog is unchanged: nothing new to speak.
        assert built.explain_dialog(BLOCKED_AGENT) is None

    def test_unblocked_pane_has_no_dialog_and_drops_the_binding(self, tools):
        built, _ = tools
        explain_and_advance_turn(built)
        unblocked = AgentInfo(**{**BLOCKED_AGENT.__dict__, "status": "working"})
        assert built.explain_dialog(unblocked) is None
        # The stale binding is gone: an answer now refuses, not executes.
        result = json.loads(built.answer_dialog("dile que sí", unblocked))
        assert result["outcome"] == "refused"


class TestAnswerDialog:
    def test_dile_que_si_presses_the_yes_option(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))

        assert result["outcome"] == "answered"
        assert result["pane_id"] == "w1:p9"
        assert result["option"] == 1
        assert result["title"] == "Sí"
        # From the dialog's first option: zero downs, then submit.
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["enter"]}]

    def test_selecciona_la_opcion_2_navigates_and_submits(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("selecciona la opción 2", BLOCKED_AGENT))

        assert result["outcome"] == "answered"
        assert result["option"] == 2
        assert result["title"] == "No"
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["down", "enter"]}]

    def test_answer_rides_the_dispatch_boundary_by_name(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(
            built.dispatch("answer_dialog", {"utterance": "dile que no"}, target=BLOCKED_AGENT)
        )

        assert result["outcome"] == "answered"
        assert result["option"] == 2
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["down", "enter"]}]

    def test_choice_in_the_same_turn_as_first_sight_is_refused(self, tools):
        """The user's words predate any explanation: the dialog was first
        seen in THIS turn, so nothing may be pressed yet."""
        built, stub = tools
        built.begin_turn()
        assert built.explain_dialog(BLOCKED_AGENT) is not None

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))

        assert result["outcome"] == "refused"
        assert result["reason"] == "just-appeared"
        assert stub.key_calls == []

    def test_changed_dialog_is_refused_until_re_explained(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)
        stub._dialog_screen = AMBIGUOUS_YES_DIALOG

        first = json.loads(built.answer_dialog("selecciona la opción 2", BLOCKED_AGENT))
        assert first["outcome"] == "refused"
        assert first["reason"] == "changed"
        assert stub.key_calls == []

        # The next turn re-explains the new dialog (spoken again)…
        built.begin_turn()
        assert built.explain_dialog(BLOCKED_AGENT) is not None
        # …and only the turn AFTER that hearing may answer.
        built.begin_turn()
        second = json.loads(built.answer_dialog("selecciona la opción 2", BLOCKED_AGENT))
        assert second["outcome"] == "answered"
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["down", "enter"]}]

    def test_manual_keys_after_explanation_block_voice_answering(self, tools):
        """Keys already sent moved the highlight to an unknown option: a
        later voice choice must not confirm a different action."""
        built, stub = tools
        explain_and_advance_turn(built)

        manual = built.send_keys_to_pane("w1:p9", ["down"])
        assert manual["outcome"] == "sent"  # the manual keypad keeps working

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "keys-sent"
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["down"]}]

    def test_second_voice_answer_on_the_same_dialog_is_refused(self, tools):
        """The first answer's enter left the highlight unknown; the dialog
        may still be on screen, but its base position is not."""
        built, stub = tools
        explain_and_advance_turn(built)
        assert json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))["outcome"] == "answered"

        again = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert again["outcome"] == "refused"
        assert again["reason"] == "keys-sent"
        assert len(stub.key_calls) == 1

    def test_unknown_option_number_is_refused(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("selecciona la opción 5", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "unknown-option"
        assert stub.key_calls == []

    def test_ambiguous_yes_is_refused(self, tools):
        stub = DialogStub(AMBIGUOUS_YES_DIALOG)
        built = BrainTools(tools[0]._settings, herdr=stub)
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "ambiguous-option"
        assert stub.key_calls == []

    def test_yes_without_a_yes_option_names_the_numbers(self, tools):
        stub = DialogStub(NINE_OPTION_DIALOG)
        built = BrainTools(tools[0]._settings, herdr=stub)
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "no-yes-no-option"
        assert stub.key_calls == []

    def test_not_a_choice_utterance_is_refused(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("¿qué hago?", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "not-a-choice"
        assert stub.key_calls == []

    def test_answer_without_any_explanation_is_refused(self, tools):
        built, stub = tools
        built.begin_turn()

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "not-explained"
        assert stub.key_calls == []

    def test_dialog_that_vanished_mid_flight_is_refused(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)
        stub._dialog_screen = "the agent moved on, no dialog here"

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "gone"
        assert stub.key_calls == []

    def test_option_beyond_the_key_budget_is_refused(self, tools):
        """MAX_SEND_KEYS bounds the sequence: option 9 needs 8 downs + enter
        (9 keys) and must be refused rather than truncated."""
        stub = DialogStub(NINE_OPTION_DIALOG)
        built = BrainTools(tools[0]._settings, herdr=stub)
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("opción 9", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "too-far"
        assert stub.key_calls == []

    def test_no_pane_at_all_is_refused(self, settings):
        stub = DialogStub(agents=[])
        built = BrainTools(settings, herdr=stub)
        built.begin_turn()

        result = json.loads(built.answer_dialog("dile que sí"))
        assert result["outcome"] == "refused"
        assert result["reason"] == "no-pane"

    def test_herdr_failure_propagates_without_inventing_success(self, tools):
        built, stub = tools
        stub._keys = {"ok": False, "error": "boom"}
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "send-failed"
        assert "boom" in result["detail"]

    def test_second_option_highlighted_selects_upward(self, tools):
        """B2: with option 2 visibly highlighted, choosing option 1 must
        move UP to it — never press enter on the standing highlight."""
        stub = DialogStub(YES_NO_DIALOG.replace("  2. No", f"  {BG}2. No{RST}"))
        built = BrainTools(tools[0]._settings, herdr=stub)
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "answered"
        assert result["option"] == 1
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["up", "enter"]}]

    def test_unknown_selection_refuses_rather_than_guessed(self, tools):
        """No visible highlight (no marker, no painted background): no key
        sequence can be KNOWN to land on the requested option — refuse."""
        stub = DialogStub(YES_NO_DIALOG)
        built = BrainTools(tools[0]._settings, herdr=stub)
        explain_and_advance_turn(built)

        result = json.loads(built.answer_dialog("dile que sí", BLOCKED_AGENT))
        assert result["outcome"] == "refused"
        assert result["reason"] == "selection-unknown"
        assert stub.key_calls == []

    def test_authorization_binds_to_the_real_user_words(self, tools):
        """B4: when the dispatch layer supplies the user's actual turn
        text, the model's forwarded words cannot authorize anything the
        user did not say."""
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(
            built.answer_dialog(
                "dile que sí", BLOCKED_AGENT, user_utterance="No elijas nada, explícame los riesgos"
            )
        )
        assert result["outcome"] == "refused"
        assert result["reason"] == "not-a-choice"
        assert stub.key_calls == []

    def test_model_forwarded_words_may_not_change_the_choice(self, tools):
        built, stub = tools
        explain_and_advance_turn(built)

        result = json.loads(
            built.answer_dialog(
                "selecciona la opción 1", BLOCKED_AGENT, user_utterance="selecciona la opción 2"
            )
        )
        assert result["outcome"] == "answered"
        assert result["option"] == 2  # the USER said 2; the model's 1 loses
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["down", "enter"]}]
