"""Command-line interface and high-level synthesis helpers for agent-tts."""

import argparse
import asyncio
import os
import re
import signal
import sys
import threading
from typing import List, Optional

from agent_tts.audio import AudioSession, cleanup_locks, play_mp3_file
from agent_tts.boundaries import (
    BoundaryMap,
    Paragraph,
    Sentence,
    Word,
    estimate_boundaries_from_text,
)
from agent_tts.cleaner import clean_agent_text
from agent_tts.constants import DEFAULT_RATE, DEFAULT_VOICE, LOCK_FILE, PID_FILE
from agent_tts.ipc import send_ipc_command
from agent_tts.playback_target import resolve_target
from agent_tts.powershell_playback import PowershellSession, is_wsl_ps_available
from agent_tts.providers import get_provider
from agent_tts.sources import read_last_agent_message
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
) -> bytes:
    """Synthesizes text into MP3 bytes using the requested provider and optionally writes to output_file."""
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


def use_pipelined_stream(
    provider: str,
    stream: str,
    no_play: bool,
    output_file: Optional[str],
    podcast: bool,
    text_len: int,
) -> bool:
    """Decides whether playback should use pipelined sentence-group streaming."""
    if no_play or stream not in ("auto", "on") or output_file or podcast:
        return False
    if stream == "on":
        return True
    return provider in STREAM_AUTO_PROVIDERS and text_len >= STREAM_AUTO_MIN_CHARS


def split_sentence_groups(text: str, max_chars: int = 250) -> List[str]:
    """Splits text into greedy sentence groups of at most max_chars characters for pipelined synthesis."""
    if not text or not text.strip():
        return []

    raw_sentences: List[str] = []
    for paragraph in text.strip().splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        raw_sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip())
    if not raw_sentences:
        raw_sentences = [text.strip()]

    # Very long single sentences may be split on commas when they exceed twice the budget.
    sentences: List[str] = []
    for sent in raw_sentences:
        if len(sent) > max_chars * 2:
            pieces = [p.strip() for p in sent.split(", ") if p.strip()]
            sentences.extend(pieces if pieces else [sent])
        else:
            sentences.append(sent)

    groups: List[str] = []
    current = ""
    for sent in sentences:
        if current and len(current) + 1 + len(sent) <= max_chars:
            current = f"{current} {sent}"
        else:
            if current:
                groups.append(current)
            current = sent
    if current:
        groups.append(current)
    return groups


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
) -> None:
    """Plays the first synthesized sentence group while remaining groups are synthesized and appended live."""
    groups = split_sentence_groups(text)
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
        )

    async def produce_first():
        """Synthesizes groups in order until one decodable segment is ready; returns it decoded."""
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
            seg_duration = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
            merge_group(group_text, result, seg_duration)
            progress["produced"] += 1
            progress["next_idx"] = idx + 1
            return decoded
        return None

    async def produce_remaining(start_idx: int) -> None:
        """Synthesizes the remaining groups and appends each decoded segment to the live buffer."""
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
            if not session.append_pcm(decoded):
                continue
            seg_duration = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
            merge_group(group_text, result, seg_duration)
            progress["produced"] += 1

    def run_producer(start_idx: int) -> None:
        """Runs the remaining production on a private event loop, then clears the producing flag."""
        try:
            asyncio.run(produce_remaining(start_idx))
        except Exception as e:
            print(f"Stream: producer stopped: {e}", file=sys.stderr)
        finally:
            with session.lock:
                session.state["producing"] = False

    with session.lock:
        session.state["producing"] = True

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
    finally:
        with session.lock:
            session.state["producing"] = False
        producer.join(timeout=5.0)


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
) -> None:
    """Synthesizes and plays audio with interactive controls."""
    session = None
    if not no_play:
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
            with open(LOCK_FILE, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
        if playback == "local":
            session = AudioSession(
                label=f"{len(text)} chars",
                auto_rewind_sec=auto_rewind_sec,
                highlight=highlight,
                autoscroll=autoscroll,
                bionic=bionic,
                zen=zen,
            )
        else:
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
                session = PowershellSession(
                    label=f"{len(text)} chars",
                    auto_rewind_sec=auto_rewind_sec,
                    highlight=highlight,
                    autoscroll=autoscroll,
                    bionic=bionic,
                    zen=zen,
                )
            else:
                session = RemoteAudioSession(
                    label=f"{len(text)} chars",
                    auto_rewind_sec=auto_rewind_sec,
                    highlight=highlight,
                    autoscroll=autoscroll,
                    bionic=bionic,
                    zen=zen,
                    target=playback,
                )
        session.start_ipc()

    # Pipelined streaming: playback starts after the first group while later groups synthesize.
    use_stream = use_pipelined_stream(
        provider=provider,
        stream=stream,
        no_play=no_play,
        output_file=output_file,
        podcast=podcast,
        text_len=len(text),
    )

    try:
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
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
        raise
    finally:
        if session:
            session.stop()
        if not no_play:
            cleanup_locks()


def main():
    parser = argparse.ArgumentParser(description="Agent Neural TTS Engine")
    parser.add_argument("text", nargs="*", help="Text to speak (reads stdin if omitted)")
    parser.add_argument("--voice", "-v", default=DEFAULT_VOICE, help="Voice (e.g. elvira, alvaro, nova, rachel)")
    parser.add_argument("--rate", "-r", default=DEFAULT_RATE, help="Speed: +20%%, +10%%, +0%%")
    parser.add_argument("--max-chars", "-m", type=int, default=0, help="Max characters to speak (0 for unlimited)")
    parser.add_argument("--raw", action="store_true", help="Do not clean text")
    parser.add_argument("--output", "-o", help="Save synthesized MP3 audio to file")
    parser.add_argument("--no-play", action="store_true", help="Do not play audio locally")
    parser.add_argument("--play-file", help="Play an existing MP3 file directly without re-synthesizing")
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
        "--provider",
        default=os.environ.get("TTS_PROVIDER", "edge"),
        choices=["edge", "openai", "elevenlabs", "eleven", "piper", "kokoro", "local"],
        help="TTS provider backend (edge, openai, elevenlabs, piper, kokoro, local)",
    )
    parser.add_argument(
        "--stream",
        choices=["auto", "on", "off"],
        default="auto",
        help="Pipelined playback: synthesize sentence groups while playing (auto: edge/openai/elevenlabs, no output/podcast, >=400 chars)",
    )
    parser.add_argument(
        "--playback",
        choices=["local", "winhost", "wsl-ps"],
        default=os.environ.get("AGENT_TTS_PLAYBACK", "local"),
        help="Playback target: local device (default), winhost server on the Windows host, or zero-install PowerShell under WSL",
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

    # Voice manager subcommands (agent-tts voice list|install|remove) dispatch
    # before normal synthesis; the store tooling lives in agent_tts.voices.
    if args.text and args.text[0] == "voice" and len(args.text) > 1:
        from agent_tts.voices import handle_voice_command

        sys.exit(handle_voice_command(args.text[1:]))
    if args.text == ["voice"]:
        from agent_tts.voices import handle_voice_command

        sys.exit(handle_voice_command(["list"]))

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
            print(res)
            sys.exit(0)
        else:
            print("Error: No active audio playback session found", file=sys.stderr)
            sys.exit(1)

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
        # Host integrations (e.g. herdr-tts) may hand over text that is already
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
            )
        )
    except KeyboardInterrupt:
        cleanup_locks()
    except Exception:
        cleanup_locks()
        sys.exit(1)


if __name__ == "__main__":
    main()
