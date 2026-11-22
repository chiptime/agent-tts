"""Exercise the real service worker; the route-only karaoke harness blocks it."""

import re
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import expect


STATIC = Path(__file__).resolve().parents[2] / "src/herdr_brain/static"
BUILD = "a11ce09"
ASSET_TYPES = {
    ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


@contextmanager
def shell_origin():
    """Serve the actual shell over loopback, with a synthetic build and Calls API."""
    assets = {
        "/" + path.relative_to(STATIC).as_posix(): path
        for path in STATIC.rglob("*") if path.suffix in ASSET_TYPES
    }
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    index = re.sub(r'((?:src|href)="/[^"?]+)(")', rf'\1?v={BUILD}\2', index)
    index = index.replace('id="app-version">dev<', f'id="app-version">{BUILD}<')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self.reply(200, "text/html; charset=utf-8", index.encode())
            elif path in assets:
                asset = assets[path]
                self.reply(200, ASSET_TYPES[asset.suffix], asset.read_bytes())
            elif path == "/calls":
                self.reply(200, "application/json", b'{"calls": [], "has_more": false}')
            else:
                self.reply(404, "application/json", b'{}')

        def reply(self, status, content_type, body):
            self.send_response(status)
            # A warm HTTP cache must not hide a missing SW cache entry.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def test_installed_shell_mounts_calls_when_offline(browser):
    context = browser.new_context(service_workers="allow", accept_downloads=False)
    try:
        page = context.new_page()
        with shell_origin() as origin:
            page.goto(origin + "/", wait_until="load")
            page.evaluate("""() => new Promise((resolve, reject) => {
                const sw = navigator.serviceWorker;
                const timer = setTimeout(() => reject(new Error('No active controller')), 5000);
                function check() {
                    if (!sw.controller) return;
                    sw.removeEventListener('controllerchange', check);
                    clearTimeout(timer);
                    resolve();
                }
                sw.addEventListener('controllerchange', check);
                check();
            })""")
            calls = page.locator("#calls-container")
            expect(calls.locator(".calls-empty:not(.hidden)")).to_have_count(1)
            expect(page.locator("#offline-banner")).to_be_hidden()

        # Keep API requests real: service data should fail offline, while the
        # navigation and all shell resources must be served by the worker.
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def record_console(message):
            path = urlsplit(message.location["url"]).path
            if message.type == "error" and Path(path).suffix in ASSET_TYPES:
                errors.append(f"{path}: {message.text}")

        page.on("console", record_console)
        context.set_offline(True)
        response = page.reload(wait_until="load")
        assert response.from_service_worker
        expect(page.locator("#app-version")).to_have_text(BUILD)
        expect(page.locator("#offline-banner")).to_have_text("Sin conexión")
        expect(page.locator("#offline-banner")).to_be_visible()
        assert calls.locator(".calls-root").count() == 1, errors
        expect(calls.locator(".calls-error:not(.hidden)")).to_have_text("Error al cargar las llamadas")
        assert not errors, errors
    finally:
        context.close()
