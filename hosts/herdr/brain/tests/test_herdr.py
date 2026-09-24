"""Unit tests for the herdr CLI wrapper.

Safety: every subprocess invocation is mocked. No test ever talks to a real
Herdr server or writes into a real agent session.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import (
    AgentInfo,
    HerdrClient,
    HerdrError,
    parse_agent_list,
    pick_active,
    sanitize_prompt_text,
)

NDJSON_LINE = json.dumps(
    {
        "id": "cli:agent:list",
        "result": {
            "agents": [
                {
                    "agent": "opencode",
                    "agent_session": {
                        "agent": "opencode",
                        "kind": "id",
                        "source": "herdr:opencode",
                        "value": "ses_aaaa",
                    },
                    "agent_status": "idle",
                    "cwd": "/tmp/a",
                    "focused": False,
                    "pane_id": "w1:p1",
                    "terminal_title_stripped": "First",
                },
                {
                    "agent": "claude",
                    "agent_session": {
                        "agent": "claude",
                        "kind": "id",
                        "source": "herdr:claude",
                        "value": "0f1e2d3c-1111-2222-3333-444455556666",
                    },
                    "agent_status": "working",
                    "cwd": "/tmp/b",
                    "focused": True,
                    "pane_id": "w1:p2",
                    "terminal_title_stripped": "Focused",
                },
            ]
        },
        "type": "agent_list",
    }
)


def make_settings(**overrides) -> Settings:
    base = dict(
        herdr_bin="herdr-fake",
        glm_api_key=None,
        glm_base_url="https://example.invalid/",
        glm_model="glm-5",
        tts_home="/tmp/herdr-brain-test-tts",
        tts_bin="/tmp/herdr-brain-test-tts/bin/herdr-tts",
        tts_voice="elvira",
        tts_rate="+0%",
        tts_timeout_s=10,
        audio_dir="/tmp/audio",
        prompt_timeout_ms=5_000,
        max_tool_rounds=4,
        approval_timeout_s=60,
        screen_lines=40,
        stt_model="small",
        stt_device="auto",
        stt_compute="auto",
        stt_warmup=False,  # tests: never start the warmup thread / touch the model
    )
    base.update(overrides)
    return Settings(**base)


class FakeRunner:
    """Records invocations and returns canned results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(cmd, result[0], stdout=result[1], stderr=result[2])


class TestParseAgentList:
    def test_parses_ndjson_line(self):
        agents = parse_agent_list(NDJSON_LINE + "\n")
        assert len(agents) == 2
        assert agents[0] == AgentInfo(
            pane_id="w1:p1",
            agent="opencode",
            status="idle",
            session_kind="id",
            session_value="ses_aaaa",
            cwd="/tmp/a",
            title="First",
            focused=False,
        )
        assert agents[1].focused is True

    def test_tolerates_noise_lines(self):
        agents = parse_agent_list("garbage\n" + NDJSON_LINE + "\nnot json {\n")
        assert len(agents) == 2

    def test_empty_output(self):
        assert parse_agent_list("") == []


class TestPickActive:
    def test_prefers_focused(self):
        agents = parse_agent_list(NDJSON_LINE)
        assert pick_active(agents).pane_id == "w1:p2"

    def test_falls_back_to_working_then_first(self):
        first = AgentInfo("p1", "opencode", "idle", "id", "ses_1", "/c", "A", False)
        second = AgentInfo("p2", "opencode", "working", "id", "ses_2", "/c", "B", False)
        third = AgentInfo("p3", "opencode", "done", "id", "ses_3", "/c", "C", False)
        assert pick_active([first, second, third]).pane_id == "p2"
        assert pick_active([first, third]).pane_id == "p1"

    def test_none_when_empty(self):
        assert pick_active([]) is None


class TestSanitizePromptText:
    def test_strips_newlines(self):
        assert sanitize_prompt_text("line one\nline two\r\nline three\r") == "line one line two line three"

    def test_collapses_whitespace(self):
        assert sanitize_prompt_text("  a   b  ") == "a b"


class TestHerdrClient:
    def test_list_agents_builds_expected_command(self):
        runner = FakeRunner(results=[(0, NDJSON_LINE, "")])
        client = HerdrClient(make_settings(), runner=runner)
        agents = client.list_agents()
        assert [a.pane_id for a in agents] == ["w1:p1", "w1:p2"]
        assert runner.calls[0]["cmd"][:3] == ["herdr-fake", "agent", "list"]

    def test_nonzero_exit_raises(self):
        runner = FakeRunner(results=[(1, "", "boom")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="boom"):
            client.list_agents()

    def test_missing_binary_raises(self):
        runner = FakeRunner(results=[FileNotFoundError("nope")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="failed to execute"):
            client.list_agents()

    def test_read_screen_clamps_lines_and_sets_flags(self):
        runner = FakeRunner(results=[(0, "screen text", "")])
        client = HerdrClient(make_settings(), runner=runner)
        text = client.read_screen("w1:p2", n_lines=500)
        assert text == "screen text"
        cmd = runner.calls[0]["cmd"]
        assert cmd == [
            "herdr-fake", "agent", "read", "w1:p2",
            "--source", "visible",
            "--lines", "60",
            "--format", "text",
        ]

    def test_send_prompt_sanitizes_and_uses_verified_flags(self):
        runner = FakeRunner(results=[(0, "submitted", "")])
        client = HerdrClient(make_settings(), runner=runner)
        result = client.send_prompt("w1:p2", "run the tests\nplease", timeout_ms=4_000)
        cmd = runner.calls[0]["cmd"]
        assert cmd[:5] == ["herdr-fake", "agent", "prompt", "w1:p2", "run the tests please"]
        assert cmd[5:] == ["--wait", "--until", "done", "--timeout", "4000"]
        assert result == {"ok": True, "status": "done", "output": "submitted"}

    def test_send_prompt_rejects_empty_text(self):
        client = HerdrClient(make_settings(), runner=FakeRunner(results=[]))
        with pytest.raises(HerdrError, match="empty"):
            client.send_prompt("p1", "   \n")

    @pytest.mark.parametrize(
        ("output", "expected_status", "expected_ok"),
        [
            ("agent_blocked", "blocked", False),
            ("agent_prompt_stalled", "stalled", False),
            ("timeout", "timeout", False),
            ("done", "done", True),
        ],
    )
    def test_send_prompt_status_parsing(self, output, expected_status, expected_ok):
        runner = FakeRunner(results=[(0, output, "")])
        client = HerdrClient(make_settings(), runner=runner)
        result = client.send_prompt("p1", "hello")
        assert result["status"] == expected_status
        assert result["ok"] is expected_ok
