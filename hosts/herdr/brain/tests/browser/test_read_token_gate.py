"""The S11 read gate in the PWA: a read 403 offers pairing, and the
headerless transports (EventSource, <audio src>) carry the device token
as a query parameter the server accepts only there.
"""

from __future__ import annotations

from playwright.sync_api import expect

NOT_PAIRED = {"detail": "dispositivo no emparejado"}
EXPIRED = {"detail": "dispositivo caducado"}


def test_read_403_not_paired_opens_the_pairing_flow(karaoke_app):
    """S11: the pairing offer is no longer write-only — the first READ
    that answers 403 «dispositivo no emparejado» opens Ajustes with the
    pairing hint, the same migration path the writes already had."""
    harness = karaoke_app()
    harness.respond("/herd", NOT_PAIRED, status=403)
    page = harness.load().page

    expect(page.locator("#settings-sheet")).to_have_class("open")
    expect(page.locator(".pair-hint")).to_be_visible()
    assert "herdr-brain pair" in page.locator(".pair-hint").text_content()


def test_read_403_expired_offers_re_pairing_with_its_own_hint(karaoke_app):
    """An expired token gets its own words (S11.3): the hint names the
    caducidad, not a generic "not paired"."""
    harness = karaoke_app()
    harness.respond("/herd", EXPIRED, status=403)
    page = harness.load().page

    expect(page.locator("#settings-sheet")).to_have_class("open")
    expect(page.locator(".pair-hint-expired")).to_be_visible()
    assert "caduc" in page.locator(".pair-hint-expired").text_content().lower()


def test_a_host_403_on_a_read_does_not_offer_pairing(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", {"detail": "host no permitido"}, status=403)
    page = harness.load().page
    page.wait_for_timeout(300)

    assert page.locator(".pair-hint").count() == 0
    assert "open" not in (page.locator("#settings-sheet").get_attribute("class") or "")


def _seeded_with_token(karaoke_app, token="tok-from-storage"):
    """A harness whose localStorage already holds a device token (the
    pre-seeded-phone shape) — add_init_script BEFORE the first load, the
    same pattern as the voice-engine preference in other suites."""
    harness = karaoke_app()
    harness.context.add_init_script(
        f"localStorage.setItem('herdr_brain_device_token', '{token}');"
    )
    return harness


def test_the_events_stream_carries_the_query_device_token(karaoke_app):
    harness = _seeded_with_token(karaoke_app)
    harness.load()

    urls = harness.page.evaluate(
        "() => __karaokeHarness.eventSources.map(s => s.url)"
    )
    assert urls == [harness.origin + "/events?device_token=tok-from-storage"]


def test_without_a_token_the_events_url_stays_bare(karaoke_app):
    """No credential in storage → the exact pre-S11 URL, byte for
    byte (the digest harness pins the same invariant)."""
    harness = karaoke_app()
    harness.load()
    urls = harness.page.evaluate(
        "() => __karaokeHarness.eventSources.map(s => s.url)"
    )
    assert urls == [harness.origin + "/events"]


def test_the_audio_element_carries_the_query_device_token(karaoke_app):
    harness = _seeded_with_token(karaoke_app)
    page = harness.load().page

    harness.event({
        "type": "transition", "pane_id": "w1:p2", "agent": "opencode",
        "status": "done", "label": "Background agent",
        "text": "Tests completed", "audio_url": "/audio/clip.mp3",
    })
    page.wait_for_function(
        "() => __karaokeHarness.media.snapshot().src.includes('/audio/clip.mp3')"
    )
    src = harness.media("snapshot")["src"]
    assert "device_token=tok-from-storage" in src
    assert src.startswith(harness.origin + "/audio/clip.mp3?")
