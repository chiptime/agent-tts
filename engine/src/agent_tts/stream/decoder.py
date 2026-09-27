"""Continuous streaming MP3 decoder using miniaudio stream_any (PRD-AT-01).

Decodes MP3 frames incrementally as network bytes arrive without bit reservoir loss
or inter-frame glitches. Provides a configurable pre-roll buffer to prevent audio underruns.
"""

import array
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Generator, Optional

import miniaudio

from agent_tts.stream.mp3_parser import Mp3FrameInfo, Mp3FrameParser


class StreamDecodeFallbackError(RuntimeError):
    """Raised when streaming MP3 frames cannot be decoded and must fall back to group synthesis."""
    pass


@dataclass
class DecodedChunk:
    """A decoded chunk of raw PCM audio samples compatible with AudioSession."""

    samples: array.array
    sample_rate: int
    nchannels: int
    sample_width: int = 2

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sample_rate * self.nchannels)


class StreamBufferSource(miniaudio.StreamableSource):
    """Thread-safe blocking StreamableSource for miniaudio's stream_any."""

    def __init__(self):
        super().__init__()
        self._queue = queue.Queue()
        self._buf = bytearray()
        self._closed = False

    def feed(self, chunk: bytes) -> None:
        """Appends raw audio bytes to the stream."""
        if chunk:
            self._queue.put(chunk)

    def close(self) -> None:
        """Signals end-of-stream."""
        self._closed = True
        self._queue.put(None)

    def read(self, num_bytes: int) -> bytes:
        """Reads up to num_bytes from the stream, blocking if necessary."""
        while len(self._buf) < num_bytes and not (self._closed and self._queue.empty()):
            try:
                item = self._queue.get(timeout=0.05)
                if item is None:
                    break
                self._buf.extend(item)
            except queue.Empty:
                if self._closed:
                    break

        take = min(num_bytes, len(self._buf))
        res = bytes(self._buf[:take])
        del self._buf[:take]
        return res

    def seek(self, offset: int, origin: miniaudio.SeekOrigin) -> bool:
        return False


class Mp3StreamDecoder:
    """Incremental MP3 frame decoder producing continuous PCM chunks."""

    def __init__(self, preroll_sec: float = 0.1, frames_per_yield: int = 5):
        self.preroll_sec = preroll_sec
        self.frames_per_yield = frames_per_yield
        self._parser = Mp3FrameParser()
        self._source = StreamBufferSource()
        self._first_frame_info: Optional[Mp3FrameInfo] = None
        self._first_frame_event = threading.Event()
        self._finished = False
        self._fallback_triggered = False

    def feed_bytes(self, chunk: bytes) -> None:
        """Feeds network bytes into the frame parser and stream decoder."""
        if not chunk or self._finished:
            return
        frames = self._parser.feed(chunk)
        for frame in frames:
            if self._first_frame_info is None:
                self._first_frame_info = frame.info
                self._first_frame_event.set()
            self._source.feed(frame.data)

    def finish_stream(self) -> None:
        """Signals that no more input bytes will be provided."""
        self._finished = True
        self._source.close()

    def decode_generator(self, timeout_sec: float = 5.0) -> Generator[DecodedChunk, None, None]:
        """Generator yielding DecodedChunk objects as audio frames are decoded."""
        # Wait for the first frame to determine audio format
        deadline = time.monotonic() + timeout_sec
        while not self._first_frame_event.is_set():
            if self._finished:
                break
            if time.monotonic() > deadline:
                raise StreamDecodeFallbackError("Timeout waiting for first MP3 frame")
            time.sleep(0.01)

        if self._first_frame_info is None:
            raise StreamDecodeFallbackError(
                f"Stream contained no valid MP3 frames (corrupt bytes: {self._parser.corrupt_bytes_count})"
            )

        info = self._first_frame_info
        sample_rate = info.sample_rate
        channels = info.channels
        samples_per_frame = info.samples_per_frame

        try:
            pcm_stream = miniaudio.stream_any(
                self._source,
                source_format=miniaudio.FileFormat.MP3,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=channels,
                sample_rate=sample_rate,
                frames_to_read=samples_per_frame,
            )
        except Exception as e:
            raise StreamDecodeFallbackError(f"miniaudio failed to initialize MP3 stream: {e}") from e

        # Pre-roll phase: accumulate at least preroll_sec worth of samples
        min_preroll_samples = int(self.preroll_sec * sample_rate * channels)
        accumulated = array.array("h")

        try:
            for pcm_frame in pcm_stream:
                accumulated.extend(pcm_frame)
                if len(accumulated) >= min_preroll_samples:
                    yield DecodedChunk(
                        samples=accumulated,
                        sample_rate=sample_rate,
                        nchannels=channels,
                    )
                    accumulated = array.array("h")
                    break

            # Yield remaining frames in small batches
            batch_target = samples_per_frame * channels * self.frames_per_yield
            for pcm_frame in pcm_stream:
                accumulated.extend(pcm_frame)
                if len(accumulated) >= batch_target:
                    yield DecodedChunk(
                        samples=accumulated,
                        sample_rate=sample_rate,
                        nchannels=channels,
                    )
                    accumulated = array.array("h")

            # Final remainder
            if len(accumulated) > 0:
                yield DecodedChunk(
                    samples=accumulated,
                    sample_rate=sample_rate,
                    nchannels=channels,
                )
        except Exception as e:
            if not isinstance(e, StreamDecodeFallbackError):
                raise StreamDecodeFallbackError(f"Stream decoding error: {e}") from e
            raise
