/* Formatted conversation reader (reader-html-integration).
 *
 * Pure UMD module following the approval.js house pattern: every DOM
 * touch goes through the injected surface ({ doc, http }) — this file
 * references no browser global at all, so the Node suite drives it
 * against a plain fake DOM (Decision 1).
 *
 * Safety contract:
 *  - innerHTML is assigned ONLY on .reader-content containers and ONLY
 *    with server-rendered reader payloads; raw transcript text always
 *    renders through textContent.
 *  - After insertion, script tags and inline handlers are neutralized
 *    and links are hardened (http/https only, target=_blank,
 *    rel=noopener noreferrer); other schemes degrade to plain text.
 *  - Every failure path (null html, insertion throw, hostile payload)
 *    falls back to strict textContent — a turn is never left empty.
 *  - Snapshot discipline: the generation counter lives HERE; responses
 *    resolved after an identity change are discarded, never merged, and
 *    the snapshot is replaced as a unit keyed by turn_id.
 *  - reader-always-formatted: loadRendered() is the load/refresh-time
 *    mount path — every turn the surface reports gets mounted formatted
 *    regardless of any expansion/truncation state; html-less turns keep
 *    their text until a later response upgrades them (progressive fill).
 */

(function (global) {
  /* Common inline event handlers stripped after insertion. Upstream
   * escapes all of these; this is defense in depth at our own boundary,
   * restricted to the attribute API of the injected surface. */
  var HANDLER_ATTRS = [
    "onload", "onerror", "onclick", "ondblclick", "onmousedown", "onmouseup",
    "onmousemove", "onmouseover", "onmouseenter", "onmouseleave", "onmouseout",
    "onkeydown", "onkeyup", "onkeypress", "onfocus", "onblur", "oninput",
    "onchange", "onsubmit", "onreset", "onselect", "onwheel", "onscroll",
    "oncontextmenu", "ondragstart", "ondragover", "ondrop", "onpointerdown",
    "onpointerup", "onpointermove", "onpointerover", "onpointerout",
    "onpointerenter", "onpointerleave", "ontouchstart", "ontouchend",
    "ontouchmove", "onanimationstart", "onanimationend", "ontransitionend",
    "onplay", "onpause", "onended"
  ];

  function createReader(deps) {
    var doc = deps && deps.doc;
    var http = deps && deps.http;
    var generation = 0;
    var curPane = null;
    var curSession = null;
    var snapshot = null;   /* { byId: {turn_id: turn}, order: [turn, ...] } */

    function syncIdentity(pane, session) {
      if (pane === curPane && session === curSession) return false;
      curPane = pane;
      curSession = session;
      generation += 1;     /* every in-flight response is now stale */
      snapshot = null;     /* reader state is replaced as a unit */
      return true;
    }

    function neutralize(container) {
      var scripts = container.querySelectorAll("script");
      for (var i = 0; i < scripts.length; i++) {
        var s = scripts[i];
        if (s.parentNode) s.parentNode.removeChild(s);
      }
      var all = container.querySelectorAll("*");
      for (var j = 0; j < all.length; j++) {
        var el = all[j];
        for (var k = 0; k < HANDLER_ATTRS.length; k++) {
          if (el.getAttribute(HANDLER_ATTRS[k]) !== null) {
            el.removeAttribute(HANDLER_ATTRS[k]);
          }
        }
      }
    }

    function hardenLinks(container) {
      var anchors = container.querySelectorAll("a");
      for (var i = 0; i < anchors.length; i++) {
        var a = anchors[i];
        var href = a.getAttribute("href") || "";
        if (/^https?:/i.test(href)) {
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener noreferrer");
        } else if (a.parentNode) {
          /* Non-HTTP scheme: the content survives as plain text, never
           * as an anchor. */
          a.parentNode.replaceChild(doc.createTextNode(a.textContent), a);
        }
      }
    }

    function mountTurn(container, turn) {
      var source = turn || {};
      var text = source.text == null ? "" : String(source.text);
      var html = typeof source.html === "string" ? source.html : null;
      try {
        if (html && container.classList.contains("reader-content")) {
          container.innerHTML = html;   /* the ONLY innerHTML assignment */
          neutralize(container);
          hardenLinks(container);
          if (!container.textContent) container.textContent = text;
          return true;
        }
      } catch (err) {
        /* Insertion failure: strict text fallback below. */
      }
      container.textContent = text;
      return false;
    }

    function selectSentence(container, sentIdx) {
      /* Scoped to the owning turn's container — NEVER a global id lookup:
       * upstream anchor ids restart per rendered turn. One query matches
       * the primary .tts-sent, every .tts-sent-cont continuation and
       * container-borne anchors alike; alignment mode is irrelevant. */
      var prev = container.querySelectorAll(".tts-selected");
      for (var i = 0; i < prev.length; i++) prev[i].classList.remove("tts-selected");
      var hits = container.querySelectorAll(
        '[data-sent-idx="' + String(sentIdx) + '"]'
      );
      for (var j = 0; j < hits.length; j++) hits[j].classList.add("tts-selected");
      return hits.length;   /* 0 is legal: empty virtual span */
    }

    function readableSuffix(container, sentIdx) {
      if (!container || !/^(0|[1-9]\d*)$/.test(String(sentIdx)) ||
          !Number.isSafeInteger(Number(sentIdx))) return "";
      var hits = container.querySelectorAll('[data-sent-idx="' + String(sentIdx) + '"]');
      var boundary = null;
      for (var i = 0; i < hits.length; i++) {
        if (hits[i].classList.contains("tts-sent")) { boundary = hits[i]; break; }
      }
      if (!boundary) return ""; // An orphan continuation is not a whole-answer fallback.
      var started = false;
      var parts = [];
      function visit(node) {
        if (node.nodeType === 3) {
          if (started) parts.push(node.nodeValue);
          return;
        }
        if (node.nodeType !== 1) return;
        var tag = node.tagName.toLowerCase();
        if (/^(script|style|noscript|button|input|select|textarea|svg|audio|video)$/.test(tag) ||
            node.getAttribute("hidden") !== null || node.getAttribute("aria-hidden") === "true") return;
        if (node === boundary) started = true;
        var block = /^(p|div|section|article|h[1-6]|ul|ol|li|blockquote|pre|table|tr|hr)$/.test(tag);
        var separator = block || tag === "br" ? "\n" : /^(td|th)$/.test(tag) ? " " : "";
        if (started && separator) parts.push(separator);
        // Visit text leaves once: nested anchors must not duplicate textContent.
        for (var j = 0; j < node.childNodes.length; j++) visit(node.childNodes[j]);
        if (started && separator) parts.push(separator);
      }
      visit(container);
      return parts.join("").replace(/[^\S\n]+/g, " ").split("\n")
        .map(function (line) { return line.trim(); }).filter(Boolean).join("\n");
    }

    function requestSnapshot(pane, session) {
      syncIdentity(pane, session);
      var token = generation;   /* captured at REQUEST time */
      return http("/conversation/" + encodeURIComponent(String(pane)) + "/rendered")
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          if (token !== generation) return;   /* THE single discard point */
          var turns = (data && data.turns) || [];
          var byId = {};
          var byKey = {};   /* role \u0000 text -> turn: host re-application key */
          for (var i = 0; i < turns.length; i++) {
            byId[turns[i].turn_id] = turns[i];
            byKey[turns[i].role + "\u0000" + turns[i].text] = turns[i];
          }
          snapshot = { byId: byId, byKey: byKey, order: turns.slice() };  /* wholesale */
          return turns;
        })
        .catch(function () {
          return undefined;   /* text stays; never empty, never an error out */
        });
    }

    function htmlFor(role, text) {
      /* Held-snapshot lookup by the host's content key (role \u0000 text —
       * the same key renderGlance reconciles on). Null until the rendered
       * payload for the CURRENT identity arrives: text stays until then. */
      if (!snapshot) return null;
      var turn = snapshot.byKey[role + "\u0000" + text];
      return turn && typeof turn.html === "string" ? turn.html : null;
    }

    function loadRendered(pane, session, getPairs) {
      /* reader-always-formatted load-time mount path: fetch the snapshot
       * for the viewed pane (generation-guarded, replaced as a unit by
       * turn_id — both live in requestSnapshot) and mount every turn the
       * surface reports RIGHT NOW. Pairs are read AFTER the response
       * resolves, so a surface rebuilt mid-flight is honored; a stale
       * response mounts nothing. Expansion/truncation state on the
       * pairs is deliberately ignored — "ver más" never gates rendering. */
      return requestSnapshot(pane, session).then(function (turns) {
        if (!turns) return turns;   /* stale (discarded) or failed: text stays */
        if (typeof getPairs === "function") {
          var pairs = getPairs() || [];
          for (var i = 0; i < pairs.length; i++) {
            var pair = pairs[i] || {};
            var turn = pair.turn || {};
            mountTurn(pair.container, {
              text: turn.text,
              html: htmlFor(turn.role, turn.text)
            });
          }
        }
        return turns;
      });
    }

    return {
      syncIdentity: syncIdentity,
      requestSnapshot: requestSnapshot,
      loadRendered: loadRendered,
      mountTurn: mountTurn,
      htmlFor: htmlFor,
      selectSentence: selectSentence,
      readableSuffix: readableSuffix,
      currentGeneration: function () { return generation; }
    };
  }

  var api = { createReader: createReader };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Reader = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
