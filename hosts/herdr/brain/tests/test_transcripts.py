"""Unit tests for the multi-turn transcript readers.

All stores are temporary fixtures; nothing on the real machine is touched.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from herdr_brain.transcripts import (
    AntigravityTranscript,
    ClaudeTranscript,
    OpencodeTranscript,
    Turn,
    format_turns,
    read_title,
    read_transcript,
)


@pytest.fixture
def antigravity_root(tmp_path):
    """Builds an Antigravity-style session directory with transcripts."""
    session = "11112222-3333-4444-5555-666677778888"
    logs_dir = tmp_path / session / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir(parents=True)
    (annotations_dir / f"{session}.pbtxt").write_text(
        'title:"Check Build Status"\n', encoding="utf-8"
    )
    events = [
        {
            "step_index": 0,
            "type": "USER_INPUT",
            "source": "USER_EXPLICIT",
            "content": "<USER_REQUEST>\ncheck the build status\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime\n</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "tool_calls": [{"name": "run_command"}],
        },
        {
            "step_index": 2,
            "type": "GENERIC",
            "source": "MODEL",
            "content": "Command finished with exit code 0",
        },
        {
            "step_index": 3,
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "content": "The build is green and passing all tests.",
        },
    ]
    path = logs_dir / "transcript_full.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return str(tmp_path), session


@pytest.fixture
def opencode_db(tmp_path):
    """Builds a minimal OpenCode-shaped SQLite store."""
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)"
    )
    messages = [
        ("m1", "user", "2026-01-01 user question?", 100),
        ("m2", "assistant", "Working on it, one moment.", 200),
        ("m3", "user", "thanks, continue", 300),
        ("m4", "assistant", "Done: tests pass and lint is clean.", 400),
    ]
    for mid, role, text, ts in messages:
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            (mid, "ses_aaaabbbbcccc", ts, json.dumps({"role": role})),
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            (f"{mid}-p1", mid, "ses_aaaabbbbcccc", ts, json.dumps({"type": "text", "text": text})),
        )
    # Assistant m4 also has a tool part that must be filtered out.
    conn.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
        ("m4-p2", "m4", "ses_aaaabbbbcccc", 401, json.dumps({"type": "tool", "tool": "bash"})),
    )
    conn.execute(
        "INSERT INTO session VALUES (?, ?)",
        ("ses_aaaabbbbcccc", "Despliegue y pruebas"),
    )
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def claude_root(tmp_path):
    """Builds a Claude-style JSONL transcript under a munged project dir."""
    project = tmp_path / "-home-bruno-project"
    project.mkdir()
    session = "0f1e2d3c-1111-2222-3333-444455556666"
    events = [
        {"type": "system", "message": {"role": "system", "content": "boot"}},
        {"type": "user", "message": {"role": "user", "content": "fix the flaky test"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "Looking at the test now."},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "bash"},
            {"type": "text", "text": "Fixed. All green."},
        ]}},
    ]
    path = project / f"{session}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return str(tmp_path), session


class TestOpencodeTranscript:
    def test_reads_turns_in_chronological_order(self, opencode_db):
        source = OpencodeTranscript(db_path=opencode_db)
        turns = source.read("ses_aaaabbbbcccc", n_turns=10)
        assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
        assert turns[-1].text == "Done: tests pass and lint is clean."

    def test_limits_to_recent_turns(self, opencode_db):
        source = OpencodeTranscript(db_path=opencode_db)
        turns = source.read("ses_aaaabbbbcccc", n_turns=2)
        assert [t.text for t in turns] == ["thanks, continue", "Done: tests pass and lint is clean."]

    def test_rejects_wrong_session_shape(self, opencode_db):
        source = OpencodeTranscript(db_path=opencode_db)
        assert source.read("not-a-session", n_turns=5) == []

    def test_multi_part_message_joins_all_text_parts(self, tmp_path):
        """A long answer split across text parts arrives complete.

        OpenCode stores one assistant message as SEVERAL text parts; the
        old one-part-per-message read made the reading view truncated.
        Parts must reassemble in chronological order regardless of the
        order the DB happens to return them in.
        """
        db = tmp_path / "opencode.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)"
        )
        conn.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
        )
        conn.execute(
            "INSERT INTO message VALUES ('m1', 'ses_aaaabbbbcccc', 1, ?)",
            (json.dumps({"role": "assistant"}),),
        )
        # Inserted deliberately out of part order: the query's ASC part
        # ordering must win.
        for pid, ts, chunk in (
            ("m1-p3", 3, "tercer fragmento"),
            ("m1-p1", 1, "primer fragmento,"),
            ("m1-p2", 2, "segundo fragmento,"),
        ):
            conn.execute(
                "INSERT INTO part VALUES (?, 'm1', 'ses_aaaabbbbcccc', ?, ?)",
                (pid, ts, json.dumps({"type": "text", "text": chunk})),
            )
        conn.commit()
        conn.close()

        source = OpencodeTranscript(db_path=str(db))
        turns = source.read("ses_aaaabbbbcccc", n_turns=10)
        assert len(turns) == 1
        assert turns[0].role == "assistant"
        assert turns[0].text == "primer fragmento,\nsegundo fragmento,\ntercer fragmento"

    def test_missing_db_returns_empty(self, tmp_path):
        source = OpencodeTranscript(db_path=str(tmp_path / "missing.db"))
        assert source.read("ses_aaaabbbbcccc", n_turns=5) == []

    def test_reads_session_title(self, opencode_db):
        source = OpencodeTranscript(db_path=opencode_db)
        assert source.read_title("ses_aaaabbbbcccc") == "Despliegue y pruebas"

    def test_unknown_session_title_returns_none(self, opencode_db):
        source = OpencodeTranscript(db_path=opencode_db)
        assert source.read_title("ses_000000000000") is None


class TestClaudeTranscript:
    def test_reads_turns_skipping_tool_blocks(self, claude_root):
        root, session = claude_root
        source = ClaudeTranscript(root=root)
        turns = source.read(session, n_turns=10)
        assert [t.role for t in turns] == ["user", "assistant", "assistant"]
        assert turns[-1].text == "Fixed. All green."

    def test_limits_to_recent_turns(self, claude_root):
        root, session = claude_root
        turns = ClaudeTranscript(root=root).read(session, n_turns=1)
        assert turns == [Turn(role="assistant", text="Fixed. All green.")]

    def test_unknown_session_returns_empty(self, claude_root):
        root, _ = claude_root
        assert ClaudeTranscript(root=root).read("ffffffff-1111-2222-3333-444455556666", 5) == []

    def test_read_title_returns_none(self, claude_root):
        root, session = claude_root
        assert ClaudeTranscript(root=root).read_title(session) is None
class TestAntigravityTranscript:
    def test_reads_turns_in_chronological_order(self, antigravity_root):
        root, session = antigravity_root
        source = AntigravityTranscript(root=root)
        turns = source.read(session, n_turns=10)
        assert [t.role for t in turns] == ["user", "assistant"]
        assert turns[0].text == "check the build status"
        assert turns[1].text == "The build is green and passing all tests."

    def test_limits_to_recent_turns(self, antigravity_root):
        root, session = antigravity_root
        source = AntigravityTranscript(root=root)
        turns = source.read(session, n_turns=1)
        assert turns == [Turn(role="assistant", text="The build is green and passing all tests.")]

    def test_prefers_transcript_full_over_transcript(self, tmp_path):
        session = "22223333-4444-5555-6666-777788889999"
        logs_dir = tmp_path / session / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "PLANNER_RESPONSE", "content": "truncated..."}),
            encoding="utf-8",
        )
        (logs_dir / "transcript_full.jsonl").write_text(
            json.dumps({"type": "PLANNER_RESPONSE", "content": "full content here"}),
            encoding="utf-8",
        )
        turns = AntigravityTranscript(root=str(tmp_path)).read(session, n_turns=10)
        assert len(turns) == 1
        assert turns[0].text == "full content here"

    def test_falls_back_to_transcript_when_full_missing(self, tmp_path):
        session = "33334444-5555-6666-7777-888899990000"
        logs_dir = tmp_path / session / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "USER_INPUT", "content": "hello world"}),
            encoding="utf-8",
        )
        turns = AntigravityTranscript(root=str(tmp_path)).read(session, n_turns=10)
        assert len(turns) == 1
        assert turns[0].text == "hello world"

    def test_strips_metadata_blocks_from_user_input(self, tmp_path):
        session = "44445555-6666-7777-8888-999900001111"
        logs_dir = tmp_path / session / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        raw = (
            "<ADDITIONAL_METADATA>\ntime=now\n</ADDITIONAL_METADATA>\n"
            "clean message without tags\n"
            "<USER_SETTINGS_CHANGE>\nmodel=test\n</USER_SETTINGS_CHANGE>"
        )
        (logs_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "USER_INPUT", "content": raw}),
            encoding="utf-8",
        )
        turns = AntigravityTranscript(root=str(tmp_path)).read(session, n_turns=10)
        assert len(turns) == 1
        assert turns[0].text == "clean message without tags"

    def test_unknown_session_returns_empty(self, antigravity_root):
        root, _ = antigravity_root
        assert AntigravityTranscript(root=root).read("ffffffff-1111-2222-3333-444455556666", 5) == []

    def test_rejects_non_uuid_session(self, antigravity_root):
        root, _ = antigravity_root
        assert AntigravityTranscript(root=root).read("invalid_session", 5) == []

    def test_reads_session_title(self, antigravity_root):
        root, session = antigravity_root
        source = AntigravityTranscript(root=root)
        assert source.read_title(session) == "Check Build Status"

    def test_unknown_session_title_returns_none(self, antigravity_root):
        root, _ = antigravity_root
        source = AntigravityTranscript(root=root)
        assert source.read_title("ffffffff-1111-2222-3333-444455556666") is None


class TestReadTranscriptRouting:
    def test_routes_opencode_by_agent_name(self, opencode_db):
        for name in ("opencode", "oa", "OA"):
            text = read_transcript(
                name, "ses_aaaabbbbcccc", 10,
                opencode=OpencodeTranscript(db_path=opencode_db),
                claude=ClaudeTranscript(root="/nonexistent"),
            )
            assert text is not None
            assert text.startswith("user: ")

    def test_routes_claude_by_agent_name(self, claude_root):
        root, session = claude_root
        text = read_transcript(
            "claude", session, 10,
            opencode=OpencodeTranscript(db_path="/nonexistent.db"),
            claude=ClaudeTranscript(root=root),
        )
        assert "Fixed. All green." in text

    def test_routes_antigravity_by_agent_name(self, antigravity_root):
        root, session = antigravity_root
        agy_source = AntigravityTranscript(root=root)
        for agent_name in ("antigravity", "agy", "AGY"):
            text = read_transcript(
                agent_name, session, 10,
                opencode=OpencodeTranscript(db_path="/nonexistent.db"),
                claude=ClaudeTranscript(root="/nonexistent"),
                antigravity=agy_source,
            )
            assert text is not None
            assert "The build is green" in text

    def test_sniffs_opencode_by_session_shape(self, opencode_db):
        text = read_transcript(
            "unknown-agent", "ses_aaaabbbbcccc", 10,
            opencode=OpencodeTranscript(db_path=opencode_db),
            claude=ClaudeTranscript(root="/nonexistent"),
        )
        assert text is not None

    def test_sniffs_antigravity_by_session_shape(self, antigravity_root):
        root, session = antigravity_root
        text = read_transcript(
            "", session, 10,
            opencode=OpencodeTranscript(db_path="/nonexistent.db"),
            claude=ClaudeTranscript(root="/nonexistent"),
            antigravity=AntigravityTranscript(root=root),
        )
        assert text is not None
        assert "The build is green" in text

    def test_returns_none_when_store_empty(self):
        result = read_transcript(
            "opencode", "ses_zzzz", 10,
            opencode=OpencodeTranscript(db_path="/nonexistent.db"),
            claude=ClaudeTranscript(root="/nonexistent"),
        )
        assert result is None

    def test_returns_none_for_unroutable_agent_and_id(self):
        result = read_transcript(
            "aider", "plain-label", 10,
            opencode=OpencodeTranscript(db_path="/nonexistent.db"),
            claude=ClaudeTranscript(root="/nonexistent"),
        )
        assert result is None

    def test_read_title_routes_opencode(self, opencode_db):
        for name in ("opencode", "oa", "OA"):
            title = read_title(
                name, "ses_aaaabbbbcccc",
                opencode=OpencodeTranscript(db_path=opencode_db),
                claude=ClaudeTranscript(root="/nonexistent"),
            )
            assert title == "Despliegue y pruebas"

    def test_read_title_routes_antigravity(self, antigravity_root):
        root, session = antigravity_root
        agy_source = AntigravityTranscript(root=root)
        for agent_name in ("antigravity", "agy", "AGY"):
            title = read_title(
                agent_name, session,
                opencode=OpencodeTranscript(db_path="/nonexistent.db"),
                claude=ClaudeTranscript(root="/nonexistent"),
                antigravity=agy_source,
            )
            assert title == "Check Build Status"

    def test_read_title_sniffs_by_session_shape(self, opencode_db, antigravity_root):
        root, session = antigravity_root
        assert read_title(
            "", "ses_aaaabbbbcccc",
            opencode=OpencodeTranscript(db_path=opencode_db),
            claude=ClaudeTranscript(root="/nonexistent"),
            antigravity=AntigravityTranscript(root=root),
        ) == "Despliegue y pruebas"
        assert read_title(
            "", session,
            opencode=OpencodeTranscript(db_path=opencode_db),
            claude=ClaudeTranscript(root="/nonexistent"),
            antigravity=AntigravityTranscript(root=root),
        ) == "Check Build Status"

    def test_read_title_returns_none_for_unroutable_agent_and_id(self):
        assert read_title(
            "aider", "plain-label",
            opencode=OpencodeTranscript(db_path="/nonexistent.db"),
            claude=ClaudeTranscript(root="/nonexistent"),
        ) is None


class TestFormatTurns:
    def test_truncates_long_turns(self):
        long_text = "x" * 2100
        out = format_turns([Turn(role="assistant", text=long_text)])
        assert out.endswith(" […]")
        assert len(out) < 2100

    def test_joins_blocks_with_blank_lines(self):
        out = format_turns([Turn("user", "hi"), Turn("assistant", "hello")])
        assert out == "user: hi\n\nassistant: hello"
