"""Unit tests for the MP3 frame parser (PRD-AT-01)."""

import struct
import unittest

from agent_tts.stream.mp3_parser import Mp3FrameInfo, Mp3FrameParser, parse_frame_header, parse_id3v2_size


def make_frame_header(
    mpeg_version: int = 2,  # 1, 2, or 2.5
    layer: int = 3,         # 3 for Layer III
    bitrate_kbps: int = 48,
    sample_rate: int = 24000,
    padding: int = 0,
    channels: int = 1,      # 1 (mono) or 2 (stereo)
) -> bytes:
    """Helper to synthesize a valid 4-byte MP3 frame header."""
    b0 = 0xFF
    # b1: sync (3 bits = 111), version (2 bits), layer (2 bits), protection (1 bit = 1: no CRC)
    v_bits = {2.5: 0b00, 2: 0b10, 1: 0b11}[mpeg_version]
    l_bits = {1: 0b11, 2: 0b10, 3: 0b01}[layer]
    b1 = 0b11100001 | (v_bits << 3) | (l_bits << 1)

    # b2: bitrate index (4 bits), sample rate index (2 bits), padding (1 bit), private (1 bit)
    if mpeg_version == 1:
        bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
        rates = [44100, 48000, 32000]
    else:
        bitrates = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
        rates = [22050, 24000, 16000] if mpeg_version == 2 else [11025, 12000, 8000]

    br_idx = bitrates.index(bitrate_kbps)
    sr_idx = rates.index(sample_rate)
    b2 = (br_idx << 4) | (sr_idx << 2) | (padding << 1)

    # b3: channel mode (2 bits), mode ext (2 bits), copyright (1 bit), original (1 bit), emphasis (2 bits)
    cm_bits = 0b11 if channels == 1 else 0b00
    b3 = (cm_bits << 6) | 0b00000100  # original = 1
    return bytes([b0, b1, b2, b3])


class TestMp3FrameParser(unittest.TestCase):
    def test_parse_id3v2_size(self):
        # 10 byte header, size encoded as 4 syncsafe bytes (7 bits each)
        # e.g. 0x00, 0x00, 0x02, 0x01 -> (2 << 7) | 1 = 257
        header = b"ID3\x04\x00\x00" + bytes([0x00, 0x00, 0x02, 0x01])
        tag_size = parse_id3v2_size(header)
        self.assertEqual(tag_size, 10 + 257)

    def test_parse_id3v2_non_id3(self):
        self.assertIsNone(parse_id3v2_size(b"\xff\xf3\x64\xc4\x00\x00\x00\x00\x00\x00"))

    def test_parse_header_mpeg2_layer3_edge_tts(self):
        # Edge TTS default: MPEG-2 Layer 3, 48 kbps, 24000 Hz, mono
        header = make_frame_header(mpeg_version=2, layer=3, bitrate_kbps=48, sample_rate=24000, padding=0, channels=1)
        info = parse_frame_header(header)
        self.assertIsNotNone(info)
        self.assertEqual(info.mpeg_version, 2)
        self.assertEqual(info.layer, 3)
        self.assertEqual(info.bitrate_kbps, 48)
        self.assertEqual(info.sample_rate, 24000)
        self.assertEqual(info.channels, 1)
        self.assertEqual(info.samples_per_frame, 576)
        # Frame size: 72 * 48000 / 24000 + 0 = 144 bytes
        self.assertEqual(info.frame_size, 144)
        self.assertAlmostEqual(info.duration_sec, 576 / 24000.0)

    def test_parse_header_mpeg1_layer3_standard(self):
        # Standard MPEG-1 Layer 3: 128 kbps, 44100 Hz, stereo, padding=0
        # Frame size: int(144 * 128000 / 44100) = int(417.959) = 417 bytes
        header = make_frame_header(mpeg_version=1, layer=3, bitrate_kbps=128, sample_rate=44100, padding=0, channels=2)
        info = parse_frame_header(header)
        self.assertIsNotNone(info)
        self.assertEqual(info.mpeg_version, 1)
        self.assertEqual(info.layer, 3)
        self.assertEqual(info.bitrate_kbps, 128)
        self.assertEqual(info.sample_rate, 44100)
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.samples_per_frame, 1152)
        self.assertEqual(info.frame_size, 417)
        self.assertAlmostEqual(info.duration_sec, 1152 / 44100.0)

    def test_parse_header_invalid_sync(self):
        self.assertIsNone(parse_frame_header(b"\x00\x00\x00\x00"))
        self.assertIsNone(parse_frame_header(b"\xff\x00\x00\x00"))

    def test_feed_complete_frames(self):
        parser = Mp3FrameParser()
        header = make_frame_header(mpeg_version=2, layer=3, bitrate_kbps=48, sample_rate=24000)
        frame1 = header + b"\xaa" * (144 - 4)
        frame2 = header + b"\xbb" * (144 - 4)

        frames = parser.feed(frame1 + frame2)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].data, frame1)
        self.assertEqual(frames[1].data, frame2)
        self.assertEqual(parser.valid_frames_count, 2)
        self.assertEqual(parser.corrupt_bytes_count, 0)

    def test_feed_chunked_streaming(self):
        parser = Mp3FrameParser()
        header = make_frame_header(mpeg_version=2, layer=3, bitrate_kbps=48, sample_rate=24000)
        frame = header + b"\x55" * (144 - 4)

        # Feed 50 bytes, then 50 bytes, then 44 bytes
        self.assertEqual(parser.feed(frame[:50]), [])
        self.assertEqual(parser.feed(frame[50:100]), [])
        res = parser.feed(frame[100:])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].data, frame)
        self.assertEqual(parser.valid_frames_count, 1)

    def test_skip_id3v2_tag_at_start(self):
        parser = Mp3FrameParser()
        # ID3 tag of 20 bytes total (10 header + 10 data)
        id3 = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10
        header = make_frame_header(mpeg_version=2, layer=3, bitrate_kbps=48, sample_rate=24000)
        frame = header + b"\x00" * (144 - 4)

        frames = parser.feed(id3 + frame)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].data, frame)
        self.assertEqual(parser.id3_skipped_bytes, 20)

    def test_corrupt_bytes_recovery(self):
        parser = Mp3FrameParser()
        header = make_frame_header(mpeg_version=2, layer=3, bitrate_kbps=48, sample_rate=24000)
        frame1 = header + b"\x11" * (144 - 4)
        garbage = b"\x01\x02\x03\x04\x05\x06"
        frame2 = header + b"\x22" * (144 - 4)

        frames = parser.feed(frame1 + garbage + frame2)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].data, frame1)
        self.assertEqual(frames[1].data, frame2)
        self.assertEqual(parser.corrupt_bytes_count, len(garbage))


if __name__ == "__main__":
    unittest.main()
