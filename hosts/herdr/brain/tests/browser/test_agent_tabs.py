"""Cockpit view tabs: ONE body, two surfaces, swapped by tap.

"Vista del agente" (transcript turns, #glance-turns) and "pantalla"
(terminal preview, #view-screen) share the cockpit as tabs, so each gets
real height instead of the terminal being a permanent 110px strip under
the turns. The wiring SHAPE is pinned structurally in
tests/js/agent-tabs-wiring.test.js; this file proves the real Chromium
behavior: exactly one panel is shown, and a tap (or arrow key) swaps it.
"""

from __future__ import annotations

import pytest
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


@pytest.mark.parametrize("viewport", [
    {"width": 390, "height": 844},
    {"width": 844, "height": 390},
    {"width": 360, "height": 640},
])
@pytest.mark.parametrize("expanded_footer", [False, True])
@pytest.mark.parametrize("has_resize_observer", [False, True])
def test_the_cockpit_fills_the_vertical_space(
    karaoke_app, viewport, expanded_footer, has_resize_observer
):
    """Use the footer's actual edge, not a guessed 104px reservation.

    The keyboard fallback and safe-area padding can make the footer taller;
    the cockpit must touch it without either a gap or an overlap.
    """
    harness = karaoke_app()
    if not has_resize_observer:
        harness.context.add_init_script("window.ResizeObserver = undefined")
    harness.respond("/herd", [])
    page = harness.load().page
    page.set_viewport_size(viewport)
    if expanded_footer:
        page.evaluate("""() => {
            document.getElementById('text-fallback').classList.remove('hidden');
            const note = document.getElementById('mic-note');
            note.textContent = 'Keyboard input is available';
            note.classList.remove('hidden');
            document.querySelector('footer').style.paddingBottom = '44px';
        }""")

    page.wait_for_function("""() => {
        const cockpit = document.getElementById('agent-view').getBoundingClientRect();
        const footer = document.querySelector('footer').getBoundingClientRect();
        return Math.abs(cockpit.bottom - footer.top) <= 1;
    }""")

    m = page.evaluate("""() => {
        const r = document.getElementById('agent-view').getBoundingClientRect();
        const g = document.getElementById('glance-turns').getBoundingClientRect();
        const footer = document.querySelector('footer').getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, height: r.height, turns: g.height,
                 footerTop: footer.top, footerBottom: footer.bottom, viewport: innerHeight };
    }""")

    assert abs(m["bottom"] - m["footerTop"]) <= 1, m
    assert abs(m["footerBottom"] - m["viewport"]) <= 1, m
    assert m["height"] > 0
    assert m["turns"] > 0


def test_call_button_stays_visible_when_the_page_layout_is_taller(karaoke_app):
    """A populated page must not push the primary call action off screen."""
    harness = _load(karaoke_app)
    page = harness.page
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("""() => {
        const turns = document.getElementById('glance-turns');
        const card = document.createElement('article');
        card.className = 'gturn assistant expanded';
        const content = document.createElement('div');
        content.className = 'reader-content';
        for (let i = 0; i < 60; i++) {
            const paragraph = document.createElement('p');
            paragraph.textContent = 'Expanded transcript paragraph with markdown content. '.repeat(4);
            content.appendChild(paragraph);
        }
        card.appendChild(content);
        turns.appendChild(card);
        // Emulate a page layout exceeding the available display area.
        document.body.style.minHeight = (innerHeight + 120) + 'px';
    }""")
    geometry = page.evaluate("""() => {
        const button = document.getElementById('call-btn').getBoundingClientRect();
        const footer = document.querySelector('footer');
        return { position: getComputedStyle(footer).position,
                 buttonTop: button.top, buttonBottom: button.bottom, viewport: innerHeight };
    }""")
    assert geometry["buttonTop"] >= 0, geometry
    assert geometry["buttonBottom"] <= geometry["viewport"], geometry
    assert geometry["position"] == "fixed"
