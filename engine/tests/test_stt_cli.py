"""agent-tts-stt CLI tests: exit codes, machine-readable stdout, metadata.

Subprocess smoke (`--help`, status vs live worker) goes through the real
module entry; request paths run main() directly against a real local
worker with a fake model. No model, network, or user state is touched.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_tts.stt.cli as stt_cli
import agent_tts.stt.worker as sttw
from agent_tts.stt.transcriber import MissingExtraError
from agent_tts.stt.worker import SttWorker

from test_stt_worker import FakeModel, fake_transcriber, start_worker

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def run_cli(argv: list[str]) -> int:
    return stt_cli.main(argv)


def last_json(stdout: str) -> dict:
    return json.loads(stdout.strip().splitlines()[-1])


class TestHelpSmoke:
    def test_module_help_lists_operations(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "agent_tts.stt.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert proc.returncode == 0
        for op in ("pull", "serve", "status", "transcribe"):
            assert op in proc.stdout

    def test_missing_subcommand_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            run_cli([])
        assert exc.value.code != 0


class TestStatus:
    def test_unavailable_socket_is_actionable_nonzero(self, tmp_path, capsys):
        code = run_cli(["status", "--socket", str(tmp_path / "missing.sock")])
        out = capsys.readouterr()
        assert code == 5  # worker_unavailable
        payload = last_json(out.out)
        assert payload["ok"] is False
        assert payload["error"]["kind"] == "worker_unavailable"
        assert "agent-tts-stt serve" in out.err

    def test_status_against_live_worker(self, tmp_path, capsys):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        try:
            assert run_cli(["status", "--socket", str(sock)]) == 0
            payload = last_json(capsys.readouterr().out)
            assert payload["ok"] is True
            assert payload["state"] in ("loading", "ready")
        finally:
            w.stop()


class TestTranscribe:
    def test_missing_extra_maps_to_exit_6_via_worker(self, tmp_path, capsys, monkeypatch):
        import sys as _sys

        from agent_tts.stt.transcriber import SttSettings, Transcriber

        monkeypatch.setitem(_sys.modules, "huggingface_hub", None)
        sock = tmp_path / "stt.sock"
        # default cached seam -> real hub probe -> typed missing_extra gate
        t = Transcriber(SttSettings(), loader=lambda: FakeModel())
        w = start_worker(sock, t)
        audio = tmp_path / "clip.webm"
        audio.write_bytes(b"x")
        try:
            code = run_cli(["transcribe", "--file", str(audio), "--socket", str(sock)])
            assert code == 6  # missing_extra, not generic 1
            payload = last_json(capsys.readouterr().out)
            assert payload["error"]["kind"] == "missing_extra"
            assert "agent-tts[stt]" in payload["error"]["message"]
        finally:
            w.stop()

    def test_happy_path_prints_json_text(self, tmp_path, capsys):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        audio = tmp_path / "clip.webm"
        audio.write_bytes(b"fake-webm-bytes")
        try:
            assert run_cli(["transcribe", "--file", str(audio), "--socket", str(sock)]) == 0
            payload = last_json(capsys.readouterr().out)
            assert payload == {"ok": True, "text": "hola rebaño"}
        finally:
            w.stop()

    def test_model_unavailable_exit_code(self, tmp_path, capsys):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock, fake_transcriber(cached=False))
        audio = tmp_path / "clip.webm"
        audio.write_bytes(b"x")
        try:
            code = run_cli(["transcribe", "--file", str(audio), "--socket", str(sock)])
            assert code == 4  # model_unavailable
            payload = last_json(capsys.readouterr().out)
            assert payload["error"]["kind"] == "model_unavailable"
            assert "agent-tts-stt pull" in payload["error"]["message"]
        finally:
            w.stop()

    def test_busy_exit_code(self, tmp_path, capsys):
        sock = tmp_path / "stt.sock"
        w = start_worker(sock)
        audio = tmp_path / "clip.webm"
        audio.write_bytes(b"x")
        try:
            with w._infer_lock:
                code = run_cli(
                    ["transcribe", "--file", str(audio), "--socket", str(sock), "--timeout", "5"]
                )
            assert code == 3  # busy
            payload = last_json(capsys.readouterr().out)
            assert payload["error"]["kind"] == "busy"
        finally:
            w.stop()

    def test_missing_file_is_typed_error(self, tmp_path, capsys):
        code = run_cli(["transcribe", "--file", str(tmp_path / "nope.webm")])
        assert code == 1
        payload = last_json(capsys.readouterr().out)
        assert payload["ok"] is False

    def test_oversize_file_rejected_before_sending(self, tmp_path, capsys, monkeypatch):
        import agent_tts.stt.worker as sttw

        monkeypatch.setattr(sttw, "MAX_AUDIO_BYTES", 8)
        audio = tmp_path / "big.webm"
        audio.write_bytes(b"x" * 16)
        code = run_cli(["transcribe", "--file", str(audio)])
        assert code == 1
        payload = last_json(capsys.readouterr().out)
        assert payload["error"]["kind"] == "invalid_request"


class TestPull:
    def test_pull_uses_injected_download_seam(self, capsys, monkeypatch):
        calls = []

        def fake_pull(settings, downloader=None):
            calls.append(settings.model)
            return "/cache/snapshot"

        monkeypatch.setattr(stt_cli, "pull_model", fake_pull)
        assert run_cli(["pull", "--model", "base"]) == 0
        assert calls == ["base"]
        assert "/cache/snapshot" in capsys.readouterr().out

    def test_pull_missing_extra_names_install_hint(self, capsys, monkeypatch):
        def boom(settings, downloader=None):
            raise MissingExtraError("faster-whisper is not installed; pip install 'agent-tts[stt]'")

        monkeypatch.setattr(stt_cli, "pull_model", boom)
        code = run_cli(["pull"])
        assert code == 6  # missing_extra
        payload = last_json(capsys.readouterr().out)
        assert payload["error"]["kind"] == "missing_extra"
        assert "agent-tts[stt]" in payload["error"]["message"]


class TestServe:
    def test_native_windows_serve_is_typed_unsupported(self, tmp_path, capsys, monkeypatch):
        import agent_tts.stt.worker as sttw

        monkeypatch.setattr(sttw, "_is_windows", lambda: True)
        code = run_cli(["serve", "--socket", str(tmp_path / "stt.sock")])
        assert code == 7  # transport_unsupported
        payload = last_json(capsys.readouterr().out)
        assert payload["error"]["kind"] == "transport_unsupported"
        assert "WSL" in payload["error"]["message"]

    def test_serve_then_shutdown_op_lifecycle(self, tmp_path):
        import threading
        import time

        import agent_tts.stt.worker as sttw
        from agent_tts.stt.transcriber import SttSettings, Transcriber

        sock = tmp_path / "stt.sock"
        t = Transcriber(SttSettings(), loader=lambda: FakeModel())
        w = SttWorker(t, socket_path=str(sock))
        served = {}

        def run_serve():
            served["report"] = w.serve()

        thread = threading.Thread(target=run_serve)
        thread.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                sttw.stt_request({"op": "status"}, socket_path=str(sock), timeout=5)
                break
            except Exception:
                time.sleep(0.05)

        reply = sttw.stt_request({"op": "shutdown"}, socket_path=str(sock), timeout=5)
        assert reply["ok"]
        thread.join(timeout=10)
        assert served["report"]["clean"] is True
        assert not sock.exists()


class TestServeSubprocessLifecycle:
    """Authorized fake-model worker subprocess (no real model/network).

    Separate concerns: (a) resident model reuse across independent CLI
    client processes in the clean path; (b) shutdown honesty while a fake
    inference is verifiably blocked (entered handshake) and while the
    loader itself is stuck — bounded process exit, no cancellation claim.
    Runtime lives under /tmp/opencode/f4-stt-runtime (parent-owned
    removal), never under pytest's basetemp.
    """

    SERVE_PROGRAM = r"""
import os, sys, time
import agent_tts.stt.cli as cli
import agent_tts.stt.transcriber as tr

count_file, block_file, entered_file, mode, socket_path = sys.argv[1:6]

def load_model():
    # one 'x' per MODEL CONSTRUCTION — the residency metric
    with open(count_file, "a") as f:
        f.write("x")
    if mode == "load-block":
        with open(entered_file, "w") as f:
            f.write("loader")
        while os.path.exists(block_file):
            time.sleep(0.02)
    return FakeModel()

class FakeModel:
    def transcribe(self, path, language=None):
        if mode == "infer-block":
            with open(entered_file, "w") as f:
                f.write("inference")
        while os.path.exists(block_file):
            time.sleep(0.02)
        return (iter([type("S", (), {"text": " rebaño subprocess "})]),
                type("I", (), {"language": "es"}))

def make(settings, **kw):
    return tr.Transcriber(settings, loader=load_model, cached=lambda: True)

cli.Transcriber = make
sys.exit(cli.main(["serve", "--socket", socket_path]))
"""

    def _runtime_dir(self, name: str) -> Path:
        root = Path("/tmp/opencode/f4-stt-runtime")
        run = root / f"{name}-{os.getpid()}-{time.monotonic_ns()}"
        run.mkdir(parents=True, mode=0o700, exist_ok=True)
        return run

    def _spawn(self, run: Path, mode: str):
        socket_path = run / "stt.sock"
        count_file = run / "loads.txt"
        block_file = run / "block"
        entered_file = run / "entered"
        if mode != "normal":
            block_file.write_text("")
        env = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(Path(__file__).resolve().parents[2] / "engine" / "src"),
            AGENT_TTS_STT_SNAPSHOT_DIR=str(run / "snapshots"),
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", self.SERVE_PROGRAM,
             str(count_file), str(block_file), str(entered_file), mode, str(socket_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        return proc, socket_path, count_file, block_file, entered_file

    def _wait_status(self, socket_path, timeout=15):
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return sttw.stt_request({"op": "status"}, socket_path=str(socket_path), timeout=5)
            except Exception:
                time.sleep(0.05)
        raise AssertionError("worker did not come up")

    def test_resident_reuse_across_cli_clients(self, tmp_path):
        """Clean path: one model load serving independent CLI processes."""
        run = self._runtime_dir("reuse")
        proc, socket_path, count_file, block_file, entered = self._spawn(run, "normal")
        try:
            assert self._wait_status(socket_path)["ok"]
            audio = tmp_path / "clip.webm"
            audio.write_bytes(b"fake-bytes")
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=str(Path(__file__).resolve().parents[2] / "engine" / "src"))
            for _ in range(2):  # two independent client processes
                out = subprocess.run(
                    [sys.executable, "-m", "agent_tts.stt.cli", "transcribe",
                     "--file", str(audio), "--socket", str(socket_path)],
                    capture_output=True, text=True, timeout=60, env=env,
                )
                assert out.returncode == 0, out.stderr
                assert json.loads(out.stdout.strip().splitlines()[-1])["text"] == "rebaño subprocess"
            assert count_file.read_text().count("x") == 1  # resident across clients
            assert sttw.stt_request({"op": "shutdown"}, socket_path=str(socket_path), timeout=5)["ok"]
            _out, stderr = proc.communicate(timeout=15)
            assert proc.returncode == 0, stderr  # clean exit
            assert not socket_path.exists()
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)

    def test_shutdown_honest_with_stuck_inference(self, tmp_path):
        """Entered handshake: wait until inference is VERIFIABLY blocked,
        then status responsiveness, then shutdown BEFORE any release."""
        run = self._runtime_dir("infer-hang")
        proc, socket_path, count_file, block_file, entered = self._spawn(run, "infer-block")
        try:
            assert self._wait_status(socket_path)["ok"]
            import threading
            import time

            def stuck_call():
                try:
                    sttw.stt_request(
                        {"op": "transcribe", "audio_b64": base64.b64encode(b"x").decode()},
                        socket_path=str(socket_path), timeout=60,
                    )
                except Exception:  # noqa: BLE001 — expected: server exits mid-reply
                    pass

            stuck = threading.Thread(target=stuck_call)
            stuck.start()
            # wait for the entered marker: inference IS blocked now
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not entered.exists():
                time.sleep(0.05)
            assert entered.exists() and entered.read_text() == "inference"

            started = time.monotonic()
            assert sttw.stt_request({"op": "status"}, socket_path=str(socket_path), timeout=5)["ok"]
            assert time.monotonic() - started < 3  # responsive during the hang

            # shutdown BEFORE releasing: exit must be bounded and honest
            assert sttw.stt_request({"op": "shutdown"}, socket_path=str(socket_path), timeout=5)["ok"]
            _out, stderr = proc.communicate(timeout=15)  # bounded process exit
            assert proc.returncode != 0  # not clean — unfinished inference
            assert "hung" in stderr
            stuck.join(timeout=10)  # daemon-thread behavior only; no cancellation claim
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()
                    proc.wait(timeout=10)

    def test_shutdown_honest_with_stuck_loader(self, tmp_path):
        """F2 subprocess proof: loader stuck before any transcribe ->
        status stays loading; shutdown reports unfinished startup; the
        process still exits bounded."""
        run = self._runtime_dir("load-hang")
        proc, socket_path, count_file, block_file, entered = self._spawn(run, "load-block")
        try:
            status = self._wait_status(socket_path)
            assert status["state"] == "loading"  # warmup stuck, truthfully
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not entered.exists():
                time.sleep(0.05)
            assert entered.read_text() == "loader"  # loader verifiably blocked

            assert sttw.stt_request({"op": "shutdown"}, socket_path=str(socket_path), timeout=5)["ok"]
            _out, stderr = proc.communicate(timeout=15)
            assert proc.returncode != 0
            assert "hung" in stderr
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()
                    proc.wait(timeout=10)


class TestPackagingMetadata:
    def test_console_script_registered_without_touching_agent_tts(self):
        text = PYPROJECT.read_text(encoding="utf-8")
        scripts = re.search(r"\[project\.scripts\](.*?)(\n\[|\Z)", text, re.S).group(1)
        assert 'agent-tts = "agent_tts.cli:main"' in scripts
        assert 'agent-tts-stt = "agent_tts.stt.cli:main"' in scripts

    def test_stt_extra_is_optional_not_base_dependency(self):
        text = PYPROJECT.read_text(encoding="utf-8")
        deps = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M).group(1)
        assert "faster-whisper" not in deps
        assert re.search(r"^stt\s*=\s*\[", text, re.M)
        assert "faster-whisper" in text

    def test_base_tts_import_has_no_stt_burden(self):
        code = (
            "import sys, agent_tts; "
            "import agent_tts.stt; "
            "print('faster_whisper' in sys.modules or 'huggingface_hub' in sys.modules)"
        )
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False"


class TestBaseInstallIsolation:
    """The STT CLI must be usable on an interpreter without TTS deps.

    Real-world case (2026-10-07 smoke test): the brain venv ships
    faster-whisper but not miniaudio; `python -m agent_tts.stt.cli` died in
    the package __init__'s eager TTS imports before reaching main().
    """

    def test_package_import_succeeds_without_tts_extras(self) -> None:
        code = (
            "import sys\n"
            "sys.modules['miniaudio'] = None\n"
            "sys.modules['edge_tts'] = None\n"
            "import agent_tts\n"
            "import agent_tts.stt.cli as stt_cli\n"
            "assert callable(stt_cli.main)\n"
            "print('ok')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().endswith("ok")

    def test_tts_attribute_access_still_lazy_fails_cleanly_without_deps(self) -> None:
        code = (
            "import sys\n"
            "sys.modules['miniaudio'] = None\n"
            "import agent_tts\n"
            "try:\n"
            "    agent_tts.audio_duration\n"
            "except ImportError:\n"
            "    print('typed-importerror')\n"
            "else:\n"
            "    print('unexpected-success')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert proc.returncode == 0, proc.stderr
        assert "typed-importerror" in proc.stdout


class TestShutdownSubcommand:
    """Operators need an explicit client-side shutdown verb (2026-10-07 real
    smoke test: worker protocol supports it, the CLI did not expose it).

    Protocol: the worker replies {"ok": true, "shutting_down": true} — the
    final clean/hung report belongs to the serve process (its stderr/exit
    code) — and the CLI polls until the socket disappears (bounded).
    """

    def test_shutdown_polls_until_socket_gone_then_zero(self, capsys, tmp_path) -> None:
        sent = []
        sock = tmp_path / "s.sock"
        sock.write_bytes(b"x")  # socket file exists at first

        def fake_request(payload, socket_path=None, timeout=None):
            sent.append(dict(payload))
            return {"ok": True, "shutting_down": True}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sttw, "stt_request", fake_request)
        orig_exists = os.path.exists  # Path.exists() would recurse through the patch

        def fake_exists(p):
            return str(p) == str(sock) and orig_exists(str(sock))

        monkeypatch.setattr(stt_cli.os.path, "exists", fake_exists)
        calls = {"n": 0}
        real_sleep = stt_cli.time.sleep

        def fast_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 2:
                sock.unlink()  # worker finishes right after the 2nd poll

        monkeypatch.setattr(stt_cli.time, "sleep", fast_sleep)
        try:
            rc = stt_cli.main(["shutdown", "--socket", str(sock)])
        finally:
            monkeypatch.undo()
        assert rc == 0
        assert sent == [{"op": "shutdown"}]
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {"ok": True, "stopped": True}

    def test_shutdown_worker_never_stops_reports_nonzero(self, capsys, tmp_path) -> None:
        sock = tmp_path / "s.sock"
        sock.write_bytes(b"x")

        def fake_request(payload, socket_path=None, timeout=None):
            return {"ok": True, "shutting_down": True}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sttw, "stt_request", fake_request)
        monkeypatch.setattr(stt_cli.time, "sleep", lambda _: None)
        monkeypatch.setattr(stt_cli.os.path, "exists", lambda p: True)
        try:
            rc = stt_cli.main(
                ["shutdown", "--socket", str(sock), "--timeout", "0.05"]
            )
        finally:
            monkeypatch.undo()
        assert rc == 1
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {"ok": False, "stopped": False}

    def test_shutdown_still_reports_worker_unavailable(self, capsys, tmp_path) -> None:
        def unavailable(payload, socket_path=None, timeout=None):
            raise sttw.WorkerUnavailableError(
                "no STT socket at x; start the worker with `agent-tts-stt serve`"
            )

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sttw, "stt_request", unavailable)
        try:
            rc = stt_cli.main(["shutdown", "--socket", str(tmp_path / "nope.sock")])
        finally:
            monkeypatch.undo()
        assert rc == 5
