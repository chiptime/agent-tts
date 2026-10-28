"""The cockpit's Terminal tab (doc 05): the live pane mirror.

/pane/{id}/mirror serves the visible window as ANSI runs (masked
server-side); the view paints them with paintRunsInto — spans built by
createElement + textContent, foreground colour, weight and reverse
video, never markup. The mirror rides the existing 5s poll ONLY while
its tab is visible: a hidden tab issues no fetch at all.
"""

from __future__ import annotations

from playwright.sync_api import expect

MIRROR_PATH = "/pane/w1%3Ap1/mirror"

AGENT = [{
    "pane_id": "w1:p1",
    "agent": "opencode",
    "agent_status": "working",
    "title": "OC | Terminal",
    "cwd": "/repo",
    "session_id": "ses_mir00000000000000session",
    "focused": True,
    "last_turn": None,
}]

RUNS_V1 = [
    [{"t": "ok ", "fg": "#00ff00"}, {"t": "bold", "b": True}],
    [{"t": "sel", "rev": True}, {"t": " plain"}],
]
RUNS_V2 = [
    [{"t": "after ", "fg": "#ff8800"}, {"t": "refresh", "b": True}],
]


def _mirror(runs, text=None):
    if text is None:
        text = "\n".join("".join(run["t"] for run in line) for line in runs)
    return {"pane_id": "w1:p1", "agent": "opencode", "status": "working",
            "runs": runs, "text": text}


def _harness(karaoke_app, payload, status=200):
    harness = karaoke_app()
    harness.respond("/herd", AGENT)
    harness.respond(MIRROR_PATH, payload, status=status, query=("lines",))
    return harness.load()


def _fetch_count(page):
    return page.evaluate(
        "p => __karaokeHarness.fetchCalls.filter(x => x === p).length", MIRROR_PATH)


def test_the_mirror_paints_styled_runs_as_text(karaoke_app):
    harness = _harness(karaoke_app, _mirror(RUNS_V1))
    page = harness.page

    page.locator("#tab-terminal").click()
    expect(page.locator("#terminal-panel")).to_be_visible()
    expect(page.locator("#mirror-screen")).to_have_text("ok bold\nsel plain")

    spans = page.evaluate("""() => {
        const el = document.getElementById('mirror-screen');
        return Array.from(el.querySelectorAll('span')).map(s => ({
            text: s.textContent, color: s.style.color,
            weight: s.style.fontWeight, bg: s.style.backgroundColor}));
    }""")
    assert [s["text"] for s in spans] == ["ok ", "bold", "sel", " plain"]
    assert spans[0]["color"] == "rgb(0, 255, 0)"
    assert spans[1]["weight"] == "700"
    assert spans[2]["bg"] != "", "reverse video paints its own background"
    assert page.evaluate(
        "() => document.querySelectorAll('#mirror-screen b, #mirror-screen img').length") == 0
    # The mirror is not fetched while its tab stays hidden in other tests.
    assert _fetch_count(page) >= 1


def test_a_blank_mirror_is_an_ordinary_empty_state(karaoke_app):
    harness = _harness(karaoke_app, _mirror([], text=None))
    page = harness.page

    page.locator("#tab-terminal").click()
    expect(page.locator("#mirror-empty")).to_be_visible()
    expect(page.locator("#mirror-empty")).to_contain_text("terminal")


def test_the_mirror_refreshes_while_visible_and_stops_when_hidden(karaoke_app):
    harness = _harness(karaoke_app, _mirror(RUNS_V1))
    page = harness.page

    page.locator("#tab-terminal").click()
    expect(page.locator("#mirror-screen")).to_have_text("ok bold\nsel plain")

    # The existing 5s poll re-reads the mirror while the tab is visible.
    harness.respond(MIRROR_PATH, _mirror(RUNS_V2), query=("lines",))
    page.wait_for_function(
        "([p, text]) => __karaokeHarness.fetchCalls.filter(x => x === p).length >= 2"
        " && document.getElementById('mirror-screen').textContent.includes(text)",
        arg=[MIRROR_PATH, "after refresh"], timeout=15000)
    expect(page.locator("#mirror-screen")).to_have_text("after refresh")

    # Hidden tab: the poll beats on, the mirror does not — no fetch at all.
    page.locator("#tab-agent").click()
    expect(page.locator("#terminal-panel")).to_be_hidden()
    before = _fetch_count(page)
    page.wait_for_timeout(5500)
    assert _fetch_count(page) == before

    # Coming back re-reads immediately instead of waiting a whole beat.
    page.locator("#tab-terminal").click()
    assert _fetch_count(page) == before + 1
