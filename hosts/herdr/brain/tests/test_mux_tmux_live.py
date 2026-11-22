"""Live isolated-tmux evidence for the tmux adapter's exact-text and focus fixes.

Safety contract (unit T7, parity-critical-remediation S7 / the parity-tmux
unit doc): these probes touch ONLY a tmux server this test module itself
created — one unique socket per run (`-L`), started with `-f /dev/null` so no
user `~/.tmux.conf` can change behavior, with HOME, TMPDIR and TMUX_TMPDIR
pointed at pytest scratch directories. The target pane runs `cat`, so nothing
is ever executed: payloads arrive as terminal input and come back verbatim as
`cat` output. Teardown destroys only that own server (`kill-server` on the
same socket) and asserts it is gone.

Two probes cover the F7 audit's live findings on tmux 3.8:

1. exact text: `send-keys -l` silently dropped a trailing `;` while exiting
   0 (and the receipt still read `sent` — a false positive). The write path
   must deliver the exact bytes for every payload: shell metacharacters,
   quotes, accents and wide Unicode.
2. focus: `new-window` without `-d` moved the session's active window.
   `create_tab` must leave the current window untouched.

Every adapter call is routed through a wrapper binary that appends its argv
to a log, so the tests can also prove the sent text never rides argv (no
`ps` exposure) and no `herdr-paste-*` buffer is left on the server.

Real tmux receives no sleeps inside the adapter; the LIVE tests may poll
(a bounded loop) because they wait on another process, not on the adapter.

Receipt note: with a `cat` pane the closed-set receipt is honest but
conservative (`typed_not_submitted` while `cat` has not printed yet, `sent`
once the echo moved) — it is timing-dependent by design; the exactness of
the delivered TEXT is the discriminator these live tests pin. Receipt
shapes stay pinned by the recorded-fixture unit tests.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from herdr_brain.mux import TmuxAdapter

TMUX_BIN = shutil.which("tmux")

pytestmark = pytest.mark.skipif(
    TMUX_BIN is None, reason="tmux is not installed on this host"
)

#: Payload battery: every item ends with the auditor's dropped ``;``.
#: ``exact`` rows must appear verbatim as a full grid line; for wide-char
#: payloads grid padding may differ, so existence (not full-line equality)
#: is asserted.
PAYLOADS = [
    ("echo hi;", True),
    ("rm -rf '$(whoami)' \"q\" `calc` && true;", True),
    ("ñ é ç ✓ «» ¿¡ —;", True),
    ("日本語 😀;", False),
]

BUFFER_PREFIX = "herdr-paste-"


@pytest.fixture(scope="module")
def scratch_server(tmp_path_factory):
    """One disposable tmux 3.8 server on a unique socket; only this moves."""
    scratch = tmp_path_factory.mktemp("tmux-live")
    home = scratch / "home"
    home.mkdir()
    sock = f"herdr-probe-{uuid.uuid4().hex[:12]}"
    argv_log = scratch / "argv.log"
    wrapper = scratch / "tmux-scratched"
    # Every adapter call goes through this wrapper, so it can never reach
    # a real session: the socket is baked in, and the argv is logged for
    # the text-never-rides-argv assertion.
    wrapper.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$TL_ARGV_LOG\"\n"
        f"exec {TMUX_BIN} -L {sock} -f /dev/null \"$@\"\n"
    )
    wrapper.chmod(0o755)
    server_env = dict(
        os.environ,
        HOME=str(home),
        TMPDIR=str(scratch),
        TMUX_TMPDIR=str(scratch),
        TL_ARGV_LOG=str(argv_log),
    )
    # os.environ patches below serve the adapter calls made inside tests
    # (their subprocesses inherit the process environment); restored after.
    saved_env = {key: os.environ.get(key) for key in (
        "HOME", "TMPDIR", "TMUX_TMPDIR", "TL_ARGV_LOG",
    )}
    os.environ["HOME"] = str(home)
    os.environ["TMPDIR"] = str(scratch)
    os.environ["TMUX_TMPDIR"] = str(scratch)
    os.environ["TL_ARGV_LOG"] = str(argv_log)

    def tmux(*args, timeout: float = 20.0):
        return subprocess.run(
            [str(wrapper), *args],
            capture_output=True, text=True, timeout=timeout, env=server_env,
        )

    created = tmux("new-session", "-d", "-s", "scr", "cat")
    assert created.returncode == 0, created.stderr
    # Wait for the server to answer through the wrapper (bounded, no sleeps
    # in the adapter — this is test-side waiting on another process).
    for _ in range(50):
        listed = tmux("list-sessions", timeout=5.0)
        if listed.returncode == 0:
            break
        time.sleep(0.1)
    else:
        pytest.fail("scratch tmux server never answered list-sessions")
    panes = tmux("list-panes", "-F", "#{pane_id}", "-t", "scr")
    pane_id = panes.stdout.strip().splitlines()[0]
    # Byte-level instrument: a tiny stdin->hex filter the byte probe runs
    # as its own pane command; it flushes per line, so short payloads are
    # visible immediately (od would buffer reads under 16 bytes).
    hexline = scratch / "hexline.py"
    hexline.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "for line in sys.stdin.buffer:\n"
        "    sys.stdout.write(line.hex())\n"
        "    sys.stdout.flush()\n"
    )
    hexline.chmod(0o755)
    try:
        yield SimpleNamespace(
            wrapper=wrapper, pane=pane_id, argv_log=argv_log, tmux=tmux,
            hexline=hexline,
        )
    finally:
        killed = tmux("kill-server")
        assert killed.returncode == 0, killed.stderr
        # ...and destroyed ONLY that own server: gone means unreachable.
        gone = tmux("list-sessions", timeout=5.0)
        assert gone.returncode != 0
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _live_adapter(scratch_server) -> TmuxAdapter:
    return TmuxAdapter(bin_path=str(scratch_server.wrapper))


@pytest.fixture(scope="module")
def ptyattach(scratch_server):
    """A helper that attaches a REAL interactive tmux client on a fresh
    pty for a bounded window, then leaves (child terminated first)."""
    import textwrap

    helper = scratch_server.hexline.parent / "ptyattach.py"
    helper.write_text(textwrap.dedent(
        """
        import os, pty, signal, subprocess, sys, time
        secs = float(sys.argv[1]); cmd = sys.argv[2:]
        master, slave = pty.openpty()
        env = dict(os.environ, TERM="xterm")
        child = subprocess.Popen(
            cmd, stdin=slave, stdout=slave, stderr=slave,
            env=env, preexec_fn=os.setsid,
        )
        os.close(slave)

        def leave(*_args):
            child.terminate()
            try:
                child.wait(timeout=3)
            except Exception:
                child.kill()
            sys.exit(0)

        signal.signal(signal.SIGTERM, leave)
        time.sleep(secs)
        leave()
        """
    ))
    helper.chmod(0o755)
    return helper


class TestPromptExactText:
    """Finding: `send-keys -l` dropped a trailing `;` (rc 0, receipt `sent`)."""

    @pytest.mark.parametrize("payload,exact", PAYLOADS)
    def test_text_arrives_verbatim(self, scratch_server, payload, exact):
        adapter = _live_adapter(scratch_server)
        result = adapter.send_prompt(scratch_server.pane, payload)
        # The honest closed set: never a fabricated positive.
        assert result["status"] in ("sent", "typed_not_submitted")
        # Wait (bounded) for the cat round-trip: its output IS the probe.
        grid_lines = []
        for _ in range(50):
            grid_lines = adapter.read_screen(scratch_server.pane).splitlines()
            if payload in grid_lines or payload in "\n".join(grid_lines):
                break
            time.sleep(0.1)
        # The exact bytes — including the trailing `;` — came back.
        if exact:
            assert payload in grid_lines, "\n".join(grid_lines)
        else:
            assert payload in "\n".join(grid_lines), "\n".join(grid_lines)
        # The text never rode argv through any adapter call.
        log = scratch_server.argv_log.read_text()
        assert payload not in log
        # No private buffer is left behind: nothing leaked into the
        # server's shared buffer list.
        buffers = scratch_server.tmux("list-buffers", "-F", "#{buffer_name}")
        assert BUFFER_PREFIX not in (buffers.stdout or "")


class TestPromptExactBytes:
    """Byte-level delivery proof (verification finding): `cat` echoes the
    text onto the GRID, where cell width and padding can blur what is
    asserted. Here the pane runs `od -An -v -tx1`, which prints the exact
    bytes it received as hex — the assertion compares BYTES, not pixels,
    for every battery payload including wide Unicode.

    The Enter the adapter presses arrives as NL (the tty line discipline
    maps CR to NL), so the expected hex ends in `0a`.
    """

    # Hex rows may be contiguous (the panes' python hex filter) or
    # space-separated (an od-based instrument); accept both spellings.
    _HEX_ROW = "^[0-9a-f]{2}(?:[ \t]*[0-9a-f]{2})*$"

    @pytest.mark.parametrize("payload,_exact", PAYLOADS)
    def test_bytes_received_match_text_sent(self, scratch_server, payload, _exact):
        adapter = _live_adapter(scratch_server)
        session = f"bytes{abs(hash(payload)) % 10000}"
        made = scratch_server.tmux(
            "new-session", "-d", "-s", session, str(scratch_server.hexline)
        )
        assert made.returncode == 0, made.stderr
        panes = scratch_server.tmux("list-panes", "-F", "#{pane_id}", "-t", session)
        pane_id = panes.stdout.strip().splitlines()[0]
        try:
            result = adapter.send_prompt(pane_id, payload)
            assert result["status"] in ("sent", "typed_not_submitted")
            expected_hex = (payload + "\n").encode("utf-8").hex()
            received = ""
            for _ in range(50):
                grid = adapter.read_screen(pane_id)
                rows = [
                    row.strip()
                    for row in grid.splitlines()
                    if re.match(self._HEX_ROW, row.strip())
                ]
                received = "".join("".join(rows).split())
                if received.endswith(expected_hex):
                    break
                time.sleep(0.1)
            # The exact byte sequence arrived, including the trailing `;`
            # tmux 3.8 used to swallow and the UTF-8 encodings of accents
            # and wide characters.
            assert received.endswith(expected_hex), received
        finally:
            # Only a session this test itself created.
            scratch_server.tmux("kill-session", "-t", session)


class TestSendTextNoEnter:
    """parity wave 2 S5: ``send_text`` types WITHOUT pressing Enter.

    The discriminator is the ``hexline`` instrument pane: it only prints
    on a terminated line, so if the adapter's typing path pressed Enter
    (or the bytes never arrived), the hex evidence shows it. Typed bytes
    are echoed by the tty line discipline as raw text — the hex-row
    filter ignores those non-hex rows.

    A bounded negative wait is deterministic here: no Enter was pressed,
    so no amount of slowness produces hex output — only a submit would.
    """

    def test_bytes_reach_the_pane_only_after_enter(self, scratch_server):
        adapter = _live_adapter(scratch_server)
        session = f"text{uuid.uuid4().hex[:8]}"
        made = scratch_server.tmux(
            "new-session", "-d", "-s", session, str(scratch_server.hexline)
        )
        assert made.returncode == 0, made.stderr
        panes = scratch_server.tmux("list-panes", "-F", "#{pane_id}", "-t", session)
        pane_id = panes.stdout.strip().splitlines()[0]
        payload = 'rm -rf \'$(whoami)\' "q" `calc` && true;'
        expected_hex = (payload + "\n").encode("utf-8").hex()

        def hex_rows() -> str:
            grid = adapter.read_screen(pane_id)
            rows = [
                row.strip()
                for row in grid.splitlines()
                if re.match(TestPromptExactBytes._HEX_ROW, row.strip())
            ]
            return "".join("".join(rows).split())

        try:
            adapter.send_text(pane_id, payload)
            # NO Enter rode with the typing: the line is unterminated, so
            # the instrument cannot have printed — the payload may sit on
            # the grid as tty echo, but no hex row may exist.
            for _ in range(20):
                assert not hex_rows(), "typing submitted: instrument printed"
                time.sleep(0.1)
            # The dialog recipe's commit: Enter is pressed SEPARATELY.
            committed = adapter.send_keys(pane_id, ["enter"])
            assert committed["ok"] is True, committed
            received = ""
            for _ in range(50):
                received = hex_rows()
                if received.endswith(expected_hex):
                    break
                time.sleep(0.1)
            # The exact bytes arrived — the trailing `;` included — and
            # NOTHING more: typing added no byte of its own.
            assert received.endswith(expected_hex), received
            # The text never rode argv through any adapter call.
            assert payload not in scratch_server.argv_log.read_text()
            # No private buffer left behind.
            buffers = scratch_server.tmux("list-buffers", "-F", "#{buffer_name}")
            assert BUFFER_PREFIX not in (buffers.stdout or "")
        finally:
            scratch_server.tmux("kill-session", "-t", session)


class TestFocusCountsOnlyRealClients:
    """Finding (live, tmux 3.8): control clients (``tmux -C``) count into
    ``#{session_attached}``, so a program-only session looked "focused"
    with no human watching. Focus requires an attached NON-control
    client, cross-checked through ``list-clients``.

    A real interactive client is spawned on a fresh pty (``ptyattach``
    helper); a control client rides a held-open pipe. The ``scr`` pane
    is the probe target throughout.
    """

    ATTACH_WINDOW_S = 6.0

    def _focused_ids(self, adapter) -> set:
        return {a.pane_id for a in adapter.list_agents() if a.focused}

    def test_no_client_never_grounds_focus(self, scratch_server):
        adapter = _live_adapter(scratch_server)
        assert self._focused_ids(adapter) == set()

    def test_control_client_does_not_ground_focus(self, scratch_server):
        adapter = _live_adapter(scratch_server)
        client = subprocess.Popen(
            ["/bin/sh", "-c",
             f"sleep {self.ATTACH_WINDOW_S:.0f} | "
             f"{scratch_server.wrapper} -C attach -t scr >/dev/null 2>&1"],
            env=dict(os.environ),
        )
        try:
            seen_control_client = False
            for _ in range(25):
                listed = scratch_server.tmux(
                    "list-clients", "-F", TmuxAdapter.LIST_CLIENTS_FORMAT
                )
                if "scr|1" in listed.stdout:
                    seen_control_client = True
                assert self._focused_ids(adapter) == set(), (
                    "a control client grounded focus"
                )
                time.sleep(0.2)
            # The probe was not vacuous: a control client WAS attached.
            assert seen_control_client, "control client never showed up"
        finally:
            client.terminate()
            client.wait(timeout=10)

    def test_real_client_grounds_focus_and_losing_it_drops_focus(
        self, scratch_server, ptyattach
    ):
        adapter = _live_adapter(scratch_server)
        client = subprocess.Popen(
            [sys.executable, str(ptyattach), f"{self.ATTACH_WINDOW_S:.0f}",
             str(scratch_server.wrapper), "attach", "-t", "scr"],
            env=dict(os.environ, TL_ARGV_LOG=str(scratch_server.argv_log)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            grounded = False
            for _ in range(30):
                if "%0" in self._focused_ids(adapter):
                    grounded = True
                    break
                time.sleep(0.2)
            assert grounded, (
                "a real attached client never grounded focus: "
                + str(scratch_server.tmux(
                    "list-clients", "-F", TmuxAdapter.LIST_CLIENTS_FORMAT
                ).stdout)
            )
            # The client leaves after its window; focus drops with it.
            client.wait(timeout=self.ATTACH_WINDOW_S + 20)
            dropped = False
            for _ in range(30):
                if "%0" not in self._focused_ids(adapter):
                    dropped = True
                    break
                time.sleep(0.2)
            assert dropped, "focus outlived its client"
        finally:
            if client.poll() is None:
                client.terminate()
                client.wait(timeout=10)


class TestCreateTabKeepsFocus:
    """Finding: `new-window` without `-d` moved the session's active window."""

    def test_create_tab_does_not_move_the_current_window(self, scratch_server):
        adapter = _live_adapter(scratch_server)

        def active_window_ids() -> set:
            listed = scratch_server.tmux(
                "list-panes", "-a", "-F", TmuxAdapter.LIST_FORMAT
            )
            actives = set()
            for line in listed.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) < 8:
                    continue
                if fields[6] == "1":  # #{window_active}
                    actives.add(fields[2])
            return actives

        before = active_window_ids()
        assert before, "scratch server lost its active window"
        created = adapter.create_tab("ProbeTab")
        after = active_window_ids()
        # The original window stays the session's current window...
        assert before <= after
        # ...and the new window was NOT made current (no focus steal).
        assert created["tab_id"] not in after
