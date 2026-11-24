"""The formatted reader (and the call drawer's reader) must authenticate.

Once the first device is paired the brain gates READS too, so the
rendered-snapshot fetches (`/conversation/<pane>/rendered` and
`/call-history/rendered`) need the device bearer like every other read.
They were built on a bare `fetch`: the paired phone got 403, the PWA kept
the raw text and the formatted view plus its tap-to-listen anchors
disappeared while every other poll kept answering 200.
"""

from playwright.sync_api import expect

from test_glance_sentence_replay import CONTENT, SURFACE, app, payload, responses

TOKEN = "device-bearer-for-the-reader-test"
TOKEN_KEY = "herdr_brain_device_token"


def _paired_app(karaoke_app):
    harness = karaoke_app()
    responses(harness, payload())
    # A paired browser: the credential the pairing card stored at claim time.
    harness.context.add_init_script(
        "localStorage.setItem(%r, %r);" % (TOKEN_KEY, TOKEN)
    )
    harness.context.add_init_script(
        "localStorage.setItem('herdr-brain-voice-engine', 'navegador');"
    )
    harness.page.set_viewport_size({"width": 1000, "height": 1000})
    harness.load()
    return harness


def test_rendered_snapshots_carry_the_device_bearer(karaoke_app):
    harness = _paired_app(karaoke_app)
    expect(harness.page.locator(SURFACE)).to_have_count(1)
    expect(harness.page.locator(CONTENT)).to_have_count(1)  # formatted view mounted

    rendered = [r for r in harness.requests if r.url.split("?")[0].endswith("/rendered")]
    assert rendered, "the reader never asked for a rendered snapshot"
    unauthenticated = [
        r.url for r in rendered
        if r.headers.get("authorization") != "Bearer " + TOKEN
    ]
    assert unauthenticated == [], unauthenticated
