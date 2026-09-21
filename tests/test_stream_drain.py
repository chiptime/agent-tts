"""Streaming drain contract for _speak_pipelined (long-text --stream runs).

Regression tests for the truncation bug where `producer.join(timeout=5.0)`
followed by the session teardown cut long --stream runs after roughly the
first two groups whenever `session.play()` returned early (wsl-ps / remote
sessions feed groups from the producer thread and return immediately).

Contract:
- playback outlives the producer: the pipeline stays alive until the producer
  exits (or an IPC stop arrives), so the final clean drain covers every group;
- fatal producer errors surface through the normal error path instead of
  silently dropping the tail (unless the user stopped the session);
- a stalled producer aborts with an explicit error, never a silent hang.
"""

import array
import asyncio
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from agent_tts.boundaries import BoundaryMap, SynthesisResult
from agent_tts.cli import _speak_pipelined, split_sentence_groups


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
    """Duck-typed session mimicking PowershellSession's streaming behavior.

    play() returns immediately (the first group was already written by
    prepare_pcm and the producer thread feeds the rest), which is exactly the
    shape that exposed the truncation bug.
    """

    def __init__(self, append_delay=0.0, fail_append_after=None, stop_on_play=False):
        self.lock = threading.Lock()
        self.boundaries = BoundaryMap()
        self.state = {"status": "playing", "stop": False, "producing": False}
        self.prepared = []
        self.appended = []
        self.append_delay = append_delay
        self.fail_append_after = fail_append_after
        self.stop_on_play = stop_on_play

    def prepare_pcm(self, decoded):
        self.prepared.append(decoded)

    def append_pcm(self, decoded):
        if self.append_delay:
            time.sleep(self.append_delay)
        if self.fail_append_after is not None and len(self.appended) >= self.fail_append_after:
            raise RuntimeError("PowerShell playback process has exited")
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


def _pipelined(session, text):
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
        )
    )


class TestStreamDrain(unittest.TestCase):
    def setUp(self):
        # A clean drain now auto-persists rendered audio; point the store at a
        # throwaway directory so tests never touch the developer's real one.
        store = tempfile.mkdtemp(prefix="agent-tts-drain-audio-")
        patcher = mock.patch.dict(os.environ, {"AGENT_TTS_AUDIO_DIR": store})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_synthesis(self):
        """Patches cli.synthesize (instant fake) and cli.miniaudio.decode."""
        async def fake_synthesize(**kwargs):
            return SynthesisResult(b"fake-mp3-bytes", BoundaryMap())

        def fake_decode(data):
            return make_decoded(2400)  # 0.1s of audio per group

        return (
            mock.patch("agent_tts.cli.synthesize", fake_synthesize),
            mock.patch("agent_tts.cli.miniaudio.decode", fake_decode),
        )

    def test_long_stream_plays_every_group_even_when_play_returns_early(self):
        # Producer pace: 5 remaining groups x 1.1s > the old 5s join timeout,
        # so the pre-fix pipeline returned early and dropped the tail.
        text = _long_text(6)
        expected_groups = len(split_sentence_groups(text))
        self.assertEqual(expected_groups, 6)
        session = FakeRemoteSession(append_delay=1.1)
        syn_patch, decode_patch = self._patch_synthesis()
        with syn_patch, decode_patch:
            _pipelined(session, text)
        # First group is prepared; every remaining group must be appended.
        self.assertEqual(len(session.prepared), 1)
        self.assertEqual(len(session.appended), expected_groups - 1)
        self.assertFalse(session.state["producing"])
        # Boundaries for every group were merged into the session map.
        self.assertGreaterEqual(len(session.boundaries.sentences), expected_groups)

    def test_fatal_producer_error_surfaces_after_playback(self):
        session = FakeRemoteSession(fail_append_after=1)
        syn_patch, decode_patch = self._patch_synthesis()
        with syn_patch, decode_patch:
            with self.assertRaises(RuntimeError) as ctx:
                _pipelined(session, _long_text(3))
        self.assertIn("PowerShell playback process has exited", str(ctx.exception))

    def test_producer_error_is_swallowed_when_user_stopped(self):
        # An IPC stop cut the session; the producer's write error is a
        # consequence of the stop, not a failure to report.
        session = FakeRemoteSession(fail_append_after=0, stop_on_play=True)
        syn_patch, decode_patch = self._patch_synthesis()
        with syn_patch, decode_patch:
            _pipelined(session, _long_text(3))  # must not raise

    def test_ipc_stop_returns_immediately(self):
        session = FakeRemoteSession(append_delay=0.05, stop_on_play=True)
        syn_patch, decode_patch = self._patch_synthesis()
        start = time.monotonic()
        with syn_patch, decode_patch:
            _pipelined(session, _long_text(4))
        self.assertLess(time.monotonic() - start, 2.0)

    def test_stalled_producer_aborts_with_explicit_error(self):
        session = FakeRemoteSession(append_delay=0.6)
        syn_patch, decode_patch = self._patch_synthesis()
        with mock.patch("agent_tts.cli.STREAM_PRODUCER_STALL_SEC", 0.3):
            with syn_patch, decode_patch:
                with self.assertRaises(RuntimeError) as ctx:
                    _pipelined(session, _long_text(3))
        self.assertIn("no progress", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
