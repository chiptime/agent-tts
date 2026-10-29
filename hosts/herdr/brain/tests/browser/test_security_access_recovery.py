"""Security-access recovery in the PWA (correction batch B1): the ONE
SSE transport must carry the CURRENT device credential. Pairing from
the real Ajustes form (from no token, or from a stale/expired one)
reopens it with the new token — exactly one non-closed EventSource, no
duplicate handlers — and media queued BEFORE pairing plays with the
credential that exists when its source is actually assigned.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

NOT_PAIRED = {"detail": "dispositivo no emparejado"}
EXPIRED = {"detail": "dispositivo caducado"}


def _unpaired_harness(karaoke_app, stale):
    """The boot shape under test: every read 403s (no token, or a stale
    one the server refuses), so Ajustes opens on the pair form; /pair
    answers a fresh synthetic token."""
    h = karaoke_app()
    if stale:
        h.context.add_init_script(
            "localStorage.setItem('herdr_brain_device_token', 'SYNTHETIC_OLD_TOKEN')"
        )
    detail = EXPIRED["detail"] if stale else NOT_PAIRED["detail"]
    h.respond("/herd", {"detail": detail}, status=403)
    h.respond("/devices", {"detail": detail}, status=403)
    h.respond("/pair", {"token": "SYNTHETIC_NEW_TOKEN", "label": "phone"}, method="POST")
    return h


def _pair_from_the_form(page):
    """The real PWA pair form: fill code + label, submit."""
    page.locator(".pair-code").fill("ABCDEFGH")
    page.locator(".pair-label").fill("phone")
    page.locator(".pair-submit").click()
    expect(page.locator(".pair-status")).to_contain_text("phone")


LIVE_SSE_TOKENS = (
    "() => __karaokeHarness.eventSources"
    ".filter(s => s.readyState !== 2)"
    ".map(s => new URL(s.url).searchParams.get('device_token'))"
)


@pytest.mark.parametrize("stale", [None, "SYNTHETIC_OLD_TOKEN"])
def test_pairing_replaces_the_stale_sse_transport(karaoke_app, stale):
    """B1: the boot EventSource (bare without a token, or built on the
    expired one) is CLOSED and one new transport opens with the token
    the successful claim just stored — never left bare/stale, never
    duplicated."""
    h = _unpaired_harness(karaoke_app, stale)
    page = h.load().page
    expect(page.locator("#settings-sheet")).to_have_class("open")

    # Boot transport reflects the pre-pairing credential…
    boot = page.evaluate(
        "() => __karaokeHarness.eventSources.map(s => s.url)"
    )
    assert boot == [h.origin + (
        "/events?device_token=SYNTHETIC_OLD_TOKEN" if stale else "/events"
    )]

    # …the pair succeeds through the real form…
    h.respond("/herd", [])
    h.respond("/devices", {"enforced": True, "current": "phone", "devices": []})
    _pair_from_the_form(page)

    # …and exactly ONE non-closed EventSource remains, with the new token.
    assert page.evaluate(LIVE_SSE_TOKENS) == ["SYNTHETIC_NEW_TOKEN"]
    all_sources = page.evaluate(
        "() => __karaokeHarness.eventSources.map(s => s.readyState)"
    )
    assert all_sources == [2, 1]  # the boot transport closed, one reopened


def test_media_queued_before_pairing_plays_with_the_new_token(karaoke_app):
    """B1 (media): audio enqueued while unpaired holds a bare URL; its
    source is credentialised when it is actually assigned to the player,
    so it plays with the token that exists THEN — not with the bare URL
    frozen at enqueue time."""
    h = _unpaired_harness(karaoke_app, None)
    page = h.load().page
    expect(page.locator("#settings-sheet")).to_have_class("open")
    h.media("queuePlay", outcome="defer")  # hold the first item's play

    # First announcement: plays (deferred) on arrival with the bare URL.
    h.event({
        "type": "transition", "pane_id": "w1:p2", "agent": "opencode",
        "status": "done", "label": "Background agent",
        "text": "First", "audio_url": "/audio/first.mp3",
    })
    page.wait_for_function(
        "() => __karaokeHarness.media.snapshot().src.includes('/audio/first.mp3')"
    )
    assert "device_token" not in h.media("snapshot")["src"]

    # Second announcement: queued behind the deferred one, still bare.
    h.event({
        "type": "transition", "pane_id": "w1:p3", "agent": "opencode",
        "status": "done", "label": "Other agent",
        "text": "Second", "audio_url": "/audio/queued.mp3",
    })
    page.wait_for_timeout(200)

    # Pair through the real form; the queue keeps the pre-pairing item.
    h.respond("/herd", [])
    h.respond("/devices", {"enforced": True, "current": "phone", "devices": []})
    _pair_from_the_form(page)
    assert page.evaluate(LIVE_SSE_TOKENS) == ["SYNTHETIC_NEW_TOKEN"]

    # Let the first item finish; the queued one's source is assigned NOW.
    pending = h.media("snapshot")["pendingPlays"]
    assert pending, "the deferred first play is still held"
    h.media("settlePlay", id=pending[0])
    h.media("end")
    page.wait_for_function(
        "() => __karaokeHarness.media.snapshot().src.includes('/audio/queued.mp3')"
    )
    src = h.media("snapshot")["src"]
    assert "device_token=SYNTHETIC_NEW_TOKEN" in src
