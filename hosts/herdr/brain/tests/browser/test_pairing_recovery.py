"""The PWA's migration path on a "not paired" 403 (Panel parity F1, S4).

Pairing the first device removes write access from every unpaired
client; the recovery the operator sees is the pairing hint (`.pair-hint`)
in Ajustes, offered automatically when a write route answers 403
«dispositivo no emparejado» (T4 blocker B3: the hint never showed
because the caller passed the parsed body while the matcher required a
`.status` field on it).
"""

from __future__ import annotations

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

NOT_PAIRED = {"detail": "dispositivo no emparejado"}


def _page(karaoke_app, *, reply_status=200, reply_payload=None):
    harness = karaoke_app()
    harness.respond("/herd", WORKING_AGENT)
    harness.respond("/view", QUIET_VIEW, query=("pane_id",))
    harness.respond(
        "/pane/reply",
        reply_payload if reply_payload is not None else {"ok": True},
        method="POST",
        status=reply_status,
    )
    page = harness.load().page
    expect(page.locator("#pane-reply")).to_be_visible()
    return harness, page


def test_not_paired_403_on_a_write_opens_the_pairing_hint(karaoke_app):
    harness, page = _page(
        karaoke_app, reply_status=403, reply_payload=NOT_PAIRED,
    )

    # The hint starts hidden: nothing has been refused yet.
    assert page.locator(".pair-hint").count() == 0

    page.locator("#pane-reply-input").fill("deploy the fix")
    page.locator("#pane-reply-send").click()

    # The refused write opens Ajustes with the pairing hint: the code
    # the operator mints with `herdr-brain pair` is the way back in.
    expect(page.locator("#settings-sheet")).to_have_class("open")
    expect(page.locator(".pair-hint")).to_be_visible()
    assert "herdr-brain pair" in page.locator(".pair-hint").text_content()


def test_other_403s_do_not_offer_pairing(karaoke_app):
    """Only the not-paired text opens the flow: a host/origin 403 is an
    operator problem, and offering a code would be a dead end."""
    harness, page = _page(
        karaoke_app, reply_status=403, reply_payload={"detail": "host no permitido"},
    )

    page.locator("#pane-reply-input").fill("deploy the fix")
    page.locator("#pane-reply-send").click()
    page.wait_for_timeout(300)

    assert page.locator(".pair-hint").count() == 0
    assert "open" not in (page.locator("#settings-sheet").get_attribute("class") or "")
