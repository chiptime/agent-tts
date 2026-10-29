"""The Avisos push card in Ajustes (S5), browser-side.

PushManager and the notification permission are stubbed from the page
(no service worker and no push service exist in this harness — the card
is what's under test, not the browser's push stack): Activar posts the
stubbed subscription to /push/subscribe with the device bearer, Probar
reports the sent count, Desactivar unsubscribes both ends.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

PUBLIC_KEY = (
    "BJyWsP1xktp0JFgftGcYQf5P1O_r-WnpHwX4xkyY4BWalHuNcl9j3TZpIeRHAl6gxlIIkG6RRIllcOXY8Rt7m7M"
)


def _push_page(karaoke_app, *, key_body=None, test_body=None, subscribe_body=None):
    harness = karaoke_app()
    harness.respond(
        "/push/key",
        key_body or {"enabled": True, "public_key": PUBLIC_KEY},
    )
    if subscribe_body is not None:
        harness.respond("/push/subscribe", subscribe_body, method="POST")
    if test_body is not None:
        harness.respond("/push/test", test_body, method="POST")
    page = harness.load().page
    return harness, page


def _stub_push_stack(page, endpoint="https://push.example.invalid/f/abc"):
    """Installs the fake PushManager + permission surface the card uses
    (app.js reads both lazily, at click time — after this call)."""
    page.evaluate(
        """([endpoint]) => {
            window.Notification = {
              permission: "default",
              requestPermission: () => Promise.resolve("granted"),
            };
            const subscription = {
              endpoint: endpoint,
              keys: { p256dh: "BROWSER-P256DH", auth: "BROWSER-AUTH" },
              toJSON() { return { endpoint: this.endpoint, keys: this.keys }; },
              unsubscribe() { window.__pushStub.unsubscribed = true; return Promise.resolve(true); },
            };
            window.__pushStub = {
              subscription,
              unsubscribed: false,
              subscribedWith: null,
              getRegistrationCalls: 0,
            };
            // The harness context has no serviceWorker AT ALL (blocked):
            // install the whole property, so app.js's
            // `"serviceWorker" in navigator` gate finds the double.
            Object.defineProperty(navigator, "serviceWorker", {
              configurable: true,
              value: {
                getRegistration: () => {
                  window.__pushStub.getRegistrationCalls++;
                  return Promise.resolve({
                    pushManager: {
                      // Stateful like the real thing: the subscription
                      // exists once subscribe() created it, and
                      // unsubscribe() clears it.
                      getSubscription: () => Promise.resolve(
                        window.__pushStub.subscribedWith ? subscription : null
                      ),
                      subscribe: (options) => {
                        window.__pushStub.subscribedWith = options;
                        return Promise.resolve(subscription);
                      },
                    },
                  });
                },
              },
            });
        }""",
        [endpoint],
    )


def test_card_boots_with_the_server_key(karaoke_app):
    _harness, page = _push_page(karaoke_app)
    page.locator("#settings-btn").click()
    expect(page.locator("#push-box")).to_be_visible()
    expect(page.locator("#push-box .push-status")).to_contain_text(
        "Sin avisos push en este dispositivo"
    )
    expect(page.locator("#push-box .push-enable")).to_be_enabled()


def test_card_without_server_keys_shows_the_operator_hint(karaoke_app):
    _harness, page = _push_page(
        karaoke_app, key_body={"enabled": False, "public_key": None}
    )
    page.locator("#settings-btn").click()
    expect(page.locator("#push-box .push-status")).to_contain_text(
        "no pudo crear sus claves de push"
    )
    assert page.locator("#push-box .push-enable").count() == 0


def test_activar_subscribes_and_posts_the_subscription(karaoke_app):
    harness, page = _push_page(
        karaoke_app,
        subscribe_body={"endpoint": "https://push.example.invalid/f/abc", "device": "phone"},
    )
    _stub_push_stack(page)
    page.locator("#settings-btn").click()
    page.locator("#push-box .push-enable").click()

    expect(page.locator("#push-box .push-status")).to_contain_text(
        "Avisos activos en este dispositivo"
    )
    posts = [r for r in harness.requests if r.url.endswith("/push/subscribe")]
    assert len(posts) == 1
    body = posts[0].post_data_json
    assert body["endpoint"] == "https://push.example.invalid/f/abc"
    assert body["keys"] == {"p256dh": "BROWSER-P256DH", "auth": "BROWSER-AUTH"}
    # The VAPID key reached PushManager as a BufferSource, not a string.
    subscribed_with = page.evaluate("() => window.__pushStub.subscribedWith")
    assert subscribed_with["userVisibleOnly"] is True
    assert subscribed_with["applicationServerKey"] is not None


def test_a_refused_subscribe_surfaces_the_server_detail(karaoke_app):
    harness, page = _push_page(karaoke_app, subscribe_body=None)
    harness.api[("POST", "/push/subscribe")] = (
        {"detail": "subscription needs keys.p256dh and keys.auth"}, 422, set()
    )
    _stub_push_stack(page)
    page.locator("#settings-btn").click()
    page.locator("#push-box .push-enable").click()

    expect(page.locator("#toast")).to_contain_text("keys.p256dh")
    # Not subscribed: the card keeps offering Activar.
    expect(page.locator("#push-box .push-enable")).to_be_visible()


def test_probar_reports_the_sent_count(karaoke_app):
    harness, page = _push_page(
        karaoke_app,
        subscribe_body={"endpoint": "https://push.example.invalid/f/abc"},
        test_body={"sent": 2},
    )
    _stub_push_stack(page)
    page.locator("#settings-btn").click()
    page.locator("#push-box .push-enable").click()
    expect(page.locator("#push-box .push-test")).to_be_visible()
    page.locator("#push-box .push-test").click()

    expect(page.locator("#toast")).to_contain_text("2")
    posts = [r for r in harness.requests if r.url.endswith("/push/test")]
    assert len(posts) == 1


def test_desactivar_unsubscribes_the_server_and_the_browser(karaoke_app):
    harness, page = _push_page(
        karaoke_app,
        subscribe_body={"endpoint": "https://push.example.invalid/f/abc"},
    )
    harness.respond("/push/unsubscribe", {"ok": True}, method="POST")
    _stub_push_stack(page)
    page.locator("#settings-btn").click()
    page.locator("#push-box .push-enable").click()
    expect(page.locator("#push-box .push-disable")).to_be_visible()

    page.locator("#push-box .push-disable").click()
    expect(page.locator("#push-box .push-status")).to_contain_text(
        "Sin avisos push en este dispositivo"
    )
    posts = [r for r in harness.requests if r.url.endswith("/push/unsubscribe")]
    assert len(posts) == 1
    assert json.loads(posts[0].post_data) == {
        "endpoint": "https://push.example.invalid/f/abc"
    }
    assert page.evaluate("() => window.__pushStub.unsubscribed") is True




