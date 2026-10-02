"""V1 for ``tools/herdr_onboarding/resolve.py`` and the brain port knob
(AT-11 task 2.1, design Decision 4).

Covers the four resolution orders plus the static parity proof that both
bash launchers carry the identical ``herdr_resolve_*`` mirror block.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_onboarding import resolve as res  # noqa: E402

from herdr_brain.config import DEFAULT_BRAIN_PORT, load_settings  # noqa: E402

PLUGIN_LAUNCHER = REPO_ROOT / "hosts" / "herdr" / "tts-plugin" / "bin" / "herdr-tts"
BRAIN_LAUNCHER = REPO_ROOT / "hosts" / "herdr" / "brain" / "bin" / "herdr-brain"
BLOCK_START = "# >>> herdr portable resolvers"
BLOCK_END = "# <<< herdr portable resolvers <<<"


def _exec_marker(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def _brew_runner(prefix: Path, calls: list) -> "subprocess.CompletedProcess[str]":
    def runner(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=str(prefix), stderr="")
    return runner


def _refusing_runner(argv, **kwargs):
    raise AssertionError(f"brew probe must not run without brew on PATH, got {argv}")


def _launcher_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(BLOCK_START)
    end = text.index(BLOCK_END, start) + len(BLOCK_END)
    return text[start:end]


class TestResolveRoot:
    def test_env_override_wins(self):
        assert res.resolve_root(env={"HERDR_PLUGIN_ROOT": "/opt/keg"}) == Path("/opt/keg")

    @pytest.mark.parametrize("depth", [1, 2, 4])
    def test_ascends_to_monorepo_marker(self, tmp_path, depth):
        root = tmp_path / "checkout"
        (root / "hosts" / "herdr" / "tts-plugin").mkdir(parents=True)
        node = root
        for i in range(depth):
            node = node / f"level{i}"
        node.mkdir(parents=True)
        assert res.resolve_root(env={}, start=node) == root

    def test_beyond_six_levels_fails_actionably(self, tmp_path):
        root = tmp_path / "checkout"
        (root / "hosts" / "herdr" / "tts-plugin").mkdir(parents=True)
        node = root
        for i in range(7):
            node = node / f"level{i}"
        node.mkdir(parents=True)
        with pytest.raises(res.ResolutionError) as exc:
            res.resolve_root(env={}, start=node)
        assert "HERDR_PLUGIN_ROOT" in str(exc.value)

    def test_default_anchor_is_the_real_monorepo_root(self):
        # resolve.py lives at <root>/tools/herdr_onboarding: its own
        # resolved location ascends to the real monorepo root.
        assert res.resolve_root(env={}) == REPO_ROOT


class TestResolveTtsHome:
    def test_explicit_valid_home_wins_over_sibling(self, tmp_path):
        # THE OQ-2 assertion (Engram #9681): an exported, valid
        # HERDR_TTS_HOME is honoured even when a sibling tts-plugin
        # directory exists.
        brain = tmp_path / "hosts" / "herdr" / "brain"
        (brain / "bin").mkdir(parents=True)
        (tmp_path / "hosts" / "herdr" / "tts-plugin").mkdir()
        explicit = tmp_path / "split-deployment" / "tts-plugin"
        explicit.mkdir(parents=True)
        got = res.resolve_tts_home(brain_root=brain, env={"HERDR_TTS_HOME": str(explicit)})
        assert got == explicit

    def test_unset_derives_the_sibling(self, tmp_path):
        brain = tmp_path / "hosts" / "herdr" / "brain"
        brain.mkdir(parents=True)
        sibling = tmp_path / "hosts" / "herdr" / "tts-plugin"
        sibling.mkdir()
        assert res.resolve_tts_home(brain_root=brain, env={}) == sibling

    def test_empty_string_env_counts_as_unset(self, tmp_path):
        brain = tmp_path / "hosts" / "herdr" / "brain"
        brain.mkdir(parents=True)
        sibling = tmp_path / "hosts" / "herdr" / "tts-plugin"
        sibling.mkdir()
        assert res.resolve_tts_home(brain_root=brain, env={"HERDR_TTS_HOME": ""}) == sibling

    def test_unusable_explicit_falls_back_to_sibling(self, tmp_path):
        brain = tmp_path / "hosts" / "herdr" / "brain"
        brain.mkdir(parents=True)
        sibling = tmp_path / "hosts" / "herdr" / "tts-plugin"
        sibling.mkdir()
        got = res.resolve_tts_home(
            brain_root=brain, env={"HERDR_TTS_HOME": str(tmp_path / "no-such-dir")}
        )
        assert got == sibling

    def test_neither_usable_errors_naming_both(self, tmp_path):
        brain = tmp_path / "solo" / "brain"
        brain.mkdir(parents=True)
        explicit = tmp_path / "no-such-dir"
        with pytest.raises(res.ResolutionError) as exc:
            res.resolve_tts_home(brain_root=brain, env={"HERDR_TTS_HOME": str(explicit)})
        message = str(exc.value)
        assert str(explicit) in message
        assert "tts-plugin" in message


class TestResolveHerdrBin:
    def test_step1_herdr_bin_env_wins(self):
        assert res.resolve_herdr_bin(env={"HERDR_BIN": "/x/herdr"}) == "/x/herdr"

    def test_step2_path_beats_homebrew_prefix(self, tmp_path):
        bindir = tmp_path / "pbin"
        bindir.mkdir()
        _exec_marker(bindir, "herdr")
        brew = tmp_path / "brew"
        (brew / "bin").mkdir(parents=True)
        _exec_marker(brew / "bin", "herdr")
        got = res.resolve_herdr_bin(env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "HOMEBREW_PREFIX": str(brew),
        }, runner=_refusing_runner)
        assert got == str(bindir / "herdr")

    def test_step3_homebrew_prefix(self, tmp_path):
        brew = tmp_path / "brew"
        (brew / "bin").mkdir(parents=True)
        _exec_marker(brew / "bin", "herdr")
        got = res.resolve_herdr_bin(env={
            "PATH": "/usr/bin:/bin",
            "HOMEBREW_PREFIX": str(brew),
        }, runner=_refusing_runner)
        assert got == str(brew / "bin" / "herdr")

    def test_step4_brew_prefix_probe(self, tmp_path):
        brewbin = tmp_path / "brewbin"
        brewbin.mkdir()
        _exec_marker(brewbin, "brew")
        brew2 = tmp_path / "brew2"
        (brew2 / "bin").mkdir(parents=True)
        _exec_marker(brew2 / "bin", "herdr")
        calls: list = []
        got = res.resolve_herdr_bin(
            env={"PATH": f"{brewbin}:/usr/bin:/bin"},
            runner=_brew_runner(brew2, calls),
        )
        assert got == str(brew2 / "bin" / "herdr")
        assert calls == [["brew", "--prefix"]]

    def test_step5_home_local_fallback(self, tmp_path):
        home = tmp_path / "home"
        (home / ".local" / "bin").mkdir(parents=True)
        _exec_marker(home / ".local" / "bin", "herdr")
        got = res.resolve_herdr_bin(
            env={"PATH": "/usr/bin:/bin", "HOME": str(home)}, runner=_refusing_runner
        )
        assert got == str(home / ".local" / "bin" / "herdr")

    def test_step6_bare_herdr_last_resort(self, tmp_path):
        got = res.resolve_herdr_bin(
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "empty-home")},
            runner=_refusing_runner,
        )
        assert got == "herdr"


class TestResolvePort:
    def test_env_wins_over_persisted_config(self, tmp_path):
        cfg = tmp_path / "config.env"
        cfg.write_text('HERDR_BRAIN_PORT="9005"\n')
        assert res.resolve_port(env={"HERDR_BRAIN_PORT": "9100"}, config_file=cfg) == 9100

    def test_invalid_env_fails_closed(self):
        with pytest.raises(res.ResolutionError):
            res.resolve_port(env={"HERDR_BRAIN_PORT": "not-a-port"})

    def test_out_of_range_env_fails_closed(self):
        with pytest.raises(res.ResolutionError):
            res.resolve_port(env={"HERDR_BRAIN_PORT": "70000"})

    def test_persisted_config_value_with_quotes_and_noise(self, tmp_path):
        cfg = tmp_path / "config.env"
        cfg.write_text('# managed\nOTHER_KEY="1"\nHERDR_BRAIN_PORT="9005"\n')
        assert res.resolve_port(env={}, config_file=cfg) == 9005

    def test_config_without_the_key_defaults(self, tmp_path):
        cfg = tmp_path / "config.env"
        cfg.write_text('OTHER_KEY="1"\n')
        assert res.resolve_port(env={}, config_file=cfg) == DEFAULT_BRAIN_PORT

    def test_no_config_file_defaults(self, tmp_path):
        assert res.resolve_port(env={}, config_file=tmp_path / "absent.env") == DEFAULT_BRAIN_PORT

    def test_xdg_derived_config_path(self, tmp_path):
        cfg_dir = tmp_path / "xdg" / "herdr-brain"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.env").write_text("HERDR_BRAIN_PORT=9006\n")
        assert res.resolve_port(env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")}) == 9006

    def test_home_derived_config_path(self, tmp_path):
        cfg_dir = tmp_path / "home" / ".config" / "herdr-brain"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.env").write_text("HERDR_BRAIN_PORT=9007\n")
        assert res.resolve_port(env={"HOME": str(tmp_path / "home")}) == 9007

    def test_invalid_persisted_value_fails_closed(self, tmp_path):
        cfg = tmp_path / "config.env"
        cfg.write_text("HERDR_BRAIN_PORT=garbage\n")
        with pytest.raises(res.ResolutionError):
            res.resolve_port(env={}, config_file=cfg)


class TestBashMirrorParity:
    """Static V1: both launchers ship the identical resolver block and the
    task-2.1 mandates hold in ``bin/herdr-brain``."""

    FUNCTIONS = (
        "herdr_resolve_root",
        "herdr_resolve_tts_home",
        "herdr_resolve_bin",
        "herdr_resolve_port",
    )

    def test_blocks_are_identical_in_both_launchers(self):
        plugin_block = _launcher_block(PLUGIN_LAUNCHER)
        brain_block = _launcher_block(BRAIN_LAUNCHER)
        assert plugin_block == brain_block
        for name in self.FUNCTIONS:
            assert f"{name}()" in plugin_block

    def test_launchers_wire_the_root_resolver(self):
        assert 'PLUGIN_ROOT="$(herdr_resolve_root herdr-tts)"' in PLUGIN_LAUNCHER.read_text(
            encoding="utf-8"
        )
        assert 'REPO_DIR="$(herdr_resolve_root herdr-brain)"' in BRAIN_LAUNCHER.read_text(
            encoding="utf-8"
        )

    def test_brain_launcher_exports_no_hardcoded_tts_default(self):
        assert "HERDR_TTS_HOME:-$HOME" not in BRAIN_LAUNCHER.read_text(encoding="utf-8")

    def test_brain_launcher_has_no_literal_brew_prefix(self):
        assert "/home/linuxbrew" not in BRAIN_LAUNCHER.read_text(encoding="utf-8")


class TestBrainPortKnob:
    def test_env_port_reaches_settings(self):
        assert load_settings({"HERDR_BRAIN_PORT": "9100"}).brain_port == 9100

    def test_default_port(self, tmp_path):
        assert load_settings({"HOME": str(tmp_path)}).brain_port == DEFAULT_BRAIN_PORT

    def test_persisted_config_reaches_settings(self, tmp_path):
        cfg_dir = tmp_path / ".config" / "herdr-brain"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.env").write_text('HERDR_BRAIN_PORT="9005"\n')
        assert load_settings({"HOME": str(tmp_path)}).brain_port == 9005

    def test_invalid_env_port_fails_closed(self):
        with pytest.raises(ValueError):
            load_settings({"HERDR_BRAIN_PORT": "nope"})
