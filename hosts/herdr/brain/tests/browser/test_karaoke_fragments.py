"""SC-01..SC-06: actual app/Chromium, exclusively simulated API and media.

This proves browser integration, not audible alignment or physical/mobile UAT.
"""

import math
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect


ANSWER = "A sentence with several distinct words. \t\n" * 12
LONG_ANSWER = "Repeated sentence with several distinct words. \t\n" * 370
MAIN = "#conversation .turn.brain"


def turn(text, role="brain", ts="2026-01-01T00:00:01Z"):
    return {"role": role, "text": text, "ts": ts}


def app(karaoke_app, text=ANSWER, *, history=None, external=None):
    harness = karaoke_app(
        history=history if history is not None else [turn("  Question?\n", "user"), turn(text)],
        external=external,
    )
    harness.context.add_init_script(
        "localStorage.setItem('herdr-brain-voice-engine', 'navegador');"
    )
    harness.page.set_default_timeout(2000)
    harness.page.set_viewport_size({"width": 1000, "height": 1000})
    harness.load()
    expect(harness.page.locator(MAIN)).to_have_count(
        sum(record["role"] != "user" for record in (history or [turn(text)]))
    )
    harness.page.locator("#call-btn").click()
    return harness


def expected_plan(harness, text=ANSWER):
    # The unchanged production splitters are also available on the RED base.
    return harness.page.evaluate("""text => {
        let offset = 0;
        return Toast.splitForTts(text, 8000).map((text, pieceIndex) => {
            const chunks = Toast.chunkifyText(text);
            const piece = {text, pieceIndex, chunks, chunkOffset: offset};
            offset += chunks.length;
            return piece;
        });
    }""", text)


def chunks(harness, owner=0):
    controls = harness.page.locator(MAIN).nth(owner).locator(".karaoke-chunk")
    assert controls.count() > 0, "Main Brain answer must have inline karaoke controls"
    return controls


def select(harness, index=0, owner=0):
    chunks(harness, owner).nth(index).click()


def source(harness, suffix, previous=None):
    harness.page.wait_for_function(
        """([suffix, previous]) => {
            const snapshot = __karaokeHarness.media.snapshot();
            return new URL(snapshot.src || document.baseURI).pathname.endsWith(suffix) &&
                (!previous || snapshot.generation !== previous.generation);
        }""", arg=[suffix, previous]
    )
    return harness.media("capture")


def active(harness, index, popup, owner=0):
    controls = chunks(harness, owner)
    expect(harness.page.locator(".karaoke-active")).to_have_count(1)
    expect(controls.nth(index)).to_have_class("karaoke-chunk karaoke-active")
    expect(controls.nth(index)).to_have_attribute("aria-pressed", "true")
    assert controls.nth(index).get_attribute("data-ordinal") == str(index + 1)
    assert harness.page.locator("#toast").text_content() == "🔊 brain: " + popup


def idle(harness):
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    expect(harness.page.locator("#toast")).to_have_class("hidden")
    assert harness.page.locator(MAIN + " .replay-btn:disabled").count() == 0
    assert harness.page.locator(MAIN + " [aria-busy='true']").count() == 0


def ready(harness, index=0, owner=0, url="/audio/owned.mp3", duration=100):
    reply = harness.queue_tts(audio_url=url)
    select(harness, index, owner)
    source(harness, url)
    assert reply.request is not None
    harness.media("metadata", duration=duration)
    harness.page.wait_for_function("() => !__karaokeHarness.media.snapshot().paused")
    return reply


def test_sc01_raw_text_history_scope_and_live_rendering(karaoke_app):
    raw = ' \tThinking.\n\nFinal <script>window.injected = true</script> 😀\n```js\nlet x = "<b>";\n```\t '
    external = [{"role": "assistant", "text": "External <b>literal</b> answer"}]
    harness = karaoke_app(history=[turn("Question\tunchanged", "user"), turn(raw)], external=external)
    harness.respond("/call-history", {"turns": [turn("Question\tunchanged", "user"), turn(raw)],
                                     "has_more": True}, query=("before", "limit"))
    harness.context.add_init_script("localStorage.setItem('herdr-brain-voice-engine', 'navegador');")
    harness.load()
    harness.page.locator("#call-btn").click()
    controls = chunks(harness)
    body = harness.page.locator(MAIN + " > span:not(.who)")
    assert body.text_content() == raw
    assert "".join(controls.all_text_contents()) == raw
    assert body.locator("script, b, a, pre, code").count() == 0
    assert harness.page.evaluate("() => window.injected === undefined")
    assert harness.page.locator("#conversation .turn.user > span:not(.who)").text_content() == "Question\tunchanged"
    assert harness.page.locator("#conversation .turn.user .karaoke-chunk").count() == 0
    expect(harness.page.locator("#glance-turns .replay-btn")).to_have_count(1)
    assert harness.page.locator("#glance-turns .karaoke-chunk").count() == 0
    external_before = harness.page.locator("#glance-turns").inner_html()
    harness.respond("/call-history", {"turns": [turn(raw, ts="2025-12-01T00:00:00Z")],
                                     "has_more": False}, query=("before", "limit"))
    harness.page.locator(".history-more").click()
    expect(harness.page.locator(MAIN)).to_have_count(2)
    assert harness.page.locator(MAIN + " > span:not(.who)").all_text_contents() == [raw, raw]
    assert chunks(harness, 0).first.get_attribute("data-ordinal") == "1"
    assert chunks(harness, 1).first.get_attribute("data-ordinal") == "1"
    harness.respond("/ask", {"answer": raw}, method="POST")
    harness.page.locator("#text-input").evaluate("input => { input.value = 'Live question'; }")
    harness.page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    expect(harness.page.locator(MAIN)).to_have_count(3)
    assert chunks(harness, 2).count() == controls.count()
    assert harness.page.locator(MAIN).nth(2).locator("span:not(.who)").first.text_content() == raw
    assert harness.page.locator("#glance-turns").inner_html() == external_before
    assert harness.page.locator("#conversation .turn.user .karaoke-chunk").count() == 0


@pytest.mark.parametrize("raw", ["", " \t\n\r\u00a0 "])
def test_sc01_whitespace_only_has_no_empty_audio_control(karaoke_app, raw):
    harness = app(karaoke_app, raw)
    assert harness.page.locator(MAIN + " > span:not(.who)").text_content() == raw
    assert harness.page.locator(MAIN + " .karaoke-chunk, " + MAIN + " .replay-btn").count() == 0
    assert "/tts" not in harness.page.evaluate("() => __karaokeHarness.fetchCalls")


def test_sc03_click_before_synthesis_waits_for_valid_metadata(karaoke_app):
    harness = app(karaoke_app)
    piece = expected_plan(harness)[0]
    reply = harness.queue_tts(audio_url="/audio/held.mp3", defer=True)
    select(harness, 2)
    active(harness, 2, piece["chunks"][2])
    assert harness.media("snapshot")["playCalls"] == 0
    assert reply.request.post_data_json == {"text": piece["text"]}
    reply.release()
    source(harness, "/held.mp3")
    for duration in ["unknown", 0, "infinity"]:
        harness.media("metadata", duration=duration)
        assert harness.media("snapshot")["playCalls"] == 0
        assert harness.media("snapshot")["currentTime"] == 0
    harness.media("metadata", duration=100)
    snapshot = harness.media("snapshot")
    assert snapshot["playCalls"] == 1
    assert snapshot["currentTime"] == pytest.approx(2 / len(piece["chunks"]) * 100)
    active(harness, 2, piece["chunks"][2])


def test_sc03_same_piece_seek_paused_resume_and_escuchar(karaoke_app):
    harness = app(karaoke_app)
    piece = expected_plan(harness)[0]
    ready(harness, 1)
    select(harness, 3)
    # Ready media must resume without another synthesis or metadata event.
    assert harness.media("snapshot")["currentTime"] == pytest.approx(3 / len(piece["chunks"]) * 100)
    assert harness.media("snapshot")["paused"] is False
    harness.media("pause")
    select(harness, 2)
    assert harness.media("snapshot")["currentTime"] == pytest.approx(2 / len(piece["chunks"]) * 100)
    assert harness.media("snapshot")["paused"] is False
    active(harness, 2, piece["chunks"][2])
    harness.page.locator(MAIN + " .replay-btn").click()
    assert harness.media("snapshot")["currentTime"] == 0
    active(harness, 0, piece["chunks"][0])
    assert sum(r.url.endswith("/tts") for r in harness.requests) == 1


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_sc03_keyboard_focus_name_and_selection(karaoke_app, key):
    harness = app(karaoke_app)
    control = chunks(harness).nth(2)
    assert control.get_attribute("role") == "button"
    assert control.get_attribute("aria-label").startswith("Escuchar fragmento 3")
    assert control.get_attribute("aria-pressed") == "false"
    harness.queue_tts(audio_url="/audio/keyboard.mp3")
    control.focus()
    # Enter through real keyboard navigation, not pointer-mode programmatic
    # focus, so Chromium's native :focus-visible heuristic is exercised.
    harness.page.keyboard.press("Tab")
    harness.page.keyboard.press("Shift+Tab")
    assert control.evaluate("el => el === document.activeElement")
    assert control.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
    control.press(key)
    source(harness, "/keyboard.mp3")
    harness.media("metadata", duration=60)
    active(harness, 2, expected_plan(harness)[0]["chunks"][2])


def test_sc02_later_piece_jump_offset_and_clamped_progress(karaoke_app):
    harness = app(karaoke_app, LONG_ANSWER)
    plan = expected_plan(harness, LONG_ANSWER)
    piece = plan[1]
    assert len(plan) >= 3
    assert chunks(harness).count() == sum(len(p["chunks"]) for p in plan)
    assert "".join(chunks(harness).all_text_contents()) == LONG_ANSWER
    reply = ready(harness, piece["chunkOffset"] + 1, duration=80)
    assert reply.request.post_data_json == {"text": piece["text"]}
    for time in [-1, 0, 40, 80, 160]:
        harness.media("advance", time=time)
        local = max(0, min(len(piece["chunks"]) - 1, math.floor(time / 80 * len(piece["chunks"]))))
        active(harness, piece["chunkOffset"] + local, piece["chunks"][local])
    assert sum(r.url.endswith("/tts") for r in harness.requests) == 1


def test_sc02_one_window_piece_boundary_and_terminal_end(karaoke_app):
    raw = "x" * 8001
    harness = app(karaoke_app, raw)
    plan = expected_plan(harness, raw)
    assert [len(p["chunks"]) for p in plan] == [1, 1]
    ready(harness)
    harness.queue_tts(audio_url="/audio/tail.mp3")
    harness.media("advance", time=90)
    active(harness, 0, plan[0]["chunks"][0])
    harness.media("end")
    source(harness, "/tail.mp3")
    harness.media("metadata", duration=10)
    harness.media("advance", time=20)
    active(harness, 1, plan[1]["chunks"][0])
    harness.media("end")
    idle(harness)


def test_sc05_hidden_popup_does_not_freeze_inline_progress(karaoke_app):
    harness = app(karaoke_app)
    ready(harness)
    harness.page.locator("#toast").evaluate("el => el.classList.add('hidden')")
    piece = expected_plan(harness)[0]
    harness.media("advance", time=50)
    index = len(piece["chunks"]) // 2
    expect(chunks(harness).nth(index)).to_have_class("karaoke-chunk karaoke-active")
    assert harness.page.locator("#toast").text_content() == "🔊 brain: " + piece["chunks"][index]
    expect(harness.page.locator("#toast")).to_have_class("hidden")


def test_sc04_equal_text_a_b_a_rejects_late_synthesis(karaoke_app):
    harness = app(karaoke_app, history=[turn(ANSWER), turn(ANSWER)])
    harness.ignore_fetch_abort()
    first = harness.queue_tts(audio_url="/audio/obsolete-a.mp3", defer=True)
    second = harness.queue_tts(audio_url="/audio/obsolete-b.mp3", defer=True)
    third = harness.queue_tts(audio_url="/audio/current-a.mp3")
    select(harness, 1, 0)
    select(harness, 2, 1)
    select(harness, 3, 0)
    source(harness, "/current-a.mp3")
    first.release()
    second.release()
    harness.media("metadata", duration=100)
    active(harness, 3, expected_plan(harness)[0]["chunks"][3], 0)
    assert third.request is not None
    harness.media("end")
    idle(harness)
    assert harness.media("snapshot")["playCalls"] == 1
    assert not any(urlsplit(play["src"]).path.endswith(("/obsolete-a.mp3", "/obsolete-b.mp3"))
                   for play in harness.media("snapshot")["plays"])


@pytest.mark.parametrize("completion", [None, "NotAllowedError"])
@pytest.mark.parametrize("shared_url", [False, True])
def test_sc04_old_source_metadata_events_and_play_completion(karaoke_app, completion, shared_url):
    harness = app(karaoke_app, history=[turn(ANSWER), turn("Different answer. " * 20)])
    harness.media("queuePlay", outcome="defer")
    ready(harness, 1, url="/audio/old.mp3")
    old = harness.media("capture")
    play_id = harness.media("snapshot")["pendingPlays"][0]
    replacement_url = "/audio/old.mp3" if shared_url else "/audio/new.mp3"
    harness.queue_tts(audio_url=replacement_url)
    select(harness, 2, 1)
    current = source(harness, replacement_url, previous=old)
    harness.media("metadata", source=old, duration=200)
    for event in ["timeupdate", "pause", "ended", "error"]:
        harness.media("emit", type=event, source=old, time=190)
    harness.media("settlePlay", id=play_id, error=completion)
    assert harness.media("snapshot")["currentTime"] == 0
    assert harness.media("snapshot")["playCalls"] == 1
    assert harness.media("capture") == current
    harness.media("metadata", duration=60)
    piece = expected_plan(harness, "Different answer. " * 20)[0]
    active(harness, 2, piece["chunks"][2], 1)
    assert "No pude" not in harness.page.locator("#banner").text_content()


@pytest.mark.parametrize("action", ["stop", "reset", "detach"])
def test_sc04_retire_while_synthesis_pending(karaoke_app, action):
    harness = app(karaoke_app)
    harness.ignore_fetch_abort()
    reply = harness.queue_tts(audio_url="/audio/retired.mp3", defer=True)
    select(harness, 2)
    if action == "stop":
        harness.page.locator("#stop-audio").click()
    elif action == "reset":
        harness.page.locator("#conv-new").evaluate("el => el.click()")
    else:
        harness.page.locator(MAIN).evaluate("el => el.remove()")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    reply.release()
    assert harness.media("snapshot")["playCalls"] == 0
    assert not urlsplit(harness.media("snapshot")["src"]).path.endswith("/retired.mp3")
    if action != "reset":
        idle(harness)
    else:
        expect(harness.page.locator("#conversation .turn")).to_have_count(0)


@pytest.mark.parametrize("action", ["stop", "reset", "detach"])
def test_sc05_retire_while_metadata_and_play_are_pending(karaoke_app, action):
    harness = app(karaoke_app)
    harness.media("queuePlay", outcome="defer")
    ready(harness)
    old = harness.media("capture")
    play_id = harness.media("snapshot")["pendingPlays"][0]
    if action == "stop":
        harness.page.locator("#stop-audio").click()
    elif action == "reset":
        harness.page.locator("#conv-new").evaluate("el => el.click()")
    else:
        harness.page.locator(MAIN).evaluate("el => el.remove()")
    harness.media("metadata", source=old, duration=200)
    harness.media("emit", type="timeupdate", source=old, time=199)
    harness.media("settlePlay", id=play_id, error="NotAllowedError")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    assert "No pude" not in harness.page.locator("#banner").text_content()
    assert harness.media("snapshot")["paused"] is True


@pytest.mark.parametrize("terminal", ["end", "error", "rejection", "error-first", "rejection-first"])
def test_sc05_terminal_cleanup_and_failure_twins(karaoke_app, terminal):
    harness = app(karaoke_app)
    if terminal in ("rejection", "error-first", "rejection-first"):
        harness.media("queuePlay", outcome="defer")
    ready(harness, 1)
    old = harness.media("capture")
    if terminal in ("end", "error"):
        harness.media(terminal)
    else:
        play_id = harness.media("snapshot")["pendingPlays"][0]
        if terminal == "error-first":
            harness.media("error")
        harness.media("settlePlay", id=play_id, error="NotAllowedError")
        if terminal == "rejection-first":
            harness.media("emit", type="error", source=old)
    idle(harness)
    assert harness.page.locator(MAIN + " > span:not(.who)").text_content() == ANSWER
    if terminal != "end":
        assert "parte 1" in harness.page.locator("#banner").text_content()
        expect(harness.page.locator("#banner")).to_be_visible()
    # The restored button really starts a fresh session.
    harness.queue_tts(audio_url="/audio/retry.mp3")
    harness.page.locator(MAIN + " .replay-btn").click()
    source(harness, "/retry.mp3")
    active(harness, 0, expected_plan(harness)[0]["chunks"][0])


def test_sc05_failed_middle_piece_keeps_global_indexes(karaoke_app):
    harness = app(karaoke_app, LONG_ANSWER)
    plan = expected_plan(harness, LONG_ANSWER)
    ready(harness)
    failed = harness.queue_tts(status=503)
    later = harness.queue_tts(audio_url="/audio/after-gap.mp3")
    harness.media("end")
    source(harness, "/after-gap.mp3")
    assert failed.request.post_data_json == {"text": plan[1]["text"]}
    assert later.request.post_data_json == {"text": plan[2]["text"]}
    assert "parte 2" in harness.page.locator("#banner").text_content()
    harness.media("metadata", duration=50)
    harness.media("advance", time=25)
    local = len(plan[2]["chunks"]) // 2
    active(harness, plan[2]["chunkOffset"] + local, plan[2]["chunks"][local])


def announcement(harness, name):
    harness.event({"type": "transition", "pane_id": "%fixture", "agent": "fixture",
                   "status": name, "label": "fixture", "text": "Unrelated " + name,
                   "audio_url": "/audio/" + name + ".mp3"})


def test_sc05_owned_supersession_stop_preserves_unrelated_queue(karaoke_app):
    harness = app(karaoke_app, history=[turn(ANSWER), turn("Other answer. " * 20)])
    ready(harness)
    announcement(harness, "foreign-one")
    announcement(harness, "foreign-two")
    harness.queue_tts(audio_url="/audio/replacement.mp3")
    select(harness, 1, 1)
    source(harness, "/replacement.mp3")
    harness.media("metadata", duration=100)
    harness.page.locator("#stop-audio").click()
    source(harness, "/foreign-one.mp3")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    harness.media("end")
    source(harness, "/foreign-two.mp3")
    harness.media("end")
    idle(harness)
    assert harness.page.locator("#conversation .turn.announce .karaoke-chunk").count() == 0


@pytest.mark.parametrize("streamed", [False, True])
def test_sc05_automatic_speech_preemption_cancellation_and_mic_recovery(karaoke_app, streamed):
    harness = app(karaoke_app)
    chunks(harness)
    held_segments = []
    stream_requests = []
    # Route the real /ask request with its client-minted cancellation identity.
    def ask_reply(route):
        body = route.request.post_data_json
        job = body["speech_request_id"]
        harness.respond("/speech/" + job + "/cancel", {"status": "cancelled"}, method="POST")
        if streamed:
            def stream_reply(route):
                stream_requests.append(route.request)
                after = int(parse_qs(urlsplit(route.request.url).query)["after"][0])
                if after == 1:
                    held_segments.append(route)
                else:
                    route.fulfill(json={"seq": after + 1, "audio_url":
                                        "/audio/automatic.mp3" if after == -1 else "/audio/automatic-queued.mp3"})

            harness.context.route(harness.origin + "/speech/" + job + "/next?*", stream_reply)
            payload = {"answer": ANSWER, "speech": {"status": "delivering"}}
        else:
            payload = {"answer": ANSWER, "audio_url": "/audio/automatic.mp3"}
        route.fulfill(json=payload)

    harness.context.route(harness.origin + "/ask", ask_reply)
    harness.page.locator("#text-input").evaluate("el => { el.value = 'Automatic question'; }")
    harness.page.locator("#text-fallback").evaluate("el => el.requestSubmit()")
    source(harness, "/automatic.mp3")
    if streamed:
        # Consume segment zero to open the existing bounded prefetch slot;
        # segment one now owns playback and segment two's reply is held.
        harness.media("end")
        source(harness, "/automatic-queued.mp3")
        harness.page.wait_for_function("() => __karaokeHarness.fetchCalls.filter(path => path.endsWith('/next')).length === 3")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    announcement(harness, "preserved")
    harness.queue_tts(audio_url="/audio/aligned.mp3")
    select(harness, 2, 1)
    source(harness, "/aligned.mp3")
    if streamed:
        assert len(stream_requests) == 3
        assert len(held_segments) == 1
        held_segments[0].fulfill(json={"seq": 2, "audio_url": "/audio/late-segment.mp3"})
    harness.media("metadata", duration=100)
    active(harness, 2, expected_plan(harness)[0]["chunks"][2], 1)
    cancels = [r for r in harness.requests if r.url.endswith("/cancel")]
    assert len(cancels) == 1
    assert cancels[0].method == "POST"
    assert cancels[0].post_data_json["speech_cancel_token"]
    harness.media("end")
    source(harness, "/preserved.mp3")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    harness.media("end")
    idle(harness)
    plays = harness.media("snapshot")["plays"]
    assert sum(urlsplit(play["src"]).path.endswith("/automatic-queued.mp3") for play in plays) == (1 if streamed else 0)
    assert not any(urlsplit(play["src"]).path.endswith("/late-segment.mp3") for play in plays)
    assert harness.page.locator("#pill-main").text_content() == "● Escuchando ▲"
    assert harness.page.evaluate("() => __karaokeHarness.recognitions.length") >= 2
    assert harness.page.evaluate("() => __karaokeHarness.microphoneCalls") == 0


def test_sc05_foreign_audio_does_not_keep_owned_marker(karaoke_app):
    harness = app(karaoke_app)
    ready(harness)
    announcement(harness, "after-owned")
    harness.media("end")
    source(harness, "/after-owned.mp3")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    assert "Unrelated after-owned" in harness.page.locator("#toast").text_content()


def test_sc04_late_ask_answer_cannot_revive_audio_or_microphone(karaoke_app):
    harness = app(karaoke_app)
    held_ask = []

    def hold_ask(route):
        body = route.request.post_data_json
        harness.respond("/speech/" + body["speech_request_id"] + "/cancel",
                        {"status": "cancelled"}, method="POST")
        held_ask.append(route)

    harness.context.route(harness.origin + "/ask", hold_ask)
    harness.page.locator("#text-input").evaluate("el => { el.value = 'Held question'; }")
    harness.page.locator("#text-fallback").evaluate("el => el.requestSubmit()")
    harness.page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/ask')")
    pending = harness.queue_tts(audio_url="/audio/chosen.mp3", defer=True)
    select(harness, 2)
    assert len(held_ask) == 1
    recognition_count = harness.page.evaluate("() => __karaokeHarness.recognitions.length")
    held_ask[0].fulfill(json={"answer": "Late automatic answer", "audio_url": "/audio/obsolete-ask.mp3"})
    expect(harness.page.locator(MAIN)).to_have_count(2)
    active(harness, 2, expected_plan(harness)[0]["chunks"][2])
    assert harness.page.evaluate("() => __karaokeHarness.recognitions.length") == recognition_count
    assert harness.media("snapshot")["playCalls"] == 0
    pending.release()
    source(harness, "/chosen.mp3")
    harness.media("metadata", duration=100)
    harness.media("end")
    idle(harness)
    assert not any(urlsplit(play["src"]).path.endswith("/obsolete-ask.mp3") for play in harness.media("snapshot")["plays"])


@pytest.mark.parametrize("action", ["stop", "reset", "detach"])
def test_sc04_retire_before_metadata_arrives(karaoke_app, action):
    harness = app(karaoke_app)
    harness.queue_tts(audio_url="/audio/no-metadata.mp3")
    select(harness, 2)
    old = source(harness, "/no-metadata.mp3")
    assert harness.media("snapshot")["playCalls"] == 0
    if action == "stop":
        harness.page.locator("#stop-audio").click()
    elif action == "reset":
        harness.page.locator("#conv-new").evaluate("el => el.click()")
    else:
        harness.page.locator(MAIN).evaluate("el => el.remove()")
    harness.media("metadata", source=old, duration=100)
    harness.media("emit", type="error", source=old)
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    assert harness.media("snapshot")["playCalls"] == 0
    assert "No pude" not in harness.page.locator("#banner").text_content()


def test_sc05_preempted_unrelated_current_item_resumes_once(karaoke_app):
    harness = app(karaoke_app)
    announcement(harness, "interrupted")
    source(harness, "/interrupted.mp3")
    announcement(harness, "waiting")
    reply = harness.queue_tts(audio_url="/audio/jump.mp3", defer=True)
    select(harness, 2)
    assert harness.media("snapshot")["paused"] is True
    active(harness, 2, expected_plan(harness)[0]["chunks"][2])
    reply.release()
    source(harness, "/jump.mp3")
    harness.media("metadata", duration=100)
    harness.media("end")
    source(harness, "/interrupted.mp3")
    harness.media("end")
    source(harness, "/waiting.mp3")
    harness.media("end")
    idle(harness)
    assert harness.page.locator("#conversation .turn.announce").count() == 2


@pytest.mark.parametrize("completion", [None, "NotAllowedError"])
def test_sc05_resumed_foreign_item_rejects_its_old_play_completion(karaoke_app, completion):
    harness = app(karaoke_app)
    harness.media("queuePlay", outcome="defer")
    announcement(harness, "resumed")
    old = source(harness, "/resumed.mp3")
    play_id = harness.media("snapshot")["pendingPlays"][0]
    announcement(harness, "still-queued")
    ready(harness, 2)
    harness.media("end")
    current = source(harness, "/resumed.mp3", previous=old)
    harness.media("settlePlay", id=play_id, error=completion)
    assert harness.media("capture") == current
    assert harness.media("snapshot")["paused"] is False
    harness.media("emit", type="ended", source=old)
    harness.media("emit", type="error", source=old)
    assert harness.media("capture") == current
    harness.media("end")
    source(harness, "/still-queued.mp3")
    harness.media("end")
    idle(harness)


def test_sc01_external_replay_keeps_the_unaligned_compatibility_path(karaoke_app):
    external = [{"role": "assistant", "text": "External unchanged answer"}]
    harness = app(karaoke_app, external=external)
    chunks(harness)
    expect(harness.page.locator("#glance-turns .replay-btn")).to_have_count(1)
    reply = harness.queue_tts(audio_url="/audio/external.mp3")
    harness.page.locator("#glance-turns .replay-btn").evaluate("el => el.click()")
    source(harness, "/external.mp3")
    assert reply.request.post_data_json == {"text": external[0]["text"]}
    assert harness.media("snapshot")["playCalls"] == 1
    assert harness.page.locator("#glance-turns .karaoke-chunk, .karaoke-active").count() == 0
    harness.media("end")
    expect(harness.page.locator("#glance-turns .replay-btn")).to_have_text("🔊 Escuchar")


@pytest.mark.parametrize("operation", ["selection", "modified", "contextmenu", "drag", "longpress", "link", "code", "native"])
def test_sc06_native_interaction_does_not_select(karaoke_app, operation):
    harness = app(karaoke_app)
    control = chunks(harness).first
    if operation == "selection":
        control.evaluate("""el => {
            const range = document.createRange(); range.selectNodeContents(el);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            el.click();
        }""")
        assert harness.page.evaluate("() => !getSelection().isCollapsed")
    elif operation == "modified":
        control.click(modifiers=["Control"])
    elif operation == "contextmenu":
        control.click(button="right")
        control.evaluate("el => el.click()")
    elif operation == "drag":
        control.dispatch_event("pointerdown", {"pointerId": 1, "clientX": 20, "clientY": 20, "button": 0})
        control.dispatch_event("pointermove", {"pointerId": 1, "clientX": 45, "clientY": 20})
        control.dispatch_event("pointerup", {"pointerId": 1, "clientX": 45, "clientY": 20})
        control.evaluate("el => el.click()")
    elif operation == "longpress":
        control.click(delay=600)
    else:
        # No rich renderer exists. Exercise exclusion with a native descendant
        # inserted by the test, not by interpreting untrusted answer markup.
        tag = {"link": "a", "code": "code", "native": "button"}[operation]
        control.evaluate("""(el, tag) => {
            const native = document.createElement(tag);
            native.textContent = 'Native operation';
            if (tag === 'a') native.href = '#native-link';
            el.appendChild(native); native.click();
        }""", tag)
        if tag == "a":
            assert harness.page.url.endswith("#native-link")
    expect(harness.page.locator(".karaoke-active")).to_have_count(0)
    assert "/tts" not in harness.page.evaluate("() => __karaokeHarness.fetchCalls")
