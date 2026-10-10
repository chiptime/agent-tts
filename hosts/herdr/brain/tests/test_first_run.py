"""V1 for the first-run wizard skeleton (AT-11 task 3.1, design slice 15).

Covers the CLI surface, the exit contract (0/10/20/30/40), the completion
marker lifecycle behind the injectable health-gate seam (the CLI wires the
real gate since task 3.5; see test_onboarding_health.py), TTY detection with
the noninteractive hint, and the
reachability matrix for the three supported installed layouts plus the
honest Homebrew-unavailable case.
"""

from __future__ import annotations

import json
import os
import pty
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_onboarding import resolve as res  # noqa: E402
from herdr_onboarding import wizard as wiz  # noqa: E402
from herdr_onboarding.cli import main  # noqa: E402


class RecordingStep:
    """Stub step: records execution and optionally raises."""

    name = "recording"
    roles = ("plugin", "brain")

    def __init__(self, error=None):
        self.ran = False
        self.error = error
        self.ctx_interactive = None

    def run(self, ctx):
        self.ran = True
        self.ctx_interactive = ctx.interactive
        if isinstance(self.error, Exception):
            raise self.error


def _gate(result=True):
    calls = []

    def gate(ctx):
        calls.append(ctx)
        return result

    gate.calls = calls
    return gate


def _run(argv, *, home, steps=None, gate=_gate(), isatty=True, stdin=None):
    out, err = StringIO(), StringIO()
    code = main(
        argv,
        env={"HOME": str(home)},
        stdin=stdin if stdin is not None else StringIO(),
        stdout=out,
        stderr=err,
        isatty=lambda: isatty,
        health_gate=gate,
        steps=steps,
    )
    return code, out.getvalue(), err.getvalue()


def _marker(home: Path) -> Path:
    return home / ".config" / "herdr-tts" / "first-run.done"


def _copy_onboarding(root: Path) -> Path:
    src = REPO_ROOT / "tools" / "herdr_onboarding"
    dst = root / "tools" / "herdr_onboarding"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return root / "tools"


def _invoke_module(lib: Path, argv, home: Path) -> subprocess.CompletedProcess:
    # sys.executable is the brain entry point's venv Python under
    # ``uv run python -m pytest``; the installed-layout fixtures have no
    # bootstrapped venv of their own, so the available venv interpreter
    # stands in for each entry point's Python.
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(lib),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "herdr_onboarding", *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestNoFirstRun:
    def test_exit_zero_no_marker_steps_not_run(self, tmp_path):
        step = RecordingStep()
        code, out, err = _run(
            ["--role", "plugin", "--no-first-run"], home=tmp_path, steps=[step]
        )
        assert code == 0
        assert not _marker(tmp_path).exists()
        assert not step.ran

    def test_json_reports_skipped(self, tmp_path):
        code, out, _ = _run(
            ["--role", "plugin", "--no-first-run", "--json"], home=tmp_path
        )
        assert code == 0
        record = json.loads(out)
        assert record["status"] == "skipped"
        assert record["exit"] == 0
        assert record["marker_written"] is False
        assert not _marker(tmp_path).exists()

    def test_skips_even_when_marker_exists(self, tmp_path):
        marker = _marker(tmp_path)
        marker.parent.mkdir(parents=True)
        marker.write_text('{"version": 1}\n')
        step = RecordingStep()
        code, _, _ = _run(
            ["--role", "brain", "--no-first-run"], home=tmp_path, steps=[step]
        )
        assert code == 0
        assert not step.ran


class TestExitContract:
    def test_completed_with_passing_gate_writes_marker(self, tmp_path):
        step = RecordingStep()
        code, _, err = _run(["--role", "plugin"], home=tmp_path, steps=[step])
        assert code == 0
        assert step.ran
        marker = _marker(tmp_path)
        assert marker.is_file()
        payload = json.loads(marker.read_text())
        assert payload["version"] == 1
        assert payload["role"] == "plugin"
        assert payload["completed_at"].endswith("Z")
        assert marker.stat().st_mode & 0o777 == 0o644

    def test_completed_no_marker_without_gate(self, tmp_path):
        # LIBRARY SEAM: a Wizard constructed with no gate completes its
        # steps but writes no marker and exits 10 ("completed with no
        # marker").  The CLI never takes this path since task 3.5: it
        # always wires the real gate (see test_onboarding_health.py).
        step = RecordingStep()
        err = StringIO()
        wizard = wiz.Wizard(
            wiz.WizardOptions(role="plugin"),
            env={"HOME": str(tmp_path)},
            stdin=StringIO(),
            stdout=StringIO(),
            stderr=err,
            isatty=lambda: True,
            steps=[step],
        )
        assert wizard.run() == 10
        assert step.ran
        assert not _marker(tmp_path).exists()
        assert "health gate" in err.getvalue()

    def test_failing_gate_exit_30_no_marker(self, tmp_path):
        step = RecordingStep()
        code, _, err = _run(
            ["--role", "plugin"], home=tmp_path, steps=[step], gate=_gate(False)
        )
        assert code == 30
        assert step.ran
        assert not _marker(tmp_path).exists()
        assert "marker" in err

    def test_gate_crash_is_exit_30_not_crash(self, tmp_path):
        def crashing_gate(ctx):
            raise RuntimeError("gate probe exploded")

        code, _, _ = _run(
            ["--role", "plugin"], home=tmp_path, steps=[], gate=crashing_gate
        )
        assert code == 30
        assert not _marker(tmp_path).exists()

    def test_missing_answer_noninteractive_exit_20(self, tmp_path):
        step = RecordingStep(error=wiz.MissingAnswer("glm-api-key"))
        code, _, err = _run(
            ["--role", "brain", "--non-interactive"],
            home=tmp_path,
            steps=[step],
        )
        assert code == 20
        assert step.ran
        assert not _marker(tmp_path).exists()
        assert "--no-first-run" in err
        assert "glm-api-key" in err

    def test_user_abort_exit_40_no_marker(self, tmp_path):
        step = RecordingStep(error=wiz.StepAbort("declined"))
        code, _, err = _run(["--role", "plugin"], home=tmp_path, steps=[step])
        assert code == 40
        assert not _marker(tmp_path).exists()

    def test_unexpected_step_error_exit_40_no_marker(self, tmp_path):
        step = RecordingStep(error=RuntimeError("disk on fire"))
        code, _, err = _run(["--role", "plugin"], home=tmp_path, steps=[step])
        assert code == 40
        assert not _marker(tmp_path).exists()
        assert "disk on fire" in err


class TestMarkerLifecycle:
    def test_marker_replace_failure_is_exit_40_and_leaves_no_partial_marker(self, tmp_path, monkeypatch):
        def fail_replace(*args):
            raise OSError("simulated marker publication failure")

        monkeypatch.setattr(os, "replace", fail_replace)
        code, _, err = _run(["--role", "plugin"], home=tmp_path, steps=[])
        assert code == 40, err
        assert not _marker(tmp_path).exists()
        assert list(_marker(tmp_path).parent.iterdir()) == []

    def test_marker_is_published_only_as_complete_json(self, tmp_path, monkeypatch):
        replace = os.replace
        publications = []

        def observe(source, destination):
            assert not Path(destination).exists()
            assert json.loads(Path(source).read_text())["version"] == 1
            assert Path(source).stat().st_mode & 0o777 == 0o644
            publications.append(destination)
            replace(source, destination)

        monkeypatch.setattr(os, "replace", observe)
        assert _run(["--role", "plugin"], home=tmp_path, steps=[])[0] == 0
        assert publications == [_marker(tmp_path)]

    def test_existing_marker_suppresses_wizard(self, tmp_path):
        marker = _marker(tmp_path)
        marker.parent.mkdir(parents=True)
        original = json.dumps(
            {"version": 1, "role": "plugin", "completed_at": "2026-01-01T00:00:00Z"}
        )
        marker.write_text(original)
        step = RecordingStep()
        code, _, _ = _run(["--role", "plugin"], home=tmp_path, steps=[step])
        assert code == 0
        assert not step.ran
        assert marker.read_text() == original

    def test_corrupt_marker_still_runs_wizard(self, tmp_path):
        marker = _marker(tmp_path)
        marker.parent.mkdir(parents=True)
        marker.write_text("not json at all")
        step = RecordingStep()
        code, _, _ = _run(["--role", "plugin"], home=tmp_path, steps=[step])
        assert code == 0
        assert step.ran

    def test_marker_honours_xdg_config_home(self, tmp_path):
        out, err = StringIO(), StringIO()
        code = main(
            ["--role", "plugin"],
            env={"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "xdg")},
            stdin=StringIO(),
            stdout=out,
            stderr=err,
            isatty=lambda: True,
            health_gate=lambda ctx: True,
        )
        assert code == 0
        assert (tmp_path / "xdg" / "herdr-tts" / "first-run.done").is_file()

    def test_marker_records_step_preferences(self, tmp_path):
        class Preferencing(RecordingStep):
            def run(self, ctx):
                ctx.preferences["voice"] = "edge"
                super().run(ctx)

        code, _, _ = _run(
            ["--role", "plugin"], home=tmp_path, steps=[Preferencing()]
        )
        assert code == 0
        payload = json.loads(_marker(tmp_path).read_text())
        assert payload["voice"] == "edge"


class TestTTYDetection:
    @pytest.mark.parametrize("host,role", [("brain", "brain"), ("tts-plugin", "plugin")])
    @pytest.mark.parametrize("exit_code", [0, 30, 40])
    def test_auto_handoff_on_real_tty_never_aborts_startup(self, tmp_path, host, role, exit_code):
        # The module is an explicit dispatcher double, executed by real Python.
        # No daemon, health endpoint or credential is touched by this drill.
        entry = "herdr-brain" if host == "brain" else "herdr-tts"
        text = (REPO_ROOT / "hosts/herdr" / host / "bin" / entry).read_text()
        start = text.index("# >>> herdr first-run hand-off")
        end = text.index("# <<< herdr first-run hand-off <<<")
        lib = tmp_path / "lib"
        module = lib / "herdr_onboarding"
        module.mkdir(parents=True)
        (module / "__main__.py").write_text(
            f"import sys\nprint('dispatcher-double', sys.argv[1:])\nraise SystemExit({exit_code})\n"
        )
        driver = tmp_path / entry
        driver.write_text(text[start:end] +
                          f'\nherdr_first_run_auto {role} "{sys.executable}" {entry}\necho startup-continued\n')
        env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "config"),
               "HERDR_ONBOARDING_HOME": str(lib), "PATH": "/usr/bin:/bin",
               "PYTHONDONTWRITEBYTECODE": "1"}
        master, slave = pty.openpty()
        try:
            child = subprocess.Popen(["bash", str(driver)], env=env, stdin=slave, stdout=slave,
                                     stderr=subprocess.PIPE, text=True)
            _, err = child.communicate(timeout=10)
            output = os.read(master, 8192).decode()
        finally:
            os.close(slave)
            os.close(master)
        assert child.returncode == 0, err
        assert "dispatcher-double" in output and role in output
        assert "startup-continued" in output
        assert not (tmp_path / "config/herdr-tts/first-run.done").exists()
        if exit_code:
            assert f"onboarding ended with exit {exit_code}" in err

    def test_launcher_handoff_blocks_are_byte_identical(self):
        blocks = []
        for host, entry in (("brain", "herdr-brain"), ("tts-plugin", "herdr-tts")):
            text = (REPO_ROOT / "hosts/herdr" / host / "bin" / entry).read_text()
            blocks.append(text[text.index("# >>> herdr first-run hand-off"):
                               text.index("# <<< herdr first-run hand-off <<<")])
        assert blocks[0] == blocks[1]

    def test_no_tty_prints_noninteractive_hint(self, tmp_path):
        code, _, err = _run(
            ["--role", "plugin"], home=tmp_path, steps=[], isatty=False
        )
        assert code == 0
        assert "non-interactively" in err
        assert "--no-first-run" in err

    def test_tty_prints_no_hint(self, tmp_path):
        code, _, err = _run(
            ["--role", "plugin"], home=tmp_path, steps=[], isatty=True
        )
        assert code == 0
        assert "non-interactively" not in err

    def test_explicit_noninteractive_flag_prints_no_tty_notice(self, tmp_path):
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive"],
            home=tmp_path,
            steps=[],
            isatty=True,
        )
        assert code == 0
        assert "no TTY detected" not in err

    def test_no_tty_runs_steps_non_interactively(self, tmp_path):
        step = RecordingStep()
        _run(["--role", "plugin"], home=tmp_path, steps=[step], isatty=False)
        assert step.ran
        assert step.ctx_interactive is False

    def test_explicit_flag_forces_non_interactive_even_with_tty(self, tmp_path):
        step = RecordingStep()
        _run(
            ["--role", "plugin", "--non-interactive"],
            home=tmp_path,
            steps=[step],
            isatty=True,
        )
        assert step.ctx_interactive is False

    def test_tty_runs_steps_interactively(self, tmp_path):
        step = RecordingStep()
        _run(["--role", "plugin"], home=tmp_path, steps=[step], isatty=True)
        assert step.ctx_interactive is True


class TestInteractivePrompt:
    def test_prompt_reads_stdin_line(self, tmp_path):
        captured = {}

        class Asking(RecordingStep):
            def run(self, ctx):
                captured["answer"] = ctx.prompt("Pick a number")

        code, _, _ = _run(
            ["--role", "plugin"],
            home=tmp_path,
            steps=[Asking()],
            stdin=StringIO("42\n"),
        )
        assert code == 0
        assert captured["answer"] == "42"

    def test_closed_stdin_aborts(self, tmp_path):
        class Asking(RecordingStep):
            def run(self, ctx):
                ctx.prompt("Pick a number")

        code, _, _ = _run(
            ["--role", "plugin"], home=tmp_path, steps=[Asking()], stdin=StringIO()
        )
        assert code == 40


class TestCLISurface:
    def test_role_required(self, tmp_path):
        code, _, err = _run(["--non-interactive"], home=tmp_path)
        assert code == 2
        assert "--role" in err

    def test_invalid_role_rejected(self, tmp_path):
        code, _, err = _run(["--role", "keg"], home=tmp_path)
        assert code == 2
        assert "plugin or brain" in err

    def test_equals_form_accepted(self, tmp_path):
        code, _, _ = _run(["--role=plugin"], home=tmp_path, steps=[])
        assert code == 0

    def test_unknown_argument_rejected_without_echoing_it(self, tmp_path):
        # A fat-fingered ``--glm-key <secret>`` must be refused WITHOUT the
        # value being reflected in any output byte.
        canary = "sk-test-canary-DO-NOT-ECHO"
        code, out, err = _run(
            ["--role", "plugin", "--glm-key", canary], home=tmp_path
        )
        assert code == 2
        assert canary not in out
        assert canary not in err
        assert not _marker(tmp_path).exists()


class TestPreferenceFlags:
    """Tasks 3.3/3.4 flags: non-secret preference answers on the CLI."""

    def test_value_flags_are_parsed_in_both_spellings(self):
        from herdr_onboarding.cli import _parse

        options, error = _parse(
            ["--role", "brain", "--voice-provider=Piper", "--keymap-style", "none",
             "--stt", "BASE", "--voice", "elvira", "--replace-keymap"]
        )
        assert error is None
        assert (options.voice_provider, options.keymap_style, options.stt) == (
            "piper", "none", "base",
        )
        assert options.voice == "elvira" and options.replace_keymap is True

    @pytest.mark.parametrize(
        "argv",
        [
            ["--role", "plugin", "--voice-provider", "espeak"],
            ["--role", "plugin", "--keymap-style", "vim"],
            ["--role", "brain", "--stt", "large"],
            ["--role", "plugin", "--stt"],
        ],
    )
    def test_invalid_value_is_refused_without_echoing_it(self, tmp_path, argv):
        code, out, err = _run(argv, home=tmp_path)
        assert code == 2
        for bad in ("espeak", "vim", "large"):
            assert bad not in out + err
        assert not _marker(tmp_path).exists()

    def test_free_form_voice_rejects_quotes(self, tmp_path):
        code, _, err = _run(["--role", "plugin", "--voice", 'a"b'], home=tmp_path)
        assert code == 2 and "double quotes" in err


class TestWizardJsonOutput:
    def test_json_completion_shape(self, tmp_path):
        code, out, _ = _run(["--role", "brain", "--json"], home=tmp_path, steps=[])
        assert code == 0
        record = json.loads(out)
        assert record["status"] == "completed"
        assert record["exit"] == 0
        assert record["role"] == "brain"
        assert record["marker_written"] is True
        assert record["marker"].endswith("first-run.done")


# ---------------------------------------------------------------------------
# Reachability matrix (design Decision 1): three supported layouts plus the
# honest Homebrew-unavailable case; each supported layout is also *invoked*
# through PYTHONPATH + ``-m`` with the venv interpreter.
# ---------------------------------------------------------------------------


def _source_checkout(tmp_path):
    root = tmp_path / "checkout"
    for host in ("tts-plugin", "brain"):
        (root / "hosts" / "herdr" / host / "bin").mkdir(parents=True)
    return root, _copy_onboarding(root)


def _curl_route_clone(tmp_path):
    # install.sh clones the WHOLE monorepo under ~/.local/share/herdr-tts/plugin
    root = tmp_path / "share" / "herdr-tts" / "plugin"
    (root / "hosts" / "herdr" / "tts-plugin" / "bin").mkdir(parents=True)
    return root, _copy_onboarding(root)


def _managed_subdir_install(tmp_path):
    # herdr's managed_path is a full monorepo checkout; plugin_root points
    # at the subdirectory (design OQ-1 predicate).
    root = tmp_path / "managed" / "github" / "agent-tts"
    (root / "hosts" / "herdr" / "tts-plugin" / "bin").mkdir(parents=True)
    return root, _copy_onboarding(root)


def _keg_prefix(tmp_path):
    # The Homebrew formula stages only the plugin subdirectory: no tools/.
    keg = tmp_path / "keg"
    (keg / "bin").mkdir(parents=True)
    (keg / "hosts" / "herdr" / "tts-plugin").mkdir(parents=True)
    return keg


class TestReachabilityResolution:
    def test_source_checkout_both_entry_points(self, tmp_path):
        root, lib = _source_checkout(tmp_path)
        for host in ("tts-plugin", "brain"):
            start = root / "hosts" / "herdr" / host / "bin"
            assert wiz.resolve_onboarding_lib(env={}, start=start) == lib

    def test_curl_route_full_clone(self, tmp_path):
        root, lib = _curl_route_clone(tmp_path)
        start = root / "hosts" / "herdr" / "tts-plugin" / "bin"
        assert wiz.resolve_onboarding_lib(env={}, start=start) == lib

    def test_managed_subdirectory_install(self, tmp_path):
        root, lib = _managed_subdir_install(tmp_path)
        start = root / "hosts" / "herdr" / "tts-plugin" / "bin"
        assert wiz.resolve_onboarding_lib(env={}, start=start) == lib

    def test_homebrew_keg_fails_honestly(self, tmp_path):
        keg = _keg_prefix(tmp_path)
        with pytest.raises(res.ResolutionError) as exc:
            wiz.resolve_onboarding_lib(env={}, start=keg / "bin")
        message = str(exc.value)
        assert wiz.ONBOARDING_HOME_ENV in message
        assert "Homebrew" in message

    def test_override_env_wins(self, tmp_path):
        root, lib = _source_checkout(tmp_path)
        override = tmp_path / "explicit-tools"
        shutil.copytree(lib, override)
        got = wiz.resolve_onboarding_lib(
            env={wiz.ONBOARDING_HOME_ENV: str(override)},
            start=root / "hosts" / "herdr" / "tts-plugin" / "bin",
        )
        assert got == override

    def test_default_anchor_is_the_real_repo_tools(self):
        assert wiz.resolve_onboarding_lib(env={}) == REPO_ROOT / "tools"


class TestReachabilityInvocation:
    def test_brain_launcher_dispatches_first_run_through_its_real_venv(self, tmp_path):
        root = tmp_path / "checkout"
        brain = root / "hosts/herdr/brain"
        (brain / "bin").mkdir(parents=True)
        (root / "hosts/herdr/tts-plugin").mkdir()
        (brain / ".venv/bin").mkdir(parents=True)
        (brain / ".venv/bin/python").symlink_to(sys.executable)
        shutil.copy2(REPO_ROOT / "hosts/herdr/brain/bin/herdr-brain", brain / "bin/herdr-brain")
        _copy_onboarding(root)
        env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
               "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(["bash", str(brain / "bin/herdr-brain"), "first-run",
                                 "--no-first-run", "--json"], env=env, capture_output=True,
                                text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "skipped"

    @pytest.mark.parametrize(
        "builder",
        [_source_checkout, _curl_route_clone, _managed_subdir_install],
        ids=["source-checkout", "curl-route-clone", "managed-subdir"],
    )
    def test_python_m_invocation_no_first_run(self, tmp_path, builder):
        _root, lib = builder(tmp_path)
        home = tmp_path / "home"
        completed = _invoke_module(lib, ["--role", "plugin", "--no-first-run"], home)
        assert completed.returncode == 0, completed.stderr
        assert not _marker(home).exists()

    @pytest.mark.parametrize(
        "builder",
        [_source_checkout, _curl_route_clone, _managed_subdir_install],
        ids=["source-checkout", "curl-route-clone", "managed-subdir"],
    )
    def test_python_m_invocation_json_skip(self, tmp_path, builder):
        _root, lib = builder(tmp_path)
        home = tmp_path / "home"
        completed = _invoke_module(
            lib, ["--role", "brain", "--no-first-run", "--json"], home
        )
        assert completed.returncode == 0, completed.stderr
        record = json.loads(completed.stdout)
        assert record["status"] == "skipped"

    def test_real_repo_module_invocation_hermetic_home(self, tmp_path):
        home = tmp_path / "home"
        completed = _invoke_module(
            REPO_ROOT / "tools", ["--role", "plugin", "--no-first-run"], home
        )
        assert completed.returncode == 0, completed.stderr
        assert not _marker(home).exists()


@pytest.mark.parametrize("scenario,local,real,expected", [("08-doctor-diagnoses-break", "1", "0", "PASS"),
                                                         ("09-post-wizard-health", "1", "0", "PASS"),
                                                         ("09-post-wizard-health", "0", "0", "BLOCKED"),
                                                         ("09-post-wizard-health", "0", "1", "BLOCKED")])
def test_acceptance_scenarios_use_existing_sandbox_api(tmp_path, scenario, local, real, expected):
    # AT-11 task 4.2: tracked HEAD via the harness, scoped candidate overlay.
    paths = ["tools/herdr_onboarding/doctor.py", "tools/herdr_onboarding/cli.py",
             "hosts/herdr/brain/bin/herdr-brain", "hosts/herdr/tts-plugin/bin/herdr-tts",
             f"scripts/acceptance/scenarios/{scenario}.sh"]
    overlay = "import pathlib,shutil,sys; src,dst=map(pathlib.Path,sys.argv[1:]); paths=" + repr(paths) + "; [shutil.copy2(src/p,dst/p) for p in paths]"
    script = '''export HERDR_ACCEPTANCE_API=1; source scripts/acceptance/clean-install.sh;
SRC="$PWD"; HARNESS_FILE="$SRC/scripts/acceptance/clean-install.sh"; HARNESS_DIR="$SRC/scripts/acceptance";
ALLOWED_TOOLS=(bash git jq curl python3 uv); TOOLS_AVAILABLE=(); TOOLS_MISSING=(); KEEP=1;
build_sandbox; "$1" -c "$2" "$SRC" "$CHECKOUT" || exit 1;
SANDBOX_ENV_BASE+=(HERDR_ACCEPTANCE_PYTHON="$1" HERDR_ACCEPTANCE_LOCAL_HEALTH="$4" HERDR_ACCEPTANCE_REAL_HEALTH="$6");
run_scenario "$3" "$CHECKOUT/scripts/acceptance/scenarios/$3.sh";
printf 'RESULT=%s FAILURES=%s STUBBED=%s REASON=%s\\n' "$RC_STATE" "$RC_FAIL" "$RC_STUB" "$RC_REASON";
[[ "$RC_STATE" == "$5" && "$RC_FAIL" == 0 ]]'''
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(["bash", "-c", script, "_", sys.executable, overlay, scenario, local, expected, real],
                            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"RESULT={expected}" in result.stdout
    if expected == "PASS":
        assert "STUBBED=1" in result.stdout
    elif real == "1":
        assert "installed sandbox brain interpreter is missing" in result.stdout
