"""Tests for the read-only prompt-cache measurement report (HT-19 F2b).

All fixtures are synthetic: tiny JSONL transcripts / SQLite databases built
in tmp_path with the exact production schemas. The developer's real agent
artifacts are never read.
"""

import io
import json
import os
import sqlite3
import sys

import pytest

from agent_tts.cache_probe import handle_cache_report_command

CLAUDE_SESSION = "3f2a9c1e-77b4-4d0e-9a51-2b6c8d9e0f12"
CLAUDE_OTHER = "aaaaaaaa-1111-2222-3333-444444444444"
OPENCODE_SESSION = "ses_abc123"
CODEX_SESSION = "01a018c2-1e32-71b3-82d1-cfc4f5f07dd3"
ANTIGRAVITY_SESSION = "c738bf00-0e3f-4031-9568-9220d6fb4192"


def run_report(argv, **kwargs):
    """Runs the cache-report command capturing both streams; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    code = handle_cache_report_command(argv, out=out, err=err, **kwargs)
    return code, out.getvalue(), err.getvalue()


# ─── Claude Code JSONL fixtures ──────────────────────────────


def _write_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


def _claude_assistant(ts, usage, message_id="msg_A", text="ok"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "requestId": message_id,
        "message": {
            "id": message_id,
            "content": [{"type": "text", "text": text}],
            "usage": usage,
        },
    }


def _claude_turn_usage(cached, written, input_tokens=2):
    return {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": written,
        "output_tokens": 100,
    }


def claude_cache_fixture(tmp_path):
    """Five requests: miss, two hits, then two misses after long gaps.

    Expected: 5 requests, 2 hits (40.0%), observed TTL window between
    412.0s (largest hit gap) and 1880.0s (smallest miss gap).
    """
    root = tmp_path / "projects"
    events = [
        {"type": "user", "message": {"content": "arregla el bug"}},
        _claude_assistant("2026-11-10T01:00:00.000Z", _claude_turn_usage(0, 20000, 4), "msg_1"),
        # One API response split into two stream-chunk lines (same message id,
        # identical usage): must count as ONE request.
        _claude_assistant("2026-11-10T01:00:03.500Z", _claude_turn_usage(20000, 1500), "msg_2"),
        _claude_assistant("2026-11-10T01:00:04.100Z", _claude_turn_usage(20000, 1500), "msg_2"),
        {"type": "cost-state", "total_cost_usd": 0.42},
        _claude_assistant("2026-11-10T01:06:55.500Z", _claude_turn_usage(21500, 900), "msg_3"),
        _claude_assistant("2026-11-10T01:38:35.500Z", _claude_turn_usage(0, 23000, 23000), "msg_4"),
        _claude_assistant("2026-11-10T02:09:55.500Z", _claude_turn_usage(0, 24000, 24000), "msg_5"),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    return root


def test_claude_report_gaps_hits_and_ttl(tmp_path):
    root = claude_cache_fixture(tmp_path)
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 0, err
    assert f"cache report: claude session {CLAUDE_SESSION}" in out
    # First turn has no gap; chunk duplicates collapse into one request.
    assert "gap=-" in out
    assert "gap=3.5s" in out
    assert "gap=412.0s" in out
    assert "gap=1900.0s" in out
    assert "gap=1880.0s" in out
    assert out.count("HIT") >= 2
    assert "cached=20000" in out
    assert "written=1500" in out
    assert "requests: 5  hits: 2 (40.0%)" in out
    assert "largest gap with a hit 412.0s" in out
    assert "smallest gap with a miss 1880.0s" in out
    assert "TTL between 412.0s and 1880.0s" in out


def test_claude_ttl_overlap_is_reported_inconclusive(tmp_path):
    root = tmp_path / "projects"
    events = [
        _claude_assistant("2026-11-10T01:00:00.000Z", _claude_turn_usage(0, 100, 100), "msg_1"),
        _claude_assistant("2026-11-10T01:10:00.000Z", _claude_turn_usage(500, 100), "msg_2"),
        _claude_assistant("2026-11-10T01:12:00.000Z", _claude_turn_usage(0, 700, 700), "msg_3"),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 0, err
    # A hit at 600s but a miss at 120s: overlapping gaps, no window.
    assert "inconclusive" in out
    assert "hit at 600.0s" in out
    assert "miss at 120.0s" in out


def test_claude_session_without_usage_is_not_measurable(tmp_path):
    root = tmp_path / "projects"
    events = [
        {
            "type": "assistant",
            "timestamp": "2026-11-10T01:00:00.000Z",
            "message": {"content": [{"type": "text", "text": "respuesta"}]},
        },
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 0, err
    assert "not measurable for claude" in out


def test_claude_empty_session_errors(tmp_path):
    root = tmp_path / "projects"
    events = [{"type": "user", "message": {"content": "solo pregunta"}}]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc != 0
    assert "no assistant" in err


def test_missing_session_errors(tmp_path):
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION],
        claude_root=str(tmp_path / "empty"),
    )
    assert rc != 0
    assert "no transcript found" in err


def test_limit_shows_last_turns_but_summarizes_all(tmp_path):
    root = claude_cache_fixture(tmp_path)
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION, "--limit", "2"],
        claude_root=str(root),
    )
    assert rc == 0, err
    assert "showing last 2 of 5 turns" in out
    assert "gap=1900.0s" in out  # turn 4 shown
    assert "gap=3.5s" not in out  # turn 2 truncated away
    assert "requests: 5  hits: 2 (40.0%)" in out


def test_json_output_shape(tmp_path):
    root = claude_cache_fixture(tmp_path)
    rc, out, err = run_report(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION, "--json"],
        claude_root=str(root),
    )
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["agent"] == "claude"
    assert payload["session_id"] == CLAUDE_SESSION
    assert payload["measurable"] is True
    assert len(payload["turns"]) == 5
    assert payload["turns"][0]["gap_s"] is None
    assert payload["turns"][1]["gap_s"] == 3.5
    assert payload["turns"][1]["cached_tokens"] == 20000
    assert payload["turns"][1]["hit"] is True
    assert payload["turns"][3]["hit"] is False
    summary = payload["summary"]
    assert summary["requests"] == 5
    assert summary["hits"] == 2
    assert summary["hit_ratio"] == 0.4
    assert summary["max_hit_gap_s"] == 412.0
    assert summary["min_miss_gap_s"] == 1880.0


def test_latest_claude_session_is_picked_by_default(tmp_path):
    root = tmp_path / "projects"
    _write_jsonl(
        root / "-home-a" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_assistant("2026-11-10T01:00:00.000Z", _claude_turn_usage(0, 100, 4), "msg_1")],
    )
    _write_jsonl(
        root / "-home-b" / f"{CLAUDE_OTHER}.jsonl",
        [_claude_assistant("2026-11-11T01:00:00.000Z", _claude_turn_usage(10, 100, 4), "msg_9")],
    )
    older = root / "-home-a" / f"{CLAUDE_SESSION}.jsonl"
    newer = root / "-home-b" / f"{CLAUDE_OTHER}.jsonl"
    os.utime(older, (1000000, 1000000))
    os.utime(newer, (2000000, 2000000))
    rc, out, err = run_report(["--agent", "claude"], claude_root=str(root))
    assert rc == 0, err
    assert f"session {CLAUDE_OTHER}" in out
    assert f"session {CLAUDE_SESSION}" not in out


# ─── OpenCode SQLite fixtures ────────────────────────────────


def opencode_cache_fixture(tmp_path):
    """Production-shaped message table; includes session filtering traps."""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
        """
    )
    rows = [
        ("msg_a1", OPENCODE_SESSION, 1_000_000, {"role": "assistant", "tokens": {
            "input": 4, "output": 100, "cache": {"read": 0, "write": 20000}}}),
        ("msg_a2", OPENCODE_SESSION, 1_003_500, {"role": "assistant", "tokens": {
            "input": 2, "output": 100, "cache": {"read": 20000, "write": 100}}}),
        # Synthetic no-op row (all counters zero): no API usage evidence.
        ("msg_z0", OPENCODE_SESSION, 1_004_000, {"role": "assistant", "tokens": {
            "input": 0, "output": 0, "cache": {"read": 0, "write": 0}}}),
        # Another session's row: must never leak into this report (older than
        # this session's newest row so the latest-session default stays here).
        ("msg_o1", "ses_other99", 900_500, {"role": "assistant", "tokens": {
            "input": 9, "output": 9, "cache": {"read": 9, "write": 9}}}),
        # A user row in the same session: not a request.
        ("msg_u1", OPENCODE_SESSION, 1_006_000, {"role": "user"}),
    ]
    conn.executemany(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        [(i, s, t, json.dumps(d)) for i, s, t, d in rows],
    )
    conn.commit()
    conn.close()
    return db_path


def test_opencode_report_reads_cache_tokens(tmp_path):
    db = opencode_cache_fixture(tmp_path)
    rc, out, err = run_report(
        ["--agent", "opencode", "--session-id", OPENCODE_SESSION], opencode_db=str(db)
    )
    assert rc == 0, err
    assert f"cache report: opencode session {OPENCODE_SESSION}" in out
    assert "gap=-" in out
    assert "gap=3.5s" in out
    assert "cached=20000" in out
    assert "written=100" in out
    # 2 real requests, 1 hit; the only miss is the first turn (no gap).
    assert "requests: 2  hits: 1 (50.0%)" in out
    assert "no miss observed" in out
    assert "TTL is at least 3.5s" in out
    # The other session's row and the zero row never appear.
    assert "cached=9" not in out


def test_opencode_rows_without_tokens_are_not_measurable(tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO message VALUES ('m1', ?, 1000, ?)",
        (OPENCODE_SESSION, json.dumps({"role": "assistant"})),
    )
    conn.commit()
    conn.close()
    rc, out, err = run_report(
        ["--agent", "opencode", "--session-id", OPENCODE_SESSION], opencode_db=str(db_path)
    )
    assert rc == 0, err
    assert "not measurable for opencode" in out


def test_opencode_latest_session_default(tmp_path):
    db = opencode_cache_fixture(tmp_path)
    rc, out, err = run_report(["--agent", "opencode"], opencode_db=str(db))
    # The newest assistant row belongs to the measured session (msg_a2 at
    # 1_003_500 beats msg_u1 because user rows are not assistant rows).
    assert rc == 0, err
    assert f"session {OPENCODE_SESSION}" in out


# ─── Codex rollout fixtures ──────────────────────────────────


def test_codex_dedupes_repeated_token_counts(tmp_path):
    root = tmp_path / "sessions" / "2026" / "11" / "10"
    events = [
        {"timestamp": "2026-11-10T01:00:00.000Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 28180, "cached_input_tokens": 5504, "output_tokens": 145}}}},
        {"timestamp": "2026-11-10T01:03:22.000Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 29516, "cached_input_tokens": 28032, "output_tokens": 27}}}},
        # Same response re-reported 300 ms later: duplicate, not a request.
        {"timestamp": "2026-11-10T01:03:22.300Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 29516, "cached_input_tokens": 28032, "output_tokens": 27}}}},
        {"timestamp": "2026-11-10T01:10:06.000Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 30000, "cached_input_tokens": 0, "output_tokens": 50}}}},
    ]
    _write_jsonl(
        root / f"rollout-2026-11-10T01-00-00-{CODEX_SESSION}.jsonl", events
    )
    rc, out, err = run_report(
        ["--agent", "codex", "--session-id", CODEX_SESSION], codex_root=str(tmp_path / "sessions")
    )
    assert rc == 0, err
    assert "requests: 3" in out
    assert "gap=202.0s" in out
    assert "gap=404.0s" in out
    assert "cached=28032" in out
    assert "TTL between 202.0s and 404.0s" in out


# ─── Honest degradation: agents without cache data ───────────


def test_antigravity_is_not_measurable(tmp_path):
    root = tmp_path / "brain"
    _write_jsonl(
        root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl",
        [{"step_index": "1", "source": "MODEL", "type": "PLANNER_RESPONSE",
          "status": "DONE", "created_at": "2026-11-10T10:00:00.000Z",
          "content": "respuesta sin datos de usage"}],
    )
    rc, out, err = run_report(
        ["--agent", "antigravity", "--session-id", ANTIGRAVITY_SESSION],
        antigravity_root=str(root),
    )
    assert rc == 0, err
    assert "not measurable for antigravity" in out


def test_aider_is_not_measurable(tmp_path):
    rc, out, err = run_report(["--agent", "aider", "--session-id", "default"])
    assert rc == 0, err
    assert "not measurable for aider" in out


def test_aider_without_session_id_still_degrades(tmp_path):
    # Aider has no session store: no latest-session lookup, honest rc 0.
    rc, out, err = run_report(["--agent", "aider"])
    assert rc == 0, err
    assert "not measurable for aider" in out


# ─── Routing (session-id sniffing) ───────────────────────────


def test_sniffs_opencode_from_ses_session(tmp_path):
    db = opencode_cache_fixture(tmp_path)
    rc, out, err = run_report(
        ["--session-id", OPENCODE_SESSION],
        opencode_db=str(db),
        claude_root=str(tmp_path / "no-claude"),
        codex_root=str(tmp_path / "no-codex"),
    )
    assert rc == 0, err
    assert "cache report: opencode session" in out


def test_sniffs_uuid_session_prefers_claude(tmp_path):
    root = claude_cache_fixture(tmp_path)
    rc, out, err = run_report(
        ["--session-id", CLAUDE_SESSION],
        claude_root=str(root),
        codex_root=str(tmp_path / "no-codex"),
    )
    assert rc == 0, err
    assert "cache report: claude session" in out


def test_unroutable_session_errors(tmp_path):
    rc, out, err = run_report(
        ["--session-id", "chat-xyz"],
        claude_root=str(tmp_path / "a"),
        codex_root=str(tmp_path / "b"),
    )
    assert rc != 0
    assert "no transcript found" in err


# ─── CLI wiring (mirrors the voice subcommand dispatch) ──────


def test_cli_dispatches_cache_report_before_parsing(monkeypatch):
    import agent_tts.cache_probe as probe_mod
    import agent_tts.cli as cli_mod

    calls = []

    def fake_handler(argv):
        calls.append(list(argv))
        return 5

    monkeypatch.setattr(probe_mod, "handle_cache_report_command", fake_handler)
    monkeypatch.setattr(sys, "argv", ["agent-tts", "cache-report", "--agent", "claude"])
    with pytest.raises(SystemExit) as excinfo:
        cli_mod.main()
    assert excinfo.value.code == 5
    assert calls == [["--agent", "claude"]]
