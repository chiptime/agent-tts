"""Cutover tooling (S8, doc 11 — the code part).

The live cutover (taking 443, uninstalling the other panel) is the
operator's; this unit pins the code halves:

* ``GET /pane/{id}`` — the deep-link compat route: a 302 to
  ``/?pane_id=<id>`` so an already-configured tts-plugin «Open Web»
  button keeps working against the brain.
* ``serve``/``unserve`` — PRINT (never run) the exact ``tailscale
  serve`` commands, for the 8443 coexistence port or the root 443,
  with a warning when recorded ``tailscale serve status --json`` shows
  ANOTHER service owning the root.
* ``qr`` — the PWA URL; an ASCII QR needs an encoder this stdlib-only
  unit does not carry, so the verb says so instead of printing an
  unscannable block.
* ``logs`` — tail the daemon log with ``-n``/``-f``.
* the launchd plist template + installer under deploy/ (never executed
  by the repo).
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain import ops_cli
from herdr_brain.config import Settings
from herdr_brain.server import create_app

from tests.conftest import SETTINGS_KWARGS

# ── GET /pane/{pane_id}: the deep-link compat redirect ──────────────────────


class FakeTTS:
    def __call__(self, settings, text, out_path):
        out_path.write_bytes(b"ID3-fake")
        return out_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.test_call_context import FakeReplayTools, FakeGatedLLM

    class ToolsWithPaneInfo(FakeReplayTools):
        def pane_info(self, pane_id):
            return None

    monkeypatch.setattr("herdr_brain.server.BrainTools", ToolsWithPaneInfo)
    app = create_app(
        settings=Settings(**{
            **SETTINGS_KWARGS,
            "state_dir": str(tmp_path / "state"),
            "audio_dir": str(tmp_path / "audio"),
        }),
        llm_factory=lambda _cfg, _tools: FakeGatedLLM(),
        tts_renderer=FakeTTS(),
    )
    return TestClient(app)


class TestPaneDeepLink:
    def test_redirects_to_the_pwa_shell_with_the_pane_preselected(self, client):
        resp = client.get("/pane/w1:p2", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/?pane_id=w1%3Ap2"

    def test_special_characters_are_percent_encoded(self, client):
        resp = client.get("/pane/a%20b", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/?pane_id=a%20b"

    def test_the_existing_pane_routes_are_not_captured(self, client):
        # Two-segment GET routes registered before the compat route keep
        # answering themselves (never a 302 to the shell).
        assert client.get("/pane/receipts").status_code == 200
        resp = client.get("/pane/permission", params={"pane_id": "w1:p9"})
        assert resp.status_code != 302

    def test_the_route_is_read_classified(self):
        from herdr_brain import access

        assert access.classify("GET", "/pane/w1:p2") == "read"


# ── serve / unserve: printed, never run ─────────────────────────────────────


class TestServeVerbs:
    def test_serve_prints_the_coexistence_command(self, tmp_path, capsys):
        exit_code = ops_cli.main(["serve"], env={"HERDR_BRAIN_PORT": "8741"})
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "tailscale serve --bg --https=8443 http://127.0.0.1:8741" in out

    def test_serve_root_prints_the_root_command(self, tmp_path, capsys):
        exit_code = ops_cli.main(["serve", "--root"], env={"HERDR_BRAIN_PORT": "8741"})
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "tailscale serve --bg http://127.0.0.1:8741" in out

    def test_serve_warns_when_another_service_owns_443(self, tmp_path, capsys):
        status = tmp_path / "status.json"
        status.write_text(
            '{"Port": 443, "Proxy": {"443": {"Targets": '
            '[{"Proxy": "http://127.0.0.1:8787"}]}}}'
        )
        exit_code = ops_cli.main(
            ["serve", "--root", "--serve-status", str(status)],
            env={"HERDR_BRAIN_PORT": "8741"},
        )
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "another service owns the HTTPS root port" in captured.out + captured.err

    def test_serve_reports_when_the_root_is_already_ours(self, tmp_path, capsys):
        status = tmp_path / "status.json"
        status.write_text(
            '{"Port": 443, "Proxy": {"443": {"Targets": '
            '[{"Proxy": "http://127.0.0.1:8741"}]}}}'
        )
        exit_code = ops_cli.main(
            ["serve", "--root", "--serve-status", str(status)],
            env={"HERDR_BRAIN_PORT": "8741"},
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "already points at this brain" in out

    def test_unserve_prints_the_exact_unset_commands(self, tmp_path, capsys):
        assert ops_cli.main(["unserve"], env={}) == 0
        out = capsys.readouterr().out
        assert "tailscale serve --https=8443 off" in out
        assert ops_cli.main(["unserve", "--root"], env={}) == 0
        out = capsys.readouterr().out
        assert "tailscale serve off" in out

    def test_serve_never_executes_anything(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
        ops_cli.main(["serve"], env={})
        ops_cli.main(["unserve", "--root"], env={})
        assert calls == []


# ── qr ──────────────────────────────────────────────────────────────────────


class TestQrVerb:
    def test_prints_the_public_url_and_says_why_no_qr(self, tmp_path, capsys):
        exit_code = ops_cli.main(
            ["qr"], env={"HERDR_BRAIN_REMOTE_URL": "https://node.tail.ts.net:8443/"}
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "https://node.tail.ts.net:8443/" in out
        assert "QR" in out  # names the limitation honestly

    def test_without_a_remote_url_it_says_so(self, tmp_path, capsys):
        exit_code = ops_cli.main(["qr"], env={})
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "remote URL" in captured.err


# ── logs ────────────────────────────────────────────────────────────────────


class TestLogsVerb:
    def _log(self, tmp_path) -> Path:
        state = tmp_path / "state"
        state.mkdir(parents=True)
        log = state / "server.log"
        log.write_text("".join(f"line-{i}\n" for i in range(1, 101)))
        return log

    def test_tail_prints_the_last_n_lines(self, tmp_path, capsys):
        self._log(tmp_path)
        exit_code = ops_cli.main(
            ["logs", "-n", "5"], env={"HERDR_BRAIN_STATE_DIR": str(tmp_path / "state")}
        )
        out = capsys.readouterr().out.splitlines()
        assert exit_code == 0
        assert out == [f"line-{i}" for i in range(96, 101)]

    def test_default_tail_is_20_lines(self, tmp_path, capsys):
        self._log(tmp_path)
        exit_code = ops_cli.main(
            ["logs"], env={"HERDR_BRAIN_STATE_DIR": str(tmp_path / "state")}
        )
        out = capsys.readouterr().out.splitlines()
        assert exit_code == 0
        assert len(out) == 20 and out[0] == "line-81"

    def test_missing_log_is_an_honest_error(self, tmp_path, capsys):
        exit_code = ops_cli.main(
            ["logs"], env={"HERDR_BRAIN_STATE_DIR": str(tmp_path / "nowhere")}
        )
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "server.log" in captured.err

    def test_follow_streams_appended_lines_and_stops(self, tmp_path, capsys):
        log = self._log(tmp_path)
        # The follow loop stops after the injected sleeper ran twice.
        state = {"n": 0}

        def fake_sleep(_s):
            state["n"] += 1
            if state["n"] == 1:
                with log.open("a") as handle:
                    handle.write("line-101\n")
            if state["n"] >= 2:
                raise KeyboardInterrupt

        exit_code = ops_cli.main(
            ["logs", "-n", "1", "-f"],
            env={"HERDR_BRAIN_STATE_DIR": str(tmp_path / "state")},
            _sleep=fake_sleep,
        )
        out = capsys.readouterr().out.splitlines()
        assert exit_code == 0
        assert "line-100" in out and "line-101" in out


# ── deploy: the launchd template (never executed) ───────────────────────────


class TestLaunchdDeploy:
    def test_plist_template_parses_and_mirrors_the_systemd_unit(self):
        template = Path(__file__).resolve().parents[1] / "deploy" / "herdr-brain.plist.template"
        text = template.read_text(encoding="utf-8")
        rendered = text.replace("@INSTALL_DIR@", "/opt/brain").replace(
            "@PYTHON@", "/opt/brain/.venv/bin/python"
        ).replace("@PORT@", "8741").replace("@HERDR_BIN@", "/usr/bin/herdr").replace(
            "@TTS_HOME@", "/opt/tts"
        )
        plist = plistlib.loads(rendered.encode("utf-8"))
        assert plist["KeepAlive"] is True
        assert plist["RunAtLoad"] is True
        assert plist["ProgramArguments"][:2] == [
            "/opt/brain/.venv/bin/python", "-m", "herdr_brain.server",
        ][:2]
        env = plist["EnvironmentVariables"]
        assert env["HERDR_BRAIN_PORT"] == "8741"
        assert env["HERDR_BIN"] == "/usr/bin/herdr"

    def test_install_script_is_bash_clean_and_never_runs_here(self):
        script = Path(__file__).resolve().parents[1] / "deploy" / "install-launchd.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
