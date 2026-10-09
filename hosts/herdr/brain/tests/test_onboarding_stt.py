"""V1 for the STT consent step (AT-11 task 3.4, design slice 18, Decision 6).

HARD RULES proven here, all with fakes at the external boundary:

- nothing is downloaded without explicit consent (no answer, ``none``,
  blank/EOF/``n`` interactively, a persisted choice on re-run);
- offline checks (the cache probe) never touch the network or spawn the
  downloader;
- on consent the download is delegated to ``python -m herdr_brain.stt
  pull`` (the brain's only download path) and the speech-surface contract
  (``herdr-tts --contract-version`` >= 1) is verified afterwards;
- refusal completes onboarding successfully and the runtime vocabulary
  stays the resolved one: ``/health`` ``stt`` is ``loading|ready|
  unavailable`` — ``unavailable`` after refusal, never ``degraded`` —
  with ``/ask`` and its ``audio_url`` untouched;
- the frozen contracts are byte-identical (pinned digests).

No test downloads a model, loads whisper, starts a service or reads real
credentials; the downloader and launcher are fake runners and the HF cache
is a temporary directory.  Nothing here is real-device proof.
"""

from __future__ import annotations

import hashlib
import io
import json
import socket
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from herdr_brain.config import Settings  # noqa: E402
from herdr_brain.server import create_app  # noqa: E402
from herdr_brain.stt import (  # noqa: E402
    STATE_LOADING,
    STATE_READY,
    STATE_UNAVAILABLE,
    Transcriber,
)
from herdr_onboarding import resolve as res  # noqa: E402
from herdr_onboarding.cli import main  # noqa: E402
from herdr_onboarding.steps import steps_for_role  # noqa: E402
from herdr_onboarding.steps import stt as stt_mod  # noqa: E402
from herdr_onboarding.steps.credentials import CredentialsStep  # noqa: E402
from herdr_onboarding.steps.stt import SttStep  # noqa: E402
from tests.test_server import FakeLLM, FakeTTS  # noqa: E402


class FakeRunner:
    """Records argv/kwargs; ``fail`` maps a command substring to a result."""

    def __init__(self, fail=None, contract="1"):
        self.calls: list = []
        self.kwargs: list = []
        self.fail = fail or {}
        self.contract = contract

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        line = " ".join(argv)
        for needle, code in self.fail.items():
            if needle in line:
                return subprocess.CompletedProcess(argv, code, "", f"boom: {needle}")
        if "--contract-version" in line:
            return subprocess.CompletedProcess(argv, 0, f"{self.contract}\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def lines(self):
        return [" ".join(c) for c in self.calls]

    def pulls(self):
        return [c for c in self.calls if "herdr_brain.stt" in " ".join(c)]


def _env(home: Path, **extra) -> dict:
    env = {"HOME": str(home)}  # hermetic: no ambient XDG_*/HF_*/HERDR_* leaks in
    env.update(extra)
    return env


def _run(argv, *, home, runner, env=None, stdin="", isatty=False, steps=None):
    out, err = StringIO(), StringIO()
    code = main(
        argv,
        env=env if env is not None else _env(home),
        stdin=StringIO(stdin),
        stdout=out,
        stderr=err,
        isatty=lambda: isatty,
        health_gate=lambda ctx: True,
        steps=steps if steps is not None else [SttStep(runner=runner)],
    )
    return code, out.getvalue(), err.getvalue()


def _env_file(home: Path) -> Path:
    return home / ".config" / "herdr-brain" / "env"


def _marker(home: Path) -> dict:
    return json.loads((home / ".config" / "herdr-tts" / "first-run.done").read_text())


def _forget_marker(home: Path) -> None:
    (home / ".config" / "herdr-tts" / "first-run.done").unlink()


def _cache_model(root: Path, size: str, *, complete: bool = True) -> None:
    snapshot = root / f"models--Systran--faster-whisper-{size}" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    files = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
    for name in files if complete else files[:2]:
        (snapshot / name).write_text("x")


class TestConsentGate:
    @pytest.mark.parametrize("flagargs", [[], ["--stt", "none"]])
    def test_no_answer_or_none_never_downloads(self, tmp_path, flagargs):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", *flagargs], home=tmp_path, runner=runner
        )
        assert code == 0, err
        assert runner.calls == []
        assert not _env_file(tmp_path).exists()
        assert _marker(tmp_path)["stt"] == "none"

    @pytest.mark.parametrize("answer", ["", "\n", "n\n", "none\n"])
    def test_interactive_blank_eof_or_decline_never_downloads(self, tmp_path, answer):
        runner = FakeRunner()
        code, out, err = _run(
            ["--role", "brain"], home=tmp_path, runner=runner, stdin=answer, isatty=True
        )
        assert code == 0, err
        assert "speech-to-text" in out.lower()
        assert runner.calls == []

    def test_unattended_without_an_answer_explains_how_to_opt_in(self, tmp_path):
        code, _, err = _run(
            ["--role", "brain", "--non-interactive"], home=tmp_path, runner=FakeRunner()
        )
        assert code == 0, err
        assert "--stt" in err and "HERDR_ONBOARDING_STT" in err

    def test_persisted_choice_is_preserved_and_never_re_downloaded(self, tmp_path):
        env_file = _env_file(tmp_path)
        env_file.parent.mkdir(parents=True)
        env_file.write_text("GLM_API_KEY=sk-x\nHERDR_BRAIN_STT_MODEL=base\n")
        before = env_file.read_text()
        runner = FakeRunner()
        code, out, err = _run(["--role", "brain"], home=tmp_path, runner=runner,
                              stdin="small\n", isatty=True)
        assert code == 0, err
        assert runner.calls == [] and "speech-to-text" not in out.lower()
        assert env_file.read_text() == before
        assert _marker(tmp_path)["stt"] == "base"


class TestAcceptedConsent:
    def test_downloads_through_the_brain_pull_cli_then_verifies_the_contract(self, tmp_path):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "base"],
            home=tmp_path, runner=runner,
        )
        assert code == 0, err
        pulls = runner.pulls()
        assert len(pulls) == 1
        assert pulls[0][1:] == ["-m", "herdr_brain.stt", "pull"]
        child_env = runner.kwargs[0]["env"]
        assert child_env["HERDR_BRAIN_STT_MODEL"] == "base"
        assert child_env["AGENT_TTS_STT_MODEL"] == "base"
        assert runner.kwargs[0]["stdout"] == subprocess.DEVNULL  # keep --json clean
        # the contract check runs AFTER the download, through the launcher
        assert runner.lines()[-1].endswith("herdr-tts --contract-version")
        assert runner.calls.index(pulls[0]) < len(runner.calls) - 1
        marker = _marker(tmp_path)
        assert marker["stt"] == "base" and marker["stt_downloaded"] is True

    def test_choice_is_persisted_next_to_the_key_without_disturbing_it(self, tmp_path):
        env_file = _env_file(tmp_path)
        env_file.parent.mkdir(parents=True)
        env_file.write_text("# mine\nGLM_API_KEY=sk-keep-me\n")
        env_file.chmod(0o600)
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "tiny"],
            home=tmp_path, runner=FakeRunner(),
        )
        assert code == 0, err
        text = env_file.read_text()
        assert text.startswith("# mine\nGLM_API_KEY=sk-keep-me\n")
        assert res._read_env_key(env_file, "HERDR_BRAIN_STT_MODEL") == "tiny"
        assert res._read_env_key(env_file, "AGENT_TTS_STT_MODEL") == "tiny"
        assert env_file.stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize("size", ["tiny", "base", "small"])
    def test_every_documented_size_is_accepted_via_env(self, tmp_path, size):
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "brain", "--non-interactive"], home=tmp_path, runner=runner,
            env=_env(tmp_path, HERDR_ONBOARDING_STT=size),
        )
        assert code == 0, err
        assert runner.kwargs[0]["env"]["HERDR_BRAIN_STT_MODEL"] == size

    def test_interactive_consent_names_the_sizes_and_downloads(self, tmp_path):
        runner = FakeRunner()
        code, out, err = _run(["--role", "brain"], home=tmp_path, runner=runner,
                              stdin="small\n", isatty=True)
        assert code == 0, err
        assert all(size in out for size in ("tiny", "base", "small"))
        assert len(runner.pulls()) == 1
        assert runner.kwargs[0]["env"]["HERDR_BRAIN_STT_MODEL"] == "small"

    def test_works_alongside_the_credentials_step(self, tmp_path):
        read_fd, write_fd = __import__("os").pipe()
        __import__("os").write(write_fd, b"sk-both-steps-1\n")
        __import__("os").close(write_fd)
        runner = FakeRunner()
        code, out, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "base"],
            home=tmp_path, runner=runner,
            env=_env(tmp_path, HERDR_ONBOARDING_SECRET_FD=str(read_fd)),
            steps=[CredentialsStep(), SttStep(runner=runner)],
        )
        assert code == 0, err
        env_file = _env_file(tmp_path)
        assert res._read_env_key(env_file, "GLM_API_KEY") == "sk-both-steps-1"
        assert res._read_env_key(env_file, "HERDR_BRAIN_STT_MODEL") == "base"
        assert "sk-both-steps-1" not in out + err


class TestOfflineProbeNeverDownloads:
    def test_cached_model_skips_the_download_but_still_verifies(self, tmp_path):
        cache = tmp_path / "hf"
        _cache_model(cache, "base")
        runner = FakeRunner()
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "base"],
            home=tmp_path, runner=runner,
            env=_env(tmp_path, HF_HUB_CACHE=str(cache)),
        )
        assert code == 0, err
        assert runner.pulls() == []
        assert runner.lines()[-1].endswith("--contract-version")
        assert _marker(tmp_path)["stt_downloaded"] is False

    def test_incomplete_cache_counts_as_absent(self, tmp_path):
        cache = tmp_path / "hf"
        _cache_model(cache, "base", complete=False)
        assert stt_mod.model_cached("base", _env(tmp_path, HF_HUB_CACHE=str(cache))) is False

    def test_cache_root_precedence_follows_huggingface(self, tmp_path):
        assert stt_mod.hf_cache_root(_env(tmp_path, HF_HUB_CACHE="/a")) == Path("/a")
        assert stt_mod.hf_cache_root(_env(tmp_path, HF_HOME="/b")) == Path("/b/hub")
        assert stt_mod.hf_cache_root(_env(tmp_path, XDG_CACHE_HOME="/c")) == Path("/c/huggingface/hub")
        assert stt_mod.hf_cache_root(_env(tmp_path)) == tmp_path / ".cache/huggingface/hub"

    def test_probe_touches_neither_network_nor_subprocess(self, tmp_path, monkeypatch):
        def forbidden(*args, **kwargs):  # pragma: no cover — must not run
            raise AssertionError("an offline check must not reach the network or spawn")

        monkeypatch.setattr(socket, "socket", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        monkeypatch.setattr(subprocess, "run", forbidden)
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        assert stt_mod.model_cached("small", _env(tmp_path)) is False


class TestFailuresAreRetryable:
    def test_download_failure_exits_40_with_nothing_persisted(self, tmp_path):
        runner = FakeRunner(fail={"herdr_brain.stt": 1})
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "base"],
            home=tmp_path, runner=runner,
        )
        assert code == 40
        assert "python -m herdr_brain.stt pull" in err
        assert not _env_file(tmp_path).exists()
        assert not (tmp_path / ".config" / "herdr-tts" / "first-run.done").exists()
        assert len(runner.calls) == 1  # no contract probe after a failed download

    def test_contract_failure_after_download_persists_nothing(self, tmp_path):
        runner = FakeRunner(contract="0")
        code, _, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "base"],
            home=tmp_path, runner=runner,
        )
        assert code == 40
        assert "contract" in err
        assert not _env_file(tmp_path).exists()

    def test_retry_after_a_failed_run_resumes_from_the_offline_cache(self, tmp_path):
        cache = tmp_path / "hf"
        env = _env(tmp_path, HF_HUB_CACHE=str(cache))
        bad = FakeRunner(contract="0")
        assert _run(["--role", "brain", "--non-interactive", "--stt", "base"],
                    home=tmp_path, runner=bad, env=env)[0] == 40
        _cache_model(cache, "base")  # the download itself did complete
        good = FakeRunner()
        code, _, err = _run(["--role", "brain", "--non-interactive", "--stt", "base"],
                            home=tmp_path, runner=good, env=env)
        assert code == 0, err
        assert good.pulls() == []


class TestRefusalPreservesTheJourney:
    def _app(self, settings, tmp_path, transcriber, tts=None):
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(tmp_path / "audio")})
        return TestClient(
            create_app(
                settings=cfg, version="t", transcriber=transcriber,
                llm_factory=lambda _c, _t: FakeLLM(), tts_renderer=tts or FakeTTS(),
            )
        )

    def test_onboarding_with_refusal_succeeds_and_marks_none(self, tmp_path):
        runner = FakeRunner()
        code, out, err = _run(
            ["--role", "brain", "--non-interactive", "--stt", "none", "--json"],
            home=tmp_path, runner=runner,
        )
        assert code == 0, err
        record = json.loads(out)
        assert record["status"] == "completed" and record["marker_written"] is True
        assert runner.calls == []
        assert _marker(tmp_path)["stt"] == "none"
        assert "unavailable" in err  # the user is told what /health will say

    def test_runtime_vocabulary_after_refusal_is_unavailable_never_degraded(
        self, settings, tmp_path
    ):
        from herdr_brain.stt import STATE_UNAVAILABLE as UNAVAILABLE

        assert {STATE_LOADING, STATE_READY, STATE_UNAVAILABLE} == {
            "loading", "ready", "unavailable",
        }
        transcriber = Transcriber(settings, cached=lambda: False)  # no model: refusal
        assert transcriber.maybe_start_warmup() is None  # and no download attempt
        client = self._app(settings, tmp_path, transcriber)
        health = client.get("/health").json()
        assert health["stt"] == "unavailable" == UNAVAILABLE
        assert health["stt"] != "degraded"
        assert "degraded" not in {STATE_LOADING, STATE_READY, STATE_UNAVAILABLE}
        resp = client.post(
            "/transcribe", files={"audio": ("c.webm", io.BytesIO(b"x"), "audio/webm")}
        )
        assert resp.status_code == 503

    def test_ask_and_audio_url_are_unaffected_by_the_refusal(self, settings, tmp_path):
        transcriber = Transcriber(settings, cached=lambda: False)
        transcriber.maybe_start_warmup()
        client = self._app(settings, tmp_path, transcriber)
        resp = client.post("/ask", json={"text": "hola"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]
        assert body["audio_url"] and body["audio_url"].startswith("/audio/")

    def test_ask_keeps_its_documented_null_audio_url_when_the_render_fails(
        self, settings, tmp_path
    ):
        transcriber = Transcriber(settings, cached=lambda: False)
        transcriber.maybe_start_warmup()
        client = self._app(settings, tmp_path, transcriber, tts=FakeTTS(fail=True))
        resp = client.post("/ask", json={"text": "hola"})
        assert resp.status_code == 200
        assert resp.json()["answer"] and resp.json()["audio_url"] is None

    def test_acceptance_reads_ready_once_the_model_is_present(self, settings):
        class _Model:
            def transcribe(self, *a, **k):  # pragma: no cover — never called
                raise AssertionError

        transcriber = Transcriber(settings, loader=lambda: _Model(), cached=lambda: True)
        transcriber.warmup()
        assert transcriber.state == STATE_READY == "ready"


class TestFrozenContractsUntouched:
    DIGESTS = {
        "contracts/tts-brain-v1.md": "2d8895f371b848d264fe80702e85cadd97ebeea78e0998943c1840905d9c5c4b",
        "contracts/ipc-v2.md": "64c8cfa7ce72edf19dfa9c5b367a57fb1a614de56564935e1d59a16d01fa8e03",
    }

    @pytest.mark.parametrize("path", sorted(DIGESTS))
    def test_frozen_contract_is_byte_identical(self, path):
        digest = hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
        assert digest == self.DIGESTS[path]


class TestRegistry:
    def test_stt_step_is_brain_only(self):
        assert any(isinstance(s, SttStep) for s in steps_for_role("brain"))
        assert not any(isinstance(s, SttStep) for s in steps_for_role("plugin"))

    def test_registry_order_is_credentials_voice_keymap_stt(self):
        assert [s.name for s in steps_for_role("brain")] == [
            "credentials", "voice", "keymap", "stt",
        ]
