/* herdr-brain service worker: installability + Web Push (S5).
 *
 * Cache discipline (deliberate): this worker NEVER caches anything. The
 * server sends Cache-Control: no-cache on index.html and every asset,
 * and asset URLs are build-stamped (?v=<git hash>), so the phone always
 * revalidates and runs the deployed build. The pass-through fetch
 * handler exists solely to satisfy PWA installability.
 *
 * Push discipline: `push` events carry the brain's own payload contract
 * (v/kind/pane_id/title/body/tag + optional question/actions). The
 * worker shows them with the payload's tag (the herd slot collapses,
 * retracts and is replaced by the coordinator server-side — the tag is
 * what makes the phone's notification drawer follow).
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
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

/* Pass-through handler: satisfies installability without caching anything. */
self.addEventListener("fetch", function () {});

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
    clickPlan: clickPlan
  };
}
