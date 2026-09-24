import json
import socket
import tempfile
import threading
import time
import unittest
from agent_tts.ipc import IPCServer, ipc_reply_json, send_ipc_command


class TestIPC(unittest.TestCase):
    def test_ipc_server_and_client(self):
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name

        def mock_handler(cmd: str) -> str:
            if cmd == "status":
                return "status=playing pos=5.00 total=10.00 label=Test"
            elif cmd.startswith("seek"):
                return "status=playing pos=15.00 total=10.00 label=Test"
            return f"OK: {cmd}"

        server = IPCServer(command_handler=mock_handler, socket_path=sock_file)
        server.start()
        time.sleep(0.1)

        try:
            res = send_ipc_command("status", socket_path=sock_file)
            self.assertEqual(res, "status=playing pos=5.00 total=10.00 label=Test")

            seek_res = send_ipc_command("seek +10", socket_path=sock_file)
            self.assertEqual(seek_res, "status=playing pos=15.00 total=10.00 label=Test")
        finally:
            server.stop()


class TestCommandFraming(unittest.TestCase):
    """RF-AT-09-4: the server reads commands with the client's line framing."""

    def _serve_echo(self, sock_file):
        server = IPCServer(command_handler=lambda cmd: f"len:{len(cmd)}", socket_path=sock_file)
        server.start()
        time.sleep(0.1)
        return server

    def _roundtrip(self, sock_file, payload: bytes) -> str:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5.0)
        client.connect(sock_file)
        try:
            client.sendall(payload)
            chunks = []
            while b"\n" not in b"".join(chunks):
                chunk = client.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="ignore").strip()
        finally:
            client.close()

    def test_command_split_across_packets_is_read_until_newline(self):
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            payload = "seek " + "x" * 3000  # spans several 1024-byte packets
            head, tail = payload[:1500], payload[1500:]
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            client.connect(sock_file)
            try:
                # First chunk carries no newline: the server must keep
                # reading instead of trusting the first recv().
                client.sendall(head.encode("utf-8"))
                time.sleep(0.1)
                client.sendall(tail.encode("utf-8") + b"\n")
                buf = b""
                while b"\n" not in buf:
                    chunk = client.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
                reply = buf.decode("utf-8", errors="ignore").strip()
            finally:
                client.close()
            self.assertEqual(reply, f"len:{len(payload)}")
        finally:
            server.stop()

    def test_command_without_newline_is_processed_at_the_byte_cap(self):
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            from agent_tts.ipc import MAX_COMMAND_BYTES

            # Exactly MAX_COMMAND_BYTES bytes and no newline: the cap is the
            # only terminator left, mirroring the client's reply cap.
            capped = b"y" * MAX_COMMAND_BYTES
            reply = self._roundtrip(sock_file, capped)
            self.assertEqual(reply, f"len:{MAX_COMMAND_BYTES}")
        finally:
            server.stop()

    def test_large_command_line_round_trips_far_above_the_old_cap(self):
        """CONF-1: a delegated play line (>= 1 MB) is not truncated at 8 KB."""
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            payload = "play " + "x" * (1024 * 1024)  # 1 MiB of text
            reply = self._roundtrip(sock_file, payload.encode("utf-8") + b"\n")
            self.assertEqual(reply, f"len:{len(payload)}")
        finally:
            server.stop()

    def test_sender_pausing_at_cap_then_continuing_is_rejected(self):
        """RC-1: a sender that pauses AT the cap and then continues is over-cap.

        The over-cap check must be a bounded WAIT, not an instantaneous
        buffered-bytes probe: at the pause instant nothing extra is
        buffered, so the old probe accepted the truncated prefix and
        dispatched it as a command (surfacing as a confusing JSON error).
        """
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            from agent_tts.ipc import MAX_COMMAND_BYTES

            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(10.0)
            client.connect(sock_file)
            try:
                # Pause at exactly the cap, long enough for the server to
                # drain every buffered byte, then push past it.
                client.sendall(b"y" * MAX_COMMAND_BYTES)
                time.sleep(0.3)
                client.sendall(b"continued past the cap\n")
                buf = b""
                while b"\n" not in buf:
                    chunk = client.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
                reply = buf.decode("utf-8", errors="ignore").strip()
            finally:
                client.close()
            self.assertTrue(reply.startswith("ERR: command too large"), reply)
        finally:
            server.stop()

    def test_command_beyond_byte_cap_fails_with_clear_error(self):
        """CONF-1: runaway input beyond the documented cap is rejected, not truncated."""
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            from agent_tts.ipc import MAX_COMMAND_BYTES

            runaway = b"z" * (MAX_COMMAND_BYTES + 2048)  # past the cap, no newline
            reply = self._roundtrip(sock_file, runaway)
            self.assertTrue(reply.startswith("ERR: command too large"), reply)
        finally:
            server.stop()


class TestPastCapProbe(unittest.TestCase):
    """RS-3: the over-cap probe waits boundedly and preserves the socket timeout."""

    def test_probe_times_out_when_the_sender_stops_at_cap(self):
        from agent_tts.ipc import _probe_past_cap

        a, b = socket.socketpair()
        try:
            a.settimeout(0.25)
            started = time.monotonic()
            # No byte ever arrives: the probe must wait out the deadline
            # (a bounded wait, not an instantaneous buffered check) and
            # then report "sender stopped at the cap".
            self.assertFalse(_probe_past_cap(a))
            self.assertGreaterEqual(time.monotonic() - started, 0.2)
        finally:
            a.close()
            b.close()

    def test_probe_sees_a_byte_arriving_within_the_deadline(self):
        from agent_tts.ipc import _probe_past_cap

        a, b = socket.socketpair()
        try:
            a.settimeout(1.0)

            def late_byte():
                time.sleep(0.1)
                b.send(b"x")

            threading.Thread(target=late_byte, daemon=True).start()
            self.assertTrue(_probe_past_cap(a))
        finally:
            a.close()
            b.close()

    def test_probe_preserves_the_connection_timeout(self):
        """RS-3: the probe must not flip the socket into unbounded blocking mode."""
        from agent_tts.ipc import _probe_past_cap

        a, b = socket.socketpair()
        try:
            a.settimeout(0.25)
            self.assertFalse(_probe_past_cap(a))
            # The pre-existing timeout survives the probe path: a later
            # sendall against a stalled peer stays bounded instead of
            # blocking forever.
            self.assertEqual(a.gettimeout(), 0.25)
            b.send(b"x")
            self.assertTrue(_probe_past_cap(a))
            self.assertEqual(a.gettimeout(), 0.25)
        finally:
            a.close()
            b.close()

    def test_probe_is_bounded_even_on_a_blocking_socket(self):
        from agent_tts.ipc import _probe_past_cap

        a, b = socket.socketpair()
        try:
            self.assertIsNone(a.gettimeout())
            started = time.monotonic()
            # A blocking socket still gets a bounded probe deadline, and
            # its blocking mode is restored afterwards.
            self.assertFalse(_probe_past_cap(a))
            self.assertLess(time.monotonic() - started, 5.0)
            self.assertIsNone(a.gettimeout())
        finally:
            a.close()
            b.close()


class TestIpcReplyJson(unittest.TestCase):
    def test_fields_and_trailing_free_text(self):
        out = json.loads(ipc_reply_json("status=playing pos=5.00 total=10.00 text=Hola mundo"))
        self.assertEqual(out, {"status": "playing", "pos": "5.00", "total": "10.00", "text": "Hola mundo"})

    def test_leading_free_text_field(self):
        out = json.loads(ipc_reply_json("text=Solo texto"))
        self.assertEqual(out, {"text": "Solo texto"})

    def test_fields_only(self):
        out = json.loads(ipc_reply_json("status=stopped"))
        self.assertEqual(out, {"status": "stopped"})

    def test_unknown_shape_degrades_to_raw(self):
        out = json.loads(ipc_reply_json("just some prose without tokens"))
        self.assertEqual(out, {"raw": "just some prose without tokens"})


if __name__ == "__main__":
    unittest.main()
