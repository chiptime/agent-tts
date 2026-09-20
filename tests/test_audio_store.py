"""Tests for the rendered-audio store convention (agent_tts.audio_store)."""

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from agent_tts import audio_store

# Arbitrary fixed epoch; dates are derived from it, never hardcoded.
NOW = 1_790_000_000.0


def _d(delta_days: int) -> str:
    """Date-partition name relative to NOW (negative = past)."""
    return (date.fromtimestamp(NOW) + timedelta(days=delta_days)).isoformat()


def _make_partition(root, name: str):
    partition = root / name
    partition.mkdir(parents=True, exist_ok=True)
    (partition / "1-panetest.mp3").write_bytes(b"x")
    return partition


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirects the audio store to tmp_path with default retention."""
    monkeypatch.setenv("AGENT_TTS_AUDIO_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_TTS_AUDIO_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("TTS_AUDIO_RETENTION_DAYS", raising=False)
    return tmp_path


class TestRetentionDays:
    def test_default_is_seven(self, monkeypatch):
        monkeypatch.delenv("AGENT_TTS_AUDIO_RETENTION_DAYS", raising=False)
        monkeypatch.delenv("TTS_AUDIO_RETENTION_DAYS", raising=False)
        assert audio_store.retention_days() == 7

    def test_agent_tts_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "14")
        monkeypatch.delenv("TTS_AUDIO_RETENTION_DAYS", raising=False)
        assert audio_store.retention_days() == 14

    def test_legacy_env_fallback(self, monkeypatch):
        monkeypatch.delenv("AGENT_TTS_AUDIO_RETENTION_DAYS", raising=False)
        monkeypatch.setenv("TTS_AUDIO_RETENTION_DAYS", "3")
        assert audio_store.retention_days() == 3

    def test_agent_tts_env_wins_over_legacy(self, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "14")
        monkeypatch.setenv("TTS_AUDIO_RETENTION_DAYS", "3")
        assert audio_store.retention_days() == 14

    def test_invalid_string_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "soon")
        monkeypatch.delenv("TTS_AUDIO_RETENTION_DAYS", raising=False)
        assert audio_store.retention_days() == 7

    def test_zero_disables_retention(self, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "0")
        assert audio_store.retention_days() == 0

    def test_negative_disables_retention(self, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "-3")
        assert audio_store.retention_days() == 0


class TestStorePath:
    def test_creates_date_subdir_with_epoch_prefixed_name(self, store):
        path = audio_store.store_path("p0", now=NOW)
        date_dir = store / _d(0)
        assert date_dir.is_dir()
        assert path == str(date_dir / f"{int(NOW)}-p0.mp3")

    def test_sanitizes_unsafe_pane_characters(self, store):
        path = audio_store.store_path("p0:main wing/x", now=NOW)
        assert os.path.basename(path) == f"{int(NOW)}-p0_main_wing_x.mp3"

    def test_empty_pane_becomes_dash(self, store):
        path = audio_store.store_path("", now=NOW)
        assert os.path.basename(path) == f"{int(NOW)}--.mp3"

    def test_suffix_passthrough(self, store):
        path = audio_store.store_path("p0", suffix=".wav", now=NOW)
        assert path.endswith(".wav")


class TestPruneExpired:
    def test_removes_only_strictly_expired_partitions(self, store):
        _make_partition(store, _d(-30))
        _make_partition(store, _d(-8))
        boundary = _make_partition(store, _d(-7))  # exactly retention_days ago: kept
        today = _make_partition(store, _d(0))
        future = _make_partition(store, _d(1))

        removed = audio_store.prune_expired(now=NOW)

        assert removed == 2
        assert not os.path.exists(store / _d(-30))
        assert not os.path.exists(store / _d(-8))
        assert boundary.is_dir()
        assert today.is_dir()
        assert future.is_dir()

    def test_skips_malformed_names_and_plain_files(self, store):
        _make_partition(store, _d(-30))
        malformed = _make_partition(store, "not-a-date")
        plain_file = store / "2020-01-01"
        plain_file.write_text("not a directory")

        removed = audio_store.prune_expired(now=NOW)

        assert removed == 1
        assert malformed.is_dir()
        assert plain_file.exists()

    def test_disabled_retention_is_a_noop(self, store, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "0")
        old = _make_partition(store, _d(-30))

        assert audio_store.prune_expired(now=NOW) == 0
        assert old.is_dir()

    def test_missing_audio_dir_is_a_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_DIR", str(tmp_path / "missing"))
        assert audio_store.prune_expired(now=NOW) == 0

    def test_fail_open_when_rmtree_raises(self, store, monkeypatch):
        _make_partition(store, _d(-30))

        def boom(path, ignore_errors=False):
            raise OSError("permission denied")

        monkeypatch.setattr(audio_store.shutil, "rmtree", boom)
        assert audio_store.prune_expired(now=NOW) == 0


def test_cli_main_runs_retention_sweep_once(tmp_path, monkeypatch):
    from agent_tts import cli

    mock_prune = MagicMock(return_value=0)
    # cli lazy-imports inside main(), so patch where it looks the name up.
    monkeypatch.setattr("agent_tts.audio_store.prune_expired", mock_prune)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli.py", "hola", "--provider", "local", "--no-play", "--output", str(tmp_path / "o.mp3")],
    )

    cli.main()

    mock_prune.assert_called_once()
