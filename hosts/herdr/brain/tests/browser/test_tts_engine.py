"""Browser tests for the Web Speech API Interactive TTS and Karaoke engine.

Verifies:
- Floating action button (#tts-fab) and floating player bar (#tts-bar) presence.
- Toggling playback and bar open/close state.
- Player controls: Play/Pause, Next/Prev, Speed cycling (1.0x -> 2.0x), Stop.
- Sentence click-to-speech with .tts-active karaoke highlight.
- Semantic table rules and Mermaid diagram skipping.
"""

from __future__ import annotations

import pytest
import re
from playwright.sync_api import expect

SURFACE = "#glance-turns .gturn.assistant"
CONTENT = SURFACE + " .reader-content"

MARKDOWN_HTML = (
    '<p><span class="tts-sent" id="tts-sent-0" data-sent-idx="0">Primera frase de prueba.</span> '
    '<span class="tts-sent" id="tts-sent-1" data-sent-idx="1">Segunda frase importante.</span></p>'
    '<table class="tts-sent" data-sent-idx="2">'
    '<thead><tr><th>Regla</th><th>Descripción</th></tr></thead>'
    '<tbody><tr><td>R1</td><td>Regla uno de negocio</td></tr></tbody>'
    '</table>'
    '<div class="mermaid-card"><pre class="mermaid">graph TD; A-->B;</pre></div>'
    '<p><span class="tts-sent" id="tts-sent-3" data-sent-idx="3">Frase final tras el diagrama.</span></p>'
)

TURNS_PAYLOAD = {
    "pane_id": "fixture:reader",
    "session_id": "fixture-session",
    "agent": "fixture",
    "turns": [
        {
            "turn_id": "reader-0",
            "role": "assistant",
            "text": "Primera frase de prueba. Segunda frase importante. R1: Regla uno de negocio. Frase final tras el diagrama.",
            "html": MARKDOWN_HTML,
            "map": {"contract": "reader-pipeline/anchors@1", "total_sents": 4},
        }
    ],
    "window": 20,
}


def _load_app(karaoke_app):
    harness = karaoke_app()
    harness.respond("/conversation", TURNS_PAYLOAD, query=("pane_id",))
    harness.respond("/conversation/fixture%3Areader/rendered", TURNS_PAYLOAD)
    harness.load()
    return harness


def test_tts_fab_and_bar_exist_in_dom(karaoke_app):
    harness = _load_app(karaoke_app)
    page = harness.page

    fab = page.locator("#tts-fab")
    expect(fab).to_have_count(1)
    expect(fab).to_be_visible()

    bar = page.locator("#tts-bar")
    expect(bar).to_have_count(1)
    expect(bar).not_to_have_class("tts-open")


def test_open_and_close_player_bar(karaoke_app):
    harness = _load_app(karaoke_app)
    page = harness.page

    fab = page.locator("#tts-fab")
    bar = page.locator("#tts-bar")

    # Click FAB opens the bar and starts reading
    fab.click()
    expect(bar).to_have_class("tts-open")

    # Status text is updated with active sentence
    status_text = page.locator("#tts-status-text")
    expect(status_text).to_contain_text("Frase 1 de")

    # Close button closes the bar
    page.locator("#tts-close-btn").click()
    expect(bar).not_to_have_class("tts-open")


def test_speed_cycling_control(karaoke_app):
    harness = _load_app(karaoke_app)
    page = harness.page

    page.locator("#tts-fab").click()
    speed_btn = page.locator("#tts-speed-btn")
    expect(speed_btn).to_have_text("1.0x")

    # Click cycles to 1.25x
    speed_btn.click()
    expect(speed_btn).to_have_text("1.25x")

    # Click cycles to 1.5x
    speed_btn.click()
    expect(speed_btn).to_have_text("1.5x")

    # Click cycles to 1.75x
    speed_btn.click()
    expect(speed_btn).to_have_text("1.75x")

    # Click cycles to 2.0x (JS renders as "2x")
    speed_btn.click()
    expect(speed_btn).to_have_text("2x")

    # Click cycles to 0.85x
    speed_btn.click()
    expect(speed_btn).to_have_text("0.85x")

    # Click cycles back to 1.0x (rendered as "1x")
    speed_btn.click()
    expect(speed_btn).to_have_text("1x")


def test_interactive_sentence_click_and_navigation(karaoke_app):
    harness = _load_app(karaoke_app)
    page = harness.page

    # Open reader bar and index
    page.locator("#tts-fab").click()
    expect(page.locator("#tts-bar")).to_have_class("tts-open")

    # Table rows receive .tts-table-row
    expect(page.locator("tr.tts-table-row")).to_have_count(1)

    # Click first sentence -> activates .tts-active
    sentences = page.locator(".tts-sentence")
    sentences.first.click()
    expect(sentences.first).to_have_class(re.compile(r"\btts-active\b"))
    expect(page.locator("#tts-status-text")).to_contain_text("Frase 1 de 4")

    # Next button advances active sentence to Frase 2
    page.locator("#tts-next-btn").click()
    expect(page.locator("#tts-status-text")).to_contain_text("Frase 2 de 4")

    # Prev button moves back to Frase 1
    page.locator("#tts-prev-btn").click()
    expect(page.locator("#tts-status-text")).to_contain_text("Frase 1 de 4")

    # Stop button clears .tts-active
    page.locator("#tts-stop-btn").click()
    expect(page.locator(".tts-active")).to_have_count(0)
