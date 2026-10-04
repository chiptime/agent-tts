"""Milestone 3 E2E: attributed pending announcements (VS3.9, PRD 03).

Real browser against the real brain app over HTTP — same harness contract as
M1/M2 (D6/T12.4): nothing in the media stack is mocked, completion evidence
is only ever the platform-fired ``ended`` event on the single ``#player``
element, and Chromium launches with exactly the autoplay flag from the
shared conftest.

What this milestone adds on the phone (VS3.6): SSE ``transition`` events
also feed a durable pending ledger — ``localStorage["herdr.speech.pending.v1"]``
(cap 100, visible overflow, persist-uncertain banner) rendered as the
JS-built ``#pending-panel`` with per-record Escuchar/Anunciado/Descartar.
The announcement itself keeps flowing through the announcer and the ONE
sequential audio queue; stopping a response job never touches records.

The browser tests below prove, through real playback:
  1. cross-channel independence — a long identified answer playing on the
     phone never mutes or drops concurrent ambient announcements; everything
     drains to real ``ended`` events in order;
  2. consolidation — the same pane/status transition repeated 3x rapidly
     (within the consolidation window) is ONE audible playback for the ONE
     consolidated record (``repeat_count`` visible in the store), and it
     waits for the current audio to finish;
  3. reload recovery — the panel restores records from localStorage with
     attribution intact, and Escuchar re-synthesizes the stored TEXT through
     the normal /tts pipeline;
  4. visible overflow — an oversized persisted store caps at 100 records,
      shows the ``+N recortados`` badge (never a silent trim), and a record
      is still listenable by regenerating speech from its text;
  5. last-row reachability — the 100-row panel's LAST row can scroll its
      action buttons clear of the fixed call footer (geometry pin).

The sixth test is the milestone's host-side integration half (no browser):
a REAL subprocess of the host pending CLI is killed with SIGKILL mid-claim
(record committed as ``announcing``, blocked against a fake daemon socket
that answers the health ping and swallows the enqueue) and a NEW process
must recover the orphan to visible ``uncertain`` with NO auto-replay;
resolution happens only through the deliberate ``retry`` action, stamped
with ``resolved_by``/``resolved_ts``.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

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

# The host pending CLI and the engine venv that can import its engine seam
# (``import tts_engine`` -> agent_tts). Both resolved from this worktree so
# the subprocess test runs the REAL production files, never an install.
REPO_ROOT = Path(__file__).resolve().parents[5]
HOST_LIB_DIR = REPO_ROOT / "hosts" / "herdr" / "tts-plugin" / "lib"
PENDING_CLI = HOST_LIB_DIR / "pending_queue.py"
_ENGINE_VENV_PY = REPO_ROOT / "engine" / ".venv" / "bin" / "python"
# Local checkouts have the engine venv; CI installs the engine into the
# active interpreter instead, so fall back to it.
ENGINE_PY = _ENGINE_VENV_PY if _ENGINE_VENV_PY.exists() else Path(sys.executable)

PENDING_STORAGE_KEY = "herdr.speech.pending.v1"

_ANN_SEQ = itertools.count(1)


# ---------------------------------------------------------------------------
# In-page instrumentation (same contract as the M2 probe): every real media
# event on the single #player is recorded with the page's monotonic clock.
# app.js's own 'ended' handler pumps the next queue item synchronously, but
# the element's currentSrc is only re-resolved on the NEXT load cycle, so a
# listener registered after app.js's still reads the finishing src.
# ---------------------------------------------------------------------------

_PLAYER_PROBE_JS = """() => {
    if (window.__probe) return;
    const p = document.getElementById('player');
    window.__probe = {events: [], errors: []};
    const rec = (type) => () => {
        window.__probe.events.push({
            type: type,
            perf: performance.now(),
            wall: Date.now() / 1000,
            src: p.currentSrc || p.src || ''
        });
    };
    for (const t of ['playing', 'ended']) {
        p.addEventListener(t, rec(t));
    }
    p.addEventListener('error', () => window.__probe.errors.push({
        perf: performance.now(), src: p.currentSrc || p.src || ''
    }));
}"""


# ---------------------------------------------------------------------------
# Fixture: brain_m3 — the real app with a FAST render double.
#
# Same shape as the M1 ``brain`` fixture (real app over uvicorn, FakeLLM,
# un-started watcher with a live SSE hub) except the renderer double copies
# the committed sample.mp3 (~1.9s) for EVERY render, so /ask answers, /tts
# regenerations and announcement replays all stay real-network, real-decode,
# real-'ended' — without paying long.mp3's ~11s per playback in every test.
# Only the RENDER is doubled (house pattern); the media pipeline never is.
# ---------------------------------------------------------------------------


class SampleRenderer:
    """Render double: every text becomes the committed sample.mp3 bytes."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, settings, text, out_path: Path) -> Path:
        self.calls.append(text)
        shutil.copyfile(FIXTURES_DIR / "sample.mp3", out_path)
        return out_path


@pytest.fixture
def brain_m3(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(audio_dir)})
    tts = SampleRenderer()
    llm = FakeLLM()
    watcher = AgentWatcher(cfg, herdr=StubHerdr(), tts_renderer=tts)  # not started
    app = create_app(
        settings=cfg,
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=tts,
        watcher=watcher,
        daemon_probe=lambda: "up",
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
    assert server.started, "brain_m3 server never came up"

    class Handle:
        pass

    handle = Handle()
    handle.url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    handle.tts = tts
    handle.llm = llm
    handle.hub = watcher.hub
    handle.audio_dir = audio_dir
    try:
        yield handle
    finally:
        # Wake the SSE generators so uvicorn drains immediately (M2 pattern).
        try:
            handle.hub.publish({"type": "transition", "text": "e2e teardown"})
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def pwa_m3(brain_m3, page):
    """The real PWA against brain_m3, SSE subscribed, probe armed."""
    page.goto(brain_m3.url)
    wait_for_sse_subscriber(brain_m3)
    page.evaluate(_PLAYER_PROBE_JS)
    return page


@pytest.fixture
def pwa(brain, page):
    """The M1-fixture app (ScriptedTTS -> long.mp3 answers), probe armed."""
    page.goto(brain.url)
    wait_for_sse_subscriber(brain)
    page.evaluate(_PLAYER_PROBE_JS)
    return page


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def publish_announcement(
    brain,
    *,
    pane_id: str,
    text: str,
    label: str,
    with_audio: bool = True,
    status: str = "done",
    speech_request_id: str | None = None,
) -> dict:
    """Publishes one attributed transition on the live hub (M1 pattern):
    the audio file is a committed MP3 copied into the app's audio dir and
    served over real HTTP. ``with_audio=False`` publishes the text-only
    shape the wire also carries (a render failure still publishes the text
    event) — repeats of an already-consolidated record never re-render."""
    payload = {
        "type": "transition",
        "pane_id": pane_id,
        "agent": "opencode",
        "status": status,
        "label": label,
        "text": text,
        "audio_url": None,
        "speech_request_id": speech_request_id or f"ann-m3-{next(_ANN_SEQ):08d}",
    }
    if with_audio:
        name = f"ann-m3-{next(_ANN_SEQ):08d}.mp3"
        shutil.copyfile(FIXTURES_DIR / "sample.mp3", brain.audio_dir / name)
        payload["audio_url"] = f"/audio/{name}"
    brain.hub.publish(payload)
    return payload


def probe_events(pwa):
    return pwa.evaluate("() => (window.__probe ? window.__probe.events : [])")


def _events_for(pwa, needle: str, kind: str):
    return sorted(
        (e for e in probe_events(pwa) if e["type"] == kind and needle in e["src"]),
        key=lambda e: e["perf"],
    )


def ended_for(pwa, needle: str):
    return _events_for(pwa, needle, "ended")


def playing_for(pwa, needle: str):
    return _events_for(pwa, needle, "playing")


def wait_ended(pwa, needle: str, timeout: int = 15000):
    pwa.wait_for_function(
        "(needle) => (window.__probe ? window.__probe.events : []).some("
        "e => e.type === 'ended' && e.src.includes(needle))",
        arg=needle,
        timeout=timeout,
    )


def wait_playing(pwa, needle: str, timeout: int = 10000):
    pwa.wait_for_function(
        "(needle) => (window.__probe ? window.__probe.events : []).some("
        "e => e.type === 'playing' && e.src.includes(needle))",
        arg=needle,
        timeout=timeout,
    )


def store_state(pwa):
    """The persisted pending ledger, read straight from localStorage."""
    return pwa.evaluate(
        """(key) => {
            try {
                const raw = localStorage.getItem(key);
                if (raw === null) return {absent: true, records: []};
                const data = JSON.parse(raw);
                return {records: data.records || [],
                        overflow_count: data.overflow_count || 0};
            } catch (err) {
                return {parse_error: String(err), records: []};
            }
        }""",
        PENDING_STORAGE_KEY,
    )


def panel_state(pwa):
    """The #pending-panel's visible surface: count, overflow badge, banner."""
    return pwa.evaluate(
        """() => {
            const panel = document.getElementById('pending-panel');
            if (!panel) return {present: false};
            const spans = Array.from(panel.querySelectorAll('span'));
            const overflow = spans.find(
                s => s.textContent.indexOf('recortados') !== -1);
            const count = spans.find(s => /^\\d+$/.test(s.textContent));
            const banner = panel.querySelector('[role="alert"]');
            return {
                present: true,
                visible: !panel.classList.contains('hidden'),
                rows: panel.querySelectorAll('li').length,
                count: count ? count.textContent : null,
                overflow_text: overflow ? overflow.textContent : null,
                overflow_visible: overflow
                    ? !overflow.classList.contains('hidden') : false,
                banner_visible: banner
                    ? !banner.classList.contains('hidden') : false
            };
        }"""
    )


def repeat_badge(pwa, row_text: str):
    """The visible 'xN' consolidation badge on one pending row."""
    return pwa.evaluate(
        """(rowText) => {
            const rows = Array.from(
                document.querySelectorAll('#pending-panel li'));
            const row = rows.find(
                r => r.textContent.indexOf(rowText) !== -1);
            if (!row) return null;
            const badge = Array.from(row.querySelectorAll('span'))
                .find(s => /^×\\d+$/.test(s.textContent));
            return badge
                ? {text: badge.textContent,
                   visible: !badge.classList.contains('hidden')}
                : null;
        }""",
        row_text,
    )


def ask_capture(pwa, text: str):
    """Asks via the REAL keyboard form; returns (request body, response)."""
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
    return bodies[-1], payload


def basename(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def click_row_action(pwa, row_text: str, action: str):
    """Clicks one pending row's action button. The pending list carries
    the footer clearance as real scrollable document height (see the
    last-row geometry pin below), so plain scrollIntoView reaches every
    row; the document-end jump stays as belt-and-braces for any row
    that still lands in the footer's zone. A real pointer click, never
    a synthetic JS dispatch."""
    pwa.evaluate(
        """(rowText) => {
            const row = Array.from(document.querySelectorAll('#pending-panel li'))
                .find(r => r.textContent.indexOf(rowText) !== -1);
            if (!row) return;
            row.scrollIntoView({block: 'center'});
            const rect = row.getBoundingClientRect();
            if (rect.bottom > window.innerHeight - 130) {
                window.scrollTo(0, document.documentElement.scrollHeight);
            }
        }""",
        row_text,
    )
    pwa.locator("#pending-panel li").filter(has_text=row_text).get_by_role(
        "button", name=action
    ).click()


# ---------------------------------------------------------------------------
# Test 1 — the active answer never mutes the ambient channel (FR-12/PRD03)
# ---------------------------------------------------------------------------


def test_pc_never_muted_cross_channel(brain, pwa):
    """While a LONG identified answer plays (ScriptedTTS's long.mp3), three
    ambient announcements arrive over SSE. The sequential queue must hold
    every one of them and play each AFTER the answer: four distinct srcs,
    exactly one real 'ended' each, nothing muted, nothing dropped by the
    pressure of the active answer — and the pending ledger keeps all three
    as records (the channels stay independent domains)."""
    request_body, payload = ask_capture(pwa, "respuesta larga bajo presion")
    # The answer is IDENTIFIED: the PWA minted a speech identity for it.
    assert request_body.get("speech_request_id"), "the answer carried no speech id"
    answer = payload["audio_url"]
    assert answer, "the legacy /ask path must return a full-file audio_url"
    answer_name = basename(answer)

    wait_playing(pwa, answer_name)
    t_pressure = time.monotonic()

    pubs = [
        publish_announcement(
            brain,
            pane_id=f"w3:p{i}",
            label=f"opencode repo{i}",
            text=f"el agente {i} termino su trabajo bajo presion",
        )
        for i in (1, 2, 3)
    ]
    names = [basename(p["audio_url"]) for p in pubs]

    # Every audio drains through the real pipeline: the answer plus all
    # three announcements reach platform 'ended'.
    pwa.wait_for_function(
        """(parts) => {
            const ev = window.__probe ? window.__probe.events : [];
            return parts.every(
                p => ev.some(e => e.type === 'ended' && e.src.includes(p)));
        }""",
        arg=[answer_name, *names],
        timeout=30000,
    )

    answer_ends = ended_for(pwa, answer_name)
    assert len(answer_ends) == 1, "the answer itself was disturbed"
    for name, pub in zip(names, pubs):
        ends = ended_for(pwa, name)
        plays = playing_for(pwa, name)
        assert len(plays) == 1, f"{name}: played {len(plays)} times"
        assert len(ends) == 1, f"{name}: reached ended {len(ends)} times"
        # No interruption: the announcement only STARTS once the answer's
        # audio has fully finished (sequential queue, FIFO).
        assert plays[0]["perf"] > answer_ends[0]["perf"], (
            f"{name} started before the answer finished"
        )

    # Bookkeeping survives too: all three are pending records, none dropped.
    panel = panel_state(pwa)
    assert panel["visible"], "the pending panel stayed hidden"
    assert panel["count"] == "3"
    state = store_state(pwa)
    assert {r["pane_id"] for r in state["records"]} == {"w3:p1", "w3:p2", "w3:p3"}

    drain = time.monotonic() - t_pressure
    print(f"[M3 evidence] cross-channel drain of 4 audios: {drain:.2f}s "
          f"(answer ~11.2s + 3 x ~1.9s announcements)")


# ---------------------------------------------------------------------------
# Test 2 — repeats consolidate to ONE record and ONE audible playback
# ---------------------------------------------------------------------------


def test_announcement_waits_then_plays_consolidated(brain_m3, pwa_m3):
    """The same pane/status transition arrives 3x rapidly (well inside the
    60s consolidation window). The first arrival carries the consolidated
    event's audio — exactly what the host's consolidated dispatch delivers,
    once — and the two repeats are metadata-only refreshes (consolidation
    NEVER re-renders audio; the text-only shape is the same one the wire
    carries when a render failed). Expected observables:

      - exactly ONE audible playback for the consolidated event (one
        'ended', one 'playing', for its distinct audio src);
      - it plays only AFTER the current audio finishes;
      - the pending store holds ONE record for the pane with a visible
        repeat_count of 3 (read straight from localStorage).
    """
    current = publish_announcement(
        brain_m3, pane_id="w4:p0", label="opencode actual",
        text="el audio actual sigue sonando",
    )
    current_name = basename(current["audio_url"])
    wait_playing(pwa_m3, current_name)

    consol_text = "evento repetido del mismo panel"
    first = publish_announcement(
        brain_m3, pane_id="w4:p1", label="opencode repetido", text=consol_text,
        speech_request_id="ann-consol-00000001",
    )
    for _ in range(2):
        publish_announcement(
            brain_m3, pane_id="w4:p1", label="opencode repetido", text=consol_text,
            with_audio=False, speech_request_id="ann-consol-00000001",
        )
    consol_name = basename(first["audio_url"])

    wait_ended(pwa_m3, consol_name, timeout=15000)

    ends = ended_for(pwa_m3, consol_name)
    plays = playing_for(pwa_m3, consol_name)
    assert len(plays) == 1, f"consolidated event played {len(plays)} times"
    assert len(ends) == 1, f"consolidated event ended {len(ends)} times"

    current_ends = ended_for(pwa_m3, current_name)
    assert len(current_ends) == 1
    assert plays[0]["perf"] > current_ends[0]["perf"], (
        "the consolidated announcement did not wait for the current audio"
    )

    # Phone-side consolidation: ONE record for the pane, repeat_count 3.
    state = store_state(pwa_m3)
    records = [r for r in state["records"] if r["pane_id"] == "w4:p1"]
    assert len(records) == 1, (
        f"expected one consolidated record, found {len(records)}: {records}"
    )
    assert records[0]["repeat_count"] == 3
    assert records[0]["state"] == "pending"
    badge = repeat_badge(pwa_m3, consol_text)
    assert badge == {"text": "×3", "visible": True}, (
        f"the x3 consolidation badge is not visible: {badge}"
    )
    print("[M3 evidence] consolidation: 3 rapid repeats -> 1 record "
          "(repeat_count=3), 1 audible playback, played after the current audio")


# ---------------------------------------------------------------------------
# Test 3 — reload restores the records; Escuchar regenerates from text
# ---------------------------------------------------------------------------


def test_reload_restores_records(brain_m3, pwa_m3):
    """Announcements land (records persisted, panel visible), then a full
    page reload: the #pending-panel is rebuilt from localStorage with the
    same ids, pane attribution and first_seen timestamps, and Escuchar on
    one record re-synthesizes its stored TEXT through the normal /tts
    pipeline — the player reaches a real 'ended' on the regenerated src."""
    r1_text = "recarga alfa termino su turno"
    r2_text = "recarga beta termino su turno"
    pubs = [
        publish_announcement(
            brain_m3, pane_id="w5:p1", label="opencode repoalfa", text=r1_text),
        publish_announcement(
            brain_m3, pane_id="w5:p2", label="opencode repobeta", text=r2_text),
    ]
    # Both announcements drained through the real player.
    for pub in pubs:
        wait_ended(pwa_m3, basename(pub["audio_url"]), timeout=15000)

    panel = panel_state(pwa_m3)
    assert panel["visible"] and panel["count"] == "2"
    before = store_state(pwa_m3)
    assert "parse_error" not in before
    assert {r["pane_id"] for r in before["records"]} == {"w5:p1", "w5:p2"}

    pwa_m3.reload()
    pwa_m3.wait_for_function(
        "() => { const p = document.getElementById('pending-panel');"
        " return p && !p.classList.contains('hidden'); }",
        timeout=10000,
    )
    pwa_m3.evaluate(_PLAYER_PROBE_JS)  # re-arm: the reload wiped the page

    after = store_state(pwa_m3)
    assert "parse_error" not in after
    by_id_before = {r["id"]: r for r in before["records"]}
    by_id_after = {r["id"]: r for r in after["records"]}
    assert set(by_id_before) == set(by_id_after), "record ids changed across reload"
    for rid, rec in by_id_after.items():
        assert rec["pane_id"] == by_id_before[rid]["pane_id"], (
            f"pane attribution lost for {rid}"
        )
        assert rec["first_seen_ts"] == by_id_before[rid]["first_seen_ts"], (
            f"first_seen_ts lost for {rid}"
        )
    panel = panel_state(pwa_m3)
    assert panel["visible"] and panel["count"] == "2" and panel["rows"] == 2

    # Escuchar: the stored TEXT goes through the normal /tts pipeline.
    with pwa_m3.expect_response(
        lambda r: r.url.endswith("/tts"), timeout=15000
    ) as resp_info:
        click_row_action(pwa_m3, r1_text, "Volver a escuchar este aviso")
    resp = resp_info.value
    assert resp.status == 200
    body = json.loads(resp.request.post_data or "{}")
    assert body["text"] == r1_text, "Escuchar re-synthesized something else"
    regenerated = basename(resp.json()["audio_url"])
    wait_ended(pwa_m3, regenerated, timeout=15000)
    assert len(ended_for(pwa_m3, regenerated)) == 1
    print(f"[M3 evidence] reload restored {len(after['records'])} records; "
          f"Escuchar regenerated '/audio/{regenerated}' from text")


# ---------------------------------------------------------------------------
# Test 4 — overflow is visible, a record still regenerates on demand
# ---------------------------------------------------------------------------


def test_overflow_visible_regenerated(brain_m3, pwa_m3):
    """Seeding localStorage directly with 130 valid records (deterministic,
    faster than publishing >100 announcements over the hub) and reloading:
    the store's own load() enforces the cap of 100 — the 30 OLDEST records
    are dropped from the list but COUNTED in overflow_count, surfaced as the
    visible '+30 recortados' badge. The persist-uncertain banner must NOT
    show (the payload is durable by construction), and one surviving
    record's Escuchar still regenerates speech from its stored text."""
    seed = {
        "records": [
            {
                "id": f"seed-{i:04d}",
                "pane_id": f"w6:p{i}",
                "agent": "opencode",
                "status": "done",
                "label": f"opencode repo{i}",
                "text": f"texto alpha-{i} termino su turno",
                "first_seen_ts": 1759000000.0 + i,
                "last_seen_ts": 1759000000.0 + i,
                "repeat_count": 1,
                "state": "pending",
            }
            for i in range(130)
        ],
        "overflow_count": 0,
    }
    pwa_m3.evaluate(
        "(arg) => localStorage.setItem(arg.key, arg.payload)",
        {"key": PENDING_STORAGE_KEY, "payload": json.dumps(seed)},
    )
    pwa_m3.reload()

    pwa_m3.wait_for_function(
        "() => { const p = document.getElementById('pending-panel');"
        " return p && !p.classList.contains('hidden'); }",
        timeout=10000,
    )
    pwa_m3.evaluate(_PLAYER_PROBE_JS)  # re-arm: the reload wiped the page
    panel = panel_state(pwa_m3)
    assert panel["count"] == "100", f"cap not enforced: {panel}"
    assert panel["rows"] == 100
    # The overflow condition is VISIBLE, never a silent trim.
    assert panel["overflow_text"] == "+30 recortados", panel
    assert panel["overflow_visible"], "the overflow badge is hidden"
    assert not panel["banner_visible"], "a durable seed must not show the banner"

    # A surviving record still regenerates speech FROM ITS TEXT. The row
    # targeted stays mid-list on purpose: this test is about overflow
    # accounting and text regeneration, not last-row reachability (that
    # is the dedicated geometry pin below).
    target_text = "texto alpha-100 termino su turno"
    with pwa_m3.expect_response(
        lambda r: r.url.endswith("/tts"), timeout=15000
    ) as resp_info:
        click_row_action(pwa_m3, target_text, "Volver a escuchar este aviso")
    resp = resp_info.value
    assert resp.status == 200
    body = json.loads(resp.request.post_data or "{}")
    assert body["text"] == target_text
    regenerated = basename(resp.json()["audio_url"])
    wait_ended(pwa_m3, regenerated, timeout=15000)
    assert len(ended_for(pwa_m3, regenerated)) == 1

    # The overflow accounting becomes DURABLE on the next mutation: marking
    # one record announced persists the honest aggregate (100 kept, 30 cut).
    click_row_action(pwa_m3, target_text, "Marcar como anunciado")
    pwa_m3.wait_for_function(
        "() => { const rows = document.querySelectorAll('#pending-panel li');"
        " return rows.length === 100 && Array.from(rows).some(r =>"
        "  r.textContent.indexOf('anunciado') !== -1); }",
        timeout=10000,
    )
    state = store_state(pwa_m3)
    assert "parse_error" not in state
    assert state["overflow_count"] == 30
    ids = {r["id"] for r in state["records"]}
    assert len(ids) == 100
    assert "seed-0000" not in ids, "the oldest record survived the cap"
    assert "seed-0029" not in ids
    assert "seed-0030" in ids and "seed-0129" in ids
    marked = next(r for r in state["records"] if r["id"] == "seed-0100")
    assert marked["state"] == "announced"
    print("[M3 evidence] overflow: 130 seeded -> 100 kept, +30 recortados "
          "badge visible, record 100 still regenerates from text")


# ---------------------------------------------------------------------------
# Test 5 — geometry pin: the LAST row scrolls clear of the fixed footer
# ---------------------------------------------------------------------------


def test_pending_last_row_reachable_above_footer(brain_m3, pwa_m3):
    """The 100-row panel on a phone-height viewport: the LAST row's
    action buttons must be able to scroll CLEAR of the fixed call
    footer. The body's own 104px padding-bottom cannot provide this —
    body is height:100%, so overflowing rows poke below the padded box
    and that padding never becomes scrollable document space. The fix
    under test (app.js PENDING_FOOTER_CLEARANCE) makes the pending
    list itself carry the footer clearance as real in-flow height, so
    the document can scroll past the last row.

    Seeding localStorage directly (the overflow test's path), reload,
    scroll the panel to its bottom, bring the LAST row into view the
    way anything reaches it (scrollIntoView center), and assert the
    row's bottom edge sits above the fixed footer's top at the
    achieved scroll position. A real pointer click on the last row's
    action button then proves the record is actionable, not merely
    visible."""
    pwa_m3.set_viewport_size({"width": 390, "height": 844})
    seed = {
        "records": [
            {
                "id": f"seed-pin-{i:04d}",
                "pane_id": f"w7:p{i}",
                "agent": "opencode",
                "status": "done",
                "label": f"opencode repopin{i}",
                "text": f"texto pin-{i} termino su turno",
                "first_seen_ts": 1759000000.0 + i,
                "last_seen_ts": 1759000000.0 + i,
                "repeat_count": 1,
                "state": "pending",
            }
            for i in range(120)
        ],
        "overflow_count": 0,
    }
    pwa_m3.evaluate(
        "(arg) => localStorage.setItem(arg.key, arg.payload)",
        {"key": PENDING_STORAGE_KEY, "payload": json.dumps(seed)},
    )
    pwa_m3.reload()

    pwa_m3.wait_for_function(
        "() => { const p = document.getElementById('pending-panel');"
        " return p && !p.classList.contains('hidden'); }",
        timeout=10000,
    )

    geo = pwa_m3.evaluate(
        """() => {
            // Bottom of the panel: the document end IS the panel end
            // (everything after it in the DOM is fixed or hidden).
            window.scrollTo(0, document.documentElement.scrollHeight);
            const rows = document.querySelectorAll('#pending-panel li');
            const last = rows[rows.length - 1];
            // The user's reach: center the last row like any other.
            last.scrollIntoView({block: 'center'});
            const rect = last.getBoundingClientRect();
            const footerTop = document.querySelector('footer')
                .getBoundingClientRect().top;
            return {
                rows: rows.length,
                scrollY: window.scrollY,
                maxScrollY: document.documentElement.scrollHeight
                    - window.innerHeight,
                lastBottom: rect.bottom,
                footerTop: footerTop,
            };
        }"""
    )
    assert geo["rows"] == 100, f"cap not enforced: {geo}"
    assert geo["scrollY"] > 0, (
        f"a 100-row panel on 844px never scrolled: {geo}"
    )
    tolerance = 2  # px, rounding only
    assert geo["lastBottom"] <= geo["footerTop"] + tolerance, (
        "the LAST row's buttons stay under the fixed footer: "
        f"bottom={geo['lastBottom']:.1f} footerTop={geo['footerTop']:.1f} "
        f"at scrollY={geo['scrollY']:.0f}/{geo['maxScrollY']:.0f}"
    )

    # Actionable, not just visible: a real pointer click on the last
    # row's button (Playwright scrolls it into view and checks the hit
    # target — the fixed footer must not intercept the pointer).
    last_text = "texto pin-119 termino su turno"
    pwa_m3.locator("#pending-panel li").filter(
        has_text=last_text).get_by_role(
        "button", name="Marcar como anunciado").click()
    state = store_state(pwa_m3)
    marked = next(
        (r for r in state["records"] if r["id"] == "seed-pin-0119"), None)
    assert marked and marked["state"] == "announced", (
        f"the last row's button click never landed: {marked}"
    )
    print(f"[M3 evidence] last of {geo['rows']} rows scrolls "
          f"{geo['footerTop'] - geo['lastBottom']:.1f}px clear of the "
          f"fixed footer and its button click lands "
          f"(scrollY={geo['scrollY']:.0f}/{geo['maxScrollY']:.0f})")


# ---------------------------------------------------------------------------
# Test 5 — kill -9 the REAL host pending CLI mid-claim (VS3.7 integration)
# ---------------------------------------------------------------------------


class _FakeBlockingDaemon:
    """A Unix-socket daemon double speaking the engine's framing v2.

    It answers ``ping`` with ``pong`` — so the client's ensure_daemon
    handshake sees a healthy daemon and NEVER spawns a real one — and
    swallows every ``enqueue`` command without replying. The dispatching
    tick then blocks on its 1s frame-read, which is exactly the mid-claim
    window this test kills inside. Every enqueue it saw is recorded: the
    count is the no-auto-replay evidence.
    """

    MAGIC = b"ATTS"
    VERSION = 2
    HEADER = 9

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self.enqueue_frames: list[str] = []
        self.pings = 0
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None

    @classmethod
    def _encode(cls, payload: str) -> bytes:
        body = payload.encode("utf-8")
        return cls.MAGIC + bytes([cls.VERSION]) + len(body).to_bytes(4, "big") + body

    def _recv_exact(self, conn: socket.socket, count: int) -> bytes | None:
        parts = []
        remaining = count
        while remaining > 0:
            chunk = conn.recv(remaining)
            if not chunk:
                return None
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def _read_frame(self, conn: socket.socket) -> str | None:
        header = self._recv_exact(conn, self.HEADER)
        if header is None or header[:4] != self.MAGIC:
            return None
        length = int.from_bytes(header[5:9], "big")
        body = self._recv_exact(conn, length) if length else b""
        if body is None:
            return None
        return body.decode("utf-8", "replace")

    def _serve(self, conn: socket.socket) -> None:
        try:
            while True:
                payload = self._read_frame(conn)
                if payload is None:
                    return
                if payload == "ping":
                    with self._lock:
                        self.pings += 1
                    conn.sendall(self._encode("pong"))
                elif payload.startswith("enqueue "):
                    with self._lock:
                        self.enqueue_frames.append(payload)
                    # Hold forever: no reply, the client blocks mid-claim.
                    time.sleep(3600)
                else:
                    conn.sendall(self._encode("ERR: unknown command"))
        except OSError:
            return

    def start(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        listener.listen(8)
        self._listener = listener
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()


def _parse_reply_line(line: str) -> dict:
    tokens = {}
    for token in line.split():
        key, sep, value = token.partition("=")
        if sep:
            tokens[key] = value
    return tokens


def test_kill9_host_pending_recovers_uncertain_no_replay(tmp_path):
    """REAL subprocesses of the host pending CLI (the production file, run
    with the engine venv so its lazy engine seam imports for real), against
    a tmp HOME ledger and a fake daemon socket:

      - ``admit`` lands a dispatchable record;
      - ``tick`` claims it (pending -> announcing, committed) and blocks on
        the fake daemon's swallowed enqueue — SIGKILL lands mid-claim;
      - a NEW process (``status``/``list``) constructs the queue, and its
        construction-time recovery flips the orphan announcing record to
        visible ``uncertain`` — attributed to crash-recovery — with NO
        auto-replay (the fake daemon saw exactly the one pre-kill enqueue);
      - the deliberate ``retry`` (with --actor) is what re-queues it, and
        the action is stamped with resolved_by/resolved_ts.
    """
    assert ENGINE_PY.exists(), f"engine venv python missing: {ENGINE_PY}"
    assert PENDING_CLI.exists(), f"host pending CLI missing: {PENDING_CLI}"

    state_dir = tmp_path / "herdr-home"
    db_path = state_dir / ".local" / "state" / "herdr-tts" / "pending.db"

    audio = tmp_path / "ann-kill9.mp3"
    shutil.copyfile(FIXTURES_DIR / "sample.mp3", audio)

    daemon = _FakeBlockingDaemon(tmp_path / "player.sock")
    daemon.start()

    env = {
        **os.environ,
        "HOME": str(state_dir),
        "AGENT_TTS_SOCKET": str(tmp_path / "player.sock"),
        "AGENT_TTS_LOCK_FILE": str(tmp_path / "playing.lock"),
        "AGENT_TTS_PID_FILE": str(tmp_path / "current.pid"),
        "PYTHONPATH": str(HOST_LIB_DIR),
    }

    def run_cli(*args: str, timeout: float = 30.0):
        proc = subprocess.run(
            [str(ENGINE_PY), str(PENDING_CLI), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc

    t0 = time.monotonic()
    # 1) admit: one ambient event with usable audio -> pending.
    adm = run_cli(
        "admit", "--pane-id", "w9:p1", "--agent", "opencode", "--status", "done",
        "--label", "opencode repokill", "--text", "evento para la prueba de kill9",
        "--audio-path", str(audio),
    )
    assert adm.returncode == 0, f"admit failed: {adm.stderr}"
    admit_reply = _parse_reply_line(adm.stdout.strip().splitlines()[-1])
    record_id = admit_reply["id"]
    assert admit_reply["ok"] == "true"
    assert admit_reply["state"] == "pending"

    # 2) tick in a REAL subprocess: claims -> announcing (committed to the
    #    ledger) BEFORE the enqueue attempt; the fake daemon swallows the
    #    enqueue, so the process sits mid-claim inside its frame read.
    tick = subprocess.Popen(
        [str(ENGINE_PY), str(PENDING_CLI), "tick"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    reader = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    try:
        deadline = time.monotonic() + 15
        seen_announcing = False
        while time.monotonic() < deadline:
            row = reader.execute(
                "SELECT state FROM pending_records WHERE id = ?", (record_id,)
            ).fetchone()
            if row and row[0] == "announcing":
                seen_announcing = True
                break
            assert tick.poll() is None, (
                "the tick exited before the claim could be observed: "
                f"rc={tick.returncode} out={tick.stdout.read() if tick.stdout else ''}"
            )
            time.sleep(0.005)
        assert seen_announcing, "the record never reached 'announcing' before the kill"

        # Strictest mid-dispatch point: the daemon is HOLDING the enqueue
        # frame the tick sent — the claim is durable and the enqueue is
        # in flight when the SIGKILL lands.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(daemon.enqueue_frames) < 1:
            assert tick.poll() is None, "the tick exited before the enqueue landed"
            time.sleep(0.005)
        assert len(daemon.enqueue_frames) == 1, (
            "the fake daemon never saw the enqueue attempt"
        )
        tick.kill()  # SIGKILL: the host "crashes" mid-claim
        tick.wait(timeout=10)
        assert tick.returncode == -9, f"expected SIGKILL, got rc={tick.returncode}"

        # The ledger is frozen mid-claim: the row is still announcing, the
        # claim reset never ran (the crash ate it).
        row = reader.execute(
            "SELECT state FROM pending_records WHERE id = ?", (record_id,)
        ).fetchone()
        assert row[0] == "announcing", f"row moved post-kill: {row[0]}"
    finally:
        reader.close()

    # 3) NEW process: constructing the queue recovers the orphan announcing
    #    record to visible uncertain. No replay: the fake daemon's enqueue
    #    count stays at the single pre-kill frame.
    status = run_cli("status")
    assert status.returncode == 0, status.stderr
    status_reply = _parse_reply_line(status.stdout.strip().splitlines()[-1])
    assert status_reply["uncertain"] == "1", status_reply
    assert status_reply["announcing"] == "0", status_reply
    assert status_reply["pending"] == "0", status_reply

    listing = run_cli("list")
    assert listing.returncode == 0, listing.stderr
    assert f"id={record_id}" in listing.stdout
    assert "state=uncertain" in listing.stdout
    assert len(daemon.enqueue_frames) == 1, "a status/list run dispatched audio"

    evidence = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    try:
        row = evidence.execute(
            "SELECT state, resolved_by, resolved_ts, claimed_ts"
            " FROM pending_records WHERE id = ?", (record_id,)
        ).fetchone()
        assert row[0] == "uncertain"
        assert row[1] == "crash-recovery", row
        assert row[2] is not None, "recovery was not attributable"
        assert row[3] is not None, "the claim evidence was lost"
    finally:
        evidence.close()

    # 4) Resolution is DELIBERATE only: retry re-queues uncertain -> pending,
    #    stamped with the actor and timestamp.
    retry = run_cli("retry", record_id, "--actor", "e2e-operator")
    assert retry.returncode == 0, retry.stderr
    retry_reply = _parse_reply_line(retry.stdout.strip().splitlines()[-1])
    assert retry_reply["ok"] == "true"
    assert retry_reply["from"] == "uncertain" and retry_reply["to"] == "pending"
    assert retry_reply["resolved_by"] == "e2e-operator"
    assert float(retry_reply["resolved_ts"]) > 0

    final = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    try:
        row = final.execute(
            "SELECT state, resolved_by FROM pending_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        assert row == ("pending", "e2e-operator"), row
    finally:
        final.close()

    # No auto-replay anywhere: exactly the one enqueue the killed tick sent.
    assert len(daemon.enqueue_frames) == 1
    print(f"[M3 evidence] kill -9 mid-claim recovered in a new process: "
          f"announcing -> uncertain (crash-recovery), dispatches=0, "
          f"deliberate retry by e2e-operator; total {time.monotonic() - t0:.2f}s")
