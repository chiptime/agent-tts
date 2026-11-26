"""Cockpit top zone (POST-07): compact composer + tabs stack under the header.

The operator first approved a floor dock (option C) and rejected it after
seeing it: "no me convence, mejor todo arriba". The final layout keeps the
DOM order — reply composer, then the tabs row, then the transcript — but
compact: panel background, hairlines between rows, 40px tabs, 2px composer
padding, real ellipsis on squeezed tab labels, model tag capped on phones.
The whole zone costs <=100px instead of the old 113px.

Proven with real boundingClientRect geometry on a phone (360×740) and a
desktop (1280×800) viewport. Pure CSS: the tab/composer IDs, roles and
i18n attributes must stay untouched.
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
    composer: rect('#pane-reply'),
    tabs: rect('#agent-tabs'),
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


def test_mobile_composer_and_tabs_stack_compact_under_the_header(karaoke_app):
    """360×740: composer under the header, tabs below it, zone <=100px."""
    page = _load(karaoke_app, {"width": 360, "height": 740})
    m = page.evaluate(GEOMETRY_JS)

    # The composer sits right under the header (no banner in this view):
    # the whole interactive zone starts within 100px of the viewport top.
    assert m["composer"]["top"] <= 100, m

    # One stack: composer, then tabs, then the transcript — adjacent rows,
    # no gaps.
    assert abs(m["composer"]["bottom"] - m["tabs"]["top"]) <= 2, m
    assert abs(m["tabs"]["bottom"] - m["turns"]["top"]) <= 2, m

    # The zone's whole vertical cost stays <=100px (was ~113px).
    zone = m["composer"]["height"] + m["tabs"]["height"]
    assert zone <= 100, m

    # The transcript still owns real height below the zone.
    assert m["turns"]["height"] > 100, m


def test_desktop_composer_and_tabs_center_in_the_reading_column(karaoke_app):
    """1280×800: the stack reads as one centered 760px card, not full-width."""
    page = _load(karaoke_app, {"width": 1280, "height": 800})
    m = page.evaluate(GEOMETRY_JS)

    center = m["viewport"]["w"] / 2
    for name in ("composer", "tabs"):
        row = m[name]
        assert row["width"] <= 762, m
        row_center = (row["left"] + row["right"]) / 2
        assert abs(row_center - center) <= 8, m

    assert abs(m["composer"]["bottom"] - m["tabs"]["top"]) <= 2, m
    assert abs(m["tabs"]["bottom"] - m["turns"]["top"]) <= 2, m
