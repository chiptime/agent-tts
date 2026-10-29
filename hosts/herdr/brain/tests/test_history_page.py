"""Paginated full-history endpoint tests (doc 05 AC2, AC3, AC7, AC8).

Stores are built programmatically in tmp_path so pagination walks a
transcript larger than any page; the HTTP surface rides a TestClient with
a stubbed herdr client, exactly like the /view contract tests. (Named
``test_history_page`` because ``test_history`` holds the call-history
store tests.)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError
from tests.conftest import SETTINGS_KWARGS


def make_agent(
    pane="w1:p1",
    agent="claude",
    value="00000000-1111-2222-3333-444444444444",
    status="working",
    focused=True,
):
    return AgentInfo(
        pane_id=pane, agent=agent, status=status, session_kind="id",
        session_value=value, cwd="/repo", title="T", focused=focused,
    )


class StubHerdr:
    def __init__(self, agents, screens=None):
        self._agents = agents
        self._screens = screens or {}
        self.screen_calls: list = []

    def list_agents(self):
        return list(self._agents)

    def active_agent(self):
        for agent in self._agents:
            if agent.focused:
                return agent
        return None

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        self.screen_calls.append(
            {"pane_id": pane_id, "n_lines": n_lines, "source": source, "ansi": as_ansi}
        )
        if isinstance(self._screens, Exception):
            raise self._screens
        screen = self._screens.get(pane_id)
        if screen is None:
            raise HerdrError("no pane")
        return screen


def build_claude_store(root: Path, session: str, n_turns: int, long_last=False) -> None:
    project = root / "-repo-"
    project.mkdir(parents=True, exist_ok=True)
    events = []
    for i in range(n_turns):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"turn {i} del historial largo"
        if long_last and i == n_turns - 1:
            text = "x" * 3_000  # far beyond the 2.000-char conversation clip
        events.append(
            {
                "type": role,
                "message": {"role": role, "content": [{"type": "text", "text": text}]},
                "timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z",
            }
        )
    (project / f"{session}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


def build_opencode_db(path: Path, session: str, n_turns: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    for i in range(n_turns):
        role = "user" if i % 2 == 0 else "assistant"
        mid = f"m{i:04d}"
        conn.execute(
            "INSERT INTO message VALUES (?,?,?,?)",
            (mid, session, i + 1, json.dumps({"role": role})),
        )
        conn.execute(
            "INSERT INTO part VALUES (?,?,?,?,?)",
            (f"{mid}-p", mid, session, i + 1,
             json.dumps({"type": "text", "text": f"opencode turn {i}"})),
        )
    conn.execute("INSERT INTO session VALUES (?,?)", (session, "Largo"))
    conn.commit()
    conn.close()


@pytest.fixture
def client_factory(settings, monkeypatch):
    def _make(agents, screens=None):
        import herdr_brain.server as server_module
        from herdr_brain.tools import BrainTools

        stub = StubHerdr(agents, screens)

        class ToolsWithStub(BrainTools):
            def __init__(self, cfg):
                super().__init__(cfg, herdr=stub)

        monkeypatch.setattr(server_module, "BrainTools", ToolsWithStub)
        cfg = Settings(**settings.__dict__)
        return TestClient(server_module.create_app(settings=cfg))

    return _make


class TestHistoryPagination:
    """Doc 05 AC3: cursor pages, full turns, exhaustion without loss."""

    SESSION = "00000000-1111-2222-3333-444444444444"

    @pytest.fixture(autouse=True)
    def _store(self, tmp_path, monkeypatch):
        build_claude_store(tmp_path, self.SESSION, 25)
        monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path))

    def test_full_turns_without_clip(self, tmp_path, monkeypatch, client_factory):
        build_claude_store(tmp_path, "99999999-1111-2222-3333-444444444444", 3, long_last=True)
        monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path))
        client = client_factory(
            [make_agent(value="99999999-1111-2222-3333-444444444444")]
        )
        body = client.get("/history").json()
        assert len(body["turns"]) == 3
        # Pages are chronological; the newest turn (the long one) is last
        # and must arrive WHOLE — the 2.000-char clip is an LLM budget.
        assert len(body["turns"][-1]["text"]) == 3_000

    def test_pages_walk_the_whole_transcript_exactly_once(self, client_factory):
        client = client_factory([make_agent()])
        collected = []
        cursor = None
        pages = 0
        while True:
            qs = f"?limit=4{('&cursor=' + cursor) if cursor else ''}"
            body = client.get("/history" + qs).json()
            assert body["reason"] is None
            collected = body["turns"] + collected  # pages are older-first
            pages += 1
            cursor = body["cursor"]
            assert body["has_more"] == (cursor is not None)
            if cursor is None:
                break
            assert pages < 20, "cursor never exhausts"
        assert len(collected) == 25
        # No turn lost, none duplicated, chronological order overall.
        assert [t["text"] for t in collected] == [f"turn {i} del historial largo" for i in range(25)]
        assert pages == 7  # 25 turns in pages of 4: 6 full + 1 partial

    def test_pages_are_chronological_inside_the_page(self, client_factory):
        client = client_factory([make_agent()])
        body = client.get("/history?limit=4").json()
        texts = [t["text"] for t in body["turns"]]
        assert texts == [f"turn {i} del historial largo" for i in range(21, 25)]

    def test_limit_is_bounded(self, client_factory):
        client = client_factory([make_agent()])
        assert len(client.get("/history?limit=0").json()["turns"]) >= 1
        body = client.get("/history?limit=99999").json()
        assert len(body["turns"]) <= 200
        assert body["limit"] <= 200

    def test_invalid_cursor_degrades_to_newest_page(self, client_factory):
        client = client_factory([make_agent()])
        body = client.get("/history?cursor=not-a-cursor").json()
        assert body["reason"] is None
        assert body["turns"][-1]["text"] == "turn 24 del historial largo"

    def test_opencode_store_paginates_by_message_not_part(self, tmp_path, monkeypatch, client_factory):
        db = tmp_path / "opencode.db"
        build_opencode_db(db, "ses_history0001", 30)
        monkeypatch.setenv("OPENCODE_DB", str(db))
        client = client_factory([make_agent(agent="opencode", value="ses_history0001")])
        total = []
        cursor = None
        while True:
            qs = f"?limit=7{('&cursor=' + cursor) if cursor else ''}"
            body = client.get("/history" + qs).json()
            total = body["turns"] + total
            cursor = body["cursor"]
            if cursor is None:
                break
        assert len(total) == 30
        assert [t["text"] for t in total] == [f"opencode turn {i}" for i in range(30)]


class TestHistoryNoSession:
    """Doc 05 AC2: an agent without a connector is a no-session, not a 500."""

    def test_agent_without_connector_answers_no_session(self, client_factory):
        client = client_factory([make_agent(agent="vim", value="whatever")])
        resp = client.get("/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["turns"] == []
        assert body["reason"] == "no-session"
        assert body["cursor"] is None
        assert body["has_more"] is False

    def test_pane_without_session_ref_answers_no_session(self, client_factory):
        client = client_factory([make_agent(value="")])
        body = client.get("/history").json()
        assert body["turns"] == []
        assert body["reason"] == "no-session"


class TestHerdRanking:
    """Doc 05 AC7: declared status ranking orders the herd."""

    def test_herd_orders_blocked_first_and_unknown_last(self, client_factory):
        panes = [
            make_agent(pane="p-idle", agent="vim", value="", status="idle", focused=False),
            make_agent(pane="p-unknown", agent="nvim", value="", status="weird", focused=False),
            make_agent(pane="p-done", agent="vim2", value="", status="done", focused=False),
            make_agent(pane="p-working", agent="vim3", value="", status="working", focused=False),
            make_agent(pane="p-blocked", agent="vim4", value="", status="blocked", focused=False),
        ]
        client = client_factory(panes)
        herd = client.get("/herd").json()
        assert [p["pane_id"] for p in herd] == [
            "p-blocked", "p-working", "p-done", "p-idle", "p-unknown",
        ]


class TestPreservedContracts:
    """Doc 05 AC8: /view and /conversation keep their documented shape."""

    @pytest.fixture(autouse=True)
    def _store(self, tmp_path, monkeypatch):
        build_claude_store(tmp_path, "00000000-1111-2222-3333-444444444444", 6)
        monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path))

    def test_view_shape_unchanged(self, client_factory):
        client = client_factory([make_agent(status="blocked")], screens={"w1:p1": "texto"})
        body = client.get("/view").json()
        assert set(body.keys()) == {
            "status", "transcript", "screen", "screen_runs", "dialog", "pending",
        }
        assert body["status"]["pane_id"] == "w1:p1"
        # Documented /view heuristic: a blocked status alone fires pending
        # detection (kind unknown) even when no marker line matched.
        assert body["pending"]["detected"] is True
        assert body["pending"]["kind"] is None

    def test_conversation_shape_and_window_unchanged(self, client_factory):
        client = client_factory([make_agent()])
        body = client.get("/conversation").json()
        assert set(body.keys()) == {
            "pane_id", "agent", "session_id", "title", "turns", "window",
        }
        assert body["window"] == 20
        assert len(body["turns"]) == 6
