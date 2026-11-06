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
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.stt import (
    LIVE_NOTICE_MODEL_ABSENT,
    LIVE_NOTICE_OOM,
    PULL_COMMAND,
    STATE_LOADING,
    STATE_READY,
    STATE_UNAVAILABLE,
    LiveTranscriber,
    Transcriber,
    resolve_snapshot_dir,
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
    last_language: str | None = None
    last_initial_prompt: str | None = None

    def transcribe(self, path, language=None, initial_prompt=None):
        if FakeModel.fail:
            raise RuntimeError("decode failed")
        FakeModel.last_path = path
        FakeModel.last_language = language
        FakeModel.last_initial_prompt = initial_prompt
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


class TestResolveSnapshotDir:
    """S4: live-model reference resolution — name / model dir / HF repo
    cache dir -> the exact path WhisperModel accepts. Never touches the
    network."""

    def test_repo_cache_dir_resolves_via_refs_main(self, tmp_path):
        repo = tmp_path / "models--Systran--faster-whisper-large-v3"
        (repo / "snapshots" / "aaa").mkdir(parents=True)
        (repo / "snapshots" / "bbb").mkdir()
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("bbb\n", encoding="utf-8")
        assert resolve_snapshot_dir(str(repo)) == str(repo / "snapshots" / "bbb")

    def test_repo_cache_dir_with_single_snapshot_and_no_ref(self, tmp_path):
        repo = tmp_path / "models--Systran--faster-whisper-large-v3"
        (repo / "snapshots" / "only").mkdir(parents=True)
        assert resolve_snapshot_dir(str(repo)) == str(repo / "snapshots" / "only")

    def test_plain_name_and_plain_model_dir_pass_through(self, tmp_path):
        assert resolve_snapshot_dir("large-v3") == "large-v3"
        model_dir = tmp_path / "ct2-export"
        model_dir.mkdir()
        assert resolve_snapshot_dir(str(model_dir)) == str(model_dir)

    def test_repo_cache_dir_without_usable_snapshot_is_none(self, tmp_path):
        empty = tmp_path / "empty-repo"
        (empty / "snapshots").mkdir(parents=True)
        assert resolve_snapshot_dir(str(empty)) is None
        two = tmp_path / "two-snapshots"
        (two / "snapshots" / "a").mkdir(parents=True)
        (two / "snapshots" / "b").mkdir()
        assert resolve_snapshot_dir(str(two)) is None


class TestLiveTranscriber:
    """S4 (D7/D12/D13): the live engine's dedicated in-process
    transcriber — loaded strictly from local files on the configured GPU,
    degrading ONCE to the fallback model with a notice reason when the
    primary is absent or CUDA/OOM strikes."""

    @staticmethod
    def _local_model(tmp_path, name="live-large", weights=True):
        model_dir = tmp_path / name
        model_dir.mkdir(parents=True, exist_ok=True)
        if weights:
            (model_dir / "model.bin").write_bytes(b"weights")
        return model_dir

    def test_live_load_is_local_files_only_on_configured_gpu(
        self, settings, tmp_path, monkeypatch
    ):
        import faster_whisper

        captured = {}

        class FakeWhisperModel:
            def __init__(self, model, device=None, compute_type=None, local_files_only=False):
                captured.update(
                    model=model,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=local_files_only,
                )

        monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisperModel)
        # Repo-cache layout: WhisperModel must receive the resolved
        # snapshot dir, never the repo root.
        repo = tmp_path / "models--Systran--faster-whisper-large-v3"
        snap = repo / "snapshots" / "edaa"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"weights")
        cfg = replace(
            settings,
            live_stt_model=str(repo),
            live_stt_device="cuda",
            live_stt_compute="int8_float16",
        )
        t = LiveTranscriber(cfg)
        t.warmup()
        assert t.state == STATE_READY
        assert captured["model"] == str(snap)
        assert captured["device"] == "cuda"
        assert captured["compute_type"] == "int8_float16"
        assert captured["local_files_only"] is True  # HARD RULE: never downloads

    def test_oom_at_load_degrades_to_fallback_with_notice(self, settings, tmp_path):
        calls = []

        def cuda_oom():
            calls.append("primary")
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

        t = LiveTranscriber(
            replace(settings, live_stt_model=str(self._local_model(tmp_path))),
            loader=cuda_oom,
            fallback_loader=lambda: (calls.append("fallback"), FakeModel())[1],
        )
        t.warmup()
        assert t.state == STATE_READY  # degraded, still serving live audio
        assert t.notice_reason == LIVE_NOTICE_OOM
        assert calls == ["primary", "fallback"]
        assert t.transcribe_in_memory(b"fake-wav") == "el rebaño está en orden"

    def test_oom_at_transcribe_degrades_once_and_retries(self, settings, tmp_path):
        class OOMAtTranscribe:
            def transcribe(self, path, language=None, initial_prompt=None):
                raise RuntimeError("CUDA error: out of memory during decode")

        loads = []

        def counting_primary():
            loads.append("primary")
            return OOMAtTranscribe()

        t = LiveTranscriber(
            replace(settings, live_stt_model=str(self._local_model(tmp_path))),
            loader=counting_primary,
            fallback_loader=FakeModel,
        )
        # First call: the primary OOMs at transcribe -> degrade once ->
        # retry the same audio on the fallback.
        assert t.transcribe_in_memory(b"fake-wav") == "el rebaño está en orden"
        assert t.notice_reason == LIVE_NOTICE_OOM
        # Later calls ride the fallback; the primary is never reloaded.
        assert t.transcribe_in_memory(b"fake-wav") == "el rebaño está en orden"
        assert loads == ["primary"]

    def test_absent_primary_degrades_without_touching_the_gpu(self, settings, tmp_path):
        calls = []
        repo = tmp_path / "repo-no-snapshot"
        (repo / "snapshots").mkdir(parents=True)  # unresolvable: no ref, no snapshot
        t = LiveTranscriber(
            replace(settings, live_stt_model=str(repo)),
            loader=lambda: (calls.append("primary"), FakeModel())[1],
            fallback_loader=lambda: (calls.append("fallback"), FakeModel())[1],
        )
        t.warmup()
        assert t.state == STATE_READY
        assert t.notice_reason == LIVE_NOTICE_MODEL_ABSENT
        assert calls == ["fallback"]  # the primary loader never runs

        # A snapshot dir without the CT2 weights counts as absent too.
        t2 = LiveTranscriber(
            replace(settings, live_stt_model=str(self._local_model(tmp_path, weights=False))),
            loader=lambda: (calls.append("primary2"), FakeModel())[1],
            fallback_loader=lambda: (calls.append("fallback2"), FakeModel())[1],
        )
        t2.warmup()
        assert t2.notice_reason == LIVE_NOTICE_MODEL_ABSENT

    def test_non_cuda_load_error_propagates_without_degrading(self, settings, tmp_path):
        def forbidden_fallback():
            raise AssertionError("fallback must not load on non-CUDA errors")

        def corrupt():
            raise RuntimeError("could not load model weights")

        t = LiveTranscriber(
            replace(settings, live_stt_model=str(self._local_model(tmp_path))),
            loader=corrupt,
            fallback_loader=forbidden_fallback,
        )
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert t.notice_reason is None
        assert "could not load" in t.error()

    def test_boot_warmup_skipped_when_primary_absent(self, settings, tmp_path, monkeypatch):
        """Boot rule: no warm thread when the primary is not local — a
        default deploy without HERDR_BRAIN_LIVE_STT_MODEL stays cold until
        first use (and no test ever loads a real model through boot)."""

        def forbidden_warmup():
            raise AssertionError("warmup must not run when the primary is absent")

        monkeypatch.setattr(LiveTranscriber, "warmup", forbidden_warmup)
        repo = tmp_path / "repo-no-snapshot"
        (repo / "snapshots").mkdir(parents=True)
        t = LiveTranscriber(replace(settings, live_stt_model=str(repo)))
        assert t.maybe_start_warmup() is None
        assert t.state == STATE_LOADING

    def test_boot_warmup_with_present_primary_starts_thread(self, settings, tmp_path):
        t = LiveTranscriber(
            replace(
                settings,
                live_stt_model=str(self._local_model(tmp_path)),
                live_stt_fallback_model="small",
            ),
            loader=lambda: FakeModel(),
        )
        thread = t.maybe_start_warmup()
        assert thread is not None and thread.daemon
        thread.join(timeout=5)
        assert t.state == STATE_READY


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


class TestLiveStreamingHints:
    """T3 (PRD-04/D8): the live socket forces the app locale on
    transcription and passes an operator-editable technical-vocabulary
    hint as the recognition prompt."""

    def test_transcribe_in_memory_forwards_language_and_prompt(self, settings):
        t = make_transcriber(settings)
        t.transcribe_in_memory(b"fake-wav", language="es", initial_prompt="commit push")
        assert FakeModel.last_language == "es"
        assert FakeModel.last_initial_prompt == "commit push"

    def test_transcribe_in_memory_defaults_unchanged(self, settings):
        """Preserve: with no hints the model call keeps today's shape
        (language=None, no prompt)."""
        t = make_transcriber(settings)
        t.transcribe_in_memory(b"fake-wav")
        assert FakeModel.last_language is None
        assert FakeModel.last_initial_prompt is None

    def test_live_transcriber_forwards_hints_through_degradation(
        self, settings, tmp_path
    ):
        """D13+D8: hints survive the OOM degrade-and-retry path."""
        t = LiveTranscriber(
            replace(settings, live_stt_model=str(
                TestLiveTranscriber._local_model(tmp_path)
            )),
            loader=lambda: _OOMOnPurpose(),
            fallback_loader=FakeModel,
        )
        out = t.transcribe_in_memory(b"fake-wav", language="en", initial_prompt="PR")
        assert out == "el rebaño está en orden"
        assert FakeModel.last_language == "en"
        assert FakeModel.last_initial_prompt == "PR"

    def test_default_vocabulary_hint(self, monkeypatch):
        from herdr_brain.stt import live_vocabulary_hint

        monkeypatch.delenv("HERDR_BRAIN_LIVE_STT_VOCABULARY", raising=False)
        hint = live_vocabulary_hint()
        assert hint is not None
        for term in ("commit", "push", "PR", "rama", "herdr", "opencode"):
            assert term in hint

    def test_env_vocabulary_overrides_the_default(self, monkeypatch):
        from herdr_brain.stt import live_vocabulary_hint

        monkeypatch.setenv("HERDR_BRAIN_LIVE_STT_VOCABULARY", "k8s, deploy, staging")
        assert live_vocabulary_hint() == "k8s deploy staging"

    def test_empty_env_disables_the_hint(self, monkeypatch):
        from herdr_brain.stt import live_vocabulary_hint

        monkeypatch.setenv("HERDR_BRAIN_LIVE_STT_VOCABULARY", "   ,  ")
        assert live_vocabulary_hint() is None

    def test_vocabulary_hint_is_bounded_and_control_char_free(self, monkeypatch):
        """Hostile or oversized vocabulary input can never blow up the
        whisper prompt: bounded length, no newlines/control characters."""
        from herdr_brain.stt import live_vocabulary_hint

        monkeypatch.setenv(
            "HERDR_BRAIN_LIVE_STT_VOCABULARY",
            "ok\n" + "x" * 500 + "\t " + " ".join(["term"] * 400),
        )
        hint = live_vocabulary_hint()
        assert hint is not None
        assert len(hint) <= 300
        assert "\n" not in hint
        assert "\t" not in hint


class _OOMOnPurpose:
    def transcribe(self, path, language=None, initial_prompt=None):
        raise RuntimeError("CUDA out of memory during decode")
