/* Build-stamp update notice (S4, doc 09) — own conception.
 *
 * The server renders every asset ref with ?v=<git hash> and the
 * footer shows the same stamp; a phone whose service worker holds the
 * previous build's shell must learn that a new one exists. The page
 * polls GET /build (the same resolve_version() the footer prints) and
 * compares against its own footer stamp; a DIFFERENT served build
 * means the world moved — one toast per build, with a Recargar action.
 *
 * Multi-tab arbitration (doc 09 risk 4): every tab that detects posts
 * {type:"seen", build, id} on a BroadcastChannel; a tab withdraws its
 * toast when a SMALLER id claims the same build, so exactly one tab
 * (the smallest id among the detectors) keeps the toast no matter who
 * detected first. The reload tap broadcasts {type:"reload"} and every
 * tab reloads — a half-updated browser (one old tab, one new) is
 * worse than a coordinated restart.
 *
 * A server without GET /build (older brain) answers 404 → the poller
 * stands down silently: no toast loop, no console noise. */

(function (global) {
  "use strict";

  var CHANNEL = "brain-updates";
  var DEFAULT_INTERVAL_MS = 15 * 60 * 1000;

  function newerBuild(served, footer) {
    if (!served || !footer) return false;
    return String(served) !== String(footer);
  }

  /* Keep our toast unless a smaller peer id claimed the same build. */
  function keepToast(myId, seenFrom) {
    if (!myId || !seenFrom || !seenFrom.length) return true;
    for (var i = 0; i < seenFrom.length; i++) {
      if (seenFrom[i] < myId) return false;
    }
    return true;
  }

  function createUpdater(deps) {
    deps = deps || {};
    var id = deps.id || ("tab-" + Math.random().toString(36).slice(2, 10));
    var fetchBuild = deps.fetchBuild || function () { return Promise.resolve(null); };
    var footerBuild = deps.footerBuild || function () { return null; };
    var show = deps.show || function () {};
    var hide = deps.hide || function () {};
    var reloadFn = deps.reload || function () {};
    var post = deps.post || function () {};
    var intervalMs = typeof deps.intervalMs === "number" ? deps.intervalMs : DEFAULT_INTERVAL_MS;

    var seenPeers = {};      /* build → [ids that claimed it] */
    var shownBuild = null;   /* the build whose toast is live */
    var disabled = false;
    var timer = null;

    if (deps.onMessage) {
      deps.onMessage(function (message) {
        if (!message || typeof message !== "object") return;
        if (message.type === "reload") {
          reloadFn();
          return;
        }
        if (message.type === "seen" && message.id !== id) {
          var build = message.build;
          if (typeof build !== "string" || !build) return;
          if (!seenPeers[build]) seenPeers[build] = [];
          if (seenPeers[build].indexOf(message.id) === -1) seenPeers[build].push(message.id);
          /* Re-arbitrate a live toast: a smaller id taking over the
           * same build withdraws ours. */
          if (shownBuild === build && !keepToast(id, seenPeers[build])) {
            shownBuild = null;
            hide();
          }
        }
      });
    }

    function detect(build) {
      var peers = seenPeers[build] || [];
      post({ type: "seen", build: build, id: id });
      if (!keepToast(id, peers)) {
        shownBuild = null;
        return;
      }
      shownBuild = build;
      show(build, {
        onClick: function () { api.reload(); },
      });
    }

    function schedule() {
      if (disabled || !intervalMs || typeof global.setInterval !== "function") return;
      if (timer) global.clearInterval(timer);
      timer = global.setInterval(api.check, intervalMs);
    }

    var api = {
      id: id,
      /* Test/ops seam: a 404 /build stands the poller down for good. */
      isDisabled: function isDisabled() { return disabled; },
      check: function check() {
        if (disabled) return Promise.resolve(false);
        return fetchBuild().then(function (served) {
          /* The sentinel "absent" = a hard 404: this brain predates
           * GET /build — the poller stands down for good (doc 09 risk
           * 5: a graceful fallback, never a toast about nothing). */
          if (served === "absent") {
            disabled = true;
            return false;
          }
          if (served === undefined || served === null) {
            /* An offline poll or an unreadable body: nothing to
             * compare, nothing to say — the next tick retries. */
            return false;
          }
          var footer = footerBuild();
          if (newerBuild(served, footer)) detect(String(served));
          return true;
        }).catch(function () { return false; });
      },
      reload: function reload() {
        post({ type: "reload", id: id });
        reloadFn();
      },
      start: function start() {
        api.check().then(schedule).catch(schedule);
        return api;
      },
    };
    return api;
  }

  var api = {
    createUpdater: createUpdater,
    newerBuild: newerBuild,
    keepToast: keepToast,
    CHANNEL: CHANNEL,
    DEFAULT_INTERVAL_MS: DEFAULT_INTERVAL_MS,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.BrainUpdates = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
