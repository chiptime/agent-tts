#!/usr/bin/env python3
"""
tts_engine.py — Cross-platform Neural TTS engine for Herdr agent feedback.
Supports Linux (PulseAudio / PipeWire / WSLg), macOS (afplay), and generic players.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
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
IPC_SOCKET = "/tmp/herdr-tts-player.sock"

_active_process: Optional[subprocess.Popen] = None
_current_playback_state: Optional[dict] = None


def cleanup_locks():
    for f in (LOCK_FILE, PID_FILE, IPC_SOCKET):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


def signal_handler(signum, frame):
    global _active_process, _current_playback_state
    if _current_playback_state:
        _current_playback_state["stop"] = True
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


def is_user_prompt(line: str) -> bool:
    """Detects whether a line is a user prompt marker across Antigravity CLI, Claude, and OpenCode."""
    # Antigravity CLI, Claude Code, Gemini CLI: starts at column 0 with >, ❯, or ?
    if re.match(r"^(>|❯|\?)\s+\S+", line):
        return True
    # OpenCode prompt line: indented with ┃ followed by user text
    if re.match(r"^\s*┃\s+[A-Za-z0-9¿¡\/\.\"\']+", line) and not re.search(
        r"(opencode-|Gentle-|Thought:)", line
    ):
        return True
    return False


def extract_last_turn(raw_text: str) -> str:
    """Isolates the most recent completed assistant response from a terminal scrollback."""
    if not raw_text or not raw_text.strip():
        return ""
    raw_lines = raw_text.splitlines()

    # 1. Identify all prompt locations in the raw scrollback
    prompts = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if is_user_prompt(line):
            p_start = i
            p_end = i
            is_opencode = "┃" in line

            # Check wrapped prompt continuation lines
            while p_end + 1 < len(raw_lines) and (p_end - p_start) < 8:
                next_l = raw_lines[p_end + 1]
                s = next_l.strip()
                # Blank lines strictly terminate the user prompt
                if not s:
                    break
                # Tool indicators or spinners strictly terminate the user prompt
                if re.search(r"^[\u2800-\u28FF●○⏺]", s):
                    break
                # Status / token lines strictly terminate the user prompt
                if re.search(
                    r"(\d+(\.\d+)?[kM]?\s*in\s*\|\s*\d+(\.\d+)?[kM]?\s*out|Tokens:\s*\d+|Quotas:\s*\[)",
                    s,
                    re.IGNORECASE,
                ):
                    break
                # Divider rules strictly terminate the user prompt
                if re.match(r"^[─━│┃═\-_*#=┼]{3,}\s*$", s):
                    break
                # A new user prompt line begins
                if is_user_prompt(next_l):
                    break

                if is_opencode:
                    if re.match(r"^\s*┃\s+\S+", next_l) and not re.search(
                        r"(opencode-|Gentle-|Thought:)", next_l
                    ):
                        p_end += 1
                    else:
                        break
                else:
                    # Antigravity/standard prompt continuation: indented, but NOT markdown headers, bullets, or tables
                    if (
                        next_l.startswith("  ")
                        and not re.search(
                            r"^(#{1,6}|[•*+-]|\d+\.|📄|🗺️|│|─|ℹ️|⚠️|❌|PASS|FAIL)",
                            s,
                        )
                        and not re.match(r"^[A-Z][a-z]+:", s)
                    ):
                        p_end += 1
                    else:
                        break

            prompts.append((p_start, p_end))
            i = p_end + 1
        else:
            i += 1

    def filter_assistant_lines(lines_slice):
        clean = []
        for l in lines_slice:
            # Strip OpenCode right-side status columns if present
            cleaned_line = re.sub(
                r"\s{6,}(?:󰚩|▼|●\s*\d|✕|Context|\$[\d,.]+|\d[\d,.]*\s*tokens|\d+%\s*used|•|LSP|Connection closed|SSE error).*$",
                "",
                l,
            )
            s = cleaned_line.strip()
            if not s:
                continue
            # Ignore spinners, tool calls, in-flight markers
            if re.search(r"^[\u2800-\u28FF●○⏺]", s):
                continue
            if re.search(
                r"(\d+(\.\d+)?[kM]?\s*in\s*\|\s*\d+(\.\d+)?[kM]?\s*out|Tokens:\s*\d+|Quotas:\s*\[|Gemini\s*\d|Claude\s*\d|GPT-?\d)",
                s,
                re.IGNORECASE,
            ):
                continue
            if re.match(r"^[─━│┃═\-_*#=┼]{3,}\s*$", s) or "Conversation compacted" in s:
                continue
            if re.search(
                r"(Running command|thinking through|ctrl\+o|expand\)|Exited /artifact|Press esc to interrupt)",
                s,
                re.IGNORECASE,
            ):
                continue
            if re.match(r"^(>|\?|❯|%|\$)\s*$", s):
                continue
            if re.match(r"^└\s*Tip:", s):
                continue
            if re.search(r"^(?:▣|╹▀▀|opencode-|Gentle-Orchestrator)", s):
                continue
            if re.search(r"^\+?\s*Thought:\s*\d+", s, re.IGNORECASE):
                continue
            clean.append(cleaned_line)
        return clean

    # 2. Search backwards from newest prompt for substantive assistant response
    for idx in range(len(prompts) - 1, -1, -1):
        _, p_end = prompts[idx]
        next_p_start = (
            prompts[idx + 1][0] if idx + 1 < len(prompts) else len(raw_lines)
        )
        sub_lines = filter_assistant_lines(raw_lines[p_end + 1 : next_p_start])
        body = " ".join(sub_lines).strip()
        body = re.sub(r"\s+", " ", body)
        if len(body) >= 20:
            return "\n".join(sub_lines)

    fallback = filter_assistant_lines(raw_lines)
    return "\n".join(fallback)


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

    # Remove common terminal status lines, chrome, spinners, and format tables
    lines = []
    for line in text.splitlines():
        # Strip OpenCode right-side status columns if present
        line = re.sub(
            r"\s{6,}(?:󰚩|▼|●\s*\d|✕|Context|\$[\d,.]+|\d[\d,.]*\s*tokens|\d+%\s*used|•|LSP|Connection closed|SSE error).*$",
            "",
            line,
        )
        stripped = line.strip()
        if not stripped:
            continue

        # Ignore lines without any alphanumeric character (divider lines, horizontal rules, empty borders)
        if not re.search(r"[A-Za-z0-9áéíóúÁÉÍÓÚñÑ¿¡]", stripped):
            continue

        # Ignore terminal status chrome and spinners
        if re.search(r"^[\u2800-\u28FF●○⏺]", stripped):
            continue
        if re.search(
            r"Tokens:\s*\d+|Quotas:\s*\[|Gemini\s*\d|Claude\s*\d|GPT-?\d",
            stripped,
            re.IGNORECASE,
        ):
            continue
        if re.search(
            r"^\d+(\.\d+)?[kM]?\s*in\s*\|\s*\d+(\.\d+)?[kM]?\s*out", stripped
        ):
            continue
        if re.match(r"^(>|\?|❯|%|\$)\s*$", stripped):
            continue
        if "Conversation compacted" in stripped:
            continue
        if re.search(
            r"(Running command|thinking through|ctrl\+o|expand\)|Exited /artifact|Press esc to interrupt)",
            stripped,
            re.IGNORECASE,
        ):
            continue
        if re.match(r"^└\s*Tip:", stripped):
            continue
        if re.search(r"^(?:▣|╹▀▀|opencode-|Gentle-Orchestrator)", stripped):
            continue
        if re.search(r"^\+?\s*Thought:\s*\d+", stripped, re.IGNORECASE):
            continue

        # Format table borders and cells into natural speech pauses
        # 1. Remove leading/trailing box bars and markdown pipes
        stripped = re.sub(r"^[─━│┃║|┌┐└┘├┤┬┴┼═╔╗╚╝╠╣╦╩╬]+\s*", "", stripped)
        stripped = re.sub(r"\s*[─━│┃║|┌┐└┘├┤┬┴┼═╔╗╚╝╠╣╦╩╬]+$", "", stripped)
        # 2. Convert internal cell separators to natural pause (" — ")
        stripped = re.sub(r"\s*[│┃║|┼]\s*", " — ", stripped)

        lines.append(stripped)

    text = "\n".join(lines)

    # Remove remaining box drawing characters
    text = re.sub(r"[─━│┃┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬┄┅┆┇┈┉┊┋┌┐└┘╭╮╰╯]+", " ", text)
    text = re.sub(r"^[=\-_*#]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Remove markdown header markers: "### Title" -> "Title"
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"#{1,6}\s+", " ", text)

    # Remove bullet markers: "• Item" or "* Item" -> "Item"
    text = re.sub(r"^\s*[•*+-]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+[•*+-]\s+", " ", text)

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


class AudioSession:
    """Manages audio playback state and Unix socket IPC server for dynamic seek/pause/resume/stop."""

    def __init__(self, label: str = "Audio"):
        self.label = label
        self.state = {
            "status": "synthesizing",
            "pos": 0,
            "paused": False,
            "stop": False,
            "total": 0,
            "sr": 24000,
            "label": label,
        }
        self.server: Optional[socket.socket] = None
        self.ipc_thread: Optional[threading.Thread] = None

    def start_ipc(self):
        global _current_playback_state
        _current_playback_state = self.state

        if os.path.exists(IPC_SOCKET):
            try:
                os.remove(IPC_SOCKET)
            except OSError:
                pass
        try:
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(IPC_SOCKET)
            self.server.listen(2)
            self.server.settimeout(0.2)
        except Exception:
            return

        def run_server():
            while not self.state["stop"]:
                try:
                    conn, _ = self.server.accept()
                    raw_cmd = conn.recv(1024).decode("utf-8", errors="ignore").strip()
                    if not raw_cmd:
                        conn.close()
                        continue

                    parts = raw_cmd.split()
                    cmd = parts[0].lower()

                    sr = self.state["sr"]
                    total_frames = self.state["total"]
                    cur_pos = self.state["pos"]
                    current_sec = cur_pos / sr if sr > 0 else 0.0
                    total_sec = total_frames / sr if sr > 0 else 0.0
                    status = self.state["status"]

                    if cmd in ("pause", "toggle-pause"):
                        if status == "playing":
                            self.state["paused"] = True
                            self.state["status"] = "paused"
                        elif status == "paused":
                            self.state["paused"] = False
                            self.state["status"] = "playing"
                        conn.sendall(f"status={self.state['status']} pos={current_sec:.1f} total={total_sec:.1f}\n".encode())
                    elif cmd == "resume":
                        if status == "paused":
                            self.state["paused"] = False
                            self.state["status"] = "playing"
                        conn.sendall(f"status={self.state['status']} pos={current_sec:.1f} total={total_sec:.1f}\n".encode())
                    elif cmd == "seek":
                        delta = float(parts[1]) if len(parts) > 1 else 0.0
                        if total_frames > 0:
                            new_pos = max(0, min(total_frames, self.state["pos"] + int(delta * sr)))
                            self.state["pos"] = new_pos
                            new_sec = new_pos / sr
                            conn.sendall(f"status={self.state['status']} pos={new_sec:.1f} total={total_sec:.1f}\n".encode())
                        else:
                            conn.sendall(f"status={self.state['status']} pos=0.0 total=0.0\n".encode())
                    elif cmd == "status":
                        conn.sendall(f"status={self.state['status']} pos={current_sec:.1f} total={total_sec:.1f}\n".encode())
                    elif cmd == "stop":
                        self.state["stop"] = True
                        self.state["status"] = "stopped"
                        conn.sendall(b"status=stopped\n")

                    conn.close()
                except socket.timeout:
                    continue
                except Exception:
                    break

        self.ipc_thread = threading.Thread(target=run_server, daemon=True)
        self.ipc_thread.start()

    def play(self, decoded: miniaudio.DecodedSoundFile):
        global _active_process
        total_frames = decoded.num_frames
        sample_rate = decoded.sample_rate
        nchannels = decoded.nchannels
        samples = decoded.samples
        sample_width = 2

        self.state["status"] = "playing"
        self.state["total"] = total_frames
        self.state["sr"] = sample_rate

        duration_s = total_frames / sample_rate if sample_rate > 0 else 0
        min_s = int(duration_s) // 60
        sec_s = int(duration_s) % 60
        dur_label = f"{min_s}:{sec_s:02d}" if min_s > 0 else f"{sec_s}s"

        try:
            subprocess.run(
                [
                    "herdr",
                    "notification",
                    "show",
                    "🔊 Reproduciendo voz",
                    "--body",
                    f"Duración: {dur_label} ({self.label})",
                    "--sound",
                    "none",
                ],
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
        except Exception:
            pass

        def audio_generator():
            num_frames = yield b""
            bytes_per_frame = nchannels * sample_width
            silence_cache = b"\x00" * (2048 * bytes_per_frame)

            while not self.state["stop"] and self.state["pos"] < total_frames:
                if self.state["paused"]:
                    need_bytes = num_frames * bytes_per_frame
                    if len(silence_cache) < need_bytes:
                        silence_cache = b"\x00" * need_bytes
                    num_frames = yield silence_cache[:need_bytes]
                    continue

                cur_pos = self.state["pos"]
                end_frame = min(total_frames, cur_pos + num_frames)
                start_sample = cur_pos * nchannels
                end_sample = end_frame * nchannels
                chunk = samples[start_sample:end_sample].tobytes()
                self.state["pos"] = end_frame

                need_bytes = num_frames * bytes_per_frame
                if len(chunk) < need_bytes:
                    chunk += b"\x00" * (need_bytes - len(chunk))

                num_frames = yield chunk

        native_ok = False
        try:
            gen = audio_generator()
            next(gen)
            with miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=nchannels,
                sample_rate=sample_rate,
            ) as device:
                device.start(gen)
                native_ok = True
                while not self.state["stop"] and self.state["pos"] < total_frames:
                    time.sleep(0.05)
        except Exception as e:
            print(f"miniaudio PlaybackDevice fallback: {e}", file=sys.stderr)
            native_ok = False

        if not native_ok:
            temp_wav = None
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_wav = f.name
            try:
                miniaudio.wav_write_file(temp_wav, decoded)
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

    def stop(self):
        self.state["stop"] = True
        self.state["status"] = "stopped"
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
        if self.ipc_thread:
            self.ipc_thread.join(timeout=0.3)
        if os.path.exists(IPC_SOCKET):
            try:
                os.remove(IPC_SOCKET)
            except OSError:
                pass


def parse_rate_to_multiplier(rate_str: str) -> float:
    """Converts rate strings like '+20%', '-10%', '1.2' to float multiplier (e.g. 1.2)."""
    if not rate_str:
        return 1.0
    s = str(rate_str).strip()
    if s.endswith("%"):
        try:
            val = float(s[:-1])
            return max(0.25, min(4.0, 1.0 + (val / 100.0)))
        except ValueError:
            return 1.0
    try:
        val = float(s)
        return max(0.25, min(4.0, val))
    except ValueError:
        return 1.0


class TTSProvider:
    """Base interface for TTS synthesis backends."""
    name: str = "base"

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[callable] = None,
    ) -> bytes:
        raise NotImplementedError


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge Neural TTS (zero-config, high quality, free)."""
    name = "edge"

    def resolve_voice(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        return VOICE_MAP.get(v, voice if voice else DEFAULT_VOICE)

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[callable] = None,
    ) -> bytes:
        resolved = self.resolve_voice(voice)
        communicate = edge_tts.Communicate(
            text=text,
            voice=resolved,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        mp3_chunks = []
        async for chunk in communicate.stream():
            if stop_checker and stop_checker():
                return b""
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])
        return b"".join(mp3_chunks)


class OpenAITTSProvider(TTSProvider):
    """OpenAI Audio TTS provider (tts-1 / tts-1-hd)."""
    name = "openai"

    VOICE_FALLBACK = {
        "elvira": "nova",
        "ximena": "nova",
        "dalia": "nova",
        "alvaro": "onyx",
        "álvaro": "onyx",
        "jorge": "onyx",
        "en": "alloy",
    }
    VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "tts-1",
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.environ.get("OPENAI_TTS_MODEL", "tts-1")

    def resolve_voice(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        if v in self.VALID_VOICES:
            return v
        return self.VOICE_FALLBACK.get(v, "nova")

    def _sync_request(self, text: str, voice: str, speed: float) -> bytes:
        if not self.api_key:
            print("Error: OpenAI TTS requires an API key (set OPENAI_API_KEY or --openai-key)", file=sys.stderr)
            return b""
        url = f"{self.base_url}/audio/speech"
        payload = json.dumps({
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "herdr-tts/0.4.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"OpenAI TTS API error ({e.code}): {err_body}", file=sys.stderr)
            return b""
        except Exception as e:
            print(f"OpenAI TTS network error: {e}", file=sys.stderr)
            return b""

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[callable] = None,
    ) -> bytes:
        if stop_checker and stop_checker():
            return b""
        target_voice = self.resolve_voice(voice)
        speed = parse_rate_to_multiplier(rate)
        return await asyncio.to_thread(self._sync_request, text, target_voice, speed)


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS provider (multilingual, ultra-realistic)."""
    name = "elevenlabs"

    VOICE_MAP = {
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "bella": "EXAVITQu4vr4xnSDxMaL",
        "antoni": "ErXwobaYiN019PkySvjV",
        "adam": "pNInz6obpgDQGcFmaJgB",
        "domi": "AZnzlk1XvdvUeBnXmlld",
        "elli": "MF3mGyEYCl7XYWbV9V6O",
        "josh": "TxGEqnHWrfWFTfGW9XjX",
        "arnold": "VR6AewLTigWG4xSOukaG",
        "sam": "yoZ06aMxZJJ28mfd3POQ",
    }
    DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM" # Rachel

    def __init__(
        self,
        api_key: str = "",
        model: str = "eleven_multilingual_v2",
    ):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.model = model or os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

    def resolve_voice_id(self, voice: str) -> str:
        v = (voice or "").lower().strip()
        if v in self.VOICE_MAP:
            return self.VOICE_MAP[v]
        if len(voice) >= 15 and not re.search(r"\s", voice):
            return voice
        return self.DEFAULT_VOICE_ID

    def _sync_request(self, text: str, voice_id: str) -> bytes:
        if not self.api_key:
            print("Error: ElevenLabs TTS requires an API key (set ELEVENLABS_API_KEY or --eleven-key)", file=sys.stderr)
            return b""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = json.dumps({
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "User-Agent": "herdr-tts/0.4.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"ElevenLabs TTS API error ({e.code}): {err_body}", file=sys.stderr)
            return b""
        except Exception as e:
            print(f"ElevenLabs TTS network error: {e}", file=sys.stderr)
            return b""

    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str = "+0%",
        pitch: str = "+0Hz",
        stop_checker: Optional[callable] = None,
    ) -> bytes:
        if stop_checker and stop_checker():
            return b""
        voice_id = self.resolve_voice_id(voice)
        return await asyncio.to_thread(self._sync_request, text, voice_id)


def get_provider(
    provider_name: str = "edge",
    openai_key: Optional[str] = None,
    openai_base_url: Optional[str] = None,
    openai_model: Optional[str] = None,
    eleven_key: Optional[str] = None,
    eleven_model: Optional[str] = None,
) -> TTSProvider:
    name = (provider_name or "edge").lower().strip()
    if name == "openai":
        return OpenAITTSProvider(
            api_key=openai_key or "",
            base_url=openai_base_url or "https://api.openai.com/v1",
            model=openai_model or "tts-1",
        )
    elif name in ("elevenlabs", "eleven"):
        return ElevenLabsTTSProvider(
            api_key=eleven_key or "",
            model=eleven_model or "eleven_multilingual_v2",
        )
    elif name == "edge":
        return EdgeTTSProvider()
    else:
        print(f"Warning: Unknown provider '{provider_name}', falling back to 'edge'", file=sys.stderr)
        return EdgeTTSProvider()


async def synthesize_and_play(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+20%",
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
) -> None:
    """Synthesizes text with selected provider, optionally saves to file, and plays via audio backend unless no_play is set."""
    global _active_process

    session = None
    if not no_play:
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
            with open(LOCK_FILE, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
        session = AudioSession(label=f"{len(text)} chars")
        session.start_ipc()

    try:
        engine = get_provider(
            provider_name=provider,
            openai_key=openai_key,
            openai_base_url=openai_base_url,
            openai_model=openai_model,
            eleven_key=eleven_key,
            eleven_model=eleven_model,
        )

        def check_stop():
            return session is not None and bool(session.state.get("stop", False))

        mp3_data = await engine.synthesize(
            text=text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            stop_checker=check_stop,
        )

        if not mp3_data or (session and session.state.get("stop")):
            return

        if output_file:
            out_dir = os.path.dirname(os.path.abspath(output_file))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(output_file, "wb") as f:
                f.write(mp3_data)

        if no_play:
            return

        decoded = miniaudio.decode(mp3_data)
        if session:
            session.play(decoded)
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
    finally:
        _active_process = None
        if session:
            session.stop()
        if not no_play:
            cleanup_locks()


def send_ipc_command(command: str) -> Optional[str]:
    """Sends an IPC command to the currently running audio player."""
    if not os.path.exists(IPC_SOCKET):
        return None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1.0)
        client.connect(IPC_SOCKET)
        client.sendall(f"{command}\n".encode("utf-8"))
        res = client.recv(1024).decode("utf-8", errors="ignore").strip()
        client.close()
        return res
    except Exception:
        return None


def play_mp3_file(mp3_path: str, label: str = "Audio") -> None:
    """Decodes an existing MP3 file to PCM in memory and plays via native player."""
    global _active_process
    if not os.path.exists(mp3_path):
        return

    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    session = AudioSession(label=label)
    session.start_ipc()
    try:
        with open(mp3_path, "rb") as f:
            mp3_data = f.read()

        decoded = miniaudio.decode(mp3_data)
        session.play(decoded)
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
    finally:
        session.stop()
        cleanup_locks()


def main():
    parser = argparse.ArgumentParser(description="Herdr Neural TTS Engine")
    parser.add_argument("text", nargs="*", help="Text to speak (reads stdin if omitted)")
    parser.add_argument("--voice", "-v", default="elvira", help="Voice (elvira, alvaro, ximena, dalia, jorge)")
    parser.add_argument("--rate", "-r", default="+20%", help="Speed: +20%%, +10%%, +0%%")
    parser.add_argument("--max-chars", "-m", type=int, default=0, help="Max characters to speak (0 for unlimited)")
    parser.add_argument("--raw", action="store_true", help="Do not clean text")
    parser.add_argument("--output", "-o", help="Save synthesized MP3 audio to file")
    parser.add_argument("--no-play", action="store_true", help="Do not play audio locally")
    parser.add_argument("--play-file", help="Play an existing MP3 file directly without re-synthesizing")
    parser.add_argument(
        "--ipc-cmd",
        help="Send an IPC command to the active audio player (e.g. 'seek +10', 'seek -10', 'toggle-pause', 'status')",
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("TTS_PROVIDER", "edge"),
        choices=["edge", "openai", "elevenlabs", "eleven"],
        help="TTS provider backend (edge, openai, elevenlabs)",
    )
    parser.add_argument("--openai-key", default=os.environ.get("OPENAI_API_KEY", ""), help="OpenAI API key")
    parser.add_argument("--openai-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), help="OpenAI custom base URL")
    parser.add_argument("--openai-model", default=os.environ.get("OPENAI_TTS_MODEL", "tts-1"), help="OpenAI TTS model (tts-1, tts-1-hd)")
    parser.add_argument("--eleven-key", default=os.environ.get("ELEVENLABS_API_KEY", ""), help="ElevenLabs API key")
    parser.add_argument("--eleven-model", default=os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"), help="ElevenLabs model")

    args = parser.parse_args()

    if args.ipc_cmd:
        res = send_ipc_command(args.ipc_cmd)
        if res is not None:
            print(res)
            sys.exit(0)
        else:
            print("Error: No active audio playback session found", file=sys.stderr)
            sys.exit(1)

    if args.play_file:
        play_mp3_file(args.play_file, label=os.path.basename(args.play_file))
        sys.exit(0)

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
                output_file=args.output,
                no_play=args.no_play,
                provider=args.provider,
                openai_key=args.openai_key,
                openai_base_url=args.openai_base_url,
                openai_model=args.openai_model,
                eleven_key=args.eleven_key,
                eleven_model=args.eleven_model,
            )
        )
    except KeyboardInterrupt:
        cleanup_locks()


if __name__ == "__main__":
    main()
