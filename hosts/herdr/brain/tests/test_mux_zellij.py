"""zellij adapter: grid parsing, screen dump, verified submit, keys.

The port's rule here: the zellij CLI surface this port relies on is
listing panes (``action list-panes``), dumping the visible screen,
submitting one prompt line (``write-chars`` + ``write 10``) and logical
keys (``action send-keys``). Everything else (bare text typing, tabs,
agent surfaces, scrollback) is refused instead of approximating.

LIVE-PINNED (parity wave 2 S6): every shape here was captured against a
real isolated zellij 0.45.1 session (fixtures under
tests/fixtures/mux/zellij/ are real-shape, synthetic content). The live
probes also proved zellij's honesty edge: a dump-screen or write-chars
to a NONEXISTENT pane exits 0 with no output — reads stay honest only
through the receipt's before/after comparison, never through exit codes.

Safety: every subprocess invocation in this module goes through
RecordedRunner with recorded fixtures. The live counterparts ride their
own isolated session (tests/test_mux_zellij_live.py).
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
        # REAL shape (zellij 0.45.1): ids repeat across kinds (a plugin
        # pane and a terminal pane may share ``id``), cwd rides
        # ``pane_cwd``, and the fixture's plugin panes (including a
        # focused floating wizard) must never surface as agents.
        runner = RecordedRunner(
            (("action", "list-panes"), 0, fixture("zellij/list-panes.json"), "")
        )
        agents = adapter(runner).list_agents()
        assert agents == [
            AgentInfo("0", "shell", "unknown", "", "", "/tmp/proj",
                      "Pane #1", True),
            AgentInfo("1", "shell", "unknown", "", "", "", "cat", False),
        ]
        assert runner.calls[0]["cmd"] == [
            "zellij-test", "action", "list-panes", "--all", "--json",
        ]

    def test_plugin_panes_are_filtered_even_when_focused(self):
        # The real grid carries a focused First-Run-Setup-Wizard plugin
        # pane; picking it as the active agent would answer a wizard.
        runner = RecordedRunner(
            (("action", "list-panes"), 0, fixture("zellij/list-panes.json"), "")
        )
        agents = adapter(runner).list_agents()
        assert all(agent.pane_id not in ("2", "3") for agent in agents)
        active = adapter(
            RecordedRunner(
                (("action", "list-panes"), 0, fixture("zellij/list-panes.json"), "")
            )
        ).active_agent()
        assert active is not None and active.pane_id == "0"

    def test_tolerates_missing_optional_fields(self):
        runner = RecordedRunner((("action", "list-panes"), 0, '[{"id": 9}]', ""))
        agents = adapter(runner).list_agents()
        assert agents == [
            AgentInfo("9", "shell", "unknown", "", "", "", "", False)
        ]

    @pytest.mark.parametrize("raw", ["", "\n", "[]"])
    def test_empty_listings_return_no_agents(self, raw):
        runner = RecordedRunner((("action", "list-panes"), 0, raw, ""))
        assert adapter(runner).list_agents() == []

    def test_unparseable_output_raises(self):
        runner = RecordedRunner((("action", "list-panes"), 0, "not json{", ""))
        with pytest.raises(HerdrError, match="unparseable"):
            adapter(runner).list_agents()


class TestReadScreen:
    def test_ansi_dump_keeps_styling(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-screen.ansi"), "")
        )
        text = adapter(runner).read_screen("1", as_ansi=True)
        # Real 0.45.1 styling: a reset prefix per dumped line, not
        # per-word color spans.
        assert "\x1b[mrun the probes" in text
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
            (("action", "dump-screen"), 0, fixture("zellij/dump-tall-before.txt"), "")
        )
        text = adapter(runner, screen_lines=2).read_screen("1")
        assert text.splitlines() == ["probes: 05 ok", "sh-5.3$"]

    def test_scrollback_source_is_refused(self):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).read_screen("1", source="recent")
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == "scrollback"
        assert runner.calls == []


class TestSendKeys:
    """parity wave 2 S6: logical keys ride ``action send-keys`` — the
    verb exists in 0.45.1 and its dialect was probed live: named keys
    are capitalized (``Enter``, ``Esc``, ``Up``) and chords are
    SPACE-separated (``Ctrl u``); ``Ctrl+u`` exits 2 with ``Invalid
    key``. The allowlist guard runs BEFORE any spawn, exactly like the
    tmux adapter's.

    S12 A1: a send that exits 0 proves nothing on zellij — a
    nonexistent pane answers exit 0 silently (live-pinned edge). The
    adapter therefore verifies the pane against a FRESH
    ``action list-panes`` listing before the send (a miss refuses
    ``gone`` without spawning the write) and again after (a vanishing
    pane downgrades the reply to an honest unknown), so ``ok: True``
    is only ever returned with the pane answering both listings.
    """

    LIST = ("action", "list-panes")

    def test_translates_neutral_spelling_to_zellij(self):
        runner = RecordedRunner(
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
            (("action", "send-keys"), 0, "", ""),
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
        )
        result = adapter(runner).send_keys("1", ["down", "Enter"])
        assert result == {"ok": True, "error": ""}
        assert runner.calls[0]["cmd"] == [
            "zellij-test", "action", "list-panes", "--all", "--json",
        ]
        assert runner.calls[1]["cmd"] == [
            "zellij-test", "action", "send-keys", "--pane-id", "1",
            "Down", "Enter",
        ]

    def test_chords_are_space_separated_in_zellij(self):
        runner = RecordedRunner(
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
            (("action", "send-keys"), 0, "", ""),
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
        )
        adapter(runner).send_keys("1", ["ctrl+u", "ctrl+k"])
        assert runner.calls[1]["cmd"] == [
            "zellij-test", "action", "send-keys", "--pane-id", "1",
            "Ctrl u", "Ctrl k",
        ]

    def test_digits_are_literal_characters(self):
        runner = RecordedRunner(
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
            (("action", "send-keys"), 0, "", ""),
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
        )
        result = adapter(runner).send_keys("1", ["1"])
        assert result == {"ok": True, "error": ""}
        assert runner.calls[1]["cmd"][-1] == "1"

    def test_rejects_non_dialog_keys_before_any_spawn(self):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).send_keys("1", ["ctrl+c", "up"])
        assert excinfo.value.reason == "refused"
        assert excinfo.value.capability == "keys"
        assert "ctrl+c" in str(excinfo.value)
        assert runner.calls == []  # guard runs BEFORE the multiplexer

    def test_rejects_empty_and_overlong_sequences(self):
        runner = RecordedRunner()
        mux = adapter(runner)
        with pytest.raises(MuxRefusal, match="empty"):
            mux.send_keys("1", [])
        with pytest.raises(MuxRefusal, match="too many keys"):
            mux.send_keys("1", ["up"] * 9)
        assert runner.calls == []  # and before any listing

    def test_cli_failure_returns_not_ok_and_never_raises(self):
        runner = RecordedRunner(
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
            (("action", "send-keys"), 1, "", "boom"),
        )
        result = adapter(runner).send_keys("1", ["enter"])
        assert result["ok"] is False
        assert "boom" in result["error"]

    def test_unlisted_pane_refuses_gone_without_sending(self):
        # S12 A1 RED: a nonexistent pane would get `send-keys` spawned
        # at it (exiting 0 silently) and answer `ok: True`. The fresh
        # listing is the honest oracle: a miss refuses `gone` and the
        # keys are never spawned at a target zellij does not list.
        runner = RecordedRunner(
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
        )
        result = adapter(runner).send_keys("999", ["enter"])
        assert result["ok"] is False
        assert "gone" in result["error"]
        assert "999" in result["error"]
        assert len(runner.calls) == 1 and "list-panes" in runner.calls[0]["cmd"]

    def test_pane_vanishing_between_checks_is_never_ok(self):
        # S12 A1 RED: pane present at the pre-listing, gone at the
        # post-listing — the send ran into a dying pane, so the keys
        # may or may not have landed. Honesty here is `ok: False`
        # (unknown), never a fabricated positive.
        runner = RecordedRunner(
            (self.LIST, 0, fixture("zellij/list-panes.json"), ""),
            (("action", "send-keys"), 0, "", ""),
            (self.LIST, 0, "", ""),  # fresh listing: empty after the vanish
        )
        result = adapter(runner).send_keys("1", ["enter"])
        assert result["ok"] is False
        assert "unknown" in result["error"]

    def test_listing_failure_is_never_ok(self):
        # S12 A1 RED: without a listing there is no oracle at all, so a
        # vanished zellij session can never fabricate `ok: True` — the
        # failure keeps the client-side context (unreachable).
        runner = RecordedRunner(
            (self.LIST, 1, "", "There is no active session!"),
        )
        result = adapter(runner).send_keys("1", ["enter"])
        assert result["ok"] is False
        assert "unreachable" in result["error"]


class TestSendPrompt:
    """f7f S2: zellij is a WRITE surface, with a verified receipt.

    ``write-chars`` types the sanitized line as ONE argv element (no
    shell sits between the brain and the CLI, so quotes, ``$`` and
    backticks stay literal), ``write 10`` submits it (Enter as the LF
    byte), and ``dump-screen`` re-reads before and after feed the SAME
    classifier herdr rides. zellij vouches the WRITE only, so the claim
    is neutral: a moved body is ``sent``, a surviving echo
    ``typed_not_submitted``, an unreadable screen ``unknown`` — never
    ``unsupported``, and never ``effective`` (zellij exposes no agent
    state). LIVE-PINNED on zellij 0.45.1 (parity wave 2 S6).

    Live honesty edge: a nonexistent pane fails SILENTLY (exit 0, no
    output) — the empty before/after dumps keep the receipt ``unknown``
    instead of a fabricated positive.
    """

    def test_write_chars_enter_and_re_read_receipts_sent(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-empty.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
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
        # Real cat-pane shape: the round-trip re-prints the text at the
        # cursor, so the echo heuristic cannot decide early and the
        # poll takes BOTH of its immediate reads (no sleeps).
        assert len(runner.calls) == 5

    def test_surviving_echo_receipts_typed_not_submitted(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-empty.txt"), ""),
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

    def test_a_silent_nonexistent_pane_receipts_unknown(self):
        # Live finding: write-chars/dump-screen on a pane that does not
        # exist exit 0 with no output. Empty before and after keep the
        # receipt honestly `unknown` — never a fabricated positive.
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, "", ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, "", ""),
            (("action", "dump-screen"), 0, "", ""),
        )
        result = adapter(runner).send_prompt("99", "run the probes")
        assert result == {
            "ok": False,
            "status": "unknown",
            "output": "sin evidencia suficiente para clasificar el envío",
        }

    def test_unreadable_screen_receipts_unknown_with_detail(self):
        # A pane that cannot be READ (the dump fails outright) must stay
        # honest too: `unknown` with a detail, never a fabricated
        # positive.
        runner = RecordedRunner(
            (("action", "dump-screen"), 1, "", "session boom"),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 1, "", "session boom"),
            (("action", "dump-screen"), 1, "", "session boom"),
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
            (("action", "dump-screen"), 0, fixture("zellij/dump-empty.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
        )
        result = adapter(runner).send_prompt("1", "run the probes")
        assert result["status"] in ("sent", "typed_not_submitted", "unknown")
        assert result["status"] == "sent"

    def test_pins_the_exact_write_commands_and_targeting(self):
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-empty.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-prompt-after-sent.txt"), ""),
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
            ["zellij-test", "action", "dump-screen", "--pane-id", "1"],
        ]

    def test_quote_breaker_payload_stays_one_literal_argv_element(self):
        payload = "rm -rf '$(whoami)' \"keep\" `id`"
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-empty.txt"), ""),
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
            (("action", "dump-screen"), 0, fixture("zellij/dump-empty.txt"), ""),
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
        # adapters share the budgeted-tail read pattern): the
        # classification path reads the FULL grid, never the display
        # tail. Real zellij dumps are compact (empty rows are trimmed),
        # so the fixtures carry real-shaped content instead of the
        # blank-padded grids tmux produces.
        runner = RecordedRunner(
            (("action", "dump-screen"), 0, fixture("zellij/dump-tall-before.txt"), ""),
            (("action", "write-chars"), 0, "", ""),
            (("action", "write"), 0, "", ""),
            (("action", "dump-screen"), 0, fixture("zellij/dump-tall-after-sent.txt"), ""),
        )
        result = adapter(runner, screen_lines=4).send_prompt("1", "run the probes")
        assert result["status"] == "sent"
        assert result["ok"] is True


class TestSendText:
    """Lead decision on parity wave 2 (fleet parity-w2): bare typing is
    opened for zellij too, consistent with the operator's tmux S5
    decision — ``write-chars`` WITHOUT the Enter ride, over the same
    verified primitives. The contract mirrors herdr's ``send_text``
    (the specification the other adapters are measured against):
    sanitized to ONE line, empty refused before any spawn, a CLI
    failure raises instead of fabricating a receipt.

    Live honesty edge (0.45.1): a nonexistent pane answers rc 0 with
    no output — send_text claims nothing; the dialog recipe re-reads
    the screen itself, which is where a silent pane shows up.
    """

    def test_types_exact_bytes_without_enter(self):
        payload = "draft $answer \"with quotes\" `and backticks`;"
        runner = RecordedRunner(
            (("action", "write-chars"), 0, "", ""),
        )
        adapter(runner).send_text("1", payload)
        # ONE call, ONE argv element: the exact bytes reach zellij as
        # literal text and NO submit ride follows (no ``write 10``).
        assert [call["cmd"] for call in runner.calls] == [
            ["zellij-test", "action", "write-chars", "--pane-id", "1", payload],
        ]

    def test_multiline_text_is_sanitized_before_the_write(self):
        # A literal newline is a real Enter in the pane: typing must
        # never submit by accident.
        runner = RecordedRunner((("action", "write-chars"), 0, "", ""))
        adapter(runner).send_text("1", "línea uno\nlínea dos")
        assert runner.calls[0]["cmd"][-1] == "línea uno línea dos"

    def test_option_like_text_is_passed_as_data(self):
        runner = RecordedRunner((("action", "write-chars"), 0, "", ""))
        adapter(runner).send_text("1", "--help")
        assert runner.calls[0]["cmd"][-2:] == ["--", "--help"]

    def test_empty_text_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).send_text("1", "  ")
        assert runner.calls == []

    def test_cli_failure_raises(self):
        runner = RecordedRunner((("action", "write-chars"), 1, "", "session boom"))
        with pytest.raises(MuxRefusal):
            adapter(runner).send_text("1", "hi;")
        assert len(runner.calls) == 1


class TestRefusedSurfaces:
    """No other zellij write path is opened: each surface refuses."""

    @pytest.mark.parametrize(
        "call,capability",
        [
            (lambda a: a.pane_run("1", "ls"), "keys"),
            (lambda a: a.create_tab("Build"), "tabs"),
            (lambda a: a.start_agent("build", "opencode", "1"), "agent_start"),
            (lambda a: a.agent_wait("1"), "agent_detection"),
        ],
        ids=["pane_run", "create_tab", "start_agent", "agent_wait"],
    )
    def test_write_surfaces_refuse_instead_of_approximating(self, call, capability):
        runner = RecordedRunner()
        with pytest.raises(MuxRefusal) as excinfo:
            call(adapter(runner))
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == capability
        assert runner.calls == []


class TestProbe:
    """``--version`` is a client-side flag and answers with no session,
    so reachability comes from one session-state roundtrip on the
    adapter's OWN pinned verb — ``action list-panes --all --json``, the
    same call ``list_agents`` rides; ``--version`` stays the version
    source, and an unreachable probe may still carry the version.
    LIVE-PINNED (parity wave 2 S6): without a session the real 0.45.1
    CLI exits 1 with ``There is no active session!`` on stderr —
    unreachable, exactly what the probe must report."""

    def test_version_without_a_session_is_not_reachable(self):
        runner = RecordedRunner(
            (("--version",), 0, "zellij 0.45.1\n", ""),
            (("action", "list-panes"), 1, "",
             "There is no active session!"),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is False
        assert "no active session" in probe.detail
        # The client still names its version: --version is the version
        # source.
        assert probe.version == "0.45.1"

    def test_reachable_when_the_session_state_answers(self):
        runner = RecordedRunner(
            (("--version",), 0, "zellij 0.45.1\n", ""),
            (("action", "list-panes"), 0, fixture("zellij/list-panes.json"), ""),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is True
        assert probe.version == "0.45.1"
        assert "list-panes" in probe.detail
        assert runner.calls[0]["cmd"] == ["zellij-test", "--version"]
        assert runner.calls[1]["cmd"] == [
            "zellij-test", "action", "list-panes", "--all", "--json",
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
    """The two REAL absent-session shapes (both captured live on
    0.45.1, stderr, rc 1) must be `unreachable`, never `refused`."""

    def test_action_without_a_session_is_unreachable(self):
        runner = RecordedRunner(
            (("action", "list-panes"), 1, "",
             "There is no active session!")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"

    def test_list_sessions_plural_shape_is_unreachable_too(self):
        # The other real shape ("No active zellij sessions found.") was
        # the misclassified one: it does not contain the singular
        # "no active session" substring the old mapping knew.
        runner = RecordedRunner(
            (("action", "list-panes"), 1, "",
             "No active zellij sessions found.")
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

    @pytest.mark.parametrize("code", [0, 1])
    def test_missing_named_session_is_unreachable_even_with_zero_exit(self, code):
        runner = RecordedRunner(
            (("action", "list-panes"), code, "other\n",
             "Session 'missing' not found. The following sessions are active:\n")
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"

    def test_selection_error_is_not_a_successful_key_write(self):
        runner = RecordedRunner(
            (("action", "list-panes"), 0, fixture("zellij/list-panes.json"), ""),
            (("action", "send-keys"), 0, "other\n",
             "Please specify the session name to send actions to. "
             "The following sessions are active:\n")
        )
        assert adapter(runner).send_keys("1", ["enter"])["ok"] is False

    def test_missing_binary_is_unreachable(self):
        runner = RecordedRunner(FileNotFoundError("zellij-test"))
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).list_agents()
        assert excinfo.value.reason == "unreachable"


class TestCapabilityMatrix:
    def test_declared_cells_and_facts(self):
        caps = ZellijAdapter(bin_path="zellij-test").capabilities()
        # Grid, styled screen, the verified prompt submit and — S6 —
        # logical keys over ``action send-keys``.
        assert caps.supported == frozenset(
            {"pane_grid", "ansi_screen", "prompt_submit", "keys"}
        )
        assert caps.fact("spaces") == "one"
        assert caps.fact("topology_latency") == "bounded:12000"
        for name in caps.supported:
            assert caps.evidence[name], f"cell {name} claims support without evidence"
