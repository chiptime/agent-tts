"""Cockpit dock (POST-07): tabs row + reply composer live at the FLOOR.

The operator's complaint: the zone [tabs Vista/Terminal/Ficheros + model
tag + search + fullscreen + reply input + Stop + Send] reads as clutter
stacked between the header and the transcript. Option C (approved
2026-11-26) docks BOTH rows at the bottom of #agent-view as one
two-row dock — tabs above composer — leaving only the header at the top,
so reading starts right under the header and the dock fits ~90px.

Proven with real boundingClientRect geometry on a phone (360×740) and a
desktop (1280×800) viewport. Pure CSS repositioning (flex `order`): the
tab/composer IDs, roles and i18n attributes must stay untouched.
"""

from __future__ import annotations

from playwright.sync_api import expect

AGENT = [
    {
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
        "title": "OC | Trabajando",
        "cwd": "/repo",
        "session_id": "ses_test0000session",
        "model": "claude-opus-5-5",
        "focused": True,
        "last_turn": None,
    }
]

QUIET_VIEW = {
    "status": {
        "active": True,
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
        "model": "claude-opus-5-5",
    },
    "screen": "$ pytest -q",
    "transcript": None,
    "pending": {"detected": False, "kind": None, "excerpt": None},
}

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

GEOMETRY_JS = """() => {
  const rect = (sel) => {
    const el = document.querySelector(sel);
    return el ? el.getBoundingClientRect() : null;
  };
  return {
    tabs: rect('#agent-tabs'),
    composer: rect('#pane-reply'),
    footer: rect('footer'),
    turns: rect('#glance-turns'),
    viewport: { w: innerWidth, h: innerHeight },
  };
}"""


def _load(karaoke_app, viewport):
    harness = karaoke_app()
    harness.respond("/herd", AGENT)
    harness.respond("/view", QUIET_VIEW, query=("pane_id",))
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    harness.respond("/conversation", TURNS, query=("pane_id",))
    page = harness.load().page
    page.set_viewport_size(viewport)
    # The status chip proves the /view render (and cockpit reveal) landed.
    expect(page.locator("#status-chip")).to_have_text("working")
    expect(page.locator("#pane-reply")).to_be_visible()
    expect(page.locator("#agent-tabs")).to_be_visible()
    expect(page.locator("#tab-model-tag")).to_be_visible()
    return page


def test_mobile_docks_tabs_and_composer_at_the_floor(karaoke_app):
    """360×740: tabs+composer adjacent, flush above the footer, ≤100px."""
    page = _load(karaoke_app, {"width": 360, "height": 740})
    m = page.evaluate(GEOMETRY_JS)

    # Only the header sits above the transcript: reading starts within
    # 100px of the viewport top (header ~65px + cockpit hairline). The
    # dock itself lives at the floor (asserted below).
    assert m["turns"]["top"] <= 100, m

    # One dock: tabs directly above the composer, composer flush on the
    # footer's edge — no gap, no overlap.
    assert abs(m["tabs"]["bottom"] - m["composer"]["top"]) <= 2, m
    assert abs(m["composer"]["bottom"] - m["footer"]["top"]) <= 2, m

    # The dock's whole vertical cost stays ≤100px (was 111px: 51 + 60).
    dock = m["tabs"]["height"] + m["composer"]["height"]
    assert dock <= 100, m

    # The transcript still owns real height below the header.
    assert m["turns"]["height"] > 100, m


def test_desktop_dock_centers_in_the_reading_column(karaoke_app):
    """1280×800: the dock reads as one centered 760px card, not full-width."""
    page = _load(karaoke_app, {"width": 1280, "height": 800})
    m = page.evaluate(GEOMETRY_JS)

    center = m["viewport"]["w"] / 2
    for name in ("tabs", "composer"):
        row = m[name]
        assert row["width"] <= 762, m
        row_center = (row["left"] + row["right"]) / 2
        assert abs(row_center - center) <= 8, m

    assert abs(m["tabs"]["bottom"] - m["composer"]["top"]) <= 2, m
    assert abs(m["composer"]["bottom"] - m["footer"]["top"]) <= 2, m
