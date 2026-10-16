"""Where the typed reply is offered, and where it must not be.

Typing is the normal way to drive an agent, so the free-text reply is offered
for whichever pane is on screen — NOT only when the screen-text heuristic
(:func:`herdr_brain.view.detect_pending`) happens to see a pending prompt.
That gate made the input flicker in and out of the cockpit.

The ONE exception is an agent already waiting at an approval or question
dialog: ``herdr agent prompt`` — the only delivery path BrainTools has —
refuses it (``agent_blocked``, before any input is written). That pane gets
the notice that its dialog is answered with key presses, which the PWA does
not synthesize on the user's behalf.

The blocked case anchors on ``#pending-banner`` being visible first, so a
hidden reply form can never pass vacuously before the polls land. The
offered cases assert the form VISIBLE, which only ``renderPending`` can do
(the form ships ``hidden`` in the markup and its ancestor ``#agent-view`` is
revealed by the same ``/view`` render).
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

# An ordinary working pane: nothing on screen trips the pending heuristic.
QUIET_VIEW = {
    "status": {
        "active": True,
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
    },
    "screen": "$ pytest -q",
    "transcript": None,
    "pending": {"detected": False, "kind": None, "excerpt": None},
}


def _load(karaoke_app, herd, view=PENDING_VIEW):
    harness = karaoke_app()
    harness.respond("/herd", herd)
    harness.respond("/view", view, query=("pane_id",))
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


def test_a_working_pane_with_nothing_pending_still_offers_the_typed_reply(karaoke_app):
    """The old gate: the input only appeared when the heuristic saw an ask."""
    page = _load(karaoke_app, WORKING_AGENT, view=QUIET_VIEW).page

    # The status chip proves the /view render landed (it ships as "…").
    expect(page.locator("#status-chip")).to_have_text("working")
    expect(page.locator("#pending-banner")).to_be_hidden()
    expect(page.locator("#pane-reply")).to_be_visible()
    expect(page.locator("#pane-reply-keys")).to_be_hidden()
