"""Browser proofs for the in-call session management glue (S5/S6/S7).

Real app bytes in route-stubbed Chromium: the call button reports
per-call start/end, opening the call drawer mounts the Llamadas list,
and a /ask response carrying ``switch_pane`` retargets the call through
the same selection path a chip tap takes (shared-context publish plus a
visible toast).
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

CALLS = {
    "calls": [
        {
            "call_id": "call-glue-1",
            "started_at": "2026-11-03T10:00:00Z",
            "ended_at": "2026-11-03T10:02:30Z",
            "duration_s": 150.0,
            "turn_count": 2,
            "status": "ended",
        }
    ],
    "has_more": False,
}


def _harness(karaoke_app):
    harness = karaoke_app()
    harness.static["/calls.js"] = "calls.js"
    harness.respond("/call/start", {"call_id": "call-glue-1", "started_at": "2026-11-03T10:00:00Z"}, method="POST")
    harness.respond("/call/end", {"call_id": "call-glue-1", "status": "ended"}, method="POST")
    harness.respond("/calls", CALLS, query=("before", "limit"))
    return harness.load()


def posted(harness, path):
    return [
        r for r in harness.requests
        if r.method == "POST" and path in r.url
    ]


def test_call_toggle_reports_start_and_end_and_mounts_calls(karaoke_app):
    harness = _harness(karaoke_app)
    page = harness.page

    page.locator("#call-btn").click()

    # The call start opens the drawer, which mounts and refreshes the
    # Llamadas section: the record from GET /calls renders.
    section = page.locator("#calls-section")
    expect(section).to_be_visible()
    expect(page.locator("#calls-container .call-item")).to_have_count(1)
    assert posted(harness, "/call/start")

    page.locator("#call-btn").click()
    page.wait_for_function("() => true")  # let the end POST land
    assert posted(harness, "/call/end")
    assert not harness.blocked_requests, harness.blocked_requests
    assert not harness.page_errors, harness.page_errors


def test_ask_switch_pane_applies_the_selection(karaoke_app):
    harness = _harness(karaoke_app)
    page = harness.page
    harness.respond(
        "/ask",
        {
            "answer": "Cambiado.",
            "pane_id": "w1:p9",
            "session_id": "default",
            "switch_pane": {"pane_id": "w1:p2", "agent": "opencode", "title": "Other"},
        },
        method="POST",
    )

    page.locator("#text-input").evaluate("el => { el.value = 'cambiate al panel Other'; }")
    page.locator("#text-fallback").evaluate("form => form.requestSubmit()")

    # The toast names the new target...
    expect(page.locator("#toast")).to_contain_text("apuntando a Other")
    # ...and the shared-context publish carried the pane the server switched to.
    context_posts = posted(harness, "/call/context")
    bodies = [json.loads(r.post_data or "{}") for r in context_posts]
    assert any(body.get("pane_id") == "w1:p2" for body in bodies), bodies
    assert not harness.blocked_requests, harness.blocked_requests
    assert not harness.page_errors, harness.page_errors
