"""Cockpit view tabs: ONE body, two surfaces, swapped by tap.

"Vista del agente" (transcript turns, #glance-turns) and "pantalla"
(terminal preview, #view-screen) share the cockpit as tabs, so each gets
real height instead of the terminal being a permanent 110px strip under
the turns. The wiring SHAPE is pinned structurally in
tests/js/agent-tabs-wiring.test.js; this file proves the real Chromium
behavior: exactly one panel is shown, and a tap (or arrow key) swaps it.
"""

from __future__ import annotations

from playwright.sync_api import expect

SCREEN_TEXT = "line one\nline two"
TURNS = {
    "pane_id": None,
    "session_id": None,
    "agent": "opencode",
    "turns": [
        {"role": "user", "text": "pregunta"},
        {"role": "assistant", "text": "respuesta del agente"},
    ],
    "window": 20,
}
VIEW_WITH_SCREEN = {
    "status": {"active": True, "pane_id": "w1:p2", "agent": "opencode"},
    "screen": SCREEN_TEXT,
    "transcript": None,
    "pending": {"detected": False},
}


def _load(karaoke_app):
    harness = karaoke_app()
    harness.respond("/view", VIEW_WITH_SCREEN, query=("pane_id",))
    harness.respond("/conversation", TURNS, query=("pane_id",))
    return harness.load()


def test_agent_view_opens_on_the_agent_tab(karaoke_app):
    page = _load(karaoke_app).page
    expect(page.locator("#tab-agent")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#tab-screen")).to_have_attribute("aria-selected", "false")
    expect(page.locator("#agent-panel")).to_be_visible()
    expect(page.locator("#glance-turns .gturn")).to_have_count(2)
    expect(page.locator("#screen-panel")).to_be_hidden()


def test_the_screen_tab_swaps_the_body(karaoke_app):
    page = _load(karaoke_app).page

    page.locator("#tab-screen").click()
    expect(page.locator("#tab-screen")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#screen-panel")).to_be_visible()
    expect(page.locator("#view-screen")).to_have_text(SCREEN_TEXT)
    expect(page.locator("#agent-panel")).to_be_hidden()
    expect(page.locator("#glance-turns")).to_be_hidden()

    page.locator("#tab-agent").click()
    expect(page.locator("#agent-panel")).to_be_visible()
    expect(page.locator("#glance-turns .gturn")).to_have_count(2)
    expect(page.locator("#screen-panel")).to_be_hidden()


def test_arrow_keys_move_between_the_tabs(karaoke_app):
    page = _load(karaoke_app).page

    page.locator("#tab-agent").press("ArrowRight")
    expect(page.locator("#tab-screen")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#screen-panel")).to_be_visible()

    page.locator("#tab-screen").press("ArrowLeft")
    expect(page.locator("#tab-agent")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#agent-panel")).to_be_visible()


def test_a_stored_tab_choice_is_honored_on_load(karaoke_app):
    harness = karaoke_app()
    harness.context.add_init_script(
        "localStorage.setItem('herdr-brain-agent-tab', 'screen');")
    harness.respond("/view", VIEW_WITH_SCREEN, query=("pane_id",))
    harness.respond("/conversation", TURNS, query=("pane_id",))

    page = harness.load().page
    expect(page.locator("#screen-panel")).to_be_visible()
    expect(page.locator("#agent-panel")).to_be_hidden()


def test_styled_screen_runs_reach_the_screen_without_letting_markup_through(karaoke_app):
    """The dialog's highlight must survive, and agent output must stay text.

    "Which option is selected" exists only as colour/attribute, so the screen
    arrives as runs. They are painted with createElement + textContent: a
    faithful mirror, never an interpretation, and never markup.
    """
    harness = karaoke_app()
    view = dict(VIEW_WITH_SCREEN)
    view["screen_runs"] = [
        [
            {"t": "Allow once", "bg": "#3c3c3c"},
            {"t": " "},
            {"t": "<b>Allow always</b>", "b": True},
        ],
    ]
    harness.respond("/view", view, query=("pane_id",))
    harness.respond("/conversation", TURNS, query=("pane_id",))

    page = harness.load().page
    page.locator("#tab-screen").click()

    expect(page.locator("#view-screen")).to_have_text("Allow once <b>Allow always</b>")
    first = page.evaluate(
        "() => { const s = document.querySelector('#view-screen span');"
        " return [s.style.background, s.textContent]; }"
    )
    # Foreground only: the terminal's own background is NOT painted, because
    # doing so produced a black box with black text on our dark theme.
    assert first == ["", "Allow once"]
    assert page.evaluate("() => document.querySelectorAll('#view-screen b').length") == 0


def test_the_cockpit_fills_the_vertical_space(karaoke_app):
    """#agent-view takes EVERYTHING between the header and the footer line.

    Pinned at a phone viewport (390x844): the section's bottom must sit on
    the footer clearance (viewport minus the fixed footer's 104px) and the
    turn list must keep the large majority of that height for itself.
    """
    harness = karaoke_app()
    harness.respond("/herd", [])
    page = harness.load().page
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)

    m = page.evaluate("""() => {
        const r = document.getElementById('agent-view').getBoundingClientRect();
        const g = document.getElementById('glance-turns').getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, height: r.height, turns: g.height,
                 footerLine: innerHeight - 104 };
    }""")

    assert m["bottom"] == m["footerLine"]
    assert m["height"] > 600
    assert m["turns"] > 500
