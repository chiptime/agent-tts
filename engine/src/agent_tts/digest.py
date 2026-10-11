"""Piecewise audio digests with stdlib ID3v2.3 chapter metadata.

MP3 turns are copied verbatim; any RIFF/WAVE turn selects normalized WAV
output for the entire digest. Notices are emitted to stderr, leaving the public
return value as just the output path. No retention settings or sources mutate.
"""

from datetime import date, datetime
import math
import os
from pathlib import Path
import shutil
import struct
import sys
from uuid import uuid4

import miniaudio

from agent_tts.audio_store import _pcm_to_wav, audio_dir


_COPY_SIZE = 64 * 1024
_SAMPLE_RATE = 44100
_CHANNELS = 2
_MAX_PCM_SIZE = 0xFFFFFFFF - 36


def _synchsafe(size: int) -> bytes:
    if not 0 <= size < (1 << 28):
        raise ValueError("ID3 tag exceeds the synchsafe size limit")
    return bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))


def _frame(name: bytes, body: bytes) -> bytes:
    # ID3v2.3 frame sizes are ordinary big-endian integers, not synchsafe.
    return name + struct.pack(">I", len(body)) + b"\x00\x00" + body


def _chapter_tag(turns: list[dict], mode: str) -> bytes:
    if mode == "none":
        return b""

    spans = []
    elapsed_ms = 0.0
    for turn in turns:
        # Inspect the MP3 container, irrespective of its filename extension.
        # This avoids decoding whole turns merely to determine chapter times.
        duration = miniaudio.mp3_get_file_info(os.fspath(turn["path"])).duration
        if not math.isfinite(duration) or duration < 0:
            raise ValueError(f"Invalid MP3 duration: {turn['path']}")
        start = round(elapsed_ms)
        elapsed_ms += duration * 1000
        end = round(elapsed_ms)
        if end > 0xFFFFFFFF:
            raise ValueError("Digest exceeds the ID3 chapter timestamp limit")
        if mode == "pane" and spans and spans[-1][0]["pane"] == turn["pane"]:
            first, start, _ = spans[-1]
            spans[-1] = (first, start, end)
        else:
            spans.append((turn, start, end))

    frames = []
    for index, (turn, start, end) in enumerate(spans):
        title = f"{turn['pane']} {datetime.fromtimestamp(turn['epoch']):%H:%M}"
        # Encoding 1 (UTF-16 with BOM) is supported by ID3v2.3, unlike UTF-8.
        title_frame = _frame(b"TIT2", b"\x01" + title.encode("utf-16") + b"\x00\x00")
        body = f"turn-{index}".encode("ascii") + b"\x00"
        body += struct.pack(">IIII", start, end, 0xFFFFFFFF, 0xFFFFFFFF)
        frames.append(_frame(b"CHAP", body + title_frame))
    payload = b"".join(frames)
    return b"ID3\x03\x00\x00" + _synchsafe(len(payload)) + payload


def _append_pcm(destination, path) -> int:
    """Decode one turn, write its sample buffer directly, then release it."""
    with open(path, "rb") as source:
        encoded = source.read()
    decoded = miniaudio.decode(
        encoded,
        output_format=miniaudio.SampleFormat.SIGNED16,
        sample_rate=_SAMPLE_RATE,
        nchannels=_CHANNELS,
    )
    del encoded
    if sys.byteorder != "little":
        decoded.samples.byteswap()
    pcm = memoryview(decoded.samples).cast("B")
    size = len(pcm)
    if destination.tell() - 44 + size > _MAX_PCM_SIZE:
        raise ValueError("Digest exceeds the RIFF/WAVE size limit")
    destination.write(pcm)
    return size


def _write_silence(destination, size: int) -> None:
    # A large requested gap must not allocate a correspondingly large buffer.
    block = bytes(min(size, _COPY_SIZE))
    while size:
        count = min(size, len(block))
        destination.write(block[:count])
        size -= count


def _write_wav(destination, turns: list[dict], gap_ms: int) -> None:
    # Start with the canonical helper's header, then patch only its lengths.
    # Passing the entire digest to _pcm_to_wav would duplicate all PCM in RAM.
    destination.write(_pcm_to_wav(b"", _SAMPLE_RATE, _CHANNELS))
    gap_bytes = (_SAMPLE_RATE * gap_ms // 1000) * _CHANNELS * 2
    pcm_size = 0
    for index, turn in enumerate(turns):
        if index:
            if pcm_size + gap_bytes > _MAX_PCM_SIZE:
                raise ValueError("Digest exceeds the RIFF/WAVE size limit")
            _write_silence(destination, gap_bytes)
            pcm_size += gap_bytes
        pcm_size += _append_pcm(destination, turn["path"])
    destination.seek(4)
    destination.write(struct.pack("<I", 36 + pcm_size))
    destination.seek(40)
    destination.write(struct.pack("<I", pcm_size))


def _check_destination(destination: Path, turns: list[dict]) -> None:
    if destination.is_symlink():
        raise ValueError("Digest output must not be a symlink")
    resolved = destination.resolve()
    for turn in turns:
        source = Path(turn["path"])
        if resolved == source.resolve() or (
            destination.exists() and destination.samefile(source)
        ):
            raise ValueError("Digest output must not overwrite a source turn")


def compile_digest(files, *, gap_ms=400, chapters="turn", output=None) -> str:
    """Compile selected turns in epoch order and return the written path.

    ``turn`` chapters cover individual turns; ``pane`` chapters cover contiguous
    runs of the same pane (a returning pane starts another span). Chapter times
    are cumulative audio durations, not wall-clock gaps. Labels use local time,
    matching the store's date convention. ``none`` writes no chapter tag.

    All-MP3 output is a raw byte join with an optional ID3v2.3 CHAP tag. Requested
    MP3 gaps are skipped with a stderr notice. Any WAV input selects signed
    16-bit, 44.1 kHz stereo WAV for every turn; gaps are whole silent PCM frames,
    and chapters degrade with a stderr notice. PCM memory is bounded by one
    decoded turn, not the entire digest. Decode/I/O errors propagate; an error
    during writing can leave an incomplete destination, but never alters inputs.

    Default filenames are unique and live directly inside ``audio_dir()``.
    Explicit output parents must already exist; no temporary files are created.
    """
    if isinstance(gap_ms, bool) or not isinstance(gap_ms, int) or gap_ms < 0:
        raise ValueError("gap_ms must be a non-negative integer")
    if chapters not in ("turn", "pane", "none"):
        raise ValueError("chapters must be turn, pane, or none")
    turns = sorted(files, key=lambda turn: (turn["epoch"], os.fspath(turn["path"])))
    if not turns:
        raise ValueError("No audio turns selected for digest")

    wav_output = False
    for turn in turns:
        with open(turn["path"], "rb") as source:
            header = source.read(12)
        if header[:4] == b"RIFF":
            if header[8:12] != b"WAVE":
                raise ValueError(f"RIFF turn is not WAVE audio: {turn['path']}")
            wav_output = True

    if output is None:
        # Parse the partition date before using it in a generated filename.
        day = date.fromisoformat(turns[0]["date"]).isoformat()
        suffix = ".wav" if wav_output else ".mp3"
        destination = Path(audio_dir()) / f"{day}-digest-{uuid4().hex}{suffix}"
    else:
        destination = Path(output)
    _check_destination(destination, turns)
    tag = b"" if wav_output else _chapter_tag(turns, chapters)

    if output is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects generated names; explicit output may replace
    # a regular file, but source aliases and symlinks were rejected above.
    with destination.open("x+b" if output is None else "w+b") as target:
        if wav_output:
            _write_wav(target, turns, gap_ms)
        else:
            target.write(tag)
            for turn in turns:
                with open(turn["path"], "rb") as source:
                    shutil.copyfileobj(source, target, length=_COPY_SIZE)

    if wav_output:
        print("Digest chapters degraded: WAV output cannot embed ID3v2 CHAP chapters.", file=sys.stderr)
    elif gap_ms and len(turns) > 1:
        print("Digest gap skipped: MP3 silence cannot be synthesized without an encoder.", file=sys.stderr)
    return str(destination)
