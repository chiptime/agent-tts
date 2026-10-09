"""V1 for credential capture (AT-11 task 3.2, design slice 16).

Covers the three ranked secret-intake channels (getpass / FD / FILE),
argv exclusion, the atomic mode-600 merge writer for
``~/.config/herdr-brain/env``, the single ``redact()`` diagnostic
boundary, plugin-role keyless completion, and the secret-handling
threat matrix: (a) a ``ps`` snapshot during a non-interactive run
contains no secret, (b) every output byte of a failed run is
secret-free, (c) a simulated crash mid-write leaves no readable partial
env file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_onboarding import prompts  # noqa: E402
from herdr_onboarding import resolve as res  # noqa: E402
from herdr_onboarding import secrets as sec  # noqa: E402
from herdr_onboarding import wizard as wiz  # noqa: E402
from herdr_onboarding.cli import main  # noqa: E402
from herdr_onboarding.steps import steps_for_role  # noqa: E402
from herdr_onboarding.steps.credentials import CredentialsStep  # noqa: E402


def _env(home: Path, **extra) -> dict:
    env = {"HOME": str(home)}
    env.update(extra)
    return env


def _brain_run(argv, *, env, gate=lambda ctx: True, isatty=True, stdin=None):
    out, err = StringIO(), StringIO()
    code = main(
        argv,
        env=env,
        stdin=stdin if stdin is not None else StringIO(),
        stdout=out,
        stderr=err,
        isatty=lambda: isatty,
        health_gate=gate,
    )
    return code, out.getvalue(), err.getvalue()


def _env_file(home: Path) -> Path:
    return home / ".config" / "herdr-brain" / "env"


def _marker(home: Path) -> Path:
    return home / ".config" / "herdr-tts" / "first-run.done"


def _pipe_with(value: str):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, value.encode())
    os.close(write_fd)
    return read_fd


class _GetpassRecorder:
    def __init__(self, value="from-getpass"):
        self.value = value
        self.calls = []

    def __call__(self, prompt):
        self.calls.append(prompt)
        return self.value


class TestChannelFD:
    def test_reads_once_and_closes(self):
        fd = _pipe_with("sk-fd-canary-1234\n")
        got = prompts.read_secret(
            "GLM API key", env={"HERDR_ONBOARDING_SECRET_FD": str(fd)}, interactive=False
        )
        assert got == "sk-fd-canary-1234"
        with pytest.raises(OSError):
            os.fstat(fd)  # closed after the single read

    def test_non_numeric_value_rejected_actionably(self):
        with pytest.raises(prompts.SecretIntakeError) as exc:
            prompts.read_secret(
                "GLM API key",
                env={"HERDR_ONBOARDING_SECRET_FD": "not-a-number"},
                interactive=False,
            )
        assert "HERDR_ONBOARDING_SECRET_FD" in str(exc.value)
        assert "integer" in str(exc.value)

    def test_unreadable_fd_rejected_actionably(self):
        closed = os.pipe()
        os.close(closed[0])
        os.close(closed[1])
        with pytest.raises(prompts.SecretIntakeError) as exc:
            prompts.read_secret(
                "GLM API key",
                env={"HERDR_ONBOARDING_SECRET_FD": str(closed[0])},
                interactive=False,
            )
        assert "HERDR_ONBOARDING_SECRET_FD" in str(exc.value)

    def test_empty_content_is_returned_empty(self):
        fd = _pipe_with("\n")
        got = prompts.read_secret(
            "GLM API key", env={"HERDR_ONBOARDING_SECRET_FD": str(fd)}, interactive=False
        )
        assert got == ""


class TestChannelFile:
    def test_mode_600_file_read_once(self, tmp_path):
        secret_file = tmp_path / "secret.env"
        secret_file.write_text("sk-file-canary-5678\n")
        secret_file.chmod(0o600)
        got = prompts.read_secret(
            "GLM API key",
            env={"HERDR_ONBOARDING_SECRET_FILE": str(secret_file)},
            interactive=False,
        )
        assert got == "sk-file-canary-5678"
        assert secret_file.is_file()  # kept by default

    def test_optional_unlink(self, tmp_path):
        secret_file = tmp_path / "secret.env"
        secret_file.write_text("sk-unlink-canary-9\n")
        secret_file.chmod(0o600)
        got = prompts.read_secret(
            "GLM API key",
            env={
                "HERDR_ONBOARDING_SECRET_FILE": str(secret_file),
                "HERDR_ONBOARDING_SECRET_UNLINK": "1",
            },
            interactive=False,
        )
        assert got == "sk-unlink-canary-9"
        assert not secret_file.exists()

    def test_loose_mode_rejected_with_chmod_remediation(self, tmp_path):
        secret_file = tmp_path / "secret.env"
        secret_file.write_text("sk-loose-canary-0\n")
        secret_file.chmod(0o644)
        with pytest.raises(prompts.SecretIntakeError) as exc:
            prompts.read_secret(
                "GLM API key",
                env={"HERDR_ONBOARDING_SECRET_FILE": str(secret_file)},
                interactive=False,
            )
        message = str(exc.value)
        assert "chmod 600" in message
        assert "600" in message

    def test_missing_file_rejected_actionably(self, tmp_path):
        with pytest.raises(prompts.SecretIntakeError) as exc:
            prompts.read_secret(
                "GLM API key",
                env={"HERDR_ONBOARDING_SECRET_FILE": str(tmp_path / "absent.env")},
                interactive=False,
            )
        assert "HERDR_ONBOARDING_SECRET_FILE" in str(exc.value)

    def test_non_regular_file_rejected(self, tmp_path):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        fifo.chmod(0o600)
        with pytest.raises(prompts.SecretIntakeError) as exc:
            prompts.read_secret(
                "GLM API key",
                env={"HERDR_ONBOARDING_SECRET_FILE": str(fifo)},
                interactive=False,
            )
        assert "regular file" in str(exc.value)


class TestChannelRanking:
    def test_fd_wins_over_file(self, tmp_path):
        secret_file = tmp_path / "secret.env"
        secret_file.write_text("sk-file-loses\n")
        secret_file.chmod(0o600)
        fd = _pipe_with("sk-fd-wins\n")
        got = prompts.read_secret(
            "GLM API key",
            env={
                "HERDR_ONBOARDING_SECRET_FD": str(fd),
                "HERDR_ONBOARDING_SECRET_FILE": str(secret_file),
            },
            interactive=False,
        )
        assert got == "sk-fd-wins"

    def test_file_used_when_no_fd(self, tmp_path):
        secret_file = tmp_path / "secret.env"
        secret_file.write_text("sk-file-fallback\n")
        secret_file.chmod(0o600)
        got = prompts.read_secret(
            "GLM API key",
            env={"HERDR_ONBOARDING_SECRET_FILE": str(secret_file)},
            interactive=False,
        )
        assert got == "sk-file-fallback"

    def test_getpass_used_when_interactive_and_no_unattended_channel(self):
        recorder = _GetpassRecorder()
        got = prompts.read_secret(
            "GLM API key", env={}, interactive=True, getpass_fn=recorder
        )
        assert got == "from-getpass"
        assert len(recorder.calls) == 1

    def test_getpass_never_called_when_non_interactive(self):
        recorder = _GetpassRecorder()
        got = prompts.read_secret(
            "GLM API key", env={}, interactive=False, getpass_fn=recorder
        )
        assert got is None
        assert recorder.calls == []

    def test_fd_suppresses_getpass_even_when_interactive(self):
        recorder = _GetpassRecorder()
        fd = _pipe_with("sk-fd-interactive\n")
        got = prompts.read_secret(
            "GLM API key",
            env={"HERDR_ONBOARDING_SECRET_FD": str(fd)},
            interactive=True,
            getpass_fn=recorder,
        )
        assert got == "sk-fd-interactive"
        assert recorder.calls == []


class TestCredentialsStepBrain:
    def test_noninteractive_fd_channel_writes_env_file(self, tmp_path):
        fd = _pipe_with("sk-brain-write-1\n")
        code, out, err = _brain_run(
            ["--role", "brain", "--non-interactive"],
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
        )
        assert code == 0, err
        env_file = _env_file(tmp_path)
        assert env_file.is_file()
        assert env_file.stat().st_mode & 0o777 == 0o600
        assert env_file.parent.stat().st_mode & 0o777 == 0o700
        assert res._read_env_key(env_file, "GLM_API_KEY") == "sk-brain-write-1"

    def test_interactive_getpass_channel(self, tmp_path):
        code, _, err = _brain_run(
            ["--role", "brain"],
            env=_env(tmp_path),
            isatty=True,
        )
        # no channel and interactive would prompt on a real tty; here stdin
        # is empty so the run aborts cleanly (no partial state)
        assert code == 40
        assert not _env_file(tmp_path).exists()

    def test_noninteractive_missing_answer_exit_20_with_hint(self, tmp_path):
        code, out, err = _brain_run(
            ["--role", "brain", "--non-interactive"], env=_env(tmp_path)
        )
        assert code == 20
        assert not _env_file(tmp_path).exists()
        assert not _marker(tmp_path).exists()
        assert "HERDR_ONBOARDING_SECRET_FD" in err
        assert "HERDR_ONBOARDING_SECRET_FILE" in err
        assert "--no-first-run" in err

    def test_empty_secret_is_missing_answer(self, tmp_path):
        fd = _pipe_with("\n")
        code, _, _ = _brain_run(
            ["--role", "brain", "--non-interactive"],
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
        )
        assert code == 20
        assert not _env_file(tmp_path).exists()

    def test_existing_key_no_demand_file_unchanged(self, tmp_path):
        env_file = _env_file(tmp_path)
        original = (
            "# seeded\nGLM_API_KEY=sk-existing-keep\n\nUNKNOWN=stay put\n"
        )
        env_file.parent.mkdir(parents=True)
        env_file.write_text(original)
        env_file.chmod(0o600)
        code, _, err = _brain_run(
            ["--role", "brain", "--non-interactive"], env=_env(tmp_path)
        )
        assert code == 0, err
        assert env_file.read_text() == original  # byte-identical, no re-ask

    def test_rotation_preserves_unknown_lines_byte_identical(self, tmp_path):
        env_file = _env_file(tmp_path)
        original = (
            "# managed comment\n"
            "GLM_API_KEY=sk-old-rotation\n"
            "\n"
            "OTHER_KEY=untouched value\n"
            "# trailing comment\n"
        )
        env_file.parent.mkdir(parents=True)
        env_file.write_text(original)
        env_file.chmod(0o600)
        fd = _pipe_with("sk-new-rotation\n")
        code, _, err = _brain_run(
            ["--role", "brain", "--non-interactive"],
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
        )
        assert code == 0, err
        expected = original.replace(
            "GLM_API_KEY=sk-old-rotation", "GLM_API_KEY=sk-new-rotation"
        )
        assert env_file.read_text() == expected
        assert env_file.stat().st_mode & 0o777 == 0o600

    def test_ambient_process_env_key_is_not_a_channel(self, tmp_path):
        # GLM_API_KEY exported in the process environment must NOT satisfy
        # the wizard: the three ranked channels are the only intake.
        code, _, _ = _brain_run(
            ["--role", "brain", "--non-interactive"],
            env=_env(tmp_path, GLM_API_KEY="sk-ambient-not-a-channel"),
        )
        assert code == 20
        assert not _env_file(tmp_path).exists()


class TestPluginRoleKeyless:
    def test_registry_has_no_credentials_step_for_plugin(self):
        assert not any(
            isinstance(step, CredentialsStep) for step in steps_for_role("plugin")
        )

    def test_registry_has_credentials_step_for_brain(self):
        assert any(
            isinstance(step, CredentialsStep) for step in steps_for_role("brain")
        )

    def test_plugin_run_completes_keyless(self, tmp_path):
        recorder = _GetpassRecorder()
        code, out, err = _brain_run(
            ["--role", "plugin", "--non-interactive"], env=_env(tmp_path)
        )
        assert code == 0, err
        assert _marker(tmp_path).is_file()
        assert not _env_file(tmp_path).exists()
        assert recorder.calls == []


class TestMergeWriter:
    def test_creates_file_and_directory_with_modes(self, tmp_path):
        path = tmp_path / "herdr-brain" / "env"
        sec.merge_env_file(path, {"GLM_API_KEY": "sk-create-1"})
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    def test_enforces_directory_700_on_preexisting_loose_dir(self, tmp_path):
        directory = tmp_path / "herdr-brain"
        directory.mkdir()
        directory.chmod(0o755)
        sec.merge_env_file(directory / "env", {"GLM_API_KEY": "sk-tighten"})
        assert directory.stat().st_mode & 0o777 == 0o700

    def test_appends_missing_key_preserving_content(self, tmp_path):
        path = tmp_path / "env"
        path.write_text("# comment\nOTHER=1\n")
        sec.merge_env_file(path, {"GLM_API_KEY": "sk-append"})
        assert path.read_text() == "# comment\nOTHER=1\nGLM_API_KEY=sk-append\n"

    def test_unterminated_last_line_stays_unterminated(self, tmp_path):
        path = tmp_path / "env"
        path.write_text("OTHER=1")  # no trailing newline
        sec.merge_env_file(path, {"GLM_API_KEY": "sk-append-edge"})
        assert path.read_text() == "OTHER=1\nGLM_API_KEY=sk-append-edge\n"

    def test_replaces_only_the_target_key(self, tmp_path):
        path = tmp_path / "env"
        path.write_text("A=1\nGLM_API_KEY=old\nB=2\n")
        sec.merge_env_file(path, {"GLM_API_KEY": "new", "B": "two"})
        assert path.read_text() == "A=1\nGLM_API_KEY=new\nB=two\n"

    def test_multiline_value_refused(self, tmp_path):
        with pytest.raises(ValueError) as exc:
            sec.merge_env_file(
                tmp_path / "env", {"GLM_API_KEY": "evil\ninjection"}
            )
        assert "GLM_API_KEY" in str(exc.value)
        assert "injection" not in str(exc.value)

    def test_crash_mid_write_leaves_target_intact_no_partial(
        self, tmp_path
    ):
        path = tmp_path / "env"
        original = "# original\nGLM_API_KEY=old-crash\n"
        path.write_text(original)

        def crashing_replace(src, dst):
            raise RuntimeError("simulated crash mid-write")

        with pytest.raises(RuntimeError):
            sec.merge_env_file(
                path,
                {"GLM_API_KEY": "new-crash"},
                _replace=crashing_replace,
            )
        assert path.read_text() == original
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestRedaction:
    def test_redact_replaces_registered_secret(self):
        assert (
            sec.redact("wrote sk-zzz-secret-1 okay", ["sk-zzz-secret-1"])
            == "wrote *** okay"
        )

    def test_redact_multiple_and_longest_first(self):
        text = "a sk-long-secret and sk-short overlap"
        got = sec.redact(text, ["sk-short", "sk-long-secret"])
        assert "sk-long-secret" not in got
        assert "sk-short" not in got

    def test_redact_ignores_short_values(self):
        assert sec.redact("abc def", ["abc"]) == "abc def"

    def test_redact_no_secrets_is_identity(self):
        assert sec.redact("plain text", []) == "plain text"

    def test_wizard_diagnostics_are_scrubbed(self, tmp_path):
        canary = "sk-diag-canary-42"
        fd = _pipe_with(canary + "\n")
        code, out, err = _brain_run(
            ["--role", "brain", "--non-interactive", "--json"],
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
            gate=lambda ctx: False,
        )
        assert code == 30
        assert canary not in out
        assert canary not in err
        assert not _marker(tmp_path).exists()

    def test_exception_message_secret_is_scrubbed(self, tmp_path):
        canary = "sk-exc-canary-77"

        class Leaky:
            name = "leaky"
            roles = ("plugin", "brain")

            def run(self, ctx):
                ctx.register_secret(canary)
                raise RuntimeError(f"boom while handling {canary}")

        out, err = StringIO(), StringIO()
        code = main(
            ["--role", "plugin"],
            env=_env(tmp_path),
            stdin=StringIO(),
            stdout=out,
            stderr=err,
            isatty=lambda: True,
            health_gate=lambda ctx: True,
            steps=[Leaky()],
        )
        assert code == 40
        assert canary not in err.getvalue()
        assert "***" in err.getvalue()


class TestThreatMatrixPs:
    @pytest.mark.parametrize("warning", [False, True], ids=["healthy-gate", "manifest-warning"])
    def test_ps_snapshot_during_noninteractive_run_has_no_secret(
        self, tmp_path, warning
    ):
        canary = "sk-ps-canary-Z1x9Q7vN"
        herdr = tmp_path / "herdr-double"
        herdr.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" > "$HOME/plugin-list.calls"\n'
            'test "$*" = "plugin list" || exit 99\n'
            + ("echo 'warning: manifest unavailable: fixture'\n" if warning else "echo 'herdr.tts enabled'\n")
        )
        herdr.chmod(0o755)
        read_fd, write_fd = os.pipe()
        try:
            env = {
                "HOME": str(tmp_path),
                "PATH": "/usr/bin:/bin",
                "XDG_CONFIG_HOME": str(tmp_path / ".config"),
                "HERDR_BIN": str(herdr),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(REPO_ROOT / "tools"),
                "HERDR_ONBOARDING_SECRET_FD": str(read_fd),
            }
            # The child blocks inside the FD read (pipe empty, writer held
            # by this test), giving a deterministic live window.  argv and
            # /proc/<pid>/environ are immutable after exec, so a snapshot
            # taken now proves no secret rides either surface.
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    # Existing test seam: real HealthGate composition and
                    # real herdr subprocess, but external HTTP is a double.
                    "import runpy, sys, socket; "
                    "from herdr_onboarding import cli, health; "
                    "socket.create_connection = lambda *a, **k: sys.exit(99); "
                    "cli.make_health_gate = lambda: health.HealthGate("
                    "fetch=lambda url, timeout: (200, '{\"tts\":\"ok\",\"stt\":\"unavailable\"}')); "
                    "sys.argv = ['herdr_onboarding'] + sys.argv[1:]; "
                    "runpy.run_module('herdr_onboarding', run_name='__main__')",
                    "--role",
                    "brain",
                    "--non-interactive",
                    "--json",
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                pass_fds=(read_fd,),
            )
            try:
                for _ in range(100):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                assert proc.poll() is None, "child exited before the live window"

                cmdline = Path(f"/proc/{proc.pid}/cmdline").read_bytes().decode(
                    errors="replace"
                )
                assert "herdr_onboarding" in cmdline
                assert canary not in cmdline

                environ = Path(f"/proc/{proc.pid}/environ").read_bytes().decode(
                    errors="replace"
                )
                assert canary not in environ

                if shutil.which("ps") is not None:
                    snapshot = subprocess.run(
                        ["ps", "-eo", "args"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    assert canary not in snapshot.stdout

                os.write(write_fd, (canary + "\n").encode())
                os.close(write_fd)
                stdout, stderr = proc.communicate(timeout=60)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
        finally:
            os.close(read_fd)
            try:
                os.close(write_fd)
            except OSError:
                pass
        assert proc.returncode == (30 if warning else 0), stderr
        record = json.loads(stdout)
        assert record["status"] == ("health-gate-failed" if warning else "completed")
        assert record["marker_written"] is (not warning)
        assert canary not in stdout
        assert canary not in stderr
        assert _marker(tmp_path).exists() is (not warning)
        assert (tmp_path / "plugin-list.calls").read_text().strip() == "plugin list"
        env_file = _env_file(tmp_path)
        assert res._read_env_key(env_file, "GLM_API_KEY") == canary
        assert env_file.stat().st_mode & 0o777 == 0o600


class TestThreatMatrixFailedRun:
    def test_subprocess_failed_run_output_secret_free(self, tmp_path):
        canary = "sk-fail-canary-Qq3w8"
        read_fd, write_fd = os.pipe()
        os.write(write_fd, (canary + "\n").encode())
        os.close(write_fd)
        # Block the destination: herdr-brain exists as a FILE, so the
        # merge writer fails AFTER the secret was captured.
        config = tmp_path / ".config"
        config.mkdir(parents=True)
        (config / "herdr-brain").write_text("not a directory")
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "herdr_onboarding",
                    "--role",
                    "brain",
                    "--non-interactive",
                ],
                env={
                    "HOME": str(tmp_path),
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": str(REPO_ROOT / "tools"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "HERDR_ONBOARDING_SECRET_FD": str(read_fd),
                },
                capture_output=True,
                text=True,
                pass_fds=(read_fd,),
                timeout=60,
            )
        finally:
            os.close(read_fd)
        assert completed.returncode == 40
        assert canary not in completed.stdout
        assert canary not in completed.stderr
        assert not _marker(tmp_path).exists()

    def test_inprocess_failed_run_json_secret_free(self, tmp_path):
        canary = "sk-json-fail-99"
        fd = _pipe_with(canary + "\n")
        code, out, err = _brain_run(
            ["--role", "brain", "--non-interactive", "--json"],
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
            gate=lambda ctx: False,
        )
        assert code == 30
        assert canary not in out + err


class TestInteractiveGetpassChannel:
    """Triangulation: the real ``getpass`` seam, not just FD/FILE."""

    def test_getpass_value_is_persisted_and_never_echoed(
        self, tmp_path, monkeypatch
    ):
        import getpass

        canary = "sk-getpass-canary-31"
        prompts_seen = []

        def fake_getpass(prompt="Password: ", stream=None):
            prompts_seen.append(prompt)
            return canary

        monkeypatch.setattr(getpass, "getpass", fake_getpass)
        code, out, err = _brain_run(
            ["--role", "brain", "--json"], env=_env(tmp_path), isatty=True
        )
        assert code == 0, err
        assert len(prompts_seen) == 1
        assert canary not in prompts_seen[0]
        assert res._read_env_key(_env_file(tmp_path), "GLM_API_KEY") == canary
        assert canary not in out + err
        assert _marker(tmp_path).is_file()

    def test_empty_getpass_answer_aborts_without_partial_state(
        self, tmp_path, monkeypatch
    ):
        import getpass

        monkeypatch.setattr(getpass, "getpass", lambda *a, **k: "")
        code, _, _ = _brain_run(
            ["--role", "brain"], env=_env(tmp_path), isatty=True
        )
        assert code == 40
        assert not _env_file(tmp_path).exists()
        assert not _marker(tmp_path).exists()

    def test_interactive_rerun_with_existing_key_never_prompts(
        self, tmp_path, monkeypatch
    ):
        import getpass

        env_file = _env_file(tmp_path)
        original = "GLM_API_KEY=sk-existing-interactive\n# note\n"
        env_file.parent.mkdir(parents=True)
        env_file.write_text(original)
        env_file.chmod(0o600)

        def forbidden(*args, **kwargs):  # pragma: no cover — must not run
            raise AssertionError("getpass must not run when a key exists")

        monkeypatch.setattr(getpass, "getpass", forbidden)
        code, _, err = _brain_run(
            ["--role", "brain"], env=_env(tmp_path), isatty=True
        )
        assert code == 0, err
        assert env_file.read_text() == original

    def test_gate_exception_carrying_the_secret_is_redacted(self, tmp_path):
        canary = "sk-gate-canary-58"
        fd = _pipe_with(canary + "\n")

        def leaky_gate(ctx):
            raise RuntimeError(f"health probe echoed {canary}")

        code, out, err = _brain_run(
            ["--role", "brain", "--non-interactive"],
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
            gate=leaky_gate,
        )
        assert code == 30
        assert canary not in out + err
        assert "***" in err
        assert not _marker(tmp_path).exists()

    def test_plugin_role_never_consumes_the_secret_channels(self, tmp_path):
        fd = _pipe_with("sk-plugin-must-not-read\n")
        try:
            code, _, err = _brain_run(
                ["--role", "plugin", "--non-interactive"],
                env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(fd)),
            )
            assert code == 0, err
            os.fstat(fd)  # still open: the keyless role did not read it
        finally:
            os.close(fd)
        assert not _env_file(tmp_path).exists()


class TestStaticSecretHygiene:
    SHELL_ROOTS = (
        "hosts/herdr/brain/bin",
        "hosts/herdr/brain/deploy",
        "hosts/herdr/brain/scripts",
        "hosts/herdr/tts-plugin/bin",
        "hosts/herdr/tts-plugin/scripts",
        "scripts/acceptance",
    )

    def test_no_shell_script_enables_xtrace(self):
        import re

        pattern = re.compile(r"^\s*(?:set\s+-[A-Za-z]*x|.*\bbash\s+-[A-Za-z]*x\b)")
        scanned = 0
        for root in self.SHELL_ROOTS:
            base = REPO_ROOT / root
            if not base.is_dir():
                continue
            for script in base.rglob("*"):
                if not script.is_file() or script.suffix not in ("", ".sh"):
                    continue
                if script.suffix == "" and "bin" not in script.parts:
                    continue
                scanned += 1
                text = script.read_text(encoding="utf-8", errors="replace")
                for number, line in enumerate(text.splitlines(), start=1):
                    if line.lstrip().startswith("#") and number != 1:
                        continue  # prose comments may warn against set -x
                    assert not pattern.match(line), f"{script}:{number}"
        assert scanned >= 5, "the static scan must cover the shell entry points"

    def test_no_argv_key_flag_no_set_x(self):
        package = REPO_ROOT / "tools" / "herdr_onboarding"
        sources = list(package.rglob("*.py"))
        assert sources, "package sources must exist"
        for source in sources:
            text = source.read_text(encoding="utf-8")
            assert "glm-key" not in text, source
            assert "glm_key" not in text, source
            assert "set -x" not in text, source
