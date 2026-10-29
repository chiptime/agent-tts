"""The PWA's dialog buttons, in real Chromium (S2).

A RECOGNIZED dialog renders as native buttons above the manual keypad:
text nodes only, so an injection-looking label stays literal. A tap
posts the exact answer payload to POST /pane/dialog/answer and shows
the outcome. A dialog the server could NOT read renders the explicit
"cannot read" card and keeps the keypad. The keypad itself gains the
digit row.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

BLOCKED_CLAUDE = [
    {
        "pane_id": "w1:p3",
        "agent": "claude",
        "agent_status": "blocked",
        "title": "Claude | pregunta",
        "cwd": "/repo",
        "session_id": "00000000-1111-2222-3333-444444444444",
        "focused": True,
        "last_turn": None,
    }
]

STRUCTURED = {
    "agent": "claude",
    "kind": "select",
    "question": "¿Qué fruta quieres?",
    "options": [
        {"label": "Manzana", "description": "la roja"},
        {"label": "Pera", "description": ""},
    ],
    "fingerprint": "fp-abc123",
    "recipe": "keys",
}

EVIL_STRUCTURED = {
    **STRUCTURED,
    "question": '¿<img src=x onerror="window.__pwned=1">?',
    "options": [
        {"label": '<script>window.__pwned=1</script>', "description": "<b>no</b>"},
        {"label": '"><svg onload="window.__pwned=1">', "description": ""},
    ],
}

UNREAD = {"unread": True, "agent": "grok", "detail": "parked card"}

VIEW = {
    "status": {"active": True, "pane_id": "w1:p3", "agent": "claude",
               "agent_status": "blocked"},
    "screen": "Which fruit?",
    "transcript": None,
    "structured": STRUCTURED,
    "pending": {"detected": True, "kind": "question", "excerpt": "Which fruit?"},
}


def _load(karaoke_app, herd=None, view=None):
    harness = karaoke_app()
    harness.respond("/herd", herd or BLOCKED_CLAUDE)
    harness.respond("/view", view or VIEW, query=("pane_id",))
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    return harness.load()


def test_recognized_dialog_renders_one_button_per_option(karaoke_app):
    page = _load(karaoke_app).page

    expect(page.locator("#dialog-buttons")).to_be_visible()
    buttons = page.locator("#dialog-buttons button")
    expect(buttons).to_have_count(2)
    expect(buttons.nth(0)).to_contain_text("Manzana")
    expect(buttons.nth(0)).to_contain_text("la roja")
    expect(buttons.nth(1)).to_contain_text("Pera")


def test_tap_posts_the_exact_answer_payload_and_shows_the_outcome(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", BLOCKED_CLAUDE)
    harness.respond("/view", VIEW, query=("pane_id",))
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    harness.respond("/pane/dialog/answer", {"ok": True, "option": 1}, method="POST")
    page = harness.load().page

    with page.expect_request(
        lambda request: request.url.endswith("/pane/dialog/answer")
        and request.method == "POST"
    ) as captured:
        page.locator("#dialog-buttons button").nth(1).click()

    assert json.loads(captured.value.post_data) == {
        "pane_id": "w1:p3", "fingerprint": "fp-abc123", "option": 1,
    }
    expect(page.locator("#dialog-status")).to_contain_text("Respondido: Pera")


def test_a_changed_dialog_shows_the_race_refusal(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", BLOCKED_CLAUDE)
    harness.respond("/view", VIEW, query=("pane_id",))
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    harness.respond(
        "/pane/dialog/answer",
        {"detail": "El diálogo cambió antes de responder; nada se envió."},
        method="POST",
        status=409,
    )
    page = harness.load().page

    page.locator("#dialog-buttons button").nth(0).click()

    expect(page.locator("#dialog-status")).to_contain_text("cambió")
    expect(page.locator("#dialog-status")).to_contain_text("teclado")


def test_unknown_dialog_shows_the_card_and_keeps_the_keypad(karaoke_app):
    view = {**VIEW, "structured": UNREAD}
    page = _load(karaoke_app, view=view).page

    expect(page.locator("#dialog-unread")).to_be_visible()
    expect(page.locator("#dialog-unread")).to_contain_text("No puedo leer este diálogo")
    expect(page.locator("#dialog-buttons")).to_be_hidden()
    expect(page.locator("#pane-keys")).to_be_visible()


def test_injection_looking_labels_stay_literal_text(karaoke_app):
    view = {**VIEW, "structured": EVIL_STRUCTURED}
    page = _load(karaoke_app, view=view).page

    expect(page.locator("#dialog-buttons button")).to_have_count(2)
    # The markup never executes: the label renders as literal text.
    assert page.evaluate("() => window.__pwned") is None
    expect(page.locator("#dialog-buttons button").nth(0)).to_contain_text("<script>")


def test_the_keypad_gains_the_digit_row(karaoke_app):
    page = _load(karaoke_app).page

    keys = page.evaluate(
        "() => [...document.querySelectorAll('#pane-keys button')].map(b => b.dataset.key)"
    )
    assert keys == [
        "left", "right", "up", "down", "tab", "esc", "enter",
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
    ]


def test_a_digit_tap_posts_that_digit(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", BLOCKED_CLAUDE)
    harness.respond("/view", VIEW, query=("pane_id",))
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    harness.respond("/pane/keys", {"ok": True}, method="POST")
    page = harness.load().page

    with page.expect_request(
        lambda request: request.url.endswith("/pane/keys") and request.method == "POST"
    ) as captured:
        page.locator('#pane-keys button[data-key="3"]').click()

    assert json.loads(captured.value.post_data) == {"pane_id": "w1:p3", "keys": ["3"]}
