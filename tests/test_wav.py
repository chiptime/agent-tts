import struct

from agent_tts.wav import WAV_HEADER_FMT, pcm_to_wav


def test_header_layout_and_sizes():
    pcm = b"\x01\x02" * 100  # 100 mono s16 frames
    wav = pcm_to_wav(pcm, sample_rate=24000, channels=1)
    assert len(wav) == 44 + len(pcm)
    (
        riff,
        riff_size,
        wave,
        fmt_id,
        fmt_size,
        audio_format,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        data_id,
        data_size,
    ) = struct.unpack(WAV_HEADER_FMT, wav[:44])
    assert riff == b"RIFF"
    assert riff_size == 36 + len(pcm)
    assert wave == b"WAVE"
    assert fmt_id == b"fmt "
    assert fmt_size == 16  # PCM fmt chunk size
    assert audio_format == 1  # PCM
    assert channels == 1
    assert sample_rate == 24000
    assert byte_rate == 24000 * 1 * 2
    assert block_align == 2
    assert bits_per_sample == 16
    assert data_id == b"data"
    assert data_size == len(pcm)


def test_payload_preserved():
    pcm = bytes(range(256))
    wav = pcm_to_wav(pcm, sample_rate=44100, channels=2, sample_width=2)
    assert wav[44:] == pcm


def test_byte_rate_and_block_align_scale_with_format():
    wav = pcm_to_wav(b"\x00" * 12, sample_rate=48000, channels=2, sample_width=2)
    assert struct.unpack("<I", wav[28:32])[0] == 48000 * 2 * 2  # byte rate
    assert struct.unpack("<H", wav[32:34])[0] == 4  # block align
