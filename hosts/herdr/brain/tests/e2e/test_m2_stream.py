"""Milestone 2 E2E: segmented speech streaming over the real media pipeline.

Real browser against the real brain app over HTTP. No media-stack mocks: the
production SegmentedSpeechProducer path runs (``tts_renderer=None``) and spawns
a per-test protocol-2 host stub (``--render-text-segmented``) that publishes
segments progressively; the PWA's segment player long-polls ``/next`` and feeds
the same global sequential audio queue as announcements. Completion evidence is
only ever the platform's own decoded/playback events (D6/T12.4).

What the stub host is: a python3 script in tmp_path answering the CLI contract
(``--contract-version``, ``--contract-capabilities`` → protocols [1, 2]) whose
``--render-text-segmented`` behavior is env-driven (SR_TOTAL, SR_DELAY_S,
SR_HOLD_FINAL_S, SR_AUDIO, SR_TIMING_LOG) so every test shapes its own stream.
The timing log is the wall-clock latency evidence source: the stub appends
``<ts> publish <seq>`` / ``<ts> complete`` lines as it publishes.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
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


# ---------------------------------------------------------------------------
# Fixture 1: short.mp3 — frame-aligned trim of sample.mp3 (~0.41s)
# ---------------------------------------------------------------------------
#
# Approach chosen: FRAME-ALIGNED trimming. sample.mp3 walks as 78 clean
# MPEG-2 Layer III frames (144 B each, 24 kHz mono, 576 samples/frame); the
# short fixture keeps the first N whole frames so no frame is ever cut
# mid-stream (there is no ID3 tag — the file starts directly at a frame
# sync, and the trimmer handles an ID3v2 header generically anyway).
# 17 frames = 2448 bytes = 0.408s: short enough to keep a 12-segment stream
# bounded, long enough for a real decoded `ended` event per segment. If the
# trimmed decode ever proves flaky in Chromium, the documented fallback is
# re-using sample.mp3 per segment and re-adjusting SR_TOTAL/SR_DELAY_S to
# keep the runtime bounded (tests 3 and 6 already use sample.mp3 per
# segment for exactly that reason — long windows for mid-playback actions).

_MPEG1_L3_BITRATES = {1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96,
                      8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320}
_MPEG2_L3_BITRATES = {1: 8, 2: 16, 3: 24, 4: 32, 5: 40, 6: 48, 7: 56,
                      8: 64, 9: 80, 10: 96, 11: 112, 12: 128, 13: 144, 14: 160}
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000),
                 0: (11025, 12000, 8000)}  # version bits -> MPEG1 / MPEG2 / MPEG2.5

SHORT_TARGET_S = 0.45  # spec window is ~0.3-0.5s per segment


def _mp3_layer3_frames(data: bytes) -> list[tuple[int, int, int, int]]:
    """Walks whole MPEG Layer III frames: [(start, length, samples, rate)].

    Skips an ID3v2 header when present and resyncs byte-by-byte past garbage;
    only complete frames inside the buffer are returned, so trimming at the
    end of any returned frame can never cut a frame in half.
    """
    pos = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        # ID3v2 syncsafe size: 7 bits per byte, MSB clear.
        tag_size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
                   ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        pos = 10 + tag_size
    frames: list[tuple[int, int, int, int]] = []
    while pos + 4 <= len(data):
        if data[pos] == 0xFF and (data[pos + 1] & 0xE0) == 0xE0:
            version = (data[pos + 1] >> 3) & 0x3
            layer = (data[pos + 1] >> 1) & 0x3  # 1 == Layer III
            bitrate_idx = data[pos + 2] >> 4
            rate_idx = (data[pos + 2] >> 2) & 0x3
            padding = (data[pos + 2] >> 1) & 0x1
            table = _MPEG1_L3_BITRATES if version == 3 else _MPEG2_L3_BITRATES
            if (version in _SAMPLE_RATES and layer == 1
                    and bitrate_idx in table and rate_idx != 3):
                rate = _SAMPLE_RATES[version][rate_idx]
                samples = 1152 if version == 3 else 576
                coef = 144 if version == 3 else 72
                length = coef * table[bitrate_idx] * 1000 // rate + padding
                if 0 < length and pos + length <= len(data):
                    frames.append((pos, length, samples, rate))
                    pos += length
                    continue
        pos += 1  # resync: not a valid frame header here
    return frames


@pytest.fixture(scope="session")
def short_mp3() -> tuple[Path, float]:
    """Builds fixtures/short.mp3 once: first N whole frames of sample.mp3.

    Sanity is checked locally here (frame math, size, duration window) and
    re-verified indirectly in-test: Chromium must actually decode and play
    the trimmed bytes to a platform `ended` with a matching duration
    (test_decoded_playback_events_distinguished).
    """
    sample_path = FIXTURES_DIR / "sample.mp3"
    data = sample_path.read_bytes()
    frames = _mp3_layer3_frames(data)
    assert len(frames) >= 20, f"sample.mp3 frame walk came up short: {len(frames)}"
    frame_dur = frames[0][2] / frames[0][3]
    count = max(1, round(SHORT_TARGET_S / frame_dur))
    keep = frames[:count]
    duration = sum(f[2] for f in keep) / keep[0][3]
    assert 0.25 <= duration <= 0.55, f"trimmed duration out of spec: {duration:.3f}s"
    end = keep[-1][0] + keep[-1][1]
    assert end < len(data), "trim consumed the whole sample"
    out = FIXTURES_DIR / "short.mp3"
    out.write_bytes(data[:end])
    # Re-walk the written bytes: the file must end exactly on a frame boundary.
    rewritten = _mp3_layer3_frames(out.read_bytes())
    assert len(rewritten) == count, "short.mp3 is not a whole number of frames"
    assert rewritten[-1][0] + rewritten[-1][1] == out.stat().st_size
    return out, duration


# ---------------------------------------------------------------------------
# Fixture 2: the protocol-2 host stub (env-driven progressive publisher)
# ---------------------------------------------------------------------------

_STUB_HOST_SOURCE = '''#!/usr/bin/env python3
"""Protocol-2 segmented TTS host stub (M2 E2E). Env-driven:

  SR_TOTAL        segments to publish
  SR_DELAY_S      seconds between segment publishes
  SR_HOLD_FINAL_S sleep before publishing the terminal (is_complete) manifest
  SR_TIMING_LOG   append "<ts> publish <seq>" / "<ts> complete" evidence lines
  SR_AUDIO        path whose bytes become every seg-%04d.mp3
"""
import json
import os
import sys
import time


def _log(event):
    path = os.environ.get("SR_TIMING_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("%.6f %s\\n" % (time.time(), event))


def _publish(out_dir, segments, revision, complete=False):
    manifest = {"revision": revision, "segments": list(segments)}
    if complete:
        manifest["is_complete"] = True
    tmp = os.path.join(out_dir, "manifest.json.tmp")
    dst = os.path.join(out_dir, "manifest.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    os.replace(tmp, dst)  # atomic re-publish


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--contract-version"]:
        print(1)
        return 0
    if argv[:1] == ["--contract-capabilities"]:
        print(json.dumps({"supported_protocols": [1, 2]}))
        return 0
    if argv[:1] == ["--render-text"]:
        # v1 fallback surface (unused on the segmented path): whole file.
        with open(os.environ["SR_AUDIO"], "rb") as src, open(argv[1], "wb") as dst:
            dst.write(src.read())
        return 0
    if argv[:1] == ["--render-text-segmented"]:
        out_dir = argv[1]
        total = int(os.environ.get("SR_TOTAL", "4"))
        delay = float(os.environ.get("SR_DELAY_S", "0.5"))
        hold = float(os.environ.get("SR_HOLD_FINAL_S", "0"))
        with open(os.environ["SR_AUDIO"], "rb") as fh:
            payload = fh.read()
        segments = []
        for seq in range(total):
            name = "seg-%04d.mp3" % seq
            with open(os.path.join(out_dir, name), "wb") as fh:
                fh.write(payload)
            segments.append({"seq": seq, "file": name, "bytes": len(payload)})
            _publish(out_dir, segments, revision=seq + 1)
            _log("publish %d" % seq)
            if seq != total - 1:
                time.sleep(delay)
        if hold > 0:
            time.sleep(hold)
        _publish(out_dir, segments, revision=total + 1, complete=True)
        _log("complete")
        return 0
    sys.stderr.write("unknown surface call: %r\\n" % (argv,))
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# Fixture 3: brain_v2 — production speech path over the stub host
# ---------------------------------------------------------------------------


@pytest.fixture
def brain_v2(tmp_path, monkeypatch):
    """The real app over HTTP with the PRODUCTION speech dispatch: no TTS
    renderer double (``tts_renderer=None``), so identified /ask turns ride
    SegmentedSpeechProducer against the protocol-2 stub above. FakeLLM and
    an un-started watcher with a live SSE hub, exactly like the M1 ``brain``
    fixture otherwise. The capabilities probe cache is reset on setup AND
    teardown so no test can inherit another stub's protocol verdict.
    """
    herdr_brain_tts.reset_capabilities_cache()
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    stub = host_dir / "herdr-tts-v2"
    stub.write_text(_STUB_HOST_SOURCE)
    stub.chmod(0o755)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    timing_log = tmp_path / "stub-timing.log"
    monkeypatch.setenv("SR_TIMING_LOG", str(timing_log))

    cfg = Settings(**{
        **SETTINGS_KWARGS,
        "tts_bin": str(stub),
        "audio_dir": str(audio_dir),
        "tts_timeout_s": 30,  # slow-pacing stubs legitimately outlive 10s
    })
    llm = FakeLLM()
    watcher = AgentWatcher(cfg, herdr=StubHerdr(), tts_renderer=None)
    app = create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=None,
        watcher=watcher,
        daemon_probe=lambda: "up",
        # Short SSE heartbeats: a dead page leaves each /events generator
        # blocked in a queue.get(heartbeat) inside the default executor,
        # and the loop cannot close until that thread returns — 2s keeps
        # teardowns fast without changing any observable behavior.
        sse_heartbeat_s=2,
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
    assert server.started, "brain_v2 server never came up"

    class Handle:
        pass

    handle = Handle()
    handle.url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    handle.llm = llm
    handle.hub = watcher.hub
    handle.audio_dir = audio_dir
    handle.timing_log = timing_log
    handle.registry = app.state.speech_jobs
    handle.settings = cfg
    try:
        yield handle
    finally:
        herdr_brain_tts.reset_capabilities_cache()
        # Wake the SSE generators before exiting: each blocks inside a
        # queue.get(heartbeat=15s) executor thread, and closing the page
        # socket cannot interrupt that — one final publish makes every
        # generator yield (failing on the dead transport), so uvicorn
        # drains immediately instead of parking the join on its timeout.
        try:
            handle.hub.publish({"type": "transition", "text": "e2e teardown"})
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass
        server.should_exit = True
        thread.join(timeout=10)


# In-page instrumentation: every 'playing'/'ended' on the single #player
# element is recorded with BOTH clocks — performance.now() (monotonic page
# clock, comparable with Resource Timing) and Date.now()/1000 (wall clock,
# comparable with the stub host's time.time() log on this same machine).
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
    // app.js registers its own 'ended' handler at page load, BEFORE this
    // probe: on a queued stream that handler synchronously pumps the next
    // item and resets the element, so a payload read inside a late 'ended'
    // listener is already wiped. Duration/position evidence is therefore
    // captured from the SAME load cycle's metadata/pause/timeupdate events.
    for (const t of ['playing', 'ended', 'loadedmetadata', 'pause', 'timeupdate']) {
        p.addEventListener(t, rec(t));
    }
    p.addEventListener('error', () => window.__probe.errors.push({
        perf: performance.now(), src: p.currentSrc || ''
    }));
}"""


@pytest.fixture
def pwa_v2(brain_v2, page):
    """The real PWA loaded against brain_v2, SSE subscribed, probe armed."""
    page.goto(brain_v2.url)
    wait_for_sse_subscriber(brain_v2)
    page.evaluate(_PLAYER_PROBE_JS)
    return page


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def configure_stub(monkeypatch, audio, total, delay, hold=0.0):
    """Shapes the stub host's progressive publish schedule for one test."""
    monkeypatch.setenv("SR_AUDIO", str(audio))
    monkeypatch.setenv("SR_TOTAL", str(total))
    monkeypatch.setenv("SR_DELAY_S", str(delay))
    monkeypatch.setenv("SR_HOLD_FINAL_S", str(hold))


def ask_stream(brain, pwa, text):
    """Asks via the REAL keyboard form; captures the /ask request body (the
    PWA-minted speech id + its random session id) and the /ask response."""
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


def ended_for(pwa, needle):
    """Ended events whose src contains `needle`, in wall-clock order."""
    events = [e for e in probe_events(pwa)
              if e["type"] == "ended" and needle in e["src"]]
    return sorted(events, key=lambda e: e["wall"])


def playing_for(pwa, needle):
    events = [e for e in probe_events(pwa)
              if e["type"] == "playing" and needle in e["src"]]
    return sorted(events, key=lambda e: e["wall"])


def decoded_duration(pwa, needle):
    """The decoded duration the media pipeline reported for this src: the
    metadata events of its own load cycle (max over samples, any of which
    proves a finite decoded duration)."""
    values = [e["dur"] for e in probe_events(pwa)
              if needle in e["src"] and e["dur"] is not None]
    assert values, f"no decoded duration sample for {needle}"
    return max(values)


def playback_position(pwa, needle):
    """Furthest currentTime the platform reported for this src."""
    values = [e["t"] for e in probe_events(pwa) if needle in e["src"]]
    return max(values) if values else -1.0


def seq_of(src, sid):
    m = re.search(r"/audio/sr-%s-(\d{4})\.mp3$" % re.escape(sid), src)
    return int(m.group(1)) if m else None


def audio_resources(pwa, needle):
    """Resource Timing entries for media fetches matching `needle`, on the
    page's own monotonic clock (performance.now) — the same clock the probe
    records 'playing' on, so fetch-vs-play ordering is causal, not
    cross-process jitter."""
    return pwa.evaluate(
        """(needle) => performance.getEntriesByType('resource')
             .filter(e => e.name.indexOf(needle) !== -1)
             .map(e => ({name: e.name, start: e.startTime, end: e.responseEnd}))""",
        needle,
    )


def timing_events(brain):
    """Parsed stub timing log: [('publish', seq, ts) | ('complete', None, ts)]."""
    out = []
    if brain.timing_log.exists():
        for line in brain.timing_log.read_text().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] == "publish":
                out.append(("publish", int(parts[2]), float(parts[0])))
            elif len(parts) == 2 and parts[1] == "complete":
                out.append(("complete", None, float(parts[0])))
    return out


def wait_for_timing(brain, kind, seq=None, timeout=25.0):
    """Bounded wait until the stub logged the given event; returns its ts."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event, event_seq, ts in timing_events(brain):
            if event == kind and (seq is None or event_seq == seq):
                return ts
        time.sleep(0.05)
    raise AssertionError(f"stub host never logged {kind} seq={seq}")


def next_status(brain, sid, session_id, after, ack, timeout=15):
    """Direct GET /speech/{id}/next with the given watermarks."""
    url = (
        f"{brain.url}/speech/{sid}/next?after={after}&ack={ack}"
        f"&session_id={urllib.parse.quote(session_id)}"
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


class NetLog:
    """Request-side network counters driven by the page's own traffic."""

    def __init__(self, page):
        self.audio_sr = []   # media fetches of streamed segments
        self.cancels = []    # POST /speech/{id}/cancel
        self.nexts = []      # GET /speech/{id}/next
        page.on("request", self._on_request)

    def _on_request(self, req):
        url = req.url
        if "/audio/sr-" in url:
            self.audio_sr.append(url)
        elif url.endswith("/cancel"):
            self.cancels.append(url)
        elif "/speech/" in url and "/next" in url:
            self.nexts.append(url)

    def next_marks(self):
        """[(after, ack)] for every /next, in request order."""
        marks = []
        for url in self.nexts:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            marks.append((int(query["after"][0]), int(query["ack"][0])))
        return marks


def player_state(page):
    return page.evaluate(
        """() => {
            const p = document.getElementById('player');
            return {ended: p.ended, paused: p.paused, src: p.currentSrc || p.src || ''};
        }"""
    )


# ---------------------------------------------------------------------------
# Test 1 — first segment plays before the terminal manifest exists
# ---------------------------------------------------------------------------


def test_first_segment_plays_before_total(brain_v2, pwa_v2, short_mp3, monkeypatch):
    """Streaming is REAL: with slow pacing (1.5s between publishes and a held
    terminal manifest) the PWA is audibly into segment 0 (real playback of
    /audio/sr-...-0000) strictly BEFORE the stub even publishes the terminal
    manifest — the whole point of protocol 2. Latency deltas are printed for
    the milestone evidence log (visible under `pytest -s`)."""
    short_path, _duration = short_mp3
    configure_stub(monkeypatch, short_path, total=4, delay=1.2, hold=0.8)

    t_ask = time.time()
    sid, _session_id, payload = ask_stream(
        brain_v2, pwa_v2, "transmision por segmentos"
    )
    # The /ask response itself must announce the async segmented dispatch.
    assert payload["answer"].startswith("Respuesta a:")
    assert payload["speech"] == {"id": sid, "status": "delivering"}
    assert payload["audio_url"] is None, "segmented turns never carry a full file"
    assert pwa_v2.get_by_text(payload["answer"]).count() >= 1

    # Real playback of segment 0: the player is unpaused on an sr- URL...
    pwa_v2.wait_for_function(
        """() => {
            const p = document.getElementById('player');
            return !p.paused && (p.currentSrc || p.src || '').includes('/audio/sr-');
        }""",
        timeout=10000,
    )
    # ...and the platform actually fired 'playing' for it (waiting on the
    # player state alone would race play() -> playing by a few tens of ms).
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.some(e => e.type === 'playing' "
        "&& e.src.includes(prefix))",
        arg=f"/audio/sr-{sid}-",
        timeout=10000,
    )
    plays = playing_for(pwa_v2, f"/audio/sr-{sid}-")
    first_play_wall = plays[0]["wall"]

    t_terminal = wait_for_timing(brain_v2, "complete", timeout=20)
    assert first_play_wall < t_terminal, (
        f"segment 0 was only played at {first_play_wall:.3f}, after the "
        f"terminal manifest publish at {t_terminal:.3f} — not streaming"
    )
    print(
        f"[M2 evidence] first-segment latency: play@+{first_play_wall - t_ask:.3f}s "
        f"ask->terminal: +{t_terminal - t_ask:.3f}s "
        f"(stream head start {t_terminal - first_play_wall:.3f}s)"
    )

    # The whole stream still drains through the real pipeline afterwards.
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 4",
        arg=f"/audio/sr-{sid}-",
        timeout=15000,
    )


# ---------------------------------------------------------------------------
# Test 2 — decoded/playback events are the automated observable (D6)
# ---------------------------------------------------------------------------


def test_decoded_playback_events_distinguished(brain_v2, pwa_v2, short_mp3, monkeypatch):
    """Every segment must show REAL decode/playback evidence, not just a
    network fetch: a platform 'playing' event, an 'ended' with currentTime
    > 0 and duration matching the trimmed fixture (±0.2s), and — the
    contrast — the segment's media fetch COMPLETED strictly before playback
    started (Resource Timing responseEnd < playing, same page clock).

    Scope note: decoded/playback events are the automated observable.
    PHYSICAL audibility (speakers actually emitting sound a human hears)
    remains the manual checklist item §8 — automation cannot hear."""
    short_path, short_dur = short_mp3
    configure_stub(monkeypatch, short_path, total=3, delay=0.25)
    sid, _session_id, _payload = ask_stream(brain_v2, pwa_v2, "eventos decodificados")

    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 3",
        arg=f"/audio/sr-{sid}-",
        timeout=15000,
    )
    for seq in range(3):
        needle = f"/audio/sr-{sid}-{seq:04d}.mp3"
        plays = playing_for(pwa_v2, needle)
        ends = ended_for(pwa_v2, needle)
        assert plays, f"segment {seq}: no real 'playing' event"
        assert ends, f"segment {seq}: no real 'ended' event"
        decoded = decoded_duration(pwa_v2, needle)
        assert abs(decoded - short_dur) <= 0.2, (
            f"segment {seq}: decoded duration {decoded:.3f}s "
            f"vs fixture {short_dur:.3f}s"
        )
        assert playback_position(pwa_v2, needle) > 0, (
            f"segment {seq}: currentTime did not advance"
        )
        # Contrast: fetch completed strictly BEFORE playback of this segment.
        resources = audio_resources(pwa_v2, needle)
        assert resources, f"segment {seq}: no resource-timing entry for the media fetch"
        assert min(r["end"] for r in resources) < plays[0]["perf"], (
            f"segment {seq}: media fetch did not complete before 'playing'"
        )


# ---------------------------------------------------------------------------
# Test 3 — transient offline: retry with watermarks, no dup/no cancel replay
# ---------------------------------------------------------------------------


def test_reconnect_no_duplicates_no_cancel_replay(brain_v2, pwa_v2, monkeypatch):
    """A transient network blip mid-stream must be invisible in the output:
    the in-flight /next fetch errors while the context is offline, the
    player retries with BOTH watermarks preserved (they never regress), and
    every segment still plays exactly once, in order, to full completion —
    with NO /speech/{id}/cancel ever issued (a connection-level abort is
    not a cancel by contract).

    Scheduling: sample.mp3 per segment (1.87s each) with 3.5s between
    publishes makes the test deterministic — after the second 'ended' the
    queue is provably EMPTY (segment 2 not published yet) and exactly one
    /next is held in flight, so the offline window can never collide with a
    media-element src swap (which would consume a segment without playing
    it through the media-error path)."""
    sample = FIXTURES_DIR / "sample.mp3"
    configure_stub(monkeypatch, sample, total=4, delay=3.5)
    net = NetLog(pwa_v2)

    sid, session_id, _payload = ask_stream(brain_v2, pwa_v2, "reconexion limpia")
    prefix = f"/audio/sr-{sid}-"

    # Mid-stream: seq 0 and 1 fully played (two real 'ended' events).
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 2",
        arg=prefix,
        timeout=20000,
    )
    # Settle into the inter-segment gap: queue empty, one held /next.
    pwa_v2.wait_for_timeout(200)
    context = pwa_v2.context
    context.set_offline(True)
    pwa_v2.wait_for_timeout(400)  # the in-flight /next fetch errors here
    context.set_offline(False)

    # Full playback resumes and completes: all four segments, exactly once.
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 4",
        arg=prefix,
        timeout=30000,
    )
    ends = ended_for(pwa_v2, prefix)
    assert [seq_of(e["src"], sid) for e in ends] == [0, 1, 2, 3], (
        f"segments did not play exactly once in order: {ends}"
    )

    # No cancel was EVER issued — offline is not consent to cancel.
    assert net.cancels == [], f"unexpected cancel POSTs: {net.cancels}"

    # The retried polls kept both watermarks: monotone, never reset, and the
    # final poll delivers the last ack (after=3, ack=3).
    deadline = time.monotonic() + 15
    marks = net.next_marks()
    while time.monotonic() < deadline and (not marks or marks[-1] != (3, 3)):
        marks = net.next_marks()
        time.sleep(0.05)
    assert marks[-1] == (3, 3), f"final ack poll never happened: {marks[-8:]}"
    afters = [m[0] for m in marks]
    acks = [m[1] for m in marks]
    assert afters == sorted(afters), f"'after' watermark regressed: {marks}"
    assert acks == sorted(acks), f"'ack' watermark regressed: {marks}"

    # Server side agrees: the job completes with the played watermarks.
    status = next_status(brain_v2, sid, session_id, after=3, ack=3)
    assert status.get("wait") is True and status.get("status") == "complete", status


# ---------------------------------------------------------------------------
# Test 4 — 12 segments against the 8-slot buffer: no degradation
# ---------------------------------------------------------------------------


def test_gt8_segments_first_before_final_e2e(brain_v2, pwa_v2, short_mp3, monkeypatch):
    """More segments than the server's SPEECH_SEGMENT_BUFFER (8): the first
    segment plays before the LAST segment is even published, all 12 play in
    order exactly once, and the job reaches complete server-side — proving
    the producer's backpressure was released by real playback acks (a job
    that degraded could never later be complete: terminal phases land
    exactly once in the registry)."""
    short_path, _duration = short_mp3
    configure_stub(monkeypatch, short_path, total=12, delay=0.3)
    net = NetLog(pwa_v2)

    sid, session_id, _payload = ask_stream(brain_v2, pwa_v2, "docena de segmentos")
    prefix = f"/audio/sr-{sid}-"

    # First segment is really playing before the last publish happens.
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'playing' "
        "&& e.src.includes(prefix)).length >= 1",
        arg=prefix,
        timeout=10000,
    )
    first_play_wall = playing_for(pwa_v2, prefix)[0]["wall"]
    t_last_publish = wait_for_timing(brain_v2, "publish", seq=11, timeout=20)
    assert first_play_wall < t_last_publish, (
        f"first play {first_play_wall:.3f} was not before last publish "
        f"{t_last_publish:.3f}"
    )

    # All twelve play, exactly once, in order.
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 12",
        arg=prefix,
        timeout=30000,
    )
    ends = ended_for(pwa_v2, prefix)
    assert [seq_of(e["src"], sid) for e in ends] == list(range(12)), (
        "the 12 segments did not play exactly once in order"
    )

    # The client's final ack flush reaches the server job.
    deadline = time.monotonic() + 15
    marks = net.next_marks()
    while time.monotonic() < deadline and (not marks or marks[-1] != (11, 11)):
        marks = net.next_marks()
        time.sleep(0.05)
    assert marks[-1] == (11, 11), f"final ack poll never happened: {marks[-8:]}"

    job = wait_job_phase(brain_v2, sid, "complete", timeout=10)
    assert job.ack_watermark == 11, job.ack_watermark
    # Backpressure evidence: the 8-slot staging buffer never exceeded its
    # bound while 12 segments flowed through it.
    assert job.segments is not None and job.segments.peak <= 8, (
        f"staging buffer peak {job.segments.peak} exceeded the cap"
    )
    status = next_status(brain_v2, sid, session_id, after=11, ack=11)
    assert status.get("wait") is True and status.get("status") == "complete", status


# ---------------------------------------------------------------------------
# Test 5 — foreign announcement interleaves with a live stream (PRD02 §4)
# ---------------------------------------------------------------------------


def test_foreign_announcement_interleaves_without_dup_or_loss(brain_v2, pwa_v2, short_mp3, monkeypatch):
    """A foreign SSE announcement (type "transition" with audio_url) landing
    mid-stream plays through the SAME global sequential queue: it is not
    lost and it does not disturb the job — every segment still plays
    exactly once in order, with the announcement's 'ended' landing strictly
    BETWEEN two consecutive segment endings (true interleave, not before
    the first nor after the last)."""
    short_path, _duration = short_mp3
    configure_stub(monkeypatch, short_path, total=5, delay=0.8)

    sid, _session_id, _payload = ask_stream(brain_v2, pwa_v2, "anuncio en medio")
    prefix = f"/audio/sr-{sid}-"

    # Wait until the stream is really under way (segment 0 fully played)…
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 1",
        arg=prefix,
        timeout=10000,
    )
    # …then a foreign announcement arrives over SSE.
    ann_name = "ann-mid2.mp3"
    shutil.copyfile(short_path, brain_v2.audio_dir / ann_name)
    brain_v2.hub.publish({
        "type": "transition",
        "pane_id": "w1:p7",
        "agent": "opencode",
        "status": "done",
        "label": "opencode repo7",
        "text": "otro agente termino su trabajo",
        "audio_url": f"/audio/{ann_name}",
        "speech_request_id": "ann-mid2-00000001",
    })

    # Everything plays: five segments plus the announcement.
    pwa_v2.wait_for_function(
        """(parts) => window.__probe.events.filter(e => e.type === 'ended' &&
            (e.src.includes(parts[0]) || e.src.includes(parts[1]))).length >= 6""",
        arg=[prefix, ann_name],
        timeout=20000,
    )
    sr_ends = ended_for(pwa_v2, prefix)
    ann_ends = ended_for(pwa_v2, ann_name)
    assert [seq_of(e["src"], sid) for e in sr_ends] == list(range(5)), (
        "the stream's segments did not survive the interleave exactly once in order"
    )
    assert len(ann_ends) == 1, f"announcement played {len(ann_ends)} times"

    # Interleave evidence in the merged play-out order on the single player.
    merged = sorted(sr_ends + ann_ends, key=lambda e: e["wall"])
    ann_index = next(i for i, e in enumerate(merged) if ann_name in e["src"])
    assert 0 < ann_index < len(merged) - 1, (
        f"announcement did not interleave mid-stream: position {ann_index} "
        f"of {len(merged)}"
    )


# ---------------------------------------------------------------------------
# Test 6 — stop button mid-stream cancels the server-side job
# ---------------------------------------------------------------------------


def test_cancel_midstream_stops_segments(brain_v2, pwa_v2, monkeypatch):
    """Stopping while segment 0 is AUDIBLY playing (sample.mp3 gives a wide
    window) cancels exactly this job server-side: /speech/{id}/cancel
    answers 200 "cancelled", no further /audio/sr- fetches happen after the
    cancel, the player goes silent, and the textual answer stays intact.

    sample.mp3 per segment keeps the stop click safely INSIDE the first
    segment's playback (1.87s) — before any 'ended' event."""
    sample = FIXTURES_DIR / "sample.mp3"
    configure_stub(monkeypatch, sample, total=4, delay=1.2)
    net = NetLog(pwa_v2)

    sid, session_id, payload = ask_stream(brain_v2, pwa_v2, "cancela en mitad")
    prefix = f"/audio/sr-{sid}-"

    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'playing' "
        "&& e.src.includes(prefix)).length >= 1",
        arg=prefix,
        timeout=10000,
    )
    with pwa_v2.expect_response(
        lambda r: "/speech/" in r.url and r.url.endswith("/cancel"),
        timeout=10000,
    ) as cancel_info:
        pwa_v2.evaluate("document.getElementById('stop-audio').click()")
    resp = cancel_info.value
    assert resp.status == 200
    assert resp.json()["status"] == "cancelled"

    # No further segment media fetches after the cancel (bounded freeze).
    pwa_v2.wait_for_timeout(300)
    frozen = len(net.audio_sr)
    pwa_v2.wait_for_timeout(1500)
    assert len(net.audio_sr) == frozen, (
        f"/audio/sr- fetches continued after cancel: {net.audio_sr[frozen:]}"
    )

    # Player silent: paused with a frozen playback position (stopAudio
    # strips the element's src attribute, though currentSrc may linger on
    # the last resource) and no new 'playing' after the cancel.
    state = player_state(pwa_v2)
    assert state["paused"], state
    pos_before = pwa_v2.evaluate(
        "() => document.getElementById('player').currentTime")
    plays_before = len(playing_for(pwa_v2, prefix))
    pwa_v2.wait_for_timeout(600)
    assert pwa_v2.evaluate("() => document.getElementById('player').currentTime") == pos_before
    assert len(playing_for(pwa_v2, prefix)) == plays_before

    # The textual answer was never touched by the speech cancel.
    assert pwa_v2.get_by_text(payload["answer"]).count() >= 1

    # Server side: terminal cancelled, and the transport agrees.
    job = wait_job_phase(brain_v2, sid, "cancelled", timeout=10)
    status = next_status(
        brain_v2, sid, session_id,
        after=max(job.ack_watermark, 0), ack=max(job.ack_watermark, 0),
    )
    assert status.get("status") == "cancelled", status


# ---------------------------------------------------------------------------
# Test 7 — stop AFTER a segment ended still cancels (VS2 remediation, defect 1)
# ---------------------------------------------------------------------------


def test_stop_after_segment_ended_still_cancels(brain_v2, pwa_v2, short_mp3, monkeypatch):
    """The speech identity must outlive segment 'ended' events (PRD01
    FR-02/T2/T6, PRD02 FR-04): the old wiring released the controller after
    EVERY ended queue item, so pressing stop once segment 0 had played out
    found no active identity — zero /speech/{id}/cancel POSTs (the server
    rendered everything anyway) and the legacy clear-all wiped foreign
    announcements from the queue.

    Here segment 0 is fully 'ended' (>=1 segment played out), segment 1 is
    audibly playing (sample.mp3 gives a wide window), and a foreign SSE
    announcement is PENDING in the queue. Pressing stop must: POST the
    cancel (200, "cancelled"), fetch no further /audio/sr- segments, and
    purge the queue BY ID — the announcement survives and plays afterwards
    (its 'ended' is the purge-by-id proof; a clear-all would have wiped it).
    """
    sample = FIXTURES_DIR / "sample.mp3"
    short_path, _duration = short_mp3
    configure_stub(monkeypatch, sample, total=4, delay=1.2)
    net = NetLog(pwa_v2)

    sid, session_id, payload = ask_stream(brain_v2, pwa_v2, "para despues del primer segmento")
    prefix = f"/audio/sr-{sid}-"

    # Segment 0 fully played out: a real platform 'ended' for it exists…
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'ended' "
        "&& e.src.includes(prefix)).length >= 1",
        arg=prefix,
        timeout=15000,
    )
    # …and the player is already busy with the job's NEXT segment (playing
    # count >= 2), so anything enqueued now is PENDING, not current.
    pwa_v2.wait_for_function(
        "(prefix) => window.__probe.events.filter(e => e.type === 'playing' "
        "&& e.src.includes(prefix)).length >= 2",
        arg=prefix,
        timeout=15000,
    )

    # A foreign announcement lands over SSE and queues behind the stream.
    ann_name = "ann-afterseg.mp3"
    shutil.copyfile(short_path, brain_v2.audio_dir / ann_name)
    brain_v2.hub.publish({
        "type": "transition",
        "pane_id": "w1:p8",
        "agent": "opencode",
        "status": "done",
        "label": "opencode repo8",
        "text": "anuncio extranjero durante la voz",
        "audio_url": f"/audio/{ann_name}",
        "speech_request_id": "ann-afterseg-000001",
    })
    pwa_v2.wait_for_timeout(300)  # SSE delivery -> enqueueAudio (PENDING)
    assert ended_for(pwa_v2, ann_name) == []
    assert playing_for(pwa_v2, ann_name) == [], (
        "the announcement must be PENDING (queued behind the playing segment)"
    )

    # STOP after the segment's 'ended': the identity must still be alive.
    with pwa_v2.expect_response(
        lambda r: "/speech/" in r.url and r.url.endswith("/cancel"),
        timeout=10000,
    ) as cancel_info:
        pwa_v2.evaluate("document.getElementById('stop-audio').click()")
    resp = cancel_info.value
    assert resp.status == 200
    assert resp.json()["status"] == "cancelled"
    assert sid in resp.url, f"cancel hit a foreign id: {resp.url}"

    # No further segment media fetches after the cancel (bounded freeze).
    pwa_v2.wait_for_timeout(300)
    frozen = len(net.audio_sr)
    plays_frozen = len(playing_for(pwa_v2, prefix))
    pwa_v2.wait_for_timeout(1500)
    assert len(net.audio_sr) == frozen, (
        f"/audio/sr- fetches continued after cancel: {net.audio_sr[frozen:]}"
    )
    assert len(playing_for(pwa_v2, prefix)) == plays_frozen, (
        "a job segment was pumped after the cancel"
    )

    # The purge was BY ID: the foreign announcement SURVIVES the stop and
    # plays out afterwards (a legacy clear-all would have wiped it).
    pwa_v2.wait_for_function(
        "(name) => window.__probe.events.some(e => e.type === 'ended' "
        "&& e.src.includes(name))",
        arg=ann_name,
        timeout=10000,
    )
    ann_ends = ended_for(pwa_v2, ann_name)
    assert len(ann_ends) == 1, f"announcement played {len(ann_ends)} times"
    assert ann_ends[0]["wall"] > 0

    # The textual answer was never touched by the speech cancel.
    assert pwa_v2.get_by_text(payload["answer"]).count() >= 1

    # Server side: the job is terminal cancelled.
    wait_job_phase(brain_v2, sid, "cancelled", timeout=10)
