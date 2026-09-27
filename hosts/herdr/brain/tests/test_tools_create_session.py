"""Unit tests for the create_session tool (new agent panels).

Safety: the HerdrClient is a fake — no tab, agent start or prompt ever
reaches a real herdr daemon. Readiness polling drives an injected fake
clock/sleeper, so the 40s budget never really sleeps.
"""

from __future__ import annotations

import pytest

from herdr_brain.config import Settings, load_settings
from herdr_brain.herdr import AgentInfo, HerdrError, sanitize_prompt_text
from herdr_brain.tools import (
    OPENCODE_POLL_INTERVAL_S,
    OPENCODE_READY_BUDGET_S,
    OPENCODE_WAIT_TIMEOUT_MS,
    TOOLS_SCHEMA,
    BrainTools,
    sanitize_agent_name,
)
from tests.conftest import StubHerdr

NEW_PANE = "w2:p3"
NEW_TAB = "w2:t7"


class FakeClock:
    """Readiness-loop double: time advances only when the sleeper runs."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list = []

    def clock(self) -> float:
        return self.now

    def sleeper(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class CreateStub(StubHerdr):
    """StubHerdr plus the create primitives, with failure switches.

    ``detect_after`` simulates the readiness race: the new pane's agent
    appears in ``list_agents`` only after that many misses (None = the
    very first poll sees it).
    """

    def __init__(
        self,
        fail_tab=False,
        fail_start=False,
        fail_prompt=False,
        fail_run=False,
        fail_wait=False,
        detect_after=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._fail_tab = fail_tab
        self._fail_start = fail_start
        self._fail_prompt = fail_prompt
        self._fail_run = fail_run
        self._fail_wait = fail_wait
        self._detect_after = detect_after
        self._list_calls = 0
        self.tab_calls: list = []
        self.start_calls: list = []
        self.pane_run_calls: list = []
        self.agent_wait_calls: list = []

    def create_tab(self, label, cwd=None):
        self.tab_calls.append({"label": label, "cwd": cwd})
        if self._fail_tab:
            raise HerdrError("tab boom")
        return {"tab_id": NEW_TAB, "pane_id": NEW_PANE}

    def start_agent(self, name, kind, pane_id, timeout_ms=None):
        self.start_calls.append(
            {"name": name, "kind": kind, "pane_id": pane_id, "timeout_ms": timeout_ms}
        )
        if self._fail_start:
            raise HerdrError("start boom")
        return pane_id

    def pane_run(self, pane_id, command):
        self.pane_run_calls.append({"pane_id": pane_id, "command": command})
        if self._fail_run:
            raise HerdrError("run boom")

    def agent_wait(self, pane_id, until="idle", timeout_ms=OPENCODE_WAIT_TIMEOUT_MS):
        self.agent_wait_calls.append(
            {"pane_id": pane_id, "until": until, "timeout_ms": timeout_ms}
        )
        if self._fail_wait:
            raise HerdrError("wait boom")

    def list_agents(self):
        agents = list(super().list_agents())
        if not self.pane_run_calls:
            # Before `oa` is typed the pane runs no agent: listings keep
            # the base herd, exactly like the pre-create world.
            return agents
        self._list_calls += 1
        if self._detect_after is None or self._list_calls > self._detect_after:
            agents.append(
                AgentInfo(
                    pane_id=NEW_PANE,
                    agent="opencode",
                    status="idle",
                    session_kind="",
                    session_value="",
                    cwd="",
                    title="",
                    focused=False,
                )
            )
        return agents

    def send_prompt(self, pane_id, text, timeout_ms=None):
        if self._fail_prompt:
            raise HerdrError("falla al enviar")
        return super().send_prompt(pane_id, text, timeout_ms)


@pytest.fixture
def stub(make_stub) -> CreateStub:
    return CreateStub(agents=make_stub()._agents)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


def make_tools(settings, stub, fake_clock) -> BrainTools:
    return BrainTools(
        settings, herdr=stub, clock=fake_clock.clock, sleeper=fake_clock.sleeper
    )


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
    def test_happy_path_with_task(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Refactor del login", "corre los tests")
        assert out == (
            "Panel creado: tab=w2:t7 pane=w2:p3 agente=refactor-del-login "
            "(opencode). Tarea entregada."
        )
        assert stub.tab_calls == [{"label": "Refactor del login", "cwd": None}]
        assert stub.pane_run_calls == [{"pane_id": "w2:p3", "command": "oa"}]
        assert stub.agent_wait_calls == [
            {"pane_id": "w2:p3", "until": "idle", "timeout_ms": OPENCODE_WAIT_TIMEOUT_MS}
        ]
        assert stub.start_calls == []
        # Same delivery-guarantee seam as send_to_session: sanitized text
        # against the NEW pane.
        assert stub.prompt_calls == [
            {"pane_id": "w2:p3", "text": "corre los tests", "timeout_ms": None}
        ]
        # last_active now points at the new panel.
        assert tools.last_active.pane_id == "w2:p3"
        assert tools.last_active.agent == "refactor-del-login"

    def test_without_task_delivers_nothing(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("claude", "Docs")
        assert out == "Panel creado: tab=w2:t7 pane=w2:p3 agente=docs (claude)."
        assert stub.prompt_calls == []
        assert tools.last_active.pane_id == "w2:p3"

    def test_non_opencode_kind_starts_agent_unchanged(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.create_session("claude", "Docs")
        assert stub.start_calls == [
            {"name": "docs", "kind": "claude", "pane_id": "w2:p3", "timeout_ms": None}
        ]
        assert stub.pane_run_calls == []
        assert stub.agent_wait_calls == []
        assert stub.tab_calls == [{"label": "Docs", "cwd": None}]

    def test_opencode_cwd_scopes_tab_and_oa_command(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.create_session("opencode", "Build", cwd="/repo")
        assert stub.tab_calls == [{"label": "Build", "cwd": "/repo"}]
        assert stub.pane_run_calls == [{"pane_id": "w2:p3", "command": "oa /repo"}]

    def test_opencode_without_cwd_types_bare_oa(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.create_session("opencode", "Build")
        assert stub.tab_calls == [{"label": "Build", "cwd": None}]
        assert stub.pane_run_calls == [{"pane_id": "w2:p3", "command": "oa"}]

    def test_opencode_cwd_with_space_is_shell_quoted(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.create_session("opencode", "Build", cwd="/my repo")
        assert stub.pane_run_calls[0]["command"] == "oa '/my repo'"

    def test_cwd_is_stripped_before_use(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.create_session("opencode", "Build", cwd="  /repo  ")
        assert stub.tab_calls == [{"label": "Build", "cwd": "/repo"}]
        assert stub.pane_run_calls[0]["command"].endswith("/repo")

    def test_readiness_polls_until_the_pane_appears(self, settings, stub, fake_clock):
        stub._detect_after = 2  # two misses, third poll sees the agent
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build", "task")
        assert out.endswith("Tarea entregada.")
        assert stub._list_calls == 3
        assert fake_clock.sleeps == [OPENCODE_POLL_INTERVAL_S] * 2

    def test_readiness_exhaustion_fails_with_ids(self, settings, stub, fake_clock):
        stub._detect_after = 10_000  # never appears within the budget
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build", "task")
        assert out == (
            f"error launching opencode: no agent appeared on pane {NEW_PANE} "
            f"within {OPENCODE_READY_BUDGET_S:.0f}s "
            f"(tab {NEW_TAB} pane {NEW_PANE} were created)"
        )
        assert stub.prompt_calls == []
        assert stub.agent_wait_calls == []
        # Bounded loop: ~40s budget at 1.5s cadence, no real sleeping.
        assert len(fake_clock.sleeps) == 26

    def test_pane_run_failure_reports_ids(self, settings, stub, fake_clock):
        stub._fail_run = True
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build")
        assert out == (
            f"error launching opencode: run boom "
            f"(tab {NEW_TAB} pane {NEW_PANE} were created)"
        )
        assert stub.agent_wait_calls == []
        assert stub.prompt_calls == []

    def test_wait_failure_is_tolerated(self, settings, stub, fake_clock):
        stub._fail_wait = True
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build", "task")
        assert out.endswith("Tarea entregada.")
        assert stub.agent_wait_calls  # it WAS attempted

    def test_task_send_failure_still_returns_ids(self, settings, stub, fake_clock):
        stub._fail_prompt = True
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build", "falla al enviar")
        assert out.startswith("Panel creado: tab=w2:t7 pane=w2:p3 agente=build.")
        assert "Fallo al enviar la tarea: falla al enviar" in out
        assert "send_to_session" in out
        # The panel itself was created and tracked despite the failure.
        assert stub.pane_run_calls and tools.last_active.pane_id == "w2:p3"

    def test_blocked_prompt_reports_failure_with_ids(self, settings, stub, fake_clock):
        stub._prompt = {"ok": False, "status": "blocked", "output": "agent_blocked"}
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build", "task")
        assert out.startswith("Panel creado: tab=w2:t7 pane=w2:p3 agente=build.")
        assert "agent_blocked" in out

    def test_timeout_status_counts_as_delivered(self, settings, stub, fake_clock):
        stub._prompt = {"ok": False, "status": "timeout", "output": "still running"}
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("opencode", "Build", "task")
        assert out.endswith("Tarea entregada.")

    def test_tab_failure_reports_error(self, settings, stub, fake_clock):
        stub._fail_tab = True
        tools = make_tools(settings, stub, fake_clock)
        assert tools.create_session("opencode", "Build") == "error creating tab: tab boom"
        assert stub.pane_run_calls == []
        assert stub.start_calls == []

    def test_start_failure_keeps_tab_id(self, settings, stub, fake_clock):
        stub._fail_start = True
        tools = make_tools(settings, stub, fake_clock)
        out = tools.create_session("claude", "Build")
        assert out == "error starting agent: start boom (tab w2:t7 was created)"

    def test_requires_kind_and_title(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        assert tools.create_session("", "T") == "error: agent_kind and title are required"
        assert tools.create_session("opencode", "  ") == "error: agent_kind and title are required"
        assert stub.tab_calls == []


class TestDispatch:
    def test_dispatches_create_session_with_unpacked_args(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        out = tools.dispatch(
            "create_session",
            {"agent_kind": "opencode", "title": "Refactor", "task": "corre lint"},
        )
        assert "Panel creado" in out and "Tarea entregada." in out

    def test_dispatch_forwards_cwd(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.dispatch(
            "create_session",
            {"agent_kind": "opencode", "title": "Build", "cwd": "/repo"},
        )
        assert stub.tab_calls == [{"label": "Build", "cwd": "/repo"}]
        assert stub.pane_run_calls == [{"pane_id": "w2:p3", "command": "oa /repo"}]

    def test_dispatch_rejects_unknown_tool_unchanged(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        assert tools.dispatch("nope", {}) == "error: unknown tool nope"


class TestSchema:
    def test_schema_entry_shape(self):
        entry = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "create_session")
        params = entry["function"]["parameters"]
        assert params["required"] == ["agent_kind", "title"]
        assert set(params["properties"]) == {"agent_kind", "title", "task", "cwd"}
        assert params["properties"]["task"]["type"] == "string"
        assert params["properties"]["cwd"]["type"] == "string"

    def test_schema_description_documents_oa_entry_point(self):
        entry = next(t for t in TOOLS_SCHEMA if t["function"]["name"] == "create_session")
        description = entry["function"]["description"]
        assert "`oa`" in description
        assert "opencode attach" in description
        assert "persistent opencode server" in description
        assert "cwd scopes both the tab" in description


class TestPromptSanity:
    def test_task_is_sanitized_like_send_to_session(self, settings, stub, fake_clock):
        tools = make_tools(settings, stub, fake_clock)
        tools.create_session("opencode", "Build", "línea uno\nlínea dos")
        assert stub.prompt_calls[0]["text"] == sanitize_prompt_text("línea uno\nlínea dos")


class TestNoAttachUrlKnob:
    """The duplicated attach URL knob is gone: `oa` owns that knowledge."""

    def test_settings_has_no_attach_url_field(self):
        from dataclasses import fields

        from tests.conftest import SETTINGS_KWARGS

        assert "opencode_attach_url" not in {f.name for f in fields(Settings)}
        # Constructing without the old knob still works (it did before T4
        # too) and load_settings ignores its env variable entirely.
        assert load_settings({"HERDR_BRAIN_OPENCODE_ATTACH_URL": "http://x:1"}).__class__ is Settings

    def test_no_attach_url_reference_remains(self):
        import inspect

        import herdr_brain.config as config_module
        import herdr_brain.tools as tools_module

        assert "opencode_attach_url" not in inspect.getsource(config_module)
        assert "opencode_attach_url" not in inspect.getsource(tools_module)
