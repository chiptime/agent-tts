"""Framing v2 contract tests: length-prefixed frames on the control channel.

Wire format (BLOQUE 1.3, decision D1 — conscious non-additive break of
RF-AT-04-2 approved before the IPC contract freeze):

    +----------------+-----------------+-----------------------+
    | MAGIC "ATTS"   | VERSION = 0x02  | LENGTH (4B, big-endian)|
    +----------------+-----------------+-----------------------+
    | PAYLOAD (LENGTH bytes, UTF-8 command/reply text)            |
    +--------------------------------------------------------------+

Both directions use the same frame shape. The header is validated
(magic, version, length vs MAX_PAYLOAD) BEFORE any body byte is read,
which structurally closes the truncation/mis-dispatch class of defects
the old newline-probe framing could not.
"""

import socket
import struct
import tempfile
import threading
import time

import pytest

import agent_tts.ipc as ipc
from agent_tts.constants import PING_TIMEOUT_SEC
from agent_tts.daemon import probe_daemon
from agent_tts.ipc import IPCServer
from agent_tts.ipc import (
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    FRAME_VERSION,
    MAX_PAYLOAD,
    READ_CHUNK,
    CommandTooLargeError,
    ProtocolMismatchError,
    encode_frame,
    read_frame,
    send_frame,
)


def _echo_server(sock_file, calls=None):
    """Starts an IPCServer whose handler records and echoes lengths."""
    if calls is None:
        calls = []

    def handler(cmd: str) -> str:
        calls.append(cmd)
        return f"len:{len(cmd)}"

    server = IPCServer(
        command_handler=handler, socket_path=sock_file, require_ownership=False
    )
    server.start()
    time.sleep(0.1)
    return server


def _read_reply_frame(client) -> str:
    """Reads one framed reply from a raw client socket."""
    client.settimeout(10.0)
    return read_frame(client)


# --- Frame layout -----------------------------------------------------------------


def test_frame_layout_is_magic_version_big_endian_length_payload():
    encoded = encode_frame("ping")
    assert encoded == FRAME_MAGIC + bytes([FRAME_VERSION]) + struct.pack(">I", 4) + b"ping"
    assert FRAME_HEADER_SIZE == 9
    assert MAX_PAYLOAD == 16 * 1024 * 1024  # the documented 16 MiB cap is kept


def test_ping_frame_carries_no_newline_byte():
    """Upgrade-window guarantee: a stale line-protocol (v1) daemon reads
    until newline; the v2 ping frame must never hand it one, so the old
    daemon stays silent and the probe classifies it as wedged (kill and
    respawn via the existing RF-AT-04-8 machinery)."""
    assert b"\n" not in encode_frame("ping")


# --- Reader validation (typed errors, pre-body) -------------------------------------


def test_read_frame_rejects_bad_magic_with_actionable_error():
    a, b = socket.socketpair()
    try:
        b.sendall(b"NOPE" + bytes([FRAME_VERSION]) + struct.pack(">I", 1) + b"x")
        with pytest.raises(ProtocolMismatchError) as excinfo:
            read_frame(a)
        assert "restart" in str(excinfo.value).lower()
    finally:
        a.close()
        b.close()


def test_read_frame_rejects_unknown_version_with_actionable_error():
    a, b = socket.socketpair()
    try:
        b.sendall(FRAME_MAGIC + bytes([FRAME_VERSION + 5]) + struct.pack(">I", 1) + b"x")
        with pytest.raises(ProtocolMismatchError):
            read_frame(a)
    finally:
        a.close()
        b.close()


def test_read_frame_rejects_oversize_length_before_reading_the_body():
    """A2''=B1'': an oversize length is rejected from the header alone —
    the reader must never try to buffer (or drain) the announced body."""
    a, b = socket.socketpair()
    try:
        huge = MAX_PAYLOAD + 1
        b.sendall(FRAME_MAGIC + bytes([FRAME_VERSION]) + struct.pack(">I", huge))
        # Deliberately send NO body: a reader that tried to read/drain the
        # announced length would block on recv instead of failing fast.
        started = time.monotonic()
        with pytest.raises(CommandTooLargeError):
            read_frame(a)
        assert time.monotonic() - started < 2.0  # no gigabyte drain attempt
    finally:
        a.close()
        b.close()


# --- A1'': slow/chunked senders arrive intact ---------------------------------------


def test_a1_double_prime_slow_chunked_sender_payload_arrives_intact():
    """A1'': the old framing probed ~1 s past the cap; a sender pausing
    mid-payload during that window was truncated and mis-dispatched.
    Exact-length reads make truncation impossible: a sender that keeps
    making progress completes whenever it finishes. The pause below
    (1.5 s) exceeds both the old 1 s probe window and the old 1 s
    connection timeout."""
    calls = []
    with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
        sock_file = tmp.name
    server = _echo_server(sock_file, calls)
    try:
        payload = "play " + "x" * (1024 * 1024)  # 1 MiB body
        body = payload.encode("utf-8")
        header = FRAME_MAGIC + bytes([FRAME_VERSION]) + struct.pack(">I", len(body))

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(30.0)
        client.connect(sock_file)
        try:
            client.sendall(header[:4])  # header itself arrives split
            time.sleep(0.1)
            client.sendall(header[4:])
            half = len(body) // 2
            for start in range(0, half, 32 * 1024):
                client.sendall(body[start : start + 32 * 1024])
                time.sleep(0.01)
            time.sleep(1.5)  # mid-payload pause: old framing truncated here
            try:
                for start in range(half, len(body), 32 * 1024):
                    client.sendall(body[start : start + 32 * 1024])
                    time.sleep(0.01)
            except BrokenPipeError:
                # The server consumed the COMPLETE frame and closed while
                # this raw sender still had write() calls in flight; the
                # in-flight tail races the close. A real client sends one
                # sendall and never writes again, so it cannot hit this.
                # The reply below proves every byte was received.
                pass
            reply = _read_reply_frame(client)
        finally:
            client.close()
        assert reply == f"len:{len(payload)}"
        assert calls == [payload]
    finally:
        server.stop()


def test_a1_double_prime_sender_pausing_per_chunk_under_header_timeout_only():
    """A1'' companion: the header phase keeps the short connection
    timeout (bounds dead connections), but a body that makes progress
    every <FRAME_BODY_TIMEOUT_SEC is never cut off mid-frame."""
    with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
        sock_file = tmp.name
    server = _echo_server(sock_file)
    try:
        payload = "seek " + "y" * (256 * 1024)
        body = payload.encode("utf-8")
        header = FRAME_MAGIC + bytes([FRAME_VERSION]) + struct.pack(">I", len(body))
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(30.0)
        client.connect(sock_file)
        try:
            client.sendall(header)
            for start in range(0, len(body), 16 * 1024):
                client.sendall(body[start : start + 16 * 1024])
                time.sleep(0.05)  # total >> the old 1 s connection timeout
            reply = _read_reply_frame(client)
        finally:
            client.close()
        assert reply == f"len:{len(payload)}"
    finally:
        server.stop()


# --- A2''=B1'': oversize frames fail with a clean typed error ------------------------


def test_a2_double_prime_b1_prime_oversize_header_gets_typed_error_reply():
    """A2''=B1'': payloads past the cap used to degrade to a mid-drain
    connection loss ('daemon closed the connection during playback').
    Framing v2 rejects the header up front with an explicit ERR reply
    and never drains the announced body (no body follows here; a drainer
    would hang waiting for it)."""
    calls = []
    with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
        sock_file = tmp.name
    server = _echo_server(sock_file, calls)
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(10.0)
        client.connect(sock_file)
        try:
            client.sendall(
                FRAME_MAGIC + bytes([FRAME_VERSION]) + struct.pack(">I", 0xFFFFFFF0)
            )
            started = time.monotonic()
            reply = read_frame(client)
            elapsed = time.monotonic() - started
        finally:
            client.close()
        assert reply.startswith("ERR: command too large"), reply
        assert elapsed < 3.0  # answered from the header; no drain, no probe
        assert calls == []  # never dispatched
    finally:
        server.stop()


def test_a2_double_prime_b1_prime_client_side_oversize_play_fails_fast(monkeypatch):
    """A2''=B1'' client path: an oversize delegated play is rejected
    locally, before any socket write — the clean size error reaches the
    CLI instead of a broken pipe or a silent connection loss."""
    from agent_tts import daemon as daemon_mod

    def _no_network(*args, **kwargs):
        raise AssertionError("oversize payload must fail before connecting")

    monkeypatch.setattr(daemon_mod, "connect_to_server", _no_network)
    payload = {"text": "x" * (MAX_PAYLOAD + 1024)}
    started = time.monotonic()
    reply = daemon_mod.send_play(payload, socket_path="/nonexistent.sock")
    assert isinstance(reply, str)
    assert reply.startswith("ERR: command too large"), reply
    assert time.monotonic() - started < 1.0


def test_frame_round_trip_uses_the_shared_read_write_helpers():
    a, b = socket.socketpair()
    try:
        send_frame(b, "status")
        assert read_frame(a) == "status"
        send_frame(a, "status=idle")
        assert read_frame(b) == "status=idle"
    finally:
        a.close()
        b.close()


# --- RI-1 / A8: chunked reads and centralized constants ------------------------------


def test_ri1_read_frame_body_reads_use_read_chunk_sized_recvs():
    """RI-1: the old 1024-byte recv chunks meant ~16k syscalls and ~3x
    peak memory per 16 MiB payload. The read loop must ask for
    READ_CHUNK bytes per syscall (64 KiB), not a small literal."""

    class CountingSock:
        def __init__(self, frame_bytes):
            self._buf = frame_bytes
            self.calls = []

        def recv(self, n):
            self.calls.append(n)
            out, self._buf = self._buf[:n], self._buf[n:]
            return out or b""

        def gettimeout(self):
            return None

        def settimeout(self, value):
            pass

    assert READ_CHUNK == 64 * 1024
    body = b"z" * (300 * 1024)  # 300 KiB -> 4 full chunks + tail
    frame = encode_frame(body)
    sock = CountingSock(frame)
    out = read_frame(sock)
    assert out == "z" * (300 * 1024)
    body_calls = [n for n in sock.calls if n > FRAME_HEADER_SIZE]
    assert body_calls[0] == READ_CHUNK
    assert len(body_calls) <= (len(body) // READ_CHUNK) + 2  # ~16k -> ~5


def test_a8_daemon_module_has_no_hardcoded_buffer_literals():
    """A8: daemon.py read loops must import the framing constants from
    ipc.py instead of hardcoding 8192/1024 buffer literals."""
    from pathlib import Path

    source = Path(ipc.__file__).with_name("daemon.py").read_text(encoding="utf-8")
    assert "8192" not in source
    assert "recv(1024" not in source
    assert "from agent_tts.ipc import" in source


# --- Version/mismatch handling -------------------------------------------------------


def test_stale_line_protocol_daemon_is_classified_wedged_for_respawn(tmp_path):
    """Protocol mismatch recovery (chosen design): a stale daemon that
    still speaks the v1 line protocol never answers a v2 ping (no
    newline in the frame), so the probe reports it as wedged and the
    existing kill-and-respawn machinery (RF-AT-04-8) replaces it — no
    new respawn path is needed."""
    sock_path = str(tmp_path / "stale.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    listener.listen(2)

    def v1_serve():
        # Emulates the old daemon: reads until a newline that never
        # arrives in a v2 frame, then gives up without replying.
        try:
            conn, _ = listener.accept()
            conn.settimeout(1.0)
            try:
                while b"\n" not in conn.recv(4096):
                    pass
            except OSError:
                pass
            finally:
                conn.close()
        except OSError:
            pass

    threading.Thread(target=v1_serve, daemon=True).start()
    try:
        status, reply = probe_daemon(PING_TIMEOUT_SEC + 1.0, sock_path)
        assert status == "wedged"
        assert reply is None
    finally:
        listener.close()


def test_probe_classifies_non_v2_reply_as_foreign(tmp_path):
    """A live owner that answers bytes which are not a v2 frame is
    foreign (an embedded/older non-daemon owner), not our daemon."""
    sock_path = str(tmp_path / "foreign.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    listener.listen(2)

    def foreign_serve():
        try:
            conn, _ = listener.accept()
            try:
                conn.recv(4096)
                conn.sendall(b"status=playing\n")  # old bare-line reply
            except OSError:
                pass
            finally:
                conn.close()
        except OSError:
            pass

    threading.Thread(target=foreign_serve, daemon=True).start()
    try:
        status, _ = probe_daemon(PING_TIMEOUT_SEC + 1.0, sock_path)
        assert status == "foreign"
    finally:
        listener.close()


def test_at_cap_payload_round_trips_exactly():
    """The 16 MiB cap is a floor for legitimate payloads: a frame whose
    body is exactly MAX_PAYLOAD bytes is served in full (parity with the
    old framing's at-cap behavior, now without the probe)."""
    with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
        sock_file = tmp.name
    server = _echo_server(sock_file)
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(60.0)
        client.connect(sock_file)
        try:
            send_frame(client, "y" * MAX_PAYLOAD)
            reply = read_frame(client)
        finally:
            client.close()
        assert reply == f"len:{MAX_PAYLOAD}"
    finally:
        server.stop()
