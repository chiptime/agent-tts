"""AT-11 doctor: isolated probes, executable repairs, read-only dispatch."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
from herdr_onboarding import cli, doctor


@pytest.fixture
def rig(tmp_path):
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_DATA_HOME": str(tmp_path / "data"), "XDG_STATE_HOME": str(tmp_path / "state"),
           "XDG_CACHE_HOME": str(tmp_path / "cache"), "PATH": str(tmp_path / "home/.local/bin"),
           "HERDR_TTS_DAEMON_PID_FILE": str(tmp_path / "tts.pid")}
    bindir = Path(env["PATH"])
    bindir.mkdir(parents=True)
    for name in ("herdr-tts", "pactl"):
        executable = bindir / name
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
    secret = Path(env["XDG_CONFIG_HOME"]) / "herdr-brain/env"
    secret.parent.mkdir(parents=True)
    secret.write_text("GLM_API_KEY=synthetic-private-value\nUNKNOWN=kept\n")
    secret.chmod(0o600)
    Path(env["HERDR_TTS_DAEMON_PID_FILE"]).write_text(str(os.getpid()))
    brain_pid = Path(env["XDG_STATE_HOME"]) / "herdr-brain/daemon.pid"
    brain_pid.parent.mkdir(parents=True)
    brain_pid.write_text(str(os.getpid()))
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        output = "1\n" if "--contract-version" in argv else "available\n"
        if "-c" in argv:
            output = "true\n"
        return subprocess.CompletedProcess(argv, 0, output, "")
    def fetch(*args):
        calls.append(("HTTP", args))
        return 200, '{"tts":"ok","stt":"unavailable"}'
    return env, runner, fetch, calls, secret


def run(rig, role="brain", **kwargs):
    env, runner, fetch, _, _ = rig
    return doctor.Doctor(role, env, runner=runner, fetch=fetch, platform="Linux", **kwargs).checks()


def test_table_covers_six_checks_and_every_repair(rig):
    results = run(rig)
    assert [r.name for r in results] == ["path", "audio", "credentials", "daemon", "contract", "stt"]
    assert all(r.result.ok for r in results), results
    for row in results:
        assert row.remediation.strip()
        assert "<" not in row.remediation  # no non-executable placeholder repairs
    assert "synthetic-private-value" not in repr(results)


@pytest.mark.parametrize("broken", ["path", "audio", "credentials", "daemon", "contract", "stt"])
def test_each_break_names_its_exact_repair(rig, broken):
    env, runner, fetch, calls, secret = rig
    if broken == "path": env["PATH"] = str(Path(env["HOME"]) / "other")
    if broken == "audio": (Path(env["PATH"]) / "pactl").unlink()
    if broken == "credentials": secret.chmod(0o644)
    if broken == "daemon": Path(env["HERDR_TTS_DAEMON_PID_FILE"]).write_text("not-a-pid")
    def faulty(argv, **kwargs):
        if broken == "contract" and "--contract-version" in argv:
            return subprocess.CompletedProcess(argv, 0, "0\n", "secret-decoy")
        if broken == "stt" and "-c" in argv:
            return subprocess.CompletedProcess(argv, 0, "false\n", "secret-decoy")
        return runner(argv, **kwargs)
    rows = doctor.Doctor("brain", env, runner=faulty, fetch=fetch, platform="Linux").checks()
    row = next(r for r in rows if r.name == broken)
    assert not row.result.ok
    out = io.StringIO()
    assert doctor.report(rows, out, json_output=False) == 1
    assert f"FAIL {broken}:" in out.getvalue()
    assert row.remediation in out.getvalue()
    assert "secret-decoy" not in out.getvalue()


@pytest.mark.parametrize("shape", ["missing", "empty", "symlink", "bad-mode"])
def test_credentials_fail_closed_without_value_or_mutation(rig, shape):
    env, _, _, _, secret = rig
    if shape == "missing": secret.unlink()
    elif shape == "empty": secret.write_text("GLM_API_KEY=\n")
    elif shape == "symlink":
        target = secret.with_name("target")
        secret.rename(target)
        secret.symlink_to(target)
    else: secret.chmod(0o640)
    before = snapshot(Path(env["HOME"]).parent)
    row = next(r for r in run(rig) if r.name == "credentials")
    assert not row.result.ok
    assert snapshot(Path(env["HOME"]).parent) == before
    assert "synthetic-private-value" not in repr(row)


def test_plugin_never_requires_brain_key_or_http(rig):
    rig[-1].unlink()
    rows = run(rig, "plugin")
    assert all(r.result.ok for r in rows)
    assert "not required" in rows[2].result.detail
    assert not any(argv == "HTTP" for argv, _ in rig[3])


@pytest.mark.parametrize("pid", ["0", "-1", "999999999", "garbage"])
def test_dead_pid_blocks_http_without_signalling(rig, pid):
    Path(rig[0]["HERDR_TTS_DAEMON_PID_FILE"]).write_text(pid)
    assert not run(rig)[3].result.ok
    assert not any(argv == "HTTP" for argv, _ in rig[3])


@pytest.mark.parametrize("body", ['{}', 'not-json', '{"tts":"degraded"}'])
def test_live_pid_not_enough_health_must_pass(rig, body):
    env, runner, _, _, _ = rig
    rows = doctor.Doctor("brain", env, runner=runner, fetch=lambda *a: (200, body), platform="Linux").checks()
    assert not rows[3].result.ok


@pytest.mark.parametrize("platform,tool", [("Darwin", "afplay"), ("Linux", "powershell.exe")])
def test_platform_audio_repairs(rig, platform, tool):
    env = rig[0]
    (Path(env["PATH"]) / "pactl").unlink()
    if tool == "powershell.exe": env["WSL_DISTRO_NAME"] = "fixture"
    d = doctor.Doctor("plugin", env, runner=rig[1], platform=platform)
    row = d.checks()[1]
    assert not row.result.ok and tool in row.remediation
    executable = Path(env["PATH"]) / tool
    executable.write_text("#!/bin/sh\nexit 0\n"); executable.chmod(0o755)
    assert d.checks()[1].result.ok


def test_cache_uses_runtime_offline_probe_and_persisted_model(rig):
    rig[-1].write_text('GLM_API_KEY=synthetic-private-value\nHERDR_BRAIN_STT_MODEL=base\n')
    assert run(rig)[5].result.ok
    argv, kwargs = next((a, k) for a, k in rig[3] if "-c" in a)
    assert "model_is_cached" in argv[2] and "herdr_brain.stt" in argv[2]
    assert argv[-1] == "base"
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
    assert "pull" not in argv


@pytest.mark.parametrize("output,status", [("garbage", 0), ("1\nwarning", 0), ("1", 1), ("", 0), ("2", 0)])
def test_contract_requires_success_and_one_integer(rig, output, status):
    def runner(argv, **kwargs):
        if "--contract-version" in argv:
            return subprocess.CompletedProcess(argv, status, output, "")
        return rig[1](argv, **kwargs)
    row = doctor.Doctor("plugin", rig[0], runner=runner, platform="Linux").contract()
    assert row.result.ok == (output == "2" and status == 0)


@pytest.mark.parametrize("exception", [OSError("synthetic-private-value"), subprocess.TimeoutExpired("synthetic-private-value", 5)])
def test_probe_failures_never_reflect_subprocess_errors(rig, exception):
    def runner(*args, **kwargs): raise exception
    rows = doctor.Doctor("brain", rig[0], runner=runner, fetch=rig[2], platform="Linux").checks()
    assert all(not row.result.ok for row in rows if row.name in ("audio", "contract", "stt"))
    assert "synthetic-private-value" not in repr(rows)


def test_failed_audio_probe_is_not_an_available_device(rig):
    d = doctor.Doctor("plugin", rig[0], platform="Linux", runner=lambda *a, **k: subprocess.CompletedProcess(a, 1, "device failed", ""))
    assert not d.audio().result.ok


def test_missing_brain_pid_blocks_health(rig):
    (Path(rig[0]["XDG_STATE_HOME"]) / "herdr-brain/daemon.pid").unlink()
    assert not run(rig)[3].result.ok
    assert not any(argv == "HTTP" for argv, _ in rig[3])


def test_repair_uses_installed_interpreter_and_both_model_names(rig):
    row = run(rig)[5]
    assert sys.executable in row.remediation
    assert "AGENT_TTS_STT_MODEL=small" in row.remediation
    assert "HERDR_BRAIN_STT_MODEL=small" in row.remediation
    assert "HERDR_BRAIN_STT_PYTHON=" in row.remediation


@pytest.mark.parametrize("tokens", [["--doctor", "--voice-provider", "edge"], ["doctor", "--no-first-run"],
                                    ["--doctor", "--fix-credentials"], ["doctor", "--glm-key", "synthetic-private-value"]])
def test_bad_doctor_flags_fail_without_echo_or_writes(rig, tokens):
    before = snapshot(Path(rig[0]["HOME"]).parent)
    err = io.StringIO()
    assert cli.main(["--role", "brain", *tokens], env=rig[0], stderr=err) == 2
    assert "synthetic-private-value" not in err.getvalue()
    assert snapshot(Path(rig[0]["HOME"]).parent) == before


def test_checks_flag_cannot_be_combined_with_mutating_subcommand(rig):
    before = snapshot(Path(rig[0]["HOME"]).parent)
    assert cli.main(["--role", "brain", "--doctor", "doctor", "--fix-credentials", "--non-interactive"],
                    env=rig[0], stderr=io.StringIO()) == 2
    assert snapshot(Path(rig[0]["HOME"]).parent) == before


def snapshot(root):
    return {str(p.relative_to(root)): (p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
            for p in root.rglob("*")}


def test_checks_only_cli_ignores_marker_and_never_runs_steps(rig, monkeypatch):
    env = rig[0]
    marker = Path(env["XDG_CONFIG_HOME"]) / "herdr-tts/first-run.done"
    marker.parent.mkdir(parents=True); marker.write_text('{"version":1}')
    rows = run(rig)
    monkeypatch.setattr(doctor.Doctor, "checks", lambda self: rows)
    before = snapshot(Path(env["HOME"]).parent)
    out = io.StringIO()
    assert cli.main(["--role", "brain", "--doctor", "--json"], env=env, stdout=out) == 0
    assert len(json.loads(out.getvalue())["checks"]) == 6
    assert snapshot(Path(env["HOME"]).parent) == before
    assert cli.main(["--role", "brain", "--doctor", "--fix-credentials"], env=env, stderr=io.StringIO()) == 2


def test_deliberate_fix_only_captures_credentials_even_with_marker(rig, monkeypatch):
    env = rig[0]
    marker = Path(env["XDG_CONFIG_HOME"]) / "herdr-tts/first-run.done"
    marker.parent.mkdir(parents=True); marker.write_text('{"version":1}')
    before = marker.read_bytes()
    rd, wr = os.pipe(); os.write(wr, b"rotated-synthetic-key\n"); os.close(wr)
    rows = run(rig)
    monkeypatch.setattr(doctor.Doctor, "checks", lambda self: rows)
    out = io.StringIO()
    assert cli.main(["--role", "brain", "doctor", "--fix-credentials", "--non-interactive"],
                    env=dict(env, HERDR_ONBOARDING_SECRET_FD=str(rd)), stdout=out) == 0
    assert "rotated-synthetic-key" in rig[-1].read_text()
    assert "UNKNOWN=kept" in rig[-1].read_text()
    assert marker.read_bytes() == before
    assert "rotated-synthetic-key" not in out.getvalue()
    assert cli.main(["--role", "plugin", "doctor", "--fix-credentials"], env=env, stderr=io.StringIO()) == 2


@pytest.mark.parametrize("role", ["brain", "plugin"])
@pytest.mark.parametrize("command", [["doctor"], ["--doctor"], ["first-run", "--doctor"]])
def test_real_dispatch_before_bootstrap_or_config_writes(tmp_path, role, command):
    # Copied launcher + shared package, installed interpreter: no real app services.
    host = "brain" if role == "brain" else "tts-plugin"
    checkout = tmp_path / "checkout"
    launcher = checkout / f"hosts/herdr/{host}/bin"
    launcher.mkdir(parents=True)
    shutil.copy2(ROOT / f"hosts/herdr/{host}/bin/herdr-{'brain' if role == 'brain' else 'tts'}", launcher)
    shutil.copytree(ROOT / "tools/herdr_onboarding", checkout / "tools/herdr_onboarding", ignore=shutil.ignore_patterns("__pycache__"))
    home = tmp_path / "home"; home.mkdir()
    config = tmp_path / "config"
    data = tmp_path / "data"
    python = checkout / "hosts/herdr/brain/.venv/bin/python" if role == "brain" else data / "herdr-tts/venv/bin/python"
    python.parent.mkdir(parents=True); python.symlink_to(sys.executable)
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(config), "XDG_DATA_HOME": str(data),
           "XDG_STATE_HOME": str(tmp_path / "state"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "HERDR_TTS_DAEMON_PID_FILE": str(tmp_path / "absent.pid"),
           "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    before = snapshot(tmp_path)
    result = subprocess.run(["bash", str(next(launcher.iterdir())), *command, "--json"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stderr
    assert len(json.loads(result.stdout)["checks"]) == 6
    assert snapshot(tmp_path) == before

