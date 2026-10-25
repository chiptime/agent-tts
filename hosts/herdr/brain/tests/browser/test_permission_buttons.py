"""The three permission buttons on a blocked opencode pane.

The dialog is answered through POST /pane/permission/reply with opencode's
exact contract values (once | always | reject) — never by typing text, which
herdr refuses for a blocked agent.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

BLOCKED_AGENT = [
    {
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "blocked",
        "title": "OC | Pide permiso",
        "cwd": "/repo",
        "session_id": "ses_test0000session",
        "focused": True,
        "last_turn": None,
    }
]

PENDING_VIEW = {
    "status": {"active": True, "pane_id": "w1:p2", "agent": "opencode"},
    "screen": "Allow edit src/x.py?",
    "transcript": None,
    "pending": {"detected": True, "kind": "permission", "excerpt": "Allow edit src/x.py?"},
}

PENDING_PERMISSION = {
    "pending": {
        "request_id": "per_1",
        "permission": "edit",
        "patterns": ["src/x.py"],
    }
}


def _load(karaoke_app, permission=PENDING_PERMISSION):
    harness = karaoke_app()
    harness.respond("/herd", BLOCKED_AGENT)
    harness.respond("/view", PENDING_VIEW, query=("pane_id",))
    if permission is not None:
        harness.respond("/pane/permission", permission, query=("pane_id",))
    harness.respond("/pane/permission/reply", {"ok": True}, method="POST")
    harness.load()
    return harness


def test_blocked_pane_with_a_pending_request_shows_three_buttons(karaoke_app):
    page = _load(karaoke_app).page

    expect(page.locator("#pending-banner")).to_be_visible()
    expect(page.locator("#pp-once")).to_be_visible()
    expect(page.locator("#pp-always")).to_be_visible()
    expect(page.locator("#pp-reject")).to_be_visible()
    expect(page.locator("#pp-once")).to_have_text("Permitir una vez")
    expect(page.locator("#pp-always")).to_have_text("Permitir siempre")
    expect(page.locator("#pp-reject")).to_have_text("Rechazar")
    # The typed input stays out of the way: a blocked agent cannot take it.
    expect(page.locator("#pane-reply")).to_be_hidden()
    expect(page.locator("#pane-reply-keys")).to_be_hidden()


def test_clicking_once_posts_the_exact_contract(karaoke_app):
    harness = _load(karaoke_app)
    page = harness.page
    expect(page.locator("#pp-once")).to_be_visible()

    page.locator("#pp-once").click()

    page.wait_for_function(
        "() => __karaokeHarness.fetchCalls.some(u => u.indexOf('/pane/permission/reply') === 0)"
    )
    posts = [
        request.post_data
        for request in harness.requests
        if request.method == "POST" and request.url.endswith("/pane/permission/reply")
    ]
    assert posts, "no reply was posted"
    assert json.loads(posts[0]) == {
        "pane_id": "w1:p2",
        "request_id": "per_1",
        "reply": "once",
    }


def test_blocked_pane_without_a_pending_request_keeps_the_keys_notice(karaoke_app):
    """S9: a claude/codex/agy dialog, or an opencode one already answered."""
    page = _load(karaoke_app, permission={"pending": None}).page

    expect(page.locator("#pending-banner")).to_be_visible()
    expect(page.locator("#pp-once")).to_be_hidden()
    expect(page.locator("#pane-reply-keys")).to_be_visible()
