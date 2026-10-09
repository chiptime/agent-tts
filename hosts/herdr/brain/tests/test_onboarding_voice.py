"""V1 for the voice step (AT-11 task 3.3, design slice 17).

Voice provider / default voice persistence into the plugin's managed
``config.env`` (the file the launcher sources), preservation of existing
preferences on re-run, and the answer ranking flag > env > question.
Everything runs against a temporary ``HOME``; nothing touches the user's
real configuration.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_onboarding.cli import main  # noqa: E402
from herdr_onboarding.steps import steps_for_role  # noqa: E402
from herdr_onboarding.steps import voice as voice_mod  # noqa: E402
from herdr_onboarding.steps.voice import VoiceStep  # noqa: E402


def _env(home: Path, **extra) -> dict:
    env = {"HOME": str(home)}  # hermetic: no ambient XDG_*/HERDR_* leaks in
    env.update(extra)
    return env


def _run(argv, *, home, env=None, stdin="", isatty=False):
    out, err = StringIO(), StringIO()
    code = main(
        argv,
        env=env if env is not None else _env(home),
        stdin=StringIO(stdin),
        stdout=out,
        stderr=err,
        isatty=lambda: isatty,
        health_gate=lambda ctx: True,
        steps=[VoiceStep()],
    )
    return code, out.getvalue(), err.getvalue()


def _forget_marker(home: Path) -> None:
    """Deleting the marker is the documented "run onboarding again"."""
    (home / ".config" / "herdr-tts" / "first-run.done").unlink()


def _config(home: Path) -> Path:
    return home / ".config" / "herdr-tts" / "config.env"


def _marker(home: Path) -> dict:
    return json.loads((home / ".config" / "herdr-tts" / "first-run.done").read_text())


class TestPersistence:
    def test_flag_persists_provider_and_marker_records_it(self, tmp_path):
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "openai"],
            home=tmp_path,
        )
        assert code == 0, err
        assert 'TTS_PROVIDER="openai"' in _config(tmp_path).read_text()
        assert _marker(tmp_path)["voice"] == "openai"

    def test_env_answer_is_used_and_flag_wins_over_it(self, tmp_path):
        env = _env(tmp_path, HERDR_ONBOARDING_VOICE_PROVIDER="piper")
        code, _, err = _run(["--role", "plugin", "--non-interactive"], home=tmp_path, env=env)
        assert code == 0, err
        assert 'TTS_PROVIDER="piper"' in _config(tmp_path).read_text()
        _forget_marker(tmp_path)
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "edge"],
            home=tmp_path,
            env=env,
        )
        assert code == 0, err
        assert 'TTS_PROVIDER="edge"' in _config(tmp_path).read_text()

    def test_default_voice_is_persisted_next_to_the_provider(self, tmp_path):
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "edge",
             "--voice", "elvira"],
            home=tmp_path,
        )
        assert code == 0, err
        text = _config(tmp_path).read_text()
        assert 'TTS_PROVIDER="edge"' in text and 'TTS_VOICE="elvira"' in text
        assert _marker(tmp_path)["voice_name"] == "elvira"

    def test_config_file_override_matches_the_launcher(self, tmp_path):
        target = tmp_path / "sandbox" / "custom.env"
        env = _env(tmp_path, HERDR_TTS_CONFIG_FILE=str(target))
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "piper"],
            home=tmp_path,
            env=env,
        )
        assert code == 0, err
        assert 'TTS_PROVIDER="piper"' in target.read_text()
        assert not _config(tmp_path).exists()

    def test_invalid_env_provider_fails_with_the_allowed_set(self, tmp_path):
        env = _env(tmp_path, HERDR_ONBOARDING_VOICE_PROVIDER="espeak")
        code, _, err = _run(["--role", "plugin", "--non-interactive"], home=tmp_path, env=env)
        assert code == 40
        assert "edge, openai, elevenlabs, piper" in err
        assert not _config(tmp_path).exists()


class TestPreservation:
    SEED = (
        "# my own notes\n"
        'TTS_PLAYBACK="remote"\n'
        'TTS_PROVIDER="elevenlabs"\n'
        "CUSTOM_THING=keep me\n"
    )

    def _seed(self, home: Path) -> Path:
        path = _config(home)
        path.parent.mkdir(parents=True)
        path.write_text(self.SEED)
        return path

    def test_rerun_without_an_answer_leaves_the_file_byte_identical(self, tmp_path):
        path = self._seed(tmp_path)
        code, _, err = _run(["--role", "plugin", "--non-interactive"], home=tmp_path)
        assert code == 0, err
        assert path.read_text() == self.SEED
        assert not path.with_name("config.env.bak").exists()
        assert _marker(tmp_path)["voice"] == "elevenlabs"

    def test_interactive_rerun_never_re_asks_an_existing_provider(self, tmp_path):
        path = self._seed(tmp_path)
        code, out, err = _run(
            ["--role", "plugin"], home=tmp_path, stdin="piper\n", isatty=True
        )
        assert code == 0, err
        assert path.read_text() == self.SEED
        assert "Voice provider" not in out

    def test_explicit_answer_replaces_in_place_keeping_everything_else(self, tmp_path):
        path = self._seed(tmp_path)
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "openai"],
            home=tmp_path,
        )
        assert code == 0, err
        assert path.read_text() == self.SEED.replace('"elevenlabs"', '"openai"')
        assert path.with_name("config.env.bak").read_text() == self.SEED

    def test_same_answer_twice_is_idempotent(self, tmp_path):
        argv = ["--role", "plugin", "--non-interactive", "--voice-provider", "piper"]
        assert _run(argv, home=tmp_path)[0] == 0
        first = _config(tmp_path).read_text()
        _forget_marker(tmp_path)
        assert _run(argv, home=tmp_path)[0] == 0
        assert _config(tmp_path).read_text() == first
        assert not _config(tmp_path).with_name("config.env.bak").exists()

    def test_duplicate_assignments_collapse_to_one(self, tmp_path):
        path = _config(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text('TTS_PROVIDER="edge"\nX=1\nTTS_PROVIDER="piper"\n')
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "openai"],
            home=tmp_path,
        )
        assert code == 0, err
        assert path.read_text() == 'TTS_PROVIDER="openai"\nX=1\n'

    def test_new_key_lands_inside_an_existing_managed_block(self, tmp_path):
        path = _config(tmp_path)
        path.parent.mkdir(parents=True)
        block = (
            f"{voice_mod.BLOCK_START}\nTTS_PLAYBACK=\"local\"\n{voice_mod.BLOCK_END}\n"
        )
        path.write_text(block)
        code, _, err = _run(
            ["--role", "plugin", "--non-interactive", "--voice-provider", "piper"],
            home=tmp_path,
        )
        assert code == 0, err
        assert path.read_text() == (
            f"{voice_mod.BLOCK_START}\nTTS_PLAYBACK=\"local\"\n"
            f"TTS_PROVIDER=\"piper\"\n{voice_mod.BLOCK_END}\n"
        )


class TestInteractive:
    def test_answer_is_persisted(self, tmp_path):
        code, out, err = _run(
            ["--role", "plugin"], home=tmp_path, stdin="openai\n", isatty=True
        )
        assert code == 0, err
        assert "Voice provider" in out
        assert 'TTS_PROVIDER="openai"' in _config(tmp_path).read_text()

    def test_blank_line_takes_the_default_provider(self, tmp_path):
        code, _, err = _run(["--role", "plugin"], home=tmp_path, stdin="\n", isatty=True)
        assert code == 0, err
        assert 'TTS_PROVIDER="edge"' in _config(tmp_path).read_text()

    def test_closed_stdin_keeps_defaults_without_aborting(self, tmp_path):
        code, _, err = _run(["--role", "plugin"], home=tmp_path, stdin="", isatty=True)
        assert code == 0, err
        assert not _config(tmp_path).exists()

    def test_json_mode_keeps_stdout_machine_readable(self, tmp_path):
        code, out, err = _run(
            ["--role", "plugin", "--json"], home=tmp_path, stdin="piper\n", isatty=True
        )
        assert code == 0, err
        assert json.loads(out)["status"] == "completed"
        assert "Voice provider" in err


class TestSafety:
    def test_noninteractive_without_any_answer_touches_nothing(self, tmp_path):
        code, _, err = _run(["--role", "plugin", "--non-interactive"], home=tmp_path)
        assert code == 0, err
        assert not _config(tmp_path).exists()
        assert _marker(tmp_path)["voice"] == "edge"  # effective launcher default

    def test_value_with_double_quote_is_refused_before_any_write(self, tmp_path):
        env = _env(tmp_path, HERDR_ONBOARDING_VOICE='bad"voice')
        code, _, err = _run(["--role", "plugin", "--non-interactive"], home=tmp_path, env=env)
        assert code == 40
        assert "double quotes" in err
        assert not _config(tmp_path).exists()

    def test_crash_mid_write_leaves_the_previous_file_intact(self, tmp_path):
        path = _config(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text('TTS_PROVIDER="edge"\n')

        def boom(src, dst):
            raise OSError("simulated crash")

        with pytest.raises(OSError):
            voice_mod.upsert_config_value(path, "TTS_PROVIDER", "piper", _replace=boom)
        assert path.read_text() == 'TTS_PROVIDER="edge"\n'
        assert [p.name for p in path.parent.iterdir() if p.suffix == ".tmp"] == []

    def test_new_file_is_mode_600_and_existing_mode_is_kept(self, tmp_path):
        path = _config(tmp_path)
        voice_mod.upsert_config_value(path, "TTS_PROVIDER", "edge")
        assert path.stat().st_mode & 0o777 == 0o600
        path.chmod(0o640)
        voice_mod.upsert_config_value(path, "TTS_PROVIDER", "piper")
        assert path.stat().st_mode & 0o777 == 0o640

    def test_last_assignment_wins_like_the_launcher_source(self, tmp_path):
        path = tmp_path / "c.env"
        path.write_text('TTS_PROVIDER="edge"\nexport TTS_PROVIDER=piper\n')
        assert voice_mod.read_config_value(path, "TTS_PROVIDER") == "piper"


class TestRegistry:
    def test_voice_step_is_offered_to_both_roles(self):
        for role in ("plugin", "brain"):
            assert any(isinstance(s, VoiceStep) for s in steps_for_role(role))
