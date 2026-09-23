"""Zero-install playback under WSL: stream WAV groups to one persistent PowerShell process.

This is the ``wsl-ps`` playback target and the automatic fallback when the
winhost server is unreachable. :class:`PowershellSession` lazily spawns ONE
``powershell.exe`` per run (found on PATH via WSL interop) and feeds it
length-prefixed WAV groups over stdin; an embedded loop script plays each
group synchronously through ``System.Media.SoundPlayer``. Pause/resume/stop
are pure pipe flow control, so sentence groups play near-gaplessly instead of
paying a process spawn per group.
"""

import os
import shutil
import struct
import subprocess
import sys
import threading
import time

from agent_tts.boundaries import BoundaryMap
from agent_tts.ipc import IPCServer
from agent_tts.playback_target import under_wsl
from agent_tts.wav import WAV_HEADER_FMT, pcm_to_wav

# Persistent reader loop (PowerShell 5.1 compatible, no PS7-only syntax).
# Framing contract: per group, Python writes struct.pack("<Q", len(wav)) +
# wav (little-endian). [Console]::OpenStandardInput() bypasses the PowerShell
# text pipeline so binary framing survives untouched, and
# [BitConverter]::ToInt64 is little-endian on x64 Windows, matching the
# Python side. The (,$w) comma trick wraps the byte[] as the constructor
# argument, as required by Windows PowerShell 5.1. EOF (or a non-positive
# length) ends the loop, so closing stdin drains playback and exits cleanly.
#
# The script is a single line with NO double quotes and NO newlines, and is
# passed via -Command (never -EncodedCommand): on real WSL interop hardware,
# processes launched with -EncodedCommand get silently terminated (rc 0) at
# random moments (observed 0.07s-3.3s after spawn) by the Windows security
# stack, while plain -Command invocations run reliably. Quote-free + newline
# free keeps the argv -> Windows command-line handoff safe.
_PS_STREAM_SCRIPT = (
    "function ReadExact([System.IO.Stream]$s,[byte[]]$b,[int]$n){$o=0;"
    "while($o -lt $n){$r=$s.Read($b,$o,$n-$o);if($r -le 0){break};$o+=$r};$o};"
    "$in=[Console]::OpenStandardInput();"
    "$h=New-Object byte[] 8;"
    "while($true){"
    "if((ReadExact $in $h 8) -lt 8){break};"
    "$len=[BitConverter]::ToInt64($h,0);"
    "if($len -le 0){break};"
    "$w=New-Object byte[] $len;"
    "if((ReadExact $in $w $len) -lt $len){break};"
    "$m=New-Object System.IO.MemoryStream(,$w);"
    "$p=New-Object System.Media.SoundPlayer($m);"
    "$p.PlaySync();"
    "$p.Dispose();$m.Dispose()"
    "}"
)
assert '"' not in _PS_STREAM_SCRIPT and "\n" not in _PS_STREAM_SCRIPT

# 8-byte little-endian length prefix per group (must match _PS_STREAM_SCRIPT).
_GROUP_HEADER = struct.Struct("<Q")

# PCM frames of a framed group derive from its WAV container length.
_WAV_HEADER_BYTES = struct.calcsize(WAV_HEADER_FMT)

# Grace period before a hard stop escalates to kill().
_STOP_GRACE_SEC = 0.3
# Bounded so an IPC stop responds within the 1s IPC client timeout: the kill
# itself cuts audio immediately; this only bounds the corpse reaping.
_STOP_KILL_WAIT_SEC = 0.5
# Generous drain budget for finish(): the script must play out the current
# group (and anything still buffered in the pipe) before exiting on EOF.
_FINISH_TIMEOUT_SEC = 60.0
_REAP_TIMEOUT_SEC = 2.0


def is_wsl_ps_available(env=None) -> bool:
    """True when powershell.exe is reachable and this process runs under WSL."""
    env = os.environ if env is None else env
    if shutil.which("powershell.exe") is None:
        return False
    return under_wsl(env)




class PowershellSession:
    """Playback session that streams length-prefixed WAV groups to ONE persistent powershell.exe.

    Duck-types the AudioSession surface used by cli.py (state dict, lock,
    boundaries, prepare_pcm/append_pcm/play/stop/start_ipc/handle_ipc_command)
    so it is a drop-in replacement for RemoteAudioSession in wsl-ps mode.

    Behavior:

    - The process is spawned lazily on the first group; construction is cheap.
    - Each accepted group is framed as ``pack("<Q", len(wav)) + wav`` and
      flushed; the embedded script reads a group as soon as the previous
      PlaySync finishes, which paces the producer thread via pipe
      backpressure (near-gapless group playback).
    - pause()/resume() gate writes with a threading.Event: a paused producer
      blocks on the gate (natural backpressure), so pause granularity is one
      group. Both return immediately (safe from the IPC thread).
    - stop() hard-stops when state["stop"] was set explicitly (IPC stop or an
      error unwind): unblock the gate, close stdin, short grace, then kill()
      — cutting mid-group audio; the exit code is swallowed. At a natural end
      of run stop() drains instead (finish()) so the last group is not
      clipped.
    - finish() drains: unblock the gate, close stdin, and wait for the script
      to play everything out and exit on EOF. A non-zero exit raises
      RuntimeError with the standard English message; a timed-out drain is
      killed and swallowed like a stop.
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
        env=None,
    ):
        self.label = label
        self.auto_rewind_sec = auto_rewind_sec
        self.boundaries = boundaries or BoundaryMap()
        self.highlight = highlight
        self.autoscroll = autoscroll
        self.bionic = bionic
        self.zen = zen
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
        # Position clock for IPC karaoke queries. SoundPlayer reports nothing
        # back, so pos derives from the bytes handed to the pipe: pipe
        # backpressure paces the writer to within ~one group of real
        # playback. _sent_frames counts accepted frames (the ramp cap); the
        # ramp interpolates inside the group handed off last, frozen while
        # paused (_ramp_time None).
        self._sent_frames = 0
        self._ramp_time = None
        self._buffer_loaded = False
        self.lock = threading.Lock()
        self.ipc_server = None
        self._proc = None
        self._dead = False
        # Set = writes allowed; cleared = writers block (pause backpressure).
        self._gate = threading.Event()
        self._gate.set()
        # Serializes group writes across threads (play vs append_pcm).
        self._write_lock = threading.Lock()

    # -- IPC ----------------------------------------------------------------

    def start_ipc(self) -> None:
        """Starts the IPC control server (same transport as local sessions)."""
        self.ipc_server = IPCServer(command_handler=self.handle_ipc_command)
        self.ipc_server.start()

    def handle_ipc_command(self, cmd: str) -> str:
        """Processes IPC commands; pause/resume/stop map to pipe flow control."""
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
                self.pause()
            return self.handle_ipc_command("status")

        if action == "resume":
            with self.lock:
                changed = self.state["status"] == "paused"
                if changed:
                    self.state["status"] = "playing"
                    self.paused_at = 0.0
            if changed:
                self.resume()
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
            if sent == "pause":
                self.pause()
            elif sent == "resume":
                self.resume()
            return self.handle_ipc_command("status")

        if action == "stop":
            self.state["stop"] = True
            with self.lock:
                self.state["status"] = "stopped"
            self._hard_stop()
            return "status=stopped"

        # Read-only karaoke family, byte-compatible with the local
        # AudioSession replies; position comes from the pipe-handoff ramp.
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

        # Navigation (seek/rewind/forward/next-*/prev-*) stays unsupported:
        # groups already handed to the pipe cannot be un-played, so remote
        # position jumps are future work (they need a drain+replay cycle).
        return (
            f"ERR: command '{action}' is not supported for wsl-ps playback "
            "(supported: status, pause, resume, toggle-pause, stop, "
            "highlight, scroll-info, sentence, paragraph)"
        )

    def _ipc_status(self) -> str:
        with self.lock:
            pos = self._pos_sec_locked()
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

    def _pos_sec_locked(self) -> float:
        """Playback position in seconds; caller must hold self.lock."""
        return (self._pos_frames_locked() / float(self.sample_rate)) if self.sample_rate else 0.0

    def _pos_frames_locked(self) -> int:
        """Playback position in frames; caller must hold self.lock.

        Frozen (ramp unset) returns the last frozen frame count; otherwise
        the frame count advanced by wall time since the last pipe handoff,
        capped at the frames actually handed to the pipe.
        """
        if self._ramp_time is None or not self.sample_rate:
            return self.current_frame
        elapsed = time.monotonic() - self._ramp_time
        return min(
            self.current_frame + int(elapsed * self.sample_rate),
            self._sent_frames,
        )

    def pos_frames(self) -> int:
        """Thread-safe playback position in frames (IPC karaoke consumers)."""
        with self.lock:
            return self._pos_frames_locked()

    # -- flow control ---------------------------------------------------------

    def pause(self) -> None:
        """Blocks subsequent group writes until resume(); returns immediately.

        The position ramp freezes at the current estimate; the group already
        in flight still plays out (pause granularity is one group), but the
        cap keeps the estimate from running past its bytes.
        """
        with self.lock:
            self.current_frame = self._pos_frames_locked()
            self._ramp_time = None
        self._gate.clear()

    def resume(self) -> None:
        """Unblocks group writes after a pause; re-arms the position ramp."""
        with self.lock:
            if self._ramp_time is None:
                self._ramp_time = time.monotonic()
        self._gate.set()

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
        self._sent_frames = 0
        self._ramp_time = None
        self.state["total"] = (self.total_frames / float(self.sample_rate)) if self.sample_rate else 0.0
        self._buffer_loaded = True

    def _group_wav(self, decoded) -> bytes:
        """Wraps one decoded segment's PCM in a WAV container for framing."""
        return pcm_to_wav(
            decoded.samples.tobytes(),
            decoded.sample_rate,
            decoded.nchannels,
            getattr(decoded, "sample_width", 2),
        )

    def prepare_pcm(self, decoded) -> None:
        """Preloads the buffer and starts the persistent process with the first group."""
        with self.lock:
            self._load_buffer_locked(decoded)
        self._write_group(self._group_wav(decoded))

    def append_pcm(self, decoded) -> bool:
        """Appends a decoded PCM group (streaming mode); mirrors the local mismatch rule.

        Returns True when the segment was accepted and written to the
        persistent process. Segments whose sample rate, channel count, or
        sample width differ from the session format are skipped (resampling
        is out of scope); a warning is logged to stderr.
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
        self._write_group(self._group_wav(decoded))
        return True

    def play(self, decoded) -> None:
        """Plays decoded PCM through the persistent process.

        Streaming path: prepare_pcm() already wrote the first group, so this
        only flips status and lets the producer feed the rest. One-shot path:
        the whole buffer is written as one framed group, then stdin is closed
        and the process is drained so playback completes before returning.
        """
        with self.lock:
            first_group = not self._buffer_loaded
            if first_group:
                self._load_buffer_locked(decoded)
            self.state["status"] = "playing"
        try:
            if first_group:
                self._write_group(self._group_wav(decoded))
                self.finish()
                with self.lock:
                    self.state["status"] = "stopped"
        except KeyboardInterrupt:
            self._hard_stop()
            raise

    def stop(self) -> None:
        """Stops the session and cleans up the IPC server.

        An explicit stop (state["stop"] set by the IPC handler or an error
        unwind) hard-stops and cuts mid-group audio; a natural end of run
        drains the tail instead so the last group is not clipped. state
        ["stop"] is set before draining so a blocked producer thread unwinds
        instead of failing its next write. Never raises: cleanup must not
        mask the run's result.
        """
        user_stopped = bool(self.state.get("stop"))
        self.state["stop"] = True
        if user_stopped:
            self._hard_stop()
        else:
            try:
                self.finish()
            except Exception:
                pass
        with self.lock:
            self.state["status"] = "stopped"
        if self.ipc_server:
            self.ipc_server.stop()
            self.ipc_server = None

    def finish(self) -> None:
        """Drains playback: lets the in-flight group write complete, closes stdin, waits for EOF.

        state["stop"] is set first so the producer pipeline unwinds after its
        current group; the pause gate is then unblocked and stdin is closed
        only once no group write is in flight (bounded by one group of pipe
        backpressure). Everything written plays out before this returns. A
        non-zero exit raises RuntimeError with the standard English message;
        a timed-out drain is killed and swallowed like a stop.
        """
        self.state["stop"] = True  # producer unwinds after its current group
        proc = self._proc
        if proc is None:
            return
        with self._write_lock:  # let any in-flight group write complete first
            self._gate.set()  # unblock a paused writer before closing the pipe
            self._close_stdin(proc)
            self._dead = True
        try:
            rc = proc.wait(timeout=_FINISH_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            self._kill_and_reap(proc)
            return
        if rc != 0:
            raise RuntimeError(self._exit_error_message(proc, rc))

    # -- process plumbing -----------------------------------------------------

    def _ensure_process(self) -> None:
        """Lazily spawns the persistent PowerShell process on the first group."""
        if self._proc is not None:
            return
        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PS_STREAM_SCRIPT,
        ]
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def _write_group(self, wav: bytes) -> None:
        """Frames one WAV group and writes it to the persistent process.

        Blocks on the pause gate first, spawns the process lazily, then
        writes header+wav in one call and flushes. Raises RuntimeError when
        the process is gone or the pipe broke.
        """
        self._gate.wait()  # pause backpressure; resume/stop/finish set the gate
        with self._write_lock:
            # Re-check under the lock: stop()/finish() may have closed stdin
            # while this writer waited for the lock.
            if self._dead:
                raise RuntimeError("PowerShell playback process has exited")
            self._ensure_process()
            proc = self._proc
            try:
                if proc.poll() is not None:
                    raise BrokenPipeError("process exited")
                proc.stdin.write(_GROUP_HEADER.pack(len(wav)) + wav)
                proc.stdin.flush()
                # Group handed to the pipe: the blocked write only returns
                # once the reader consumed enough, so playback is at (about)
                # this group's start. Resync the ramp there and raise the
                # cap; backpressure keeps the estimate within ~one group.
                pcm_frames = (len(wav) - _WAV_HEADER_BYTES) // self.frame_size
                with self.lock:
                    self.current_frame = self._sent_frames
                    self._sent_frames += max(0, pcm_frames)
                    self._ramp_time = time.monotonic()
            except (BrokenPipeError, ValueError, OSError) as e:
                raise self._fail_after_write_error(e) from e
            except KeyboardInterrupt:
                self._hard_stop()
                raise

    def _fail_after_write_error(self, exc) -> RuntimeError:
        """Converts a broken group write into the standard English playback error."""
        self._dead = True
        proc = self._proc
        if proc is None:
            return RuntimeError("PowerShell playback failed: no process")
        rc = proc.poll()
        if rc is None:
            # stdin was closed by a concurrent stop()/finish() closer, not by
            # process death; the closer owns reaping the process.
            return RuntimeError("PowerShell playback interrupted (stdin closed)")
        try:
            proc.wait(timeout=_REAP_TIMEOUT_SEC)
        except Exception:
            pass
        if rc == 0:
            # Clean script exit while a straggler write was still in flight.
            return RuntimeError("PowerShell playback interrupted (process exited)")
        return RuntimeError(self._exit_error_message(proc, rc))

    @staticmethod
    def _exit_error_message(proc, rc: int) -> str:
        """Builds the English error for a non-zero exit, with the last stderr line.

        Windows PowerShell 5.1 wraps redirected stderr in CLIXML markup
        (``#< CLIXML`` ... ``</Objs>``); those lines are skipped when picking
        the human-readable tail.
        """
        detail = ""
        try:
            data = proc.stderr.read() if proc.stderr is not None else b""
        except Exception:
            data = b""
        lines = (data or b"").decode("utf-8", errors="ignore").strip().splitlines()
        readable = [
            line
            for line in lines
            if not line.startswith(("#<", "<Objs", "</Objs"))
        ]
        tail = readable[-1] if readable else (lines[-1] if lines else "")
        if tail:
            detail = f": {tail}"
        return f"PowerShell playback failed (exit code {rc}){detail}"

    @staticmethod
    def _close_stdin(proc) -> None:
        """Closes the process stdin; a concurrent in-flight write errors on its own."""
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass

    def _hard_stop(self) -> None:
        """Unconditionally cuts playback: gate, kill, best-effort stdin close, reap.

        kill() comes FIRST: closing stdin before the kill would block on the
        io lock while a backpressured producer write is in flight (paced by
        the current group's playback), delaying the cut by seconds. After
        the kill the writer fails fast with EPIPE, so the close is instant.
        The exit code is swallowed: a stop is not an error.
        """
        self._gate.set()  # unblock a paused writer first
        with self.lock:  # the cut kills audio: freeze the position estimate
            self.current_frame = self._pos_frames_locked()
            self._ramp_time = None
        proc = self._proc
        if proc is None:
            return
        self._dead = True
        try:
            proc.wait(timeout=_STOP_GRACE_SEC)
            self._close_stdin(proc)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        self._close_stdin(proc)
        try:
            proc.wait(timeout=_STOP_KILL_WAIT_SEC)
        except subprocess.TimeoutExpired:
            pass

    def _kill_and_reap(self, proc) -> None:
        """Kills the process and reaps it; swallows all exit codes."""
        self._dead = True
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=_STOP_KILL_WAIT_SEC)
        except Exception:
            pass

    def __enter__(self) -> "PowershellSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.state["stop"] = True
        self.stop()
        return False
