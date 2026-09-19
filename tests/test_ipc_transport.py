"""IPC transport selection tests: AF_UNIX on POSIX, TCP loopback + port marker on Windows."""

import re
import socket
import threading
import time

import agent_tts.ipc as ipc
from agent_tts.audio import AudioSession
from agent_tts.boundaries import BoundaryMap, Sentence
from agent_tts.constants import DEFAULT_VOICE


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


# --- Status payload (dashboard v2) -------------------------------------------------


def _field(payload: str, key: str) -> str:
    """Extracts a `key=value` field whose value may contain spaces (kv-by-prefix protocol)."""
    m = re.search(rf"(?:^| ){re.escape(key)}=(.*?)(?: \w+=|$)", payload)
    return m.group(1) if m else ""


def _status_session(sentence_text: str, **kwargs) -> AudioSession:
    """Builds an AudioSession with a single sentence and a mid-sentence frame position."""
    bmap = BoundaryMap(
        sentences=[Sentence(index=0, start_sec=0.0, duration_sec=5.0, text=sentence_text)]
    )
    session = AudioSession(label="T", boundaries=bmap, **kwargs)
    session.sample_rate = 24000
    session.total_frames = 24000 * 5
    session.current_frame = 24000  # 1.0s into the sentence
    session.state["status"] = "playing"
    return session


def _serve_status(session: AudioSession, socket_path: str) -> str:
    """Serves one status request from the session over a real IPC socket."""
    server = ipc.IPCServer(command_handler=session.handle_ipc_command, socket_path=socket_path)
    server.start()
    time.sleep(0.05)
    try:
        return ipc.send_ipc_command("status", socket_path=socket_path) or ""
    finally:
        server.stop()


def test_status_payload_keeps_legacy_fields_and_adds_engine_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AGENT_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_TTS_VOICE", raising=False)
    session = _status_session("Hola mundo desde el motor.")
    payload = _serve_status(session, str(tmp_path / "s.sock"))

    # Legacy contract: state word first, then pos/total/sent_idx/para_idx/sentence.
    assert payload.startswith("status=playing")
    assert _field(payload, "pos") == "1.00"
    assert _field(payload, "total") == "5.00"
    assert _field(payload, "sent_idx") == "0"
    assert _field(payload, "sentence") == "Hola mundo desde el motor."
    # New engine metadata for the host dashboard.
    assert _field(payload, "provider") == "edge"
    assert _field(payload, "voice") == DEFAULT_VOICE
    assert _field(payload, "text") == "Hola mundo desde el motor."


def test_status_text_snippet_is_single_line_capped_and_clean(tmp_path):
    nasty = "\x1b[1mHola\x1b[0m\nmundo\tcon\rcontrol\x07chars y un texto larguísimo " * 3
    session = _status_session(nasty.strip())
    payload = _serve_status(session, str(tmp_path / "s.sock"))
    snippet = _field(payload, "text")

    assert "\n" not in snippet
    assert "\t" not in snippet
    assert "\r" not in snippet
    assert "\x07" not in snippet
    assert "\x1b" not in snippet
    assert len(snippet) <= 60
    assert snippet.endswith("...")
    # Full sentence field keeps the legacy unbounded behavior for old clients.
    assert "\n" not in _field(payload, "sentence")


def test_provider_and_voice_resolution_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_TTS_PROVIDER", "kokoro")
    monkeypatch.setenv("AGENT_TTS_VOICE", "ef_dora")

    # Explicit args win over environment.
    explicit = _status_session("x", provider="elevenlabs", voice="rachel")
    assert explicit.provider == "elevenlabs"
    assert explicit.voice == "rachel"

    # Host-level env wins over the CLI's provider default source.
    from_env = _status_session("x")
    assert from_env.provider == "kokoro"
    assert from_env.voice == "ef_dora"

    # Without host env, the CLI's own default source (TTS_PROVIDER) applies.
    monkeypatch.delenv("AGENT_TTS_PROVIDER")
    monkeypatch.delenv("AGENT_TTS_VOICE")
    from_cli_env = _status_session("x")
    assert from_cli_env.provider == "openai"
    assert from_cli_env.voice == DEFAULT_VOICE

    # Bare defaults with no env at all.
    monkeypatch.delenv("TTS_PROVIDER")
    bare = _status_session("x")
    assert bare.provider == "edge"
    assert bare.voice == DEFAULT_VOICE


def test_status_roundtrip_survives_long_sentence_payload(tmp_path):
    # Regression guard for the recv-until-newline client: a long legacy
    # sentence= plus the appended metadata must arrive complete, not
    # truncated at the first 1024-byte recv.
    long_sentence = "Palabra " * 150  # ~1200 chars
    session = _status_session(long_sentence.strip())
    payload = _serve_status(session, str(tmp_path / "s.sock"))

    assert len(payload) > 1024
    assert _field(payload, "provider") == "edge"
    assert _field(payload, "text").endswith("...")
    assert _field(payload, "sentence").startswith("Palabra")
