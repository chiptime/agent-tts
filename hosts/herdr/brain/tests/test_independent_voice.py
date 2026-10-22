import hashlib, json
import pytest
from herdr_brain.tools import BrainTools
from herdr_brain.view import ansi_runs, speakable_dialog
from tests.test_voice_dialog import DialogStub, BLOCKED_AGENT, YES_NO_DIALOG, explain_and_advance_turn
from tests.test_llm import make_brain, text_response, tool_call_response

def digest(stub):
    return hashlib.sha256(json.dumps([stub.key_calls, stub.prompt_calls]).encode()).hexdigest()

@pytest.mark.parametrize("words", ["dile que sí", "selecciona la opción 1"])
def test_visible_second_highlight_selects_first_or_refuses(settings, words):
    class Menu(DialogStub):
        def __init__(self):
            super().__init__(YES_NO_DIALOG.replace("  2. No", "  \x1b[48;2;60;60;60m2. No\x1b[0m"))
            self.highlight = 1
            self.selected = None
        def send_keys(self, pane_id, keys):
            for key in keys:
                if key == "down": self.highlight = (self.highlight + 1) % 2
                elif key == "up": self.highlight = (self.highlight - 1) % 2
                elif key == "enter": self.selected = self.highlight + 1
            return super().send_keys(pane_id, keys)
    stub = Menu()
    assert any(run.get("bg") for line in ansi_runs(stub._dialog_screen) for run in line if "2. No" in run["t"])
    tools = BrainTools(settings, herdr=stub)
    explain_and_advance_turn(tools)
    before = digest(stub)
    result = json.loads(tools.answer_dialog(words, BLOCKED_AGENT))
    print("HIGHLIGHT", words, result, "actual selected", stub.selected)
    if result["outcome"] == "refused": assert digest(stub) == before
    else: assert stub.selected == 1

@pytest.mark.parametrize("words", ["no selecciones la opción 1", "opción 1 o 2", "selecciona 2, no, mejor 1"])
def test_ambiguous_or_negated_choices_do_not_act(settings, words):
    stub = DialogStub()
    tools = BrainTools(settings, herdr=stub)
    explain_and_advance_turn(tools)
    before = digest(stub)
    result = json.loads(tools.answer_dialog(words, BLOCKED_AGENT))
    print("AMBIGUOUS", words, result)
    assert digest(stub) == before
    assert result["outcome"] == "refused"

def test_model_cannot_replace_user_words_with_yes(settings):
    stub = DialogStub()
    llm, tools = make_brain(settings, stub, [text_response("¿Corremos los tests? Opción 1: Sí. Opción 2: No."), tool_call_response("guess", "answer_dialog", {"utterance": "dile que sí"}), text_response("Hecho.")])
    llm.ask("¿Qué pregunta el agente?", pane_id=BLOCKED_AGENT.pane_id)
    before = digest(stub)
    llm.ask("No elijas nada, explícame los riesgos", pane_id=BLOCKED_AGENT.pane_id)
    print("MODEL_GUESS", stub.key_calls)
    assert digest(stub) == before

def test_speakable_labels_are_faithful(settings):
    tools = BrainTools(settings, herdr=DialogStub())
    tools.begin_turn()
    dialog = tools.explain_dialog(BLOCKED_AGENT)
    assert speakable_dialog(dialog, "opencode") == "opencode pregunta: ¿Corremos los tests antes de subir? Opción 1: Sí. Opción 2: No."

@pytest.mark.parametrize("words", ["", "opción 0", "opción 99", "no sé"])
def test_rejected_choices_preserve_actions(settings, words):
    stub = DialogStub()
    tools = BrainTools(settings, herdr=stub)
    explain_and_advance_turn(tools)
    before = digest(stub)
    assert json.loads(tools.answer_dialog(words, BLOCKED_AGENT))["outcome"] == "refused"
    assert digest(stub) == before

def test_changed_option_description_does_not_reuse_explanation(settings):
    stub = DialogStub(YES_NO_DIALOG.replace("  2. No", "    Destino: staging\n  2. No"))
    tools = BrainTools(settings, herdr=stub)
    explain_and_advance_turn(tools)
    stub._dialog_screen = stub._dialog_screen.replace("staging", "production")
    before = digest(stub)
    result = json.loads(tools.answer_dialog("dile que sí", BLOCKED_AGENT))
    print("CHANGED_DESCRIPTION", result)
    assert digest(stub) == before
    assert result["outcome"] == "refused"

def test_duplicate_option_numbers_require_clarification(settings):
    stub = DialogStub(YES_NO_DIALOG.replace("2. No", "1. No"))
    tools = BrainTools(settings, herdr=stub)
    explain_and_advance_turn(tools)
    before = digest(stub)
    result = json.loads(tools.answer_dialog("selecciona la opción 1", BLOCKED_AGENT))
    assert digest(stub) == before
    assert result["outcome"] == "refused"
