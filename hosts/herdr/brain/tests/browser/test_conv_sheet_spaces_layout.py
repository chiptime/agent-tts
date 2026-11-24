"""Browser proof: the conversation sheet keeps its agent list reachable
on a phone even when the operator has many herdr spaces.

Regression: the spaces section (one wrapping chip per herdr workspace)
was a non-shrinking flex child of the 60dvh sheet. With fourteen spaces
on a 390x844 viewport the chips took ~350px and squeezed #conv-list to
~12px, so the conversations were present but invisible. The spaces now
ride one horizontally scrolling row and the list keeps the rest.

Runs from hosts/herdr/brain, separate from the unit/API suites:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
        -m pytest -p no:cacheprovider tests/browser/test_conv_sheet_spaces_layout.py
"""

import re

import pytest
from playwright.sync_api import expect

SPACES = [
    {
        "workspace_id": f"w{i}",
        "label": f"workspace-{i:02d}",
        "cwd": f"/srv/work/space-{i}",
        "inside_roots": True,
        "focused": i == 13,
    }
    for i in range(14)
]
ROOTS = {
    "roots": [{"path": "/srv/work", "label": "Work"}],
    "recents": [],
    "harnesses": ["claude", "opencode"],
    "spaces": SPACES,
    "mux": {"name": "herdr", "children": ["herdr"]},
}


HERD = [
    {
        "pane_id": f"w1:p{i}",
        "agent": "opencode",
        "agent_status": "working" if i == 0 else "idle",
        "title": f"Conversation {i}",
        "cwd": "/srv/work/space-1",
        "focused": i == 0,
    }
    for i in range(4)
]


@pytest.fixture
def phone_app(karaoke_app):
    harness = karaoke_app()  # configure first, then load()
    harness.respond("/session/roots", ROOTS)
    harness.respond("/herd", HERD)
    harness.respond(
        "/view", {"status": {"active": True, "pane_id": "w1:p0"},
                  "screen": "", "transcript": None,
                  "pending": {"detected": False, "kind": None, "excerpt": None}},
        query=("pane_id",),
    )
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    harness.page.set_viewport_size({"width": 390, "height": 844})
    harness.load()
    return harness


def open_conv_sheet(page) -> None:
    page.click("#pane-picker")
    page.wait_for_function(
        "() => document.getElementById('conv-sheet').classList.contains('open')"
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#conv-spaces button.cs-space').length === 14"
    )


def test_many_spaces_leave_the_conversation_list_visible_on_a_phone(phone_app):
    page = phone_app.page
    open_conv_sheet(page)
    page.wait_for_timeout(350)  # let the sheet's slide-in transition settle

    layout = page.evaluate(
        """() => {
            const sheet = document.getElementById('conv-sheet').getBoundingClientRect();
            const spaces = document.getElementById('conv-spaces').getBoundingClientRect();
            const list = document.getElementById('conv-list');
            const row = list.querySelector('.conv-row');
            const rowBox = row ? row.getBoundingClientRect() : null;
            return {
                sheetBottom: sheet.bottom,
                spacesHeight: spaces.height,
                listHeight: list.clientHeight,
                rowTop: rowBox ? rowBox.top : null,
                rowBottom: rowBox ? rowBox.bottom : null,
            };
        }"""
    )
    # The list keeps real room: at least two 63px rows of the sheet.
    assert layout["listHeight"] >= 126, layout
    # The first conversation row is actually inside the visible sheet.
    assert layout["rowTop"] is not None, layout
    assert layout["rowBottom"] <= layout["sheetBottom"], layout
    # Spaces stay one compact row (title + one chip row).
    assert layout["spacesHeight"] <= 110, layout


def test_spaces_row_scrolls_sideways_and_every_space_stays_tappable(phone_app):
    page = phone_app.page
    open_conv_sheet(page)

    scroll = page.evaluate(
        """() => {
            const chips = document.querySelectorAll('#conv-spaces button.cs-space');
            const row = chips[0].parentElement;
            const tops = new Set([...chips].map(c => Math.round(c.getBoundingClientRect().top)));
            return {
                rows: tops.size,
                scrollable: row.scrollWidth > row.clientWidth,
                overflowX: getComputedStyle(row).overflowX,
            };
        }"""
    )
    assert scroll["rows"] == 1, scroll
    assert scroll["scrollable"], scroll
    assert scroll["overflowX"] == "auto", scroll

    # The last space can be scrolled to and is visible inside the row.
    last = page.locator("#conv-spaces button.cs-space[data-space='w13']")
    last.scroll_into_view_if_needed()
    assert last.is_visible()


def test_conversation_sheet_reaches_the_viewport_floor(phone_app):
    """The conversation sheet anchors at the viewport floor and covers the
    call footer, like the calls popup — no reserved strip below it."""
    page = phone_app.page
    open_conv_sheet(page)
    page.wait_for_timeout(350)  # let the slide-in transition settle
    geo = page.evaluate(
        """() => {
            const sheet = document.getElementById('conv-sheet');
            const s = sheet.getBoundingClientRect();
            const b = document.getElementById('call-btn').getBoundingClientRect();
            const hit = document.elementFromPoint(
                b.left + b.width / 2, b.top + b.height / 2);
            return {
                vh: window.innerHeight,
                sheetBottom: s.bottom,
                coversFooter: !!hit && sheet.contains(hit),
            };
        }"""
    )
    assert geo["sheetBottom"] >= geo["vh"] - 2, geo
    assert geo["coversFooter"], geo


def test_pending_sheet_reaches_the_viewport_floor(phone_app):
    page = phone_app.page
    page.evaluate("""() => {
        const sheet = document.getElementById('pending-sheet');
        sheet.classList.add('open');
        sheet.removeAttribute('inert');
    }""")
    page.wait_for_timeout(350)
    geo = page.evaluate("""() => ({
        bottom: document.getElementById('pending-sheet').getBoundingClientRect().bottom,
        viewport: window.innerHeight,
    })""")
    assert abs(geo["bottom"] - geo["viewport"]) <= 2, geo


def test_startup_calls_request_carries_the_device_bearer(karaoke_app):
    harness = karaoke_app()
    harness.context.add_init_script("localStorage.setItem('herdr_brain_device_token', 'fixture-token')")
    page = harness.load().page
    page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/calls')")
    calls = [r for r in harness.requests if r.url.endswith('/calls')]
    assert len(calls) == 1
    assert calls[0].headers.get('authorization') == 'Bearer fixture-token'


def test_unpaired_startup_calls_request_stays_unauthenticated(karaoke_app):
    harness = karaoke_app()
    page = harness.load().page
    page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/calls')")
    calls = [r for r in harness.requests if r.url.endswith('/calls')]
    assert len(calls) == 1
    assert 'authorization' not in calls[0].headers


def test_drawer_reader_tap_speaks_the_selected_sentence(karaoke_app):
    text = 'First sentence. Second sentence.'
    turn = {'role': 'brain', 'text': text, 'ts': '2026-01-01T00:00:00Z'}
    harness = karaoke_app(history=[turn])
    harness.respond('/call-history/rendered', {'turns': [{
        **turn, 'role': 'assistant', 'turn_id': 't0',
        'html': '<p><span class="tts-sent" data-sent-idx="0">First sentence.</span> '
                '<span class="tts-sent" data-sent-idx="1">Second sentence.</span></p>',
        'map': {'contract': 'reader-pipeline/anchors@1', 'total_sents': 2},
    }], 'has_more': False}, query=('before', 'limit'))
    page = harness.load().page
    page.evaluate("""() => {
        const drawer = document.getElementById('call-drawer');
        drawer.classList.add('open');
        drawer.removeAttribute('inert');
    }""")
    page.locator('#conversation .reader-content .tts-sent').nth(1).click()
    expect(page.locator('#tts-bar')).to_have_class('tts-open')
    expect(page.locator('#tts-status-text')).to_contain_text('Frase 2 de 2')
    assert '/tts' not in page.evaluate('() => __karaokeHarness.fetchCalls')


def test_drawer_reader_falls_back_to_server_for_selected_suffix(karaoke_app):
    text = 'First sentence. Second sentence.'
    turn = {'role': 'brain', 'text': text, 'ts': '2026-01-01T00:00:00Z'}
    harness = karaoke_app(history=[turn])
    harness.context.add_init_script("Object.defineProperty(window, 'speechSynthesis', {get: () => undefined})")
    harness.respond('/call-history/rendered', {'turns': [{
        **turn, 'role': 'assistant', 'turn_id': 't0',
        'html': '<p><span class="tts-sent" data-sent-idx="0">First sentence.</span> '
                '<span class="tts-sent" data-sent-idx="1">Second sentence.</span></p>',
        'map': {'contract': 'reader-pipeline/anchors@1', 'total_sents': 2},
    }], 'has_more': False}, query=('before', 'limit'))
    reply = harness.queue_tts()
    page = harness.load().page
    page.evaluate("""() => {
        const drawer = document.getElementById('call-drawer');
        drawer.classList.add('open');
        drawer.removeAttribute('inert');
    }""")
    page.locator('#conversation .reader-content .tts-sent').nth(1).click()
    page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/tts')")
    assert reply.request.post_data_json['text'] == 'Second sentence.'


def _submit_failing_ask(harness):
    harness.expect_blocked(harness.origin + "/ask", method="POST")
    page = harness.load().page
    page.locator("#text-input").evaluate("input => { input.value = 'Hello brain'; }")
    with page.expect_event("requestfailed", lambda r: r.url.endswith("/ask")):
        page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    return page


def test_offline_ask_failure_shows_only_the_offline_banner(karaoke_app):
    harness = karaoke_app()
    page = harness.load().page
    harness.context.set_offline(True)
    page.wait_for_function("() => navigator.onLine === false")
    harness.expect_blocked(harness.origin + "/ask", method="POST")
    page.locator("#text-input").evaluate("input => { input.value = 'Hello brain'; }")
    with page.expect_event("requestfailed", lambda r: r.url.endswith("/ask")):
        page.locator("#text-fallback").evaluate("form => form.requestSubmit()")
    page.wait_for_timeout(300)
    expect(page.locator("#offline-banner")).not_to_have_class(re.compile(r"\bhidden\b"))
    expect(page.locator("#banner")).to_have_class(re.compile(r"\bhidden\b"))


def test_online_ask_failure_still_shows_the_network_banner(karaoke_app):
    page = _submit_failing_ask(karaoke_app())
    expect(page.locator("#banner")).to_contain_text("Error de red hablando con el brain")


def test_glance_reader_still_speaks_selected_sentence(karaoke_app):
    text = 'First sentence. Second sentence.'
    turn = {'role': 'assistant', 'text': text, 'turn_id': 't0',
            'html': '<p><span class="tts-sent" data-sent-idx="0">First sentence.</span> '
                    '<span class="tts-sent" data-sent-idx="1">Second sentence.</span></p>',
            'map': {'contract': 'reader-pipeline/anchors@1', 'total_sents': 2}}
    payload = {'pane_id': 'fixture:reader', 'session_id': 'session', 'agent': 'fixture',
               'turns': [turn], 'window': 20}
    harness = karaoke_app()
    harness.respond('/conversation', payload, query=('pane_id',))
    harness.respond('/conversation/fixture%3Areader/rendered', payload)
    page = harness.load().page
    page.locator('#glance-turns .reader-content .tts-sent').nth(1).click()
    expect(page.locator('#tts-status-text')).to_contain_text('Frase 2 de 2')
    assert '/tts' not in page.evaluate('() => __karaokeHarness.fetchCalls')


def test_conversation_sheet_is_tall_on_a_phone(phone_app):
    """With enough rows the sheet grows to 85% of the viewport height."""
    page = phone_app.page
    open_conv_sheet(page)
    page.wait_for_timeout(350)
    geo = page.evaluate(
        """() => ({
            vh: window.innerHeight,
            maxH: parseFloat(getComputedStyle(document.getElementById('conv-sheet')).maxHeight),
        })"""
    )
    assert abs(geo["maxH"] - 0.85 * geo["vh"]) <= 2, geo
