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

    function validSentenceIndex(sentIdx) {
      return /^(0|[1-9]\d*)$/.test(String(sentIdx)) && Number.isSafeInteger(Number(sentIdx));
    }

    /* The synthesis string, its sentence ranges and its DOM origins are
     * produced together. Each text leaf is visited once, even inside
     * nested/continuation anchors; repeated sentences never require
     * searching the resulting string. sources[i] is the text node and
     * UTF-16 offset that produced rawText[i], or null for synthetic
     * separator/collapsed-whitespace characters (they own no leaf). */
    function readableReplay(container, sentIdx) {
      var empty = Object.freeze({ rawText: "", sentences: Object.freeze([]), sources: Object.freeze([]) });
      if (!container || (sentIdx !== undefined && !validSentenceIndex(sentIdx))) return empty;
      var hits = sentIdx === undefined ? [] : container.querySelectorAll(
        '[data-sent-idx="' + String(sentIdx) + '"]');
      var boundary = null;
      for (var i = 0; i < hits.length; i++) {
        if (hits[i].classList.contains("tts-sent")) { boundary = hits[i]; break; }
      }
      if (sentIdx !== undefined && !boundary) return empty;
      var started = sentIdx === undefined;
      var sentence = null;
      var parts = [];
      var sources = [];
      var sentences = [];
      var pendingSpace = "";
      var previousSentence = null;

      function append(char, index, origin) {
        var offset = parts.length; // charAt preserves the plan's UTF-16 offsets.
        var last = sentences[sentences.length - 1];
        if (index !== null) {
          if (last && last.sentIdx === index && last.raw[1] === offset) last.raw[1]++;
          else sentences.push({ sentIdx: index, raw: [offset, offset + 1] });
        }
        parts.push(char);
        sources.push(origin || null);
        previousSentence = index;
      }

      function text(value, node) {
        for (var k = 0; k < value.length; k++) {
          var char = value.charAt(k);
          if (/\s/.test(char)) {
            if (char === "\n") pendingSpace = "\n";
            else if (!pendingSpace) pendingSpace = " ";
          } else {
            // Trim empty lines/edges and collapse inline whitespace exactly
            // once. Separators and unanchored gaps remain with the last sentence.
            if (parts.length && pendingSpace) append(pendingSpace, previousSentence, null);
            pendingSpace = "";
            append(char, sentence, node ? { node: node, off: k } : null);
          }
        }
      }

      function visit(node) {
        if (node.nodeType === 3) {
          if (started) text(node.nodeValue, node);
          return;
        }
        if (node.nodeType !== 1) return;
        var tag = node.tagName.toLowerCase();
        if (/^(script|style|noscript|button|input|select|textarea|svg|audio|video)$/.test(tag) ||
            node.getAttribute("hidden") !== null || node.getAttribute("aria-hidden") === "true") return;
        if (node === boundary) started = true;
        var index = node.getAttribute("data-sent-idx");
        if (started && validSentenceIndex(index) &&
            (node.classList.contains("tts-sent") || node.classList.contains("tts-sent-cont"))) {
          sentence = Number(index);
        }
        var block = /^(p|div|section|article|h[1-6]|ul|ol|li|blockquote|pre|table|tr|hr)$/.test(tag);
        var separator = block || tag === "br" ? "\n" : /^(td|th)$/.test(tag) ? " " : "";
        if (started && separator) text(separator, null);
        // Visit text leaves once: nested anchors must not duplicate textContent.
        for (var j = 0; j < node.childNodes.length; j++) visit(node.childNodes[j]);
        if (started && separator) text(separator, null);
      }
      visit(container);
      return Object.freeze({ rawText: parts.join(""), sentences: Object.freeze(sentences.map(function (entry) {
        return Object.freeze({ sentIdx: entry.sentIdx, raw: Object.freeze(entry.raw) });
      })), sources: Object.freeze(sources.map(function (origin) {
        return origin ? Object.freeze(origin) : null;
      })) });
    }

    function readableSuffix(container, sentIdx) {
      if (!validSentenceIndex(sentIdx)) return ""; // Undefined is never whole-answer intent here.
      return readableReplay(container, sentIdx).rawText;
    }

    function sentenceForChunk(replay, chunk) {
      if (!replay || !chunk || !chunk.raw) return null;
      for (var i = 0; i < replay.sentences.length; i++) {
        var entry = replay.sentences[i];
        // Use the first overlap of this already-timed popup window, not a new
        // duration split when a window covers several sentences or a gap.
        if (entry.raw[0] < chunk.raw[1] && entry.raw[1] > chunk.raw[0]) return entry.sentIdx;
      }
      return null;
    }

    /* Map a raw interval [start, end) to the DOM text-leaf runs that produced
     * its visible characters: one { node, start, end, raw } per contiguous
     * node portion, in traversal order. Synthetic separators and collapsed
     * whitespace own no leaf and are skipped; a gap INSIDE one text node can
     * only be DOM whitespace (non-whitespace always appends a real char), so
     * the run extends across it without painting any new visible text. A gap
     * BETWEEN nodes breaks the run — block separators paint nothing. */
    function rawRanges(replay, start, end) {
      var runs = [];
      if (!replay || !replay.sources) return runs;
      var from = Math.max(0, Math.floor(Number(start) || 0));
      var to = Math.min(replay.sources.length, Math.ceil(Number(end) || 0));
      var run = null;
      for (var i = from; i < to; i++) {
        var origin = replay.sources[i];
        if (!origin) continue;
        if (run && run.node === origin.node && origin.off >= run.start) {
          // Same node, at or after the run start: merge. The extraction walks
          // leaves in order, so offsets never move backward inside a node.
          run.end = origin.off + 1;
          run.raw[1] = i + 1;
        } else {
          if (run) runs.push(run);
          run = { node: origin.node, start: origin.off, end: origin.off + 1, raw: [i, i + 1] };
        }
      }
      if (run) runs.push(run);
      return runs;
    }

    /* Ancestor walk over parentNode: true only for nodes that currently
     * live inside this container. Native-faithful in real and fake DOM
     * alike, and it also rejects detached nodes (their chain never
     * reaches the container). */
    function ownedByContainer(container, node) {
      for (var cur = node; cur; cur = cur.parentNode) {
        if (cur === container) return true;
      }
      return false;
    }

    /* Owned leaf marks: the only DOM mutation is wrapping text-node portions
     * in <span class="tts-selected tts-range" data-tts-range="a:b"> — no HTML
     * replacement, no flattening, links/selection/copy stay native. The raw
     * bounds in the attribute make repainting the SAME window a no-op, so
     * repeated progress events and observer-driven syncs cannot loop. */
    function paintRawRange(container, replay, start, end) {
      if (!container || !replay || !replay.sources) return 0;
      var runs = rawRanges(replay, start, end);
      var marks = container.querySelectorAll(".tts-range");
      if (marks.length === runs.length) {
        var same = true;
        for (var i = 0; i < runs.length; i++) {
          if (marks[i].getAttribute("data-tts-range") !== runs[i].raw[0] + ":" + runs[i].raw[1] ||
              marks[i].childNodes.length !== 1 || marks[i].childNodes[0].nodeType !== 3) {
            same = false;
            break;
          }
        }
        if (same) return runs.length;   // already painted exactly this window
      }
      clearPaintedRanges(container, false);   // keep pieces attached: the
      // pre-computed runs reference them. Repaint fragmentation is bounded
      // to the current window; the healed teardown path below merges it back.
      var painted = 0;
      for (var j = 0; j < runs.length; j++) {
        var run = runs[j];
        var node = run.node;
        // Containment is validated BEFORE any split/wrap: a replay extracted
        // from another (attached) response must paint nothing anywhere, and
        // stale or partially foreign sources must never touch their leaves.
        if (!node || node.nodeType !== 3 ||
            !ownedByContainer(container, node) ||
            node.nodeValue === null || node.nodeValue.length < run.end) {
          continue;   // foreign, stale or detached: never paint blind
        }
        try {
          if (run.end < node.nodeValue.length) node.splitText(run.end);
          var mid = run.start > 0 ? node.splitText(run.start) : node;
          var mark = doc.createElement("span");
          mark.classList.add("tts-selected", "tts-range");   // existing green CSS + ownership
          mark.setAttribute("data-tts-range", run.raw[0] + ":" + run.raw[1]);
          node.parentNode.replaceChild(mark, mid);
          mark.appendChild(mid);
          painted++;
        } catch (err) {
          /* Hostile surface: skip this run, keep the rest of the window. */
        }
      }
      return painted;
    }

    /* Remove ONLY owned range marks (anchor-level .tts-selected selections
     * from selectSentence are class-only and stay). Unwrapping re-inserts the
     * exact text nodes; the healed teardown (heal !== false) normalizes so
     * the rendered HTML is byte-identical again. The repaint path skips the
     * heal on purpose: normalize would merge away the split leaves the next
     * runs still reference. */
    function clearPaintedRanges(container, heal) {
      if (!container) return 0;
      var marks = container.querySelectorAll(".tts-range");
      for (var i = 0; i < marks.length; i++) {
        var mark = marks[i];
        var parent = mark.parentNode;
        if (!parent) continue;
        while (mark.childNodes.length) parent.insertBefore(mark.childNodes[0], mark);
        parent.removeChild(mark);
        if (heal !== false && parent.normalize) parent.normalize();
      }
      return marks.length;
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
      readableReplay: readableReplay,
      sentenceForChunk: sentenceForChunk,
      rawRanges: rawRanges,
      paintRawRange: paintRawRange,
      clearPaintedRanges: clearPaintedRanges,
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
