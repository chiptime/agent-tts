"""zellij adapter: grid parsing, screen dump, explicit refusals.

The port's rule here: the zellij CLI surface this port relies on is
listing panes and dumping the visible screen. Everything else (logical
keys, submit, tabs, agent surfaces, scrollback) is unverifiable through
that surface, so the adapter REFUSES instead of approximating.

Safety: every subprocess invocation goes through RecordedRunner with
recorded fixtures under tests/fixtures/mux/zellij/. No test ever talks to
a real zellij session.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from herdr_brain.herdr import AgentInfo, HerdrError
from herdr_brain.mux import MuxRefusal, ZellijAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "mux"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class RecordedRunner:
    """Replays recorded CLI output in call order, pinning each argv."""

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


def adapter(runner: RecordedRunner, **kwargs) -> ZellijAdapter:
    return ZellijAdapter(bin_path="zellij-test", runner=runner, **kwargs)


class TestListAgents:
    def test_parses_recorded_pane_array(self):
        runner = RecordedRunner(
            (("list-panes",), 0, fixture("zellij/list-panes.json"), "")
        )
        agents = adapter(runner).list_agents()
        assert agents == [
            AgentInfo("1", "shell", "unknown", "", "", "/Users/bruno/Code/repo",
                      "opencode", True),
            AgentInfo("2", "shell", "unknown", "", "", "", "repl (python)", False),
        ]
        assert runner.calls[0]["cmd"] == [
            "zellij-test", "list-panes", "--all", "--json",
        ]

    def test_tolerates_missing_optional_fields(self):
        runner = RecordedRunner((("list-panes",), 0, '[{"id": 9}]', ""))
        agents = adapter(runner).list_agents()
        assert agents == [
            AgentInfo("9", "shell", "unknown", "", "", "", "", False)
        ]

    @pytest.mark.parametrize("raw", ["", "\n", "[]"])
    def test_empty_listings_return_no_agents(self, raw):
        runner = RecordedRunner((("list-panes",), 0, raw, ""))
        assert adapter(runner).list_agents() == []

    def test_unparseable_output_raises(self):
        runner = RecordedRunner((("list-panes",), 0, "not json{", ""))
        with pytest.raises(HerdrError, match="unparseable"):
            adapter(runner).list_agents()

    def test_active_agent_picks_the_focused_pane(self):
        runner = RecordedRunner(
            (("list-panes",), 0, fixture("zellij/list-panes.json"), "")
        )
        active = adapter(runner).active_agent()
        assert active is not None and active.pane_id == "1"


class TestReadScreen:
    def test_ansi_dump_keeps_styling(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.ansi"), "")
        )
        text = adapter(runner).read_screen("1", as_ansi=True)
        assert "\x1b[32mstatus:\x1b[0m ok" in text
        assert runner.calls[0]["cmd"] == [
            "zellij-test", "action", "dump-screen", "--pane-id", "1", "--ansi",
        ]

    def test_plain_dump_drops_ansi_flag(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), "")
        )
        text = adapter(runner).read_screen("1")
        assert text == "\n".join(fixture("zellij/dump-screen.txt").splitlines())
        assert runner.calls[0]["cmd"] == [
            "zellij-test", "action", "dump-screen", "--pane-id", "1",
        ]

    def test_clamps_to_screen_lines(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), "")
        )
        text = adapter(runner, screen_lines=2).read_screen("1")
        assert text.splitlines() == ["status: ok", "$"]

    def test_scrollback_source_is_refused(self):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).read_screen("1", source="recent")
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == "scrollback"
        assert runner.calls == []


class TestRefusedSurfaces:
    """No zellij write path is verified: each surface refuses, cleanly."""

    @pytest.mark.parametrize(
        "call,capability",
        [
            (lambda a: a.send_keys("1", ["enter"]), "keys"),
            (lambda a: a.pane_run("1", "ls"), "keys"),
            (lambda a: a.send_prompt("1", "hi"), "prompt_submit"),
            (lambda a: a.create_tab("Build"), "tabs"),
            (lambda a: a.start_agent("build", "opencode", "1"), "agent_start"),
            (lambda a: a.agent_wait("1"), "agent_detection"),
        ],
        ids=["send_keys", "pane_run", "send_prompt", "create_tab",
             "start_agent", "agent_wait"],
    )
    def test_write_surfaces_refuse_instead_of_approximating(self, call, capability):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            call(adapter(runner))
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == capability
        assert runner.calls == []


class TestFailureMapping:
    def test_no_active_session_is_unreachable(self):
        runner = RecordedRunner(
            (("list-panes",), 1, "", "error: no active session found")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"

    def test_other_failures_are_refused(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 1, "", "boom")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).read_screen("1")
        assert excinfo.value.reason == "refused"

    def test_missing_binary_is_unreachable(self):
        runner = RecordedRunner(FileNotFoundError("zellij-test"))
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"


class TestCapabilityMatrix:
    def test_declared_cells_and_facts(self):
        caps = ZellijAdapter(bin_path="zellij-test").capabilities()
        # Mirror only: the grid and a styled screen. Nothing else is
        # verified through zellij's CLI, so nothing else is declared.
        assert caps.supported == frozenset({"pane_grid", "ansi_screen"})
        assert caps.fact("spaces") == "one"
        assert caps.fact("topology_latency") == "bounded:12000"
        for name in caps.supported:
            assert caps.evidence[name], f"cell {name} claims support without evidence"
