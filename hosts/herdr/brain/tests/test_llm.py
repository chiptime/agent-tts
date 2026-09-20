"""Routing-policy and tool-loop tests with a fully mocked LLM client.

No network: the OpenAI-compatible client is replaced by a scripted fake.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from herdr_brain.config import Settings
from herdr_brain.llm import SYSTEM_PROMPT, BrainLLM, BrainLLMError
from herdr_brain.tools import BrainTools


def tool_call_response(call_id, name, arguments, content=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
            )
        ],
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def text_response(content):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ScriptedLLM:
    """Fake OpenAI-compatible client driven by a response script."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.create_kwargs: list = []
        outer = self
        completions = SimpleNamespace()

        def create(**kwargs):
            outer.create_kwargs.append(kwargs)
            return outer.responses.pop(0)

        completions.create = create
        self.chat = SimpleNamespace(completions=completions)


def make_brain(settings, stub, responses):
    tools = BrainTools(settings, herdr=stub)
    llm = BrainLLM(settings, tools, client=ScriptedLLM(responses))
    return llm, tools


class TestRoutingPolicy:
    def test_state_question_routes_to_read_transcript(self, settings, make_stub):
        stub = make_stub(screen="fallback screen")
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "read_transcript", {"n_turns": 5}),
                text_response("Estás refactorizando el módulo de auth; tests en verde."),
            ],
        )
        result = llm.ask("en que estas trabajando?")
        # Read-only path only: nothing was ever sent to the agent session.
        assert stub.prompt_calls == []
        assert result["answer"].startswith("Estás")
        assert result["pane_id"] == "w1:p9"
        assert result["agent"] == "opencode"

    def test_transcript_result_reaches_llm_as_tool_message(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        import sqlite3

        db = tmp_path / "opencode.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?, 1, ?)",
            (active_agent.session_value, json.dumps({"role": "assistant"})),
        )
        conn.execute(
            "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
            (active_agent.session_value, json.dumps({"type": "text", "text": "Refactoring auth."})),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        llm, _ = make_brain(
            settings, make_stub(),
            responses=[
                tool_call_response("c1", "read_transcript", {}),
                text_response("ok"),
            ],
        )
        llm.ask("resume what happened")
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages and "Refactoring auth." in tool_messages[0]["content"]

    def test_action_request_routes_to_send_to_session(self, settings, make_stub):
        stub = make_stub()
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "send_to_session", {"text": "run the full test suite"}),
                text_response("Done: the agent finished the suite, all green."),
            ],
        )
        result = llm.ask("corre los tests por favor")
        assert stub.prompt_calls == [
            {"pane_id": "w1:p9", "text": "run the full test suite", "timeout_ms": None}
        ]
        assert "all green" in result["answer"]

    def test_schema_and_system_prompt_passed_to_llm(self, settings, make_stub):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("hi")])
        llm.ask("hello")
        first = llm._client.create_kwargs[0]
        assert {t["function"]["name"] for t in first["tools"]} == {
            "get_status", "read_transcript", "read_screen", "send_to_session",
        }
        assert first["messages"][0]["content"] == SYSTEM_PROMPT


class TestLoopRobustness:
    def test_plain_answer_without_tools(self, settings, make_stub):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("Hello there.")])
        assert llm.ask("hi")["answer"] == "Hello there."

    def test_max_rounds_guard(self, settings, make_stub):
        endless = Settings(**{**settings.__dict__, "max_tool_rounds": 2})
        responses = [tool_call_response(f"c{i}", "get_status", {}) for i in range(10)]
        llm, _ = make_brain(endless, make_stub(), responses=responses)
        result = llm.ask("loop forever")
        assert "could not" in result["answer"]
        assert len(llm._client.create_kwargs) == 3  # rounds + 1

    def test_invalid_tool_arguments_become_error_string(self, settings, make_stub):
        scripted = ScriptedLLM([])
        bad = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(
                    id="c1",
                    function=SimpleNamespace(name="read_transcript", arguments="not json"),
                )],
            ))]
        )
        scripted.responses = [bad, text_response("recovered")]
        llm = BrainLLM(settings, BrainTools(settings, herdr=make_stub()), client=scripted)
        llm.ask("x")
        tool_messages = [
            m for m in scripted.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"].startswith("error: invalid tool arguments")

    def test_unknown_tool_reported(self, settings, make_stub):
        stub = make_stub()
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "deploy_to_prod", {}),
                text_response("cannot do that"),
            ],
        )
        result = llm.ask("deploy")
        assert result["answer"] == "cannot do that"
        assert stub.prompt_calls == []


class TestKeyHandling:
    def test_missing_key_raises_before_any_call(self, settings, make_stub, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        no_key = Settings(**{**settings.__dict__, "glm_api_key": None})
        with pytest.raises(BrainLLMError, match="GLM_API_KEY"):
            BrainLLM(no_key, BrainTools(no_key, herdr=make_stub()))


class TestToolCallLogging:
    def test_tool_calls_logged_with_truncated_args(self, settings, make_stub, caplog):
        import logging as logging_module

        long_text = "x" * 300
        llm, _ = make_brain(
            settings, make_stub(),
            responses=[
                tool_call_response("c1", "send_to_session", {"text": long_text, "timeout_ms": 5000}),
                text_response("done"),
            ],
        )
        with caplog.at_level(logging_module.INFO, logger="herdr_brain.tool_calls"):
            llm.ask("do it")
        records = [r.getMessage() for r in caplog.records]
        assert any("name=send_to_session" in m for m in records)
        logged = next(m for m in records if "name=send_to_session" in m)
        assert "timeout_ms=5000" in logged
        # Full payload never lands in the log, only the truncated excerpt.
        assert long_text not in logged
        assert logged.count("x") <= 80

    def test_read_transcript_call_logged(self, settings, make_stub, caplog):
        import logging as logging_module

        llm, _ = make_brain(
            settings, make_stub(),
            responses=[
                tool_call_response("c1", "read_transcript", {"n_turns": 3}),
                text_response("ok"),
            ],
        )
        with caplog.at_level(logging_module.INFO, logger="herdr_brain.tool_calls"):
            llm.ask("status?")
        assert any("name=read_transcript" in r.getMessage() for r in caplog.records)


class TestSystemPromptPolicy:
    def test_policy_baked_in(self):
        assert "read_transcript" in SYSTEM_PROMPT
        assert "send_to_session" in SYSTEM_PROMPT
        assert "3 short sentences" in SYSTEM_PROMPT
        assert "NEVER send anything" in SYSTEM_PROMPT
