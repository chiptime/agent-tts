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
    # (floating exit button, Escape exits Zen, i18n labels, setZen);
    # then for the call continuation window, the silent Android speech
    # gate for the Browser engine and the "call transcription" settings
    # labels (index.html, app.js + both i18n); then for the glance
    # click-to-speech handover to the Web Speech engine (click on text
    # indexes the turn and plays in-browser, server audio stops); then for
    # the live call engine (stream-uplink.js script tag, the En vivo engine
    # and its fallback notices in app.js + both i18n).
    # Re-pinned after the agent-error UI and session-picker refresh changes.
    # app.js re-pinned for the push deep link (S5): a notification tap
    # opens the chat it is about (?pane_id= boot deep link consumed once
    # + the brain-open-pane worker message); sw.js rides the same change
    # but is not served (nor requested) in this harness. Includes the
    # concurrent live-engine app.js hunks on this tree.
    # Re-pinned again for the live-call drawer work (2026-11-17): the
    # stacked interim strip + the ✚ new-call button (index.html, app.js,
    # both i18n) and the 80dvh sheet diving behind the footer (index.html)
    # Re-pinned for parity-f7d (2026-11-19): the Cambiar modelo sheet
    # action and the context-window tail in app.js, the model-picker
    # chrome in dialogs.js, and both i18n..
    # Re-pinned once more for the Llamadas popup: the history left the
    # drawer for a 🕘-launched sheet (index.html + app.js; calls.js rides
    # unchanged) — then the sheet anchored at the viewport floor covering
    # the footer (index.html only).
    # Re-pinned on the merged main bytes for the formatted call-drawer
    # reader (/call-history/rendered: index.html, app.js, reader.js).
    # app.js re-pinned for the pane model in the herd row sub-line (parity F7a S3).
    # app.js, dialogs.js and both i18n re-pinned for parity F7b S1: the
    # multiselect checkbox toggles above the keypad (local selection
    # state, one explicit submit button posting {choices}).
    # app.js, dialogs.js and both i18n re-pinned again for parity F7b S3:
    # the free-text row opens an inline editor in the card (input + one
    # explicit send posting {option, text}, destructive second tap).
    # app.js, dialogs.js and both i18n re-pinned once more for parity
    # F7b S4: the wizard stepper (step metadata from the N/M marker,
    # Next/Back over local captures, review with Confirm/Edit, abort
    # card on drift).
    # index.html, app.js, dialogs.js and both i18n re-pinned for the model
    # picker card UX (2026-11-19): a search input over a bounded, scrolling
    # list, rows keyed by their ORIGINAL option index, the current model
    # highlighted (model/provider split riding the payload additively).
    # Re-pinned for parity-f7c S1 (the ✚ "Nueva sesión" sheet): the
    # header entry + sheet markup (index.html), the pure model + wiring
    # (app.js) and the newsession.* keys (both i18n). The sheet fetches
    # /session/roots and /operator/config only when OPENED, so boot-time
    # traffic is unchanged.
    # app.js re-pinned for parity-f7c S2 (idempotent launch): the sheet
    # mints a fresh request_id per Launch tap (mintRequestId in the pure
    # model + the field in the POST /session/new body). No other bytes.
    # Re-pinned for parity-f7c S3 (resume): the sheet's "Retomar" half —
    # harness picker from /sessions/recent's enabled list, per-harness
    # recent rows, one tap POSTing /session/resume with a fresh
    # request_id (resumeRows in the pure model + the wiring), the
    # .ns-resume chip style (index.html) and the newsession.resume*
    # keys (both i18n). The resume fetches ride readFetch on OPEN, so
    # boot-time traffic is unchanged.
    # Re-pinned for parity-f7c S1b (spaces): the folder picker's
    # "Espacios" group — every workspace from /session/roots' new
    # spaces array (spaceRows in the pure model, disabled entries with
    # a reason badge, workspace_id in the POST /session/new body) and
    # the newsession.folder_spaces / space_* keys (app.js + both i18n).
    # The spaces ride the same /session/roots OPEN fetch, so boot-time
    # traffic is unchanged.
    # Re-pinned for the header ✚ dedup (2026-11-19): #new-open left
    # the header and the conversation sheet's "Nueva conversación"
    # (#conv-new) became the launch-sheet opener — the mid-call
    # POST /reset survives on #call-new (index.html, app.js, and both
    # i18n for the now-dead herd.new_started/new_failed keys).
    # Re-pinned for parity-f7g S5 (the send tracker card): the card
    # markup + sheet styles + the send-track.js script tag (index.html)
    # and the new send-track.js module itself. app.js and both i18n
    # re-pinned in place below (tracker wiring, track.* keys).
    # Re-pinned for parity-crew-safety T1 (2026-11-20): the crew /herd
    # contract — refreshHerd unwraps {panes, peers}, remote panes render
    # with a qualified <host>:<pane> row id and a host chip, unreachable
    # peers render as informational rows (index.html styles), and both
    # i18n gain herd.peer_unreachable. app.js, index.html (/) and both
    # i18n re-pinned below.
    "/": "581ce9741fe02438a1ead1b1dd3c7ed3669e6cc1526e39da44a233fa9ef39ea0",
    # send-track.js re-pinned for the correction batch (f7g): the
    # per-receipt seq guard (a lower seq is a stale delivery, ignored),
    # a start for an already-known receipt never regresses it, and a
    # bounded per-pane memory of dismissed receipt ids (a dismissed
    # receipt stays dismissed; only a new id shows a card again).
    "/send-track.js": "21b8fba1b449b14e66cc295ddfe5f829b4b7786a0cf4d86ee1fc9270a247050b",
    # app.js re-pinned for the held-card visibility fix (F7d phone
    # report): renderPermission no longer re-hides the #pane-reply-keys
    # wrapper an idle pane's held picker card just opened (modalKeysPane
    # rides renderPending into the wrapper's toggle).
    # Re-pinned again for the full opencode catalog card (2026-11-19):
    # GET /pane/models on demand, the shared mountModelSearch builder,
    # catalog rows answering by model_name (+ provider_name).
    # Re-pinned for the verified opencode switch: a 502 model-picker
    # answer shows the server's detail, and a success carrying
    # ``next`` restarts the modal poll (opencode's Select variant).
    # Re-pinned for parity-f7g S5 (the send tracker card): the SSE
    # send.track feed, the receipts-poll convergence, the 1s tick
    # cadence and the per-pane map in the composer area.
    # Re-pinned for parity-f7e S1(2): the Stop capability — app.js
    # disables #pane-stop per the pane's can_stop /herd key with the
    # Spanish explanation, receipts.js gains stopButtonModel, and both
    # i18n gain stop.unverified.
    # Re-pinned for parity-f7e S3 (held Claude hook permissions): the
    # global #hook-permission card above the pane permission box
    # (index.html), its 5s-beat /hook/permissions poll + answer wiring
    # (app.js) and the holdpermission.* keys (both i18n).
    # Re-pinned for parity T4/S4 (session safety): the resume retry id
    # (nsModel.resumeRequestId + held newResumeRetry), the picked
    # workspace_id on POST /session/resume and the
    # newsession.err_resume_workspace key (both i18n).
    # Re-pinned for parity T8/S8 (sheetfix): the launch sheet opened from
    # the conversation sheet reuses that sheet's history entry
    # (replaceState handover) — the pending history.back() traversal
    # used to close the just-opened sheet.
    # app.js re-pinned for parity-w2 S3: the stop-failure toast model
    # (stopFailureToast pure block) branches on the server's stable
    # dialog_open reason.
    "/app.js": "bc9a7a8f64592005ef3b64a525561294f30a74a3d164009df196fc23a3dc3b9b",
    "/push.js": "e1acadd98d95420cf9924ea633b32d9ec5b51c8654c5e7df923c2bbc5547f52c",
    "/receipts.js": "df01a313d8f6101bd9601d5a2a65eb420df7791c417a09e2d0d8c269a36742b0",
    # dialogs.js re-pinned for the parity-f7d follow-up: MODEL_SWITCH_COMMANDS
    # gains pi (/switch, omp's own box on the same TUI) and opencode +
    # opencode2 (/models, live-verified on opencode 1.19.5) so the
    # "Cambiar modelo" affordance reaches those panes too. Re-pinned
    # again for the modal poll (F7d fix): createModelSwitchPoller +
    # modalViewUrl here, the /view?modal=1 watch and the held picker
    # card in app.js.
    "/dialogs.js": "c7c0b15e783d9c2cb10dcd5fcc07eb3029536303a4bb4158f9e93ce111e3fb26",
    "/herd-group.js": "de284c825e3c99aef07db2dd53198484ec8b7ceba94dd97dbbae0303560df399",
    "/toast.js": "cbccb72b22530ff8f83250bebd8d498ca20977db9536aa094aa759bb9632357e",
    "/reader.js": "b99edccdba5adb1c407ebc3064f7045214c0152677a1da2cfa2c388393792989",
    "/speech.js": "23b10e3ffe9b22c7480929cb33d12e4a0e3d378f49b841645ec670634a9fe709",
    "/announce.js": "e525a591e5268d2cbfc1d93956469b81da41fb3c87b17c43caccbc7ebd00e9df",
    # app.js and approval.js re-pinned for the T-07/T-08 spoken-dialog,
    # approval-binding and B1–B8 correction changes (gate speech id +
    # explanation receipts, clarify identity, item-aware playback proof);
    # no other static bytes changed.
    "/approval.js": "af8c8517ec62e9ee8bc82aa9804e2656c722c690164c07709c1e1e32f70c8c8d",
    "/consult.js": "fc2e4d18c206164fcec7a8baf2be1697e5b6d86152ed12c432169a70c1113bc4",
    "/endpointing.js": "7f5b6e58ecbae33f3134625fb242e6f5b5001c579714ad79cddb18f4044eb9f8",
    "/vad.js": "0bb7e5891c2da564aa1ab88b763a6d85381f374b0ed4c90e8eab498b4ffe2cad",
    # S4/S3 PWA unit: i18n runtime + dictionaries, settings sheet,
    # update notice, operator dock, offline-shell sw.js.
    "/dock.js": "8f41ef8e9b5365739f50071a3033bef1e75e242f56d72d2b13ccc78fe11134b6",
    "/i18n.js": "8bcd2ceb9f56109afaa0ec158e1a6f002799c4a983261b9d58cd38723c06719f",
    # Both i18n re-pinned for parity-f7g S5: the track.* keys (the
    # tracker card's Spanish lines and their English locale ride).
    # Re-pinned for parity T4/S4: newsession.err_resume_workspace.
    # Re-pinned for parity-w2 S3: stop.dialog_open (the localized
    # «Hay un diálogo abierto; respóndelo primero.» refusal copy).
    "/i18n/en.js": "45f4d1b63eca2ed9dc9deaad798a6dde36d94e1f05dd1fb63392f70a168b4b6b",
    "/i18n/es.js": "510a845bb0a589040975cf87960e43f205912de21fbee250c07593c0cb9a3d8d",
    "/settings.js": "1c2cf3ed92bf959c19ecde0107618b8d1ff0690d2c6fa7c842a2fdc551e05f72",
    "/updates.js": "7f87250cf6cd9c2b8944da632fe036d0c18a87a90f48f70b84e18bcd338cfa84",
    "/calls.js": "9b9ec591709d282c6e673770b2ffc1ed1562650104d8e8393126d186aa764165",
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
