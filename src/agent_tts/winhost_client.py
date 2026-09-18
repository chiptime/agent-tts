"""Client transport for the winhost protocol (v1) plus the remote playback session.

Streams raw s16 PCM over TCP to ``agent-tts --winhost`` running on the Windows
host, and provides :class:`RemoteAudioSession`, a drop-in stand-in for the
local AudioSession surface used by cli.py that ships PCM to the remote target
(winhost server, or a persistent PowerShell process under WSL as zero-install
fallback).
"""

import json
import socket
import sys
import threading
import time

from agent_tts.boundaries import BoundaryMap
from agent_tts.ipc import IPCServer
from agent_tts.playback_target import CONNECT_TIMEOUT_SEC, candidate_hosts, winhost_port
from agent_tts.powershell_playback import PowershellSession, is_wsl_ps_available

PROTOCOL_VERSION = 1
SEND_CHUNK_FRAMES = 4096
PUMP_IDLE_SLEEP_SEC = 0.03


class WinhostUnavailable(Exception):
    """Raised when no candidate Windows host accepts a winhost connection."""

    def __init__(self, location):
        super().__init__(f"winhost unreachable at {location}")
        self.location = location


class WinhostStream:
    """An open PLAY connection to a winhost server (protocol v1 framing)."""

    def __init__(self, sock, host, port, rate, channels, sample_width):
        self.sock = sock
        self.host = host
        self.port = port
        self.rate = rate
        self.channels = channels
        self.sample_width = sample_width
        header = {
            "v": PROTOCOL_VERSION,
            "cmd": "play",
            "rate": rate,
            "channels": channels,
            "format": "s16",
        }
        sock.sendall((json.dumps(header) + "\n").encode("utf-8"))

    def send_pcm(self, chunk: bytes) -> None:
        self.sock.sendall(chunk)

    def finish(self) -> None:
        """Half-closes the write side and waits for the server to close, so the remote playback completes before returning."""
        try:
            self.sock.shutdown(socket.SHUT_WR)
            while True:
                if not self.sock.recv(65536):
                    break
        except OSError:
            pass
        finally:
            self.close()

    def abort(self) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def open_stream(rate, channels, sample_width, env=None, connect_timeout=CONNECT_TIMEOUT_SEC) -> WinhostStream:
    """Connects to the first reachable Windows host candidate and opens a PLAY stream.

    Raises WinhostUnavailable when every candidate refuses or times out.
    """
    port = winhost_port(env)
    tried = []
    for host in candidate_hosts(env):
        tried.append(host)
        try:
            sock = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError:
            continue
        try:
            sock.settimeout(None)
            return WinhostStream(sock, host, port, rate, channels, sample_width)
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
    raise WinhostUnavailable(f"{','.join(tried) or 'no-candidates'}:{port}")


def send_control(cmd, env=None) -> bool:
    """Best-effort one-shot control message (pause/resume/stop) to the Windows host.

    A dead host is a no-op: connection errors are swallowed and logged once
    to stderr.
    """
    payload = (json.dumps({"v": PROTOCOL_VERSION, "cmd": cmd}) + "\n").encode("utf-8")
    port = winhost_port(env)
    last_error = None
    for host in candidate_hosts(env):
        try:
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SEC)
        except OSError as e:
            last_error = e
            continue
        try:
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass
        return True
    print(f"winhost control '{cmd}': no reachable host on port {port} ({last_error})", file=sys.stderr)
    return False


class RemoteAudioSession:
    """Playback session that ships PCM to a remote target instead of a local device.

    Duck-types the AudioSession surface used by cli.py (state dict, lock,
    boundaries, prepare_pcm/append_pcm/play/stop/start_ipc/handle_ipc_command)
    so the synthesis pipeline stays unchanged. Targets:

    - "winhost": stream PCM over TCP to the Windows host; if unreachable,
      warn once on stderr and route the rest of the run through one
      persistent PowerShell session (PowershellSession) for near-gapless
      playback.
    - "wsl-ps": always play through one persistent PowerShell session
      (zero install).

    Terminal visuals (highlight/zen/autoscroll) are not rendered remotely
    because frame delivery is decoupled from real-time playback.
    """

    def __init__(
        self,
        label: str = "Audio",
        auto_rewind_sec: float = 2.0,
        boundaries=None,
        highlight: bool = False,
        autoscroll: bool = False,
        bionic: bool = False,
        zen: bool = False,
        target: str = "winhost",
        env=None,
    ):
        self.label = label
        self.auto_rewind_sec = auto_rewind_sec
        self.boundaries = boundaries or BoundaryMap()
        self.highlight = highlight
        self.autoscroll = autoscroll
        self.bionic = bionic
        self.zen = zen
        self.target = target
        self.env = env
        self.state = {
            "status": "synthesizing",
            "pos": 0.0,
            "total": 0.0,
            "stop": False,
            "producing": False,
            "label": label,
        }
        self.paused_at: float = 0.0
        self.raw_bytes = b""
        self.sample_rate = 24000
        self.nchannels = 1
        self.bytes_per_sample = 2
        self.frame_size = 2
        self.total_frames = 0
        self.current_frame = 0
        self._buffer_loaded = False
        self.lock = threading.Lock()
        self.ipc_server = None
        self._stream = None
        self._remote_live = False
        self._ps_mode = target == "wsl-ps"
        self._fallback_warned = False
        self._ps_session = None

    # -- IPC ----------------------------------------------------------------

    def start_ipc(self) -> None:
        """Starts the IPC control server (same transport as local sessions)."""
        self.ipc_server = IPCServer(command_handler=self.handle_ipc_command)
        self.ipc_server.start()

    def handle_ipc_command(self, cmd: str) -> str:
        """Processes IPC commands; pause/resume/stop forward to the remote target."""
        parts = cmd.strip().split()
        if not parts:
            return "ERR: empty command"
        action = parts[0].lower()

        if action == "status":
            return self._ipc_status()

        if action == "pause":
            with self.lock:
                changed = self.state["status"] == "playing"
                if changed:
                    self.state["status"] = "paused"
                    self.paused_at = time.time()
            if changed:
                self._remote_control("pause")
                self._control_ps_session("pause")
            return self.handle_ipc_command("status")

        if action == "resume":
            with self.lock:
                changed = self.state["status"] == "paused"
                if changed:
                    self.state["status"] = "playing"
                    self.paused_at = 0.0
            if changed:
                self._remote_control("resume")
                self._control_ps_session("resume")
            return self.handle_ipc_command("status")

        if action in ("toggle-pause", "toggle_pause"):
            sent = None
            with self.lock:
                if self.state["status"] == "playing":
                    self.state["status"] = "paused"
                    self.paused_at = time.time()
                    sent = "pause"
                elif self.state["status"] == "paused":
                    self.state["status"] = "playing"
                    self.paused_at = 0.0
                    sent = "resume"
            if sent:
                self._remote_control(sent)
                self._control_ps_session(sent)
            return self.handle_ipc_command("status")

        if action == "stop":
            self.state["stop"] = True
            with self.lock:
                self.state["status"] = "stopped"
            self._remote_control("stop")
            self._control_ps_session("stop")
            return "status=stopped"

        return (
            f"ERR: command '{action}' is not supported for {self.target} playback "
            "(supported: status, pause, resume, toggle-pause, stop)"
        )

    def _ipc_status(self) -> str:
        with self.lock:
            pos = (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0
            total = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0
            sent = self.boundaries.get_sentence_at(pos)
            sent_clean = sent.text.replace("\n", " ").strip() if sent else ""
            sent_idx = sent.index if sent else -1
            para = self.boundaries.get_paragraph_at(pos)
            para_idx = para.index if para else -1
            return (
                f"status={self.state['status']} pos={pos:.2f} total={total:.2f} "
                f"sent_idx={sent_idx} para_idx={para_idx} sentence={sent_clean}"
            )

    def _remote_control(self, cmd: str) -> None:
        """Forwards a control command to the winhost server; a dead host is a no-op."""
        if not self._remote_live:
            return
        try:
            send_control(cmd, env=self.env)
        except Exception as e:
            print(f"winhost control '{cmd}' failed: {e}", file=sys.stderr)

    def _control_ps_session(self, cmd: str) -> None:
        """Forwards a control command to the fallback PowerShell session, if any."""
        ps = self._ps_session
        if ps is None:
            return
        if cmd == "pause":
            ps.pause()
        elif cmd == "resume":
            ps.resume()
        elif cmd == "stop":
            ps.state["stop"] = True
            ps.stop()

    # -- PCM pipeline (mirrors local AudioSession) ---------------------------

    def _load_buffer_locked(self, decoded) -> None:
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
        """Preloads the playback buffer and opens the remote transport before streaming starts."""
        with self.lock:
            self._load_buffer_locked(decoded)
        if not self._ensure_transport(self.sample_rate, self.nchannels, self.bytes_per_sample):
            self._ensure_ps_session().prepare_pcm(decoded)

    def append_pcm(self, decoded) -> bool:
        """Appends a decoded PCM segment (streaming mode); mirrors the local mismatch rule.

        Returns True when the segment was accepted. In PowerShell mode each
        accepted segment is written to the persistent PowerShell process,
        which plays groups back-to-back.
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
        if self._ps_mode:
            try:
                return self._ensure_ps_session().append_pcm(decoded)
            except RuntimeError as e:
                print(f"Stream: PowerShell playback failed: {e}", file=sys.stderr)
                return False
        return True

    def play(self, decoded) -> None:
        """Plays the buffered PCM through the remote target; blocks until playback completes."""
        with self.lock:
            if not self._buffer_loaded:
                self._load_buffer_locked(decoded)
            self.state["status"] = "playing"

        if not self._ensure_transport(self.sample_rate, self.nchannels, self.bytes_per_sample):
            self._ensure_ps_session().play(decoded)
            return

        stream = self._stream
        failed = False
        try:
            while not self.state["stop"]:
                with self.lock:
                    available = self.total_frames - self.current_frame
                    producing = self.state.get("producing", False)
                if available > 0:
                    frames = min(SEND_CHUNK_FRAMES, available)
                    with self.lock:
                        start = self.current_frame * self.frame_size
                        chunk = self.raw_bytes[start:start + frames * self.frame_size]
                        self.current_frame += frames
                    try:
                        stream.send_pcm(chunk)
                    except OSError:
                        failed = True
                        break
                    continue
                if producing:
                    # Buffer drained but more segments are on the way.
                    time.sleep(PUMP_IDLE_SLEEP_SEC)
                    continue
                break
        finally:
            if failed or self.state["stop"]:
                stream.abort()
            else:
                # Half-close and wait for the server to close so playback
                # completes remotely before this call returns.
                try:
                    stream.finish()
                except Exception:
                    pass
            if self._stream is stream:
                self._stream = None
            with self.lock:
                self.state["status"] = "stopped"

    def stop(self) -> None:
        """Stops the session, signals the remote target, and cleans up the IPC server.

        An explicit stop (state["stop"] set before this call, e.g. by the IPC
        handler) hard-stops the PowerShell session and cuts mid-group audio;
        a natural end of run drains the tail instead so the last group is not
        clipped.
        """
        user_stopped = bool(self.state.get("stop"))
        self.state["stop"] = True
        self.state["status"] = "stopped"
        self._remote_control("stop")
        ps = self._ps_session
        if ps is not None:
            if user_stopped:
                ps.state["stop"] = True
                ps.stop()
            else:
                try:
                    ps.finish()
                except Exception:
                    pass  # cleanup must not mask the run's result
        stream = self._stream
        if stream is not None:
            stream.abort()
            self._stream = None
        if self.ipc_server:
            self.ipc_server.stop()
            self.ipc_server = None

    # -- transport / fallback -------------------------------------------------

    def _ensure_transport(self, rate, channels, sample_width) -> bool:
        """Opens the winhost stream on first use.

        Returns True when a live TCP stream carries the audio, False when the
        run is in PowerShell mode. On an unreachable winhost server, warns
        once and switches the whole run to per-group PowerShell playback.
        """
        if self._ps_mode:
            return False
        if self._stream is not None:
            return True
        try:
            self._stream = open_stream(rate, channels, sample_width, env=self.env)
        except WinhostUnavailable as e:
            self._enter_ps_fallback(e)
            return False
        self._remote_live = True
        return True

    def _enter_ps_fallback(self, error: WinhostUnavailable) -> None:
        if not is_wsl_ps_available(env=self.env):
            raise RuntimeError(
                f"{error}; wsl-ps fallback unavailable "
                "(powershell.exe not found on PATH or not running under WSL)"
            )
        if not self._fallback_warned:
            print(
                f"winhost unreachable at {error.location}; falling back to wsl-ps playback",
                file=sys.stderr,
            )
            self._fallback_warned = True
        self._ps_mode = True
        self._ensure_ps_session()

    # -- PowerShell session ---------------------------------------------------

    def _ensure_ps_session(self) -> PowershellSession:
        """Lazily creates the persistent PowerShell session used for the whole run."""
        if self._ps_session is None:
            self._ps_session = PowershellSession(
                label=self.label,
                auto_rewind_sec=self.auto_rewind_sec,
                boundaries=self.boundaries,
                highlight=self.highlight,
                autoscroll=self.autoscroll,
                bionic=self.bionic,
                zen=self.zen,
                env=self.env,
            )
        return self._ps_session
