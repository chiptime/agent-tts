"""Key pad behaviour under a race and under failure.

Two things the happy-path tests cannot see:

- S6: one send per tap while a request is in flight, and a failed send must
  re-enable the pad and say so.
- S7: the pad's target is the pane whose permission screen is on display —
  NOT whatever the last /herd poll moved the focus to. A poll can land while
  a /view response is still in flight, and answering a dialog for a pane you
  are not looking at would be silent and hard to undo.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

A, B = "w1:p2", "w1:p9"


def herd(focus=A):
    return [
        dict(
            pane_id=pane,
            agent="opencode",
            agent_status="blocked",
            title=pane,
            cwd="/repo",
            session_id="fake" + pane,
            focused=pane == focus,
            last_turn=None,
        )
        for pane in (A, B)
    ]


def blocked_app(karaoke_app, focus=A):
    """Two blocked panes; the cockpit shows A's permission screen."""
    harness = karaoke_app()
    harness.respond("/herd", herd(focus))
    harness.respond(
        "/view",
        {
            "status": {"active": True, "pane_id": A, "agent_status": "blocked"},
            "screen": "PERMISSION FOR " + A,
            "transcript": None,
            "pending": {"detected": True, "kind": "permission", "excerpt": A},
        },
        query=("pane_id",),
    )
    harness.load()
    expect(harness.page.locator("#pane-keys")).to_be_visible()
    return harness


@pytest.mark.parametrize("failure", ["http", "network"])
def test_one_send_while_busy_then_a_failure_recovers(karaoke_app, failure):
    harness = blocked_app(karaoke_app)
    page, held = harness.page, []
    harness.context.route("**/pane/keys", lambda route: held.append(route))

    enter = page.locator('#pane-keys [data-key="enter"]')
    expect(page.locator("#pane-reply")).to_be_hidden()
    assert page.locator("#pane-keys button").evaluate_all(
        "bs => bs.map(b => b.dataset.key)"
    ) == ["left", "right", "up", "down", "tab", "esc", "enter"]

    enter.click()
    expect(enter).to_be_disabled()
    enter.dispatch_event("click")  # a second tap while in flight must be inert
    assert len(held) == 1
    assert page.locator("#pane-keys button:disabled").count() == 7
    assert held[0].request.post_data_json == {"pane_id": A, "keys": ["enter"]}

    if failure == "http":
        held[0].fulfill(status=502, json={"detail": "synthetic failure"})
        message = "No se pudo enviar la tecla: synthetic failure"
    else:
        held[0].abort("failed")
        message = "No se pudo enviar la tecla: sin conexión con el brain"

    expect(enter).to_be_enabled()
    expect(page.locator("#toast")).to_contain_text(message)
    assert len(held) == 1


def test_a_tap_acknowledges_itself_before_the_response(karaoke_app):
    """The pad must never leave you wondering whether the tap landed."""
    harness = blocked_app(karaoke_app)
    page, held = harness.page, []
    harness.context.route("**/pane/keys", lambda route: held.append(route))

    hint = page.locator("#pane-keys-hint")
    expect(hint).to_have_text("El agente espera — respóndele con teclas:")

    page.locator('#pane-keys [data-key="enter"]').click()

    expect(hint).to_contain_text("Enviado: Intro")
    assert len(held) == 1
    held[0].fulfill(json={"ok": True, "pane_id": A})


def test_a_failed_tap_restores_the_neutral_hint(karaoke_app):
    harness = blocked_app(karaoke_app)
    page, held = harness.page, []
    harness.context.route("**/pane/keys", lambda route: held.append(route))

    hint = page.locator("#pane-keys-hint")
    page.locator('#pane-keys [data-key="left"]').click()
    expect(hint).to_contain_text("Enviado: Izquierda")

    held[0].fulfill(status=502, json={"detail": "nope"})
    expect(hint).to_have_text("El agente espera — respóndele con teclas:")


def test_a_focus_poll_cannot_answer_a_different_visible_dialog(karaoke_app):
    """The regression: the pad answered the NEWLY focused pane's dialog."""
    harness = blocked_app(karaoke_app)
    page = harness.page
    sends = []

    # The herd poll now reports B as focused, but /view is held: the screen
    # (and the permission dialog it shows) is still A's.
    harness.respond("/herd", herd(B))
    harness.context.route("**/view*", lambda route: None)
    harness.context.route(
        "**/pane/keys",
        lambda route: (
            sends.append(route.request.post_data_json),
            route.fulfill(json={"ok": True, "pane_id": B}),
        ),
    )

    expect(page.locator("#pane-picker-label")).to_have_text(B, timeout=7000)
    expect(page.locator("#view-screen")).to_contain_text("PERMISSION FOR " + A)

    button = page.locator('#pane-keys [data-key="enter"]')
    # Hiding or disabling the pad until the new pane renders is also safe.
    if button.is_visible() and button.is_enabled():
        button.click()
        page.evaluate("() => Promise.resolve()")

    assert all(send["pane_id"] == A for send in sends), sends


def test_the_dialog_is_shown_next_to_the_pad(karaoke_app):
    """The question was always in the screen text; now it sits where the keys are."""
    harness = blocked_app(karaoke_app)
    page = harness.page

    dialog = page.locator("#pane-keys-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("PERMISSION FOR " + A)
