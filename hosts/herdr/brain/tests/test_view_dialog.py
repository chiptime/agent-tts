"""parse_dialog: the blocked pane's question, structured for readable HTML.

The screen text ALREADY carries the whole question tool (question, numbered
options with descriptions, key hint). This recovers that structure so the PWA
can paint readable HTML instead of a terminal mirror. It claims NOTHING about
which option is selected — that signal does not exist in the surface herdr
exposes (verified: no cursor in api schema/snapshot; identical backgrounds).

Fixture text is the REAL dialog captured from a blocked opencode pane
(wJ:pG), gutter and side columns included.
"""

from __future__ import annotations

from herdr_brain.view import dialog_fingerprint, parse_dialog, speakable_dialog

REAL_DIALOG = """
     → Asked 1 question
  ┃
  ┃  Cuando la suite descubra puntos flacos, ¿la PRD incluye corregirlos en el motor?
  ┃
  ┃  1. No, solo detectar y priorizar (Recomendado)
  ┃     La PRD entrega la suite y un informe de debilidades priorizado por impacto. Cada arreglo del
  ┃     motor va en una PRD/cambio aparte.
  ┃  2. Sí, los críticos
  ┃     Incluye arreglar los falsos positivos de mayor impacto dentro de la misma PRD.
  ┃  3. Sí, todos
  ┃     Detectar y corregir todo en la misma PRD. Alcance grande y abierto.                                 ~/Code/personal/hungry/compare-prices:
  ┃  4. Type your own answer
  ┃
  ┃  ↑↓ select  enter submit  esc dismiss
  ┃
"""


def test_recovers_the_real_question_tool_dialog():
    dialog = parse_dialog(REAL_DIALOG)

    assert dialog is not None
    assert dialog["question"] == (
        "Cuando la suite descubra puntos flacos, "
        "¿la PRD incluye corregirlos en el motor?"
    )
    assert [opt["n"] for opt in dialog["options"]] == [1, 2, 3, 4]
    assert dialog["options"][0]["title"] == "No, solo detectar y priorizar (Recomendado)"
    assert dialog["options"][0]["description"] == (
        "La PRD entrega la suite y un informe de debilidades priorizado por "
        "impacto. Cada arreglo del motor va en una PRD/cambio aparte."
    )
    assert dialog["options"][2]["title"] == "Sí, todos"
    # The side column (a path appended after wide spacing) is cut away.
    assert dialog["options"][2]["description"].endswith("Alcance grande y abierto.")
    assert dialog["options"][3]["title"] == "Type your own answer"
    assert dialog["options"][3]["description"] == ""
    assert dialog["hint"] == "↑↓ select  enter submit  esc dismiss"


def test_text_without_numbered_options_is_not_a_dialog():
    assert parse_dialog("just some output\nno options here") is None


def test_options_without_descriptions_still_parse():
    dialog = parse_dialog("¿Seguimos?\n  1. Sí\n  2. No\n  ↑↓ select  enter submit")

    assert dialog["question"] == "¿Seguimos?"
    assert dialog["options"] == [
        {"n": 1, "title": "Sí", "description": "", "selected": False},
        {"n": 2, "title": "No", "description": "", "selected": False},
    ]
    assert dialog["selected"] is None  # nothing visibly highlighted


# -- speakable verbal prompt (S8/FR-08) ----------------------------------

def test_speakable_dialog_speaks_the_real_question_and_every_option_label():
    """S8: the spoken prompt carries the ACTUAL question and every option
    label verbatim — nothing invented, no business choice omitted, and no
    screen needed to know the choice."""
    dialog = parse_dialog(REAL_DIALOG)

    spoken = speakable_dialog(dialog, agent="opencode")

    assert spoken.startswith("opencode pregunta:")
    # The real question, verbatim.
    assert "Cuando la suite descubra puntos flacos" in spoken
    # Every numbered option label, verbatim and in order.
    assert "Opción 1: No, solo detectar y priorizar (Recomendado)." in spoken
    assert "Opción 2: Sí, los críticos." in spoken
    assert "Opción 3: Sí, todos." in spoken
    assert "Opción 4: Type your own answer." in spoken
    assert spoken.index("Opción 1:") < spoken.index("Opción 2:") < spoken.index("Opción 3:")
    # Concise: descriptions are NOT read out (the labels carry the choice).
    assert "La PRD entrega la suite" not in spoken


def test_speakable_dialog_without_agent_still_names_the_asker():
    dialog = parse_dialog("¿Seguimos?\n  1. Sí\n  2. No\n  ↑↓ select  enter submit")

    spoken = speakable_dialog(dialog)

    assert "pregunta" in spoken
    assert "¿Seguimos?" in spoken
    assert "Opción 1: Sí." in spoken and "Opción 2: No." in spoken


def test_speakable_dialog_of_nothing_is_empty():
    assert speakable_dialog(None) == ""
    assert speakable_dialog({}) == ""


# -- dialog identity (S9 changed-dialog guard) ---------------------------

def test_dialog_fingerprint_is_stable_and_content_sensitive():
    dialog = parse_dialog(REAL_DIALOG)

    assert dialog_fingerprint(dialog) == dialog_fingerprint(parse_dialog(REAL_DIALOG))
    # A changed question is a different dialog.
    revised = dict(dialog, question="¿Pregunta nueva?")
    assert dialog_fingerprint(revised) != dialog_fingerprint(dialog)
    # A changed option label is a different dialog (the action changed).
    revised_options = [dict(opt) for opt in dialog["options"]]
    revised_options[1] = dict(revised_options[1], title="Sí, todos los críticos")
    assert dialog_fingerprint(dict(dialog, options=revised_options)) != dialog_fingerprint(dialog)
    # A removed option is a different dialog.
    assert dialog_fingerprint(dict(dialog, options=dialog["options"][:3])) != dialog_fingerprint(dialog)
    # S14/B5: descriptions are MEANING-BEARING — a changed description
    # ("Destino: staging" → "Destino: production") is a different action,
    # so prior spoken authority must not survive it. Only whitespace is
    # normalized (terminal wrapping noise), never the words.
    drifted = [dict(opt, description="otro destino: production") for opt in dialog["options"]]
    assert dialog_fingerprint(dict(dialog, options=drifted)) != dialog_fingerprint(dialog)
    rewrapped = [dict(opt, description=" ".join(opt["description"].split()))
                 for opt in dialog["options"]]
    assert dialog_fingerprint(dict(dialog, options=rewrapped)) == dialog_fingerprint(dialog)
    # Presentation-only selection changes never touch the identity.
    marked = [dict(opt, selected=True) for opt in dialog["options"]]
    assert dialog_fingerprint(dict(dialog, options=marked)) == dialog_fingerprint(dialog)


def test_dialog_fingerprint_of_nothing_is_empty():
    assert dialog_fingerprint(None) == ""
    assert dialog_fingerprint({}) == ""
