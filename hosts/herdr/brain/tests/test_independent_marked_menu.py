import pytest
from herdr_brain.view import ansi_runs, parse_dialog

@pytest.mark.parametrize("selected", [1, 2])
def test_highlight_marker_preserves_all_spoken_options(selected):
    lines = ["¿Corremos los tests?", "  1. Sí", "  2. No", "  3. Solo unitarios", "  ↑↓ select  enter submit"]
    lines[selected] = "❯ " + "\x1b[1;36m" + lines[selected].strip() + "\x1b[0m"
    styled = "\n".join(lines)
    plain = "\n".join("".join(run["t"] for run in line) for line in ansi_runs(styled))
    dialog = parse_dialog(plain)
    print("MARKED_MENU", dialog)
    assert dialog is not None
    assert dialog["question"] == "¿Corremos los tests?"
    assert [(x["n"], x["title"]) for x in dialog["options"]] == [(1, "Sí"), (2, "No"), (3, "Solo unitarios")]
