"""Model per pane (parity F7a S3).

brain never imports agent_tts: the model comes from the engine CLI
``agent-tts last-model`` run with the engine python (same seam as the STT
engine). Unknown harnesses, missing sessions and any CLI failure are an
honest ``None``; results are cached per (harness, session).
"""
from __future__ import annotations

import subprocess

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.pane_model import PaneModelResolver
from herdr_brain.tools import BrainTools

from tests.conftest import SETTINGS_KWARGS


class FakeRunner:
    def __init__(self, stdout="claude-opus-5-5\n", returncode=0, exc=None):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode
        self.exc = exc

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if self.exc is not None:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def resolver(runner, clock=None, enabled=True):
    return PaneModelResolver(
        python_resolver=lambda: "/venv/bin/python",
        runner=runner,
        clock=clock or Clock(),
        background=False,
        enabled=enabled,
    )


def test_runs_the_engine_last_model_cli_and_returns_its_first_line():
    runner = FakeRunner(stdout="claude-opus-5-5\nnoise\n")
    assert resolver(runner).model("claude", "abc-123") == "claude-opus-5-5"
    assert runner.calls == [[
        "/venv/bin/python", "-m", "agent_tts.cli", "last-model",
        "--agent", "claude", "--session-id", "abc-123",
    ]]


@pytest.mark.parametrize("alias,canonical", [("oa", "opencode"), ("agy", "antigravity")])
def test_harness_aliases_resolve_to_the_engine_name(alias, canonical):
    runner = FakeRunner(stdout="m\n")
    assert resolver(runner).model(alias, "s1") == "m"
    assert runner.calls[0][5] == canonical


@pytest.mark.parametrize("agent", ["pi", "grok", "kiro", "muse", "cursor", "hermes", "", None])
def test_harness_without_extractor_is_none_without_running_anything(agent):
    runner = FakeRunner()
    assert resolver(runner).model(agent, "s1") is None
    assert runner.calls == []


def test_missing_session_is_none_without_running_anything():
    runner = FakeRunner()
    assert resolver(runner).model("claude", "") is None
    assert runner.calls == []


@pytest.mark.parametrize("runner", [
    FakeRunner(returncode=1, stdout=""),
    FakeRunner(stdout="   \n"),
    FakeRunner(exc=subprocess.TimeoutExpired(["x"], 5)),
    FakeRunner(exc=OSError("no such file")),
])
def test_cli_failures_are_none(runner):
    assert resolver(runner).model("codex", "s1") is None


def test_unavailable_engine_python_is_none():
    runner = FakeRunner()
    res = PaneModelResolver(
        python_resolver=lambda: None, runner=runner, clock=Clock(), background=False,
        enabled=True,
    )
    assert res.model("claude", "s1") is None
    assert runner.calls == []


def test_results_are_cached_until_the_ttl_expires():
    runner = FakeRunner(stdout="m1\n")
    clock = Clock()
    res = resolver(runner, clock)
    assert res.model("claude", "s1") == "m1"
    runner.stdout = "m2\n"
    clock.now += 10
    assert res.model("claude", "s1") == "m1"
    assert len(runner.calls) == 1
    clock.now += res.ttl_s
    assert res.model("claude", "s1") == "m2"
    assert len(runner.calls) == 2


def test_disabled_resolver_never_runs():
    runner = FakeRunner()
    assert resolver(runner, enabled=False).model("claude", "s1") is None
    assert runner.calls == []


def test_off_env_disables_the_default_resolver(monkeypatch):
    monkeypatch.setenv("HERDR_BRAIN_PANE_MODEL", "off")
    assert PaneModelResolver().enabled is False


def test_background_mode_answers_cached_then_refreshes_without_blocking():
    runner = FakeRunner(stdout="m1\n")
    spawned = []
    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=Clock(),
        background=True, spawn=spawned.append, enabled=True,
    )
    assert res.model("claude", "s1") is None  # nothing known yet: never blocks
    assert len(spawned) == 1
    assert res.model("claude", "s1") is None  # one refresh in flight, not two
    assert len(spawned) == 1
    spawned[0]()
    assert res.model("claude", "s1") == "m1"


def test_background_spawn_failure_answers_none_and_does_not_wedge_the_key():
    runner = FakeRunner(stdout="m1\n")
    attempts = []

    def flaky_spawn(job):
        attempts.append(job)
        if len(attempts) == 1:
            raise RuntimeError("can't start new thread")

    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=Clock(),
        background=True, spawn=flaky_spawn, enabled=True,
    )
    assert res.model("claude", "s1") is None  # spawn raised: honest None, no raise
    assert res.model("claude", "s1") is None  # key not stuck inflight: retries spawn
    assert len(attempts) == 2
    attempts[1]()
    assert res.model("claude", "s1") == "m1"


def test_raising_clock_keeps_the_never_raise_promise_in_background_mode():
    runner = FakeRunner(stdout="m1\n")
    spawned = []

    def broken_clock():
        raise RuntimeError("clock unavailable")

    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=broken_clock,
        background=True, spawn=spawned.append, enabled=True,
    )
    assert res.model("claude", "s1") is None
    spawned[0]()  # _refresh survives the raising clock and clears inflight
    assert res.model("claude", "s1") == "m1"  # cached answer, still no raise


def test_raising_clock_keeps_the_never_raise_promise_in_foreground_mode():
    runner = FakeRunner(stdout="m1\n")

    def broken_clock():
        raise RuntimeError("clock unavailable")

    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=broken_clock,
        background=False, enabled=True,
    )
    assert res.model("claude", "s1") == "m1"  # lookup ran; broken clock is not a raise


# ── wiring: /view status and /herd entries carry the model ──────────────────


class FixedModels:
    def __init__(self, models):
        self.models = models

    def model(self, agent, session_id):
        return self.models.get((agent, session_id))


CLAUDE = AgentInfo(
    pane_id="w1:p1", agent="claude", status="idle", session_kind="id",
    session_value="abc", cwd="/repo", title="Claude", focused=True,
)
PI = AgentInfo(
    pane_id="w1:p2", agent="pi", status="idle", session_kind="id",
    session_value="def", cwd="/repo", title="Pi", focused=False,
)


def make_tools(make_stub, settings, models):
    stub = make_stub(agents=[CLAUDE, PI])
    return BrainTools(settings, herdr=stub, pane_model=FixedModels(models))


def test_view_status_carries_the_pane_model(make_stub, settings):
    tools = make_tools(make_stub, settings, {("claude", "abc"): "claude-opus-5-5"})
    assert tools.agent_view("w1:p1")["status"]["model"] == "claude-opus-5-5"
    assert tools.agent_view("w1:p2")["status"]["model"] is None


def test_state_carries_the_active_pane_model(make_stub, settings):
    tools = make_tools(make_stub, settings, {("claude", "abc"): "claude-opus-5-5"})
    assert tools.status_payload()["model"] == "claude-opus-5-5"


def test_herd_entries_carry_the_model(make_stub, settings):
    tools = make_tools(make_stub, settings, {("claude", "abc"): "claude-opus-5-5"})
    by_pane = {entry["pane_id"]: entry for entry in tools.herd()}
    assert by_pane["w1:p1"]["model"] == "claude-opus-5-5"
    assert by_pane["w1:p2"]["model"] is None


def test_inactive_status_has_a_null_model(make_stub, settings):
    stub = make_stub(agents=[])
    tools = BrainTools(settings, herdr=stub, pane_model=FixedModels({}))
    assert tools.status_payload()["model"] is None
