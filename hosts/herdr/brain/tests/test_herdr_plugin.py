"""FR-12/G-11: the desktop voice-call entry is a real plugin action.

Two halves:
- Manifest: ``herdr brain call`` is a declared plugin action whose
  command matches the launcher's implemented subcommands, and the four
  pre-existing actions are unchanged.
- CLI: ``bin/herdr-brain call`` health-checks the service, opens the
  call URL through a stubbed browser opener and reports the browser
  consent/gesture truthfully. Every subprocess boundary (curl, the
  opener) is a stub inside an isolated HOME/PATH/TMPDIR — no test ever
  opens a real browser, starts the real service or touches the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

BRAIN_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = BRAIN_ROOT / "bin" / "herdr-brain"
MANIFEST = BRAIN_ROOT / "herdr-plugin.toml"

EXPECTED_LEGACY_ACTIONS = {
    "status": ["bin/herdr-brain", "status"],
    "url": ["bin/herdr-brain", "url"],
    "restart": ["bin/herdr-brain", "restart"],
    "stop": ["bin/herdr-brain", "stop"],
}


def _load_manifest() -> dict:
    import tomllib

    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def _stub(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Isolated HOME/PATH/TMPDIR with stub curl and browser opener.

    Both stubs append one line per invocation to ``calls.log`` so tests
    assert exactly which boundaries fired. The PATH keeps the system
    directories for readlink/mkdir; xdg-open absence is not assumed —
    tests that need a specific opener pin HERDR_BRAIN_BROWSER.
    """
    home = tmp_path / "home"
    stub_dir = tmp_path / "stubs"
    for directory in (home, stub_dir, home / ".local" / "state", home / ".config"):
        directory.mkdir(parents=True)
    calls = tmp_path / "calls.log"
    stub_dir.joinpath("calls.log").symlink_to(calls)

    _stub(stub_dir / "curl", f"""\
        #!/bin/sh
        # Stub service boundary: answers the launcher's health probe.
        echo "curl $*" >> "{calls}"
        if [ -n "${{STUB_CURL_DOWN:-}}" ]; then exit 7; fi
        for arg in "$@"; do
          case "$arg" in
            http://127.0.0.1:*/health) echo '{{"status": "ok"}}'; exit 0 ;;
          esac
        done
        exit 7
    """)
    _stub(stub_dir / "open-browser", f"""\
        #!/bin/sh
        # Stub desktop browser opener: records the URL it was handed.
        echo "open $*" >> "{calls}"
        exit ${{STUB_OPENER_RC:-0}}
    """)
    _stub(stub_dir / "xdg-open", f"""\
        #!/bin/sh
        # Stub fallback opener (only reached when HERDR_BRAIN_BROWSER is unset).
        echo "xdg-open $*" >> "{calls}"
        exit ${{STUB_OPENER_RC:-0}}
    """)

    env = {
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "TMPDIR": str(tmp_path),
        "PATH": f"{stub_dir}:/usr/bin:/bin",
        "HERDR_BRAIN_PORT": "8765",
        "HERDR_BRAIN_BROWSER": str(stub_dir / "open-browser"),
        "HERDR_ONBOARDING_NO_FIRST_RUN": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return calls


def run_cli(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k.startswith(("HERDR_", "XDG_", "TMPDIR", "HOME", "PATH"))}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(LAUNCHER), *args], capture_output=True, text=True, timeout=30, env=env
    )


def read_calls(calls: Path) -> list[str]:
    if not calls.exists():
        return []
    return [line for line in calls.read_text().splitlines() if line.strip()]


class TestManifestDeclaresCallAction:
    def test_call_action_is_declared_with_the_launcher_command(self):
        actions = {a["id"]: a for a in _load_manifest()["actions"]}
        assert "call" in actions
        call = actions["call"]
        assert call["command"] == ["bin/herdr-brain", "call"]
        assert call["title"]
        assert call["description"]
        assert call["contexts"] == ["workspace", "global"]

    def test_legacy_actions_are_unchanged(self):
        actions = {a["id"]: a for a in _load_manifest()["actions"]}
        for action_id, command in EXPECTED_LEGACY_ACTIONS.items():
            assert action_id in actions, f"pre-existing action {action_id} disappeared"
            assert actions[action_id]["command"] == command
        assert set(actions) == set(EXPECTED_LEGACY_ACTIONS) | {"call"}

    def test_every_manifest_action_is_implemented_by_the_launcher(self):
        launcher = LAUNCHER.read_text(encoding="utf-8")
        for action in _load_manifest()["actions"]:
            sub = action["command"][1]
            pattern = f"{sub}|--{sub})"
            assert pattern in launcher, f"action {sub!r} has no launcher case"
            assert f"cmd_{sub}()" in launcher, f"action {sub!r} has no cmd_ function"


class TestCallCommand:
    def test_healthy_service_opens_the_call_ui_once(self, cli_env):
        result = run_cli("call")
        assert result.returncode == 0, result.stderr
        calls = read_calls(cli_env)
        assert "curl -fsS -m 2 http://127.0.0.1:8765/health" in calls
        assert calls.count("open http://localhost:8765/") == 1
        # Truthful desktop guidance: consent and gesture are named.
        assert "http://localhost:8765/" in result.stdout
        assert "mic" in result.stdout.lower()

    def test_down_service_refuses_to_open_and_says_how_to_start(self, cli_env):
        result = run_cli("call", extra_env={"STUB_CURL_DOWN": "1"})
        assert result.returncode != 0
        assert "restart" in result.stderr
        assert "http://localhost:8765/" in result.stderr
        assert read_calls(cli_env) == ["curl -fsS -m 2 http://127.0.0.1:8765/health"]

    def test_missing_opener_is_an_honest_error(self, tmp_path, monkeypatch):
        # No HERDR_BRAIN_BROWSER, and a PATH with no xdg-open/open at all
        # (only bash and readlink, which launcher bootstrapping needs).
        import shutil

        bash = shutil.which("bash")
        assert bash, "bash must exist for the launcher"
        home = tmp_path / "home"
        minimal = tmp_path / "minimal"
        home.mkdir(parents=True)
        minimal.mkdir(parents=True)
        minimal.joinpath("bash").symlink_to(bash)
        for tool in ("readlink", "dirname", "mkdir"):
            _stub(minimal / tool, f"#!/bin/sh\nexec /usr/bin/{tool} \"$@\"\n")
        # The health probe must succeed so the failure under test is the
        # MISSING OPENER, not a dead service.
        _stub(minimal / "curl", """\
            #!/bin/sh
            for arg in "$@"; do
              case "$arg" in
                http://127.0.0.1:*/health) echo '{"status": "ok"}'; exit 0 ;;
              esac
            done
            exit 7
        """)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        monkeypatch.setenv("PATH", str(minimal))
        monkeypatch.setenv("HERDR_BRAIN_PORT", "8765")
        monkeypatch.setenv("HERDR_ONBOARDING_NO_FIRST_RUN", "1")
        monkeypatch.delenv("HERDR_BRAIN_BROWSER", raising=False)
        result = run_cli("call")
        assert result.returncode != 0
        assert "HERDR_BRAIN_BROWSER" in result.stderr
        assert "http://localhost:8765/" in result.stderr

    def test_failing_opener_reports_failure_without_lying(self, cli_env):
        result = run_cli("call", extra_env={"STUB_OPENER_RC": "3"})
        assert result.returncode != 0
        assert "failed" in result.stderr
        assert "http://localhost:8765/" in result.stderr

    def test_help_lists_call_next_to_the_legacy_commands(self, cli_env):
        result = run_cli("help")
        assert result.returncode == 0
        assert "call" in result.stdout
        for legacy in EXPECTED_LEGACY_ACTIONS:
            assert legacy in result.stdout


class TestLegacyCommandsUnchanged:
    """PRESERVE: the new action must not alter old launcher behavior."""

    def test_url_output_shape(self, cli_env):
        result = run_cli("url")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["Local URL: http://localhost:8765/"]

    def test_status_reports_health_and_url(self, cli_env):
        result = run_cli("status")
        assert result.returncode == 0
        assert "Health Check: OK (200)" in result.stdout
        assert "Local URL: http://localhost:8765/" in result.stdout

    def test_resolve_port_env_override(self, cli_env):
        result = run_cli("_resolve", "port")
        assert result.returncode == 0
        assert result.stdout.strip() == "8765"
