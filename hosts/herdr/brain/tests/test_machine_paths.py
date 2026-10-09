"""Machine-path independence of the brain package source (AT-11 M2).

The installed brain must run on any machine: defaults derive from the
installation location (sibling rule) instead of embedding the maintainer's
home layout. Covered here:

- the default tts-plugin home derives from this module tree's real
  location by ascending to the monorepo marker ``hosts/herdr/tts-plugin``
  (the same marker rule as ``tools/herdr_onboarding/resolve.py``, mirrored
  locally because design Decision 1 forbids runtime dependencies from the
  installed brain package);
- an explicit ``HERDR_TTS_HOME`` always wins over the derived default;
- an undeducible installation (standalone brain without the marker) fails
  loudly with an actionable message naming ``HERDR_TTS_HOME``;
- the config module source carries no literal machine path;
- the missing-GLM_API_KEY guidance is machine-agnostic.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from herdr_brain import config
from herdr_brain.config import Settings, load_settings
from herdr_brain.llm import BrainLLMError, build_openai_client
from tests.conftest import SETTINGS_KWARGS

# tests/ -> brain/ -> herdr/ -> hosts/ -> monorepo root
REPO_ROOT = Path(__file__).resolve().parents[4]
MONOREPO_MARKER = Path("hosts") / "herdr" / "tts-plugin"


class TestDefaultTtsHome:
    def test_source_layout_yields_the_real_sibling(self) -> None:
        """Without a start override, this checkout's own tts-plugin wins."""
        assert config._default_tts_home() == REPO_ROOT / MONOREPO_MARKER

    def test_hermetic_layout_yields_its_own_sibling(self, tmp_path: Path) -> None:
        """Any root works: a replica checkout derives ITS OWN marker
        directory, so no hardcoded machine path can satisfy this."""
        root = tmp_path / "anywhere"
        tts = root / MONOREPO_MARKER
        tts.mkdir(parents=True)
        package = root / "hosts" / "herdr" / "brain" / "src" / "herdr_brain"
        package.mkdir(parents=True)
        assert config._default_tts_home(start=package) == tts

    def test_explicit_env_wins_over_derived_default(self, tmp_path: Path) -> None:
        cfg = load_settings(env={"HERDR_TTS_HOME": str(tmp_path)})
        assert cfg.tts_home == tmp_path

    def test_undeducible_layout_raises_actionable_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="HERDR_TTS_HOME"):
            config._default_tts_home(start=tmp_path)

    def test_load_settings_without_marker_fails_loudly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "__file__", str(tmp_path / "standalone" / "config.py"))
        with pytest.raises(ValueError, match="HERDR_TTS_HOME"):
            load_settings(env={})


class TestNoLiteralMachinePathInConfig:
    def test_machine_specific_constant_is_gone(self) -> None:
        assert not hasattr(config, "DEFAULT_TTS_HOME")

    def test_module_source_carries_no_machine_path(self) -> None:
        source = inspect.getsource(config)
        for literal in ("~/Code", "/home/", ".dotfiles", "linuxbrew", "tail2640fd"):
            assert literal not in source


class TestMissingApiKeyGuidance:
    def test_error_message_is_machine_agnostic(self) -> None:
        settings = Settings(**{**SETTINGS_KWARGS, "glm_api_key": None})
        with pytest.raises(BrainLLMError) as excinfo:
            build_openai_client(settings)
        message = str(excinfo.value)
        assert "GLM_API_KEY" in message
        assert "dotfiles" not in message.lower()
        assert "~" not in message
