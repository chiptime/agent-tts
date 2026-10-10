"""V1 for the completion health gate (AT-11 task 3.5, design slice 19).

The gate is the only thing allowed to authorize the completion marker:
``/health`` must report ``tts: ok`` AND ``herdr plugin list`` must exit 0
without a single warning line.  The ``stt`` field is deliberately outside
the gate (``unavailable`` is the correct state after an STT refusal).

Boundaries: the HTTP probe and the ``herdr`` subprocess are FAKES labelled
as such (``FakeFetch`` / ``FakeHerdr``) — except where a test says
otherwise: ``TestRefusalNeverFailsTheGate`` feeds the gate the REAL brain
``create_app`` ``/health`` handler through ``TestClient`` (in-process; no
socket, no model, no network), and the subprocess tests run the REAL
``python -m herdr_onboarding`` with a stub ``herdr`` script and an injected
transport failure, never a real loopback port. Every test is HOME-isolated;
nothing reads real credentials or
configuration, and nothing touches a real service.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.request
import urllib.response
from email.message import Message
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_brain.config import Settings  # noqa: E402
from herdr_brain.server import create_app  # noqa: E402
from herdr_brain.stt import Transcriber  # noqa: E402
from herdr_onboarding import cli as cli_mod  # noqa: E402
from herdr_onboarding import health  # noqa: E402
from herdr_onboarding.cli import main  # noqa: E402
from herdr_onboarding.steps.stt import SttStep  # noqa: E402
from herdr_onboarding.wizard import Wizard, WizardOptions  # noqa: E402
from tests.test_server import FakeLLM, FakeTTS  # noqa: E402


class FakeFetch:
    """Scripted ``/health`` probe: each call pops the next scripted reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _ok(tts="ok", stt="unavailable"):
    return (200, json.dumps({"status": "ok", "version": "t", "stt": stt, "tts": tts}))


class FakeHerdr:
    """Scripted ``herdr plugin list`` runner."""

    def __init__(self, stdout="herdr.tts  enabled\n", stderr="", code=0, error=None):
        self.stdout, self.stderr, self.code, self.error = stdout, stderr, code, error
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


def _no_sleep(_seconds):
    return None


def _env(home: Path, **extra) -> dict:
    env = {"HOME": str(home)}  # hermetic: no ambient XDG_*/HERDR_* leaks in
    env.update(extra)
    return env


def _marker(home: Path) -> Path:
    return home / ".config" / "herdr-tts" / "first-run.done"


def _gate(fetch=None, runner=None, attempts=3):
    return health.make_health_gate(
        fetch=fetch or FakeFetch(_ok("ok")),
        runner=runner or FakeHerdr(),
        sleep=_no_sleep,
        attempts=attempts,
    )


def _run(argv, *, home, gate, steps=(), env=None):
    out, err = StringIO(), StringIO()
    code = main(
        argv,
        env=env if env is not None else _env(home),
        stdin=StringIO(),
        stdout=out,
        stderr=err,
        isatty=lambda: False,
        health_gate=gate,
        steps=list(steps),
    )
    return code, out.getvalue(), err.getvalue()


class TestBrainHealthCheck:
    @pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 305, 306, 307, 308])
    def test_redirect_never_requests_or_accepts_remote_health(self, tmp_path, monkeypatch, status):
        requests = []
        remote = "http://remote-health.invalid/health"

        class MemoryHTTP(urllib.request.HTTPHandler):
            """External transport double; urllib's redirect machinery stays real."""

            def http_open(self, request):
                requests.append(request.full_url)
                headers = Message()
                headers["Location"] = remote
                # The redirected response would pass the gate if requested.
                code = status if len(requests) == 1 else 200
                response = urllib.response.addinfourl(
                    BytesIO(b'{"tts":"ok"}'), headers, request.full_url, code
                )
                response.msg = "memory transport fixture"
                return response

        def forbidden(*args, **kwargs):
            raise AssertionError("redirect test must never create a socket")

        monkeypatch.setattr(urllib.request, "HTTPHandler", MemoryHTTP)
        monkeypatch.setattr(socket, "socket", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        result = health.check_brain_health(
            _env(tmp_path), attempts=1, sleep=_no_sleep
        )
        assert not result.ok
        assert f"HTTP {status}" in result.detail
        assert requests == ["http://127.0.0.1:8741/health"]

    def test_tts_ok_passes(self, tmp_path):
        result = health.check_brain_health(
            _env(tmp_path), fetch=FakeFetch(_ok("ok")), sleep=_no_sleep
        )
        assert result.ok
        assert "tts: ok" in result.detail

    @pytest.mark.parametrize("tts", ["degraded", "missing", "unknown", None])
    def test_any_other_tts_value_fails(self, tmp_path, tts):
        result = health.check_brain_health(
            _env(tmp_path), fetch=FakeFetch(_ok(tts)), sleep=_no_sleep, attempts=2
        )
        assert not result.ok
        assert repr(tts) in result.detail

    @pytest.mark.parametrize("stt", ["unavailable", "loading", "ready"])
    def test_stt_value_never_influences_the_gate(self, tmp_path, stt):
        result = health.check_brain_health(
            _env(tmp_path), fetch=FakeFetch(_ok("ok", stt)), sleep=_no_sleep
        )
        assert result.ok

    def test_http_error_status_fails(self, tmp_path):
        result = health.check_brain_health(
            _env(tmp_path), fetch=FakeFetch((503, "nope")), sleep=_no_sleep, attempts=2
        )
        assert not result.ok and "HTTP 503" in result.detail

    def test_non_json_body_fails(self, tmp_path):
        result = health.check_brain_health(
            _env(tmp_path), fetch=FakeFetch((200, "<html>")), sleep=_no_sleep, attempts=2
        )
        assert not result.ok and "JSON" in result.detail

    def test_unreachable_brain_fails_with_the_remedy(self, tmp_path):
        result = health.check_brain_health(
            _env(tmp_path),
            fetch=FakeFetch(ConnectionRefusedError("refused")),
            sleep=_no_sleep,
            attempts=2,
        )
        assert not result.ok
        assert "unreachable" in result.detail and "herdr-brain restart" in result.detail

    def test_retries_until_the_daemon_reports_ok(self, tmp_path):
        sleeps = []
        fetch = FakeFetch(ConnectionRefusedError("starting"), _ok("degraded"), _ok("ok"))
        result = health.check_brain_health(
            _env(tmp_path), fetch=fetch, sleep=sleeps.append, attempts=5, delay=0.25
        )
        assert result.ok
        assert len(fetch.urls) == 3
        assert sleeps == [0.25, 0.25]

    def test_port_follows_the_portable_resolution_order(self, tmp_path):
        env = _env(tmp_path, HERDR_BRAIN_PORT="18741")
        fetch = FakeFetch(_ok())
        health.check_brain_health(env, fetch=fetch, sleep=_no_sleep)
        assert fetch.urls == ["http://127.0.0.1:18741/health"]

        persisted = tmp_path / ".config" / "herdr-brain"
        persisted.mkdir(parents=True)
        (persisted / "config.env").write_text("HERDR_BRAIN_PORT=19999\n")
        fetch = FakeFetch(_ok())
        health.check_brain_health(_env(tmp_path), fetch=fetch, sleep=_no_sleep)
        assert fetch.urls == ["http://127.0.0.1:19999/health"]

    def test_default_port_is_8741(self, tmp_path):
        fetch = FakeFetch(_ok())
        health.check_brain_health(_env(tmp_path), fetch=fetch, sleep=_no_sleep)
        assert fetch.urls == ["http://127.0.0.1:8741/health"]

    def test_invalid_port_fails_without_probing(self, tmp_path):
        fetch = FakeFetch(_ok())
        result = health.check_brain_health(
            _env(tmp_path, HERDR_BRAIN_PORT="not-a-port"), fetch=fetch, sleep=_no_sleep
        )
        assert not result.ok and fetch.urls == []


class TestPluginListCheck:
    def test_clean_list_passes_and_uses_the_resolved_binary(self, tmp_path):
        herdr = FakeHerdr()
        result = health.check_plugin_list(
            _env(tmp_path, HERDR_BIN="/opt/herdr/bin/herdr"), runner=herdr
        )
        assert result.ok
        assert herdr.calls == [["/opt/herdr/bin/herdr", "plugin", "list"]]

    @pytest.mark.parametrize(
        "stream", ["stdout", "stderr"], ids=["warning-on-stdout", "warning-on-stderr"]
    )
    def test_manifest_warning_fails(self, tmp_path, stream):
        line = "warning: manifest unavailable: No such file or directory (os error 2)\n"
        herdr = FakeHerdr(**{stream: line})
        result = health.check_plugin_list(_env(tmp_path), runner=herdr)
        assert not result.ok
        assert "manifest unavailable" in result.detail
        assert "herdr plugin unlink" in result.detail

    def test_indented_uppercase_warning_still_counts(self, tmp_path):
        result = health.check_plugin_list(
            _env(tmp_path), runner=FakeHerdr(stdout="  WARNING  something odd\n")
        )
        assert not result.ok

    def test_colored_warning_still_blocks_completion(self, tmp_path):
        result = health.check_plugin_list(
            _env(tmp_path), runner=FakeHerdr(stderr="\x1b[33mwarning:\x1b[0m failed to load plugin manifest\n")
        )
        assert not result.ok

    def test_a_plugin_merely_named_warning_is_not_a_warning(self, tmp_path):
        result = health.check_plugin_list(
            _env(tmp_path), runner=FakeHerdr(stdout="herdr.warnings-demo  enabled\n")
        )
        assert result.ok

    def test_nonzero_exit_fails(self, tmp_path):
        result = health.check_plugin_list(_env(tmp_path), runner=FakeHerdr(code=3))
        assert not result.ok and "exited 3" in result.detail

    def test_missing_binary_fails_naming_the_override(self, tmp_path):
        result = health.check_plugin_list(
            _env(tmp_path), runner=FakeHerdr(error=FileNotFoundError("no herdr"))
        )
        assert not result.ok and "HERDR_BIN" in result.detail

    def test_timeout_fails(self, tmp_path):
        err = subprocess.TimeoutExpired(["herdr"], 30)
        result = health.check_plugin_list(_env(tmp_path), runner=FakeHerdr(error=err))
        assert not result.ok


class TestGateComposition:
    @pytest.mark.parametrize("json_output", [False, True])
    def test_interrupted_gate_exits_40_without_traceback_or_marker(self, tmp_path, json_output):
        # Authoritative design.md:1348 assigns 40 to user abort, not exit 10.
        secret = "fixture-interrupted-gate-canary"

        class Step:
            name, roles = "s", ("plugin",)

            def run(self, ctx):
                ctx.register_secret(secret)

        def interrupted(ctx):
            raise KeyboardInterrupt(secret)

        argv = ["--role", "plugin"] + (["--json"] if json_output else [])
        try:
            code, out, err = _run(argv, home=tmp_path, gate=interrupted, steps=[Step()])
        except KeyboardInterrupt:
            pytest.fail("KeyboardInterrupt escaped the wizard gate boundary")
        assert code == 40
        assert "aborted" in err + out
        assert "Traceback" not in err + out and secret not in err + out
        assert not _marker(tmp_path).exists()
        if json_output:
            record = json.loads(out)
            assert record["status"] == "aborted" and record["exit"] == 40
            assert record["marker_written"] is False

    def test_both_checks_must_pass(self, tmp_path):
        code, _, err = _run(["--role", "plugin"], home=tmp_path, gate=_gate())
        assert code == 0, err
        assert _marker(tmp_path).is_file()

    def test_unhealthy_brain_means_exit_30_and_no_marker(self, tmp_path):
        gate = _gate(fetch=FakeFetch(_ok("degraded")), attempts=2)
        code, _, err = _run(["--role", "plugin"], home=tmp_path, gate=gate)
        assert code == 30
        assert not _marker(tmp_path).exists()
        assert "FAILED" in err and "next launch will retry" in err

    def test_plugin_warning_means_exit_30_and_no_marker(self, tmp_path):
        herdr = FakeHerdr(stdout="warning: manifest unavailable: gone\n")
        code, _, err = _run(["--role", "plugin"], home=tmp_path, gate=_gate(runner=herdr))
        assert code == 30
        assert not _marker(tmp_path).exists()
        assert "manifest unavailable" in err

    def test_both_failures_are_reported_not_just_the_first(self, tmp_path):
        gate = _gate(
            fetch=FakeFetch(ConnectionRefusedError("x")),
            runner=FakeHerdr(code=1),
            attempts=1,
        )
        code, _, err = _run(["--role", "plugin"], home=tmp_path, gate=gate)
        assert code == 30
        assert "brain /health: FAILED" in err and "plugin list: FAILED" in err

    def test_retry_after_a_failure_completes_cleanly(self, tmp_path):
        failing = _gate(fetch=FakeFetch(ConnectionRefusedError("down")), attempts=1)
        assert _run(["--role", "plugin"], home=tmp_path, gate=failing)[0] == 30
        assert not _marker(tmp_path).exists()
        assert _run(["--role", "plugin"], home=tmp_path, gate=_gate())[0] == 0
        assert _marker(tmp_path).is_file()

    def test_marker_is_the_atomic_last_step(self, tmp_path):
        events = []

        class Step:
            name, roles = "s", ("plugin",)

            def run(self, ctx):
                events.append("step")

        def gate(ctx):
            events.append("gate")
            assert not _marker(tmp_path).exists()  # still absent while the gate runs
            return True

        code, _, _ = _run(["--role", "plugin"], home=tmp_path, gate=gate, steps=[Step()])
        assert code == 0 and events == ["step", "gate"]
        assert _marker(tmp_path).is_file()

    def test_gate_output_is_redacted_through_the_diagnostic_boundary(self, tmp_path):
        secret = "sk-gate-canary-7Hq2"
        herdr = FakeHerdr(stdout=f"warning: manifest unavailable: {secret}\n")

        class Step:
            name, roles = "s", ("plugin",)

            def run(self, ctx):
                ctx.register_secret(secret)

        code, _, err = _run(
            ["--role", "plugin"], home=tmp_path, gate=_gate(runner=herdr), steps=[Step()]
        )
        assert code == 30
        assert secret not in err and "***" in err


class TestRefusalNeverFailsTheGate:
    """Spec: 'no later wizard or launch step fails because of the missing
    model'.  The gate reads the REAL brain /health handler (in-process)."""

    def _fetch_from_app(self, settings, tmp_path, transcriber):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
        client = TestClient(
            create_app(
                settings=cfg,
                version="t",
                transcriber=transcriber,
                llm_factory=lambda _c, _t: FakeLLM(),
                tts_renderer=FakeTTS(),
                daemon_probe=lambda: "up",  # external daemon double, no live socket
            )
        )

        def fetch(_url, _timeout):
            reply = client.get("/health")
            return reply.status_code, reply.text

        return fetch, client

    def test_stt_refusal_run_completes_with_the_marker(self, settings, tmp_path):
        transcriber = Transcriber(settings, cached=lambda: False)  # model absent
        assert transcriber.maybe_start_warmup() is None  # refusal: no download
        fetch, client = self._fetch_from_app(settings, tmp_path, transcriber)
        assert client.get("/health").json()["stt"] == "unavailable"
        # Only the STT refusal step runs here; the gate is the real one with
        # the real /health handler behind a fake socket.
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "none"],
            home=tmp_path,
            gate=_gate(fetch=fetch),
            steps=[SttStep(runner=FakeHerdr())],
        )
        assert code == 0, err
        assert json.loads(_marker(tmp_path).read_text())["stt"] == "none"


class TestWizardSeam:
    def test_wizard_without_a_gate_still_reports_exit_10_no_marker(self, tmp_path):
        # The library seam is unchanged: only the CLI wires the real gate.
        wizard = Wizard(
            WizardOptions(role="plugin"),
            env=_env(tmp_path),
            stdin=StringIO(),
            stdout=StringIO(),
            stderr=StringIO(),
            isatty=lambda: False,
            steps=[],
        )
        assert wizard.run() == 10
        assert not _marker(tmp_path).exists()

    def test_cli_without_an_injected_gate_wires_the_real_one(self, tmp_path, monkeypatch):
        built = []

        def fake_make(**kwargs):
            built.append(kwargs)
            return lambda ctx: True

        monkeypatch.setattr(cli_mod, "make_health_gate", fake_make)
        out, err = StringIO(), StringIO()
        code = main(
            ["--role", "plugin"],
            env=_env(tmp_path),
            stdin=StringIO(),
            stdout=out,
            stderr=err,
            isatty=lambda: False,
            steps=[],
        )
        assert code == 0, err.getvalue()
        assert built == [{}]  # production construction: no test seams passed
        assert _marker(tmp_path).is_file()


class TestRealProcessGate:
    """Real CLI/default gate, with explicit herdr and HTTP transport doubles."""

    def _stub_herdr(self, tmp_path: Path, body: str) -> Path:
        script = tmp_path / "bin" / "herdr"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)
        return script

    @pytest.mark.parametrize("role", ["plugin", "brain"])
    def test_unreachable_brain_exits_30_without_a_marker(self, tmp_path, role):
        herdr = self._stub_herdr(
            tmp_path,
            'test "$*" = "plugin list" || exit 99\n'
            'printf "%s\\n" "$*" > "$HOME/plugin-list.calls"\n'
            "echo 'herdr.tts enabled'",
        )
        doubles = tmp_path / "doubles"
        doubles.mkdir()
        # Test-only transport injection before the real -m entry point imports.
        # urllib error handling, gate composition/retries and marker logic stay real.
        (doubles / "sitecustomize.py").write_text('''
import os, pathlib, socket, urllib.error, urllib.request
home = pathlib.Path(os.environ["HOME"])
def unavailable(self, request, *args, **kwargs):
    url = request.full_url if hasattr(request, "full_url") else request
    with (home / "transport.calls").open("a") as stream:
        stream.write(str(url) + "\\n")
    raise urllib.error.URLError("fixture transport unavailable")
def forbidden(*args, **kwargs):
    (home / "socket-attempted").touch()
    raise AssertionError("process gate test must never create a socket")
urllib.request.OpenerDirector.open = unavailable
socket.socket = forbidden
socket.create_connection = forbidden
''')
        secret = "fixture-process-gate-canary"
        read_fd, write_fd = os.pipe()
        os.write(write_fd, (secret + "\n").encode())
        os.close(write_fd)
        env = {
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / ".config"),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": f"{doubles}:{REPO_ROOT / 'tools'}",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HERDR_BIN": str(herdr),
            "HERDR_BRAIN_PORT": "18741",
            "HERDR_ONBOARDING_SECRET_FD": str(read_fd),
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "herdr_onboarding", "--role", role,
                 "--non-interactive", "--json"],
                env=env,
                pass_fds=(read_fd,),
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            os.close(read_fd)
        assert completed.returncode == 30, completed.stderr
        record = json.loads(completed.stdout)
        assert record["status"] == "health-gate-failed"
        assert record["marker_written"] is False
        assert "fixture transport unavailable" in completed.stderr
        assert secret not in completed.stdout + completed.stderr
        assert "Traceback" not in completed.stderr
        assert not _marker(tmp_path).exists()
        assert not (tmp_path / "socket-attempted").exists()
        assert not (doubles / "__pycache__").exists()
        assert (tmp_path / "transport.calls").read_text().splitlines() == [
            "http://127.0.0.1:18741/health"
        ] * health.HEALTH_ATTEMPTS
        assert (tmp_path / "plugin-list.calls").read_text().strip() == "plugin list"
        env_file = tmp_path / ".config/herdr-brain/env"
        if role == "brain":
            assert secret in env_file.read_text()
            assert env_file.stat().st_mode & 0o777 == 0o600
        else:
            assert not env_file.exists()
