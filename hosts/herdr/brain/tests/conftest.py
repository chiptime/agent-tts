"""Shared fixtures for herdr-brain tests."""

from __future__ import annotations

import pytest

from herdr_brain.config import Settings

SETTINGS_KWARGS = dict(
    herdr_bin="herdr-fake",
    glm_api_key="test-key",
    glm_base_url="https://example.invalid/",
    glm_model="glm-5",
    tts_python="/venv/bin/python",
    tts_engine="/engine/tts_engine.py",
    tts_voice="elvira",
    tts_rate="+0%",
    tts_max_chars=300,
    tts_extra_args=(),
    tts_timeout_s=10,
    audio_dir="/tmp/herdr-brain-test-audio",
    prompt_timeout_ms=5_000,
    max_tool_rounds=4,
    screen_lines=40,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(**SETTINGS_KWARGS)


@pytest.fixture(autouse=True)
def hermetic_stores(tmp_path, monkeypatch):
    """Never let tests touch the real transcript stores on this machine."""
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "nonexistent-opencode.db"))
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path / "nonexistent-claude"))
