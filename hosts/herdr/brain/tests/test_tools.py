"""Unit tests for the brain tool layer. Subprocess is never real here."""

from __future__ import annotations

import json

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.tools import TOOLS_SCHEMA, BrainTools


@pytest.fixture
def real_transcript(active_agent, monkeypatch, tmp_path):
    """Serves a canned transcript for the active session via OPENCODE_DB."""
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
        (active_agent.session_value, json.dumps({"type": "text", "text": "Refactoring the auth module."})),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("OPENCODE_DB", str(db))


class TestGetStatus:
    def test_returns_active_pane_fields(self, settings, make_stub, active_agent):
        tools = BrainTools(settings, herdr=make_stub())
        status = json.loads(tools.get_status())
        assert status == {
            "active": True,
            "agent": "opencode",
            "status": "working",
            "pane_id": active_agent.pane_id,
            "session_id": active_agent.session_value,
            "cwd": "/repo",
            "title": "OpenCode",
        }

    def test_no_agents(self, settings, make_stub):
        tools = BrainTools(settings, herdr=make_stub(agents=[]))
        assert json.loads(tools.get_status())["active"] is False


class TestReadTranscript:
    def test_prefers_transcript(self, settings, make_stub, active_agent, real_transcript):
        tools = BrainTools(settings, herdr=make_stub())
        out = tools.read_transcript()
        assert "Refactoring the auth module." in out
        assert "transcript unavailable" not in out
        assert tools.last_active.pane_id == active_agent.pane_id

    def test_falls_back_to_screen_when_connector_fails(self, settings, make_stub, active_agent):
        stub = make_stub(screen="visible text")
        tools = BrainTools(settings, herdr=stub)
        out = tools.read_transcript()
        assert out.startswith("[transcript unavailable")
        assert "visible text" in out
        assert stub.screen_calls == [{"pane_id": active_agent.pane_id, "n_lines": None}]

    def test_no_active_agent(self, settings, make_stub):
        tools = BrainTools(settings, herdr=make_stub(agents=[]))
        assert tools.read_transcript() == "error: no active agent pane"


class TestReadScreen:
    def test_reads_active_pane(self, settings, make_stub, active_agent):
        stub = make_stub(screen="terminal lines")
        tools = BrainTools(settings, herdr=stub)
        assert tools.read_screen(30) == "terminal lines"
        assert stub.screen_calls[0]["n_lines"] == 30

    def test_screen_failure_reported(self, settings, make_stub):
        tools = BrainTools(settings, herdr=make_stub(fail_screen=True))
        assert tools.read_screen().startswith("error reading screen")


class TestSendToSession:
    def test_forwards_to_active_pane(self, settings, make_stub, active_agent):
        stub = make_stub()
        tools = BrainTools(settings, herdr=stub)
        result = json.loads(tools.send_to_session("run the test suite\nnow"))
        assert stub.prompt_calls == [
            {"pane_id": active_agent.pane_id, "text": "run the test suite now", "timeout_ms": None}
        ]
        assert result["ok"] is True
        assert result["pane_id"] == active_agent.pane_id

    def test_blocked_prompt_reported(self, settings, make_stub):
        stub = make_stub(prompt={"ok": False, "status": "blocked", "output": "agent_blocked"})
        tools = BrainTools(settings, herdr=stub)
        result = json.loads(tools.send_to_session("hello"))
        assert result["ok"] is False
        assert result["status"] == "blocked"

    def test_herdr_error_returned_as_message(self, settings, make_stub):
        stub = make_stub()

        def boom(pane_id, text, timeout_ms=None):
            raise HerdrError("herdr exited with 1: boom")

        stub.send_prompt = boom
        tools = BrainTools(settings, herdr=stub)
        assert tools.send_to_session("x").startswith("error sending prompt")


class TestHerd:
    @staticmethod
    def _agent(pane, value, status="idle", focused=False, kind="id", title="T"):
        return AgentInfo(
            pane_id=pane, agent="opencode", status=status, session_kind=kind,
            session_value=value, cwd="/repo", title=title, focused=focused,
        )

    def test_herd_lists_all_agents_with_last_turn(self, settings, make_stub, monkeypatch, tmp_path):
        import sqlite3

        first = self._agent("w1:p1", "ses_herd0000aaa", status="working", focused=True)
        second = self._agent("w1:p2", "ses_herd0000bbb")
        db = tmp_path / "herd.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?, 1, ?)",
            ("ses_herd0000aaa", json.dumps({"role": "assistant"})),
        )
        conn.execute(
            "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
            ("ses_herd0000aaa", json.dumps({"type": "text", "text": "Trabajando en el despliegue."})),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        tools = BrainTools(settings, herdr=make_stub(agents=[first, second]))
        herd = tools.herd()
        assert [e["pane_id"] for e in herd] == ["w1:p1", "w1:p2"]
        assert herd[0]["agent_status"] == "working"
        assert herd[0]["focused"] is True
        assert herd[0]["last_turn"] == {
            "role": "assistant", "text": "Trabajando en el despliegue."
        }
        # Second agent's store does not exist: isolated failure, null turn.
        assert herd[1]["last_turn"] is None

    def test_herd_session_id_only_for_id_kind(self, settings, make_stub):
        plain = self._agent("w1:p3", "whatever", kind="none")
        tools = BrainTools(settings, herdr=make_stub(agents=[plain]))
        herd = tools.herd()
        assert herd[0]["session_id"] is None
        assert herd[0]["last_turn"] is None

    def test_herd_empty_on_list_failure(self, settings, make_stub):
        stub = make_stub(agents=[])

        def boom():
            raise HerdrError("list failed")

        stub.list_agents = boom
        assert BrainTools(settings, herdr=stub).herd() == []

    def test_last_turn_truncated(self, settings, make_stub, monkeypatch, tmp_path):
        import sqlite3

        agent = self._agent("w1:p1", "ses_herd0000ccc")
        db = tmp_path / "long.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?, 1, ?)",
            ("ses_herd0000ccc", json.dumps({"role": "assistant"})),
        )
        conn.execute(
            "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
            ("ses_herd0000ccc", json.dumps({"type": "text", "text": "w" * 500})),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        tools = BrainTools(settings, herdr=make_stub(agents=[agent]))
        turn = tools.last_turn(agent)
        assert len(turn["text"]) == 160
        assert turn["text"].endswith("...")


class TestAgentView:
    def test_composes_status_screen_and_pending(self, settings, make_stub, active_agent):
        blocked = AgentInfo(**{**active_agent.__dict__, "status": "blocked"})
        stub = make_stub(agents=[blocked], screen="Shall I deploy to prod? (y/n)")
        tools = BrainTools(settings, herdr=stub)
        view = tools.agent_view()
        assert view["status"]["agent_status"] == "blocked"
        assert view["transcript"] is None  # hermetic store: no transcript
        assert view["screen"].endswith("(y/n)")
        assert view["pending"]["detected"] is True
        assert view["pending"]["kind"] == "permission"

    def test_screen_failure_degrades(self, settings, make_stub):
        tools = BrainTools(settings, herdr=make_stub(fail_screen=True))
        view = tools.agent_view()
        assert view["screen"] is None
        assert view["pending"]["detected"] is False

    def test_transcript_tail_truncates(self, settings, make_stub, active_agent, real_transcript, monkeypatch, tmp_path):
        import sqlite3

        db = tmp_path / "long.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
        long_text = "z" * 900
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?, 1, ?)",
            (active_agent.session_value, json.dumps({"role": "assistant"})),
        )
        conn.execute(
            "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
            (active_agent.session_value, json.dumps({"type": "text", "text": long_text})),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        tools = BrainTools(settings, herdr=make_stub(screen="all good"))
        view = tools.agent_view()
        assert view["transcript"] is not None
        assert len(view["transcript"][0]["text"]) == 300
        assert view["pending"]["detected"] is False


class TestTargeting:
    @staticmethod
    def _agent(pane, value, focused=False, title="T"):
        return AgentInfo(
            pane_id=pane, agent="opencode", status="working", session_kind="id",
            session_value=value, cwd="/repo", title=title, focused=focused,
        )

    def test_resolve_target_explicit_pane(self, settings, make_stub):
        focused = self._agent("w1:p1", "ses_t0000000001", focused=True)
        other = self._agent("w1:p2", "ses_t0000000002")
        stub = make_stub(agents=[focused, other])
        tools = BrainTools(settings, herdr=stub)
        assert tools.resolve_target("w1:p2").pane_id == "w1:p2"

    def test_resolve_target_falls_back_to_focused(self, settings, make_stub, active_agent):
        stub = make_stub(agents=[active_agent])
        tools = BrainTools(settings, herdr=stub)
        assert tools.resolve_target("w9:missing").pane_id == active_agent.pane_id
        assert tools.resolve_target(None).pane_id == active_agent.pane_id

    def test_resolve_target_none_on_herdr_failure(self, settings, make_stub):
        stub = make_stub(agents=[])

        def boom():
            raise HerdrError("list failed")

        stub.list_agents = boom
        assert BrainTools(settings, herdr=stub).resolve_target("x") is None

    def test_send_to_session_honors_explicit_target(self, settings, make_stub, active_agent):
        other = self._agent("w1:p2", "ses_t0000000002")
        stub = make_stub(agents=[active_agent, other])
        tools = BrainTools(settings, herdr=stub)
        result = json.loads(tools.send_to_session("do it", target=other))
        assert stub.prompt_calls == [
            {"pane_id": "w1:p2", "text": "do it", "timeout_ms": None}
        ]
        assert result["pane_id"] == "w1:p2"

    def test_read_screen_honors_explicit_target(self, settings, make_stub, active_agent):
        other = self._agent("w1:p2", "ses_t0000000002")
        stub = make_stub(agents=[active_agent, other], screen="other screen")
        tools = BrainTools(settings, herdr=stub)
        assert tools.read_screen(30, target=other) == "other screen"
        assert stub.screen_calls == [{"pane_id": "w1:p2", "n_lines": 30}]

    def test_read_transcript_honors_explicit_target(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        import sqlite3

        other = self._agent("w1:p2", "ses_t0000000002")
        db = tmp_path / "target.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?, 1, ?)",
            (other.session_value, json.dumps({"role": "assistant"})),
        )
        conn.execute(
            "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
            (other.session_value, json.dumps({"type": "text", "text": "Pantalla del otro agente."})),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        stub = make_stub(agents=[active_agent, other])
        tools = BrainTools(settings, herdr=stub)
        out = tools.read_transcript(target=other)
        assert "Pantalla del otro agente." in out
        assert "transcript unavailable" not in out

    def test_dispatch_passes_target_through(self, settings, make_stub, active_agent):
        other = self._agent("w1:p2", "ses_t0000000002")
        stub = make_stub(agents=[active_agent, other])
        tools = BrainTools(settings, herdr=stub)
        tools.dispatch("read_screen", {"n_lines": 20}, target=other)
        assert stub.screen_calls[0]["pane_id"] == "w1:p2"

    def test_agent_view_targets_pane(self, settings, make_stub, active_agent):
        other = self._agent("w1:p2", "ses_t0000000002")
        stub = make_stub(agents=[active_agent, other], screen="tail of other")
        tools = BrainTools(settings, herdr=stub)
        view = tools.agent_view("w1:p2")
        assert view["status"]["pane_id"] == "w1:p2"
        assert stub.screen_calls == [{"pane_id": "w1:p2", "n_lines": 12}]


class TestDispatch:
    def test_unknown_tool(self, settings, make_stub):
        tools = BrainTools(settings, herdr=make_stub())
        assert tools.dispatch("delete_everything", {}) == "error: unknown tool delete_everything"

    def test_invalid_arguments(self, settings, make_stub):
        tools = BrainTools(settings, herdr=make_stub())
        out = tools.dispatch("read_transcript", {"n_turns": "many"})
        assert out.startswith("error: invalid arguments")

    def test_schema_names(self):
        names = {tool["function"]["name"] for tool in TOOLS_SCHEMA}
        assert names == {"get_status", "read_transcript", "read_screen", "send_to_session"}

    def test_descriptions_carry_cost_hints(self):
        by_name = {tool["function"]["name"]: tool["function"]["description"] for tool in TOOLS_SCHEMA}
        assert "FIRST" in by_name["read_transcript"]
        assert "Fallback" in by_name["read_screen"]
        assert "SLOW" in by_name["send_to_session"]
        assert "Rarely needed" in by_name["get_status"]
