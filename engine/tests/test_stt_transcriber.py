"""Engine STT core tests: settings, cache policy, lazy transcriber, pull.

HARD RULE proven here: every test uses a fake loader/fake hub — the real
faster-whisper model is never loaded, imported, or downloaded. The real
loader seam proves the offline contract: WhisperModel only ever receives a
locally resolved path, never a repo alias that could fetch silently.
"""

from __future__ import annotations

import os
import sys
import threading
import types
from pathlib import Path

import pytest

from agent_tts.stt.transcriber import (
    EXTRA_HINT,
    PULL_COMMAND,
    STATE_READY,
    STATE_UNAVAILABLE,
    InvalidConfigError,
    MissingExtraError,
    ModelUnavailableError,
    SttSettings,
    Transcriber,
    model_is_cached,
    pull_model,
    resolve_local_model,
)


class FakeModel:
    """faster-whisper test double: canned segments, optional failure."""

    fail = False
    last_path: str | None = None
    last_suffix: str | None = None

    def transcribe(self, path, language=None):
        if FakeModel.fail:
            raise RuntimeError("decode failed")
        FakeModel.last_path = path
        return (
            iter([types.SimpleNamespace(text="  el  rebaño está  en orden ")]),
            types.SimpleNamespace(language="es"),
        )


def make_transcriber(**kwargs) -> Transcriber:
    overrides = {"loader": lambda: FakeModel(), "cached": lambda: True}
    overrides.update(kwargs)
    return Transcriber(SttSettings(), **overrides)


class TestSettings:
    def test_defaults_reuse_brain_defaults(self):
        s = SttSettings()
        assert (s.model, s.device, s.compute_type) == ("small", "auto", "auto")

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("AGENT_TTS_STT_MODEL", "base")
        monkeypatch.setenv("AGENT_TTS_STT_DEVICE", "cpu")
        monkeypatch.setenv("AGENT_TTS_STT_COMPUTE", "int8")
        s = SttSettings.from_env()
        assert (s.model, s.device, s.compute_type) == ("base", "cpu", "int8")

    def test_isolated_env_keeps_defaults(self, monkeypatch):
        for var in (
            "AGENT_TTS_STT_MODEL",
            "AGENT_TTS_STT_DEVICE",
            "AGENT_TTS_STT_COMPUTE",
        ):
            monkeypatch.delenv(var, raising=False)
        s = SttSettings.from_env()
        assert s.model == "small"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"model": ""},
            {"model": "   "},
            {"device": "gpu"},
            {"compute_type": ""},
        ],
    )
    def test_invalid_settings_raise_typed_error(self, kwargs):
        with pytest.raises(InvalidConfigError):
            SttSettings(**kwargs)


class TestModelIsCached:
    def test_local_path_shortcut(self, tmp_path):
        assert model_is_cached(str(tmp_path)) is True

    def test_all_inference_files_present(self, monkeypatch):
        fake_hub = types.SimpleNamespace(
            try_to_load_from_cache=lambda repo, f: f"/cache/{f}"
        )
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        assert model_is_cached("small") is True

    def test_missing_model_bin_means_absent(self, monkeypatch):
        def lookup(repo, filename):
            return None if filename == "model.bin" else f"/cache/{filename}"

        fake_hub = types.SimpleNamespace(try_to_load_from_cache=lookup)
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        assert model_is_cached("small") is False

    def test_interrupted_snapshot_with_weights_counts(self, monkeypatch):
        def lookup(repo, filename):
            return None if filename == "README.md" else f"/cache/{filename}"

        fake_hub = types.SimpleNamespace(try_to_load_from_cache=lookup)
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        assert model_is_cached("small") is True

    def test_hub_errors_mean_absent(self, monkeypatch):
        def boom(repo, filename):
            raise RuntimeError("offline")

        fake_hub = types.SimpleNamespace(try_to_load_from_cache=boom)
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        assert model_is_cached("small") is False

    def test_missing_hub_probe_raises_missing_extra(self, monkeypatch):
        # An absent hub library means the EXTRA is missing — telling the
        # user to `pull` would be wrong (pull needs the same library).
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        with pytest.raises(MissingExtraError):
            model_is_cached("small")

    def test_local_path_shortcut_needs_no_hub(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        assert model_is_cached(str(tmp_path)) is True


class TestResolveOfflineAssets:
    """The resolved directory must satisfy faster-whisper's OFFLINE paths.

    Evidence from the installed dependency (brain .venv, transcribe.py):
    a directory input skips download_model, BUT a missing tokenizer.json
    falls back to tokenizers.Tokenizer.from_pretrained("openai/whisper-tiny")
    — a hub fetch. We refuse such directories instead.
    """

    @staticmethod
    def asset_dir(tmp_path, files=("config.json", "model.bin", "tokenizer.json")):
        d = tmp_path / "ct2"
        d.mkdir()
        for name in files:
            (d / name).write_bytes(b"x")
        return d

    def test_rejects_regular_file_model_path(self, tmp_path):
        f = tmp_path / "model.bin"
        f.write_bytes(b"x")
        with pytest.raises(InvalidConfigError):
            resolve_local_model(str(f))

    def test_missing_tokenizer_refused_not_network(self, tmp_path):
        d = self.asset_dir(tmp_path, files=("config.json", "model.bin"))
        with pytest.raises(ModelUnavailableError) as exc:
            resolve_local_model(str(d))
        assert "tokenizer" in str(exc.value)

    def test_resolves_relative_dir_to_absolute_verified(self, tmp_path, monkeypatch):
        d = self.asset_dir(tmp_path)
        monkeypatch.chdir(tmp_path)
        resolved = resolve_local_model("ct2")
        assert resolved == str(d.resolve())
        assert os.path.isabs(resolved)

    def test_local_files_only_flag_passed_to_constructor(self, monkeypatch, tmp_path):
        d = self.asset_dir(tmp_path)
        snap_root = tmp_path / "snapshots"
        recorded = {"model_args": []}

        class FakeWhisperModel:
            def __init__(self, model, **kwargs):
                recorded["model_args"].append((model, kwargs))

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = FakeWhisperModel
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
        t = Transcriber(SttSettings(model=str(d)), snapshot_dir=str(snap_root))
        t.warmup()
        assert t.state == STATE_READY
        model, kwargs = recorded["model_args"][0]
        assert str(snap_root) in model  # loads from the private snapshot
        assert Path(model).is_absolute()
        assert kwargs.get("local_files_only") is True

    def test_snapshot_without_tokenizer_never_constructs_model(self, monkeypatch, tmp_path):
        incomplete = self.asset_dir(tmp_path, files=("config.json", "model.bin"))
        constructed = []

        class FakeWhisperModel:
            def __init__(self, model, **kwargs):
                constructed.append(model)

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = FakeWhisperModel
        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = lambda **kw: str(incomplete)
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        t = Transcriber(SttSettings(model="small"), snapshot_dir=str(tmp_path / "s"))
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert constructed == []  # the tokenizer fallback is unreachable


class TestRuntimeAssetSnapshot:
    """Late-race offline guarantee against the dependency's REAL branch.

    faster-whisper 1.2.1 transcribe.py:689-708 checks tokenizer.json AFTER
    native model construction and its fallback
    (Tokenizer.from_pretrained("openai/whisper-tiny")) ignores
    local_files_only. The engine closes this by loading from a private
    runtime snapshot directory whose entries are hardlinks/copies this
    process owns — file PRESENCE in that dir selects the offline
    from_file branch; concurrent source-cache deletion cannot flip it.
    """

    @staticmethod
    def make_source(tmp_path, blob_backed=False):
        source = tmp_path / "src-model"
        source.mkdir()
        if blob_backed:
            blob = tmp_path / "blobs" / "tok.bin"
            blob.parent.mkdir()
            blob.write_bytes(b"tokenizer-bytes")
            (source / "tokenizer.json").symlink_to(blob)
        else:
            (source / "tokenizer.json").write_bytes(b"tokenizer-bytes")
        (source / "config.json").write_bytes(b"cfg")
        (source / "model.bin").write_bytes(b"weights")
        return source

    @staticmethod
    def install_fake_native(monkeypatch):
        recorded = {"paths": [], "kwargs": []}

        class FakeWhisperModel:
            def __init__(self, model_path, **kwargs):
                # simulates the race window: the source cache is wiped
                # WHILE the native constructor runs
                recorded["paths"].append(model_path)
                recorded["kwargs"].append(kwargs)

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = FakeWhisperModel
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
        return recorded

    def test_load_uses_private_snapshot_surviving_source_deletion(
        self, tmp_path, monkeypatch
    ):
        source = self.make_source(tmp_path)
        recorded = {"paths": [], "kwargs": []}

        class DeletingModel:
            def __init__(self, model_path, **kwargs):
                # deterministic mid-construction race: the source cache's
                # tokenizer.json is deleted WHILE the native ctor runs
                recorded["paths"].append(model_path)
                recorded["kwargs"].append(kwargs)
                (source / "tokenizer.json").unlink(missing_ok=True)

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = DeletingModel
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

        snap_root = tmp_path / "snapshots"
        t = Transcriber(SttSettings(model=str(source)), snapshot_dir=str(snap_root))
        t.warmup()
        assert t.state == STATE_READY
        used = recorded["paths"][0]
        assert used != str(source)  # never the mutable cache dir
        assert Path(used, "tokenizer.json").is_file()  # offline branch selected
        assert not (source / "tokenizer.json").exists()  # source really was wiped

    def test_snapshot_hardlinks_blob_backed_assets(self, tmp_path, monkeypatch):
        source = self.make_source(tmp_path, blob_backed=True)
        recorded = self.install_fake_native(monkeypatch)
        snap_root = tmp_path / "snapshots"
        t = Transcriber(SttSettings(model=str(source)), snapshot_dir=str(snap_root))
        t.warmup()
        assert t.state == STATE_READY
        used = Path(recorded["paths"][0])
        tok = used / "tokenizer.json"
        assert tok.is_file() and not tok.is_symlink()  # real entry, not a link out
        # wipe the entire HF-style cache: blob, symlink and source dir
        import shutil

        shutil.rmtree(tmp_path / "blobs")
        shutil.rmtree(source)
        assert tok.read_bytes() == b"tokenizer-bytes"  # inode retained
        assert (used / "model.bin").is_file()

    def test_source_vanishing_before_link_is_typed_offline_failure(
        self, tmp_path, monkeypatch
    ):
        source = self.make_source(tmp_path)
        recorded = self.install_fake_native(monkeypatch)
        real_link = os.link

        def vanishing_link(src, dst, **kwargs):
            import shutil as _shutil

            _shutil.rmtree(source)
            return real_link(src, dst, **kwargs)

        monkeypatch.setattr(os, "link", vanishing_link)
        t = Transcriber(SttSettings(model=str(source)), snapshot_dir=str(tmp_path / "s"))
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert "not available locally" in t.error()
        assert recorded["paths"] == []  # native ctor never ran

    def test_snapshot_reused_across_restarts(self, tmp_path, monkeypatch):
        source = self.make_source(tmp_path)
        self.install_fake_native(monkeypatch)
        snap_root = tmp_path / "snapshots"
        for _ in range(2):
            t = Transcriber(SttSettings(model=str(source)), snapshot_dir=str(snap_root))
            t.warmup()
            assert t.state == STATE_READY
        targets = {p.parent for p in snap_root.rglob("tokenizer.json")}
        assert len(targets) == 1  # same snapshot dir, no duplication

    def test_snapshot_root_rejects_unsafe_existing_dir(self, tmp_path):
        from agent_tts.stt.transcriber import ensure_snapshot_root

        bad = tmp_path / "loose"
        bad.mkdir()
        os.chmod(bad, 0o755)
        with pytest.raises(Exception) as exc:
            ensure_snapshot_root(str(bad))
        assert "unsafe" in str(exc.value).lower()


class TestRealLoaderOfflineContract:
    def _install_fakes(self, monkeypatch, snapshot_path=None, snap_root=None):
        recorded = {"model_args": [], "snapshot_calls": []}
        if snapshot_path is None:
            # default: a real directory satisfying the offline asset check
            import tempfile

            snapshot_path = tempfile.mkdtemp()
            for name in ("config.json", "model.bin", "tokenizer.json"):
                Path(snapshot_path, name).write_bytes(b"x")
        if snap_root is None:
            import tempfile

            snap_root = tempfile.mkdtemp()

        class FakeWhisperModel:
            def __init__(self, model, device=None, compute_type=None, **kwargs):
                recorded["model_args"].append((model, device, compute_type))

        def snapshot_download(**kwargs):
            recorded["snapshot_calls"].append(kwargs)
            return snapshot_path

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = FakeWhisperModel
        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = snapshot_download
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        return recorded, str(snapshot_path), str(snap_root)

    def test_real_loader_passes_resolved_local_path_not_alias(self, monkeypatch):
        recorded, source_path, snap_root = self._install_fakes(monkeypatch)
        t = Transcriber(SttSettings(model="small"), snapshot_dir=snap_root)
        t.warmup()
        assert t.state == STATE_READY
        used = recorded["model_args"][0][0]
        assert used != source_path and used.startswith(snap_root)
        assert Path(used, "tokenizer.json").is_file()  # offline branch
        assert recorded["snapshot_calls"][0]["repo_id"] == "Systran/faster-whisper-small"
        assert recorded["snapshot_calls"][0]["local_files_only"] is True

    def test_real_loader_absent_model_never_downloads(self, monkeypatch):
        recorded, _, snap_root = self._install_fakes(monkeypatch)

        def refuse(**kwargs):
            raise RuntimeError("local_files_only refused: not cached")

        sys.modules["huggingface_hub"].snapshot_download = refuse
        t = Transcriber(SttSettings(model="small"), snapshot_dir=snap_root)
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert PULL_COMMAND in t.error()
        assert recorded["model_args"] == []  # WhisperModel never constructed

    def test_missing_extra_names_install_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        t = Transcriber(SttSettings())
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert "agent-tts[stt]" in t.error()

    def test_resolve_local_model_local_path_needs_no_hub(self, tmp_path, monkeypatch):
        d = tmp_path / "ct2"
        d.mkdir()
        for name in ("config.json", "model.bin", "tokenizer.json"):
            (d / name).write_bytes(b"x")
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        assert resolve_local_model(str(d)) == str(d.resolve())

    def test_resolve_absent_model_raises_typed_with_pull_hint(self, monkeypatch):
        def refuse(**kwargs):
            raise RuntimeError("not cached")

        fake_hub = types.ModuleType("huggingface_hub")
        fake_hub.snapshot_download = refuse
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
        with pytest.raises(ModelUnavailableError) as exc:
            resolve_local_model("small")
        assert PULL_COMMAND in str(exc.value)


class TestTranscriber:
    def test_lazy_transcribe_before_warmup(self):
        t = make_transcriber()
        assert t.transcribe_bytes(b"x") == "el rebaño está en orden"

    def test_warmup_ready_and_error_none(self):
        t = make_transcriber()
        t.warmup()
        assert (t.state, t.error()) == (STATE_READY, None)

    def test_warmup_failure_marks_unavailable(self):
        def boom():
            raise RuntimeError("load exploded")

        t = Transcriber(SttSettings(), loader=boom)
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert "load exploded" in t.error()

    def test_transcribe_trims_and_cleans_temp(self):
        t = make_transcriber()
        out = t.transcribe_bytes(b"fake-bytes", suffix=".mp3")
        assert out == "el rebaño está en orden"
        assert FakeModel.last_path and not Path(FakeModel.last_path).exists()

    def test_transcribe_failure_still_cleans_temp(self):
        t = make_transcriber()
        t.warmup()
        FakeModel.fail = True
        try:
            with pytest.raises(RuntimeError):
                t.transcribe_bytes(b"junk")
            assert not Path(FakeModel.last_path).exists()
        finally:
            FakeModel.fail = False

    def test_model_loaded_once_under_concurrency(self):
        loads = []

        def counting_loader():
            loads.append(1)
            return FakeModel()

        t = Transcriber(SttSettings(), loader=counting_loader, cached=lambda: True)
        threads = [threading.Thread(target=lambda: t.transcribe_bytes(b"x")) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(loads) == 1

    def test_transcribe_without_model_refuses_and_never_loads(self):
        def forbidden():
            raise AssertionError("loader must not run when the model is absent")

        t = Transcriber(SttSettings(), loader=forbidden, cached=lambda: False)
        with pytest.raises(ModelUnavailableError) as exc:
            t.transcribe_bytes(b"x")
        assert PULL_COMMAND in str(exc.value)

    def test_transcribe_missing_extra_precedence_over_pull_hint(self, monkeypatch):
        # Hub library absent -> the actionable fix is installing the extra,
        # not `pull`; the typed error must say so before any pull hint.
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        t = Transcriber(SttSettings(), loader=lambda: FakeModel())
        with pytest.raises(MissingExtraError) as exc:
            t.transcribe_bytes(b"x")
        assert "agent-tts[stt]" in str(exc.value)

    def test_recovery_health_failed_then_succeeding_load(self):
        """F3: a first failed warmup then a successful retry must clear
        stale unavailable/error state in the actual load path."""
        calls = {"n": 0}

        def flaky_loader():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient load failure")
            return FakeModel()

        t = Transcriber(SttSettings(), loader=flaky_loader, cached=lambda: True)
        t.warmup()
        assert t.state == STATE_UNAVAILABLE
        assert "transient load failure" in t.error()
        # retry succeeds: health follows the actual load outcome
        assert t.transcribe_bytes(b"x") == "el rebaño está en orden"
        assert t.state == STATE_READY
        assert t.error() is None

    def test_model_present_reflects_cache_seam(self):
        assert make_transcriber(cached=lambda: True).model_present() is True
        assert make_transcriber(cached=lambda: False).model_present() is False


class TestPull:
    def test_pull_is_the_only_download_operation(self):
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return "/cache/snapshot"

        path = pull_model(SttSettings(model="base"), downloader=downloader)
        assert path == "/cache/snapshot"
        assert calls == [{"repo_id": "Systran/faster-whisper-base"}]

    def test_pull_local_path_skips_download(self, tmp_path):
        local_model = tmp_path / "ct2-model"
        local_model.mkdir()

        def forbidden(**kwargs):
            raise AssertionError("local model must not download")

        assert pull_model(SttSettings(model=str(local_model)), downloader=forbidden) == str(local_model)

    def test_pull_without_hub_names_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)
        with pytest.raises(MissingExtraError) as exc:
            pull_model(SttSettings())
        assert "agent-tts[stt]" in str(exc.value)

    def test_extra_hint_mentions_stt_extra(self):
        assert "agent-tts[stt]" in EXTRA_HINT
        assert PULL_COMMAND == "agent-tts-stt pull"
