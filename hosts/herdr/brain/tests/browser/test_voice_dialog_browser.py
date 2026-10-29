"""Voice dialog answering and spoken-approval binding through the real app.

Route-only KaraokeHarness scenarios (browser speech engine): the DOM, the
endpointer, the mic routing, the approval flow and the audio queue are the
PRODUCTION app.js/approval.js bytes; only the service endpoints and media
devices are fixture boundaries. These tests pin the S9 client contract
(dialog utterances ride /ask) and the S14 client contract (a spoken
«adelante» carries the explanation receipt only when its playback really
completed).
"""

import json
from urllib.parse import urlsplit


GATE = {
    "gate_id": "gate-voice-0001",
    "tool": "send_to_session",
    "agent": "opencode",
    "text": "arregla el bug del login",
    "pane_id": "w1:p9",
    "timeout_ms": 300000,
    "expires_in_s": 300,
}

ASK_WITH_GATE = {
    "answer": "He preparado los cambios en auth.py. ¿Se envía?",
    "audio_url": "/audio/fixture-0.mp3",
    "speech": {"id": "sp-echo-0000001", "status": "complete"},
    "approval": GATE,
}

CLARIFY = {
    "decision": "clarify",
    "answer": "Antes de confirmar: enviar a opencode «arregla el bug del login». Di sí para confirmar.",
    "audio_url": "/audio/clarify.mp3",
    "approval": None,
}


def voice_app(karaoke_app, ask_payload):
    """Browser-engine harness with one scripted /ask responder."""
    harness = karaoke_app()
    harness.context.add_init_script(
        "localStorage.setItem('herdr-brain-voice-engine', 'navegador');"
    )
    harness.respond("/ask", ask_payload, method="POST")
    harness.respond(
        "/approval/" + GATE["gate_id"] + "/resolve", CLARIFY, method="POST"
    )
    harness.load()
    harness.page.set_default_timeout(10000)
    harness.page.locator("#call-btn").click()
    harness.page.wait_for_function(
        "() => __karaokeHarness.recognitions.length > 0"
    )
    return harness


def say(harness, text, recognition_index):
    """One finalized browser-SR utterance through the real endpointer."""
    harness.page.evaluate(
        """([text, index]) => {
            const rec = __karaokeHarness.recognitions[index];
            rec.emit('result', {resultIndex: 0,
                results: [{isFinal: true, 0: {transcript: text}}]});
        }""",
        [text, recognition_index],
    )


def post_body(harness, path_suffix):
    """The LAST POST body sent to a path ending with the suffix."""
    for request in reversed(harness.requests):
        parsed = urlsplit(request.url)
        if request.method == "POST" and parsed.path.endswith(path_suffix):
            return json.loads(request.post_data)
    raise AssertionError("no POST to " + path_suffix)


def wait_post(harness, path_suffix):
    harness.page.wait_for_function(
        "suffix => __karaokeHarness.fetchCalls.some(path => path.endsWith(suffix))",
        arg=path_suffix,
    )


def play_through(harness):
    """Drives the native media seam through one queued item's lifecycle."""
    harness.media("metadata", duration=10)
    harness.media("end")


def test_adelante_after_the_full_explanation_carries_the_speech_receipt(karaoke_app):
    harness = voice_app(karaoke_app, ASK_WITH_GATE)

    say(harness, "qué está pasando", 0)
    wait_post(harness, "/ask")
    ask_body = post_body(harness, "/ask")
    # The explanation audio plays and FULLY ends before the user can speak.
    harness.page.wait_for_function(
        "() => !document.getElementById('player').paused"
    )
    play_through(harness)
    # The gate is armed and the call sits in confirming.
    harness.page.wait_for_function(
        "() => __karaokeHarness.recognitions.length > 1"
    )

    say(harness, "adelante", 1)
    wait_post(harness, "/approval/" + GATE["gate_id"] + "/resolve")
    resolve_body = post_body(harness, "/resolve")
    assert resolve_body["utterance"] == "adelante"
    # The receipt is the speech identity whose playback explained the gate.
    assert resolve_body["speech_request_id"] == ask_body["speech_request_id"]


def test_gate_explained_without_audio_carries_no_receipt(karaoke_app):
    """S14: when the gated answer never SPOKE (TTS degraded to text), the
    user heard nothing — the confirm word must carry a null receipt so the
    server clarifies (restates) instead of executing. The mic re-arms in
    confirming, so the confirm word really dispatches."""
    no_audio = dict(ASK_WITH_GATE, audio_url=None)
    harness = voice_app(karaoke_app, no_audio)

    say(harness, "qué está pasando", 0)
    wait_post(harness, "/ask")
    # No audio ever plays: the flow re-arms the mic under confirming.
    harness.page.wait_for_function(
        "() => __karaokeHarness.recognitions.length > 1"
    )
    assert harness.page.evaluate("() => document.getElementById('player').paused")

    say(harness, "adelante", 1)
    wait_post(harness, "/approval/" + GATE["gate_id"] + "/resolve")
    resolve_body = post_body(harness, "/resolve")
    assert resolve_body["speech_request_id"] is None

    # The clarify answer is spoken through the same queue…
    harness.page.wait_for_function(
        "() => document.getElementById('player').src.includes('clarify.mp3')"
    )
    play_through(harness)
    # …the gate stays live for the explicit confirmation round.
    assert harness.page.evaluate(
        "() => document.getElementById('approval-float').childElementCount"
    ) > 0


def test_dialog_choice_utterances_ride_the_ask_pipeline(karaoke_app):
    """S9 client contract: a spoken dialog choice is a normal turn — no
    second transport, no client-side key dispatch; the server's tool loop
    owns the mapping."""
    harness = voice_app(karaoke_app, {"answer": "Hecho.", "audio_url": None})

    say(harness, "selecciona la opción 2", 0)
    wait_post(harness, "/ask")
    ask_body = post_body(harness, "/ask")
    assert ask_body["text"] == "selecciona la opción 2"
