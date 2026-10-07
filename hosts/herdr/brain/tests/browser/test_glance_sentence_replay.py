"""External rendered-reader replay: intercepted APIs, no provider or physical UAT."""

from urllib.parse import quote

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect

from test_karaoke_fragments import announcement, source
from test_karaoke_real_media import RealMediaApp, real_media_browser


PANE = "fixture:reader"
SESSION = "fixture-session"
SURFACE = "#glance-turns .gturn.assistant"
CONTENT = SURFACE + " .reader-content"
EARLIER = "Earlier phrase must not replay."
SELECTED = "Read this emphasized ending."
SUFFIX = SELECTED + "\nNext phrase.\nOne\nTwo items\nA B\nprint(\"safe\")\nFinal tail."
RAW = EARLIER + " " + SELECTED + " Next phrase. One Two items A B print(\"safe\") Final tail."
FULL = EARLIER + " " + SUFFIX
HTML = (
    '<p><span class="tts-sent" id="tts-sent-0" data-sent-idx="0">' + EARLIER + '</span> '
    '<span class="tts-sent" id="tts-sent-1" data-sent-idx="1">Read <strong>this</strong></span>'
    '<em class="tts-sent-cont" data-sent-idx="1"> emphasized</em>'
    '<span class="tts-sent-cont" data-sent-idx="1"> ending.</span></p>'
    '<p><span class="tts-sent" id="tts-sent-2" data-sent-idx="2">Next '
    '<a href="https://fixture.invalid/read">phrase</a>.</span></p>'
    '<ul class="tts-sent" data-sent-idx="3"><li>One</li><li>Two <em>items</em></li></ul>'
    '<table class="tts-sent" data-sent-idx="4"><tr><td>A</td><td>B</td></tr></table>'
    '<pre class="tts-sent" data-sent-idx="5"><code>print("safe")</code></pre><p>Final tail.</p>'
)
POLL_PROBE = """(() => {
    const interval = window.setInterval;
    window.setInterval = (callback, ms, ...args) => {
        if (ms === 5000) { window.__pollGlance = callback; return 0; }
        return interval(callback, ms, ...args);
    };
})();"""


def payload(*, pane=PANE, session=SESSION, texts=(RAW,), html=HTML):
    turns = [{"turn_id": "reader-" + str(i), "role": "assistant", "text": text,
              "html": html, "map": {"contract": "reader-pipeline/anchors@1", "total_sents": 6}}
             for i, text in enumerate(texts)]
    return {"pane_id": pane, "session_id": session, "agent": "fixture", "turns": turns, "window": 20}


def responses(harness, data):
    harness.respond("/conversation", data, query=("pane_id",))
    harness.respond("/conversation/" + quote(data["pane_id"], safe="") + "/rendered", data)


def app(karaoke_app, *, texts=(RAW,), html=HTML, user=False):
    harness = karaoke_app()
    data = payload(texts=texts, html=html)
    if user:
        data["turns"].insert(0, {"turn_id": "user-0", "role": "user", "text": RAW, "html": HTML})
    responses(harness, data)
    harness.context.add_init_script(POLL_PROBE)
    harness.context.add_init_script("localStorage.setItem('herdr-brain-voice-engine', 'navegador');")
    harness.page.set_default_timeout(2000)
    harness.page.set_viewport_size({"width": 1000, "height": 1000})
    harness.load()
    expect(harness.page.locator(SURFACE)).to_have_count(len(texts))
    if html:
        expect(harness.page.locator(CONTENT)).to_have_count(len(texts))
    return harness


def anchor(harness, index=1, owner=0, continuation=False):
    cls = ".tts-sent-cont" if continuation else ".tts-sent"
    return harness.page.locator(SURFACE).nth(owner).locator(cls + f'[data-sent-idx="{index}"]').first


def selected(harness, index=1, owner=0):
    """Green paints the ACTIVE popup window's exact visible text as owned
    range marks, never the whole sentence anchor and never an envelope: the
    CONCATENATION of the marks' own text in DOM order must reconstruct the
    chunk the toast is showing (whitespace placement approximate,
    non-whitespace characters exact — unpainted interior content fails).
    Occurrence evidence for the expected `index`: no mark may sit inside an
    earlier sentence's anchor, and the expected index's anchor (or a fully
    unanchored window) must own the paint — identical text painted in a
    different occurrence is rejected. Marks live only in the owning turn
    and main karaoke stays idle."""
    page = harness.page
    page.wait_for_function(
        """owner => document.querySelectorAll('#glance-turns .gturn.assistant')[owner]
             .querySelectorAll('.tts-selected').length > 0""", arg=owner)
    turn = page.locator(SURFACE).nth(owner)
    marks = turn.locator(".tts-selected")
    for mark in marks.all():
        assert mark.text_content().strip(), "empty range mark"
    owners = marks.evaluate_all(
        """nodes => nodes.map(node => {
            const anchor = node.closest('[data-sent-idx]');
            return anchor ? anchor.dataset.sentIdx : null;
        })""")
    assert all(o is None or int(o) >= index for o in owners), \
        "painted an anchor of an earlier sentence than %d: %r" % (index, owners)
    assert any(o == str(index) for o in owners) or all(o is None for o in owners), \
        "expected owning occurrence %d not painted: %r" % (index, owners)
    green = marks.evaluate_all("nodes => nodes.map(node => node.textContent).join('')")
    popup = page.locator("#toast").text_content()
    assert popup is not None and ": " in popup, popup
    for prefix in ("🔊 agente: ", "🔊 brain: "):
        if popup.startswith(prefix):
            popup = popup[len(prefix):]
            break
    assert "".join(green.split()) == "".join(popup.split()), (green, popup)
    expect(page.locator("#glance-turns .tts-selected")).to_have_count(marks.count())
    expect(page.locator(".karaoke-active")).to_have_count(0)


def idle(harness):
    expect(harness.page.locator("#glance-turns .tts-selected, .karaoke-active")).to_have_count(0)
    assert harness.page.locator(SURFACE + " .replay-btn:disabled, " + SURFACE + " [aria-busy='true']").count() == 0


def poll(harness):
    with harness.page.expect_response(lambda response: "/rendered" in response.url):
        harness.page.evaluate("() => __pollGlance()")


def ready(harness, *, index=1, owner=0, continuation=False, url="/audio/suffix.mp3", playing=True):
    reply = harness.queue_tts(audio_url=url)
    anchor(harness, index, owner, continuation).click()
    source(harness, url)
    harness.media("metadata", duration=100)
    if playing:
        harness.page.wait_for_function("() => !__karaokeHarness.media.snapshot().paused")
    return reply


def test_external_escuchar_then_later_sentence_requests_fresh_readable_suffix(karaoke_app):
    harness = app(karaoke_app)
    before = harness.page.locator(CONTENT).inner_html()
    full = harness.queue_tts(audio_url="/audio/full.mp3")
    harness.page.locator(SURFACE + " .replay-btn").click()
    source(harness, "/full.mp3")
    harness.media("metadata", duration=100)
    harness.media("advance", time=65)
    assert full.request.post_data_json == {"text": FULL}
    reply = ready(harness)
    assert reply.request.post_data_json == {"text": SUFFIX}
    assert reply.request.post_data_json["text"].startswith(SELECTED)
    assert EARLIER not in reply.request.post_data_json["text"]
    assert harness.media("snapshot")["currentTime"] == 0
    selected(harness)
    # Only classes/accessibility attributes may change, never the rich structure.
    assert harness.page.locator(CONTENT + " strong").text_content() == "this"
    assert harness.page.locator(CONTENT + " em").all_text_contents() == [" emphasized", "items"]
    assert harness.page.locator(CONTENT + " a").get_attribute("rel") == "noopener noreferrer"
    assert harness.page.locator(CONTENT + " code").text_content() == 'print("safe")'
    harness.media("advance", time=90)
    selected(harness, index=3)  # First sentence in the popup's second raw window.
    harness.media("end")
    idle(harness)
    assert harness.page.locator(CONTENT).inner_html().replace(' aria-pressed="false"', '') == before.replace(' aria-pressed="false"', '')


@pytest.mark.parametrize("continuation", [False, True])
def test_before_escuchar_includes_primary_and_all_continuations(karaoke_app, continuation):
    harness = app(karaoke_app)
    reply = ready(harness, continuation=continuation)
    assert reply.request.post_data_json == {"text": SUFFIX}
    assert harness.media("snapshot")["playCalls"] == 1
    assert harness.media("snapshot")["currentTime"] == 0
    selected(harness)


@pytest.mark.parametrize("index, expected", [(3, 'One\nTwo items\nA B\nprint("safe")\nFinal tail.'),
                                           (4, 'A B\nprint("safe")\nFinal tail.')])
def test_container_borne_sentence_anchors(karaoke_app, index, expected):
    harness = app(karaoke_app)
    reply = ready(harness, index=index)
    assert reply.request.post_data_json == {"text": expected}
    selected(harness, index)


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_focused_keyboard_has_spanish_name_and_scoped_visible_focus(karaoke_app, key):
    harness = app(karaoke_app, user=True)
    control = anchor(harness)
    assert control.get_attribute("role") == "button"
    assert control.get_attribute("aria-label") == "Escuchar desde la frase 2"
    control.focus()
    harness.page.keyboard.press("Tab")
    harness.page.keyboard.press("Shift+Tab")
    assert control.evaluate("el => el === document.activeElement")
    assert control.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
    reply = harness.queue_tts(audio_url="/audio/key.mp3")
    control.press(key)
    source(harness, "/key.mp3")
    harness.media("metadata", duration=60)
    assert reply.request.post_data_json == {"text": SUFFIX}
    selected(harness)
    assert harness.page.locator("#glance-turns .gturn.user [role='button'], #glance-turns .gturn.user .replay-btn").count() == 0


@pytest.mark.parametrize("operation", ["selection", "modified", "double", "double-native", "double-delayed", "contextmenu", "drag", "longpress", "link", "code", "native", "user"])
def test_native_operations_do_not_activate_reader(karaoke_app, operation):
    harness = app(karaoke_app, user=True)
    control = anchor(harness)
    if operation == "selection":
        control.evaluate("""el => {
            const range = document.createRange(); range.selectNodeContents(el);
            getSelection().removeAllRanges(); getSelection().addRange(range); el.click();
        }""")
        assert harness.page.evaluate("() => !getSelection().isCollapsed")
    elif operation == "modified":
        control.click(modifiers=["Control"])
    elif operation == "double":
        control.dispatch_event("click", {"detail": 2, "button": 0})
    elif operation == "double-native":
        control.dblclick()
    elif operation == "double-delayed":
        control.click()
        harness.page.wait_for_timeout(350)
        control.click(click_count=2)
    elif operation == "contextmenu":
        control.click(button="right")
        control.evaluate("el => el.click()")
    elif operation == "drag":
        control.dispatch_event("pointerdown", {"clientX": 20, "clientY": 20, "button": 0})
        control.dispatch_event("pointermove", {"clientX": 45, "clientY": 20})
        control.dispatch_event("pointerup", {"clientX": 45, "clientY": 20})
        control.evaluate("el => el.click()")
    elif operation == "longpress":
        control.click(delay=600)
    elif operation == "code":
        harness.page.locator(CONTENT + " code").click()
    elif operation == "link":
        # Route is deliberately intercepted in a new tab; no external traffic escapes.
        harness.context.route("https://fixture.invalid/read", lambda route: route.fulfill(body="fixture link"))
        with harness.page.expect_popup() as popup:
            harness.page.locator(CONTENT + " a").click()
        popup.value.close()
    elif operation == "native":
        control.evaluate("""el => {
            const button = document.createElement('button'); button.textContent = 'Native';
            button.onclick = () => { window.__nativeWorked = true; };
            el.appendChild(button); button.click();
        }""")
        assert harness.page.evaluate("() => __nativeWorked")
    else:
        harness.page.locator("#glance-turns .gturn.user .tts-sent").first.click()
    harness.page.wait_for_timeout(600)  # Allow any deferred single-click activation to expose itself.
    idle(harness)
    assert "/tts" not in harness.page.evaluate("() => __karaokeHarness.fetchCalls")


def test_duplicate_ids_equal_text_out_of_order_and_polling_keep_dom_owner(karaoke_app):
    harness = app(karaoke_app, texts=(RAW, RAW))
    harness.ignore_fetch_abort()
    first = harness.queue_tts(audio_url="/audio/stale.mp3", defer=True)
    second = harness.queue_tts(audio_url="/audio/current.mp3")
    anchor(harness, owner=0).click()
    harness.page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/tts')")
    anchor(harness, owner=1, continuation=True).click()
    source(harness, "/current.mp3")
    harness.media("metadata", duration=100)
    selected(harness, owner=1)
    first.release()
    assert second.request.post_data_json == {"text": SUFFIX}
    harness.media("advance", time=60)
    selected(harness, index=3, owner=1)
    poll(harness)
    selected(harness, index=3, owner=1)
    assert sum(request.url.endswith("/tts") for request in harness.requests) == 2
    # A same-content remount has new anchors, but the same owning turn/plan.
    harness.page.locator(CONTENT).nth(1).evaluate("(el, html) => { el.innerHTML = html; }", HTML)
    selected(harness, index=3, owner=1)
    harness.media("advance", time=60)
    selected(harness, index=3, owner=1)
    assert harness.media("snapshot")["playCalls"] == 1


@pytest.mark.parametrize("action", ["stop", "remove", "raw-remove", "poll-remove", "raw-content", "content", "session", "pane"])
def test_retirement_while_synthesis_pending_discards_late_audio(karaoke_app, action):
    harness = app(karaoke_app)
    harness.ignore_fetch_abort()
    reply = harness.queue_tts(audio_url="/audio/retired.mp3", defer=True)
    anchor(harness).click()
    selected(harness)
    if action == "stop":
        harness.page.locator("#stop-audio").click()
    elif action == "remove":
        harness.page.locator(SURFACE).evaluate("el => el.remove()")
    elif action == "raw-remove":
        harness.page.locator(SURFACE + " .gt-text").evaluate("el => el.remove()")
    else:
        data = payload(session="replacement" if action == "session" else SESSION,
                       pane="other-pane" if action == "pane" else PANE,
                       texts=(RAW + " Changed.",) if action == "raw-content" else (RAW,),
                       html=HTML.replace("Final tail.", "Changed tail.") if action == "content" else HTML)
        if action == "poll-remove":
            data["turns"] = []
        responses(harness, data)
        poll(harness)
    idle(harness)
    reply.release()
    assert harness.media("snapshot")["playCalls"] == 0
    assert "retired.mp3" not in harness.media("snapshot")["src"]


def test_late_metadata_and_pending_play_do_not_highlight_new_owner(karaoke_app):
    harness = app(karaoke_app, texts=(RAW, RAW))
    harness.media("queuePlay", outcome="defer")
    ready(harness)
    old = harness.media("capture")
    play_id = harness.media("snapshot")["pendingPlays"][0]
    harness.queue_tts(audio_url="/audio/new.mp3")
    anchor(harness, owner=1).click()
    source(harness, "/new.mp3")
    harness.media("metadata", source=old, duration=200)
    for event in ["pause", "timeupdate", "error", "ended"]:
        harness.media("emit", type=event, source=old, time=190)
    harness.media("settlePlay", id=play_id, error="NotAllowedError")
    selected(harness, owner=1)
    assert harness.media("snapshot")["playCalls"] == 1
    harness.media("metadata", duration=100)
    assert harness.media("snapshot")["currentTime"] == 0
    assert "No pude" not in harness.page.locator("#banner").text_content()


@pytest.mark.parametrize("failure", ["synthesis", "media", "play"])
def test_failure_recovers_controls_and_allows_fresh_retry(karaoke_app, failure):
    harness = app(karaoke_app)
    if failure == "synthesis":
        harness.queue_tts(status=503)
        anchor(harness).click()
    else:
        if failure == "play":
            harness.media("queuePlay", outcome="reject")
        ready(harness, playing=failure != "play")
        if failure == "media":
            harness.media("error")
    idle(harness)
    expect(harness.page.locator("#banner")).to_contain_text("No pude")
    reply = ready(harness, url="/audio/retry.mp3")
    assert reply.request.post_data_json == {"text": SUFFIX}
    selected(harness)


def test_main_external_supersession_and_stop_preserve_unrelated_announcements(karaoke_app):
    harness = app(karaoke_app)
    harness.page.locator("#call-btn").click()
    ready(harness)
    announcement(harness, "foreign-one")
    announcement(harness, "foreign-two")
    harness.queue_tts(audio_url="/audio/main.mp3")
    harness.page.locator("#conversation .karaoke-chunk").first.click()
    source(harness, "/main.mp3")
    harness.media("metadata", duration=100)
    expect(harness.page.locator("#glance-turns .tts-selected")).to_have_count(0)
    expect(harness.page.locator(".karaoke-active")).to_have_count(1)
    harness.page.locator("#drawer-close").click()
    ready(harness, url="/audio/external-again.mp3")
    harness.page.locator("#stop-audio").click()
    source(harness, "/foreign-one.mp3")
    harness.media("end")
    source(harness, "/foreign-two.mp3")
    harness.media("end")
    idle(harness)


def test_long_suffix_uses_existing_bounded_splitter(karaoke_app):
    tail = "Remaining sentence. " * 1000
    html = HTML + "<p>" + tail + "</p>"
    harness = app(karaoke_app, html=html)
    expected = harness.page.evaluate("text => Toast.splitForTts(text, 8000)", SUFFIX + "\n" + tail.strip())
    replies = [harness.queue_tts(audio_url=f"/audio/piece-{i}.mp3") for i in range(len(expected))]
    anchor(harness).click()
    for i, piece in enumerate(expected):
        source(harness, f"/piece-{i}.mp3")
        assert replies[i].request.post_data_json == {"text": piece}
        assert len(piece) <= 8000
        harness.media("metadata", duration=100)
        harness.media("end")
    idle(harness)


@pytest.mark.parametrize("nested", [False, True])
def test_repeated_text_and_nested_anchors_use_the_later_dom_boundary(karaoke_app, nested):
    second = ('<strong class="tts-sent-cont" data-sent-idx="1">Echo.</strong>' if nested else "Echo.")
    html = ('<p><span class="tts-sent" data-sent-idx="0">Echo.</span> '
            '<span class="tts-sent" data-sent-idx="1">' + second + '</span></p><p>Tail.</p>')
    harness = app(karaoke_app, texts=("Echo. Echo. Tail.",), html=html)
    reply = ready(harness, continuation=nested)
    assert reply.request.post_data_json == {"text": "Echo.\nTail."}
    selected(harness)


@pytest.mark.parametrize("action", ["stop", "sentence"])
def test_pending_full_escuchar_is_owned_and_cannot_revive(karaoke_app, action):
    harness = app(karaoke_app)
    harness.ignore_fetch_abort()
    full = harness.queue_tts(audio_url="/audio/obsolete-full.mp3", defer=True)
    harness.page.locator(SURFACE + " .replay-btn").click()
    harness.page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/tts')")
    if action == "stop":
        harness.page.locator("#stop-audio").click()
        idle(harness)
    else:
        ready(harness)
        selected(harness)
    full.release()
    harness.page.wait_for_timeout(50)
    assert "obsolete-full" not in harness.media("snapshot")["src"]


def test_unrendered_escuchar_retains_owned_full_text_fallback(karaoke_app):
    harness = app(karaoke_app, html=None)
    reply = harness.queue_tts(audio_url="/audio/plain.mp3")
    harness.page.locator(SURFACE + " .replay-btn").click()
    source(harness, "/plain.mp3")
    harness.media("metadata", duration=100)
    assert reply.request.post_data_json == {"text": RAW}
    harness.page.locator("#stop-audio").click()
    idle(harness)


@pytest.mark.parametrize("activation", ["click", "Enter", "Space"])
def test_missing_sentence_index_never_aliases_whole_escuchar(karaoke_app, activation):
    html = HTML.replace(' data-sent-idx="1"', "", 1)
    harness = app(karaoke_app, html=html)
    reply = harness.queue_tts(audio_url="/audio/invalid-sentence.mp3")
    control = harness.page.locator(CONTENT + " .tts-sent").nth(1)
    if activation == "click":
        control.click()
    else:
        control.evaluate("el => { el.tabIndex = 0; }")
        control.press(activation)
    harness.page.wait_for_timeout(650)
    assert reply.request is None, "Missing sentence index must not synthesize the raw whole answer"
    idle(harness)
    # The same fixture reply is consumed only by legitimate whole-answer replay.
    harness.page.locator(SURFACE + " .replay-btn").click()
    source(harness, "/invalid-sentence.mp3")
    assert reply.request.post_data_json == {"text": FULL}


def full_near_end(harness):
    reply = harness.queue_tts(audio_url="/audio/full.mp3")
    harness.page.locator(SURFACE + " .replay-btn").click()
    source(harness, "/full.mp3")
    harness.media("metadata", duration=100)
    harness.media("advance", time=99.9)
    assert reply.request.post_data_json == {"text": FULL}


def test_pending_sentence_click_survives_natural_previous_audio_end(karaoke_app):
    harness = app(karaoke_app)
    full_near_end(harness)
    reply = harness.queue_tts(audio_url="/audio/suffix.mp3")
    anchor(harness).click()
    harness.media("end")
    harness.page.wait_for_timeout(650)
    assert reply.request is not None, "Natural completion must not retire the user's pending sentence click"
    assert reply.request.post_data_json == {"text": SUFFIX}
    source(harness, "/suffix.mp3")
    harness.media("metadata", duration=100)
    assert harness.media("snapshot")["currentTime"] == 0
    selected(harness)


@pytest.mark.parametrize("action", ["stop", "reset", "full-replay", "sentence", "main", "remove", "content", "session", "pane", "ask", "error"])
def test_explicit_retirement_still_cancels_delayed_sentence_intent(karaoke_app, action):
    harness = app(karaoke_app)
    full_near_end(harness)
    reply = harness.queue_tts(audio_url="/audio/replacement.mp3")
    anchor(harness).click()
    expected = [{"text": FULL}]
    if action == "stop":
        harness.page.locator("#stop-audio").click()
    elif action == "reset":
        harness.page.locator("#conv-new").evaluate("el => el.click()")
    elif action == "full-replay":
        harness.page.locator(SURFACE + " .replay-btn").click()
        source(harness, "/replacement.mp3")
        expected.append({"text": FULL})
    elif action == "sentence":
        anchor(harness, index=2).press("Enter")
        source(harness, "/replacement.mp3")
        expected.append({"text": SUFFIX.split("\n", 1)[1]})
    elif action == "main":
        harness.page.locator("#conversation .karaoke-chunk").first.evaluate("el => el.click()")
        source(harness, "/replacement.mp3")
        expected.append({"text": harness.answer_text})
    elif action == "remove":
        harness.page.locator(SURFACE).evaluate("el => el.remove()")
    elif action == "content":
        harness.page.locator(CONTENT + " strong").evaluate("el => { el.textContent = 'Changed'; }")
    elif action in ("session", "pane"):
        data = payload(session="replacement" if action == "session" else SESSION,
                       pane="replacement-pane" if action == "pane" else PANE)
        responses(harness, data)
        poll(harness)
    elif action == "ask":
        harness.respond("/ask", {"answer": "Replacement fixture answer"}, method="POST")
        harness.page.locator("#text-input").evaluate("el => { el.value = 'Replacement question'; }")
        harness.page.locator("#text-fallback").evaluate("el => el.requestSubmit()")
    else:
        harness.media("error")
    harness.page.wait_for_timeout(650)
    assert [request.post_data_json for request in harness.requests if request.url.endswith("/tts")] == expected
    if len(expected) == 1:
        assert reply.request is None


def progress_content(count=5, *, repeated=False, nested=False):
    texts = [f"Sentence {i:03d} has enough distinct readable words to fill one popup window."
             for i in range(count)]
    if repeated:
        texts = [texts[0]] * count
    paragraphs = []
    for i, text in enumerate(texts):
        body = (f'<strong class="tts-sent-cont" data-sent-idx="{i}">' +
                f'<span class="tts-sent" data-sent-idx="{i}">{text}</span></strong>'
                if nested else text)
        paragraphs.append(f'<p class="tts-sent" id="tts-sent-{i}" data-sent-idx="{i}">{body}</p>')
    return texts, "".join(paragraphs)


def progress_plan(harness, text):
    return harness.page.evaluate("text => Karaoke.createPlan('fixture-proof', text)", text)


@pytest.mark.parametrize("hidden", [False, True], ids=["visible-popup", "hidden-popup"])
def test_intermediate_click_tracks_same_popup_global_index(karaoke_app, hidden):
    texts, html = progress_content()
    harness = app(karaoke_app, texts=("\n".join(texts),), html=html)
    suffix = "\n".join(texts[1:])
    plan = progress_plan(harness, suffix)
    assert len(plan["chunks"]) == len(texts) - 1
    reply = ready(harness)
    assert reply.request.post_data_json == {"text": suffix}
    selected(harness, index=1)
    if hidden:
        harness.page.locator("#toast").evaluate("el => el.classList.add('hidden')")
    for global_index in [1, 2, 3]:
        harness.media("advance", time=(global_index + 0.2) / len(plan["chunks"]) * 100)
        expect(harness.page.locator("#toast")).to_contain_text(plan["chunks"][global_index]["popupText"])
        owners = harness.page.locator(CONTENT + " .tts-selected").evaluate_all(
            """nodes => nodes.map(node => {
                const anchor = node.closest('[data-sent-idx]');
                return anchor ? anchor.dataset.sentIdx : null;
            })""")
        assert set(owners) <= {str(global_index + 1), None}, \
            "Popup advanced but the painted window stayed behind: %r" % owners
        selected(harness, index=global_index + 1)
        expect(anchor(harness, index=global_index + 1)).to_have_attribute("aria-pressed", "true")
        expect(anchor(harness, index=1)).to_have_attribute("aria-pressed", "false")
        if hidden:
            expect(harness.page.locator("#toast")).to_have_class("hidden")
    # The active index must never replace the original suffix boundary.
    reply = ready(harness, index=2, url="/audio/fresh-after-progress.mp3")
    assert reply.request.post_data_json == {"text": "\n".join(texts[2:])}
    assert harness.media("snapshot")["currentTime"] == 0
    selected(harness, index=2)
    harness.media("end")
    idle(harness)


@pytest.mark.parametrize("hidden", [False, True], ids=["visible-popup", "hidden-popup"])
def test_shared_anchor_popup_windows_paint_only_their_own_visible_text(karaoke_app, hidden):
    esta = "Esta sesión queda protegida, junto con Whisper y el servidor de síntesis."
    las = "Las terminales antiguas de las pruebas consumen muy poco; cerrarlas apenas ayudaría."
    question = "¿Cuáles de estas sesiones puedes cerrar sin interrumpir trabajo que quieras conservar?"
    request = "Indícame sus PID o terminales; si todas siguen trabajando, no cerraremos ninguna."
    html = (f'<p class="tts-sent" data-sent-idx="9">{esta}</p>'
            f'<p class="tts-sent" data-sent-idx="10">{las}</p>'
            f'<p class="tts-sent" data-sent-idx="11">{question} {request}</p>')
    raw = " ".join([esta, las, question, request])
    harness = app(karaoke_app, texts=(raw,), html=html)
    suffix = esta + "\n" + las + "\n" + question + " " + request
    plan = progress_plan(harness, suffix)
    chunks = plan["chunks"]
    assert len(chunks) >= 6, "the exact tail spans several popup windows"
    question_window = next(chunk for chunk in chunks
                           if "quieras" in chunk["popupText"] and "Indícame" not in chunk["popupText"])
    request_window = next(chunk for chunk in chunks if "no cerraremos ninguna" in chunk["popupText"])
    assert question_window["globalIndex"] < request_window["globalIndex"]
    before = harness.page.locator(CONTENT).inner_html()
    reply = ready(harness, index=9)
    assert reply.request.post_data_json == {"text": suffix}
    if hidden:
        harness.page.locator("#toast").evaluate("el => el.classList.add('hidden')")

    def green():
        return harness.page.locator(CONTENT).evaluate(
            "el => Array.from(el.querySelectorAll('.tts-selected'))"
            ".map(node => node.textContent).join(' ')")

    norm = lambda text: "".join(text.split())
    for window, forbidden in ((question_window, ["Indícame", "no cerraremos"]),
                              (request_window, ["¿Cuáles", "quieras", "conservar"])):
        harness.media("advance", time=(window["globalIndex"] + 0.2) / len(chunks) * 100)
        expect(harness.page.locator("#toast")).to_contain_text(window["popupText"])
        assert norm(green()) == norm(window["popupText"]), (
            "popup window %r must paint only its own visible text" % window["popupText"])
        for token in forbidden:
            assert token not in green(), f"{token} painted outside the active popup window"
        if hidden:
            expect(harness.page.locator("#toast")).to_have_class("hidden")
    harness.media("end")
    idle(harness)
    assert harness.page.locator(CONTENT).inner_html().replace(' aria-pressed="false"', "") == \
        before.replace(' aria-pressed="false"', "")
    assert harness.page.locator(CONTENT + " p").count() == 3


def inject_marks(harness, wraps, popup):
    """Helper-detection probe fixture: manually paint ONLY the given leaf
    substrings as owned range marks and show `popup` in the toast, without
    any active replay (glanceReplay stays null, so no observer repaint can
    mask the sabotaged state). `wraps` is a list of
    [data-sent-idx, leaf ordinal, start, end, raw] descriptors."""
    harness.page.evaluate(
        """([wraps, popup, selector]) => {
            const toast = document.getElementById('toast');
            toast.classList.remove('hidden');
            toast.textContent = '🔊 agente: ' + popup;
            const content = document.querySelectorAll(selector)[0];
            for (const [idx, leaf, start, end, raw] of wraps) {
                const anchor = content.querySelectorAll('[data-sent-idx="' + idx + '"]')[leaf];
                const node = anchor.firstChild;
                if (end < node.data.length) node.splitText(end);
                const mid = start > 0 ? node.splitText(start) : node;
                const mark = document.createElement('span');
                mark.className = 'tts-selected tts-range';
                mark.setAttribute('data-tts-range', raw);
                node.parentNode.replaceChild(mark, mid);
                mark.appendChild(mid);
            }
        }""",
        [wraps, popup, CONTENT])


def test_selected_rejects_edge_only_paint_with_unpainted_interior(karaoke_app):
    # Deliberate hole: only the window's edge words are marked, the interior
    # stays unpainted, endpoints and toast window are otherwise exact. An
    # envelope comparison (first-to-last-mark Range) still sees the full
    # window text and wrongly accepts; the helper must reject it.
    harness = app(karaoke_app)
    inject_marks(harness, [
        ["1", 0, 0, 4, "0:4"],        # 'Read' (primary anchor's first leaf)
        ["1", 2, 1, 8, "24:31"],      # 'ending.' (third continuation leaf)
    ], "Read this emphasized ending.")
    with pytest.raises(AssertionError):
        selected(harness)


def test_selected_rejects_identical_text_in_the_wrong_occurrence(karaoke_app):
    # Repeated identical sentences: the window text matches exactly, but the
    # paint sits on the FIRST occurrence while the expected owner is the
    # second. Ignoring the expected index accepts the wrong occurrence.
    html = ('<p><span class="tts-sent" data-sent-idx="0">Echo.</span> '
            '<span class="tts-sent" data-sent-idx="1">Echo.</span></p><p>Tail.</p>')
    harness = app(karaoke_app, texts=("Echo. Echo. Tail.",), html=html)
    inject_marks(harness, [["0", 0, 0, 5, "0:5"]], "Echo.")
    with pytest.raises(AssertionError):
        selected(harness, index=1)


@pytest.mark.parametrize("nested", [False, True])
def test_repeated_sentence_progress_selects_following_primary_and_continuations(karaoke_app, nested):
    texts, html = progress_content(repeated=True, nested=nested)
    harness = app(karaoke_app, texts=("\n".join(texts),), html=html)
    reply = ready(harness, continuation=nested)
    assert reply.request.post_data_json == {"text": "\n".join(texts[1:])}
    for global_index in range(4):
        harness.media("advance", time=(global_index + 0.2) / 4 * 100)
        selected(harness, index=global_index + 1)
    assert harness.media("snapshot")["playCalls"] == 1


def test_rendered_escuchar_tracks_from_the_first_readable_sentence(karaoke_app):
    texts, html = progress_content()
    harness = app(karaoke_app, texts=("Original source formatting differs from the rendered text.",), html=html)
    reply = harness.queue_tts(audio_url="/audio/readable-full.mp3")
    harness.page.locator(SURFACE + " .replay-btn").click()
    source(harness, "/readable-full.mp3")
    assert reply.request.post_data_json == {"text": "\n".join(texts)}
    harness.media("metadata", duration=100)
    harness.page.wait_for_function("() => !__karaokeHarness.media.snapshot().paused")
    selected(harness, index=0)
    harness.media("advance", time=44)
    expect(harness.page.locator("#toast")).to_contain_text(texts[2])
    selected(harness, index=2)


def test_unanchored_blocks_keep_the_previous_sentence_until_the_popup_reaches_the_next_anchor(karaoke_app):
    texts, _ = progress_content(3)
    gap = "Unanchored words fill a whole popup window between two readable DOM anchors."
    html = (f'<p class="tts-sent" data-sent-idx="0">{texts[0]}</p>' +
            f'<p class="tts-sent" data-sent-idx="1">{texts[1]}</p><p>{gap}</p>' +
            f'<p class="tts-sent" data-sent-idx="2">{texts[2]}</p>')
    harness = app(karaoke_app, texts=("source",), html=html)
    reply = ready(harness)
    suffix = texts[1] + "\n" + gap + "\n" + texts[2]
    assert reply.request.post_data_json == {"text": suffix}
    plan = progress_plan(harness, suffix)
    assert len(plan["chunks"]) == 3
    harness.media("advance", time=40)
    expect(harness.page.locator("#toast")).to_contain_text(gap)
    selected(harness, index=1)
    harness.media("advance", time=75)
    expect(harness.page.locator("#toast")).to_contain_text(texts[2])
    selected(harness, index=2)


def test_long_anchored_suffix_tracks_popup_global_offsets_across_tts_pieces(karaoke_app):
    texts, html = progress_content(230)
    harness = app(karaoke_app, texts=("\n".join(texts),), html=html)
    plan = progress_plan(harness, "\n".join(texts[1:]))
    assert len(plan["pieces"]) > 2
    assert len(plan["chunks"]) == len(texts) - 1
    replies = [harness.queue_tts(audio_url=f"/audio/tracking-piece-{i}.mp3")
               for i in range(len(plan["pieces"]))]
    anchor(harness).click()
    for piece_index, piece in enumerate(plan["pieces"]):
        source(harness, f"/tracking-piece-{piece_index}.mp3")
        assert replies[piece_index].request.post_data_json == {"text": piece["text"]}
        assert len(piece["text"]) <= 8000
        harness.media("metadata", duration=100)
        harness.page.wait_for_function("() => !__karaokeHarness.media.snapshot().paused")
        for local_index in [0, len(piece["chunks"]) // 2, len(piece["chunks"]) - 1]:
            global_index = piece["chunkOffset"] + local_index
            harness.media("advance", time=(local_index + 0.2) / len(piece["chunks"]) * 100)
            expect(harness.page.locator("#toast")).to_contain_text(plan["chunks"][global_index]["popupText"])
            selected(harness, index=global_index + 1)
        harness.media("end")
    idle(harness)


@pytest.mark.parametrize("action", ["stop", "end", "error", "remove", "content", "raw-content",
                                    "index", "session", "pane"])
def test_advanced_highlight_retires_and_late_progress_cannot_restore_it(karaoke_app, action):
    texts, html = progress_content()
    raw = "\n".join(texts)
    harness = app(karaoke_app, texts=(raw,), html=html)
    ready(harness)
    harness.media("advance", time=35)
    selected(harness, index=2)
    old = harness.media("capture")
    if action == "stop":
        harness.page.locator("#stop-audio").click()
    elif action == "end":
        harness.media("end")
    elif action == "error":
        harness.media("error")
    elif action == "remove":
        harness.page.locator(SURFACE).evaluate("el => el.remove()")
    elif action == "content":
        anchor(harness, index=2).evaluate("el => { el.textContent = 'Changed reader content.'; }")
    elif action == "raw-content":
        harness.page.locator(SURFACE + " .gt-text").evaluate("el => { el.textContent = 'Changed response.'; }")
    elif action == "index":
        anchor(harness, index=2).evaluate("el => el.setAttribute('data-sent-idx', '7')")
    else:
        data = payload(texts=(raw,), html=html,
                       session="new-session" if action == "session" else SESSION,
                       pane="new-pane" if action == "pane" else PANE)
        responses(harness, data)
        poll(harness)
    idle(harness)
    for event in ["timeupdate", "loadedmetadata", "pause", "ended", "error"]:
        harness.media("emit", type=event, source=old, time=95)
    idle(harness)
    if action in ("stop", "end", "error"):
        reply = ready(harness, url="/audio/usable-again.mp3")
        assert reply.request.post_data_json == {"text": "\n".join(texts[1:])}
        selected(harness, index=1)


@pytest.fixture
def native_glance(real_media_browser):
    context = real_media_browser.new_context(service_workers="block", accept_downloads=False, permissions=[])
    harness = RealMediaApp(context)
    data = payload()
    harness.api["/conversation"] = (data, {"pane_id"})
    harness.api["/conversation/" + quote(PANE, safe="") + "/rendered"] = (data, set())
    context.add_init_script(POLL_PROBE)
    try:
        yield harness.load()
    finally:
        context.close()
        assert not harness.blocked, harness.blocked
        assert not harness.errors, harness.errors


def test_native_mp3_fresh_suffix_starts_at_zero_and_progress_survives_reselection(native_glance):
    harness = native_glance
    expect(harness.page.locator(CONTENT)).to_have_count(1)
    harness.page.locator(SURFACE + " .replay-btn").click()
    harness.page.wait_for_function("() => !player.paused && !player.muted && player.currentTime > 0.1")
    anchor(harness).click()
    harness.page.wait_for_function("() => __realMedia.events.filter(e => e.type === 'play-call' && !e.muted).length >= 2")
    assert harness.tts == [{"text": FULL}, {"text": SUFFIX}]
    calls = [event for event in harness.snapshot()["events"] if event["type"] == "play-call" and not event["muted"]]
    assert calls[-1]["time"] == pytest.approx(0, abs=0.03)
    selected(harness)
    # Same-source main reselection queues an old native pause; popup must still advance.
    harness.page.locator("#conversation .karaoke-chunk").nth(2).click()
    harness.page.wait_for_function("() => !player.paused && !player.muted")
    harness.page.locator("#conversation .karaoke-chunk").nth(1).click()
    harness.page.wait_for_function("() => !player.paused && !player.muted")
    harness.page.locator("#player").evaluate("p => { p.currentTime = p.duration * 0.85; }")
    controls = harness.page.locator("#conversation .karaoke-chunk")
    target = int(controls.count() * 0.85)
    expect(controls.nth(target)).to_have_class("karaoke-chunk karaoke-active")
    harness.page.locator("#player").evaluate("p => p.pause()")
    time = harness.snapshot()["time"]
    harness.page.wait_for_timeout(120)
    assert harness.snapshot()["time"] == pytest.approx(time, abs=0.03)


def test_native_mp3_external_highlight_follows_popup_progress_when_hidden_and_remounted(native_glance):
    harness = native_glance
    anchor(harness).click()
    harness.page.wait_for_function("() => !player.paused && !player.muted && player.currentTime > 0.08")
    assert harness.tts == [{"text": SUFFIX}]
    selected(harness)
    harness.page.locator("#toast").evaluate("el => el.classList.add('hidden')")
    harness.page.locator("#player").evaluate("p => { p.currentTime = p.duration * 0.65; }")
    expect(harness.page.locator("#toast")).to_contain_text('One Two items A B print("safe") Final tail.')
    selected(harness, index=3)
    expect(harness.page.locator("#toast")).to_have_class("hidden")
    assert any(event["type"] == "timeupdate" and event["time"] > event["duration"] * 0.5
               for event in harness.snapshot()["events"] if isinstance(event["duration"], (int, float)))
    harness.page.locator("#player").evaluate("p => p.pause()")
    poll(harness)
    harness.page.locator(CONTENT).evaluate("(el, html) => { el.innerHTML = html; }", HTML)
    selected(harness, index=3)
    # Natural completion clears the advanced highlight with native MP3 events.
    harness.page.locator("#player").evaluate("p => { p.currentTime = p.duration - 0.12; return p.play(); }")
    harness.page.wait_for_function("() => __realMedia.events.some(event => event.type === 'ended')")
    idle(harness)


def test_native_unrendered_fallback_after_stop_handles_current_src_lag(native_glance):
    harness = native_glance
    data = payload(html=None)
    harness.api["/conversation/" + quote(PANE, safe="") + "/rendered"] = (data, set())
    poll(harness)
    expect(harness.page.locator(CONTENT)).to_have_count(0)
    harness.page.locator("#conversation .karaoke-chunk").nth(2).click()
    harness.page.wait_for_function("() => !player.paused && !player.muted && player.currentTime > 0.1")
    harness.page.locator("#stop-audio").click()
    harness.page.wait_for_function("() => player.paused && !player.muted")
    harness.audio_name = "sample.mp3"
    previous_calls = sum(event["type"] == "play-call" for event in harness.snapshot()["events"])
    harness.page.locator(SURFACE + " .replay-btn").evaluate("button => button.click()")
    try:
        harness.page.wait_for_function("() => player.currentSrc.includes('/sample.mp3') && !player.paused && player.currentTime > 0.08")
    except PlaywrightTimeoutError:
        pytest.fail("Native fallback did not start: " + repr(harness.snapshot()) +
                    "; banner=" + harness.page.locator("#banner").text_content())
    # The play-call probe observes currentSrc before native source selection,
    # so identify this command by call order, not the predecessor's URL.
    calls = [event for event in harness.snapshot()["events"] if event["type"] == "play-call"][previous_calls:]
    assert len(calls) == 1
    assert calls[0]["muted"] is False
    assert calls[0]["time"] == pytest.approx(0, abs=0.03)
