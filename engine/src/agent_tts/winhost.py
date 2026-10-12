"""Windows host audio server (winhost protocol v1).

Runs on Windows via ``agent-tts --winhost``: accepts TCP connections carrying
raw s16 PCM with a one-line JSON header and plays them natively through
miniaudio (WASAPI). One playback session is active at a time; a new PLAY
preempts the current one.

Wire format (v1):
- PLAY:    ``{"v":1,"cmd":"play","rate":24000,"channels":1,"format":"s16"}`` + LF,
           followed by raw PCM bytes until client EOF (half-close).
- CONTROL: ``{"v":1,"cmd":"pause"|"resume"|"stop"}`` + LF, then close.
- ACTIVITY: ``{"v":1,"cmd":"activity"}`` + LF; one JSON reply then close.
- Optional PLAY fields: ``gain_db`` (signed attenuation, default 0 dB) and
  ``paused`` (initial gate, avoiding a pre-play control registration race).
- GAIN: ``{"v":1,"cmd":"gain","gain_db":0}`` + LF changes the active
  stream's software gain; 0 dB restores unity. No OS volume is changed.
"""

from array import array
import json
import math
import socket
import sys
import threading

import miniaudio

from agent_tts import ducking
from agent_tts.playback_target import winhost_bind_host, winhost_port

MAX_HEADER_BYTES = 8192
HEADER_READ_TIMEOUT_SEC = 2.0
CLIENT_RECV_BUFFER = 65536
PROTOCOL_VERSION = 1


def _probe_wasapi_endpoints() -> None:
    """Windows-only, read-only IMMDeviceEnumerator probe (M-01: MANUAL).

    Enumerating active endpoints is NOT evidence of active capture or a call.
    Session classification is deliberately not implemented blind here.
    """
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                    ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    def guid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    def call(obj, index, args, *values):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(table[index])
        return method(obj, *values)

    def checked(hr):
        if hr < 0:
            raise OSError("WASAPI enumeration failed")

    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    initialized = ole.CoInitializeEx(None, 0)
    checked(initialized)
    enumerator = ctypes.c_void_p()
    collection = ctypes.c_void_p()
    try:
        clsid = guid("bcde0395-e52f-467c-8e3d-c4579291692e")
        iid = guid("a95664d2-9614-4f35-a746-de8db63617e6")
        checked(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator)))
        # IMMDeviceEnumerator::EnumAudioEndpoints(eAll, DEVICE_STATE_ACTIVE).
        checked(call(enumerator, 3, [ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
                     2, 1, ctypes.byref(collection)))
        count = ctypes.c_uint32()
        checked(call(collection, 3, [ctypes.POINTER(ctypes.c_uint32)], ctypes.byref(count)))
    finally:
        try:
            for obj in (collection, enumerator):
                if obj:
                    call(obj, 2, [])  # IUnknown::Release; never mutate device settings.
        finally:
            ole.CoUninitialize()


def detect_host_activity() -> dict:
    """Fail-open host activity; real WASAPI classification needs M-01 validation.

    Non-Windows never loads ctypes/COM. Even a successful endpoint probe cannot
    classify communication/capture streams, so this minimal backend returns
    inactive rather than inventing positives from device availability.
    """
    try:
        if sys.platform != "win32":
            raise NotImplementedError("WASAPI is Windows-only")
        _probe_wasapi_endpoints()
        raise NotImplementedError("WASAPI session classification requires manual host validation")
    except Exception:
        ducking._warn_once("winhost_detection", "winhost activity unavailable; ducking ignored (M-01 host validation required)")
        return {"communication_active": False, "capture_active": False}


def default_device_factory(rate, channels):
    """Opens a native playback device (WASAPI on Windows) for the given stream format."""
    return miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=channels,
        sample_rate=rate,
    )


class PlayStream:
    """One active playback session fed from a client socket.

    A reader thread pulls PCM from the socket into a bounded-by-RAM buffer
    while a miniaudio callback generator drains it at real-time pace. Pause
    is a gate in the generator (silence), so no vendor pause API is needed.
    """

    def __init__(self, conn, header, device_factory, initial_data=b""):
        self.conn = conn
        self.rate = int(header["rate"])
        self.channels = int(header["channels"])
        self.frame_size = 2 * self.channels  # s16 little-endian
        self.device_factory = device_factory
        self.buffer = bytearray(initial_data)
        self.buffer_lock = threading.Lock()
        self.gain = 1.0
        self.set_gain_db(header.get("gain_db", 0.0))
        self.eof = False
        self.paused = header.get("paused") is True
        self.stopped = False
        self.finished = threading.Event()
        self.device = None

    def run(self):
        """Starts reading and playback; blocks until the stream finishes or is stopped."""
        self.conn.settimeout(None)
        reader = threading.Thread(target=self._read_loop, daemon=True)
        reader.start()
        try:
            self.device = self.device_factory(self.rate, self.channels)
        except Exception as e:
            print(
                f"winhost: failed to open playback device ({self.rate}Hz/{self.channels}ch): {e}",
                file=sys.stderr,
            )
            self.stopped = True
            self.finished.set()
            return
        gen = self._guarded_generator()
        next(gen)  # Prime the generator for the miniaudio callback.
        self.device.start(gen)
        self.finished.wait()

    def close(self):
        if self.device is not None:
            try:
                self.device.stop()
            except Exception:
                pass
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
        try:
            self.conn.close()
        except OSError:
            pass

    def pause(self):
        self.paused = True

    def set_gain_db(self, db):
        """Signed dB attenuation; malformed/amplifying gains fail open to unity."""
        try:
            if isinstance(db, bool) or not isinstance(db, (int, float)):
                raise ValueError("gain must be numeric")
            db = float(db)
            if not math.isfinite(db) or db > 0:
                raise ValueError("gain must be finite attenuation")
        except (TypeError, ValueError, OverflowError):
            ducking._warn_once("winhost_gain", "invalid winhost gain_db; using unity gain")
            db = 0.0
        with self.buffer_lock:
            self.gain = ducking.gain_linear(db)

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True

    def _read_loop(self):
        try:
            while True:
                chunk = self.conn.recv(CLIENT_RECV_BUFFER)
                if not chunk:
                    break
                with self.buffer_lock:
                    self.buffer.extend(chunk)
        except OSError:
            pass
        finally:
            with self.buffer_lock:
                self.eof = True

    def _pcm_generator(self):
        num_frames = yield b""
        while not self.stopped:
            if self.paused:
                num_frames = yield bytes(num_frames * self.frame_size)
                continue
            with self.buffer_lock:
                frames = min(num_frames, len(self.buffer) // self.frame_size)
                nbytes = frames * self.frame_size
                chunk = bytes(self.buffer[:nbytes])
                del self.buffer[:nbytes]
                eof = self.eof
                gain = self.gain
            if frames:
                if gain != 1.0:
                    samples = array("h", chunk)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    for i, sample in enumerate(samples):
                        samples[i] = int(sample * gain)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    chunk = samples.tobytes()
                num_frames = yield chunk
                continue
            if eof:
                break
            # Buffer underrun while the client is still sending: hold silence.
            num_frames = yield bytes(num_frames * self.frame_size)
        yield b""

    def _guarded_generator(self):
        try:
            yield from self._pcm_generator()
        finally:
            self.finished.set()


class WinhostServer:
    """Threaded TCP server playing PCM streams on the Windows host."""

    def __init__(self, host=None, port=None, device_factory=None):
        self.host = winhost_bind_host() if host is None else host
        self.port = winhost_port() if port is None else port
        self.device_factory = device_factory or default_device_factory
        self.server_sock = None
        self._active = None
        self._active_lock = threading.Lock()
        # Introspection hook (used by tests): last PLAY header received.
        self.last_header = None

    def bind(self):
        """Binds the listening socket and returns the resolved (host, port)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(5)
        self.server_sock = sock
        self.host, self.port = sock.getsockname()
        return self.host, self.port

    def shutdown(self):
        if self.server_sock is not None:
            try:
                self.server_sock.close()
            except OSError:
                pass
            self.server_sock = None
        with self._active_lock:
            active = self._active
        if active is not None:
            active.stop()

    def serve_forever(self):
        """Accepts client connections until the socket closes or Ctrl+C."""
        if self.server_sock is None:
            self.bind()
        lan_note = ""
        if self.host in ("0.0.0.0", "::"):
            lan_note = " (bound to all interfaces: the port is open on the LAN)"
        print(f"winhost: listening on {self.host}:{self.port}{lan_note}", file=sys.stderr)
        print("winhost: play from WSL with agent-tts --playback winhost", file=sys.stderr)
        try:
            while True:
                try:
                    conn, addr = self.server_sock.accept()
                except OSError:
                    break
                threading.Thread(target=self._handle_connection, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def _handle_connection(self, conn, addr):
        peer = addr[0] if isinstance(addr, tuple) else str(addr)
        try:
            header, leftover = self._read_header(conn)
        except Exception as e:
            print(f"winhost: bad header from {peer}: {e}", file=sys.stderr)
            _close_quietly(conn)
            return
        if header is None:
            _close_quietly(conn)
            return
        cmd = header.get("cmd")
        if cmd == "play":
            self._handle_play(conn, header, leftover)
        elif cmd in ("pause", "resume", "stop", "gain"):
            self._apply_control(cmd, header)
            _close_quietly(conn)
        elif cmd == "activity":
            try:
                state = detect_host_activity()
                conn.sendall((json.dumps({"ok": True, **state}) + "\n").encode("utf-8"))
            finally:
                _close_quietly(conn)
        else:
            print(f"winhost: unknown command {cmd!r} from {peer}", file=sys.stderr)
            _close_quietly(conn)

    def _read_header(self, conn):
        """Reads and validates the one-line JSON header.

        Returns (header, leftover) where leftover carries any payload bytes
        received beyond the header line (TCP may coalesce them into the same
        segment); leftover must be forwarded to the playback stream.
        Returns (None, b"") on clean EOF before any header.
        """
        conn.settimeout(HEADER_READ_TIMEOUT_SEC)
        buf = b""
        while b"\n" not in buf:
            if len(buf) > MAX_HEADER_BYTES:
                raise ValueError("header too large")
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        line, _, leftover = buf.partition(b"\n")
        line = line.strip()
        if not line:
            return None, b""
        header = json.loads(line.decode("utf-8"))
        if not isinstance(header, dict):
            raise ValueError("header must be a JSON object")
        if header.get("v") != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version {header.get('v')!r}")
        if header.get("cmd") == "play":
            rate = header.get("rate")
            channels = header.get("channels")
            if not isinstance(rate, int) or isinstance(rate, bool) or not (8000 <= rate <= 192000):
                raise ValueError(f"invalid sample rate {rate!r}")
            if not isinstance(channels, int) or isinstance(channels, bool) or not (1 <= channels <= 2):
                raise ValueError(f"invalid channel count {channels!r}")
            if header.get("format") != "s16":
                raise ValueError(f"unsupported format {header.get('format')!r} (only s16)")
        return header, leftover

    def _handle_play(self, conn, header, leftover=b""):
        self.last_header = header
        stream = PlayStream(conn, header, self.device_factory, initial_data=leftover)
        self._activate(stream)
        try:
            stream.run()
        finally:
            stream.close()
            self._deactivate(stream)

    def _activate(self, stream):
        """Registers a new active session, preempting (stopping) any current one."""
        with self._active_lock:
            old = self._active
            self._active = stream
        if old is not None and old is not stream:
            old.stop()
            old.finished.wait(timeout=2.0)

    def _deactivate(self, stream):
        with self._active_lock:
            if self._active is stream:
                self._active = None

    def _apply_control(self, cmd, header=None):
        with self._active_lock:
            stream = self._active
        if stream is None:
            return  # Control before any PLAY (or after it finished): no-op.
        if cmd == "pause":
            stream.pause()
        elif cmd == "resume":
            stream.resume()
        elif cmd == "stop":
            stream.stop()
        elif cmd == "gain":
            stream.set_gain_db((header or {}).get("gain_db", 0.0))


def _close_quietly(conn):
    try:
        conn.close()
    except OSError:
        pass


def run_winhost_server(host=None, port=None, device_factory=None):
    """Runs the winhost server in the foreground (blocks)."""
    server = WinhostServer(host=host, port=port, device_factory=device_factory)
    server.serve_forever()
