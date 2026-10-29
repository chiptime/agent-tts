"""Independent S10 public-state queue and idle recovery probes."""
import json
import pytest
from tests.browser.test_karaoke_fragments import app, ready, source


def announce(h, name="one"):
    h.event({"type": "transition", "pane_id": "w1:" + name, "status": "done",
             "label": name, "text": "Background " + name, "audio_url": "/audio/" + name + ".mp3"})


def no_actions(h):
    assert not [r for r in h.requests if r.method == "POST" and "/approval/" in r.url]


@pytest.mark.parametrize("kind", ["owned", "external"])
@pytest.mark.parametrize("finish", ["end", "stop"])
def test_manual_idle_drains_foreign_fifo(karaoke_app, kind, finish):
    h = app(karaoke_app, external=[{"role": "assistant", "text": "External replay"}])
    if kind == "owned":
        ready(h)
    else:
        h.queue_tts(audio_url="/audio/external.mp3")
        h.page.locator("#glance-turns .replay-btn").evaluate("el => el.click()")
        source(h, "/external.mp3")
    announce(h, "one")
    announce(h, "two")
    assert h.media("snapshot")["playCalls"] == 1
    if finish == "end":
        h.media("end")
    else:
        h.page.locator("#stop-audio").click()
    source(h, "/one.mp3")
    h.media("end")
    source(h, "/two.mp3")
    h.media("end")
    h.page.wait_for_function("() => document.querySelector('#pill-main').textContent === '● Escuchando ▲'")
    assert h.media("snapshot")["playCalls"] == 3
    no_actions(h)


def test_paused_call_defers_until_resume(karaoke_app):
    h = app(karaoke_app)
    h.page.locator("#pause-btn").click()
    announce(h)
    h.page.wait_for_timeout(600)
    assert h.media("snapshot")["playCalls"] == 0
    records = json.loads(h.page.evaluate("localStorage.getItem('herdr.speech.pending.v1')"))["records"]
    assert any(r["pane_id"] == "w1:one" for r in records)
    h.page.locator("#pause-btn").click()
    source(h, "/one.mp3")
    no_actions(h)


def test_buffered_speech_survives_idle_pump(karaoke_app):
    h = app(karaoke_app)
    h.page.evaluate("""() => {
      const result = Object.assign([{transcript: 'Still talking'}], {isFinal: false});
      __karaokeHarness.recognitions.at(-1).emit('result', {resultIndex: 0, results: [result]});
    }""")
    announce(h)
    h.page.wait_for_timeout(250)
    assert h.media("snapshot")["playCalls"] == 0
    no_actions(h)


@pytest.mark.parametrize("finish", ["end", "stop"])
def test_streaming_gap_reserves_turn(karaoke_app, finish):
    from urllib.parse import parse_qs, urlsplit
    h = app(karaoke_app)
    held = []
    def ask(route):
        sid = route.request.post_data_json["speech_request_id"]
        h.respond("/speech/" + sid + "/cancel", {"status": "cancelled"}, method="POST")
        def segment(route):
            query = parse_qs(urlsplit(route.request.url).query)
            if query["after"] == ["0"]:
                held.append(route)
            else:
                route.fulfill(json={"wait": True, "status": "complete"})
        h.context.route(h.origin + "/speech/" + sid + "/next?*", segment)
        route.fulfill(json={"answer": "First. Last.", "audio_url": "/audio/first.mp3",
                            "speech": {"id": sid, "status": "delivering", "first_seq": 0, "first_is_final": False}})
    h.context.route(h.origin + "/ask", ask)
    h.page.locator("#text-input").evaluate("el => { el.value = 'Question'; }")
    h.page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    source(h, "/first.mp3")
    announce(h)
    h.media("end")
    h.page.wait_for_timeout(600)
    assert h.media("snapshot")["playCalls"] == 1
    assert held
    held.pop(0).fulfill(json={"seq": 1, "audio_url": "/audio/last.mp3", "is_final": True})
    source(h, "/last.mp3")
    if finish == "stop":
        h.page.locator("#stop-audio").click()
    else:
        h.media("end")
    source(h, "/one.mp3")
    no_actions(h)


def test_captured_barge_speech_beats_idle_microtask(audio_capture_context):
    from tests.browser.test_barge_in import BargeInApp, detection_frame, ORIGIN
    h = BargeInApp(audio_capture_context)
    held = []
    def transcribe(route):
        if h.asks:
            held.append(route)
        else:
            route.fulfill(json={"text": "First question"})
    h.context.route(ORIGIN + "/transcribe", transcribe)
    try:
        h.load()
        h.wait_playing()
        h.emit_announcement()
        h.wait_pause()
        h.page.wait_for_function("() => __barge.transcribes >= 2")
        p = h.probe()
        frame, pause = detection_frame(p)
        assert pause - frame["t"] < 100
        assert len([e for e in p["events"] if e["type"] == "playing"]) == 1
        assert any(r["pane_id"] == "w1:p2" for r in p["pending"]["records"])
        assert h.approval_posts == []
    finally:
        audio_capture_context.close()
        assert h.blocked == []
        assert h.errors == []


def test_confirmation_does_not_yield_to_background(karaoke_app):
    from tests.browser.test_voice_dialog_browser import GATE
    h = app(karaoke_app)
    h.respond("/ask", {"answer": "A proposed action", "audio_url": "/audio/explain.mp3", "approval": GATE}, method="POST")
    h.page.locator("#text-input").evaluate("el => { el.value = 'Propose work'; }")
    h.page.locator("#text-fallback").evaluate("el => el.requestSubmit()")
    source(h, "/explain.mp3")
    h.media("end")
    announce(h)
    h.page.wait_for_timeout(600)
    assert h.media("snapshot")["playCalls"] == 1
    assert h.page.locator("#approval-float").evaluate("el => el.childElementCount") > 0
    no_actions(h)
