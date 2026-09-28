"""Unit tests for frame-level MP3 streaming integration in cli.py (PRD-AT-01)."""

import asyncio
import os
import unittest
from unittest import mock

from agent_tts.audio import AudioSession
from agent_tts.cli import (
    _speak_pipelined,
    resolve_stream_mode,
    use_pipelined_stream,
)
from agent_tts.providers.base import TTSProvider

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_24k.mp3")


class FakeFrameEngine(TTSProvider):
    name = "edge"
    supports_stream = True
    stream_yields_group_chunks = False

    def __init__(self, data: bytes, boundary_events=None):
        self.data = data
        self.boundary_events = boundary_events or []

    def synthesize_stream(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", stop_checker=None, on_event=None):
        if on_event:
            for ev in self.boundary_events:
                on_event(ev)
        chunk_size = 720
        for i in range(0, len(self.data), chunk_size):
            if stop_checker and stop_checker():
                break
            yield self.data[i : i + chunk_size]


class TestResolveStreamMode(unittest.TestCase):
    def test_auto_selects_frames_for_streaming_mp3_providers(self):
        self.assertEqual(
            resolve_stream_mode("edge", "auto", supports_stream=True, stream_yields_group_chunks=False, auto_lang=False),
            "frames",
        )
        self.assertEqual(
            resolve_stream_mode("openai", "auto", supports_stream=True, stream_yields_group_chunks=False, auto_lang=False),
            "frames",
        )
        self.assertEqual(
            resolve_stream_mode("elevenlabs", "auto", supports_stream=True, stream_yields_group_chunks=False, auto_lang=False),
            "frames",
        )

    def test_auto_selects_groups_for_piper_and_whole_utterance(self):
        # Piper yields complete group chunks
        self.assertEqual(
            resolve_stream_mode("piper", "auto", supports_stream=True, stream_yields_group_chunks=True, auto_lang=False),
            "groups",
        )
        # Kokoro does not support streaming
        self.assertEqual(
            resolve_stream_mode("kokoro", "auto", supports_stream=False, stream_yields_group_chunks=False, auto_lang=False),
            "groups",
        )

    def test_explicit_modes_override_defaults(self):
        self.assertEqual(
            resolve_stream_mode("edge", "groups", supports_stream=True, stream_yields_group_chunks=False, auto_lang=False),
            "groups",
        )
        self.assertEqual(
            resolve_stream_mode("edge", "frames", supports_stream=True, stream_yields_group_chunks=False, auto_lang=False),
            "frames",
        )

    def test_auto_lang_forces_groups(self):
        self.assertEqual(
            resolve_stream_mode("edge", "auto", supports_stream=True, stream_yields_group_chunks=False, auto_lang=True),
            "groups",
        )


class TestUsePipelinedStreamModes(unittest.TestCase):
    def test_frames_and_groups_force_streaming(self):
        self.assertTrue(use_pipelined_stream("edge", "frames", False, None, False, 10))
        self.assertTrue(use_pipelined_stream("edge", "groups", False, None, False, 10))

    def test_auto_requires_threshold(self):
        self.assertFalse(use_pipelined_stream("edge", "auto", False, None, False, 100))
        self.assertTrue(use_pipelined_stream("edge", "auto", False, None, False, 500))

    def test_off_disables_streaming(self):
        self.assertFalse(use_pipelined_stream("edge", "off", False, None, False, 500))


class TestSpeakPipelinedFrameStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_PATH, "rb") as f:
            cls.mp3_data = f.read()

    def test_frame_streaming_prepares_and_appends_pcm(self):
        session = AudioSession(label="test-frames")
        # Mock play to not block or open audio devices
        session.play = mock.MagicMock()

        engine = FakeFrameEngine(
            self.mp3_data,
            boundary_events=[
                {"type": "WordBoundary", "offset": 1000000, "duration": 2500000, "text": "Prueba"},
                {"type": "WordBoundary", "offset": 3500000, "duration": 2500000, "text": "corta"},
            ],
        )

        asyncio.run(
            _speak_pipelined(
                session=session,
                text="Prueba corta.",
                check_stop=lambda: False,
                voice="alvaro",
                rate="+0%",
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
                engine=engine,
                stream="frames",
            )
        )

        session.play.assert_called_once()
        self.assertGreater(session.total_frames, 0)
        self.assertGreater(session.state["total"], 0.5)
        # Boundaries populated from events
        self.assertEqual(len(session.boundaries.words), 2)
        self.assertEqual(session.boundaries.words[0].text, "Prueba")

    @mock.patch("agent_tts.cli.synthesize", new_callable=mock.AsyncMock)
    def test_frame_streaming_fallback_on_corrupt_stream(self, mock_synth):
        session = AudioSession(label="test-fallback")
        session.play = mock.MagicMock()

        # Engine produces corrupt bytes that will cause Mp3StreamDecoder to trigger fallback
        corrupt_engine = FakeFrameEngine(b"corrupt non-mp3 bytes that fail decoder " * 10)

        # Mock synthesize to return valid audio when called during group fallback
        mock_synth.return_value = self.mp3_data

        asyncio.run(
            _speak_pipelined(
                session=session,
                text="Primera oracion. Segunda oracion.",
                check_stop=lambda: False,
                voice="alvaro",
                rate="+0%",
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
                engine=corrupt_engine,
                stream="frames",
            )
        )

        # Fallback to synthesize_group occurred
        self.assertGreaterEqual(mock_synth.call_count, 1)
        session.play.assert_called_once()


class TestCliParserStreamChoices(unittest.TestCase):
    def test_parser_accepts_all_stream_choices(self):
        from agent_tts.cli import build_parser

        parser = build_parser()
        for choice in ["auto", "on", "off", "frames", "groups"]:
            args = parser.parse_args(["Hello", "--stream", choice])
            self.assertEqual(args.stream, choice)


if __name__ == "__main__":
    unittest.main()

