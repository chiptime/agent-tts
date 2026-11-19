"""The model-switch flow end to end, in real Chromium (F7d S2 + fix).

The phone report: "le doy a cambiar modelo… en ningún momento hay un
feedback de mi opencode a mi PWA". The command IS delivered and the
modal read DOES recognize the picker (verified server-side); the one
unverified link was the PWA render path — no browser harness drove it.
These tests drive the REAL app from the sheet's "Cambiar modelo" tap
to the painted card:

- an IDLE opencode pane (its picker never blocks it) whose modal poll
  answers a structured ``model_picker`` must render the option card
  from the HELD modal payload — the same card shape a blocked pane's
  live payload paints — retire the poll, and answer a tap through
  POST /pane/dialog/answer with that render's own fingerprint;
- a modal read that never yields structured ends in the none-toast,
  and no card ever paints.

Only the HTTP boundary is doubled: statics are the real files, routes
are exact same-origin paths. The modal read is intercepted by its
``modal=1`` query (registered after the harness catch-all, which only
the newer route shadows) so the plain /view poll keeps its own
idle payload — exactly the split the real server makes.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

PANE = "w1:p4"

OPENCODE_HERD = [
    {
        "pane_id": PANE,
        "agent": "opencode",
        "agent_status": "idle",
        "title": "OC | agent-tts",
        "cwd": "/home/user/repo",
        "session_id": "00000000-1111-2222-3333-444444444444",
        "focused": True,
        "last_turn": None,
        "model": "glm-4.7",
    }
]

IDLE_VIEW = {
    "status": {"active": False, "pane_id": PANE, "agent": "opencode",
               "agent_status": "idle"},
    "screen": None,
    "screen_runs": None,
    "transcript": None,
    "pending": {"detected": False},
}

# The picker the live server read: 16 rows, exactly one current (the
# opencode adapter's ● row rides along ONLY on that option).
MODELS = [
    ("claude-sonnet-4-5", "anthropic"),
    ("claude-opus-4-1", "anthropic"),
    ("gemini-2.5-pro", "google"),
    ("gemini-2.5-flash", "google"),
    ("gpt-5.2", "openai"),
    ("gpt-5.1-codex", "openai"),
    ("glm-4.7", "zai"),
    ("glm-4.7-air", "zai"),
    ("grok-4", "xai"),
    ("grok-code-fast-1", "xai"),
    ("kimi-k2", "moonshot"),
    ("deepseek-v3.2", "deepseek"),
    ("qwen3-coder", "alibaba"),
    ("mistral-large-3", "mistral"),
    ("llama-4-maverick", "meta"),
    ("o4-mini", "openai"),
]

PICKER_STRUCTURED = {
    "unread": False,
    "agent": "opencode",
    "kind": "model_picker",
    "question": "Select model",
    "step": None,
    "hint": "ctrl+a select all · ctrl+f filter",
    "fingerprint": "a1b2c3d4e5f60718",
    "recipe": "walk",
    "options": [
        {
            "label": label,
            "description": provider,
            "checked": None,
            **({"current": True} if index == 0 else {}),
        }
        for index, (label, provider) in enumerate(MODELS)
    ],
}

PICKER_VIEW = {**IDLE_VIEW, "structured": PICKER_STRUCTURED}


def _modal_route(harness, payload):
    """Answers every /view?…&modal=1 read; returns the request log."""
    reads = []

    def handle(route):
        reads.append(route.request)
        route.fulfill(json=payload)

    # Registered AFTER the harness catch-all: only this newer pattern
    # shadows it, and only the modal read carries modal=1 — the plain
    # /view poll keeps the respond() payload (its query never names
    # modal, so the catch-all still serves it).
    harness.context.route("**/view*modal=1*", handle)
    return reads


def _load(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", OPENCODE_HERD)
    harness.respond("/view", IDLE_VIEW, query=("pane_id",))
    harness.respond("/pane/reply", {"ok": True}, method="POST")
    return harness


def test_picker_card_renders_on_an_idle_pane_and_answers_a_tap(karaoke_app):
    harness = _load(karaoke_app)
    harness.respond("/pane/dialog/answer", {"ok": True, "option": 3}, method="POST")
    modal_reads = _modal_route(harness, PICKER_VIEW)
    page = harness.load().page

    # The sheet offers "Cambiar modelo" for the opencode pane; the tap
    # sends the picker command through the plain reply path.
    page.locator("#pane-picker").click()
    expect(page.locator("#conv-model")).to_be_visible()
    with page.expect_request(
        lambda request: request.url.endswith("/pane/reply")
        and request.method == "POST"
    ) as delivered:
        page.locator("#conv-model").click()
    assert json.loads(delivered.value.post_data) == {"pane_id": PANE, "text": "/models"}

    # The FIRST modal poll paints the card: one button per option in
    # the dialog card area, keypad shape for the held picker, and the
    # composer away (the picker owns the keyboard).
    expect(page.locator("#dialog-buttons")).to_be_visible()
    buttons = page.locator("#dialog-buttons button")
    expect(buttons).to_have_count(len(MODELS))
    expect(buttons.nth(0)).to_contain_text("claude-sonnet-4-5")
    expect(buttons.nth(0)).to_contain_text("anthropic")
    expect(page.locator("#pane-keys")).to_be_visible()
    expect(page.locator("#pane-reply")).to_be_hidden()
    assert len(modal_reads) == 1, "the structured read must stop the poll"

    # Two missed beats later the poll is still retired and the held
    # card survived them (a plain /view repaint keeps painting it).
    page.wait_for_timeout(1600)
    assert len(modal_reads) == 1
    expect(page.locator("#dialog-buttons")).to_be_visible()

    # A tap answers through the guarded endpoint with THIS render's
    # fingerprint and the tapped option's index — nothing else moves.
    with page.expect_request(
        lambda request: request.url.endswith("/pane/dialog/answer")
        and request.method == "POST"
    ) as captured:
        buttons.nth(3).click()
    assert json.loads(captured.value.post_data) == {
        "pane_id": PANE,
        "fingerprint": PICKER_STRUCTURED["fingerprint"],
        "option": 3,
    }
    expect(page.locator("#dialog-status")).to_contain_text("Respondido:")


def test_a_modal_read_that_never_yields_structured_ends_in_the_none_toast(karaoke_app):
    harness = _load(karaoke_app)
    modal_reads = _modal_route(harness, IDLE_VIEW)  # the picker never appears
    page = harness.load().page

    page.locator("#pane-picker").click()
    expect(page.locator("#conv-model")).to_be_visible()
    page.locator("#conv-model").click()

    # Bounded: the budget runs out and the none-toast says so plainly.
    expect(page.locator("#toast")).to_contain_text(
        "no puede cambiar de modelo", timeout=20000
    )
    # No card ever painted from a payload the server never structured.
    expect(page.locator("#dialog-buttons")).to_be_hidden()
    stopped_at = len(modal_reads)
    page.wait_for_timeout(1600)
    assert len(modal_reads) == stopped_at, "the poll retired with the toast"
