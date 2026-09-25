"""Unit tests for the tab/agent-start CLI primitives.

Safety: every subprocess invocation is mocked. No test ever creates a real
tab or starts a real agent on the live daemon.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import HerdrClient, HerdrError, parse_success_result


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
        stt_warmup=False,
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


def tab_created_envelope(tab_id: str = "w2:t1", pane_id: str = "w2:p1") -> str:
    """A success envelope shaped like the pinned tab_created API variant."""
    return json.dumps(
        {
            "id": "cli:tab:create",
            "result": {
                "type": "tab_created",
                "tab": {
                    "tab_id": tab_id,
                    "workspace_id": "w2",
                    "number": 3,
                    "label": "Refactor",
                    "focused": True,
                    "pane_count": 1,
                    "agent_status": "unknown",
                },
                "root_pane": {
                    "pane_id": pane_id,
                    "terminal_id": "t1",
                    "workspace_id": "w2",
                    "tab_id": tab_id,
                    "focused": True,
                    "agent_status": "idle",
                    "revision": 1,
                },
            },
        }
    )


def agent_started_envelope(pane_id: str = "w2:p1", agent: str = "refactor") -> str:
    """A success envelope shaped like the pinned agent_started API variant."""
    return json.dumps(
        {
            "id": "cli:agent:start",
            "result": {
                "type": "agent_started",
                "agent": {
                    "pane_id": pane_id,
                    "agent": agent,
                    "agent_status": "idle",
                    "focused": True,
                },
                "argv": ["/usr/bin/opencode"],
            },
        }
    )


class TestParseSuccessResult:
    def test_parses_result_object(self):
        result = parse_success_result(tab_created_envelope())
        assert result["type"] == "tab_created"

    def test_tolerates_noise_lines(self):
        raw = "herdr 1.2.3\n" + tab_created_envelope() + "\nnot json {\n"
        assert parse_success_result(raw)["type"] == "tab_created"

    def test_unparseable_output_raises(self):
        with pytest.raises(HerdrError, match="unparseable"):
            parse_success_result("")


class TestCreateTab:
    def test_builds_expected_command_and_parses_ids(self):
        runner = FakeRunner(results=[(0, tab_created_envelope(), "")])
        client = HerdrClient(make_settings(), runner=runner)
        created = client.create_tab("Refactor del login")
        assert created == {"tab_id": "w2:t1", "pane_id": "w2:p1"}
        assert runner.calls[0]["cmd"] == [
            "herdr-fake", "tab", "create", "--label", "Refactor del login",
        ]

    def test_cwd_flag_appended_when_given(self):
        runner = FakeRunner(results=[(0, tab_created_envelope(), "")])
        client = HerdrClient(make_settings(), runner=runner)
        client.create_tab("Refactor", cwd="/repo")
        assert runner.calls[0]["cmd"][-2:] == ["--cwd", "/repo"]

    def test_nonzero_exit_raises(self):
        runner = FakeRunner(results=[(1, "", "tab boom")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="tab boom"):
            client.create_tab("Refactor")

    def test_missing_keys_raise(self):
        raw = json.dumps({"id": "x", "result": {"type": "tab_created", "tab": {}}})
        runner = FakeRunner(results=[(0, raw, "")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="missing tab_id/root_pane"):
            client.create_tab("Refactor")

    def test_unparseable_output_raises(self):
        runner = FakeRunner(results=[(0, "created ok (no json)", "")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="unparseable"):
            client.create_tab("Refactor")


class TestStartAgent:
    def test_builds_expected_command_and_returns_pane(self):
        runner = FakeRunner(results=[(0, agent_started_envelope(), "")])
        client = HerdrClient(make_settings(), runner=runner)
        pane = client.start_agent("refactor", "opencode", "w2:p1")
        assert pane == "w2:p1"
        assert runner.calls[0]["cmd"] == [
            "herdr-fake", "agent", "start", "refactor",
            "--kind", "opencode",
            "--pane", "w2:p1",
            "--timeout", "30000",
        ]

    def test_custom_timeout_flag_and_subprocess_budget(self):
        runner = FakeRunner(results=[(0, agent_started_envelope(), "")])
        client = HerdrClient(make_settings(), runner=runner)
        client.start_agent("refactor", "claude", "w2:p1", timeout_ms=120_000)
        assert runner.calls[0]["cmd"][-2:] == ["--timeout", "120000"]
        assert runner.calls[0]["timeout"] == 150.0  # 120s readiness + 30s margin

    def test_args_appended_after_separator(self):
        runner = FakeRunner(results=[(0, agent_started_envelope(), "")])
        client = HerdrClient(make_settings(), runner=runner)
        client.start_agent(
            "refactor", "opencode", "w2:p1",
            args=["attach", "http://localhost:4096", "--dir", "/repo"],
        )
        assert runner.calls[0]["cmd"][-5:] == [
            "--", "attach", "http://localhost:4096", "--dir", "/repo",
        ]

    def test_no_args_keeps_invocation_byte_identical(self):
        runner = FakeRunner(results=[
            (0, agent_started_envelope(), ""),
            (0, agent_started_envelope(), ""),
        ])
        client = HerdrClient(make_settings(), runner=runner)
        client.start_agent("refactor", "claude", "w2:p1")
        assert "--" not in runner.calls[0]["cmd"]
        client.start_agent("refactor", "claude", "w2:p1", args=[])
        assert "--" not in runner.calls[1]["cmd"]
        assert runner.calls[0]["cmd"] == runner.calls[1]["cmd"]

    def test_nonzero_exit_raises(self):
        runner = FakeRunner(results=[(1, "", "agent boom")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="agent boom"):
            client.start_agent("refactor", "opencode", "w2:p1")

    def test_missing_agent_info_raises(self):
        raw = json.dumps({"id": "x", "result": {"type": "agent_started"}})
        runner = FakeRunner(results=[(0, raw, "")])
        client = HerdrClient(make_settings(), runner=runner)
        with pytest.raises(HerdrError, match="missing agent info"):
            client.start_agent("refactor", "opencode", "w2:p1")


class TestStartAgentOaComposition:
    """The composed argv for the user's `oa` entry point (opencode attach).

    Drives BrainTools.create_session against the FakeRunner-backed client
    so the FINAL CLI invocation is pinned end to end at the subprocess
    boundary (no stubbed HerdrClient in between).
    """

    def _tools(self, runner) -> "BrainTools":
        from herdr_brain.tools import BrainTools

        settings = make_settings()
        return BrainTools(settings, herdr=HerdrClient(settings, runner=runner))

    def test_opencode_with_cwd_composes_attach_argv(self):
        runner = FakeRunner(results=[
            (0, tab_created_envelope(), ""),
            (0, agent_started_envelope(), ""),
        ])
        self._tools(runner).create_session("opencode", "Build", cwd="/repo")
        assert runner.calls[1]["cmd"] == [
            "herdr-fake", "agent", "start", "build",
            "--kind", "opencode",
            "--pane", "w2:p1",
            "--timeout", "30000",
            "--", "attach", "http://localhost:4096", "--dir", "/repo",
        ]

    def test_opencode_without_cwd_omits_dir(self):
        runner = FakeRunner(results=[
            (0, tab_created_envelope(), ""),
            (0, agent_started_envelope(), ""),
        ])
        self._tools(runner).create_session("opencode", "Build")
        assert runner.calls[1]["cmd"][-3:] == [
            "--", "attach", "http://localhost:4096",
        ]

    def test_non_opencode_kind_has_no_separator_nor_args(self):
        runner = FakeRunner(results=[
            (0, tab_created_envelope(), ""),
            (0, agent_started_envelope(), ""),
        ])
        self._tools(runner).create_session("claude", "Docs")
        assert runner.calls[1]["cmd"] == [
            "herdr-fake", "agent", "start", "docs",
            "--kind", "claude",
            "--pane", "w2:p1",
            "--timeout", "30000",
        ]
        assert "--" not in runner.calls[1]["cmd"]
