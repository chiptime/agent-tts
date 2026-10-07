"""Engine STT worker tests: framing, endpoint safety, protocol, lifecycle.

Every test listens only on sockets inside pytest's per-test temp dir — no
global runtime directory, no real model, no network. The transport tests
use the REAL local Unix socket framing (threaded SttWorker + stt_request
client), not an overmocked pair.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

import agent_tts.stt.worker as sttw
from agent_tts.stt.transcriber import (
    MissingExtraError,
    SttError,
    SttSettings,
    Transcriber,
)
from agent_tts.stt.worker import (
    FRAME_VERSION,
    MAX_AUDIO_BYTES,
    SttWorker,
    AddressInUseError,
    BusyError,
    FrameTooLargeError,
    ProtocolMismatchError,
    TransportUnsupportedError,
    UnsafeEndpointError,
    WorkerUnavailableError,
    encode_frame,
    ensure_runtime_dir,
    stt_request,
    stt_runtime_dir,
)


class FakeModel:
    """Canned segments; `block` parks the call to simulate a hang."""

    block_event: threading.Event | None = None

    def transcribe(self, path, language=None):
        if FakeModel.block_event is not None:
            FakeModel.block_event.wait(timeout=30)
        return (
            iter([types.SimpleNamespace(text=" hola rebaño ")]),
            types.SimpleNamespace(language="es"),
        )


def fake_transcriber(cached: bool = True) -> Transcriber:
    return Transcriber(SttSettings(), loader=lambda: FakeModel(), cached=lambda: cached)


def start_worker(sock_path: Path, transcriber: Transcriber | None = None) -> SttWorker:
    w = SttWorker(transcriber or fake_transcriber(), socket_path=str(sock_path))
    w.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if stt_request({"op": "status"}, socket_path=str(sock_path), timeout=5)["ok"]:
                return w
        except Exception:
            time.sleep(0.02)
    raise AssertionError("worker did not come up")


# ---------------------------------------------------------------------------
# Framing primitives
# ---------------------------------------------------------------------------


class TestFraming:
    def test_roundtrip(self):
        frame = encode_frame(json.dumps({"op": "status"}))
        assert frame[:4] == b"ASTT" and frame[4] == 1

    @pytest.mark.parametrize("size", [sttw.MAX_PAYLOAD + 1, sttw.MAX_PAYLOAD * 2])
    def test_encode_oversize_raises_locally(self, size):
        with pytest.raises(FrameTooLargeError):
            encode_frame(b"x" * size)

    def test_read_frame_rejects_bad_magic(self):
        a, b = socket.socketpair()
        try:
            b.sendall(b"XXXX" + bytes([1]) + (0).to_bytes(4, "big"))
            with pytest.raises(ProtocolMismatchError):
                sttw.read_frame(a)
        finally:
            a.close()
            b.close()

    def test_read_frame_rejects_bad_version(self):
        a, b = socket.socketpair()
        try:
            b.sendall(b"ASTT" + bytes([9]) + (0).to_bytes(4, "big"))
            with pytest.raises(ProtocolMismatchError):
                sttw.read_frame(a)
        finally:
            a.close()
            b.close()

    def test_read_frame_rejects_oversize_header_without_body(self):
        a, b = socket.socketpair()
        try:
            length = (sttw.MAX_PAYLOAD + 1).to_bytes(4, "big")
            b.sendall(b"ASTT" + bytes([1]) + length)
            with pytest.raises(FrameTooLargeError):
                sttw.read_frame(a)
        finally:
            a.close()
            b.close()


# ---------------------------------------------------------------------------
# Runtime directory and endpoint safety
# ---------------------------------------------------------------------------


class TestRuntimeDir:
    def test_valid_xdg_runtime_dir_is_preferred(self, tmp_path, monkeypatch):
        xdg = tmp_path / "xdg"
        xdg.mkdir(mode=0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))
        assert stt_runtime_dir() == str(xdg / "agent-tts-stt")

    def test_group_writable_xdg_is_rejected(self, tmp_path, monkeypatch):
        xdg = tmp_path / "xdg-bad"
        xdg.mkdir()
        os.chmod(xdg, 0o770)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))
        # unsafe XDG falls back to a private uid-specific temp directory
        assert "agent-tts-stt-" in stt_runtime_dir()

    def test_env_override_wins(self, tmp_path, monkeypatch):
        override = tmp_path / "runtime"
        monkeypatch.setenv("AGENT_TTS_STT_RUNTIME_DIR", str(override))
        assert stt_runtime_dir() == str(override)

    def test_ensure_creates_mode_0700(self, tmp_path):
        target = tmp_path / "runtime"
        ensure_runtime_dir(str(target))
        st = os.lstat(target)
        assert stat.S_ISDIR(st.st_mode)
        assert st.st_mode & 0o077 == 0

    def test_ensure_rejects_symlink_dir(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(UnsafeEndpointError):
            ensure_runtime_dir(str(link))

    def test_ensure_rejects_group_permissions(self, tmp_path):
        target = tmp_path / "loose"
        target.mkdir()
        os.chmod(target, 0o750)
        with pytest.raises(UnsafeEndpointError):
            ensure_runtime_dir(str(target))

    def test_ensure_rejects_foreign_owner(self, tmp_path, monkeypatch):
        target = tmp_path / "foreign"
        target.mkdir(mode=0o700)
        monkeypatch.setattr(sttw, "_euid", lambda: os.geteuid() + 4242)
        with pytest.raises(UnsafeEndpointError):
            ensure_runtime_dir(str(target))


# ---------------------------------------------------------------------------
# Socket target validation (client side)
# ---------------------------------------------------------------------------


class TestClientTargetValidation:
    def test_rejects_symlink_socket(self, tmp_path):
        real = tmp_path / "real.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(real))
        listener.listen(1)
        link = tmp_path / "link.sock"
        link.symlink_to(real)
        try:
            with pytest.raises(UnsafeEndpointError):
                stt_request({"op": "status"}, socket_path=str(link))
        finally:
            listener.close()

    def test_rejects_non_socket_file(self, tmp_path):
        plain = tmp_path / "plain.sock"
        plain.write_text("not a socket")
        with pytest.raises(UnsafeEndpointError):
            stt_request({"op": "status"}, socket_path=str(plain))

    def test_missing_socket_is_actionable_worker_unavailable(self, tmp_path):
        missing = tmp_path / "missing.sock"
        with pytest.raises(WorkerUnavailableError) as exc:
            stt_request({"op": "status"}, socket_path=str(missing))
        assert "agent-tts-stt serve" in str(exc.value)


# ---------------------------------------------------------------------------
# Live worker protocol over a real socket
# ---------------------------------------------------------------------------


class TestWorkerProtocol:
    def test_socket_is_mode_0600(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            st = os.lstat(sock)
            assert stat.S_ISSOCK(st.st_mode)
            assert st.st_mode & 0o077 == 0
        finally:
            w.stop()

    def test_serve_auto_warmup_without_manual_warmup(self, tmp_path):
        """F2: production serve never calls warmup() by hand — the worker
        itself must drive loading -> ready (or unavailable + error)."""
        gate = threading.Event()
        t = Transcriber(
            SttSettings(),
            loader=lambda: (gate.wait(timeout=5), FakeModel())[1],
            cached=lambda: True,
        )
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, t)
        try:
            assert stt_request({"op": "status"}, socket_path=str(sock))["state"] == "loading"
            gate.set()
            deadline = time.monotonic() + 5
            state = None
            while time.monotonic() < deadline:
                state = stt_request({"op": "status"}, socket_path=str(sock))["state"]
                if state == "ready":
                    break
                time.sleep(0.05)
            assert state == "ready"  # no manual t.warmup() anywhere
        finally:
            gate.set()
            w.stop()

    def test_failed_load_surfaces_error_in_status(self, tmp_path):
        def boom():
            raise RuntimeError("model load exploded")

        t = Transcriber(SttSettings(), loader=boom, cached=lambda: True)
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, t)
        try:
            deadline = time.monotonic() + 5
            reply = None
            while time.monotonic() < deadline:
                reply = stt_request({"op": "status"}, socket_path=str(sock))
                if reply["state"] == "unavailable":
                    break
                time.sleep(0.05)
            assert reply["state"] == "unavailable"
            assert "model load exploded" in reply["error"]
        finally:
            w.stop()

    def test_busy_stays_truthful_during_warmup_and_inference(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            assert stt_request({"op": "status"}, socket_path=str(sock))["busy"] is False
            with w._infer_lock:
                assert stt_request({"op": "status"}, socket_path=str(sock))["busy"] is True
        finally:
            w.stop()

    def test_transcribe_roundtrip_and_model_reuse(self, tmp_path):
        loads = []

        def counting_loader():
            loads.append(1)
            return FakeModel()

        t = Transcriber(SttSettings(), loader=counting_loader, cached=lambda: True)
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, t)
        try:
            for _ in range(2):
                reply = stt_request(
                    {
                        "op": "transcribe",
                        "audio_b64": base64.b64encode(b"fake-webm").decode(),
                        "suffix": ".webm",
                    },
                    socket_path=str(sock),
                )
                assert reply["text"] == "hola rebaño"
            assert len(loads) == 1  # model stays resident across requests
        finally:
            w.stop()

    def test_busy_reply_while_status_stays_responsive(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            with w._infer_lock:  # simulate an in-flight transcription
                started = time.monotonic()
                with pytest.raises(BusyError):
                    stt_request(
                        {"op": "transcribe", "audio_b64": base64.b64encode(b"x").decode()},
                        socket_path=str(sock),
                        timeout=5,
                    )
                status = stt_request({"op": "status"}, socket_path=str(sock), timeout=5)
                assert status["ok"] and time.monotonic() - started < 3
        finally:
            w.stop()

    @pytest.mark.parametrize(
        "payload",
        [
            b"{not json",
            json.dumps({"op": "unknown", "v": sttw.FRAME_VERSION}).encode(),
            json.dumps({"op": "transcribe", "v": 99}).encode(),
            json.dumps({"op": "status", "v": True}).encode(),
            json.dumps({"op": "transcribe", "v": sttw.FRAME_VERSION, "audio_b64": "!!!not-base64!!!"}).encode(),
            json.dumps({"op": "transcribe", "v": sttw.FRAME_VERSION, "audio_b64": base64.b64encode(b"").decode()}).encode(),
            json.dumps({"op": "transcribe", "v": sttw.FRAME_VERSION, "audio_b64": base64.b64encode(b"x").decode(), "suffix": ".sh"}).encode(),
            json.dumps({"op": "transcribe", "v": sttw.FRAME_VERSION, "audio_b64": base64.b64encode(b"x").decode(), "suffix": [".sh"]}).encode(),
            json.dumps([1, 2, 3]).encode(),
        ],
        ids=[
            "bad-json",
            "unknown-op",
            "wrong-version",
            "bool-version",
            "bad-base64",
            "empty-audio",
            "bad-suffix",
            "unhashable-suffix",
            "not-an-object",
        ],
    )
    def test_invalid_requests_get_typed_error(self, tmp_path, payload):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5)
            client.connect(str(sock))
            try:
                client.sendall(sttw.encode_frame(payload))
                reply = json.loads(sttw.read_frame(client, body_timeout_sec=5))
                assert reply["ok"] is False
                assert reply["error"]["kind"] == "invalid_request"
            finally:
                client.close()
        finally:
            w.stop()

    def test_oversize_audio_rejected_by_server_bound(self, tmp_path, monkeypatch):
        # Base64 of anything over the audio cap also exceeds the frame cap,
        # so the server-side audio bound is exercised with a small patched
        # cap (defense in depth beyond the frame header check).
        monkeypatch.setattr(sttw, "MAX_AUDIO_BYTES", 4)
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5)
            client.connect(str(sock))
            try:
                payload = json.dumps(
                    {
                        "op": "transcribe",
                        "v": sttw.FRAME_VERSION,
                        "audio_b64": base64.b64encode(b"x" * 16).decode(),
                    }
                ).encode()
                client.sendall(sttw.encode_frame(payload))
                reply = json.loads(sttw.read_frame(client, body_timeout_sec=5))
                assert reply["ok"] is False
                assert reply["error"]["kind"] == "invalid_request"
                assert "cap" in reply["error"]["message"]
            finally:
                client.close()
        finally:
            w.stop()

    def test_client_injects_protocol_version(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            reply = stt_request({"op": "status"}, socket_path=str(sock))
            assert reply["ok"] and reply["v"] == FRAME_VERSION
        finally:
            w.stop()


class TestAudioBoundary:
    """Real caps end-to-end (no patched constants): a maximum-legal audio
    request must fit the frame envelope (base64 + JSON overhead)."""

    def test_exact_cap_audio_succeeds_end_to_end(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            data = b"\x00" * sttw.MAX_AUDIO_BYTES
            reply = stt_request(
                {"op": "transcribe", "audio_b64": base64.b64encode(data).decode()},
                socket_path=str(sock),
                timeout=30,
            )
            assert reply["text"] == "hola rebaño"
        finally:
            w.stop()

    def test_cap_plus_one_rejected_end_to_end(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            data = b"\x00" * (sttw.MAX_AUDIO_BYTES + 1)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(30)
            client.connect(str(sock))
            try:
                payload = json.dumps(
                    {
                        "op": "transcribe",
                        "v": sttw.FRAME_VERSION,
                        "audio_b64": base64.b64encode(data).decode(),
                    }
                ).encode()
                client.sendall(sttw.encode_frame(payload))  # fits the frame cap...
                reply = json.loads(sttw.read_frame(client, body_timeout_sec=30))
                assert reply["ok"] is False  # ...but the decoded audio bound fires
                assert reply["error"]["kind"] == "invalid_request"
            finally:
                client.close()
        finally:
            w.stop()


# ---------------------------------------------------------------------------
# Ownership, stale/live sockets, teardown
# ---------------------------------------------------------------------------


def _bind_stale_socket(path: Path) -> None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # leaves the file behind on purpose


class TestSocketOwnership:
    def test_stale_socket_is_preserved_with_actionable_error(self, tmp_path):
        sock = tmp_path / "stt.sock"
        _bind_stale_socket(sock)
        inode_before = os.lstat(sock).st_ino
        w = SttWorker(fake_transcriber(), socket_path=str(sock))
        with pytest.raises(AddressInUseError) as exc:
            w.start()
        assert str(sock) in str(exc.value)
        assert os.lstat(sock).st_ino == inode_before  # never unlinked

    def test_live_socket_is_never_stolen(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w1 = start_worker(sock)
        try:
            w2 = SttWorker(fake_transcriber(), socket_path=str(sock))
            with pytest.raises(AddressInUseError):
                w2.start()
            # the live worker keeps serving untouched
            assert stt_request({"op": "status"}, socket_path=str(sock))["ok"]
        finally:
            w1.stop()

    def test_non_socket_path_is_refused(self, tmp_path):
        plain = tmp_path / "plain.sock"
        plain.write_text("junk")
        w = SttWorker(fake_transcriber(), socket_path=str(plain))
        with pytest.raises(UnsafeEndpointError):
            w.start()

    def test_symlink_socket_path_is_refused(self, tmp_path):
        real = tmp_path / "real.sock"
        _bind_stale_socket(real)
        link = tmp_path / "link.sock"
        link.symlink_to(real)
        w = SttWorker(fake_transcriber(), socket_path=str(link))
        with pytest.raises(UnsafeEndpointError):
            w.start()

    def test_teardown_never_removes_replaced_socket(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        # replace the socket file behind the worker's back
        os.unlink(sock)
        _bind_stale_socket(sock)
        replacement_inode = os.lstat(sock).st_ino
        w.stop()
        assert os.lstat(sock).st_ino == replacement_inode  # survivor untouched

    def test_teardown_skips_symlink_replacement(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        target = tmp_path / "elsewhere.sock"
        _bind_stale_socket(target)
        os.unlink(sock)
        (tmp_path / "stt.sock").symlink_to(target)
        w.stop()
        assert os.lstat(tmp_path / "stt.sock") and stat.S_ISLNK(
            os.lstat(tmp_path / "stt.sock").st_mode
        )  # the symlink itself is never unlinked
        assert os.lstat(target)  # and the target survives

    def test_teardown_skips_regular_file_replacement(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        os.unlink(sock)
        sock.write_text("someone else's file")
        w.stop()
        assert sock.read_text() == "someone else's file"

    def test_explicit_socket_parent_world_writable_rejected(self, tmp_path):
        loose = tmp_path / "loose"
        loose.mkdir()
        os.chmod(loose, 0o777)
        w = SttWorker(fake_transcriber(), socket_path=str(loose / "stt.sock"))
        with pytest.raises(UnsafeEndpointError):
            w.start()

    def test_explicit_socket_parent_foreign_owner_rejected(self, tmp_path, monkeypatch):
        owned = tmp_path / "owned"
        owned.mkdir(mode=0o700)
        monkeypatch.setattr(sttw, "_euid", lambda: os.geteuid() + 4242)
        w = SttWorker(fake_transcriber(), socket_path=str(owned / "stt.sock"))
        with pytest.raises(UnsafeEndpointError):
            w.start()

    def test_normal_teardown_removes_own_socket(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        w.stop()
        assert not sock.exists()

    def test_shutdown_op_stops_worker_and_cleans(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        reply = stt_request({"op": "shutdown"}, socket_path=str(sock), timeout=5)
        assert reply["ok"] and reply.get("shutting_down") is True
        report = w.wait_stopped(timeout=5)
        assert report["clean"] is True
        assert not sock.exists()
        with pytest.raises(WorkerUnavailableError):
            stt_request({"op": "status"}, socket_path=str(sock))

    def test_second_worker_after_clean_stop_can_bind(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w1 = start_worker(sock)
        w1.stop()
        w2 = start_worker(sock)  # same path reusable after clean teardown
        try:
            assert stt_request({"op": "status"}, socket_path=str(sock))["ok"]
        finally:
            w2.stop()


# ---------------------------------------------------------------------------
# Platform guard, bounded connections, hung inference honesty
# ---------------------------------------------------------------------------


class TestLifecycleBounds:
    def test_native_windows_transport_is_typed_unsupported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sttw, "_is_windows", lambda: True)
        w = SttWorker(fake_transcriber(), socket_path=str(tmp_path / "stt.sock"))
        with pytest.raises(TransportUnsupportedError) as exc:
            w.start()
        assert "WSL" in str(exc.value)
        with pytest.raises(TransportUnsupportedError):
            stt_request({"op": "status"}, socket_path=str(tmp_path / "s.sock"))

    def test_hung_inference_shutdown_is_bounded_and_honest(self, tmp_path):
        release = threading.Event()
        FakeModel.block_event = release
        try:
            t = Transcriber(SttSettings(), loader=lambda: FakeModel(), cached=lambda: True)
            sock = tmp_path / "stt.sock"
            w = start_worker(sock, t)

            result = {}

            def call_transcribe():
                try:
                    stt_request(
                        {"op": "transcribe", "audio_b64": base64.b64encode(b"x").decode()},
                        socket_path=str(sock),
                        timeout=10,
                    )
                except Exception as exc:  # noqa: BLE001 — recorded, not hidden
                    result["error"] = exc

            caller = threading.Thread(target=call_transcribe)
            caller.start()
            time.sleep(0.3)  # let the fake model park inside transcribe()
            started = time.monotonic()
            report = w.stop(grace=0.5)
            assert time.monotonic() - started < 5  # bounded control-plane exit
            assert report["clean"] is False and report["hung"] >= 1
            release.set()
            caller.join(timeout=10)
            w.join_workers(timeout=5)
        finally:
            FakeModel.block_event = None

    def test_connection_cap_returns_busy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sttw, "MAX_CONNECTIONS", 2)
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        squatters = []
        try:
            for _ in range(2):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(sock))
                squatters.append(s)
            deadline = time.monotonic() + 3
            last_error = None
            while time.monotonic() < deadline:
                try:
                    stt_request({"op": "status"}, socket_path=str(sock), timeout=5)
                    time.sleep(0.05)
                    continue
                except BusyError as exc:
                    last_error = exc
                    break
                except Exception:
                    time.sleep(0.05)
            assert isinstance(last_error, BusyError)
        finally:
            for s in squatters:
                s.close()
            w.stop()

    def test_failed_bind_releases_every_owned_resource(self, tmp_path):
        """F5: a startup failure (here: overlong AF_UNIX path) must not
        leave the election flock held by the dead worker object."""
        deep = tmp_path
        for i in range(12):
            deep = deep / f"very-long-segment-{i:02d}"
        deep.mkdir(parents=True)
        sock = deep / "stt.sock"
        assert len(str(sock)) > 108  # beyond AF_UNIX address space
        w = SttWorker(fake_transcriber(), socket_path=str(sock))
        with pytest.raises(SttError) as exc:
            w.start()
        assert exc.value.kind in ("endpoint_unsafe", "address_in_use")
        # the election lock must be free for the next candidate
        import fcntl

        lock_path = str(sock) + ".lock"
        handle = open(lock_path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            handle.close()

    def test_connection_registry_prunes_completed_threads(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            for _ in range(25):
                assert stt_request({"op": "status"}, socket_path=str(sock))["ok"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with w._conn_lock:
                    live = [t for t in w._conn_threads if t.is_alive()]
                    total = len(w._conn_threads)
                if total <= 4 and not live:
                    break
                time.sleep(0.1)
            assert total <= 4, f"registry retained {total} threads"
        finally:
            w.stop()

    def test_missing_extra_worker_reply_is_typed(self, tmp_path, monkeypatch):
        """F3: absent hub library surfaces as missing_extra through the
        worker protocol, not a generic error."""
        import types as _types

        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        t = Transcriber(SttSettings(), loader=lambda: FakeModel())
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, t)
        try:
            with pytest.raises(MissingExtraError) as exc:
                stt_request(
                    {"op": "transcribe", "audio_b64": base64.b64encode(b"x").decode()},
                    socket_path=str(sock),
                )
            assert "agent-tts[stt]" in str(exc.value)
        finally:
            w.stop()

    def test_stuck_warmup_shutdown_reports_unfinished_startup(self, tmp_path):
        """F2: a hung loader (before any transcribe) must be counted in the
        shutdown report — not clean=True/hung=0 while startup is stuck."""
        gate = threading.Event()

        def stuck_loader():
            gate.wait(timeout=30)
            return FakeModel()

        t = Transcriber(SttSettings(), loader=stuck_loader, cached=lambda: True)
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, t)
        try:
            # confirm the warmup task is actually RUNNING and stuck
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with w._conn_lock:
                    pass
                warm = getattr(w, "_warmup_thread", None)
                if warm is not None and warm.is_alive():
                    break
                time.sleep(0.05)
            assert warm is not None and warm.is_alive()
            assert stt_request({"op": "status"}, socket_path=str(sock))["state"] == "loading"
            report = w.stop(grace=0.5)
            assert report["clean"] is False
            assert report["hung"] >= 1  # startup counted, no reaping lie
        finally:
            gate.set()
            warm = getattr(w, "_warmup_thread", None)
            if warm is not None:
                warm.join(timeout=5)

    def test_recovery_status_refreshes_after_failed_then_succeeding_load(self, tmp_path):
        calls = {"n": 0}

        def flaky_loader():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient load failure")
            return FakeModel()

        t = Transcriber(SttSettings(), loader=flaky_loader, cached=lambda: True)
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, t)
        try:
            deadline = time.monotonic() + 5
            reply = None
            while time.monotonic() < deadline:
                reply = stt_request({"op": "status"}, socket_path=str(sock))
                if reply["state"] == "unavailable":
                    break
                time.sleep(0.05)
            assert reply["state"] == "unavailable"
            assert "transient load failure" in reply["error"]
            # a transcribe retries the load and succeeds -> health follows
            text = stt_request(
                {"op": "transcribe", "audio_b64": base64.b64encode(b"x").decode()},
                socket_path=str(sock),
            )
            assert text["text"] == "hola rebaño"
            status = stt_request({"op": "status"}, socket_path=str(sock))
            assert status["state"] == "ready"
            assert "error" not in status
        finally:
            w.stop()

    def test_subprocess_cli_against_live_worker(self, tmp_path):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, fake_transcriber())
        try:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, "-m", "agent_tts.stt.cli", "status", "--socket", str(sock)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            assert proc.returncode == 0, proc.stderr
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            assert payload["ok"] is True
            assert payload["state"] in ("loading", "ready")  # auto-warmup may have finished
        finally:
            w.stop()
