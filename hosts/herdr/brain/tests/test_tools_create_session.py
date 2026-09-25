"""Unit tests for the create_session tool (new agent panels).

Safety: the HerdrClient is a fake — no tab, agent start or prompt ever
reaches a real herdr daemon.
"""

from __future__ import annotations

import json

import pytest

from herdr_brain.herdr import HerdrError, sanitize_prompt_text
from herdr_brain.tools import TOOLS_SCHEMA, BrainTools, sanitize_agent_name
from tests.conftest import StubHerdr


class CreateStub(StubHerdr):
    """StubHerdr plus the create primitives, with failure switches."""

    def __init__(self, fail_tab=False, fail_start=False, fail_prompt=False, **kwargs):
        super().__init__(**kwargs)
        self._fail_tab = fail_tab
        self._fail_start = fail_start
        self._fail_prompt = fail_prompt
        self.tab_calls: list = []
        self.start_calls: list = []

    def create_tab(self, label, cwd=None):
        self.tab_calls.append({"label": label, "cwd": cwd})
        if self._fail_tab:
            raise HerdrError("tab boom")
        return {"tab_id": "w2:t7", "pane_id": "w2:p3"}

    def start_agent(self, name, kind, pane_id, timeout_ms=None):
        self.start_calls.append(
            {"name": name, "kind": kind, "pane_id": pane_id, "timeout_ms": timeout_ms}
        )
        if self._fail_start:
            raise HerdrError("start boom")
        return pane_id

    def send_prompt(self, pane_id, text, timeout_ms=None):
        if self._fail_prompt:
            raise HerdrError("falla al enviar")
        return super().send_prompt(pane_id, text, timeout_ms)


@pytest.fixture
def stub(make_stub) -> CreateStub:
    return CreateStub(agents=make_stub()._agents)


class TestSanitizeAgentName:
    def test_slugs_and_truncates(self):
        assert sanitize_agent_name("Refactor del Login!") == "refactor-del-login"
        assert sanitize_agent_name("  --Multi--Espacio--  ") == "multi-espacio"
        long = "a" * 40
        assert sanitize_agent_name(long) == "a" * 24

    def test_falls_back_when_nothing_survives(self):
        assert sanitize_agent_name("¡¿¡?!") == "agente"
        assert sanitize_agent_name("") == "agente"


class TestCreateSession:
    def test_happy_path_with_task(self, settings, stub):
        tools = BrainTools(settings, herdr=stub)
        out = tools.create_session("opencode", "Refactor del login", "corre los tests")
        assert out == (
            "Panel creado: tab=w2:t7 pane=w2:p3 agente=refactor-del-login "
            "(opencode). Tarea entregada."
        )
        assert stub.tab_calls == [{"label": "Refactor del login", "cwd": None}]
        assert stub.start_calls == [
            {"name": "refactor-del-login", "kind": "opencode", "pane_id": "w2:p3", "timeout_ms": None}
        ]
        # Same delivery-guarantee seam as send_to_session: sanitized text
        # against the NEW pane.
        assert stub.prompt_calls == [
            {"pane_id": "w2:p3", "text": "corre los tests", "timeout_ms": None}
        ]
        # last_active now points at the new panel.
        assert tools.last_active.pane_id == "w2:p3"
        assert tools.last_active.agent == "refactor-del-login"

    def test_without_task_delivers_nothing(self, settings, stub):
        tools = BrainTools(settings, herdr=stub)
        out = tools.create_session("claude", "Docs")
        assert out == "Panel creado: tab=w2:t7 pane=w2:p3 agente=docs (claude)."
        assert stub.prompt_calls == []
        assert tools.last_active.pane_id == "w2:p3"

    def test_task_send_failure_still_returns_ids(self, settings, stub):
        stub._fail_prompt = True
        tools = BrainTools(settings, herdr=stub)
        out = tools.create_session("opencode", "Build", "falla al enviar")
        assert out.startswith("Panel creado: tab=w2:t7 pane=w2:p3 agente=build.")
        assert "Fallo al enviar la tarea: falla al enviar" in out
        assert "send_to_session" in out
        # The panel itself was created and tracked despite the failure.
        assert stub.start_calls and tools.last_active.pane_id == "w2:p3"

    def test_blocked_prompt_reports_failure_with_ids(self, settings, stub):
        stub._prompt = {"ok": False, "status": "blocked", "output": "agent_blocked"}
        tools = BrainTools(settings, herdr=stub)
        out = tools.create_session("opencode", "Build", "task")
        assert out.startswith("Panel creado: tab=w2:t7 pane=w2:p3 agente=build.")
        assert "agent_blocked" in out

    def test_timeout_status_counts_as_delivered(self, settings, stub):
        stub._prompt = {"ok": False, "status": "timeout", "output": "still running"}
        tools = BrainTools(settings, herdr=stub)
        out = tools.create_session("opencode", "Build", "task")
        assert out.endswith("Tarea entregada.")

    def test_tab_failure_reports_error(self, settings, stub):
        stub._fail_tab = True
        tools = BrainTools(settings, herdr=stub)
        assert tools.create_session("opencode", "Build") == "error creating tab: tab boom"
        assert stub.start_calls == []

    def test_start_failure_keeps_tab_id(self, settings, stub):
        stub._fail_start = True
        tools = BrainTools(settings, herdr=stub)
        out = tools.create_session("opencode", "Build")
        assert out == "error starting agent: start boom (tab w2:t7 was created)"

    def test_requires_kind_and_title(self, settings, stub):
        tools = BrainTools(settings, herdr=stub)
        assert tools.create_session("", "T") == "error: agent_kind and title are required"
        assert tools.create_session("opencode", "  ") == "error: agent_kind and title are required"
        assert stub.tab_calls == []


class TestDispatch:
    def test_dispatches_create_session_with_unpacked_args(self, settings, stub):
        tools = BrainTools(settings, herdr=stub)
        out = tools.dispatch(
            "create_session",
            {"agent_kind": "opencode", "title": "Refactor", "task": "corre lint"},
        )
        assert "Panel creado" in out and "Tarea entregada." in out

    def test_dispatch_rejects_unknown_tool_unchanged(self, settings, stub):
        tools = BrainTools(settings, herdr=stub)
        assert tools.dispatch("nope", {}) == "error: unknown tool nope"


class TestSchema:
    def test_schema_entry_shape(self):
        entry = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "create_session")
        params = entry["function"]["parameters"]
        assert params["required"] == ["agent_kind", "title"]
        assert set(params["properties"]) == {"agent_kind", "title", "task"}
        assert params["properties"]["task"]["type"] == "string"


class TestPromptSanity:
    def test_task_is_sanitized_like_send_to_session(self, settings, stub):
        tools = BrainTools(settings, herdr=stub)
        tools.create_session("opencode", "Build", "línea uno\nlínea dos")
        assert stub.prompt_calls[0]["text"] == sanitize_prompt_text("línea uno\nlínea dos")
