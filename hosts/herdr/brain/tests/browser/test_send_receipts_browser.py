"""Verified sends in the PWA (S12): receipt card, stop, steer.

The composer's reply comes back with a receipt over the closed outcome
set; the phone renders the outcome in Spanish (text nodes only), offers
Submit/Discard ONLY while a draft sits typed and unsubmitted, arms the
Stop button with a double tap, and exposes steer exclusively through a
long press on Enviar — never as a visible default control.
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

EFFECTIVE_RECEIPT = {
    "id": "rct-a1b2c3d4e5",
    "pane_id": "w1:p2",
    "outcome": "effective",
    "detail": "el agente terminó el turno",
    "text_hint": "",
    "created_at": 1,
    "resolved_at": None,
}

TYPED_RECEIPT = {
    "id": "rct-typed0001",
    "pane_id": "w1:p2",
    "outcome": "typed_not_submitted",
    "detail": "el texto quedó escrito en la línea de entrada sin enviarse",
    "text_hint": "sigue",
    "created_at": 1,
    "resolved_at": None,
}


def _page(karaoke_app, reply_body=None):
    harness = karaoke_app()
    harness.respond("/herd", WORKING_AGENT)
    harness.respond("/view", QUIET_VIEW, query=("pane_id",))
    if reply_body is not None:
        harness.respond("/pane/reply", reply_body, method="POST")
    page = harness.load().page
    expect(page.locator("#pane-reply")).to_be_visible()
    return harness, page


def _posts(harness, suffix):
    return [
        r for r in harness.requests
        if r.method == "POST" and r.url.endswith(suffix)
    ]


def test_effective_receipt_is_shown_and_spoken_as_sent(karaoke_app):
    harness, page = _page(
        karaoke_app,
        {"ok": True, "pane_id": "w1:p2", "receipt": EFFECTIVE_RECEIPT},
    )
    page.locator("#pane-reply-input").fill("run the tests please")
    page.locator("#pane-reply-send").click()

    expect(page.locator("#pane-receipt")).to_be_visible()
    expect(page.locator("#pane-receipt-label")).to_contain_text("Enviado")
    expect(page.locator("#pane-receipt-label")).to_contain_text("tomó el turno")
    # effective is a resolved outcome: no Submit/Discard pair.
    expect(page.locator("#pane-receipt-actions")).to_be_hidden()
    expect(page.locator("#pane-reply-input")).to_have_value("")


def test_typed_receipt_offers_submit_and_discard(karaoke_app):
    body = {"ok": False, "pane_id": "w1:p2", "receipt": TYPED_RECEIPT}
    harness, page = _page(karaoke_app, body)
    page.locator("#pane-reply-input").fill("sigue adelante")
    page.locator("#pane-reply-send").click()

    expect(page.locator("#pane-receipt")).to_be_visible()
    expect(page.locator("#pane-receipt-label")).to_contain_text("sin enviar")
    expect(page.locator("#pane-receipt-actions")).to_be_visible()

    resolved = dict(EFFECTIVE_RECEIPT, id=TYPED_RECEIPT["id"], outcome="sent")
    harness.respond(
        "/pane/receipt/rct-typed0001/submit",
        {"ok": True, "pane_id": "w1:p2", "receipt": resolved},
        method="POST",
    )
    with page.expect_request(
        lambda request: request.url.endswith("/pane/receipt/rct-typed0001/submit")
    ) as captured:
        page.locator("#pane-receipt-submit").click()

    assert json.loads(captured.value.post_data) == {"receipt_id": "rct-typed0001"}
    # Resolved: the label updates and the actions retire.
    expect(page.locator("#pane-receipt-label")).to_contain_text("Enviado")
    expect(page.locator("#pane-receipt-actions")).to_be_hidden()


def test_discard_finishes_a_typed_receipt(karaoke_app):
    body = {"ok": False, "pane_id": "w1:p2", "receipt": TYPED_RECEIPT}
    harness, page = _page(karaoke_app, body)
    page.locator("#pane-reply-input").fill("sigue adelante")
    page.locator("#pane-reply-send").click()
    expect(page.locator("#pane-receipt-actions")).to_be_visible()

    discarded = dict(EFFECTIVE_RECEIPT, id=TYPED_RECEIPT["id"], outcome="discarded")
    harness.respond(
        "/pane/receipt/rct-typed0001/discard",
        {"ok": True, "pane_id": "w1:p2", "receipt": discarded},
        method="POST",
    )
    page.locator("#pane-receipt-discard").click()

    expect(page.locator("#pane-receipt-label")).to_contain_text("Descartado")
    expect(page.locator("#pane-receipt-actions")).to_be_hidden()
    assert _posts(harness, "/pane/receipt/rct-typed0001/discard")


def test_stop_arms_on_the_first_tap_and_fires_on_the_second(karaoke_app):
    harness, page = _page(karaoke_app)
    stop = page.locator("#pane-stop")
    expect(stop).to_be_visible()
    assert "Detener" in stop.text_content()

    # First tap: arms, no request leaves.
    stop.click()
    expect(stop).to_contain_text("Toca otra vez")
    page.wait_for_timeout(200)
    assert _posts(harness, "/pane/stop") == []

    harness.respond("/pane/stop", {"ok": True, "pane_id": "w1:p2", "keys": ["esc"]}, method="POST")
    with page.expect_request(
        lambda request: request.url.endswith("/pane/stop")
    ) as captured:
        stop.click()

    assert json.loads(captured.value.post_data) == {"pane_id": "w1:p2"}
    expect(stop).to_contain_text("Detener")
    expect(stop).not_to_have_class(".*armed.*")


def test_stop_disarms_when_the_window_elapses(karaoke_app):
    harness, page = _page(karaoke_app)
    stop = page.locator("#pane-stop")

    stop.click()
    expect(stop).to_contain_text("Toca otra vez")
    # The arming window is ~3 s: past it, the tap re-arms instead of firing.
    page.wait_for_timeout(3400)
    stop.click()
    expect(stop).to_contain_text("Toca otra vez")
    assert _posts(harness, "/pane/stop") == []


def test_steer_is_hidden_and_only_a_long_press_sends_it(karaoke_app):
    body = {"ok": True, "pane_id": "w1:p2", "receipt": EFFECTIVE_RECEIPT}
    harness, page = _page(karaoke_app, body)
    # No visible steer control exists anywhere on the page.
    assert page.locator("[id*='steer'], [class*='steer']").count() == 0
    assert "steer" not in page.locator("#agent-view").inner_text().lower()

    # A normal tap never steers.
    page.locator("#pane-reply-input").fill("keep going")
    page.locator("#pane-reply-send").click()
    page.wait_for_timeout(300)
    plain = [json.loads(r.post_data) for r in _posts(harness, "/pane/reply")]
    assert plain == [{"pane_id": "w1:p2", "text": "keep going"}]

    # The deliberate secondary action: press and HOLD Enviar (~600 ms).
    page.locator("#pane-reply-input").fill("keep going")
    send = page.locator("#pane-reply-send")
    with page.expect_request(
        lambda request: request.url.endswith("/pane/reply")
    ) as captured:
        send.dispatch_event("pointerdown")
        page.wait_for_timeout(900)  # the hold timer fires inside the window

    assert json.loads(captured.value.post_data) == {
        "pane_id": "w1:p2", "text": "keep going", "steer": True,
    }
    send.dispatch_event("pointerup")
    # The hold consumed the send: the following submit must not re-send.
    page.wait_for_timeout(200)
    steered = [json.loads(r.post_data) for r in _posts(harness, "/pane/reply")]
    assert steered == [
        {"pane_id": "w1:p2", "text": "keep going"},
        {"pane_id": "w1:p2", "text": "keep going", "steer": True},
    ]
