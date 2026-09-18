"""IPC transport selection tests: AF_UNIX on POSIX, TCP loopback + port marker on Windows."""

import socket
import threading
import time

import agent_tts.ipc as ipc


def _echo_server(sock, max_connections=4):
    """Accepts connections until idle or the cap, echoing commands back with an OK prefix.

    Per-connection errors are swallowed (mirrors IPCServer's listener loop).
    """
    sock.listen(5)
    sock.settimeout(0.2)
    for _ in range(max_connections):
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            break
        except OSError:
            break
        try:
            conn.settimeout(0.5)
            data = conn.recv(1024).decode("utf-8", errors="ignore")
            conn.sendall(f"OK:{data.strip()}\n".encode("utf-8"))
        except OSError:
            pass
        finally:
            conn.close()


def test_posix_unix_socket_selection(tmp_path):
    path = str(tmp_path / "test.sock")
    srv = ipc.server_socket(path)
    assert srv.family == socket.AF_UNIX
    thread = threading.Thread(target=_echo_server, args=(srv,), daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        client = ipc.connect_to_server(socket_path=path)
        assert client is not None
        client.close()
        assert ipc.send_ipc_command("status", socket_path=path) == "OK:status"
    finally:
        srv.close()
        thread.join(timeout=1)


def test_posix_connect_without_socket_file_returns_none(tmp_path):
    missing = str(tmp_path / "missing.sock")
    assert ipc.connect_to_server(socket_path=missing) is None
    assert ipc.send_ipc_command("status", socket_path=missing) is None


def test_windows_tcp_selection(tmp_path, monkeypatch):
    marker = tmp_path / "agent-tts-ipc.port"
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", str(marker))
    monkeypatch.setattr(ipc, "_is_windows", lambda: True)

    srv = ipc.server_socket("/unused/path.sock")
    assert srv.family == socket.AF_INET
    port = int(marker.read_text().strip())
    assert 0 < port < 65536

    thread = threading.Thread(target=_echo_server, args=(srv,), daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        assert ipc.connect_to_server(socket_path="/unused/path.sock") is not None
        assert ipc.send_ipc_command("pause", socket_path="/unused/path.sock") == "OK:pause"
    finally:
        srv.close()
        thread.join(timeout=1)


def test_windows_client_without_marker_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "IPC_PORT_FILE", str(tmp_path / "missing.port"))
    monkeypatch.setattr(ipc, "_is_windows", lambda: True)
    assert ipc.connect_to_server(socket_path="/whatever") is None
