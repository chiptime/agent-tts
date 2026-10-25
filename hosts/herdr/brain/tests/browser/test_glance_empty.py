"""A terminal snapshot must not hide an empty conversation's explanation."""

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("session", [None, "fixture-session"])
def test_empty_reader_with_screen_recovers_when_turns_arrive(karaoke_app, session):
    harness = karaoke_app()
    page = harness.page
    harness.context.add_init_script("""(() => {
        const interval = window.setInterval;
        window.setInterval = (callback, ms, ...args) => {
            if (ms === 5000) { window.__pollGlance = callback; return 0; }
            return interval(callback, ms, ...args);
        };
    })();""")
    data = {"pane_id": "fixture:empty", "agent": "agy", "session_id": session,
            "turns": [], "window": 20}
    harness.respond("/conversation", data, query=("pane_id",))
    harness.respond("/conversation/fixture%3Aempty/rendered", data)
    harness.respond("/view", {"status": {"active": False}, "screen": "Terminal is available.",
                              "pending": {"detected": False}}, query=("pane_id",))
    harness.load()
    expect(page.locator("#view-screen")).to_have_text("Terminal is available.")
    # Poll after the terminal snapshot has arrived, not just during boot.
    with page.expect_response(lambda response: "/rendered" in response.url):
        page.evaluate("() => __pollGlance()")
    empty = page.locator("#glance-empty")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("Pantalla")
    expect(empty).to_contain_text("sesión" if session is None else "conversación")
    page.locator("#tab-screen").click()
    expect(page.locator("#view-screen")).to_be_visible()
    page.locator("#tab-agent").click()
    expect(empty).to_be_visible()

    data = {**data, "session_id": "fixture-session", "turns": [
        {"role": "assistant", "text": "The conversation is now available."}]}
    harness.respond("/conversation", data, query=("pane_id",))
    harness.respond("/conversation/fixture%3Aempty/rendered", data)
    with page.expect_response(lambda response: "/rendered" in response.url):
        page.evaluate("() => __pollGlance()")
    expect(empty).to_be_hidden()
    expect(page.locator("#glance-turns .gt-text")).to_have_text(
        "The conversation is now available.")
    expect(page.locator("#glance-turns .replay-btn")).to_have_count(1)
