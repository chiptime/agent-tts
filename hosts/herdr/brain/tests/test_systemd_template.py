"""V1 for the generated systemd unit (AT-11 task 2.4, design Decision 5).

``deploy/herdr-brain.service.tmpl`` is the only versioned unit source;
``deploy/install.sh`` substitutes install-time discovered values into it. The
generated unit is never tracked. Systemd itself is never touched here:
``--generate-only`` drills only render and inspect, and the full-install tests
run against hermetic ``systemctl``/``loginctl``/``curl`` stubs.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
BRAIN_SRC = REPO_ROOT / "hosts" / "herdr" / "brain"
DEPLOY_SRC = BRAIN_SRC / "deploy"
TEMPLATE = DEPLOY_SRC / "herdr-brain.service.tmpl"
STATIC_UNIT = DEPLOY_SRC / "herdr-brain.service"

DESIGN_PLACEHOLDERS = ("@HERDR_BIN@", "@INSTALL_DIR@", "@PYTHON@", "@PORT@", "@ENV_FILE@")
LEFTOVER = re.compile(r"@[A-Z][A-Z_]*@")
MARKER = "# herdr-brain-managed:"

PROBE = "#!/bin/sh\nexit 0\n"

SYSTEMCTL_STUB = """#!/bin/sh
echo "$*" >> "$FAKE_SYSTEMCTL_LOG"
case "$*" in
  *show*MainPID*) echo "${FAKE_UNIT_MAINPID:-0}"; exit 0 ;;
  *is-active*) [ -n "${FAKE_UNIT_MAINPID:-}" ] && exit 0 || exit 3 ;;
esac
exit 0
"""

LISTENER = """
import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen(1)
print("ready", flush=True)
while True:
    time.sleep(0.1)
"""


def _make_exec(path: Path, body: str = PROBE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Rig:
    """A relocatable brain checkout (arbitrary path) with hermetic tooling."""

    def __init__(self, tmp_path: Path, root_name: str = "checkout"):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.tools = tmp_path / "tools"
        self.tools.mkdir(parents=True, exist_ok=True)
        herdr = tmp_path / root_name / "hosts" / "herdr"
        self.brain = herdr / "brain"
        self.tts = herdr / "tts-plugin"
        (self.brain / "bin").mkdir(parents=True)
        (self.brain / "deploy").mkdir(parents=True)
        shutil.copy2(BRAIN_SRC / "bin" / "herdr-brain", self.brain / "bin" / "herdr-brain")
        shutil.copy2(DEPLOY_SRC / "install.sh", self.brain / "deploy" / "install.sh")
        shutil.copy2(TEMPLATE, self.brain / "deploy" / TEMPLATE.name)
        _make_exec(self.brain / ".venv" / "bin" / "python")
        _make_exec(self.tts / "bin" / "herdr-tts")
        self.herdr_bin = _make_exec(self.tools / "herdr")
        self.systemctl_log = tmp_path / "systemctl.log"
        self.xdg_config = tmp_path / "xdg-config"
        self.procs: list[subprocess.Popen] = []

    @property
    def installer(self) -> Path:
        return self.brain / "deploy" / "install.sh"

    @property
    def unit(self) -> Path:
        return self.xdg_config / "systemd" / "user" / "herdr-brain.service"

    @property
    def env_file(self) -> Path:
        return self.xdg_config / "herdr-brain" / "env"

    def stub_systemd(self) -> None:
        _make_exec(self.tools / "systemctl", SYSTEMCTL_STUB)
        _make_exec(self.tools / "loginctl")
        _make_exec(self.tools / "curl", "#!/bin/sh\necho '{\"status\":\"ok\"}'\n")
        _make_exec(self.tools / "journalctl")

    def env(self, **extra: str) -> dict:
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.tools}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.xdg_config),
            "XDG_STATE_HOME": str(self.tmp / "xdg-state"),
            "FAKE_SYSTEMCTL_LOG": str(self.systemctl_log),
        }
        env.update(extra)
        return env

    def install(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.installer), *args],
            env=self.env(**env),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def listener(self, port: int) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", LISTENER, str(port)], stdout=subprocess.PIPE, text=True
        )
        self.procs.append(proc)
        assert proc.stdout.readline().strip() == "ready"
        return proc

    def systemctl_calls(self) -> list[str]:
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text().splitlines()

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


def _directive(unit_text: str, key: str) -> str:
    for line in unit_text.splitlines():
        if line.startswith(key + "="):
            return line[len(key) + 1 :]
    raise AssertionError(f"{key} missing from unit:\n{unit_text}")


class TestTemplateHygiene:
    def test_template_exists_and_static_unit_is_gone(self):
        assert TEMPLATE.is_file()
        assert not STATIC_UNIT.exists()

    def test_template_carries_every_design_placeholder(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for placeholder in DESIGN_PLACEHOLDERS:
            assert placeholder in text, placeholder

    def test_template_has_no_machine_specific_absolute_path(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        assert not re.search(r"/home/|/Users/|linuxbrew|\.dotfiles|%h/Code", text)

    def test_generated_unit_is_ignored_and_untracked(self):
        if not (REPO_ROOT / ".git").exists():
            pytest.skip("not a git checkout")
        rel = "hosts/herdr/brain/deploy/herdr-brain.service"
        ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO_ROOT)
        assert ignored.returncode == 0, "generated deploy/*.service must be git-ignored"
        tracked = subprocess.run(
            ["git", "ls-files", "--", "hosts/herdr/brain/deploy"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert not [t for t in tracked if t.endswith(".service")]
        assert "hosts/herdr/brain/deploy/herdr-brain.service.tmpl" in tracked


class TestGeneration:
    def test_generation_substitutes_discovered_values(self, rig):
        port = _free_port()
        result = rig.install("--generate-only", HERDR_BRAIN_PORT=str(port))
        assert result.returncode == 0, result.stderr
        text = rig.unit.read_text()
        assert not LEFTOVER.search(text)
        assert _directive(text, "WorkingDirectory") == str(rig.brain)
        assert _directive(text, "ExecStart") == f"{rig.brain}/.venv/bin/python -m herdr_brain.server"
        assert _directive(text, "Environment=HERDR_BIN") == str(rig.herdr_bin)
        assert _directive(text, "Environment=HERDR_BRAIN_PORT") == str(port)
        assert _directive(text, "Environment=HERDR_TTS_HOME") == str(rig.tts)
        assert _directive(text, "EnvironmentFile") == str(rig.env_file)
        assert text.startswith(MARKER)

    def test_default_and_persisted_port(self, rig):
        assert rig.install("--generate-only").returncode == 0
        assert _directive(rig.unit.read_text(), "Environment=HERDR_BRAIN_PORT") == "8741"
        cfg = rig.xdg_config / "herdr-brain" / "config.env"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('HERDR_BRAIN_PORT="9005"\n')
        assert rig.install("--generate-only").returncode == 0
        assert _directive(rig.unit.read_text(), "Environment=HERDR_BRAIN_PORT") == "9005"

    def test_explicit_herdr_bin_wins_over_path(self, rig):
        explicit = _make_exec(rig.tmp / "elsewhere" / "herdr")
        result = rig.install("--generate-only", HERDR_BIN=str(explicit))
        assert result.returncode == 0, result.stderr
        assert _directive(rig.unit.read_text(), "Environment=HERDR_BIN") == str(explicit)

    def test_homebrew_prefix_is_discovered_from_the_environment(self, rig, tmp_path):
        rig.herdr_bin.unlink()
        brew_herdr = _make_exec(tmp_path / "brew-prefix" / "bin" / "herdr")
        result = rig.install("--generate-only", HOMEBREW_PREFIX=str(tmp_path / "brew-prefix"))
        assert result.returncode == 0, result.stderr
        assert _directive(rig.unit.read_text(), "Environment=HERDR_BIN") == str(brew_herdr)

    def test_generate_only_touches_no_systemd_and_no_secrets(self, rig):
        rig.stub_systemd()
        result = rig.install("--generate-only", GLM_API_KEY="must-not-be-written")
        assert result.returncode == 0, result.stderr
        assert rig.systemctl_calls() == []
        assert not rig.env_file.exists()
        assert "must-not-be-written" not in rig.unit.read_text()

    def test_two_arbitrary_install_paths_render_distinct_units(self, tmp_path):
        a = Rig(tmp_path / "a")
        b = Rig(tmp_path / "b", root_name="some/deeper/clone")
        try:
            for r in (a, b):
                assert r.install("--generate-only").returncode == 0
            assert _directive(a.unit.read_text(), "WorkingDirectory") == str(a.brain)
            assert _directive(b.unit.read_text(), "WorkingDirectory") == str(b.brain)
        finally:
            a.close()
            b.close()

    def test_no_dotfiles_dependency(self, rig):
        private = rig.home / ".dotfiles" / "shell" / "private-env.sh"
        private.parent.mkdir(parents=True)
        private.write_text('export GLM_API_KEY="from-dotfiles"\n')
        result = rig.install("--generate-only")
        assert result.returncode == 0, result.stderr
        assert ".dotfiles" not in result.stdout + result.stderr
        assert "from-dotfiles" not in rig.unit.read_text()


class TestHardFailures:
    def test_unsubstituted_placeholder_is_a_hard_failure(self, rig):
        tmpl = rig.brain / "deploy" / TEMPLATE.name
        tmpl.write_text(tmpl.read_text() + "Environment=EXTRA=@BOGUS_VALUE@\n")
        result = rig.install("--generate-only")
        assert result.returncode != 0
        assert "@BOGUS_VALUE@" in result.stderr
        assert not rig.unit.exists()

    def test_missing_herdr_binary_fails_actionably(self, rig):
        rig.herdr_bin.unlink()
        result = rig.install("--generate-only")
        assert result.returncode != 0
        assert "HERDR_BIN" in result.stderr
        assert not rig.unit.exists()

    def test_unrelated_existing_unit_is_never_overwritten(self, rig):
        rig.unit.parent.mkdir(parents=True)
        rig.unit.write_text("[Unit]\nDescription=Somebody else's service\n")
        result = rig.install("--generate-only")
        assert result.returncode != 0
        assert str(rig.unit) in result.stderr
        assert rig.unit.read_text() == "[Unit]\nDescription=Somebody else's service\n"

    def test_install_path_that_systemd_cannot_represent_is_rejected(self, tmp_path):
        spaced = Rig(tmp_path / "x", root_name="my clone")
        try:
            result = spaced.install("--generate-only")
            assert result.returncode != 0
            assert "whitespace" in result.stderr or "cannot be represented" in result.stderr
            assert not spaced.unit.exists()
        finally:
            spaced.close()


class TestUnitOwnershipAndUpgrade:
    def test_legacy_static_unit_is_recognised_and_replaced(self, rig):
        rig.unit.parent.mkdir(parents=True)
        rig.unit.write_text(
            "[Unit]\nDescription=Herdr Brain (voice PWA backend)\n[Service]\nExecStart=/old/path\n"
        )
        result = rig.install("--generate-only")
        assert result.returncode == 0, result.stderr
        assert "/old/path" not in rig.unit.read_text()
        assert rig.unit.read_text().startswith(MARKER)

    def test_regeneration_is_idempotent(self, rig):
        assert rig.install("--generate-only").returncode == 0
        first = rig.unit.read_text()
        again = rig.install("--generate-only")
        assert again.returncode == 0
        assert rig.unit.read_text() == first
        assert "up to date" in again.stdout


class TestFullInstall:
    """Optional systemd route against hermetic stubs (no real systemd)."""

    def _prepare(self, rig):
        rig.stub_systemd()
        port = _free_port()
        listener = rig.listener(port)
        return port, listener

    def test_fresh_install_writes_secrets_safely_and_enables_the_unit(self, rig):
        port, unit_proc = self._prepare(rig)
        result = rig.install(
            HERDR_BRAIN_PORT=str(port),
            FAKE_UNIT_MAINPID=str(unit_proc.pid),
            GLM_API_KEY="s3cr3t-value",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "s3cr3t-value" not in result.stdout + result.stderr
        assert rig.env_file.read_text().strip().endswith("GLM_API_KEY=s3cr3t-value")
        assert oct(rig.env_file.stat().st_mode & 0o777) == "0o600"
        assert "s3cr3t-value" not in rig.unit.read_text()
        calls = rig.systemctl_calls()
        assert any("daemon-reload" in c for c in calls)
        assert any("enable" in c and "herdr-brain.service" in c for c in calls)

    def test_install_without_a_key_warns_and_still_creates_the_env_file(self, rig):
        port, unit_proc = self._prepare(rig)
        result = rig.install(HERDR_BRAIN_PORT=str(port), FAKE_UNIT_MAINPID=str(unit_proc.pid))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "GLM_API_KEY" in result.stdout + result.stderr
        assert rig.env_file.is_file()
        assert oct(rig.env_file.stat().st_mode & 0o777) == "0o600"

    def test_env_merge_preserves_unrelated_lines(self, rig):
        port, unit_proc = self._prepare(rig)
        rig.env_file.parent.mkdir(parents=True)
        rig.env_file.write_text("# mine\nOTHER=1\nGLM_API_KEY=old\n")
        result = rig.install(
            HERDR_BRAIN_PORT=str(port),
            FAKE_UNIT_MAINPID=str(unit_proc.pid),
            GLM_API_KEY="new",
        )
        assert result.returncode == 0, result.stderr
        lines = rig.env_file.read_text().splitlines()
        assert "# mine" in lines and "OTHER=1" in lines
        assert lines.count("GLM_API_KEY=new") == 1 and "GLM_API_KEY=old" not in lines

    def test_upgrade_stops_regenerates_reloads_then_restarts(self, rig):
        port, unit_proc = self._prepare(rig)
        env = dict(HERDR_BRAIN_PORT=str(port), FAKE_UNIT_MAINPID=str(unit_proc.pid))
        rig.unit.parent.mkdir(parents=True)
        rig.unit.write_text(f"{MARKER} old generation\n[Service]\nExecStart=/stale\n")
        result = rig.install(**env)
        assert result.returncode == 0, result.stdout + result.stderr
        calls = rig.systemctl_calls()
        order = {
            name: next(i for i, c in enumerate(calls) if name in c)
            for name in ("stop", "daemon-reload", "restart")
        }
        assert order["stop"] < order["daemon-reload"] < order["restart"]
        assert "/stale" not in rig.unit.read_text()

    def test_unchanged_reinstall_does_not_bounce_the_service(self, rig):
        port, unit_proc = self._prepare(rig)
        env = dict(HERDR_BRAIN_PORT=str(port), FAKE_UNIT_MAINPID=str(unit_proc.pid))
        assert rig.install(**env).returncode == 0
        rig.systemctl_log.write_text("")
        again = rig.install(**env)
        assert again.returncode == 0, again.stdout + again.stderr
        assert not any("restart" in c or " stop" in c for c in rig.systemctl_calls())

    def test_foreign_port_holder_aborts_before_any_change(self, rig):
        rig.stub_systemd()
        port = _free_port()
        foreign = rig.listener(port)
        result = rig.install(HERDR_BRAIN_PORT=str(port))  # no unit owns it
        assert result.returncode != 0
        assert foreign.poll() is None
        assert not rig.unit.exists()
        assert not any("daemon-reload" in c for c in rig.systemctl_calls())
