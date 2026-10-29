"""Operator dock (S3, PWA half): the four TOML surfaces behind the FIXED
GET /operator/config contract.

The dock is data-driven chrome with security edges, each pinned here:

- a 404 (no operator TOML) and an all-empty catalog keep the dock hidden;
- quick replies SEND through the composer's verified reply path (exact
  /pane/reply body); commands with a trailing space only FILL (no POST),
  complete commands send;
- key presets post EXACTLY {pane_id, id} to /operator/preset;
- launchers with no_prompts need a second tap (the first never posts);
  a server 409 confirmation_required re-arms, cwd_missing toasts an
  honest error;
- operator labels are TEXT: an injection-looking label renders literally.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from playwright.sync_api import expect

AGENT = [
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
    "status": {
        "active": True,
        "pane_id": "w1:p2",
        "agent": "opencode",
        "agent_status": "working",
    },
    "screen": "$ pytest -q",
    "transcript": None,
    "pending": {"detected": False, "kind": None, "excerpt": None},
}

FULL_CONFIG = {
    "quick_replies": [
        {"id": "qr1", "label": "Adelante", "text": "sí, continúa"},
    ],
    "commands": [
        {"id": "cmd1", "label": "Compactar", "command": "/compact"},
        {"id": "cmd2", "label": "Buscar", "command": "/grep "},
    ],
    "key_presets": [{"id": "k1", "label": "Reintentar", "keys": "up enter"}],
    "launchers": [
        {"id": "l1", "label": "Shell aquí", "no_prompts": False},
        {"id": "l2", "label": "Todo el panel", "no_prompts": True},
    ],
}


def dock_harness(karaoke_app, config, *, status=200):
    harness = karaoke_app()
    harness.respond("/herd", AGENT)
    harness.respond("/view", QUIET_VIEW, query=("pane_id",))
    if status == 404:
        harness.respond("/operator/config", {"detail": "none"}, status=404, query=("agent",))
    else:
        harness.respond("/operator/config", config, query=("agent",))
    # The action endpoints the dock posts to (ok-shaped minimal bodies;
    # the receipt contract itself is pinned by the S12 suite).
    harness.respond("/pane/reply", {"ok": True}, method="POST")
    harness.respond("/operator/preset", {"ok": True}, method="POST")
    harness.respond("/operator/launch", {"ok": True}, method="POST")
    return harness.load()


def dock_row(harness, row_id):
    return harness.page.locator(f'#operator-dock [data-row="{row_id}"]')


def posts_of(harness, path):
    return [
        request.post_data_json
        for request in harness.requests
        if request.method == "POST"
        and urlsplit(request.url).path == path
    ]


def test_missing_endpoint_keeps_dock_hidden(karaoke_app):
    harness = dock_harness(karaoke_app, None, status=404)
    assert harness.page.locator("#operator-dock").is_hidden()
    assert [r.url.split("?")[0] for r in harness.requests if "/operator/config" in r.url]


def test_empty_catalog_keeps_dock_hidden(karaoke_app):
    empty = {"quick_replies": [], "commands": [], "key_presets": [], "launchers": []}
    harness = dock_harness(karaoke_app, empty)
    harness.page.wait_for_timeout(300)
    assert harness.page.locator("#operator-dock").is_hidden()


def test_full_catalog_renders_four_sections(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    dock = harness.page.locator("#operator-dock")
    expect(dock).to_be_visible()
    for row_id in ("qr:qr1", "cmd:cmd1", "cmd:cmd2", "preset:k1", "launch:l1", "launch:l2"):
        expect(dock_row(harness, row_id)).to_be_visible()
    assert dock_row(harness, "qr:qr1").text_content() == "Adelante"
    sections = harness.page.locator("#operator-dock .dock-label").all_text_contents()
    assert sections == ["Respuestas rápidas", "Comandos", "Teclas", "Lanzadores"]


def test_quick_reply_sends_through_the_verified_reply_path(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    dock_row(harness, "qr:qr1").click()
    harness.page.wait_for_timeout(200)
    bodies = posts_of(harness, "/pane/reply")
    assert bodies == [{"pane_id": "w1:p2", "text": "sí, continúa"}]
    # The composer clears — the send owned it.
    assert harness.page.locator("#pane-reply-input").input_value() == ""


def test_command_with_trailing_space_only_fills(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    dock_row(harness, "cmd:cmd2").click()
    harness.page.wait_for_timeout(200)
    assert harness.page.locator("#pane-reply-input").input_value() == "/grep "
    assert posts_of(harness, "/pane/reply") == []


def test_complete_command_sends(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    dock_row(harness, "cmd:cmd1").click()
    harness.page.wait_for_timeout(200)
    assert posts_of(harness, "/pane/reply") == [{"pane_id": "w1:p2", "text": "/compact"}]


def test_key_preset_posts_the_exact_payload(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    dock_row(harness, "preset:k1").click()
    harness.page.wait_for_timeout(200)
    assert posts_of(harness, "/operator/preset") == [{"pane_id": "w1:p2", "id": "k1"}]


def test_no_prompts_launcher_needs_two_taps(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    launcher = dock_row(harness, "launch:l2")
    launcher.click()
    harness.page.wait_for_timeout(150)
    assert posts_of(harness, "/operator/launch") == []
    assert "dock-armed" in (launcher.get_attribute("class") or "")
    launcher.click()
    harness.page.wait_for_timeout(200)
    assert posts_of(harness, "/operator/launch") == [
        {"id": "l2", "confirm": True}
    ]


def test_plain_launcher_posts_on_first_tap(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    dock_row(harness, "launch:l1").click()
    harness.page.wait_for_timeout(200)
    assert posts_of(harness, "/operator/launch") == [{"id": "l1", "confirm": True}]


def test_confirmation_required_409_re_arms_the_row(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    harness.respond(
        "/operator/launch", {"detail": "confirmation_required"}, method="POST", status=409
    )
    launcher = dock_row(harness, "launch:l1")
    launcher.click()
    harness.page.wait_for_timeout(200)
    assert "dock-armed" in (launcher.get_attribute("class") or "")
    assert harness.page.locator("#toast").is_hidden()


def test_cwd_missing_409_toasts_an_honest_error(karaoke_app):
    harness = dock_harness(karaoke_app, FULL_CONFIG)
    harness.respond(
        "/operator/launch", {"detail": "cwd_missing"}, method="POST", status=409
    )
    launcher = dock_row(harness, "launch:l1")
    launcher.click()
    expect(harness.page.locator("#toast")).to_be_visible()
    assert "directorio" in (harness.page.locator("#toast").text_content() or "")
    assert "dock-armed" not in (launcher.get_attribute("class") or "")


def test_injection_looking_labels_render_literally(karaoke_app):
    hostile = {
        "quick_replies": [
            {"id": "evil", "label": '<img src=x onerror="window.__pwned=1">', "text": "x"},
            {"id": "evil2", "label": "<script>window.__pwned=2</script>", "text": "y"},
        ],
        "commands": [{"id": "evil3", "label": '<a href="javascript:alert(1)">enlace</a>',
                      "command": "/x"}],
        "key_presets": [],
        "launchers": [],
    }
    harness = dock_harness(karaoke_app, hostile)
    page = harness.page
    literal = page.locator('#operator-dock [data-row="qr:evil"]')
    expect(literal).to_be_visible()
    assert literal.text_content() == '<img src=x onerror="window.__pwned=1">'
    assert literal.locator("img").count() == 0
    assert page.locator('#operator-dock [data-row="qr:evil2"]').text_content() == \
        "<script>window.__pwned=2</script>"
    assert page.evaluate("() => window.__pwned") is None
    assert page.locator("#operator-dock a").count() == 0


