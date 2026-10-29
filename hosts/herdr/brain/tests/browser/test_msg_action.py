"""The Mensaje action in the agent cockpit (S13), browser-side.

A small operator composer under the composer row: offered when the
viewed pane has a live agent session, sends POST /msg/send addressed to
``pane#session`` (the session token pins the conversation), and speaks
the delivery outcome honestly (delivered / held while busy / refused
with the server's reason). Text nodes only.
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

VIEW_WITH_SESSION = {
    "status": {
        "active": True,
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
        "session_id": "ses_test0000session",
    },
    "screen": "$ pytest -q",
    "transcript": None,
    "pending": {"detected": False, "kind": None, "excerpt": None},
}

VIEW_WITHOUT_SESSION = {
    "status": {
        "active": True,
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
        "session_id": None,
    },
    "screen": "$ pytest -q",
    "transcript": None,
    "pending": {"detected": False, "kind": None, "excerpt": None},
}


def _page(karaoke_app, view=VIEW_WITH_SESSION, send_body=None, send_status=200):
    harness = karaoke_app()
    harness.respond("/herd", WORKING_AGENT)
    harness.respond("/view", view, query=("pane_id",))
    if send_body is not None:
        harness.api[("POST", "/msg/send")] = (send_body, send_status, set())
    page = harness.load().page
    return harness, page


def _msg_posts(harness):
    return [r for r in harness.requests if r.method == "POST" and r.url.endswith("/msg/send")]


def test_the_button_appears_only_with_a_live_session(karaoke_app):
    _h, page = _page(karaoke_app)
    expect(page.locator("#pane-msg")).to_be_visible()

    _h2, page2 = _page(karaoke_app, view=VIEW_WITHOUT_SESSION)
    expect(page2.locator("#pane-msg")).to_be_hidden()


def test_send_posts_the_pane_session_address_and_closes(karaoke_app):
    harness, page = _page(
        karaoke_app, send_body={"id": "bm_abc123", "state": "typed", "reason": ""}
    )
    page.locator("#pane-msg").click()
    expect(page.locator("#pane-msg-form")).to_be_visible()
    page.locator("#pane-msg-input").fill("¿Cómo va el despliegue?")
    page.locator("#pane-msg-send").click()

    expect(page.locator("#toast")).to_contain_text("Mensaje entregado al agente")
    expect(page.locator("#pane-msg-form")).to_be_hidden()

    posts = _msg_posts(harness)
    assert len(posts) == 1
    assert posts[0].post_data_json == {
        "address": "w1:p2#ses_test0000session",
        "body": "¿Cómo va el despliegue?",
    }


def test_a_held_message_says_it_waits_for_the_receiver(karaoke_app):
    _h, page = _page(
        karaoke_app,
        send_body={"id": "bm_abc124", "state": "held", "reason": "receiver is working"},
    )
    page.locator("#pane-msg").click()
    page.locator("#pane-msg-input").fill("avísame al terminar")
    page.locator("#pane-msg-send").click()
    expect(page.locator("#toast")).to_contain_text("cuando esté libre")


def test_a_refusal_shows_the_server_reason_and_keeps_the_text(karaoke_app):
    harness, page = _page(karaoke_app)
    harness.api[("POST", "/msg/send")] = (
        {"detail": "unknown pane w1:p2; get live addresses from: herdr-brain msg who"},
        422,
        set(),
    )
    page.locator("#pane-msg").click()
    page.locator("#pane-msg-input").fill("hola")
    page.locator("#pane-msg-send").click()

    expect(page.locator("#toast")).to_contain_text("unknown pane")
    # The composer stays open with the text: a refusal is retryable.
    expect(page.locator("#pane-msg-form")).to_be_visible()
    assert page.locator("#pane-msg-input").input_value() == "hola"


def test_cancel_closes_the_composer_without_sending(karaoke_app):
    harness, page = _page(karaoke_app, send_body={"id": "bm_x", "state": "typed", "reason": ""})
    page.locator("#pane-msg").click()
    page.locator("#pane-msg-input").fill("borrador")
    page.locator("#pane-msg-cancel").click()

    expect(page.locator("#pane-msg-form")).to_be_hidden()
    assert _msg_posts(harness) == []


def test_a_second_click_toggles_the_composer_closed(karaoke_app):
    _h, page = _page(karaoke_app)
    page.locator("#pane-msg").click()
    expect(page.locator("#pane-msg-form")).to_be_visible()
    page.locator("#pane-msg").click()
    expect(page.locator("#pane-msg-form")).to_be_hidden()
