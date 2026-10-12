"""Client transport for the winhost protocol (v1) plus the remote playback session.

Streams raw s16 PCM over TCP to ``agent-tts --winhost`` running on the Windows
host, and provides :class:`RemoteAudioSession`, a drop-in stand-in for the
local AudioSession surface used by cli.py that ships PCM to the remote target
(winhost server, a persistent PowerShell process under WSL as zero-install
fallback, or the local device for the "windows" target fallback).
"""

import json
import os
import socket
import sys
import threading
import time

from agent_tts import ducking
from agent_tts.audio import AudioSession
from agent_tts.boundaries import BoundaryMap, estimate_boundaries_from_text
from agent_tts.constants import DEFAULT_VOICE
from agent_tts.ipc import IPCServer
from agent_tts.playback_target import (
    CONNECT_TIMEOUT_SEC,
    WINDOWS_TARGET,
    candidate_hosts,
    winhost_port,
)
from agent_tts.powershell_playback import PowershellSession, is_wsl_ps_available

PROTOCOL_VERSION = 1
SEND_CHUNK_FRAMES = 4096
PUMP_IDLE_SLEEP_SEC = 0.03
DUCK_SWEEP_SEC = 2.0
MAX_ACTIVITY_BYTES = 8192


def query_activity(env=None) -> dict:
    """Read one bounded v1 activity reply; every failure degrades to inactive."""
    inactive = {"communication_active": False, "capture_active": False}
    payload = (json.dumps({"v": PROTOCOL_VERSION, "cmd": "activity"}) + "\n").encode("utf-8")
    try:
        port = winhost_port(env)
        for host in candidate_hosts(env):
            sock = None
            try:
                sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SEC)
                deadline = time.monotonic() + CONNECT_TIMEOUT_SEC
                sock.sendall(payload)
                sock.shutdown(socket.SHUT_WR)
                buf = b""
                while b"\n" not in buf:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("activity reply timed out")
                    sock.settimeout(remaining)
                    chunk = sock.recv(min(4096, MAX_ACTIVITY_BYTES + 1 - len(buf)))
                    if not chunk or len(buf) + len(chunk) > MAX_ACTIVITY_BYTES:
                        raise ValueError("invalid activity reply framing")
                    buf += chunk
                state = json.loads(buf.partition(b"\n")[0].decode("utf-8"))
                if not isinstance(state, dict) or state.get("ok") is not True:
                    raise ValueError("activity reply must acknowledge success")
                if any(type(state.get(key)) is not bool for key in inactive):
                    raise ValueError("activity flags must be booleans")
                return {key: state[key] for key in inactive}
            except Exception:
                continue
            finally:
                if sock is not None:
                    sock.close()
    except Exception:
        pass
    ducking._warn_once("winhost_query", "winhost activity query unavailable; continuing without ducking")
    return inactive

# Read-only karaoke queries served by remote sessions, byte-compatible with
# the local AudioSession replies (highlight/scroll-info/sentence/paragraph
# plus their aliases). Navigation commands stay remote-unsupported.
_READ_ONLY_ACTIONS = frozenset(
    {
        "highlight",
        "current-highlight",
        "scroll-info",
        "autoscroll-info",
        "autoscroll",
        "sentence",
        "current-sentence",
        "current_sentence",
        "paragraph",
        "current-paragraph",
        "current_paragraph",
    }
)


class WinhostUnavailable(Exception):
    """Raised when no candidate Windows host accepts a winhost connection."""

    def __init__(self, location):
        super().__init__(f"winhost unreachable at {location}")
        self.location = location


class WinhostStream:
    """An open PLAY connection to a winhost server (protocol v1 framing)."""

    def __init__(self, sock, host, port, rate, channels, sample_width, gain_db=0.0, paused=False):
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
        if gain_db != 0.0:
            header["gain_db"] = gain_db
        if paused:
            header["paused"] = True
        sock.sendall((json.dumps(header) + "\n").encode("utf-8"))

    def send_pcm(self, chunk: bytes) -> None:
        self.sock.sendall(chunk)

    def finish(self, on_wait=None) -> None:
        """Half-close and drain; an optional wait hook supervises live policy.

        Small receive timeouts allow activity sweeps while the host drains its
        queued PCM, including while auto-paused. Legacy callers stay blocking.
        """
        try:
            self.sock.shutdown(socket.SHUT_WR)
            if on_wait is not None:
                self.sock.settimeout(0.2)
            while True:
                if on_wait is not None:
                    on_wait()
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    if on_wait is not None:
                        continue
                    raise
                if not chunk:
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


def open_stream(rate, channels, sample_width, env=None, connect_timeout=CONNECT_TIMEOUT_SEC,
                *, gain_db=0.0, paused=False) -> WinhostStream:
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
            return WinhostStream(sock, host, port, rate, channels, sample_width, gain_db=gain_db, paused=paused)
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
    raise WinhostUnavailable(f"{','.join(tried) or 'no-candidates'}:{port}")


def send_control(cmd, env=None, *, gain_db=None) -> bool:
    """Best-effort one-shot control message (pause/resume/stop) to the Windows host.

    A dead host is a no-op: connection errors are swallowed and logged once
    to stderr.
    """
    header = {"v": PROTOCOL_VERSION, "cmd": cmd}
    if cmd == "gain":
        header["gain_db"] = 0.0 if gain_db is None else gain_db
    payload = (json.dumps(header) + "\n").encode("utf-8")
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
    - "windows": stream PCM over TCP to the Windows host; if unreachable,
      warn once on stderr and route the rest of the run through the local
      AudioSession device (WSLg PulseAudio under WSL). PowerShell is
      never used by this target.

    Terminal visuals (highlight/zen/autoscroll) are not rendered remotely
    because frame delivery is decoupled from real-time playback; after a
    "windows" local fallback they render again through the local session.
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
        provider: str = "",
        voice: str = "",
        env=None,
        document_text: str = "",
    ):
        self.label = label
        self.auto_rewind_sec = auto_rewind_sec
        self.boundaries = boundaries or BoundaryMap()
        self.document_text = document_text
        self.highlight = highlight
        self.autoscroll = autoscroll
        self.bionic = bionic
        self.zen = zen
        self.target = target
        self.env = env
        # Engine metadata surfaced by the IPC status payload, resolved the
        # same way as the local AudioSession (explicit arg > AGENT_TTS_*
        # env > TTS_PROVIDER env > built-in defaults) so a delegated play
        # reports what the request asked for on every target kind (RS-2).
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
        self._local_mode = False
        self._fallback_warned = False
        self._ps_session = None
        self._local_session = None
        self._duck_policy = ducking.resolve_policy(target, env) if target in ("winhost", "windows") else "ignore"
        self._duck_db = ducking.duck_db(env) if self._duck_policy == "duck" else 0.0
        self._duck_gain_db = 0.0
        self._duck_state = {"communication_active": False, "capture_active": False}
        self._duck_next_check = 0.0
        self._duck_paused = False
        self._duck_running = False

    def _apply_remote_activity(self, state: dict) -> None:
        with self.lock:
            if self.state["stop"]:
                return
            self._duck_state = dict(state)
            self.state["comm_active"] = bool(state.get("communication_active"))
            action = ducking.decide(
                {"comm_active": state.get("communication_active"), "capture_active": state.get("capture_active")},
                self._duck_policy,
            )
            gain_db = -self._duck_db if action == "duck" else 0.0
            changed = gain_db != self._duck_gain_db
            self._duck_gain_db = gain_db
            live = self._remote_live and self._duck_running
            auto_paused = self._duck_paused
        if live and changed:
            send_control("gain", env=self._activity_env(), gain_db=gain_db)
        if live and action == "pause":
            self.handle_ipc_command("pause", _automatic=True)
        elif live and auto_paused:
            self.handle_ipc_command("resume", _automatic=True)

    def _activity_env(self):
        """Pin live queries/duck controls to the endpoint carrying this stream."""
        stream = self._stream
        if stream is None:
            return self.env
        return {"AGENT_TTS_WINHOST_HOST": stream.host, "AGENT_TTS_WINHOST_PORT": str(stream.port)}

    def _check_remote_ducking(self) -> None:
        with self.lock:
            eligible = self.state["status"] == "playing" or (
                self.state["status"] == "paused" and self._duck_paused
            )
            if (self._duck_policy == "ignore" or not self._duck_running or not self._remote_live
                    or self.state["stop"] or not eligible or time.monotonic() < self._duck_next_check):
                return
        state = query_activity(self._activity_env())
        with self.lock:
            self._duck_next_check = time.monotonic() + DUCK_SWEEP_SEC
        self._apply_remote_activity(state)

    # -- IPC ----------------------------------------------------------------

    def start_ipc(self) -> None:
        """Starts the IPC control server (same transport as local sessions)."""
        self.ipc_server = IPCServer(command_handler=self.handle_ipc_command)
        self.ipc_server.start()

    def handle_ipc_command(self, cmd: str, *, _automatic: bool = False) -> str:
        """Processes IPC commands; pause/resume/stop forward to the remote target."""
        parts = cmd.strip().split()
        if not parts:
            return "ERR: empty command"
        action = parts[0].lower()

        if action == "status":
            return self._ipc_status()

        if action == "pause":
            with self.lock:
                if not _automatic:
                    self._duck_paused = False
                changed = self.state["status"] == "playing"
                if changed:
                    self.state["status"] = "paused"
                    self.paused_at = time.time()
                    self._duck_paused = _automatic
            if changed:
                self._remote_control("pause")
                self._control_ps_session("pause")
                self._control_local_session("pause")
            return self.handle_ipc_command("status")

        if action == "resume":
            with self.lock:
                changed = self.state["status"] == "paused" and (not _automatic or self._duck_paused)
                if changed:
                    self.state["status"] = "playing"
                    self.paused_at = 0.0
                    self._duck_paused = False
            if changed:
                self._remote_control("resume")
                self._control_ps_session("resume")
                self._control_local_session("resume")
            return self.handle_ipc_command("status")

        if action in ("toggle-pause", "toggle_pause"):
            sent = None
            with self.lock:
                self._duck_paused = False
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
                self._control_local_session(sent)
            return self.handle_ipc_command("status")

        if action == "stop":
            self.state["stop"] = True
            with self.lock:
                self.state["status"] = "stopped"
                self._duck_running = False
            self._remote_control("stop")
            self._control_ps_session("stop")
            self._control_local_session("stop")
            return "status=stopped"

        if action in _READ_ONLY_ACTIONS:
            # Byte-compatible with the local AudioSession replies. When a
            # fallback session carries playback, its clock is the truth, so
            # the carrier answers; a live winhost stream answers from this
            # session's own pump-advanced counters.
            carrier = self._carrier_session()
            if carrier is not None and hasattr(carrier, "handle_ipc_command"):
                return carrier.handle_ipc_command(cmd)
            return self._read_only_ipc(action)

        # Navigation (seek/rewind/forward/next-*/prev-*) stays unsupported:
        # PCM already streamed to the remote device cannot be un-played, so
        # remote position jumps are future work (they need a re-stream).
        return (
            f"ERR: command '{action}' is not supported for {self.target} playback "
            "(supported: status, pause, resume, toggle-pause, stop, "
            "highlight, scroll-info, sentence, paragraph)"
        )

    def _ipc_status(self) -> str:
        pos, total = self._pos_total_sec()
        with self.lock:
            sent = self.boundaries.get_sentence_at(pos)
            sent_clean = sent.text.replace("\n", " ").strip() if sent else ""
            sent_idx = sent.index if sent else -1
            para = self.boundaries.get_paragraph_at(pos)
            para_idx = para.index if para else -1
            return (
                f"status={self.state['status']} pos={pos:.2f} total={total:.2f} "
                f"sent_idx={sent_idx} para_idx={para_idx} sentence={sent_clean} "
                f"provider={self.provider} voice={self.voice}"
            )

    def _carrier_session(self):
        """The fallback session actually carrying playback, if any.

        A "windows" run that fell back to the local device reports through
        the local AudioSession; a "winhost" run that fell back to wsl-ps
        reports through the persistent PowershellSession.
        """
        local = getattr(self, "_local_session", None)
        if local is not None:
            return local
        return self._ps_session

    def _pos_total_sec(self):
        """(pos, total) in seconds from whichever clock carries playback.

        The carrier's counters win once a fallback session owns playback;
        otherwise this session's own counters (advanced by the winhost
        pump as chunks are handed to the socket). Carriers without a
        playback clock (minimal test stubs) fall back to this session.
        """
        src = self._carrier_session() or self
        if not hasattr(src, "current_frame"):
            src = self
        pos_getter = getattr(src, "pos_frames", None)
        if pos_getter is not None:
            pos_frames = pos_getter()
        else:
            with src.lock:
                pos_frames = src.current_frame
        with src.lock:
            total_frames = src.total_frames
            rate = src.sample_rate
        pos = (pos_frames / float(rate)) if rate else 0.0
        total = (total_frames / float(rate)) if rate else 0.0
        return pos, total

    def _pos_sec_locked(self) -> float:
        """This session's playback position in seconds; caller holds self.lock."""
        return (self.current_frame / float(self.sample_rate)) if self.sample_rate else 0.0

    def _read_only_ipc(self, action: str) -> str:
        """Answers the read-only karaoke family from this session's counters.

        Byte-compatible with AudioSession.handle_ipc_command; used while a
        live winhost stream carries playback (the pump loop advances
        current_frame as chunks are handed to the socket).
        """
        if action in ("highlight", "current-highlight"):
            with self.lock:
                pos = self._pos_sec_locked()
                return self.boundaries.format_highlighted_sentence(pos, ansi=True, bionic=self.bionic)

        if action in ("scroll-info", "autoscroll-info", "autoscroll"):
            with self.lock:
                pos = self._pos_sec_locked()
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

        if action in ("sentence", "current-sentence", "current_sentence"):
            with self.lock:
                pos = self._pos_sec_locked()
                cur_sent = self.boundaries.get_sentence_at(pos)
                if cur_sent:
                    text_clean = cur_sent.text.replace("\n", " ").strip()
                    return f"sent_idx={cur_sent.index} start={cur_sent.start_sec:.2f} end={cur_sent.end_sec:.2f} text={text_clean}"
                return "sent_idx=-1 text="

        if action in ("paragraph", "current-paragraph", "current_paragraph"):
            with self.lock:
                pos = self._pos_sec_locked()
                cur_p = self.boundaries.get_paragraph_at(pos)
                if cur_p:
                    text_clean = cur_p.text.replace("\n", " ").strip()
                    if len(text_clean) > 80:
                        text_clean = text_clean[:77] + "..."
                    return f"para_idx={cur_p.index} start={cur_p.start_sec:.2f} end={cur_p.end_sec:.2f} text={text_clean}"
                return "para_idx=-1 text="

        return (
            f"ERR: command '{action}' is not supported for {self.target} playback "
            "(supported: status, pause, resume, toggle-pause, stop, "
            "highlight, scroll-info, sentence, paragraph)"
        )

    def _remote_control(self, cmd: str) -> None:
        """Forwards a control command to the winhost server; a dead host is a no-op."""
        if not self._remote_live:
            return
        try:
            env = self.env if self._duck_policy == "ignore" else self._activity_env()
            send_control(cmd, env=env)
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

    def _control_local_session(self, cmd: str) -> None:
        """Forwards a control command to the fallback local session, if any."""
        local = self._local_session
        if local is None:
            return
        local.handle_ipc_command(cmd)

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
        self._ensure_document_boundaries_locked()

    def _ensure_document_boundaries_locked(self) -> None:
        """Fills an empty boundary map from the carrier document once audio duration is known.

        Safety net for runs whose map was never populated upstream (carrier
        built without boundaries and no caller estimated them): the first
        moment both the document text and a real audio duration exist, the
        shared map is filled IN PLACE — mirroring cli.py's streaming
        merge_group — so every session reading it (carrier, fallback local
        device, PowerShell) serves real sentence data. Runs that already
        carry sentence boundaries (native or per-segment merged) are left
        untouched.
        """
        if self.boundaries.sentences or not self.document_text.strip():
            return
        total = self.state.get("total", 0.0)
        if total <= 0.0:
            return
        estimated = estimate_boundaries_from_text(self.document_text, total)
        self.boundaries.sentences.extend(estimated.sentences)
        self.boundaries.words.extend(estimated.words)
        self.boundaries.paragraphs.extend(estimated.paragraphs)

    def prepare_pcm(self, decoded) -> None:
        """Preloads the playback buffer and opens the remote transport before streaming starts."""
        with self.lock:
            self._load_buffer_locked(decoded)
        if not self._ensure_transport(self.sample_rate, self.nchannels, self.bytes_per_sample):
            self._fallback_session().prepare_pcm(decoded)

    def append_pcm(self, decoded) -> bool:
        """Appends a decoded PCM segment (streaming mode); mirrors the local mismatch rule.

        Returns True when the segment was accepted. In PowerShell mode each
        accepted segment is written to the persistent PowerShell process,
        which plays groups back-to-back. After a "windows" local fallback
        each accepted segment is appended to the local session buffer.
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
            self._ensure_document_boundaries_locked()
        if self._ps_mode:
            try:
                return self._ensure_ps_session().append_pcm(decoded)
            except RuntimeError as e:
                print(f"Stream: PowerShell playback failed: {e}", file=sys.stderr)
                return False
        if self._local_session is not None:
            return self._local_session.append_pcm(decoded)
        return True

    def play(self, decoded) -> None:
        """Plays the buffered PCM through the remote target; blocks until playback completes."""
        with self.lock:
            if not self._buffer_loaded:
                self._load_buffer_locked(decoded)
            self.state["status"] = "playing"

        if not self._ensure_transport(self.sample_rate, self.nchannels, self.bytes_per_sample):
            self._fallback_session().play(decoded)
            return

        stream = self._stream
        self._duck_running = True
        if self._duck_policy != "ignore":
            self._apply_remote_activity(self._duck_state)
            self._check_remote_ducking()
        failed = False
        try:
            while not self.state["stop"]:
                self._check_remote_ducking()
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
                    if self._duck_policy == "ignore":
                        stream.finish()
                    else:
                        stream.finish(on_wait=self._check_remote_ducking)
                except Exception:
                    pass
            if self._stream is stream:
                self._stream = None
            with self.lock:
                self.state["status"] = "stopped"
                self._duck_running = False
                self._duck_paused = False
                self._remote_live = False
                if "comm_active" in self.state:
                    self.state["comm_active"] = False

    def stop(self) -> None:
        """Stops the session, signals the remote target, and cleans up the IPC server.

        An explicit stop (state["stop"] set before this call, e.g. by the IPC
        handler) hard-stops the PowerShell session and cuts mid-group audio;
        a natural end of run drains the tail instead so the last group is not
        clipped. The fallback local session (if any) is always flagged to
        stop; its playback loop exits promptly on the flag.
        """
        user_stopped = bool(self.state.get("stop"))
        self.state["stop"] = True
        self.state["status"] = "stopped"
        self._duck_running = False
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
        local = self._local_session
        if local is not None:
            try:
                local.stop()
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

        Returns True when a live TCP stream carries the audio, False when
        the run is in PowerShell mode or on the fallback local device. On
        an unreachable winhost server, warns once and switches the whole
        run to per-group PowerShell playback ("winhost") or to the local
        device ("windows").
        """
        if self._ps_mode or self._local_mode:
            return False
        if self._stream is not None:
            return True
        options = {}
        if self._duck_policy != "ignore":
            activity = query_activity(self.env)
            self._apply_remote_activity(activity)
            self._duck_next_check = time.monotonic() + DUCK_SWEEP_SEC
            options["gain_db"] = self._duck_gain_db
            options["paused"] = self._duck_policy == "pause" and (
                activity["communication_active"] or activity["capture_active"]
            )
        try:
            self._stream = open_stream(rate, channels, sample_width, env=self.env, **options)
        except WinhostUnavailable as e:
            if self.target == WINDOWS_TARGET:
                self._enter_local_fallback(e)
            else:
                self._enter_ps_fallback(e)
            return False
        self._remote_live = True
        return True

    def _fallback_session(self):
        """Returns the session that carries the run once the remote target failed.

        "wsl-ps" (and "winhost" after the ps fallback) routes through the
        persistent PowerShell session; "windows" routes through the local
        AudioSession device.
        """
        if self._ps_mode:
            return self._ensure_ps_session()
        return self._ensure_local_session()

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

    def _enter_local_fallback(self, error: WinhostUnavailable) -> None:
        """Switches the run to the local audio device ("windows" target only).

        No PowerShell machinery is touched: this target never spawns
        powershell.exe, even when the winhost server is absent.
        """
        if not self._fallback_warned:
            print(
                f"winhost unreachable at {error.location}; falling back to local playback",
                file=sys.stderr,
            )
            self._fallback_warned = True
        self._local_mode = True
        self._ensure_local_session()

    # -- fallback sessions ----------------------------------------------------

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
                provider=self.provider,
                voice=self.voice,
                env=self.env,
            )
        return self._ps_session

    def _ensure_local_session(self) -> AudioSession:
        """Lazily creates the local AudioSession used after the "windows" fallback.

        Mirrors the local path cli.py builds: same label, rewind, boundary,
        and terminal-visual settings, so highlight/zen/autoscroll render
        again on the fallback device.
        """
        if self._local_session is None:
            self._local_session = AudioSession(
                label=self.label,
                auto_rewind_sec=self.auto_rewind_sec,
                boundaries=self.boundaries,
                highlight=self.highlight,
                autoscroll=self.autoscroll,
                bionic=self.bionic,
                zen=self.zen,
            )
        return self._local_session
