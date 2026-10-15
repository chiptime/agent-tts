"""BrainTools.reply_to_pane: the user-typed direct reply to one exact pane."""

from __future__ import annotations

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.tools import REPLY_WAIT_MS, BrainTools


def _other_agent() -> AgentInfo:
    return AgentInfo(
        pane_id="w1:p2",
        agent="claude",
        status="blocked",
        session_kind="id",
        session_value="ses_other",
        cwd="/other",
        title="Other",
        focused=False,
    )


def test_delivers_to_the_exact_pane_with_a_short_wait(settings, make_stub):
    stub = make_stub(agents=[_other_agent()])
    tools = BrainTools(settings, herdr=stub)

    result = tools.reply_to_pane("w1:p2", "yes, go ahead")

    assert result["outcome"] == "delivered"
    assert result["pane_id"] == "w1:p2"
    assert stub.prompt_calls == [
        {"pane_id": "w1:p2", "text": "yes, go ahead", "timeout_ms": REPLY_WAIT_MS}
    ]


def test_unknown_pane_is_never_redirected_to_the_focused_one(settings, make_stub):
    stub = make_stub()  # only the focused w1:p9 exists
    tools = BrainTools(settings, herdr=stub)

    result = tools.reply_to_pane("w9:p9", "yes")

    assert result["outcome"] == "unknown_pane"
    assert stub.prompt_calls == []


def test_completion_wait_expiring_still_counts_as_delivered(settings, make_stub):
    stub = make_stub(
        agents=[_other_agent()],
        prompt={"ok": False, "status": "timeout", "output": ""},
    )

    result = BrainTools(settings, herdr=stub).reply_to_pane("w1:p2", "yes")

    assert result["outcome"] == "delivered"


def test_blocked_prompt_is_reported_as_not_delivered(settings, make_stub):
    stub = make_stub(
        agents=[_other_agent()],
        prompt={"ok": False, "status": "blocked", "output": "rejected"},
    )

    result = BrainTools(settings, herdr=stub).reply_to_pane("w1:p2", "yes")

    assert result["outcome"] == "blocked"


def test_herdr_failure_is_reported_not_raised(settings, make_stub):
    class Failing(type(make_stub())):
        def send_prompt(self, pane_id, text, timeout_ms=None):
            raise HerdrError("socket gone")

    stub = Failing(agents=[_other_agent()])

    result = BrainTools(settings, herdr=stub).reply_to_pane("w1:p2", "yes")

    assert result["outcome"] == "error"
    assert "socket gone" in result["detail"]


def test_list_failure_is_reported_not_raised(settings, make_stub):
    class Failing(type(make_stub())):
        def list_agents(self):
            raise HerdrError("no herdr")

    result = BrainTools(settings, herdr=Failing()).reply_to_pane("w1:p2", "yes")

    assert result["outcome"] == "error"
