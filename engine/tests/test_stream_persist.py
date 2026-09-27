"""Persistence contract for the pipelined streaming pipeline.

merge_chunks_to_audio (audio_store) plus the pipeline-level persist behavior
of _speak_pipelined: rendered bytes are merged and persisted exactly once at
a clean end of the pipeline — never on stop/interrupt/exception, never in
podcast mode, never when retention is disabled, and fail-open on merge or
store errors (playback already succeeded).
"""

import array
import asyncio
import contextlib
import io
import os
import struct
import tempfile
import unittest
import wave
from unittest import mock

from agent_tts.audio_store import merge_chunks_to_audio
from agent_tts.boundaries import BoundaryMap, SynthesisResult
from agent_tts.cli import _speak_pipelined, split_sentence_groups, use_pipelined_stream
import miniaudio


def make_decoded(samples, sample_rate=24000, nchannels=1, sample_width=2):
    """Builds a minimal stand-in for a miniaudio decoded sound with the given shape."""

    class FakeDecoded:
        pass

    decoded = FakeDecoded()
    decoded.sample_rate = sample_rate
    decoded.nchannels = nchannels
    decoded.sample_width = sample_width
    decoded.samples = array.array("h", samples)
    return decoded


def make_wav_bytes(pcm: bytes, sample_rate: int = 24000, nchannels: int = 1) -> bytes:
    """Builds a real, decodable 16-bit WAV with the stdlib wave module."""
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(nchannels)
    w.setsampwidth(2)
    w.setframerate(sample_rate)
    w.writeframes(pcm)
    w.close()
    return buf.getvalue()


class TestMergeChunksToAudio(unittest.TestCase):
    def test_empty_list_yields_empty_bytes(self):
        self.assertEqual(merge_chunks_to_audio([]), b"")

    def test_mp3_chunks_are_plainly_joined(self):
        chunks = [b"\xff\xfb\x90c" + b"a" * 10, b"\xff\xfb\x90c" + b"b" * 10]
        self.assertEqual(merge_chunks_to_audio(chunks), b"".join(chunks))

    def test_wav_chunks_merge_into_one_canonical_wav(self):
        # Fake-decoded 16-bit mono samples, mirroring test_stream.py's fake style:
        # real miniaudio.decode resamples, the fake keeps sample bytes verbatim.
        samples_a = [100, -100, 200, -200]
        samples_b = [300, -300]
        decoded_by_chunk = {
            b"RIFF-AAAA": make_decoded(samples_a),
            b"RIFF-BBBB": make_decoded(samples_b),
        }
        with mock.patch("agent_tts.audio_store.miniaudio.decode", decoded_by_chunk.__getitem__):
            merged = merge_chunks_to_audio([b"RIFF-AAAA", b"RIFF-BBBB"])
        # One canonical 44-byte header only, followed by the concatenated PCM.
        self.assertEqual(len(merged), 44 + 2 * (len(samples_a) + len(samples_b)))
        self.assertEqual(merged[:4], b"RIFF")
        self.assertEqual(merged[8:12], b"WAVE")
        fmt = struct.unpack("<IHHIIHH", merged[16:36])
        self.assertEqual(fmt, (16, 1, 1, 24000, 24000 * 2, 2, 16))
        self.assertEqual(merged[36:40], b"data")
        self.assertEqual(struct.unpack("<I", merged[40:44])[0], 2 * (len(samples_a) + len(samples_b)))
        self.assertEqual(
            merged[44:],
            array.array("h", samples_a).tobytes() + array.array("h", samples_b).tobytes(),
        )

    def test_wav_chunks_with_mismatched_sample_rates_raise(self):
        decoded_by_chunk = {
            b"RIFF-24": make_decoded([1, 2], sample_rate=24000),
            b"RIFF-48": make_decoded([3, 4], sample_rate=48000),
        }
        with mock.patch("agent_tts.audio_store.miniaudio.decode", decoded_by_chunk.__getitem__):
            with self.assertRaises(ValueError):
                merge_chunks_to_audio([b"RIFF-24", b"RIFF-48"])

    def test_wav_chunks_with_mismatched_channels_raise(self):
        decoded_by_chunk = {
            b"RIFF-MONO": make_decoded([1, 2], nchannels=1),
            b"RIFF-STEREO": make_decoded([3, 4, 5, 6], nchannels=2),
        }
        with mock.patch("agent_tts.audio_store.miniaudio.decode", decoded_by_chunk.__getitem__):
            with self.assertRaises(ValueError):
                merge_chunks_to_audio([b"RIFF-MONO", b"RIFF-STEREO"])

    def test_undecodable_wav_chunk_raises_value_error(self):
        with self.assertRaises(ValueError):
            merge_chunks_to_audio([b"RIFF" + b"\x00" * 64])

    def test_real_wav_chunks_produce_one_valid_container(self):
        a = make_wav_bytes(b"\x11\x22" * 100, 24000)
        b = make_wav_bytes(b"\x33\x44" * 80, 24000)
        merged = merge_chunks_to_audio([a, b])
        decoded_a = miniaudio.decode(a)
        decoded_b = miniaudio.decode(b)
        # Exactly one header (canonical, from the first chunk's decoded shape)
        # followed by the concatenated decoded PCM of both chunks.
        self.assertEqual(len(merged), 44 + len(bytes(decoded_a.samples)) + len(bytes(decoded_b.samples)))
        self.assertEqual(
            struct.unpack("<IHHIIHH", merged[16:36]),
            (
                16,
                1,
                decoded_a.nchannels,
                decoded_a.sample_rate,
                decoded_a.sample_rate * decoded_a.nchannels * 2,
                decoded_a.nchannels * 2,
                16,
            ),
        )
        self.assertEqual(merged[44:], bytes(decoded_a.samples) + bytes(decoded_b.samples))


class TestStreamGateOutputFile(unittest.TestCase):
    def test_output_file_no_longer_blocks_streaming(self):
        self.assertTrue(use_pipelined_stream("edge", "on", False, "/tmp/a.mp3", False, 10))

    def test_output_file_auto_still_needs_provider_and_length(self):
        self.assertFalse(use_pipelined_stream("piper", "auto", False, "/tmp/a.mp3", False, 10000))
        self.assertFalse(use_pipelined_stream("edge", "auto", False, "/tmp/a.mp3", False, 399))
        self.assertTrue(use_pipelined_stream("edge", "auto", False, "/tmp/a.mp3", False, 1000))


class FakeRemoteSession:
    """Duck-typed session mimicking the streaming-capable remote sessions."""

    def __init__(self, stop_on_play=False):
        import threading

        self.lock = threading.Lock()
        self.boundaries = BoundaryMap()
        self.state = {"status": "playing", "stop": False, "producing": False}
        self.prepared = []
        self.appended = []
        self.stop_on_play = stop_on_play

    def prepare_pcm(self, decoded):
        self.prepared.append(decoded)

    def append_pcm(self, decoded):
        self.appended.append(decoded)
        return True

    def play(self, decoded):
        if self.stop_on_play:
            self.state["stop"] = True

    def stop(self):
        self.state["stop"] = True


def _long_text(groups: int) -> str:
    sentence = (
        "The quick brown fox jumps over the lazy dog while the rain keeps "
        "falling softly on the old tin roof of the barn across the field. "
    )
    return sentence * groups


def _pipelined(session, text, output_file=None, podcast=False, persist_name=None):
    return asyncio.run(
        _speak_pipelined(
            session=session,
            text=text,
            check_stop=lambda: bool(session.state.get("stop")),
            voice="es-ES-ElviraNeural",
            rate="+20%",
            volume="+0%",
            pitch="+0Hz",
            provider="edge",
            openai_key=None,
            openai_base_url=None,
            openai_model=None,
            eleven_key=None,
            eleven_model=None,
            piper_model=None,
            auto_lang=False,
            output_file=output_file,
            podcast=podcast,
            persist_name=persist_name,
        )
    )


class TestPipelinePersistence(unittest.TestCase):
    def setUp(self):
        self.audio_dir = tempfile.mkdtemp(prefix="agent-tts-persist-audio-")
        # Opt-in retention: the positive-persist tests enable a window
        # explicitly; the default is off (DEFAULT_RETENTION_DAYS = 0).
        patcher = mock.patch.dict(
            os.environ,
            {"AGENT_TTS_AUDIO_DIR": self.audio_dir, "AGENT_TTS_AUDIO_RETENTION_DAYS": "7"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_synthesis(self):
        async def fake_synthesize(**kwargs):
            return SynthesisResult(b"fake-mp3-bytes", BoundaryMap())

        def fake_decode(data):
            return make_decoded([0] * 2400)

        return (
            mock.patch("agent_tts.cli.synthesize", fake_synthesize),
            mock.patch("agent_tts.cli.miniaudio.decode", fake_decode),
        )

    def _stored_files(self):
        files = []
        for day in os.listdir(self.audio_dir):
            day_dir = os.path.join(self.audio_dir, day)
            for name in os.listdir(day_dir):
                files.append(os.path.join(day_dir, name))
        return files

    def test_clean_run_persists_merged_audio_in_store(self):
        text = _long_text(3)
        expected_groups = len(split_sentence_groups(text))
        self.assertGreater(expected_groups, 1)
        session = FakeRemoteSession()
        syn_patch, decode_patch = self._patch_synthesis()
        stderr = io.StringIO()
        with syn_patch, decode_patch, contextlib.redirect_stderr(stderr):
            _pipelined(session, text, persist_name="opencode-session-x")
        files = self._stored_files()
        self.assertEqual(len(files), 1)
        # Date-partition layout and the persist name in the filename.
        day = os.path.basename(os.path.dirname(files[0]))
        self.assertRegex(day, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("-opencode-session-x", os.path.basename(files[0]))
        with open(files[0], "rb") as f:
            self.assertEqual(f.read(), b"fake-mp3-bytes" * expected_groups)
        self.assertIn("Stored: ", stderr.getvalue())

    def test_retention_zero_disables_auto_persist(self):
        session = FakeRemoteSession()
        syn_patch, decode_patch = self._patch_synthesis()
        with mock.patch.dict(os.environ, {"AGENT_TTS_AUDIO_RETENTION_DAYS": "0"}):
            with syn_patch, decode_patch:
                _pipelined(session, _long_text(3), persist_name="cli-test")
        self.assertEqual(self._stored_files(), [])

    def test_output_file_receives_merged_bytes_after_playback(self):
        out_dir = tempfile.mkdtemp(prefix="agent-tts-persist-out-")
        out_path = os.path.join(out_dir, "nested", "merged.mp3")
        expected_groups = len(split_sentence_groups(_long_text(2)))
        session = FakeRemoteSession()
        syn_patch, decode_patch = self._patch_synthesis()
        with syn_patch, decode_patch:
            _pipelined(session, _long_text(2), output_file=out_path)
        with open(out_path, "rb") as f:
            self.assertEqual(f.read(), b"fake-mp3-bytes" * expected_groups)
        # --output takes precedence: nothing lands in the store.
        self.assertEqual(self._stored_files(), [])

    def test_podcast_mode_never_persists(self):
        session = FakeRemoteSession()
        syn_patch, decode_patch = self._patch_synthesis()
        with syn_patch, decode_patch:
            _pipelined(session, _long_text(2), podcast=True, persist_name="cli-test")
        self.assertEqual(self._stored_files(), [])

    def test_stopped_run_does_not_persist(self):
        session = FakeRemoteSession(stop_on_play=True)
        syn_patch, decode_patch = self._patch_synthesis()
        with syn_patch, decode_patch:
            _pipelined(session, _long_text(3), persist_name="cli-test")
        self.assertEqual(self._stored_files(), [])

    def test_merge_failure_skips_persistence_and_warns(self):
        session = FakeRemoteSession()
        syn_patch, decode_patch = self._patch_synthesis()
        stderr = io.StringIO()
        with mock.patch("agent_tts.audio_store.merge_chunks_to_audio", side_effect=ValueError("boom")):
            with syn_patch, decode_patch, contextlib.redirect_stderr(stderr):
                _pipelined(session, _long_text(2), persist_name="cli-test")
        self.assertEqual(self._stored_files(), [])
        self.assertIn("skipping persistence", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
