"""Minimal helpers to package raw PCM bytes into RIFF/WAVE containers."""

import struct

# Standard 44-byte PCM WAVE header: RIFF descriptor + fmt chunk + data chunk.
WAV_HEADER_FMT = "<4sI4s4sIHHIIHH4sI"


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int, sample_width: int = 2) -> bytes:
    """Wraps raw little-endian PCM bytes in a standard 44-byte RIFF/WAVE header."""
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    bits_per_sample = sample_width * 8
    header = struct.pack(
        WAV_HEADER_FMT,
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size for PCM
        1,  # audio format: PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        len(pcm),
    )
    return header + pcm
