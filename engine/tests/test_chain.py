"""Hito Cadena acceptance tests: --play-chain gapless reproduction (AT-08, T6).

Three levels, all deterministic (timing thresholds live in
scripts/chain_metrics.py, never here — the T5 flake discipline):

- Assembly unit tests: chain files decode into ONE continuous PCM stream
  with the configurable inter-item silence (default 0 ms — US-AT-08-3:
  nothing inserted), and the combined ``BoundaryMap`` rebases every
  per-file sentence/word/paragraph onto chain-global positions
  (RF-AT-08-4).
- Session-level controls: seek, pause/resume, and phrase navigation
  address chain-global positions through the combined map, crossing
  file boundaries.
- Daemon end-to-end: the chain enters the priority queue as ONE item
  (same priority/policy machinery, D4), plays through ONE session in
  order, honors the SessionHandle stop-flag mid-file (a preempted chain
  stops cutting audio — remaining files are NOT played), exposes the
  controls over the whole chain through the real IPC channel, and emits
  the per-item ``chain-item`` trace seam the metrics harness measures.
"""

import array
import types
from pathlib import Path

import pytest

from agent_tts.audio import AudioSession
from agent_tts.boundaries import BoundaryMap, Paragraph, Sentence, Word
from agent_tts.chain import (
    ChainError,
    ChainSource,
    assemble_chain,
    assemble_chain_files,
    decode_chain_files,
    missing_chain_files,
)
from agent_tts.daemon import Daemon, ProviderCache
from agent_tts.wav import pcm_to_wav

RATE = 24000  # fabricated decoded segments (unit tests)
WAV_RATE = 44100  # decode-native format: miniaudio passthrough, values preserved
WAV_CH = 2


def _decoded(mark: int, seconds: float, rate: int = RATE, nchannels: int = 1):
    """A decoded-like segment whose every sample carries ``mark``."""
    count = int(rate * seconds) * nchannels
    return types.SimpleNamespace(
        sample_rate=rate,
        nchannels=nchannels,
        sample_width=2,
        duration=seconds,
        samples=array.array("h", [mark] * count),
    )


def _two_sentence_map(name: str, seconds: float) -> BoundaryMap:
    """A per-file map with two sentences (and one word each) spanning the file."""
    half = seconds / 2.0
    sentences = [
        Sentence(index=i, start_sec=i * half, duration_sec=half, text=f"{name} s{i}", paragraph_index=0)
        for i in range(2)
    ]
    words = [
        Word(index=i, sentence_index=i, start_sec=i * half, duration_sec=half, text=f"w{i}")
        for i in range(2)
    ]
    paragraphs = [
        Paragraph(index=0, start_sec=0.0, duration_sec=seconds, text=name, sentence_indices=[0, 1])
    ]
    return BoundaryMap(sentences=sentences, words=words, paragraphs=paragraphs)


def _file_pcm(mark: int, seconds: float) -> bytes:
    """Expected decoded PCM of a file written by _write_wav (native format)."""
    return array.array("h", [mark] * int(WAV_RATE * seconds * WAV_CH)).tobytes()


def _write_wav(path: Path, mark: int, seconds: float) -> Path:
    """Writes one real WAV file whose every sample carries ``mark``.

    44100/stereo is the decode-native format: ``miniaudio.decode`` passes
    the bytes through unchanged, so tests can assert on exact PCM.
    """
    path.write_bytes(pcm_to_wav(_file_pcm(mark, seconds), WAV_RATE, WAV_CH, 2))
    return path


# --- assembly: one continuous PCM stream -----------------------------------------------


def test_assemble_concatenates_files_in_order_with_no_inserted_gap():
    """US-AT-08-3: default 0 ms — the stream is exactly file1's PCM then file2's."""
    chain = assemble_chain(
        [ChainSource(_decoded(11, 0.5), "a.wav"), ChainSource(_decoded(22, 0.25), "b.wav")]
    )
    samples = chain.decoded.samples
    assert len(samples) == int(RATE * 0.75)
    assert list(samples[: int(RATE * 0.5)]) == [11] * int(RATE * 0.5)
    assert list(samples[int(RATE * 0.5) :]) == [22] * int(RATE * 0.25)
    assert chain.decoded.sample_rate == RATE
    assert chain.decoded.duration == pytest.approx(0.75)


def test_assemble_inserts_the_configurable_silence_between_items():
    chain = assemble_chain(
        [ChainSource(_decoded(1, 1.0), "a.wav"), ChainSource(_decoded(2, 1.0), "b.wav")],
        gap_ms=100.0,
    )
    samples = chain.decoded.samples
    silence = int(RATE * 0.1)
    assert len(samples) == 2 * RATE + silence
    assert list(samples[RATE : RATE + silence]) == [0] * silence  # silence between, not around
    assert list(samples[:RATE]) == [1] * RATE
    assert list(samples[RATE + silence :]) == [2] * RATE


def test_assemble_rejects_empty_mixted_and_negative_inputs():
    with pytest.raises(ChainError):
        assemble_chain([])
    with pytest.raises(ChainError):
        assemble_chain(
            [ChainSource(_decoded(1, 0.1), "a.wav"), ChainSource(_decoded(2, 0.1, rate=44100), "b.wav")]
        )
    with pytest.raises(ChainError):
        assemble_chain([ChainSource(_decoded(1, 0.1), "a.wav")], gap_ms=-1)


def test_assemble_is_frame_aligned_for_stereo_too():
    chain = assemble_chain(
        [ChainSource(_decoded(1, 0.5, nchannels=2), "a.wav"), ChainSource(_decoded(2, 0.5, nchannels=2), "b.wav")],
        gap_ms=50.0,
    )
    silence_frames = int(RATE * 0.05)
    assert len(chain.decoded.samples) == (int(RATE * 0.5) + silence_frames + int(RATE * 0.5)) * 2


# --- combined BoundaryMap --------------------------------------------------------------


def test_combined_map_rebases_sentences_words_paragraphs_chain_globally():
    """RF-AT-08-4: per-file maps compose onto chain-global positions."""
    chain = assemble_chain(
        [
            ChainSource(_decoded(1, 1.0), "a.wav", boundaries=_two_sentence_map("a", 1.0)),
            ChainSource(_decoded(2, 1.0), "b.wav", boundaries=_two_sentence_map("b", 1.0)),
        ],
        gap_ms=250.0,
    )
    bmap = chain.boundaries
    # Four sentences, globally indexed, each half shifted by the item
    # offset (1.0 s of audio + 0.25 s of silence before file b).
    assert [s.index for s in bmap.sentences] == [0, 1, 2, 3]
    assert bmap.sentences[2].start_sec == pytest.approx(1.25)
    assert bmap.sentences[3].start_sec == pytest.approx(1.75)
    assert bmap.sentences[2].text == "b s0"
    # Words rebase with their sentences (index and sentence_index).
    assert [w.index for w in bmap.words] == [0, 1, 2, 3]
    assert [w.sentence_index for w in bmap.words] == [0, 1, 2, 3]
    assert bmap.words[2].start_sec == pytest.approx(1.25)
    # Paragraphs stay per-file but chain-global in time.
    assert [p.index for p in bmap.paragraphs] == [0, 1]
    assert bmap.paragraphs[1].start_sec == pytest.approx(1.25)
    assert bmap.paragraphs[1].sentence_indices == [2, 3]
    # Navigation lands chain-globally: file b's second sentence.
    assert bmap.get_sentence_at(1.8).index == 3


def test_synthetic_map_gives_one_navigation_unit_per_file():
    """Files without boundary metadata: each file IS one sentence/paragraph."""
    chain = assemble_chain(
        [ChainSource(_decoded(1, 0.5), "a.wav"), ChainSource(_decoded(2, 0.5), "b.wav"),
         ChainSource(_decoded(3, 0.5), "c.wav")],
        gap_ms=100.0,
    )
    bmap = chain.boundaries
    assert [s.text for s in bmap.sentences] == ["a.wav", "b.wav", "c.wav"]
    assert [s.start_sec for s in bmap.sentences] == pytest.approx([0.0, 0.6, 1.2])
    assert [p.index for p in bmap.paragraphs] == [0, 1, 2]
    assert bmap.get_sentence_at(0.7).text == "b.wav"
    # Item views carry the chain-global offsets the trace seam watches.
    assert [it.start_sec for it in chain.items] == pytest.approx([0.0, 0.6, 1.2])
    assert [it.duration_sec for it in chain.items] == pytest.approx([0.5, 0.5, 0.5])


# --- file inputs -----------------------------------------------------------------------


def test_missing_chain_files_lists_absent_paths(tmp_path):
    a = _write_wav(tmp_path / "a.wav", 1, 0.1)
    missing = missing_chain_files([str(a), str(tmp_path / "gone.wav")])
    assert missing == [str(tmp_path / "gone.wav")]


def test_decode_chain_files_raises_chain_error_for_undecodable(tmp_path):
    good = _write_wav(tmp_path / "a.wav", 1, 0.1)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(ChainError):
        decode_chain_files([str(good), str(bad)])


def test_assemble_chain_files_decodes_real_files(tmp_path):
    a = _write_wav(tmp_path / "a.wav", 7, 0.2)
    b = _write_wav(tmp_path / "b.wav", 9, 0.1)
    chain = assemble_chain_files([str(a), str(b)])
    assert chain.decoded.samples.tobytes() == _file_pcm(7, 0.2) + _file_pcm(9, 0.1)
    assert chain.decoded.sample_rate == WAV_RATE and chain.decoded.nchannels == WAV_CH
    assert [it.name for it in chain.items] == ["a.wav", "b.wav"]


# --- session-level controls over the whole chain ---------------------------------------


def _chain_session(files=3, seconds=1.0, gap_ms=0.0):
    """Session loaded with an assembled 2-sentence-per-file chain (no device)."""
    sources = [
        ChainSource(_decoded(i + 1, seconds), f"f{i}.wav", boundaries=_two_sentence_map(f"f{i}", seconds))
        for i in range(files)
    ]
    chain = assemble_chain(sources, gap_ms=gap_ms)
    session = AudioSession(label="chain-test")
    session.boundaries = chain.boundaries
    # Load the buffer exactly the way play() would (without the device).
    session._load_decoded_locked(chain.decoded)
    session.state["status"] = "playing"
    return session, chain


def test_seek_addresses_chain_global_positions():
    session, chain = _chain_session(files=3, seconds=1.0)
    # File 3's second sentence starts at chain-global 2.5 s.
    reply = session.handle_ipc_command("seek +2.5")
    assert "pos=2.50" in reply
    assert session.current_frame == round(2.5 * RATE)
    # Clamped at the chain end, never beyond.
    session.handle_ipc_command("seek +999")
    assert session.current_frame <= session.total_frames


def test_phrase_navigation_crosses_file_boundaries():
    session, chain = _chain_session(files=3, seconds=1.0)
    # Stand at file 2's last sentence (chain-global 1.5): the next
    # sentence belongs to file 3 and must be reachable in one hop.
    session.current_frame = round(1.6 * RATE)
    reply = session.handle_ipc_command("next-sentence")
    assert "sent_idx=4" in reply and "pos=2.00" in reply
    assert session.current_frame == round(2.0 * RATE)
    # And the current sentence at that position is file 3's first.
    assert "text=f2 s0" in session.handle_ipc_command("sentence")


def test_pause_resume_and_scroll_info_span_the_whole_chain():
    session, chain = _chain_session(files=3, seconds=1.0, gap_ms=100.0)
    session.current_frame = round(1.5 * RATE)
    assert "status=paused" in session.handle_ipc_command("pause")
    assert "status=playing" in session.handle_ipc_command("resume")
    info = session.handle_ipc_command("scroll-info")
    total = 3 * 1.0 + 2 * 0.1
    assert f"total={total:.2f}" in info
    assert "total_sents=6" in info and "total_paras=3" in info
