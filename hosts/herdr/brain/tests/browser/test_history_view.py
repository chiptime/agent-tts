"""The cockpit's Historial tab (doc 05): full transcript, one cursor
page at a time.

The pure pagination state is tests/js/history.test.js; this file
proves the real Chromium behavior: the first page renders turns as
text nodes, "Cargar más" walks the returned cursor prepending older
turns without duplicates (a transcript that grew between fetches
re-serves the boundary), agent text is never markup, an agent without
a readable history is an ordinary empty state, and a 403
«dispositivo no emparejado» on /history opens the pairing flow.
"""

from __future__ import annotations

from playwright.sync_api import expect

HISTORY_QUERY = ("pane_id", "cursor", "limit")

AGENT = [{
    "pane_id": "w1:p1",
    "agent": "opencode",
    "agent_status": "working",
    "title": "OC | Historial",
    "cwd": "/repo",
    "session_id": "ses_hist0000000000000session",
    "focused": True,
    "last_turn": None,
}]


def _page(turns, cursor=None, has_more=None, reason=None):
    return {
        "pane_id": "w1:p1",
        "agent": "opencode",
        "session_id": "ses_hist0000000000000session",
        "title": "Historial",
        "turns": turns,
        "reason": reason,
        "cursor": cursor,
        "has_more": bool(cursor) if has_more is None else has_more,
        "limit": 60,
    }


def _harness(karaoke_app, history_payload):
    harness = karaoke_app()
    harness.respond("/herd", AGENT)
    harness.respond("/history", history_payload, query=HISTORY_QUERY)
    return harness.load()


def _open_history(harness):
    harness.page.locator("#tab-history").click()
    expect(harness.page.locator("#history-panel")).to_be_visible()


def test_first_page_renders_turns_as_text_nodes(karaoke_app):
    harness = _harness(karaoke_app, _page([
        {"role": "user", "text": "primera pregunta", "created_at": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "text": "respuesta completa y sin recorte", "created_at": None},
    ], cursor="Y3Iz"))
    page = harness.page
    _open_history(harness)

    turns = page.locator("#history-turns .hturn")
    expect(turns).to_have_count(2)
    expect(turns.nth(0).locator(".ht-role")).to_have_text("tú")
    expect(turns.nth(0).locator(".ht-text")).to_have_text("primera pregunta")
    expect(turns.nth(1).locator(".ht-role")).to_have_text("agente")
    expect(turns.nth(1).locator(".ht-text")).to_have_text("respuesta completa y sin recorte")
    # One first page per open — no second /history fetch landed.
    assert page.evaluate(
        "() => __karaokeHarness.fetchCalls.filter(p => p === '/history').length") == 1


def test_load_more_walks_the_cursor_and_prepends_without_duplicates(karaoke_app):
    """The returned cursor drives the next page; the prepended page never
    re-renders a turn already on screen (grown-transcript overlap)."""
    harness = _harness(karaoke_app, _page(
        [{"role": "user", "text": "borde b", "created_at": "t2"},
         {"role": "assistant", "text": "borde c", "created_at": "t3"}],
        cursor="Y3Iy"))
    page = harness.page
    _open_history(harness)
    expect(page.locator("#history-more")).to_be_visible()

    # The transcript grew: the older page re-serves the boundary turns b, c.
    harness.respond("/history", _page(
        [{"role": "user", "text": "viejo a0", "created_at": "t1"},
         {"role": "user", "text": "borde b", "created_at": "t2"},
         {"role": "assistant", "text": "borde c", "created_at": "t3"}],
        cursor=None, has_more=False), query=HISTORY_QUERY)
    page.locator("#history-more").click()

    older = next(r for r in reversed(harness.requests) if r.url.split("?")[0].endswith("/history"))
    assert "cursor=Y3Iy" in older.url and "pane_id=w1%3Ap1" in older.url
    expect(page.locator("#history-turns .hturn")).to_have_count(3)
    texts = page.locator("#history-turns .hturn .ht-text").all_inner_texts()
    assert texts == ["viejo a0", "borde b", "borde c"]
    expect(page.locator("#history-more")).to_be_hidden()


def test_turn_text_is_rendered_as_literal_text_never_markup(karaoke_app):
    harness = _harness(karaoke_app, _page(
        [{"role": "assistant",
          "text": "<img src=x onerror=alert(1)> y <script>alert(2)</script>",
          "created_at": None}]))
    page = harness.page
    _open_history(harness)

    expect(page.locator("#history-turns .hturn .ht-text")).to_contain_text(
        "<img src=x onerror=alert(1)>")
    assert page.evaluate("() => document.querySelectorAll('#history-turns img, #history-turns script').length") == 0


def test_an_agent_without_readable_history_is_an_ordinary_empty_state(karaoke_app):
    harness = _harness(karaoke_app, _page([], cursor=None, has_more=False,
                                          reason="no-session"))
    page = harness.page
    _open_history(harness)

    expect(page.locator("#history-empty")).to_be_visible()
    expect(page.locator("#history-empty")).to_contain_text("Sin historial")
    expect(page.locator("#history-more")).to_be_hidden()
    expect(page.locator("#history-turns .hturn")).to_have_count(0)


def test_parking_the_scroll_at_the_top_loads_the_next_older_page(karaoke_app):
    """Scroll-to-top is the hands-free pager: parked at the oldest edge
    with pages left, the next older page loads without the button."""
    long_text = "turno largo que desborda la lista\n" * 120
    harness = _harness(karaoke_app, _page(
        [{"role": "user", "text": long_text + " b", "created_at": "t2"},
         {"role": "assistant", "text": long_text + " c", "created_at": "t3"}],
        cursor="Y3Iy"))
    page = harness.page
    _open_history(harness)
    expect(page.locator("#history-turns .hturn")).to_have_count(2)

    harness.respond("/history", _page(
        [{"role": "user", "text": "viejo a0", "created_at": "t1"},
         {"role": "user", "text": long_text + " b", "created_at": "t2"},
         {"role": "assistant", "text": long_text + " c", "created_at": "t3"}],
        cursor=None, has_more=False), query=HISTORY_QUERY)
    # The first page parks the list at the BOTTOM (newest); scrolling
    # back up to the top edge asks for the older page by itself.
    assert page.evaluate(
        "() => document.getElementById('history-turns').scrollTop > 0")
    page.evaluate(
        "() => { const el = document.getElementById('history-turns');"
        " el.scrollTop = 0; }")
    expect(page.locator("#history-turns .hturn")).to_have_count(3)
    texts = page.locator("#history-turns .hturn .ht-text").all_inner_texts()
    assert texts[0] == "viejo a0"
    assert texts[1:] == [long_text + " b", long_text + " c"]


def test_a_403_not_paired_on_history_opens_the_pairing_flow(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", AGENT)
    harness.respond("/history", {"detail": "dispositivo no emparejado"},
                    status=403, query=HISTORY_QUERY)
    page = harness.load().page

    page.locator("#tab-history").click()
    expect(page.locator("#settings-sheet")).to_have_class("open")
    expect(page.locator(".pair-hint")).to_be_visible()


def test_the_agent_and_screen_tabs_still_swap_the_body(karaoke_app):
    """Preservation: the two original cockpit tabs behave exactly as
    before the Historial/Terminal tabs joined the tablist — one body,
    tap or arrow keys, no stacked surfaces."""
    harness = karaoke_app()
    harness.respond("/herd", AGENT)
    # The arrow walk selects the reading tabs; their reads are real
    # traffic this harness must answer.
    harness.respond("/history", _page([]), query=HISTORY_QUERY)
    harness.respond("/pane/w1%3Ap1/mirror", {
        "pane_id": "w1:p1", "agent": "opencode", "status": "working",
        "runs": [], "text": None,
    }, query=("lines",))
    harness.respond("/view", {
        "status": {"active": True, "pane_id": "w1:p1", "agent": "opencode"},
        "screen": "line one\nline two",
        "transcript": None, "pending": {"detected": False},
    }, query=("pane_id",))
    page = harness.load().page

    page.locator("#tab-screen").click()
    expect(page.locator("#tab-screen")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#screen-panel")).to_be_visible()
    expect(page.locator("#agent-panel")).to_be_hidden()
    # The pinned pre-existing arrows: screen -> agent (left), agent -> screen (right).
    page.locator("#tab-screen").press("ArrowLeft")
    expect(page.locator("#tab-agent")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#agent-panel")).to_be_visible()
    expect(page.locator("#screen-panel")).to_be_hidden()
    page.locator("#tab-agent").press("ArrowRight")
    expect(page.locator("#tab-screen")).to_have_attribute("aria-selected", "true")
    # And the whole tablist is reachable with arrows only.
    page.locator("#tab-screen").press("ArrowRight")
    expect(page.locator("#tab-history")).to_have_attribute("aria-selected", "true")
    page.locator("#tab-history").press("ArrowRight")
    expect(page.locator("#tab-terminal")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#terminal-panel")).to_be_visible()
