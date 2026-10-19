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
    ) == "#1b1d22"
    assert page.evaluate("() => localStorage.getItem('herdr-brain-theme')") == "dark"
    expect_label = page.locator("#theme-btn")
    assert "Oscuro" in (expect_label.text_content() or "")

    # The choice survives a reload.
    page.reload()
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"


def test_dark_cockpit_stays_darker_than_the_body(karaoke_app):
    harness = karaoke_app()
    harness.context.add_init_script(
        "localStorage.setItem('herdr-brain-theme', 'dark')"
    )
    page = harness.load().page
    colours = page.evaluate("""() => ({
        body: getComputedStyle(document.body).backgroundColor,
        cockpit: getComputedStyle(document.getElementById('agent-view')).backgroundColor
    })""")
    assert colours == {
        "body": "rgb(27, 29, 34)",
        "cockpit": "rgb(10, 10, 10)",
    }


def test_light_markdown_colours_have_readable_contrast(karaoke_app):
    harness = karaoke_app()
    harness.context.add_init_script(
        "localStorage.setItem('herdr-brain-theme', 'light')"
    )
    page = harness.load().page
    ratios = page.evaluate("""() => {
        const sample = document.createElement('div');
        sample.className = 'reader-content';
        sample.innerHTML = '<h2>Heading</h2><strong>Emphasis</strong>' +
            '<a href="#">Link</a><code>inline</code><pre><code>block</code></pre>' +
            '<table><thead><tr><th>Column</th></tr></thead></table>';
        document.getElementById('glance-turns').appendChild(sample);
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 1;
        const ctx = canvas.getContext('2d');
        function rgba(css) {
            ctx.clearRect(0, 0, 1, 1);
            ctx.fillStyle = css;
            ctx.fillRect(0, 0, 1, 1);
            return Array.from(ctx.getImageData(0, 0, 1, 1).data);
        }
        function background(el) {
            if (!el) return [255, 255, 255];
            const colour = rgba(getComputedStyle(el).backgroundColor);
            const parent = colour[3] < 255 ? background(el.parentElement) : colour;
            const alpha = colour[3] / 255;
            return colour.slice(0, 3).map((c, i) => c * alpha + parent[i] * (1 - alpha));
        }
        function luminance(rgb) {
            return rgb.slice(0, 3).map(c => {
                c /= 255;
                return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
            }).reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
        }
        const result = {};
        for (const selector of ['h2', 'strong', 'a', 'code', 'pre code', 'th']) {
            const el = sample.querySelector(selector);
            const fg = luminance(rgba(getComputedStyle(el).color));
            const bg = luminance(background(el));
            result[selector] = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
        }
        sample.remove();
        return result;
    }""")
    for element, ratio in ratios.items():
        assert ratio >= 4.5, f"{element}: contrast {ratio:.2f}:1 is below 4.5:1"
