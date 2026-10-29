"""PWA shell (S4): offline banner, build-stamp update notice, i18n es/en.

The service worker itself cannot run in this harness (service workers are
blocked by design — the SW routing decisions live in tests/js/sw-cache);
these tests pin the PAGE half of doc 09:

- the «Sin conexión» banner follows the browser's online/offline events;
- GET /build vs the footer stamp produces exactly ONE toast with a
  Recargar action, a matching build produces none, and a missing
  endpoint (404) stands the poller down silently;
- the language row switches every migrated label without a reload,
  persists across reloads, syncs <html lang>, and the Spanish default
  renders byte-identical to the pre-i18n strings;
- the Ajustes rows: text size walks the --font-scale steps, Zen hides
  the chrome and keeps the call button, the haptics row is hidden where
  navigator.vibrate does not exist (desktop Chromium).
"""

from __future__ import annotations

from playwright.sync_api import expect


def spanish_snapshot(page) -> dict:
    """Visible chrome under es — the byte-identical default contract."""
    return page.evaluate(
        """() => ({
            lang: document.documentElement.lang,
            settingsTitle: document.querySelector('#settings-sheet .s-title').textContent,
            call: document.getElementById('call-btn').textContent,
            tabAgent: document.getElementById('glance-label').textContent,
            tabScreen: document.querySelector('#tab-screen').textContent,
            tabHistory: document.querySelector('#tab-history').textContent,
            tabTerminal: document.querySelector('#tab-terminal').textContent,
            replyPlaceholder: document.getElementById('pane-reply-input').getAttribute('placeholder'),
            send: document.getElementById('pane-reply-send').textContent,
            pendingEmpty: document.getElementById('pending-empty').textContent,
            offline: document.getElementById('offline-banner').textContent,
        })"""
    )


EXPECTED_SPANISH = {
    "lang": "es",
    "settingsTitle": "Ajustes",
    "call": "📞 Llamar",
    "tabAgent": "Vista del agente",
    "tabScreen": "Pantalla",
    "tabHistory": "Historial",
    "tabTerminal": "Terminal",
    "replyPlaceholder": "Responde al agente…",
    "send": "Enviar",
    "pendingEmpty": "Sin pendientes",
    "offline": "Sin conexión",
}


def test_spanish_default_is_byte_identical(karaoke_harness):
    page = karaoke_harness.page
    assert spanish_snapshot(page) == EXPECTED_SPANISH
    assert page.evaluate("() => localStorage.getItem('brain:locale:v1')") is None


def test_both_dictionaries_loaded_in_the_page(karaoke_harness):
    page = karaoke_harness.page
    parity = page.evaluate(
        """() => {
            if (!window.I18N) return { ok: false, reason: "no runtime" };
            const probe = window.I18N.t("settings.title");
            return { ok: probe === "Ajustes", locales: window.I18N.locales() };
        }"""
    )
    assert parity["ok"] is True, parity
    assert parity["locales"] == ["es", "en"]


def test_offline_banner_follows_connectivity(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    assert page.locator("#offline-banner").is_hidden()
    harness.context.set_offline(True)
    expect(page.locator("#offline-banner")).to_be_visible()
    assert page.locator("#offline-banner").text_content() == "Sin conexión"
    harness.context.set_offline(False)
    expect(page.locator("#offline-banner")).to_be_hidden()


def test_newer_build_shows_one_reload_toast(karaoke_app):
    # The served build differs from the footer BEFORE the boot poll
    # runs — exactly the stale-shell world the toast exists for.
    harness = karaoke_app()
    harness.respond("/build", {"build": "0beef00"})
    page = harness.load().page
    expect(page.locator("#toast")).to_be_visible()
    assert "Nueva versión disponible" in (page.locator("#toast").text_content() or "")
    reload_button = page.locator("#toast .toast-action")
    expect(reload_button).to_be_visible()
    assert reload_button.text_content() == "Recargar"
    # The reload tap restarts the app (the synthetic world serves the
    # same raw shell again — the app reboots on /call-history).
    reload_button.click()
    page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/call-history')",
                           timeout=8000)
    assert page.locator("#conversation").count() == 1


def test_matching_build_never_toasts(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    # The raw-index harness footer reads "dev".
    harness.respond("/build", {"build": "dev"})
    page.evaluate("() => window.__brainUpdater && window.__brainUpdater.check()")
    page.wait_for_timeout(200)
    assert page.locator("#toast").is_hidden()


def test_missing_build_endpoint_stands_down(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    # Default /build is the 404: no toast, and one further manual check
    # never re-fetches — the poller stood down.
    page.evaluate("() => window.__brainUpdater && window.__brainUpdater.check()")
    page.wait_for_timeout(150)
    assert page.locator("#toast").is_hidden()
    assert page.evaluate("() => window.__brainUpdater.isDisabled()") is True
    before = page.evaluate("() => __karaokeHarness.fetchCalls.filter(p => p === '/build').length")
    page.evaluate("() => window.__brainUpdater && window.__brainUpdater.check()")
    page.wait_for_timeout(150)
    after = page.evaluate("() => __karaokeHarness.fetchCalls.filter(p => p === '/build').length")
    assert before == after


def test_language_switch_repaints_persists_and_returns(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    before = spanish_snapshot(page)

    page.locator("#settings-btn").click()
    page.locator("#language-btn").click()

    expect(page.locator("#settings-sheet .s-title")).to_have_text("Settings")
    assert page.evaluate("() => document.documentElement.lang") == "en"
    assert page.evaluate("() => localStorage.getItem('brain:locale:v1')") == "en"
    assert page.locator("#call-btn").text_content() == "📞 Call"
    assert page.locator("#tab-history").text_content() == "History"
    assert (
        page.locator("#pane-reply-input").get_attribute("placeholder")
        == "Reply to the agent…"
    )
    # The locale POST rides the device-auth read path (field additive).
    locale_posts = [
        request.post_data_json
        for request in harness.requests
        if request.method == "POST" and request.url.endswith("/call/context")
    ]
    assert {"locale": "en"} in locale_posts

    # Back to Spanish: every label byte-identical to the fresh boot.
    page.locator("#language-btn").click()
    expect(page.locator("#settings-sheet .s-title")).to_have_text("Ajustes")
    assert spanish_snapshot(page) == before

    # English survives a reload.
    page.locator("#language-btn").click()
    expect(page.locator("#settings-sheet .s-title")).to_have_text("Settings")
    page.reload()
    page.wait_for_function("() => __karaokeHarness.fetchCalls.includes('/call-history')")
    assert page.evaluate("() => document.documentElement.lang") == "en"
    assert page.locator("#tab-terminal").text_content() == "Terminal"


def test_theme_cycle_preserves_the_pinned_toggle(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    # Headless chromium prefers light: the pinned pre-S4 contract.
    page.locator("#settings-btn").click()
    # Nothing stored yet: mode auto, resolved light (system preference).
    assert "Auto" in (page.locator("#theme-btn").text_content() or "")
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "light"
    # First tap flips the system resolution — the pinned pre-S4 toggle.
    page.locator("#theme-btn").click()
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"
    assert "Oscuro" in (page.locator("#theme-btn").text_content() or "")
    assert page.evaluate("() => localStorage.getItem('herdr-brain-theme')") == "dark"
    page.locator("#theme-btn").click()
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "light"
    assert "Claro" in (page.locator("#theme-btn").text_content() or "")
    page.locator("#theme-btn").click()
    assert "Auto" in (page.locator("#theme-btn").text_content() or "")
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "light"
    page.locator("#theme-btn").click()
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"


def test_text_size_walks_three_css_steps(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    page.locator("#settings-btn").click()
    scales = []
    for _ in range(3):
        page.locator("#font-btn").click()
        scales.append(
            page.evaluate(
                "() => document.documentElement.style.getPropertyValue('--font-scale')"
            )
        )
    assert scales == ["1.15", "0.9", "1"]
    assert page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--font-scale').trim()"
    ) == "1"


def test_zen_hides_chrome_and_keeps_the_call(karaoke_harness):
    harness = karaoke_harness
    page = harness.page
    page.locator("#settings-btn").click()
    page.locator("#zen-btn").click()
    assert page.evaluate("() => document.body.classList.contains('zen')") is True
    assert page.locator("header").is_hidden()
    assert page.locator("#call-btn").is_visible()
    page.locator("#zen-btn").click()
    assert page.locator("header").is_visible()


def test_haptics_row_hidden_without_vibrate(karaoke_app):
    harness = karaoke_app()
    # A device with no vibrate API at all (the honest iOS/desktop case).
    harness.context.add_init_script(
        "Object.defineProperty(navigator, 'vibrate', "
        "{configurable: true, value: undefined});"
    )
    page = harness.load().page
    assert page.evaluate("() => typeof navigator.vibrate") == "undefined"
    page.locator("#settings-btn").click()
    assert page.locator("#haptics-row").is_hidden()
