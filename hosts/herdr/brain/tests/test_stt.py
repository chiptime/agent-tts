"""Tests for server-side STT: transcriber lifecycle + /transcribe endpoint.

HARD RULES proven here: the whisper model is always a fake loader — no test
ever loads or downloads the real model — and the boot warmup thread only
runs when the model is already local AND warmup is enabled.
"""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.stt import (
    PULL_COMMAND,
    STATE_READY,
    STATE_UNAVAILABLE,
    Transcriber,
)


@pytest.fixture
def audio_dir(tmp_path) -> Path:
    out = tmp_path / "audio"
    out.mkdir()
    return out


class FakeModel:
    """faster-whisper test double: returns canned segments per audio."""

    last_path: str | None = None
    fail = False

    def transcribe(self, path, language=None):
        if FakeModel.fail:
            raise RuntimeError("decode failed")
        FakeModel.last_path = path
        return (
            iter([type("Seg", (), {"text": " el rebaño está en orden "})]),
            type("Info", (), {"language": "es"}),
        )


def make_transcriber(settings: Settings, **kwargs) -> Transcriber:
    return Transcriber(settings, loader=lambda: FakeModel(), **kwargs)


class TestTranscriber:
    def test_starts_loading_and_warms_up_to_ready(self, settings):
        t = make_transcriber(settings)
        assert t.state == "loading"
        t.warmup()
        assert t.state == "ready"
        assert t.error() is None

    def test_warmup_failure_marks_unavailable_with_error(self, settings):
        def boom():
            raise RuntimeError("model download failed")

        t = Transcriber(settings, loader=boom)
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert "model download failed" in t.error()

    def test_transcribe_bytes_writes_temp_and_trims(self, settings, tmp_path):
        t = make_transcriber(settings)
        t.warmup()
        out = t.transcribe_bytes(b"fake-bytes.mp3", suffix=".mp3")
        assert out == "el rebaño está en orden"
        # temp file cleanup
        assert FakeModel.last_path and not __import__("os").path.exists(FakeModel.last_path)

    def test_transcribe_before_warmup_lazy_loads(self, settings):
        t = make_transcriber(settings)
        assert t.transcribe_bytes(b"x") == "el rebaño está en orden"
        assert t.state == "loading"  # warmup() not called: state untouched

    def test_model_loaded_once_under_concurrency(self, settings):
        loads: list = []

        def counting_loader():
            loads.append(1)
            return FakeModel()

        t = Transcriber(settings, loader=counting_loader)
        threads = [
            threading.Thread(target=lambda: t.transcribe_bytes(b"x")) for _ in range(4)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(loads) == 1

    def test_maybe_start_warmup_without_model_marks_unavailable(self, settings):
        t = make_transcriber(settings, cached=lambda: False)
        assert t.maybe_start_warmup() is None  # no thread, no download attempt
        assert t.state == STATE_UNAVAILABLE
        assert PULL_COMMAND in t.error()

    def test_maybe_start_warmup_with_model_starts_thread(self, settings):
        t = make_transcriber(settings, cached=lambda: True)
        thread = t.maybe_start_warmup()
        assert thread is not None and thread.daemon
        thread.join(timeout=5)
        assert t.state == STATE_READY


class TestTranscribeEndpoint:
    def _app(self, settings, audio_dir, transcriber):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        return TestClient(create_app(settings=cfg, version="test", transcriber=transcriber))

    def test_transcribe_returns_text(self, settings, audio_dir):
        t = make_transcriber(settings)
        t.warmup()  # only warmup() flips state to READY; HTTP never lazy-loads
        client = self._app(settings, audio_dir, t)
        resp = client.post(
            "/transcribe",
            files={"audio": ("clip.webm", io.BytesIO(b"fake-webm-bytes"), "audio/webm")},
        )
        assert resp.status_code == 200
        assert resp.json() == {"text": "el rebaño está en orden"}

    def test_transcribe_503_while_loading(self, settings, audio_dir):
        transcriber = Transcriber(settings, loader=lambda: FakeModel())  # never warmed
        client = self._app(settings, audio_dir, transcriber)
        resp = client.post(
            "/transcribe",
            files={"audio": ("clip.webm", io.BytesIO(b"x"), "audio/webm")},
        )
        assert resp.status_code == 503
        assert "cargando" in resp.json()["detail"]

    def test_transcribe_503_when_unavailable_names_pull_command(self, settings, audio_dir):
        def boom():
            raise RuntimeError("no model")

        t = Transcriber(settings, loader=boom)
        t.warmup()
        client = self._app(settings, audio_dir, t)
        resp = client.post(
            "/transcribe",
            files={"audio": ("clip.webm", io.BytesIO(b"x"), "audio/webm")},
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "no disponible" in detail
        assert PULL_COMMAND in detail  # the hint names the exact command

    def test_transcribe_503_on_decode_failure(self, settings, audio_dir):
        t = make_transcriber(settings)
        t.warmup()
        FakeModel.fail = True
        try:
            client = self._app(settings, audio_dir, t)
            resp = client.post(
                "/transcribe",
                files={"audio": ("clip.webm", io.BytesIO(b"junk"), "audio/webm")},
            )
            assert resp.status_code == 503
            assert "transcribir" in resp.json()["detail"]
        finally:
            FakeModel.fail = False

    def test_transcribe_empty_audio_400(self, settings, audio_dir):
        t = make_transcriber(settings)
        t.warmup()
        client = self._app(settings, audio_dir, t)
        resp = client.post(
            "/transcribe", files={"audio": ("clip.webm", io.BytesIO(b""), "audio/webm")}
        )
        assert resp.status_code == 400

    def test_health_reports_stt_state(self, settings, audio_dir):
        t = make_transcriber(settings)
        client = self._app(settings, audio_dir, t)
        health = client.get("/health").json()
        assert health["stt"] == "loading"
        t.warmup()
        assert client.get("/health").json()["stt"] == STATE_READY


class TestBootPolicy:
    """The LEGACY backend's boot policy (stt_backend="builtin"): the
    server NEVER auto-downloads — no thread without local files, no
    thread at all when test settings disable warmup. The engine backend's
    boot policy lives in test_stt_engine.py."""

    def _cfg(self, settings, audio_dir, **overrides):
        base = {
            **settings.__dict__,
            "audio_dir": str(audio_dir),
            "stt_backend": "builtin",  # these tests patch the legacy Transcriber
            **overrides,
        }
        return Settings(**base)

    def test_boot_without_model_reports_unavailable_with_hint(
        self, settings, audio_dir, monkeypatch
    ):
        import herdr_brain.server as server_module

        monkeypatch.setattr(
            server_module, "Transcriber",
            lambda cfg: Transcriber(cfg, loader=lambda: FakeModel(), cached=lambda: False),
        )
        client = TestClient(
            create_app(settings=self._cfg(settings, audio_dir, stt_warmup=True), version="t")
        )
        assert client.get("/health").json()["stt"] == STATE_UNAVAILABLE
        resp = client.post(
            "/transcribe",
            files={"audio": ("clip.webm", io.BytesIO(b"x"), "audio/webm")},
        )
        assert resp.status_code == 503
        assert PULL_COMMAND in resp.json()["detail"]

    def test_boot_with_model_warms_in_background(self, settings, audio_dir, monkeypatch):
        import herdr_brain.server as server_module

        warmed = threading.Event()
        real_warmup = Transcriber.warmup

        def visible_warmup(self):
            real_warmup(self)
            warmed.set()

        monkeypatch.setattr(Transcriber, "warmup", visible_warmup)
        monkeypatch.setattr(
            server_module, "Transcriber",
            lambda cfg: Transcriber(
                cfg, loader=lambda: FakeModel(), cached=lambda: True
            ),
        )
        client = TestClient(
            create_app(settings=self._cfg(settings, audio_dir, stt_warmup=True), version="t")
        )
        assert warmed.wait(timeout=5), "warmup thread did not run"
        assert client.get("/health").json()["stt"] == STATE_READY

    def test_boot_with_warmup_disabled_never_starts_the_thread(
        self, settings, audio_dir, monkeypatch
    ):
        """Default test settings (stt_warmup=False): no warmup, no presence
        check, no model — the app still builds and /health stays loading."""
        import herdr_brain.server as server_module

        def forbidden(self):
            raise AssertionError("warmup must not run when stt_warmup=False")

        monkeypatch.setattr(Transcriber, "maybe_start_warmup", forbidden)
        monkeypatch.setattr(
            server_module, "Transcriber",
            lambda cfg: Transcriber(cfg, loader=lambda: FakeModel()),
        )
        client = TestClient(
            create_app(settings=self._cfg(settings, audio_dir), version="t")
        )
        assert client.get("/health").json()["stt"] == "loading"


class TestModelPresence:
    """Offline presence check — never raises, never downloads."""

    def test_local_path_shortcut(self, tmp_path):
        from herdr_brain.stt import model_is_cached

        assert model_is_cached(str(tmp_path)) is True

    def test_all_model_files_present(self, monkeypatch):
        import huggingface_hub

        from herdr_brain.stt import model_is_cached

        monkeypatch.setattr(
            huggingface_hub, "try_to_load_from_cache", lambda repo, f: "/cache/" + f
        )
        assert model_is_cached("small") is True

    def test_missing_model_bin_means_absent(self, monkeypatch):
        import huggingface_hub

        from herdr_brain.stt import model_is_cached

        def lookup(repo, filename):
            return None if filename == "model.bin" else "/cache/" + filename

        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lookup)
        assert model_is_cached("small") is False

    def test_interrupted_snapshot_with_weights_still_counts(self, monkeypatch):
        """An interrupted first download leaves README/.gitattributes
        missing; the weights are complete and usable — presence must be
        True (the check only asks for inference files, no full snapshot)."""
        import huggingface_hub

        from herdr_brain.stt import model_is_cached

        def lookup(repo, filename):
            # Every inference file resolves; snapshot_download would still
            # raise IncompleteSnapshotError for the missing metadata files.
            return "/cache/" + filename if filename != "README.md" else None

        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lookup)
        assert model_is_cached("small") is True

    def test_hub_errors_mean_absent(self, monkeypatch):
        import huggingface_hub

        from herdr_brain.stt import model_is_cached

        def boom(repo, filename):
            raise RuntimeError("offline")

        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", boom)
        assert model_is_cached("small") is False


class TestCli:
    def test_module_help_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "herdr_brain.stt", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0
        assert "pull" in proc.stdout

    def test_pull_subcommand_requires_nothing_else(self):
        from herdr_brain.stt import main

        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0  # subcommand is required; no accidental pull
