"""MP3 frame-accurate parser and validator (PRD-AT-01).

Implements byte-level parsing of MP3 streams:
- ID3v2 header detection and skipping
- Sync word (0xFFE/0xFFF) detection
- Header decoding for MPEG-1, MPEG-2, and MPEG-2.5 Layer III
- Frame size and sample duration calculation
- Robust error recovery from corrupt byte streams
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Mp3FrameInfo:
    """Decoded metadata from an MP3 frame header."""

    mpeg_version: float  # 1.0, 2.0, or 2.5
    layer: int           # 1, 2, or 3
    bitrate_kbps: int
    sample_rate: int
    padding: int
    channels: int        # 1 (mono) or 2 (stereo)
    samples_per_frame: int
    frame_size: int      # total frame length in bytes
    duration_sec: float


@dataclass(frozen=True)
class Mp3Frame:
    """A single complete MP3 frame with its metadata."""

    data: bytes
    info: Mp3FrameInfo


# Bitrate tables (kbps) for Layer III
_BITRATES_MPEG1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BITRATES_MPEG2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]

# Sample rate tables (Hz)
_SAMPLE_RATES = {
    1.0: [44100, 48000, 32000],
    2.0: [22050, 24000, 16000],
    2.5: [11025, 12000, 8000],
}


def parse_id3v2_size(header: bytes) -> Optional[int]:
    """Returns the total byte size of an ID3v2 tag (header + body), or None if not an ID3v2 tag."""
    if len(header) < 10 or header[:3] != b"ID3":
        return None
    # Flags: footer present if bit 4 (0x10) is set
    footer_present = bool(header[5] & 0x10)
    # 4 syncsafe integer bytes (7 bits each)
    size = (header[6] << 21) | (header[7] << 14) | (header[8] << 7) | header[9]
    return 10 + size + (10 if footer_present else 0)


def parse_frame_header(header: bytes) -> Optional[Mp3FrameInfo]:
    """Parses a 4-byte MP3 frame header. Returns Mp3FrameInfo or None if invalid."""
    if len(header) < 4:
        return None

    b0, b1, b2, b3 = header[:4]

    # Sync word: 11 bits of 1s (0xFF followed by top 3 bits 1)
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None

    # MPEG Audio Version
    v_raw = (b1 >> 3) & 0x03
    if v_raw == 0b00:
        mpeg_version = 2.5
    elif v_raw == 0b10:
        mpeg_version = 2.0
    elif v_raw == 0b11:
        mpeg_version = 1.0
    else:
        return None  # Reserved version

    # Layer
    l_raw = (b1 >> 1) & 0x03
    if l_raw == 0b01:
        layer = 3
    elif l_raw == 0b10:
        layer = 2
    elif l_raw == 0b11:
        layer = 1
    else:
        return None  # Reserved layer

    # Bitrate index
    br_idx = (b2 >> 4) & 0x0F
    if br_idx == 0 or br_idx == 15:
        return None  # 0 is free, 15 is bad

    if layer == 3:
        if mpeg_version == 1.0:
            bitrate_kbps = _BITRATES_MPEG1_L3[br_idx]
        else:
            bitrate_kbps = _BITRATES_MPEG2_L3[br_idx]
    else:
        return None  # Only Layer III is supported for MP3 streaming

    # Sampling rate index
    sr_idx = (b2 >> 2) & 0x03
    if sr_idx == 3:
        return None  # Reserved

    sample_rate = _SAMPLE_RATES[mpeg_version][sr_idx]
    padding = (b2 >> 1) & 0x01

    # Channels: 0b11 is Single channel (Mono), others are 2 channels
    channels = 1 if ((b3 >> 6) & 0x03) == 0b11 else 2

    # Samples per frame and frame length formula
    if mpeg_version == 1.0:
        samples_per_frame = 1152
        frame_size = int(144 * (bitrate_kbps * 1000) / sample_rate) + padding
    else:
        samples_per_frame = 576
        frame_size = int(72 * (bitrate_kbps * 1000) / sample_rate) + padding

    if frame_size <= 4:
        return None

    duration_sec = samples_per_frame / float(sample_rate)

    return Mp3FrameInfo(
        mpeg_version=mpeg_version,
        layer=layer,
        bitrate_kbps=bitrate_kbps,
        sample_rate=sample_rate,
        padding=padding,
        channels=channels,
        samples_per_frame=samples_per_frame,
        frame_size=frame_size,
        duration_sec=duration_sec,
    )


class Mp3FrameParser:
    """Stateful streaming MP3 frame parser."""

    def __init__(self):
        self._buffer = bytearray()
        self._id3_checked = False
        self.id3_skipped_bytes = 0
        self.valid_frames_count = 0
        self.corrupt_bytes_count = 0

    def feed(self, chunk: bytes) -> List[Mp3Frame]:
        """Feeds incoming bytes and returns any newly completed MP3 frames."""
        self._buffer.extend(chunk)
        frames: List[Mp3Frame] = []

        # Check for ID3v2 tag at the start of the stream
        if not self._id3_checked:
            if len(self._buffer) >= 10:
                id3_size = parse_id3v2_size(bytes(self._buffer[:10]))
                if id3_size is not None:
                    if len(self._buffer) >= id3_size:
                        del self._buffer[:id3_size]
                        self.id3_skipped_bytes = id3_size
                        self._id3_checked = True
                    else:
                        # Wait for full ID3 tag to arrive
                        return frames
                else:
                    self._id3_checked = True
            else:
                return frames

        # Extract frames from buffer
        while len(self._buffer) >= 4:
            # Check for sync word: 0xFF followed by 0b111...
            if self._buffer[0] != 0xFF or (self._buffer[1] & 0xE0) != 0xE0:
                # Corrupt / garbage byte, skip 1 byte
                del self._buffer[0]
                self.corrupt_bytes_count += 1
                continue

            info = parse_frame_header(bytes(self._buffer[:4]))
            if info is None:
                # False sync word match or invalid header
                del self._buffer[0]
                self.corrupt_bytes_count += 1
                continue

            # We have a valid header. Do we have the complete frame?
            if len(self._buffer) < info.frame_size:
                # Incomplete frame, wait for more data
                break

            frame_data = bytes(self._buffer[:info.frame_size])
            del self._buffer[:info.frame_size]
            frames.append(Mp3Frame(data=frame_data, info=info))
            self.valid_frames_count += 1

        return frames
