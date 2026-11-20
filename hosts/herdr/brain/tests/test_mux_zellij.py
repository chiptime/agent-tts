"""zellij adapter: grid parsing, screen dump, verified submit, refusals.

The port's rule here: the zellij CLI surface this port relies on is
listing panes, dumping the visible screen and (f7f S2) submitting one
prompt line. Everything else (logical keys, bare text typing, tabs,
agent surfaces, scrollback) is unverifiable through that surface, so
the adapter REFUSES instead of approximating.

UNVERIFIED live (f7f D2): zellij is not installed on this host. The
write-path fixtures under tests/fixtures/mux/zellij/ are CLI-shape
derived (write-chars / write 10 / dump-screen) and pinned by the tests
below; they must be re-pinned against a live session before the write
path is trusted in production.

Safety: every subprocess invocation goes through RecordedRunner with
recorded fixtures under tests/fixtures/mux/zellij/. No test ever talks
to a real zellij session.
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


class TestSendPrompt:
    """f7f S2: zellij becomes a WRITE surface, with a verified receipt.

    ``write-chars`` types the sanitized line as ONE argv element (no
    shell sits between the brain and the CLI, so quotes, ``$`` and
    backticks stay literal), ``write 10`` submits it (Enter as the LF
    byte), and ``dump-screen`` re-reads before and after feed the SAME
    classifier herdr rides. zellij vouches the WRITE only, so the claim
    is neutral: a moved body is ``sent``, a surviving echo
    ``typed_not_submitted``, an unreadable screen ``unknown`` — never
    ``unsupported``, and never ``effective`` (zellij exposes no agent
    state). CLI-shape derived, UNVERIFIED live (see module docstring).
    """

    def test_write_chars_enter_and_re_read_receipts_sent(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
        )
        result = adapter(runner).send_prompt("1", "run the probes")
        # classify_send's own detail, verbatim: the same classifier, not
        # a second one.
        assert result == {
            "ok": True,
            "status": "sent",
            "output": "enviado; la pantalla del agente avanzó",
        }
        # The body moved and the echo is gone: the poll stops after one
        # re-read (immediate evidence, no completion wait).
        assert len(runner.calls) == 4

    def test_surviving_echo_receipts_typed_not_submitted(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-typed.txt"), ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-typed.txt"), ""),
        )
        result = adapter(runner).send_prompt("1", "run the probes")
        assert result == {
            "ok": False,
            "status": "typed_not_submitted",
            "output": "el texto quedó escrito en la línea de entrada sin enviarse",
        }

    def test_unreadable_screen_receipts_unknown_with_detail(self):
        # A pane that cannot be read must stay honest: `unknown` with a
        # detail, never a fabricated positive.
        runner = RecordedRunner(
            (("action", "dump-screen"), 1, "", "no such pane: 1"),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 1, "", "no such pane: 1"),
            (("action", "dump-screen"), 1, "", "no such pane: 1"),
        )
        result = adapter(runner).send_prompt("1", "run the probes")
        assert result == {
            "ok": False,
            "status": "unknown",
            "output": "no se pudo leer la pantalla del agente",
        }

    def test_never_claims_effective(self):
        # Even a finished-looking round-trip is `sent`: zellij exposes
        # no agent-turn state, so completion is never claimed.
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
        )
        result = adapter(runner).send_prompt("1", "run the probes")
        assert result["status"] in ("sent", "typed_not_submitted", "unknown")
        assert result["status"] == "sent"

    def test_pins_the_exact_write_commands_and_targeting(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
        )
        adapter(runner).send_prompt("1", "run the probes")
        assert [call["cmd"] for call in runner.calls] == [
            ["zellij-test", "action", "dump-screen", "--pane-id", "1"],
            # The pane is targeted exactly like dump-screen targets it.
            ["zellij-test", "action", "write-chars", "--pane-id", "1",
             "run the probes"],
            ["zellij-test", "action", "write", "--pane-id", "1", "10"],
            ["zellij-test", "action", "dump-screen", "--pane-id", "1"],
        ]

    def test_quote_breaker_payload_stays_one_literal_argv_element(self):
        payload = "rm -rf '$(whoami)' \"keep\" `id`"
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-typed.txt"), ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-typed.txt"), ""),
        )
        adapter(runner).send_prompt("1", payload)
        write = runner.calls[1]["cmd"]
        # ONE argv element: quotes, $() and backticks reach zellij as
        # literal text, never as CLI syntax.
        assert write == [
            "zellij-test", "action", "write-chars", "--pane-id", "1", payload,
        ]

    def test_multiline_text_is_sanitized_before_the_write(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
        )
        adapter(runner).send_prompt("1", "línea uno\nlínea dos")
        # A literal newline is a real Enter in the pane: sanitized to
        # ONE line before it reaches write-chars.
        assert runner.calls[1]["cmd"][-1] == "línea uno línea dos"

    def test_empty_prompt_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).send_prompt("1", "  ")
        assert runner.calls == []

    def test_a_pane_taller_than_the_screen_budget_still_classifies(self):
        # Finding B (live on tmux 3.8, mirrored here because both
        # adapters share the budgeted-tail read pattern): a pane taller
        # than the display budget holds its prompt rows at the TOP with
        # blank rows below, so the budgeted TAIL read saw only blanks
        # and classified an honest-but-wrong `unknown`. The
        # classification path reads the FULL grid; read_screen's public
        # tail semantics stay the display budget. UNVERIFIED live
        # (zellij not installed, f7f D2).
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-tall-before.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-tall-after-sent.txt"), ""),
        )
        result = adapter(runner, screen_lines=4).send_prompt("1", "run the probes")
        assert result["status"] == "sent"
        assert result["ok"] is True


class TestRefusedSurfaces:
    """No other zellij write path is verified: each surface refuses."""

    @pytest.mark.parametrize(
        "call,capability",
        [
            (lambda a: a.send_keys("1", ["enter"]), "keys"),
            (lambda a: a.pane_run("1", "ls"), "keys"),
            (lambda a: a.create_tab("Build"), "tabs"),
            (lambda a: a.start_agent("build", "opencode", "1"), "agent_start"),
            (lambda a: a.agent_wait("1"), "agent_detection"),
        ],
        ids=["send_keys", "pane_run", "create_tab",
             "start_agent", "agent_wait"],
    )
    def test_write_surfaces_refuse_instead_of_approximating(self, call, capability):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            call(adapter(runner))
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == capability
        assert runner.calls == []

    def test_send_text_stays_refused_outside_s2(self):
        # f7f S2 opens ONLY prompt_submit: bare text typing (the dialog
        # recipe's typing half) stays refused `unsupported` until it has
        # its own verified shape.
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).send_text("1", "draft text")
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == "send_text"
        assert runner.calls == []


class TestProbe:
    """Finding A extended to zellij (parent decision): ``--version`` is
    a client-side flag and answers with no session, so reachability
    comes from one session-state roundtrip on the adapter's OWN pinned
    verb — ``list-panes --all --json``, the same call ``list_agents``
    rides; ``--version`` stays the version source, and an unreachable
    probe may still carry the version. UNVERIFIED live (zellij not
    installed, f7f D2): the state-touch shape is CLI-shape derived like
    the rest of this adapter's surface."""

    def test_version_without_a_session_is_not_reachable(self):
        runner = RecordedRunner(
            (("--version",), 0, "zellij 0.44.2\n", ""),
            (("list-panes",), 1, "",
             "error connecting to session executor socket"),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is False
        assert "error connecting" in probe.detail
        # The client still names its version: --version is the version
        # source.
        assert probe.version == "0.44.2"

    def test_reachable_when_the_session_state_answers(self):
        runner = RecordedRunner(
            (("--version",), 0, "zellij 0.44.2\n", ""),
            (("list-panes",), 0, fixture("zellij/list-panes.json"), ""),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is True
        assert probe.version == "0.44.2"
        assert "list-panes" in probe.detail
        assert runner.calls[0]["cmd"] == ["zellij-test", "--version"]
        assert runner.calls[1]["cmd"] == [
            "zellij-test", "list-panes", "--all", "--json",
        ]

    def test_missing_binary_is_not_reachable(self):
        runner = RecordedRunner(FileNotFoundError("zellij-test"))
        probe = adapter(runner).probe()
        assert probe.reachable is False
        assert "zellij-test" in probe.detail

    def test_nonzero_version_exit_is_not_reachable(self):
        runner = RecordedRunner((("--version",), 1, "", "boom"))
        probe = adapter(runner).probe()
        assert probe.reachable is False


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
        # Grid, styled screen and (f7f S2) the verified prompt submit.
        # The write cell's evidence names its UNVERIFIED-live status.
        assert caps.supported == frozenset({"pane_grid", "ansi_screen", "prompt_submit"})
        assert caps.fact("spaces") == "one"
        assert caps.fact("topology_latency") == "bounded:12000"
        for name in caps.supported:
            assert caps.evidence[name], f"cell {name} claims support without evidence"
