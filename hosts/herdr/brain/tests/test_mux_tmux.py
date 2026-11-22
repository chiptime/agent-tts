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
    """Finding C (live, tmux 3.8): ``send-keys -l`` silently dropped a
    trailing ``;`` while exiting 0 — and the receipt even read ``sent``
    on the altered write. The write path now delivers the exact bytes
    through a PRIVATE paste buffer: the text rides stdin, never argv."""

    def test_types_through_private_buffer_then_enter(self):
        payload = "echo hi; rm -rf '$(whoami)' & echo \"later\""
        runner = RecordedRunner(
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
        )
        adapter(runner).pane_run("%3", payload)
        load, paste, delete = runner.calls
        buffer_name = load["cmd"][3]
        # Private buffer name: never a tmux default buffer, never another
        # actor's named buffer.
        assert buffer_name.startswith("herdr-paste-")
        assert load["cmd"] == ["tmux-test", "load-buffer", "-b", buffer_name, "-"]
        # The exact bytes ride STDIN, not argv: `ps` never sees them.
        assert load["input"] == payload
        # Paste and its Enter submit ride ONE client invocation (`;` is
        # tmux's own command separator): another client's command cannot
        # slot between the paste and the submit inside one round-trip.
        assert paste["cmd"] == [
            "tmux-test", "paste-buffer", "-d", "-S", "-b", buffer_name,
            "-t", "%3", ";", "send-keys", "-t", "%3", "Enter",
        ]
        # Deleted afterwards: no sensitive-text lifetime on the server's
        # shared buffer list.
        assert delete["cmd"] == ["tmux-test", "delete-buffer", "-b", buffer_name]
        # No call carries the payload inside argv.
        assert not any(payload in " ".join(c["cmd"]) for c in runner.calls)

    def test_concurrent_sends_use_private_non_colliding_buffers(self):
        names = []
        for _ in range(2):
            runner = RecordedRunner(
                (("load-buffer",), 0, "", ""),
                (("paste-buffer",), 0, "", ""),
                (("delete-buffer",), 0, "", ""),
            )
            adapter(runner).pane_run("%3", "echo hi;")
            names.append(runner.calls[0]["cmd"][3])
        # uuid names: two sends in flight on one server never share one.
        assert names[0].startswith("herdr-paste-")
        assert names[0] != names[1]

    def test_paste_failure_deletes_its_buffer_and_never_submits(self):
        runner = RecordedRunner(
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 1, "", "can't find pane: %9"),
            (("delete-buffer",), 0, "", ""),
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).pane_run("%9", "echo hi;")
        assert excinfo.value.reason == "gone"
        buffer_name = runner.calls[0]["cmd"][3]
        # Only the buffer this operation created is cleaned up.
        assert runner.calls[2]["cmd"] == [
            "tmux-test", "delete-buffer", "-b", buffer_name,
        ]
        # Nothing was pasted, so nothing is submitted: the paste-and-Enter
        # invocation failed as a whole and no other write happened.
        assert [c["cmd"][1] for c in runner.calls] == [
            "load-buffer", "paste-buffer", "delete-buffer",
        ]

    def test_load_failure_attempts_cleanup_and_propagates(self):
        runner = RecordedRunner(
            (("load-buffer",), 1, "", "buffer boom"),
            # The best-effort delete may itself fail; it must be swallowed.
            (("delete-buffer",), 1, "", "can't find buffer"),
        )
        with pytest.raises(MuxRefusal, match="buffer boom"):
            adapter(runner).pane_run("%3", "echo hi;")
        assert runner.calls[1]["cmd"][1] == "delete-buffer"

    def test_empty_command_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).pane_run("%3", "   ")
        assert runner.calls == []


class TestSendPrompt:
    """f7f S2: the fire-and-forget submit gains its verified read-back.

    capture-pane is read BEFORE and immediately AFTER the submission
    (plain text — the receipt classifier matches echo tails on unstyled
    lines, so ``-e`` would corrupt the match), and the SAME classifier
    herdr rides decides the outcome. tmux vouches the WRITE only, so the
    claim handed to the classifier is neutral: a moved body is ``sent``,
    a surviving echo ``typed_not_submitted``, an unreadable screen
    ``unknown`` — and ``effective`` (turn completion) is never claimed
    because tmux exposes no agent state.

    The write itself rides the private paste-buffer pipeline (TestPaneRun
    pins it in detail): the text rides stdin, never argv.
    """

    def test_body_movement_receipts_sent(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-sent.txt"), ""),
        )
        result = adapter(runner).send_prompt("%3", "run the probes")
        # classify_send's own detail, verbatim: the same classifier, not
        # a second one.
        assert result == {
            "ok": True,
            "status": "sent",
            "output": "enviado; la pantalla del agente avanzó",
        }
        # The body moved and the echo is gone: the poll stops after one
        # re-read (immediate evidence, no completion wait).
        assert len(runner.calls) == 5

    def test_surviving_echo_receipts_typed_not_submitted(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-typed.txt"), ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-typed.txt"), ""),
        )
        result = adapter(runner).send_prompt("%3", "run the probes")
        assert result == {
            "ok": False,
            "status": "typed_not_submitted",
            "output": "el texto quedó escrito en la línea de entrada sin enviarse",
        }

    def test_unreadable_screen_receipts_unknown_with_detail(self):
        # A pane that cannot be read (here: the pane vanishes between
        # listing and send) must stay honest: `unknown` with a detail,
        # never a fabricated positive.
        runner = RecordedRunner(
            (("capture-pane",), 1, "", "can't find pane: %3"),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 1, "", "can't find pane: %3"),
            (("capture-pane",), 1, "", "can't find pane: %3"),
        )
        result = adapter(runner).send_prompt("%3", "run the probes")
        assert result == {
            "ok": False,
            "status": "unknown",
            "output": "no se pudo leer la pantalla del agente",
        }

    def test_a_finished_looking_pane_never_claims_effective(self):
        # The after screen shows a completed round-trip ("probes ok: 3
        # passed" plus a fresh prompt), but tmux exposes no agent-turn
        # state: the receipt is `sent`, never `effective`.
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-sent.txt"), ""),
        )
        result = adapter(runner).send_prompt("%3", "run the probes")
        assert result["status"] in ("sent", "typed_not_submitted", "unknown")
        assert result["status"] == "sent"

    def test_pins_the_exact_command_sequence_with_plain_reads(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-sent.txt"), ""),
        )
        adapter(runner).send_prompt("%3", "run the probes")
        # The uuid makes exact argv pinning name-dependent: pin the read
        # and submit calls exactly, and the buffer plumbing as prefix rules.
        assert runner.calls[0]["cmd"] == ["tmux-test", "capture-pane", "-p", "-t", "%3"]
        assert runner.calls[4]["cmd"] == ["tmux-test", "capture-pane", "-p", "-t", "%3"]
        load, paste, delete = runner.calls[1], runner.calls[2], runner.calls[3]
        name = load["cmd"][3]
        assert name.startswith("herdr-paste-")
        assert load["cmd"] == ["tmux-test", "load-buffer", "-b", name, "-"]
        assert load["input"] == "run the probes"
        assert paste["cmd"] == [
            "tmux-test", "paste-buffer", "-d", "-S", "-b", name, "-t", "%3",
            ";", "send-keys", "-t", "%3", "Enter",
        ]
        assert delete["cmd"] == ["tmux-test", "delete-buffer", "-b", name]

    def test_multiline_text_is_sanitized_before_the_write(self):
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-sent.txt"), ""),
        )
        adapter(runner).send_prompt("%3", "línea uno\nlínea dos")
        # A literal newline is a real Enter in the pane: sanitized to
        # ONE line before it reaches the paste buffer.
        assert runner.calls[1]["input"] == "línea uno línea dos"

    def test_quote_breaker_payload_stays_outside_argv(self):
        payload = "rm -rf '$(whoami)' \"keep\" `id`;"
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-visible.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-typed.txt"), ""),
            (("capture-pane",), 0, fixture("tmux/prompt-after-typed.txt"), ""),
        )
        adapter(runner).send_prompt("%3", payload)
        # The exact bytes — including the trailing `;` tmux 3.8 dropped
        # from argv — ride stdin only: no shell and no tmux parser ever
        # separate, quote or eat them.
        assert runner.calls[1]["input"] == payload
        assert not any(payload in " ".join(c["cmd"]) for c in runner.calls)

    def test_empty_prompt_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).send_prompt("%3", "  ")
        assert runner.calls == []

    def test_a_pane_taller_than_the_screen_budget_still_classifies(self):
        # Finding B (live, tmux 3.8 scratch server): a fresh pane taller
        # than the display budget holds its prompt rows at the TOP with
        # blank rows below, so the budgeted TAIL read saw only blanks
        # and classified an honest-but-wrong `unknown`. The
        # classification path reads the FULL grid; read_screen's public
        # tail semantics stay the display budget.
        runner = RecordedRunner(
            (("capture-pane",), 0, fixture("tmux/capture-tall-before.txt"), ""),
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
            (("capture-pane",), 0, fixture("tmux/capture-tall-after-sent.txt"), ""),
        )
        result = adapter(runner, screen_lines=4).send_prompt("%3", "run the probes")
        assert result["status"] == "sent"
        assert result["ok"] is True


class TestSendText:
    """parity wave 2 S5 (operator choice «Admitir»): bare text typing is
    enabled over the T7 exact path — the exact bytes ride a PRIVATE paste
    buffer by stdin (never argv, so tmux 3.8 cannot swallow a trailing
    ``;``), and NO Enter is pressed: typing only, the caller commits.

    The contract mirrors herdr's ``send_text`` (the specification the
    other adapters are measured against): sanitized to ONE line, empty
    refused before any spawn, and a CLI failure raises instead of
    returning a receipt — the dialog recipe re-reads the screen itself.
    """

    def test_types_exact_bytes_without_enter(self):
        payload = "draft $answer \"with quotes\" `and backticks`;"
        runner = RecordedRunner(
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
        )
        adapter(runner).send_text("%3", payload)
        load, paste, delete = runner.calls
        name = load["cmd"][3]
        assert name.startswith("herdr-paste-")
        # The exact bytes — trailing `;` included — ride stdin, not argv.
        assert load["cmd"] == ["tmux-test", "load-buffer", "-b", name, "-"]
        assert load["input"] == payload
        # Typing only: the paste invocation carries NO submit tail.
        assert paste["cmd"] == [
            "tmux-test", "paste-buffer", "-d", "-S", "-b", name, "-t", "%3",
        ]
        # The private buffer is cleaned up afterwards.
        assert delete["cmd"] == ["tmux-test", "delete-buffer", "-b", name]
        # No call ever carries the payload inside argv.
        assert not any(payload in " ".join(c["cmd"]) for c in runner.calls)

    def test_paste_failure_propagates_and_cleans_up(self):
        runner = RecordedRunner(
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 1, "", "can't find pane: %9"),
            (("delete-buffer",), 0, "", ""),
        )
        with pytest.raises(MuxRefusal) as excinfo:
            adapter(runner).send_text("%9", "hi;")
        assert excinfo.value.reason == "gone"
        assert excinfo.value.capability == "send_text"
        # Only the buffer this operation created is cleaned up.
        name = runner.calls[0]["cmd"][3]
        assert runner.calls[2]["cmd"] == ["tmux-test", "delete-buffer", "-b", name]
        # Nothing was pasted and nothing more happened: no submit ride.
        assert [c["cmd"][1] for c in runner.calls] == [
            "load-buffer", "paste-buffer", "delete-buffer",
        ]

    def test_multiline_text_is_sanitized_before_the_write(self):
        # A literal newline is a real Enter in the pane: typing must
        # never submit by accident, so text is sanitized to ONE line.
        runner = RecordedRunner(
            (("load-buffer",), 0, "", ""),
            (("paste-buffer",), 0, "", ""),
            (("delete-buffer",), 0, "", ""),
        )
        adapter(runner).send_text("%3", "línea uno\nlínea dos")
        assert runner.calls[0]["input"] == "línea uno línea dos"

    def test_empty_text_refused_without_invoking(self):
        runner = RecordedRunner()
        with pytest.raises(HerdrError, match="empty"):
            adapter(runner).send_text("%3", "  ")
        assert runner.calls == []


class TestProbe:
    """Finding A (live, tmux 3.8 scratch server): ``-V`` answers with NO
    server — a wrapper pointed at a nonexistent socket still prints the
    version. A probe must add reachability facts: the SERVER roundtrip
    (``list-sessions``, the cheapest command that needs the server)
    decides ``reachable``; ``-V`` stays the version source only, and an
    unreachable probe may still carry the version."""

    def test_version_without_a_server_is_not_reachable(self):
        runner = RecordedRunner(
            (("-V",), 0, "tmux 3.8\n", ""),
            (("list-sessions",), 1, "",
             "error connecting to /tmp/tmux-1000/no-such (No such file or directory)"),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is False
        assert "error connecting" in probe.detail
        # The client still names its version: -V is the version source.
        assert probe.version == "3.8"

    def test_reachable_when_the_server_answers_list_sessions(self):
        runner = RecordedRunner(
            (("-V",), 0, "tmux 3.8\n", ""),
            (("list-sessions",), 0,
             "scratch: 1 windows (created Tue Nov 18 10:00:00 2026)\n", ""),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is True
        assert probe.version == "3.8"
        assert "list-sessions" in probe.detail
        assert runner.calls[0]["cmd"] == ["tmux-test", "-V"]
        assert runner.calls[1]["cmd"] == ["tmux-test", "list-sessions"]

    def test_no_server_running_stderr_is_also_unreachable(self):
        # The other no-server shape: the socket exists but no server is
        # listening on it.
        runner = RecordedRunner(
            (("-V",), 0, "tmux 3.8\n", ""),
            (("list-sessions",), 1, "",
             "no server running on /tmp/tmux-1000/default"),
        )
        probe = adapter(runner).probe()
        assert probe.reachable is False

    def test_missing_binary_is_not_reachable(self):
        runner = RecordedRunner(FileNotFoundError("tmux-test"))
        probe = adapter(runner).probe()
        assert probe.reachable is False
        assert "tmux-test" in probe.detail

    def test_nonzero_version_exit_is_not_reachable(self):
        runner = RecordedRunner((("-V",), 1, "", "boom"))
        probe = adapter(runner).probe()
        assert probe.reachable is False


class TestCreateTab:
    def test_parses_window_and_pane_ids_without_stealing_focus(self):
        runner = RecordedRunner(
            (("new-window",), 0, fixture("tmux/new-window.txt"), "")
        )
        created = adapter(runner).create_tab("Build")
        assert created == {"tab_id": "@7", "pane_id": "%23"}
        # Live finding (tmux 3.8): without -d, new-window made the new
        # window the session's current one — focus theft from whoever is
        # attached. -d keeps the current window where it was.
        assert runner.calls[0]["cmd"] == [
            "tmux-test", "new-window", "-d", "-P", "-F",
            "#{window_id} #{pane_id}", "-n", "Build",
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
        # The best-effort buffer cleanup runs even on a failed load.
        runner = RecordedRunner(
            (("load-buffer",), 1, "", "unknown option"),
            (("delete-buffer",), 0, "", ""),
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
