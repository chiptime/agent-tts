"""Browser tests for the Calls drawer view (static/calls.js).

Proves real Chromium behavior with mocked backend service responses:
- list renders call records with date, duration, turn count, and status
- tapping a call opens the detail transcript view
- clicking back returns to the list
- empty and error states render gracefully
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

CALLS_FIXTURE = [
    {
        "call_id": "call-browser-1",
        "started_at": "2026-11-03T10:00:00Z",
        "ended_at": "2026-11-03T10:02:30Z",
        "duration_s": 150.0,
        "turn_count": 2,
        "status": "ended",
    },
    {
        "call_id": "call-browser-2",
        "started_at": "2026-11-03T10:10:00Z",
        "ended_at": "2026-11-03T10:11:00Z",
        "duration_s": 60.0,
        "turn_count": 1,
        "status": "interrupted",
    },
]

CALL_DETAIL_FIXTURE = {
    "call": CALLS_FIXTURE[0],
    "turns": [
        {"ts": "2026-11-03T10:00:05Z", "role": "user", "text": "hola brain desde browser test"},
        {"ts": "2026-11-03T10:00:10Z", "role": "assistant", "text": "hola usuario todo en orden"},
    ],
    "has_more": False,
}


def _harness(karaoke_app, calls_payload=None, detail_payload=None, calls_status=200):
    harness = karaoke_app()
    harness.static["/calls.js"] = "calls.js"
    if calls_payload is None:
        calls_payload = {"calls": CALLS_FIXTURE, "has_more": False}
    harness.respond("/calls", calls_payload, status=calls_status, query=("before", "limit"))
    if detail_payload is None:
        detail_payload = CALL_DETAIL_FIXTURE
    harness.respond(
        f"/calls/{CALLS_FIXTURE[0]['call_id']}",
        detail_payload,
        query=("before", "limit"),
    )
    return harness.load()


def test_calls_list_renders_items_and_detail_navigation(karaoke_app):
    harness = _harness(karaoke_app)
    page = harness.page

    # Mount Calls UI into #calls-container via window.Calls.createCallsUI
    page.evaluate("""
        () => {
            const container = document.getElementById("calls-container");
            const drawer = document.getElementById("call-drawer");
            drawer.style.transition = "none";
            drawer.classList.add("open");
            drawer.removeAttribute("inert");
            document.getElementById("calls-section").classList.remove("hidden");
            const ui = window.Calls.createCallsUI({
                doc: document,
                http: (url) => window.fetch(url).then(r => r.json()),
                t: (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key),
            });
            ui.mount(container);
            window.__callsUI = ui;
        }
    """)

    # Verify calls list rendered 2 items
    items = page.locator("#calls-container .call-item")
    expect(items).to_have_count(2)
    expect(items.nth(0)).to_contain_text("Finalizada")
    expect(items.nth(1)).to_contain_text("Interrumpida")

    # Click first call item to open detail
    items.nth(0).click()

    # Detail turns should be visible
    turns = page.locator("#calls-container .call-turn")
    expect(turns).to_have_count(2)
    expect(turns.nth(0)).to_contain_text("hola brain desde browser test")
    expect(turns.nth(1)).to_contain_text("hola usuario todo en orden")

    # Click back button
    back_btn = page.locator("#calls-container .calls-back-btn")
    expect(back_btn).to_be_visible()
    back_btn.click()

    # Returned to list
    expect(page.locator("#calls-container .calls-list-view")).to_be_visible()
    expect(items).to_have_count(2)


def test_calls_empty_state_renders(karaoke_app):
    harness = _harness(karaoke_app, calls_payload={"calls": [], "has_more": False})
    page = harness.page

    page.evaluate("""
        () => {
            const container = document.getElementById("calls-container");
            const drawer = document.getElementById("call-drawer");
            drawer.style.transition = "none";
            drawer.classList.add("open");
            drawer.removeAttribute("inert");
            document.getElementById("calls-section").classList.remove("hidden");
            const ui = window.Calls.createCallsUI({
                doc: document,
                http: (url) => window.fetch(url).then(r => r.json()),
                t: (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key),
            });
            ui.mount(container);
        }
    """)

    empty = page.locator("#calls-container .calls-empty")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("Sin llamadas registradas")


def test_calls_error_state_renders(karaoke_app):
    harness = _harness(karaoke_app, calls_payload={"detail": "server error"}, calls_status=500)
    page = harness.page

    page.evaluate("""
        () => {
            const container = document.getElementById("calls-container");
            const drawer = document.getElementById("call-drawer");
            drawer.style.transition = "none";
            drawer.classList.add("open");
            drawer.removeAttribute("inert");
            document.getElementById("calls-section").classList.remove("hidden");
            const ui = window.Calls.createCallsUI({
                doc: document,
                http: (url) => window.fetch(url).then(r => {
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return r.json();
                }),
                t: (key, vars) => (window.I18N ? window.I18N.t(key, vars) : key),
            });
            ui.mount(container);
        }
    """)

    err = page.locator("#calls-container .calls-error")
    expect(err).to_be_visible()
    expect(err).to_contain_text("Error al cargar las llamadas")
