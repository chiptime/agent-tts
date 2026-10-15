"""Where the typed reply is offered, and where it must not be.

``herdr agent prompt`` — the only delivery path BrainTools has — refuses an
agent that is already waiting at an approval or question dialog
(``agent_blocked``, before any input is written). So the free-text reply is
offered only for a pane where herdr would actually accept it; a blocked pane
gets the notice that its dialog is answered with key presses, which the PWA
does not synthesize on the user's behalf.

Both cases anchor on ``#pending-banner`` being visible first, so a hidden
reply form can never pass vacuously before the polls land.
"""

from __future__ import annotations

from playwright.sync_api import expect

BLOCKED_AGENT = [
    {
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "blocked",
        "title": "OC | Espera permiso",
        "cwd": "/repo",
        "session_id": "ses_test0000session",
        "focused": True,
        "last_turn": None,
    }
]

WORKING_AGENT = [{**BLOCKED_AGENT[0], "agent_status": "working", "title": "OC | Trabajando"}]

PENDING_VIEW = {
    "status": {"active": True, "pane_id": "w1:p2", "agent": "opencode"},
    "screen": "Allow? [y/n]",
    "transcript": None,
    "pending": {"detected": True, "kind": "permission", "excerpt": "Allow? [y/n]"},
}


def _load(karaoke_app, herd):
    harness = karaoke_app()
    harness.respond("/herd", herd)
    harness.respond("/view", PENDING_VIEW, query=("pane_id",))
    return harness.load()


def test_blocked_pane_offers_no_typed_reply(karaoke_app):
    page = _load(karaoke_app, BLOCKED_AGENT).page

    expect(page.locator("#pending-banner")).to_be_visible()
    expect(page.locator("#pane-reply")).to_be_hidden()
    expect(page.locator("#pane-reply-keys")).to_be_visible()


def test_pending_but_not_blocked_pane_offers_the_typed_reply(karaoke_app):
    page = _load(karaoke_app, WORKING_AGENT).page

    expect(page.locator("#pending-banner")).to_be_visible()
    expect(page.locator("#pane-reply")).to_be_visible()
    expect(page.locator("#pane-reply-keys")).to_be_hidden()
