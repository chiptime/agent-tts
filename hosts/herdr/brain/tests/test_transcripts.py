"""Unit tests for the multi-turn transcript readers.

All stores are temporary fixtures; nothing on the real machine is touched.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from herdr_brain.transcripts import (
    ClaudeTranscript,
    OpencodeTranscript,
    Turn,
    format_turns,
    read_transcript,
)


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

    def test_missing_db_returns_empty(self, tmp_path):
        source = OpencodeTranscript(db_path=str(tmp_path / "missing.db"))
        assert source.read("ses_aaaabbbbcccc", n_turns=5) == []


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


class TestReadTranscriptRouting:
    def test_routes_opencode_by_agent_name(self, opencode_db):
        text = read_transcript(
            "opencode", "ses_aaaabbbbcccc", 10,
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

    def test_sniffs_opencode_by_session_shape(self, opencode_db):
        text = read_transcript(
            "unknown-agent", "ses_aaaabbbbcccc", 10,
            opencode=OpencodeTranscript(db_path=opencode_db),
            claude=ClaudeTranscript(root="/nonexistent"),
        )
        assert text is not None

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


class TestFormatTurns:
    def test_truncates_long_turns(self):
        long_text = "x" * 2100
        out = format_turns([Turn(role="assistant", text=long_text)])
        assert out.endswith(" […]")
        assert len(out) < 2100

    def test_joins_blocks_with_blank_lines(self):
        out = format_turns([Turn("user", "hi"), Turn("assistant", "hello")])
        assert out == "user: hi\n\nassistant: hello"
