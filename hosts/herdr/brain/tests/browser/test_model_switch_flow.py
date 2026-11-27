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
            "label": f"{label} {provider}",
            "model": label,
            "provider": provider,
            "description": "",
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
    # f7f S4: opening the conversation sheet (#pane-picker) fetches
    # /session/roots for the "Espacios" section. An empty herdr-backed
    # payload declares the traffic without touching any assertion
    # below (the section renders "Sin espacios").
    harness.respond("/session/roots", {
        "roots": [], "recents": [], "spaces": [], "harnesses": [],
        "mux": {"name": "herdr", "children": ["herdr"]},
    })
    # The full-catalog read the opencode card makes on demand: an EMPTY
    # catalog (the CLI is unavailable) keeps the picker's own options,
    # which is what every screen-option test below exercises.
    harness.respond(
        "/pane/models", {"models": [], "reason": "cli-failed"}, query=("pane_id",)
    )
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


def _open_picker(karaoke_app):
    harness = _load(karaoke_app)
    harness.respond("/pane/dialog/answer", {"ok": True, "option": 6}, method="POST")
    _modal_route(harness, PICKER_VIEW)
    page = harness.load().page
    page.locator("#pane-picker").click()
    expect(page.locator("#conv-model")).to_be_visible()
    page.locator("#conv-model").click()
    expect(page.locator("#dialog-buttons .dlg-model-list button")).to_have_count(
        len(MODELS)
    )
    return page


def test_the_picker_list_scrolls_inside_the_card(karaoke_app):
    """Operator report: 'no me deja hacer scroll'. The 16 rows must live
    in a bounded list that scrolls on its own (overflow-y auto)."""
    page = _open_picker(karaoke_app)

    metrics = page.evaluate(
        """() => {
          const list = document.querySelector('#dialog-buttons .dlg-model-list');
          const box = list.getBoundingClientRect();
          list.scrollTop = list.scrollHeight;
          return {
            overflowY: getComputedStyle(list).overflowY,
            scrollHeight: list.scrollHeight,
            clientHeight: list.clientHeight,
            scrolled: list.scrollTop,
            height: box.height,
            viewport: window.innerHeight,
          };
        }"""
    )

    assert metrics["overflowY"] == "auto"
    assert metrics["scrollHeight"] > metrics["clientHeight"], metrics
    assert metrics["scrolled"] > 0, "the list must actually scroll"
    assert metrics["height"] < metrics["viewport"], "the card stays bounded"
    # The last row is reachable and tappable after scrolling.
    last = page.locator("#dialog-buttons .dlg-model-list button").last
    last.scroll_into_view_if_needed()
    expect(last).to_be_visible()


def test_typing_narrows_the_rows_and_a_tap_sends_the_original_index(karaoke_app):
    page = _open_picker(karaoke_app)
    search = page.locator("#dialog-buttons .dlg-model-search")
    expect(search).to_be_visible()

    # Case- and accent-insensitive substring over the model name.
    search.fill("GLM")
    visible = page.locator("#dialog-buttons .dlg-model-list button:visible")
    expect(visible).to_have_count(2)
    expect(visible.nth(0)).to_contain_text("glm-4.7")
    expect(visible.nth(1)).to_contain_text("glm-4.7-air")

    # The provider is secondary searchable text.
    search.fill("moonshot")
    expect(visible).to_have_count(1)
    expect(visible.nth(0)).to_contain_text("kimi-k2")

    # A tap on a FILTERED row sends the row's ORIGINAL option index.
    search.fill("air")
    expect(visible).to_have_count(1)
    original = [model for model, _provider in MODELS].index("glm-4.7-air")
    with page.expect_request(
        lambda request: request.url.endswith("/pane/dialog/answer")
        and request.method == "POST"
    ) as captured:
        visible.nth(0).click()
    assert json.loads(captured.value.post_data) == {
        "pane_id": PANE,
        "fingerprint": PICKER_STRUCTURED["fingerprint"],
        "option": original,
    }
    expect(page.locator("#dialog-status")).to_contain_text("Respondido:")


def test_a_search_with_no_hits_says_so_and_clearing_restores_every_row(karaoke_app):
    page = _open_picker(karaoke_app)
    search = page.locator("#dialog-buttons .dlg-model-search")
    visible = page.locator("#dialog-buttons .dlg-model-list button:visible")

    search.fill("zzz-no-such-model")
    expect(visible).to_have_count(0)
    expect(page.locator("#dialog-buttons .dlg-model-none")).to_be_visible()

    search.fill("")
    expect(visible).to_have_count(len(MODELS))
    expect(page.locator("#dialog-buttons .dlg-model-none")).to_be_hidden()


def test_the_current_model_is_highlighted_and_shows_provider_apart(karaoke_app):
    page = _open_picker(karaoke_app)
    rows = page.locator("#dialog-buttons .dlg-model-list button")

    current = page.locator("#dialog-buttons .dlg-model-list button.dlg-current")
    expect(current).to_have_count(1)
    expect(current).to_contain_text("claude-sonnet-4-5")
    expect(current).to_have_attribute("aria-current", "true")
    # The label shows the clean model name; the provider is secondary.
    expect(rows.nth(1).locator("span").nth(0)).to_have_text("claude-opus-4-1")
    expect(rows.nth(1).locator(".dlg-opt-desc")).to_have_text("anthropic")


# ── the FULL catalog: every connected model, not the painted window ────

CATALOG_PROVIDERS = [
    ("opencode-go", "OpenCode Go"),
    ("zai-coding-plan", "Z.AI Coding Plan"),
    ("anthropic", "Anthropic"),
    ("openai", "OpenAI"),
    ("openrouter", "OpenRouter"),
]


def _catalog():
    """230+ entries: far more than the 16-row window the screen painted,
    with models the picker NEVER listed (Kimi K2.7 Code)."""
    models = []
    for index in range(228):
        provider, provider_name = CATALOG_PROVIDERS[index % len(CATALOG_PROVIDERS)]
        models.append(
            {
                "id": f"{provider}/model-{index}",
                "name": f"Model {index}",
                "provider": provider,
                "provider_name": provider_name,
                "current": False,
            }
        )
    models.append(
        {
            "id": "opencode-go/kimi-k2.7-code",
            "name": "Kimi K2.7 Code",
            "provider": "opencode-go",
            "provider_name": "OpenCode Go",
            "current": False,
        }
    )
    models.append(
        {
            "id": "anthropic/claude-opus-5-5",
            "name": "Claude Opus 5.5",
            "provider": "anthropic",
            "provider_name": "Anthropic",
            "current": True,
        }
    )
    return models


def _open_catalog_picker(karaoke_app, catalog):
    harness = _load(karaoke_app)
    harness.respond(
        "/pane/models", {"models": catalog, "reason": ""}, query=("pane_id",)
    )
    harness.respond("/pane/dialog/answer", {"ok": True, "option": None}, method="POST")
    _modal_route(harness, PICKER_VIEW)
    page = harness.load().page
    page.locator("#pane-picker").click()
    expect(page.locator("#conv-model")).to_be_visible()
    page.locator("#conv-model").click()
    # The card first paints the picker's own options; the catalog then
    # REPLACES them with every connected model.
    expect(page.locator("#dialog-buttons .dlg-model-list button[data-catalog]")).to_have_count(
        len(catalog)
    )
    return harness, page


def test_the_full_catalog_replaces_the_painted_window(karaoke_app):
    catalog = _catalog()
    harness, page = _open_catalog_picker(karaoke_app, catalog)

    rows = page.locator("#dialog-buttons .dlg-model-list button")
    expect(rows).to_have_count(len(catalog))
    assert len(catalog) > 200 > len(MODELS)
    # No screen-option button survives next to the catalog.
    expect(page.locator("#dialog-buttons button[data-index]")).to_have_count(0)
    # The read asked for THIS pane, once.
    reads = [r for r in harness.requests if "/pane/models" in r.url]
    assert len(reads) == 1 and "pane_id=" in reads[0].url
    # The server's current model is highlighted.
    current = page.locator("#dialog-buttons .dlg-model-list button.dlg-current")
    expect(current).to_have_count(1)
    expect(current).to_contain_text("Claude Opus 5.5")
    # Provider rides as the secondary line.
    expect(rows.nth(0).locator(".dlg-opt-desc")).to_have_text("OpenCode Go")
    # The 230-row list scrolls inside the bounded card.
    metrics = page.evaluate(
        """() => {
          const list = document.querySelector('#dialog-buttons .dlg-model-list');
          list.scrollTop = list.scrollHeight;
          return {
            overflowY: getComputedStyle(list).overflowY,
            scrollHeight: list.scrollHeight,
            clientHeight: list.clientHeight,
            scrolled: list.scrollTop,
            viewport: window.innerHeight,
          };
        }"""
    )
    assert metrics["overflowY"] == "auto"
    assert metrics["scrollHeight"] > metrics["clientHeight"] * 5, metrics
    assert metrics["scrolled"] > 0
    assert metrics["clientHeight"] < metrics["viewport"]


def test_filtering_the_catalog_and_tapping_sends_the_model_name(karaoke_app):
    _harness, page = _open_catalog_picker(karaoke_app, _catalog())
    search = page.locator("#dialog-buttons .dlg-model-search")
    visible = page.locator("#dialog-buttons .dlg-model-list button:visible")

    # A model the picker window NEVER listed is reachable by search.
    search.fill("kimi k2.7")
    expect(visible).to_have_count(1)
    expect(visible.nth(0)).to_contain_text("Kimi K2.7 Code")
    expect(visible.nth(0)).to_contain_text("OpenCode Go")

    # The tap answers by NAME (+ provider), never an option index, with
    # the picker render's own fingerprint.
    with page.expect_request(
        lambda request: request.url.endswith("/pane/dialog/answer")
        and request.method == "POST"
    ) as captured:
        visible.nth(0).click()
    assert json.loads(captured.value.post_data) == {
        "pane_id": PANE,
        "fingerprint": PICKER_STRUCTURED["fingerprint"],
        "model_name": "Kimi K2.7 Code",
        "provider_name": "OpenCode Go",
    }
    expect(page.locator("#dialog-status")).to_contain_text("Respondido: Kimi K2.7 Code")


def test_a_provider_search_narrows_the_catalog_and_clearing_restores_it(karaoke_app):
    catalog = _catalog()
    _harness, page = _open_catalog_picker(karaoke_app, catalog)
    search = page.locator("#dialog-buttons .dlg-model-search")
    visible = page.locator("#dialog-buttons .dlg-model-list button:visible")

    search.fill("z.ai coding")
    expected = sum(1 for m in catalog if m["provider_name"] == "Z.AI Coding Plan")
    expect(visible).to_have_count(expected)
    search.fill("zzz-no-such-model")
    expect(visible).to_have_count(0)
    expect(page.locator("#dialog-buttons .dlg-model-none")).to_be_visible()
    search.fill("")
    expect(visible).to_have_count(len(catalog))


def test_a_refused_catalog_answer_says_the_dialog_changed(karaoke_app):
    harness = _load(karaoke_app)
    harness.respond(
        "/pane/models", {"models": _catalog(), "reason": ""}, query=("pane_id",)
    )
    harness.respond(
        "/pane/dialog/answer", {"detail": "changed"}, method="POST", status=409
    )
    _modal_route(harness, PICKER_VIEW)
    page = harness.load().page
    page.locator("#pane-picker").click()
    page.locator("#conv-model").click()
    expect(page.locator("#dialog-buttons .dlg-model-list button[data-catalog]")).to_have_count(
        len(_catalog())
    )
    page.locator("#dialog-buttons .dlg-model-search").fill("model 7")
    page.locator("#dialog-buttons .dlg-model-list button:visible").first.click()
    expect(page.locator("#dialog-status")).to_contain_text("cambió")


# ── POST-09: the agy (antigravity) switch flow end to end ──────────────
#
# The pane's agent string is `agy`; the structured payload's agent is
# the recognizer's canonical `antigravity`. /model opens the picker,
# the catalog card replaces the painted rows, and a tap answers by
# NAME through the guarded endpoint (the server resolves it against
# the fresh picker's own family rows and walks — no Search typing).

AGY_PANE = "w1:p6"

AGY_HERD = [
    {
        "pane_id": AGY_PANE,
        "agent": "agy",
        "agent_status": "idle",
        "title": "agy | repo",
        "cwd": "/home/user/repo",
        "session_id": "aaaaaaaa-1111-2222-3333-444444444444",
        "focused": True,
        "last_turn": None,
        "model": "Gemini 4 Neon (High)",
    }
]

AGY_IDLE_VIEW = {
    "status": {"active": False, "pane_id": AGY_PANE, "agent": "agy",
               "agent_status": "idle"},
    "screen": None,
    "screen_runs": None,
    "transcript": None,
    "pending": {"detected": False},
}

AGY_PICKER_STRUCTURED = {
    "unread": False,
    "agent": "antigravity",
    "kind": "model_picker",
    "question": "Switch Model",
    "step": None,
    "hint": "Keyboard: ↑/↓ Navigate  ←/→ Effort  enter Select  esc Go Back",
    "fingerprint": "b2c3d4e5f6071829",
    "recipe": "walk",
    "options": [
        {"label": "Gemini 4 Neon", "description": "", "checked": None,
         "current": True},
        {"label": "Gemini 4 Carbon", "description": "", "checked": None},
        {"label": "GPT-OSS 120B (Medium)", "description": "", "checked": None},
    ],
}

AGY_PICKER_VIEW = {**AGY_IDLE_VIEW, "structured": AGY_PICKER_STRUCTURED}

AGY_CATALOG = [
    {
        "id": "gemini-4-neon-high", "name": "Gemini 4 Neon",
        "ids": ["gemini-4-neon-high", "gemini-4-neon-low"], "current": True,
    },
    {
        "id": "gemini-4-carbon-medium", "name": "Gemini 4 Carbon",
        "ids": ["gemini-4-carbon-medium"], "current": False,
    },
    {
        "id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)",
        "ids": ["gpt-oss-120b-medium"], "current": False,
    },
]


def test_the_agy_pane_opens_its_picker_and_a_catalog_tap_answers_by_name(
    karaoke_app,
):
    harness = karaoke_app()
    harness.respond("/herd", AGY_HERD)
    harness.respond("/view", AGY_IDLE_VIEW, query=("pane_id",))
    harness.respond("/pane/reply", {"ok": True}, method="POST")
    harness.respond("/session/roots", {
        "roots": [], "recents": [], "spaces": [], "harnesses": [],
        "mux": {"name": "herdr", "children": ["herdr"]},
    })
    harness.respond(
        "/pane/models", {"models": AGY_CATALOG, "reason": ""}, query=("pane_id",)
    )
    harness.respond(
        "/pane/dialog/answer", {"ok": True, "option": None}, method="POST"
    )
    modal_reads = _modal_route(harness, AGY_PICKER_VIEW)
    page = harness.load().page

    # The affordance reaches an agy pane and sends agy's own command.
    page.locator("#pane-picker").click()
    expect(page.locator("#conv-model")).to_be_visible()
    with page.expect_request(
        lambda request: request.url.endswith("/pane/reply")
        and request.method == "POST"
    ) as delivered:
        page.locator("#conv-model").click()
    assert json.loads(delivered.value.post_data) == {
        "pane_id": AGY_PANE, "text": "/model",
    }

    # The picker card paints, and the catalog REPLACES the painted
    # rows (one read, for this pane).
    expect(page.locator("#dialog-buttons .dlg-model-list button[data-catalog]")).to_have_count(
        len(AGY_CATALOG)
    )
    assert len(modal_reads) == 1
    reads = [r for r in harness.requests if "/pane/models" in r.url]
    assert len(reads) == 1 and "pane_id=" in reads[0].url
    current = page.locator("#dialog-buttons .dlg-model-list button.dlg-current")
    expect(current).to_have_count(1)
    expect(current).to_contain_text("Gemini 4 Neon")

    # A tap answers by NAME (+ fingerprint), no provider, no index.
    with page.expect_request(
        lambda request: request.url.endswith("/pane/dialog/answer")
        and request.method == "POST"
    ) as captured:
        page.locator(
            "#dialog-buttons .dlg-model-list button[data-catalog]"
        ).nth(1).click()
    assert json.loads(captured.value.post_data) == {
        "pane_id": AGY_PANE,
        "fingerprint": AGY_PICKER_STRUCTURED["fingerprint"],
        "model_name": "Gemini 4 Carbon",
    }
    expect(page.locator("#dialog-status")).to_contain_text("Respondido: Gemini 4 Carbon")
