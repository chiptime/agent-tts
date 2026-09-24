"""Command-line interface and high-level synthesis helpers for agent-tts."""

import argparse
import asyncio
import os
import signal
import sys
import threading
import time
from typing import List, Optional

from agent_tts.audio import AudioSession, _write_player_locks, cleanup_locks, play_mp3_file
from agent_tts.boundaries import (
    BoundaryMap,
    Paragraph,
    Sentence,
    Word,
    estimate_boundaries_from_text,
)
from agent_tts.cleaner import clean_agent_text
from agent_tts.constants import DEFAULT_RATE, DEFAULT_VOICE
from agent_tts.ipc import send_ipc_command
from agent_tts.playback_target import resolve_target
from agent_tts.powershell_playback import PowershellSession, is_wsl_ps_available
from agent_tts.providers import TTSProvider, get_provider
from agent_tts.sources import read_last_agent_message
from agent_tts.text import split_sentence_groups
from agent_tts.winhost_client import RemoteAudioSession
import miniaudio


def signal_handler(signum, frame):
    cleanup_locks()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
_sigterm = getattr(signal, "SIGTERM", None)
if _sigterm is not None:
    # SIGTERM is not delivered on Windows, but keep the guard portable.
    signal.signal(_sigterm, signal_handler)


async def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    volume: str = "+0%",
    pitch: str = "+0Hz",
    output_file: Optional[str] = None,
    provider: str = "edge",
    openai_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    openai_model: Optional[str] = None,
    eleven_key: Optional[str] = None,
    eleven_model: Optional[str] = None,
    piper_model: Optional[str] = None,
    stop_checker=None,
    auto_lang: bool = False,
    engine: Optional[TTSProvider] = None,
) -> bytes:
    """Synthesizes text into MP3 bytes using the requested provider and optionally writes to output_file.

    ``engine`` overrides provider construction (the daemon passes its cached
    instance so the model stays warm between requests, RNF-AT-04-5); by
    default a fresh provider is built per call, exactly as before.
    """
    if engine is None:
        engine = get_provider(
            provider_name=provider,
            openai_key=openai_key,
            openai_base_url=openai_base_url,
            openai_model=openai_model,
            eleven_key=eleven_key,
            eleven_model=eleven_model,
            piper_model=piper_model,
        )

    if auto_lang:
        from agent_tts.lang_detector import segment_by_language, resolve_voice_for_language
        from agent_tts.boundaries import BoundaryMap, Sentence, SynthesisResult, Word

        segments = segment_by_language(text, default_lang="es")
        detected_langs = {lang for lang, _ in segments}
        if len(detected_langs) > 1:
            chunks = []
            combined_sentences = []
            combined_words = []
            current_time_offset = 0.0
            global_sent_idx = 0
            global_word_idx = 0

            for lang, seg_text in segments:
                if stop_checker and stop_checker():
                    return SynthesisResult(b"", BoundaryMap())

                seg_voice = resolve_voice_for_language(voice, lang, provider=provider)
                seg_mp3 = await engine.synthesize(
                    text=seg_text,
                    voice=seg_voice,
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                    stop_checker=stop_checker,
                )
                if not seg_mp3:
                    continue

                chunks.append(bytes(seg_mp3))

                if hasattr(seg_mp3, "boundaries") and seg_mp3.boundaries.sentences:
                    bmap = seg_mp3.boundaries
                    seg_dur = max(s.end_sec for s in bmap.sentences)
                    for s in bmap.sentences:
                        combined_sentences.append(
                            Sentence(
                                index=global_sent_idx,
                                start_sec=s.start_sec + current_time_offset,
                                duration_sec=s.duration_sec,
                                text=s.text,
                            )
                        )
                        global_sent_idx += 1

                    for w in bmap.words:
                        combined_words.append(
                            Word(
                                index=global_word_idx,
                                sentence_index=w.sentence_index + (global_sent_idx - len(bmap.sentences)),
                                start_sec=w.start_sec + current_time_offset,
                                duration_sec=w.duration_sec,
                                text=w.text,
                            )
                        )
                        global_word_idx += 1

                    current_time_offset += seg_dur
                else:
                    try:
                        decoded = miniaudio.decode(bytes(seg_mp3))
                        seg_dur = (len(decoded.samples) // (decoded.nchannels * 2)) / float(decoded.sample_rate)
                        from agent_tts.boundaries import estimate_boundaries_from_text
                        est_bmap = estimate_boundaries_from_text(seg_text, seg_dur)
                        for s in est_bmap.sentences:
                            combined_sentences.append(
                                Sentence(
                                    index=global_sent_idx,
                                    start_sec=s.start_sec + current_time_offset,
                                    duration_sec=s.duration_sec,
                                    text=s.text,
                                )
                            )
                            global_sent_idx += 1
                        for w in est_bmap.words:
                            combined_words.append(
                                Word(
                                    index=global_word_idx,
                                    sentence_index=w.sentence_index + (global_sent_idx - len(est_bmap.sentences)),
                                    start_sec=w.start_sec + current_time_offset,
                                    duration_sec=w.duration_sec,
                                    text=w.text,
                                )
                            )
                            global_word_idx += 1
                        current_time_offset += seg_dur
                    except Exception:
                        pass

            combined_bytes = b"".join(chunks)
            if not combined_bytes:
                return SynthesisResult(b"", BoundaryMap())

            boundary_map = BoundaryMap(sentences=combined_sentences, words=combined_words)
            mp3_data = SynthesisResult(combined_bytes, boundary_map)

            if output_file:
                out_dir = os.path.dirname(os.path.abspath(output_file))
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(output_file, "wb") as f:
                    f.write(mp3_data)

            return mp3_data

    mp3_data = await engine.synthesize(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        stop_checker=stop_checker,
    )

    if not mp3_data:
        return b""

    if output_file:
        out_dir = os.path.dirname(os.path.abspath(output_file))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_file, "wb") as f:
            f.write(mp3_data)

    return mp3_data


STREAM_AUTO_PROVIDERS = ("edge", "openai", "elevenlabs", "eleven")
STREAM_AUTO_MIN_CHARS = 400
# Safety bound for the streaming producer: if it goes this long without
# finishing a new group (and is still alive), the pipeline aborts with an
# explicit error instead of hanging forever. Generous by design: a slow
# network synthesis run must never be cut by it in practice.
STREAM_PRODUCER_STALL_SEC = 300.0


def use_pipelined_stream(
    provider: str,
    stream: str,
    no_play: bool,
    output_file: Optional[str],
    podcast: bool,
    text_len: int,
) -> bool:
    """Decides whether playback should use pipelined sentence-group streaming.

    An ``output_file`` no longer refuses streaming: the pipeline plays
    normally and the merged audio is written to the file once playback
    completes — the file appears at the end of the run, not progressively.
    Podcast mode and ``no_play`` keep refusing (unchanged semantics).
    """
    if no_play or stream not in ("auto", "on") or podcast:
        return False
    if stream == "on":
        return True
    return provider in STREAM_AUTO_PROVIDERS and text_len >= STREAM_AUTO_MIN_CHARS


def group_for_chunk(groups: List[str], chunk_idx: int) -> Optional[str]:
    """Maps a stream chunk to its sentence group by index, or None beyond the known groups.

    The streaming provider and this orchestration split the text with the same
    shared splitter, so chunk order matches group order; a None result means
    the stream produced more chunks than groups (degraded boundaries).
    """
    if 0 <= chunk_idx < len(groups):
        return groups[chunk_idx]
    return None


def shift_boundary_map(
    bmap: BoundaryMap,
    offset_sec: float,
    sent_index_base: int,
    word_index_base: int,
    paragraph_index_base: int,
) -> BoundaryMap:
    """Returns a copy of a BoundaryMap with times offset and sentence/word/paragraph indices rebased."""
    sentences = [
        Sentence(
            index=s.index + sent_index_base,
            start_sec=s.start_sec + offset_sec,
            duration_sec=s.duration_sec,
            text=s.text,
            paragraph_index=s.paragraph_index + paragraph_index_base,
        )
        for s in bmap.sentences
    ]
    words = [
        Word(
            index=w.index + word_index_base,
            sentence_index=w.sentence_index + sent_index_base,
            start_sec=w.start_sec + offset_sec,
            duration_sec=w.duration_sec,
            text=w.text,
        )
        for w in bmap.words
    ]
    paragraphs = [
        Paragraph(
            index=p.index + paragraph_index_base,
            start_sec=p.start_sec + offset_sec,
            duration_sec=p.duration_sec,
            text=p.text,
            sentence_indices=[i + sent_index_base for i in p.sentence_indices],
        )
        for p in bmap.paragraphs
    ]
    return BoundaryMap(sentences=sentences, words=words, paragraphs=paragraphs)


async def _speak_pipelined(
    session: AudioSession,
    text: str,
    check_stop,
    voice: str,
    rate: str,
    volume: str,
    pitch: str,
    provider: str,
    openai_key: Optional[str],
    openai_base_url: Optional[str],
    openai_model: Optional[str],
    eleven_key: Optional[str],
    eleven_model: Optional[str],
    piper_model: Optional[str],
    auto_lang: bool,
    output_file: Optional[str] = None,
    podcast: bool = False,
    persist_name: Optional[str] = None,
    engine: Optional[TTSProvider] = None,
) -> None:
    """Plays the first synthesized sentence group while remaining groups are synthesized and appended live.

    Every successfully produced group/chunk is kept in ``rendered_chunks``;
    at a clean end of the pipeline the bytes are merged and persisted once
    (to ``output_file`` or the rendered-audio store). A stopped, interrupted,
    or failed run never persists: partial audio is worthless. ``engine``
    overrides per-group provider construction (daemon's warm cache).
    """
    groups = split_sentence_groups(text)
    # Raw bytes of every successfully produced group/chunk, in playback order.
    rendered_chunks: List[bytes] = []
    progress = {
        "offset": 0.0,
        "sent": 0,
        "word": 0,
        "para": 0,
        "produced": 0,
        "next_idx": 0,
    }

    def merge_group(group_text: str, result, seg_duration: float) -> None:
        """Shifts a group's boundaries by the accumulated offset and merges them into the session."""
        bmap = getattr(result, "boundaries", None)
        if bmap is None or not bmap.sentences:
            bmap = estimate_boundaries_from_text(group_text, seg_duration)
        shifted = shift_boundary_map(
            bmap,
            progress["offset"],
            progress["sent"],
            progress["word"],
            progress["para"],
        )
        with session.lock:
            session.boundaries.sentences.extend(shifted.sentences)
            session.boundaries.words.extend(shifted.words)
            session.boundaries.paragraphs.extend(shifted.paragraphs)
        progress["offset"] += seg_duration
        progress["sent"] += len(shifted.sentences)
        progress["word"] += len(shifted.words)
        progress["para"] += len(shifted.paragraphs)

    # Capability-driven streaming: the producer consumes synthesize_stream()
    # ONCE only when the engine's stream yields one chunk per sentence group
    # (one persistent piper process), so chunks map to groups by index.
    # Stream-capable engines whose chunks are raw fragments (openai,
    # elevenlabs) keep the per-group path and its aligned boundaries.
    if engine is None:
        try:
            engine = get_provider(
                provider_name=provider,
                openai_key=openai_key,
                openai_base_url=openai_base_url,
                openai_model=openai_model,
                eleven_key=eleven_key,
                eleven_model=eleven_model,
                piper_model=piper_model,
            )
        except Exception:
            engine = None  # the per-group path below surfaces the construction error as today

    # auto-lang switches voices per language segment, which a single text-level
    # stream cannot do: it keeps the per-group path.
    stream_gen = None
    if (
        not auto_lang
        and engine is not None
        and getattr(engine, "supports_stream", False)
        and getattr(engine, "stream_yields_group_chunks", False)
    ):
        stream_gen = engine.synthesize_stream(text, voice, rate, volume, pitch, stop_checker=check_stop)

    stream_state = {"failed": False, "warned_extra": False}

    def warn_extra_chunks() -> None:
        """Prints the extra-chunks warning once (degraded boundaries, never fatal)."""
        if not stream_state["warned_extra"]:
            stream_state["warned_extra"] = True
            print(
                "Stream: more chunks than sentence groups; appending extra audio without boundaries",
                file=sys.stderr,
            )

    def persist_rendered() -> None:
        """Persists the rendered audio exactly once, at a clean end of the pipeline.

        Fail-open by contract (playback already succeeded): merge or store
        errors print one English stderr line and are otherwise swallowed.
        Called only after the drain loop completed without a stop, interrupt,
        or fatal error — partial audio is never persisted.
        """
        if not rendered_chunks or check_stop():
            return
        try:
            from agent_tts.audio_store import merge_chunks_to_audio, retention_days, store_path

            try:
                merged = merge_chunks_to_audio(rendered_chunks)
            except Exception as e:
                print(
                    f"Stream: rendered audio could not be merged; skipping persistence: {e}",
                    file=sys.stderr,
                )
                return
            if output_file:
                out_dir = os.path.dirname(os.path.abspath(output_file))
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(output_file, "wb") as f:
                    f.write(merged)
                return
            if podcast or retention_days() == 0:
                return
            path = store_path(persist_name or "cli")
            with open(path, "wb") as f:
                f.write(merged)
            print(f"Stored: {path}", file=sys.stderr)
        except Exception as e:
            print(f"Stream: could not persist rendered audio: {e}", file=sys.stderr)

    async def pull_chunk(idx: int) -> Optional[bytes]:
        """Pulls the next stream chunk off the event loop; None at end of stream or on failure."""
        try:
            # The generator is blocking/sync: next() runs on a worker thread so
            # the producer loop (and its stall watchdog) stays responsive.
            return await asyncio.to_thread(next, stream_gen, None)
        except Exception as e:
            stream_state["failed"] = True
            print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
            return None

    async def stream_first():
        """Consumes stream chunks until one decodes; returns the first decoded segment."""
        idx = 0
        while not check_stop():
            chunk = await pull_chunk(idx)
            if chunk is None:
                return None
            group_text = group_for_chunk(groups, idx)
            if group_text is None:
                warn_extra_chunks()
            try:
                decoded = miniaudio.decode(chunk)
            except Exception as e:
                print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
                idx += 1
                continue
            rendered_chunks.append(chunk)
            if group_text is not None:
                seg_duration = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
                merge_group(group_text, chunk, seg_duration)
            progress["produced"] += 1
            progress["next_idx"] = idx + 1
            return decoded
        return None

    async def stream_remaining(start_idx: int) -> None:
        """Appends the remaining stream chunks live, merging boundaries by chunk index."""
        idx = start_idx
        try:
            while not check_stop():
                chunk = await pull_chunk(idx)
                if chunk is None:
                    break
                group_text = group_for_chunk(groups, idx)
                if group_text is None:
                    warn_extra_chunks()
                try:
                    decoded = miniaudio.decode(chunk)
                except Exception as e:
                    print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
                    idx += 1
                    continue
                rendered_chunks.append(chunk)
                if not session.append_pcm(decoded):
                    idx += 1
                    continue
                if group_text is not None:
                    seg_duration = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
                    merge_group(group_text, chunk, seg_duration)
                progress["produced"] += 1
                idx += 1
            if not stream_state["failed"] and not check_stop():
                missing = len(groups) - min(idx, len(groups))
                if missing > 0 and progress["produced"] > 0:
                    print(
                        f"Stream: {missing} of {len(groups)} sentence groups produced no chunk; "
                        "boundaries degraded",
                        file=sys.stderr,
                    )
        finally:
            # Close the sync generator so the persistent piper process (its
            # finally) shuts down even when the pipeline stops early.
            try:
                await asyncio.to_thread(stream_gen.close)
            except Exception:
                pass

    async def synthesize_group(group_text: str):
        return await synthesize(
            text=group_text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            provider=provider,
            openai_key=openai_key,
            openai_base_url=openai_base_url,
            openai_model=openai_model,
            eleven_key=eleven_key,
            eleven_model=eleven_model,
            piper_model=piper_model,
            stop_checker=check_stop,
            auto_lang=auto_lang,
            engine=engine,
        )

    async def produce_first():
        """Synthesizes groups in order until one decodable segment is ready; returns it decoded."""
        if stream_gen is not None:
            return await stream_first()
        for idx, group_text in enumerate(groups):
            if check_stop():
                return None
            try:
                result = await synthesize_group(group_text)
            except Exception as e:
                print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
                continue
            if not result:
                if check_stop():
                    return None
                print(f"Stream: group {idx} failed: empty audio", file=sys.stderr)
                continue
            try:
                decoded = miniaudio.decode(result)
            except Exception as e:
                print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
                continue
            rendered_chunks.append(bytes(result))
            seg_duration = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
            merge_group(group_text, result, seg_duration)
            progress["produced"] += 1
            progress["next_idx"] = idx + 1
            return decoded
        return None

    async def produce_remaining(start_idx: int) -> None:
        """Synthesizes the remaining groups and appends each decoded segment to the live buffer."""
        if stream_gen is not None:
            return await stream_remaining(start_idx)
        for idx in range(start_idx, len(groups)):
            if check_stop():
                break
            group_text = groups[idx]
            try:
                result = await synthesize_group(group_text)
            except Exception as e:
                print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
                continue
            if not result:
                if check_stop():
                    break
                print(f"Stream: group {idx} failed: empty audio", file=sys.stderr)
                continue
            try:
                decoded = miniaudio.decode(result)
            except Exception as e:
                print(f"Stream: group {idx} failed: {e}", file=sys.stderr)
                continue
            rendered_chunks.append(bytes(result))
            if not session.append_pcm(decoded):
                continue
            seg_duration = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
            merge_group(group_text, result, seg_duration)
            progress["produced"] += 1

    producer_error = []

    def run_producer(start_idx: int) -> None:
        """Runs the remaining production on a private event loop, then clears the producing flag.

        A fatal producer exception (e.g. the playback process died mid-write)
        is recorded so the drain wait can re-raise it on the main thread; the
        per-group failures above are already handled inline.
        """
        try:
            asyncio.run(produce_remaining(start_idx))
        except Exception as e:
            print(f"Stream: producer stopped: {e}", file=sys.stderr)
            producer_error.append(e)
        finally:
            with session.lock:
                session.state["producing"] = False

    with session.lock:
        session.state["producing"] = True

    try:
        first_decoded = await produce_first()
        if first_decoded is None:
            with session.lock:
                session.state["producing"] = False
            if progress["produced"] == 0:
                if check_stop():
                    return
                raise RuntimeError("Stream: synthesis produced no audio")
            return

        # Load the first segment into the playback buffer before the producer race can start;
        # play() detects the preloaded buffer and skips re-initialization.
        session.prepare_pcm(first_decoded)

        producer = threading.Thread(target=run_producer, args=(progress["next_idx"],), daemon=True)
        producer.start()
        try:
            session.play(first_decoded)
            # play() may return before every group has been produced and played
            # (the wsl-ps/remote sessions feed groups from the producer thread and
            # return immediately). Keep the pipeline alive until the producer has
            # exited or an IPC stop was requested, so the final clean drain covers
            # the whole text instead of truncating after the first groups.
            last_seen = progress["produced"]
            last_progress = time.monotonic()
            while producer.is_alive() and not check_stop():
                producer.join(timeout=0.1)
                if progress["produced"] != last_seen:
                    last_seen = progress["produced"]
                    last_progress = time.monotonic()
                elif time.monotonic() - last_progress > STREAM_PRODUCER_STALL_SEC:
                    raise RuntimeError(
                        f"Stream: producer made no progress for {STREAM_PRODUCER_STALL_SEC:.0f}s; "
                        "aborting stream playback"
                    )
            if producer_error and not check_stop():
                # Surface the fatal producer error through the normal error path
                # (stderr / exit non-zero) instead of silently dropping the tail.
                raise producer_error[0]
            # Clean end of the pipeline: playback drained without stop or error;
            # persist the rendered audio once (fail-open).
            persist_rendered()
        finally:
            with session.lock:
                session.state["producing"] = False
            producer.join(timeout=5.0)
    finally:
        if stream_gen is not None:
            # Safety net: the generator is normally exhausted (or closed by
            # stream_remaining); this also covers first-phase stops and errors
            # so the persistent piper process never outlives the pipeline.
            try:
                await asyncio.to_thread(stream_gen.close)
            except Exception:
                pass


def _build_playback_session(
    playback: str,
    label: str,
    auto_rewind_sec: float = 2.0,
    highlight: bool = False,
    autoscroll: bool = False,
    bionic: bool = False,
    zen: bool = False,
):
    """Builds the playback session for an already-resolved target.

    "local" plays through AudioSession (miniaudio); "wsl-ps" through one
    persistent PowershellSession; every other target through
    RemoteAudioSession. Callers own the session's lifecycle (IPC exposure
    and teardown): speak() for the classic in-process path, the daemon for
    the vía única.
    """
    if playback == "local":
        return AudioSession(
            label=label,
            auto_rewind_sec=auto_rewind_sec,
            highlight=highlight,
            autoscroll=autoscroll,
            bionic=bionic,
            zen=zen,
        )
    if playback == "wsl-ps" and not is_wsl_ps_available():
        print(
            "Error: --playback wsl-ps requires powershell.exe on PATH and a WSL environment "
            "(WSL_DISTRO_NAME set or /proc/version mentioning Microsoft)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if highlight or autoscroll or zen:
        print(
            f"Note: --playback {playback} streams audio to the Windows host; "
            "terminal highlight/zen/autoscroll views are not rendered remotely",
            file=sys.stderr,
        )
    if playback == "wsl-ps":
        # Zero-install WSL target: one persistent powershell.exe for
        # the whole run, fed length-prefixed WAV groups over stdin.
        return PowershellSession(
            label=label,
            auto_rewind_sec=auto_rewind_sec,
            highlight=highlight,
            autoscroll=autoscroll,
            bionic=bionic,
            zen=zen,
        )
    return RemoteAudioSession(
        label=label,
        auto_rewind_sec=auto_rewind_sec,
        highlight=highlight,
        autoscroll=autoscroll,
        bionic=bionic,
        zen=zen,
        target=playback,
    )


async def _play_speech(
    session,
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    volume: str = "+0%",
    pitch: str = "+0Hz",
    output_file: Optional[str] = None,
    no_play: bool = False,
    provider: str = "edge",
    openai_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    openai_model: Optional[str] = None,
    eleven_key: Optional[str] = None,
    eleven_model: Optional[str] = None,
    piper_model: Optional[str] = None,
    auto_lang: bool = False,
    podcast: bool = False,
    podcast_title: str = "",
    stream: str = "auto",
    persist_name: Optional[str] = None,
    engine: Optional[TTSProvider] = None,
) -> None:
    """Runs the synthesis + playback pipeline over a prepared session.

    No channel or session lifecycle here: the caller owns the ownership
    election, the IPC exposure, and the session teardown. ``session`` is
    None only in no-play mode (synthesis/podcast/output without audio).
    ``engine`` overrides provider construction (the daemon's warm cache).
    """
    # Pipelined streaming: playback starts after the first group while later groups synthesize.
    use_stream = use_pipelined_stream(
        provider=provider,
        stream=stream,
        no_play=no_play,
        output_file=output_file,
        podcast=podcast,
        text_len=len(text),
    )

    def check_stop():
        return session is not None and bool(session.state.get("stop", False))

    if use_stream:
        await _speak_pipelined(
            session=session,
            text=text,
            check_stop=check_stop,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            provider=provider,
            openai_key=openai_key,
            openai_base_url=openai_base_url,
            openai_model=openai_model,
            eleven_key=eleven_key,
            eleven_model=eleven_model,
            piper_model=piper_model,
            auto_lang=auto_lang,
            output_file=output_file,
            podcast=podcast,
            persist_name=persist_name,
            engine=engine,
        )
        return

    mp3_data = await synthesize(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        output_file=output_file,
        provider=provider,
        openai_key=openai_key,
        openai_base_url=openai_base_url,
        openai_model=openai_model,
        eleven_key=eleven_key,
        eleven_model=eleven_model,
        piper_model=piper_model,
        stop_checker=check_stop,
        auto_lang=auto_lang,
        engine=engine,
    )

    if not mp3_data or (session and session.state.get("stop")):
        return

    if podcast and mp3_data:
        from agent_tts.podcast import PodcastFeed
        feed = PodcastFeed()
        title = podcast_title or (text[:50] + "..." if len(text) > 50 else text)
        feed.add_episode(bytes(mp3_data), title=title, description=text)

    if hasattr(mp3_data, "boundaries") and mp3_data.boundaries and session:
        session.boundaries = mp3_data.boundaries

    if no_play:
        return

    decoded = miniaudio.decode(mp3_data)
    if session:
        if not session.boundaries.sentences:
            from agent_tts.boundaries import estimate_boundaries_from_text
            total_duration = getattr(decoded, "duration", None) or (
                len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
            )
            session.boundaries = estimate_boundaries_from_text(text, total_duration)
        session.play(decoded)


async def speak(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    volume: str = "+0%",
    pitch: str = "+0Hz",
    output_file: Optional[str] = None,
    no_play: bool = False,
    provider: str = "edge",
    openai_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    openai_model: Optional[str] = None,
    eleven_key: Optional[str] = None,
    eleven_model: Optional[str] = None,
    piper_model: Optional[str] = None,
    auto_rewind_sec: float = 2.0,
    highlight: bool = False,
    autoscroll: bool = False,
    bionic: bool = False,
    zen: bool = False,
    auto_lang: bool = False,
    podcast: bool = False,
    podcast_title: str = "",
    stream: str = "auto",
    playback: str = "local",
    persist_name: Optional[str] = None,
) -> None:
    """Synthesizes and plays audio with interactive controls."""
    session = None
    if not no_play:
        # Single lock/pid protocol shared with play_mp3_data: the ownership
        # election happens here, before any IPC server binds the channel.
        _write_player_locks()
        session = _build_playback_session(
            playback,
            f"{len(text)} chars",
            auto_rewind_sec=auto_rewind_sec,
            highlight=highlight,
            autoscroll=autoscroll,
            bionic=bionic,
            zen=zen,
        )
        session.start_ipc()

    try:
        await _play_speech(
            session,
            text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            output_file=output_file,
            no_play=no_play,
            provider=provider,
            openai_key=openai_key,
            openai_base_url=openai_base_url,
            openai_model=openai_model,
            eleven_key=eleven_key,
            eleven_model=eleven_model,
            piper_model=piper_model,
            auto_lang=auto_lang,
            podcast=podcast,
            podcast_title=podcast_title,
            stream=stream,
            persist_name=persist_name,
        )
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
        raise
    finally:
        if session:
            session.stop()
        if not no_play:
            cleanup_locks()


def main():
    # Retention sweep; fail-open (audio_store is imported lazily so `import
    # cli` never pays for it).
    try:
        from agent_tts.audio_store import prune_expired

        prune_expired()
    except Exception:
        pass

    # Voice manager subcommands (agent-tts voice list|install|remove) have
    # their own argument grammar (e.g. `voice list --json`), so they dispatch
    # BEFORE the synthesis parser; the store tooling lives in agent_tts.voices.
    if len(sys.argv) > 1 and sys.argv[1] == "voice":
        from agent_tts.voices import handle_voice_command

        sys.exit(handle_voice_command(sys.argv[2:] or ["list"]))

    parser = argparse.ArgumentParser(description="Agent Neural TTS Engine")
    parser.add_argument("text", nargs="*", help="Text to speak (reads stdin if omitted)")
    parser.add_argument("--voice", "-v", default=DEFAULT_VOICE, help="Voice (e.g. elvira, alvaro, nova, rachel)")
    parser.add_argument("--rate", "-r", default=DEFAULT_RATE, help="Speed: +20%%, +10%%, +0%%")
    parser.add_argument("--max-chars", "-m", type=int, default=0, help="Max characters to speak (0 for unlimited)")
    parser.add_argument("--raw", action="store_true", help="Do not clean text")
    parser.add_argument("--output", "-o", help="Save synthesized MP3 audio to file")
    parser.add_argument("--no-play", action="store_true", help="Do not play audio locally")
    parser.add_argument("--play-file", help="Play an existing MP3 file directly without re-synthesizing")
    parser.add_argument("--probe", help="Print the duration in seconds of an audio file and exit")
    parser.add_argument(
        "--highlight",
        "-H",
        action="store_true",
        help="Enable live word and sentence karaoke highlighting in terminal",
    )
    parser.add_argument(
        "--autoscroll",
        action="store_true",
        help="Enable synchronized auto-scroll reader view tracking spoken sentences",
    )
    parser.add_argument(
        "--bionic",
        action="store_true",
        help="Enable Bionic Reading (bold fixation on initial letters for rapid reading)",
    )
    parser.add_argument(
        "--zen",
        action="store_true",
        help="Enable Zen Mode (minimalist high-contrast distraction-free reader with auto-scroll)",
    )
    parser.add_argument("--next-sentence", action="store_true", help="Jump to next sentence in active playback")
    parser.add_argument("--prev-sentence", action="store_true", help="Jump to previous sentence in active playback")
    parser.add_argument("--current-sentence", action="store_true", help="Get current sentence text from active playback")
    parser.add_argument("--next-paragraph", action="store_true", help="Jump to next paragraph in active playback")
    parser.add_argument("--prev-paragraph", action="store_true", help="Jump to previous paragraph in active playback")
    parser.add_argument("--current-paragraph", action="store_true", help="Get current paragraph text from active playback")
    parser.add_argument("--scroll-info", action="store_true", help="Get current synchronized scroll status from active playback")
    parser.add_argument(
        "--tldr",
        "--summarize",
        dest="summarize",
        action="store_true",
        help="Condense long logs, diffs, or verbose output into a punchy spoken summary before playback",
    )
    parser.add_argument(
        "--llm-summary",
        action="store_true",
        help="Produce ONE executive sentence via a locally installed LLM CLI "
        "(claude -p, codex exec, ollama; model: AGENT_TTS_OLLAMA_MODEL); "
        "falls back to --tldr heuristics on any failure. Wins when combined with --tldr",
    )
    parser.add_argument(
        "--auto-lang",
        action="store_true",
        help="Automatically detect embedded language changes and switch neural voices on the fly",
    )
    parser.add_argument(
        "--podcast",
        action="store_true",
        help="Publish this audio session as an episode to the local private podcast RSS feed",
    )
    parser.add_argument(
        "--podcast-title",
        default="",
        help="Custom title for the podcast episode",
    )
    parser.add_argument(
        "--podcast-serve",
        nargs="?",
        const=8844,
        type=int,
        help="Run the local podcast HTTP server (default port: 8844)",
    )
    parser.add_argument(
        "--ipc-cmd",
        help="Send an IPC command to the active audio player (e.g. 'seek +10', 'seek -10', 'toggle-pause', 'status')",
    )
    parser.add_argument(
        "--ipc-json",
        action="store_true",
        help="With --ipc-cmd: print the reply as a single-line JSON object instead of the human string",
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("TTS_PROVIDER", "edge"),
        choices=["edge", "openai", "elevenlabs", "eleven", "piper", "kokoro", "local"],
        help="TTS provider backend (edge, openai, elevenlabs, piper, kokoro, local)",
    )
    parser.add_argument(
        "--stream",
        choices=["auto", "on", "off"],
        default="auto",
        help="Pipelined playback: synthesize sentence groups while playing (auto: edge/openai/elevenlabs, no podcast, >=400 chars); with --output the merged file is written after playback",
    )
    parser.add_argument(
        "--playback",
        choices=["local", "winhost", "wsl-ps", "windows", "auto"],
        default=os.environ.get("AGENT_TTS_PLAYBACK", "local"),
        help="Playback target: local device (default), winhost server on the Windows host, "
        "zero-install PowerShell under WSL, windows: Windows host with local-device fallback "
        "(never PowerShell), or auto: environment-based selection",
    )
    parser.add_argument(
        "--winhost",
        action="store_true",
        help="Run the Windows host audio server: receive PCM over TCP and play it natively via WASAPI",
    )
    parser.add_argument(
        "--winhost-host",
        default=None,
        help="Windows host address (client target, or server bind address with --winhost); default: auto-detect / AGENT_TTS_WINHOST_HOST or AGENT_TTS_WINHOST_BIND",
    )
    parser.add_argument(
        "--winhost-port",
        type=int,
        default=None,
        help="Windows host port for winhost playback (default: 7717 / AGENT_TTS_WINHOST_PORT)",
    )
    parser.add_argument("--openai-key", default=os.environ.get("OPENAI_API_KEY", ""), help="OpenAI API key")
    parser.add_argument("--openai-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), help="OpenAI custom base URL")
    parser.add_argument("--openai-model", default=os.environ.get("OPENAI_TTS_MODEL", "tts-1"), help="OpenAI TTS model (tts-1, tts-1-hd)")
    parser.add_argument("--eleven-key", default=os.environ.get("ELEVENLABS_API_KEY", ""), help="ElevenLabs API key")
    parser.add_argument("--eleven-model", default=os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"), help="ElevenLabs model")
    parser.add_argument(
        "--piper-model",
        default=os.environ.get("PIPER_MODEL", ""),
        help="Path to Piper ONNX model file (.onnx)",
    )
    parser.add_argument(
        "--pre-extracted",
        action="store_true",
        help="Treat input text as the final message (e.g. provided by an integration layer that already resolved the chat transcript); skips terminal-scrollback turn extraction, keeps markdown-to-speech cleaning",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent tool name for the transcript connector layer (e.g. opencode, claude); unknown names are sniffed from the session id shape",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Agent session id; resolves the last assistant message from the tool's structured transcript before falling back to the provided text",
    )

    args = parser.parse_args()

    # Flag overrides win over environment for the winhost transport settings.
    if args.winhost_host:
        os.environ["AGENT_TTS_WINHOST_HOST"] = args.winhost_host
    if args.winhost_port:
        os.environ["AGENT_TTS_WINHOST_PORT"] = str(args.winhost_port)

    if args.winhost:
        # Server mode: receive PCM on this (Windows) host and play it natively;
        # text/synthesis arguments are ignored.
        from agent_tts.winhost import run_winhost_server

        run_winhost_server(host=args.winhost_host, port=args.winhost_port)
        sys.exit(0)

    if args.podcast_serve:
        from agent_tts.podcast import run_podcast_server
        run_podcast_server(port=args.podcast_serve)
        sys.exit(0)

    ipc_cmd = args.ipc_cmd
    if args.next_sentence:
        ipc_cmd = "next-sentence"
    elif args.prev_sentence:
        ipc_cmd = "prev-sentence"
    elif args.current_sentence:
        ipc_cmd = "sentence"
    elif args.next_paragraph:
        ipc_cmd = "next-paragraph"
    elif args.prev_paragraph:
        ipc_cmd = "prev-paragraph"
    elif args.current_paragraph:
        ipc_cmd = "paragraph"
    elif args.scroll_info:
        ipc_cmd = "scroll-info"

    if ipc_cmd:
        res = send_ipc_command(ipc_cmd)
        if res is not None:
            if args.ipc_json:
                from agent_tts.ipc import ipc_reply_json

                print(ipc_reply_json(res))
            else:
                print(res)
            sys.exit(0)
        else:
            print("Error: No active audio playback session found", file=sys.stderr)
            sys.exit(1)

    if args.probe:
        # Host-facing utility mode: duration of one audio file as a plain
        # float (one line), so bash hosts need no Python of their own.
        from agent_tts.audio_store import audio_duration

        try:
            duration = audio_duration(args.probe)
        except Exception as e:
            print(f"Error: cannot probe {args.probe}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"{duration:.3f}")
        sys.exit(0)

    if args.play_file:
        play_mp3_file(
            args.play_file,
            label=os.path.basename(args.play_file),
            highlight=args.highlight,
            autoscroll=args.autoscroll,
            bionic=args.bionic,
            zen=args.zen,
        )
        sys.exit(0)

    input_text = ""
    if args.text:
        input_text = " ".join(args.text).strip()
    elif not sys.stdin.isatty():
        input_text = sys.stdin.read().strip()

    if not input_text:
        sys.exit(0)

    # Agent Connectors (Vision B): with an agent identity + session id, the
    # engine itself resolves the last assistant message from the tool's
    # structured transcript; the provided text remains the scrollback fallback.
    source_result = None
    if args.session_id:
        source_result = read_last_agent_message(args.agent, args.session_id)
        if source_result:
            print(
                f"Sources: last message via {source_result.source} ({len(source_result.text)} chars)",
                file=sys.stderr,
            )
        else:
            print(
                f"Sources: no transcript for {args.agent or 'auto'}/{args.session_id}, falling back to scrollback",
                file=sys.stderr,
            )

    if source_result:
        speech_text = (
            source_result.text
            if args.raw
            else clean_agent_text(
                source_result.text,
                max_chars=args.max_chars,
                summarize=args.summarize or args.llm_summary,
                llm_summary=args.llm_summary,
                pre_extracted=True,
            )
        )
    else:
        # Host integrations may hand over text that is already
        # the exact message to speak; scrollback turn extraction is then skipped
        # while message cleaning (markdown-to-speech, tables, lexicon) still runs.
        speech_text = input_text if args.raw else clean_agent_text(
            input_text,
            max_chars=args.max_chars,
            summarize=args.summarize or args.llm_summary,
            llm_summary=args.llm_summary,
            pre_extracted=args.pre_extracted,
        )
    if not speech_text:
        sys.exit(0)

    try:
        asyncio.run(
            speak(
                text=speech_text,
                voice=args.voice,
                rate=args.rate,
                output_file=args.output,
                no_play=args.no_play,
                provider=args.provider,
                openai_key=args.openai_key,
                openai_base_url=args.openai_base_url,
                openai_model=args.openai_model,
                eleven_key=args.eleven_key,
                eleven_model=args.eleven_model,
                piper_model=args.piper_model,
                highlight=args.highlight,
                autoscroll=args.autoscroll,
                bionic=args.bionic,
                zen=args.zen,
                auto_lang=args.auto_lang,
                podcast=args.podcast,
                podcast_title=args.podcast_title,
                stream=args.stream,
                playback=resolve_target(args.playback, os.environ),
                persist_name=args.agent or args.session_id or "cli",
            )
        )
    except KeyboardInterrupt:
        cleanup_locks()
    except Exception:
        cleanup_locks()
        sys.exit(1)


if __name__ == "__main__":
    main()
