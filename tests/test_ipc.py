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
    """RF-AT-09-4: the server reads commands with the client's line framing."""

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
