"""Foundation proof only: real Chromium/DOM, simulated services and media."""

import hashlib
import json
from importlib.metadata import version
import subprocess
import sys

import pytest


# Original KARAOKE-B0 static inputs, not instrumented or reconstructed scripts.
EXPECTED_DIGESTS = {
    # Re-pinned on the MERGED bytes. Both this branch and main change the
    # served static files (the sheet grouping and the typed-reply input),
    # so these three hashes have to be recomputed from disk after every
    # merge that touches index.html, app.js or herd-group.js.
    # "/" and "/app.js" re-pinned for device pairing (F1): the Ajustes
    # card, the bearer on write fetches and pairing.js wiring; then for the
    # destructive-command guard (destructive.js + composer confirmation);
    # then for S11 (reads carry the bearer, EventSource/<audio> carry the
    # device_token query parameter, the read-403 pairing offer and the
    # caducado hint); then for the B1 correction (the SSE transport
    # reopens with the current credential on pairing/revoke/expiry, and
    # media URLs are credentialised at source-assignment time); then
    # for the T2 reading views (Historial + Terminal tabs, history.js);
    # then for S2 (recognized-dialog buttons above the keypad, the
    # unread-dialog card, the digit row and dialogs.js); then for S12
    # (verified sends: the receipt card, the Stop button and the steer
    # long-press in the composer, receipts.js); then for S5/S13 (the
    # Avisos push card + push.js, the Mensaje composer, and the sw.js
    # push/notificationclick handlers); then for the Zen exit control
    # (floating exit button, Escape exits Zen, i18n labels, setZen).
    "/": "378143ff1d202fef5232b5edfe3eb96ecbb16156be5840a33329d908acc9b5f8",
    "/app.js": "2c7fbacf512611c5c9bd32d13d395451e3c8a41c8b442262816d5dce56e7d31c",
    "/push.js": "e1acadd98d95420cf9924ea633b32d9ec5b51c8654c5e7df923c2bbc5547f52c",
    "/receipts.js": "ca7b22c52c8160a3ac577f694eb65ec0798b77cd6ac179a5c567dffdc1fb2ba9",
    "/dialogs.js": "0e5b6afde7ef662591f66fc69891e3b1ec343fafe0d2c5be8fbef0c5a124a4c0",
    "/herd-group.js": "e923e48334e72e303ee3399edfd4d69f5c1bb4d49ef906f9eb9d5c8932c7426c",
    "/toast.js": "cbccb72b22530ff8f83250bebd8d498ca20977db9536aa094aa759bb9632357e",
    "/reader.js": "28daec6199b0e756e1285b40f20fc860c1d9b4d2169cb2433c1ccfb12db31a76",
    "/speech.js": "23b10e3ffe9b22c7480929cb33d12e4a0e3d378f49b841645ec670634a9fe709",
    "/announce.js": "e525a591e5268d2cbfc1d93956469b81da41fb3c87b17c43caccbc7ebd00e9df",
    # app.js and approval.js re-pinned for the T-07/T-08 spoken-dialog,
    # approval-binding and B1–B8 correction changes (gate speech id +
    # explanation receipts, clarify identity, item-aware playback proof);
    # no other static bytes changed.
    "/approval.js": "0254382dac1fd1be00d608d2476a1ec82582dfd502a48bad6a0c5cc8041d741f",
    "/consult.js": "fc2e4d18c206164fcec7a8baf2be1697e5b6d86152ed12c432169a70c1113bc4",
    "/endpointing.js": "86c1b18218b22d9f2a812b3209322f7691eb468019a53278653f5375326f0664",
    "/vad.js": "fcce79bcf4182dc493dbae6c3f790cf5a1a83ccc0d45714c520b612f87dbf4d1",
    # S4/S3 PWA unit: i18n runtime + dictionaries, settings sheet,
    # update notice, operator dock, offline-shell sw.js.
    "/dock.js": "4a5dc20d575c3c9e5f098116fc9314f24297b2934cb3142bc885b6e1d0155adb",
    "/i18n.js": "8bcd2ceb9f56109afaa0ec158e1a6f002799c4a983261b9d58cd38723c06719f",
    "/i18n/en.js": "231f7c1b1bbd591fd9dbb6178aa8b553367d093a6a7b103de3f6f321a55e9e77",
    "/i18n/es.js": "c44cabfc28ef5d25cc83bc14d86e372283987502eb0f831c797803f37e346db7",
    "/settings.js": "1c2cf3ed92bf959c19ecde0107618b8d1ff0690d2c6fa7c842a2fdc551e05f72",
    "/updates.js": "7f87250cf6cd9c2b8944da632fe036d0c18a87a90f48f70b84e18bcd338cfa84",
}


def test_real_app_with_isolated_media(karaoke_harness, browser, pytestconfig, request):
    harness = karaoke_harness
    page = harness.page
    node = subprocess.run(
        ["node", "--version"], check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()
    versions = {
        "python": sys.version,
        "pytest": version("pytest"),
        "pytest-playwright": version("pytest-playwright"),
        "chromium": browser.version,
        "node": node,
    }
    pytestconfig._karaoke_versions = versions
    (harness.artifact_dir / "versions.json").write_text(
        json.dumps(versions, indent=2) + "\n", encoding="utf-8"
    )
    assert sys.version_info >= (3, 11)
    assert int(node.removeprefix("v").split(".")[0]) >= 20
    assert browser.browser_type.name == "chromium"
    assert "_tts_backend_stubs" not in request.fixturenames
    assert "hermetic_stores" not in request.fixturenames
    assert harness.server_preloaded or "herdr_brain.server" not in sys.modules

    # This is the initial RED boundary: the fixture must load the actual app.
    assert page.locator("#conversation").count() == 1
    assert page.locator("#glance-turns").count() == 1
    page.locator("#conversation .turn.brain .replay-btn").wait_for(state="attached")
    assert "Escuchar" in page.locator("#conversation .replay-btn").text_content()
    assert page.evaluate("() => document.styleSheets[0].cssRules.length") > 0
    assert page.evaluate("""() => [
        typeof Endpointing.createEndpointer, typeof Vad.createVad,
        typeof ApprovalFlow.createApprovalFlow, typeof Reader.createReader,
        typeof Toast.createToast, typeof Announce.createAnnouncer,
        typeof Speech.createSpeechController, typeof Consult.createConsultUI
    ]""") == ["function"] * 8
    for path, digest in EXPECTED_DIGESTS.items():
        response = harness.static_responses[path]
        assert response.status == 200
        assert hashlib.sha256(response.body()).hexdigest() == digest, path
    assert not harness.page_errors
    assert not harness.blocked_requests
    assert harness.context.service_workers == []
    assert page.evaluate("() => __karaokeHarness.eventSources.map(s => s.url)") == [
        harness.origin + "/events"
    ]
    assert page.evaluate("() => __karaokeHarness.microphoneCalls") == 0
    assert page.evaluate("""async () =>
        (await navigator.permissions.query({name: 'microphone'})).state
    """) != "granted"

    page.evaluate("""() => {
        window.harnessEvents = [];
        const player = document.getElementById('player');
        for (const type of ['loadedmetadata', 'durationchange', 'play',
                            'playing', 'pause', 'timeupdate']) {
            player.addEventListener(type, () => harnessEvents.push(type));
        }
    }""")
    reply = harness.queue_tts(audio_url="/audio/smoke.mp3")
    # The existing drawer is collapsed on boot; activate its real replay button.
    page.locator("#conversation .replay-btn").evaluate("button => button.click()")
    page.wait_for_function("() => document.getElementById('player').src.endsWith('/smoke.mp3')")
    assert reply.request.post_data_json == {"text": harness.answer_text}
    harness.media("metadata", duration=24)
    page.wait_for_function("() => __karaokeHarness.media.snapshot().playCalls === 1")
    harness.media("advance", time=6)
    harness.media("pause")
    snapshot = harness.media("snapshot")
    assert snapshot["duration"] == 24
    assert snapshot["currentTime"] == 6
    assert snapshot["paused"] is True
    assert sorted(page.evaluate("() => harnessEvents")) == sorted([
        "play", "playing", "loadedmetadata", "durationchange", "timeupdate", "pause"
    ])
    # No real media fetch/decoder was needed by the actual shared player.
    assert not any(r.url.endswith("/smoke.mp3") for r in harness.requests)


@pytest.mark.parametrize("url, method", [
    ("/unregistered-path", "GET"),
    ("/app.js", "POST"),
    ("/app.js?unregistered=1", "GET"),
    ("http://localhost:41731/app.js", "GET"),
    ("https://fixture.invalid/app.js", "GET"),
])
def test_unexpected_traffic_is_aborted(karaoke_harness, url, method):
    harness = karaoke_harness
    absolute = harness.origin + url if url.startswith("/") else url
    harness.expect_blocked(absolute, method=method)
    assert harness.page.evaluate("""async ([url, method]) => {
        try { await fetch(url, {method}); return 'escaped'; }
        catch (error) { return error.name; }
    }""", [absolute, method]) == "TypeError"
    assert harness.blocked_requests == [{"method": method, "url": absolute}]


def test_no_physical_microphone_or_service_worker(karaoke_harness):
    harness = karaoke_harness
    assert harness.page.evaluate("""async () => {
        try { await navigator.mediaDevices.getUserMedia({audio: true}); return 'escaped'; }
        catch (error) { return error.name; }
    }""") == "NotAllowedError"
    assert harness.page.evaluate("() => __karaokeHarness.microphoneCalls") == 1
    assert harness.page.evaluate("""async () => {
        try { await navigator.serviceWorker.register('/sw.js'); return 'escaped'; }
        catch (error) { return 'blocked'; }
    }""") == "blocked"
    assert harness.context.service_workers == []
    assert not any(request.url.endswith("/sw.js") for request in harness.requests)


def test_media_unknown_duration_and_late_source_events(karaoke_harness):
    harness = karaoke_harness
    harness.page.evaluate("""() => {
        window.metadataObservations = [];
        document.getElementById('player').addEventListener('loadedmetadata', event => {
            metadataObservations.push({id: event.target.id, src: event.target.currentSrc,
                                       duration: event.target.duration});
        });
    }""")
    first = harness.media("source", src="/audio/first.mp3")
    assert harness.media("snapshot")["duration"] == "NaN"
    harness.media("metadata", duration="infinity")
    assert harness.media("snapshot")["duration"] == "Infinity"
    harness.media("metadata", duration=0)
    assert harness.media("snapshot")["duration"] == 0
    second = harness.media("source", src="/audio/second.mp3")
    assert first != second
    harness.media("emit", type="loadedmetadata", source=first, duration=100)
    harness.media("advance", source=first, time=33)
    observed = harness.page.evaluate("() => metadataObservations.at(-1)")
    assert observed == {"id": "player", "src": first["src"], "duration": 100}
    snapshot = harness.media("snapshot")
    assert snapshot["generation"] == second["generation"]
    assert snapshot["src"] == second["src"]
    assert snapshot["duration"] == "NaN"
    assert snapshot["currentTime"] == 0
    harness.media("metadata", duration=40)
    harness.media("advance", time=10)
    assert harness.media("snapshot")["currentTime"] == 10
    assert harness.media("snapshot")["duration"] == 40


@pytest.mark.parametrize("error", [None, "NotAllowedError"])
def test_deferred_play_completion_cannot_mutate_new_source(karaoke_harness, error):
    harness = karaoke_harness
    first = harness.media("source", src="/audio/old.mp3")
    harness.media("queuePlay", outcome="defer")
    play_id = harness.media("play")
    assert harness.media("snapshot")["pendingPlays"] == [play_id]
    second = harness.media("source", src="/audio/new.mp3")
    harness.media("settlePlay", id=play_id, error=error)
    snapshot = harness.media("snapshot")
    assert snapshot["outcomes"][str(play_id)] == ("rejected" if error else "resolved")
    assert snapshot["pendingPlays"] == []
    assert snapshot["generation"] == second["generation"] != first["generation"]
    assert snapshot["paused"] is True
    assert snapshot["events"] == [{"type": "play", **first}]


def test_play_rejection_and_media_error_are_controllable(karaoke_harness):
    harness = karaoke_harness
    harness.media("source", src="/audio/rejected.mp3")
    harness.media("queuePlay", outcome="reject", error="NotSupportedError")
    play_id = harness.media("play")
    harness.media("error")
    harness.media("end")
    snapshot = harness.media("snapshot")
    assert snapshot["outcomes"][str(play_id)] == "rejected"
    assert snapshot["paused"] is True
    assert snapshot["ended"] is True
    assert [event["type"] for event in snapshot["events"]] == ["play", "error", "ended"]


def test_tts_responses_can_be_held_reordered_and_fail(karaoke_harness):
    harness = karaoke_harness
    late = harness.queue_tts(audio_url="/audio/late.mp3", defer=True)
    early = harness.queue_tts(audio_url="/audio/early.mp3")
    failed = harness.queue_tts(payload={"detail": "Simulated synthesis failure"}, status=503)
    harness.ignore_fetch_abort()
    harness.page.evaluate("""() => {
        window.fixtureReplies = [];
        window.oldRequest = new AbortController();
        const request = (text, signal) => fetch('/tts', {
            method: 'POST', headers: {'content-type': 'application/json'},
            body: JSON.stringify({text}), signal
        }).then(async response => fixtureReplies.push({status: response.status, body: await response.json()}));
        window.firstReply = request('held', oldRequest.signal);
        window.secondReply = request('immediate');
    }""")
    harness.page.wait_for_function("() => fixtureReplies.length === 1")
    assert late.request.post_data_json == {"text": "held"}
    assert early.request.post_data_json == {"text": "immediate"}
    assert late.route is not None
    harness.page.evaluate("() => oldRequest.abort()")
    late.release()
    harness.page.wait_for_function("() => fixtureReplies.length === 2")
    harness.page.evaluate("""async () => {
        const response = await fetch('/tts', {method: 'POST'});
        fixtureReplies.push({status: response.status, body: await response.json()});
    }""")
    assert failed.request is not None
    assert harness.page.evaluate("() => fixtureReplies") == [
        {"status": 200, "body": {"audio_url": "/audio/early.mp3"}},
        {"status": 200, "body": {"audio_url": "/audio/late.mp3"}},
        {"status": 503, "body": {"detail": "Simulated synthesis failure"}},
    ]
