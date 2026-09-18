"""Audio playback engine with miniaudio C backend and frame-accurate seek/pause."""

import os
import sys
import threading
import time
from typing import Optional
import miniaudio

from agent_tts.constants import IPC_SOCKET, LOCK_FILE, PID_FILE
from agent_tts.ipc import IPCServer

_current_session: Optional["AudioSession"] = None


def cleanup_locks() -> None:
    """Removes all transient lock, pid, and socket files."""
    for f in (LOCK_FILE, PID_FILE, IPC_SOCKET):
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


class AudioSession:
    """Interactive PCM audio playback session with frame-accurate seek and smart auto-rewind."""

    def __init__(self, label: str = "Audio", auto_rewind_sec: float = 2.0):
        self.label = label
        self.auto_rewind_sec = auto_rewind_sec
        self.state = {
            "status": "synthesizing",
            "pos": 0.0,
            "total": 0.0,
            "stop": False,
            "label": label,
        }
        self.paused_at: float = 0.0
        self.decoded_data = None
        self.sample_rate = 24000
        self.nchannels = 1
        self.bytes_per_sample = 2
        self.frame_size = 2
        self.total_frames = 0
        self.current_frame = 0
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
                return f"status={self.state['status']} pos={pos:.2f} total={total:.2f} label={self.state['label']}"

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

        return f"ERR: unknown command '{action}'"

    def play(self, decoded) -> None:
        """Plays decoded PCM audio frames via miniaudio.PlaybackDevice."""
        self.decoded_data = decoded.samples
        self.sample_rate = decoded.sample_rate
        self.nchannels = decoded.nchannels
        self.bytes_per_sample = 2  # miniaudio default is s16
        self.frame_size = self.nchannels * self.bytes_per_sample
        self.total_frames = len(self.decoded_data) // self.frame_size
        self.current_frame = 0

        self.state["status"] = "playing"
        self.state["total"] = self.total_frames / float(self.sample_rate)

        device = miniaudio.PlaybackDevice(
            output_format=decoded.format,
            nchannels=self.nchannels,
            sample_rate=self.sample_rate,
        )

        def stream_generator(num_frames: int):
            while not self.state["stop"] and self.current_frame < self.total_frames:
                with self.lock:
                    status = self.state["status"]
                    curr = self.current_frame
                    tot = self.total_frames

                if status == "paused":
                    # Emit silence while paused to keep device stream alive
                    silence_frames = min(num_frames, 512)
                    yield bytes(silence_frames * self.frame_size)
                    continue

                if status != "playing":
                    break

                frames_available = tot - curr
                frames_to_read = min(num_frames, frames_available)

                start_byte = curr * self.frame_size
                end_byte = start_byte + (frames_to_read * self.frame_size)
                chunk = self.decoded_data[start_byte:end_byte]

                with self.lock:
                    self.current_frame += frames_to_read

                yield chunk

        try:
            device.start(stream_generator)
            while not self.state["stop"] and self.current_frame < self.total_frames:
                time.sleep(0.05)
        except Exception as e:
            print(f"Device error: {e}", file=sys.stderr)
        finally:
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


def play_mp3_data(mp3_data: bytes, label: str = "Audio", auto_rewind_sec: float = 2.0) -> None:
    """Decodes in-memory MP3 bytes and plays them through a tracked AudioSession."""
    global _current_session
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    session = AudioSession(label=label, auto_rewind_sec=auto_rewind_sec)
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


def play_mp3_file(mp3_path: str, label: str = "Audio", auto_rewind_sec: float = 2.0) -> None:
    """Decodes an existing MP3 file to PCM in memory and plays via native player."""
    if not os.path.exists(mp3_path):
        return
    with open(mp3_path, "rb") as f:
        data = f.read()
    play_mp3_data(data, label=label or os.path.basename(mp3_path), auto_rewind_sec=auto_rewind_sec)
