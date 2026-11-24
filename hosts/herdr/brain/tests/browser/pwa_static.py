"""The PWA static files every browser harness serves.

One list for every route-only harness (the shared ``KaraokeHarness``, the
real-media and desktop-call suites): each kept its own copy, and a script
added to ``index.html`` later (``send-track.js``) reached only one of them,
so the others blocked it as undeclared traffic. Add new static files HERE.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src/herdr_brain/static"

PWA_STATIC_NAMES = (
    "index.html",
    "app.js", "endpointing.js", "vad.js", "approval.js", "reader.js",
    "toast.js", "announce.js", "speech.js", "consult.js",
    "herd-group.js", "sw.js",
    "manifest.webmanifest", "icon.svg", "karaoke.js",
    "mermaid.min.js", "mermaid-viewer.js", "tts-engine.js",
    "pairing.js", "destructive.js", "history.js", "dialogs.js",
    "receipts.js", "send-track.js", "push.js", "calls.js",
    "i18n.js", "i18n/es.js", "i18n/en.js",
    "settings.js", "updates.js", "dock.js",
    "stream-uplink.js", "stream-worklet.js",
)


def static_routes() -> dict:
    """``{"/name": "name"}`` for every present file, plus ``/`` -> index."""
    routes = {"/": "index.html"}
    for name in PWA_STATIC_NAMES:
        if (STATIC / name).is_file():
            routes["/" + name] = name
    return routes
