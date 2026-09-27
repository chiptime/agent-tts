"""TD-01 piper sentence-group streaming tests.

Covers the persistent-process contract of PiperTTSProvider.synthesize_stream
(one Popen spawn for the whole text, one complete RIFF-parsed WAV per group,
stop_checker termination, missing-binary error surface) and the capability
driven consumption of that stream by the _speak_pipelined producer.
"""

import array
import asyncio
import contextlib
import io
import struct
import subprocess
import threading
import unittest
from unittest import mock

import pytest

from agent_tts.boundaries import BoundaryMap, SynthesisResult
from agent_tts.cli import _speak_pipelined, group_for_chunk, split_sentence_groups, use_pipelined_stream
from agent_tts.providers import piper as piper_mod
from agent_tts.providers.piper import PiperTTSProvider, _read_stream_wav
from agent_tts.wav import pcm_to_wav


# -- fake piper process -------------------------------------------------------


class FakeStdout:
    """stdout pipe stand-in: reads pull from what the fake piper has written.

    Unlike io.BytesIO (whose single position makes a post-write read hit EOF),
    a real pipe has independent write and read ends, so reads consume from the
    buffer without disturbing writes.
    """

    def __init__(self):
        self._buffer = bytearray()

    def write(self, data):
        self._buffer.extend(data)
        return len(data)

    def read(self, size=-1):
        if size is None or size < 0:
            data = bytes(self._buffer)
            del self._buffer[:]
            return data
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def getvalue(self):
        return bytes(self._buffer)


class FakeStdin(io.BytesIO):
    """stdin pipe that answers each written line with the fake piper output."""

    def __init__(self, on_line):
        super().__init__()
        self.close_count = 0
        self.lines = []
        self._on_line = on_line

    def write(self, data):
        result = super().write(data)
        if data.endswith(b"\n"):
            line = bytes(data).decode("utf-8").strip()
            self.lines.append(line)
            self._on_line(line)
        return result

    def close(self):
        self.close_count += 1
        super().close()


class FakePopen:
    """Stand-in for subprocess.Popen simulating a persistent piper process."""

    instances = []
    responder = None  # callable(line_text) -> wav bytes appended to stdout

    def __init__(self, argv, stdin=None, stdout=None, stderr=None):
        self.argv = argv
        self.stdout_arg = stdout
        self.stderr_arg = stderr
        self.stdin = FakeStdin(on_line=self._handle_line)
        self.stdout = FakeStdout()
        self.exit_code = None  # None = still running
        self.terminate_count = 0
        FakePopen.instances.append(self)

    def _handle_line(self, line):
        if self.exit_code is not None or FakePopen.responder is None:
            return
        self.stdout.write(FakePopen.responder(line))

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminate_count += 1
        self.exit_code = -15

    def wait(self, timeout=None):
        if self.exit_code is None and self.stdin.close_count:
            self.exit_code = 0
        if self.exit_code is None:
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
            raise AssertionError("fake process never exits")
        return self.exit_code


@pytest.fixture
def fake_popen(monkeypatch):
    FakePopen.instances = []
    FakePopen.responder = None
    monkeypatch.setattr(piper_mod.subprocess, "Popen", FakePopen)
    return FakePopen


@pytest.fixture
def provider(tmp_path):
    """Provider pointing at a fake executable binary so is_available() passes."""
    binary = tmp_path / "piper"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return PiperTTSProvider(binary_path=str(binary))


def line_wavs(count, pcm_frames=40):
    """Builds the list of WAVs the fake piper returns, one per stdin line."""
    wavs = []
    for i in range(count):
        pcm = struct.pack("<h", i + 1) * pcm_frames
        wavs.append(pcm_to_wav(pcm, sample_rate=24000, channels=1))
    return wavs


def sequential_responder(wavs):
    """Responder handing out the next WAV per line."""

    def respond(line):
        idx = respond.seen
        respond.seen += 1
        if idx >= len(wavs):
            return b""
        return wavs[idx]

    respond.seen = 0
    return respond


def parse_wav_data(data):
    """Independent RIFF walker: returns the data chunk payload of a WAV."""
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    riff_size = struct.unpack("<I", data[4:8])[0]
    assert len(data) == riff_size + 8
    pos = 12
    while pos + 8 <= len(data):
        chunk_id, size = struct.unpack("<4sI", data[pos:pos + 8])
        pos += 8
        payload = data[pos:pos + size]
        pos += size + (size % 2)
        if chunk_id == b"data":
            return payload
    raise AssertionError("no data chunk found")


def long_sentences(count):
    """Builds a text that splits into exactly `count` groups (each sentence > 125 chars)."""
    sentence = (
        "The quick brown fox jumps over the lazy dog while the rain keeps "
        "falling softly on the old tin roof of the barn across the field. "
    )
    return sentence * count


# -- provider: persistent process and WAV framing ------------------------------


def test_n_groups_yield_n_wavs_from_exactly_one_spawn(fake_popen, provider):
    wavs = line_wavs(4)
    FakePopen.responder = sequential_responder(wavs)
    text = long_sentences(4)
    assert len(split_sentence_groups(text)) == 4

    out = list(provider.synthesize_stream(text, "es_ES-davefx-medium"))

    assert len(out) == 4
    assert len(FakePopen.instances) == 1  # one process for the whole text
    proc = FakePopen.instances[0]
    assert proc.stdin.lines == split_sentence_groups(text)  # every group, in order
    assert out == wavs  # every complete WAV, in group order
    assert proc.stdin.close_count >= 1  # orderly shutdown
    assert proc.wait() == 0


def test_every_yielded_chunk_is_a_complete_wav(fake_popen, provider):
    wavs = line_wavs(3)
    FakePopen.responder = sequential_responder(wavs)

    for chunk in provider.synthesize_stream(long_sentences(3), "voice"):
        pcm = parse_wav_data(chunk)  # raises unless the WAV is complete
        assert len(pcm) == 80
        assert chunk == pcm_to_wav(pcm, sample_rate=24000, channels=1)


def test_riff_walk_survives_chunks_before_data(fake_popen, provider):
    # A LIST chunk between fmt and data shifts the data offset: a hardcoded
    # 44-byte header assumption would corrupt the stream here.
    pcm = b"\x0a\x0b" * 10
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
    list_chunk = b"LIST" + struct.pack("<I", 4) + b"INFO"
    data_chunk = b"data" + struct.pack("<I", len(pcm)) + pcm
    body = b"WAVE" + fmt + list_chunk + data_chunk
    shifted_wav = b"RIFF" + struct.pack("<I", len(body)) + body
    FakePopen.responder = lambda line: shifted_wav

    out = list(provider.synthesize_stream(long_sentences(2), "voice"))

    assert out == [shifted_wav, shifted_wav]
    assert parse_wav_data(out[0]) == pcm


class TestReadStreamWav(unittest.TestCase):
    def test_drains_trailing_chunks_after_data(self):
        pcm = b"\x01\x02" * 8
        fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
        data_chunk = b"data" + struct.pack("<I", len(pcm)) + pcm
        list_chunk = b"LIST" + struct.pack("<I", 4) + b"INFO"
        body = b"WAVE" + fmt + data_chunk + list_chunk
        wav = b"RIFF" + struct.pack("<I", len(body)) + body

        result = _read_stream_wav(io.BytesIO(wav))

        self.assertEqual(result, wav)  # trailing chunk drained: next WAV starts clean

    def test_clean_end_of_stream(self):
        self.assertIsNone(_read_stream_wav(io.BytesIO(b"")))

    def test_truncation_and_garbage_raise(self):
        truncated = b"RIFF" + struct.pack("<I", 500) + b"WAVE" + b"fmt "
        with self.assertRaises(ValueError):
            _read_stream_wav(io.BytesIO(truncated))
        with self.assertRaises(ValueError):
            _read_stream_wav(io.BytesIO(b"garbage on stdout, no riff"))


def test_stop_checker_terminates_process_and_ends_cleanly(fake_popen, provider):
    wavs = line_wavs(4)
    FakePopen.responder = sequential_responder(wavs)
    state = {"seen": 0}

    def stop_after_first():
        return state["seen"] >= 1

    gen = provider.synthesize_stream(long_sentences(4), "voice", stop_checker=stop_after_first)
    first = next(gen)
    state["seen"] = 1  # fires before the next line is written

    assert next(gen, None) is None  # generator ends cleanly, no second chunk
    proc = FakePopen.instances[0]
    assert proc.terminate_count == 1  # process terminated, no orphan
    assert proc.stdin.close_count >= 1
    assert len(proc.stdin.lines) == 1  # no line written after the stop
    assert parse_wav_data(first)  # the first chunk was still delivered whole


def test_missing_binary_matches_synthesize_error_surface(fake_popen, capsys):
    provider = PiperTTSProvider(binary_path="/nonexistent/path/piper")

    out = list(provider.synthesize_stream(long_sentences(2), "voice"))

    assert out == []
    assert FakePopen.instances == []  # never spawned
    stderr = capsys.readouterr().err
    assert "Piper offline TTS is not installed" in stderr
    assert "pip install piper-tts" in stderr


def test_empty_text_spawns_nothing(fake_popen, provider):
    out = list(provider.synthesize_stream("   \n \t ", "voice"))

    assert out == []
    assert FakePopen.instances == []


def test_command_helpers_keep_single_shot_shape(fake_popen, provider):
    """The single-shot path keeps its command shape after the helper refactor."""
    model = provider._resolve_model("es_ES-davefx-medium")
    cmd = provider._build_command(model, 1.2)

    assert cmd[0] == provider.binary_path
    assert cmd[1:3] == ["--output_file", "-"]
    assert cmd[3:5] == ["--length_scale", "0.83"]  # 1 / 1.2


# -- dispatch regression: piper never streams in auto --------------------------


def test_piper_never_auto_streams():
    assert use_pipelined_stream("piper", "auto", no_play=False, output_file=None, podcast=False, text_len=5000) is False
    assert use_pipelined_stream("piper", "on", no_play=False, output_file=None, podcast=False, text_len=10) is True


# -- chunk <-> group mapping helper ---------------------------------------------


class TestGroupForChunk(unittest.TestCase):
    GROUPS = ["first group", "second group", "third group"]

    def test_in_range_chunks_map_by_index(self):
        self.assertEqual(group_for_chunk(self.GROUPS, 0), "first group")
        self.assertEqual(group_for_chunk(self.GROUPS, 2), "third group")

    def test_extra_chunks_have_no_group(self):
        self.assertIsNone(group_for_chunk(self.GROUPS, 3))
        self.assertIsNone(group_for_chunk(self.GROUPS, 99))

    def test_empty_groups_never_match(self):
        self.assertIsNone(group_for_chunk([], 0))


# -- _speak_pipelined consumes the stream capability -----------------------------


def make_decoded(frames, sample_rate=24000, nchannels=1, sample_width=2):
    """Builds a minimal stand-in for a miniaudio decoded sound with the given shape."""

    class FakeDecoded:
        pass

    decoded = FakeDecoded()
    decoded.sample_rate = sample_rate
    decoded.nchannels = nchannels
    decoded.sample_width = sample_width
    decoded.samples = array.array("h", [0] * (frames * nchannels))
    return decoded


class FakeRemoteSession:
    """Duck-typed session mirroring the streaming session shape of the drain tests."""

    def __init__(self):
        self.lock = threading.Lock()
        self.boundaries = BoundaryMap()
        self.state = {"status": "playing", "stop": False, "producing": False}
        self.prepared = []
        self.appended = []

    def prepare_pcm(self, decoded):
        self.prepared.append(decoded)

    def append_pcm(self, decoded):
        self.appended.append(decoded)
        return True

    def play(self, decoded):
        pass


class StoppingSession(FakeRemoteSession):
    """Session that requests a stop after N appended groups (deterministic early stop)."""

    def __init__(self, stop_after):
        super().__init__()
        self.stop_after = stop_after

    def append_pcm(self, decoded):
        result = super().append_pcm(decoded)
        if len(self.appended) >= self.stop_after:
            self.state["stop"] = True
        return result


class FakeStreamEngine:
    """Engine advertising the full group-chunk streaming contract.

    Mirrors PiperTTSProvider: supports_stream plus one chunk per sentence
    group, which routes _speak_pipelined to the single-pass path.
    """

    name = "fake-piper"
    supports_stream = True
    stream_yields_group_chunks = True

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.calls = []
        self.consumed = 0
        self.closed = False

    def synthesize_stream(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", stop_checker=None):
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "rate": rate,
                "volume": volume,
                "pitch": pitch,
                "stop_checker": stop_checker,
            }
        )
        try:
            for chunk in self._chunks:
                if stop_checker and stop_checker():
                    return
                self.consumed += 1
                yield chunk
        finally:
            self.closed = True


def _run_pipelined(session, text, engine):
    """Runs _speak_pipelined against a fake stream engine, capturing stderr."""

    def fake_decode(data):
        return make_decoded(2400)  # 0.1s of audio per chunk

    stderr = io.StringIO()
    with mock.patch("agent_tts.cli.get_provider", return_value=engine), mock.patch(
        "agent_tts.cli.miniaudio.decode", fake_decode
    ), contextlib.redirect_stderr(stderr):
        asyncio.run(
            _speak_pipelined(
                session=session,
                text=text,
                check_stop=lambda: bool(session.state.get("stop")),
                voice="test-voice",
                rate="+20%",
                volume="+0%",
                pitch="+0Hz",
                provider="piper",
                openai_key=None,
                openai_base_url=None,
                openai_model=None,
                eleven_key=None,
                eleven_model=None,
                piper_model=None,
                auto_lang=False,
            )
        )
    return stderr.getvalue()


class FakeHttpStreamEngine:
    """Engine mirroring openai/elevenlabs: streams raw HTTP fragments.

    supports_stream is True but chunks are NOT one per sentence group, so the
    per-group path must stay in charge (synthesize_stream never invoked).
    """

    name = "fake-http"
    supports_stream = True
    stream_yields_group_chunks = False

    def __init__(self):
        self.calls = []

    def synthesize_stream(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", stop_checker=None):
        self.calls.append({"text": text, "voice": voice, "rate": rate})
        return iter([b"should-never-be-consumed"])


def _run_pipelined_per_group(session, text, engine):
    """Runs _speak_pipelined with a counting fake cli.synthesize (per-group path)."""

    async def fake_synthesize(**kwargs):
        fake_synthesize.calls.append(kwargs)
        return SynthesisResult(b"fake-mp3-bytes", BoundaryMap())

    fake_synthesize.calls = []

    def fake_decode(data):
        return make_decoded(2400)  # 0.1s of audio per group

    stderr = io.StringIO()
    with mock.patch("agent_tts.cli.get_provider", return_value=engine), mock.patch(
        "agent_tts.cli.synthesize", fake_synthesize
    ), mock.patch("agent_tts.cli.miniaudio.decode", fake_decode), contextlib.redirect_stderr(stderr):
        asyncio.run(
            _speak_pipelined(
                session=session,
                text=text,
                check_stop=lambda: bool(session.state.get("stop")),
                voice="test-voice",
                rate="+20%",
                volume="+0%",
                pitch="+0Hz",
                provider="openai",
                openai_key=None,
                openai_base_url=None,
                openai_model=None,
                eleven_key=None,
                eleven_model=None,
                piper_model=None,
                auto_lang=False,
            )
        )
    return fake_synthesize.calls, stderr.getvalue()


class TestFragmentStreamKeepsPerGroupPath(unittest.TestCase):
    def test_supports_stream_without_group_chunks_stays_per_group(self):
        groups = split_sentence_groups(long_sentences(3))
        self.assertEqual(len(groups), 3)
        engine = FakeHttpStreamEngine()
        session = FakeRemoteSession()

        calls, stderr = _run_pipelined_per_group(session, long_sentences(3), engine)

        # synthesize() ran once per group (per-group path, aligned boundaries).
        self.assertEqual(len(calls), 3)
        for call, group_text in zip(calls, groups):
            self.assertEqual(call["text"], group_text)
            self.assertEqual(call["voice"], "test-voice")
        # The single-pass stream was never consumed.
        self.assertEqual(engine.calls, [])
        # Playback shape matches the per-group pipeline.
        self.assertEqual(len(session.prepared), 1)
        self.assertEqual(len(session.appended), 2)
        self.assertGreaterEqual(len(session.boundaries.sentences), 3)
        self.assertNotIn("Stream:", stderr)  # no degraded-boundary warnings
        self.assertFalse(session.state["producing"])


class TestPipelinedStreamConsumption(unittest.TestCase):
    def test_stream_consumed_once_and_every_group_played(self):
        groups = split_sentence_groups(long_sentences(4))
        self.assertEqual(len(groups), 4)
        engine = FakeStreamEngine(line_wavs(4))
        session = FakeRemoteSession()

        stderr = _run_pipelined(session, long_sentences(4), engine)

        # ONE stream call for the whole text, with the same args the per-group
        # path passes to synthesize().
        self.assertEqual(len(engine.calls), 1)
        call = engine.calls[0]
        self.assertEqual(call["text"], long_sentences(4))
        self.assertEqual(call["voice"], "test-voice")
        self.assertEqual(call["rate"], "+20%")
        self.assertEqual(call["volume"], "+0%")
        self.assertEqual(call["pitch"], "+0Hz")
        self.assertTrue(callable(call["stop_checker"]))
        # First chunk prepared, the rest appended live, all chunks consumed.
        self.assertEqual(len(session.prepared), 1)
        self.assertEqual(len(session.appended), 3)
        self.assertEqual(engine.consumed, 4)
        self.assertTrue(engine.closed)
        # Boundaries for every group were merged into the session map.
        self.assertGreaterEqual(len(session.boundaries.sentences), 4)
        self.assertFalse(session.state["producing"])
        self.assertNotIn("Stream:", stderr)  # no warnings on the happy path

    def test_more_chunks_than_groups_warns_and_still_appends(self):
        engine = FakeStreamEngine(line_wavs(5))
        session = FakeRemoteSession()

        stderr = _run_pipelined(session, long_sentences(3), engine)  # must not raise

        self.assertEqual(len(session.appended), 4)  # first prepared + 4 appended
        self.assertEqual(engine.consumed, 5)  # extra audio is still consumed
        self.assertIn("more chunks than sentence groups", stderr)

    def test_fewer_chunks_than_groups_warns_with_degraded_boundaries(self):
        engine = FakeStreamEngine(line_wavs(2))
        session = FakeRemoteSession()

        stderr = _run_pipelined(session, long_sentences(3), engine)  # must not raise

        self.assertEqual(len(session.appended), 1)
        self.assertIn("sentence groups produced no chunk", stderr)
        self.assertIn("boundaries degraded", stderr)

    def test_early_stop_closes_generator_before_exhaustion(self):
        engine = FakeStreamEngine(line_wavs(4))
        session = StoppingSession(stop_after=1)

        _run_pipelined(session, long_sentences(4), engine)

        # Chunk 0 came from the first phase, chunk 1 was appended and then the
        # stop fired: the generator must be closed BEFORE exhausting its 4 chunks.
        self.assertEqual(engine.consumed, 2)
        self.assertTrue(engine.closed)
        self.assertFalse(session.state["producing"])

    def test_empty_stream_reports_no_audio(self):
        engine = FakeStreamEngine([])
        session = FakeRemoteSession()

        with self.assertRaises(RuntimeError) as ctx:
            _run_pipelined(session, long_sentences(2), engine)
        self.assertIn("no audio", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
