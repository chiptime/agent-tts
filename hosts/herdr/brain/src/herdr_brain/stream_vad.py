"""Server-side Silero speech/silence framing over the live call socket.

S6 bounded increment (PRD-05 FR-01, T4): neural speech/silence FRAMING
only — the semantic turn-completion detector (FR-02/FR-03) is a separate,
not-yet-chosen local component and is deliberately NOT faked here. The
framer's opinion is advisory: the client's energy endpointer keeps owning
the turn decision, and Silero may only ever EXTEND a close, never shorten
one (D3: never cutting the speaker wins over speed).

Model reuse (L40): the ONNX weights and the session recipe come from the
already-installed faster-whisper package — ``assets/silero_vad_v6.onnx``
loaded with ``onnxruntime``'s CPUExecutionProvider and the same
single-threaded SessionOptions faster-whisper's own VAD uses. Nothing is
downloaded and no dependency changes. Where faster-whisper's chunker is
stateless-batched (h/c reset per call), this framer feeds 512-sample
chunks incrementally and carries the h/c recurrence INSIDE one framer —
one framer per connection, so calls never share state.

Fail-closed everywhere (FR-06): a missing runtime, missing weights or a
broken session latches a reason token and answers "energy" — the energy
endpointer path with its current windows is the fallback, and the socket
never dies because of framing.

Raw audio never leaves RAM (D9): the framer consumes the same PCM16 bytes
the ring buffer already holds, writes nothing, logs nothing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# Silero chunk quantum: 512 samples @ 16 kHz = 32 ms per inference step,
# each riding the previous chunk's last 64 samples as context
# (faster-whisper's batching recipe applied incrementally).
CHUNK_SAMPLES = 512
CONTEXT_SAMPLES = 64
SPEECH_PROB_INPUT = CHUNK_SAMPLES + CONTEXT_SAMPLES  # the model's [*, 576]

# Hysteresis thresholds, matching faster-whisper's Silero chunker
# semantics: >= speech opens, < silence closes, in between holds whatever
# state a previous chunk established (no flapping mid-utterance).
SPEECH_THRESHOLD = 0.5
SILENCE_THRESHOLD = max(SPEECH_THRESHOLD - 0.15, 0.01)  # 0.35

# Framing policy defaults. D6: measured later; these are the
# deterministic starting points. ONSET_CHUNKS debounces single speech
# blips; MIN_SILENCE_MS is how much neural silence CONFIRMS a speech_end
# (framing, not a turn decision — the turn close stays the client's
# energy window, possibly deferred by this framing's opinion).
ONSET_CHUNKS = 2               # ~64 ms of consecutive speech to open
MIN_SILENCE_MS = 500.0         # sub-threshold run that closes a segment
HEARTBEAT_CHUNKS = 31          # ~1 s liveness beat while speech is active

# Bounded work: a single pathological frame must never run unbounded
# inference in the socket's receive loop. Beyond this pending backlog the
# framer keeps the NEWEST samples and restarts its neural state (state
# cannot span a gap anyway); the audio itself stays untouched in the ring
# buffer for transcription.
MAX_PENDING_SAMPLES = 32000    # 2 s of 16 kHz audio

# FR-06 reason tokens — internal diagnostics only, never rendered raw.
UNAVAILABLE_NO_RUNTIME = "silero_vad_no_runtime"
UNAVAILABLE_MODEL_MISSING = "silero_vad_model_missing"
UNAVAILABLE_LOAD_FAILED = "silero_vad_load_failed"


def default_model_path() -> Optional[Path]:
    """The bundled Silero weights shipped inside faster-whisper's assets.

    Imported lazily: importing faster_whisper pulls in ctranslate2, which
    this module must not require at import time (the socket endpoint may
    live in processes that only want the framing policy).
    """
    try:
        from faster_whisper.utils import get_assets_path
    except Exception:  # noqa: BLE001 — faster-whisper absent: no weights
        return None
    # get_assets_path() returns a plain str in current faster-whisper.
    return Path(get_assets_path()) / "silero_vad_v6.onnx"


def _default_session_factory(path: Path):
    """onnxruntime session built exactly like faster-whisper's own VAD:
    CPU provider, single intra/inter-op thread, quiet logs. Local-only —
    no network, no downloads."""
    import onnxruntime

    opts = onnxruntime.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    opts.enable_cpu_mem_arena = False
    opts.log_severity_level = 4
    return onnxruntime.InferenceSession(
        str(path), providers=["CPUExecutionProvider"], sess_options=opts
    )


class SileroVadEngine:
    """Process-shared model holder with fail-closed availability.

    ``ensure_loaded`` is idempotent and thread-safe: the first caller
    loads the ONNX session once (call it from an executor — loading is
    bounded but not free); every later caller reuses it. A failed load
    latches its reason and never retries per call. ``new_framer`` mints a
    fresh per-connection framer — the SESSION is shared, the STATE never
    is (per-call state isolation).
    """

    def __init__(
        self,
        model_path: Optional[Path | str] = None,
        session_factory: Optional[Callable[[Path], object]] = None,
    ):
        self._model_path = Path(model_path) if model_path is not None else None
        self._session_factory = session_factory
        self._session: Optional[object] = None
        self._load_attempted = False
        self._lock = threading.Lock()
        self.unavailable_reason: Optional[str] = None

    def ensure_loaded(self) -> bool:
        """Loads the session on first use; True when framing is available.

        On any failure the engine latches one ``unavailable_reason`` token
        and stays available=False forever — callers degrade to the energy
        path (FR-06) instead of retrying per frame.
        """
        with self._lock:
            if self._session is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True
            path = self._model_path if self._model_path is not None else default_model_path()
            if path is None or not path.is_file():
                self.unavailable_reason = UNAVAILABLE_MODEL_MISSING
                return False
            factory = self._session_factory or _default_session_factory
            try:
                self._session = factory(path)
            except ImportError:
                self.unavailable_reason = UNAVAILABLE_NO_RUNTIME
                return False
            except Exception:  # noqa: BLE001 — any session failure fails closed
                self.unavailable_reason = UNAVAILABLE_LOAD_FAILED
                return False
            return True

    def new_framer(self) -> "VadSpeechFramer":
        """A fresh per-connection framer bound to this engine's session.

        Raises if the engine has not loaded (the caller checks
        ``ensure_loaded`` first — an unloaded engine must answer the
        energy fallback, never mint a dead framer).
        """
        if self._session is None:
            raise RuntimeError("silero engine not loaded")
        return VadSpeechFramer(self._step)

    def _step(
        self, chunk: np.ndarray, context: np.ndarray, h: np.ndarray, c: np.ndarray
    ) -> tuple[float, np.ndarray, np.ndarray]:
        """One streaming inference: a [1, 576] frame (context + 512 new
        samples) through the shared session, returning the speech
        probability plus the advanced h/c recurrence (per-caller arrays —
        concurrent framers each carry their own)."""
        frame = np.concatenate([context, chunk])[None, :].astype(np.float32)
        probs, hn, cn = self._session.run(
            None, {"input": frame, "h": h, "c": c}
        )
        return float(np.asarray(probs).reshape(-1)[0]), hn, cn


class VadSpeechFramer:
    """Per-call streaming speech/silence framer (PRD-05 FR-01).

    Owns every mutable gram of framing state: the pending PCM16 backlog,
    the 64-sample context, the h/c recurrence and the hysteresis policy.
    ``feed`` accepts arbitrary PCM16 byte frames (the uplink sends ~100 ms
    worklet frames; the script-processor fallback sends odd sizes),
    re-chunks them into 512-sample windows and returns the framing
    events::

        {"event": "speech_start", "at_ms": float}
        {"event": "speech_end", "at_ms": float, "silence_ms": float}
        {"event": "heartbeat", "at_ms": float, "speech": True}
        {"event": "unavailable"}

    Deterministic and engine-agnostic: ``step`` is any callable returning
    ``(speech_probability, h_next, c_next)`` — unit tests script it, the
    engine binds the real ONNX session. Any step failure fails closed: one
    ``unavailable`` event, then the framer stays silent forever (the
    energy endpointer is then the only opinion — a dead framer must never
    kill its socket).
    """

    def __init__(
        self,
        step: Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray], tuple],
        sample_rate: int = 16000,
        speech_threshold: float = SPEECH_THRESHOLD,
        silence_threshold: float = SILENCE_THRESHOLD,
        onset_chunks: int = ONSET_CHUNKS,
        min_silence_ms: float = MIN_SILENCE_MS,
        heartbeat_chunks: int = HEARTBEAT_CHUNKS,
        max_pending_samples: int = MAX_PENDING_SAMPLES,
    ):
        self._step = step
        self._sample_rate = sample_rate
        self._chunk_ms = 1000.0 * CHUNK_SAMPLES / sample_rate
        self._speech_threshold = speech_threshold
        self._silence_threshold = silence_threshold
        self._onset_chunks = max(1, onset_chunks)
        self._min_silence_ms = min_silence_ms
        self._heartbeat_chunks = max(1, heartbeat_chunks)
        self._max_pending_samples = max_pending_samples
        self.reset()

    # ── state ──

    def reset(self) -> None:
        """Fresh framer: no pending audio, zeroed neural state, closed gate."""
        self._pending = bytearray()
        self._chunks = 0
        self._dead = False
        self._reset_state()

    def _reset_state(self) -> None:
        """Neural + policy state (shared by reset and backlog overflow)."""
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._triggered = False
        self._onset_run = 0
        self._silence_chunks = 0
        self._chunks_since_beat = 0

    @property
    def speech_active(self) -> bool:
        return self._triggered

    @property
    def chunks_fed(self) -> int:
        return self._chunks

    # ── the framing loop ──

    def feed(self, pcm16_bytes: bytes) -> list[dict]:
        """Consumes arbitrary PCM16 little-endian bytes; returns events.

        Bounded by design: at most ``max_pending_samples`` of backlog is
        ever processed per call (excess restarts the neural state from the
        newest samples — the dropped span stays available to transcription
        through the ring buffer, only the FRAMING skips it).
        """
        if self._dead:
            return []  # fail-closed: swallow bytes, never raise, no events
        if pcm16_bytes:
            self._pending.extend(pcm16_bytes)
        pending_samples = len(self._pending) // 2
        if pending_samples > self._max_pending_samples:
            keep_bytes = self._max_pending_samples * 2
            del self._pending[:-keep_bytes]  # keep the NEWEST samples
            self._chunks = 0
            self._reset_state()  # state cannot span the dropped gap
        events: list[dict] = []
        chunk_bytes = CHUNK_SAMPLES * 2
        while len(self._pending) >= chunk_bytes:
            raw = bytes(self._pending[:chunk_bytes])
            del self._pending[:chunk_bytes]
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
            samples /= 32768.0  # the model's [-1, 1] float domain
            try:
                prob, hn, cn = self._step(samples, self._context, self._h, self._c)
            except Exception:  # noqa: BLE001 — any model failure fails closed
                self._dead = True
                self._pending.clear()
                return [{"event": "unavailable"}]
            self._context = samples[-CONTEXT_SAMPLES:].copy()
            self._h = hn if isinstance(hn, np.ndarray) else np.asarray(hn)
            self._c = cn if isinstance(cn, np.ndarray) else np.asarray(cn)
            self._chunks += 1
            event = self._policy(prob)
            if event is not None:
                events.append(event)
        return events

    def _policy(self, prob: float) -> Optional[dict]:
        """Hysteresis gate over one chunk's speech probability."""
        if not self._triggered:
            if prob >= self._speech_threshold:
                self._onset_run += 1
                if self._onset_run >= self._onset_chunks:
                    self._triggered = True
                    self._onset_run = 0
                    self._silence_chunks = 0
                    self._chunks_since_beat = 0
                    return {
                        "event": "speech_start",
                        "at_ms": self._chunks * self._chunk_ms,
                    }
            else:
                self._onset_run = 0
            return None
        if prob < self._silence_threshold:
            self._silence_chunks += 1
            if self._silence_chunks * self._chunk_ms >= self._min_silence_ms:
                silence_ms = self._silence_chunks * self._chunk_ms
                self._triggered = False
                self._silence_chunks = 0
                self._onset_run = 0
                return {
                    "event": "speech_end",
                    "at_ms": self._chunks * self._chunk_ms,
                    "silence_ms": silence_ms,
                }
            return None
        # Speech, or the hysteresis band above the silence threshold: a
        # recovery before the silence confirmed resets the silence run.
        self._silence_chunks = 0
        self._chunks_since_beat += 1
        if self._chunks_since_beat >= self._heartbeat_chunks:
            self._chunks_since_beat = 0
            return {
                "event": "heartbeat",
                "at_ms": self._chunks * self._chunk_ms,
                "speech": True,
            }
        return None
