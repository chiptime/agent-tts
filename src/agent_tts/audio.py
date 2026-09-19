"""Audio playback engine with miniaudio C backend and frame-accurate seek/pause."""

import os
import shutil
import sys
import threading
import time
from typing import Optional
import miniaudio

from agent_tts.boundaries import BoundaryMap, apply_bionic_reading
from agent_tts.cleaner import strip_ansi
from agent_tts.constants import DEFAULT_VOICE, IPC_SOCKET, LOCK_FILE, PID_FILE
from agent_tts.ipc import IPCServer

# Length cap for the sanitized text snippet carried by the IPC status payload.
STATUS_SNIPPET_MAX_CHARS = 60

_current_session: Optional["AudioSession"] = None


def _sanitize_snippet(text: str, limit: int = STATUS_SNIPPET_MAX_CHARS) -> str:
    """Collapses sentence text into a single-line, control-char-free display snippet.

    Strips ANSI escapes and non-printable characters, collapses whitespace, and
    caps the result at ``limit`` chars (longer text gets a trailing ellipsis).
    """
    cleaned = strip_ansi(text or "")
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 3] + "..."
    return cleaned


def cleanup_locks() -> None:
    """Removes all transient lock, pid, and socket files."""
    for f in (LOCK_FILE, PID_FILE, IPC_SOCKET):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


class AudioSession:
    """Interactive PCM audio playback session with frame-accurate seek, smart auto-rewind, and sentence navigation."""

    def __init__(
        self,
        label: str = "Audio",
        auto_rewind_sec: float = 2.0,
        boundaries: Optional[BoundaryMap] = None,
        highlight: bool = False,
        autoscroll: bool = False,
        bionic: bool = False,
        zen: bool = False,
        provider: str = "",
        voice: str = "",
    ):
        self.label = label
        self.auto_rewind_sec = auto_rewind_sec
        self.boundaries = boundaries or BoundaryMap()
        self.highlight = highlight
        self.autoscroll = autoscroll
        self.bionic = bionic
        self.zen = zen
        # Engine metadata surfaced by the IPC status payload. Resolution order:
        # explicit arg > AGENT_TTS_PROVIDER / AGENT_TTS_VOICE env (settable by
        # hosts launching the CLI) > TTS_PROVIDER env (same default source the
        # CLI uses) > built-in CLI defaults.
        self.provider = (
            provider
            or os.environ.get("AGENT_TTS_PROVIDER", "")
            or os.environ.get("TTS_PROVIDER", "")
            or "edge"
        )
        self.voice = voice or os.environ.get("AGENT_TTS_VOICE", "") or DEFAULT_VOICE
        self.state = {
            "status": "synthesizing",
            "pos": 0.0,
            "total": 0.0,
            "stop": False,
            "producing": False,
            "label": label,
        }
        self.paused_at: float = 0.0
        self.decoded_data = None
        self.raw_bytes = b""
        self.sample_rate = 24000
        self.nchannels = 1
        self.bytes_per_sample = 2
        self.frame_size = 2
        self.total_frames = 0
        self.current_frame = 0
        self._buffer_loaded = False
        self.lock = threading.Lock()
        self.ipc_server: Optional[IPCServer] = None

    def start_ipc(self) -> None:
        """Starts the Unix socket IPC server for interactive control."""
        self.ipc_server = IPCServer(command_handler=self.handle_ipc_command)
        self.ipc_server.start()

    def handle_ipc_command(self, cmd: str) -> str:
        """Processes commands arriving over the IPC socket."""
        parts = cmd.strip().split()
        if not parts:
            return "ERR: empty command"
        action = parts[0].lower()

        if action == "status":
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                total = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0
                sent = self.boundaries.get_sentence_at(pos)
                sent_clean = sent.text.replace("\n", " ").strip() if sent else ""
                sent_idx = sent.index if sent else -1
                para = self.boundaries.get_paragraph_at(pos)
                para_idx = para.index if para else -1
                # Single-line kv payload: legacy fields first (clients parsing
                # just the state word or pos/total keep working), engine
                # metadata appended at the end for dashboard consumers.
                return (
                    f"status={self.state['status']} pos={pos:.2f} total={total:.2f} "
                    f"sent_idx={sent_idx} para_idx={para_idx} sentence={sent_clean} "
                    f"provider={self.provider} voice={self.voice} "
                    f"text={_sanitize_snippet(sent_clean)}"
                )

        elif action == "pause":
            with self.lock:
                if self.state["status"] == "playing":
                    self.state["status"] = "paused"
                    self.paused_at = time.time()
            return self.handle_ipc_command("status")

        elif action == "resume":
            with self.lock:
                if self.state["status"] == "paused":
                    # Smart auto-rewind if paused for more than 2 seconds
                    if self.auto_rewind_sec > 0 and self.paused_at > 0:
                        elapsed_paused = time.time() - self.paused_at
                        if elapsed_paused >= 2.0:
                            rewind_frames = int(self.auto_rewind_sec * self.sample_rate)
                            self.current_frame = max(0, self.current_frame - rewind_frames)
                    self.state["status"] = "playing"
                    self.paused_at = 0.0
            return self.handle_ipc_command("status")

        elif action in ("toggle-pause", "toggle_pause"):
            with self.lock:
                if self.state["status"] == "playing":
                    self.state["status"] = "paused"
                    self.paused_at = time.time()
                elif self.state["status"] == "paused":
                    if self.auto_rewind_sec > 0 and self.paused_at > 0:
                        elapsed_paused = time.time() - self.paused_at
                        if elapsed_paused >= 2.0:
                            rewind_frames = int(self.auto_rewind_sec * self.sample_rate)
                            self.current_frame = max(0, self.current_frame - rewind_frames)
                    self.state["status"] = "playing"
                    self.paused_at = 0.0
            return self.handle_ipc_command("status")

        elif action == "stop":
            self.state["stop"] = True
            with self.lock:
                self.state["status"] = "stopped"
            return "status=stopped"

        elif action == "seek":
            if len(parts) < 2:
                return "ERR: seek requires delta in seconds, e.g. seek +10 or seek -10"
            try:
                delta_sec = float(parts[1])
                with self.lock:
                    delta_frames = int(delta_sec * self.sample_rate)
                    new_frame = max(0, min(self.total_frames, self.current_frame + delta_frames))
                    self.current_frame = new_frame
                return self.handle_ipc_command("status")
            except ValueError:
                return f"ERR: invalid delta {parts[1]}"

        elif action == "rewind":
            return self.handle_ipc_command("seek -10")

        elif action == "forward":
            return self.handle_ipc_command("seek +10")

        elif action in ("next-sentence", "next_sentence", "next-sent"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                next_sent = self.boundaries.get_next_sentence(pos)
                if next_sent:
                    self.current_frame = round(next_sent.start_sec * self.sample_rate)
                    text_clean = next_sent.text.replace("\n", " ").strip()
                    return f"status={self.state['status']} pos={next_sent.start_sec:.2f} sent_idx={next_sent.index} sentence={text_clean}"
                return "status=playing at_end=true"

        elif action in ("prev-sentence", "prev_sentence", "prev-sent"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                prev_sent = self.boundaries.get_prev_sentence(pos)
                if prev_sent:
                    self.current_frame = round(prev_sent.start_sec * self.sample_rate)
                    text_clean = prev_sent.text.replace("\n", " ").strip()
                    return f"status={self.state['status']} pos={prev_sent.start_sec:.2f} sent_idx={prev_sent.index} sentence={text_clean}"
                return "status=playing at_start=true"

        elif action in ("sentence", "current-sentence", "current_sentence"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                cur_sent = self.boundaries.get_sentence_at(pos)
                if cur_sent:
                    text_clean = cur_sent.text.replace("\n", " ").strip()
                    return f"sent_idx={cur_sent.index} start={cur_sent.start_sec:.2f} end={cur_sent.end_sec:.2f} text={text_clean}"
                return "sent_idx=-1 text="

        elif action in ("next-paragraph", "next_paragraph", "next-para"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                next_p = self.boundaries.get_next_paragraph(pos)
                if next_p:
                    self.current_frame = round(next_p.start_sec * self.sample_rate)
                    text_clean = next_p.text.replace("\n", " ").strip()
                    if len(text_clean) > 80:
                        text_clean = text_clean[:77] + "..."
                    return f"status={self.state['status']} pos={next_p.start_sec:.2f} para_idx={next_p.index} paragraph={text_clean}"
                return "status=playing at_end=true"

        elif action in ("prev-paragraph", "prev_paragraph", "prev-para"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                prev_p = self.boundaries.get_prev_paragraph(pos)
                if prev_p:
                    self.current_frame = round(prev_p.start_sec * self.sample_rate)
                    text_clean = prev_p.text.replace("\n", " ").strip()
                    if len(text_clean) > 80:
                        text_clean = text_clean[:77] + "..."
                    return f"status={self.state['status']} pos={prev_p.start_sec:.2f} para_idx={prev_p.index} paragraph={text_clean}"
                return "status=playing at_start=true"

        elif action in ("paragraph", "current-paragraph", "current_paragraph"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                cur_p = self.boundaries.get_paragraph_at(pos)
                if cur_p:
                    text_clean = cur_p.text.replace("\n", " ").strip()
                    if len(text_clean) > 80:
                        text_clean = text_clean[:77] + "..."
                    return f"para_idx={cur_p.index} start={cur_p.start_sec:.2f} end={cur_p.end_sec:.2f} text={text_clean}"
                return "para_idx=-1 text="

        elif action in ("highlight", "current-highlight"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                return self.boundaries.format_highlighted_sentence(pos, ansi=True, bionic=self.bionic)

        elif action in ("scroll-info", "autoscroll-info", "autoscroll"):
            with self.lock:
                pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                tot = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0
                sent = self.boundaries.get_sentence_at(pos)
                sent_idx = sent.index if sent else -1
                total_sents = len(self.boundaries.sentences)
                para = self.boundaries.get_paragraph_at(pos)
                para_idx = para.index if para else -1
                total_paras = len(self.boundaries.paragraphs)
                pct = (pos / tot * 100.0) if tot > 0 else 0.0
                return (
                    f"pos={pos:.2f} total={tot:.2f} pct={pct:.1f} "
                    f"sent_idx={sent_idx} total_sents={total_sents} "
                    f"para_idx={para_idx} total_paras={total_paras}"
                )

        return f"ERR: unknown command '{action}'"

    def _render_fullscreen_view(self, pos: float, tot: float) -> None:
        """Renders distraction-free full-screen reader view with synchronized auto-scroll."""
        cols, rows = shutil.get_terminal_size((80, 24))
        if cols < 20 or rows < 5:
            return

        sent = self.boundaries.get_sentence_at(pos)
        curr_sent_idx = sent.index if sent else 0
        total_sents = len(self.boundaries.sentences)

        # 1. Title Header
        title = "─── ZEN MODE ───" if self.zen else "─── AUTO-SCROLL READER ───"
        pad_top = max(0, (cols - len(title)) // 2)
        header = f"\x1b[2m{' ' * pad_top}{title}\x1b[0m"

        # 2. Content lines (surrounding sentences centered vertically)
        prev_sents = []
        if curr_sent_idx > 0:
            for idx in range(max(0, curr_sent_idx - 2), curr_sent_idx):
                s = self.boundaries.sentences[idx]
                txt = apply_bionic_reading(s.text) if self.bionic else s.text
                prev_sents.append(f"\x1b[2;37m{txt}\x1b[0m")

        curr_hl = self.boundaries.format_highlighted_sentence(pos, ansi=True, bionic=self.bionic)

        next_sents = []
        if curr_sent_idx + 1 < total_sents:
            for idx in range(curr_sent_idx + 1, min(total_sents, curr_sent_idx + 3)):
                s = self.boundaries.sentences[idx]
                txt = apply_bionic_reading(s.text) if self.bionic else s.text
                next_sents.append(f"\x1b[2;37m{txt}\x1b[0m")

        all_content = prev_sents + [curr_hl] + next_sents
        content_rows = max(1, rows - 3)
        top_padding = max(1, (content_rows - len(all_content)) // 2)

        # 3. Progress Bar Footer (pinned at bottom row)
        elapsed_m, elapsed_s = divmod(int(pos), 60)
        total_m, total_s = divmod(int(tot), 60)
        pct = (pos / tot * 100.0) if tot > 0 else 0.0

        bar_width = max(10, min(30, cols - 45))
        filled_len = int(bar_width * (pos / tot)) if tot > 0 else 0
        filled_len = min(bar_width, max(0, filled_len))
        bar_chars = (
            "━" * max(0, filled_len - 1)
            + ("╸" if 0 < filled_len < bar_width else ("━" if filled_len == bar_width else ""))
            + "━" * (bar_width - filled_len)
        )

        status_left = f"▶ {elapsed_m:02d}:{elapsed_s:02d} / {total_m:02d}:{total_s:02d}  {bar_chars} {pct:3.0f}%"
        status_right = f"Oración {curr_sent_idx + 1}/{total_sents}"
        gap = max(2, cols - len(status_left) - len(status_right) - 2)
        footer = f"\x1b[7m {status_left}{' ' * gap}{status_right} \x1b[0m"

        output_lines = [header]
        for _ in range(top_padding):
            output_lines.append("")
        output_lines.extend(all_content)
        while len(output_lines) < rows - 1:
            output_lines.append("")
        output_lines.append(footer)

        buf = "\x1b[H" + "\n".join(l + "\x1b[K" for l in output_lines[:rows])
        sys.stdout.write(buf)
        sys.stdout.flush()

    def _load_decoded_locked(self, decoded) -> None:
        """Replaces the playback buffer with a freshly decoded segment. Caller must hold self.lock."""
        self.raw_bytes = decoded.samples.tobytes()
        self.sample_rate = decoded.sample_rate
        self.nchannels = decoded.nchannels
        self.bytes_per_sample = getattr(decoded, "sample_width", 2)
        self.frame_size = self.nchannels * self.bytes_per_sample
        self.total_frames = len(self.raw_bytes) // self.frame_size
        self.current_frame = 0
        self.state["total"] = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0
        self._buffer_loaded = True

    def prepare_pcm(self, decoded) -> None:
        """Preloads the playback buffer with the first decoded segment before streaming starts."""
        with self.lock:
            self._load_decoded_locked(decoded)

    def append_pcm(self, decoded) -> bool:
        """Appends a decoded PCM segment to the live playback buffer (streaming mode).

        Returns True when the segment was appended. Segments whose sample rate,
        channel count, or sample width differ from the session format are skipped
        (resampling is out of scope); a warning is logged to stderr.
        """
        seg_rate = decoded.sample_rate
        seg_channels = decoded.nchannels
        seg_width = getattr(decoded, "sample_width", 2)
        if seg_rate != self.sample_rate or seg_channels != self.nchannels or seg_width != self.bytes_per_sample:
            print(
                f"Stream: skipping segment with mismatched format "
                f"({seg_rate}Hz/{seg_channels}ch/{seg_width * 8}bit vs "
                f"{self.sample_rate}Hz/{self.nchannels}ch/{self.bytes_per_sample * 8}bit)",
                file=sys.stderr,
            )
            return False
        with self.lock:
            self.raw_bytes += decoded.samples.tobytes()
            self.total_frames = len(self.raw_bytes) // self.frame_size
            self.state["total"] = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0
        return True

    def _stream_generator(self):
        """Yields PCM frames to the device, holding in silence while synthesis is still producing."""
        num_frames = yield b""
        while not self.state["stop"]:
            with self.lock:
                status = self.state["status"]
                producing = self.state.get("producing", False)
                curr = self.current_frame
                tot = self.total_frames

            if status == "paused":
                silence_frames = min(num_frames, 512)
                num_frames = yield bytes(silence_frames * self.frame_size)
                continue

            if status != "playing":
                break

            frames_available = tot - curr
            if frames_available <= 0:
                if producing:
                    # Buffer drained but more segments are on the way: bridge with silence.
                    silence_frames = min(num_frames, 512)
                    num_frames = yield bytes(silence_frames * self.frame_size)
                    continue
                break

            frames_to_read = min(num_frames, frames_available)

            start_byte = curr * self.frame_size
            end_byte = start_byte + (frames_to_read * self.frame_size)
            chunk = self.raw_bytes[start_byte:end_byte]

            with self.lock:
                self.current_frame += frames_to_read

            num_frames = yield chunk

        yield b""

    def play(self, decoded) -> None:
        """Plays decoded PCM frames via miniaudio.PlaybackDevice; supports live-buffer streaming."""
        with self.lock:
            if not self._buffer_loaded:
                self._load_decoded_locked(decoded)
            self.state["status"] = "playing"

        sample_fmt = getattr(decoded, "sample_format", miniaudio.SampleFormat.SIGNED16)
        device = miniaudio.PlaybackDevice(
            output_format=sample_fmt,
            nchannels=self.nchannels,
            sample_rate=self.sample_rate,
        )

        stream = self._stream_generator()
        next(stream)  # Prime generator for miniaudio callback

        use_fullscreen = self.zen or self.autoscroll
        if use_fullscreen:
            sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
            sys.stdout.flush()

        try:
            device.start(stream)
            last_hl = ""
            last_sent_idx = -1
            prev_lines = 1

            while not self.state["stop"] and (self.state.get("producing") or self.current_frame < self.total_frames):
                with self.lock:
                    pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
                    tot = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0

                if use_fullscreen and self.boundaries.sentences:
                    self._render_fullscreen_view(pos, tot)
                elif self.highlight and self.boundaries.sentences:
                    sent = self.boundaries.get_sentence_at(pos)
                    curr_sent_idx = sent.index if sent else -1

                    if curr_sent_idx != last_sent_idx and last_sent_idx != -1:
                        # Print completed previous sentence and advance line
                        if prev_lines > 1:
                            sys.stdout.write(f"\x1b[{prev_lines - 1}A")
                        sys.stdout.write("\r\x1b[J")
                        if 0 <= last_sent_idx < len(self.boundaries.sentences):
                            p_sent = self.boundaries.sentences[last_sent_idx]
                            p_text = apply_bionic_reading(p_sent.text) if self.bionic else p_sent.text
                            sys.stdout.write(f"\x1b[2m{p_text}\x1b[0m\n")
                        else:
                            sys.stdout.write("\n")
                        sys.stdout.flush()
                        prev_lines = 1
                        last_sent_idx = curr_sent_idx
                    elif last_sent_idx == -1 and curr_sent_idx != -1:
                        last_sent_idx = curr_sent_idx

                    hl = self.boundaries.format_highlighted_sentence(pos, ansi=True, bionic=self.bionic)
                    if hl != last_hl:
                        cols, _ = shutil.get_terminal_size((80, 24))
                        if prev_lines > 1:
                            sys.stdout.write(f"\x1b[{prev_lines - 1}A")
                        sys.stdout.write(f"\r\x1b[J{hl}")
                        sys.stdout.flush()
                        last_hl = hl
                        if sent:
                            prev_lines = max(1, (len(sent.text) + cols - 1) // cols)

                time.sleep(0.03)
        except Exception as e:
            print(f"Device error: {e}", file=sys.stderr)
            raise
        finally:
            if use_fullscreen:
                sys.stdout.write("\x1b[?25h\x1b[?1049l\n")
                sys.stdout.flush()
            elif self.highlight:
                sys.stdout.write("\n")
                sys.stdout.flush()
            try:
                device.stop()
                device.close()
            except Exception:
                pass
            self.state["status"] = "stopped"

    def stop(self) -> None:
        """Terminates session and cleans up server."""
        self.state["stop"] = True
        self.state["status"] = "stopped"
        if self.ipc_server:
            self.ipc_server.stop()
            self.ipc_server = None


def play_mp3_data(
    mp3_data: bytes,
    label: str = "Audio",
    auto_rewind_sec: float = 2.0,
    boundaries: Optional[BoundaryMap] = None,
    highlight: bool = False,
    autoscroll: bool = False,
    bionic: bool = False,
    zen: bool = False,
    provider: str = "",
    voice: str = "",
) -> None:
    """Decodes in-memory MP3 bytes and plays them through a tracked AudioSession."""
    global _current_session
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    if boundaries is None and hasattr(mp3_data, "boundaries"):
        boundaries = getattr(mp3_data, "boundaries")

    session = AudioSession(
        label=label,
        auto_rewind_sec=auto_rewind_sec,
        boundaries=boundaries,
        highlight=highlight,
        autoscroll=autoscroll,
        bionic=bionic,
        zen=zen,
        provider=provider,
        voice=voice,
    )
    _current_session = session
    session.start_ipc()
    try:
        decoded = miniaudio.decode(mp3_data)
        session.play(decoded)
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
    finally:
        session.stop()
        _current_session = None
        cleanup_locks()


def play_mp3_file(
    mp3_path: str,
    label: str = "Audio",
    auto_rewind_sec: float = 2.0,
    boundaries: Optional[BoundaryMap] = None,
    highlight: bool = False,
    autoscroll: bool = False,
    bionic: bool = False,
    zen: bool = False,
    provider: str = "",
    voice: str = "",
) -> None:
    """Decodes an existing MP3 file to PCM in memory and plays via native player."""
    if not os.path.exists(mp3_path):
        return
    with open(mp3_path, "rb") as f:
        data = f.read()
    play_mp3_data(
        data,
        label=label or os.path.basename(mp3_path),
        auto_rewind_sec=auto_rewind_sec,
        boundaries=boundaries,
        highlight=highlight,
        autoscroll=autoscroll,
        bionic=bionic,
        zen=zen,
        provider=provider,
        voice=voice,
    )
