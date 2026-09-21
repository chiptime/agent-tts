import array
import asyncio
import contextlib
import io
import json
import unittest
import urllib.error
from unittest import mock

from agent_tts.audio import AudioSession
from agent_tts.boundaries import BoundaryMap, Paragraph, Sentence, Word
from agent_tts.cli import shift_boundary_map, split_sentence_groups, use_pipelined_stream
from agent_tts.providers import ElevenLabsTTSProvider, OpenAITTSProvider


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


class TestSplitSentenceGroups(unittest.TestCase):
    def test_empty_text_returns_empty_list(self):
        self.assertEqual(split_sentence_groups(""), [])
        self.assertEqual(split_sentence_groups("   \n \t "), [])

    def test_short_text_is_single_group(self):
        text = "Hello world. This is short."
        self.assertEqual(split_sentence_groups(text), [text])

    def test_greedy_packing_respects_max_chars(self):
        text = "One two. Three four. Five six. Seven eight."
        groups = split_sentence_groups(text, max_chars=16)
        self.assertGreater(len(groups), 1)
        for group in groups:
            self.assertLessEqual(len(group), 16)
        # No text lost or reordered.
        self.assertEqual(" ".join(groups), text)

    def test_long_sentence_split_on_commas(self):
        sentence = "A" * 300 + ", " + "B" * 300
        groups = split_sentence_groups(sentence, max_chars=250)
        self.assertEqual(groups, ["A" * 300, "B" * 300])

    def test_long_sentence_without_commas_stays_whole(self):
        text = "x" * 1000
        self.assertEqual(split_sentence_groups(text, max_chars=250), [text])

    def test_newlines_act_as_sentence_separators(self):
        groups = split_sentence_groups("First line here\nSecond line here", max_chars=250)
        self.assertEqual(groups, ["First line here Second line here"])

    def test_never_returns_empty_for_non_empty_text(self):
        for text in (".", "a", "...", "No punctuation at all here"):
            self.assertTrue(split_sentence_groups(text))


class TestShiftBoundaryMap(unittest.TestCase):
    def _make_bmap(self):
        sentences = [
            Sentence(index=0, start_sec=0.0, duration_sec=1.0, text="One.", paragraph_index=0),
            Sentence(index=1, start_sec=1.0, duration_sec=1.5, text="Two.", paragraph_index=1),
        ]
        words = [
            Word(index=0, sentence_index=0, start_sec=0.0, duration_sec=0.5, text="One."),
            Word(index=1, sentence_index=1, start_sec=1.0, duration_sec=0.5, text="Two."),
        ]
        paragraphs = [
            Paragraph(index=0, start_sec=0.0, duration_sec=1.0, text="One.", sentence_indices=[0]),
            Paragraph(index=1, start_sec=1.0, duration_sec=1.5, text="Two.", sentence_indices=[1]),
        ]
        return BoundaryMap(sentences=sentences, words=words, paragraphs=paragraphs)

    def test_offsets_and_index_rebasing(self):
        shifted = shift_boundary_map(
            self._make_bmap(),
            offset_sec=7.5,
            sent_index_base=10,
            word_index_base=20,
            paragraph_index_base=3,
        )
        self.assertEqual([s.index for s in shifted.sentences], [10, 11])
        self.assertEqual([s.start_sec for s in shifted.sentences], [7.5, 8.5])
        self.assertEqual([s.duration_sec for s in shifted.sentences], [1.0, 1.5])
        self.assertEqual([s.paragraph_index for s in shifted.sentences], [3, 4])
        self.assertEqual([w.index for w in shifted.words], [20, 21])
        self.assertEqual([w.sentence_index for w in shifted.words], [10, 11])
        self.assertEqual([w.start_sec for w in shifted.words], [7.5, 8.5])
        self.assertEqual([p.index for p in shifted.paragraphs], [3, 4])
        self.assertEqual([p.start_sec for p in shifted.paragraphs], [7.5, 8.5])
        self.assertEqual(shifted.paragraphs[1].sentence_indices, [11])

    def test_original_map_is_not_mutated(self):
        bmap = self._make_bmap()
        shift_boundary_map(bmap, offset_sec=5.0, sent_index_base=2, word_index_base=4, paragraph_index_base=1)
        self.assertEqual(bmap.sentences[0].start_sec, 0.0)
        self.assertEqual(bmap.sentences[0].index, 0)
        self.assertEqual(bmap.words[1].start_sec, 1.0)
        self.assertEqual(bmap.paragraphs[1].index, 1)


class TestAudioSessionStreaming(unittest.TestCase):
    def _make_session(self, frames):
        session = AudioSession()
        session.sample_rate = 24000
        session.nchannels = 1
        session.bytes_per_sample = 2
        session.frame_size = 2
        session.raw_bytes = bytes(frames * 2)
        session.total_frames = frames
        session.current_frame = 0
        session.state["status"] = "playing"
        return session

    def test_prepare_pcm_then_append_grows_buffer(self):
        session = AudioSession()
        session.prepare_pcm(make_decoded(100))
        self.assertTrue(session._buffer_loaded)
        self.assertEqual(session.total_frames, 100)
        self.assertEqual(session.current_frame, 0)
        self.assertTrue(session.append_pcm(make_decoded(50)))
        self.assertEqual(session.total_frames, 150)

    def test_append_pcm_grows_buffer_and_total(self):
        session = AudioSession()
        self.assertTrue(session.append_pcm(make_decoded(100)))
        self.assertEqual(session.total_frames, 100)
        self.assertEqual(len(session.raw_bytes), 200)
        self.assertAlmostEqual(session.state["total"], 100 / 24000)
        self.assertTrue(session.append_pcm(make_decoded(50)))
        self.assertEqual(session.total_frames, 150)
        self.assertAlmostEqual(session.state["total"], 150 / 24000)

    def test_append_pcm_skips_mismatched_format(self):
        session = AudioSession()
        session.append_pcm(make_decoded(100))
        self.assertFalse(session.append_pcm(make_decoded(50, sample_rate=48000)))
        self.assertFalse(session.append_pcm(make_decoded(50, nchannels=2)))
        self.assertEqual(session.total_frames, 100)

    def test_stream_generator_holds_silence_while_producing(self):
        session = self._make_session(100)
        session.state["producing"] = True

        gen = session._stream_generator()
        self.assertEqual(next(gen), b"")  # Prime

        chunk = gen.send(40)
        self.assertEqual(len(chunk), 80)
        self.assertEqual(session.current_frame, 40)

        chunk = gen.send(40)
        self.assertEqual(session.current_frame, 80)

        # Only 20 frames remain: partial read drains the buffer exactly.
        chunk = gen.send(40)
        self.assertEqual(len(chunk), 40)
        self.assertEqual(session.current_frame, 100)

        # Drained while producing: silence instead of ending the stream.
        chunk = gen.send(40)
        self.assertEqual(chunk, bytes(80))
        self.assertEqual(session.current_frame, 100)

        # Appended PCM becomes available mid-stream and playback resumes.
        self.assertTrue(session.append_pcm(make_decoded(10)))
        chunk = gen.send(40)
        self.assertEqual(len(chunk), 20)
        self.assertEqual(session.current_frame, 110)

        # Producer finishes: next request terminates the stream.
        session.state["producing"] = False
        self.assertEqual(gen.send(40), b"")
        with self.assertRaises(StopIteration):
            gen.send(40)

    def test_stream_generator_ends_when_drained_and_not_producing(self):
        session = self._make_session(100)
        session.state["producing"] = False

        gen = session._stream_generator()
        self.assertEqual(next(gen), b"")  # Prime

        chunk = gen.send(100)
        self.assertEqual(len(chunk), 200)
        self.assertEqual(session.current_frame, 100)

        # Drained and nothing else is coming: end of stream.
        self.assertEqual(gen.send(100), b"")
        with self.assertRaises(StopIteration):
            gen.send(100)


class FakeChunkedResponse:
    """Stand-in for a urllib streaming response that hands out one MP3 chunk per read()."""

    def __init__(self, chunks, fail_after=None):
        self._chunks = list(chunks)
        self._pos = 0
        self._fail_after = fail_after
        self.read_calls = 0

    def read(self, amt=-1):
        self.read_calls += 1
        if self._fail_after is not None and self._pos >= self._fail_after:
            raise ConnectionError("connection reset mid-stream")
        if self._pos >= len(self._chunks):
            return b""
        chunk = self._chunks[self._pos]
        self._pos += 1
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(routes):
    """Builds a urlopen stand-in serving responses by request; records every request seen."""

    def handler(req, timeout=30):
        outcome = routes(req)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    seen = []

    def recording_handler(req, timeout=30):
        seen.append(req)
        return handler(req, timeout=timeout)

    return recording_handler, seen


def payload_of(req):
    return json.loads(req.data.decode("utf-8"))


class TestStreamGate(unittest.TestCase):
    def test_auto_streams_edge_and_http_providers_for_long_texts(self):
        for provider in ("edge", "openai", "elevenlabs", "eleven"):
            self.assertTrue(use_pipelined_stream(provider, "auto", False, None, False, 400))
            self.assertFalse(use_pipelined_stream(provider, "auto", False, None, False, 399))

    def test_auto_excludes_providers_without_pipelining_support(self):
        self.assertFalse(use_pipelined_stream("piper", "auto", False, None, False, 10000))

    def test_on_forces_streaming_for_any_provider_or_length(self):
        self.assertTrue(use_pipelined_stream("piper", "on", False, None, False, 10))

    def test_podcast_and_no_play_disable_streaming(self):
        base = {"provider": "edge", "stream": "auto", "no_play": False, "output_file": None, "podcast": False, "text_len": 1000}
        for override in ({"podcast": True}, {"no_play": True}):
            case = dict(base)
            case.update(override)
            self.assertFalse(use_pipelined_stream(**case))

    def test_output_file_no_longer_disables_streaming(self):
        # The merged audio is written to the file after playback completes.
        self.assertTrue(use_pipelined_stream("edge", "on", False, "/tmp/a.mp3", False, 10))
        self.assertTrue(use_pipelined_stream("edge", "auto", False, "/tmp/a.mp3", False, 1000))
        self.assertFalse(use_pipelined_stream("edge", "auto", False, "/tmp/a.mp3", False, 399))

    def test_off_disables_streaming(self):
        self.assertFalse(use_pipelined_stream("edge", "off", False, None, False, 10000))


class TestElevenLabsStreaming(unittest.TestCase):
    CHUNKS = [b"\x01" * 64, b"\x02" * 64, b"\x03" * 64]
    FULL = b"FULL_MP3_BYTES"

    def _provider(self):
        return ElevenLabsTTSProvider(api_key="test-key")

    def test_stream_chunks_arrive_incrementally(self):
        handler, seen = fake_urlopen(lambda req: FakeChunkedResponse(self.CHUNKS))
        with mock.patch("urllib.request.urlopen", handler):
            out = list(self._provider().synthesize_stream("Hola mundo", "rachel"))
        self.assertEqual(b"".join(out), b"".join(self.CHUNKS))
        self.assertEqual(len(seen), 1)
        self.assertIn("/text-to-speech/21m00Tcm4TlvDq8ikWAM/stream", seen[0].full_url)
        self.assertIn("output_format=mp3_44100_128", seen[0].full_url)
        headers = {k.lower(): v for k, v in seen[0].headers.items()}
        self.assertEqual(headers.get("xi-api-key"), "test-key")

    def test_stream_first_chunk_arrives_before_response_is_drained(self):
        resp = FakeChunkedResponse(self.CHUNKS)
        handler, _ = fake_urlopen(lambda req: resp)
        out = []
        with mock.patch("urllib.request.urlopen", handler):
            for chunk in self._provider().synthesize_stream("Hola mundo", "rachel", stop_checker=lambda: bool(out)):
                out.append(chunk)
        # Only the first chunk was consumed: the consumer received it while the
        # response still had unread data (a buffering client would have drained all 3).
        self.assertEqual(len(out), 1)
        self.assertEqual(resp.read_calls, 1)

    def test_synthesize_falls_back_when_stream_endpoint_fails(self):
        def routes(req):
            if "/stream" in req.full_url:
                return urllib.error.URLError("boom")
            return FakeChunkedResponse([self.FULL])

        handler, seen = fake_urlopen(routes)
        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", handler), contextlib.redirect_stderr(stderr):
            result = asyncio.run(self._provider().synthesize("Hola mundo", "rachel", "+0%"))
        self.assertEqual(bytes(result), self.FULL)
        self.assertEqual(len(seen), 2)
        self.assertIn("/stream", seen[0].full_url)
        self.assertNotIn("/stream", seen[1].full_url)
        self.assertIn("falling back to full-response synthesis", stderr.getvalue())

    def test_synthesize_falls_back_on_midstream_failure(self):
        def routes(req):
            if "/stream" in req.full_url:
                return FakeChunkedResponse([b"partial"], fail_after=1)
            return FakeChunkedResponse([self.FULL])

        handler, seen = fake_urlopen(routes)
        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", handler), contextlib.redirect_stderr(stderr):
            result = asyncio.run(self._provider().synthesize("Hola mundo", "rachel", "+0%"))
        self.assertEqual(bytes(result), self.FULL)
        self.assertEqual(len(seen), 2)
        self.assertIn("falling back to full-response synthesis", stderr.getvalue())

    def test_synthesize_without_key_makes_no_requests(self):
        def boom(req, timeout=30):
            raise AssertionError("no request should be made without an API key")

        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", boom), contextlib.redirect_stderr(stderr):
            result = asyncio.run(ElevenLabsTTSProvider(api_key="").synthesize("Hola mundo", "rachel", "+0%"))
        self.assertEqual(bytes(result), b"")
        self.assertIn("requires an API key", stderr.getvalue())


class TestOpenAIStreaming(unittest.TestCase):
    CHUNKS = [b"\x01" * 64, b"\x02" * 64, b"\x03" * 64]
    FULL = b"FULL_MP3_BYTES"

    def _provider(self):
        return OpenAITTSProvider(api_key="test-key")

    def test_stream_chunks_arrive_incrementally(self):
        handler, seen = fake_urlopen(lambda req: FakeChunkedResponse(self.CHUNKS))
        with mock.patch("urllib.request.urlopen", handler):
            out = list(self._provider().synthesize_stream("Hola mundo", "elvira"))
        self.assertEqual(b"".join(out), b"".join(self.CHUNKS))
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].full_url.endswith("/audio/speech"))
        payload = payload_of(seen[0])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["voice"], "nova")
        self.assertEqual(payload["response_format"], "mp3")

    def test_stream_first_chunk_arrives_before_response_is_drained(self):
        resp = FakeChunkedResponse(self.CHUNKS)
        handler, _ = fake_urlopen(lambda req: resp)
        out = []
        with mock.patch("urllib.request.urlopen", handler):
            for chunk in self._provider().synthesize_stream("Hola mundo", "elvira", stop_checker=lambda: bool(out)):
                out.append(chunk)
        self.assertEqual(len(out), 1)
        self.assertEqual(resp.read_calls, 1)

    def test_synthesize_falls_back_when_streaming_fails(self):
        def routes(req):
            if payload_of(req).get("stream"):
                return urllib.error.URLError("boom")
            return FakeChunkedResponse([self.FULL])

        handler, seen = fake_urlopen(routes)
        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", handler), contextlib.redirect_stderr(stderr):
            result = asyncio.run(self._provider().synthesize("Hola mundo", "elvira", "+0%"))
        self.assertEqual(bytes(result), self.FULL)
        self.assertEqual(len(seen), 2)
        self.assertTrue(payload_of(seen[0]).get("stream"))
        self.assertNotIn("stream", payload_of(seen[1]))
        self.assertIn("falling back to full-response synthesis", stderr.getvalue())

    def test_synthesize_falls_back_on_midstream_failure(self):
        def routes(req):
            if payload_of(req).get("stream"):
                return FakeChunkedResponse([b"partial"], fail_after=1)
            return FakeChunkedResponse([self.FULL])

        handler, seen = fake_urlopen(routes)
        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", handler), contextlib.redirect_stderr(stderr):
            result = asyncio.run(self._provider().synthesize("Hola mundo", "elvira", "+0%"))
        self.assertEqual(bytes(result), self.FULL)
        self.assertEqual(len(seen), 2)
        self.assertIn("falling back to full-response synthesis", stderr.getvalue())

    def test_synthesize_without_key_makes_no_requests(self):
        def boom(req, timeout=30):
            raise AssertionError("no request should be made without an API key")

        stderr = io.StringIO()
        with mock.patch("urllib.request.urlopen", boom), contextlib.redirect_stderr(stderr):
            result = asyncio.run(OpenAITTSProvider(api_key="").synthesize("Hola mundo", "elvira", "+0%"))
        self.assertEqual(bytes(result), b"")
        self.assertIn("requires an API key", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
