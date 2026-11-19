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
import re
from typing import Optional

import pytest

from herdr_brain.approval_lexicon import (
    destructive_text_reason,
    resolve_dialog_choice,
    resolve_dialog_choices,
    resolve_dialog_free_text,
)
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


# -- S5: multi-select and free-text answers by voice ------------------------
#
# The spoken forms of parity-f7b S5: a multiselect is answered by a
# multi-choice utterance ("marca la uno y la tres"), a free-text row by
# a spoken answer ("responde: usa la rama main"). Both keep the S9
# verbal binding (explain first, a later turn's words, refusals that
# press nothing) and both ride the SAME structured answering path the
# PWA's POST /pane/dialog/answer uses — no second choreography.

BLOCKED_CLAUDE = AgentInfo(
    pane_id="w1:p3",
    agent="claude",
    status="blocked",
    session_kind="id",
    session_value="00000000-1111-2222-3333-444444444444",
    cwd="/repo",
    title="Claude",
    focused=True,
)


def voice_multi_screen(checked, pointer) -> str:
    """The claude multi-select shape the endpoint suite pinned: three
    checkbox rows plus a submit row, pointer included."""
    names = ("Cheese", "Pepperoni", "Mushrooms")
    rows = []
    for index, name in enumerate(names):
        marker = "Filled checkbox" if index in checked else "Empty checkbox"
        arrow = "❯" if index == pointer else " "
        rows.append(f"│ {arrow} {index + 1}. {marker} {name}")
    arrow = "❯" if pointer == 3 else " "
    rows.append(f"│ {arrow} 4. Submit")
    return (
        "│ Which toppings do you want?\n"
        "│ Pick every one you like.\n"
        "│\n"
        + "\n".join(rows)
        + "\n│\n"
        + "  Space to select · Enter to submit · ↑/↓ to navigate · Esc to cancel\n"
    )


def voice_free_text_screen(pointer, typed: str = "") -> str:
    """The claude select shape with a ``Type something.`` free-text row;
    once text is typed the row shows it verbatim."""
    row3 = typed if typed else "Type something."
    return (
        "│ Which fruit do you want?\n"
        "│ Pick the one you like best.\n"
        "│\n"
        f"│ {'❯' if pointer == 0 else ' '} 1. Manzana\n"
        f"│ {'❯' if pointer == 1 else ' '} 2. Pera\n"
        f"│ {'❯' if pointer == 2 else ' '} 3. {row3}\n"
        "│\n"
        "  Enter to select · ↑/↓ to navigate · Esc to cancel\n"
    )


class VoiceMultiStub(DialogStub):
    """Drives the multi-select screen like the endpoint suite's stub: a
    digit flips its box and moves the pointer there, the arrows walk,
    Enter closes the dialog."""

    def __init__(self, checked, pointer=0):
        super().__init__(agents=[BLOCKED_CLAUDE])
        self.checked = set(checked)
        self.pointer = pointer
        self.closed = False
        self.swapped: Optional[str] = None

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        self.screen_calls.append(
            {"pane_id": pane_id, "n_lines": n_lines, "source": source, "as_ansi": as_ansi}
        )
        if self.closed:
            return "$ pytest -q\n..........\n10 passed in 0.02s\n$ _"
        screen = voice_multi_screen(self.checked, self.pointer)
        if self.swapped:
            return screen.replace("Which toppings", self.swapped)
        return screen

    def send_keys(self, pane_id, keys):
        self.key_calls.append({"pane_id": pane_id, "keys": list(keys)})
        for key in keys:
            if key in ("1", "2", "3"):
                index = int(key) - 1
                if index in self.checked:
                    self.checked.discard(index)
                else:
                    self.checked.add(index)
                self.pointer = index
            elif key == "4":
                self.pointer = 3
            elif key == "down":
                self.pointer = min(3, self.pointer + 1)
            elif key == "up":
                self.pointer = max(0, self.pointer - 1)
            elif key == "enter":
                self.closed = True
        return self._keys


class AnsiPaintedVoiceMultiStub(VoiceMultiStub):
    """The live styled-read shape: SGR paint on every line of every
    styled (``as_ansi``) read — the raw bytes recognizers must strip,
    never trust."""

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        text = super().read_screen(pane_id, n_lines, source, as_ansi)
        if not as_ansi:
            return text
        return "\n".join(
            f"\x1b[38;2;1;2;3m{line}\x1b[0m" for line in text.split("\n")
        )


class VoiceFreeTextStub(DialogStub):
    """Drives the free-text select: a digit moves the pointer to its row
    (the field takes the keyboard), literal text lands in the row
    verbatim, Enter commits the typed answer."""

    def __init__(self):
        super().__init__(agents=[BLOCKED_CLAUDE])
        self.pointer = 0
        self.typed = ""
        self.closed = False
        self.swapped: Optional[str] = None
        self.text_calls: list = []

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        self.screen_calls.append(
            {"pane_id": pane_id, "n_lines": n_lines, "source": source, "as_ansi": as_ansi}
        )
        if self.closed:
            return "$ pytest -q\n..........\n10 passed in 0.02s\n$ _"
        screen = voice_free_text_screen(self.pointer, self.typed)
        if self.swapped:
            return screen.replace("Which fruit", self.swapped)
        return screen

    def send_keys(self, pane_id, keys):
        self.key_calls.append({"pane_id": pane_id, "keys": list(keys)})
        for key in keys:
            if key in ("1", "2", "3"):
                self.pointer = int(key) - 1
            elif key == "enter" and self.typed:
                self.closed = True
        return self._keys

    def send_text(self, pane_id, text):
        self.text_calls.append({"pane_id": pane_id, "text": text})
        self.typed = text


def build_tools(settings, stub):
    return BrainTools(settings, herdr=stub, sleeper=lambda _s: None)


def explain_claude_and_advance(tools_obj):
    tools_obj.begin_turn()
    dialog = tools_obj.explain_dialog(BLOCKED_CLAUDE)
    assert dialog is not None
    tools_obj.begin_turn()
    return dialog


class TestMultiChoiceUtteranceLexicon:
    """resolve_dialog_choices: a multi-selection utterance → the exact
    spoken option set, with the same fail-closed discipline as a single
    choice (negations, competing alternatives and junk all fall through).
    """

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            # The S5 acceptance utterance, verbatim.
            ("marca la uno y la tres", (1, 3)),
            # Digits, context words and comma lists.
            ("selecciona las opciones 1 y 3", (1, 3)),
            ("marca la 1, la 3 y la 5", (1, 3, 5)),
            ("elige 2 y 4", (2, 4)),
            # Number words in Spanish and English.
            ("marca la dos y la cuatro", (2, 4)),
            ("pick one and three", (1, 3)),
            # One item is still a valid (singleton) selection set.
            ("marca la uno", (1,)),
        ],
    )
    def test_multi_selections_resolve(self, utterance, expected):
        assert resolve_dialog_choices(utterance) == expected

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "hola",
            "dile que sí",                  # a boolean answer, not a set
            "opción 1 o 2",                  # competing alternatives
            "marca la uno o la tres",        # "o" never joins a set
            "no marques la 1 y la 3",        # negated
            "selecciona 2, no, mejor 1",     # self-corrected
            "marca la 1 y la 1",             # a repeated mention
            "marca la 0 y la 2",             # options are 1-based
            "baja el volumen 50",            # a digit without choice context
            "marca la uno y luego la tres",  # junk word inside the list
        ],
    )
    def test_ambiguous_or_vague_multi_utterances_are_not_sets(self, utterance):
        assert resolve_dialog_choices(utterance) is None


class TestFreeTextUtteranceLexicon:
    """resolve_dialog_free_text: a spoken free-text answer must NAME
    itself as one ("responde: …"); the remainder is the text, verbatim.
    """

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("responde: usa la rama main", "usa la rama main"),
            ("responde que usa la rama main", "usa la rama main"),
            ("dile usa la rama main", "usa la rama main"),
            ("contesta: main", "main"),
            ("Responde: usa la rama Main", "usa la rama Main"),
            ("answer: use the main branch", "use the main branch"),
        ],
    )
    def test_lead_in_answers_resolve(self, utterance, expected):
        assert resolve_dialog_free_text(utterance) == expected

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "usa la rama main",   # no lead-in: not a declared free-text answer
            "responde:",          # nothing after the lead-in
            "responde",           # the bare verb is not an answer
        ],
    )
    def test_undeclared_or_blank_answers_are_none(self, utterance):
        assert resolve_dialog_free_text(utterance) is None


class TestDestructiveTextAnalyzer:
    """The server-side twin of the PWA's destructive.js: same segments,
    same words, same rules, same Spanish reasons — voice free-text is
    checked against it because no second tap exists by voice.
    """

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("rm -rf /", "rm -r (borrado recursivo)"),
            ("git push -f origin main", "git push --force (reescribe el historial)"),
            ("sudo pacman -Syu", "sudo (se ejecuta como root)"),
            ("convert --force out.png", "flag --force"),
            ("dd if=img.iso of=/dev/sda", "dd if= (escritura cruda en disco)"),
            ("mkfs.ext4 /dev/sda", "mkfs (formatea un sistema de ficheros)"),
            ("echo hi > /etc/passwd", "redirect a una ruta del sistema"),
            ("sudo rm -rf /tmp/demo", "rm -r (borrado recursivo)"),
        ],
    )
    def test_destructive_commands_carry_their_reason(self, text, reason):
        assert destructive_text_reason(text) == reason

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "usa la rama main",
            "grep -r pattern .",
            "asume que sí",
            "echo hi > /dev/null",
        ],
    )
    def test_ordinary_answers_are_not_destructive(self, text):
        assert destructive_text_reason(text) is None

    def test_every_segment_is_analyzed(self):
        """The split on ; & | newlines is what makes scoping honest: a
        destructive segment anywhere in the answer fires, and the rules
        see the words quotes would otherwise group."""
        assert (
            destructive_text_reason("sudo rm -rf x; grep -r pattern .")
            == "rm -r (borrado recursivo)"
        )
        assert (
            destructive_text_reason('bash -c "sudo reboot"')
            == "sudo (se ejecuta como root)"
        )


class TestVoiceMultiSelectAnswer:
    """S5: "marca la uno y la tres" answers a multi-select after
    explain_dialog, through the SAME structured path a PWA tap uses."""

    def _tools(self, settings, checked=set(), pointer=0):
        stub = VoiceMultiStub(checked, pointer)
        return build_tools(settings, stub), stub

    def test_marca_la_uno_y_la_tres_marks_the_exact_set_and_submits(
        self, settings
    ):
        built, stub = self._tools(settings, checked={1})
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "marca la uno y la tres",
                BLOCKED_CLAUDE,
                choices=["la uno", "la tres"],
            )
        )

        assert result["outcome"] == "answered"
        assert result["pane_id"] == "w1:p3"
        # The SPOKEN numbering the explanation used rides the result.
        assert result["choices"] == [1, 3]
        assert result["titles"] == ["Cheese", "Mushrooms"]
        # The structured choreography: every wrong box turned off and
        # every requested one on — one digit each, re-read between —
        # then the walk to Submit and Enter.
        assert stub.key_calls == [
            {"pane_id": "w1:p3", "keys": ["1"]},
            {"pane_id": "w1:p3", "keys": ["2"]},
            {"pane_id": "w1:p3", "keys": ["3"]},
            {"pane_id": "w1:p3", "keys": ["down"]},
            {"pane_id": "w1:p3", "keys": ["enter"]},
        ]
        assert stub.checked == {0, 2}
        # The spoken confirmation: the note names the selection set.
        assert "options" in result["note"]

    def test_multi_choice_without_any_explanation_is_refused(self, settings):
        built, stub = self._tools(settings)
        built.begin_turn()

        result = json.loads(
            built.answer_dialog(
                "marca la uno y la tres", BLOCKED_CLAUDE, choices=["la uno", "la tres"]
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "not-explained"
        assert stub.key_calls == []

    def test_a_styled_ansi_read_still_answers_the_spoken_set(self, settings):
        """The live bug class (f7d): the styled read carries SGR paint
        on every line, and the spoken structured path read it RAW — the
        recognizer saw ANSI, claimed nothing, and every spoken set
        answer refused ``unread-dialog``. The reader must hand the
        recognizer the SAME escape-stripped text the view path
        derives."""
        stub = AnsiPaintedVoiceMultiStub(checked=set())
        built = build_tools(settings, stub)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "marca la uno y la tres",
                BLOCKED_CLAUDE,
                choices=["la uno", "la tres"],
            )
        )

        assert result["outcome"] == "answered"
        assert result["choices"] == [1, 3]
        assert result["titles"] == ["Cheese", "Mushrooms"]
        assert [call["keys"] for call in stub.key_calls] == [
            ["1"], ["3"], ["down"], ["enter"],
        ]
        assert stub.checked == {0, 2}

    def test_a_changed_dialog_refuses_before_any_key(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)
        stub.swapped = "Which sauces"  # the agent redrew a different question

        result = json.loads(
            built.answer_dialog(
                "marca la uno y la tres", BLOCKED_CLAUDE, choices=["la uno", "la tres"]
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "changed"
        assert stub.key_calls == []

    def test_an_ambiguous_multi_item_refuses_like_a_single_one(self, settings):
        """An item joined by "o" (or the usual single-choice ambiguity)
        refuses the WHOLE answer with the same reason as today."""
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "marca la uno o la tres", BLOCKED_CLAUDE, choices=["la uno"]
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "not-a-choice"
        assert stub.key_calls == []

    def test_an_unknown_option_in_the_set_is_refused(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "marca la uno y la nueve", BLOCKED_CLAUDE, choices=["la uno"]
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "unknown-option"
        assert stub.key_calls == []

    def test_multi_utterance_without_the_param_stays_single_discipline(
        self, settings
    ):
        """PRESERVE: with no ``choices`` argument the utterance resolves
        through the SINGLE-choice lexicon exactly as before — a
        multi-number sentence is not a choice there."""
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog("marca la uno y la tres", BLOCKED_CLAUDE)
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "not-a-choice"
        assert stub.key_calls == []

    def test_multi_answer_rides_the_dispatch_boundary_by_name(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.dispatch(
                "answer_dialog",
                {"utterance": "marca la uno y la tres", "choices": ["la uno", "la tres"]},
                target=BLOCKED_CLAUDE,
            )
        )

        assert result["outcome"] == "answered"
        assert result["choices"] == [1, 3]
        assert stub.checked == {0, 2}

    def test_user_words_win_over_the_forwarded_fragments(self, settings):
        """B4 discipline on lists: the model's fragment list can never
        add an option the user did not name."""
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "marca la uno y la tres",
                BLOCKED_CLAUDE,
                user_utterance="marca la uno",
                choices=["la uno", "la tres"],
            )
        )

        assert result["outcome"] == "answered"
        assert result["choices"] == [1]  # only what the USER named
        assert stub.checked == {0}

    def test_choices_and_text_together_is_refused(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.dispatch(
                "answer_dialog",
                {
                    "utterance": "marca la uno",
                    "choices": ["la uno"],
                    "text": "hola",
                },
                target=BLOCKED_CLAUDE,
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "invalid-arguments"
        assert stub.key_calls == []


class TestVoiceFreeTextAnswer:
    """S5: "responde: X" answers a free-text row after explain_dialog,
    through the SAME typed-field recipe a PWA tap uses."""

    def _tools(self, settings):
        stub = VoiceFreeTextStub()
        return build_tools(settings, stub), stub

    def test_responde_types_and_commits_the_spoken_text(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "responde: usa la rama main",
                BLOCKED_CLAUDE,
                option=3,
                text="usa la rama main",
            )
        )

        assert result["outcome"] == "answered"
        assert result["pane_id"] == "w1:p3"
        assert result["option"] == 3
        assert result["text"] == "usa la rama main"
        # The structured recipe: the row's digit focuses the field, the
        # clears run one verified key each, the literal text rides the
        # typing surface, Enter commits.
        assert [call["keys"] for call in stub.key_calls] == [
            ["3"], ["ctrl+u"], ["ctrl+k"], ["enter"],
        ]
        assert stub.text_calls == [{"pane_id": "w1:p3", "text": "usa la rama main"}]
        assert stub.closed is True
        # The spoken confirmation: the note names the typed text.
        assert "typed" in result["note"]

    def test_free_text_without_any_explanation_is_refused(self, settings):
        built, stub = self._tools(settings)
        built.begin_turn()

        result = json.loads(
            built.answer_dialog(
                "responde: usa la rama main", BLOCKED_CLAUDE, option=3, text="usa la rama main"
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "not-explained"
        assert stub.key_calls == []
        assert stub.text_calls == []

    def test_a_changed_dialog_refuses_before_any_key(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)
        stub.swapped = "Which sauce"  # the agent redrew a different question

        result = json.loads(
            built.answer_dialog(
                "responde: usa la rama main", BLOCKED_CLAUDE, option=3, text="usa la rama main"
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "changed"
        assert stub.key_calls == []
        assert stub.text_calls == []

    def test_destructive_text_by_voice_is_refused(self, settings):
        """The pinned decision: the destructive analyzer (the PWA's
        destructive.js, ported server-side) has no second tap by voice,
        so a matching free-text answer is REFUSED naming the danger —
        voice never types it."""
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "responde: sudo rm -rf /tmp/demo",
                BLOCKED_CLAUDE,
                option=3,
                text="sudo rm -rf /tmp/demo",
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "destructive-text"
        assert "rm -r (borrado recursivo)" in result["detail"]
        assert stub.key_calls == []
        assert stub.text_calls == []
        assert stub.closed is False

    def test_user_words_win_over_the_forwarded_text(self, settings):
        """B4 discipline on text: the model's text argument can never
        substitute what the user actually said."""
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "responde: usa la rama main",
                BLOCKED_CLAUDE,
                user_utterance="responde: usa la rama main",
                option=3,
                text="sudo rm -rf /",
            )
        )

        assert result["outcome"] == "answered"
        assert stub.text_calls == [{"pane_id": "w1:p3", "text": "usa la rama main"}]
        assert stub.closed is True

    def test_a_choice_utterance_never_becomes_free_text(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "dile que sí", BLOCKED_CLAUDE, option=3, text="sí"
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "not-a-choice"
        assert stub.key_calls == []
        assert stub.text_calls == []

    def test_text_without_an_option_is_refused(self, settings):
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "responde: usa la rama main", BLOCKED_CLAUDE, text="usa la rama main"
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "invalid-arguments"
        assert stub.key_calls == []

    def test_an_undeclared_free_text_answer_is_refused(self, settings):
        """No lead-in, no answer: the user's words must NAME the text
        for the model to be allowed to type anything."""
        built, stub = self._tools(settings)
        explain_claude_and_advance(built)

        result = json.loads(
            built.answer_dialog(
                "usa la rama main", BLOCKED_CLAUDE, option=3, text="usa la rama main"
            )
        )

        assert result["outcome"] == "refused"
        assert result["reason"] == "not-a-choice"
        assert stub.key_calls == []
        assert stub.text_calls == []


# ── parity-f7d S3: switch_model (voice) ─────────────────────────────
#
# A model switch is a WRITE against a live terminal, so it rides the
# same approval-gate discipline as create_session: the spoken «cambia
# el modelo a X» PROPOSES (frozen pane + model), the user's
# confirmation disposes. On approve the tool sends the harness's own
# command (/model, or /switch on omp), waits for the picker, locates
# the option by name (fuzzy but UNAMBIGUOUS) and walks it with arrows
# and Enter — NEVER a digit, because in these lists a digit confirms
# and rewrites the default model (ADR 0009).

from pathlib import Path


SWITCH_FIXTURES = Path(__file__).parent / "fixtures" / "dialogs"
CLAUDE_MODEL_PICKER = (SWITCH_FIXTURES / "claude-model-picker.txt").read_text(
    encoding="utf-8"
)
CLAUDE_SWITCH_CONFIRM = (SWITCH_FIXTURES / "claude-switch-model-yes.txt").read_text(
    encoding="utf-8"
)
OMP_SWITCH_BOX = (SWITCH_FIXTURES / "omp-switch-model.txt").read_text(
    encoding="utf-8"
)
OPENCODE_MODELS_PICKER = (
    SWITCH_FIXTURES / "opencode-models-picker.txt"
).read_text(encoding="utf-8")

IDLE_CLAUDE = AgentInfo(
    pane_id="w1:p5",
    agent="claude",
    status="idle",
    session_kind="id",
    session_value="sid-1",
    cwd="/repo",
    title="Claude",
    focused=True,
)

IDLE_OMP = AgentInfo(
    pane_id="w2:p1",
    agent="omp",
    status="idle",
    session_kind="id",
    session_value="sid-2",
    cwd="/repo",
    title="Omp",
    focused=False,
)

IDLE_OPENCODE = AgentInfo(
    pane_id="w3:p2",
    agent="opencode",
    status="idle",
    session_kind="id",
    session_value="sid-3",
    cwd="/repo",
    title="OpenCode",
    focused=False,
)


def switch_picker_screen(pointer: int) -> str:
    """The pinned /model list with the pointer on ``pointer``."""
    screen = CLAUDE_MODEL_PICKER
    rows = ["Default (recommended) ✔", "Opus (1M context)", "Fable", "Sonnet", "Haiku"]
    for index, label in enumerate(rows):
        glyph = "❯" if index == pointer else " "
        screen = re.sub(
            rf"^(\s*)[❯ ] {index + 1}\. {re.escape(label)}",
            rf"\1{glyph} {index + 1}. {label}",
            screen,
            flags=re.MULTILINE,
        )
    return screen


def switch_confirm_screen(pointer: int) -> str:
    """The pinned "Switch model?" confirmation with the pointer moved."""
    yes = "❯ 1. Yes, switch to Fable 5.1"
    no = "❯ 2. No, go back"
    if pointer == 0:
        return CLAUDE_SWITCH_CONFIRM.replace("❯ 2. No, go back", "  2. No, go back")
    return CLAUDE_SWITCH_CONFIRM.replace(yes, "  1. Yes, switch to Fable 5.1").replace(
        "  2. No, go back", no
    )


class SwitchModelStub:
    """Herdr double driving a claude pane through the /model flow:
    sending the command opens the picker; arrows move its pointer;
    Enter commits the list and — the conversation being cached — the
    "Switch model?" confirmation appears; Enter on it finishes."""

    def __init__(self, agents, first_screen=None):
        self._agents = agents
        self.stage = "shell"
        self.pointer = 0
        self.confirm_pointer = 0
        self.first_screen = first_screen
        self.prompt_calls: list = []
        self.key_calls: list = []

    def list_agents(self):
        return list(self._agents)

    def active_agent(self):
        from herdr_brain.herdr import pick_active

        return pick_active(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.stage == "shell":
            return self.first_screen if self.first_screen is not None else "$ claude\n❯ _"
        if self.stage == "list":
            return switch_picker_screen(self.pointer)
        if self.stage == "confirm":
            return switch_confirm_screen(self.confirm_pointer)
        return "$ claude\nModel set. ❯ _"

    def send_prompt(self, pane_id, text, timeout_ms=None):
        self.prompt_calls.append({"pane_id": pane_id, "text": text})
        if text in ("/model", "/switch"):
            self.stage = "list"
        return {"ok": True, "status": "done", "output": ""}

    def send_keys(self, pane_id, keys):
        self.key_calls.append({"pane_id": pane_id, "keys": list(keys)})
        for key in keys:
            if self.stage == "list":
                if key == "down":
                    self.pointer = min(4, self.pointer + 1)
                elif key == "up":
                    self.pointer = max(0, self.pointer - 1)
                elif key == "enter":
                    self.stage = "confirm"
            elif self.stage == "confirm":
                if key == "down":
                    self.confirm_pointer = min(1, self.confirm_pointer + 1)
                elif key == "up":
                    self.confirm_pointer = max(0, self.confirm_pointer - 1)
                elif key == "enter":
                    self.stage = "done"
        return {"ok": True, "error": ""}


class OmpSwitchStub(SwitchModelStub):
    """The omp /switch box: Enter on a row switches and the box closes
    (no confirmation stage)."""

    def __init__(self, agents):
        super().__init__(agents)
        self.omp_pointer = 3  # the current model's row, as captured

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.stage == "list":
            return self._omp_screen()
        return super().read_screen(pane_id, n_lines, source, as_ansi)

    def _omp_screen(self):
        # Simplest faithful drive: move the ❯ glyph between model rows,
        # and let the detail rows follow the pointer as omp does — the
        # chips row claims "● current" only while the CURRENT model
        # (opus-5-5) is pointed, else it is blank.
        screen = OMP_SWITCH_BOX
        screen = screen.replace("│ ❯ anthropic/claude-opus-5-5", "│ @ anthropic/claude-opus-5-5")
        screen = screen.replace("│   anthropic/claude-fable-5-1", "│ ! anthropic/claude-fable-5-1")
        if self.omp_pointer == 3:
            screen = screen.replace("│ @ anthropic/claude-opus-5-5", "│ ❯ anthropic/claude-opus-5-5")
            screen = screen.replace("│ ! anthropic/claude-fable-5-1", "│   anthropic/claude-fable-5-1")
            return screen
        screen = screen.replace("│ @ anthropic/claude-opus-5-5", "│   anthropic/claude-opus-5-5")
        screen = screen.replace("│ ! anthropic/claude-fable-5-1", "│ ❯ anthropic/claude-fable-5-1")
        rows = []
        for line in screen.split("\n"):
            if line.startswith("│   ● current"):
                line = "│" + " " * (len(line) - 2) + "│"
            rows.append(line)
        return "\n".join(rows)

    def send_keys(self, pane_id, keys):
        self.key_calls.append({"pane_id": pane_id, "keys": list(keys)})
        for key in keys:
            if self.stage == "list":
                if key == "down":
                    self.omp_pointer = min(5, self.omp_pointer + 1)
                elif key == "up":
                    self.omp_pointer = max(0, self.omp_pointer - 1)
                elif key == "enter":
                    self.stage = "done"
        return {"ok": True, "error": ""}


class OpencodeModelsStub(SwitchModelStub):
    """The opencode /models picker: the command opens the modal, typing
    filters it (the placeholder Search row becomes the query), Enter on
    the single remaining row commits and the modal closes."""

    def __init__(self, agents):
        super().__init__(agents)
        self.text_calls: list = []
        self.query = ""
        # Which row labels the filter keeps, by flat containment (the
        # honest stand-in for opencode's own filter).
        self.filter = lambda query: [
            label
            for label in (
                "GLM-5.3-Flash",
                "Qwen3.8 Flash",
                "GLM-5.3",
                "Z.AI Coding Plan",
                "GLM-5.3 Highspeed",
            )
            if " ".join(query.lower().split())
            in " ".join(label.lower().split())
        ]
        self.close_on_enter = True

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        if self.stage == "picker":
            return OPENCODE_MODELS_PICKER
        if self.stage == "filtered":
            return self._filtered_screen()
        if self.stage == "shell":
            return "$ opencode\n❯ _"
        return "$ opencode\n✓ GLM-5.3 Highspeed set ❯ _"

    def send_prompt(self, pane_id, text, timeout_ms=None):
        self.prompt_calls.append({"pane_id": pane_id, "text": text})
        if text == "/models":
            self.stage = "picker"
        return {"ok": True, "status": "done", "output": ""}

    def send_text(self, pane_id, text):
        self.text_calls.append({"pane_id": pane_id, "text": text})
        self.query = text
        self.stage = "filtered"

    def send_keys(self, pane_id, keys):
        self.key_calls.append({"pane_id": pane_id, "keys": list(keys)})
        if self.stage == "filtered" and "enter" in keys and self.close_on_enter:
            self.stage = "done"
        return {"ok": True, "error": ""}

    def _filtered_screen(self):
        fixture_lines = OPENCODE_MODELS_PICKER.split("\n")
        title = fixture_lines[0]
        blank = fixture_lines[1]
        column = title.index("Select model")
        query_row = fixture_lines[2].replace("Search", self.query, 1)
        footer = next(
            line
            for line in fixture_lines
            if "Connect provider" in line
        )
        rows = [" " * column + label for label in self.filter(self.query)]
        return "\n".join([title, blank, query_row, blank, *rows, blank, footer])


class NotingPaneModel:
    """PaneModelResolver double that records note_switched calls."""

    def __init__(self):
        self.noted = []

    def model(self, agent, session_id):
        return None

    def context(self, agent, session_id):
        return None

    def note_switched(self, agent, session_id):
        self.noted.append((agent, session_id))


class TestSwitchModel:
    def _tools(self, stub, pane_model=None):
        return BrainTools(
            _settings(), herdr=stub, sleeper=lambda _s: None, pane_model=pane_model
        )

    def test_sends_the_command_walks_and_confirms_never_a_digit(self):
        stub = SwitchModelStub([IDLE_CLAUDE])
        noted = NotingPaneModel()
        tools = self._tools(stub, pane_model=noted)

        result = tools.switch_model("w1:p5", "Fable")

        assert result["outcome"] == "switched"
        assert result["model"] == "Fable"
        assert [call["text"] for call in stub.prompt_calls] == ["/model"]
        # Walk to Fable (row 2) with re-reads, Enter; the cached
        # conversation raises the confirmation, whose Yes row is
        # already pointed: Enter alone commits it.
        keys = [key for call in stub.key_calls for key in call["keys"]]
        assert keys == ["down", "down", "enter", "enter"]
        # THE invariant (ADR 0009): no digit ever reaches the picker.
        assert all(key not in "0123456789" for key in keys)
        # The pane's model cache was invalidated for the next /view.
        assert noted.noted == [("claude", "sid-1")]

    def test_the_omp_switch_box_walks_by_id(self):
        stub = OmpSwitchStub([IDLE_OMP])
        tools = self._tools(stub)

        result = tools.switch_model("w2:p1", "anthropic/claude-fable-5-1")

        assert result["outcome"] == "switched"
        assert [call["text"] for call in stub.prompt_calls] == ["/switch"]
        keys = [key for call in stub.key_calls for key in call["keys"]]
        assert keys == ["down", "enter"]
        assert all(key not in "0123456789" for key in keys)

    def test_an_ambiguous_name_refuses_and_presses_nothing(self):
        stub = OmpSwitchStub([IDLE_OMP])
        tools = self._tools(stub)

        result = tools.switch_model("w2:p1", "opus")

        assert result["outcome"] == "refused"
        assert result["reason"] == "ambiguous"
        # The command opened the picker; the refusal pressed NOTHING.
        assert stub.key_calls == []

    def test_an_unknown_name_refuses_and_presses_nothing(self):
        stub = SwitchModelStub([IDLE_CLAUDE])
        tools = self._tools(stub)

        result = tools.switch_model("w1:p5", "gpt-99")

        assert result["outcome"] == "refused"
        assert result["reason"] == "no-match"
        assert stub.key_calls == []

    def test_an_unsupported_harness_refuses_before_sending(self):
        codex = AgentInfo(**{**IDLE_CLAUDE.__dict__, "agent": "codex"})
        stub = SwitchModelStub([codex])
        tools = self._tools(stub)

        result = tools.switch_model(codex.pane_id, "o4-high")

        assert result["outcome"] == "refused"
        assert result["reason"] == "unsupported"
        assert stub.prompt_calls == []
        assert stub.key_calls == []

    def test_opencode_searches_the_picker_and_never_presses_a_digit(self):
        """The opencode flow is SEARCH-FIRST: the name is TYPED into
        the picker's own Search box, and Enter goes out only once the
        filtered list shows exactly one row naming the target."""
        stub = OpencodeModelsStub([IDLE_OPENCODE])
        noted = NotingPaneModel()
        tools = self._tools(stub, pane_model=noted)

        result = tools.switch_model(IDLE_OPENCODE.pane_id, "GLM-5.3 Highspeed")

        assert result["outcome"] == "switched"
        assert result["model"] == "GLM-5.3 Highspeed"
        assert [call["text"] for call in stub.prompt_calls] == ["/models"]
        assert [call["text"] for call in stub.text_calls] == ["GLM-5.3 Highspeed"]
        # Exactly one key — Enter. The typed name is literal text into
        # the Search box, never a keypress: the never-digit rule holds
        # by construction (digits ride only inside the name).
        keys = [key for call in stub.key_calls for key in call["keys"]]
        assert keys == ["enter"]
        assert all(key not in "0123456789" for key in keys)
        # The pane's model cache was invalidated for the next /view.
        assert noted.noted == [("opencode", "sid-3")]

    def test_opencode_refuses_an_ambiguous_filter_and_presses_nothing(self):
        stub = OpencodeModelsStub([IDLE_OPENCODE])
        stub.filter = lambda query: ["GLM-5.3", "GLM-5.3-Flash", "GLM-5.3 Highspeed"]
        tools = self._tools(stub)

        result = tools.switch_model(IDLE_OPENCODE.pane_id, "GLM-5.3")

        assert result["outcome"] == "refused"
        assert result["reason"] == "ambiguous"
        assert "GLM-5.3" in result["detail"]
        # The surprise stops everything: Enter never left, and nothing
        # else is sent either — the typed filter stays in the box (no
        # ctrl+u) so the operator can finish by hand.
        assert stub.key_calls == []

    def test_opencode_refuses_a_name_the_filter_empties(self):
        stub = OpencodeModelsStub([IDLE_OPENCODE])
        stub.filter = lambda query: []
        tools = self._tools(stub)

        result = tools.switch_model(IDLE_OPENCODE.pane_id, "Kimi K2.7 Code")

        assert result["outcome"] == "refused"
        assert result["reason"] == "no-match"
        assert stub.key_calls == []

    def test_opencode_refuses_when_enter_does_not_close_the_picker(self):
        stub = OpencodeModelsStub([IDLE_OPENCODE])
        stub.close_on_enter = False
        tools = self._tools(stub)

        result = tools.switch_model(IDLE_OPENCODE.pane_id, "GLM-5.3 Highspeed")

        assert result["outcome"] == "refused"
        assert result["reason"] == "no-progress"
        keys = [key for call in stub.key_calls for key in call["keys"]]
        assert keys == ["enter"]
        assert all(key not in "0123456789" for key in keys)

    def test_opencode_refuses_when_the_picker_never_appears(self):
        stub = OpencodeModelsStub([IDLE_OPENCODE])
        stub.send_prompt = lambda pane_id, text, timeout_ms=None: (
            stub.prompt_calls.append({"pane_id": pane_id, "text": text}),
            {"ok": True, "status": "done", "output": ""},
        )[-1]
        tools = self._tools(stub)

        result = tools.switch_model(IDLE_OPENCODE.pane_id, "GLM-5.3 Highspeed")

        assert result["outcome"] == "refused"
        assert result["reason"] == "no-picker"
        assert stub.text_calls == []
        assert stub.key_calls == []

    def test_a_picker_that_never_appears_refuses(self):
        stub = SwitchModelStub([IDLE_CLAUDE])
        # The command lands but the pane stays a plain shell: no
        # picker ever opens (a foreign /model handler, a slow startup).
        stub.stage = "shell"
        stub.prompt_calls = []
        original_prompt = stub.send_prompt

        def prompt_without_picker(pane_id, text, timeout_ms=None):
            call = original_prompt(pane_id, text, timeout_ms)
            stub.stage = "shell"  # the picker never appears
            return call

        stub.send_prompt = prompt_without_picker
        tools = self._tools(stub)

        result = tools.switch_model("w1:p5", "Fable")

        assert result["outcome"] == "refused"
        assert result["reason"] == "no-picker"
        assert stub.key_calls == []

    def test_an_unknown_pane_refuses(self):
        stub = SwitchModelStub([IDLE_CLAUDE])
        tools = self._tools(stub)

        result = tools.switch_model("w9:p9", "Fable")

        assert result["outcome"] == "refused"
        assert result["reason"] == "unknown-pane"
        assert stub.prompt_calls == []


def _settings():
    from tests.conftest import SETTINGS_KWARGS
    from herdr_brain.config import Settings
    import tempfile, pathlib

    return Settings(
        **{**SETTINGS_KWARGS, "state_dir": str(pathlib.Path(tempfile.mkdtemp()) / "s")}
    )


class TestSwitchModelApproveReplay:
    """The approve execution (server layer): the frozen (pane, model)
    replays through switch_model, the outcome is audited as
    ``model.switch`` — the model NAME is metadata, nothing beyond it
    rides the line — and the report rides the normal loop."""

    def test_approve_replays_the_frozen_switch_and_audits_it(
        self, settings, tmp_path, monkeypatch
    ):
        import herdr_brain.server as server_module
        from fastapi.testclient import TestClient
        from tests.test_server import FakeLLM, FakeTTS

        calls = []

        class ReplayTools:
            def __init__(self, cfg, **kwargs):
                pass

            def switch_model(self, pane_id, model):
                calls.append({"pane_id": pane_id, "model": model})
                return {"outcome": "switched", "model": model, "pane_id": pane_id}

        monkeypatch.setattr(server_module, "BrainTools", ReplayTools)
        cfg = settings.__class__(
            **{
                **settings.__dict__,
                "state_dir": str(tmp_path / "state"),
                "audio_dir": str(tmp_path / "audio"),
                "audit_content": "preview",
            }
        )
        llm = FakeLLM(result={
            "answer": "Modelo cambiado a Fable.",
            "pane_id": "w1:p5", "agent": "claude", "session_id": "s1",
        })
        app = server_module.create_app(
            settings=cfg, llm_factory=lambda c, t: llm, tts_renderer=FakeTTS()
        )
        gate = app.state.approval_store.propose(
            "s1", text="", pane_id="w1:p5", agent="claude",
            tool="switch_model", task="Fable",
        )

        response = TestClient(app).post(f"/approval/{gate.gate_id}/approve")

        assert response.status_code == 200
        assert response.json()["answer"] == "Modelo cambiado a Fable."
        assert calls == [{"pane_id": "w1:p5", "model": "Fable"}]
        audit = (tmp_path / "state" / "audit.log").read_text(encoding="utf-8")
        entry = json.loads(
            next(line for line in audit.splitlines() if "model.switch" in line)
        )
        assert entry["action"] == "model.switch"
        assert entry["pane_id"] == "w1:p5"
        # No browser device on a bare TestClient post: the key is
        # omitted, which reads back as None.
        assert entry.get("device") is None
        # The model name is metadata, not content: exactly the frozen
        # name and the outcome ride the detail.
        assert entry["detail"] == {
            "model": "Fable", "outcome": "switched", "reason": "",
        }

    def test_a_refused_replay_is_audited_as_what_it_is(
        self, settings, tmp_path, monkeypatch
    ):
        import herdr_brain.server as server_module
        from fastapi.testclient import TestClient
        from tests.test_server import FakeLLM, FakeTTS

        class RefusingTools:
            def __init__(self, cfg, **kwargs):
                pass

            def switch_model(self, pane_id, model):
                return {
                    "outcome": "refused", "reason": "ambiguous",
                    "detail": "opus is ambiguous", "model": model,
                    "pane_id": pane_id,
                }

        monkeypatch.setattr(server_module, "BrainTools", RefusingTools)
        cfg = settings.__class__(
            **{
                **settings.__dict__,
                "state_dir": str(tmp_path / "state"),
                "audio_dir": str(tmp_path / "audio"),
                "audit_content": "preview",
            }
        )
        llm = FakeLLM(result={
            "answer": "No cambié el modelo: nombre ambiguo.",
            "pane_id": None, "agent": None, "session_id": "s1",
        })
        app = server_module.create_app(
            settings=cfg, llm_factory=lambda c, t: llm, tts_renderer=FakeTTS()
        )
        gate = app.state.approval_store.propose(
            "s1", text="", pane_id="w1:p5", agent="claude",
            tool="switch_model", task="opus",
        )

        response = TestClient(app).post(f"/approval/{gate.gate_id}/approve")

        assert response.status_code == 200
        audit = (tmp_path / "state" / "audit.log").read_text(encoding="utf-8")
        entry = json.loads(
            next(line for line in audit.splitlines() if "model.switch" in line)
        )
        assert entry["detail"]["outcome"] == "refused"
        assert entry["detail"]["reason"] == "ambiguous"
        # The refusal detail (candidate names) never rides the audit
        # line: the model name alone is metadata.
        assert "opus is ambiguous" not in audit
