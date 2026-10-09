"""V1 for the keymap step (AT-11 task 3.3, design slice 17).

Two layers, labelled honestly:

- command-sequence tests inject a fake runner at the external boundary
  (``bash <launcher> keymap …`` and ``herdr server reload-config``); they
  prove WHAT the wizard asks the plugin mechanism to do, never that herdr
  really reloads;
- the sandbox drill (``TestSandboxDrill``) executes the REAL plugin
  launcher (``keymap adopt|apply``) against a temporary ``HOME`` with a
  stub venv interpreter and a stub ``herdr`` that records its argv.  It
  proves adoption + managed-block application + the automatic reload
  call end to end on disk; it is not proof against a real herdr server.
"""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_onboarding.cli import main  # noqa: E402
from herdr_onboarding.steps import steps_for_role  # noqa: E402
from herdr_onboarding.steps.keymap import KeymapStep  # noqa: E402


class FakeRunner:
    """Records argv; ``fail`` maps a substring of the command to an rc."""

    def __init__(self, fail=None):
        self.calls: list = []
        self.fail = fail or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        line = " ".join(argv)
        for needle, code in self.fail.items():
            if needle in line:
                return subprocess.CompletedProcess(argv, code, "", f"boom: {needle}")
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    def lines(self):
        return [" ".join(c) for c in self.calls]


def _env(home: Path, **extra) -> dict:
    env = {"HOME": str(home)}  # hermetic: no ambient XDG_*/HERDR_* leaks in
    env.update(extra)
    return env


def _run(argv, *, home, runner=None, env=None, stdin="", isatty=False, step=None):
    out, err = StringIO(), StringIO()
    code = main(
        argv,
        env=env if env is not None else _env(home),
        stdin=StringIO(stdin),
        stdout=out,
        stderr=err,
        isatty=lambda: isatty,
        health_gate=lambda ctx: True,
        steps=[step or KeymapStep(runner=runner)],
    )
    return code, out.getvalue(), err.getvalue()


def _keymap(home: Path) -> Path:
    return home / ".config" / "herdr-tts" / "keymap.json"


def _marker(home: Path) -> dict:
    return json.loads((home / ".config" / "herdr-tts" / "first-run.done").read_text())


def _seed_user_keymap(home: Path) -> str:
    path = _keymap(home)
    path.parent.mkdir(parents=True)
    text = '{"style": "direct", "bindings": {"menu": "prefix+j"}}\n'
    path.write_text(text)
    return text


class TestAdoptApplyReload:
    def test_fresh_adoption_runs_adopt_apply_then_reload_automatically(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        lines = runner.lines()
        assert len(lines) == 3
        assert lines[0].startswith("bash ") and lines[0].endswith("keymap adopt --style menu")
        assert "--force" not in lines[0]
        assert lines[1].endswith("keymap apply")
        assert lines[2] == "herdr server reload-config"  # no manual reload step
        marker = _marker(tmp_path)
        assert marker["keymap"] == "menu" and marker["keymap_reloaded"] is True

    @pytest.mark.parametrize("style", ["direct", "ctrlalt"])
    def test_other_styles_are_passed_through(self, tmp_path, style):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", style],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        assert runner.lines()[0].endswith(f"keymap adopt --style {style}")

    def test_launcher_is_the_plugin_one_from_the_resolved_root(self, tmp_path):
        runner = FakeRunner()
        _run(["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
             home=tmp_path, runner=runner)
        launcher = Path(runner.calls[0][1])
        assert launcher == REPO_ROOT / "hosts/herdr/tts-plugin/bin/herdr-tts"
        assert launcher.is_file()

    def test_explicit_tts_home_wins(self, tmp_path):
        home = tmp_path / "plugin-home"
        home.mkdir()
        runner = FakeRunner()
        _run(["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
             home=tmp_path, runner=runner,
             env=_env(tmp_path, HERDR_TTS_HOME=str(home)))
        assert runner.calls[0][1] == str(home / "bin" / "herdr-tts")

    def test_reload_uses_the_discovered_herdr_binary(self, tmp_path):
        runner = FakeRunner()
        _run(["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
             home=tmp_path, runner=runner, env=_env(tmp_path, HERDR_BIN="/opt/herdr/bin/herdr"))
        assert runner.lines()[-1] == "/opt/herdr/bin/herdr server reload-config"


class TestFailuresDegradeLikeTheInstaller:
    def test_adopt_failure_warns_and_never_applies_or_reloads(self, tmp_path):
        runner = FakeRunner(fail={"keymap adopt": 1})
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        assert len(runner.calls) == 1
        assert "herdr-tts keymap init" in err
        assert _marker(tmp_path)["keymap"] == "failed"

    def test_reload_failure_is_a_warning_with_the_manual_command(self, tmp_path):
        runner = FakeRunner(fail={"reload-config": 1})
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        assert "run it manually later" in err
        marker = _marker(tmp_path)
        assert marker["keymap"] == "menu" and marker["keymap_reloaded"] is False

    def test_missing_executable_is_a_warning_not_a_crash(self, tmp_path):
        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
            home=tmp_path,
            runner=missing,
        )
        assert code == 0, err
        assert _marker(tmp_path)["keymap"] == "failed"


class TestExistingKeymapIsNeverOverwritten:
    def test_style_without_consent_preserves_file_and_informs(self, tmp_path):
        original = _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu"],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        assert runner.calls == []  # nothing ran, not even a no-op adopt
        assert _keymap(tmp_path).read_text() == original
        assert "preserved" in err and "--replace-keymap" in err
        assert _marker(tmp_path)["keymap"] == "existing"

    def test_no_style_at_all_preserves_without_asking(self, tmp_path):
        original = _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        code, out, err = _run(
            ["--role", "plugin"], home=tmp_path, runner=runner, stdin="menu\n", isatty=True
        )
        assert code == 0, err
        assert runner.calls == [] and "Keymap style" not in out
        assert _keymap(tmp_path).read_text() == original
        assert "left untouched" in err

    def test_flag_consent_replaces_through_the_launchers_own_force(self, tmp_path):
        _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu",
             "--replace-keymap"],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        assert runner.lines()[0].endswith("keymap adopt --style menu --force")
        assert _marker(tmp_path)["keymap"] == "menu"

    def test_env_consent_is_equivalent_to_the_flag(self, tmp_path):
        _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        env = _env(tmp_path, HERDR_ONBOARDING_KEYMAP_STYLE="direct",
                   HERDR_ONBOARDING_REPLACE_KEYMAP="yes")
        code, _, err = _run(["--role", "plugin", "--non-interactive"],
                            home=tmp_path, runner=runner, env=env)
        assert code == 0, err
        assert runner.lines()[0].endswith("keymap adopt --style direct --force")

    def test_interactive_yes_grants_consent(self, tmp_path):
        _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        code, out, err = _run(
            ["--role", "plugin", "--keymap-style", "menu"],
            home=tmp_path, runner=runner, stdin="y\n", isatty=True,
        )
        assert code == 0, err
        assert "Replace your existing keymap" in out
        assert runner.lines()[0].endswith("--force")

    @pytest.mark.parametrize("answer", ["n\n", "\n", ""])
    def test_interactive_no_blank_or_eof_keeps_the_file(self, tmp_path, answer):
        original = _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--keymap-style", "menu"],
            home=tmp_path, runner=runner, stdin=answer, isatty=True,
        )
        assert code == 0, err
        assert runner.calls == []
        assert _keymap(tmp_path).read_text() == original


class TestOptOutAndQuestions:
    def test_none_runs_nothing_and_creates_nothing(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "none"],
            home=tmp_path,
            runner=runner,
        )
        assert code == 0, err
        assert runner.calls == []
        assert not _keymap(tmp_path).exists()
        assert _marker(tmp_path)["keymap"] == "none"

    def test_none_with_an_existing_keymap_leaves_it_alone_even_with_consent(self, tmp_path):
        original = _seed_user_keymap(tmp_path)
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "none",
             "--replace-keymap"],
            home=tmp_path, runner=runner,
        )
        assert code == 0, err
        assert runner.calls == [] and _keymap(tmp_path).read_text() == original

    def test_noninteractive_without_an_answer_changes_nothing(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(["--role", "plugin", "--non-interactive"], home=tmp_path, runner=runner)
        assert code == 0, err
        assert runner.calls == []
        assert _marker(tmp_path)["keymap"] == "skipped"

    def test_interactive_blank_takes_the_installer_default_menu(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(["--role", "plugin"], home=tmp_path, runner=runner,
                            stdin="\n", isatty=True)
        assert code == 0, err
        assert runner.lines()[0].endswith("keymap adopt --style menu")

    def test_interactive_answer_selects_a_style(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(["--role", "plugin"], home=tmp_path, runner=runner,
                            stdin="Direct\n", isatty=True)
        assert code == 0, err
        assert runner.lines()[0].endswith("keymap adopt --style direct")

    def test_interactive_eof_skips_without_aborting(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(["--role", "plugin"], home=tmp_path, runner=runner,
                            stdin="", isatty=True)
        assert code == 0, err
        assert runner.calls == []

    def test_invalid_env_style_fails_naming_the_allowed_set(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive"], home=tmp_path, runner=runner,
            env=_env(tmp_path, HERDR_ONBOARDING_KEYMAP_STYLE="vim"),
        )
        assert code == 40
        assert "menu, direct, ctrlalt, none" in err
        assert runner.calls == []


class TestRegistry:
    def test_keymap_step_is_offered_to_both_roles(self):
        for role in ("plugin", "brain"):
            assert any(isinstance(s, KeymapStep) for s in steps_for_role(role))


class TestSandboxDrill:
    """REAL launcher, stub herdr, temporary HOME (see module docstring)."""

    def _sandbox(self, tmp_path: Path):
        home = tmp_path / "home"
        venv_py = home / ".local" / "share" / "herdr-tts" / "venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("")
        venv_py.chmod(0o755)  # satisfies the launcher's bootstrap guard; never run
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "herdr.log"
        stub = bindir / "herdr"
        stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
        stub.chmod(0o755)
        env = {"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin"}
        return home, env, log

    def _drill(self, argv, env, stdin=""):
        out, err = StringIO(), StringIO()
        code = main(
            argv, env=env, stdin=StringIO(stdin), stdout=out, stderr=err,
            isatty=lambda: False, health_gate=lambda ctx: True, steps=[KeymapStep()],
        )
        return code, err.getvalue()

    def test_adopt_apply_reload_end_to_end(self, tmp_path):
        home, env, log = self._sandbox(tmp_path)
        code, err = self._drill(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "menu"], env
        )
        assert code == 0, err
        assert (home / ".config/herdr-tts/keymap.json").is_file()
        toml = (home / ".config/herdr/config.toml").read_text()
        assert "generated by: herdr-tts keymap apply" in toml
        # the launcher's own `config check` precedes the automatic reload
        assert log.read_text().splitlines()[-1] == "server reload-config"
        marker = json.loads((home / ".config/herdr-tts/first-run.done").read_text())
        assert marker["keymap"] == "menu" and marker["keymap_reloaded"] is True

    def test_existing_user_keymap_survives_then_is_replaced_only_with_consent(self, tmp_path):
        home, env, log = self._sandbox(tmp_path)
        keymap = home / ".config/herdr-tts/keymap.json"
        keymap.parent.mkdir(parents=True)
        mine = '{"style": "direct", "bindings": {"menu": "prefix+j"}}\n'
        keymap.write_text(mine)

        code, err = self._drill(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "direct"], env
        )
        assert code == 0, err
        assert keymap.read_text() == mine and not log.exists()

        (home / ".config/herdr-tts/first-run.done").unlink()
        code, err = self._drill(
            ["--role", "plugin", "--non-interactive", "--keymap-style", "direct",
             "--replace-keymap"], env
        )
        assert code == 0, err
        assert keymap.read_text() != mine
        assert log.read_text().splitlines()[-1] == "server reload-config"
        assert list(keymap.parent.glob("keymap.json.*")), "launcher keeps a backup"
