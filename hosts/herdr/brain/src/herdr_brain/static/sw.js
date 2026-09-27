/* herdr-brain service worker: installability only.
 *
 * Cache discipline (deliberate): this worker NEVER caches anything. The
 * server sends Cache-Control: no-cache on index.html and every asset, and
 * asset URLs are build-stamped (?v=<git hash>), so the phone always
 * revalidates and runs the deployed build. The pass-through fetch handler
 * exists solely to satisfy PWA installability.
 */

self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

/* Pass-through handler: satisfies installability without caching anything. */
self.addEventListener("fetch", function () {});
