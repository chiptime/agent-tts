"""Display and voice selection must agree after integrating both dialog parsers."""

import json

import pytest

from herdr_brain.tools import BrainTools
from herdr_brain.view import ansi_runs, parse_dialog
from tests.test_voice_dialog import BLOCKED_AGENT, DialogStub


@pytest.mark.parametrize(
    "first,second,selected",
    [
        ("1. Yes", "2. No", None),
        ("❯ 1. Yes", "❯ 2. No", None),
        ("1. Yes", "❯ 2. No", 2),
        ("1. Yes", "\x1b[7m2. No\x1b[0m", 2),
        ("\x1b[40m1. Yes\x1b[0m", "\x1b[44m2. No\x1b[0m", 2),
        ("\x1b[44m1. Yes\x1b[0m", "\x1b[44m2. No\x1b[0m", None),
    ],
)
def test_selection_survives_merge(settings, first, second, selected):
    raw = f"Run tests?\n{first}\n{second}\nenter submit"
    runs = ansi_runs(raw)
    flat = "\n".join("".join(run["t"] for run in line) for line in runs)
    dialog = parse_dialog(flat, screen_runs=runs)
    assert dialog["selected"] == selected
    assert [option["selected"] for option in dialog["options"]] == [
        selected == 1, selected == 2,
    ]

    stub = DialogStub(raw)
    tools = BrainTools(settings, herdr=stub)
    tools.begin_turn()
    tools.explain_dialog(BLOCKED_AGENT)
    tools.begin_turn()
    result = json.loads(tools.answer_dialog("option 1", BLOCKED_AGENT))
    if selected is None:
        assert result["reason"] == "selection-unknown"
        assert stub.key_calls == []
    else:
        assert result["outcome"] == "answered"
        assert stub.key_calls == [{"pane_id": "w1:p9", "keys": ["up", "enter"]}]
