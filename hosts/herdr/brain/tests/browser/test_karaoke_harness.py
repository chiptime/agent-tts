"""Foundation proof only: real Chromium/DOM, simulated services and media."""

import hashlib
import json
from importlib.metadata import version
import subprocess
import sys

import pytest


# Original KARAOKE-B0 static inputs, not instrumented or reconstructed scripts.
EXPECTED_DIGESTS = {
    "/": "b9947bc4c36103b03aa3ed11957acf40cebd66936892dbd3a2f875398818724c",
    "/app.js": "f282c84fb06a0271fe6ffa0a08fe42fad1e4683e4091a83ecb1e452b6bfd63b7",
    "/toast.js": "6c306cbe005036b7d697af7c3a6faa1fa5b90a6b444ee23176516b197c8df7e3",
    "/reader.js": "855d2cf588de36e89c0ca76cd60ddf063ff05bb5d9ba7daf7cff02c2051f42dd",
    "/speech.js": "785b966f634ffaaa70d92a8bafbf9b8a13c658617de2707d99b80736142823e6",
    "/announce.js": "b56e0152c4369bbab32177311340882123d75a2373ab2f786d2f5b60b5a500fb",
    "/approval.js": "b982f0ca6172380dcdb98f4d2841ac40c11fd691749429ca9424bb268c6b335a",
    "/consult.js": "fc2e4d18c206164fcec7a8baf2be1697e5b6d86152ed12c432169a70c1113bc4",
    "/endpointing.js": "632421b05ffff5d1d84d7a248719692b835324492a858b19901541f5ea51061d",
    "/vad.js": "03d7200920183a0e77a8065d6ff845d3384b7d699f572018547873e42aa287a8",
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
    assert "herdr_brain.server" not in sys.modules

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
