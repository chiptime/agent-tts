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
    def test_default_is_zero_opt_in(self, monkeypatch):
        monkeypatch.delenv("AGENT_TTS_AUDIO_RETENTION_DAYS", raising=False)
        monkeypatch.delenv("TTS_AUDIO_RETENTION_DAYS", raising=False)
        assert audio_store.retention_days() == 0

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
        assert audio_store.retention_days() == 0

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


class TestSelectWindow:
    def _turn(self, store, day, filename):
        partition = store / day
        partition.mkdir(parents=True, exist_ok=True)
        path = partition / filename
        path.write_bytes(b"audio")
        return path

    def test_exact_date_selects_only_that_partition(self, store):
        path = self._turn(store, _d(-1), "1-pane-main.mp3")
        self._turn(store, _d(0), "2-other.mp3")

        assert audio_store.select_window(date=_d(-1), now=NOW) == [
            {"epoch": 1, "pane": "pane-main", "path": str(path), "date": _d(-1)}
        ]

    def test_since_days_includes_boundary_and_today_not_future(self, store):
        self._turn(store, _d(-3), "1-too-old.mp3")
        boundary = self._turn(store, _d(-2), "2-boundary.mp3")
        middle = self._turn(store, _d(-1), "3-middle.mp3")
        today = self._turn(store, _d(0), "4-today.mp3")
        self._turn(store, _d(1), "5-future.mp3")

        selected = audio_store.select_window(since_days=3, now=NOW)

        assert [item["path"] for item in selected] == [
            str(boundary), str(middle), str(today)
        ]

    def test_default_window_and_one_day_select_today(self, store, monkeypatch):
        self._turn(store, _d(-1), "1-yesterday.mp3")
        today = self._turn(store, _d(0), "2-today.mp3")
        monkeypatch.setattr(audio_store.time, "time", lambda: NOW)

        assert [item["path"] for item in audio_store.select_window()] == [str(today)]
        assert audio_store.select_window(since_days=1, now=NOW) == audio_store.select_window()

    def test_date_takes_precedence_over_since_days(self, store):
        selected_day = self._turn(store, _d(-10), "1-selected.mp3")
        self._turn(store, _d(0), "2-today.mp3")

        assert [
            item["path"]
            for item in audio_store.select_window(date=_d(-10), since_days=0, now=NOW)
        ] == [str(selected_day)]

    def test_orders_numerically_by_epoch_across_partitions(self, store):
        last = self._turn(store, _d(-1), "100-pane.mp3")
        middle = self._turn(store, _d(0), "10-pane.mp3")
        first = self._turn(store, _d(0), "2-pane.mp3")

        assert [
            item["path"] for item in audio_store.select_window(since_days=2, now=NOW)
        ] == [str(first), str(middle), str(last)]

    def test_deduplicates_repeated_paths_not_epochs(self, store, monkeypatch):
        first = self._turn(store, _d(0), "1-pane.mp3")
        second = self._turn(store, _d(0), "1-other.wav")
        scandir = audio_store.os.scandir

        def repeated_entries(path):
            with scandir(path) as entries:
                items = list(entries)
            context = MagicMock()
            context.__enter__.return_value = iter(items + items)
            return context

        monkeypatch.setattr(audio_store.os, "scandir", repeated_entries)

        selected = audio_store.select_window(now=NOW)

        assert len(selected) == 2
        assert {item["path"] for item in selected} == {str(first), str(second)}

    def test_selects_mp3_and_wav_without_reading_audio(self, store):
        mp3 = self._turn(store, _d(0), "1-pane_mp3.mp3")
        wav = self._turn(store, _d(0), "2-pane_wav.wav")

        assert [item["path"] for item in audio_store.select_window(now=NOW)] == [
            str(mp3), str(wav)
        ]

    def test_already_pruned_partition_is_empty(self, store, monkeypatch):
        self._turn(store, _d(-30), "1-old.mp3")
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "7")
        assert audio_store.prune_expired(now=NOW) == 1

        assert audio_store.select_window(date=_d(-30), now=NOW) == []

    def test_absent_and_empty_partitions_are_empty(self, store):
        assert audio_store.select_window(date=_d(-1), now=NOW) == []
        (store / _d(0)).mkdir()
        assert audio_store.select_window(now=NOW) == []

    def test_absent_window_does_not_create_directories(self, store, monkeypatch):
        missing_root = store / "missing"
        monkeypatch.setenv("AGENT_TTS_AUDIO_DIR", str(missing_root))

        assert audio_store.select_window(date=_d(0), now=NOW) == []
        assert audio_store.select_window(since_days=7, now=NOW) == []
        assert not missing_root.exists()
        monkeypatch.setenv("AGENT_TTS_AUDIO_DIR", str(store))
        assert audio_store.select_window(date=_d(0), now=NOW) == []
        assert list(store.iterdir()) == []

    def test_selection_never_writes_or_prunes_existing_store(self, store, monkeypatch):
        old = self._turn(store, _d(-30), "1-old.mp3")
        before = old.stat()
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "1")

        def forbidden(*args, **kwargs):
            pytest.fail("selection must not create directories, store paths, or prune")

        monkeypatch.setattr(audio_store.os, "makedirs", forbidden)
        monkeypatch.setattr(audio_store, "store_path", forbidden)
        monkeypatch.setattr(audio_store, "prune_expired", forbidden)

        assert [
            item["path"] for item in audio_store.select_window(date=_d(-30), now=NOW)
        ] == [str(old)]
        assert old.read_bytes() == b"audio"
        assert old.stat().st_mtime_ns == before.st_mtime_ns
        assert list(store.iterdir()) == [old.parent]
        assert list(old.parent.iterdir()) == [old]

    def test_skips_foreign_partitions_and_malformed_turns(self, store):
        self._turn(store, "not-a-date", "1-pane.mp3")
        self._turn(store, "2026-99-99", "1-pane.mp3")
        valid = self._turn(store, _d(0), "3--.wav")
        self._turn(store, _d(0), "no-epoch.mp3")
        self._turn(store, _d(0), "2-.mp3")
        self._turn(store, _d(0), "4-pane")
        (valid.parent / "5-directory.mp3").mkdir()
        (store / _d(-1)).write_bytes(b"not a partition")

        assert audio_store.select_window(since_days=3, now=NOW) == [
            {"epoch": 3, "pane": "-", "path": str(valid), "date": _d(0)}
        ]

    def test_partition_pruned_during_scan_is_skipped(self, store, monkeypatch):
        missing = self._turn(store, _d(-1), "1-old.mp3").parent
        kept = self._turn(store, _d(0), "2-kept.mp3")
        scandir = audio_store.os.scandir

        def racing_scandir(path):
            if os.fspath(path) == str(missing):
                raise FileNotFoundError("partition was pruned")
            return scandir(path)

        monkeypatch.setattr(audio_store.os, "scandir", racing_scandir)

        assert [
            item["path"] for item in audio_store.select_window(since_days=2, now=NOW)
        ] == [str(kept)]

    @pytest.mark.parametrize("days", [0, -1, 1.5, "2", True])
    def test_rejects_non_positive_or_non_integer_since_days(self, store, days):
        with pytest.raises(ValueError):
            audio_store.select_window(since_days=days, now=NOW)

    @pytest.mark.parametrize("day", ["2026-1-01", "2026-99-99", "../2026-01-01"])
    def test_rejects_invalid_date(self, store, day):
        with pytest.raises(ValueError):
            audio_store.select_window(date=day, now=NOW)


class TestPruneExpired:
    def test_removes_only_strictly_expired_partitions(self, store, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "7")
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

    def test_skips_malformed_names_and_plain_files(self, store, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_AUDIO_RETENTION_DAYS", "7")
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
    # Vía única: the speak path delegates to the daemon; the sweep test
    # only cares about startup, so the delegation boundary is faked (and
    # must never touch the real system channel from the test suite).
    monkeypatch.setattr("agent_tts.daemon.delegate_play", MagicMock(return_value="status=done"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli.py", "hola", "--provider", "local", "--no-play", "--output", str(tmp_path / "o.mp3")],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0

    mock_prune.assert_called_once()


class TestAudioDuration:
    """audio_duration decodes a file and returns its length in seconds."""

    def _write_wav(self, root, name: str, samples: int, sample_rate: int = 8000) -> str:
        pcm = b"\x00\x00" * samples  # mono 16-bit silence
        path = root / name
        path.write_bytes(audio_store._pcm_to_wav(pcm, sample_rate, 1))
        return str(path)

    def test_one_second_wav(self, tmp_path):
        path = self._write_wav(tmp_path, "one-second.wav", samples=8000)
        assert audio_store.audio_duration(path) == pytest.approx(1.0)

    def test_fractional_duration(self, tmp_path):
        path = self._write_wav(tmp_path, "half.wav", samples=4000)
        assert audio_store.audio_duration(path) == pytest.approx(0.5)

    def test_missing_file_raises_os_error(self, tmp_path):
        with pytest.raises(OSError):
            audio_store.audio_duration(str(tmp_path / "missing.wav"))

    def test_undecodable_file_raises(self, tmp_path):
        path = tmp_path / "garbage.wav"
        path.write_bytes(b"not audio at all")
        with pytest.raises(Exception):
            audio_store.audio_duration(str(path))


def test_cli_probe_prints_duration_as_plain_float(tmp_path, monkeypatch, capsys):
    from agent_tts import cli

    pcm = b"\x00\x00" * 8000  # one second of mono 16-bit silence at 8 kHz
    wav = tmp_path / "probe.wav"
    wav.write_bytes(audio_store._pcm_to_wav(pcm, 8000, 1))

    monkeypatch.setattr(sys, "argv", ["cli.py", "--probe", str(wav)])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "1.000"


def test_cli_probe_missing_file_fails_clean(tmp_path, monkeypatch, capsys):
    from agent_tts import cli

    monkeypatch.setattr(sys, "argv", ["cli.py", "--probe", str(tmp_path / "nope.wav")])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert "Error" in capsys.readouterr().err
