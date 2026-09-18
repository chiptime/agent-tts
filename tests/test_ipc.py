import tempfile
import time
import unittest
from agent_tts.ipc import IPCServer, send_ipc_command


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


if __name__ == "__main__":
    unittest.main()
