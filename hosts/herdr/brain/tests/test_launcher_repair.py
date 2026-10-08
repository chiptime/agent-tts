"""V1 for the ``bin/herdr-brain`` launcher repair (AT-11 task 2.2, RNF-4).

The launcher must carry no developer-machine coupling (maintainer dotfiles
scrape, literal Homebrew prefix, personal tailnet domain) and must resolve
its environment dynamically through the portable resolver block. Behavior is
proven by running a *copy* of the real launcher inside a sandbox layout whose
``.venv/bin/python`` is a probe that prints the environment it receives.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
BRAIN_LAUNCHER = REPO_ROOT / "hosts" / "herdr" / "brain" / "bin" / "herdr-brain"

MACHINE_PATTERNS = [
    (re.compile(r"\.dotfiles"), "maintainer dotfiles path"),
    (re.compile(r"/home/linuxbrew"), "literal brew prefix"),
    (re.compile(r"tail2640fd\.ts\.net"), "personal tailnet domain"),
    (re.compile(r"Code/personal/(?:herdr-tts|herdr-brain|agent-tts)"), "maintainer clone path"),
]

PROBE = """#!/bin/sh
# Stand-in for .venv/bin/python: report what the launcher exported.
echo "ARGV=$*"
echo "TTS_HOME=${HERDR_TTS_HOME:-}"
echo "HERDR_BIN=${HERDR_BIN:-}"
echo "PORT=${HERDR_BRAIN_PORT:-}"
echo "GLM=${GLM_API_KEY:-}"
if [ -x "${HERDR_TTS_HOME:-/nonexistent}/bin/herdr-tts" ]; then
  echo "TTS_SURFACE=ok"
else
  echo "TTS_SURFACE=missing"
fi
"""


def _make_exec(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _install_brain(brain: Path) -> Path:
    """Copy the real launcher into ``brain/bin`` and give it a probe venv."""
    launcher = brain / "bin" / "herdr-brain"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BRAIN_LAUNCHER, launcher)
    _make_exec(brain / ".venv" / "bin" / "python", PROBE)
    return launcher


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    return tmp_path, home, stubs


def _env(tmp_path: Path, home: Path, stubs: Path, **extra: str) -> dict:
    env = {
        "HOME": str(home),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
    }
    env.update(extra)
    return env


def _run(launcher: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(launcher), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _probe(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def _monorepo(tmp_path: Path) -> tuple[Path, Path]:
    """Corrected layout: brain and tts-plugin are siblings under hosts/herdr."""
    herdr = tmp_path / "checkout" / "hosts" / "herdr"
    launcher = _install_brain(herdr / "brain")
    _make_exec(herdr / "tts-plugin" / "bin" / "herdr-tts")
    return launcher, herdr / "tts-plugin"


class TestNoMachineCoupling:
    def test_launcher_has_no_machine_specific_pattern(self):
        findings = []
        for lineno, line in enumerate(BRAIN_LAUNCHER.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, desc in MACHINE_PATTERNS:
                if pattern.search(line):
                    findings.append(f"herdr-brain:{lineno}: {desc}: {line.strip()[:80]}")
        assert not findings, "\n".join(findings)

    def test_dotfiles_key_is_not_scraped(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        private = home / ".dotfiles" / "shell" / "private-env.sh"
        private.parent.mkdir(parents=True)
        private.write_text('export GLM_API_KEY="leaked-from-dotfiles"\n')
        result = _run(launcher, _env(tmp_path, home, stubs), "serve")
        assert result.returncode == 0, result.stderr
        assert _probe(result.stdout)["GLM"] == ""
        assert "leaked-from-dotfiles" not in result.stdout + result.stderr

    def test_env_file_key_is_still_discovered(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        env_file = tmp_path / "xdg-config" / "herdr-brain" / "env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("GLM_API_KEY=from-env-file\n")
        result = _run(launcher, _env(tmp_path, home, stubs), "serve")
        assert result.returncode == 0, result.stderr
        assert _probe(result.stdout)["GLM"] == "from-env-file"

    def test_exported_key_wins_over_env_file(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        env_file = tmp_path / "xdg-config" / "herdr-brain" / "env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("GLM_API_KEY=from-env-file\n")
        env = _env(tmp_path, home, stubs, GLM_API_KEY="from-process-env")
        result = _run(launcher, env, "serve")
        assert _probe(result.stdout)["GLM"] == "from-process-env"


class TestTtsSurfaceParity:
    """Legacy-shaped and corrected layouts both resolve the TTS surface."""

    def test_corrected_layout_resolves_sibling_tts_plugin(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, tts_home = _monorepo(tmp_path)
        result = _run(launcher, _env(tmp_path, home, stubs), "serve")
        assert result.returncode == 0, result.stderr
        probe = _probe(result.stdout)
        assert Path(probe["TTS_HOME"]).resolve() == tts_home.resolve()
        assert probe["TTS_SURFACE"] == "ok"

    def test_legacy_shaped_layout_honours_exported_tts_home(self, sandbox):
        tmp_path, home, stubs = sandbox
        # Standalone brain checkout: no sibling tts-plugin directory at all.
        launcher = _install_brain(tmp_path / "legacy" / "herdr-brain")
        legacy_tts = tmp_path / "legacy" / "herdr-tts"
        _make_exec(legacy_tts / "bin" / "herdr-tts")
        env = _env(tmp_path, home, stubs, HERDR_TTS_HOME=str(legacy_tts))
        result = _run(launcher, env, "serve")
        assert result.returncode == 0, result.stderr
        probe = _probe(result.stdout)
        assert probe["TTS_HOME"] == str(legacy_tts)
        assert probe["TTS_SURFACE"] == "ok"

    def test_exported_tts_home_wins_even_with_sibling_present(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, sibling = _monorepo(tmp_path)
        override = tmp_path / "split" / "tts-plugin"
        _make_exec(override / "bin" / "herdr-tts")
        env = _env(tmp_path, home, stubs, HERDR_TTS_HOME=str(override))
        probe = _probe(_run(launcher, env, "serve").stdout)
        assert probe["TTS_HOME"] == str(override)
        assert probe["TTS_HOME"] != str(sibling)

    def test_no_sibling_and_no_export_fails_actionably(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher = _install_brain(tmp_path / "lonely" / "herdr-brain")
        result = _run(launcher, _env(tmp_path, home, stubs), "serve")
        assert result.returncode != 0
        assert "HERDR_TTS_HOME" in result.stderr
        assert "tts-plugin" in result.stderr


class TestHerdrBinDiscovery:
    def test_binary_found_on_path(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        herdr = _make_exec(stubs / "herdr")
        probe = _probe(_run(launcher, _env(tmp_path, home, stubs), "serve").stdout)
        assert probe["HERDR_BIN"] == str(herdr)

    def test_explicit_herdr_bin_wins(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        _make_exec(stubs / "herdr")
        env = _env(tmp_path, home, stubs, HERDR_BIN="/opt/custom/herdr")
        assert _probe(_run(launcher, env, "serve").stdout)["HERDR_BIN"] == "/opt/custom/herdr"

    def test_homebrew_prefix_is_environment_derived(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        brew_herdr = _make_exec(tmp_path / "brew-prefix" / "bin" / "herdr")
        env = _env(tmp_path, home, stubs, HOMEBREW_PREFIX=str(tmp_path / "brew-prefix"))
        assert _probe(_run(launcher, env, "serve").stdout)["HERDR_BIN"] == str(brew_herdr)

    def test_local_bin_then_bare_name_fallback(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        env = _env(tmp_path, home, stubs)
        assert _probe(_run(launcher, env, "serve").stdout)["HERDR_BIN"] == "herdr"
        local = _make_exec(home / ".local" / "bin" / "herdr")
        assert _probe(_run(launcher, env, "serve").stdout)["HERDR_BIN"] == str(local)


class TestRemoteUrlIsConfigurableOnly:
    def test_url_does_not_probe_tailscale_nor_print_a_personal_domain(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        marker = tmp_path / "tailscale-was-called"
        _make_exec(
            stubs / "tailscale",
            f"#!/bin/sh\ntouch {marker}\necho '100.64.0.1 desktop-abc user linux -'\n",
        )
        result = _run(launcher, _env(tmp_path, home, stubs), "url")
        assert result.returncode == 0, result.stderr
        assert "Local URL: http://localhost:8741/" in result.stdout
        assert "ts.net" not in result.stdout
        assert not marker.exists()

    def test_remote_url_from_environment(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        env = _env(tmp_path, home, stubs, HERDR_BRAIN_REMOTE_URL="https://brain.example.test:8443/")
        result = _run(launcher, env, "url")
        assert "Remote URL: https://brain.example.test:8443/" in result.stdout

    def test_remote_url_from_persisted_config(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        cfg = tmp_path / "xdg-config" / "herdr-brain" / "config.env"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('# managed\nHERDR_BRAIN_REMOTE_URL="https://cfg.example.test/"\n')
        result = _run(launcher, _env(tmp_path, home, stubs), "url")
        assert "Remote URL: https://cfg.example.test/" in result.stdout

    def test_environment_remote_url_wins_over_config(self, sandbox):
        tmp_path, home, stubs = sandbox
        launcher, _ = _monorepo(tmp_path)
        cfg = tmp_path / "xdg-config" / "herdr-brain" / "config.env"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("HERDR_BRAIN_REMOTE_URL=https://cfg.example.test/\n")
        env = _env(tmp_path, home, stubs, HERDR_BRAIN_REMOTE_URL="https://env.example.test/")
        result = _run(launcher, env, "url")
        assert "Remote URL: https://env.example.test/" in result.stdout
        assert "cfg.example.test" not in result.stdout


def test_launcher_is_executable():
    assert os.access(BRAIN_LAUNCHER, os.X_OK)
