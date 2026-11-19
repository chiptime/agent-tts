"""The held Claude hook permission card (f7e S3), browser-side.

A Claude pane whose PreToolUse hook is blocked on the brain socket
surfaces as a global card: it renders from the /hook/permissions beat,
Permitir/Denegar answer the OLDEST hold through the device-gated POST,
and the card refreshes after the answer. No undeclared traffic: the
routes the card touches are declared, everything else must stay quiet.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

HELD = {
    "request_id": "hp-fixed-request-id-000001",
    "tool_name": "Bash",
    "summary": "command: echo hook-probe",
    "session_id": "ses-abc",
    "cwd": "/repo",
    "pane_id": "w1:p3",
    "agent": "claude",
    "created_at": 0.0,
    "expires_in_s": 119.0,
}


def _page_with_hold(karaoke_app, pending):
    harness = karaoke_app()
    harness.respond("/hook/permissions", {"pending": pending})
    return harness, harness.load().page


def test_card_hidden_with_nothing_held(karaoke_app):
    harness, page = _page_with_hold(karaoke_app, [])
    expect(page.locator("#hook-permission")).to_be_hidden()


def test_card_renders_the_held_request(karaoke_app):
    harness, page = _page_with_hold(karaoke_app, [HELD])
    expect(page.locator("#hook-permission")).to_be_visible()
    expect(page.locator("#hook-permission-title")).to_contain_text(
        "Permiso de Claude pendiente"
    )
    expect(page.locator("#hook-permission-what")).to_contain_text("Bash")
    expect(page.locator("#hook-permission-what")).to_contain_text("w1:p3")
    expect(page.locator("#hook-permission-what")).to_contain_text("echo hook-probe")


def test_card_counts_extra_holds(karaoke_app):
    other = dict(HELD, request_id="hp-second", tool_name="Edit")
    harness, page = _page_with_hold(karaoke_app, [HELD, other])
    expect(page.locator("#hook-permission-what")).to_contain_text("+1")


def test_allow_posts_the_decision_and_clears(karaoke_app):
    harness = karaoke_app()
    # A mutable payload: the route serves the SAME object, so clearing
    # it models "the brain no longer holds the request".
    state = {"pending": [HELD]}
    harness.respond("/hook/permissions", state)
    page = harness.load().page
    harness.respond(
        "/hook/permissions/hp-fixed-request-id-000001/answer",
        {"ok": True, "decision": "allow"},
        method="POST",
    )
    page.locator("#hp-allow").click()
    # The toast for the granted decision…
    expect(page.locator("#toast")).to_contain_text("Permiso concedido", timeout=8000)
    # …and the card falls back once the beat reads an empty list.
    state["pending"] = []
    expect(page.locator("#hook-permission")).to_be_hidden(timeout=10000)
    posted = [
        r for r in harness.requests
        if r.method == "POST" and "/hook/permissions/" in r.url and "/answer" in r.url
    ]
    assert len(posted) == 1
    assert json.loads(posted[0].post_data) == {"decision": "allow"}


def test_deny_posts_the_decision(karaoke_app):
    harness, page = _page_with_hold(karaoke_app, [HELD])
    harness.respond(
        "/hook/permissions/hp-fixed-request-id-000001/answer",
        {"ok": True, "decision": "deny"},
        method="POST",
    )
    page.locator("#hp-deny").click()
    expect(page.locator("#toast")).to_contain_text("Permiso rechazado", timeout=8000)
    posted = [
        r for r in harness.requests
        if r.method == "POST" and "/hook/permissions/" in r.url and "/answer" in r.url
    ]
    assert json.loads(posted[0].post_data) == {"decision": "deny"}


def test_gone_hold_refreshes_without_an_error(karaoke_app):
    harness = karaoke_app()
    state = {"pending": [HELD]}
    harness.respond("/hook/permissions", state)
    page = harness.load().page
    harness.respond(
        "/hook/permissions/hp-fixed-request-id-000001/answer",
        {"detail": "esa solicitud ya fue respondida o caducó"},
        method="POST",
        status=409,
    )
    page.locator("#hp-allow").click()
    # 409 is not an error toast: the card re-reads the pending list and
    # falls away once it comes back empty.
    state["pending"] = []
    expect(page.locator("#hook-permission")).to_be_hidden(timeout=10000)
