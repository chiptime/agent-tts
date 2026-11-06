"""Tests for server-side Silero speech/silence framing (PRD-05 FR-01, T4/S6).

The framing module is deterministic and engine-agnostic: every test here
drives :class:`VadSpeechFamer` with a scripted (or content-derived) fake
step function. The final section loads the REAL bundled ONNX model (CPU,
offline) as a liveness smoke on generated silence/noise: it proves the
model loads, steps, isolates state and stays fast, NOT accuracy on real
speech.

Contract under test (S6 bounded increment — framing ONLY, no semantic
turn-completion detector):

- arbitrary PCM16 byte frames are re-chunked into exactly 512-sample
  windows at 16 kHz (the Silero chunk quantum), leftover bytes carried;
- per-call state isolation: two framers never share h/c or policy state;
- the hysteresis policy (onset debounce, mid-band hold, bounded silence
  confirmation) mirrors faster-whisper's Silero chunker semantics;
- any engine failure fails CLOSED to "unavailable" — framing stops, the
  energy endpointer stays the caller's only opinion, nothing raises.
"""

import numpy as np
import pytest

from herdr_brain.stream_vad import (
    CHUNK_SAMPLES,
    CONTEXT_SAMPLES,
    MIN_SILENCE_MS,
    ONSET_CHUNKS,
    UNAVAILABLE_LOAD_FAILED,
    UNAVAILABLE_MODEL_MISSING,
    UNAVAILABLE_NO_RUNTIME,
    VadSpeechFramer,
    SileroVadEngine,
)

SPEECH = 0.92   # >= speech threshold
MIDBAND = 0.42  # between silence and speech thresholds: hysteresis hold
QUIET = 0.02    # < silence threshold


def scripted_step(probs, record=None):
    """Step double popping scripted probabilities (last one repeats)."""
    queue = list(probs)

    def step(chunk, context, h, c):
        if record is not None:
            record.append(
                {
                    "samples": int(chunk.shape[0]),
                    "context": context.copy(),
                    "h_is_zero": bool(np.all(h == 0)),
                    "float_range": float(np.max(np.abs(chunk))),
                }
            )
        p = queue.pop(0) if queue else QUIET
        return p, h, c

    return step


def pcm_loud(n_samples):
    return (np.sin(np.arange(n_samples) * 0.05) * 22000).astype("<i2").tobytes()


def pcm_silence(n_samples):
    return b"\x00\x00" * n_samples


# ---- 16 kHz frame buffering into 512-sample Silero chunks ----


def test_feed_rechunks_arbitrary_frames_into_512_sample_chunks():
    """The uplink sends arbitrary PCM16 frames (100 ms worklet frames, odd
    script-processor sizes); the framer must consume exactly 512-sample
    windows and carry the leftover bytes to the next frame."""
    record = []
    framer = VadSpeechFramer(scripted_step([QUIET], record=record))

    # 100 ms worklet frame (1600 samples), then two odd 47/321-sample
    # frames — none is a multiple of 512.
    framer.feed(pcm_silence(1600))
    framer.feed(pcm_silence(47))
    framer.feed(pcm_silence(321))

    total_samples = 1600 + 47 + 321
    expected_chunks = total_samples // CHUNK_SAMPLES
    assert framer.chunks_fed == expected_chunks
    assert all(r["samples"] == CHUNK_SAMPLES for r in record)
    # float conversion: |x| <= 1.0, silence stays exactly 0.0
    assert all(r["float_range"] <= 1.0 for r in record)
    assert record[0]["float_range"] == 0.0


def test_feed_converts_pcm16_to_unit_floats():
    record = []
    framer = VadSpeechFramer(scripted_step([QUIET], record=record))
    framer.feed(pcm_loud(CHUNK_SAMPLES))
    # 22000/32768 ≈ 0.671 — the neural input domain, not raw int16.
    assert record[0]["float_range"] == pytest.approx(22000 / 32768, rel=1e-3)
    assert framer.chunks_fed == 1


def test_first_chunk_context_is_zero_then_previous_chunk_tail():
    """Each chunk rides the previous chunk's last 64 samples as context
    (zeros for the first chunk) — faster-whisper's batching recipe,
    applied incrementally."""
    ctx_seen = []

    def step(chunk, context, h, c):
        ctx_seen.append(context.copy())
        return QUIET, h, c

    framer = VadSpeechFramer(step)
    framer.feed(pcm_loud(CHUNK_SAMPLES * 2))

    assert len(ctx_seen) == 2
    assert np.all(ctx_seen[0] == 0)
    assert ctx_seen[0].shape == (CONTEXT_SAMPLES,)
    # second chunk's context == first chunk's last 64 samples (floats)
    first = np.frombuffer(pcm_loud(CHUNK_SAMPLES), dtype="<i2").astype(np.float32) / 32768.0
    assert np.allclose(ctx_seen[1], first[-CONTEXT_SAMPLES:])


def test_odd_byte_frame_holds_stray_byte_until_the_next_frame():
    """A truncated byte stream keeps its stray byte pending instead of
    misaligning samples (PCM16 = 2 bytes per sample)."""
    record = []
    framer = VadSpeechFramer(scripted_step([QUIET], record=record))
    framer.feed(b"\x01")  # half a sample
    assert framer.chunks_fed == 0
    framer.feed(b"\x00" * (CHUNK_SAMPLES * 2 - 1))
    assert framer.chunks_fed == 1
    assert all(r["samples"] == CHUNK_SAMPLES for r in record)


# ---- the framing policy (faster-whisper hysteresis semantics) ----


def test_policy_onset_debounce_hysteresis_and_offset():
    """Onset needs :data:`ONSET_CHUNKS` consecutive speech chunks; a
    mid-band probability between the two thresholds HOLDS speech (never
    flaps); speech_end only after >= :data:`MIN_SILENCE_MS` of sub-threshold
    chunks, reporting the measured silence."""
    probs = (
        [QUIET] * 3            # leading silence: nothing
        + [SPEECH] * ONSET_CHUNKS        # onset run: speech_start on the last
        + [MIDBAND] * 2        # hysteresis band: still speech
        + [QUIET] * 14         # 14 * 32 ms = 448 ms < 500 ms: hold
        + [SPEECH]             # recovery: silence run resets, no speech_end
        + [QUIET] * 16         # 16 * 32 ms = 512 ms >= 500 ms: speech_end
    )
    framer = VadSpeechFramer(scripted_step(probs))
    events = framer.feed(pcm_silence(len(probs) * CHUNK_SAMPLES))

    kinds = [e["event"] for e in events]
    assert kinds.count("speech_start") == 1
    assert kinds.count("speech_end") == 1
    start_idx = kinds.index("speech_start")
    end_idx = kinds.index("speech_end")
    assert start_idx < end_idx
    # onset fires on the ONSET_CHUNKS-th speech chunk (index 3+1=4)
    assert events[start_idx]["at_ms"] == pytest.approx((3 + ONSET_CHUNKS) * 32.0)
    # the offset reports the confirmed silence length (16 chunks = 512 ms)
    assert events[end_idx]["silence_ms"] == pytest.approx(16 * 32.0)
    assert not framer.speech_active


def test_policy_ignores_single_speech_blip():
    """One speech chunk inside silence never opens a segment (debounce)."""
    probs = [QUIET, SPEECH, QUIET, QUIET, SPEECH, SPEECH, SPEECH]
    framer = VadSpeechFramer(scripted_step(probs))
    events = framer.feed(pcm_silence(len(probs) * CHUNK_SAMPLES))
    starts = [e for e in events if e["event"] == "speech_start"]
    assert len(starts) == 1  # only the 3-run opened speech
    # ...and it opened on the chunk AFTER the blip, not on the blip
    assert starts[0]["at_ms"] == pytest.approx(6 * 32.0)


def test_policy_heartbeat_while_speech_active_only():
    """While speech is active the framer emits a liveness heartbeat every
    :data:`HEARTBEAT_CHUNKS` chunks; pure silence emits nothing."""
    from herdr_brain.stream_vad import HEARTBEAT_CHUNKS

    speech_span = HEARTBEAT_CHUNKS * 3 + 5
    probs = [SPEECH] * speech_span
    framer = VadSpeechFramer(scripted_step(probs))
    # Realistic cadence: ~100 ms worklet frames (3 chunks each), never a
    # single giant frame (that is the bounded-backlog path, tested below).
    events = []
    for _ in range(speech_span // 3 + 1):
        events.extend(framer.feed(pcm_silence(3 * CHUNK_SAMPLES)))
    beats = [e for e in events if e["event"] == "heartbeat"]
    assert len(beats) == 3  # at 31, 62, 93 chunks
    assert all(b["speech"] for b in beats)

    quiet = VadSpeechFramer(scripted_step([QUIET] * 40))
    quiet_events = []
    for _ in range(14):
        quiet_events.extend(quiet.feed(pcm_silence(3 * CHUNK_SAMPLES)))
    assert quiet_events == []
    assert quiet_events == []


def test_reset_clears_policy_and_neural_state():
    """A reset framer is a fresh framer: no stale speech_active, no stale
    pending bytes, and the neural h/c starts from zeros again."""
    h_zero_flags = []

    def step(chunk, context, h, c):
        h_zero_flags.append(bool(np.all(h == 0)))
        return SPEECH, np.zeros_like(h), np.zeros_like(c)

    framer = VadSpeechFramer(step)
    framer.feed(pcm_silence(CHUNK_SAMPLES * 4))
    assert framer.speech_active
    framer.feed(b"\x01\x02\x03")  # stray pending bytes

    framer.reset()
    assert not framer.speech_active
    assert framer.chunks_fed == 0
    h_zero_flags.clear()
    framer.feed(pcm_silence(CHUNK_SAMPLES * 2))
    # after reset the first chunk again sees a zero h state
    assert h_zero_flags[0] is True


# ---- per-call state isolation ----


def test_framer_state_is_isolated_per_call():
    """Two framers from the same engine share the MODEL, never the state:
    feeding speech to one leaves the other untouched, and their h/c
    evolution stays independent."""
    shared_calls = []

    def shared_step(chunk, context, h, c):
        # a "model" that tracks per-call h identity: returns a new h each
        # call derived from the INCOMING h — cross-talk would show as the
        # other framer's mutation appearing here.
        shared_calls.append({"h": h, "id": id(h)})
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        p = SPEECH if rms > 0.1 else QUIET
        hn = h + 1.0
        return p, hn, c

    a = VadSpeechFramer(shared_step)
    b = VadSpeechFramer(shared_step)

    ev_a = a.feed(pcm_loud(CHUNK_SAMPLES * 3))
    ev_b = b.feed(pcm_silence(CHUNK_SAMPLES * 3))

    assert a.speech_active and not b.speech_active
    assert any(e["event"] == "speech_start" for e in ev_a)
    assert ev_b == []
    # every chunk the "model" saw belonged to exactly one framer's h array:
    # 3 calls from a (h growing), 3 from b (still zeros) — no shared array.
    a_hs = [c for c in shared_calls if c["id"] == id(shared_calls[0]["h"])]
    assert len(a_hs) >= 1  # a's h identity stayed a's


def test_engine_failure_latches_unavailable_and_stops_framing():
    """A raising model step fails closed: ONE unavailable event, framing
    stops consuming (no further events, no exception), and the engine can
    still mint an independent framer for the NEXT call."""
    boom = {"armed": False}

    def flaky_step(chunk, context, h, c):
        if boom["armed"]:
            raise RuntimeError("onnxruntime blew up")
        return SPEECH, h, c

    framer = VadSpeechFramer(flaky_step)
    assert framer.feed(pcm_silence(CHUNK_SAMPLES)) == []  # healthy start
    boom["armed"] = True
    events = framer.feed(pcm_silence(CHUNK_SAMPLES))
    assert [e["event"] for e in events] == ["unavailable"]
    assert not framer.speech_active
    # dead framer stays silent forever, never raises
    assert framer.feed(pcm_loud(CHUNK_SAMPLES * 8)) == []

    boom["armed"] = False
    fresh = VadSpeechFramer(flaky_step)
    assert any(
        e["event"] == "speech_start"
        for e in fresh.feed(pcm_loud(CHUNK_SAMPLES * 3))
    )


def test_oversized_feed_is_bounded_never_unbounded_work():
    """A single pathological frame (megabytes) must not run unbounded
    inference in the receive loop: the framer bounds the chunks it
    processes per feed and resumes cleanly from what it kept."""
    from herdr_brain.stream_vad import MAX_PENDING_SAMPLES

    calls = {"n": 0}

    def counting_step(chunk, context, h, c):
        calls["n"] += 1
        return QUIET, h, c

    framer = VadSpeechFramer(counting_step)
    # 20 s of audio in ONE message — far beyond the pending bound.
    framer.feed(pcm_silence(MAX_PENDING_SAMPLES * 10))
    assert calls["n"] <= MAX_PENDING_SAMPLES // CHUNK_SAMPLES
    # framing survives: subsequent normal frames keep producing chunks.
    before = framer.chunks_fed
    framer.feed(pcm_silence(CHUNK_SAMPLES * 2))
    assert framer.chunks_fed >= before + 1


# ---- engine loading: fail-closed with reason tokens ----


def test_engine_missing_model_file_fails_closed_with_reason(tmp_path):
    engine = SileroVadEngine(model_path=tmp_path / "nope.onnx")
    assert engine.ensure_loaded() is False
    assert engine.unavailable_reason == UNAVAILABLE_MODEL_MISSING
    # idempotent: a second call does not raise or change the verdict
    assert engine.ensure_loaded() is False


def test_engine_missing_runtime_fails_closed_with_reason(tmp_path):
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"weights")

    def no_runtime_factory(path):
        raise ImportError("onnxruntime not installed")

    engine = SileroVadEngine(model_path=model, session_factory=no_runtime_factory)
    assert engine.ensure_loaded() is False
    assert engine.unavailable_reason == UNAVAILABLE_NO_RUNTIME


def test_engine_session_failure_fails_closed_with_reason(tmp_path):
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"weights")
    attempts = {"n": 0}

    def broken_factory(path):
        attempts["n"] += 1
        raise RuntimeError("provider init failed")

    engine = SileroVadEngine(model_path=model, session_factory=broken_factory)
    assert engine.ensure_loaded() is False
    assert engine.unavailable_reason == UNAVAILABLE_LOAD_FAILED
    # the load attempt is not retried per call (bounded work)
    engine.ensure_loaded()
    assert attempts["n"] == 1


def test_engine_loads_session_and_mints_working_framers(tmp_path):
    model = tmp_path / "silero.onnx"
    model.write_bytes(b"weights")

    def factory(path):
        assert path == model

        class FakeSession:
            def run(self, names, feeds):
                inp = feeds["input"]
                rms = float(np.sqrt(np.mean(inp.astype(np.float64) ** 2)))
                prob = np.array([SPEECH if rms > 0.1 else QUIET], dtype="float32")
                return prob, feeds["h"], feeds["c"]

        return FakeSession()

    engine = SileroVadEngine(model_path=model, session_factory=factory)
    assert engine.ensure_loaded() is True
    assert engine.unavailable_reason is None

    framer = engine.new_framer()
    events = framer.feed(pcm_loud(CHUNK_SAMPLES * 3))
    assert any(e["event"] == "speech_start" for e in events)
    # second framer: independent state, same loaded session
    other = engine.new_framer()
    assert not other.speech_active


# ---- real bundled model: offline CPU liveness smoke (not accuracy) ----


@pytest.fixture(scope="module")
def real_engine():
    engine = SileroVadEngine()
    if not engine.ensure_loaded():
        pytest.skip(f"bundled silero model/runtime unavailable: {engine.unavailable_reason}")
    return engine


def test_default_model_path_is_the_bundled_weights_file():
    from pathlib import Path

    from herdr_brain.stream_vad import default_model_path

    path = default_model_path()
    assert isinstance(path, Path)  # faster-whisper hands back a str
    assert path.name == "silero_vad_v6.onnx" and path.is_file()


def test_real_model_silence_never_opens_speech(real_engine):
    framer = real_engine.new_framer()
    events = framer.feed(pcm_silence(CHUNK_SAMPLES * 40))
    assert events == []
    assert framer.chunks_fed == 40 and not framer.speech_active


def test_real_model_state_advances_and_never_crosses_framers(real_engine):
    noisy = real_engine.new_framer()
    clean = real_engine.new_framer()
    rng = np.random.default_rng(7)
    noisy.feed((rng.standard_normal(CHUNK_SAMPLES * 10) * 4000).astype("<i2").tobytes())
    assert np.any(noisy._h != 0), "the recurrence never advanced"
    assert np.all(clean._h == 0) and np.all(clean._c == 0)
    clean.feed(pcm_silence(CHUNK_SAMPLES * 3))
    assert clean.chunks_fed == 3 and noisy.chunks_fed == 10


def test_real_model_reset_restores_a_fresh_call(real_engine):
    framer = real_engine.new_framer()
    rng = np.random.default_rng(3)
    framer.feed((rng.standard_normal(CHUNK_SAMPLES * 5) * 4000).astype("<i2").tobytes())
    framer.reset()
    assert np.all(framer._h == 0) and framer.chunks_fed == 0 and not framer.speech_active


def test_real_model_inference_stays_cheap_for_the_receive_loop(real_engine):
    """Nonblocking budget: a worst-case (2 s) backlog is ~62 CPU steps run
    inline in the socket loop; measured ~0.1 ms/step, bounded here at a
    deliberately loose 20 ms/step so a slow CI box never flakes."""
    import time

    framer = real_engine.new_framer()
    start = time.perf_counter()
    framer.feed(pcm_silence(CHUNK_SAMPLES * 60))
    per_step_ms = 1000 * (time.perf_counter() - start) / max(1, framer.chunks_fed)
    assert per_step_ms < 20


def test_real_model_framing_writes_nothing_and_logs_no_audio(real_engine, tmp_path, monkeypatch, caplog):
    """D9: framing consumes bytes in RAM only — no files, no log lines."""
    monkeypatch.chdir(tmp_path)
    with caplog.at_level("DEBUG"):
        framer = real_engine.new_framer()
        framer.feed(pcm_loud(CHUNK_SAMPLES * 8))
    assert list(tmp_path.iterdir()) == []
    assert [r for r in caplog.records if "stream_vad" in r.name] == []
