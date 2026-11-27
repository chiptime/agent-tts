"""Tests for the last-model probe (HT-19 L8/S4a).

``agent-tts last-model`` prints the model of the LAST assistant request of
a session — the model that holds the freshest context, used by the plugin
to key the pre-cool TTL per model. All fixtures are synthetic: tiny JSONL
transcripts / SQLite databases built in tmp_path with the exact production
schemas. The developer's real agent artifacts are never read.
"""

import io
import json
import os
import sqlite3
import sys

import pytest

from agent_tts.cache_probe import handle_last_model_command

CLAUDE_SESSION = "3f2a9c1e-77b4-4d0e-9a51-2b6c8d9e0f12"
CLAUDE_OTHER = "aaaaaaaa-1111-2222-3333-444444444444"
OPENCODE_SESSION = "ses_abc123"
OPENCODE_OTHER = "ses_other99"
CODEX_SESSION = "01a018c2-1e32-71b3-82d1-cfc4f5f07dd3"
ANTIGRAVITY_SESSION = "c738bf00-0e3f-4031-9568-9220d6fb4192"


def _antigravity_request(created, input_tokens, cached, output, model=None):
    """One real-shaped PLANNER_RESPONSE (POST-09): per-request token
    counts always, `model` only on requests recorded 2026-11-17+."""
    event = {
        "step_index": "7", "source": "MODEL", "type": "PLANNER_RESPONSE",
        "status": "DONE", "created_at": created,
        "input_tokens": input_tokens, "cache_read_tokens": cached,
        "output_tokens": output, "content": "hecho",
    }
    if model is not None:
        event["model"] = model
    return event


def run_last_model(argv, **kwargs):
    """Runs the last-model command capturing both streams; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    code = handle_last_model_command(argv, out=out, err=err, **kwargs)
    return code, out.getvalue(), err.getvalue()


# ─── Shared fixture builders (same production schemas as test_cache_probe) ───


def _write_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


def _claude_assistant(ts, usage, message_id="msg_A", model=None, text="ok"):
    message = {
        "id": message_id,
        "content": [{"type": "text", "text": text}],
        "usage": usage,
    }
    if model is not None:
        message["model"] = model
    return {"type": "assistant", "timestamp": ts, "requestId": message_id,
            "message": message}


def _claude_usage(cached, written, input_tokens=2):
    return {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": written,
        "output_tokens": 100,
    }


def _opencode_db(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        [(i, s, t, json.dumps(d)) for i, s, t, d in rows],
    )
    conn.commit()
    conn.close()
    return path


def _oc_row(row_id, session, created, model, tokens=True):
    data = {"role": "assistant"}
    if tokens:
        data["tokens"] = {"input": 4, "output": 100,
                          "cache": {"read": 10, "write": 20}}
    if model is not None:
        data["modelID"] = model
    return (row_id, session, created, data)


# ─── OpenCode: modelID on the last request row ────────────────


def test_opencode_last_model_of_latest_request(tmp_path):
    db = _opencode_db(
        tmp_path / "opencode.db",
        [
            _oc_row("msg_a1", OPENCODE_SESSION, 1_000_000, "glm-5.2"),
            _oc_row("msg_a2", OPENCODE_SESSION, 1_003_500, "glm-5.3"),
        ],
    )
    rc, out, err = run_last_model(
        ["--agent", "opencode", "--session-id", OPENCODE_SESSION], opencode_db=str(db)
    )
    assert rc == 0, err
    assert out == "glm-5.3\n"


def test_opencode_mid_session_model_switch_returns_final_model(tmp_path):
    db = _opencode_db(
        tmp_path / "opencode.db",
        [
            _oc_row("msg_a1", OPENCODE_SESSION, 1_000_000, "deepseek-v4-pro"),
            _oc_row("msg_a2", OPENCODE_SESSION, 1_003_500, "glm-5.2"),
            _oc_row("msg_a3", OPENCODE_SESSION, 1_007_000, "glm-5.3-flash"),
        ],
    )
    rc, out, err = run_last_model(
        ["--agent", "opencode", "--session-id", OPENCODE_SESSION], opencode_db=str(db)
    )
    assert rc == 0, err
    assert out == "glm-5.3-flash\n"


def test_opencode_latest_session_default(tmp_path):
    # The newest assistant-with-tokens row belongs to the OTHER session; a
    # user row in this session (newer) must not win the latest lookup.
    db = _opencode_db(
        tmp_path / "opencode.db",
        [
            _oc_row("msg_a1", OPENCODE_SESSION, 1_000_000, "glm-5.2"),
            ("msg_u1", OPENCODE_SESSION, 2_000_000, {"role": "user"}),
            _oc_row("msg_o2", OPENCODE_OTHER, 1_500_000, "gemini-3.1-pro-preview"),
        ],
    )
    rc, out, err = run_last_model(["--agent", "opencode"], opencode_db=str(db))
    assert rc == 0, err
    assert out == "gemini-3.1-pro-preview\n"


def test_opencode_rows_without_model_error_honestly(tmp_path):
    db = _opencode_db(
        tmp_path / "opencode.db",
        [_oc_row("msg_a1", OPENCODE_SESSION, 1_000_000, None)],
    )
    rc, out, err = run_last_model(
        ["--agent", "opencode", "--session-id", OPENCODE_SESSION], opencode_db=str(db)
    )
    assert rc == 1
    assert out == ""
    assert "no model recorded" in err


# ─── Claude: message.model per event, last request wins ───────


def test_claude_last_event_model_wins(tmp_path):
    root = tmp_path / "projects"
    events = [
        _claude_assistant("2026-11-10T01:00:00.000Z", _claude_usage(0, 100),
                          "msg_1", model="claude-haiku-4-5"),
        _claude_assistant("2026-11-10T01:01:00.000Z", _claude_usage(500, 100),
                          "msg_2", model="claude-sonnet-5-5"),
        _claude_assistant("2026-11-10T01:02:00.000Z", _claude_usage(900, 100),
                          "msg_3", model="claude-opus-4-8"),
        # Stream-chunk duplicate of the final response (same message id):
        # one request, its model is still the answer.
        _claude_assistant("2026-11-10T01:02:00.400Z", _claude_usage(900, 100),
                          "msg_3", model="claude-opus-4-8"),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_last_model(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 0, err
    assert out == "claude-opus-4-8\n"


def test_claude_synthetic_error_message_is_not_a_request(tmp_path):
    # Claude Code logs API error messages (e.g. a usage-limit notice) as
    # assistant lines with model "<synthetic>" and all-zero usage. No API
    # request happened: the last REAL model is still the answer.
    root = tmp_path / "projects"
    events = [
        _claude_assistant("2026-11-10T01:00:00.000Z", _claude_usage(500, 100),
                          "msg_1", model="claude-opus-5-5"),
        _claude_assistant("2026-11-10T01:01:00.000Z",
                          _claude_usage(0, 0, input_tokens=0),
                          "msg_2", model="<synthetic>",
                          text="You've hit your 5-hour limit"),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_last_model(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 0, err
    assert out == "claude-opus-5-5\n"


def test_claude_only_synthetic_lines_have_no_requests(tmp_path):
    root = tmp_path / "projects"
    events = [
        _claude_assistant("2026-11-10T01:01:00.000Z",
                          _claude_usage(0, 0, input_tokens=0),
                          "msg_1", model="<synthetic>"),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_last_model(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 1
    assert out == ""
    assert "<synthetic>" not in out


def test_claude_chunk_group_fills_missing_model(tmp_path):
    # First kept line of the final response carries no model; a later chunk
    # of the same response does: the request's model must be recovered.
    root = tmp_path / "projects"
    events = [
        _claude_assistant("2026-11-10T01:00:00.000Z", _claude_usage(0, 100),
                          "msg_1", model="claude-haiku-4-5"),
        _claude_assistant("2026-11-10T01:01:00.000Z", _claude_usage(500, 100),
                          "msg_2", model=None),
        _claude_assistant("2026-11-10T01:01:00.300Z", _claude_usage(500, 100),
                          "msg_2", model="claude-sonnet-5-5"),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_last_model(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 0, err
    assert out == "claude-sonnet-5-5\n"


def test_claude_final_request_without_model_errors(tmp_path):
    root = tmp_path / "projects"
    events = [
        _claude_assistant("2026-11-10T01:00:00.000Z", _claude_usage(0, 100),
                          "msg_1", model="claude-haiku-4-5"),
        _claude_assistant("2026-11-10T01:01:00.000Z", _claude_usage(500, 100),
                          "msg_2", model=None),
    ]
    _write_jsonl(root / "-home-project" / f"{CLAUDE_SESSION}.jsonl", events)
    rc, out, err = run_last_model(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION], claude_root=str(root)
    )
    assert rc == 1
    assert out == ""
    assert "no model recorded" in err


def test_claude_latest_session_default(tmp_path):
    root = tmp_path / "projects"
    _write_jsonl(
        root / "-home-a" / f"{CLAUDE_SESSION}.jsonl",
        [_claude_assistant("2026-11-10T01:00:00.000Z", _claude_usage(0, 100),
                           "msg_1", model="claude-haiku-4-5")],
    )
    _write_jsonl(
        root / "-home-b" / f"{CLAUDE_OTHER}.jsonl",
        [_claude_assistant("2026-11-11T01:00:00.000Z", _claude_usage(10, 100),
                           "msg_9", model="claude-sonnet-5-5")],
    )
    os.utime(root / "-home-a" / f"{CLAUDE_SESSION}.jsonl", (1000000, 1000000))
    os.utime(root / "-home-b" / f"{CLAUDE_OTHER}.jsonl", (2000000, 2000000))
    rc, out, err = run_last_model(["--agent", "claude"], claude_root=str(root))
    assert rc == 0, err
    assert out == "claude-sonnet-5-5\n"


# ─── Codex: turn_context carries the model ────────────────────


def test_codex_last_model_from_turn_context(tmp_path):
    root = tmp_path / "sessions" / "2026" / "11" / "10"
    events = [
        {"timestamp": "2026-11-10T01:00:00.000Z", "type": "turn_context", "payload": {
            "model": "gpt-5.1", "cwd": "/home/project"}},
        {"timestamp": "2026-11-10T01:00:05.000Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 28180, "cached_input_tokens": 5504,
                "output_tokens": 145}}}},
        # Mid-session model switch: the final turn_context governs.
        {"timestamp": "2026-11-10T01:03:00.000Z", "type": "turn_context", "payload": {
            "model": "gpt-5.5", "cwd": "/home/project"}},
        {"timestamp": "2026-11-10T01:03:22.000Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 29516, "cached_input_tokens": 28032,
                "output_tokens": 27}}}},
    ]
    _write_jsonl(
        root / f"rollout-2026-11-10T01-00-00-{CODEX_SESSION}.jsonl", events
    )
    rc, out, err = run_last_model(
        ["--agent", "codex", "--session-id", CODEX_SESSION],
        codex_root=str(tmp_path / "sessions"),
    )
    assert rc == 0, err
    assert out == "gpt-5.5\n"


def test_codex_without_turn_context_errors(tmp_path):
    root = tmp_path / "sessions" / "2026" / "11" / "10"
    events = [
        {"timestamp": "2026-11-10T01:00:05.000Z", "type": "event_msg", "payload": {
            "type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 28180, "cached_input_tokens": 5504,
                "output_tokens": 145}}}},
    ]
    _write_jsonl(
        root / f"rollout-2026-11-10T01-00-00-{CODEX_SESSION}.jsonl", events
    )
    rc, out, err = run_last_model(
        ["--agent", "codex", "--session-id", CODEX_SESSION],
        codex_root=str(tmp_path / "sessions"),
    )
    assert rc == 1
    assert "no model recorded" in err


# ─── Honest degradation and routing ───────────────────────────


def test_unknown_agent_errors(tmp_path):
    rc, out, err = run_last_model(["--agent", "bogus"])
    assert rc == 1
    assert "unknown agent" in err


def test_missing_transcript_errors(tmp_path):
    rc, out, err = run_last_model(
        ["--agent", "claude", "--session-id", CLAUDE_SESSION],
        claude_root=str(tmp_path / "empty"),
    )
    assert rc == 1
    assert "no transcript found" in err


def test_antigravity_has_no_model(tmp_path):
    root = tmp_path / "brain"
    _write_jsonl(
        root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl",
        [{"step_index": "1", "source": "MODEL", "type": "PLANNER_RESPONSE",
          "status": "DONE", "created_at": "2026-11-10T10:00:00.000Z",
          "content": "respuesta sin modelo"}],
    )
    rc, out, err = run_last_model(
        ["--agent", "antigravity", "--session-id", ANTIGRAVITY_SESSION],
        antigravity_root=str(root),
    )
    assert rc == 1
    assert "no model recorded" in err


def test_antigravity_reads_the_last_request_model(tmp_path):
    """The transcript's model field (agy ~1.5.x): the LAST request's
    model is the answer, mid-session switch included."""
    root = tmp_path / "brain"
    _write_jsonl(
        root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl",
        [
            # Pre-2026-11-17 event shape: usage, no model field.
            _antigravity_request("2026-11-10T10:00:00Z", 100, 0, 10),
            _antigravity_request(
                "2026-11-26T00:55:00Z", 2102, 0, 1151, model="gemini-4-hydrogen-medium"
            ),
            _antigravity_request(
                "2026-11-26T00:55:55Z", 29658, 314493, 5260,
                model="gemini-4-hydrogen-high",
            ),
        ],
    )
    rc, out, err = run_last_model(
        ["--agent", "antigravity", "--session-id", ANTIGRAVITY_SESSION],
        antigravity_root=str(root),
    )
    assert rc == 0, err
    assert out == "gemini-4-hydrogen-high\n"


def test_antigravity_session_without_model_field_errors_honestly(tmp_path):
    """D5: sessions whose requests predate the `model` field answer
    `no model recorded` — never guessed from the global settings.json."""
    root = tmp_path / "brain"
    _write_jsonl(
        root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl",
        [
            _antigravity_request("2026-11-10T10:00:00Z", 100, 0, 10),
            _antigravity_request("2026-11-10T10:01:00Z", 200, 50, 20),
        ],
    )
    rc, out, err = run_last_model(
        ["--agent", "antigravity", "--session-id", ANTIGRAVITY_SESSION],
        antigravity_root=str(root),
    )
    assert rc == 1
    assert out == ""
    assert "no model recorded" in err


def test_antigravity_last_request_without_model_errors(tmp_path):
    """A mid-session switch BACK to an old-style client (or a truncated
    transcript): the last request carries no model, so there is nothing
    honest to report."""
    root = tmp_path / "brain"
    _write_jsonl(
        root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl",
        [
            _antigravity_request(
                "2026-11-26T00:55:00Z", 2102, 0, 1151, model="gemini-4-neon-high"
            ),
            _antigravity_request("2026-11-26T00:56:00Z", 2102, 3000, 1151),
        ],
    )
    rc, out, err = run_last_model(
        ["--agent", "antigravity", "--session-id", ANTIGRAVITY_SESSION],
        antigravity_root=str(root),
    )
    assert rc == 1
    assert "no model recorded" in err


def test_antigravity_missing_transcript_errors(tmp_path):
    rc, out, err = run_last_model(
        ["--agent", "antigravity", "--session-id", ANTIGRAVITY_SESSION],
        antigravity_root=str(tmp_path / "empty"),
    )
    assert rc == 1
    assert "no transcript found" in err


def test_sniffs_opencode_from_ses_session(tmp_path):
    db = _opencode_db(
        tmp_path / "opencode.db",
        [_oc_row("msg_a1", OPENCODE_SESSION, 1_000_000, "glm-5.3")],
    )
    rc, out, err = run_last_model(
        ["--session-id", OPENCODE_SESSION],
        opencode_db=str(db),
        claude_root=str(tmp_path / "no-claude"),
        codex_root=str(tmp_path / "no-codex"),
    )
    assert rc == 0, err
    assert out == "glm-5.3\n"


# ─── CLI wiring (mirrors the cache-report dispatch) ───────────


def test_cli_dispatches_last_model_before_parsing(monkeypatch):
    import agent_tts.cache_probe as probe_mod
    import agent_tts.cli as cli_mod

    calls = []

    def fake_handler(argv):
        calls.append(list(argv))
        return 5

    monkeypatch.setattr(probe_mod, "handle_last_model_command", fake_handler)
    monkeypatch.setattr(sys, "argv", ["agent-tts", "last-model", "--agent", "opencode"])
    with pytest.raises(SystemExit) as excinfo:
        cli_mod.main()
    assert excinfo.value.code == 5
    assert calls == [["--agent", "opencode"]]
