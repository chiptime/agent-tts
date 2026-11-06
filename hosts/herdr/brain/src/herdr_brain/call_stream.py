"""WebSocket streaming uplink endpoint and in-memory audio ring buffer (PRD-03),
plus the streaming partial-transcript loop (PRD-04, T3/S5)."""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import logging
import math
import wave
from typing import Optional

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from .pairing import PairingStore
from .spoken_lexicon import LOCALES as SPOKEN_LOCALES
from .stt import Transcriber, live_vocabulary_hint
from .stream_vad import SileroVadEngine
from .turn_detector import (
    UNAVAILABLE_INFERENCE_ERROR,
    VERDICT_COMPLETE,
    VERDICT_INCOMPLETE,
    VERDICT_UNAVAILABLE,
    TurnDetector,
    default_turn_detector,
    pcm16_to_audio,
)

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM = 2 bytes per sample
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 32,000 bytes/sec
DEFAULT_PREROLL_MS = 500

# T3 streaming cadence (D6: measured later; these are the deterministic
# defaults). One partial pass per ~1 s of NEW audio keeps partials within
# the ≤1 s-behind-live target (FR-03 assumption); each pass transcribes at
# most ~5 s from the utterance origin (FR-05 cap).
PASS_INTERVAL_MS = 1000
WINDOW_CAP_MS = 5000
STREAM_PREROLL_MS = 500  # utterance origin begins ~500 ms before onset
MIN_PASS_AUDIO_MS = 300  # never run whisper on a sliver of audio

# L19 finals parity: a parked window whose confirmation REVISES the tail
# gets bounded retries to stabilize the revision (every final word needs
# two consecutive agreeing appearances; a revision that stabilizes on
# the next pass commits then). Total passes a parked window may cost.
CONFIRM_PASS_LIMIT = 3
# The turn-end flush (audio confirmed after ``stream stop``) is bounded
# so a pathological backend can never hang the call. The budget covers
# confirming a parked window (plus retries) AND first+confirming passes
# over residual audio that no pass has covered yet.
FLUSH_PASS_LIMIT = 8

# L16 honesty: when the ring's bounded capacity has already dropped audio
# the origin still expects, recovery is impossible — the session discloses
# the loss ONCE per utterance as this notice reason (mapped to the
# existing "part of the turn could not be transcribed" banner) and
# resumes from the earliest surviving audio. Never a silent skip.
LIVE_NOTICE_AUDIO_GAP = "live_audio_gap"
RING_GAP_TOLERANCE_MS = 50  # byte-alignment/float slack below which a
# position difference is rounding, not lost audio

# S6/FR-02 amended (D18): the local semantic turn detector is ADVISORY.
# It reads the last 8 s ending at the client's silence close (the model's
# native window; preprocess trims/pads again) and never decides a turn.
TURN_DETECTOR_WINDOW_MS = 8000

# L59 extender-only: real Spanish human audio showed a 16.1% false-complete
# rate, so a verdict may only ever DEFER the client's energy close, never
# shorten a turn (D3). A client that sees ``smart_turn`` in ``vad_status``
# may send a bounded ``turn_probe`` just before its close; the server
# evaluates the same native window and answers one ``turn_probe_result``.
# Probe fields are untrusted socket input: each is clamped, and the window
# never exceeds the model's native 8 s (the existing advisory seam).
TURN_PROBE_MAX_FIELD_MS = 60000.0

# Silent-window guard. Whisper echoes its ``initial_prompt`` (the live
# vocabulary hint) or hallucinates a word ("you") when it is fed silence,
# so inference NEVER runs on a window without speech-level energy — in the
# streaming passes and in the turn-end transcribe alike.
#
# The test is the LOUDEST short frame's int16 RMS, not the window's mean:
# a brief word inside seconds of silence must not be diluted below the
# threshold by the silent rest. 20 ms frames are shorter than a phoneme.
#
# Threshold 100 (≈ -50 dBFS, 0.003 of full scale):
#   - digital silence (a dead/muted stream) is exactly 0 and sensor/dither
#     noise is a few LSB up to a few tens (≈ 3-40) — all well below it;
#   - whispered or far-field speech sits around -45…-35 dBFS (≈ 180-600 RMS
#     per frame) and normal speech at -30…-15 dBFS (≈ 1000-5800), so real
#     speech clears it by 2x or more even before phone AGC;
#   - the client's own energy VAD opens a turn only at 2.5x an adaptive
#     floor of at least 0.004 (≥ ~330 RMS), so a turn the client accepted
#     as speech is always above this threshold.
# It is deliberately conservative: dropping a quiet word is worse than
# transcribing near-silent noise, which the hint-echo cases never reach.
SILENCE_FRAME_MS = 20
SILENCE_FRAME_RMS_THRESHOLD = 100.0
# Never run the model on less audio than this (300 ms, same bar the passes
# already use): a sliver cannot carry a transcribable word.
MIN_INFERENCE_AUDIO_MS = MIN_PASS_AUDIO_MS

# L13: the ONLY wording a transcription failure ever sends to the client.
# A fixed generic constant — exception text can carry paths, model names
# or configuration values and must never reach the client.
STT_FAILURE_MESSAGE = "live transcription failed"


class AudioRingBuffer:
    """In-memory bounded ring buffer for PCM16 16 kHz mono audio (D9: never persisted to disk)."""

    def __init__(self, max_seconds: float = 120.0, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.max_bytes = int(max_seconds * BYTES_PER_SECOND)
        self._buffer = bytearray()
        self._appended_bytes = 0  # total since clear(), incl. dropped oldest

    def append(self, pcm16_bytes: bytes) -> None:
        """Appends raw PCM16 samples and bounds buffer to max_bytes by dropping oldest."""
        if not pcm16_bytes:
            return
        self._buffer.extend(pcm16_bytes)
        self._appended_bytes += len(pcm16_bytes)
        if len(self._buffer) > self.max_bytes:
            excess = len(self._buffer) - self.max_bytes
            del self._buffer[:excess]

    def get_window(self, duration_ms: float = 0, pre_roll_ms: float = DEFAULT_PREROLL_MS) -> bytes:
        """Extracts an audio slice covering the utterance duration plus pre-roll before onset."""
        total_ms = (duration_ms if duration_ms > 0 else 0) + (pre_roll_ms if pre_roll_ms > 0 else 0)
        if total_ms <= 0:
            return bytes(self._buffer)
        needed_samples = int(total_ms * self.sample_rate / 1000.0)
        needed_bytes = needed_samples * SAMPLE_WIDTH
        if needed_bytes >= len(self._buffer):
            return bytes(self._buffer)
        return bytes(self._buffer[-needed_bytes:])

    def total_ms(self) -> float:
        """Milliseconds of audio appended since the last clear (constant,
        unaffected by the bounded drop of oldest bytes)."""
        return self._appended_bytes / BYTES_PER_SECOND * 1000.0

    def earliest_ms(self) -> float:
        """Timeline position of the oldest audio still buffered — the
        ring's bounded capacity drops everything before it."""
        return self.total_ms() - len(self._buffer) / BYTES_PER_SECOND * 1000.0

    def get_range(self, start_ms: float, end_ms: Optional[float] = None) -> bytes:
        """The audio between two positions on the since-clear timeline.

        Positions predating the retained window (oldest bytes dropped)
        clamp to the earliest audio still buffered — a bounded, honest
        slice, never an error. Slices stay 2-byte aligned (whole samples).
        """
        total_ms = self.total_ms()
        end = total_ms if end_ms is None else min(end_ms, total_ms)
        earliest_ms = total_ms - len(self._buffer) / BYTES_PER_SECOND * 1000.0
        start = max(start_ms, earliest_ms)
        if end <= start:
            return b""
        offset_ms = start - earliest_ms
        start_b = int(offset_ms / 1000.0 * BYTES_PER_SECOND) & ~1
        end_b = int((offset_ms + (end - start)) / 1000.0 * BYTES_PER_SECOND) & ~1
        end_b = min(end_b, len(self._buffer))
        return bytes(self._buffer[start_b:end_b])

    def clear(self) -> None:
        self._buffer.clear()
        self._appended_bytes = 0

    def __len__(self) -> int:
        return len(self._buffer)


def pcm16_to_wav_bytes(pcm16_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wraps raw 16 kHz mono PCM16 bytes in a standard WAV container in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16_data)
    return buf.getvalue()


def peak_frame_rms(pcm16_data: bytes) -> float:
    """Int16 RMS of the loudest :data:`SILENCE_FRAME_MS` frame of raw 16 kHz
    mono PCM16 (a trailing partial frame counts too). Empty input is 0."""
    usable = len(pcm16_data) - (len(pcm16_data) % SAMPLE_WIDTH)
    if usable <= 0:
        return 0.0
    samples = np.frombuffer(pcm16_data, dtype="<i2", count=usable // SAMPLE_WIDTH)
    frame = max(1, SAMPLE_RATE * SILENCE_FRAME_MS // 1000)
    squares = samples.astype(np.float64) ** 2
    full = (len(squares) // frame) * frame
    peak = 0.0
    if full:
        per_frame = squares[:full].reshape(-1, frame).mean(axis=1)
        peak = float(per_frame.max())
    if full < len(squares):
        peak = max(peak, float(squares[full:].mean()))
    return peak ** 0.5


def window_has_no_speech(pcm16_data: bytes) -> bool:
    """True when a window must NOT reach the model: shorter than
    :data:`MIN_INFERENCE_AUDIO_MS`, or without any frame at speech-level
    energy (:data:`SILENCE_FRAME_RMS_THRESHOLD`)."""
    if len(pcm16_data) < MIN_INFERENCE_AUDIO_MS * BYTES_PER_SECOND // 1000:
        return True
    return peak_frame_rms(pcm16_data) < SILENCE_FRAME_RMS_THRESHOLD


def _bounded_probe_ms(raw: object, default: float) -> float:
    """Untrusted probe timing field -> a finite non-negative millisecond
    value, clamped to :data:`TURN_PROBE_MAX_FIELD_MS`."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = float(raw)
    if not math.isfinite(value):
        return default
    return max(0.0, min(value, TURN_PROBE_MAX_FIELD_MS))


def _bounded_prob(raw: object) -> Optional[float]:
    """Detector probability -> ``[0, 1]`` rounded for the wire, or None
    when the value is not a finite number."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value):
        return None
    return round(min(1.0, max(0.0, value)), 4)


def validate_device_token(
    pairing_store: Optional[PairingStore],
    token: Optional[str],
) -> tuple[bool, Optional[str]]:
    """Validates the device token against PairingStore when enforcement is active."""
    if pairing_store is None:
        return True, None
    if pairing_store.enforced():
        if not token:
            return False, "missing device token"
        status, _device = pairing_store.resolve_status(token)
        if status == "ok":
            return True, None
        if status == "expired":
            return False, "device credential expired"
        return False, "device not paired"
    # When pairing is not enforced, token is optional but first-message contract still applies
    return True, None


def resolve_streaming_locale(raw: object) -> Optional[str]:
    """D8: the app locale arrives in the auth message and forces the
    transcription language. Untrusted socket input: anything that is not
    a known spoken locale (or is oversized) becomes whisper auto-detect —
    never an error, never a string passed through to the model."""
    if not isinstance(raw, str) or len(raw) > 8:
        return None
    locale = raw.strip().lower()
    return locale if locale in SPOKEN_LOCALES else None


def resolve_transcribe_call(
    transcriber: Transcriber,
) -> tuple[object, bool]:
    """The transcription seam for this connection plus whether it accepts
    the D8 hints. Backends with the plain (data, suffix) seam — e.g. the
    engine CLI bridge — keep today's exact call shape; hint-capable
    backends receive (data, suffix, language, initial_prompt)."""
    call = getattr(transcriber, "transcribe_in_memory", None)
    if call is None:
        call = transcriber.transcribe_bytes
    try:
        params = inspect.signature(call).parameters
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return call, False
    return call, "language" in params


class StreamingTranscriptionSession:
    """Per-connection streaming partial-transcript loop (PRD-04, T3/S5).

    Between ``stream start`` and ``stream stop`` (the client's VAD onset /
    silence close), every ~:data:`PASS_INTERVAL_MS` of NEW audio triggers
    one transcription pass over the window from the utterance origin,
    capped at ~:data:`WINDOW_CAP_MS` per pass (FR-05). Each pass emits
    the local-agreement contract (FR-02, deterministic):

    - ``{"type": "final", "text": <delta>}`` ONLY for the token prefix
      with two consecutive agreeing appearances (L17: a full window that
      cannot grow parks itself after its first pass and re-runs the SAME
      window once as a confirmation before any of its tail commits);
    - ``{"type": "partial", "text": <full guess>}`` — committed finals
      plus the unstable tail — as the display-level current best guess.

    Finals are DISPLAY ONLY: the turn-end ``transcribe`` command stays the
    single dispatch source, so a turn's text routes exactly once and no
    partial ever reaches /ask (AC4). Ordering guards:

    - one inference in flight per call — passes and the turn-end
      transcribe share ``inference_lock``;
    - a generation counter drops results from a pass that outlived its
      utterance (late after stop/transcribe/hangup);
    - the origin resets at every turn boundary (stop, transcribe, clear)
      so a new utterance's partials never re-emit the finished turn's
      text (prefix-overlap protection);
    - chronological catch-up (L16): a pass's window boundaries are
      captured at pass START, and the origin may only slide to a
      boundary the pass actually transcribed — so audio that arrived
      while inference was slow is covered by later passes in order,
      overlap allowed, gaps never; if the ring's bound already dropped
      audio the origin expects, that loss is disclosed as a
      ``live_audio_gap`` notice instead of being skipped silently.

    Raw audio stays in the ring buffer (RAM only, D9); nothing here
    writes files or logs transcript text.
    """

    def __init__(
        self,
        websocket: WebSocket,
        transcriber: Optional[Transcriber],
        ring_buffer: AudioRingBuffer,
        language: Optional[str] = None,
        initial_prompt: Optional[str] = None,
    ):
        self._ws = websocket
        self._transcriber = transcriber
        self._ring = ring_buffer
        self._language = language
        self._initial_prompt = initial_prompt
        self._transcribe_call, self._accepts_hints = (
            resolve_transcribe_call(transcriber) if transcriber is not None
            else (None, False)
        )
        self.inference_lock = asyncio.Lock()
        self._generation = 0
        self._active = False
        self._task: Optional[asyncio.Task] = None
        self._origin_ms = 0.0
        self._boundary_ms = 0.0  # position of the last known turn boundary
        self._last_pass_ms = 0.0
        self._prev_tokens: list[str] = []
        self._committed = 0
        self._finals_text = ""
        self._error_latched = False
        self._gap_notified = False
        # L17: a FULL window parks itself here after its first pass —
        # its unagreed tail may only become final once a confirmation
        # pass over the SAME window agrees on it (FR-02: every final
        # word needs two consecutive agreeing appearances). Tuple of
        # (window_start, window_end, tokens).
        self._pending_full: Optional[tuple[float, float, list[str]]] = None
        # L19: passes spent on the current parked window (bounded
        # stabilization retries for revised tails).
        self._confirm_attempts = 0
        # L19: the turn-end flush — ``stream stop`` runs up to
        # FLUSH_PASS_LIMIT confirming passes over whatever is still
        # unconfirmed (a parked window and/or the residual audio), so
        # stable tail words become final instead of vanishing (FR-04
        # parity between concatenated finals and the turn transcript).
        self._flushing = False
        self._flush_remaining = 0
        self._flush_settled = False
        # L20: how far the passes have transcribed on the ring timeline
        # (drives the flush over never-covered residual audio), and the
        # fail-closed parity latch — a turn whose words cannot stabilize
        # within the bounds answers the generic error, never a
        # successful transcript its finals cannot match.
        self._covered_ms = 0.0
        self._parity_failed = False

    # ── client control messages ──

    def start_utterance(self) -> None:
        """``stream start``: a new utterance begins at the VAD onset. The
        window origin keeps ~500 ms of pre-onset audio — except after a
        known turn boundary, where the boundary wins (no preroll into a
        finished turn)."""
        self._generation += 1
        self._active = True
        self._error_latched = False
        self._gap_notified = False
        self._prev_tokens = []
        self._committed = 0
        self._finals_text = ""
        self._pending_full = None
        self._confirm_attempts = 0
        self._flushing = False
        self._flush_remaining = 0
        self._parity_failed = False
        self._origin_ms = max(
            self._boundary_ms, self._ring.total_ms() - STREAM_PREROLL_MS
        )
        self._last_pass_ms = self._origin_ms
        self._schedule_pass()

    @property
    def parity_failed(self) -> bool:
        """L20: this turn failed closed — agreement could not be reached
        within the bounds, so the turn-end transcript must answer the
        generic error instead of an incompatible successful transcript."""
        return self._parity_failed

    def stop_utterance(self) -> None:
        """``stream stop``: the client closed the turn. Everything still
        unconfirmed — a parked window and/or the residual audio beyond
        the origin — gets up to :data:`FLUSH_PASS_LIMIT` confirming
        passes (L19 finals parity) before the session settles. The
        generation is NOT bumped: the in-flight pass and the flush
        belong to this same utterance; the true turn boundary is the
        turn-end transcript (``reset_after_transcript``)."""
        self._active = False
        self._boundary_ms = self._ring.total_ms()
        unconfirmed = (
            self._pending_full is not None
            or self._ring.total_ms() - self._origin_ms >= MIN_PASS_AUDIO_MS
        )
        if (
            unconfirmed
            and self._transcriber is not None
            and not self._error_latched
            and not self._parity_failed
        ):
            self._flushing = True
            self._flush_settled = False
            self._flush_remaining = FLUSH_PASS_LIMIT
            self._schedule_pass()

    async def finish_flush(self) -> None:
        """Waits until a pending turn-end flush fully settles (or fails),
        so the turn-end transcript can never overtake the finals it must
        include (L20: stop+transcribe arrive back-to-back — the inline
        transcript handler would otherwise win the inference lock ahead
        of the flush passes and emit a transcript with no finals)."""
        while self._flushing and not self._parity_failed:
            await asyncio.sleep(0.01)

    def reset_after_transcript(self) -> None:
        """The turn-end transcript was served: the finished turn's audio
        is behind us, so the next utterance's window starts fresh. This
        is the hard boundary — any still-pending flush or pass result is
        dropped here."""
        self._generation += 1
        self._active = False
        self._flushing = False
        self._flush_remaining = 0
        self._pending_full = None
        self._confirm_attempts = 0
        self._boundary_ms = self._ring.total_ms()
        self._prev_tokens = []
        self._committed = 0
        self._finals_text = ""

    def clear(self) -> None:
        """The ring was cleared: every position resets with it."""
        self._generation += 1
        self._active = False
        self._flushing = False
        self._flush_remaining = 0
        self._pending_full = None
        self._confirm_attempts = 0
        self._boundary_ms = 0.0
        self._origin_ms = 0.0
        self._last_pass_ms = 0.0
        self._covered_ms = 0.0
        self._parity_failed = False
        self._prev_tokens = []
        self._committed = 0
        self._finals_text = ""
        self._gap_notified = False

    def on_audio(self) -> None:
        """A frame arrived: maybe schedule the next pass."""
        self._schedule_pass()

    async def aclose(self) -> None:
        """Cancels the in-flight pass task at disconnect (its executor
        call, if any, is abandoned mid-flight and its result discarded)."""
        task = self._task
        self._task = None
        self._active = False
        self._flushing = False
        self._generation += 1
        if task is not None and not task.done():
            task.cancel()

    # ── the pass loop ──

    def _live(self) -> bool:
        """A pass may run while the utterance is active OR while the
        turn-end flush still has confirming passes to spend."""
        return self._active or self._flushing

    def _schedule_pass(self) -> None:
        if (
            not self._live()
            or self._error_latched
            or self._transcriber is None
            or self._task is not None
        ):
            return
        # A parked full window or a turn-end flush needs no new audio:
        # their passes re-run/confirm without waiting for the interval.
        if (
            self._pending_full is None
            and not self._flushing
            and self._ring.total_ms() - self._last_pass_ms < PASS_INTERVAL_MS
        ):
            return
        self._task = asyncio.get_running_loop().create_task(self._run_pass())

    async def _run_pass(self) -> None:
        generation = self._generation
        # A pass counts against the flush budget only if the flush was
        # already running when the pass STARTED (an ordinary pass that
        # completes after the stop must not drain the budget, L20).
        flush_pass = self._flushing and not self._active
        try:
            # Stale before it starts: the utterance ended (and the flush
            # was drained or cancelled) between scheduling and this step.
            if generation != self._generation or not self._live():
                return
            # L16 honesty: if the ring's bound already dropped audio the
            # origin still expects, say so once and resume from the
            # earliest survivor — never silently skip.
            await self._signal_ring_gap_once()
            # Capture the window boundaries BEFORE inference: everything
            # after the pass (emissions, the origin slide, catch-up
            # cadence) refers to exactly the audio THIS pass transcribed,
            # never to the ring's later duration (the L16 root cause).
            window_start = self._origin_ms
            window_end = min(
                self._ring.total_ms(), window_start + WINDOW_CAP_MS
            )
            if window_end - window_start < MIN_PASS_AUDIO_MS:
                self._flush_settled = True  # nothing left to confirm
                return
            window_full = window_end - window_start >= WINDOW_CAP_MS
            pcm16 = self._ring.get_range(window_start, window_end)
            if not pcm16:
                self._flush_settled = True
                return
            if window_has_no_speech(pcm16):
                # Silent window: no inference (Whisper would echo the
                # vocabulary hint or hallucinate). The pass is a settled
                # no-op that still counts as covered, so the cadence and
                # the turn-end flush move on. A window that grew from the
                # origin is a superset of every earlier one, so silence
                # here means no tokens exist to protect; a FULL silent
                # window additionally slides the origin so the loop can
                # never wedge on it (speech after >5 s of silence still
                # gets its own window).
                self._covered_ms = max(self._covered_ms, window_end)
                self._last_pass_ms = window_end
                self._flush_settled = True
                if window_full:
                    self._origin_ms = window_end
                    self._prev_tokens = []
                    self._committed = 0
                    self._pending_full = None
                return
            wav_data = pcm16_to_wav_bytes(pcm16)
            async with self.inference_lock:
                # The utterance may have ended while this pass waited on
                # the lock (turn-end transcribe, hangup): its audio
                # verdict is stale before it is even computed.
                if generation != self._generation or not self._live():
                    return
                text = await self.run_inference(wav_data)
            if generation != self._generation or not self._live():
                return
            self._covered_ms = max(self._covered_ms, window_end)
            self._flush_settled = await self._emit_local_agreement(
                text, window_start, window_end, window_full
            )
            self._last_pass_ms = window_end
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — fail closed like L13
            # Same policy as the turn-end path: the fixed generic message
            # only, socket keeps serving, class name only in the log. The
            # latch stops error spam until the next utterance.
            self._error_latched = True
            logger.warning(
                "live streaming pass failed on /call/stream (%s)",
                type(exc).__name__,
            )
            try:
                await self._ws.send_json({
                    "type": "error",
                    "message": STT_FAILURE_MESSAGE,
                })
            except Exception:  # noqa: BLE001 — socket already dying
                pass
        finally:
            self._task = None
            if flush_pass:
                self._flush_remaining -= 1
                clean = (
                    self._pending_full is None
                    and self._flush_settled
                    and self._ring.total_ms() - self._covered_ms
                    < MIN_PASS_AUDIO_MS
                )
                if clean or self._flush_remaining <= 0:
                    if (
                        not clean
                        and self._flush_remaining <= 0
                        and not self._parity_failed
                        and not self._error_latched
                    ):
                        # Dirty end: words the bounds could not stabilize.
                        # Fail closed (L20) — no successful transcript may
                        # follow for this turn. Emitted from a task: the
                        # finally must not await (cancellation safety).
                        self._fail_parity_closed()
                    self._flushing = False
                    self._flush_remaining = 0
            # Catch-up: audio that arrived mid-pass is processed next,
            # never silently lost (bounded by one pass at a time).
            self._schedule_pass()

    def _fail_parity_closed(self) -> None:
        """L20 fail-closed latch for an unstabilizable turn: the generic
        error reaches the client (its existing visible fallback), the
        turn-end transcript will answer the same error instead of a
        successful incompatible text, and all pass scheduling stops."""
        self._parity_failed = True
        self._error_latched = True
        self._pending_full = None
        logger.warning(
            "live streaming could not stabilize finals parity "
            "within the confirmation bound on /call/stream"
        )

        async def _send() -> None:
            try:
                await self._ws.send_json({
                    "type": "error",
                    "message": STT_FAILURE_MESSAGE,
                })
            except Exception:  # noqa: BLE001 — socket already dying
                pass

        try:
            asyncio.get_running_loop().create_task(_send())
        except RuntimeError:  # pragma: no cover — loop already gone
            pass

    async def _signal_ring_gap_once(self) -> None:
        """Detects unrecoverable audio loss at the ring bound. When the
        earliest surviving audio is beyond the origin, the stretch in
        between can never be transcribed again: disclose it once per
        utterance (informational notice, no engine fallback) and move
        the origin to what still exists."""
        earliest = self._ring.earliest_ms()
        if self._origin_ms < earliest - RING_GAP_TOLERANCE_MS:
            self._origin_ms = earliest
            self._last_pass_ms = max(self._last_pass_ms, earliest)
            if self._gap_notified:
                return
            self._gap_notified = True
            try:
                await self._ws.send_json({
                    "type": "notice",
                    "reason": LIVE_NOTICE_AUDIO_GAP,
                })
            except Exception:  # noqa: BLE001 — socket already dying
                pass

    async def run_inference(self, wav_data: bytes) -> str:
        """One transcription on the connection's seam, in the executor,
        with the D8 hints when the backend accepts them. The caller owns
        ``inference_lock`` (one in-flight inference per call, FR-05)."""
        loop = asyncio.get_running_loop()
        if self._accepts_hints:
            return await loop.run_in_executor(
                None, self._transcribe_call, wav_data, ".wav",
                self._language, self._initial_prompt,
            )
        return await loop.run_in_executor(
            None, self._transcribe_call, wav_data, ".wav"
        )

    async def _emit_local_agreement(
        self,
        text: str,
        window_start: float,
        window_end: float,
        window_full: bool,
    ) -> bool:
        """Emits one pass's messages from the tokens it produced; returns
        whether the stream is settled (nothing left to confirm: no
        parked window and every token committed).

        FR-02 (L17): a token becomes final ONLY when two CONSECUTIVE
        passes agree on it — never from a single appearance, including
        the first pass over a full window and the tail of a full window
        in catch-up. A full window (measured at pass start, never from
        the ring's later growth — the L16 root cause) can never grow, so
        its first pass parks the window in ``_pending_full`` and the
        NEXT pass re-runs the SAME window as a confirmation.

        L19 finals parity: when the confirmation REVISES the tail, the
        agreed prefix commits and the window RE-PARKS (bounded by
        :data:`CONFIRM_PASS_LIMIT`) — a revision that stabilizes on the
        next pass commits then, so concatenated finals keep every word
        of the turn. When stabilization cannot be reached within the
        bound, the turn FAILS CLOSED (L20): the generic error message is
        emitted, the parity latch blocks the turn-end transcript (it
        must answer the same error instead of an incompatible successful
        text), and pass scheduling stops. Never a silent parity break,
        never unconfirmable words in a final.

        The origin only ever slides to a boundary the passes actually
        transcribed, so catch-up covers audio that arrived during slow
        inference in order (overlap allowed, gaps never).
        """
        tokens = (text or "").split()

        # Is this pass the confirmation of a parked full window?
        pending = self._pending_full
        if pending is not None:
            self._pending_full = None
        confirming = (
            pending is not None
            and pending[0] == window_start
            and pending[1] == window_end
        )
        compare_tokens = pending[2] if confirming else self._prev_tokens

        agreed = 0
        for prev, cur in zip(compare_tokens, tokens):
            if prev != cur:
                break
            agreed += 1
        if agreed > self._committed:
            fragment = " ".join(tokens[self._committed:agreed])
            self._committed = agreed
            self._finals_text = (
                f"{self._finals_text} {fragment}".strip() if self._finals_text
                else fragment
            )
            await self._ws.send_json({"type": "final", "text": fragment})

        if confirming and tokens == pending[2]:
            # The parked window has a matching second opinion: commit
            # everything it covers and slide past the confirmed boundary.
            self._origin_ms = window_end
            self._prev_tokens = []
            self._committed = 0
            tail_tokens: list[str] = []
        elif confirming and self._confirm_attempts < CONFIRM_PASS_LIMIT:
            # L19: a revised tail re-parks for one more stabilizing pass
            # — sliding now would drop the revision from the finals.
            self._confirm_attempts += 1
            self._pending_full = (window_start, window_end, tokens)
            self._prev_tokens = tokens
            tail_tokens = tokens[self._committed:]
        elif confirming:
            # L20 fail closed: the window never stabilizes within the
            # bound — the turn answers the generic error, never a
            # successful transcript these finals cannot match.
            self._fail_parity_closed()
            try:
                await self._ws.send_json({
                    "type": "error",
                    "message": STT_FAILURE_MESSAGE,
                })
            except Exception:  # noqa: BLE001 — socket already dying
                pass
            self._flushing = False
            self._flush_remaining = 0
            return True
        elif window_full:
            # First pass over a full window: the unagreed tail parks —
            # no final from a single appearance. The confirmation pass
            # re-runs this exact window (scheduled immediately).
            self._confirm_attempts = 1
            self._pending_full = (window_start, window_end, tokens)
            self._prev_tokens = tokens
            tail_tokens = tokens[self._committed:]
        else:
            # Growing window: the classic local-agreement loop.
            self._prev_tokens = tokens
            tail_tokens = tokens[self._committed:]

        tail = " ".join(tail_tokens)
        partial = (
            f"{self._finals_text} {tail}".strip() if tail else self._finals_text
        )
        if partial:
            await self._ws.send_json({"type": "partial", "text": partial})
        return self._pending_full is None and self._committed >= len(tokens)


async def handle_call_stream(
    websocket: WebSocket,
    pairing_store: Optional[PairingStore],
    transcriber: Optional[Transcriber],
    active_call: Optional[dict] = None,
    vad_engine: Optional[SileroVadEngine] = None,
    turn_detector: Optional[TurnDetector] = None,
    local_models_allowed: bool = True,
) -> None:
    """Handles a WebSocket /call/stream connection lifecycle: auth -> frames -> transcribe.

    ``transcriber`` is the live engine's DEDICATED transcriber (S4); when
    it has degraded to its fallback model it exposes ``notice_reason``,
    forwarded to the client exactly once per connection as an informational
    ``{"type": "notice"}`` — unlike an ``error`` message, a notice never
    triggers the client's transport fallback. A transcription that RAISES
    fails closed (L13): the socket answers ``{"type": "error"}`` with the
    fixed generic :data:`STT_FAILURE_MESSAGE` (exception detail never
    reaches the client) and keeps serving, while a transcription that
    succeeds with no speech stays a legitimate empty transcript.

    T3 (PRD-04): the auth message may carry the app ``locale`` (D8 —
    forced transcription language, unknown values fall back to whisper
    auto-detect), and a :class:`StreamingTranscriptionSession` drives the
    partial/final contract between ``stream start``/``stream stop``
    control messages. The turn-end ``transcribe`` command keeps today's
    exact reply shape and stays the ONLY dispatch source.

    S6 bounded increment (PRD-05 FR-01): the auth message may also opt
    into Silero speech/silence framing with ``"vad": true``. Opted-in
    connections receive exactly one ``{"type": "vad_status"}`` naming the
    VAD actually in use — ``silero`` when the model loaded, ``energy``
    plus a reason token when it could not (FR-06 fallback) — and then
    ``{"type": "vad"}`` transition events from a per-connection framer.
    Framing is advisory only: it never touches the turn-end transcribe
    path, and clients that do not opt in see zero new messages.

    S6/FR-02 amended (D18): an enabled local Smart Turn v3.2 detector
    (``turn_detector`` injected, else the env-selected default when
    ``HERDR_BRAIN_SMART_TURN_ENABLED`` is on) adds exactly one ADVISORY
    ``{"type": "vad", "event": "turn_complete", "verdict": ...,
    "prob": ...}`` at the client's ``stream stop``, computed from the last
    8 s of local audio. It never closes or reopens a turn, never sends a
    transcript and never touches the framing path (D3). A detector that
    cannot load answers one bounded ``unavailable`` event and the call
    continues exactly as today; disabled connections see zero new
    messages.
    """
    await websocket.accept()

    # 1. First-message auth
    try:
        first_msg = await asyncio.wait_for(websocket.receive(), timeout=5.0)
    except Exception:
        try:
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    token = None
    locale: Optional[str] = None
    wants_vad = False
    is_auth_shape = False
    if "text" in first_msg and first_msg["text"]:
        try:
            data = json.loads(first_msg["text"])
            if isinstance(data, dict) and data.get("type") == "auth":
                token = data.get("token") or data.get("device_token")
                locale = resolve_streaming_locale(data.get("locale"))
                # Untrusted socket input: only an explicit JSON true opts
                # in — anything else keeps the pre-S6 protocol untouched.
                wants_vad = data.get("vad") is True
                is_auth_shape = True
        except Exception:
            is_auth_shape = False

    if not is_auth_shape:
        await websocket.close(code=1008)
        return

    # Reject if pairing is enforced and token is invalid
    if pairing_store and pairing_store.enforced():
        valid, _reason = validate_device_token(pairing_store, token)
        if not valid:
            await websocket.close(code=1008)  # Policy Violation
            return

    await websocket.send_json({"type": "auth_ok"})

    # D13: forward a degraded-live-model notice exactly once per
    # connection — right after auth when the model degraded at boot, else
    # before the first transcript that survived the degradation.
    notice_sent = False

    async def send_stt_notice_once() -> None:
        nonlocal notice_sent
        if notice_sent:
            return
        reason = getattr(transcriber, "notice_reason", None)
        if reason:
            notice_sent = True
            await websocket.send_json({"type": "notice", "reason": reason})

    await send_stt_notice_once()

    # S6/FR-02 amended (D18): optional local semantic completion detector.
    # Disabled by default; an injected detector always wins (tests and a
    # future composition-root seam). The bounded load runs off the event
    # loop once per connection. It is resolved BEFORE vad_status so the
    # capability the client may rely on (``smart_turn``) is the truth
    # actually measured here: true only when enabled AND loaded.
    advisory_detector = (
        turn_detector if turn_detector is not None else (
            default_turn_detector() if local_models_allowed else None
        )
    )
    advisory_available = False
    advisory_failure_reason: Optional[str] = None
    if advisory_detector is not None:
        loop = asyncio.get_running_loop()
        try:
            advisory_available = await loop.run_in_executor(
                None, advisory_detector.ensure_loaded
            )
        except Exception:  # noqa: BLE001 — advisory only
            advisory_available = False
        if not advisory_available:
            advisory_failure_reason = (
                getattr(advisory_detector, "unavailable_reason", None)
                or "smart_turn_unavailable"
            )

    # S6/FR-06: negotiate the VAD actually in use for opted-in clients.
    # The engine load happens off the event loop (bounded, once per
    # process); every outcome — silero, or energy + a reason token when
    # the runtime/weights/session are unavailable — is reported
    # truthfully so the client's debug panel can show it. A framer is
    # per-connection: its h/c state never crosses calls. The same message
    # advertises whether the extender-only Smart Turn probe exists: a
    # false capability keeps the client on today's zero-round-trip path.
    framer = None
    if wants_vad:
        engine = vad_engine if vad_engine is not None else (
            SileroVadEngine() if local_models_allowed else None
        )
        loop = asyncio.get_running_loop()
        available = engine is not None and await loop.run_in_executor(None, engine.ensure_loaded)
        if available:
            framer = engine.new_framer()
            await websocket.send_json(
                {
                    "type": "vad_status",
                    "engine": "silero",
                    "reason": None,
                    "smart_turn": advisory_available,
                }
            )
        else:
            await websocket.send_json({
                "type": "vad_status",
                "engine": "energy",
                "reason": getattr(engine, "unavailable_reason", None) or "local_models_disabled",
                "smart_turn": advisory_available,
            })

    # Enabled but unavailable: one bounded event and the call continues
    # exactly as today (no turn change, no transcript, no probe).
    if advisory_detector is not None and not advisory_available:
        advisory_detector = None
        try:
            await websocket.send_json(
                {"type": "vad", "event": "unavailable", "reason": advisory_failure_reason}
            )
        except Exception:  # noqa: BLE001 — advisory only
            pass

    ring_buffer = AudioRingBuffer(max_seconds=120.0)
    # Diagnostics (level only, never content): how much audio this
    # connection delivered. Tells a silent phone uplink apart from a
    # transcription problem without logging any audio.
    stream_bytes = 0
    # L59: a probe already delivered this utterance's advisory. The legacy
    # ``stream stop`` advisory is suppressed for that turn so one turn never
    # produces two verdicts; non-probing clients keep the old behavior.
    probed_this_turn = False
    # D8: the vocabulary hint is read once per connection (operators edit
    # the env between runs, not mid-call).
    session = StreamingTranscriptionSession(
        websocket,
        transcriber,
        ring_buffer,
        language=locale,
        initial_prompt=live_vocabulary_hint(),
    )

    # 2. Frame streaming and transcription loop
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                stream_bytes += len(msg["bytes"])
                ring_buffer.append(msg["bytes"])
                session.on_audio()
                if framer is not None:
                    # S6 framing: bounded per-frame work (the framer caps
                    # its own backlog); any failure fails CLOSED to one
                    # unavailable event and silent energy fallback — the
                    # socket and the turn path must never notice.
                    try:
                        for event in framer.feed(msg["bytes"]):
                            await websocket.send_json({"type": "vad", **event})
                    except Exception as exc:  # noqa: BLE001 — framing is advisory
                        framer = None
                        logger.warning(
                            "live vad framing failed on /call/stream (%s)",
                            type(exc).__name__,
                        )
                        try:
                            await websocket.send_json(
                                {"type": "vad", "event": "unavailable"}
                            )
                        except Exception:
                            pass
            elif "text" in msg and msg["text"]:
                try:
                    payload = json.loads(msg["text"])
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                cmd = payload.get("type")
                if cmd == "diag":
                    # Level-only uplink diagnostics from the live client
                    # (numbers/opaque tokens only — never audio or
                    # transcript text): AudioContext state, capture mode,
                    # frame counters, analyser peak, the client's fallback
                    # reason and the served build. This is the one line
                    # that makes a deaf device attempt conclusive.
                    def _token(key: str, limit: int) -> str:
                        value = payload.get(key)
                        if not isinstance(value, str):
                            return "?"
                        return "".join(
                            ch for ch in value if ch >= " " and ch != "\x7f"
                        )[:limit] or "?"

                    def _n(key: str, cast=float) -> float:
                        value = payload.get(key)
                        if isinstance(value, bool) or not isinstance(value, (int, float)):
                            return -1.0
                        return cast(value)

                    logger.info(
                        "live client diag: mode=%s posted=%d sent=%d "
                        "queued=%d buffered=%d fails=%d maxrms=%.4f "
                        "ctx=%s build=%s reason=%s state=%s st=%s ext=%d probes=%d",
                        _token("mode", 12), int(_n("posted")), int(_n("sent")),
                        int(_n("queued")), int(_n("buffered")), int(_n("fails")),
                        _n("maxrms"), _token("ctx", 12),
                        _token("build", 24), _token("reason", 24),
                        _token("state", 14), _token("st", 12),
                        int(_n("ext")), int(_n("probes")),
                    )
                    continue
                if cmd == "turn_probe":
                    # L59 extender-only: the client's energy endpointer is
                    # still the decision owner — this answers ONE advisory
                    # over the same bounded ring window as the stop seam.
                    # It never touches turn state, never sends a transcript
                    # and never closes anything (D3).
                    probed_this_turn = True
                    if advisory_detector is None:
                        await websocket.send_json({
                            "type": "turn_probe_result",
                            "verdict": VERDICT_UNAVAILABLE,
                        })
                        continue
                    duration_ms = _bounded_probe_ms(payload.get("duration_ms"), 0.0)
                    pre_roll_ms = _bounded_probe_ms(
                        payload.get("pre_roll_ms"), DEFAULT_PREROLL_MS
                    )
                    window_ms = duration_ms + pre_roll_ms
                    if window_ms <= 0:
                        window_ms = TURN_DETECTOR_WINDOW_MS
                    window_ms = min(window_ms, TURN_DETECTOR_WINDOW_MS)
                    total_ms = ring_buffer.total_ms()
                    start_ms = max(0.0, total_ms - window_ms)
                    audio = pcm16_to_audio(
                        ring_buffer.get_range(start_ms, total_ms)
                    )
                    if audio.size:
                        try:
                            loop = asyncio.get_running_loop()
                            prob, verdict, reason = await loop.run_in_executor(
                                None, advisory_detector.classify, audio
                            )
                        except Exception:  # noqa: BLE001 — advisory only
                            prob, verdict, reason = (
                                None,
                                VERDICT_UNAVAILABLE,
                                UNAVAILABLE_INFERENCE_ERROR,
                            )
                    else:
                        prob, verdict, reason = (
                            None,
                            VERDICT_UNAVAILABLE,
                            "smart_turn_bad_audio",
                        )
                    if verdict not in (VERDICT_COMPLETE, VERDICT_INCOMPLETE):
                        verdict = VERDICT_UNAVAILABLE
                        reason = reason or UNAVAILABLE_INFERENCE_ERROR
                    # Level-only probe diagnostics (numbers/tokens only).
                    logger.info(
                        "live turn probe diag: verdict=%s prob=%s window_ms=%d "
                        "window_bytes=%d",
                        verdict,
                        "%.3f" % _bounded_prob(prob) if _bounded_prob(prob) is not None else "?",
                        int(window_ms),
                        int(audio.nbytes),
                    )
                    result: dict = {
                        "type": "turn_probe_result",
                        "verdict": verdict,
                    }
                    prob_value = _bounded_prob(prob)
                    if verdict != VERDICT_UNAVAILABLE and prob_value is not None:
                        result["prob"] = prob_value
                    if verdict == VERDICT_UNAVAILABLE:
                        result["reason"] = reason or UNAVAILABLE_INFERENCE_ERROR
                        # A detector that cannot answer stops being a
                        # capability for this connection; the client then
                        # closes exactly like today.
                        advisory_detector = None
                    try:
                        await websocket.send_json(result)
                    except Exception:  # noqa: BLE001 — socket already dying
                        pass
                    continue
                if cmd == "transcribe":
                    # L20 fail-closed parity: a turn whose words could not
                    # stabilize within the bounds answers the generic
                    # error — never a successful transcript its finals
                    # cannot match (no inference runs for it either).
                    if session.parity_failed:
                        await websocket.send_json({
                            "type": "error",
                            "message": STT_FAILURE_MESSAGE,
                        })
                        continue
                    # L20 ordering: a pending turn-end flush finishes
                    # FIRST — stop+transcribe arrive back-to-back and the
                    # inline transcript would otherwise win the inference
                    # lock ahead of the flush passes, emitting a
                    # transcript with no preceding finals.
                    await session.finish_flush()
                    if session.parity_failed:
                        await websocket.send_json({
                            "type": "error",
                            "message": STT_FAILURE_MESSAGE,
                        })
                        continue
                    duration_ms = float(payload.get("duration_ms", 0))
                    pre_roll_ms = float(payload.get("pre_roll_ms", DEFAULT_PREROLL_MS))
                    pcm16 = ring_buffer.get_window(duration_ms=duration_ms, pre_roll_ms=pre_roll_ms)
                    # Diagnostics (level only): connection audio volume vs
                    # the turn window's peak sample, so a silent uplink is
                    # distinguishable from a transcription failure.
                    if pcm16:
                        samples = np.frombuffer(
                            pcm16[: len(pcm16) - (len(pcm16) % 2)], dtype=np.int16
                        )
                        peak = int(np.abs(samples).max()) if samples.size else 0
                    else:
                        peak = 0
                    logger.info(
                        "live transcribe diag: conn_bytes=%d window_bytes=%d peak=%d",
                        stream_bytes, len(pcm16), peak,
                    )
                    wav_data = pcm16_to_wav_bytes(pcm16)
                    text = ""
                    failure: Optional[Exception] = None
                    if transcriber is not None and window_has_no_speech(pcm16):
                        # Silent (or sliver) turn: no inference — the
                        # client keeps its existing not-understood path
                        # on an empty successful transcript.
                        logger.debug(
                            "live turn-end window has no speech; inference skipped"
                        )
                    elif transcriber is not None:
                        try:
                            # One inference per call (FR-05): the
                            # turn-end transcribe shares the streaming
                            # session's inference lock — a partial pass
                            # already in flight finishes first, and this
                            # full-window pass runs alone.
                            async with session.inference_lock:
                                text = await session.run_inference(wav_data)
                        except Exception as exc:  # noqa: BLE001 — every failure fails closed
                            failure = exc
                    if failure is not None:
                        # L13 fail-closed: a transcription exception is an
                        # error, never an empty successful transcript the UI
                        # would read as "not understood" (the client then
                        # recovers via its PRD-03 fallback). Only the
                        # exception's class name is logged — its message can
                        # carry paths, model names or configuration values.
                        logger.warning(
                            "live transcription failed on /call/stream (%s)",
                            type(failure).__name__,
                        )
                        await websocket.send_json({
                            "type": "error",
                            "message": STT_FAILURE_MESSAGE,
                        })
                    else:
                        await send_stt_notice_once()
                        text_str = (text or "").strip()
                        await websocket.send_json({
                            "type": "transcript",
                            "text": text_str,
                            "duration_ms": duration_ms,
                            "pre_roll_ms": pre_roll_ms,
                        })
                        # Prefix-overlap protection: the finished turn's
                        # audio is behind us — the next utterance's
                        # partials start a fresh window.
                        session.reset_after_transcript()
                    probed_this_turn = False
                elif cmd == "stream":
                    # T3 utterance gating: the client's VAD onset opens
                    # the partial loop, its silence close ends it. This
                    # keeps idle silence and the agent's own playback off
                    # the GPU (S5: partials run WHILE SPEAKING).
                    if payload.get("action") == "start":
                        session.start_utterance()
                        probed_this_turn = False
                    elif payload.get("action") == "stop":
                        session.stop_utterance()
                        # S6/FR-02 amended: one ADVISORY verdict over the
                        # last 8 s ending at the client's silence close.
                        # This never closes/reopens a turn (D3), sends no
                        # transcript, and any failure stops the advisory
                        # stream with one bounded `unavailable`.
                        #
                        # L59: a client that already probed this utterance
                        # got its advisory before the close, so the legacy
                        # stop verdict is suppressed — one turn, one verdict.
                        already_probed = probed_this_turn
                        probed_this_turn = False
                        if advisory_detector is not None and not already_probed:
                            total_ms = ring_buffer.total_ms()
                            start_ms = max(
                                0.0, total_ms - TURN_DETECTOR_WINDOW_MS
                            )
                            audio = pcm16_to_audio(
                                ring_buffer.get_range(start_ms, total_ms)
                            )
                            if audio.size:
                                try:
                                    loop = asyncio.get_running_loop()
                                    prob, verdict, reason = await loop.run_in_executor(
                                        None, advisory_detector.classify, audio
                                    )
                                except Exception:  # noqa: BLE001 — advisory only
                                    prob, verdict, reason = (
                                        None,
                                        VERDICT_UNAVAILABLE,
                                        UNAVAILABLE_INFERENCE_ERROR,
                                    )
                                if verdict == VERDICT_UNAVAILABLE:
                                    advisory_detector = None
                                    try:
                                        await websocket.send_json({
                                            "type": "vad",
                                            "event": "unavailable",
                                            "reason": reason,
                                        })
                                    except Exception:  # noqa: BLE001
                                        pass
                                else:
                                    try:
                                        await websocket.send_json({
                                            "type": "vad",
                                            "event": "turn_complete",
                                            "verdict": verdict,
                                            "prob": float(prob),
                                        })
                                    except Exception:  # noqa: BLE001
                                        pass
                elif cmd == "clear":
                    ring_buffer.clear()
                    session.clear()
                    probed_this_turn = False
                    if framer is not None:
                        framer.reset()  # framing state dies with the ring
                elif cmd == "ping":
                    await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("call_stream connection ended: %s", exc)
    finally:
        await session.aclose()
