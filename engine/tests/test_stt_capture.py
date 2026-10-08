"""Microphone capture tests: pure endpointing, WAV trim, PowerShell seam.

Everything here is fake-first: synthetic PCM (alternating ±amplitude gives
an exact RMS), canned runner results. No real microphone, powershell.exe,
network, or audio device is ever touched.
"""

from __future__ import annotations

import base64
import io
import struct
import wave

import pytest

import agent_tts.stt.capture as cap_mod

from agent_tts.stt.capture import (
    FRAME_MS,
    MAX_WAV_BYTES,
    CaptureConfig,
    CaptureOversizeError,
    CaptureUnavailableError,
    EmptyCaptureError,
    PowerShellCapture,
    analyze_pcm,
    trim_trailing_silence,
)
from agent_tts.stt.transcriber import InvalidConfigError, SttError


def tone_pcm(seconds: float, amplitude: int, rate: int = 16000) -> bytes:
    """Deterministic 16-bit mono PCM whose per-frame RMS equals amplitude."""
    n = int(seconds * rate)
    samples = [amplitude, -amplitude] * (n // 2 + 1)
    return struct.pack("<%dh" % n, *samples[:n])


def make_wav(pcm: bytes, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def b64_wav(wav: bytes) -> str:
    return base64.b64encode(wav).decode("ascii")


def fake_runner(payload: str):
    calls: list = []

    def run(argv, timeout):
        calls.append((list(argv), timeout))
        return payload

    run.calls = calls
    return run


LOUD, SILENT = 8000, 0


class TestCaptureConfig:
    def test_defaults_are_the_documented_v1_contract(self):
        cfg = CaptureConfig()
        assert cfg.sample_rate == 16000
        assert cfg.channels == 1
        assert cfg.bits == 16
        assert 1.0 <= cfg.silence_threshold_rms <= 32768.0  # sensible 16-bit range
        assert cfg.silence_seconds == 1.2
        assert cfg.max_seconds == 30.0
        assert cfg.device == "default"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"sample_rate": 100},
            {"sample_rate": 100000},
            {"channels": 2},
            {"bits": 8},
            {"silence_threshold_rms": 0},
            {"silence_threshold_rms": 40000.0},
            {"silence_seconds": 0.0},
            {"silence_seconds": 60.0},
            {"max_seconds": 0.1},
            {"max_seconds": 600.0},
            {"silence_seconds": 5.0, "max_seconds": 5.0},  # never satisfiable
            {"device": "USB Mic 2"},
            {"device": ""},
        ],
    )
    def test_invalid_values_raise_typed_invalid_config(self, kwargs):
        with pytest.raises(InvalidConfigError):
            CaptureConfig(**kwargs)

    def test_non_default_device_is_rejected_honestly(self):
        with pytest.raises(InvalidConfigError, match="default capture device"):
            CaptureConfig(device="headset")


class TestAnalyzePcm:
    def test_loud_then_silence_ends_by_silence(self):
        cfg = CaptureConfig()
        analysis = analyze_pcm(tone_pcm(2.0, LOUD) + tone_pcm(2.5, SILENT), cfg)
        assert analysis.ended_by == "silence"
        assert analysis.speech_frames > 0
        assert analysis.trailing_silence_sec == pytest.approx(2.5, abs=0.1)
        assert analysis.speech_end_sec == pytest.approx(2.0, abs=0.1)
        assert analysis.duration_sec == pytest.approx(4.5, abs=0.01)

    def test_continuous_loud_ends_by_max_duration(self):
        analysis = analyze_pcm(tone_pcm(3.0, LOUD), CaptureConfig())
        assert analysis.ended_by == "max_duration"
        assert analysis.trailing_silence_sec == 0.0

    def test_all_silence_has_no_speech_frames(self):
        analysis = analyze_pcm(tone_pcm(2.0, SILENT), CaptureConfig())
        assert analysis.speech_frames == 0
        assert analysis.last_speech_frame == -1
        assert analysis.speech_end_sec == 0.0

    def test_sub_frame_input_yields_no_frames(self):
        analysis = analyze_pcm(b"\x00" * 100, CaptureConfig())  # < one 30ms frame
        assert analysis.frames == 0
        assert analysis.duration_sec < 0.01

    def test_threshold_respects_explicit_config(self):
        cfg = CaptureConfig(silence_threshold_rms=20000.0)
        analysis = analyze_pcm(tone_pcm(1.0, LOUD) + tone_pcm(1.0, SILENT), cfg)
        assert analysis.speech_frames == 0  # 8000 < 20000: all counts as silence


class TestTrimTrailingSilence:
    def test_loud_then_silence_is_trimmed_to_speech_end(self):
        cfg = CaptureConfig()
        wav = make_wav(tone_pcm(2.0, LOUD) + tone_pcm(2.5, SILENT))
        trimmed, stats = trim_trailing_silence(wav, cfg)
        assert stats["trimmed"] is True
        assert stats["kept_sec"] == pytest.approx(2.0, abs=0.1)
        assert stats["raw_duration_sec"] == pytest.approx(4.5, abs=0.01)
        assert stats["trailing_silence_sec"] == pytest.approx(2.5, abs=0.1)
        assert len(trimmed) < len(wav)
        with wave.open(io.BytesIO(trimmed)) as w:  # still a valid mono 16k WAV
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == 16000
            assert w.getnframes() == int(round(stats["kept_sec"] * 16000))

    def test_continuous_loud_is_returned_untrimmed(self):
        wav = make_wav(tone_pcm(1.5, LOUD))
        trimmed, stats = trim_trailing_silence(wav, CaptureConfig())
        assert stats["trimmed"] is False
        assert stats["trailing_silence_sec"] == 0.0
        assert trimmed == wav

    def test_all_silence_raises_typed_empty_capture(self):
        with pytest.raises(EmptyCaptureError) as exc:
            trim_trailing_silence(make_wav(tone_pcm(1.5, SILENT)), CaptureConfig())
        assert exc.value.kind == "empty_capture"
        assert exc.value.exit_code == 9

    def test_sub_frame_recording_is_typed_empty_capture(self):
        with pytest.raises(EmptyCaptureError):
            trim_trailing_silence(make_wav(b"\x00" * 100), CaptureConfig())

    def test_garbage_is_rejected_as_invalid_wav(self):
        with pytest.raises(SttError, match="RIFF|WAV"):
            trim_trailing_silence(b"not-a-wav-at-all" * 8, CaptureConfig())

    def test_truncated_riff_header_is_rejected(self):
        with pytest.raises(SttError):
            trim_trailing_silence(b"RIFF\x00\x00\x00\x00WAVEjunk", CaptureConfig())

    def test_non_16bit_wav_is_rejected(self):
        pcm8 = bytes([120] * 16000)  # 8-bit samples
        with pytest.raises(SttError, match="16"):
            trim_trailing_silence(make_wav(pcm8, width=1), CaptureConfig())

    def test_absurd_size_rejected_before_parse(self, monkeypatch):
        from agent_tts.stt import capture as cap

        monkeypatch.setattr(cap, "MAX_WAV_BYTES", 64)
        with pytest.raises(SttError, match="absurd|large"):
            trim_trailing_silence(make_wav(tone_pcm(0.5, LOUD)), CaptureConfig())


class TestCaptureScript:
    def test_script_is_single_line_and_quote_free(self):
        script = PowerShellCapture.script(CaptureConfig())
        assert '"' not in script  # WSL interop mangles double quotes (playback lesson)
        assert "\n" not in script

    def test_script_embeds_the_recording_contract(self):
        script = PowerShellCapture.script(CaptureConfig(max_seconds=12.5))
        assert "samplespersec 16000" in script
        assert "channels 1" in script
        assert "bitspersample 16" in script
        assert "Start-Sleep -Milliseconds 12500" in script
        assert "mciSendString" in script

    def test_argv_mirrors_the_playback_spawn_pattern(self):
        argv, timeout = PowerShellCapture.argv_and_timeout(CaptureConfig(max_seconds=5.0))
        assert argv[0] == "powershell.exe"
        assert argv[1:4] == ["-NoLogo", "-NoProfile", "-NonInteractive"]
        assert argv[4] == "-Command"
        assert timeout == pytest.approx(5.0 + 20.0)  # window + spawn/encode slack


class TestPowerShellCaptureRun:
    def test_happy_path_trims_and_reports_honest_max_duration(self):
        cfg = CaptureConfig(max_seconds=10.0)
        wav = make_wav(tone_pcm(2.0, LOUD) + tone_pcm(8.0, SILENT))
        runner = fake_runner(b64_wav(wav))
        result = PowerShellCapture().run(cfg, runner=runner)
        assert runner.calls[0][1] == pytest.approx(30.0)  # 10s window + 20s slack
        # v1 truth: the recorder has no live stop, so recording ran the full
        # window; ended_by says max_duration and stats carry the trim story.
        assert result.ended_by == "max_duration"
        assert result.stats["trimmed"] is True
        assert result.stats["evidence_ended_by"] == "silence"
        assert result.stats["raw_duration_sec"] == pytest.approx(10.0, abs=0.05)
        assert result.duration == pytest.approx(2.0, abs=0.1)
        assert result.wav_bytes[:4] == b"RIFF"
        assert len(result.wav_bytes) < len(wav)

    def test_runner_argv_carries_the_script(self):
        runner = fake_runner(b64_wav(make_wav(tone_pcm(1.0, LOUD))))
        PowerShellCapture().run(CaptureConfig(), runner=runner)
        argv = runner.calls[0][0]
        assert argv[-1] == PowerShellCapture.script(CaptureConfig())
        assert "-Command" in argv

    def test_garbage_stdout_is_typed_unavailable(self):
        runner = fake_runner("%%% not base64 %%%")
        with pytest.raises(CaptureUnavailableError) as exc:
            PowerShellCapture().run(CaptureConfig(), runner=runner)
        assert exc.value.kind == "capture_unavailable"
        assert exc.value.exit_code == 8

    def test_valid_b64_but_not_wav_is_typed_unavailable(self):
        runner = fake_runner(base64.b64encode(b"garbage-bytes" * 20).decode())
        with pytest.raises(CaptureUnavailableError, match="WAV"):
            PowerShellCapture().run(CaptureConfig(), runner=runner)

    def test_oversize_output_is_rejected_before_decode(self):
        huge = "A" * 200_000
        runner = fake_runner(huge)
        cfg = CaptureConfig(max_seconds=1.0, silence_seconds=0.5)  # ~64KB ceiling
        with pytest.raises(CaptureOversizeError) as exc:
            PowerShellCapture().run(cfg, runner=runner)
        assert exc.value.exit_code == 8

    def test_runner_crash_is_typed_unavailable(self):
        def boom(argv, timeout):
            raise RuntimeError("powershell died")

        with pytest.raises(CaptureUnavailableError, match="powershell died"):
            PowerShellCapture().run(CaptureConfig(), runner=boom)

    def test_runner_timeout_is_typed_unavailable(self):
        import subprocess

        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(cmd="powershell.exe", timeout=timeout)

        with pytest.raises(CaptureUnavailableError, match="timed out"):
            PowerShellCapture().run(CaptureConfig(), runner=slow)

    def test_all_silence_recording_is_typed_empty(self):
        runner = fake_runner(b64_wav(make_wav(tone_pcm(3.0, SILENT))))
        with pytest.raises(EmptyCaptureError):
            PowerShellCapture().run(CaptureConfig(max_seconds=3.0), runner=runner)

    def test_missing_powershell_is_typed_unavailable_without_spawning(
        self, monkeypatch
    ):
        from agent_tts.stt import capture as cap

        monkeypatch.setattr(cap.shutil, "which", lambda name: None)
        # also simulate a machine WITHOUT WSL interop files (this dev box has
        # the real /mnt/c powershell — the locator would legitimately find it)
        monkeypatch.setattr(cap.os.path, "isfile", lambda p: False)

        def must_not_spawn(argv, timeout):
            raise AssertionError("default runner must not spawn without powershell.exe")

        monkeypatch.setattr(cap.subprocess, "run", must_not_spawn)
        with pytest.raises(CaptureUnavailableError, match="powershell.exe"):
            PowerShellCapture().run(CaptureConfig())

    def test_default_runner_spawns_and_decodes_stdout(self, monkeypatch):
        from agent_tts.stt import capture as cap

        wav = make_wav(tone_pcm(1.0, LOUD))
        seen = {}

        def fake_run(argv, timeout=None, **kw):
            seen["argv"] = list(argv)
            seen["timeout"] = timeout
            return type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": b64_wav(wav),
                    "stderr": "",
                },
            )()

        monkeypatch.setattr(cap.shutil, "which", lambda name: "/mnt/c/powershell.exe")
        monkeypatch.setattr(cap.subprocess, "run", fake_run)
        result = PowerShellCapture().run(CaptureConfig(max_seconds=2.0))
        assert seen["argv"][0] == "/mnt/c/powershell.exe"  # resolved before dispatch
        assert seen["timeout"] == pytest.approx(22.0)
        assert result.duration == pytest.approx(1.0, abs=0.1)
        assert result.stats["trimmed"] is False  # 1s loud < 2s window, no tail
        assert result.wav_bytes == wav  # nothing to trim: byte-identical

    def test_default_runner_maps_nonzero_exit_to_stderr_tail(self, monkeypatch):
        from agent_tts.stt import capture as cap

        def fake_run(argv, timeout=None, **kw):
            return type(
                "R",
                (),
                {
                    "returncode": 3,
                    "stdout": "",
                    "stderr": "#< CLIXML\n<Objs J=\"1\">x</Objs>\nmci-error 2 record",
                },
            )()

        monkeypatch.setattr(cap.shutil, "which", lambda name: "/mnt/c/powershell.exe")
        monkeypatch.setattr(cap.subprocess, "run", fake_run)
        with pytest.raises(CaptureUnavailableError, match="mci-error 2 record"):
            PowerShellCapture().run(CaptureConfig())


class TestPowerShellLocator:
    """Regression (2026-10-08 U4 real test): WSL interop may have powershell.exe
    at the well-known /mnt/c path WITHOUT it being on PATH; the locator must
    fall back to those standard locations before giving up."""

    def test_falls_back_to_well_known_wsl_path(self, monkeypatch) -> None:
        monkeypatch.setattr(cap_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            cap_mod.os.path, "isfile",
            lambda p: p == "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        )
        assert cap_mod.PowerShellCapture._locate_powershell() == (
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        )

    def test_missing_everywhere_raises_capture_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr(cap_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(cap_mod.os.path, "isfile", lambda p: False)
        with pytest.raises(cap_mod.CaptureUnavailableError):
            cap_mod.PowerShellCapture._locate_powershell()

    def test_path_hit_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(cap_mod.shutil, "which", lambda name: "/usr/bin/powershell.exe")
        assert cap_mod.PowerShellCapture._locate_powershell() == "/usr/bin/powershell.exe"
