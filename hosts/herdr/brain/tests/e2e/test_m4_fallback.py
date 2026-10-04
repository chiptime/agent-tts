"""Milestone 4 E2E: the engine's cross-provider fallback chain through the
whole real stack (VS4.5, PRD 04).

Real browser against the real brain app over HTTP — same harness contract as
M1/M2/M3 (D6/T12.4): nothing in the media stack is mocked, completion
evidence is only ever the platform-fired ``ended`` event on the single
``#player`` element, and Chromium launches with exactly the autoplay flag
from the shared conftest.

What is REAL here: the walker (agent_tts.fallback.run_fallback_synthesis via
cli.synthesize), the budget (4 total / 2 per link), failure classification,
the in-link retry backoff, the stop-before-every-attempt rule, the manifest
protocol (contracts/tts-brain-v2.md), the brain producer subprocess +
/speech/{id}/next transport, and the PWA segment player with Chromium's real
decode/playback pipeline. Zero network: the ONLY faked layer is the provider
boundary, injected by a test-double HOST.

The double host (below) is a python script born in tmp_path and pointed at
by Settings.tts_bin. It answers the herdr-tts CLI surface
(--contract-version, --contract-capabilities -> [1, 2], --render-text) and
implements --render-text-segmented by importing the WORKTREE engine +
lib/segmented_render.py and running segmented_render.main() UNMODIFIED —
the exact way the production host drives the engine (real sentence
grouping, one cli.synthesize per group, atomic per-segment manifest
publishes). Before the first render it patches the provider seam the
engine's own tests patch (agent_tts.fallback.get_provider — a test double
monkeypatching at import time is precisely its job) with deterministic
fakes that return REAL fixture MP3 bytes and script env-controlled
failures. Every provider submit and every real walker attempt record is
appended to jsonl evidence files for precise assertions.

Because each sentence group is its own cli.synthesize intent, every group
gets a fresh 4/2 budget — the audibility rules never engage on the host
side (a file-writing host owns no audible audio), so a mid-render primary
failure falls back to the secondary for the SAME group text without ever
re-rendering already-published groups: the phone hears the complete answer
exactly once per segment.

The three tests:
  1. test_fake_providers_e2e — a 5-group answer whose primary dies
     mid-render (after group 0): the phone hears ALL five segments through
     the real pipeline exactly once in order (the secondary delivers every
     failed group), with real decoded durations, and the evidence files
     show the chain walked inside the locked budget (4 total / 2 per link
     per intent).
  2. test_cancel_during_fallback_zero_further_attempts — stop pressed
     while the primary is failing over and the secondary is mid-render:
     immediate silence (player paused, zero further /audio/sr- fetches),
     the submit counters FROZEN at cancel time (polled with a deadline —
     zero provider attempts after the stop), job terminal cancelled
     server-side.
  3. test_no_duplicate_submissions — a slower 4-group run asserted
     precisely: per intent the sum of submits stays <= 4, each submit is
     logged exactly once, and the per-(group, provider) counters match the
     expected walk EXACTLY (primary: 1 ok group then exactly 2 per failed
     group — the locked in-link retry; secondary: exactly 1 per failed
     group; nothing beyond the locked rules).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import pytest
import uvicorn

import herdr_brain.tts as herdr_brain_tts
from herdr_brain.config import Settings
from herdr_brain.server import create_app
from herdr_brain.watcher import AgentWatcher
from tests.conftest import SETTINGS_KWARGS, StubHerdr
from tests.e2e.conftest import (
    FIXTURES_DIR,
    FakeLLM,
    ask_via_keyboard,
    wait_for_sse_subscriber,
)

# Worktree locations resolved from THIS test file so every subprocess runs
# the REAL production files of this checkout, never an install.
REPO_ROOT = Path(__file__).resolve().parents[5]
ENGINE_SRC = REPO_ROOT / "engine" / "src"
HOST_LIB_DIR = REPO_ROOT / "hosts" / "herdr" / "tts-plugin" / "lib"
_ENGINE_VENV_PY = REPO_ROOT / "engine" / ".venv" / "bin" / "python"
# Local checkouts have the engine venv; CI installs the engine into the
# active interpreter instead (see .github/workflows/ci.yml), so fall back to it.
ENGINE_PY = _ENGINE_VENV_PY if _ENGINE_VENV_PY.exists() else Path(sys.executable)

# Locked fallback budget (engine fallback.py: FALLBACK_TOTAL_ATTEMPTS /
# FALLBACK_LINK_ATTEMPTS). The brain venv cannot import agent_tts, so the
# locked numbers are pinned here as literals next to their reference.
FALLBACK_TOTAL_ATTEMPTS = 4
FALLBACK_LINK_ATTEMPTS = 2

PRIMARY_PROVIDER = "edge"        # the provider the host requests by default
SECONDARY_PROVIDER = "m4secondary"  # the chain link in fallback.json

# One long sentence (> 2 x 125 chars pairs never merge): the engine's greedy
# 250-char grouping gives EXACTLY one group per sentence (verified against
# agent_tts.text.split_sentence_groups with the engine venv).
_LONG_SENTENCE = (
    "La respuesta completa del agente cubre primero el contexto amplio de "
    "la conversacion con detalles suficientes para ocupar una frase larga "
    "que supere con claridad el limite de agrupacion"
)


def groups_text(n: int) -> str:
    """Ask text whose answer splits into exactly ``n`` sentence groups."""
    return " ".join(f"{_LONG_SENTENCE}. Tramo {i}." for i in range(n))


# ---------------------------------------------------------------------------
# The fallback-capable host double (born in tmp_path, chmod +x, shebang =
# the worktree ENGINE venv python so agent_tts imports actually resolve).
# ---------------------------------------------------------------------------

_DOUBLE_HOST_SOURCE = '''#!/usr/bin/env __ENGINE_PYTHON__
"""M4 fallback host double (voice-stack VS4.5 E2E). A TEST DOUBLE.

Answers the herdr-tts CLI surface exactly like a protocol-2 host:
  --contract-version        -> 1
  --contract-capabilities   -> {"supported_protocols": [1, 2]}
  --render-text OUT TEXT    -> whole fixture bytes (v1 surface, unused here)
  --render-text-segmented OUT TEXT_FILE --speech-request-id ID \\
      [--voice V] [--rate R]  -> the REAL engine fallback orchestration

The segmented path imports the WORKTREE lib/segmented_render.py and runs
its main() unmodified — the way the production host drives the engine
(real split_sentence_groups, one real cli.synthesize per group, atomic
per-segment manifest publishes, exit codes 0/3/1). The ONLY faked layer is
the provider boundary: agent_tts.fallback.get_provider is patched at
import time (the same seam the engine's own tests use) to return the two
deterministic fakes below. Fakes emit REAL MP3 bytes per segment and
script their behavior through env:

  M4_PRIMARY_OK_SUBMITS   primary succeeds while its total submit count
                          is <= N (default 1); every later submit fails
  M4_PRIMARY_FAIL_MODE    retryable (URLError: in-link retry + real
                          backoff) | skip (HTTP 403: advance now) |
                          empty (b"": retryable empty result)
  M4_SECONDARY_HOLD_UNTIL path of a gate file: while absent, every
                          secondary submit BLOCKS inside the fake (the
                          mid-fallback cancel window); bounded by
                          M4_SECONDARY_HOLD_S so a lost test cannot hang
  M4_AUDIO                file whose whole bytes become every segment
  M4_SUBMIT_LOG           jsonl, one record per provider submit
                          ({provider, text, ts}) — the submit counters
  M4_WALK_LOG             jsonl, the REAL walker's attempt records
                          ({provider, outcome, ts, text, walk_ok})
  M4_ENGINE_SRC           worktree engine/src for sys.path
  M4_HOST_LIB             worktree host lib dir for sys.path
"""
import hashlib
import json
import os
import sys
import time
import urllib.error

PRIMARY = "edge"
SECONDARY = "m4secondary"


def _append_jsonl(path, payload):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\\n")
        fh.flush()
        os.fsync(fh.fileno())


def _text_id(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


class FakeProvider:
    """Deterministic provider double: real MP3 bytes, scripted failures."""

    name = "m4-double"
    supports_stream = False

    def __init__(self, provider_name):
        self.provider_name = provider_name
        self.submits = 0

    async def synthesize(self, text, voice, rate,
                         volume="+0%", pitch="+0Hz", stop_checker=None):
        self.submits += 1
        _append_jsonl(os.environ.get("M4_SUBMIT_LOG", ""), {
            "kind": "submit",
            "provider": self.provider_name,
            "text": _text_id(text),
            "ts": time.time(),
        })
        if self.provider_name == PRIMARY and self.submits > int(
                os.environ.get("M4_PRIMARY_OK_SUBMITS", "1")):
            mode = os.environ.get("M4_PRIMARY_FAIL_MODE", "retryable")
            if mode == "skip":
                raise urllib.error.HTTPError(
                    "m4://primary", 403, "forbidden (m4 double)", None, None)
            if mode == "empty":
                return b""
            raise urllib.error.URLError(
                "connection reset mid-render (m4 double)")
        if self.provider_name == SECONDARY:
            gate = os.environ.get("M4_SECONDARY_HOLD_UNTIL", "")
            if gate:
                deadline = time.monotonic() + float(
                    os.environ.get("M4_SECONDARY_HOLD_S", "25"))
                while not os.path.exists(gate) and time.monotonic() < deadline:
                    time.sleep(0.02)
        with open(os.environ["M4_AUDIO"], "rb") as fh:
            return fh.read()


def _install_engine_seams():
    """sys.path for engine + host lib, then patch ONLY the provider layer."""
    sys.path.insert(0, os.environ["M4_ENGINE_SRC"])
    sys.path.insert(0, os.environ["M4_HOST_LIB"])

    import agent_tts.fallback as fb

    registry = {
        PRIMARY: FakeProvider(PRIMARY),
        SECONDARY: FakeProvider(SECONDARY),
    }

    def factory(**kwargs):
        # _build_link_engine passes the full provider option set; the
        # double ignores everything but the name.
        return registry[kwargs["provider_name"]]

    fb.get_provider = factory

    walk_log = os.environ.get("M4_WALK_LOG", "")
    real_walk = fb.run_fallback_synthesis

    async def evidence_walk(*args, **kwargs):
        # Evidence wrapper: append the REAL walker's attempt records after
        # each walk completes (a killed mid-walk process honestly leaves
        # no terminal record — the submit log carries the counters then).
        result = await real_walk(*args, **kwargs)
        intent = args[1] if len(args) > 1 else kwargs.get("intent")
        for record in result.attempts:
            _append_jsonl(walk_log, {
                "kind": "attempt",
                "provider": record.provider,
                "outcome": record.outcome,
                "ts": record.ts,
                "text": _text_id(intent.text),
                "walk_ok": result.ok,
            })
        return result

    fb.run_fallback_synthesis = evidence_walk


def main():
    argv = sys.argv[1:]
    # Surface probes answer WITHOUT importing the engine: instant, and a
    # broken engine can never break the contract handshake.
    if argv[:1] == ["--contract-version"]:
        print(1)
        return 0
    if argv[:1] == ["--contract-capabilities"]:
        print(json.dumps({"supported_protocols": [1, 2]}))
        return 0
    if argv[:1] == ["--render-text"]:
        with open(os.environ["M4_AUDIO"], "rb") as src, \\
                open(argv[1], "wb") as dst:
            dst.write(src.read())
        return 0
    if argv[:1] == ["--render-text-segmented"]:
        _install_engine_seams()
        import segmented_render
        # segmented_render.main re-parses the argv WITHOUT the surface flag
        # (out_dir input_text_file --speech-request-id ... --voice --rate).
        return segmented_render.main(argv[1:])
    sys.stderr.write("unknown surface call: %r\\n" % (argv,))
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Segment audio: real decodable MP3 per segment (~0.45s), reusing M2's
# frame-aligned trimmer when importable; the committed sample.mp3 (~1.9s)
# is the whole-file fallback. Either way Chromium decodes real bytes.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def m4_segment_audio() -> Path:
    try:
        from tests.e2e.test_m2_stream import SHORT_TARGET_S, _mp3_layer3_frames

        sample = FIXTURES_DIR / "sample.mp3"
        data = sample.read_bytes()
        frames = _mp3_layer3_frames(data)
        assert frames, "sample.mp3 frame walk found no frames"
        frame_dur = frames[0][2] / frames[0][3]
        keep = frames[: max(1, round(SHORT_TARGET_S / frame_dur))]
        out = FIXTURES_DIR / "short.mp3"
        out.write_bytes(data[: keep[-1][0] + keep[-1][1]])
        duration = sum(f[2] for f in keep) / keep[0][3]
        assert 0.25 <= duration <= 0.55, duration
        return out
    except Exception:  # noqa: BLE001 — the committed fallback must hold
        return FIXTURES_DIR / "sample.mp3"


# ---------------------------------------------------------------------------
# Fixture: brain_m4 — the real app with the PRODUCTION segmented speech
# dispatch (tts_renderer=None) over the fallback-capable double host.
# ---------------------------------------------------------------------------


@pytest.fixture
def brain_m4(tmp_path, monkeypatch):
    """Real app over HTTP, production speech dispatch, double host binary.

    Same shape as the M2 ``brain_v2`` fixture (FakeLLM, un-started watcher
    with a live SSE hub, uvicorn on an ephemeral port) except tts_bin is
    the fallback-capable double: identified /ask turns ride the REAL
    SegmentedSpeechProducer -> double host -> REAL engine fallback walk.
    The AGENT_TTS socket/lock/pid env defaults are pointed into tmp_path
    so the engine's import-time signal wiring can never touch this
    machine's real herdr-tts daemon state.
    """
    herdr_brain_tts.reset_capabilities_cache()

    host_dir = tmp_path / "host"
    host_dir.mkdir()
    host_bin = host_dir / "herdr-tts-m4"
    host_bin.write_text(
        _DOUBLE_HOST_SOURCE.replace("__ENGINE_PYTHON__", str(ENGINE_PY))
    )
    host_bin.chmod(0o755)

    fallback_config = tmp_path / "fallback.json"
    fallback_config.write_text(
        json.dumps(
            {
                "fallback_enabled": True,
                "chain": [
                    {
                        "provider": SECONDARY_PROVIDER,
                        "voice": "m4-voice-secondary",
                        "notes": "M4 e2e deterministic secondary (double)",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    submit_log = tmp_path / "m4-submits.jsonl"
    walk_log = tmp_path / "m4-walks.jsonl"
    monkeypatch.setenv("AGENT_TTS_FALLBACK_CONFIG", str(fallback_config))
    monkeypatch.setenv("M4_ENGINE_SRC", str(ENGINE_SRC))
    monkeypatch.setenv("M4_HOST_LIB", str(HOST_LIB_DIR))
    monkeypatch.setenv("M4_SUBMIT_LOG", str(submit_log))
    monkeypatch.setenv("M4_WALK_LOG", str(walk_log))
    monkeypatch.setenv("AGENT_TTS_SOCKET", str(tmp_path / "player.sock"))
    monkeypatch.setenv("AGENT_TTS_LOCK_FILE", str(tmp_path / "playing.lock"))
    monkeypatch.setenv("AGENT_TTS_PID_FILE", str(tmp_path / "current.pid"))

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    cfg = Settings(**{
        **SETTINGS_KWARGS,
        "tts_bin": str(host_bin),
        "audio_dir": str(audio_dir),
        "tts_timeout_s": 30,  # real engine import + real backoff fit easily
    })
    llm = FakeLLM()
    watcher = AgentWatcher(cfg, herdr=StubHerdr(), tts_renderer=None)
    app = create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=None,
        watcher=watcher,
        daemon_probe=lambda: "up",
        sse_heartbeat_s=2,  # fast SSE teardown (M2 lesson)
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.02)
    assert server.started, "brain_m4 server never came up"

    class Handle:
        pass

    handle = Handle()
    handle.url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    handle.llm = llm
    handle.hub = watcher.hub
    handle.audio_dir = audio_dir
    handle.registry = app.state.speech_jobs
    handle.settings = cfg
    handle.submit_log = submit_log
    handle.walk_log = walk_log
    handle.fallback_config = fallback_config
    try:
        yield handle
    finally:
        herdr_brain_tts.reset_capabilities_cache()
        try:
            handle.hub.publish({"type": "transition", "text": "e2e teardown"})
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass
        server.should_exit = True
        thread.join(timeout=10)


# In-page instrumentation (M2 contract): every real media event on the
# single #player is recorded with both clocks and the decoded duration.
_PLAYER_PROBE_JS = """() => {
    if (window.__probe) return;
    const p = document.getElementById('player');
    window.__probe = {events: [], errors: []};
    const rec = (type) => () => {
        window.__probe.events.push({
            type: type,
            perf: performance.now(),
            wall: Date.now() / 1000,
            src: p.currentSrc || p.src || '',
            t: p.currentTime,
            dur: (p.duration === Infinity || isNaN(p.duration)) ? null : p.duration
        });
    };
    for (const t of ['playing', 'ended', 'loadedmetadata', 'pause', 'timeupdate']) {
        p.addEventListener(t, rec(t));
    }
    p.addEventListener('error', () => window.__probe.errors.push({
        perf: performance.now(), src: p.currentSrc || p.src || ''
    }));
}"""


@pytest.fixture
def pwa_m4(brain_m4, page):
    """The real PWA loaded against brain_m4, SSE subscribed, probe armed."""
    page.goto(brain_m4.url)
    wait_for_sse_subscriber(brain_m4)
    page.evaluate(_PLAYER_PROBE_JS)
    return page


# ---------------------------------------------------------------------------
# Shared helpers (M2 patterns, self-contained per house style)
# ---------------------------------------------------------------------------


def configure_m4(monkeypatch, audio: Path, *, primary_ok_submits: int,
                 fail_mode: str = "retryable",
                 secondary_hold_until: Path | None = None,
                 secondary_hold_s: float = 25.0):
    """Shapes the double host's failure script for one test.

    Env is read by the host subprocess at spawn time (per /ask turn), so a
    monkeypatch here configures exactly this test's renders.
    """
    monkeypatch.setenv("M4_AUDIO", str(audio))
    monkeypatch.setenv("M4_PRIMARY_OK_SUBMITS", str(primary_ok_submits))
    monkeypatch.setenv("M4_PRIMARY_FAIL_MODE", fail_mode)
    if secondary_hold_until is not None:
        monkeypatch.setenv("M4_SECONDARY_HOLD_UNTIL", str(secondary_hold_until))
        monkeypatch.setenv("M4_SECONDARY_HOLD_S", str(secondary_hold_s))
    else:
        monkeypatch.delenv("M4_SECONDARY_HOLD_UNTIL", raising=False)


def ask_stream(brain, pwa, text):
    """Asks via the REAL keyboard form; captures the PWA-minted speech id,
    its session id and the /ask response (M2 pattern)."""
    bodies = []

    def on_request(req):
        if req.method == "POST" and req.url.endswith("/ask"):
            try:
                bodies.append(json.loads(req.post_data or "{}"))
            except ValueError:
                pass

    pwa.on("request", on_request)
    try:
        with pwa.expect_response(
            lambda r: r.url.endswith("/ask"), timeout=15000
        ) as info:
            ask_via_keyboard(pwa, text)
        payload = info.value.json()
    finally:
        pwa.remove_listener("request", on_request)
    assert bodies, "the /ask request body was never captured"
    sid = bodies[-1].get("speech_request_id")
    session_id = bodies[-1].get("session_id")
    assert sid, "the PWA asked without minting a speech_request_id"
    assert session_id, "the PWA asked without a session_id"
    return sid, session_id, payload


def probe_events(pwa):
    return pwa.evaluate("() => (window.__probe ? window.__probe.events : [])")


def _events_for(pwa, needle: str, kind: str):
    return sorted(
        (e for e in probe_events(pwa) if e["type"] == kind and needle in e["src"]),
        key=lambda e: e["wall"],
    )


def ended_for(pwa, needle: str):
    return _events_for(pwa, needle, "ended")


def playing_for(pwa, needle: str):
    return _events_for(pwa, needle, "playing")


def decoded_duration(pwa, needle: str):
    """Max finite duration the media pipeline reported for this src (any
    event of its own load cycle proves real decoded bytes)."""
    values = [e["dur"] for e in probe_events(pwa)
              if needle in e["src"] and e["dur"] is not None]
    return max(values) if values else None


def seq_of(src, sid):
    m = re.search(r"/audio/sr-%s-(\d{4})\.mp3$" % re.escape(sid), src)
    return int(m.group(1)) if m else None


class NetLog:
    """Request-side network counters driven by the page's own traffic."""

    def __init__(self, page):
        self.audio_sr = []
        self.cancels = []
        page.on("request", self._on_request)

    def _on_request(self, req):
        url = req.url
        if "/audio/sr-" in url:
            self.audio_sr.append(url)
        elif url.endswith("/cancel"):
            self.cancels.append(url)


def player_state(page):
    return page.evaluate(
        """() => {
            const p = document.getElementById('player');
            return {ended: p.ended, paused: p.paused, src: p.currentSrc || p.src || ''};
        }"""
    )


def wait_job_phase(brain, sid, phase, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = brain.registry.get(sid)
        if job is not None and job.phase == phase:
            return job
        time.sleep(0.05)
    job = brain.registry.get(sid)
    now = job.phase if job is not None else "missing"
    raise AssertionError(f"speech job {sid} never reached {phase} (now: {now})")


# --- evidence readers ------------------------------------------------------


def read_jsonl(path) -> list:
    if not Path(path).exists():
        return []
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            pass  # a torn final line (killed process) is honest evidence
    return records


def _order_by_first_appearance(records):
    """{text_hash: group_index} by first appearance. Deterministic: the
    segmented host submits groups strictly in order, so first-submit order
    IS the manifest seq order."""
    order: dict = {}
    for rec in records:
        if rec["text"] not in order:
            order[rec["text"]] = len(order)
    return order


def submit_counter(records) -> Counter:
    """(group, provider) -> submits, groups indexed by first appearance."""
    order = _order_by_first_appearance(records)
    counts: Counter = Counter()
    for rec in records:
        counts[(order[rec["text"]], rec["provider"])] += 1
    return counts


def walk_sequences(records) -> dict:
    """group -> [(provider, outcome), ...] in record order (the real
    walker's attempt log per intent)."""
    order = _order_by_first_appearance(records)
    walks: dict = {}
    for rec in records:
        walks.setdefault(order[rec["text"]], []).append(
            (rec["provider"], rec["outcome"])
        )
    return walks


def wait_for_submits(brain, predicate, timeout=20.0, description=""):
    """Bounded poll of the submit counter file until predicate holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = read_jsonl(brain.submit_log)
        if predicate(records):
            return records
        time.sleep(0.05)
    raise AssertionError(
        f"submit evidence never satisfied ({description}): "
        f"{read_jsonl(brain.submit_log)}"
    )


def assert_submits_frozen(brain, window_s=3.0):
    """The counters must not move: zero provider attempts after the stop."""
    frozen = len(read_jsonl(brain.submit_log))
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        current = len(read_jsonl(brain.submit_log))
        assert current == frozen, (
            f"provider attempts continued after cancel: "
            f"{frozen} -> {current} records"
        )
        time.sleep(0.1)


# The locked walk of one FAILED group with a retryable primary failure:
# two in-link retries (link cap 2), then the chain link delivers.
EXPECTED_FAILED_GROUP_WALK = [
    (PRIMARY_PROVIDER, "retryable-fail"),
    (PRIMARY_PROVIDER, "retryable-fail"),
    (SECONDARY_PROVIDER, "ok"),
]


# ---------------------------------------------------------------------------
# Test 1 — primary dies mid-render; the phone still hears EVERYTHING once
# ---------------------------------------------------------------------------


def test_fake_providers_e2e(brain_m4, pwa_m4, m4_segment_audio, monkeypatch):
    """A 5-group answer: the primary (edge double) renders group 0, then
    fails technically on every later submit (URLError mid-render). Each
    failed group's intent walks the REAL fallback chain — in-link retry
    with the real 2 s backoff, then the chain link — and the secondary
    delivers the group. The phone must hear the COMPLETE answer without
    the beginning ever repeating: every seq ends exactly once, in exact
    order, no seq played twice, real decoded durations, and the evidence
    shows the chain walked inside the locked 4/2 budget."""
    groups = 5
    configure_m4(
        monkeypatch, m4_segment_audio,
        primary_ok_submits=1, fail_mode="retryable",
    )

    sid, _session_id, payload = ask_stream(
        brain_m4, pwa_m4, groups_text(groups)
    )
    assert payload["answer"].startswith("Respuesta a:")
    assert payload["speech"] == {"id": sid, "status": "delivering"}
    assert payload["audio_url"] is None, "segmented turns never carry a full file"
    prefix = f"/audio/sr-{sid}-"

    # Every segment drains through the real pipeline.
    pwa_m4.wait_for_function(
        """(parts) => window.__probe.events.filter(
               e => e.type === 'ended' && e.src.includes(parts[0])).length
             >= parts[1]""",
        arg=[prefix, groups],
        timeout=45000,  # 4 failed groups x 2 s real backoff + browser
    )

    ends = ended_for(pwa_m4, prefix)
    end_seqs = [seq_of(e["src"], sid) for e in ends]
    # Exact order, exactly once each — the beginning never repeats.
    assert end_seqs == list(range(groups)), (
        f"segments did not play exactly once in order: {end_seqs}"
    )
    assert len(set(end_seqs)) == groups, f"a seq played twice: {end_seqs}"

    plays = playing_for(pwa_m4, prefix)
    play_seqs = [seq_of(e["src"], sid) for e in plays]
    assert sorted(play_seqs) == list(range(groups)), play_seqs
    for seq in range(groups):
        assert play_seqs.count(seq) == 1, f"segment {seq} played twice"
        # Real audio: the media pipeline decoded actual bytes for it.
        dur = decoded_duration(pwa_m4, f"{prefix}{seq:04d}.mp3")
        assert dur is not None and dur > 0, (
            f"segment {seq}: no decoded duration evidence"
        )
        assert ends[seq]["t"] > 0, f"segment {seq}: ended at position 0"

    # Server side: the whole job completed.
    wait_job_phase(brain_m4, sid, "complete", timeout=10)

    # Chain-walk evidence: group 0 on the primary, every other group
    # walked primary-retryable x2 -> secondary ok.
    walks = walk_sequences(read_jsonl(brain_m4.walk_log))
    assert set(walks) == set(range(groups)), walks
    assert walks[0] == [(PRIMARY_PROVIDER, "ok")], walks[0]
    for group in range(1, groups):
        assert walks[group] == EXPECTED_FAILED_GROUP_WALK, (
            f"group {group} walk: {walks[group]}"
        )

    # Counter totals sane vs the locked budget 4/2 — per INTENT (each
    # group is its own cli.synthesize call with a fresh budget).
    counts = submit_counter(read_jsonl(brain_m4.submit_log))
    expected_keys = {(0, PRIMARY_PROVIDER)}
    for group in range(1, groups):
        expected_keys.add((group, PRIMARY_PROVIDER))
        expected_keys.add((group, SECONDARY_PROVIDER))
    assert set(counts) == expected_keys, counts
    assert counts[(0, PRIMARY_PROVIDER)] == 1
    for group in range(1, groups):
        assert counts[(group, PRIMARY_PROVIDER)] == FALLBACK_LINK_ATTEMPTS
        assert counts[(group, SECONDARY_PROVIDER)] == 1
        intent_total = sum(
            n for (g, _p), n in counts.items() if g == group
        )
        assert intent_total <= FALLBACK_TOTAL_ATTEMPTS, (
            f"group {group} burned {intent_total} submits"
        )

    print(f"[M4 evidence] {groups} groups: primary ok on group 0, then "
          f"{groups - 1} full fallback walks (2x retryable + secondary), "
          f"total submits={sum(counts.values())}, all within 4/2")


# ---------------------------------------------------------------------------
# Test 2 — cancel during the fallback window freezes everything
# ---------------------------------------------------------------------------


def test_cancel_during_fallback_zero_further_attempts(
    brain_m4, pwa_m4, m4_segment_audio, monkeypatch, tmp_path
):
    """Group 0 renders on the primary and plays; group 1's intent then
    fails over (primary retryable x2 with the real 2 s backoff) and the
    secondary's submit BLOCKS inside the double (gate file) — the process
    is provably mid-fallback when STOP is pressed. Expected: the cancel
    lands (200 "cancelled"), silence is immediate (player paused, no
    further /audio/sr- fetches), the submit counters FROZE at cancel time
    (zero provider attempts after the stop, polled with a deadline), and
    the job is terminal cancelled server-side."""
    gate = tmp_path / "release-secondary"
    configure_m4(
        monkeypatch, m4_segment_audio,
        primary_ok_submits=1, fail_mode="retryable",
        secondary_hold_until=gate, secondary_hold_s=25.0,
    )
    net = NetLog(pwa_m4)

    sid, session_id, payload = ask_stream(brain_m4, pwa_m4, groups_text(2))
    prefix = f"/audio/sr-{sid}-"
    assert payload["speech"] == {"id": sid, "status": "delivering"}

    # Deterministic cancel point: wait until the evidence proves the walk
    # is INSIDE the secondary's blocked submit — primary has exactly its
    # locked 3 submits (group-0 ok + group-1's two retryable failures) and
    # the secondary exactly 1 (the blocked one).
    wait_for_submits(
        brain_m4,
        lambda records: (
            sum(1 for r in records if r["provider"] == PRIMARY_PROVIDER) == 3
            and sum(1 for r in records if r["provider"] == SECONDARY_PROVIDER) == 1
        ),
        timeout=30,
        description="primary 3 submits + secondary 1 blocked submit",
    )

    # STOP while the secondary is mid-render (falling back).
    with pwa_m4.expect_response(
        lambda r: "/speech/" in r.url and r.url.endswith("/cancel"),
        timeout=10000,
    ) as cancel_info:
        pwa_m4.evaluate("document.getElementById('stop-audio').click()")
    resp = cancel_info.value
    assert resp.status == 200
    assert resp.json()["status"] == "cancelled"
    assert sid in resp.url, f"cancel hit a foreign id: {resp.url}"

    # Immediate silence: no further segment media fetches after the cancel.
    pwa_m4.wait_for_timeout(300)
    frozen_fetches = len(net.audio_sr)
    pwa_m4.wait_for_timeout(1500)
    assert len(net.audio_sr) == frozen_fetches, (
        f"/audio/sr- fetches continued after cancel: "
        f"{net.audio_sr[frozen_fetches:]}"
    )

    # The player went silent.
    state = player_state(pwa_m4)
    assert state["paused"], state
    pos_before = pwa_m4.evaluate(
        "() => document.getElementById('player').currentTime")
    plays_before = len(playing_for(pwa_m4, prefix))
    pwa_m4.wait_for_timeout(600)
    assert pwa_m4.evaluate(
        "() => document.getElementById('player').currentTime") == pos_before
    assert len(playing_for(pwa_m4, prefix)) == plays_before

    # The submit counters FROZE at cancel time: zero provider attempts
    # after the stop (bounded poll of the counter file).
    assert_submits_frozen(brain_m4, window_s=3.0)

    # Job terminal cancelled server-side; the transport agrees.
    job = wait_job_phase(brain_m4, sid, "cancelled", timeout=10)
    assert job.cancel_requested

    url = (
        f"{brain_m4.url}/speech/{sid}/next?after={max(job.ack_watermark, 0)}"
        f"&ack={max(job.ack_watermark, 0)}"
        f"&session_id={urllib.parse.quote(session_id)}"
    )
    with urllib.request.urlopen(url, timeout=10) as raw:
        assert json.loads(raw.read().decode("utf-8"))["status"] == "cancelled"

    # Group 1 never reached the phone: only seq 0 was ever fetched.
    fetched_seqs = {seq_of(u, sid) for u in net.audio_sr}
    assert fetched_seqs <= {0}, f"segments beyond 0 were fetched: {fetched_seqs}"
    print(f"[M4 evidence] cancel mid-fallback: counters frozen at "
          f"{len(read_jsonl(brain_m4.submit_log))} submits, fetches frozen at "
          f"{frozen_fetches}, job cancelled")


# ---------------------------------------------------------------------------
# Test 3 — no duplicate submissions, asserted precisely
# ---------------------------------------------------------------------------


def test_no_duplicate_submissions(brain_m4, pwa_m4, m4_segment_audio, monkeypatch):
    """A slower 4-group run (three full fallback walks with the real 2 s
    backoffs each) asserted to the submit: per intent (group) the sum of
    provider submits stays <= FALLBACK_TOTAL_ATTEMPTS (4), every submit is
    logged exactly once (the submit counter file and the walker's attempt
    log agree 1:1), and the per-(group, provider) counters match the
    expected walk EXACTLY — the primary submits each failed group's text
    exactly twice (the locked in-link retry) and the secondary exactly
    once; no (group, provider) pair is ever submitted beyond that."""
    groups = 4
    configure_m4(
        monkeypatch, m4_segment_audio,
        primary_ok_submits=1, fail_mode="retryable",
    )

    t0 = time.monotonic()
    sid, _session_id, payload = ask_stream(
        brain_m4, pwa_m4, groups_text(groups)
    )
    prefix = f"/audio/sr-{sid}-"

    pwa_m4.wait_for_function(
        """(parts) => window.__probe.events.filter(
               e => e.type === 'ended' && e.src.includes(parts[0])).length
             >= parts[1]""",
        arg=[prefix, groups],
        timeout=45000,
    )
    wait_job_phase(brain_m4, sid, "complete", timeout=10)

    end_seqs = [seq_of(e["src"], sid) for e in ended_for(pwa_m4, prefix)]
    assert end_seqs == list(range(groups)), end_seqs

    submit_records = read_jsonl(brain_m4.submit_log)
    walk_records = read_jsonl(brain_m4.walk_log)
    counts = submit_counter(submit_records)

    # Expected walk EXACTLY: group 0 once on the primary; every failed
    # group exactly 2 primary submits (locked in-link retry) + 1 secondary.
    expected = {(0, PRIMARY_PROVIDER): 1}
    for group in range(1, groups):
        expected[(group, PRIMARY_PROVIDER)] = FALLBACK_LINK_ATTEMPTS
        expected[(group, SECONDARY_PROVIDER)] = 1
    assert dict(counts) == expected, (
        f"submit counters diverged from the locked walk:\n"
        f"  got      {dict(counts)}\n  expected {expected}"
    )

    # Per intent, the shared budget holds: total submits per group <= 4.
    for group in range(groups):
        intent_total = sum(
            n for (g, _p), n in counts.items() if g == group
        )
        assert intent_total <= FALLBACK_TOTAL_ATTEMPTS, (
            f"group {group}: {intent_total} submits exceed the shared budget"
        )
    total = sum(counts.values())
    assert total == 1 + (groups - 1) * 3, total

    # Each submit is logged exactly once: the submit counter file and the
    # real walker's attempt log agree 1:1 (ok/retryable-fail records ARE
    # submits; skip/cancelled record nothing). The locked in-link retry
    # legitimately submits the same text twice — so the AGREEMENT between
    # the two independent files is the each-submit-once evidence.
    walk_submit_class = Counter(
        (rec["provider"], rec["text"])
        for rec in walk_records
        if rec["outcome"] in ("ok", "retryable-fail")
    )
    file_counts = Counter(
        (rec["provider"], rec["text"]) for rec in submit_records
    )
    assert walk_submit_class == file_counts, (
        f"attempt log vs submit file disagree:\n"
        f"  walk  {dict(walk_submit_class)}\n  file  {dict(file_counts)}"
    )

    # No (group, provider) text pair was ever submitted beyond the locked
    # retry rules: primary <= 2 per text, secondary exactly 1 per text.
    for (provider, _text), n in file_counts.items():
        cap = FALLBACK_LINK_ATTEMPTS if provider == PRIMARY_PROVIDER else 1
        assert n <= cap, f"{provider} submitted the same text {n} times"

    # The walk sequences themselves are exactly the locked shape.
    walks = walk_sequences(walk_records)
    assert walks[0] == [(PRIMARY_PROVIDER, "ok")]
    for group in range(1, groups):
        assert walks[group] == EXPECTED_FAILED_GROUP_WALK, (
            f"group {group}: {walks[group]}"
        )

    print(f"[M4 evidence] no duplicates: {total} total submits for "
          f"{groups} groups (1 ok + 3 walks x [2 primary + 1 secondary]), "
          f"all intents <= 4, wall {time.monotonic() - t0:.1f}s")
