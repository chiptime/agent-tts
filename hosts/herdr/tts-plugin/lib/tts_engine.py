#!/usr/bin/env python3
"""
tts_engine.py — Cross-platform Neural TTS engine for Herdr agent feedback.
Supports Linux (PulseAudio / PipeWire / WSLg), macOS (afplay), and generic players.
"""

import argparse
import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Optional

try:
    import edge_tts
    import miniaudio
except ImportError:
    print("Error: Missing edge_tts or miniaudio. Run: pip install edge-tts miniaudio", file=sys.stderr)
    sys.exit(1)

VOICE_MAP = {
    "elvira": "es-ES-ElviraNeural",
    "alvaro": "es-ES-AlvaroNeural",
    "álvaro": "es-ES-AlvaroNeural",
    "ximena": "es-ES-XimenaNeural",
    "dalia": "es-MX-DaliaNeural",
    "jorge": "es-MX-JorgeNeural",
    "en": "en-US-JennyNeural",
}

DEFAULT_VOICE = "es-ES-ElviraNeural"
LOCK_FILE = "/tmp/herdr-tts-playing.lock"
PID_FILE = "/tmp/herdr-tts-current.pid"

_active_process: Optional[subprocess.Popen] = None


def cleanup_locks():
    for f in (LOCK_FILE, PID_FILE):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


def signal_handler(signum, frame):
    global _active_process
    if _active_process and _active_process.poll() is None:
        try:
            _active_process.terminate()
            _active_process.wait(timeout=0.5)
        except Exception:
            _active_process.kill()
    cleanup_locks()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def extract_last_turn(raw_text: str) -> str:
    """Isolates the most recent completed assistant response from a terminal scrollback."""
    if not raw_text:
        return ""
    lines = raw_text.splitlines()

    # 1. Clean end lines (status bar, empty prompt, dividers, spinners)
    cleaned_end = []
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Ignore terminal status chrome
        if re.search(
            r"(Tokens:\s*\d+|Quotas:\s*\[|Gemini\s*\d|Claude\s*\d|GPT-?\d|\d+(\.\d+)?[kM]?\s*in\s*\|\s*\d+(\.\d+)?[kM]?\s*out)",
            stripped,
            re.IGNORECASE,
        ):
            continue
        # Ignore empty prompt lines: ">", "?", "❯", "%", "$"
        if re.match(r"^(>|\?|❯|%|\$)\s*$", stripped):
            continue
        # Ignore divider rules
        if re.match(r"^[─━│┃═\-_*#=]{3,}\s*$", stripped):
            continue
        # Ignore Braille spinners and in-flight indicators (●, ○, ⣾, ⣟, ⠋, ⢿, etc.)
        if re.search(r"^[\u2800-\u28FF●○]", stripped):
            continue
        if re.match(r"^└\s*Tip:", stripped):
            continue
        if "Conversation compacted" in stripped:
            continue

        cleaned_end.append(line)
        if len(cleaned_end) > 300:
            break

    if not cleaned_end:
        return ""

    cleaned_lines = list(reversed(cleaned_end))

    # 2. Find prompt ranges (start_idx to end_of_prompt_idx)
    # A prompt starts with > or ❯ and may continue across indented lines (spaces)
    prompts = []
    i = 0
    while i < len(cleaned_lines):
        stripped = cleaned_lines[i].strip()
        if re.match(r"^(>|❯)\s+[A-Za-z0-9¿¡\/\.]+", stripped):
            p_start = i
            p_end = i
            while p_end + 1 < len(cleaned_lines):
                next_line = cleaned_lines[p_end + 1]
                next_stripped = next_line.strip()
                if (
                    next_line.startswith("  ")
                    and not re.search(r"^[\u2800-\u28FF●○─━│┃═\-_*#=]", next_stripped)
                    and not re.match(r"^(>|❯|\?)\s+", next_stripped)
                ):
                    p_end += 1
                else:
                    break
            prompts.append((p_start, p_end))
            i = p_end + 1
        else:
            i += 1

    # Helper to check if a slice of lines has substantive assistant content
    def get_substantive_text(lines_slice):
        clean_slice = []
        for l in lines_slice:
            s = l.strip()
            if not s:
                continue
            if re.search(r"^[\u2800-\u28FF●○]", s):
                continue
            if re.search(
                r"(Tokens:\s*\d+|Quotas:\s*\[|Gemini\s*\d|Claude\s*\d|GPT-?\d|\d+(\.\d+)?[kM]?\s*in\s*\|\s*\d+(\.\d+)?[kM]?\s*out)",
                s,
                re.IGNORECASE,
            ):
                continue
            if re.match(r"^[─━│┃═\-_*#=]{3,}\s*$", s) or "Conversation compacted" in s:
                continue
            if re.search(
                r"(Running command|thinking through|ctrl\+o to expand|Exited /artifact|Press esc to interrupt)",
                s,
                re.IGNORECASE,
            ):
                continue
            if re.match(r"^└\s*Tip:", s):
                continue
            clean_slice.append(l)

        body = "\n".join(clean_slice).strip()
        # Strip ANSI codes
        body = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", body)
        # Strip divider box drawing characters
        body = re.sub(r"[─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬┄┅┆┇┈┉┊┋┌┐└┘╭╮╰╯]+", " ", body)
        # Normalize whitespace
        body = re.sub(r"\s+", " ", body).strip()
        return body

    # 3. Search backwards from newest to oldest prompt for substantive assistant response
    for idx in range(len(prompts) - 1, -1, -1):
        _, p_end = prompts[idx]
        next_p_start = prompts[idx + 1][0] if idx + 1 < len(prompts) else len(cleaned_lines)
        sub_lines = cleaned_lines[p_end + 1 : next_p_start]
        body = get_substantive_text(sub_lines)
        # If this turn has at least 30 chars of substantive assistant text, that is our response
        if len(body) >= 30:
            return "\n".join(sub_lines)

    return "\n".join(cleaned_lines)


def clean_agent_text(raw_text: str, max_chars: int = 0) -> str:
    """Cleans terminal output and markdown formatting for natural voice reading."""
    if not raw_text:
        return ""

    # 0. Isolate the latest completed turn if multiple turns exist in the buffer
    text = extract_last_turn(raw_text)

    # Strip ANSI escape codes
    text = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)

    # Strip HTML/XML tags (e.g. <style>, <TextArea>, <div>) so Edge TTS SSML is not broken
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove box drawing characters and dividers
    text = re.sub(r"[─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬┄┅┆┇┈┉┊┋┌┐└┘╭╮╰╯]+", " ", text)
    text = re.sub(r"^[=\-_*#]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Remove markdown header markers: "### Title" -> "Title"
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"#{1,6}\s+", " ", text)

    # Remove bullet markers: "• Item" or "* Item" -> "Item"
    text = re.sub(r"^\s*[•*+-]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+[•*+-]\s+", " ", text)

    # Remove common terminal status lines / chrome / spinners
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"^[\u2800-\u28FF●○]", stripped):
            continue
        if re.search(r"Tokens:\s*\d+|Quotas:\s*\[|Gemini\s*\d|Claude\s*\d|GPT-?\d", stripped, re.IGNORECASE):
            continue
        if re.search(r"^\d+(\.\d+)?[kM]?\s*in\s*\|\s*\d+(\.\d+)?[kM]?\s*out", stripped):
            continue
        if re.match(r"^>\s*$", stripped) or re.match(r"^\?\s*$", stripped):
            continue
        if "Conversation compacted" in stripped:
            continue
        if re.search(r"(Running command|thinking through|ctrl\+o to expand|Exited /artifact)", stripped, re.IGNORECASE):
            continue
        lines.append(stripped)

    text = "\n".join(lines)

    # Replace fenced code blocks with audio cue
    text = re.sub(r"```[a-zA-Z0-9_-]*\n[\s\S]*?```", " [bloque de código] ", text)
    text = re.sub(r"```[\s\S]*?```", " [bloque de código] ", text)

    # Replace inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Replace markdown links: [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Simplify URLs
    text = re.sub(r"https?://\S+", " enlace ", text)

    # Strip bold / italics markdown
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)

    # Normalize multiple whitespace and newlines
    text = re.sub(r"\s+", " ", text).strip()

    # Apply length cap only if max_chars is specified and positive
    if max_chars > 0 and len(text) > max_chars:
        truncated = text[:max_chars]
        last_period = max(
            truncated.rfind(". "),
            truncated.rfind("! "),
            truncated.rfind("? "),
            truncated.rfind("; "),
        )
        if last_period > int(max_chars * 0.75):
            text = truncated[: last_period + 1] + " ... y más contenido."
        else:
            text = truncated.rstrip() + " ... y más contenido."

    return text


def spawn_player(wav_path: str) -> subprocess.Popen:
    """Spawns the best available audio player depending on OS and sound server."""
    # 1. macOS native
    if sys.platform == "darwin" and shutil.which("afplay"):
        return subprocess.Popen(["afplay", wav_path])

    # 2. Linux native (PulseAudio / PipeWire / WSLg)
    if shutil.which("paplay"):
        env = os.environ.copy()
        # WSLg specific server path check (if not already set in environment)
        if "PULSE_SERVER" not in env and os.path.exists("/mnt/wslg/PulseServer"):
            env["PULSE_SERVER"] = "unix:/mnt/wslg/PulseServer"
        return subprocess.Popen(["paplay", wav_path], env=env)

    # 3. Fallback to mpv
    if shutil.which("mpv"):
        return subprocess.Popen(["mpv", "--no-video", "--really-quiet", wav_path])

    # 4. Fallback to ffplay
    if shutil.which("ffplay"):
        return subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav_path])

    # 5. Fallback to aplay (raw ALSA)
    if shutil.which("aplay"):
        return subprocess.Popen(["aplay", "-q", wav_path])

    raise RuntimeError("No suitable audio player found (tried afplay, paplay, mpv, ffplay, aplay)")


async def synthesize_and_play(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+20%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> None:
    """Synthesizes text with edge-tts, decodes to WAV in memory, and plays via audio backend."""
    global _active_process
    resolved_voice = VOICE_MAP.get(voice.lower().strip(), voice)

    # Write PID and lock
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    temp_wav = None
    try:
        communicate = edge_tts.Communicate(
            text=text,
            voice=resolved_voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )

        mp3_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])

        if not mp3_chunks:
            return

        mp3_data = b"".join(mp3_chunks)

        # Decode MP3 to PCM using miniaudio (in-memory C decoder)
        decoded = miniaudio.decode(mp3_data)

        # Write temporary WAV file for player
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_wav = f.name

        miniaudio.wav_write_file(temp_wav, decoded)

        # Show Herdr toast notification that audio playback has started
        try:
            duration_s = int(decoded.num_frames / decoded.sample_rate)
            min_s = duration_s // 60
            sec_s = duration_s % 60
            dur_label = f"{min_s}:{sec_s:02d}" if min_s > 0 else f"{sec_s}s"
            subprocess.run(
                [
                    "herdr",
                    "notification",
                    "show",
                    "🔊 Reproduciendo voz",
                    "--body",
                    f"Duración: {dur_label} ({len(text)} caracteres)",
                    "--sound",
                    "none",
                ],
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
        except Exception:
            pass

        _active_process = spawn_player(temp_wav)
        _active_process.wait()
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
    finally:
        _active_process = None
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except OSError:
                pass
        cleanup_locks()


def main():
    parser = argparse.ArgumentParser(description="Herdr Neural TTS Engine")
    parser.add_argument("text", nargs="*", help="Text to speak (reads stdin if omitted)")
    parser.add_argument("--voice", "-v", default="elvira", help="Voice (elvira, alvaro, ximena, dalia, jorge)")
    parser.add_argument("--rate", "-r", default="+20%", help="Speed: +20%%, +10%%, +0%%")
    parser.add_argument("--max-chars", "-m", type=int, default=0, help="Max characters to speak (0 for unlimited)")
    parser.add_argument("--raw", action="store_true", help="Do not clean text")

    args = parser.parse_args()

    input_text = ""
    if args.text:
        input_text = " ".join(args.text).strip()
    elif not sys.stdin.isatty():
        input_text = sys.stdin.read().strip()

    if not input_text:
        sys.exit(0)

    speech_text = input_text if args.raw else clean_agent_text(input_text, max_chars=args.max_chars)
    if not speech_text:
        sys.exit(0)

    try:
        asyncio.run(
            synthesize_and_play(
                text=speech_text,
                voice=args.voice,
                rate=args.rate,
            )
        )
    except KeyboardInterrupt:
        cleanup_locks()


if __name__ == "__main__":
    main()
