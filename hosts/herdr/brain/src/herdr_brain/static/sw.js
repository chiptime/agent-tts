/* herdr-brain service worker: offline shell (S4) + Web Push (S5).
 *
 * OFFLINE SHELL (doc 09). This worker used to be a deliberate
 * pass-through; it now precaches the app shell and nothing else. The
 * build stamp the server renders into the index (?v=<git hash> and the
 * footer) IS the shell identity: at install the worker fetches "/",
 * reads the stamped asset refs out of that HTML, and precaches exactly
 * those URLs under brain-shell-<build>. Activation deletes every
 * other brain-shell cache (a stale build never serves a mixed shell).
 *
 * Routing:
 *  - navigations → network-first, falling back to the cached "/"
 *    (ignoreSearch, so deep links like /?pane_id=w1:p2 resolve offline);
 *  - the EXPLICIT shell asset list → cache-first (stamped URLs change
 *    with the build, so a hit is by construction the current shell);
 *  - EVERYTHING else → network only. The API denylist is written out
 *    below for the reader, but the rule is default-deny: anything not
 *    in the shell list is never cached (risk 2: an authenticated
 *    transcript or approval body must never live in a persistent
 *    cache). Only 200, non-redirected, same-origin (basic) responses
 *    are ever stored (risk 1: an error page cached as the shell would
 *    brick the installed PWA).
 *
 * PUSH DISCIPLINE (S5, unchanged). `push` events carry the brain's own
 * payload contract (v/kind/pane_id/title/body/tag + optional
 * question/actions). The worker shows them with the payload's tag (the
 * herd slot collapses, retracts and is replaced by the coordinator
 * server-side — the tag is what makes the phone's notification drawer
 * follow).
 *
 * GATE BUTTON DECISION (deliberate, safe option): the brain's push
 * payloads may carry approve/reject actions for a live approval gate,
 * but this worker NEVER answers them itself. A decision POST would need
 * a credential the worker must not hold: the device bearer lives in the
 * page's localStorage (unreadable from here), and shipping the server's
 * approval token inside a payload would put a write secret on the wire
 * the phone's lock screen guards far worse than the PWA does. So every
 * tap — action button or body — only FOCUSES (or opens) the PWA; the
 * decision then happens in the page, where the approval card already
 * runs with the device bearer behind the access gate. The buttons exist
 * to say what to do, and to get the operator one tap away from doing
 * it.
 */

/* ── pure helpers (exported for tests/js) ───────────────────────────── */

/* The explicit shell: every static asset this PWA loads. A file not in
 * this list is never cached, even same-origin. */
var SHELL_ASSETS = [
  "/app.js", "/endpointing.js", "/vad.js", "/approval.js", "/pairing.js",
  "/destructive.js", "/history.js", "/dialogs.js", "/receipts.js",
  "/push.js", "/reader.js", "/toast.js", "/karaoke.js", "/announce.js",
  "/herd-group.js", "/mermaid.min.js", "/mermaid-viewer.js", "/speech.js",
  "/tts-engine.js", "/consult.js",
  "/i18n.js", "/i18n/es.js", "/i18n/en.js",
  "/settings.js", "/updates.js", "/dock.js",
  "/sw.js", "/manifest.webmanifest", "/icon.svg",
];
var SHELL_ASSET_SET = {};
for (var i = 0; i < SHELL_ASSETS.length; i++) SHELL_ASSET_SET[SHELL_ASSETS[i]] = true;

/* The API denylist, spelled out for the reader — the routing rule is
 * default-deny, so this list is documentation plus a belt: even if a
 * path were ever added to the shell by mistake, these stay network. */
var API_DENYLIST = [
  "/events", "/ask", "/transcribe", "/audio/", "/history", "/view", "/herd",
  "/push/", "/msg/", "/operator/", "/pane/", "/approval/", "/call/",
  "/devices", "/pair", "/build", "/tts", "/reset", "/conversation",
  "/call-history",
];

var CACHE_PREFIX = "brain-shell-";

function cacheName(build) {
  return CACHE_PREFIX + String(build || "dev");
}

/* The stamped refs of a rendered index → the precache list. The build
 * comes from the footer stamp (the same resolve_version() the operator
 * reads); every ref keeps its own ?v= (they are all the same build). */
function shellFromIndex(html) {
  var urls = ["/"];
  var build = "dev";
  if (typeof html !== "string" || !html) {
    return { build: build, urls: urls };
  }
  var refRe = /(?:src|href)="(\/[A-Za-z0-9._\-\/]+)(\?v=([A-Za-z0-9._\-]+))?"/g;
  var match;
  var seen = {};
  while ((match = refRe.exec(html)) !== null) {
    var path = match[1];
    /* Only shell assets: the index also refs nothing else today, but
     * the list is the authority, not the HTML. Bare refs (a dev build
     * without stamps) precache bare. */
    if (SHELL_ASSET_SET[path] !== true) continue;
    if (!seen[path]) {
      seen[path] = true;
      urls.push(match[2] ? path + match[2] : path);
    }
  }
  var footer = html.match(/id="app-version">v?([A-Za-z0-9._\-]+)</);
  if (footer && footer[1] && footer[1] !== "dev") {
    build = footer[1];
  } else if (urls.length > 1) {
    var stamped = urls[1].split("?v=")[1];
    if (stamped) build = stamped;
  }
  return { build: build, urls: urls };
}

/* One request URL → routing strategy.
 *  "navigation" — network-first, cached-shell fallback
 *  "asset"      — cache-first (explicit shell list only)
 *  "bypass"     — the worker never touches it (network only) */
function fetchPlan(url, method) {
  if (!url || method !== "GET") return "bypass";
  if (url.origin !== self.location.origin) return "bypass";
  var path = url.pathname;
  if (path === "/" || path === "/index.html") return "navigation";
  if (SHELL_ASSET_SET[path] === true) return "asset";
  return "bypass";
}

/* Only a 200, non-redirected, same-origin response may enter the
 * cache: an error page or a redirect cached as the shell would brick
 * the installed PWA (doc 09, risk 1). */
function cacheableResponse(status, redirected, type) {
  return status === 200 && redirected === false && type === "basic";
}

/* Every brain cache that is not the current build — deleted on
 * activate. Unrelated caches (none today, but the origin is shared)
 * are left alone. */
function cachesToSweep(names, currentBuild) {
  var keep = cacheName(currentBuild);
  var out = [];
  if (!names || !names.length) return out;
  for (var i = 0; i < names.length; i++) {
    if (names[i].indexOf(CACHE_PREFIX) === 0 && names[i] !== keep) out.push(names[i]);
  }
  return out;
}

/* One push payload → showNotification options. The payload is masked
 * server-side before it is encrypted to the subscription; the worker
 * renders it verbatim, never rewrites copy. Unknown kinds still show:
 * a newer brain's notification beats a silent one. */
function pushNotificationOptions(payload) {
  var data = payload || {};
  var options = {
    tag: data.tag || "brain-herd",
    data: { paneId: data.pane_id || null, gate: null },
    requireInteraction: data.kind === "blocked"
  };
  if (data.body) options.body = data.body;
  if (data.actions && data.actions.length) {
    options.actions = data.actions.map(function (action) {
      return { action: String(action.action || ""), title: String(action.title || "") };
    });
    var withGate = data.actions[0];
    if (withGate && withGate.gate) options.data.gate = String(withGate.gate);
  }
  return options;
}

/* One notificationclick → what the worker should do about it.
 * ALWAYS { focus: true, url } — never an approving POST (see the
 * decision note above). A gate action lands on the gate's deep link so
 * the PWA can surface the live approval card; a plain click lands on
 * the app root. */
function clickPlan(event) {
  var notification = (event && event.notification) || {};
  var data = notification.data || {};
  var action = (event && event.action) || "";
  if (action === "approve" || action === "reject") {
    return { focus: true, url: data.gate ? "/?gate=" + encodeURIComponent(data.gate) : "/" };
  }
  return { focus: true, url: "/" };
}

/* ── registration and handlers (the service worker context) ─────────── */

self.addEventListener("install", function (event) {
  /* The shell is read from the LIVE rendered index, so the precache
   * and the build stamp can never disagree. A failed fetch fails the
   * install — the browser keeps the previous worker and its shell. */
  event.waitUntil(
    fetch("/", { cache: "reload" }).then(function (resp) {
      if (!resp.ok) throw new Error("index fetch " + resp.status);
      return resp.text();
    }).then(function (html) {
      var shell = shellFromIndex(html);
      return caches.open(cacheName(shell.build)).then(function (cache) {
        return cache.addAll(shell.urls);
      });
    }).then(function () {
      self.skipWaiting();
    })
  );
});

self.addEventListener("activate", function (event) {
  /* Sweep every brain cache the current build does not name, then
   * claim the clients so the new shell serves this navigation. */
  event.waitUntil(
    caches.keys().then(function (names) {
      var current = null;
      for (var i = 0; i < names.length; i++) {
        if (names[i].indexOf(CACHE_PREFIX) === 0) {
          var build = names[i].slice(CACHE_PREFIX.length);
          if (current === null || build > current) current = build;
        }
      }
      var doomed = cachesToSweep(names, current);
      return Promise.all(doomed.map(function (name) {
        return caches.delete(name);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("fetch", function (event) {
  var plan = fetchPlan(new URL(event.request.url), event.request.method);

  if (plan === "navigation") {
    /* Network-first: the index carries the CURRENT stamps; offline it
     * degrades to the cached shell (ignoreSearch covers deep links). */
    event.respondWith(
      fetch(event.request).catch(function () {
        return caches.open(currentShellCache()).then(function (cache) {
          return cache.match("/", { ignoreSearch: true });
        }).then(function (cached) {
          return cached || Response.error();
        });
      })
    );
    return;
  }

  if (plan === "asset") {
    event.respondWith(
      caches.match(event.request).then(function (hit) {
        if (hit) return hit;
        return fetch(event.request).then(function (resp) {
          if (cacheableResponse(resp.status, resp.redirected, resp.type)) {
            var copy = resp.clone();
            caches.open(currentShellCache()).then(function (cache) {
              cache.put(event.request, copy);
            });
          }
          return resp;
        });
      })
    );
    return;
  }

  /* bypass: no respondWith — the browser does the request natively. */
});

/* The freshest brain-shell cache this worker knows about (the one it
 * installed with; older ones are gone after activate). */
function currentShellCache() {
  return CACHE_PREFIX + (shellMemory.build || "dev");
}

var shellMemory = { build: null };
self.addEventListener("message", function (event) {
  /* The page tells the worker which shell build it booted with (the
   * footer stamp) so runtime fills land in the right cache when the
   * page is newer than the installed worker. */
  var data = event && event.data;
  if (data && data.type === "brain-shell-build" && data.build) {
    shellMemory.build = data.build;
  }
});

/* Web Push (S5): show the payload. A push event with no parsable data
 * still shows a fallback line — the browser REQUIRES a visible
 * notification per push (silent pushes are reported and can revoke the
 * subscription), and an unparseable payload is information too. */
self.addEventListener("push", function (event) {
  var payload = null;
  try {
    payload = event.data ? event.data.json() : null;
  } catch (err) {
    payload = null;
  }
  var options = pushNotificationOptions(payload);
  var title = (payload && payload.title) || "herdr-brain";
  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

/* notificationclick: focus an existing window or open the PWA — the
 * only action this worker ever takes on a decision (see the gate
 * button decision note above). */
self.addEventListener("notificationclick", function (event) {
  var plan = clickPlan(event);
  event.notification.close();
  event.waitUntil(focusOrOpen(plan.url));
});

function focusOrOpen(url) {
  return self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(function (windows) {
      for (var i = 0; i < windows.length; i++) {
        var client = windows[i];
        if ("focus" in client) {
          if (client.url && client.url.indexOf("/?gate=") !== -1) continue;
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    });
}

/* Node (tests/js) gets the pure helpers; the worker context keeps the
 * listeners above. */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    pushNotificationOptions: pushNotificationOptions,
    clickPlan: clickPlan,
    shellFromIndex: shellFromIndex,
    cacheName: cacheName,
    fetchPlan: fetchPlan,
    cacheableResponse: cacheableResponse,
    cachesToSweep: cachesToSweep,
    SHELL_ASSETS: SHELL_ASSETS,
    API_DENYLIST: API_DENYLIST,
  };
}
