"""Routing-policy and tool-loop tests with a fully mocked LLM client.

No network: the OpenAI-compatible client is replaced by a scripted fake,
and the transport-cancellation tests drive the PRODUCTION AsyncOpenAI
stack over an httpx MockTransport that blocks inside HTTP I/O.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import AsyncOpenAI

from herdr_brain.approval import PROPOSED, SUPERSEDED, ApprovalGateStore
from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.llm import SYSTEM_PROMPT, BrainLLM, BrainLLMError, TurnCancelled
from herdr_brain.memory import ConversationStore
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
    # The gate store is what production wires; tests reach it via
    # llm._approval_store (same convention as llm._client).
    llm.attach_approval_store(ApprovalGateStore(timeout_s=settings.approval_timeout_s))
    return llm, tools


class TestRoutingPolicy:
    @pytest.mark.parametrize("question", ["sesiones tengo abiertas", "qué sesiones tengo abiertas?"])
    def test_open_session_question_reaches_inventory_and_surfaces_result(
        self, question, settings, make_stub, active_agent, monkeypatch
    ):
        """Scripted calls prove wiring and result flow, not live-model routing."""
        other = TestSelection._other(active_agent)
        stub = make_stub(agents=[active_agent, other])
        listing = Mock(wraps=stub.list_agents)
        monkeypatch.setattr(stub, "list_agents", listing)
        llm, tools = make_brain(
            settings, stub, responses=[tool_call_response("inventory", "list_open_sessions", {})]
        )
        scripted = llm._client
        original_create = scripted.chat.completions.create

        def create(**kwargs):
            if scripted.create_kwargs:
                tool_message = next(m for m in kwargs["messages"] if m["role"] == "tool")
                inventory = json.loads(tool_message["content"])
                titles = ", ".join(s["title"] for s in inventory["sessions"])
                scripted.responses.append(text_response(f"{inventory['count']} open sessions: {titles}."))
            return original_create(**kwargs)

        monkeypatch.setattr(scripted.chat.completions, "create", create)
        result = llm.ask(question, pane_id=other.pane_id)
        assert result["answer"] == "2 open sessions: OpenCode, Other."
        assert result["pane_id"] == other.pane_id and tools.last_active.pane_id == other.pane_id
        assert result["approval"] is None and llm._approval_store.current("default") is None
        assert stub.prompt_calls == []
        assert len(scripted.create_kwargs) == 2
        assert listing.call_count == 2  # target resolution + on-demand inventory only
        messages = scripted.create_kwargs[-1]["messages"]
        assert next(m for m in messages if m["role"] == "user")["content"] == question
        inventory_message = next(m for m in messages if m["role"] == "tool")
        assert inventory_message["tool_call_id"] == "inventory"
        inventory = json.loads(inventory_message["content"])
        assert inventory["available"] is True and inventory["selected_pane_id"] == other.pane_id
        assert [s["status"] for s in inventory["sessions"]] == ["working", "idle"]
        # Other sessions' metadata is not injected into the per-turn system block.
        assert active_agent.pane_id not in messages[0]["content"]

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
        # The send was gated, never executed: nothing reached the pane.
        assert stub.prompt_calls == []
        gate = llm._approval_store.current("default")
        assert gate is not None and gate.state == PROPOSED
        assert gate.action.text == "run the full test suite"
        assert result["approval"].gate_id == gate.gate_id
        assert "all green" in result["answer"]

    def test_schema_and_system_prompt_passed_to_llm(self, settings, make_stub):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("hi")])
        llm.ask("hello")
        first = llm._client.create_kwargs[0]
        assert {t["function"]["name"] for t in first["tools"]} == {
            "get_status", "read_transcript", "read_screen", "send_to_session",
            "create_session", "list_open_sessions",
            # On-demand consult surface (T9): read-only, no approval gate.
            "consult_work_status", "consult_history",
            "get_followup_context", "end_followup",
        }
        system = first["messages"][0]["content"]
        assert system.startswith(SYSTEM_PROMPT)
        assert "LIVE CONTEXT" in system


class TestLiveContext:
    def test_block_with_active_pane(self, active_agent):
        from datetime import datetime

        from herdr_brain.llm import build_live_context

        block = build_live_context(active_agent, now=datetime(2026, 9, 21, 14, 30))
        assert "LIVE CONTEXT" in block
        assert "opencode (working)" in block
        assert f"Pane: {active_agent.pane_id}" in block
        assert f"Session: {active_agent.session_value}" in block
        assert "Working directory: /repo" in block
        assert "2026-09-21 14:30" in block

    def test_block_without_active_pane(self):
        from herdr_brain.llm import build_live_context

        block = build_live_context(None)
        assert "none right now" in block

    def test_system_message_reflects_stubbed_active_pane(self, settings, make_stub, active_agent):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("ok")])
        llm.ask("who is active?")
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert f"Pane: {active_agent.pane_id}" in system
        assert "Working directory: /repo" in system

    def test_live_context_not_stored_in_memory(self, settings, make_stub):
        store = ConversationStore()
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("ok")])
        llm.attach_store(store)
        llm.ask("hello", session_id="s1")
        stored = store.history("s1")
        assert [m.role for m in stored] == ["user", "assistant"]
        assert all("LIVE CONTEXT" not in m.content for m in stored)


class TestConversationMemory:
    def test_follow_up_sees_prior_turns(self, settings, make_stub):
        store = ConversationStore()
        llm, _ = make_brain(
            settings, make_stub(),
            responses=[
                text_response("Estoy refactorizando el módulo de auth."),
                text_response("Además de eso, dejé los tests en verde."),
            ],
        )
        llm.attach_store(store)
        llm.ask("en que estas trabajando?", session_id="s1")
        llm.ask("y ¿qué más?", session_id="s1")

        second = llm._client.create_kwargs[1]["messages"]
        roles = [m["role"] for m in second]
        assert roles == ["system", "user", "assistant", "user"]
        assert second[1]["content"] == "en que estas trabajando?"
        assert second[2]["content"] == "Estoy refactorizando el módulo de auth."
        assert second[3]["content"] == "y ¿qué más?"

    def test_reset_clears_history(self, settings, make_stub):
        store = ConversationStore()
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("a1")])
        llm.attach_store(store)
        llm.ask("first", session_id="s1")
        store.reset("s1")
        llm._client.create_kwargs.clear()
        llm._client.responses.append(text_response("a2"))
        llm.ask("second", session_id="s1")
        second = llm._client.create_kwargs[0]["messages"]
        assert [m["role"] for m in second] == ["system", "user"]

    def test_sessions_are_independent(self, settings, make_stub):
        store = ConversationStore()
        llm, _ = make_brain(
            settings, make_stub(),
            responses=[
                text_response("answer one"),
                text_response("answer two"),
                text_response("check"),
            ],
        )
        llm.attach_store(store)
        llm.ask("q1", session_id="s1")
        llm.ask("q2", session_id="s2")
        llm.ask("context?", session_id="s1")
        third = llm._client.create_kwargs[2]["messages"]
        contents = [m.get("content", "") for m in third]
        assert "q1" in contents and "answer one" in contents
        assert "q2" not in contents

    def test_ring_cap_bounds_stored_messages(self, settings, make_stub):
        from herdr_brain.memory import MAX_MESSAGES

        store = ConversationStore()
        responses = [text_response(f"r{i}") for i in range(MAX_MESSAGES + 2)]
        llm, _ = make_brain(settings, make_stub(), responses=responses)
        llm.attach_store(store)
        for i in range(MAX_MESSAGES + 2):
            llm.ask(f"q{i}", session_id="s1")
        assert len(store.history("s1")) == MAX_MESSAGES

    def test_response_includes_session_id(self, settings, make_stub):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("hi")])
        result = llm.ask("hello", session_id="abc")
        assert result["session_id"] == "abc"

    def test_default_session_when_omitted(self, settings, make_stub):
        from herdr_brain.memory import DEFAULT_SESSION

        llm, _ = make_brain(settings, make_stub(), responses=[text_response("hi")])
        assert llm.ask("hello")["session_id"] == DEFAULT_SESSION


class TestPendingHint:
    def test_attention_line_when_screen_asks_permission(self, settings, make_stub, active_agent):
        stub = make_stub(screen="Do you want to allow this? (y/n)")
        llm, _ = make_brain(settings, stub, responses=[text_response("ok")])
        llm.ask("cual es el estado?")
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert "ATTENTION" in system
        assert "pending permission" in system
        assert "Do you want to allow this?" in system

    def test_blocked_status_hint_even_without_text_match(self, settings, make_stub, active_agent):
        blocked = AgentInfo(**{**active_agent.__dict__, "status": "blocked"})
        stub = make_stub(agents=[blocked], screen="Press any key")
        llm, _ = make_brain(settings, stub, responses=[text_response("ok")])
        llm.ask("estado")
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert "the agent is BLOCKED" in system

    def test_no_attention_when_all_quiet(self, settings, make_stub):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("ok")])
        llm.ask("estado")
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert "ATTENTION" not in system

    def test_read_before_confirm_rule_in_static_prompt(self):
        assert "Never send a blind yes" in SYSTEM_PROMPT
        assert "FIRST read_screen" in SYSTEM_PROMPT
        assert "surface it to the user proactively" in SYSTEM_PROMPT


class TestSelection:
    @staticmethod
    def _other(active):
        return AgentInfo(
            pane_id="w1:p2", agent="opencode", status="idle", session_kind="id",
            session_value="ses_t0000000002", cwd="/other", title="Other",
            focused=False,
        )

    def test_live_context_marks_selection_and_focus(self, settings, make_stub, active_agent):
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("ok")])
        llm.ask("hi")
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert "Selected agent: opencode (working) — focused: yes" in system

    def test_ask_with_pane_id_targets_that_agent(self, settings, make_stub, active_agent):
        other = self._other(active_agent)
        stub = make_stub(agents=[active_agent, other], screen="other tail")
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "send_to_session", {"text": "run lint"}),
                text_response("Done: lint passed on the other agent."),
            ],
        )
        result = llm.ask("corre lint en el otro", pane_id="w1:p2")
        # Live context reflects the selected, non-focused agent.
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert "Selected agent: opencode (idle) — focused: no" in system
        assert "Pane: w1:p2" in system
        # The write froze against the selected pane, not the focused one.
        assert stub.prompt_calls == []
        gate = llm._approval_store.current("default")
        assert gate.action.pane_id == "w1:p2"
        assert result["pane_id"] == "w1:p2"

    def test_stale_pane_id_falls_back_to_focused(self, settings, make_stub, active_agent):
        stub = make_stub(agents=[active_agent])
        llm, _ = make_brain(settings, stub, responses=[text_response("ok")])
        result = llm.ask("hi", pane_id="w9:gone")
        assert result["pane_id"] == active_agent.pane_id

    def test_pending_hint_ties_to_selected_agent(self, settings, make_stub, active_agent):
        other = self._other(active_agent)
        stub = make_stub(
            agents=[active_agent, other],
            screen="Working normally",  # focused pane: quiet
        )

        original_read = stub.read_screen

        def read_screen(pane_id, n_lines=None, as_ansi=False):
            if pane_id == "w1:p2":
                return "Continue with deploy? (y/n)"
            return original_read(pane_id, n_lines=n_lines)

        stub.read_screen = read_screen
        llm, _ = make_brain(settings, stub, responses=[text_response("ok")])
        llm.ask("estado", pane_id="w1:p2")
        system = llm._client.create_kwargs[0]["messages"][0]["content"]
        assert "ATTENTION" in system
        assert "Continue with deploy?" in system


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


class TestApprovalGate:
    """send_to_session interception: freeze exact args, never execute."""

    def test_send_freezes_exact_args_and_opens_gate(self, settings, make_stub):
        stub = make_stub()
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response(
                    "c1",
                    "send_to_session",
                    {"text": "arregla el bug del login", "timeout_ms": 300000},
                ),
                text_response("Voy a enviar a opencode: arregla el bug del login."),
            ],
        )
        result = llm.ask("dile que arregle el login", session_id="s1")
        # AC1: nothing reached the pane; the args are frozen in a live gate.
        assert stub.prompt_calls == []
        gate = llm._approval_store.current("s1")
        assert gate is not None and gate.state == PROPOSED
        assert gate.tool == "send_to_session"
        assert gate.action.text == "arregla el bug del login"
        assert gate.action.timeout_ms == 300000
        assert gate.action.pane_id == "w1:p9"
        assert gate.action.agent == "opencode"
        assert result["approval"].gate_id == gate.gate_id
        # The blocked turn still completes normally with the model answer.
        assert result["answer"].startswith("Voy a enviar")

    def test_read_only_tools_never_open_gates(self, settings, make_stub):
        # AC2: reads dispatch exactly as before; no gate, no overhead.
        stub = make_stub(screen="working on the fix")
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "read_transcript", {"n_turns": 5}),
                text_response("Está trabajando en el fix."),
            ],
        )
        result = llm.ask("qué está haciendo?", session_id="s1")
        assert result["approval"] is None
        assert llm._approval_store.current("s1") is None
        assert stub.prompt_calls == []
        assert stub.screen_calls  # the read really ran, untouched path

    def test_tool_message_tells_model_the_send_is_pending(self, settings, make_stub):
        llm, _ = make_brain(
            settings, make_stub(),
            responses=[
                tool_call_response("c1", "send_to_session", {"text": "x"}),
                text_response("Eco de lo que voy a enviar."),
            ],
        )
        llm.ask("manda x")
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        content = tool_messages[0]["content"]
        assert "pending user approval" in content
        assert "NOT" in content
        assert "never say it was sent" in content

    def test_second_ask_supersedes_previous_live_gate(self, settings, make_stub):
        # One live gate per session: the newer send wins with its own args.
        stub = make_stub()
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "send_to_session", {"text": "first send"}),
                text_response("eco uno"),
                tool_call_response(
                    "c2", "send_to_session", {"text": "second send", "timeout_ms": 5000}
                ),
                text_response("eco dos"),
            ],
        )
        first = llm.ask("manda esto", session_id="s1")
        second = llm.ask("no, manda esto otro", session_id="s1")
        gates = llm._approval_store
        assert gates.get(first["approval"].gate_id).state == SUPERSEDED
        live = gates.current("s1")
        assert live.gate_id == second["approval"].gate_id
        assert live.action.text == "second send"
        assert live.action.timeout_ms == 5000
        assert stub.prompt_calls == []

    def test_send_without_wired_store_blocks_fail_safe(self, settings, make_stub):
        # AC1 even when wiring is broken: no store, no execution.
        stub = make_stub()
        llm = BrainLLM(
            settings,
            BrainTools(settings, herdr=stub),
            client=ScriptedLLM(
                [
                    tool_call_response("c1", "send_to_session", {"text": "x"}),
                    text_response("no pude enviarlo."),
                ]
            ),
        )
        result = llm.ask("manda x")
        assert stub.prompt_calls == []
        assert result["approval"] is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"].startswith(
            "error: send_to_session is blocked"
        )

    def test_send_without_active_pane_mirrors_tool_error(self, settings, make_stub):
        stub = make_stub(agents=[])
        llm, _ = make_brain(
            settings, stub,
            responses=[
                tool_call_response("c1", "send_to_session", {"text": "x"}),
                text_response("no hay agente activo."),
            ],
        )
        result = llm.ask("manda x")
        assert stub.prompt_calls == []
        assert result["approval"] is None
        assert llm._approval_store.current("default") is None
        tool_messages = [
            m for m in llm._client.create_kwargs[-1]["messages"] if m.get("role") == "tool"
        ]
        assert tool_messages[0]["content"] == "error: no active agent pane"


class TestKeyHandling:
    def test_missing_key_raises_before_any_call(self, settings, make_stub, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        no_key = Settings(**{**settings.__dict__, "glm_api_key": None})
        with pytest.raises(BrainLLMError, match="GLM_API_KEY"):
            BrainLLM(no_key, BrainTools(no_key, herdr=make_stub()))


class TestDisconnectCancellation:
    """Cooperative abort of the tool loop when the /ask client is gone.

    The cancel event is the REQUEST-scoped disconnect signal (FR-06),
    deliberately separate from a speech job's cancel event: every check
    guards a side-effect boundary, so an abandoned turn can never open a
    gate, dispatch a tool or land in memory.
    """

    def test_cancelled_before_any_round_makes_no_provider_call(self, settings, make_stub):
        llm, _ = make_brain(settings, make_stub(), responses=[])
        with pytest.raises(TurnCancelled):
            llm.ask("question", cancel_event=_set_event())
        assert llm._client.create_kwargs == []

    def test_cancelled_between_rounds_stops_the_loop(self, settings, make_stub):
        cancel = threading.Event()
        scripted = ScriptedLLM([tool_call_response("c1", "get_status", {})])
        original_create = scripted.chat.completions.create

        def create(**kwargs):
            response = original_create(**kwargs)  # record the round
            cancel.set()  # the disconnect lands right after round 1 returns
            return response

        scripted.chat.completions.create = create
        llm = BrainLLM(settings, BrainTools(settings, herdr=make_stub()), client=scripted)
        with pytest.raises(TurnCancelled):
            llm.ask("question", cancel_event=cancel)
        assert len(scripted.create_kwargs) == 1  # no second provider round

    def test_cancelled_before_tool_call_dispatches_nothing(self, settings, make_stub):
        stub = make_stub()
        cancel = threading.Event()
        scripted = ScriptedLLM([tool_call_response("c1", "send_to_session", {"text": "x"})])
        original_create = scripted.chat.completions.create

        def create(**kwargs):
            response = original_create(**kwargs)  # record the round
            cancel.set()  # lands after the model asked for the send
            return response

        scripted.chat.completions.create = create
        llm = BrainLLM(settings, BrainTools(settings, herdr=stub), client=scripted)
        llm.attach_approval_store(ApprovalGateStore(timeout_s=settings.approval_timeout_s))
        with pytest.raises(TurnCancelled):
            llm.ask("manda x", cancel_event=cancel)
        # The send never ran and no gate opened: the check guards the
        # side-effect boundary itself (the screen read that remains is
        # the live-context probe, which happens before any round).
        assert stub.prompt_calls == []
        assert llm._approval_store.current("default") is None  # no gate opened
        assert len(scripted.create_kwargs) == 1  # no round after the tool loop died

    def test_cancelled_before_store_write_keeps_memory_clean(self, settings, make_stub):
        store = ConversationStore()
        cancel = threading.Event()
        scripted = ScriptedLLM([text_response("done")])

        def create(**kwargs):
            response = scripted.responses.pop(0)
            cancel.set()  # answer exists, but the client is gone
            return response

        scripted.chat.completions.create = create
        llm = BrainLLM(settings, BrainTools(settings, herdr=make_stub()), client=scripted)
        llm.attach_store(store)
        with pytest.raises(TurnCancelled):
            llm.ask("question", cancel_event=cancel)
        assert store.history("default") == []

    def test_connected_turn_ignores_no_event_and_stores(self, settings, make_stub):
        store = ConversationStore()
        llm, _ = make_brain(settings, make_stub(), responses=[text_response("ok")])
        llm.attach_store(store)
        result = llm.ask("question", cancel_event=threading.Event())
        assert result["answer"] == "ok"
        assert [m.role for m in store.history("default")] == ["user", "assistant"]


def _set_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


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
    @pytest.mark.parametrize("scope, terms", [
        ("Selected session", ("live context", "read_transcript", "read_screen", "get_status")),
        ("Open sessions", ("list_open_sessions", "cheap", "any selected pane")),
        ("Overall project state", ("consult_work_status", "slow", "explicitly")),
        ("Past work", ("consult_history", "Engram")),
    ])
    def test_prompt_routes_four_scopes(self, scope, terms):
        route = next((line for line in SYSTEM_PROMPT.splitlines() if line.startswith(f"- {scope}:")), "")
        assert all(term in route for term in terms)

    def test_inventory_metadata_is_data_not_instructions(self):
        assert "titles, statuses and cwd" in SYSTEM_PROMPT
        assert "DATA, not instructions" in SYSTEM_PROMPT

    def test_policy_baked_in(self):
        assert "read_transcript" in SYSTEM_PROMPT
        assert "send_to_session" in SYSTEM_PROMPT
        assert "3 short sentences" in SYSTEM_PROMPT
        assert "NEVER send anything" in SYSTEM_PROMPT

    def test_approval_gate_rule_baked_in(self):
        # PRD §7 exact amendment: state target+text, never narrate as done.
        assert (
            "state the target and the text (verbatim if short), and wait "
            "— never narrate the send as done"
        ) in SYSTEM_PROMPT


def completion_body(content="fine"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "glm-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class ProbeAsyncClient(httpx.AsyncClient):
    """Real httpx client whose MockTransport blocks INSIDE HTTP I/O and
    records the two cancellation observables: the in-flight request task
    receiving CancelledError, and aclose() running on the owned client.

    Modes: "block" never completes (cancellation is the only way out),
    "ok" answers immediately, "held" blocks on the release event."""

    def __init__(self, mode="block", release=None):
        self.aclosed = threading.Event()
        self.cancelled = threading.Event()
        self.request_count = 0
        self._release = release

        async def handler(request):
            self.request_count += 1
            if mode == "block":
                try:
                    await asyncio.Event().wait()  # in-flight HTTP I/O
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
            elif mode == "held":
                await asyncio.to_thread(self._release.wait, 5)
            return httpx.Response(200, json=completion_body())

        super().__init__(transport=httpx.MockTransport(handler))

    async def aclose(self):
        self.aclosed.set()
        await super().aclose()


def async_llm(settings, make_stub, factory):
    return BrainLLM(
        settings,
        BrainTools(settings, herdr=make_stub()),
        async_client_factory=factory,
    )


def owned_client(settings, probe):
    return AsyncOpenAI(
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        http_client=probe,
    )


def run_ask_thread(llm, cancel_event):
    outcome = {}

    def run():
        try:
            outcome["result"] = llm.ask("question", cancel_event=cancel_event)
        except BaseException as exc:  # noqa: BLE001 — recorded, asserted below
            outcome["exc"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


class TestProviderTransportCancellation:
    """FR-06 correction: a disconnected ask() must abort the PRODUCTION
    HTTP operation itself — the production AsyncOpenAI stack over a real
    httpx transport — not merely signal a cooperative flag. These probes
    block inside HTTP I/O and would otherwise hang until the provider's
    own timeout."""

    def test_disconnect_cancels_in_flight_http_and_closes_owned_transport(
        self, settings, make_stub
    ):
        probe = ProbeAsyncClient("block")
        llm = async_llm(settings, make_stub, lambda: owned_client(settings, probe))
        cancel = threading.Event()
        thread, outcome = run_ask_thread(llm, cancel)
        try:
            time.sleep(0.3)
            cancelled_at = time.monotonic()
            cancel.set()
            thread.join(5)
        finally:
            cancel.set()
            thread.join(5)
        assert not thread.is_alive(), "worker must unblock promptly, not ride the provider timeout"
        assert isinstance(outcome.get("exc"), TurnCancelled)
        assert probe.cancelled.wait(1), "the awaited HTTP task itself was cancelled"
        assert probe.aclosed.wait(1), "the owned transport was closed"
        assert probe.request_count == 1
        assert time.monotonic() - cancelled_at < 3  # far below any provider timeout

    def test_cancelling_one_turn_leaves_a_concurrent_connected_turn_alone(
        self, settings, make_stub
    ):
        probes = [ProbeAsyncClient("block"), ProbeAsyncClient("ok")]
        created = []
        first_client = threading.Event()
        counter = itertools.count()

        def factory():
            index = next(counter)  # atomic: turns never share an owned client
            created.append(index)
            if index == 0:
                first_client.set()
            return owned_client(settings, probes[index])

        llm = async_llm(settings, make_stub, factory)
        cancel_a = threading.Event()
        never = threading.Event()
        thread_a, outcome_a = run_ask_thread(llm, cancel_a)
        # Deterministic assignment: turn A owns the blocking transport
        # before turn B even starts (its ask() opened the first client).
        assert first_client.wait(2)
        thread_b, outcome_b = run_ask_thread(llm, never)
        try:
            time.sleep(0.3)
            cancel_a.set()
            thread_a.join(5)
            thread_b.join(5)
        finally:
            cancel_a.set()
            thread_a.join(5)
            never.set()
            thread_b.join(5)
        assert not thread_a.is_alive() and not thread_b.is_alive()
        # The abandoned turn died through ITS owned transport...
        assert isinstance(outcome_a.get("exc"), TurnCancelled)
        assert probes[0].cancelled.wait(1)
        assert probes[0].aclosed.wait(1)
        # ...while the connected turn completed with a full answer through
        # its own separate client, untouched by the cancellation.
        assert outcome_b.get("result", {}).get("answer") == "fine"
        assert not probes[1].cancelled.is_set()
        assert probes[1].request_count == 1
        assert len(created) == 2  # each turn owned its own client

    def test_speech_job_cancel_event_never_aborts_the_provider_transport(
        self, settings, make_stub
    ):
        """An explicit /speech cancel on a still-connected request must
        keep the full textual-answer behavior: the provider channel only
        reacts to the REQUEST-scoped disconnect event."""
        release = threading.Event()
        probe = ProbeAsyncClient("held", release)
        llm = async_llm(settings, make_stub, lambda: owned_client(settings, probe))
        job_cancel = threading.Event()  # the SPEECH job's event, not the request's
        disconnect_event = threading.Event()  # never set: client stays connected
        thread, outcome = run_ask_thread(llm, disconnect_event)
        try:
            time.sleep(0.2)
            job_cancel.set()  # the user pressed stop-voice mid-turn
            time.sleep(0.3)
            assert thread.is_alive(), "a speech cancel must not abort the provider"
            release.set()
            thread.join(5)
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive()
        assert outcome.get("result", {}).get("answer") == "fine"
        assert not probe.cancelled.is_set()
        assert probe.request_count == 1
