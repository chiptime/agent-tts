"""Browser integration tests for the Mermaid diagram viewer and lightbox modal."""

from __future__ import annotations

from urllib.parse import quote
from playwright.sync_api import expect


MERMAID_MD = "```mermaid\ngraph TD\n  A[Inicio] --> B[Fin]\n```"
MERMAID_HTML = (
    '<p>Diagrama de arquitectura:</p>'
    '<pre><code class="language-mermaid">graph TD\n  A[Inicio] --> B[Fin]</code></pre>'
)


def _setup_mermaid_conversation(harness, html=MERMAID_HTML, text=MERMAID_MD):
    pane_id = "test:mermaid"
    session_id = "mermaid-ses"
    data = {
        "pane_id": pane_id,
        "session_id": session_id,
        "agent": "test-agent",
        "turns": [
            {
                "turn_id": "turn-1",
                "role": "assistant",
                "text": text,
                "html": html,
                "map": {"contract": "reader-pipeline/anchors@1", "total_sents": 1}
            }
        ],
        "window": 20
    }
    harness.respond("/conversation", data, query=("pane_id",))
    harness.respond("/conversation/" + quote(pane_id, safe="") + "/rendered", data)
    harness.context.add_init_script(
        f"localStorage.setItem('herdr-brain-pane', '{pane_id}');"
        f"localStorage.setItem('herdr-brain-session', '{session_id}');"
    )
    return pane_id


def test_mermaid_diagram_renders_card_and_svg(karaoke_app):
    harness = karaoke_app()
    _setup_mermaid_conversation(harness)
    page = harness.load().page

    # Wait for the reader turn to be rendered
    card = page.locator("#glance-turns .mermaid-card")
    expect(card).to_be_visible(timeout=5000)

    # Check toolbar elements
    expect(card.locator(".mermaid-badge")).to_have_text("Mermaid")
    expect(card.locator(".mermaid-btn-toggle")).to_have_text("Código")
    expect(card.locator(".mermaid-btn-zoom")).to_be_visible()

    # Check rendered SVG diagram
    svg = card.locator(".mermaid-diagram svg")
    expect(svg).to_be_visible(timeout=5000)
    assert page.evaluate("() => document.querySelector('.mermaid-diagram svg') !== null")


def test_mermaid_toggle_switches_code_and_diagram(karaoke_app):
    harness = karaoke_app()
    _setup_mermaid_conversation(harness)
    page = harness.load().page

    card = page.locator("#glance-turns .mermaid-card")
    expect(card).to_be_visible(timeout=5000)

    toggle_btn = card.locator(".mermaid-btn-toggle")
    code_wrap = card.locator(".mermaid-code-wrap")

    # Initially code is hidden
    expect(code_wrap).to_have_class("mermaid-code-wrap hidden")
    assert toggle_btn.text_content() == "Código"

    # Toggle to show code
    toggle_btn.click()
    expect(code_wrap).not_to_have_class("mermaid-code-wrap hidden")
    assert toggle_btn.text_content() == "Diagrama"

    # Toggle back to hide code
    toggle_btn.click()
    expect(code_wrap).to_have_class("mermaid-code-wrap hidden")
    assert toggle_btn.text_content() == "Código"


def test_mermaid_modal_lightbox_viewer_interaction(karaoke_app):
    harness = karaoke_app()
    _setup_mermaid_conversation(harness)
    page = harness.load().page

    card = page.locator("#glance-turns .mermaid-card")
    expect(card).to_be_visible(timeout=5000)

    modal = page.locator("#mermaid-modal")
    backdrop = page.locator("#mermaid-backdrop")

    # Initially modal is hidden
    expect(modal).to_have_class("mermaid-modal hidden")
    expect(backdrop).to_have_class("mermaid-backdrop hidden")

    # Clicking diagram or zoom button opens modal
    card.locator(".mermaid-diagram-wrap").click()
    expect(modal).not_to_have_class("mermaid-modal hidden")
    expect(backdrop).not_to_have_class("mermaid-backdrop hidden")

    # Canvas contains the SVG
    canvas_svg = modal.locator("#mm-canvas svg")
    expect(canvas_svg).to_be_visible()

    # Zoom controls work
    zoom_display = modal.locator("#mm-zoom-display")
    assert zoom_display.text_content() == "100%"

    modal.locator("#mm-zoom-in").click()
    assert zoom_display.text_content() == "125%"

    modal.locator("#mm-zoom-reset").click()
    assert zoom_display.text_content() == "100%"

    # Close modal via close button
    modal.locator("#mm-close").click()
    expect(modal).to_have_class("mermaid-modal hidden")
    expect(backdrop).to_have_class("mermaid-backdrop hidden")
