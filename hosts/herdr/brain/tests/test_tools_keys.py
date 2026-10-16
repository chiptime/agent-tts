"""BrainTools.send_keys_to_pane: answering a blocked agent's dialog.

``herdr agent prompt`` refuses an agent waiting at an approval or question
dialog, so that dialog is answered with logical keys. This is the second
narrow write path: no LLM, no gate, and a closed key allowlist.
"""

from __future__ import annotations

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.tools import BrainTools


def _blocked_agent() -> AgentInfo:
    return AgentInfo(
        pane_id="w1:p2",
        agent="opencode",
        status="blocked",
        session_kind="id",
        session_value="ses_blocked",
        cwd="/repo",
        title="Blocked",
        focused=True,
    )


def test_sends_the_keys_to_the_exact_pane(settings, make_stub):
    stub = make_stub(agents=[_blocked_agent()])
    tools = BrainTools(settings, herdr=stub)

    result = tools.send_keys_to_pane("w1:p2", ["right", "enter"])

    assert result["outcome"] == "sent"
    assert result["pane_id"] == "w1:p2"
    assert stub.key_calls == [{"pane_id": "w1:p2", "keys": ["right", "enter"]}]
    # A key press is not a prompt: the text path must stay untouched.
    assert stub.prompt_calls == []


def test_unknown_pane_is_never_redirected_to_the_focused_one(settings, make_stub):
    stub = make_stub()  # only the focused w1:p9 exists
    tools = BrainTools(settings, herdr=stub)

    result = tools.send_keys_to_pane("w9:p9", ["enter"])

    assert result["outcome"] == "unknown_pane"
    assert stub.key_calls == []


def test_cli_failure_is_reported_not_raised(settings, make_stub):
    stub = make_stub(
        agents=[_blocked_agent()], keys={"ok": False, "error": "agent_not_found"}
    )
    tools = BrainTools(settings, herdr=stub)

    result = tools.send_keys_to_pane("w1:p2", ["enter"])

    assert result["outcome"] == "error"
    assert "agent_not_found" in result["detail"]


def test_list_failure_is_reported_not_raised(settings, make_stub):
    class Failing(type(make_stub())):
        def list_agents(self):
            raise HerdrError("no herdr")

    result = BrainTools(settings, herdr=Failing()).send_keys_to_pane("w1:p2", ["enter"])

    assert result["outcome"] == "error"


def test_a_refused_key_list_is_reported_not_raised(settings, make_stub):
    class Refusing(type(make_stub())):
        def send_keys(self, pane_id, keys):
            raise HerdrError("unsupported key(s): ctrl+c")

    stub = Refusing(agents=[_blocked_agent()])

    result = BrainTools(settings, herdr=stub).send_keys_to_pane("w1:p2", ["ctrl+c"])

    assert result["outcome"] == "error"
    assert "unsupported key" in result["detail"]
