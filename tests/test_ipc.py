import json
import socket
import tempfile
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

        # Opt out of the ownership default: this suite exercises the raw
        # transport with a private temp path, no election involved.
        server = IPCServer(
            command_handler=mock_handler, socket_path=sock_file, require_ownership=False
        )
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
    """RF-AT-09-4 (framing v2): commands and replies are length-prefixed
    frames (magic + version + big-endian length), so a frame split across
    many packets is reassembled from the header's exact length."""

    def _serve_echo(self, sock_file):
        # require_ownership=False: raw framing test on a private temp path.
        server = IPCServer(
            command_handler=lambda cmd: f"len:{len(cmd)}",
            socket_path=sock_file,
            require_ownership=False,
        )
        server.start()
        time.sleep(0.1)
        return server

    def _roundtrip(self, sock_file, payload: bytes) -> str:
        from agent_tts.ipc import read_frame

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(30.0)
        client.connect(sock_file)
        try:
            client.sendall(payload)
            return read_frame(client)
        finally:
            client.close()

    def test_command_split_across_packets_is_read_to_the_exact_length(self):
        from agent_tts.ipc import FRAME_MAGIC, FRAME_VERSION, encode_frame

        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            payload = "seek " + "x" * 3000  # spans several packet-sized pieces
            frame = encode_frame(payload)
            head, tail = frame[:1500], frame[1500:]
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(30.0)
            client.connect(sock_file)
            try:
                # First piece carries an incomplete header/body: the server
                # must keep reading until the announced length, not trust a
                # single recv().
                client.sendall(head)
                time.sleep(0.1)
                client.sendall(tail)
                from agent_tts.ipc import read_frame

                reply = read_frame(client)
            finally:
                client.close()
            self.assertEqual(reply, f"len:{len(payload)}")
            self.assertEqual(FRAME_MAGIC + bytes([FRAME_VERSION]), frame[:5])
        finally:
            server.stop()

    def test_command_without_any_special_terminator_is_read_to_the_length(self):
        # v2 needs no newline terminator: the length prefix is the only
        # framing authority (the old probe-at-cap heuristic is gone).
        from agent_tts.ipc import encode_frame

        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            payload = "status no newline anywhere"
            reply = self._roundtrip(sock_file, encode_frame(payload))
            self.assertEqual(reply, f"len:{len(payload)}")
        finally:
            server.stop()

    def test_large_framed_command_round_trips_far_above_the_old_8k_cap(self):
        """CONF-1: a delegated play payload (>= 1 MB) is not truncated."""
        from agent_tts.ipc import encode_frame

        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            payload = "play " + "x" * (1024 * 1024)  # 1 MiB of text
            reply = self._roundtrip(sock_file, encode_frame(payload))
            self.assertEqual(reply, f"len:{len(payload)}")
        finally:
            server.stop()

    def test_command_beyond_byte_cap_fails_with_clear_error(self):
        """CONF-1: a frame header announcing more than MAX_PAYLOAD is
        rejected up front with an explicit ERR reply, never buffered."""
        import struct

        from agent_tts.ipc import FRAME_MAGIC, FRAME_VERSION, MAX_PAYLOAD, read_frame

        with tempfile.NamedTemporaryFile(suffix=".sock", delete=True) as tmp:
            sock_file = tmp.name
        server = self._serve_echo(sock_file)
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(10.0)
            client.connect(sock_file)
            try:
                client.sendall(
                    FRAME_MAGIC
                    + bytes([FRAME_VERSION])
                    + struct.pack(">I", MAX_PAYLOAD + 2048)
                )
                reply = read_frame(client)
            finally:
                client.close()
            self.assertTrue(reply.startswith("ERR: command too large"), reply)
        finally:
            server.stop()


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

    def test_error_field_is_trailing_free_text(self):
        # A3 typed error shape: error= is the final, space-bearing field.
        out = json.loads(ipc_reply_json("ok=false error=no active playback session"))
        self.assertEqual(out, {"ok": "false", "error": "no active playback session"})

    def test_leading_error_field(self):
        out = json.loads(ipc_reply_json("error=playback target unavailable (exit 1)"))
        self.assertEqual(out, {"error": "playback target unavailable (exit 1)"})


if __name__ == "__main__":
    unittest.main()
