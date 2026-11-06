"""Local semantic turn-completion detector (PRD-05 FR-02 amended, D18).

Covers the pure Whisper log-mel preprocessing, the real CPU ONNX model on
generated audio, every fail-closed path, the never-raises contract and the
env-driven default-off settings. No downloads run inside pytest: the real
model is used only when it is already present at the configured path.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from herdr_brain import turn_detector as td
from herdr_brain.config import (
    DEFAULT_SMART_TURN_ENABLED,
    DEFAULT_SMART_TURN_MODEL,
    DEFAULT_SMART_TURN_THRESHOLD,
    load_settings,
)

_MODEL_PATH = td.default_model_path()
_BASE_ENV = {"HERDR_TTS_HOME": "/tmp/herdr-tts-surface"}


def _tone(seconds: float = 1.0, freq: float = 220.0, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * 16000), dtype=np.float32) / 16000.0
    return (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


# ── preprocessing ───────────────────────────────────────────────────────


class TestPreprocess:
    def test_shape_dtype_determinism_and_finiteness(self):
        audio = _tone(1.0)
        a = td.preprocess(audio)
        b = td.preprocess(audio)
        assert a.shape == (80, 800)
        assert a.dtype == np.float32
        assert np.array_equal(a, b)
        assert np.isfinite(a).all()

    def test_short_audio_is_zero_padded_at_the_front(self):
        audio = _tone(1.0)
        padded = td.preprocess(audio)
        explicit = td.preprocess(
            np.concatenate([np.zeros(td.WINDOW_SAMPLES - audio.size, np.float32), audio])
        )
        assert np.array_equal(padded, explicit)

    def test_long_audio_keeps_the_last_eight_seconds(self):
        audio = _tone(9.0)
        assert np.array_equal(
            td.preprocess(audio), td.preprocess(audio[-td.WINDOW_SAMPLES:])
        )

    @pytest.mark.parametrize(
        "bad",
        [
            np.zeros(0, dtype=np.float32),
            np.zeros((2, 16000), dtype=np.float32),
            np.array([0.0, np.nan, 0.1], dtype=np.float32),
        ],
        ids=["empty", "not-1d", "non-finite"],
    )
    def test_rejects_invalid_audio(self, bad):
        with pytest.raises(ValueError):
            td.preprocess(bad)

    def test_matches_faster_whisper_whisper_mel_reference_recipe(self):
        """Cross-implementation check, exact today.

        faster-whisper ships an independently maintained Whisper mel
        implementation. Configured with the pinned reference's window and
        framing (front-pad/truncate to 8 s, Hann-400, hop 160, center
        reflect, drop the final frame, Slaney mel, log10/clamp/normalise)
        it must match this port bit-for-bit. This does NOT prove parity with
        the reference's torch path (transformers/torch are absent here);
        see the module docstring.
        """
        fw = pytest.importorskip("faster_whisper.feature_extractor")
        FeatureExtractor = fw.FeatureExtractor

        def reference(audio: np.ndarray) -> np.ndarray:
            array = np.asarray(audio, dtype=np.float32).reshape(-1)
            if array.size > td.WINDOW_SAMPLES:
                array = array[-td.WINDOW_SAMPLES:]
            elif array.size < td.WINDOW_SAMPLES:
                array = np.pad(array, (td.WINDOW_SAMPLES - array.size, 0))
            window = np.hanning(td.N_FFT).astype(np.float32)
            stft = FeatureExtractor.stft(
                array,
                td.N_FFT,
                td.HOP_LENGTH,
                window=window,
                center=True,
                mode="reflect",
                return_complex=True,
            )
            magnitudes = np.abs(stft[..., :-1]) ** 2
            mel = FeatureExtractor.get_mel_filters(
                td.SAMPLE_RATE, td.N_FFT, td.N_MELS
            ).astype(np.float32) @ magnitudes
            log_spec = np.log10(np.clip(mel, a_min=1e-10, a_max=None))
            log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
            return ((log_spec + 4.0) / 4.0).astype(np.float32)

        audio = _tone(1.5)
        assert np.array_equal(td.preprocess(audio), reference(audio))


# ── real model inference (skipped when the weights are absent) ─────────


def _require_model() -> Path:
    if not _MODEL_PATH.is_file():
        pytest.skip(
            f"Smart Turn model not present at {_MODEL_PATH}; "
            "run scripts/fetch-smart-turn.sh"
        )
    return _MODEL_PATH


def test_real_model_returns_a_probability_in_range():
    detector = td.TurnDetector(model_path=_require_model())
    assert detector.ensure_loaded() is True
    probability, verdict, reason = detector.classify(_tone(1.5))
    assert reason is None
    assert isinstance(probability, float)
    assert 0.0 <= probability <= 1.0
    assert verdict in (td.VERDICT_COMPLETE, td.VERDICT_INCOMPLETE)


def test_real_model_is_deterministic_on_the_same_audio():
    detector = td.TurnDetector(model_path=_require_model())
    audio = _tone(2.0)
    first = detector.classify(audio)
    second = detector.classify(audio)
    assert first == second


# ── fail-closed paths (never raise into the call path) ─────────────────


class _InputSpec:
    def __init__(self, shape, name="input_features"):
        self.shape = shape
        self.name = name


class _FakeSession:
    """Minimal ONNX session double with a configurable output/failure."""

    def __init__(self, outputs=None, run_error=None, shapes=None):
        self._outputs = outputs if outputs is not None else [np.array([[0.8]], np.float32)]
        self._run_error = run_error
        self._shapes = shapes or [["batch", 80, 800]]
        self.run_calls = 0

    def get_inputs(self):
        return [_InputSpec(shape) for shape in self._shapes]

    def run(self, _outputs, _feeds):
        self.run_calls += 1
        if self._run_error is not None:
            raise self._run_error
        return self._outputs


def _model_file(tmp_path: Path) -> Path:
    path = tmp_path / "smart-turn.onnx"
    path.write_bytes(b"test-stub")
    return path


def test_missing_model_degrades_to_unavailable(tmp_path):
    detector = td.TurnDetector(model_path=tmp_path / "absent.onnx")
    assert detector.ensure_loaded() is False
    assert detector.unavailable_reason == td.UNAVAILABLE_MODEL_MISSING
    assert detector.classify(_tone()) == (
        None,
        td.VERDICT_UNAVAILABLE,
        td.UNAVAILABLE_MODEL_MISSING,
    )


def test_missing_runtime_degrades_to_unavailable(tmp_path):
    detector = td.TurnDetector(
        model_path=_model_file(tmp_path),
        session_factory=lambda _path: (_ for _ in ()).throw(ImportError("no onnxruntime")),
    )
    assert detector.ensure_loaded() is False
    assert detector.unavailable_reason == td.UNAVAILABLE_NO_RUNTIME


def test_bad_model_shape_degrades_to_unavailable(tmp_path):
    detector = td.TurnDetector(
        model_path=_model_file(tmp_path),
        session_factory=lambda _path: _FakeSession(shapes=[["batch", 80, 400]]),
    )
    assert detector.ensure_loaded() is False
    assert detector.unavailable_reason == td.UNAVAILABLE_BAD_MODEL_SHAPE


def test_bad_audio_degrades_to_unavailable_without_running_the_model(tmp_path):
    session = _FakeSession()
    detector = td.TurnDetector(
        model_path=_model_file(tmp_path), session_factory=lambda _path: session
    )
    assert detector.classify(np.zeros((2, 160), np.float32)) == (
        None,
        td.VERDICT_UNAVAILABLE,
        td.UNAVAILABLE_BAD_AUDIO,
    )
    assert session.run_calls == 0


def test_inference_exception_degrades_to_unavailable(tmp_path):
    detector = td.TurnDetector(
        model_path=_model_file(tmp_path),
        session_factory=lambda _path: _FakeSession(run_error=RuntimeError("boom")),
    )
    assert detector.classify(_tone()) == (
        None,
        td.VERDICT_UNAVAILABLE,
        td.UNAVAILABLE_INFERENCE_ERROR,
    )


def test_malformed_output_degrades_to_unavailable(tmp_path):
    for output in (np.zeros((1, 2), np.float32), np.array([[1.5]], np.float32)):
        detector = td.TurnDetector(
            model_path=_model_file(tmp_path),
            session_factory=lambda _path, out=output: _FakeSession(outputs=[out]),
        )
        assert detector.classify(_tone()) == (
            None,
            td.VERDICT_UNAVAILABLE,
            td.UNAVAILABLE_BAD_OUTPUT,
        )


def test_lazy_load_is_thread_safe_and_happens_once(tmp_path):
    calls: list[int] = []
    load_lock = threading.Lock()

    def factory(_path):
        time.sleep(0.05)
        with load_lock:
            calls.append(1)
        return _FakeSession()

    detector = td.TurnDetector(model_path=_model_file(tmp_path), session_factory=factory)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: detector.ensure_loaded(), range(8)))
    assert all(results)
    assert len(calls) == 1


# ── settings and the default-off flag ──────────────────────────────────


class TestSettings:
    def test_defaults_are_off_and_point_at_runtime_state(self):
        settings = load_settings(dict(_BASE_ENV))
        assert DEFAULT_SMART_TURN_ENABLED is False
        assert DEFAULT_SMART_TURN_THRESHOLD == 0.5
        assert settings.smart_turn_enabled is False
        assert settings.smart_turn_threshold == 0.5
        assert settings.smart_turn_model == DEFAULT_SMART_TURN_MODEL
        assert settings.smart_turn_model.endswith(
            "models/smart-turn-v3.2-cpu.onnx"
        )
        assert "~" not in settings.smart_turn_model

    def test_env_overrides(self):
        settings = load_settings(
            {
                **_BASE_ENV,
                "HERDR_BRAIN_SMART_TURN_ENABLED": "1",
                "HERDR_BRAIN_SMART_TURN_MODEL": "/tmp/smart-turn.onnx",
                "HERDR_BRAIN_SMART_TURN_THRESHOLD": "0.7",
            }
        )
        assert settings.smart_turn_enabled is True
        assert settings.smart_turn_model == "/tmp/smart-turn.onnx"
        assert settings.smart_turn_threshold == 0.7

    @pytest.mark.parametrize("raw", ["-0.1", "1.5", "nan", "nope"])
    def test_invalid_threshold_fails_loudly(self, raw):
        with pytest.raises(ValueError):
            load_settings(
                {**_BASE_ENV, "HERDR_BRAIN_SMART_TURN_THRESHOLD": raw}
            )


def test_default_turn_detector_is_none_when_disabled(monkeypatch):
    monkeypatch.setenv("HERDR_TTS_HOME", "/tmp/herdr-tts-surface")
    monkeypatch.delenv("HERDR_BRAIN_SMART_TURN_ENABLED", raising=False)
    assert td.default_turn_detector() is None


def test_default_turn_detector_is_cached_when_enabled(monkeypatch):
    monkeypatch.setenv("HERDR_TTS_HOME", "/tmp/herdr-tts-surface")
    monkeypatch.setenv("HERDR_BRAIN_SMART_TURN_ENABLED", "1")
    monkeypatch.setenv("HERDR_BRAIN_SMART_TURN_MODEL", "/tmp/smart-turn-test.onnx")
    monkeypatch.setenv("HERDR_BRAIN_SMART_TURN_THRESHOLD", "0.7")
    detector = td.default_turn_detector()
    assert isinstance(detector, td.TurnDetector)
    assert td.default_turn_detector() is detector


def test_pcm16_conversion_scales_and_ignores_an_odd_trailing_byte():
    audio = td.pcm16_to_audio(b"\x00\x40\x00\xc0\xff")
    assert audio.dtype == np.float32
    np.testing.assert_allclose(audio, np.array([0.5, -0.5], np.float32))
    assert td.pcm16_to_audio(b"").size == 0
