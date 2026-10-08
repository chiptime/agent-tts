"""Tests for the engine-backed STT transcriber (F4.10 brain migration).

HARD RULES proven here: the brain never imports agent_tts and never
starts the real worker — every subprocess seam is a fake runner
returning canned CompletedProcess objects and the engine python is an
injected resolver. Before the first explicit interaction (boot warmup
probe or a transcribe) reading ``.state`` spawns NOTHING, one status
probe serves a whole TTL window, and transcribe temp files are always
cleaned.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings, load_settings
from herdr_brain.server import create_app
from herdr_brain.stt import STATE_READY, STATE_UNAVAILABLE
from herdr_brain.stt_engine import (
    ENGINE_PYTHON_ENV,
    STATUS_TTL_S,
    EnginePythonUnavailable,
    EngineSttError,
    EngineTranscriber,
    run_engine_pull,
)

FAKE_PY = "/fake/engine-python"


@pytest.fixture
def audio_dir(tmp_path) -> Path:
    out = tmp_path / "audio"
    out.mkdir()
    return out


def cp(code: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def status_ok(state: str = "ready") -> SimpleNamespace:
    return cp(0, json.dumps({"ok": True, "state": state, "model": "small"}))


class ScriptedRunner:
    """Answers status/transcribe ops with canned outcomes, records argv."""

    def __init__(self, status=None, transcribe=None):
        self.calls: list[dict] = []
        self.status = status if status is not None else status_ok()
        self.transcribe = transcribe if transcribe is not None else cp(
            0, json.dumps({"ok": True, "text": " el rebaño está en orden "})
        )

    def __call__(self, argv, timeout, capture=True):
        self.calls.append({"argv": list(argv), "timeout": timeout, "capture": capture})
        return self.status if "status" in argv else self.transcribe

    def count(self, op: str) -> int:
        return sum(1 for c in self.calls if op in c["argv"])

    def file_arg(self) -> str:
        call = next(c for c in self.calls if "transcribe" in c["argv"])
        return call["argv"][-1]


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_engine(settings, runner=None, resolver=None, clock=None) -> EngineTranscriber:
    return EngineTranscriber(
        settings,
        runner=runner if runner is not None else ScriptedRunner(),
        python_resolver=resolver if resolver is not None else (lambda: FAKE_PY),
        now=clock if clock is not None else FakeClock(),
    )


def cfg(settings, **overrides) -> Settings:
    return Settings(**{**settings.__dict__, **overrides})


class TestTranscribe:
    def test_happy_path_returns_text_and_cleans_temp(self, settings):
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner)
        out = t.transcribe_bytes(b"fake-webm-bytes")
        assert out == "el rebaño está en orden"
        call = runner.calls[0]
        assert call["argv"][:4] == [FAKE_PY, "-m", "agent_tts.stt.cli", "transcribe"]
        assert call["argv"][4] == "--file"
        tmp = call["argv"][5]
        assert tmp.endswith(".webm")  # suffix rides the filename, not a flag
        assert call["timeout"] == 120.0  # bounded default
        assert not Path(tmp).exists()  # always cleaned

    def test_custom_suffix_rides_the_filename(self, settings):
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner)
        t.transcribe_bytes(b"mp3-bytes", suffix=".mp3")
        assert runner.file_arg().endswith(".mp3")

    def test_empty_text_is_valid(self, settings):
        runner = ScriptedRunner(transcribe=cp(0, json.dumps({"ok": True, "text": ""})))
        t = make_engine(settings, runner=runner)
        assert t.transcribe_bytes(b"silence") == ""

    def test_worker_unavailable_exit5_maps_state_and_hint(self, settings):
        runner = ScriptedRunner(
            transcribe=cp(
                5,
                json.dumps({"ok": False, "error": {"kind": "worker_unavailable"}}),
                "agent-tts-stt: worker_unavailable: no worker",
            )
        )
        t = make_engine(settings, runner=runner)
        with pytest.raises(EngineSttError) as exc:
            t.transcribe_bytes(b"x")
        assert "agent-tts-stt serve" in str(exc.value)
        assert t.state == STATE_UNAVAILABLE
        assert "agent-tts-stt serve" in t.error()
        assert not Path(runner.file_arg()).exists()  # cleaned on failure too

    def test_other_nonzero_exit_names_stderr_tail(self, settings):
        runner = ScriptedRunner(
            transcribe=cp(1, json.dumps({"ok": False}), "ValueError: boom in decode")
        )
        t = make_engine(settings, runner=runner)
        with pytest.raises(EngineSttError) as exc:
            t.transcribe_bytes(b"x")
        assert "boom in decode" in str(exc.value)
        assert "exit 1" in str(exc.value)
        assert not Path(runner.file_arg()).exists()

    def test_success_flips_state_ready_without_status_op(self, settings):
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner)
        assert t.state == "loading"
        t.transcribe_bytes(b"x")
        assert t.state == STATE_READY  # the transcribe outcome is the evidence
        assert runner.count("status") == 0


class TestStateCache:
    def test_state_read_before_any_probe_never_spawns(self, settings):
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner)
        assert t.state == "loading"
        assert t.state == "loading"
        assert runner.calls == []  # no subprocess from a plain health read

    def test_probe_serves_the_whole_ttl_window(self, settings):
        clock = FakeClock()
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner, clock=clock)
        thread = t.maybe_start_warmup()
        assert thread is not None and thread.daemon
        thread.join(timeout=5)
        assert runner.count("status") == 1
        assert t.state == STATE_READY
        assert t.state == STATE_READY  # cached: still one probe
        clock.advance(STATUS_TTL_S + 0.1)
        assert t.state == STATE_READY  # value from the probe
        assert runner.count("status") == 2  # stale cache re-probed once

    def test_failed_probe_is_unavailable_with_hint(self, settings):
        runner = ScriptedRunner(
            status=cp(5, json.dumps({"ok": False}), "agent-tts-stt: no worker")
        )
        t = make_engine(settings, runner=runner)
        t.maybe_start_warmup().join(timeout=5)
        assert t.state == STATE_UNAVAILABLE
        assert "agent-tts-stt serve" in t.error()

    def test_model_present_true_when_worker_reachable(self, settings):
        for state in ("ready", "loading"):
            runner = ScriptedRunner(status=status_ok(state))
            t = make_engine(settings, runner=runner)
            assert t.model_present() is True

    def test_model_present_false_when_unavailable(self, settings):
        runner = ScriptedRunner(status=status_ok("unavailable"))
        t = make_engine(settings, runner=runner)
        assert t.model_present() is False


class TestEnginePythonResolution:
    def test_missing_python_is_typed_unavailable_naming_env(self, settings):
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner, resolver=lambda: None)
        with pytest.raises(EnginePythonUnavailable) as exc:
            t.transcribe_bytes(b"x")
        assert ENGINE_PYTHON_ENV in str(exc.value)
        assert t.state == STATE_UNAVAILABLE  # health tells the truth
        assert ENGINE_PYTHON_ENV in t.error()
        assert runner.calls == []

    def test_env_var_wins_verbatim(self, monkeypatch):
        from herdr_brain.stt_engine import resolve_engine_python

        monkeypatch.setenv(ENGINE_PYTHON_ENV, "/opt/custom/python")
        assert resolve_engine_python() == "/opt/custom/python"

    def test_fallback_is_the_plugin_data_venv(self, monkeypatch, tmp_path):
        from herdr_brain.stt_engine import resolve_engine_python

        monkeypatch.delenv(ENGINE_PYTHON_ENV, raising=False)
        venv = tmp_path / ".local" / "share" / "herdr-tts" / "venv" / "bin" / "python"
        venv.parent.mkdir(parents=True)
        venv.write_text("#!bin/sh\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_engine_python() == str(venv)

    def test_no_env_and_no_venv_raises_naming_env(self, monkeypatch):
        from herdr_brain.stt_engine import resolve_engine_python

        monkeypatch.delenv(ENGINE_PYTHON_ENV, raising=False)
        monkeypatch.setenv("HOME", "/nonexistent-home-for-tests")
        with pytest.raises(EnginePythonUnavailable) as exc:
            resolve_engine_python()
        assert ENGINE_PYTHON_ENV in str(exc.value)


class TestPullDelegation:
    def test_pull_streams_the_engine_cli(self):
        runner = ScriptedRunner()
        rc = run_engine_pull(python_resolver=lambda: FAKE_PY, runner=runner)
        assert rc == 0
        call = runner.calls[0]
        assert call["argv"] == [FAKE_PY, "-m", "agent_tts.stt.cli", "pull"]
        assert call["capture"] is False  # progress streams to the operator

    def test_pull_propagates_engine_exit_code(self):
        runner = ScriptedRunner(transcribe=cp(2, "", "pull failed"))
        rc = run_engine_pull(python_resolver=lambda: FAKE_PY, runner=runner)
        assert rc == 2

    def test_pull_without_python_fails_naming_env(self, capsys):
        rc = run_engine_pull(python_resolver=lambda: None, runner=ScriptedRunner())
        assert rc == 1
        assert ENGINE_PYTHON_ENV in capsys.readouterr().err


class TestSttCliBackendSwitch:
    def test_pull_with_engine_backend_delegates(self, settings, monkeypatch):
        import herdr_brain.stt as stt_module
        import herdr_brain.stt_engine as engine_module

        def forbidden(_settings):
            raise AssertionError("legacy Transcriber must not run in engine mode")

        monkeypatch.setattr(stt_module, "load_settings", lambda: cfg(settings))
        monkeypatch.setattr(stt_module, "Transcriber", forbidden)
        called = {}

        def fake_pull():
            called["argv"] = True
            return 77

        monkeypatch.setattr(engine_module, "run_engine_pull", fake_pull)
        assert stt_module.main(["pull"]) == 77
        assert called.get("argv") is True

    def test_pull_with_builtin_backend_stays_legacy(self, settings, monkeypatch):
        import herdr_brain.stt as stt_module
        import herdr_brain.stt_engine as engine_module

        class StubTranscriber:
            def __init__(self, _settings):
                self.state = "loading"

            def model_present(self):
                return True

            def warmup(self):
                self.state = STATE_READY

            def error(self):
                return None

        monkeypatch.setattr(
            stt_module, "load_settings", lambda: cfg(settings, stt_backend="builtin")
        )
        monkeypatch.setattr(stt_module, "Transcriber", StubTranscriber)

        def forbidden():
            raise AssertionError("engine pull must not run in builtin mode")

        monkeypatch.setattr(engine_module, "run_engine_pull", forbidden)
        assert stt_module.main(["pull"]) == 0


class TestServerWiring:
    def _fake_engine(self):
        class FakeEngine:
            built_with = None
            warmup_calls = 0

            def __init__(self, settings):
                FakeEngine.built_with = settings
                self.state = "loading"

            def maybe_start_warmup(self):
                FakeEngine.warmup_calls += 1
                return None

            def error(self):
                return None

            def model_present(self):
                return True

            def transcribe_bytes(self, data, suffix=".webm"):
                return ""

        return FakeEngine

    def test_engine_backend_builds_engine_transcriber(self, settings, monkeypatch):
        import herdr_brain.server as server_module

        FakeEngine = self._fake_engine()
        monkeypatch.setattr(server_module, "EngineTranscriber", FakeEngine)
        client = TestClient(create_app(settings=cfg(settings), version="t"))
        assert FakeEngine.built_with is not None
        assert client.get("/health").json()["stt"] == "loading"
        assert FakeEngine.warmup_calls == 0  # stt_warmup=False in test settings

    def test_engine_backend_warmup_flag_fires_probe(self, settings, monkeypatch):
        import herdr_brain.server as server_module

        FakeEngine = self._fake_engine()
        monkeypatch.setattr(server_module, "EngineTranscriber", FakeEngine)
        TestClient(create_app(settings=cfg(settings, stt_warmup=True), version="t"))
        assert FakeEngine.warmup_calls == 1

    def test_builtin_backend_builds_legacy_transcriber(self, settings, monkeypatch):
        import herdr_brain.server as server_module

        class ForbiddenEngine:
            def __init__(self, *_args):
                raise AssertionError("engine transcriber must not run in builtin mode")

        built = {}

        class StubLegacy:
            def __init__(self, s):
                built["settings"] = s
                self.state = "loading"

            def maybe_start_warmup(self):
                return None

        monkeypatch.setattr(server_module, "EngineTranscriber", ForbiddenEngine)
        monkeypatch.setattr(server_module, "Transcriber", StubLegacy)
        client = TestClient(
            create_app(settings=cfg(settings, stt_backend="builtin"), version="t")
        )
        assert built["settings"] is not None
        assert client.get("/health").json()["stt"] == "loading"


class TestConfigBackend:
    def test_default_backend_is_engine(self):
        assert load_settings({}).stt_backend == "engine"

    def test_env_selects_builtin(self):
        env = {"HERDR_BRAIN_STT_BACKEND": "builtin"}
        assert load_settings(env).stt_backend == "builtin"

    def test_env_rejects_unknown_values(self):
        with pytest.raises(ValueError, match="HERDR_BRAIN_STT_BACKEND"):
            load_settings({"HERDR_BRAIN_STT_BACKEND": "cloud"})


class TestServerTranscribeIntegration:
    """The exact /transcribe + /health contract over the engine backend."""

    def _client(self, settings, audio_dir, transcriber):
        client_settings = cfg(settings, audio_dir=str(audio_dir))
        return TestClient(
            create_app(settings=client_settings, version="t", transcriber=transcriber)
        )

    def _post_audio(self, client, payload=b"fake-webm-bytes"):
        return client.post(
            "/transcribe",
            files={"audio": ("clip.webm", __import__("io").BytesIO(payload), "audio/webm")},
        )

    def test_endpoint_serves_engine_transcriptions(self, settings, audio_dir, tmp_path):
        runner = ScriptedRunner()
        t = make_engine(settings, runner=runner)
        t.maybe_start_warmup().join(timeout=5)
        client = self._client(settings, audio_dir, t)
        resp = self._post_audio(client)
        assert resp.status_code == 200
        assert resp.json() == {"text": "el rebaño está en orden"}

    def test_worker_death_mid_transcribe_503s_then_health_flips(
        self, settings, audio_dir
    ):
        runner = ScriptedRunner(
            transcribe=cp(5, json.dumps({"ok": False}), "no worker")
        )
        t = make_engine(settings, runner=runner)
        t.maybe_start_warmup().join(timeout=5)  # status said ready
        client = self._client(settings, audio_dir, t)
        resp = self._post_audio(client)
        assert resp.status_code == 503
        assert "agent-tts-stt serve" in resp.json()["detail"]
        assert client.get("/health").json()["stt"] == STATE_UNAVAILABLE

    def test_engine_mode_keeps_the_unavailable_503_shape(self, settings, audio_dir):
        runner = ScriptedRunner(status=status_ok("unavailable"))
        t = make_engine(settings, runner=runner)
        t.maybe_start_warmup().join(timeout=5)
        client = self._client(settings, audio_dir, t)
        resp = self._post_audio(client)
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "no disponible" in detail
        assert "python -m herdr_brain.stt pull" in detail  # still the actionable hint
