import json
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
