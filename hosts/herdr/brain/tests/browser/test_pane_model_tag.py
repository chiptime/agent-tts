"""The pane's model shows in the herd rows and the pane picker (parity F7a S3).

A pane whose /herd entry carries ``model`` shows it in its sub-line next to
the workspace; a pane with ``model: null`` shows no model text.
"""

from __future__ import annotations

from playwright.sync_api import expect

A, B = "w1:p2", "w1:p9"


def load(karaoke_app):
    harness = karaoke_app()
    harness.respond(
        "/herd",
        [
            dict(
                pane_id=A, agent="claude", agent_status="idle", title="Claude",
                cwd="/home/u/repo", session_id="sa", model="claude-opus-5-5",
                focused=True, last_turn=None,
            ),
            dict(
                pane_id=B, agent="pi", agent_status="idle", title="Pi",
                cwd="/home/u/repo", session_id="sb", model=None,
                focused=False, last_turn=None,
            ),
        ],
    )
    harness.respond(
        "/view",
        {
            "status": {"active": True, "pane_id": A, "agent_status": "idle",
                       "model": "claude-opus-5-5"},
            "screen": "", "transcript": None,
            "pending": {"detected": False, "kind": None, "excerpt": None},
        },
        query=("pane_id",),
    )
    harness.load()
    return harness


def test_herd_rows_show_the_model_only_when_known(karaoke_app):
    page = load(karaoke_app).page
    rows = page.locator(".conv-row .cr-sub")
    expect(page.locator('.conv-row[data-pane="' + A + '"] .cr-sub')).to_have_text(
        "repo · claude-opus-5-5"
    )
    expect(page.locator('.conv-row[data-pane="' + B + '"] .cr-sub')).to_have_text("repo")
    assert rows.count() == 2


def test_pane_picker_shows_the_selected_pane_model(karaoke_app):
    page = load(karaoke_app).page
    expect(page.locator("#pane-picker")).to_contain_text("claude-opus-5-5")
