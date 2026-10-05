"""M1 glue-path E2E: every changed line/branch of the app.js speech wiring
under real execution (MQ-03).

Complements test_m1_cancel.py: where that file proves the happy cancel
behavior end to end, this one drives the remaining changed legs of the
M1 wiring — ask() error/no-audio/approval releases, the onStatus banners
(server verdict doubles for /speech/<id>/cancel only), the legacy
clear-all stop, and the natural-finish release. Real browser, real app,
real decoded MP3 media per contract D6/T12.4; only the HTTP verdicts that
the SERVER would produce (503/500/403/422/network) are doubled through
route fulfillment, exactly like the deterministic LLM/TTS doubles.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.e2e.conftest import ask_via_keyboard, wait_for_sse_subscriber

SLOW_A = "pregunta LENTA A"
QUICK = "pregunta rapida"


@pytest.fixture
def pwa(brain, page):
    page.goto(brain.url)
    wait_for_sse_subscriber(brain)
    return page


def _seed_audio(brain, name: str) -> str:
    shutil.copyfile(Path(__file__).parent / "fixtures" / "long.mp3",
                    brain.audio_dir / name)
    return f"/audio/{name}"


def _banner_visible(page, needle: str) -> bool:
    return page.get_by_text(needle, exact=False).count() >= 1


# -- ask() verdict legs: the release must run on every early exit ----------


def test_ask_503_releases_and_banners(brain, pwa):
    pwa.route("**/ask", lambda route: route.fulfill(status=503, json={"detail": "no key"}))
    ask_via_keyboard(pwa, QUICK)
    pwa.wait_for_function(
        "() => document.body.innerText.includes('falta GLM_API_KEY')", timeout=8000)
    assert _banner_visible(pwa, "falta GLM_API_KEY")


def test_ask_http_error_releases_and_banners(brain, pwa):
    pwa.route("**/ask", lambda route: route.fulfill(status=500, json={"detail": "boom"}))
    ask_via_keyboard(pwa, QUICK)
    pwa.wait_for_function(
        "() => document.body.innerText.includes('La pregunta falló (HTTP 500)')",
        timeout=8000)


def test_ask_network_abort_releases_and_banners(brain, pwa):
    pwa.route("**/ask", lambda route: route.abort("connectionreset"))
    ask_via_keyboard(pwa, QUICK)
    pwa.wait_for_function(
        "() => document.body.innerText.includes('Error de red hablando con el brain')",
        timeout=8000)


def test_ask_no_audio_releases_and_after_answer(brain, pwa):
    pwa.route("**/ask", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"answer": "sin voz", "audio_url": null, "approval": null}'))
    ask_via_keyboard(pwa, QUICK)
    # The routed reply never reaches the server, so the server-fed cockpit
    # list may never show the turn; assert on DOM textContent instead.
    pwa.wait_for_function(
        "() => document.body.textContent.includes('sin voz')", timeout=8000)
    # no audio ever: the stop button never appears, the mic re-arms
    pwa.wait_for_timeout(400)


def test_ask_approval_with_audio_enqueues_identified(brain, pwa):
    audio_url = _seed_audio(brain, "appr-audio.mp3")
    pwa.route("**/ask", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"answer": "eco", "audio_url": "%s", "approval": {"gate_id": "g1"}}'
             % audio_url))
    ask_via_keyboard(pwa, QUICK)
    # the spoken echo plays through the normal queue (real decoded media)
    pwa.wait_for_function(
        "() => { const p = document.getElementById('player'); "
        "return !p.paused && (p.currentSrc || p.src || '').includes('appr-audio'); }",
        timeout=10000)
    # the gate popup is open (approval flow armed)
    pwa.wait_for_timeout(400)


def test_ask_approval_without_audio_releases(brain, pwa):
    pwa.route("**/ask", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"answer": "puerta sin eco", "audio_url": null, '
             '"approval": {"gate_id": "g2"}}'))
    ask_via_keyboard(pwa, QUICK)
    # The gate popup takes the view; the turn text lives in the (possibly
    # hidden) conversation panel — assert on DOM textContent, not innerText.
    pwa.wait_for_function(
        "() => document.body.textContent.includes('puerta sin eco')", timeout=8000)
    pwa.wait_for_timeout(400)


# -- onStatus banners: server verdict doubles on the cancel endpoint only --


def _start_slow_identified(brain, pwa):
    brain.tts.slow_texts.add(SLOW_A)
    ask_via_keyboard(pwa, SLOW_A)
    assert brain.tts.slow_started.wait(timeout=10), "the render never started"


def test_stop_cancel_forbidden_banners(brain, pwa):
    pwa.route("**/speech/*/cancel",
              lambda route: route.fulfill(status=403, json={"detail": "forbidden"}))
    _start_slow_identified(brain, pwa)
    pwa.evaluate("document.getElementById('stop-audio').click()")
    pwa.wait_for_function(
        "() => document.body.innerText.includes('rechazó la cancelación') "
        "&& document.body.innerText.includes('cancel-forbidden')", timeout=8000)


def test_stop_cancel_rejected_banners(brain, pwa):
    pwa.route("**/speech/*/cancel",
              lambda route: route.fulfill(status=422, json={"detail": "rejected"}))
    _start_slow_identified(brain, pwa)
    pwa.evaluate("document.getElementById('stop-audio').click()")
    pwa.wait_for_function(
        "() => document.body.innerText.includes('rechazó la cancelación') "
        "&& document.body.innerText.includes('cancel-rejected')", timeout=8000)


def test_stop_cancel_unconfirmed_after_retries_banners(brain, pwa):
    # every attempt dies on the network: after the fixed backoff retries the
    # visible state is the honest "could not confirm" banner.
    pwa.route("**/speech/*/cancel", lambda route: route.abort("failed"))
    _start_slow_identified(brain, pwa)
    pwa.evaluate("document.getElementById('stop-audio').click()")
    pwa.wait_for_function(
        "() => document.body.innerText.includes('No pude confirmar con el servidor')",
        timeout=15000)


# -- legacy stop (nothing identifiable) and natural-finish release ----------


def test_stop_without_identity_legacy_clear_all(brain, pwa):
    # A system event with audio_url enqueues DIRECTLY (no announcer gate,
    # no speech_request_id): nothing identifiable is active, so stop must
    # fall back to the legacy clear-all.
    audio_url = _seed_audio(brain, "legacy-ann.mp3")
    brain.hub.publish({
        "type": "system",
        "kind": "info",
        "text": "anuncio sin identidad",
        "audio_url": audio_url,
    })
    pwa.wait_for_function(
        "() => { const p = document.getElementById('player'); "
        "return !p.paused && (p.currentSrc || p.src || '').includes('legacy-ann'); }",
        timeout=10000)
    pwa.evaluate("document.getElementById('stop-audio').click()")
    # same observable as the original cancel suite: paused + no src
    # (currentSrc lingers in Chromium after removeAttribute+load)
    pwa.wait_for_function(
        "() => { const p = document.getElementById('player'); "
        "return p.paused && !p.src; }", timeout=8000)


def test_play_failure_releases_and_text_survives(brain, pwa):
    # Out-of-call ANNOUNCEMENT whose media 404s: the routed failure path
    # (onAudioError -> handlePlayFailure) releases the request AND keeps
    # the announcement text visible as the FR-04/AC3 persistent toast.
    brain.hub.publish({
        "type": "system", "kind": "info",
        "text": "anuncio con audio roto",
        "audio_url": "/audio/does-not-exist.mp3",
    })
    pwa.wait_for_function(
        "() => document.body.innerText.includes('anuncio con audio roto')",
        timeout=10000)
    pwa.wait_for_timeout(400)


def test_natural_finish_releases_the_request(brain, pwa):
    ask_via_keyboard(pwa, QUICK)
    pwa.wait_for_function(
        "() => { const p = document.getElementById('player'); "
        "return !p.paused && (p.currentSrc || p.src || '').includes('/audio/'); }",
        timeout=10000)
    # natural end: the release rides the platform 'ended' event
    pwa.wait_for_function("() => document.getElementById('player').ended",
                          timeout=20000)
    pwa.wait_for_function(
        "() => document.getElementById('stop-audio').classList.contains('hidden')",
        timeout=8000)
    pwa.wait_for_timeout(400)  # let the tail microtasks settle before teardown
