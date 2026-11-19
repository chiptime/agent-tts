"""The PWA send-tracking card (parity-f7g S5).

Every tracked send paints a persistent card under the composer that
follows the send to its outcome: Spanish copy, a locally ticking m:ss
elapsed while non-terminal, state from BOTH the `send.track` SSE events
and the `track` object on GET /pane/receipts (whichever arrives), a
dismiss once terminal, and a per-pane map so switching panes repaints
that pane's own tracker. The error message is masked server-side and
renders as text. Untracked outcomes (typed_not_submitted …) never
start a card and never poll.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

WORKING_AGENT = {
    "pane_id": "w1:p2",
    "agent": "opencode",
    "agent_status": "working",
    "title": "OC | Trabajando",
    "cwd": "/repo",
    "session_id": "ses_test0000session",
    "focused": True,
    "last_turn": None,
}

OTHER_AGENT = dict(
    WORKING_AGENT, pane_id="w1:p3", agent="claude", title="CL | Otro",
)


def _view(pane_id):
    return {
        "status": {"active": True, "pane_id": pane_id, "agent": "opencode",
                   "agent_status": "working"},
        "screen": "$ pytest -q",
        "transcript": None,
        "pending": {"detected": False, "kind": None, "excerpt": None},
    }


SENT_RECEIPT = {
    "id": "rct-track0001",
    "pane_id": "w1:p2",
    "outcome": "sent",
    "detail": "sin confirmación aún",
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


def _seconds(text):
    match = re.search(r"(\d+):(\d+)", text or "")
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _page(karaoke_app, reply_body=None, receipts=None, herd=None):
    harness = karaoke_app()
    harness.respond("/herd", herd or [WORKING_AGENT])
    harness.respond("/view", _view("w1:p2"), query=("pane_id",))
    if reply_body is not None:
        harness.respond("/pane/reply", reply_body, method="POST")
    if receipts is not None:
        harness.respond("/pane/receipts", receipts, query=("pane_id",))
    page = harness.load().page
    expect(page.locator("#pane-reply")).to_be_visible()
    return harness, page


def _send(page):
    page.locator("#pane-reply-input").fill("run the tests please")
    page.locator("#pane-reply-send").click()
    expect(page.locator("#pane-track")).to_be_visible()


def _select_pane(page, pane):
    """Opens the pane sheet and taps one row (the established pattern)."""
    page.locator("#pane-picker").click()
    page.locator('.conv-row[data-pane="' + pane + '"]').click()


def _track_event(page, **fields):
    payload = {"type": "send.track", "pane_id": "w1:p2"}
    payload.update(fields)
    harness_page = page
    harness_page.evaluate(
        "payload => __karaokeHarness.eventSources[0].emit('message', "
        "{data: JSON.stringify(payload)})",
        payload,
    )


def test_tracker_card_follows_a_send_to_its_terminal_outcome(karaoke_app):
    # The poll listing carries NO track: SSE drives, the poll must never
    # regress it (the "never regress on a miss" rule).
    harness, page = _page(
        karaoke_app,
        {"ok": True, "pane_id": "w1:p2", "receipt": SENT_RECEIPT},
        receipts={"receipts": [dict(SENT_RECEIPT)]},
    )
    # The card is a polite live region (S5 accessibility).
    assert page.locator("#pane-track").get_attribute("aria-live") == "polite"

    _send(page)
    expect(page.locator("#pane-track-state")).to_contain_text("Enviado")
    expect(page.locator("#pane-track-dismiss")).to_be_hidden()

    _track_event(page, receipt_id="rct-track0001", state="started", elapsed_s=1)
    expect(page.locator("#pane-track-state")).to_contain_text("Empezó")

    _track_event(page, receipt_id="rct-track0001", state="working", elapsed_s=5)
    expect(page.locator("#pane-track-state")).to_contain_text("Trabajando")
    # The elapsed ticks locally (m:ss, from sent_at), not from the event.
    expect(page.locator("#pane-track-elapsed")).to_have_text(re.compile(r"· 0:0\d"))

    first = _seconds(page.locator("#pane-track-elapsed").text_content())
    # The repaint rides the always-on 1s tick: 3.2 s guarantees the
    # displayed second advances by at least two, whatever the phase.
    page.wait_for_timeout(3200)
    second = _seconds(page.locator("#pane-track-elapsed").text_content())
    assert first is not None and second is not None and second >= first + 2

    # The stalled flag is a warning, not a terminal: no dismiss yet.
    _track_event(page, receipt_id="rct-track0001", state="working",
                 elapsed_s=200, stalled=True)
    expect(page.locator("#pane-track-state")).to_contain_text(
        "Parece colgado (3 min sin actividad)")
    expect(page.locator("#pane-track-dismiss")).to_be_hidden()

    # Terminal error: the masked message renders as text, the elapsed
    # stops, the dismiss appears.
    _track_event(page, receipt_id="rct-track0001", state="error", elapsed_s=210,
                 error={"name": "APIError", "message": "cuota agotada"})
    expect(page.locator("#pane-track-state")).to_have_text("Error: cuota agotada")
    expect(page.locator("#pane-track-elapsed")).to_have_text("")
    expect(page.locator("#pane-track-dismiss")).to_be_visible()

    page.locator("#pane-track-dismiss").click()
    expect(page.locator("#pane-track")).to_be_hidden()
    # The polling feed was engaged for the viewed pane's tracker.
    assert any(
        r.url.endswith("/pane/receipts?pane_id=w1%3Ap2") or
        "/pane/receipts?pane_id=" in r.url
        for r in harness.requests if r.method == "GET"
    )


def test_no_start_converges_through_receipts_polling_without_sse(karaoke_app):
    # No SSE event is emitted: the card must converge from the track
    # object on GET /pane/receipts alone.
    tracked = dict(
        SENT_RECEIPT,
        track={"state": "no_start", "sent_at": 1732000000.0,
               "elapsed_s": 15.0, "stalled": False},
    )
    harness, page = _page(
        karaoke_app,
        {"ok": True, "pane_id": "w1:p2", "receipt": SENT_RECEIPT},
        receipts={"receipts": [tracked]},
    )
    _send(page)
    # The hot poll (first ~12 s at 1.5 s) lands the state within a beat.
    expect(page.locator("#pane-track-state")).to_contain_text(
        "No arrancó en 15 s — revisa el agente")
    expect(page.locator("#pane-track-dismiss")).to_be_visible()
    polls = [r for r in harness.requests
             if r.method == "GET" and "/pane/receipts" in r.url]
    assert polls, "the receipts poll must run while the tracker is in flight"


def test_typed_receipt_keeps_submit_discard_and_never_tracks(karaoke_app):
    # PRESERVE: the pre-f7g receipt behavior is untouched — a typed
    # draft offers Submit/Discard, the follow-up resolves the card, and
    # an untracked outcome starts no tracker card and no polling.
    harness, page = _page(
        karaoke_app,
        {"ok": False, "pane_id": "w1:p2", "receipt": TYPED_RECEIPT},
    )
    page.locator("#pane-reply-input").fill("sigue adelante")
    page.locator("#pane-reply-send").click()

    expect(page.locator("#pane-receipt")).to_be_visible()
    expect(page.locator("#pane-receipt-label")).to_contain_text("sin enviar")
    expect(page.locator("#pane-receipt-actions")).to_be_visible()
    expect(page.locator("#pane-track")).to_be_hidden()

    resolved = dict(SENT_RECEIPT, id=TYPED_RECEIPT["id"], outcome="sent")
    harness.respond(
        "/pane/receipt/rct-typed0001/submit",
        {"ok": True, "pane_id": "w1:p2", "receipt": resolved},
        method="POST",
    )
    page.locator("#pane-receipt-submit").click()
    expect(page.locator("#pane-receipt-label")).to_contain_text("Enviado")
    expect(page.locator("#pane-receipt-actions")).to_be_hidden()

    # Long enough for a hot poll to have fired had the submit been
    # tracked: the tracker card stays hidden and no receipts poll runs.
    page.wait_for_timeout(2200)
    expect(page.locator("#pane-track")).to_be_hidden()
    assert not [r for r in harness.requests
                if r.method == "GET" and "/pane/receipts" in r.url]


def test_switching_panes_repaints_that_panes_own_tracker(karaoke_app):
    harness, page = _page(
        karaoke_app,
        {"ok": True, "pane_id": "w1:p2", "receipt": SENT_RECEIPT},
        receipts={"receipts": [dict(SENT_RECEIPT)]},
        herd=[WORKING_AGENT, OTHER_AGENT],
    )
    _send(page)
    expect(page.locator("#pane-track-state")).to_contain_text("Enviado")

    # Switch to the other pane: no tracker there, the card hides.
    harness.respond("/view", _view("w1:p3"), query=("pane_id",))
    _select_pane(page, "w1:p3")
    expect(page.locator("#pane-track")).to_be_hidden()

    # Switch back: the pane's own tracker repaints from the local map.
    harness.respond("/view", _view("w1:p2"), query=("pane_id",))
    _select_pane(page, "w1:p2")
    expect(page.locator("#pane-track")).to_be_visible()
    expect(page.locator("#pane-track-state")).to_contain_text("Enviado")
