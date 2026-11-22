"""Live isolated-zellij evidence for the zellij adapter (parity wave 2 S6).

Safety contract: these probes touch ONLY a zellij session this module
itself created — one unique session name per run, spawned on a private
pty with HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME, TMPDIR,
XDG_RUNTIME_DIR and ZELLIJ_CONFIG_DIR pointed at pytest scratch
directories (``XDG_RUNTIME_DIR`` is the one that matters: zellij rides
it for session sockets, so the operator's shared runtime dir is never
touched and this module's runtime dir holds exactly ONE session), and
a minimal ``config.kdl`` (``default_shell "/bin/cat"``) so the
First-Run Setup Wizard can never appear. The cat panes execute
nothing: payloads are typed text that comes back verbatim. Teardown
kills AND deletes the session (``kill-session`` leaves an EXITED,
resurrectable session — ``delete-session --force`` removes it) and
asserts the listing is empty.

Shapes pinned live on zellij 0.45.1:

1. the listing verb is ``action list-panes`` (the top-level command
   does not exist) and its real grid carries plugin panes — including
   a focusable wizard — that the adapter must filter;
2. the prompt submit (``write-chars`` + ``write 10``) lands verbatim;
3. ``send_text`` (fleet parity-w2 lead decision) types WITHOUT Enter:
   the text appears once as tty echo, and cat re-prints it only after
   a separate Enter;
4. ``send_keys`` chords are SPACE-separated (``Ctrl u``) and Enter
   executes a typed line on a shell pane;
5. a nonexistent pane fails SILENTLY (exit 0, no output) — the
   receipt stays honestly ``unknown``.

Real zellij receives no sleeps inside the adapter; the LIVE tests may
poll (bounded loops) because they wait on another process.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import shutil
import struct
import subprocess
import termios
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from herdr_brain.mux import MuxRefusal, ZellijAdapter

ZELLIJ_BIN = shutil.which("zellij")

pytestmark = pytest.mark.skipif(
    ZELLIJ_BIN is None, reason="zellij is not installed on this host"
)

#: The adapter's poll waits on receipt reads; the LIVE waits below are
#: test-side waiting on another process (bounded, no adapter sleeps).
_POLL_ROUNDS = 50
_POLL_DELAY = 0.1


def _sandbox(scratch, session=None):
    # Do not inherit any ZELLIJ_* overrides or the operator's runtime dir.
    env = {"PATH": os.defpath, "TERM": "xterm-256color", "LC_ALL": "C.UTF-8"}
    for key, subdir in {
        "HOME": "home", "XDG_CONFIG_HOME": "cfg", "XDG_DATA_HOME": "data",
        "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "run", "TMPDIR": "tmp",
    }.items():
        directory = scratch / subdir
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    cfg = scratch / "cfg"
    (cfg / "config.kdl").write_text('default_shell "/bin/cat"\n')
    prefix = [ZELLIJ_BIN, "--config-dir", str(cfg)]

    def run(*args):
        return subprocess.run(
            [*prefix, *args], cwd=scratch, env=env, capture_output=True,
            text=True, timeout=10,
        )

    def adapter(target=session):
        def runner(cmd, **kwargs):
            selector = ["--session", target] if target and cmd[1] == "action" else []
            return subprocess.run(
                [*prefix, *selector, *cmd[1:]], cwd=scratch, env=env, **kwargs
            )

        return ZellijAdapter(bin_path=ZELLIJ_BIN, runner=runner)

    return SimpleNamespace(env=env, prefix=prefix, run=run, adapter=adapter)


@pytest.fixture(scope="module")
def live_session(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("zellij-live")
    session = f"t4-{uuid.uuid4().hex[:8]}"
    sandbox = _sandbox(scratch, session)
    master, slave = pty.openpty()
    proc = None
    drain = None
    stop = threading.Event()

    def drain_pty():
        # A full PTY buffer would stall the client. Stop without waiting on it.
        while not stop.wait(0.01):
            try:
                os.read(master, 65536)
            except BlockingIOError:
                continue
            except OSError:
                break

    def action(*args):
        return sandbox.run("--session", session, "action", *args)

    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.set_blocking(master, False)
        proc = subprocess.Popen(
            [*sandbox.prefix, "--session", session], env=sandbox.env, cwd=scratch,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        drain = threading.Thread(target=drain_pty, daemon=True)
        drain.start()
        for _ in range(_POLL_ROUNDS):
            raw = action("list-panes", "--all", "--json")
            if raw.stdout.startswith("[") and any(
                p.get("pane_command") == "/bin/cat" for p in json.loads(raw.stdout)
            ):
                break
            assert proc.poll() is None, f"zellij client exited: {proc.returncode}"
            time.sleep(_POLL_DELAY)
        else:
            pytest.fail(f"scratch zellij did not start: {raw.stderr}")
        cat_id = next(str(p["id"]) for p in json.loads(raw.stdout)
                      if p.get("pane_command") == "/bin/cat")
        made = action("new-pane", "--", "/bin/sh")
        assert made.returncode == 0 and made.stdout.strip().startswith("terminal_"), made
        yield SimpleNamespace(
            name=session, cat=cat_id, sh=made.stdout.strip().removeprefix("terminal_"),
            scratch=scratch, adapter=sandbox.adapter, run=sandbox.run, action=action,
        )
    finally:
        try:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        finally:
            stop.set()
            if drain is not None:
                drain.join(timeout=1)
            os.close(slave)
            os.close(master)
            try:
                sandbox.run("kill-session", session)
            finally:
                for _ in range(10):
                    sandbox.run("delete-session", "--force", session)
                    listed = sandbox.run("list-sessions", "--short")
                    if "No active zellij sessions found." in listed.stderr:
                        break
                    time.sleep(_POLL_DELAY)
                else:
                    pytest.fail(f"scratch session {session} leaked or listing failed: {listed}")


def _payload_lines(grid: str, payload: str) -> int:
    """How many grid lines carry exactly the payload (echo vs re-print)."""
    return sum(
        1 for line in grid.splitlines() if line.strip() == payload.strip()
    )


class TestProbe:
    def test_probe_reachable_with_the_binary_version(self, live_session):
        version_out = live_session.run("--version").stdout
        expected = version_out.strip().split()[-1]
        probe = live_session.adapter().probe()
        assert probe.reachable is True
        assert probe.version == expected
        assert "list-panes" in probe.detail

    def test_probe_without_a_session_is_unreachable(self, tmp_path_factory):
        sandbox = _sandbox(tmp_path_factory.mktemp("zellij-empty"))
        probe = sandbox.adapter().probe()
        assert probe.reachable is False
        assert "There is no active session!" in probe.detail
        listed = sandbox.run("list-sessions")
        assert (listed.returncode, listed.stderr.strip()) == (
            1, "No active zellij sessions found.",
        )

    def test_missing_named_session_is_unreachable(self, live_session):
        raw = live_session.run("--session", "missing", "action", "list-panes", "--json")
        assert "Session 'missing' not found." in raw.stderr
        with pytest.raises(MuxRefusal) as exc:
            live_session.adapter("missing").list_agents()
        assert exc.value.reason == "unreachable"


class TestLiveListing:
    def test_plugins_present_raw_and_filtered_by_the_adapter(self, live_session):
        raw = json.loads(live_session.action("list-panes", "--all", "--json").stdout)
        # The probe is not vacuous: the REAL grid carries plugin panes.
        assert any(entry.get("is_plugin") for entry in raw)
        adapter = live_session.adapter()
        agents = adapter.list_agents()
        terminal_ids = {str(e["id"]) for e in raw if not e.get("is_plugin")}
        plugin_titles = {
            e["title"] for e in raw
            if e.get("is_plugin") and e.get("title")
        }
        # Ids REPEAT across kinds (a plugin and a terminal may share
        # one numeric id), so equality with the terminal ids is the
        # filter proof: plugin-only panes never surface, and no agent
        # ever carries a plugin title.
        assert {a.pane_id for a in agents} == terminal_ids
        assert all(a.title not in plugin_titles for a in agents)
        assert all(a.agent == "shell" and a.status == "unknown" for a in agents)
        assert all(a.cwd == str(live_session.scratch) for a in agents)


class TestLiveWrites:
    def test_send_prompt_receipts_sent_and_text_lands(self, live_session):
        payload = f"live probe {uuid.uuid4().hex[:8]};"
        adapter = live_session.adapter()
        result = adapter.send_prompt(live_session.cat, payload)
        # The honest closed set: cat re-prints the line, so the body
        # moves and the receipt upgrades to `sent` — never `effective`.
        assert result["status"] in ("sent", "typed_not_submitted")
        assert result["ok"] is (result["status"] == "sent")
        grid = ""
        for _ in range(_POLL_ROUNDS):
            grid = adapter.read_screen(live_session.cat)
            if _payload_lines(grid, payload) == 2:
                break
            time.sleep(_POLL_DELAY)
        assert _payload_lines(grid, payload) == 2, grid
        assert "\x1b[" in adapter.read_screen(live_session.cat, as_ansi=True)

    def test_send_text_types_without_enter(self, live_session):
        payload = f"--typed-only {uuid.uuid4().hex[:8]};"
        adapter = live_session.adapter()
        adapter.send_text(live_session.cat, payload)
        # Typed text is echoed by the tty exactly ONCE (cat has not
        # printed — no Enter rode with the typing).
        seen = 0
        for _ in range(_POLL_ROUNDS):
            seen = _payload_lines(
                adapter.read_screen(live_session.cat), payload
            )
            if seen >= 1:
                break
            time.sleep(_POLL_DELAY)
        assert seen == 1, adapter.read_screen(live_session.cat)
        # The recipe's commit: a SEPARATE Enter makes cat re-print it.
        committed = adapter.send_keys(live_session.cat, ["enter"])
        assert committed["ok"] is True, committed
        printed = 0
        for _ in range(_POLL_ROUNDS):
            printed = _payload_lines(
                adapter.read_screen(live_session.cat), payload
            )
            if printed >= 2:
                break
            time.sleep(_POLL_DELAY)
        assert printed >= 2, adapter.read_screen(live_session.cat)

    def test_send_keys_ctrl_u_clears_the_typed_text(self, live_session):
        payload = f"clear me {uuid.uuid4().hex[:8]}"
        adapter = live_session.adapter()
        adapter.send_text(live_session.sh, payload)
        typed = ""
        for _ in range(_POLL_ROUNDS):
            typed = adapter.read_screen(live_session.sh)
            if payload in typed:
                break
            time.sleep(_POLL_DELAY)
        assert payload in typed, typed
        cleared = adapter.send_keys(live_session.sh, ["ctrl+u"])
        assert cleared["ok"] is True, cleared
        clean = ""
        for _ in range(_POLL_ROUNDS):
            clean = adapter.read_screen(live_session.sh)
            if payload not in clean:
                break
            time.sleep(_POLL_DELAY)
        assert payload not in clean, clean

    def test_send_keys_enter_executes_a_typed_line(self, live_session):
        marker = f"mkout-{uuid.uuid4().hex[:8]}"
        adapter = live_session.adapter()
        adapter.send_text(live_session.sh, f"echo {marker}")
        adapter.send_keys(live_session.sh, ["enter"])
        ran = ""
        for _ in range(_POLL_ROUNDS):
            ran = adapter.read_screen(live_session.sh)
            lines = [line.strip() for line in ran.splitlines()]
            if marker in lines:  # the shell's OUTPUT, not the typed text
                break
            time.sleep(_POLL_DELAY)
        assert marker in [line.strip() for line in ran.splitlines()], ran

    def test_a_silent_nonexistent_pane_receipts_unknown(self, live_session):
        # Live honesty edge: writes and reads against a pane that does
        # not exist exit 0 with no output. The receipt must stay
        # honestly `unknown` — and bare typing claims nothing at all.
        adapter = live_session.adapter()
        result = adapter.send_prompt("9999", "nowhere")
        assert result == {
            "ok": False,
            "status": "unknown",
            "output": "sin evidencia suficiente para clasificar el envío",
        }
        assert adapter.send_text("9999", "nowhere") is None
