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

from herdr_brain.view import parse_dialog

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
        {"n": 1, "title": "Sí", "description": "", "selected": True},
        {"n": 2, "title": "No", "description": "", "selected": False},
    ]


def test_cursor_on_first_option_does_not_break_question_or_drop_option():
    screen = """
     He terminado de analizar el código.
     ¿Quieres que aplique los cambios?
       ❯ 1. Sí, aplicar ahora
            Modifica los archivos según el plan.
         2. No, cancelar
            Descarta los cambios.
       ↑↓ select  enter submit
    """
    dialog = parse_dialog(screen)

    assert dialog is not None
    assert dialog["question"] == "¿Quieres que aplique los cambios?"
    assert len(dialog["options"]) == 2
    assert dialog["options"][0]["n"] == 1
    assert dialog["options"][0]["title"] == "Sí, aplicar ahora"
    assert dialog["options"][0]["selected"] is True
    assert dialog["options"][1]["n"] == 2
    assert dialog["options"][1]["title"] == "No, cancelar"
    assert dialog["options"][1]["selected"] is False


def test_cursor_on_second_option_marks_it_selected():
    screen = """
     ¿Cuál prefieres?
       1. Opción uno
       > 2. Opción dos
       3. Opción tres
       ↑↓ select  enter submit
    """
    dialog = parse_dialog(screen)

    assert dialog is not None
    assert dialog["question"] == "¿Cuál prefieres?"
    assert [opt["selected"] for opt in dialog["options"]] == [False, True, False]


def test_multiline_wrapped_question():
    screen = """
     ¿Deseas continuar con la migración de base de datos
     para el esquema de producción?
       1. Sí
       2. No
       ↑↓ select  enter submit
    """
    dialog = parse_dialog(screen)

    assert dialog is not None
    assert dialog["question"] == (
        "¿Deseas continuar con la migración de base de datos "
        "para el esquema de producción?"
    )


def test_permission_prompt_horizontal_buttons():
    screen = """
     △ Permission required
     → Shell command
       $ npm test

     Allow once   Allow always   Reject
     ⇆ select  enter confirm
    """
    dialog = parse_dialog(screen)

    assert dialog is not None
    assert dialog["question"] == "Permission required: Shell command"
    assert dialog["detail"] == "$ npm test"
    assert dialog["layout"] == "buttons"
    assert [opt["title"] for opt in dialog["options"]] == ["Allow once", "Allow always", "Reject"]
    assert dialog["options"][0]["selected"] is True
    assert dialog["hint"] == "⇆ select  enter confirm"


def test_permission_prompt_selection_from_screen_runs():
    screen = "△ Permission required\n→ Shell command\n  $ npm test\nAllow once   Allow always   Reject"
    screen_runs = [
        [{"t": "△ Permission required"}],
        [{"t": "→ Shell command"}],
        [{"t": "  $ npm test"}],
        [
            {"t": "Allow once", "bg": "#141414"},
            {"t": "   "},
            {"t": "Allow always", "bg": "#f59e0b"},
            {"t": "   "},
            {"t": "Reject", "bg": "#141414"},
        ],
    ]
    dialog = parse_dialog(screen, screen_runs=screen_runs)

    assert dialog is not None
    assert dialog["options"][0]["selected"] is False
    assert dialog["options"][1]["selected"] is True
    assert dialog["options"][2]["selected"] is False
