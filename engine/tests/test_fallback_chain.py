"""Fallback chain tests (VS4.1 config + VS4.2 walker + VS4.3 audible
partials + VS4.4 attempt-log schema).

All providers here are fakes — zero network. Backoff is exercised with
an injected sleeper (fake clock discipline: no test ever sleeps for
real), and attempt timestamps with an injected clock.
"""

import array
import asyncio
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import struct
import subprocess
import threading
import time as time_mod
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from agent_tts import cli, fallback
from agent_tts.boundaries import BoundaryMap
from agent_tts.fallback import (
    AGENT_TTS_FALLBACK_CONFIG_ENV,
    DEFAULT_FALLBACK_CONFIG_PATH,
    RETRYABLE,
    SKIP_LINK,
    FallbackConfig,
    FallbackConfigError,
    FallbackLink,
    SynthesisIntent,
    WalkResult,
    classify_exception,
    fallback_config_path,
    link_key_missing,
    load_fallback_config,
    run_fallback_synthesis,
)
from agent_tts.text import split_sentence_groups
from agent_tts.wav import pcm_to_wav

ENGINE_DIR = Path(cli.__file__).resolve().parent
PROVIDERS_DIR = ENGINE_DIR / "providers"


# --- Fakes and helpers -----------------------------------------------------------

class FakeProvider:
    """Deterministic fake provider: a script of per-submit outcomes.

    Outcomes are bytes (``b""`` = swallowed failure, like the real
    elevenlabs/openai full-response path) or an Exception instance to
    raise through the engine seam (like edge does on network errors).
    """

    name = "fake"
    supports_stream = False

    def __init__(self, script=None, name="fake"):
        self.provider_name = name
        self.script = list(script or [])
        self.submits = 0
        self.voices = []

    async def synthesize(self, text, voice, rate, volume="+0%", pitch="+0Hz", stop_checker=None):
        self.submits += 1
        self.voices.append(voice)
        outcome = self.script.pop(0) if self.script else b"OK"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeSleeper:
    """Records backoff requests instead of sleeping."""

    def __init__(self):
        self.sleeps = []

    async def __call__(self, seconds):
        self.sleeps.append(seconds)


@pytest.fixture(autouse=True)
def isolated_fallback_env(tmp_path, monkeypatch):
    """Every test starts from a missing config in a tmp dir and a clean
    cache, so neither the machine's real config nor a previous test can
    leak in."""
    monkeypatch.delenv(AGENT_TTS_FALLBACK_CONFIG_ENV, raising=False)
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, str(tmp_path / "missing-fallback.json"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    fallback._reset_fallback_config_cache()
    yield
    fallback._reset_fallback_config_cache()


def write_config(tmp_path, payload, name="fallback.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps(payload) if isinstance(payload, (dict, list)) else payload,
        encoding="utf-8",
    )
    fallback._reset_fallback_config_cache()
    return str(path)


def make_intent(**overrides):
    base = dict(
        text="hello world",
        voice="v-primary",
        rate="+0%",
        volume="+0%",
        pitch="+0Hz",
        output_file=None,
        provider="alpha",
        openai_key=None,
        openai_base_url=None,
        openai_model=None,
        eleven_key=None,
        eleven_model=None,
        piper_model=None,
        auto_lang=False,
    )
    base.update(overrides)
    return SynthesisIntent(**base)


def enabled_config(*links):
    return FallbackConfig(enabled=True, chain=tuple(links), source="test")


def make_factory(registry):
    """Provider factory fake that records every build call."""
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return registry[kwargs["provider_name"]]

    factory.calls = calls
    return factory


async def registry_attempt(intent, link, engine):
    """Single-attempt callback that runs the fake engine like the real one."""
    return await engine.synthesize(text=intent.text, voice=link.voice, rate=intent.rate)


def run_walk(registry, config, intent=None, stop_checker=None, primary_engine=None, audibility=None):
    """Runs the walker with fake timing; returns (result, sleeper, factory)."""
    sleeper = FakeSleeper()
    factory = make_factory(registry)
    started = time_mod.monotonic()
    result = asyncio.run(
        run_fallback_synthesis(
            single_attempt=registry_attempt,
            intent=intent or make_intent(),
            config=config,
            primary_engine=primary_engine,
            engine_factory=factory,
            stop_checker=stop_checker,
            sleeper=sleeper,
            clock=lambda: 4242.0,
            audibility=audibility,
        )
    )
    result.wall_elapsed = time_mod.monotonic() - started
    return result, sleeper, factory


def http_error(code):
    try:
        raise urllib.error.HTTPError("url", code, "msg", None, None)  # type: ignore[arg-type]
    except urllib.error.HTTPError as e:
        return e


def outcomes(result):
    return [(r.provider, r.outcome) for r in result.attempts]


# --- VS4.1 required tests ---------------------------------------------------------

def test_no_config_behavior_identical(monkeypatch, tmp_path):
    """No config file ⇒ the exact historical path: the walker is never
    consulted, the engine is called once, and the result equals the
    direct single-attempt call bit for bit."""
    # Config level: a missing file resolves to the disabled representation.
    resolved = fallback.resolve_active_fallback_config()
    assert resolved.enabled is False
    assert resolved.chain == ()

    # Orchestration level: the walker must never run.
    def _bomb(*args, **kwargs):
        raise AssertionError("fallback walker ran with no config file")

    monkeypatch.setattr(fallback, "run_fallback_synthesis", _bomb)

    fresh = FakeProvider([b"PRIMARY-AUDIO", b"PRIMARY-AUDIO"])
    monkeypatch.setattr(cli, "get_provider", lambda **kwargs: fresh)

    via_public = asyncio.run(cli.synthesize(text="hi", voice="es-ES-ElviraNeural", provider="alpha"))
    via_direct = asyncio.run(
        cli._synthesize_single(text="hi", voice="es-ES-ElviraNeural", provider="alpha")
    )
    assert via_public == via_direct == b"PRIMARY-AUDIO"
    assert fresh.submits == 2  # one per call above, nothing else

    # Daemon-style prebuilt engine flows through identically.
    warm = FakeProvider([b"WARM"])
    out = asyncio.run(cli.synthesize(text="hi", engine=warm))
    assert out == b"WARM"
    assert warm.submits == 1


def test_schema_requires_notes(tmp_path):
    """``notes`` is mandatory and must be a non-empty string (D3)."""
    good = {"fallback_enabled": True, "chain": [{"provider": "edge", "voice": "elvira", "notes": "why"}]}
    path = write_config(tmp_path, good)
    config = load_fallback_config(path)
    assert config.enabled and config.chain[0].notes == "why"

    for bad_notes in (None, "", "   ", 7):
        payload = {
            "fallback_enabled": True,
            "chain": [{"provider": "edge", "voice": "elvira", "notes": bad_notes}],
        }
        with pytest.raises(FallbackConfigError) as excinfo:
            load_fallback_config(write_config(tmp_path, payload, name="bad.json"))
        assert "notes" in excinfo.value.reason

    # And when the field is absent entirely.
    payload = {"fallback_enabled": True, "chain": [{"provider": "edge", "voice": "elvira"}]}
    with pytest.raises(FallbackConfigError) as excinfo:
        load_fallback_config(write_config(tmp_path, payload, name="bad.json"))
    assert "notes" in excinfo.value.reason


@pytest.mark.parametrize(
    "payload",
    [
        "{ not json",                     # invalid JSON
        '["a", "b"]',                     # top level is not an object
        {"chain": []},                    # missing fallback_enabled
        {"fallback_enabled": True},       # missing chain
        {"fallback_enabled": "yes", "chain": []},   # non-boolean flag
        {"fallback_enabled": 1, "chain": []},       # truthy non-bool
        {"fallback_enabled": False, "chain": {}},   # chain not a list
        {"fallback_enabled": False, "chain": ["x"]},  # link not an object
        {"fallback_enabled": False, "chain": [{"provider": "edge"}]},  # link fields missing
        {"fallback_enabled": False, "extra": 1, "chain": []},  # unknown top field
        {
            "fallback_enabled": False,
            "chain": [{"provider": "edge", "voice": "v", "notes": "n", "priority": 1}],
        },  # unknown link field
        {
            "fallback_enabled": False,
            "chain": [{"provider": 3, "voice": "v", "notes": "n"}],
        },  # non-string provider
    ],
)
def test_invalid_config_typed_error(tmp_path, payload):
    """Every schema violation raises the typed error (never a raw
    json/error leak) with a machine-readable reason."""
    path = write_config(tmp_path, payload, name="invalid.json")
    with pytest.raises(FallbackConfigError) as excinfo:
        load_fallback_config(path)
    assert isinstance(excinfo.value.reason, str) and excinfo.value.reason
    assert excinfo.value.path == path


def test_generic_config_path_no_host_dependency(tmp_path, monkeypatch):
    """The default path follows the generic ~/.config/agent-tts pattern —
    no host paths inside the engine, and the env override is honored."""
    # Default: derived from the user's home, portable tilde constant.
    monkeypatch.delenv(AGENT_TTS_FALLBACK_CONFIG_ENV, raising=False)
    assert DEFAULT_FALLBACK_CONFIG_PATH == "~/.config/agent-tts/fallback.json"
    assert fallback_config_path() == os.path.expanduser(DEFAULT_FALLBACK_CONFIG_PATH)
    assert "herdr" not in DEFAULT_FALLBACK_CONFIG_PATH
    assert not DEFAULT_FALLBACK_CONFIG_PATH.startswith("/")

    # Env override is honored by load_fallback_config(path=None).
    env_path = write_config(
        tmp_path,
        {"fallback_enabled": True, "chain": [{"provider": "edge", "voice": "elvira", "notes": "n"}]},
        name="env-fallback.json",
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, env_path)
    config = load_fallback_config()
    assert config.enabled is True
    assert config.source == env_path

    # An explicit path beats the env override.
    explicit_path = write_config(
        tmp_path,
        {"fallback_enabled": False, "chain": []},
        name="explicit-fallback.json",
    )
    config = load_fallback_config(explicit_path)
    assert config.enabled is False
    assert config.source == explicit_path

    # The engine module itself carries no host or provider-internal coupling.
    source = (ENGINE_DIR / "fallback.py").read_text(encoding="utf-8")
    for forbidden in ("herdr", "hosts/", "/home/", "winhost", "_sync_request", "_sync_streamed", "urlopen"):
        assert forbidden not in source, forbidden


# --- VS4.2 required tests ---------------------------------------------------------

def test_stop_checker_before_each_attempt():
    """stop_checker runs before EVERY attempt — before the first submit,
    before an in-link resubmit, and between links. A cancelled walk
    returns b"" and never falls back."""
    alpha = FakeProvider([b""])
    beta = FakeProvider([b"OK"])
    registry = {"alpha": alpha, "beta": beta}

    # (a) Cancelled before anything: exactly one cancelled record, zero submits.
    result, sleeper, factory = run_walk(
        registry, enabled_config(FallbackLink("beta", "vb", "n")), stop_checker=lambda: True
    )
    assert result.cancelled and result.audio == b""
    assert outcomes(result) == [("alpha", "cancelled")]
    assert alpha.submits == beta.submits == 0
    assert factory.calls == []
    assert sleeper.sleeps == []

    # (b) Cancelled after a retryable FAILURE (raised, not empty): the
    # failure is recorded, then the pre-resubmit stop check cancels —
    # no sleep, no resubmit, no fallback to the next link.
    alpha2 = FakeProvider([])
    stop_flag = {"stop": False}

    def stop():
        return stop_flag["stop"]

    async def fail_then_stop(*args, **kwargs):
        alpha2.submits += 1
        stop_flag["stop"] = True
        raise urllib.error.URLError("network down")

    alpha2.synthesize = fail_then_stop
    result, sleeper, _ = run_walk({"alpha": alpha2}, enabled_config(), stop_checker=stop)
    assert result.cancelled and result.audio == b""
    assert outcomes(result) == [("alpha", "retryable-fail"), ("alpha", "cancelled")]
    assert alpha2.submits == 1
    assert sleeper.sleeps == []  # cancel wins over the backoff

    # (c) Cancelled between links: the failing link is skipped-class, the
    # stop fires before the NEXT link's first submit.
    alpha3 = FakeProvider([http_error(403)])
    beta3 = FakeProvider([b"OK"])
    stop_flag["stop"] = False  # fresh walk: fires during alpha's attempt

    async def skip_fail_then_stop(*args, **kwargs):
        alpha3.submits += 1
        stop_flag["stop"] = True
        raise http_error(403)

    alpha3.synthesize = skip_fail_then_stop
    result, sleeper, _ = run_walk(
        {"alpha": alpha3, "beta": beta3},
        enabled_config(FallbackLink("beta", "vb", "n")),
        stop_checker=stop,
    )
    assert result.cancelled
    assert outcomes(result) == [("alpha", "skip"), ("beta", "cancelled")]
    assert beta3.submits == 0


def test_same_provider_retry_reused_budget_shared():
    """The same-provider in-link retries and the cross-provider attempts
    draw on ONE shared budget: 2 per link, 4 total — a fifth submit
    never happens, links that no longer fit are never touched."""
    alpha = FakeProvider([b"", b""])   # primary: two retryable failures
    beta = FakeProvider([b"", b""])    # link 1: two more — budget dies here
    gamma = FakeProvider([b"OK"])      # link 2: must NEVER be submitted
    delta = FakeProvider([b"OK"])      # link 3: must NEVER be submitted
    registry = {"alpha": alpha, "beta": beta, "gamma": gamma, "delta": delta}
    config = enabled_config(
        FallbackLink("beta", "vb", "n1"),
        FallbackLink("gamma", "vg", "n2"),
        FallbackLink("delta", "vd", "n3"),
    )

    result, sleeper, _ = run_walk(registry, config)

    assert result.exhausted is True and result.audio == b""
    # The shared budget: alpha's same-provider retries (2) consumed the
    # same counter as beta's cross-provider attempts (2) — nothing left.
    assert alpha.submits == 2
    assert beta.submits == 2
    assert gamma.submits == delta.submits == 0
    assert outcomes(result) == [("alpha", "retryable-fail")] * 2 + [("beta", "retryable-fail")] * 2
    # Fixed backoff (fake clock): 2 s before the first resubmit, 4 s
    # before the second; advancing links does not sleep.
    assert sleeper.sleeps == [2.0, 4.0]
    # No real sleeping happened.
    assert result.wall_elapsed < 0.5


def test_missing_key_skips_before_submit():
    """A keyless link is skipped BEFORE any submit (not-configured, honest
    skip): no engine build, no network, outcome ``skip`` — and the walk
    continues to the configured links."""
    openai_fake = FakeProvider([b"SHOULD-NOT-RUN"])
    edge_fake = FakeProvider([b"EDGE-AUDIO"])
    registry = {"openai": openai_fake, "edge": edge_fake}
    config = enabled_config(FallbackLink("edge", "elvira", "free local voice"))
    intent = make_intent(provider="openai", openai_key=None)

    result, _, factory = run_walk(registry, config, intent=intent)

    assert result.ok is True and result.audio == b"EDGE-AUDIO"
    assert openai_fake.submits == 0                      # skipped before submit
    assert ("openai", "skip") in outcomes(result)        # honest skip record
    assert ("edge", "ok") in outcomes(result)
    # The keyless link was never even built: skip precedes construction.
    assert [c["provider_name"] for c in factory.calls] == ["edge"]
    # The chain link's voice reached the fake engine.
    assert edge_fake.voices == ["elvira"]

    # With a key present (explicit or env), the same provider submits.
    intent_keyed = make_intent(provider="openai", openai_key="sk-test")
    ok_openai = FakeProvider([b"OPENAI-AUDIO"])
    result, _, _ = run_walk({"openai": ok_openai}, enabled_config(), intent=intent_keyed)
    assert result.audio == b"OPENAI-AUDIO"
    assert ok_openai.submits == 1
    assert ("openai", "skip") not in outcomes(result)


def test_missing_elevenlabs_key_env_and_alias(tmp_path, monkeypatch):
    """elevenlabs/eleven alias links follow the same key rule (env-backed)."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "ek-test")
    eleven_fake = FakeProvider([b"ELEVEN-AUDIO"])
    result, _, _ = run_walk(
        {"eleven": eleven_fake},
        enabled_config(FallbackLink("eleven", "rachel", "n")),
        intent=make_intent(provider="edge"),
    )
    assert result.audio == b"ELEVEN-AUDIO"

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    eleven_keyless = FakeProvider([b"NOPE"])
    result, _, _ = run_walk(
        {"elevenlabs": eleven_keyless},
        enabled_config(FallbackLink("elevenlabs", "rachel", "n")),
        intent=make_intent(provider="edge"),
    )
    assert eleven_keyless.submits == 0
    assert ("elevenlabs", "skip") in outcomes(result)


def test_providers_not_rewritten(tmp_path, monkeypatch):
    """T9 FR-06: providers stay untouched. The walk never modifies the
    provider modules on disk, the working tree carries no provider
    changes, and the engine couples only through the public factory."""
    def _hash_providers():
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(PROVIDERS_DIR.glob("*.py"))
        }

    # One enabled walk through the real orchestration seat (fakes only).
    alpha = FakeProvider([http_error(403)])
    beta = FakeProvider([b"BETA-AUDIO"])
    registry = {"alpha": alpha, "beta": beta}

    config_path = write_config(
        tmp_path,
        {
            "fallback_enabled": True,
            "chain": [{"provider": "beta", "voice": "vb", "notes": "backup"}],
        },
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, config_path)
    fallback._reset_fallback_config_cache()

    factory = make_factory(registry)
    original_get_provider = fallback.get_provider
    monkeypatch.setattr(fallback, "get_provider", factory)
    result = asyncio.run(cli.synthesize(text="hi", provider="alpha", voice="v1"))

    assert result == b"BETA-AUDIO"  # the walk really ran
    assert [c["provider_name"] for c in factory.calls] == ["alpha", "beta"]

    before = _hash_providers()
    # A second walk must not touch the files either.
    asyncio.run(
        run_fallback_synthesis(
            single_attempt=registry_attempt,
            intent=make_intent(),
            config=enabled_config(FallbackLink("beta", "vb", "n")),
            engine_factory=make_factory({"alpha": alpha, "beta": beta}),
            sleeper=FakeSleeper(),
            clock=lambda: 0.0,
        )
    )
    assert _hash_providers() == before

    # The working tree carries no provider modifications (T9 FR-06):
    # HEAD comparison catches staged and unstaged edits alike.
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", str(PROVIDERS_DIR)],
            cwd=str(ENGINE_DIR.parent.parent),
            capture_output=True,
        )
        assert proc.returncode == 0, "providers/ modified in the working tree"
    except FileNotFoundError:
        pytest.skip("git unavailable")

    # Structural pin: the walker never references provider internals.
    source = (ENGINE_DIR / "fallback.py").read_text(encoding="utf-8")
    for forbidden in (
        "from agent_tts.providers.elevenlabs",
        "from agent_tts.providers.openai",
        "import agent_tts.providers.edge",
        "OpenAITTSProvider(",
        "ElevenLabsTTSProvider(",
        "_sync_request",
        "_sync_streamed",
    ):
        assert forbidden not in source, forbidden


# --- Supporting tests: configuration ----------------------------------------------

def test_missing_config_file_yields_disabled_silent(tmp_path):
    """A missing file is not an error: disabled representation, no raise."""
    config = load_fallback_config(str(tmp_path / "does-not-exist.json"))
    assert config.enabled is False and config.chain == ()


def test_disabled_flag_config_yields_disabled(tmp_path):
    """fallback_enabled:false (valid chain or not) keeps today's behavior."""
    path = write_config(
        tmp_path,
        {"fallback_enabled": False, "chain": [{"provider": "edge", "voice": "v", "notes": "n"}]},
    )
    config = load_fallback_config(path)
    assert config.enabled is False
    # The chain is still parsed (strict schema) for diagnostics.
    assert config.chain == (FallbackLink("edge", "v", "n"),)


def test_valid_config_loads_links(tmp_path):
    path = write_config(
        tmp_path,
        {
            "fallback_enabled": True,
            "chain": [
                {"provider": "openai", "voice": "nova", "notes": "cheap"},
                {"provider": "edge", "voice": "elvira", "notes": "free"},
            ],
        },
    )
    config = load_fallback_config(path)
    assert config.enabled is True
    assert config.chain == (
        FallbackLink("openai", "nova", "cheap"),
        FallbackLink("edge", "elvira", "free"),
    )
    assert config.source == path


def test_orchestration_failopen_on_invalid_config(tmp_path, capsys):
    """The seat fails open: an invalid file disables fallback (one stderr
    line) instead of breaking speech; the typed error stays available."""
    path = write_config(tmp_path, "{ broken", name="broken.json")
    import os as _os

    _os.environ[AGENT_TTS_FALLBACK_CONFIG_ENV] = path
    fallback._reset_fallback_config_cache()
    resolved = fallback.resolve_active_fallback_config()
    assert resolved.enabled is False
    assert "fallback disabled" in capsys.readouterr().err
    # Direct callers still get the typed error.
    with pytest.raises(FallbackConfigError):
        load_fallback_config(path)


def test_unreadable_config_typed_error(tmp_path):
    """An unreadable file is a typed error too, never a raw OSError."""
    if os.geteuid() == 0:
        pytest.skip("chmod-based unreadability does not apply to root")
    path = tmp_path / "secret.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0)
    try:
        with pytest.raises(FallbackConfigError) as excinfo:
            load_fallback_config(str(path))
        assert "could not read file" in excinfo.value.reason
    finally:
        path.chmod(0o644)


def test_attempt_log_counts():
    """AttemptLog.counts() summarizes outcomes (VS4.4 diagnostics seam)."""
    log = fallback.AttemptLog(clock=lambda: 1.0)
    log.record("alpha", "ok")
    log.record("alpha", "retryable-fail")
    log.record("beta", "retryable-fail")
    log.record("beta", "skip")
    log.record("beta", "cancelled")
    assert log.counts() == {"ok": 1, "retryable-fail": 2, "skip": 1, "cancelled": 1}


def test_config_cache_invalidates_on_write(tmp_path, monkeypatch):
    """Editing the file invalidates the resolved-config cache."""
    path = write_config(tmp_path, {"fallback_enabled": False, "chain": []})
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, path)
    assert fallback.resolve_active_fallback_config().enabled is False
    write_config(tmp_path, {
        "fallback_enabled": True,
        "chain": [{"provider": "edge", "voice": "v", "notes": "n"}],
    })
    resolved = fallback.resolve_active_fallback_config()
    assert resolved.enabled is True and len(resolved.chain) == 1


# --- Supporting tests: classification ---------------------------------------------

@pytest.mark.parametrize(
    "exc,expected",
    [
        (http_error(500), RETRYABLE),
        (http_error(503), RETRYABLE),
        (http_error(429), RETRYABLE),
        (http_error(401), SKIP_LINK),
        (http_error(404), SKIP_LINK),
        (urllib.error.URLError("connection refused"), RETRYABLE),
        (TimeoutError("timed out"), RETRYABLE),
        (ConnectionError("reset"), RETRYABLE),
        (OSError("broken pipe"), RETRYABLE),
        (ValueError("unsupported voice"), SKIP_LINK),
        (RuntimeError("OpenAI TTS requires an API key"), SKIP_LINK),
        (KeyError("mystery"), SKIP_LINK),  # unclassified never burns retries
    ],
)
def test_classification_table(exc, expected):
    assert classify_exception(exc) == expected


def test_skip_link_advances_without_backoff():
    """4xx (≠429) skips the link immediately: no retry, no sleep."""
    alpha = FakeProvider([http_error(403)])
    beta = FakeProvider([b"BETA"])
    result, sleeper, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
    )
    assert result.ok and result.audio == b"BETA"
    assert alpha.submits == 1
    assert sleeper.sleeps == []
    assert outcomes(result) == [("alpha", "skip"), ("beta", "ok")]


def test_http_429_retries_in_link_with_backoff():
    """429 is retryable: same provider resubmitted after the 2 s backoff."""
    alpha = FakeProvider([http_error(429), b"RECOVERED"])
    result, sleeper, _ = run_walk({"alpha": alpha}, enabled_config())
    assert result.ok and result.audio == b"RECOVERED"
    assert alpha.submits == 2
    assert sleeper.sleeps == [2.0]


def test_http_5xx_then_next_link():
    """5xx exhausts the in-link budget (2 submits) then advances."""
    alpha = FakeProvider([http_error(500), http_error(503)])
    beta = FakeProvider([b"BETA"])
    result, sleeper, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
    )
    assert result.ok and result.audio == b"BETA"
    assert alpha.submits == 2 and beta.submits == 1
    assert sleeper.sleeps == [2.0]  # one in-link resubmit only


def test_unclassified_exception_skips_link():
    """An unknown exception never burns retry budget: skip and advance."""
    alpha = FakeProvider([KeyError("boom")])
    beta = FakeProvider([b"BETA"])
    result, sleeper, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
    )
    assert result.ok
    assert alpha.submits == 1 and sleeper.sleeps == []
    assert outcomes(result)[0] == ("alpha", "skip")


# --- Supporting tests: walker mechanics --------------------------------------------

def test_primary_success_first_attempt():
    alpha = FakeProvider([b"PRIMARY"])
    result, sleeper, factory = run_walk({"alpha": alpha}, enabled_config())
    assert result.ok and result.audio == b"PRIMARY"
    assert outcomes(result) == [("alpha", "ok")]
    assert sleeper.sleeps == []
    assert [c["provider_name"] for c in factory.calls] == ["alpha"]


def test_primary_engine_reused_for_first_link():
    """The daemon's prebuilt (warm) engine serves the primary link: the
    factory is only consulted for chain links."""
    warm = FakeProvider([b"WARM"])
    beta = FakeProvider([b"BETA"])
    result, _, factory = run_walk(
        {"beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
        primary_engine=warm,
    )
    assert result.ok and result.audio == b"WARM"
    assert warm.submits == 1
    assert factory.calls == []  # primary never rebuilt


def test_build_failure_skips_link(capsys):
    """A provider that cannot be constructed (e.g. missing optional deps)
    is a not-configured skip, not a crash."""
    beta = FakeProvider([b"BETA"])

    def factory(**kwargs):
        if kwargs["provider_name"] == "broken":
            raise RuntimeError("kokoro provider requires optional dependencies")
        return beta

    sleeper = FakeSleeper()
    result = asyncio.run(
        run_fallback_synthesis(
            single_attempt=registry_attempt,
            intent=make_intent(provider="broken"),
            config=enabled_config(FallbackLink("ok", "v", "n")),
            engine_factory=factory,
            sleeper=sleeper,
            clock=lambda: 0.0,
        )
    )
    assert result.ok is True and result.audio == b"BETA"
    assert outcomes(result) == [("broken", "skip"), ("ok", "ok")]
    assert "could not be built" in capsys.readouterr().err


def test_attempt_records_schema():
    """Attempt records carry exactly the VS4.4 schema {provider, outcome, ts}."""
    alpha = FakeProvider([b""])
    beta = FakeProvider([b"BETA"])
    result, _, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
    )
    assert result.ok
    fields = set(result.attempts[0].__dataclass_fields__)
    assert fields == {"provider", "outcome", "ts"}
    assert all(r.ts == 4242.0 for r in result.attempts)  # injected clock
    assert all(isinstance(r.provider, str) for r in result.attempts)
    assert all(r.outcome in ("ok", "retryable-fail", "skip", "cancelled") for r in result.attempts)


def test_empty_result_cancel_never_falls_back():
    """A provider returning b"" because the job was stopped is a cancel:
    zero further attempts, never a fallback."""
    alpha = FakeProvider([b""])
    beta = FakeProvider([b"BETA"])
    stop_flag = {"stop": False}
    original = alpha.synthesize

    async def stop_after_empty(*args, **kwargs):
        out = await original(*args, **kwargs)
        stop_flag["stop"] = True
        return out

    alpha.synthesize = stop_after_empty
    result, sleeper, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
        stop_checker=lambda: stop_flag["stop"],
    )
    assert result.cancelled and result.audio == b""
    assert beta.submits == 0
    assert sleeper.sleeps == []
    assert outcomes(result) == [("alpha", "cancelled")]


def test_budget_per_link_cap_non_stream():
    """A link never exceeds FALLBACK_LINK_ATTEMPTS submits even when the
    total budget would allow more."""
    alpha = FakeProvider([b""] * 5)
    beta = FakeProvider([b""] * 5)
    result, _, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
    )
    assert result.exhausted
    assert alpha.submits == 2 and beta.submits == 2  # per-link caps
    assert len(result.attempts) == fallback.FALLBACK_TOTAL_ATTEMPTS


# --- Supporting tests: orchestration seat (cli.synthesize) --------------------------

def test_cli_enabled_path_walks_chain(tmp_path, monkeypatch):
    """The seat delegates to the walker when enabled: primary fails with
    a skip-class error, the chain link synthesizes with ITS voice."""
    config_path = write_config(
        tmp_path,
        {
            "fallback_enabled": True,
            "chain": [{"provider": "beta", "voice": "chain-voice", "notes": "backup"}],
        },
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, config_path)

    alpha = FakeProvider([http_error(403)])
    beta = FakeProvider([b"BETA-AUDIO"])
    factory = make_factory({"alpha": alpha, "beta": beta})
    monkeypatch.setattr(fallback, "get_provider", factory)

    result = asyncio.run(cli.synthesize(text="hi", provider="alpha", voice="job-voice"))
    assert result == b"BETA-AUDIO"
    assert alpha.submits == 1 and beta.submits == 1
    # The chain link ran with the CHAIN's voice, not the job's.
    assert beta.voices == ["chain-voice"]
    # The primary was built through the factory (no engine passed in).
    assert [c["provider_name"] for c in factory.calls] == ["alpha", "beta"]


def test_cli_enabled_path_in_link_retry(tmp_path, monkeypatch):
    """A retryable primary failure is resubmitted in-link before the
    chain advances (fake sleeper injected at walker level; here the
    primary recovers on submit 2 so no chain link is needed)."""
    config_path = write_config(
        tmp_path,
        {
            "fallback_enabled": True,
            "chain": [{"provider": "beta", "voice": "vb", "notes": "n"}],
        },
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, config_path)

    alpha = FakeProvider([http_error(429), b"RECOVERED"])
    monkeypatch.setattr(fallback, "get_provider", make_factory({"alpha": alpha}))

    result = asyncio.run(cli.synthesize(text="hi", provider="alpha", voice="job-voice"))
    assert result == b"RECOVERED"
    assert alpha.submits == 2


def test_cli_enabled_path_exhaustion_returns_empty(tmp_path, monkeypatch, capsys):
    """Every link failing (skip-class here, so zero real sleeps) ends the
    walk: b"" returned — today's failure contract — plus one summary line."""
    config_path = write_config(
        tmp_path,
        {
            "fallback_enabled": True,
            "chain": [{"provider": "beta", "voice": "vb", "notes": "n"}],
        },
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, config_path)

    alpha = FakeProvider([http_error(401)])
    beta = FakeProvider([http_error(403)])
    monkeypatch.setattr(fallback, "get_provider", make_factory({"alpha": alpha, "beta": beta}))

    result = asyncio.run(cli.synthesize(text="hi", provider="alpha", voice="job-voice"))
    assert result == b""
    err = capsys.readouterr().err
    assert "Fallback: synthesis failed on every link" in err
    assert alpha.submits == 1 and beta.submits == 1


def test_cli_disabled_flag_keeps_direct_path(tmp_path, monkeypatch):
    """fallback_enabled:false in an existing file = the direct path."""
    config_path = write_config(
        tmp_path,
        {"fallback_enabled": False, "chain": [{"provider": "beta", "voice": "v", "notes": "n"}]},
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, config_path)

    def _bomb(*args, **kwargs):
        raise AssertionError("walker ran while fallback_enabled is false")

    monkeypatch.setattr(fallback, "run_fallback_synthesis", _bomb)
    alpha = FakeProvider([b"DIRECT"])
    monkeypatch.setattr(cli, "get_provider", lambda **kwargs: alpha)
    assert asyncio.run(cli.synthesize(text="hi", provider="alpha")) == b"DIRECT"
    assert alpha.submits == 1


# --- VS4.3/VS4.4: fakes and helpers for audible partials --------------------------

class RecordingProvider(FakeProvider):
    """Fake provider that records every submit's text.

    A scripted Exception is raised AFTER partial internal bytes were
    "produced" — bytes the provider boundary swallows, exactly like the
    real elevenlabs/openai engines die mid-synthesis.
    """

    def __init__(self, script=None, name="fake"):
        super().__init__(script, name)
        self.texts = []
        self.swallowed_partials = []

    async def synthesize(self, text, voice, rate, volume="+0%", pitch="+0Hz", stop_checker=None):
        self.submits += 1
        self.voices.append(voice)
        self.texts.append(text)
        outcome = self.script.pop(0) if self.script else b"OK"
        if isinstance(outcome, Exception):
            self.swallowed_partials.append(text[: max(1, len(text) // 2)].encode("utf-8"))
            raise outcome
        return outcome


class FakePipelinedSession:
    """Duck-typed playback session: records the segments committed to the
    speaker (the audible marker the tests assert on) instead of opening
    an audio device."""

    def __init__(self):
        self.lock = threading.Lock()
        self.boundaries = BoundaryMap()
        self.state = {"status": "playing", "stop": False, "producing": False}
        self.prepared = []
        self.appended = []

    def prepare_pcm(self, decoded):
        self.prepared.append(decoded)

    def append_pcm(self, decoded):
        self.appended.append(decoded)
        return True

    def play(self, decoded):
        pass


def fake_decoded(frames=2400):
    """Miniaudio-decoded stand-in (0.1 s of silence at 24 kHz mono)."""

    class _Decoded:
        pass

    decoded = _Decoded()
    decoded.sample_rate = 24000
    decoded.nchannels = 1
    decoded.sample_width = 2
    decoded.samples = array.array("h", [0] * frames)
    return decoded


PIPELINED_SENTENCE = (
    "The quick brown fox jumps over the lazy dog while the rain keeps "
    "falling softly on the old tin roof of the barn across the field."
)
MP3_FIXTURE = Path(__file__).parent / "fixtures" / "sample_24k.mp3"


def three_group_text():
    """Text that splits into exactly three pipelined sentence groups."""
    text = (PIPELINED_SENTENCE + " ") * 3
    assert len(split_sentence_groups(text)) == 3
    return text


def run_pipelined_walk(session, text, primary, chain_registry, probe=None):
    """Drives _speak_pipelined with the REAL cli.synthesize (walker live).

    Fake engines, fake decode, no network, no audio device. Returns
    (stderr text, chain factory)."""
    factory = make_factory(chain_registry)

    def fake_decode(data):
        return fake_decoded()

    stderr = io.StringIO()
    with mock.patch(
        "agent_tts.cli.get_provider", return_value=primary
    ), mock.patch("agent_tts.fallback.get_provider", factory), mock.patch(
        "agent_tts.cli.miniaudio.decode", fake_decode
    ), contextlib.redirect_stderr(stderr):
        asyncio.run(
            cli._speak_pipelined(
                session=session,
                text=text,
                check_stop=lambda: bool(session.state.get("stop", False)),
                voice="primary-voice",
                rate="+0%",
                volume="+0%",
                pitch="+0Hz",
                provider="alpha",
                openai_key=None,
                openai_base_url=None,
                openai_model=None,
                eleven_key=None,
                eleven_model=None,
                piper_model=None,
                auto_lang=False,
                stream="groups",
                audibility=probe,
            )
        )
    return stderr.getvalue(), factory


def armed_chain_config(tmp_path, monkeypatch, voice="chain-voice"):
    """Enables a one-link chain (beta) through the real config seat."""
    config_path = write_config(
        tmp_path,
        {
            "fallback_enabled": True,
            "chain": [{"provider": "beta", "voice": voice, "notes": "backup"}],
        },
    )
    monkeypatch.setenv(AGENT_TTS_FALLBACK_CONFIG_ENV, config_path)


# --- VS4.3 required tests ----------------------------------------------------------


def test_automatic_fallback_only_never_audible_bytes(tmp_path, monkeypatch):
    """Rule 1: automatic fallback re-synthesizes ONLY bytes that were
    NEVER audible. File/no-play jobs own no audio until completion, so a
    mid-synthesis primary failure allows a FULL restart on the next
    link — the whole text is re-synthesized from scratch and the log
    shows the chain."""
    full_text = "File job body text that is long enough to matter here. " * 4

    # (a) Walker level (file intent, fake clock): the primary dies
    # mid-synthesis twice; the next link restarts from scratch.
    alpha = RecordingProvider([urllib.error.URLError("reset mid-synthesis")] * 2)
    beta = RecordingProvider([b"FULL-AUDIO-BETA"])
    result, sleeper, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "chain-voice", "backup")),
        intent=make_intent(text=full_text, output_file=str(tmp_path / "walker-out.mp3")),
    )
    assert result.ok and result.audio == b"FULL-AUDIO-BETA"
    assert result.partial_uncertain is False
    assert outcomes(result) == [
        ("alpha", "retryable-fail"),
        ("alpha", "retryable-fail"),
        ("beta", "ok"),
    ]
    # FULL restart on the next link: beta received the WHOLE text from
    # scratch (never a resume), with the chain link's voice.
    assert alpha.texts == [full_text, full_text]
    assert beta.texts == [full_text]
    assert beta.voices == ["chain-voice"]
    # The primary's internal partial bytes were swallowed at the provider
    # boundary: only beta's full render exists in the result.
    assert alpha.swallowed_partials and result.audio == b"FULL-AUDIO-BETA"
    assert sleeper.sleeps == [2.0]  # one in-link resubmit; link cap then advances

    # (b) The real seat, file mode (skip-class failure: no real backoff).
    armed_chain_config(tmp_path, monkeypatch)
    out_file = tmp_path / "seat-out.mp3"
    alpha_seat = RecordingProvider([http_error(403)])
    beta_seat = RecordingProvider([b"SEAT-FILE-AUDIO"])
    monkeypatch.setattr(
        fallback, "get_provider", make_factory({"alpha": alpha_seat, "beta": beta_seat})
    )

    via_seat = asyncio.run(
        cli.synthesize(
            text=full_text, provider="alpha", voice="job-voice", output_file=str(out_file)
        )
    )
    assert via_seat == b"SEAT-FILE-AUDIO"
    assert alpha_seat.texts == [full_text] and beta_seat.texts == [full_text]
    assert out_file.read_bytes() == b"SEAT-FILE-AUDIO"


def test_partial_audible_stops_visible_deliberate_replay_acknowledged_duplicates(
    tmp_path, monkeypatch
):
    """Rule 2 (pipelined groups): once a partial is ALREADY audible, a
    synthesis failure stops the job with a VISIBLE partial/uncertain
    state — no automatic retry, NO further link — and the way out is a
    deliberate replay intent that re-reads from the failed group start,
    with duplicates explicitly acknowledged in the state/evidence."""
    text = three_group_text()
    groups = split_sentence_groups(text)
    armed_chain_config(tmp_path, monkeypatch)

    # Group 0 succeeds and is committed to the session (audible); group 1
    # dies mid-synthesis on the primary.
    alpha = RecordingProvider([b"G0-AUDIO", urllib.error.URLError("reset mid-group-1")])
    beta = RecordingProvider([b"NEVER-PLAYED"])
    session = FakePipelinedSession()
    probe = fallback.AudibilityProbe()

    stderr, factory = run_pipelined_walk(session, text, alpha, {"beta": beta}, probe=probe)

    # Audible marker: exactly group 0 was committed; the failed group
    # never reached the speaker.
    assert len(session.prepared) == 1 and session.appended == []

    # NO further link attempts: beta was neither built nor submitted.
    assert beta.submits == 0
    assert [c["provider_name"] for c in factory.calls] == []

    # VISIBLE state: session flag + stderr, and the honest state line —
    # never the misleading every-link-failed summary.
    assert session.state.get("partial_uncertain") is True
    assert "partial/uncertain" in stderr
    assert "duplicates" in stderr
    assert "failed on every link" not in stderr

    # Typed outcome surfaced: the per-intent log (retrievable from the
    # job's probe) records partial-uncertain; group 0's ok walk is
    # retrievable evidence too.
    assert probe.partial_uncertain is True and probe.audible() is True
    assert outcomes(probe.walks[0]) == [("alpha", "ok")]
    failed_walk = probe.walks[-1]
    assert failed_walk.partial_uncertain is True and failed_walk.audio == b""
    assert outcomes(failed_walk) == [("alpha", "partial-uncertain")]
    assert "duplicates" in failed_walk.failure_reason

    # Deliberate replay: a NEW synthesis intent for the pending text,
    # re-reading from the START of the failed group — duplicates of
    # already-heard audio are acknowledged by the state above, never
    # hidden behind a fake "exact resume".
    replay_text = " ".join(groups[1:])
    replay = RecordingProvider([b"REPLAY-AUDIO"])
    out = asyncio.run(cli.synthesize(text=replay_text, provider="alpha", engine=replay))
    assert out == b"REPLAY-AUDIO"
    assert replay.texts == [replay_text]
    assert replay.texts[0].startswith(groups[1])


class FailingFrameEngine:
    """Frames-mode engine: streams valid fixture MP3, then dies
    mid-stream — optionally waiting for a gate so the death is
    deterministic (after the cushion became audible)."""

    name = "alpha"
    supports_stream = True
    stream_yields_group_chunks = False

    def __init__(self, data, fail_after, die_after=None):
        self._data = data
        self._fail_after = fail_after
        self._die_after = die_after
        self.streamed = 0
        self.failed = False

    def synthesize_stream(
        self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", stop_checker=None, on_event=None
    ):
        view = self._data[: self._fail_after]
        for i in range(0, len(view), 720):
            if stop_checker and stop_checker():
                return
            self.streamed += 1
            yield view[i : i + 720]
        self.failed = True
        if self._die_after is not None:
            # Bounded flag wait (test-side ordering only): die strictly
            # after the cushion was committed to the session.
            deadline = time_mod.monotonic() + 5.0
            while not self._die_after() and time_mod.monotonic() < deadline:
                time_mod.sleep(0.005)
        raise urllib.error.URLError("connection reset mid-stream")


def test_frames_mode_abstains_after_partial(tmp_path, monkeypatch):
    """Rule 3 (frames/PC playback): after an audible partial the frames
    path ABSTAINS — zero cross-provider attempts (the chain stays armed
    and untouched), honest visible state, only deliberate replay."""
    armed_chain_config(tmp_path, monkeypatch)

    # The payload repeats the fixture: miniaudio's streaming decoder
    # demands a 64 KB first read, so the cushion only decodes mid-stream
    # once enough bytes are live — a short stream would stall until the
    # feeder's finish_stream, making the audible-before-death ordering
    # (the point of this test) unreachable.
    data = MP3_FIXTURE.read_bytes() * 8
    probe = fallback.AudibilityProbe()
    engine = FailingFrameEngine(data, fail_after=int(len(data) * 0.85), die_after=probe.audible)
    beta = RecordingProvider([b"NEVER"])
    session = FakePipelinedSession()
    factory = make_factory({"beta": beta})

    stderr = io.StringIO()
    with mock.patch("agent_tts.fallback.get_provider", factory), contextlib.redirect_stderr(stderr):
        asyncio.run(
            cli._speak_pipelined(
                session=session,
                text=three_group_text(),
                check_stop=lambda: False,
                voice="primary-voice",
                rate="+0%",
                volume="+0%",
                pitch="+0Hz",
                provider="alpha",
                openai_key=None,
                openai_base_url=None,
                openai_model=None,
                eleven_key=None,
                eleven_model=None,
                piper_model=None,
                auto_lang=False,
                engine=engine,
                stream="frames",
                audibility=probe,
            )
        )
    err = stderr.getvalue()

    # An audible partial really happened: the cushion was committed and
    # live frames kept playing while the stream died mid-way.
    assert len(session.prepared) == 1
    assert len(session.appended) > 0
    assert engine.streamed > 0 and engine.failed

    # Abstention: ZERO cross-provider attempts (nothing built, nothing
    # submitted, the walker never even ran).
    assert beta.submits == 0
    assert [c["provider_name"] for c in factory.calls] == []
    assert probe.walks == []

    # Honest visible state.
    assert probe.partial_uncertain is True
    assert session.state.get("partial_uncertain") is True
    assert "partial/uncertain" in err and "duplicates" in err


def test_noplay_full_restart_allowed(tmp_path, monkeypatch):
    """Rule 1 (no_play): a synthesis-only job owns no audio — nothing can
    be audible — so the next link is a FULL restart, exactly like file mode."""
    armed_chain_config(tmp_path, monkeypatch)

    full_text = "No play body text that is long enough to matter here. " * 4
    alpha = RecordingProvider([http_error(403)])
    beta = RecordingProvider([b"NOPLAY-FULL-AUDIO"])
    monkeypatch.setattr(
        fallback, "get_provider", make_factory({"alpha": alpha, "beta": beta})
    )

    # The daemon's direct-synthesis shape: session=None, no_play=True.
    asyncio.run(
        cli._play_speech(None, full_text, voice="job-voice", provider="alpha", no_play=True)
    )
    assert alpha.texts == [full_text]
    assert beta.texts == [full_text]  # FULL restart on the next link
    assert beta.voices == ["chain-voice"]
    assert beta.submits == 1


# --- VS4.4 required test ------------------------------------------------------------


def test_attempt_log_schema():
    """VS4.4: the attempt-log schema is pinned — records carry exactly
    {provider, outcome, ts}, outcomes come from exactly the five-value
    honest vocabulary (one honest outcome per scenario below), and each
    intent's log is retrievable from its own walk result, never shared
    across intents."""
    # Exact record fields.
    assert {f.name for f in dataclasses.fields(fallback.AttemptRecord)} == {
        "provider",
        "outcome",
        "ts",
    }
    # Exact outcome vocabulary.
    assert fallback.ATTEMPT_OUTCOMES == frozenset(
        {"ok", "retryable-fail", "skip", "cancelled", "partial-uncertain"}
    )
    # Nothing outside the vocabulary is recordable.
    with pytest.raises(ValueError):
        fallback.AttemptLog(clock=lambda: 0.0).record("alpha", "bogus")

    # Honest outcome per scenario.
    r_ok, _, _ = run_walk({"alpha": FakeProvider([b"X"])}, enabled_config())
    assert outcomes(r_ok) == [("alpha", "ok")] and r_ok.ok

    r_retry, _, _ = run_walk({"alpha": FakeProvider([http_error(429), b"RECOVERED"])}, enabled_config())
    assert outcomes(r_retry) == [("alpha", "retryable-fail"), ("alpha", "ok")]

    r_skip, _, _ = run_walk(
        {"alpha": FakeProvider([http_error(403)]), "beta": FakeProvider([b"B"])},
        enabled_config(FallbackLink("beta", "vb", "n")),
    )
    assert outcomes(r_skip) == [("alpha", "skip"), ("beta", "ok")]

    r_cancel, _, _ = run_walk(
        {"alpha": FakeProvider([b""])}, enabled_config(), stop_checker=lambda: True
    )
    assert outcomes(r_cancel) == [("alpha", "cancelled")] and r_cancel.cancelled

    probe = fallback.AudibilityProbe()
    probe.mark_audible()
    r_partial, _, _ = run_walk(
        {"alpha": FakeProvider([b""]), "beta": FakeProvider([b"NEVER"])},
        enabled_config(FallbackLink("beta", "vb", "n")),
        audibility=probe,
    )
    assert outcomes(r_partial) == [("alpha", "partial-uncertain")]
    assert r_partial.partial_uncertain and not r_partial.exhausted and not r_partial.cancelled
    assert "duplicates" in r_partial.failure_reason

    # Every record: string provider, injected-clock timestamp.
    for result in (r_ok, r_retry, r_skip, r_cancel, r_partial):
        assert all(r.ts == 4242.0 for r in result.attempts)
        assert all(isinstance(r.provider, str) and r.provider for r in result.attempts)

    # Per-intent logs are per-walk: retrievable from each result, never
    # shared across intents.
    assert r_ok.attempts is not r_skip.attempts
    assert len(r_skip.attempts) == 2
    assert {r.provider for r in r_skip.attempts} == {"alpha", "beta"}
    assert all(r.outcome in fallback.ATTEMPT_OUTCOMES for r in r_partial.attempts)


# --- VS4.3 supporting tests: walker and pipeline branches ---------------------------


def test_raised_failure_after_audible_partial_stops():
    """Walker contract, raised-failure variant: audible job + failure ⇒
    partial-uncertain stop before ANY retry, fallback or backoff."""
    probe = fallback.AudibilityProbe()
    probe.mark_audible()
    alpha = FakeProvider([urllib.error.URLError("down"), b"NEVER"])
    beta = FakeProvider([b"NEVER"])
    result, sleeper, _ = run_walk(
        {"alpha": alpha, "beta": beta},
        enabled_config(FallbackLink("beta", "vb", "n")),
        audibility=probe,
    )
    assert result.partial_uncertain and result.audio == b""
    assert not result.ok and not result.cancelled and not result.exhausted
    assert outcomes(result) == [("alpha", "partial-uncertain")]
    assert alpha.submits == 1 and beta.submits == 0
    assert sleeper.sleeps == []  # no automatic retry of any kind


class ExplodingDecoder:
    """Decoder stand-in: yields one cushion chunk, then dies mid-stream."""

    def __init__(self, *args, **kwargs):
        pass

    def feed_bytes(self, chunk):
        pass

    def finish_stream(self):
        pass

    def decode_generator(self):
        yield b"pcm-cushion"
        raise RuntimeError("decoder died mid-stream")


class OneChunkFrameEngine:
    """Frames engine whose feeder completes normally (the decode side dies)."""

    name = "alpha"
    supports_stream = True
    stream_yields_group_chunks = False

    def synthesize_stream(
        self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", stop_checker=None, on_event=None
    ):
        yield b"fed-bytes"


def test_frame_decode_error_after_audible_partial_raises_honestly(tmp_path, monkeypatch):
    """Frames decode death after an audible partial: the honest state is
    printed AND the error still surfaces (never swallowed into a fake resume)."""
    armed_chain_config(tmp_path, monkeypatch)

    probe = fallback.AudibilityProbe()
    beta = RecordingProvider([b"NEVER"])
    session = FakePipelinedSession()
    factory = make_factory({"beta": beta})
    stderr = io.StringIO()

    with mock.patch("agent_tts.cli.Mp3StreamDecoder", ExplodingDecoder), mock.patch(
        "agent_tts.fallback.get_provider", factory
    ), contextlib.redirect_stderr(stderr):
        with pytest.raises(RuntimeError, match="decoder died mid-stream"):
            asyncio.run(
                cli._speak_pipelined(
                    session=session,
                    text=three_group_text(),
                    check_stop=lambda: False,
                    voice="primary-voice",
                    rate="+0%",
                    volume="+0%",
                    pitch="+0Hz",
                    provider="alpha",
                    openai_key=None,
                    openai_base_url=None,
                    openai_model=None,
                    eleven_key=None,
                    eleven_model=None,
                    piper_model=None,
                    auto_lang=False,
                    engine=OneChunkFrameEngine(),
                    stream="frames",
                    audibility=probe,
                )
            )

    assert len(session.prepared) == 1  # the cushion was audible
    assert probe.partial_uncertain is True
    assert "partial/uncertain" in stderr.getvalue()
    assert beta.submits == 0  # abstention: zero cross-provider attempts


class FailingGroupStreamEngine:
    """Group-chunk stream (piper-style): one valid WAV, then death."""

    name = "alpha"
    supports_stream = True
    stream_yields_group_chunks = True

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.failed = False

    def synthesize_stream(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", stop_checker=None):
        for chunk in self._chunks:
            if stop_checker and stop_checker():
                return
            yield chunk
        self.failed = True
        raise urllib.error.URLError("piper died after the first group")


def test_group_stream_failure_after_audible_notes_partial(tmp_path, monkeypatch):
    """Group-chunk stream dying after audible bytes: honest partial/
    uncertain stop; the armed chain stays untouched (no automatic
    cross-fallback exists on that path — and none is added)."""
    armed_chain_config(tmp_path, monkeypatch)

    wav = pcm_to_wav(struct.pack("<h", 1) * 40, sample_rate=24000, channels=1)
    engine = FailingGroupStreamEngine([wav])
    probe = fallback.AudibilityProbe()
    beta = RecordingProvider([b"NEVER"])
    session = FakePipelinedSession()
    factory = make_factory({"beta": beta})
    stderr = io.StringIO()

    with mock.patch("agent_tts.fallback.get_provider", factory), contextlib.redirect_stderr(stderr):
        asyncio.run(
            cli._speak_pipelined(
                session=session,
                text=three_group_text(),
                check_stop=lambda: False,
                voice="primary-voice",
                rate="+0%",
                volume="+0%",
                pitch="+0Hz",
                provider="alpha",
                openai_key=None,
                openai_base_url=None,
                openai_model=None,
                eleven_key=None,
                eleven_model=None,
                piper_model=None,
                auto_lang=False,
                engine=engine,
                stream="groups",
                audibility=probe,
            )
        )

    assert len(session.prepared) == 1  # group 0 was audible
    assert engine.failed
    assert probe.partial_uncertain is True
    assert session.state.get("partial_uncertain") is True
    assert "partial/uncertain" in stderr.getvalue()
    assert beta.submits == 0 and factory.calls == []
