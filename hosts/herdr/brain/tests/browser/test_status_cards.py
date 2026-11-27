"""The Ajustes status cards (POST-08): Cuotas de IA + Carga del host.

The cards are what's under test: OPENING Ajustes fires GET /quotas +
GET /load exactly once each through readFetch (the device bearer rides
withDeviceAuth) and NOTHING fires at boot (C3 — the conftest fails
closed on undeclared traffic). Bars, chips, meta, honest
unavailable/stale/fail states and the per-card ↻ refresh ride the
payload shapes the server actually ships (POST-01 / POST-02).
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

QUOTAS_OK = {
    "available": True,
    "reason": "",
    "stale": False,
    "fetched_at": "2026-11-26T17:00:00+00:00",
    "providers": [
        {
            "provider": "claude",
            "display_name": "Claude",
            "kind": "subscription",
            "label": "5h window",
            "used_percent": 42,
            "resets_at": "2026-11-26T21:59:00+00:00",
            "state": "warning",
            "status_indicator": None,
            "status_summary": None,
            "detail": "session roll soon",
            "cost_today_usd": 2.1,
            "cost_month_to_date_usd": 14.5,
        },
        {
            "provider": "gemini",
            "display_name": "Gemini",
            "kind": "api",
            "label": None,
            "used_percent": None,
            "resets_at": None,
            "state": "ok",
            "status_indicator": None,
            "status_summary": None,
            "detail": None,
            "cost_today_usd": None,
            "cost_month_to_date_usd": None,
        },
    ],
}

QUOTAS_UNAVAILABLE = {
    "available": False,
    "reason": "ai-quotas-not-found",
    "stale": False,
    "fetched_at": None,
    "providers": [],
}

LOAD_OK = {
    "host": "harness",
    "available": True,
    "ts": "2026-11-26T17:00:00+00:00",
    "cpu_percent": 63.4,
    "ram_percent": 41,
    "disk_percent": 88,
    "net_rx_bps": 1234,
    "net_tx_bps": 512,
    "alerts": {"configured": 2, "active": 1},
}

LOAD_UNAVAILABLE = {
    "host": "harness",
    "available": False,
    "reason": "load metrics read Linux /proc (unavailable on darwin)",
    "alerts": {"configured": 0, "active": 0},
}


def _cards_page(karaoke_app, *, quotas=None, load=None, quotas_status=200):
    harness = karaoke_app()
    harness.respond(
        "/quotas",
        quotas if quotas is not None else QUOTAS_UNAVAILABLE,
        status=quotas_status,
    )
    harness.respond("/load", load if load is not None else LOAD_UNAVAILABLE)
    page = harness.load().page
    return harness, page


def _open_ajustes(page):
    page.locator("#settings-btn").click()
    expect(page.locator("#settings-sheet")).to_be_visible()


def _requests(harness, path):
    return [r for r in harness.requests if r.url.endswith(path)]


def test_opening_ajustes_fetches_both_cards_exactly_once_and_boot_is_silent(karaoke_app):
    harness, page = _cards_page(karaoke_app, quotas=QUOTAS_OK, load=LOAD_OK)
    assert _requests(harness, "/quotas") == []
    assert _requests(harness, "/load") == []

    _open_ajustes(page)
    expect(page.locator("#quotas-box .quota-row")).to_have_count(2)
    expect(page.locator("#load-box .load-row")).to_have_count(3)

    assert len(_requests(harness, "/quotas")) == 1
    assert len(_requests(harness, "/load")) == 1


def test_quota_rows_render_bar_chip_and_meta(karaoke_app):
    _harness, page = _cards_page(karaoke_app, quotas=QUOTAS_OK, load=LOAD_OK)
    _open_ajustes(page)
    box = page.locator("#quotas-box")
    expect(box.locator(".quota-row")).to_have_count(2)
    expect(box.locator(".quota-name").first).to_have_text("Claude")
    expect(box.locator(".quota-bar-fill").first).to_have_attribute(
        "style", re.compile(r"width:\s*42%")
    )
    expect(box.locator(".quota-state").first).to_have_text("aviso")  # live es dict
    meta = box.locator(".quota-meta").first
    expect(meta).to_contain_text("42%")
    expect(meta).to_contain_text("5h window")
    expect(meta).to_contain_text("session roll soon")
    expect(meta).to_contain_text(re.compile(r"Renueva \w{3} \d{2}:\d{2}"))
    expect(meta).to_contain_text("$2.10 hoy")
    expect(meta).to_contain_text("$14.50 mes")
    # gemini has no usable percent: no bar, ok chip
    expect(box.locator(".quota-row").nth(1).locator(".quota-bar-fill")).to_have_count(0)
    expect(box.locator(".quota-row").nth(1).locator(".quota-state")).to_have_text("ok")


def test_stale_snapshot_shows_the_cached_hint(karaoke_app):
    stale = dict(QUOTAS_OK, stale=True)
    _harness, page = _cards_page(karaoke_app, quotas=stale, load=LOAD_OK)
    _open_ajustes(page)
    expect(page.locator("#quotas-box .quota-stale")).to_contain_text("caché")


def test_unavailable_quotas_render_the_reason_and_no_rows(karaoke_app):
    _harness, page = _cards_page(karaoke_app)  # defaults: unavailable
    _open_ajustes(page)
    box = page.locator("#quotas-box")
    expect(box.locator(".push-status")).to_contain_text("Sin datos de cuotas")
    expect(box.locator(".quota-reason")).to_have_text("ai-quotas-not-found")
    expect(box.locator(".quota-row")).to_have_count(0)


def test_load_card_renders_host_metrics_net_and_alerts(karaoke_app):
    _harness, page = _cards_page(karaoke_app, quotas=QUOTAS_OK, load=LOAD_OK)
    _open_ajustes(page)
    box = page.locator("#load-box")
    expect(box.locator(".push-status")).to_have_text("harness")
    expect(box.locator(".load-row")).to_have_count(3)
    expect(box.locator(".load-row").nth(2).locator(".load-value")).to_have_text("88%")
    expect(box.locator(".load-row").nth(2).locator(".quota-bar-fill")).to_have_attribute(
        "style", re.compile(r"width:\s*88%")
    )
    expect(box.locator(".load-net")).to_contain_text("↓ 1.2 KB/s")
    expect(box.locator(".load-net")).to_contain_text("↑ 512 B/s")
    expect(box.locator(".load-alerts")).to_contain_text("1/2")


def test_load_before_the_first_sample_shows_the_waiting_state(karaoke_app):
    waiting = {
        "host": "harness", "available": True, "ts": None,
        "cpu_percent": None, "ram_percent": None, "disk_percent": None,
        "net_rx_bps": None, "net_tx_bps": None,
        "alerts": {"configured": 0, "active": 0},
    }
    _harness, page = _cards_page(karaoke_app, quotas=QUOTAS_OK, load=waiting)
    _open_ajustes(page)
    box = page.locator("#load-box")
    expect(box.locator(".load-waiting")).to_contain_text("primera muestra")
    expect(box.locator(".load-row")).to_have_count(0)


def test_load_unavailable_renders_the_reason_and_no_rows(karaoke_app):
    _harness, page = _cards_page(karaoke_app)  # defaults: unavailable
    _open_ajustes(page)
    box = page.locator("#load-box")
    expect(box.locator(".push-status")).to_contain_text("no disponible")
    expect(box.locator(".quota-reason")).to_contain_text("unavailable on darwin")
    expect(box.locator(".load-row")).to_have_count(0)


def test_refresh_button_refetches_the_card(karaoke_app):
    harness, page = _cards_page(karaoke_app, quotas=QUOTAS_OK, load=LOAD_OK)
    _open_ajustes(page)
    expect(page.locator("#quotas-box .quota-row")).to_have_count(2)
    assert len(_requests(harness, "/quotas")) == 1

    page.locator("#quotas-box .cards-refresh").click()
    expect(page.locator("#quotas-box .quota-row")).to_have_count(2)
    assert len(_requests(harness, "/quotas")) == 2
    # The sibling card is untouched by the quotas ↻: still one fetch.
    assert len(_requests(harness, "/load")) == 1


def test_a_500_answer_fails_soft_and_the_other_card_survives(karaoke_app):
    _harness, page = _cards_page(karaoke_app, load=LOAD_OK, quotas_status=500)
    _open_ajustes(page)
    box = page.locator("#quotas-box")
    expect(box.locator(".push-status")).to_contain_text("sin conexión")
    expect(box.locator(".quota-row")).to_have_count(0)
    expect(page.locator("#load-box .load-row")).to_have_count(3)
