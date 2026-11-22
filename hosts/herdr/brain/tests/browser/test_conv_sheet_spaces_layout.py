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

import pytest

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
