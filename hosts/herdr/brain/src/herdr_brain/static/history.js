/* Reading-view state — PURE logic, no DOM.
 *
 * The cockpit's "Historial" and "Terminal" tabs (doc 05) read two
 * endpoints whose contract is owned by the server (history_view.py,
 * mirror.py). This module holds everything about those views that is
 * decidable without the DOM: the cursor-pagination state machine for
 * /history, the URL shapes of both reads and the shared state poll clock. app.js owns the fetching
 * (readFetch: bearer + pairing recovery), the DOM painting
 * (createElement/textContent only) and the visibility gating.
 *
 * Page contract (mirrored from history_view.py): every page arrives in
 * CHRONOLOGICAL order; the first page is the NEWEST window and older
 * pages walk towards the past through the opaque cursor, so an older
 * page is always PREPENDED under what is held. `cursor` travels only
 * while `has_more` is true, and an agent without a readable history is
 * an ordinary empty page with reason "no-session" — never an error.
 *
 * Duplicate guard: the cursor counts turns from the NEWEST, so a
 * transcript that GREW between the first-page fetch and an older-page
 * fetch shifts the window back and re-serves the boundary turns. The
 * overlap lands at the incoming page's tail (its newest end) against
 * the held list's head (its oldest end); trimBoundaryOverlap cuts
 * exactly that contiguous run, keeping multiplicity honest.
 */
(function (global) {
  "use strict";

  /* Page size the history view asks for: the server's default and the
   * initial render window of doc 05 (≤ 60 turns), one bounded page per
   * request. */
  var HISTORY_PAGE_LIMIT = 60;

  /* One page request descriptor, or null when the model will not issue
   * one. Kept opaque on purpose: callers only need the URL. */
  function request(url) {
    return { url: url };
  }

  /* Stable identity of a turn for the boundary trim. role+text+time is
   * the finest grain the pages carry; two turns equal on all three are
   * indistinguishable by contract. */
  function turnKey(turn) {
    return (turn && turn.role || "") + "\u0000" +
      (turn && turn.text || "") + "\u0000" +
      (turn && turn.created_at || "");
  }

  /* Cuts the contiguous boundary overlap between a held chronological
   * list and an incoming older page (see the header note). The overlap
   * is the largest k whose incoming TAIL (its newest k turns) equals
   * the held HEAD (its oldest k turns), element by element; everything
   * before it is genuinely new. Returns the turns of `incoming` to
   * prepend, in order. */
  function trimBoundaryOverlap(held, incoming) {
    var maxK = Math.min(held.length, incoming.length);
    for (var k = maxK; k > 0; k--) {
      var matches = true;
      for (var m = 0; m < k; m++) {
        if (turnKey(incoming[incoming.length - k + m]) !== turnKey(held[m])) {
          matches = false;
          break;
        }
      }
      if (matches) return incoming.slice(0, incoming.length - k);
    }
    return incoming.slice();
  }

  /* Match literal text per turn in chronological order. Unicode case
   * matching in the original string keeps the offsets valid even when
   * lowercasing preceding characters would change their length. */
  function findInTurns(turns, query) {
    query = query.trim();
    if (!query) return [];
    var literal = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "giu");
    var matches = [];
    turns.forEach(function (turn, turnIndex) {
      var hit;
      while ((hit = literal.exec(turn.text || "")) !== null) {
        matches.push({ turnIndex: turnIndex, start: hit.index, end: literal.lastIndex });
      }
    });
    return matches;
  }

  function createHistoryModel() {
    var paneId = null;
    var turns = [];
    var cursor = null;
    var hasMore = false;
    var loading = false;
    var loadedFirst = false;
    var reason = null;
    var sessionId = null;

    function state() {
      return {
        paneId: paneId, count: turns.length, cursor: cursor,
        hasMore: hasMore, loading: loading, reason: reason,
        sessionId: sessionId,
      };
    }

    /* First page of `id`: issues unless a request is in flight or this
     * pane's first page is already held. A different pane after a reset
     * (or a fail) starts fresh — the guard is the pane identity, never
     * a stale assumption about which agent the view shows. */
    function firstPage(id) {
      if (loading) return null;
      if (loadedFirst && paneId === id) return null;
      loading = true;
      paneId = id;
      return request(historyUrl(id, null, HISTORY_PAGE_LIMIT));
    }

    function applyFirstPage(payload) {
      payload = payload || {};
      turns = (payload.turns || []).slice();
      cursor = payload.cursor || null;
      hasMore = !!payload.has_more;
      reason = payload.reason || null;
      sessionId = payload.session_id || null;
      loading = false;
      loadedFirst = true;
      return { turns: turns.slice() };
    }

    /* Next older page: only while a cursor exists and nothing is in
     * flight. The URL carries the cursor exactly as the server minted
     * it (opaque token, never re-derived here). */
    function olderPage() {
      if (loading || !loadedFirst || !hasMore || !cursor) return null;
      loading = true;
      return request(historyUrl(paneId, cursor, HISTORY_PAGE_LIMIT));
    }

    function applyOlderPage(payload) {
      payload = payload || {};
      var incoming = (payload.turns || []).slice();
      var fresh = trimBoundaryOverlap(turns, incoming);
      turns = fresh.concat(turns);
      cursor = payload.cursor || null;
      hasMore = !!payload.has_more;
      loading = false;
      return { prepend: fresh };
    }

    /* A failed fetch keeps every turn already on screen; only the
     * in-flight flag clears, so the pager can be retried. */
    function fail() {
      loading = false;
    }

    /* Wipe (agent switch): nothing held, nothing in flight, nothing
     * loaded — the next firstPage() issues unconditionally. */
    function reset() {
      paneId = null;
      turns = [];
      cursor = null;
      hasMore = false;
      loading = false;
      loadedFirst = false;
      reason = null;
      sessionId = null;
    }

    return {
      state: state,
      firstPage: firstPage,
      applyFirstPage: applyFirstPage,
      olderPage: olderPage,
      applyOlderPage: applyOlderPage,
      find: function (query) { return findInTurns(turns, query); },
      turnAt: function (index) { return turns[index]; },
      fail: fail,
      reset: reset,
    };
  }

  function historyUrl(paneId, cursor, limit) {
    var parts = [];
    if (paneId != null) parts.push("pane_id=" + encodeURIComponent(paneId));
    if (cursor) parts.push("cursor=" + encodeURIComponent(cursor));
    if (limit) parts.push("limit=" + limit);
    return "/history" + (parts.length ? "?" + parts.join("&") : "");
  }

  /* The mirror reads the pane's visible window; naming no line count
   * takes the server's clamped default (mirror.py). No pane, no read. */
  function mirrorUrl(paneId) {
    if (paneId == null) return null;
    return "/pane/" + encodeURIComponent(paneId) + "/mirror";
  }

  /* One poll clock. A healthy stream allows a slower background safety
   * net; foreground attention expires unless a reader renews it.
   * Re-arms exactly like the reference engine's applyCadence: clear the
   * interval, arm the new one — one place owns the poll timer. */
  function createStatePoller(options) {
    var WATCHED = 1500, NORMAL = 5000, IDLE = 10000, ATTENTION_WINDOW = 10000;
    var timer = null, armedMs;
    var started = false, healthy = false, needsRecovery = false, hidden = false;
    var lastAttention = -Infinity;

    function cadence() {
      if (!healthy) return NORMAL;
      if (hidden) return IDLE;
      return options.now() - lastAttention <= ATTENTION_WINDOW ? WATCHED : NORMAL;
    }

    /* Swap the interval to `ms` if it differs. The one place the poll
     * timer is re-armed; the armed interval keeps its phase otherwise. */
    function setCadence() {
      if (!started) return;
      var ms = cadence();
      if (timer !== null && ms === armedMs) return;
      if (timer !== null) options.clearIntervalFn(timer);
      armedMs = ms;
      timer = options.setIntervalFn(function () {
        // Attention expires without a DOM event; the expiry costs only
        // this tick, not an extra hot read.
        if (cadence() !== armedMs) { setCadence(); return; }
        try { options.refresh(); } finally { setCadence(); }
      }, ms);
    }

    return {
      start: function () { started = true; setCadence(); },
      noteAttention: function () { lastAttention = options.now(); setCadence(); },
      setHidden: function (value) { hidden = value; setCadence(); },
      setStreamHealth: function (value) {
        if (!value) needsRecovery = true;
        if (healthy === value) return;
        healthy = value;
        var reconcile = started && healthy && needsRecovery;
        if (healthy) needsRecovery = false;
        // Native EventSource reopens the stream. Read once after its
        // recovery to catch state that changed while we weren't listening.
        try { if (reconcile) options.refresh(); } finally { setCadence(); }
      },
    };
  }

  var api = {
    createHistoryModel: createHistoryModel,
    createStatePoller: createStatePoller,
    trimBoundaryOverlap: trimBoundaryOverlap,
    turnKey: turnKey,
    historyUrl: historyUrl,
    mirrorUrl: mirrorUrl,
    HISTORY_PAGE_LIMIT: HISTORY_PAGE_LIMIT,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.HistoryView = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
