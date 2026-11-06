"""Local semantic turn-completion detector (PRD-05 FR-02, amended D18).

Smart Turn v3.2 CPU ONNX, fed the same Whisper log-mel features as the
pinned public reference. Audio-native, local-only (D2/D18): the caller
hands this module raw 16 kHz mono audio; nothing is uploaded, persisted
or logged, and the verdict is ADVISORY — the client's energy endpointer
still owns the turn close (D3: never cutting the speaker wins over
speed).

Pinned reference (read 2026-11-06):

- https://github.com/pipecat-ai/smart-turn/commit/4786657e242dfe77dd138699ac564ee074a2a543
- https://raw.githubusercontent.com/pipecat-ai/smart-turn/4786657e242dfe77dd138699ac564ee074a2a543/inference.py
- https://raw.githubusercontent.com/pipecat-ai/smart-turn/4786657e242dfe77dd138699ac564ee074a2a543/audio_utils.py
- ``inference.py``: ``WhisperFeatureExtractor(chunk_length=8)`` with
  ``sampling_rate=16000``, ``padding="max_length"``, ``max_length=8*16000``,
  ``truncation=True``, ``do_normalize=True``; ONNX input
  ``input_features`` ``[batch, 80, 800]``; output ``[batch, 1]``.
- ``audio_utils.py``: front-pad to 8 s when shorter, keep the LAST 8 s
  when longer.
- Weights: https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx
  (HF repo commit ``f766f81d3cfdf7737ac64aad813d91bbfd56bf93``, 8,679,182
  bytes, SHA-256 ``2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f``),
  stored as runtime state by ``scripts/fetch-smart-turn.sh``.

Preprocessing is the Whisper log-mel recipe documented by the task and
implemented by ``WhisperFeatureExtractor``: Hann window ``n_fft=400``,
hop 160, 80 Slaney mel bins to 8 kHz, ``log10(max(mel, 1e-10))``, clamp
to ``max - 8.0``, then ``(x + 4.0) / 4.0``. It is ported with numpy only
(no transformers/torch dependency).

PARITY STATUS: exact numerical parity with the pinned reference's *torch*
path is UNPROVEN on this machine because ``transformers``/``torch`` are
not installed and installing dependencies is out of scope. The port
matches the reference's documented recipe and uses the same symmetric
Hann window as ``torch.hann_window(400, periodic=False)`` (numpy:
``np.hanning(400)``). It is pinned in tests against faster-whisper's
independently maintained Whisper mel/STFT implementation; that
cross-check is exact. What remains unproven is the reference stack's own
float32 accumulation order, not a design choice here.

Every failure degrades to ``unavailable`` with one bounded reason token
(:data:`UNAVAILABLE_*`); nothing here ever raises into the call path and
no verdict is fabricated.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

SAMPLE_RATE = 16000
WINDOW_SECONDS = 8.0
WINDOW_SAMPLES = int(WINDOW_SECONDS * SAMPLE_RATE)  # 128,000
N_FFT = 400
HOP_LENGTH = 160
N_MELS = 80
MEL_MAX_HZ = 8000.0

# Model contract (verified against the pinned CPU ONNX file).
INPUT_NAME = "input_features"
FEATURE_SHAPE = (N_MELS, WINDOW_SAMPLES // HOP_LENGTH)  # (80, 800)

VERDICT_COMPLETE = "complete"
VERDICT_INCOMPLETE = "incomplete"
VERDICT_UNAVAILABLE = "unavailable"

# Bounded, non-sensitive reason tokens (never rendered raw to the user).
UNAVAILABLE_MODEL_MISSING = "smart_turn_model_missing"
UNAVAILABLE_NO_RUNTIME = "smart_turn_no_runtime"
UNAVAILABLE_LOAD_FAILED = "smart_turn_load_failed"
UNAVAILABLE_BAD_MODEL_SHAPE = "smart_turn_bad_model_shape"
UNAVAILABLE_BAD_AUDIO = "smart_turn_bad_audio"
UNAVAILABLE_BAD_OUTPUT = "smart_turn_bad_output"
UNAVAILABLE_INFERENCE_ERROR = "smart_turn_inference_error"


def default_model_path() -> Path:
    """The persistent runtime location of the pinned CPU ONNX weights.

    Runtime state, never the repository: ``config`` owns the same default
    (``HERDR_BRAIN_SMART_TURN_MODEL`` overrides it).
    """
    from .config import DEFAULT_SMART_TURN_MODEL

    return Path(DEFAULT_SMART_TURN_MODEL).expanduser()


def pcm16_to_audio(pcm16_bytes: bytes) -> np.ndarray:
    """Raw PCM16 little-endian mono bytes -> float32 samples in [-1, 1].

    Odd trailing bytes are ignored (whole samples only); empty input is an
    empty array, never an error.
    """
    usable = len(pcm16_bytes) - (len(pcm16_bytes) % 2)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(pcm16_bytes, dtype="<i2", count=usable // 2)
    return samples.astype(np.float32) / 32768.0


def _mel_filterbank() -> np.ndarray:
    """Whisper's 80-bin Slaney mel filterbank, ``[80, 201]`` float32.

    Port of the reference recipe (``WhisperFeatureExtractor.mel_filters``):
    the mel scale is linear below 1 kHz and logarithmic above, with Slaney
    area normalisation. Cached per process (immutable output).
    """
    global _MEL_FILTERS
    cached = _MEL_FILTERS
    if cached is not None:
        return cached
    fft_freqs = np.fft.rfftfreq(n=N_FFT, d=1.0 / SAMPLE_RATE)
    # The Slaney mel value of 8 kHz under this scale.
    max_mel = 15.0 + 27.0 * np.log(MEL_MAX_HZ / 1000.0) / np.log(6.4)
    mels = np.linspace(0.0, max_mel, N_MELS + 2)
    f_sp = 200.0 / 3.0
    freqs = f_sp * mels
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_region = mels >= min_log_mel
    freqs[log_region] = min_log_hz * np.exp(
        logstep * (mels[log_region] - min_log_mel)
    )
    fdiff = np.diff(freqs)
    ramps = freqs.reshape(-1, 1) - fft_freqs.reshape(1, -1)
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0.0, np.minimum(lower, upper))
    enorm = 2.0 / (freqs[2 : N_MELS + 2] - freqs[:N_MELS])
    weights *= enorm[:, None]
    filters = weights.astype(np.float32)
    _MEL_FILTERS = filters
    return filters


_MEL_FILTERS: Optional[np.ndarray] = None


def preprocess(audio: np.ndarray) -> np.ndarray:
    """1-D 16 kHz float32 audio -> the model's ``[80, 800]`` float32 input.

    Mirrors the reference exactly: keep the LAST 8 s when longer, zero-pad
    at the FRONT when shorter, then Whisper log-mel extraction. The input
    is not peak-normalised (the reference does not normalise inside
    ``predict_endpoint``; callers pass audio already in [-1, 1]).

    Raises ``ValueError`` for a non-1-D, empty or non-finite input — batch
    shape errors are a caller bug, while :meth:`TurnDetector.classify`
    converts them to an ``unavailable`` result for the socket path.
    """
    array = np.asarray(audio)
    if array.ndim != 1:
        raise ValueError("audio must be a 1-D array")
    array = array.astype(np.float32, copy=False)
    if array.size == 0:
        raise ValueError("audio must not be empty")
    if not np.all(np.isfinite(array)):
        raise ValueError("audio must contain finite samples")

    if array.size > WINDOW_SAMPLES:
        array = array[-WINDOW_SAMPLES:]
    elif array.size < WINDOW_SAMPLES:
        array = np.pad(array, (WINDOW_SAMPLES - array.size, 0))

    # torch.stft(center=True, pad_mode="reflect") equivalent, then drop the
    # final frame exactly like the reference (`stft[..., :-1]`).
    pad = N_FFT // 2
    padded = np.pad(array, (pad, pad), mode="reflect")
    n_frames = 1 + (padded.size - N_FFT) // HOP_LENGTH
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, N_FFT),
        strides=(HOP_LENGTH * padded.strides[0], padded.strides[0]),
        writeable=False,
    )
    # Symmetric Hann, matching torch.hann_window(400, periodic=False) —
    # the window WhisperFeatureExtractor's torch path actually uses.
    window = np.hanning(N_FFT).astype(np.float32)
    spectrum = np.fft.rfft(frames * window, n=N_FFT, axis=-1)
    magnitudes = np.abs(spectrum[:-1].T) ** 2

    mel_spec = _mel_filterbank() @ magnitudes
    log_spec = np.log10(np.maximum(mel_spec, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    features = ((log_spec + 4.0) / 4.0).astype(np.float32)
    if features.shape != FEATURE_SHAPE:  # pragma: no cover — invariant
        raise ValueError(
            f"unexpected feature shape {features.shape}, expected {FEATURE_SHAPE}"
        )
    return features


def _default_session_factory(path: Path):
    """CPU-only ONNX session: single intra/inter-op threads, quiet logs."""
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 4
    return onnxruntime.InferenceSession(
        str(path), providers=["CPUExecutionProvider"], sess_options=options
    )


class _BadModelShape(Exception):
    """Internal: the loaded ONNX graph does not match the pinned contract."""


class TurnDetector:
    """Lazily-loaded, thread-safe Smart Turn v3.2 CPU classifier.

    ``ensure_loaded`` is idempotent: the first caller loads the ONNX
    session once (call it off the event loop); later callers reuse it. A
    failed load latches one bounded reason token and never retries per
    call. ``classify`` never raises: every failure answers
    ``(None, "unavailable", <reason token>)``.
    """

    def __init__(
        self,
        model_path: Optional[Path | str] = None,
        threshold: float = 0.5,
        session_factory: Optional[Callable[[Path], object]] = None,
    ):
        self._model_path = (
            Path(model_path).expanduser() if model_path is not None else None
        )
        self._threshold = float(threshold)
        self._session_factory = session_factory
        self._session: Optional[object] = None
        self._input_name = INPUT_NAME
        self._load_attempted = False
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self.unavailable_reason: Optional[str] = None

    def ensure_loaded(self) -> bool:
        """Loads the session on first use; True when inference is available."""
        with self._lock:
            if self._session is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True
            path = (
                self._model_path
                if self._model_path is not None
                else default_model_path()
            )
            if not path.is_file():
                self.unavailable_reason = UNAVAILABLE_MODEL_MISSING
                return False
            factory = self._session_factory or _default_session_factory
            try:
                session = factory(path)
                self._input_name = self._validate_session(session)
            except ImportError:
                self.unavailable_reason = UNAVAILABLE_NO_RUNTIME
                return False
            except _BadModelShape:
                self.unavailable_reason = UNAVAILABLE_BAD_MODEL_SHAPE
                return False
            except Exception:  # noqa: BLE001 — any load failure fails closed
                self.unavailable_reason = UNAVAILABLE_LOAD_FAILED
                return False
            self._session = session
            return True

    @staticmethod
    def _validate_session(session: object) -> str:
        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise _BadModelShape
        spec = inputs[0]
        shape = list(spec.shape)
        if len(shape) != 3:
            raise _BadModelShape
        # Dim 0 is the symbolic batch; dims 1-2 must match the pinned file.
        for actual, expected in zip(shape[1:], FEATURE_SHAPE):
            if isinstance(actual, int) and actual != expected:
                raise _BadModelShape
        return str(spec.name)

    def classify(
        self, audio: np.ndarray
    ) -> tuple[Optional[float], str, Optional[str]]:
        """Classify one utterance.

        Returns ``(probability, verdict, reason)``:

        - success: ``(p, "complete" | "incomplete", None)``;
        - failure: ``(None, "unavailable", <bounded reason token>)``.

        Never raises into the call path and never produces a verdict from
        an invalid model output.
        """
        if not self.ensure_loaded():
            return (
                None,
                VERDICT_UNAVAILABLE,
                self.unavailable_reason or UNAVAILABLE_LOAD_FAILED,
            )
        try:
            features = preprocess(audio)
        except Exception:  # noqa: BLE001 — bad caller audio fails closed
            return (None, VERDICT_UNAVAILABLE, UNAVAILABLE_BAD_AUDIO)
        try:
            with self._run_lock:
                outputs = self._session.run(
                    None, {self._input_name: features[None, :, :]}
                )
        except Exception:  # noqa: BLE001 — runtime failure fails closed
            return (None, VERDICT_UNAVAILABLE, UNAVAILABLE_INFERENCE_ERROR)
        try:
            values = np.asarray(outputs[0], dtype=np.float64).reshape(-1)
            # The pinned graph answers exactly one probability per batch.
            if values.size != 1:
                raise ValueError(f"unexpected model output size {values.size}")
            probability = float(values[0])
        except Exception:  # noqa: BLE001 — malformed output fails closed
            return (None, VERDICT_UNAVAILABLE, UNAVAILABLE_BAD_OUTPUT)
        if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
            return (None, VERDICT_UNAVAILABLE, UNAVAILABLE_BAD_OUTPUT)
        verdict = (
            VERDICT_COMPLETE if probability > self._threshold else VERDICT_INCOMPLETE
        )
        return (probability, verdict, None)


_default_detectors: dict[tuple[str, float], TurnDetector] = {}
_default_detectors_lock = threading.Lock()


def default_turn_detector() -> Optional[TurnDetector]:
    """The process-wide detector selected by the environment, or None.

    Default OFF (``HERDR_BRAIN_SMART_TURN_ENABLED``). When enabled, the
    detector is cached by ``(model path, threshold)`` so concurrent calls
    share one lazily-loaded ONNX session. A settings read failure disables
    the advisory signal instead of breaking a call.
    """
    try:
        from .config import load_settings

        settings = load_settings()
    except Exception:  # noqa: BLE001 — advisory feature must never block a call
        return None
    if not settings.smart_turn_enabled:
        return None
    key = (str(settings.smart_turn_model), float(settings.smart_turn_threshold))
    with _default_detectors_lock:
        detector = _default_detectors.get(key)
        if detector is None:
            detector = TurnDetector(
                settings.smart_turn_model,
                threshold=settings.smart_turn_threshold,
            )
            _default_detectors[key] = detector
        return detector
