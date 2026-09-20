"""Unit tests for the brain tool layer. Subprocess is never real here."""

from __future__ import annotations

import json

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError, HerdrClient, sanitize_prompt_text
from herdr_brain.tools import TOOLS_SCHEMA, BrainTools


class StubHerdr:
    """HerdrClient test double: canned agents, screen and prompt results."""

    def __init__(self, agents=None, screen="screen body", prompt=None, fail_screen=False):
        self._agents = agents
        self._screen = screen
        self._prompt = prompt or {"ok": True, "status": "done", "output": "did it"}
        self._fail_screen = fail_screen
        self.screen_calls: list = []
        self.prompt_calls: list = []

    def active_agent(self):
        return self._agents[0] if self._agents else None

    def read_screen(self, pane_id, n_lines=None):
        self.screen_calls.append({"pane_id": pane_id, "n_lines": n_lines})
        if self._fail_screen:
            raise HerdrError("read failed")
        return self._screen

    def send_prompt(self, pane_id, text, timeout_ms=None):
        self.prompt_calls.append(
            {"pane_id": pane_id, "text": sanitize_prompt_text(text), "timeout_ms": timeout_ms}
        )
        return self._prompt


@pytest.fixture
def active_opencode():
    return AgentInfo(
        pane_id="w1:p2",
        agent="opencode",
        status="working",
        session_kind="id",
        session_value="ses_test0000session",
        cwd="/repo",
        title="OpenCode",
        focused=True,
    )


@pytest.fixture
def real_transcript(active_opencode, monkeypatch, tmp_path):
    """Serves a canned transcript for the active session via OPENCODE_DB."""
    import sqlite3

    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
    conn.execute(
        "INSERT INTO message VALUES ('m1', ?, 1, ?)",
        (active_opencode.session_value, json.dumps({"role": "assistant"})),
    )
    conn.execute(
        "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
        (active_opencode.session_value, json.dumps({"type": "text", "text": "Refactoring the auth module."})),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("OPENCODE_DB", str(db))


class TestGetStatus:
    def test_returns_active_pane_fields(self, settings: Settings, active_opencode):
        tools = BrainTools(settings, herdr=StubHerdr(agents=[active_opencode]))
        status = json.loads(tools.get_status())
        assert status == {
            "active": True,
            "agent": "opencode",
            "status": "working",
            "pane_id": "w1:p2",
            "session_id": active_opencode.session_value,
            "cwd": "/repo",
            "title": "OpenCode",
        }

    def test_no_agents(self, settings: Settings):
        tools = BrainTools(settings, herdr=StubHerdr(agents=[]))
        assert json.loads(tools.get_status())["active"] is False


class TestReadTranscript:
    def test_prefers_transcript(self, settings, active_opencode, real_transcript):
        tools = BrainTools(settings, herdr=StubHerdr(agents=[active_opencode]))
        out = tools.read_transcript()
        assert "Refactoring the auth module." in out
        assert "transcript unavailable" not in out
        assert tools.last_active.pane_id == "w1:p2"

    def test_falls_back_to_screen_when_connector_fails(self, settings, active_opencode):
        stub = StubHerdr(agents=[active_opencode], screen="visible text")
        tools = BrainTools(settings, herdr=stub)
        out = tools.read_transcript()
        assert out.startswith("[transcript unavailable")
        assert "visible text" in out
        assert stub.screen_calls == [{"pane_id": "w1:p2", "n_lines": None}]

    def test_no_active_agent(self, settings):
        tools = BrainTools(settings, herdr=StubHerdr(agents=[]))
        assert tools.read_transcript() == "error: no active agent pane"


class TestReadScreen:
    def test_reads_active_pane(self, settings, active_opencode):
        stub = StubHerdr(agents=[active_opencode], screen="terminal lines")
        tools = BrainTools(settings, herdr=stub)
        assert tools.read_screen(30) == "terminal lines"
        assert stub.screen_calls[0]["n_lines"] == 30

    def test_screen_failure_reported(self, settings, active_opencode):
        tools = BrainTools(settings, herdr=StubHerdr(agents=[active_opencode], fail_screen=True))
        assert tools.read_screen().startswith("error reading screen")


class TestSendToSession:
    def test_forwards_to_active_pane(self, settings, active_opencode):
        stub = StubHerdr(agents=[active_opencode])
        tools = BrainTools(settings, herdr=stub)
        result = json.loads(tools.send_to_session("run the test suite\nnow"))
        assert stub.prompt_calls == [
            {"pane_id": "w1:p2", "text": "run the test suite now", "timeout_ms": None}
        ]
        assert result["ok"] is True
        assert result["pane_id"] == "w1:p2"

    def test_blocked_prompt_reported(self, settings, active_opencode):
        stub = StubHerdr(
            agents=[active_opencode],
            prompt={"ok": False, "status": "blocked", "output": "agent_blocked"},
        )
        tools = BrainTools(settings, herdr=stub)
        result = json.loads(tools.send_to_session("hello"))
        assert result["ok"] is False
        assert result["status"] == "blocked"

    def test_herdr_error_returned_as_message(self, settings, active_opencode, monkeypatch):
        stub = StubHerdr(agents=[active_opencode])

        def boom(pane_id, text, timeout_ms=None):
            raise HerdrError("herdr exited with 1: boom")

        stub.send_prompt = boom
        tools = BrainTools(settings, herdr=stub)
        assert tools.send_to_session("x").startswith("error sending prompt")


class TestDispatch:
    def test_unknown_tool(self, settings):
        tools = BrainTools(settings, herdr=StubHerdr())
        assert tools.dispatch("delete_everything", {}) == "error: unknown tool delete_everything"

    def test_invalid_arguments(self, settings):
        tools = BrainTools(settings, herdr=StubHerdr())
        out = tools.dispatch("read_transcript", {"n_turns": "many"})
        assert out.startswith("error: invalid arguments")

    def test_schema_names(self):
        names = {tool["function"]["name"] for tool in TOOLS_SCHEMA}
        assert names == {"get_status", "read_transcript", "read_screen", "send_to_session"}
