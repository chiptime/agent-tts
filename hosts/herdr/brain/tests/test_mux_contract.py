"""The mux port contract: refusal shape, capability declarations, herdr composition.

The herdr adapter is the specification: every method delegates verbatim to
the HerdrClient it wraps, so observed behavior (results, exceptions,
arguments) is byte-identical and today's callers keep working unchanged.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from herdr_brain.herdr import AgentInfo, HerdrClient, HerdrError
from herdr_brain.mux import (
    CAPABILITY_NAMES,
    REFUSAL_REASONS,
    Capabilities,
    HerdrMuxAdapter,
    MuxAdapter,
    MuxRefusal,
    TmuxAdapter,
    ZellijAdapter,
)

AGENT = AgentInfo(
    pane_id="w1:p9",
    agent="opencode",
    status="working",
    session_kind="id",
    session_value="ses_test0000session",
    cwd="/repo",
    title="OpenCode",
    focused=True,
)

# Unique sentinel objects: delegation must return the very same value the
# client produced, not an equal-looking copy.
SCREEN_RESULT = "screen body"
KEYS_RESULT = {"ok": True, "error": ""}
PROMPT_RESULT = {"ok": True, "status": "done", "output": "did it"}
TAB_RESULT = {"tab_id": "w2:t1", "pane_id": "w2:p1"}
START_RESULT = "w2:p1"


class RecordingClient:
    """HerdrClient double that records every call's bound arguments."""

    def __init__(self, fail_with: BaseException | None = None):
        self.calls: list = []
        self.fail_with = fail_with

    def _record(self, name, args):
        self.calls.append((name, args))
        if self.fail_with is not None:
            raise self.fail_with

    def list_agents(self):
        self._record("list_agents", ())
        return [AGENT]

    def active_agent(self):
        self._record("active_agent", ())
        return AGENT

    def read_screen(
        self, pane_id, n_lines=None, source="visible", as_ansi=False
    ):
        self._record("read_screen", (pane_id, n_lines, source, as_ansi))
        return SCREEN_RESULT

    def send_keys(self, pane_id, keys):
        self._record("send_keys", (pane_id, tuple(keys)))
        return KEYS_RESULT

    def pane_run(self, pane_id, command):
        self._record("pane_run", (pane_id, command))
        return None

    def send_prompt(self, pane_id, text, timeout_ms=None):
        self._record("send_prompt", (pane_id, text, timeout_ms))
        return PROMPT_RESULT

    def create_tab(self, label, cwd=None):
        self._record("create_tab", (label, cwd))
        return TAB_RESULT

    def start_agent(self, name, kind, pane_id, timeout_ms=None):
        self._record("start_agent", (name, kind, pane_id, timeout_ms))
        return START_RESULT

    def agent_wait(self, pane_id, until="idle", timeout_ms=15_000):
        self._record("agent_wait", (pane_id, until, timeout_ms))
        return None


# (name, adapter call, recorded bound arguments, expected result)
DELEGATION_CASES = [
    ("list_agents", lambda a: a.list_agents(), ("list_agents", ()), [AGENT]),
    ("active_agent", lambda a: a.active_agent(), ("active_agent", ()), AGENT),
    (
        "read_screen defaults",
        lambda a: a.read_screen("w1:p9"),
        ("read_screen", ("w1:p9", None, "visible", False)),
        SCREEN_RESULT,
    ),
    (
        "read_screen explicit",
        lambda a: a.read_screen("w1:p9", 30, "recent", True),
        ("read_screen", ("w1:p9", 30, "recent", True)),
        SCREEN_RESULT,
    ),
    (
        "send_keys",
        lambda a: a.send_keys("w1:p9", ["enter"]),
        ("send_keys", ("w1:p9", ("enter",))),
        KEYS_RESULT,
    ),
    (
        "pane_run",
        lambda a: a.pane_run("w1:p9", "oa '/my repo'"),
        ("pane_run", ("w1:p9", "oa '/my repo'")),
        None,
    ),
    (
        "send_prompt explicit timeout",
        lambda a: a.send_prompt("w1:p9", "hi", 1000),
        ("send_prompt", ("w1:p9", "hi", 1000)),
        PROMPT_RESULT,
    ),
    (
        "send_prompt default timeout",
        lambda a: a.send_prompt("w1:p9", "hi"),
        ("send_prompt", ("w1:p9", "hi", None)),
        PROMPT_RESULT,
    ),
    (
        "create_tab with cwd",
        lambda a: a.create_tab("Build", "/repo"),
        ("create_tab", ("Build", "/repo")),
        TAB_RESULT,
    ),
    (
        "create_tab default cwd",
        lambda a: a.create_tab("Build"),
        ("create_tab", ("Build", None)),
        TAB_RESULT,
    ),
    (
        "start_agent default timeout",
        lambda a: a.start_agent("build", "opencode", "w2:p1"),
        ("start_agent", ("build", "opencode", "w2:p1", None)),
        START_RESULT,
    ),
    (
        "agent_wait defaults",
        lambda a: a.agent_wait("w2:p1"),
        ("agent_wait", ("w2:p1", "idle", 15_000)),
        None,
    ),
    (
        "agent_wait explicit",
        lambda a: a.agent_wait("w2:p1", until="done", timeout_ms=5000),
        ("agent_wait", ("w2:p1", "done", 5000)),
        None,
    ),
]


@pytest.mark.parametrize(
    "name,call,recorded,expected", DELEGATION_CASES, ids=[c[0] for c in DELEGATION_CASES]
)
def test_herdr_adapter_delegates_verbatim(name, call, recorded, expected):
    client = RecordingClient()
    adapter = HerdrMuxAdapter(client)
    result = call(adapter)
    assert result == expected
    assert client.calls == [recorded]


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.read_screen("w1:p9"),
        lambda a: a.send_prompt("w1:p9", "hi"),
        lambda a: a.agent_wait("w2:p1"),
        lambda a: a.list_agents(),
    ],
    ids=["read_screen", "send_prompt", "agent_wait", "list_agents"],
)
def test_herdr_adapter_propagates_the_same_exception_instance(call):
    boom = HerdrError("cli exploded")
    adapter = HerdrMuxAdapter(RecordingClient(fail_with=boom))
    with pytest.raises(HerdrError) as excinfo:
        call(adapter)
    assert excinfo.value is boom


API_METHODS = [
    "list_agents",
    "active_agent",
    "read_screen",
    "send_keys",
    "pane_run",
    "send_prompt",
    "create_tab",
    "start_agent",
    "agent_wait",
]


@pytest.mark.parametrize("method", API_METHODS)
def test_signatures_match_herdr_client_exactly(method):
    # Drop-in proof: the adapter's signature IS the client's signature.
    assert inspect.signature(getattr(HerdrMuxAdapter, method)) == inspect.signature(
        getattr(HerdrClient, method)
    )


def _tmux() -> TmuxAdapter:
    return TmuxAdapter(bin_path="tmux-test")


def _zellij() -> ZellijAdapter:
    return ZellijAdapter(bin_path="zellij-test")


@pytest.mark.parametrize(
    "factory", [lambda: HerdrMuxAdapter(RecordingClient()), _tmux, _zellij],
    ids=["herdr", "tmux", "zellij"],
)
def test_every_adapter_satisfies_the_port_protocol(factory):
    assert isinstance(factory(), MuxAdapter)


def test_bare_herdr_client_is_not_a_mux_adapter(settings):
    # No capabilities(): the concrete client alone does not speak the port.
    assert not isinstance(HerdrClient(settings), MuxAdapter)


class TestRefusal:
    def test_reason_vocabulary_is_closed(self):
        assert REFUSAL_REASONS == frozenset(
            {"unsupported", "gone", "refused", "unreachable"}
        )

    def test_refusal_is_a_herdr_error_so_callers_keep_degrading(self):
        # Today's callers degrade through `except HerdrError`; the refusal
        # must flow through that same net untouched.
        try:
            raise MuxRefusal("unsupported", "agent_start", "tmux cannot start agents")
        except HerdrError as exc:
            assert isinstance(exc, MuxRefusal)
            assert "unsupported" in str(exc)
            assert "agent_start" in str(exc)
            assert "tmux cannot start agents" in str(exc)

    @pytest.mark.parametrize("reason", ["maybe", "", "Unsupported", "gone "])
    def test_unknown_reason_is_rejected(self, reason):
        with pytest.raises(ValueError, match="unknown refusal reason"):
            MuxRefusal(reason, "keys")

    def test_fields_expose_reason_capability_detail(self):
        refusal = MuxRefusal("gone", "pane_grid", "pane %9 vanished")
        assert refusal.reason == "gone"
        assert refusal.capability == "pane_grid"
        assert refusal.detail == "pane %9 vanished"


class TestCapabilities:
    def test_claiming_without_probe_evidence_is_rejected(self):
        with pytest.raises(ValueError, match="probe evidence"):
            Capabilities(
                supported=frozenset({"keys"}), evidence={}, facts={}
            )

    def test_unknown_capability_name_is_rejected(self):
        with pytest.raises(ValueError, match="unknown capability"):
            Capabilities(
                supported=frozenset({"telepathy"}),
                evidence={"telepathy": "guessed"},
                facts={},
            )

    def test_supports_and_fact_helpers(self):
        caps = Capabilities(
            supported=frozenset({"keys"}),
            evidence={"keys": "pinned by fixture"},
            facts={"spaces": "many"},
        )
        assert caps.supports("keys") is True
        assert caps.supports("tabs") is False
        assert caps.fact("spaces") == "many"
        assert caps.fact("topology_latency") == ""  # absent facts default to ""

    def test_capabilities_are_immutable(self):
        caps = Capabilities(
            supported=frozenset({"keys"}), evidence={"keys": "x"}, facts={}
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.supported = frozenset({"tabs"})  # type: ignore[misc]


class TestHerdrCapabilityMatrix:
    def test_herdr_declares_the_full_matrix_with_evidence(self):
        caps = HerdrMuxAdapter(RecordingClient()).capabilities()
        assert caps.supported == frozenset(CAPABILITY_NAMES)
        assert caps.fact("topology_latency") == "push"
        assert caps.fact("spaces") == "many"
        for name in CAPABILITY_NAMES:
            assert caps.evidence[name], f"cell {name} claims support without evidence"
