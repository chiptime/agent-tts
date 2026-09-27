"""Unit tests for the continuous streaming MP3 decoder (PRD-AT-01)."""

import os
import threading
import time
import unittest

from agent_tts.stream.decoder import (
    DecodedChunk,
    Mp3StreamDecoder,
    StreamBufferSource,
    StreamDecodeFallbackError,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_24k.mp3")


class TestStreamBufferSource(unittest.TestCase):
    def test_feed_and_read(self):
        source = StreamBufferSource()
        source.feed(b"Hello ")
        source.feed(b"World!")
        source.close()

        read1 = source.read(5)
        self.assertEqual(read1, b"Hello")
        read2 = source.read(10)
        self.assertEqual(read2, b" World!")
        read3 = source.read(10)
        self.assertEqual(read3, b"")

    def test_blocking_read_until_feed(self):
        source = StreamBufferSource()
        result = []

        def worker():
            data = source.read(4)
            result.append(data)

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.05)
        self.assertEqual(result, [])  # Still waiting

        source.feed(b"DATA")
        t.join(timeout=1.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(result, [b"DATA"])


class TestMp3StreamDecoder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_PATH, "rb") as f:
            cls.mp3_data = f.read()

    def test_decode_stream_yields_preroll_and_subsequent_chunks(self):
        decoder = Mp3StreamDecoder(preroll_sec=0.1)

        # Producer thread feeding in 720-byte chunks every 10ms
        def producer():
            chunk_size = 720
            for i in range(0, len(self.mp3_data), chunk_size):
                decoder.feed_bytes(self.mp3_data[i : i + chunk_size])
                time.sleep(0.005)
            decoder.finish_stream()

        t_prod = threading.Thread(target=producer)
        t_prod.start()

        chunks = list(decoder.decode_generator())
        t_prod.join(timeout=2.0)

        self.assertGreater(len(chunks), 1)
        # First chunk satisfies preroll (>= 100 ms)
        first_chunk = chunks[0]
        self.assertIsInstance(first_chunk, DecodedChunk)
        self.assertGreaterEqual(first_chunk.duration, 0.095)  # slight float tolerance
        self.assertEqual(first_chunk.sample_rate, 24000)
        self.assertEqual(first_chunk.nchannels, 1)

        # Total duration matches expected total audio
        total_duration = sum(c.duration for c in chunks)
        self.assertAlmostEqual(total_duration, len(self.mp3_data) / 144 * 0.024, delta=0.1)

    def test_corrupt_stream_triggers_fallback_error(self):
        decoder = Mp3StreamDecoder(preroll_sec=0.1)
        # Feed 1000 bytes of pure garbage
        decoder.feed_bytes(b"\x00\x01\x02\x03" * 250)
        decoder.finish_stream()

        with self.assertRaises(StreamDecodeFallbackError):
            list(decoder.decode_generator())


if __name__ == "__main__":
    unittest.main()
