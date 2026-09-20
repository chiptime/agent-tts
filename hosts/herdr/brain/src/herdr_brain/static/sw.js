/* herdr-brain service worker: installability only.
 * Deliberately no caching — the API, audio and UI assets always hit the
 * network so the brain's answers are never served stale. */

self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

/* Pass-through handler: satisfies installability without caching anything. */
self.addEventListener("fetch", function () {});
