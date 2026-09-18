"""Command-line interface and high-level synthesis helpers for agent-tts."""

import argparse
import asyncio
import os
import signal
import sys
from typing import Optional

from agent_tts.audio import AudioSession, cleanup_locks, play_mp3_file
from agent_tts.cleaner import clean_agent_text
from agent_tts.constants import DEFAULT_RATE, DEFAULT_VOICE, LOCK_FILE, PID_FILE
from agent_tts.ipc import send_ipc_command
from agent_tts.providers import get_provider
import miniaudio


def signal_handler(signum, frame):
    cleanup_locks()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


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
    auto_lang: bool = False,
    podcast: bool = False,
    podcast_title: str = "",
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
        session = AudioSession(
            label=f"{len(text)} chars",
            auto_rewind_sec=auto_rewind_sec,
            highlight=highlight,
        )
        session.start_ipc()

    try:
        def check_stop():
            return session is not None and bool(session.state.get("stop", False))

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
                frame_size = session.nchannels * session.bytes_per_sample if session.nchannels else 4
                total_duration = (len(decoded.samples) // frame_size) / float(decoded.sample_rate)
                session.boundaries = estimate_boundaries_from_text(text, total_duration)
            session.play(decoded)
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
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
    parser.add_argument("--highlight", action="store_true", help="Enable live word and sentence highlighting in terminal")
    parser.add_argument("--next-sentence", action="store_true", help="Jump to next sentence in active playback")
    parser.add_argument("--prev-sentence", action="store_true", help="Jump to previous sentence in active playback")
    parser.add_argument("--current-sentence", action="store_true", help="Get current sentence text from active playback")
    parser.add_argument("--next-paragraph", action="store_true", help="Jump to next paragraph in active playback")
    parser.add_argument("--prev-paragraph", action="store_true", help="Jump to previous paragraph in active playback")
    parser.add_argument("--current-paragraph", action="store_true", help="Get current paragraph text from active playback")
    parser.add_argument(
        "--tldr",
        "--summarize",
        dest="summarize",
        action="store_true",
        help="Condense long logs, diffs, or verbose output into a punchy spoken summary before playback",
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
        choices=["edge", "openai", "elevenlabs", "eleven", "piper", "local"],
        help="TTS provider backend (edge, openai, elevenlabs, piper, local)",
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

    args = parser.parse_args()

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

    if ipc_cmd:
        res = send_ipc_command(ipc_cmd)
        if res is not None:
            print(res)
            sys.exit(0)
        else:
            print("Error: No active audio playback session found", file=sys.stderr)
            sys.exit(1)

    if args.play_file:
        play_mp3_file(args.play_file, label=os.path.basename(args.play_file), highlight=args.highlight)
        sys.exit(0)

    input_text = ""
    if args.text:
        input_text = " ".join(args.text).strip()
    elif not sys.stdin.isatty():
        input_text = sys.stdin.read().strip()

    if not input_text:
        sys.exit(0)

    speech_text = input_text if args.raw else clean_agent_text(input_text, max_chars=args.max_chars, summarize=args.summarize)
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
                auto_lang=args.auto_lang,
                podcast=args.podcast,
                podcast_title=args.podcast_title,
            )
        )
    except KeyboardInterrupt:
        cleanup_locks()


if __name__ == "__main__":
    main()
