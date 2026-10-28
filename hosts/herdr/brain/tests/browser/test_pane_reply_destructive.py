"""The destructive-command guard on the typed reply (Panel parity S6).

``POST /pane/reply`` types into a real terminal, so a reply matching one of
the reviewed destructive patterns (``static/destructive.js``) needs a second,
explicit confirmation before it is sent. An ordinary reply keeps the single
tap. The guard runs on the final composer text at send time, so typed and
dictated text take the same path.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

WORKING_AGENT = [
    {
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
        "title": "OC | Trabajando",
        "cwd": "/repo",
        "session_id": "ses_test0000session",
        "focused": True,
        "last_turn": None,
    }
]

QUIET_VIEW = {
    "status": {"active": True, "pane_id": "w1:p2", "agent": "opencode", "agent_status": "working"},
    "screen": "$ pytest -q",
    "transcript": None,
    "pending": {"detected": False, "kind": None, "excerpt": None},
}


def _page(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", WORKING_AGENT)
    harness.respond("/view", QUIET_VIEW, query=("pane_id",))
    harness.respond("/pane/reply", {"ok": True}, method="POST")
    page = harness.load().page
    expect(page.locator("#pane-reply")).to_be_visible()
    return harness, page


def _reply_posts(harness):
    return [r for r in harness.requests if r.method == "POST" and r.url.endswith("/pane/reply")]


def test_a_dismissed_confirmation_sends_nothing(karaoke_app):
    harness, page = _page(karaoke_app)
    dialogs = []

    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", dismiss)
    page.locator("#pane-reply-input").fill("rm -rf build")
    page.locator("#pane-reply-send").click()

    page.wait_for_timeout(300)
    assert len(dialogs) == 1
    assert "rm -r" in dialogs[0] and "rm -rf build" in dialogs[0]
    assert _reply_posts(harness) == []
    expect(page.locator("#pane-reply-input")).to_have_value("rm -rf build")


def test_an_accepted_confirmation_sends_the_reply_unchanged(karaoke_app):
    harness, page = _page(karaoke_app)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator("#pane-reply-input").fill("git push --force origin main")

    with page.expect_request(
        lambda request: request.url.endswith("/pane/reply") and request.method == "POST"
    ) as captured:
        page.locator("#pane-reply-send").click()

    assert json.loads(captured.value.post_data) == {
        "pane_id": "w1:p2", "text": "git push --force origin main",
    }
    expect(page.locator("#pane-reply-input")).to_have_value("")


def test_an_ordinary_reply_sends_with_one_tap_and_no_dialog(karaoke_app):
    harness, page = _page(karaoke_app)
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.locator("#pane-reply-input").fill("run the tests please")

    with page.expect_request(
        lambda request: request.url.endswith("/pane/reply") and request.method == "POST"
    ) as captured:
        page.locator("#pane-reply-send").click()

    assert json.loads(captured.value.post_data) == {"pane_id": "w1:p2", "text": "run the tests please"}
    assert dialogs == []
    assert len(_reply_posts(harness)) == 1
