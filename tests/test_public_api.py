"""Tests for the public package surface (agent_tts.__init__ exports)."""

import re
from pathlib import Path

import agent_tts


def test_all_names_resolve_to_real_attributes():
    missing = [name for name in agent_tts.__all__ if not hasattr(agent_tts, name)]
    assert missing == []


def test_host_facing_surface_is_exported():
    for name in ("main", "audio_store", "audio_duration", "provider_names", "provider_voices", "ipc_reply_json"):
        assert name in agent_tts.__all__
    # The audio_store module surface hosts rely on.
    for attr in ("prune_expired", "store_path", "audio_dir", "retention_days", "audio_duration"):
        assert hasattr(agent_tts.audio_store, attr)


def test_main_is_importable_from_the_package_root():
    from agent_tts import main

    from agent_tts.cli import main as cli_main

    assert main is cli_main


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    match = re.search(
        r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, "pyproject.toml must declare a project version"
    assert agent_tts.__version__ == match.group(1)
