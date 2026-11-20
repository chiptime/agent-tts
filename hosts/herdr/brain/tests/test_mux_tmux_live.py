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
import shutil
import subprocess
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
    try:
        yield SimpleNamespace(
            wrapper=wrapper, pane=pane_id, argv_log=argv_log, tmux=tmux
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
