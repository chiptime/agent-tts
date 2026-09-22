"""Tests for the agent transcript connector layer (agent_tts.sources)."""

import json
import sqlite3

import pytest

from agent_tts.cleaner import clean_agent_text
from agent_tts.sources import SourceResult, read_last_agent_message
from agent_tts.sources import antigravity as antigravity_module
from agent_tts.sources import claude as claude_module
from agent_tts.sources import codex as codex_module
from agent_tts.sources import opencode as opencode_module
from agent_tts.sources.aider import AiderSource
from agent_tts.sources.antigravity import AntigravitySource
from agent_tts.sources.claude import ClaudeSource
from agent_tts.sources.codex import CodexSource
from agent_tts.sources.opencode import OpencodeSource

OPENCODE_SESSION = "ses_abc123"
CLAUDE_SESSION = "3f2a9c1e-77b4-4d0e-9a51-2b6c8d9e0f12"
ANTIGRAVITY_SESSION = "c738bf00-0e3f-4031-9568-9220d6fb4192"
CODEX_SESSION = "01a018c2-1e32-71b3-82d1-cfc4f5f07dd3"
AIDER_SESSION = "default"  # pass-through label: aider has no session store


# ─── OpenCode adapter ────────────────────────────────────────

@pytest.fixture
def opencode_db(tmp_path):
    """Fake OpenCode DB with the exact production schema and tricky rows."""
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
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
        """
    )
    message_rows = [
        ("msg_u1", OPENCODE_SESSION, 100, {"role": "user"}),
        ("msg_a1", OPENCODE_SESSION, 200, {"role": "assistant"}),
        ("msg_a2", OPENCODE_SESSION, 300, {"role": "assistant"}),
        ("msg_o1", "ses_other99", 400, {"role": "assistant"}),
        ("msg_e1", "ses_empty01", 500, {"role": "assistant"}),
    ]
    part_rows = [
        ("p_u1", "msg_u1", OPENCODE_SESSION, 101, {"type": "text", "text": "pregunta del usuario"}),
        ("p_a1", "msg_a1", OPENCODE_SESSION, 201, {"type": "text", "text": "respuesta antigua"}),
        ("p_a2_reason", "msg_a2", OPENCODE_SESSION, 305, {"type": "reasoning", "text": "pensamiento interno"}),
        ("p_a2_t1", "msg_a2", OPENCODE_SESSION, 301, {"type": "text", "text": "Primera parte"}),
        ("p_a2_tool", "msg_a2", OPENCODE_SESSION, 302, {"type": "tool", "tool": "bash"}),
        ("p_a2_t2", "msg_a2", OPENCODE_SESSION, 303, {"type": "text", "text": "Segunda parte"}),
        ("p_o1", "msg_o1", "ses_other99", 401, {"type": "text", "text": "de otra sesión"}),
        ("p_e1", "msg_e1", "ses_empty01", 501, {"type": "tool", "tool": "read"}),
    ]
    conn.executemany(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        [(i, s, t, json.dumps(d)) for i, s, t, d in message_rows],
    )
    conn.executemany(
        "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
        [(i, m, s, t, json.dumps(d)) for i, m, s, t, d in part_rows],
    )
    conn.commit()
    conn.close()
    return db_path


def test_opencode_newest_message_filtered_and_joined(opencode_db):
    source = OpencodeSource(db_path=str(opencode_db))
    assert source.read(OPENCODE_SESSION) == "Primera parte\n\nSegunda parte"


def test_opencode_rejects_invalid_session_id(opencode_db):
    source = OpencodeSource(db_path=str(opencode_db))
    assert source.read("ses_x") is None  # too short
    assert source.read("msg_123456") is None  # wrong shape
    assert source.read("") is None


def test_opencode_missing_db_returns_none(tmp_path):
    source = OpencodeSource(db_path=str(tmp_path / "missing.db"))
    assert source.read(OPENCODE_SESSION) is None


def test_opencode_unknown_session_returns_none(opencode_db):
    source = OpencodeSource(db_path=str(opencode_db))
    assert source.read("ses_missing1") is None


def test_opencode_assistant_without_text_returns_none(opencode_db):
    source = OpencodeSource(db_path=str(opencode_db))
    assert source.read("ses_empty01") is None


# ─── Claude Code adapter ─────────────────────────────────────

def _write_claude_session(root, session_id, lines):
    project = root / "-home-bruno-project"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def claude_root(tmp_path):
    root = tmp_path / "projects"
    _write_claude_session(
        root,
        CLAUDE_SESSION,
        [
            {"type": "user", "message": {"content": "arregla el bug"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "razonamiento interno"},
                        {"type": "text", "text": "Primera parte de la respuesta"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        {"type": "text", "text": "Segunda parte de la respuesta"},
                    ]
                },
            },
            {"type": "cost-state", "total_cost_usd": 0.42},
            {"type": "progress", "data": "trailing noise"},
        ],
    )
    return root


def test_claude_reads_last_assistant_text(claude_root):
    source = ClaudeSource(root=str(claude_root))
    assert source.read(CLAUDE_SESSION) == (
        "Primera parte de la respuesta\nSegunda parte de la respuesta"
    )


def test_claude_skips_textless_assistant_and_continues_backwards(claude_root):
    path = claude_root / "-home-bruno-project" / f"{CLAUDE_SESSION}.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    # A newer assistant message that only ran tools: the reader must keep
    # scanning backwards to the previous assistant message that has text.
    events.extend(
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            },
            {"type": "progress", "data": "more noise"},
        ]
    )
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    source = ClaudeSource(root=str(claude_root))
    assert source.read(CLAUDE_SESSION) == (
        "Primera parte de la respuesta\nSegunda parte de la respuesta"
    )


def test_claude_reverse_scan_finds_message_across_chunks(tmp_path):
    """The tail reader must reassemble lines that span 64 KB chunk boundaries."""
    root = tmp_path / "projects"
    filler = "x" * 200_000  # far larger than one 64 KB chunk
    _write_claude_session(
        root,
        CLAUDE_SESSION,
        [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "respuesta vieja"}]}},
            {"type": "other", "data": filler},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "respuesta final"}]}},
            {"type": "cost-state", "total_cost_usd": 1.0},
        ],
    )
    source = ClaudeSource(root=str(root))
    assert source.read(CLAUDE_SESSION) == "respuesta final"


def test_claude_scan_cap_stops_before_old_messages(tmp_path):
    root = tmp_path / "projects"
    _write_claude_session(
        root,
        CLAUDE_SESSION,
        [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "fuera del alcance"}]}},
            {"type": "other", "data": "y" * 10_000},
            {"type": "cost-state", "total_cost_usd": 1.0},
        ],
    )
    # Cap smaller than the distance to the only assistant message.
    source = ClaudeSource(root=str(root), scan_cap=1024)
    assert source.read(CLAUDE_SESSION) is None


def test_claude_missing_file_returns_none(tmp_path):
    source = ClaudeSource(root=str(tmp_path / "projects"))
    assert source.read(CLAUDE_SESSION) is None


def test_claude_rejects_invalid_session_id(claude_root):
    source = ClaudeSource(root=str(claude_root))
    assert source.read("short-id") is None
    assert source.read("") is None


def test_claude_default_constants():
    assert claude_module.DEFAULT_SCAN_CAP_BYTES == 8 * 1024 * 1024
    assert "claude" in claude_module.DEFAULT_ROOT
    assert "projects" in claude_module.DEFAULT_ROOT
    assert "opencode.db" in opencode_module.DEFAULT_DB_PATH


# ─── Antigravity CLI adapter ─────────────────────────────────

def _write_antigravity_session(root, session_id, events):
    logs = root / session_id / ".system_generated" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _agy_event(step, type_, **extra):
    """A transcript line shaped like the real Antigravity schema."""
    event = {
        "step_index": str(step),
        "source": "MODEL" if type_ != "USER_INPUT" else "USER",
        "type": type_,
        "status": "DONE",
        "created_at": "2026-09-18T10:00:00.000Z",
    }
    event.update(extra)
    return event


@pytest.fixture
def antigravity_root(tmp_path):
    root = tmp_path / "brain"
    _write_antigravity_session(
        root,
        ANTIGRAVITY_SESSION,
        [
            _agy_event(1, "USER_INPUT", content="<USER_REQUEST> pregúntale a Collie algo"),
            _agy_event(2, "PLANNER_RESPONSE", content="Respuesta antigua del planner"),
            _agy_event(3, "PLANNER_RESPONSE", thinking="razonamiento interno", tool_calls="read_file"),
            _agy_event(
                4,
                "GENERIC",
                content="Created At: 2026-09-18T10:00:01Z\nThe output was large and was saved to: file:///tmp/out.txt",
            ),
            _agy_event(5, "SYSTEM_MESSAGE", source="SYSTEM", content="checkpoint guardado"),
            _agy_event(6, "PLANNER_RESPONSE", content="   "),
            _agy_event(
                7,
                "PLANNER_RESPONSE",
                content="### 1. Respuesta final\n\n**No, no te devolverá audio por ahora.**",
            ),
            _agy_event(8, "CHECKPOINT"),
        ],
    )
    return root


def test_antigravity_reads_last_planner_response(antigravity_root):
    source = AntigravitySource(root=str(antigravity_root))
    assert source.read(ANTIGRAVITY_SESSION) == (
        "### 1. Respuesta final\n\n**No, no te devolverá audio por ahora.**"
    )


def test_antigravity_skips_tool_summaries_and_system_noise(antigravity_root):
    path = antigravity_root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    # Trailing GENERIC tool summaries, system chatter and a fresh user input
    # are newer than the last planner response but must never be returned.
    events.extend(
        [
            _agy_event(9, "GENERIC", content="Created At: ...\nThe output was large and was saved to: file:///x"),
            _agy_event(10, "SYSTEM_MESSAGE", source="SYSTEM", content="session saved"),
            _agy_event(11, "USER_INPUT", content="<USER_REQUEST> otra pregunta"),
            _agy_event(12, "CHECKPOINT"),
        ]
    )
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    source = AntigravitySource(root=str(antigravity_root))
    assert source.read(ANTIGRAVITY_SESSION) == (
        "### 1. Respuesta final\n\n**No, no te devolverá audio por ahora.**"
    )


def test_antigravity_skips_thinking_only_and_empty_planner_lines(antigravity_root):
    path = antigravity_root / ANTIGRAVITY_SESSION / ".system_generated" / "logs" / "transcript.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    # Newer planner lines with only thinking/tool payload or blank content
    # carry no user-visible prose: keep scanning backwards.
    events.extend(
        [
            _agy_event(13, "PLANNER_RESPONSE", thinking="solo razonamiento", tool_calls="bash"),
            _agy_event(14, "PLANNER_RESPONSE", content=""),
        ]
    )
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    source = AntigravitySource(root=str(antigravity_root))
    assert source.read(ANTIGRAVITY_SESSION) == (
        "### 1. Respuesta final\n\n**No, no te devolverá audio por ahora.**"
    )


def test_antigravity_scan_cap_stops_before_old_planner_response(tmp_path):
    root = tmp_path / "brain"
    _write_antigravity_session(
        root,
        ANTIGRAVITY_SESSION,
        [
            _agy_event(1, "PLANNER_RESPONSE", content="fuera del alcance"),
            _agy_event(2, "GENERIC", content="r" * 10_000),
            _agy_event(3, "CHECKPOINT"),
        ],
    )
    # Cap smaller than the distance to the only planner response.
    source = AntigravitySource(root=str(root), scan_cap=1024)
    assert source.read(ANTIGRAVITY_SESSION) is None


def test_antigravity_missing_session_returns_none(tmp_path):
    source = AntigravitySource(root=str(tmp_path / "brain"))
    assert source.read(ANTIGRAVITY_SESSION) is None


def test_antigravity_rejects_invalid_session_id(antigravity_root):
    source = AntigravitySource(root=str(antigravity_root))
    assert source.read("short-id") is None
    assert source.read("") is None


def test_antigravity_default_constants():
    assert antigravity_module.DEFAULT_SCAN_CAP_BYTES == 8 * 1024 * 1024
    assert "antigravity-cli" in antigravity_module.DEFAULT_ROOT
    assert "brain" in antigravity_module.DEFAULT_ROOT


# ─── Codex CLI adapter ───────────────────────────────────────

def _write_codex_session(root, session_id, events, uuid_prefix=""):
    day = root / "2026" / "08" / "19"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-2026-08-19T08-42-50-{uuid_prefix}{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _codex_event(type_, payload):
    return {"timestamp": "2026-08-19T08:42:50.000Z", "type": type_, "payload": payload}


@pytest.fixture
def codex_root(tmp_path):
    root = tmp_path / "sessions"
    _write_codex_session(
        root,
        CODEX_SESSION,
        [
            _codex_event("session_meta", {"id": CODEX_SESSION}),
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "<permissions instructions>\n..."}],
                },
            ),
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "<environment_context>\n  <cwd>/tmp/project</cwd>\n</environment_context>"}
                    ],
                },
            ),
            _codex_event("event_msg", {"type": "task_started"}),
            _codex_event("response_item", {"type": "reasoning", "summary": []}),
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Primera parte"},
                        {"type": "output_text", "text": "Segunda parte"},
                    ],
                },
            ),
            _codex_event("event_msg", {"type": "token_count", "info": {"total_token_usage": 100}}),
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "<turn_aborted>\nThe user interrupted the previous turn on purpose.</turn_aborted>"}
                    ],
                },
            ),
            _codex_event("event_msg", {"type": "turn_aborted"}),
        ],
    )
    return root


def test_codex_reads_last_assistant_output_text(codex_root):
    source = CodexSource(sessions_root=str(codex_root))
    assert source.read(CODEX_SESSION) == "Primera parte\nSegunda parte"


def test_codex_skips_event_msg_noise_and_aborted_user_blocks(codex_root):
    path = codex_root / "2026" / "08" / "19" / f"rollout-2026-08-19T08-42-50-{CODEX_SESSION}.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    # Newer environment noise (token counts, an interrupted user turn) must
    # not displace the last real assistant message.
    events.extend(
        [
            _codex_event("event_msg", {"type": "token_count", "info": {}}),
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<turn_aborted>interrupted</turn_aborted>"}],
                },
            ),
            _codex_event("event_msg", {"type": "turn_aborted"}),
        ]
    )
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    source = CodexSource(sessions_root=str(codex_root))
    assert source.read(CODEX_SESSION) == "Primera parte\nSegunda parte"


def test_codex_falls_back_to_agent_message_channel(tmp_path):
    root = tmp_path / "sessions"
    _write_codex_session(
        root,
        CODEX_SESSION,
        [
            _codex_event("event_msg", {"type": "task_started"}),
            _codex_event("event_msg", {"type": "agent_message", "message": "mensaje antiguo"}),
            _codex_event("event_msg", {"type": "token_count", "info": {}}),
            _codex_event("event_msg", {"type": "agent_message", "message": "mensaje final del canal alterno"}),
        ],
    )
    source = CodexSource(sessions_root=str(root))
    assert source.read(CODEX_SESSION) == "mensaje final del canal alterno"


def test_codex_prefers_response_item_over_newer_agent_message(tmp_path):
    root = tmp_path / "sessions"
    _write_codex_session(
        root,
        CODEX_SESSION,
        [
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "respuesta del canal principal"}],
                },
            ),
            _codex_event("event_msg", {"type": "agent_message", "message": "eco posterior del canal alterno"}),
        ],
    )
    source = CodexSource(sessions_root=str(root))
    assert source.read(CODEX_SESSION) == "respuesta del canal principal"


def test_codex_uuid_mismatch_in_filename_returns_none(tmp_path):
    root = tmp_path / "sessions"
    # A different uuid that merely ends with the requested id substring:
    # the glob matches, but the exact filename anchor must reject it.
    _write_codex_session(
        root,
        CODEX_SESSION,
        [
            _codex_event(
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "de otra sesión"}],
                },
            ),
        ],
        uuid_prefix="0",
    )
    source = CodexSource(sessions_root=str(root))
    assert source.read(CODEX_SESSION) is None


def test_codex_missing_session_returns_none(tmp_path):
    source = CodexSource(sessions_root=str(tmp_path / "sessions"))
    assert source.read(CODEX_SESSION) is None


def test_codex_rejects_invalid_session_id(codex_root):
    source = CodexSource(sessions_root=str(codex_root))
    assert source.read("short-id") is None
    assert source.read("") is None


def test_codex_default_constants():
    assert codex_module.DEFAULT_SCAN_CAP_BYTES == 8 * 1024 * 1024
    assert ".codex" in codex_module.DEFAULT_SESSIONS_ROOT
    assert "sessions" in codex_module.DEFAULT_SESSIONS_ROOT


# ─── Aider markdown adapter ──────────────────────────────────

def _write_aider_history(root, content):
    path = root / ".aider.chat.history.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def aider_history(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write_aider_history(
        root,
        (
            "# aider chat started at 2026-09-18T10:00:00\n"
            "\n"
            "> Tokens: 4.2k in | 120 out\n"
            "\n"
            "#### User:\n"
            "Arregla el bug del reproductor.\n"
            "\n"
            "#### Assistant:\n"
            "Respuesta antigua.\n"
            "\n"
            "#### /add player.py\n"
            "\n"
            "#### User:\n"
            "Ahora explica el arreglo.\n"
            "\n"
            "#### Assistant:\n"
            "Primera parte del arreglo\n"
            "\n"
            "```python\n"
            "print('hola')\n"
            "```\n"
            "\n"
            "Segunda parte del arreglo.\n"
        ),
    )
    return root


def test_aider_reads_last_assistant_turn(aider_history):
    source = AiderSource(history_path=str(aider_history / ".aider.chat.history.md"))
    # Multiple turns: the newest assistant turn wins, with its fenced code
    # block preserved verbatim and earlier turns/slash commands discarded.
    assert source.read(AIDER_SESSION) == (
        "Primera parte del arreglo\n\n```python\nprint('hola')\n```\n\nSegunda parte del arreglo."
    )


def test_aider_fake_heading_inside_fence_is_not_a_boundary(aider_history):
    path = aider_history / ".aider.chat.history.md"
    _write_aider_history(
        aider_history,
        "#### User:\n"
        "¿qué tal?\n"
        "\n"
        "#### Assistant:\n"
        "Antes del bloque.\n"
        "\n"
        "```text\n"
        "#### User:\n"
        "#### Assistant:\n"
        "Esto parece un turno pero es código de ejemplo.\n"
        "```\n"
        "\n"
        "Después del bloque.\n",
    )
    # Heading-shaped lines inside the fence are transcript content: the last
    # assistant turn must span the whole message, fences included.
    source = AiderSource(history_path=str(path))
    assert source.read(AIDER_SESSION) == (
        "Antes del bloque.\n\n"
        "```text\n"
        "#### User:\n"
        "#### Assistant:\n"
        "Esto parece un turno pero es código de ejemplo.\n"
        "```\n"
        "\n"
        "Después del bloque."
    )


def test_aider_skips_trailing_user_turn(aider_history):
    path = aider_history / ".aider.chat.history.md"
    content = path.read_text(encoding="utf-8")
    # A newer user prompt after the last assistant answer must not displace
    # the assistant turn.
    _write_aider_history(aider_history, content + "\n#### User:\nPregunta final sin respuesta.\n")
    source = AiderSource(history_path=str(path))
    assert source.read(AIDER_SESSION).startswith("Primera parte del arreglo")
    assert source.read(AIDER_SESSION).endswith("Segunda parte del arreglo.")


def test_aider_missing_history_file_returns_none(tmp_path):
    source = AiderSource(history_path=str(tmp_path / "missing" / ".aider.chat.history.md"))
    assert source.read(AIDER_SESSION) is None


def test_aider_empty_history_file_returns_none(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = _write_aider_history(root, "")
    source = AiderSource(history_path=str(path))
    assert source.read(AIDER_SESSION) is None


def test_aider_without_assistant_turn_returns_none(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = _write_aider_history(root, "#### User:\nSolo preguntas, ninguna respuesta.\n")
    source = AiderSource(history_path=str(path))
    assert source.read(AIDER_SESSION) is None


def test_aider_rejects_invalid_session_id(aider_history):
    source = AiderSource(history_path=str(aider_history / ".aider.chat.history.md"))
    assert source.read("") is None
    assert source.read("   ") is None
    assert source.read(None) is None


def test_aider_session_id_is_a_pass_through_label(aider_history):
    # Aider has no session store: any non-blank label resolves the same file.
    source = AiderSource(history_path=str(aider_history / ".aider.chat.history.md"))
    assert source.read("whatever-label-42") is not None
    assert source.read(AIDER_SESSION) is not None


def test_aider_env_override_history_path(monkeypatch, tmp_path):
    root = tmp_path / "elsewhere"
    root.mkdir()
    path = _write_aider_history(root, "#### Assistant:\nrespuesta desde la ruta alternativa\n")
    monkeypatch.setenv("AGENT_TTS_AIDER_HISTORY", str(path))
    source = AiderSource()
    assert source.read(AIDER_SESSION) == "respuesta desde la ruta alternativa"


def test_aider_env_relative_path_raises_value_error(monkeypatch):
    monkeypatch.setenv("AGENT_TTS_AIDER_HISTORY", "relative/.aider.chat.history.md")
    with pytest.raises(ValueError) as ctx:
        AiderSource()
    assert "absolute" in str(ctx.value)
    assert "AGENT_TTS_AIDER_HISTORY" in str(ctx.value)


def test_aider_env_unset_raises_actionable_error(monkeypatch):
    # No silent CWD fallback: the engine may run from a different directory
    # than the user's aider session, so the path must be provided.
    monkeypatch.delenv("AGENT_TTS_AIDER_HISTORY", raising=False)
    with pytest.raises(SystemExit) as ctx:
        AiderSource()
    message = str(ctx.value)
    assert "AGENT_TTS_AIDER_HISTORY" in message
    assert "absolute" in message


def test_aider_unconfigured_fails_loudly_through_routing(monkeypatch):
    monkeypatch.delenv("AGENT_TTS_AIDER_HISTORY", raising=False)
    with pytest.raises(SystemExit):
        read_last_agent_message("aider", AIDER_SESSION)


def test_aider_message_flows_redacted_through_cleaner(aider_history):
    # End-to-end: connector output must reach speech already sanitized, so a
    # leaked credential inside the last assistant turn is redacted by the
    # shared cleaning stage.
    path = aider_history / ".aider.chat.history.md"
    secret = "sk-proj-aaaaaaaaaaaaaaaaaaaa123456"
    _write_aider_history(
        aider_history,
        "#### User:\nConfigura el cliente.\n"
        "\n"
        "#### Assistant:\n"
        "Listo, la clave es " + secret + " y rota en 30 días.\n"
        "\n"
        "```bash\n"
        "export API_KEY=\"" + secret + "\"\n"
        "```\n",
    )
    text = AiderSource(history_path=str(path)).read(AIDER_SESSION)
    cleaned = clean_agent_text(text, pre_extracted=True)
    assert secret not in cleaned
    # The pronunciation lexicon may expand "API", so assert the spoken
    # redaction marker by its suffix (same style as the cleaner tests).
    assert "omitida" in cleaned


# ─── Orchestrator routing ────────────────────────────────────

def test_routes_by_agent_name(monkeypatch):
    seen = {}

    class FakeOpencode:
        def read(self, session_id):
            seen["opencode"] = session_id
            return "texto desde opencode"

    class FakeClaude:
        def read(self, session_id):  # pragma: no cover - must not be called
            seen["claude"] = session_id
            return "texto desde claude"

    monkeypatch.setattr("agent_tts.sources.opencode.OpencodeSource", FakeOpencode)
    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", FakeClaude)

    result = read_last_agent_message("opencode", OPENCODE_SESSION)
    assert result == SourceResult(text="texto desde opencode", source="opencode")
    assert seen == {"opencode": OPENCODE_SESSION}


def test_routes_claude_by_agent_name(monkeypatch):
    class FakeClaude:
        def read(self, session_id):
            return "texto desde claude"

    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", FakeClaude)
    result = read_last_agent_message("claude", CLAUDE_SESSION)
    assert result == SourceResult(text="texto desde claude", source="claude")


def test_sniffs_opencode_from_session_pattern(monkeypatch):
    class FakeOpencode:
        def read(self, session_id):
            return "oli"

    monkeypatch.setattr("agent_tts.sources.opencode.OpencodeSource", FakeOpencode)
    result = read_last_agent_message(None, OPENCODE_SESSION)
    assert result is not None
    assert result.source == "opencode"


def test_sniffs_claude_from_uuid_pattern(monkeypatch):
    class FakeClaude:
        def read(self, session_id):
            return "claude sniffed"

    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", FakeClaude)
    result = read_last_agent_message(None, CLAUDE_SESSION)
    assert result is not None
    assert result.source == "claude"


def test_unknown_agent_name_falls_back_to_sniffing(monkeypatch):
    class FakeOpencode:
        def read(self, session_id):
            return "resuelto"

    monkeypatch.setattr("agent_tts.sources.opencode.OpencodeSource", FakeOpencode)
    result = read_last_agent_message("gemini", OPENCODE_SESSION)
    assert result is not None
    assert result.source == "opencode"


def test_unroutable_session_returns_none(monkeypatch):
    class ShouldNotBeBuilt:  # pragma: no cover - never instantiated
        pass

    monkeypatch.setattr("agent_tts.sources.opencode.OpencodeSource", ShouldNotBeBuilt)
    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", ShouldNotBeBuilt)
    assert read_last_agent_message(None, "chat-xyz") is None


def test_empty_session_id_returns_none():
    assert read_last_agent_message("opencode", None) is None
    assert read_last_agent_message(None, "") is None


def test_adapter_miss_returns_none(monkeypatch):
    class FakeOpencode:
        def read(self, session_id):
            return None

    monkeypatch.setattr("agent_tts.sources.opencode.OpencodeSource", FakeOpencode)
    assert read_last_agent_message("opencode", OPENCODE_SESSION) is None


def test_routes_agy_alias_to_antigravity_adapter(monkeypatch):
    seen = {}

    class FakeAntigravity:
        def read(self, session_id):
            seen["agy"] = session_id
            return "texto desde antigravity"

    monkeypatch.setattr("agent_tts.sources.antigravity.AntigravitySource", FakeAntigravity)
    result = read_last_agent_message("agy", ANTIGRAVITY_SESSION)
    assert result == SourceResult(text="texto desde antigravity", source="antigravity")
    assert seen == {"agy": ANTIGRAVITY_SESSION}


def test_routes_antigravity_by_full_name(monkeypatch):
    class FakeAntigravity:
        def read(self, session_id):
            return "texto desde antigravity"

    monkeypatch.setattr("agent_tts.sources.antigravity.AntigravitySource", FakeAntigravity)
    result = read_last_agent_message("antigravity", ANTIGRAVITY_SESSION)
    assert result is not None
    assert result.source == "antigravity"


def test_routes_codex_by_agent_name(monkeypatch):
    class FakeCodex:
        def read(self, session_id):
            return "texto desde codex"

    monkeypatch.setattr("agent_tts.sources.codex.CodexSource", FakeCodex)
    result = read_last_agent_message("codex", CODEX_SESSION)
    assert result == SourceResult(text="texto desde codex", source="codex")


def test_routes_aider_by_agent_name(monkeypatch):
    class FakeAider:
        def read(self, session_id):
            return "texto desde aider"

    monkeypatch.setattr("agent_tts.sources.aider.AiderSource", FakeAider)
    result = read_last_agent_message("aider", AIDER_SESSION)
    assert result == SourceResult(text="texto desde aider", source="aider")


def test_uuid_sniffing_tries_claude_codex_then_antigravity(monkeypatch):
    class FakeClaude:
        def read(self, session_id):
            return None

    class FakeCodex:
        def read(self, session_id):
            return None

    class FakeAntigravity:
        def read(self, session_id):
            return "encontrado en antigravity"

    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", FakeClaude)
    monkeypatch.setattr("agent_tts.sources.codex.CodexSource", FakeCodex)
    monkeypatch.setattr("agent_tts.sources.antigravity.AntigravitySource", FakeAntigravity)

    result = read_last_agent_message(None, ANTIGRAVITY_SESSION)
    assert result == SourceResult(text="encontrado en antigravity", source="antigravity")


def test_uuid_sniffing_stops_at_first_hit(monkeypatch):
    class FakeClaude:
        def read(self, session_id):
            return "texto desde claude"

    class MustNotBeBuilt:  # pragma: no cover - never instantiated
        pass

    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", FakeClaude)
    monkeypatch.setattr("agent_tts.sources.codex.CodexSource", MustNotBeBuilt)
    monkeypatch.setattr("agent_tts.sources.antigravity.AntigravitySource", MustNotBeBuilt)

    result = read_last_agent_message(None, CLAUDE_SESSION)
    assert result == SourceResult(text="texto desde claude", source="claude")


def test_uuid_sniffing_all_miss_returns_none(monkeypatch):
    class FakeNone:
        def read(self, session_id):
            return None

    monkeypatch.setattr("agent_tts.sources.claude.ClaudeSource", FakeNone)
    monkeypatch.setattr("agent_tts.sources.codex.CodexSource", FakeNone)
    monkeypatch.setattr("agent_tts.sources.antigravity.AntigravitySource", FakeNone)
    assert read_last_agent_message(None, CLAUDE_SESSION) is None
