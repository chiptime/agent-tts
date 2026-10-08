"""V1 for the ownership-aware port policy (AT-11 task 2.3, audit C5).

The brain launcher and installer must never signal a process they cannot
prove they own. Ownership is proven by (a) our pidfile naming a live
``herdr_brain`` process, or (b) the systemd unit's ``MainPID``; anything else
makes the launcher exit 98 with an actionable message and leave the foreign
listener alive. Real listeners (child Python processes) occupy a real free
port; ``systemctl`` is always stubbed so the host's own units are never read.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
BRAIN_LAUNCHER = REPO_ROOT / "hosts" / "herdr" / "brain" / "bin" / "herdr-brain"
INSTALL_SH = REPO_ROOT / "hosts" / "herdr" / "brain" / "deploy" / "install.sh"

PORT_IN_USE = 98

PROBE = """#!/bin/sh
echo "ARGV=$*"
"""

SYSTEMCTL_STUB = """#!/bin/sh
# Hermetic systemctl: records calls; behaves as an active unit whose MainPID
# is $FAKE_UNIT_MAINPID (empty = no unit). `stop` kills it the way systemd would.
echo "$*" >> "$FAKE_SYSTEMCTL_LOG"
case "$*" in
  *show*MainPID*) echo "${FAKE_UNIT_MAINPID:-0}"; exit 0 ;;
  *is-active*) [ -n "${FAKE_UNIT_MAINPID:-}" ] && exit 0 || exit 3 ;;
  *stop*) [ -n "${FAKE_UNIT_MAINPID:-}" ] && kill -KILL "$FAKE_UNIT_MAINPID" 2>/dev/null; exit 0 ;;
esac
exit 0
"""

LISTENER = """
import signal, socket, sys, time
port, term_file = int(sys.argv[1]), sys.argv[2]
def on_term(signum, frame):
    if term_file != "-":
        open(term_file, "w").write("TERM")
    if "--exit-on-term" in sys.argv:
        sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(1)
print("ready", flush=True)
while True:
    time.sleep(0.1)
"""


def _make_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Rig:
    """One sandbox: monorepo-shaped brain checkout + hermetic PATH and XDG."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.stubs = tmp_path / "stubs"
        self.stubs.mkdir()
        herdr = tmp_path / "checkout" / "hosts" / "herdr"
        brain = herdr / "brain"
        self.launcher = brain / "bin" / "herdr-brain"
        self.launcher.parent.mkdir(parents=True)
        shutil.copy2(BRAIN_LAUNCHER, self.launcher)
        _make_exec(brain / ".venv" / "bin" / "python", PROBE)
        _make_exec(herdr / "tts-plugin" / "bin" / "herdr-tts", "#!/bin/sh\nexit 0\n")
        _make_exec(self.stubs / "systemctl", SYSTEMCTL_STUB)
        self.systemctl_log = tmp_path / "systemctl.log"
        self.state_dir = tmp_path / "xdg-state" / "herdr-brain"
        self.state_dir.mkdir(parents=True)
        self.port = _free_port()
        self.procs: list[subprocess.Popen] = []

    @property
    def pidfile(self) -> Path:
        return self.state_dir / "daemon.pid"

    def env(self, **extra: str) -> dict:
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.tmp / "xdg-config"),
            "XDG_STATE_HOME": str(self.tmp / "xdg-state"),
            "HERDR_BRAIN_PORT": str(self.port),
            "FAKE_SYSTEMCTL_LOG": str(self.systemctl_log),
        }
        env.update(extra)
        return env

    def listener(self, *argv_tail: str, term_file: Path | None = None) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", LISTENER, str(self.port), str(term_file or "-"), *argv_tail],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.procs.append(proc)
        assert proc.stdout.readline().strip() == "ready"
        return proc

    def run(self, *args: str, timeout: int = 30, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.launcher), *args],
            env=self.env(**env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def close(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout:
                proc.stdout.close()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def _alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def _wait_dead(proc: subprocess.Popen, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


class TestForeignListener:
    def test_foreign_listener_aborts_and_stays_alive(self, rig, tmp_path):
        term = tmp_path / "term-seen"
        foreign = rig.listener(term_file=term)
        result = rig.run("_daemon")
        assert result.returncode == PORT_IN_USE
        assert _alive(foreign)
        assert not term.exists(), "launcher signalled a process it does not own"
        assert "ARGV=" not in result.stdout, "server must not start on a foreign-held port"

    def test_message_names_pid_process_port_and_both_remediations(self, rig):
        foreign = rig.listener()
        result = rig.run("_daemon")
        message = result.stderr
        assert str(foreign.pid) in message
        assert str(rig.port) in message
        assert "python" in message.lower()
        assert f"HERDR_BRAIN_PORT=" in message
        assert "stop" in message.lower()
        assert _alive(foreign)

    def test_pidfile_naming_the_foreign_pid_is_not_proof_of_ownership(self, rig, tmp_path):
        # PID reuse: our stale pidfile now points at an unrelated listener.
        term = tmp_path / "term-seen"
        foreign = rig.listener(term_file=term)
        rig.pidfile.write_text(f"{foreign.pid}\n")
        result = rig.run("_daemon")
        assert result.returncode == PORT_IN_USE
        assert _alive(foreign)
        assert not term.exists()

    def test_unrelated_unit_mainpid_is_not_proof_of_ownership(self, rig, tmp_path):
        term = tmp_path / "term-seen"
        foreign = rig.listener(term_file=term)
        bystander = subprocess.Popen(["sleep", "60"])
        rig.procs.append(bystander)
        result = rig.run("_daemon", FAKE_UNIT_MAINPID=str(bystander.pid))
        assert result.returncode == PORT_IN_USE
        assert _alive(foreign)
        assert _alive(bystander), "systemctl stop must not run for a unit that does not own the listener"

    def test_supervisor_does_not_restart_forever_on_a_port_conflict(self, rig):
        foreign = rig.listener()
        result = rig.run("_daemon-supervised", timeout=20)
        assert result.returncode == PORT_IN_USE
        assert "restarting" not in result.stdout
        assert _alive(foreign)


class TestOwnedListener:
    def test_own_manual_instance_is_terminated_then_server_starts(self, rig):
        own = rig.listener("-m", "herdr_brain.server", "--exit-on-term")
        rig.pidfile.write_text(f"{own.pid}\n")
        result = rig.run("_daemon")
        assert result.returncode == 0, result.stderr
        assert _wait_dead(own)
        assert "ARGV=-m herdr_brain.server" in result.stdout

    def test_unit_owned_listener_is_stopped_via_systemctl_not_signalled(self, rig, tmp_path):
        term = tmp_path / "term-seen"
        unit = rig.listener(term_file=term)
        result = rig.run("_daemon", FAKE_UNIT_MAINPID=str(unit.pid))
        assert result.returncode == 0, result.stderr
        assert "stop herdr-brain" in rig.systemctl_log.read_text()
        assert _wait_dead(unit)
        assert not term.exists(), "a unit-owned listener must be managed via systemctl, not kill"
        assert "ARGV=-m herdr_brain.server" in result.stdout


class TestStalePidfile:
    def test_stale_pidfile_is_cleaned_without_signalling_a_reused_pid(self, rig):
        bystander = subprocess.Popen(["sleep", "60"])
        rig.procs.append(bystander)
        rig.pidfile.write_text(f"{bystander.pid}\n")
        result = rig.run("_daemon")  # port is free
        assert result.returncode == 0, result.stderr
        assert _alive(bystander), "reused PID in a stale pidfile was signalled"
        assert rig.pidfile.read_text().strip() != str(bystander.pid)

    def test_dead_pid_in_pidfile_is_harmless(self, rig):
        rig.pidfile.write_text("999999\n")
        result = rig.run("_daemon")
        assert result.returncode == 0, result.stderr
        assert rig.pidfile.read_text().strip() != "999999"


class TestStopNeverSignalsForeignProcesses:
    def test_stop_leaves_a_foreign_listener_alone(self, rig, tmp_path):
        term = tmp_path / "term-seen"
        foreign = rig.listener(term_file=term)
        result = rig.run("stop")
        assert "not running" in result.stdout.lower()
        assert _alive(foreign)
        assert not term.exists()

    def test_stop_terminates_a_pidfile_owned_instance(self, rig):
        own = rig.listener("-m", "herdr_brain.server", "--exit-on-term")
        rig.pidfile.write_text(f"{own.pid}\n")
        result = rig.run("stop")
        assert result.returncode == 0, result.stderr
        assert _wait_dead(own)

    def test_stop_uses_systemctl_for_a_unit_owned_listener(self, rig, tmp_path):
        term = tmp_path / "term-seen"
        unit = rig.listener(term_file=term)
        rig.run("stop", FAKE_UNIT_MAINPID=str(unit.pid))
        assert "stop herdr-brain" in rig.systemctl_log.read_text()
        assert _wait_dead(unit)
        assert not term.exists()


class TestInstallerClaim:
    """``_claim-port installer`` is the single policy the installer delegates to."""

    def test_foreign_listener_blocks_the_installer_and_survives(self, rig, tmp_path):
        term = tmp_path / "term-seen"
        foreign = rig.listener(term_file=term)
        result = rig.run("_claim-port", "installer")
        assert result.returncode == PORT_IN_USE
        assert _alive(foreign)
        assert not term.exists()
        assert "HERDR_BRAIN_PORT=" in result.stderr

    def test_unit_owned_listener_is_left_for_the_installer_to_manage(self, rig):
        unit = rig.listener()
        result = rig.run("_claim-port", "installer", FAKE_UNIT_MAINPID=str(unit.pid))
        assert result.returncode == 0, result.stderr
        assert _alive(unit)
        assert "stop herdr-brain" not in (
            rig.systemctl_log.read_text() if rig.systemctl_log.exists() else ""
        )

    def test_own_stray_manual_instance_is_terminated(self, rig):
        own = rig.listener("-m", "herdr_brain.server", "--exit-on-term")
        rig.pidfile.write_text(f"{own.pid}\n")
        result = rig.run("_claim-port", "installer")
        assert result.returncode == 0, result.stderr
        assert _wait_dead(own)

    def test_free_port_is_a_noop(self, rig):
        assert rig.run("_claim-port", "installer").returncode == 0

    def test_resolve_port_subcommand_prints_the_resolved_port(self, rig):
        assert rig.run("_resolve", "port").stdout.strip() == str(rig.port)


class TestInstallerDelegatesToThePolicy:
    def test_install_sh_never_signals_listeners_directly(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert "kill -TERM" not in text
        assert "kill -KILL" not in text
        assert "_claim-port installer" in text

    def test_launcher_only_signals_through_the_ownership_guard(self):
        lines = BRAIN_LAUNCHER.read_text(encoding="utf-8").splitlines()
        direct = [
            (n, line.strip())
            for n, line in enumerate(lines, 1)
            if "kill -" in line and "kill -0" not in line and not line.strip().startswith("#")
        ]
        # The only raw signal sites: signal_owned's guarded kill (ownership is
        # re-verified on the line before) and the supervisor forwarding to the
        # child it spawned itself.
        allowed = ('kill -"$1" "$2"', 'kill -"$forward_sig" "$child_pid"')
        assert all(any(a in line for a in allowed) for _, line in direct), direct
        text = "\n".join(lines)
        guard = text.index("signal_owned() {")
        assert 'owner_of "$2")" == "pidfile"' in text[guard : guard + 200]
