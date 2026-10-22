"""SSE speech waits for a user turn; the existing media queue remains real."""

import pytest


@pytest.mark.parametrize("finish", ["end", "stop"])
def test_announcement_waits_for_thinking_and_answer_playback(karaoke_app, finish):
    app = karaoke_app()
    app.context.add_init_script("localStorage.setItem('herdr-brain-voice-engine', 'navegador');")
    held = []
    app.context.route(app.origin + "/ask", lambda route: held.append(route))
    app.load()
    page = app.page
    page.locator("#call-btn").click()
    page.locator("#text-input").evaluate("input => { input.value = 'Synthetic question'; }")
    page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/ask')")
    assert held
    app.event({
        "type": "transition", "pane_id": "w1:p2", "agent": "opencode", "status": "done",
        "label": "Background agent", "text": "Tests completed", "audio_url": "/audio/queued.mp3",
    })
    page.evaluate("() => Promise.resolve()")
    assert app.media("snapshot")["playCalls"] == 0
    # The queued announcement must not claim the player before the answer.
    speech_id = held[0].request.post_data_json["speech_request_id"]
    app.respond("/speech/" + speech_id + "/cancel", {"status": "cancelled"}, method="POST")
    held.pop().fulfill(json={"answer": "Synthetic answer", "audio_url": "/audio/answer.mp3"})
    page.wait_for_function("() => __karaokeHarness.media.snapshot().src.includes('/answer.mp3')")
    assert app.media("snapshot")["playCalls"] == 1
    if finish == "end":
        app.media("end")
    else:
        page.locator("#stop-audio").click()
    page.wait_for_function("() => __karaokeHarness.media.snapshot().src.includes('/queued.mp3')")
    assert app.media("snapshot")["playCalls"] == 2


def test_buffered_browser_speech_is_not_conversational_idle(karaoke_app):
    app = karaoke_app()
    app.context.add_init_script("localStorage.setItem('herdr-brain-voice-engine', 'navegador');")
    app.load()
    app.page.locator("#call-btn").click()
    app.page.evaluate("""() => {
        const result = Object.assign([{transcript: 'An active user utterance'}], {isFinal: false});
        __karaokeHarness.recognitions.at(-1).emit('result', {resultIndex: 0, results: [result]});
    }""")
    app.event({"type": "transition", "pane_id": "w1:p2", "status": "done",
               "label": "Background agent", "text": "Queued update", "audio_url": "/audio/queued.mp3"})
    app.page.evaluate("() => Promise.resolve()")
    assert app.media("snapshot")["playCalls"] == 0
