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

    def test_read_screen_can_keep_the_styling(self):
        """``as_ansi`` is how the selection of a dialog survives the read."""
        runner = FakeRunner(results=[(0, "styled", "")])
        client = HerdrClient(make_settings(), runner=runner)

        assert client.read_screen("w1:p2", as_ansi=True) == "styled"

        assert runner.calls[0]["cmd"][-2:] == ["--format", "ansi"]

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

    def test_send_prompt_reports_done_on_a_clean_exit(self):
        runner = FakeRunner(results=[(0, "submitted", "")])
        client = HerdrClient(make_settings(), runner=runner)
        result = client.send_prompt("p1", "hello")
        assert result == {"ok": True, "status": "done", "output": "submitted"}

    @pytest.mark.parametrize(
        ("code", "expected_status"),
        [
            ("agent_blocked", "blocked"),
            ("agent_prompt_stalled", "stalled"),
            ("timeout", "timeout"),
        ],
    )
    def test_send_prompt_reads_a_submission_outcome_from_the_error_envelope(
        self, code, expected_status
    ):
        """herdr reports these on STDERR with exit 1 (verified on 0.9.1).

        ``herdr agent prompt --wait`` exits non-zero with an EMPTY stdout
        and ``{"error": {"code": ...}}`` on stderr when the caller timeout
        expires — after the prompt was already written to the pane. Reading
        the exit code as the verdict turns a delivered prompt into a failure
        and invites a resend that duplicates it.
        """
        envelope = json.dumps(
            {"error": {"code": code, "message": "nope"}, "id": "cli:agent:prompt"}
        )
        runner = FakeRunner(results=[(1, "", envelope)])
        client = HerdrClient(make_settings(), runner=runner)
        result = client.send_prompt("p1", "hello")
        assert result["status"] == expected_status
        assert result["ok"] is False

    def test_send_prompt_still_raises_on_an_unrelated_cli_error(self):
        envelope = json.dumps(
            {"error": {"code": "agent_not_found", "message": "gone"}, "id": "cli:agent:prompt"}
        )
        runner = FakeRunner(results=[(1, "", envelope)])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="agent_not_found"):
            client.send_prompt("p1", "hello")

    def test_send_prompt_raises_when_the_error_carries_no_envelope(self):
        """A non-zero exit with unparseable stderr must stay a FAILURE.

        Only a recognized code turns an exit into a submission outcome; a
        crash whose stderr is plain text (or empty) must never be reported
        as a delivered prompt, which would invite the resend this whole
        fix exists to prevent.
        """
        runner = FakeRunner(results=[(1, "", "herdr exploded")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="herdr exploded"):
            client.send_prompt("p1", "hello")

    def test_send_prompt_defaults_to_done_when_a_clean_exit_has_no_output(self):
        """Exit 0 with empty stdout keeps the legacy default (``done``)."""
        runner = FakeRunner(results=[(0, "", "")])
        client = HerdrClient(make_settings(), runner=runner)
        assert client.send_prompt("p1", "hello") == {"ok": True, "status": "done", "output": ""}


class TestSendKeys:
    """The dialog surface: ``herdr agent send-keys`` (see herdr --skill).

    ``agent prompt`` refuses an agent waiting at a dialogue, so its dialog is
    answered with logical keys. herdr validates every key before writing.
    """

    def test_passes_each_key_as_its_own_argv(self):
        runner = FakeRunner(results=[(0, "", "")])
        client = HerdrClient(make_settings(), runner=runner)

        result = client.send_keys("w1:p2", ["right", "enter"])

        assert runner.calls[0]["cmd"] == [
            "herdr-fake", "agent", "send-keys", "w1:p2", "right", "enter",
        ]
        assert result["ok"] is True

    @pytest.mark.parametrize("keys", [["ctrl+c"], ["y"], ["enter", "rm -rf /"], ["ENTER"]])
    def test_refuses_a_key_outside_the_allowlist_without_running_anything(self, keys):
        runner = FakeRunner(results=[])
        client = HerdrClient(make_settings(), runner=runner)

        with pytest.raises(HerdrError, match="unsupported key"):
            client.send_keys("w1:p2", keys)

        assert runner.calls == []

    def test_refuses_an_empty_key_sequence(self):
        runner = FakeRunner(results=[])
        client = HerdrClient(make_settings(), runner=runner)

        with pytest.raises(HerdrError, match="empty"):
            client.send_keys("w1:p2", [])

        assert runner.calls == []

    def test_refuses_more_keys_than_the_cap(self):
        runner = FakeRunner(results=[])
        client = HerdrClient(make_settings(), runner=runner)

        with pytest.raises(HerdrError, match="too many keys"):
            client.send_keys("w1:p2", ["enter"] * 50)

        assert runner.calls == []

    def test_reports_a_cli_failure_without_raising(self):
        runner = FakeRunner(results=[(1, "", "agent_not_found")])
        client = HerdrClient(make_settings(), runner=runner)

        result = client.send_keys("w9:p9", ["enter"])

        assert result["ok"] is False
        assert "agent_not_found" in result["error"]
