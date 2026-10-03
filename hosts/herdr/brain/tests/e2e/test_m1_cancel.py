"""Milestone 1 E2E: stop cancels THAT request's server-side speech (VS1.8).

Real browser, real brain app over HTTP, deterministic LLM/TTS doubles, real
media pipeline (committed MP3 fixtures) — per contract D6/T12.4. The stop
button and hang-up share one cancel path; these tests prove the observable:
the PWA POSTs /speech/{id}/cancel with the capability token, the server
aborts exactly that job, other requests and foreign announcements survive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.conftest import ask_via_keyboard, wait_for_sse_subscriber

SLOW_A = "pregunta LENTA A"
SLOW_B = "pregunta LENTA B"
QUICK = "pregunta rapida"


def player_state(page):
    return page.evaluate(
        """() => {
            const p = document.getElementById('player');
            return {ended: p.ended, paused: p.paused, src: p.currentSrc || p.src || ''};
        }"""
    )


@pytest.fixture
def pwa(brain, page):
    page.goto(brain.url)
    wait_for_sse_subscriber(brain)
    return page


def test_stop_button_cancels_server_speech(brain, pwa):
    brain.tts.slow_texts.add(SLOW_A)

    with pwa.expect_response(
        lambda r: "/speech/" in r.url and r.url.endswith("/cancel")
    ) as cancel_info:
        ask_via_keyboard(pwa, SLOW_A)
        assert brain.tts.slow_started.wait(timeout=10), "the render never started"
        pwa.evaluate("document.getElementById('stop-audio').click()")

    resp = cancel_info.value
    assert resp.status == 200
    assert resp.json()["status"] == "cancelled"
    # The capability token rode the body, never the URL.
    assert "speech_cancel_token" not in resp.url
    # Server side: the render was aborted, no audio exists.
    assert brain.tts.aborted == ["Respuesta a: " + SLOW_A]
    assert not any(brain.audio_dir.glob("*.mp3"))
    # Client side: nothing ever played, and the answer text survived.
    state = player_state(pwa)
    assert state["paused"] and not state["src"]
    assert pwa.get_by_text("Respuesta a: " + SLOW_A).count() >= 1


def test_crossed_requests_isolated(brain, pwa, browser):
    brain.tts.slow_texts.update({SLOW_A, SLOW_B})
    other = browser.new_context()
    try:
        page_b = other.new_page()
        page_b.goto(brain.url)

        ask_via_keyboard(page_b, SLOW_B)
        assert brain.tts.slow_started.wait(timeout=10)

        with pwa.expect_response(
            lambda r: "/speech/" in r.url and r.url.endswith("/cancel")
        ) as cancel_info:
            ask_via_keyboard(pwa, SLOW_A)
            # wait for the SECOND slow render (B already holds the slot above)
            pwa.wait_for_timeout(300)
            pwa.evaluate("document.getElementById('stop-audio').click()")

        assert cancel_info.value.status == 200
        verdict = cancel_info.value.json()["status"]
        assert verdict in ("cancelled", "unknown-or-expired", "already-complete")
        # Whichever of A/B held the render slot, B is NEVER aborted: release
        # it and its audio plays out on the other page.
        brain.tts.release_slow.set()
        page_b.wait_for_function("() => document.getElementById('player').ended",
                                 timeout=15000)
        assert not any("LENTA B" in text for text in brain.tts.aborted)
    finally:
        other.close()


def test_foreign_announcement_plays_after_cancel(brain, pwa):
    # A quick identified turn starts playing the LONG fixture...
    ask_via_keyboard(pwa, QUICK)
    pwa.wait_for_function(
        "() => { const p = document.getElementById('player'); "
        "return !p.paused && (p.currentSrc || p.src || '').includes('/audio/'); }",
        timeout=10000,
    )
    # ...and while it plays, a foreign announcement arrives over SSE.
    brain.hub.publish({
        "type": "transition",
        "pane_id": "w1:p7",
        "agent": "opencode",
        "status": "done",
        "label": "opencode repo7",
        "text": "otro agente terminó su trabajo",
        "audio_url": "/audio/ann-foreign.mp3",
        "speech_request_id": "ann-foreign-0000001",
    })
    import shutil

    shutil.copyfile(
        Path(__file__).parent / "fixtures" / "long.mp3",
        brain.audio_dir / "ann-foreign.mp3",
    )

    with pwa.expect_response(
        lambda r: "/speech/" in r.url and r.url.endswith("/cancel")
    ) as cancel_info:
        pwa.evaluate("document.getElementById('stop-audio').click()")

    assert cancel_info.value.status == 200
    # The job had already completed server-side: honest idempotent verdict.
    assert cancel_info.value.json()["status"] in ("already-complete", "cancelled")
    # The foreign announcement was NOT dropped: it takes the freed speaker.
    pwa.wait_for_function(
        "() => { const p = document.getElementById('player'); "
        "return p.ended || (p.currentSrc || p.src || '').includes('ann-foreign'); }",
        timeout=15000,
    )
    pwa.wait_for_function("() => document.getElementById('player').ended", timeout=20000)
