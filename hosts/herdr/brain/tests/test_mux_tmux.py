"""tmux adapter: grid parsing, screen reads, key writes, refusals.

Safety: every subprocess invocation goes through RecordedRunner with
recorded fixtures under tests/fixtures/mux/tmux/. No test ever talks to a
real tmux server or writes into a live terminal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.mux import MuxRefusal, TmuxAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "mux"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class RecordedRunner:
    """Replays recorded CLI output in call order, pinning each argv.

    Each step is ``(argv_prefix, returncode, stdout, stderr)`` matched
    against the arguments AFTER the binary, or an Exception instance to
    raise. A call that does not match the next step's prefix fails loudly,
    so the recorded script doubles as an exact command pin.
    """

    def __init__(self, *steps):
        self.steps = list(steps)
        self.remaining = list(steps)
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), **kwargs})
        if not self.remaining:
            raise AssertionError(f"unexpected subprocess call: {cmd}")
        step = self.remaining.pop(0)
        if isinstance(step, Exception):
            raise step
        prefix, rc, out, err = step
        argv = tuple(cmd[1:])
        if argv[: len(prefix)] != tuple(prefix):
            raise AssertionError(
                f"expected argv starting with {list(prefix)}, got {cmd}"
            )
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)


def adapter(runner: RecordedRunner, **kwargs) -> TmuxAdapter:
    return TmuxAdapter(bin_path="tmux-test", runner=runner, **kwargs)


class TestListAgents:
    def test_parses_recorded_grid(self):
        runner = RecordedRunner(
            (("list-panes",), 0, fixture("tmux/list-panes.txt"), "")
        )
        agents = adapter(runner).list_agents()
        assert agents == [
            # focused: pane_active=1 AND window_active=1 AND session attached
            AgentInfo("%3", "shell", "unknown", "", "", "/Users/bruno/Code/repo",
                      "opencode: chat", True),
            # active pane of its window, but not the attached session's focus
            AgentInfo("%5", "shell", "unknown", "", "", "/Users/bruno",
                      "repl (python)", False),
            # its session has no attached client
            AgentInfo("%7", "shell", "unknown", "", "", "/tmp", "zsh", False),
        ]

    def test_pins_exact_command_and_format_string(self):
        runner = RecordedRunner(
            (("list-panes",), 0, fixture("tmux/list-panes.txt"), "")
        )
        adapter(runner).list_agents()
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "list-panes", "-a", "-F", TmuxAdapter.LIST_FORMAT,
        ]
        assert "#{pane_id}" in TmuxAdapter.LIST_FORMAT
        assert "#{session_attached}" in TmuxAdapter.LIST_FORMAT

    def test_active_agent_picks_the_focused_pane(self):
        runner = RecordedRunner(
            (("list-panes",), 0, fixture("tmux/list-panes.txt"), "")
        )
        active = adapter(runner).active_agent()
        assert active is not None and active.pane_id == "%3"

    def test_skips_malformed_lines(self):
        raw = "%3\tmain\t@1\t/repo\tt\t1\t1\t1\nnoise without tabs\n"
        runner = RecordedRunner((("list-panes",), 0, raw, ""))
        agents = adapter(runner).list_agents()
        assert [a.pane_id for a in agents] == ["%3"]


class TestReadScreen:
    def test_visible_plain_text(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), "")
        )
        text = adapter(runner).read_screen("%3")
        assert text == "\n".join(fixture("tmux/capture-visible.txt").splitlines())
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "capture-pane", "-p", "-t", "%3",
        ]

    def test_visible_ansi_adds_escape_flag(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.ansi"), "")
        )
        text = adapter(runner).read_screen("%3", as_ansi=True)
        assert "\x1b[1m$\x1b[0m" in text
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "capture-pane", "-p", "-t", "%3", "-e",
        ]

    def test_recent_reads_history_with_backlook_flag(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-recent.txt"), "")
        )
        text = adapter(runner).read_screen("%3", n_lines=5, source="recent")
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "capture-pane", "-p", "-t", "%3", "-S", "-5",
        ]
        assert text.splitlines() == [f"line {n:02d}" for n in range(8, 13)]

    def test_recent_clamps_to_the_backlog_cap(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-recent.txt"), "")
        )
        adapter(runner).read_screen("%3", n_lines=5000, source="recent")
        assert runner.calls[0]["cmd"][-1] == "-120"

    def test_visible_defaults_to_screen_lines(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-recent.txt"), "")
        )
        text = adapter(runner, screen_lines=4).read_screen("%3")
        assert text.splitlines() == ["line 09", "line 10", "line 11", "line 12"]


class TestSendKeys:
    def test_translates_neutral_spelling_to_tmux(self):
        runner = RecordedRunner((("send-keys",), 0, "", ""))
        result = adapter(runner).send_keys("%3", ["down", "Enter"])
        assert result == {"ok": True, "error": ""}
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "send-keys", "-t", "%3", "Down", "Enter",
        ]

    def test_rejects_non_dialog_keys_before_any_spawn(self):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).send_keys("%3", ["ctrl+c", "up"])
        assert excinfo.value.reason == "refused"
        assert excinfo.value.capability == "keys"
        assert "ctrl+c" in str(excinfo.value)
        assert runner.calls == []  # guard runs BEFORE the multiplexer

    def test_rejects_empty_and_overlong_sequences(self):
        runner = RecordedRunner()
        mux = adapter(runner)
        with pytest.raises(MuxRefusal, match="empty"):
            mux.send_keys("%3", [])
        with pytest.raises(MuxRefusal, match="too many keys"):
            mux.send_keys("%3", ["up"] * 9)
        assert runner.calls == []

    def test_cli_failure_returns_not_ok_and_never_raises(self):
        runner = RecordedRunner((("send-keys",), 1, "", "pane boom"))
        result = adapter(runner).send_keys("%3", ["enter"])
        assert result["ok"] is False
        assert "pane boom" in result["error"]


class TestPaneRun:
    def test_types_literal_text_then_enter_as_two_calls(self):
        payload = "echo hi; rm -rf '$(whoami)' & echo \"later\""
        runner = RecordedRunner(
            (("send-keys", "-t", "%3", "-l"), 0, "", ""),
            (("send-keys",), 0, "", ""),
        )
        adapter(runner).pane_run("%3", payload)
        first, second = runner.calls
        # ONE argv element with -l: no shell sits between the brain and
        # tmux, so ;, $() and quotes stay literal text.
        assert first["cmd"] == ["tmux-test", "send-keys", "-t", "%3", "-l", payload]
        assert second["cmd"] == ["tmux-test", "send-keys", "-t", "%3", "Enter"]

    def test_empty_command_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).pane_run("%3", "   ")
        assert runner.calls == []


class TestSendPrompt:
    def test_submits_sanitized_line_and_reports_unknown(self):
        runner = RecordedRunner(
            (("send-keys", "-t", "%3", "-l"), 0, "", ""),
            (("send-keys",), 0, "", ""),
        )
        result = adapter(runner).send_prompt("%3", "línea uno\nlínea dos")
        assert result == {"ok": True, "status": "unknown", "output": ""}
        assert runner.calls[0]["cmd"][-1] == "línea uno línea dos"
        assert runner.calls[1]["cmd"] == ["tmux-test", "send-keys", "-t", "%3", "Enter"]
        assert len(runner.calls) == 2  # fire-and-forget: no completion wait

    def test_empty_prompt_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).send_prompt("%3", "  ")
        assert runner.calls == []


class TestCreateTab:
    def test_parses_window_and_pane_ids(self):
        runner = RecordedRunner(
            (("new-window",), 0, fixture("tmux/new-window.txt"), "")
        )
        created = adapter(runner).create_tab("Build")
        assert created == {"tab_id": "@7", "pane_id": "%23"}
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "new-window", "-P", "-F", "#{window_id} #{pane_id}",
            "-n", "Build",
        ]

    def test_cwd_appends_flag(self):
        runner = RecordedRunner(
            (("new-window",), 0, fixture("tmux/new-window.txt"), "")
        )
        adapter(runner).create_tab("Build", cwd="/repo")
        assert runner.calls[0]["cmd"][-2:] == ["-c", "/repo"]

    def test_unparseable_output_raises(self):
        runner = RecordedRunner((("new-window",), 0, "created ok\n", ""))
        with pytest.raises(HerdrError, match="missing window/pane"):
            adapter(runner).create_tab("Build")


class TestRefusedSurfaces:
    def test_start_agent_refuses_without_spawning(self):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).start_agent("build", "opencode", "%3")
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == "agent_start"
        assert runner.calls == []

    def test_agent_wait_refuses_without_spawning(self):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).agent_wait("%3")
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == "agent_detection"
        assert runner.calls == []


class TestFailureMapping:
    def test_no_server_running_is_unreachable(self):
        runner = RecordedRunner(
            (("list-panes",), 1, "", "no server running on /tmp/tmux-1000/default")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"

    def test_missing_pane_is_gone(self):
        runner = RecordedRunner(
            (("capture-pane",), 1, "", "can't find pane: %9")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).read_screen("%9")
        assert excinfo.value.reason == "gone"
        assert excinfo.value.capability == "ansi_screen"

    def test_other_failures_are_refused(self):
        runner = RecordedRunner(
            (("send-keys", "-t", "%3", "-l"), 1, "", "unknown option")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).pane_run("%3", "ls")
        assert excinfo.value.reason == "refused"

    def test_missing_binary_is_unreachable(self):
        runner = RecordedRunner(FileNotFoundError("tmux-test"))
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"
        assert "tmux-test" in str(excinfo.value)

    def test_timeout_is_unreachable(self):
        runner = RecordedRunner(
            subprocess.TimeoutExpired(cmd=["tmux-test"], timeout=15)
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"


class TestCapabilityMatrix:
    def test_declared_cells_and_facts(self):
        caps = TmuxAdapter(bin_path="tmux-test").capabilities()
        assert caps.supported == frozenset(
            {"pane_grid", "ansi_screen", "scrollback", "prompt_submit", "keys", "tabs"}
        )
        # No agent surface is declared: tmux cannot detect or start agents.
        assert not caps.supports("agent_detection")
        assert not caps.supports("session_ref")
        assert not caps.supports("agent_start")
        assert caps.fact("topology_latency") == "bounded:1000"
        assert caps.fact("spaces") == "many"
        for name in caps.supported:
            assert caps.evidence[name], f"cell {name} claims support without evidence"
