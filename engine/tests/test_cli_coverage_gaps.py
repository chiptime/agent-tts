"""Coverage-gap tests for agent_tts.cli (D4 module-floor remediation, VS4).

The fallback wrapper added its own fully-covered code to cli.py; this file
closes the module's PRE-EXISTING uncovered paths so the whole touched module
reaches the D4 floor (>=90% lines and branches):

- ``_synthesize_single`` auto-language multi-segment composition (boundaries,
  estimation, decode failures, empty segments, stop, output file).
- ``_speak_pipelined`` error/degradation paths (provider construction
  failure, frame-decoder fallbacks, feeder errors, group-stream
  extra/missing/undecodable chunks, append skips, close failures).
- ``_play_speech`` stream/podcast/boundaries/no-audio branches and the
  ``speak`` error handler.
- ``main()`` dispatch branches (voice subcommand, --winhost, --podcast-serve,
  ipc shortcuts, stdin, --session-id sources, empty-input exits).
- Reply-rendering helper edge branches.

House rules: deterministic fakes only (no network, no audio device, no real
sleeps); files under tmp_path; the fallback chain is forced off via env so
``cli.synthesize`` takes the historical direct path.
"""

import array
import asyncio
import json
import os
import sys
import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import agent_tts.audio_store as audio_store_mod
import agent_tts.daemon as daemon_mod
import agent_tts.podcast as podcast_mod
from agent_tts import cli
from agent_tts import fallback
from agent_tts.boundaries import BoundaryMap, Sentence, SynthesisResult, Word
from agent_tts.providers.base import TTSProvider
from agent_tts.sources.base import SourceResult
from agent_tts.text import split_sentence_groups

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_24k.mp3")

MP3_BYTES = b"fake-mp3-bytes-for-tests"
BAD_BYTES = b"BAD-undecodable-chunk"


# --- shared deterministic fakes ------------------------------------------------


class FakeDecoded:
    """Decoded-audio stand-in: enough surface for the cli pipeline."""

    def __init__(self, n_samples=2400, sample_rate=24000, nchannels=1):
        self.samples = array.array("h", bytes(n_samples * 2))
        self.sample_rate = sample_rate
        self.nchannels = nchannels
        self.duration = n_samples / float(sample_rate)


def fake_miniaudio(monkeypatch, fail_on=BAD_BYTES):
    """Replaces cli's miniaudio with a deterministic decoder."""

    def decode(data):
        raw = bytes(data)
        if fail_on and raw.startswith(fail_on):
            raise ValueError("corrupt mp3 payload")
        return FakeDecoded()

    monkeypatch.setattr(cli, "miniaudio", SimpleNamespace(decode=decode))


class FakeSession:
    """Playback session duck-type: records instead of touching a device."""

    def __init__(self):
        self.lock = threading.Lock()
        self.boundaries = BoundaryMap()
        self.state = {}
        self.prepared = []
        self.played = []
        self.appended = []
        self.append_results = []

    def prepare_pcm(self, decoded):
        self.prepared.append(decoded)

    def append_pcm(self, decoded):
        self.appended.append(decoded)
        if self.append_results:
            return self.append_results.pop(0)
        return True

    def play(self, decoded):
        self.played.append(decoded)


class VoiceScriptedEngine(TTSProvider):
    """Returns queued results per resolved voice (auto-lang tests)."""

    name = "edge"

    def __init__(self, by_voice):
        self.by_voice = by_voice
        self.calls = []

    async def synthesize(self, text=None, voice=None, rate=None, volume=None,
                         pitch=None, stop_checker=None):
        self.calls.append((voice, text))
        queue = self.by_voice[voice]
        return queue.popleft() if isinstance(queue, deque) else queue


class ScriptedGroupStream:
    """Sync stream iterator with scriptable failure and close() error."""

    def __init__(self, chunks, fail_at=None, close_error=False):
        self.chunks = list(chunks)
        self.i = 0
        self.fail_at = fail_at
        self.close_error = close_error
        self.close_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.fail_at is not None and self.i == self.fail_at:
            raise ValueError("group stream broke")
        if self.i >= len(self.chunks):
            raise StopIteration
        chunk = self.chunks[self.i]
        self.i += 1
        return chunk

    def close(self):
        self.close_count += 1
        if self.close_error:
            raise RuntimeError("close failed")


class GroupChunkEngine(TTSProvider):
    """Engine whose stream yields whole sentence-group chunks (piper shape)."""

    name = "edge"
    supports_stream = True
    stream_yields_group_chunks = True

    def __init__(self, stream):
        self.stream = stream

    def synthesize_stream(self, text, voice, rate="+0%", volume="+0%",
                          pitch="+0Hz", stop_checker=None, on_event=None):
        return self.stream


def long_group_text(n_groups):
    """Builds a text that split_sentence_groups cuts into exactly n groups."""
    sentences = []
    for i in range(n_groups):
        filler = " ".join(f"palabra{i}_{j}" for j in range(40))
        sentences.append(f"Frase larga numero {i} con {filler}.")
    text = " ".join(sentences)
    assert len(split_sentence_groups(text)) == n_groups  # sanity for the script
    return text


def run_pipelined(session, text, check_stop=lambda: False, engine=None,
                  stream="groups", output_file=None, podcast=True):
    return asyncio.run(
        cli._speak_pipelined(
            session=session,
            text=text,
            check_stop=check_stop,
            voice="elvira",
            rate="+0%",
            volume="+0%",
            pitch="+0Hz",
            provider="edge",
            openai_key=None,
            openai_base_url=None,
            openai_model=None,
            eleven_key=None,
            eleven_model=None,
            piper_model=None,
            auto_lang=False,
            output_file=output_file,
            podcast=podcast,
            engine=engine,
            stream=stream,
        )
    )


@pytest.fixture(autouse=True)
def fallback_off(monkeypatch, tmp_path):
    """Keeps the fallback chain disabled so cli.synthesize stays historical."""
    monkeypatch.setenv("AGENT_TTS_FALLBACK_CONFIG", str(tmp_path / "no-fallback.json"))
    fallback._reset_fallback_config_cache()
    yield
    fallback._reset_fallback_config_cache()


@pytest.fixture(autouse=True)
def retention_off(monkeypatch):
    """Never writes rendered audio to the user's audio store from these tests."""
    monkeypatch.setattr(audio_store_mod, "retention_days", lambda: 0)


# --- auto-language multi-segment synthesis (_synthesize_single) ----------------

BILINGUAL_TEXT = "El archivo está listo. The build failed and the tests are broken."
TRILINGUAL_TEXT = "El código está roto. The pipeline failed again. La prueba funciona bien."


def test_auto_lang_multi_segment_combines_boundaries_and_writes_output(tmp_path):
    es_map = BoundaryMap(
        sentences=[Sentence(index=0, start_sec=0.0, duration_sec=1.5, text="El archivo está listo.")],
        words=[Word(index=0, sentence_index=0, start_sec=0.0, duration_sec=0.5, text="El")],
    )
    en_map = BoundaryMap(
        sentences=[Sentence(index=0, start_sec=0.0, duration_sec=2.0, text="The build failed.")],
        words=[Word(index=0, sentence_index=0, start_sec=0.0, duration_sec=0.4, text="The")],
    )
    engine = VoiceScriptedEngine({
        "elvira": SynthesisResult(b"es-audio", es_map),
        "en-US-JennyNeural": SynthesisResult(b"en-audio", en_map),
    })
    out = tmp_path / "nested" / "out.mp3"

    result = asyncio.run(
        cli.synthesize(BILINGUAL_TEXT, voice="elvira", auto_lang=True,
                       engine=engine, output_file=str(out))
    )

    # One synthesis per detected language segment, with the resolved voice.
    assert [v for v, _ in engine.calls] == ["elvira", "en-US-JennyNeural"]
    assert result == b"es-audioen-audio"
    assert out.read_bytes() == b"es-audioen-audio"
    # Combined boundary map: sentence offset shifts by the first segment end.
    sentences = result.boundaries.sentences
    assert len(sentences) == 2
    assert sentences[1].start_sec == pytest.approx(1.5)
    assert sentences[1].index == 1
    # The English word is rebased onto the global sentence indexing.
    assert result.boundaries.words[1].sentence_index == 1


def test_auto_lang_skips_empty_segments_and_estimates_without_boundaries(monkeypatch):
    fake_miniaudio(monkeypatch)
    engine = VoiceScriptedEngine({
        "alvaro": deque([b"mp3-es", BAD_BYTES]),   # estimate path, then decode failure
        "en-US-GuyNeural": deque([b""]),           # empty segment -> skipped
    })

    result = asyncio.run(
        cli.synthesize(TRILINGUAL_TEXT, voice="alvaro", auto_lang=True, engine=engine)
    )

    assert len(engine.calls) == 3  # the empty segment is still attempted
    # Both non-empty chunks are kept even when boundaries cannot be built.
    assert result == b"mp3-es" + BAD_BYTES
    # The first segment got estimated boundaries from the decoded duration.
    assert len(result.boundaries.sentences) >= 1
    assert result.boundaries.sentences[0].text.startswith("El código")


def test_auto_lang_all_segments_empty_returns_empty_result():
    engine = VoiceScriptedEngine({"elvira": b"", "en-US-JennyNeural": b""})
    result = asyncio.run(
        cli.synthesize(BILINGUAL_TEXT, voice="elvira", auto_lang=True, engine=engine)
    )
    assert bytes(result) == b""
    assert isinstance(result, SynthesisResult)
    assert result.boundaries.sentences == []


def test_auto_lang_stop_checker_aborts_before_first_segment():
    engine = VoiceScriptedEngine({"elvira": b"x", "en-US-JennyNeural": b"y"})
    result = asyncio.run(
        cli.synthesize(BILINGUAL_TEXT, voice="elvira", auto_lang=True,
                       engine=engine, stop_checker=lambda: True)
    )
    assert bytes(result) == b""
    assert engine.calls == []  # stopped before any network attempt


def test_auto_lang_single_language_takes_the_plain_path():
    text = "El sistema funciona perfectamente."
    engine = VoiceScriptedEngine({"elvira": SynthesisResult(b"one-lang", BoundaryMap())})
    result = asyncio.run(
        cli.synthesize(text, voice="elvira", auto_lang=True, engine=engine)
    )
    assert result == b"one-lang"
    # Single detected language: one plain call with the requested voice.
    assert engine.calls == [("elvira", text)]


def test_synthesize_empty_audio_returns_empty_bytes():
    engine = VoiceScriptedEngine({"elvira": b""})
    result = asyncio.run(cli.synthesize("Hola.", voice="elvira", engine=engine))
    assert result == b""


def test_synthesize_single_attempt_writes_output_file(tmp_path):
    bmap = BoundaryMap(sentences=[Sentence(index=0, start_sec=0.0, duration_sec=1.0, text="Hola.")])
    engine = VoiceScriptedEngine({"elvira": SynthesisResult(b"single", bmap)})
    out = tmp_path / "single.mp3"
    result = asyncio.run(
        cli.synthesize("Hola.", voice="elvira", engine=engine, output_file=str(out))
    )
    assert result == b"single"
    assert out.read_bytes() == b"single"


# --- _speak_pipelined: degraded and error paths --------------------------------


def test_pipelined_group_loop_survives_provider_construction_failure(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no provider")

    monkeypatch.setattr(cli, "get_provider", boom)

    async def fake_synthesize(**kwargs):
        return MP3_BYTES

    monkeypatch.setattr(cli, "synthesize", fake_synthesize)
    fake_miniaudio(monkeypatch)
    session = FakeSession()
    text = long_group_text(3)

    run_pipelined(session, text, engine=None)

    # Provider construction failed (engine stays None) but the per-group
    # path still synthesized, merged and played every group.
    assert session.prepared and len(session.played) == 1
    assert len(session.appended) == 2  # remaining groups after the first


def test_pipelined_group_failures_then_success(monkeypatch, capsys):
    class Raise:
        def __init__(self, exc):
            self.exc = exc

    queue = deque([
        Raise(RuntimeError("synthesis exploded")),  # group 0: exception
        b"",                                       # group 1: empty audio
        BAD_BYTES,                                 # group 2: undecodable
        MP3_BYTES,                                 # group 3: first success
        MP3_BYTES,                                 # group 4: append refused
        Raise(RuntimeError("late failure")),       # group 5: exception
        b"",                                       # group 6: empty audio
        BAD_BYTES,                                 # group 7: undecodable
    ])

    async def fake_synthesize(**kwargs):
        item = queue.popleft()
        if isinstance(item, Raise):
            raise item.exc
        return item

    monkeypatch.setattr(cli, "synthesize", fake_synthesize)
    fake_miniaudio(monkeypatch)
    session = FakeSession()
    session.append_results = [False]  # first remaining append is refused
    text = long_group_text(8)

    run_pipelined(session, text, engine=None)

    err = capsys.readouterr().err
    assert "group 0 failed: synthesis exploded" in err
    assert "group 1 failed: empty audio" in err
    assert "group 2 failed:" in err
    assert "group 5 failed: late failure" in err
    assert "group 6 failed: empty audio" in err
    assert "group 7 failed:" in err
    assert len(session.played) == 1


def test_pipelined_merges_real_boundaries_from_streamed_group(monkeypatch):
    bmap = BoundaryMap(
        sentences=[Sentence(index=0, start_sec=0.0, duration_sec=1.0, text="Frase con limites.")]
    )

    async def fake_synthesize(**kwargs):
        return SynthesisResult(b"with-map", bmap)

    monkeypatch.setattr(cli, "synthesize", fake_synthesize)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(2), engine=None)

    # merge_group used the provided boundary map (not an estimation).
    assert session.boundaries.sentences[0].text == "Frase con limites."


class NoStreamInitEngine(TTSProvider):
    name = "edge"
    supports_stream = True
    stream_yields_group_chunks = False

    def synthesize_stream(self, *args, **kwargs):
        raise RuntimeError("cannot start stream")


def test_pipelined_frames_init_failure_falls_back_to_groups(monkeypatch, capsys):
    async def fake_synthesize(**kwargs):
        return MP3_BYTES

    monkeypatch.setattr(cli, "synthesize", fake_synthesize)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(2), engine=NoStreamInitEngine(), stream="frames")

    err = capsys.readouterr().err
    assert "frame streaming failed to initialize" in err
    assert len(session.played) == 1


class ChunkedFrameEngine(TTSProvider):
    """Frame-stream engine over the real MP3 fixture, with optional hooks."""

    name = "edge"
    supports_stream = True
    stream_yields_group_chunks = False

    def __init__(self, data, events=None, after_chunk=None):
        self.data = data
        self.events = events or []
        self.after_chunk = after_chunk

    def synthesize_stream(self, text, voice, rate="+0%", volume="+0%",
                          pitch="+0Hz", stop_checker=None, on_event=None):
        if on_event:
            for ev in self.events:
                on_event(ev)
        for i in range(0, len(self.data), 720):
            yield self.data[i:i + 720]
            if self.after_chunk:
                self.after_chunk(i)


@pytest.fixture(scope="module")
def fixture_mp3():
    with open(FIXTURE_PATH, "rb") as f:
        return f.read()


def test_pipelined_frame_feeder_error_is_reported_not_fatal(monkeypatch, capsys, fixture_mp3):
    def explode_after(last_offset):
        if last_offset >= len(fixture_mp3) - 720:
            raise RuntimeError("network died")

    engine = ChunkedFrameEngine(fixture_mp3, events=[
        {"type": "WordBoundary", "offset": 1000000, "duration": 2500000, "text": "Prueba"},
        {"type": "SentenceBoundary", "offset": 0, "text": "Prueba corta."},  # ignored type
    ], after_chunk=explode_after)
    session = FakeSession()

    run_pipelined(session, "Prueba corta.", engine=engine, stream="frames")

    err = capsys.readouterr().err
    assert "network feeder error: network died" in err
    assert len(session.played) == 1
    # The WordBoundary event shaped the live map (the SentenceBoundary was ignored).
    assert session.boundaries.words[0].text == "Prueba"


def test_pipelined_frame_feeder_stops_when_check_stop_flips(monkeypatch, fixture_mp3):
    stop = [False]
    engine = ChunkedFrameEngine(fixture_mp3, after_chunk=lambda i: stop.__setitem__(0, i >= 720))
    session = FakeSession()

    run_pipelined(session, "Prueba corta.", check_stop=lambda: stop[0],
                  engine=engine, stream="frames")

    # The feeder broke out of its loop; playback still finished cleanly.
    assert len(session.played) == 1


class EmptyDecoder:
    def __init__(self, *args, **kwargs):
        pass

    def feed_bytes(self, data):
        pass

    def finish_stream(self):
        pass

    def decode_generator(self):
        return iter(())


class ExplodingDecoder:
    def __init__(self, *args, **kwargs):
        pass

    def feed_bytes(self, data):
        pass

    def finish_stream(self):
        pass

    def decode_generator(self):
        raise RuntimeError("decoder exploded")
        yield  # noqa: unreachable - makes this a generator function


@pytest.mark.parametrize("decoder_cls,expected_err", [
    (EmptyDecoder, None),  # decoder exhausts without frames -> silent group fallback
    (ExplodingDecoder, "frame decoder error"),
])
def test_pipelined_frames_decoder_failure_falls_back_to_groups(
    monkeypatch, capsys, fixture_mp3, decoder_cls, expected_err
):
    async def fake_synthesize(**kwargs):
        return MP3_BYTES

    monkeypatch.setattr(cli, "Mp3StreamDecoder", decoder_cls)
    monkeypatch.setattr(cli, "synthesize", fake_synthesize)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, "Primera oracion. Segunda oracion.",
                  engine=ChunkedFrameEngine(fixture_mp3), stream="frames")

    err = capsys.readouterr().err
    if expected_err:
        assert expected_err in err
    assert len(session.played) == 1


class CountdownDecoder:
    """Yields two decoded chunks, refuses one append, then blows up."""

    def __init__(self, *args, **kwargs):
        pass

    def feed_bytes(self, data):
        pass

    def finish_stream(self):
        pass

    def decode_generator(self):
        yield FakeDecoded()
        yield FakeDecoded()
        raise RuntimeError("decoder blew mid-stream")
        yield  # noqa: unreachable - makes this a generator function


def test_pipelined_remaining_frames_error_surfaces_after_play(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Mp3StreamDecoder", CountdownDecoder)
    fake_miniaudio(monkeypatch)
    session = FakeSession()
    session.append_results = [False]  # first remaining append refused

    with pytest.raises(RuntimeError, match="decoder blew mid-stream"):
        run_pipelined(session, "Prueba corta.",
                      engine=ChunkedFrameEngine(b"ignored-by-fake-decoder"),
                      stream="frames")

    err = capsys.readouterr().err
    assert "frame decoding error mid-stream" in err
    # Playback started (first chunk) before the producer died.
    assert len(session.played) == 1


def test_pipelined_group_stream_extra_and_undecodable_chunks(monkeypatch, capsys):
    stream = ScriptedGroupStream(
        [BAD_BYTES, MP3_BYTES, MP3_BYTES, MP3_BYTES], close_error=True
    )
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, "Una sola frase.", engine=GroupChunkEngine(stream))

    err = capsys.readouterr().err
    assert "group 0 failed:" in err                       # first chunk undecodable
    assert "more chunks than sentence groups" in err      # degraded boundaries
    assert stream.close_count >= 2                        # inner + safety-net close
    assert len(session.played) == 1


def test_pipelined_group_stream_remaining_decode_failure_and_append_refusal(
    monkeypatch, capsys
):
    stream = ScriptedGroupStream([MP3_BYTES, MP3_BYTES, BAD_BYTES, MP3_BYTES])
    fake_miniaudio(monkeypatch)
    session = FakeSession()
    session.append_results = [False]  # first remaining append is refused

    run_pipelined(session, long_group_text(2), engine=GroupChunkEngine(stream))

    err = capsys.readouterr().err
    assert "group 2 failed:" in err      # undecodable chunk skipped mid-stream
    assert len(session.played) == 1
    # Both remaining decodes were attempted (one refused, one accepted)...
    assert len(session.appended) == 2
    # ...but only the first-phase group merged boundaries: the refused append
    # (idx1) skipped its merge, and idx3 maps to no group.
    assert len(session.boundaries.sentences) == 1


def test_pipelined_all_groups_fail_raises_no_audio(monkeypatch, capsys):
    async def always_raises(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(cli, "synthesize", always_raises)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    with pytest.raises(RuntimeError, match="produced no audio"):
        run_pipelined(session, long_group_text(2), engine=None)

    assert session.played == []


def test_pipelined_stop_on_empty_group_returns_silently(monkeypatch):
    stop = [False]

    async def empty_then_stop(**kwargs):
        result = b""
        stop[0] = True  # the stop arrives with the empty result
        return result

    monkeypatch.setattr(cli, "synthesize", empty_then_stop)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(2), check_stop=lambda: stop[0], engine=None)

    assert session.played == [] and session.prepared == []


def test_pipelined_remaining_group_empty_audio_warns(monkeypatch, capsys):
    queue = deque([MP3_BYTES, b""])

    async def fake_synthesize(**kwargs):
        return queue.popleft()

    monkeypatch.setattr(cli, "synthesize", fake_synthesize)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(2), engine=None)

    assert "group 1 failed: empty audio" in capsys.readouterr().err
    assert len(session.played) == 1


def test_pipelined_stop_during_remaining_empty_group_breaks(monkeypatch):
    stop = [False]
    queue = deque([MP3_BYTES, b""])

    async def empty_and_stop(**kwargs):
        result = queue.popleft()
        if not result:
            stop[0] = True  # the stop arrives with the empty remaining group
        return result

    monkeypatch.setattr(cli, "synthesize", empty_and_stop)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(2),
                  check_stop=lambda: stop[0], engine=None)

    assert len(session.played) == 1


def test_pipelined_group_stream_missing_groups_warn(capsys, monkeypatch):
    stream = ScriptedGroupStream([MP3_BYTES, MP3_BYTES])
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(4), engine=GroupChunkEngine(stream))

    err = capsys.readouterr().err
    assert "sentence groups produced no chunk" in err
    assert len(session.played) == 1


def test_pipelined_group_stream_pull_failure_fails_the_stream(monkeypatch, capsys):
    stream = ScriptedGroupStream([MP3_BYTES], fail_at=1)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, long_group_text(3), engine=GroupChunkEngine(stream))

    err = capsys.readouterr().err
    assert "group 1 failed: group stream broke" in err
    assert session.appended == []  # producer failed before any remaining append
    assert len(session.played) == 1


def test_pipelined_stopped_before_first_chunk_returns_silently(monkeypatch):
    stream = ScriptedGroupStream([MP3_BYTES])
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    run_pipelined(session, "Una frase.", check_stop=lambda: True,
                  engine=GroupChunkEngine(stream))

    assert session.played == [] and session.prepared == []


def test_pipelined_stopped_in_group_loop_never_synthesizes(monkeypatch):
    def boom(**kwargs):
        raise AssertionError("synthesize must not run when stopped")

    monkeypatch.setattr(cli, "get_provider", boom)
    monkeypatch.setattr(cli, "synthesize", boom)
    session = FakeSession()

    run_pipelined(session, "Una frase.", check_stop=lambda: True, engine=None)

    assert session.played == []


def test_pipelined_persist_failure_is_fail_open(monkeypatch, capsys, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    stream = ScriptedGroupStream([MP3_BYTES])
    fake_miniaudio(monkeypatch)
    monkeypatch.setattr(audio_store_mod, "merge_chunks_to_audio", lambda chunks: b"merged")
    session = FakeSession()

    run_pipelined(session, "Una frase.", engine=GroupChunkEngine(stream),
                  output_file=str(blocker / "out.mp3"), podcast=False)

    err = capsys.readouterr().err
    assert "could not persist rendered audio" in err
    assert len(session.played) == 1


def test_build_playback_session_notes_remote_visuals(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    session = cli._build_playback_session("winhost", "L", highlight=True, env={})
    err = capsys.readouterr().err
    assert "streams audio to the Windows host" in err
    assert session.target == "winhost"


# --- _play_speech branches ------------------------------------------------------


def test_play_speech_uses_pipelined_stream_and_returns(monkeypatch):
    stream = ScriptedGroupStream([MP3_BYTES])
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    asyncio.run(
        cli._play_speech(session, "Hola mundo.", provider="edge",
                         engine=GroupChunkEngine(stream), stream="groups")
    )

    assert len(session.played) == 1


def test_play_speech_returns_without_audio_on_empty_synthesis(monkeypatch):
    async def empty(**kwargs):
        return b""

    monkeypatch.setattr(cli, "synthesize", empty)
    session = FakeSession()

    asyncio.run(cli._play_speech(session, "Hola.", stream="off"))

    assert session.played == []


def test_play_speech_returns_when_session_already_stopped(monkeypatch):
    async def ok(**kwargs):
        return MP3_BYTES

    monkeypatch.setattr(cli, "synthesize", ok)
    session = FakeSession()
    session.state["stop"] = True

    asyncio.run(cli._play_speech(session, "Hola.", stream="off"))

    assert session.played == []


def test_play_speech_podcast_adds_episode_and_keeps_boundaries(monkeypatch):
    recorded = []

    class FakeFeed:
        def add_episode(self, mp3_data, title, description):
            recorded.append((bytes(mp3_data), title, description))

    monkeypatch.setattr(podcast_mod, "PodcastFeed", FakeFeed)

    async def ok(**kwargs):
        bmap = BoundaryMap(sentences=[Sentence(index=0, start_sec=0.0, duration_sec=1.0, text="Hola.")])
        return SynthesisResult(b"podcast-audio", bmap)

    monkeypatch.setattr(cli, "synthesize", ok)
    session = FakeSession()

    asyncio.run(
        cli._play_speech(None, "Hola.", no_play=True, podcast=True,
                         podcast_title="", stream="off")
    )
    asyncio.run(
        cli._play_speech(session, "Hola.", no_play=True, stream="off")
    )

    # Podcast episode recorded with the spoken text as default title.
    assert recorded == [(b"podcast-audio", "Hola.", "Hola.")]
    # Session adopted the synthesis boundary map even without playing.
    assert session.boundaries.sentences[0].text == "Hola."
    assert session.played == []


def test_play_speech_local_play_estimates_boundaries(monkeypatch):
    async def ok(**kwargs):
        return MP3_BYTES  # plain bytes: no native boundaries

    monkeypatch.setattr(cli, "synthesize", ok)
    fake_miniaudio(monkeypatch)
    session = FakeSession()

    asyncio.run(cli._play_speech(session, "Hola mundo.", stream="off"))

    # No boundaries from the provider: the session estimates from the text.
    assert len(session.boundaries.sentences) >= 1
    assert len(session.played) == 1


def test_build_playback_session_local(monkeypatch):
    from agent_tts.audio import AudioSession

    session = cli._build_playback_session("local", "L", highlight=True, provider="edge")
    assert isinstance(session, AudioSession)
    assert session.provider == "edge"


def test_build_playback_session_wsl_ps_unavailable_exits(monkeypatch, capsys):
    monkeypatch.setattr(cli, "is_wsl_ps_available", lambda: False)
    with pytest.raises(SystemExit) as excinfo:
        cli._build_playback_session("wsl-ps", "L")
    assert excinfo.value.code == 1
    assert "requires powershell.exe" in capsys.readouterr().err


# --- speak() error handler ------------------------------------------------------


def test_speak_prints_playback_error_and_reraises(monkeypatch, capsys):
    async def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "_play_speech", boom)
    with pytest.raises(ValueError, match="boom"):
        asyncio.run(cli.speak("hola", no_play=True))
    assert "Playback error: boom" in capsys.readouterr().err


# --- main() dispatch branches ---------------------------------------------------


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    return excinfo.value.code


def test_main_prune_failure_is_fail_open(monkeypatch):
    def boom():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(audio_store_mod, "prune_expired", boom)
    assert _run_cli(monkeypatch, ["cli.py", "--help"]) == 0


def test_main_voice_subcommand_dispatches_before_parsing(monkeypatch):
    calls = []

    def fake_voice(argv):
        calls.append(list(argv))
        return 7

    import agent_tts.voices as voices_mod
    monkeypatch.setattr(voices_mod, "handle_voice_command", fake_voice)
    assert _run_cli(monkeypatch, ["cli.py", "voice", "install", "x"]) == 7
    assert calls == [["install", "x"]]


def test_main_winhost_server_mode(monkeypatch):
    import agent_tts.winhost as winhost_mod
    with mock.patch.object(winhost_mod, "run_winhost_server") as run_server:
        code = _run_cli(monkeypatch, ["cli.py", "--winhost",
                                      "--winhost-host", "winbox",
                                      "--winhost-port", "7718"])
    assert code == 0
    run_server.assert_called_once_with(host="winbox", port=7718)
    assert os.environ["AGENT_TTS_WINHOST_HOST"] == "winbox"
    assert os.environ["AGENT_TTS_WINHOST_PORT"] == "7718"


def test_main_podcast_server_mode(monkeypatch):
    with mock.patch.object(podcast_mod, "run_podcast_server") as run_server:
        code = _run_cli(monkeypatch, ["cli.py", "--podcast-serve"])
    assert code == 0
    run_server.assert_called_once_with(port=8844)


@pytest.mark.parametrize("flag,expected_cmd", [
    ("--prev-sentence", "prev-sentence"),
    ("--current-sentence", "sentence"),
    ("--next-paragraph", "next-paragraph"),
    ("--prev-paragraph", "prev-paragraph"),
    ("--current-paragraph", "paragraph"),
    ("--scroll-info", "scroll-info"),
])
def test_main_sentence_shortcuts_send_mapped_ipc_commands(monkeypatch, capsys, flag, expected_cmd):
    commands = []

    def fake_send(cmd, socket_path=None):
        commands.append(cmd)
        return "ok=true"

    monkeypatch.setattr(daemon_mod, "send_control_command", fake_send)
    code = _run_cli(monkeypatch, ["cli.py", flag])
    assert code == 0
    assert commands == [expected_cmd]
    assert capsys.readouterr().out == "ok=true\n"


class FakeStdin:
    def __init__(self, text="", tty=False):
        self._text = text
        self._tty = tty

    def isatty(self):
        return self._tty

    def read(self):
        return self._text


def test_main_reads_text_from_stdin(monkeypatch):
    payloads = []

    def fake_delegate(payload, queue=None):
        payloads.append(payload)
        return "ok=true"

    monkeypatch.setattr(daemon_mod, "delegate_play", fake_delegate)
    monkeypatch.setattr(cli, "clean_agent_text", lambda text, **kwargs: text)
    monkeypatch.setattr(sys, "stdin", FakeStdin("  texto desde stdin  "))

    assert _run_cli(monkeypatch, ["cli.py"]) == 0
    assert payloads[0]["text"] == "texto desde stdin"


def test_main_empty_input_exits_zero_without_delegating(monkeypatch):
    with mock.patch.object(daemon_mod, "delegate_play") as delegate:
        monkeypatch.setattr(sys, "stdin", FakeStdin(tty=True))
        assert _run_cli(monkeypatch, ["cli.py"]) == 0
    delegate.assert_not_called()


def _delegate_recorder(monkeypatch, payloads):
    def fake_delegate(payload, queue=None):
        payloads.append(payload)
        return "ok=true"

    monkeypatch.setattr(daemon_mod, "delegate_play", fake_delegate)
    monkeypatch.setattr(cli, "clean_agent_text", lambda text, **kwargs: text)


def test_main_session_id_source_resolves_last_message(monkeypatch, capsys):
    payloads = []
    _delegate_recorder(monkeypatch, payloads)

    def fake_read(agent, session_id):
        assert (agent, session_id) == ("opencode", "sess-1")
        return SourceResult("Mensaje final del agente", "opencode")

    monkeypatch.setattr(cli, "read_last_agent_message", fake_read)
    code = _run_cli(monkeypatch, ["cli.py", "--agent", "opencode",
                                  "--session-id", "sess-1", "fallback"])
    assert code == 0
    assert payloads[0]["text"] == "Mensaje final del agente"
    assert payloads[0]["persist_name"] == "opencode"
    assert "last message via opencode" in capsys.readouterr().err


def test_main_session_id_raw_source_skips_cleaning(monkeypatch, capsys):
    payloads = []
    _delegate_recorder(monkeypatch, payloads)
    with mock.patch.object(cli, "clean_agent_text") as clean:
        monkeypatch.setattr(
            cli, "read_last_agent_message",
            lambda agent, session_id: SourceResult("Texto crudo", "claude"),
        )
        code = _run_cli(monkeypatch, ["cli.py", "--raw", "--session-id", "s", "fallback"])
    assert code == 0
    clean.assert_not_called()
    assert payloads[0]["text"] == "Texto crudo"


def test_main_session_id_without_transcript_falls_back(monkeypatch, capsys):
    payloads = []
    _delegate_recorder(monkeypatch, payloads)
    monkeypatch.setattr(cli, "read_last_agent_message", lambda agent, session_id: None)
    code = _run_cli(monkeypatch, ["cli.py", "--session-id", "missing", "texto directo"])
    assert code == 0
    assert payloads[0]["text"] == "texto directo"
    assert "falling back to scrollback" in capsys.readouterr().err


def test_main_empty_speech_text_exits_zero(monkeypatch):
    monkeypatch.setattr(cli, "clean_agent_text", lambda text, **kwargs: "")
    with mock.patch.object(daemon_mod, "delegate_play") as delegate:
        assert _run_cli(monkeypatch, ["cli.py", "hola"]) == 0
    delegate.assert_not_called()


# --- reply-rendering helper edges -----------------------------------------------


def test_reply_error_text_bare_and_transport_shapes():
    assert cli._reply_error_text("ok=false") == "ok=false"
    assert cli._reply_error_text("ERR:") == "ERR:"
    assert cli._reply_error_text("") is None
    assert cli._reply_error_text("ok=true x=1") is None


def test_truncate_text_marks_the_cut():
    assert cli._truncate_text("short", 60) == "short"
    truncated = cli._truncate_text("x" * 80, 60)
    assert len(truncated) == 60
    assert truncated.endswith("...")


def test_queue_snapshot_from_reply_rejects_bad_json():
    assert cli._queue_snapshot_from_reply("ok=true") is None
    assert cli._queue_snapshot_from_reply("ok=true queue={not json}") is None
    snap = cli._queue_snapshot_from_reply('ok=true queue={"pending":[]}')
    assert snap == {"pending": []}


def test_render_status_reply_bad_json_prints_raw():
    reply = "ok=true queue={broken}"
    assert cli._render_status_reply(reply) == reply


def test_render_status_reply_bad_coalesced_defaults_to_one():
    snapshot = {"pending": [{
        "id": 1, "priority": "working", "policy": "queue",
        "coalesced": "not-a-number", "announcement": "A" * 80,
    }]}
    token = "queue=" + json.dumps(snapshot, separators=(",", ":"))
    rendered = cli._render_status_reply("ok=true state=playing " + token)
    lines = rendered.splitlines()
    assert lines[0] == "ok=true state=playing"
    assert "queued[1]" in lines[1]
    assert "coalesced=" not in lines[1]
    # Long announcements are truncated for single-line display.
    assert "text=" + "A" * 57 + "..." in lines[1]


def test_print_enqueue_ack_ignores_malformed_fields(capsys):
    cli._print_enqueue_ack("ok=true item=abc queue_len=2")
    assert capsys.readouterr().out == ""


def test_print_enqueue_ack_silent_when_item_left_pending(monkeypatch, capsys):
    import agent_tts.ipc as ipc_mod
    status = "ok=true queue=" + json.dumps({"pending": [{"id": 99}]},
                                           separators=(",", ":"))
    monkeypatch.setattr(ipc_mod, "send_ipc_command", lambda cmd: status)
    cli._print_enqueue_ack("ok=true item=5 queue_len=1")
    assert capsys.readouterr().out == ""


def test_print_enqueue_ack_survives_status_failure(monkeypatch, capsys):
    import agent_tts.ipc as ipc_mod

    def boom(cmd):
        raise OSError("no daemon")

    monkeypatch.setattr(ipc_mod, "send_ipc_command", boom)
    # The bare token (no '=') is skipped by the field parser.
    cli._print_enqueue_ack("ok=true acknowledged item=5 queue_len=3 coalesced=2")
    assert capsys.readouterr().out == "queued: item=5 position=3 queue_len=3 coalesced=2\n"
