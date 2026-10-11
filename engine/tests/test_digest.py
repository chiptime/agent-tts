"""Container-aware digest compilation and independently parsed chapter tags."""

import array
import builtins
from datetime import datetime
import io
from pathlib import Path
import struct
import weakref
import wave

import miniaudio
import pytest

from agent_tts.audio_store import _pcm_to_wav
from agent_tts import digest
from agent_tts.digest import compile_digest


def _mp3(frames=4):
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz: zero side information/main data.
    return (b"\xff\xfb\x90\x00" + bytes(413)) * frames


def _turn(tmp_path, index, data, pane="pane-a", suffix=".mp3"):
    path = tmp_path / f"{index}-{pane}{suffix}"
    path.write_bytes(data)
    epoch = 1791705600 + index * 60
    return {
        "epoch": epoch,
        "pane": pane,
        "path": str(path),
        "date": datetime.fromtimestamp(epoch).date().isoformat(),
    }


def _frames(data):
    """Parse ID3v2.3 without relying on the implementation's tag helpers."""
    assert data[:6] == b"ID3\x03\x00\x00"
    assert all(byte < 128 for byte in data[6:10])
    size = sum(byte << shift for byte, shift in zip(data[6:10], (21, 14, 7, 0)))
    end = 10 + size
    pos = 10
    frames = []
    while pos < end:
        name = data[pos:pos + 4]
        frame_size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        assert data[pos + 8:pos + 10] == b"\x00\x00"
        body = data[pos + 10:pos + 10 + frame_size]
        assert len(body) == frame_size
        frames.append((name, body))
        pos += 10 + frame_size
    assert pos == end
    return frames, data[end:]


def _chapters(data):
    frames, audio = _frames(data)
    result = []
    for name, body in frames:
        if name != b"CHAP":
            continue
        identifier, body = body.split(b"\x00", 1)
        start, end, start_byte, end_byte = struct.unpack(">IIII", body[:16])
        assert (start_byte, end_byte) == (0xFFFFFFFF, 0xFFFFFFFF)
        title = body[16:]
        assert title[:4] == b"TIT2"
        title_size = struct.unpack(">I", title[4:8])[0]
        assert title[8:11] == b"\x00\x00\x01"  # flags, UTF-16 encoding
        assert len(title) == 10 + title_size
        label = title[11:].decode("utf-16").rstrip("\x00")
        result.append((identifier, start, end, label))
    assert len({chapter[0] for chapter in result}) == len(result)
    return result, audio


def test_all_mp3_has_parseable_turn_chapters_and_raw_epoch_order(tmp_path, capsys):
    first = _turn(tmp_path, 1, _mp3(4))
    second = _turn(tmp_path, 2, _mp3(7), pane="pane-b")
    output = tmp_path / "digest.mp3"
    result = compile_digest([second, first], output=output)
    assert result == str(output)
    chapters, audio = _chapters(output.read_bytes())
    assert audio == _mp3(4) + _mp3(7)
    assert len(chapters) == 2
    first_ms = round(miniaudio.mp3_get_file_info(first["path"]).duration * 1000)
    total_ms = round(sum(miniaudio.mp3_get_file_info(t["path"]).duration
                         for t in (first, second)) * 1000)
    assert [(c[1], c[2]) for c in chapters] == [(0, first_ms), (first_ms, total_ms)]
    assert [c[3] for c in chapters] == [
        f"{t['pane']} {datetime.fromtimestamp(t['epoch']):%H:%M}"
        for t in (first, second)
    ]
    assert "gap" in capsys.readouterr().err.lower()
    assert miniaudio.decode(output.read_bytes()).num_frames > 0


def test_mp3_none_is_exact_raw_join_without_duration_decoding(tmp_path, monkeypatch, capsys):
    turns = [_turn(tmp_path, i, _mp3()) for i in (1, 2)]
    def forbidden(*args, **kwargs):
        pytest.fail("chapters=none must not decode/probe MP3 durations")
    monkeypatch.setattr(miniaudio, "decode", forbidden)
    monkeypatch.setattr(miniaudio, "mp3_get_file_info", forbidden)
    output = tmp_path / "digest.mp3"
    compile_digest(turns, gap_ms=0, chapters="none", output=output)
    assert output.read_bytes() == _mp3() * 2
    assert not capsys.readouterr().err


def test_pane_chapters_cover_contiguous_spans_not_overlapping_panes(tmp_path):
    turns = [_turn(tmp_path, i, _mp3(), pane=pane) for i, pane in
             enumerate(("pane-a", "pane-a", "pane-b", "pane-a"), start=1)]
    output = tmp_path / "digest.mp3"
    compile_digest(turns, chapters="pane", gap_ms=0, output=output)
    chapters, audio = _chapters(output.read_bytes())
    assert len(chapters) == 3
    duration = miniaudio.mp3_get_file_info(turns[0]["path"]).duration * 1000
    assert [(c[1], c[2]) for c in chapters] == [
        (0, round(duration * 2)), (round(duration * 2), round(duration * 3)),
        (round(duration * 3), round(duration * 4)),
    ]
    assert chapters[0][3].endswith(f"{datetime.fromtimestamp(turns[0]['epoch']):%H:%M}")
    assert audio == _mp3() * 4


@pytest.mark.parametrize("containers", [("wav", "wav"), ("mp3", "wav"), ("wav", "mp3")])
def test_wav_or_mixed_decodes_every_turn_and_records_degradation(tmp_path, capsys, containers):
    wav = _pcm_to_wav(struct.pack("<h", 700) * 4410 * 2, 44100, 2)
    turns = [_turn(tmp_path, i, wav if container == "wav" else _mp3())
             for i, container in enumerate(containers, start=1)]
    expected = b"".join(miniaudio.decode(Path(t["path"]).read_bytes()).samples.tobytes()
                        for t in turns)
    output = tmp_path / "digest.wav"
    compile_digest(turns, gap_ms=0, output=output)
    data = output.read_bytes()
    assert data == _pcm_to_wav(expected, 44100, 2)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert b"CHAP" not in data
    with wave.open(io.BytesIO(data)) as sound:
        assert (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) == (44100, 2, 2)
    notice = capsys.readouterr().err.lower()
    assert "chapters" in notice and "degraded" in notice


@pytest.mark.parametrize("gap_ms", [0, 1, 400])
def test_wav_gap_is_frame_aligned_only_between_turns(tmp_path, gap_ms):
    pcm = struct.pack("<h", 800) * 4410 * 2
    turns = [_turn(tmp_path, i, _pcm_to_wav(pcm, 44100, 2)) for i in (1, 2, 3)]
    output = tmp_path / "digest.wav"
    compile_digest(turns, gap_ms=gap_ms, output=output)
    silence = bytes((44100 * gap_ms // 1000) * 2 * 2)
    assert output.read_bytes() == _pcm_to_wav(silence.join([pcm] * 3), 44100, 2)


def test_wav_normalizes_different_rates_channels_and_suffixes(tmp_path):
    first = _turn(tmp_path, 1, _pcm_to_wav(bytes(2400 * 2), 24000, 1))
    second = _turn(tmp_path, 2, _pcm_to_wav(bytes(4800 * 4), 48000, 2), suffix=".wav")
    output = tmp_path / "digest.wav"
    compile_digest([first, second], gap_ms=0, output=output)
    expected = b"".join(miniaudio.decode(Path(t["path"]).read_bytes()).samples.tobytes()
                        for t in (first, second))
    assert output.read_bytes() == _pcm_to_wav(expected, 44100, 2)


@pytest.mark.parametrize("container", ["mp3", "wav"])
@pytest.mark.parametrize("explicit", [False, True])
def test_only_destination_is_written_and_sources_remain_unmodified(tmp_path, monkeypatch, container, explicit):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("AGENT_TTS_AUDIO_DIR", str(store))
    data = _mp3() if container == "mp3" else _pcm_to_wav(bytes(400), 44100, 2)
    turn = _turn(tmp_path, 1, data)
    source = Path(turn["path"])
    source_mtime = source.stat().st_mtime_ns
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output = tmp_path / "explicit.audio" if explicit else None
    result = Path(compile_digest([turn], output=output))
    if explicit:
        assert result == output
        assert not list(store.iterdir())
    else:
        assert result.parent == store
        assert result.suffix == f".{container}"
        assert result.name.startswith(f"{turn['date']}-digest")
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after.keys() - before.keys() == {result}
    assert all(after[path] == content for path, content in before.items())
    assert source.read_bytes() == data
    assert source.stat().st_mtime_ns == source_mtime


def test_generated_names_do_not_overwrite_previous_digests(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TTS_AUDIO_DIR", str(tmp_path / "store"))
    turn = _turn(tmp_path, 1, _mp3())
    first = compile_digest([turn], gap_ms=0)
    second = compile_digest([turn], gap_ms=0)
    assert first != second
    assert Path(first).read_bytes() == Path(second).read_bytes()


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_output_cannot_overwrite_a_source_even_through_aliases(tmp_path, alias):
    turn = _turn(tmp_path, 1, _mp3())
    source = Path(turn["path"])
    output = source
    if alias != "same":
        output = tmp_path / "alias.mp3"
        if alias == "symlink":
            output.symlink_to(source)
        else:
            output.hardlink_to(source)
    with pytest.raises(ValueError):
        compile_digest([turn], output=output)
    assert source.read_bytes() == _mp3()


def test_output_symlink_cannot_escape_the_destination(tmp_path):
    turn = _turn(tmp_path, 1, _mp3())
    foreign = tmp_path / "foreign.mp3"
    foreign.write_bytes(b"leave alone")
    output = tmp_path / "digest.mp3"
    output.symlink_to(foreign)
    with pytest.raises(ValueError):
        compile_digest([turn], output=output)
    assert foreign.read_bytes() == b"leave alone"


@pytest.mark.parametrize("options", [{"gap_ms": -1}, {"gap_ms": 0.5}, {"gap_ms": True},
                                     {"chapters": "invalid"}])
def test_invalid_options_do_not_create_output(tmp_path, options):
    turn = _turn(tmp_path, 1, _mp3())
    output = tmp_path / "digest.mp3"
    with pytest.raises(ValueError):
        compile_digest([turn], output=output, **options)
    assert not output.exists()


def test_empty_selection_does_not_create_output(tmp_path):
    output = tmp_path / "digest.mp3"
    with pytest.raises(ValueError, match="(?i)empty|no.*audio|no.*turn"):
        compile_digest([], output=output)
    assert not output.exists()


def test_wav_releases_each_decoded_buffer_before_decoding_next(tmp_path, monkeypatch):
    turns = [_turn(tmp_path, i, _pcm_to_wav(bytes(400), 44100, 2)) for i in range(1, 8)]
    previous = None
    calls = 0
    def decode(data, **kwargs):
        nonlocal previous, calls
        assert previous is None or previous() is None
        decoded = miniaudio.DecodedSoundFile(
            "<test>", 2, 44100, miniaudio.SampleFormat.SIGNED16, array.array("h", [0] * 200)
        )
        previous = weakref.ref(decoded)
        calls += 1
        return decoded
    monkeypatch.setattr(miniaudio, "decode", decode)
    output = tmp_path / "digest.wav"
    compile_digest(turns, gap_ms=0, output=output)
    assert calls == len(turns)
    assert output.stat().st_size == 44 + len(turns) * 400


def test_mp3_copies_with_bounded_reads(tmp_path, monkeypatch):
    turns = [_turn(tmp_path, i, _mp3(200)) for i in (1, 2)]
    reads = []
    class BoundedReader:
        def __init__(self, path, mode):
            self.source = builtins.open(path, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            reads.append(size)
            return self.source.read(size)

    monkeypatch.setattr(digest, "open", BoundedReader, raising=False)
    output = tmp_path / "digest.mp3"
    compile_digest(turns, gap_ms=0, output=output)
    chapters, audio = _chapters(output.read_bytes())
    assert len(chapters) == 2
    assert audio == _mp3(200) * 2
    assert reads.count(64 * 1024) >= 4


def test_unicode_long_chapter_title_uses_v23_frame_sizes(tmp_path):
    turn = _turn(tmp_path, 1, _mp3())
    turn["pane"] = "pane-" + "\u00e9" * 90
    output = tmp_path / "digest.mp3"
    compile_digest([turn], gap_ms=0, output=output)
    chapters, audio = _chapters(output.read_bytes())
    assert chapters[0][3] == f"{turn['pane']} {datetime.fromtimestamp(turn['epoch']):%H:%M}"
    assert audio == _mp3()


@pytest.mark.parametrize("chapters", ["none", "pane"])
def test_wav_chapter_modes_never_embed_chap(tmp_path, chapters, capsys):
    pcm = bytes(400)
    turn = _turn(tmp_path, 1, _pcm_to_wav(pcm, 44100, 2))
    output = tmp_path / "digest.wav"
    compile_digest([turn], chapters=chapters, output=output)
    assert output.read_bytes() == _pcm_to_wav(pcm, 44100, 2)
    assert "degraded" in capsys.readouterr().err


def test_invalid_riff_is_not_joined_as_mp3(tmp_path):
    turn = _turn(tmp_path, 1, b"RIFF\x00\x00\x00\x00AVI ")
    output = tmp_path / "digest.mp3"
    with pytest.raises(ValueError, match="not WAVE"):
        compile_digest([turn], output=output)
    assert not output.exists()
