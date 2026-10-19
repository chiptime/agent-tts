"""Two explicit themes: light and dark, switchable and persisted.

The dark theme speaks the TUI's own language (near-black base/panel), and
the choice lives in localStorage, defaulting to the system preference.
"""

from __future__ import annotations


def test_light_by_default_and_dark_on_toggle(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", [])
    page = harness.load().page

    # Headless chromium prefers light: the light theme is the base.
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "light"
    assert page.evaluate(
        "() => getComputedStyle(document.body).getPropertyValue('--bg').trim()"
    ) == "#f4f6f9"

    page.locator("#settings-btn").click()
    page.locator("#theme-btn").click()

    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"
    assert page.evaluate(
        "() => getComputedStyle(document.body).getPropertyValue('--bg').trim()"
    ) == "#0a0a0a"
    assert page.evaluate("() => localStorage.getItem('herdr-brain-theme')") == "dark"
    expect_label = page.locator("#theme-btn")
    assert "Oscuro" in (expect_label.text_content() or "")

    # The choice survives a reload.
    page.reload()
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"
