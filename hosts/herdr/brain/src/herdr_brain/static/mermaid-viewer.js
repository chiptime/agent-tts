/* Formatted mermaid diagram viewer (mermaid-viewer).
 *
 * Pure UMD module following the reader.js and toast.js house pattern:
 * every DOM touch goes through the injected surface ({ doc, mermaid, root }) —
 * this file references no browser global directly so the Node suite
 * can drive it against a plain fake DOM.
 *
 * Capabilities:
 *  - Automatically scans reader / transcript containers for <code class="language-mermaid">.
 *  - Transforms the raw code block into an interactive .mermaid-card with:
 *      * Header toolbar with badge, [Código] toggle, [Copiar] button, and [⛶ Visor] expand button.
 *      * Rendered Mermaid SVG diagram.
 *      * Fail-soft syntax error fallback: invalid diagrams display a notice and retain the code block.
 *  - Fullscreen / Lightbox Modal Viewer ("Visor de gráficos"):
 *      * Large canvas overlay for inspecting diagrams on desktop and mobile.
 *      * Pan & drag (mouse and touch).
 *      * Zoom controls (+, -, reset) and wheel / pinch zoom with percentage display.
 *      * Keyboard (Esc) and backdrop dismissal.
 *  - Explicit theme support (dark/light) matching herdr-brain's palette:
 *      * Automatically synchronizes with data-theme="dark"|"light".
 *      * Re-renders diagrams dynamically when the theme changes.
 */

(function (global) {
  "use strict";

  function createMermaidViewer(deps) {
    var doc = (deps && deps.doc) || (typeof document !== "undefined" ? document : null);
    var mermaid = (deps && deps.mermaid) || (global && global.mermaid) || null;
    var root = (deps && deps.root) || (doc && doc.body) || null;

    var currentTheme = (deps && deps.theme) || "dark";
    var cards = [];
    var seqId = 0;

    /* Modal viewer state */
    var modalEl = null;
    var backdropEl = null;
    var viewportEl = null;
    var canvasEl = null;
    var zoomDisplayEl = null;
    var currentZoom = 1.0;
    var panX = 0;
    var panY = 0;
    var currentBaseWidth = 400;
    var currentBaseHeight = 300;
    var isDragging = false;
    var startDragX = 0;
    var startDragY = 0;
    var currentRawCode = "";
    var currentSvgHtml = "";

    function getTheme() {
      if (currentTheme) return currentTheme;
      if (doc && doc.documentElement && doc.documentElement.getAttribute) {
        var t = doc.documentElement.getAttribute("data-theme");
        if (t === "light" || t === "dark") return t;
      }
      return "dark";
    }

    function initMermaid(themeName) {
      if (!mermaid || !mermaid.initialize) return;
      var isDark = (themeName || getTheme()) === "dark";
      try {
        mermaid.initialize({
          startOnLoad: false,
          suppressErrorRendering: true,
          securityLevel: "loose",
          theme: isDark ? "dark" : "default",
          themeVariables: isDark ? {
            darkMode: true,
            background: "#0a0a0a",
            primaryColor: "#1d5fbf",
            lineColor: "#7fd88f",
            textColor: "#eeeeee",
            mainBkg: "#141414"
          } : {
            darkMode: false,
            background: "#ffffff",
            primaryColor: "#12855c",
            lineColor: "#12855c",
            textColor: "#111111",
            mainBkg: "#f4f6f9"
          }
        });
      } catch (err) {
        /* fail-soft if initialize throws */
      }
    }

    /* Initialize with active theme */
    initMermaid(getTheme());

    /* Find ancestor <pre> element without relying on Element.prototype.closest */
    function findPreAncestor(el) {
      var cur = el;
      while (cur) {
        if (cur.tagName && cur.tagName.toLowerCase() === "pre") return cur;
        cur = cur.parentNode;
      }
      return null;
    }

    /* Find ancestor with class without relying on Element.prototype.closest */
    function findClassAncestor(el, className) {
      var cur = el;
      while (cur) {
        if (cur.classList && cur.classList.contains(className)) return cur;
        cur = cur.parentNode;
      }
      return null;
    }

    function applyCanvasTransform() {
      if (!canvasEl) return;
      var svgEl = canvasEl.querySelector ? canvasEl.querySelector("svg") : null;
      if (svgEl && svgEl.style && currentBaseWidth > 0) {
        svgEl.style.maxWidth = "none";
        svgEl.style.width = Math.round(currentBaseWidth * currentZoom) + "px";
        svgEl.style.height = Math.round(currentBaseHeight * currentZoom) + "px";
      }
      var transform = "translate(" + Math.round(panX) + "px, " + Math.round(panY) + "px)";
      if (canvasEl.style) {
        canvasEl.style.transform = transform;
      }
      if (zoomDisplayEl) {
        zoomDisplayEl.textContent = Math.round(currentZoom * 100) + "%";
      }
    }

    function setZoom(level, focalX, focalY) {
      var oldZoom = currentZoom;
      currentZoom = Math.max(0.2, Math.min(10.0, level));
      if (focalX !== undefined && focalY !== undefined && oldZoom > 0) {
        var ratio = currentZoom / oldZoom;
        panX = focalX - (focalX - panX) * ratio;
        panY = focalY - (focalY - panY) * ratio;
      }
      applyCanvasTransform();
    }

    function resetZoom() {
      currentZoom = 1.0;
      panX = 0;
      panY = 0;
      applyCanvasTransform();
    }

    function copyCode(code, btn) {
      if (!code) return;
      var done = function () {
        if (!btn) return;
        var orig = btn.textContent;
        btn.textContent = "¡Copiado!";
        setTimeout(function () {
          btn.textContent = orig;
        }, 1500);
      };

      if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
        global.navigator.clipboard.writeText(code).then(done).catch(function () {
          fallbackCopy(code, done);
        });
      } else {
        fallbackCopy(code, done);
      }
    }

    function fallbackCopy(text, cb) {
      if (!doc || !doc.createElement) return;
      try {
        var ta = doc.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "absolute";
        ta.style.left = "-9999px";
        doc.body.appendChild(ta);
        ta.select();
        doc.execCommand("copy");
        doc.body.removeChild(ta);
        if (cb) cb();
      } catch (e) {
        /* fail-soft */
      }
    }

    function ensureModal() {
      if (modalEl) return modalEl;
      if (!doc) return null;

      modalEl = doc.getElementById ? doc.getElementById("mermaid-modal") : null;
      backdropEl = doc.getElementById ? doc.getElementById("mermaid-backdrop") : null;
      if (modalEl) {
        viewportEl = doc.getElementById("mm-viewport");
        canvasEl = doc.getElementById("mm-canvas");
        zoomDisplayEl = doc.getElementById("mm-zoom-display");
        wireModalEvents();
        return modalEl;
      }

      /* Dynamically create if not already in document */
      backdropEl = doc.createElement("div");
      backdropEl.className = "mermaid-backdrop hidden";
      backdropEl.setAttribute("aria-hidden", "true");

      modalEl = doc.createElement("section");
      modalEl.className = "mermaid-modal hidden";
      modalEl.setAttribute("role", "dialog");
      modalEl.setAttribute("aria-modal", "true");
      modalEl.setAttribute("aria-label", "Visor de diagrama Mermaid");

      var head = doc.createElement("div");
      head.className = "mm-head";

      var titleWrap = doc.createElement("div");
      titleWrap.className = "mm-title-wrap";
      var title = doc.createElement("span");
      title.className = "mm-title";
      title.textContent = "Diagrama Mermaid";
      zoomDisplayEl = doc.createElement("span");
      zoomDisplayEl.className = "mm-zoom-level";
      zoomDisplayEl.textContent = "100%";
      titleWrap.appendChild(title);
      titleWrap.appendChild(zoomDisplayEl);

      var actions = doc.createElement("div");
      actions.className = "mm-actions";

      var btnIn = doc.createElement("button");
      btnIn.type = "button";
      btnIn.className = "mm-btn";
      btnIn.id = "mm-zoom-in";
      btnIn.textContent = "＋";
      btnIn.title = "Acercar (Zoom +)";

      var btnOut = doc.createElement("button");
      btnOut.type = "button";
      btnOut.className = "mm-btn";
      btnOut.id = "mm-zoom-out";
      btnOut.textContent = "－";
      btnOut.title = "Alejar (Zoom -)";

      var btnReset = doc.createElement("button");
      btnReset.type = "button";
      btnReset.className = "mm-btn";
      btnReset.id = "mm-zoom-reset";
      btnReset.textContent = "↺";
      btnReset.title = "Restablecer zoom";

      var btnCopy = doc.createElement("button");
      btnCopy.type = "button";
      btnCopy.className = "mm-btn";
      btnCopy.id = "mm-copy-code";
      btnCopy.textContent = "Copiar";
      btnCopy.title = "Copiar código fuente";

      var btnClose = doc.createElement("button");
      btnClose.type = "button";
      btnClose.className = "mm-btn mm-close-btn";
      btnClose.id = "mm-close";
      btnClose.textContent = "✕";
      btnClose.title = "Cerrar visor";

      actions.appendChild(btnIn);
      actions.appendChild(btnOut);
      actions.appendChild(btnReset);
      actions.appendChild(btnCopy);
      actions.appendChild(btnClose);

      head.appendChild(titleWrap);
      head.appendChild(actions);

      viewportEl = doc.createElement("div");
      viewportEl.className = "mm-viewport";
      viewportEl.id = "mm-viewport";

      canvasEl = doc.createElement("div");
      canvasEl.className = "mm-canvas";
      canvasEl.id = "mm-canvas";
      viewportEl.appendChild(canvasEl);

      var footer = doc.createElement("div");
      footer.className = "mm-footer";
      var hint = doc.createElement("span");
      hint.className = "mm-hint";
      hint.textContent = "Arrastra para mover · Rueda o botones para zoom";
      footer.appendChild(hint);

      modalEl.appendChild(head);
      modalEl.appendChild(viewportEl);
      modalEl.appendChild(footer);

      if (root) {
        root.appendChild(backdropEl);
        root.appendChild(modalEl);
      }

      wireModalEvents();
      return modalEl;
    }

    function wireModalEvents() {
      if (!modalEl) return;
      var btnClose = modalEl.querySelector ? modalEl.querySelector("#mm-close") : null;
      var btnIn = modalEl.querySelector ? modalEl.querySelector("#mm-zoom-in") : null;
      var btnOut = modalEl.querySelector ? modalEl.querySelector("#mm-zoom-out") : null;
      var btnReset = modalEl.querySelector ? modalEl.querySelector("#mm-zoom-reset") : null;
      var btnCopy = modalEl.querySelector ? modalEl.querySelector("#mm-copy-code") : null;

      if (btnClose && btnClose.addEventListener) {
        btnClose.addEventListener("click", closeModal);
      }
      if (backdropEl && backdropEl.addEventListener) {
        backdropEl.addEventListener("click", closeModal);
      }
      if (btnIn && btnIn.addEventListener) {
        btnIn.addEventListener("click", function () {
          setZoom(currentZoom + 0.25);
        });
      }
      if (btnOut && btnOut.addEventListener) {
        btnOut.addEventListener("click", function () {
          setZoom(currentZoom - 0.25);
        });
      }
      if (btnReset && btnReset.addEventListener) {
        btnReset.addEventListener("click", resetZoom);
      }
      if (btnCopy && btnCopy.addEventListener) {
        btnCopy.addEventListener("click", function () {
          copyCode(currentRawCode, btnCopy);
        });
      }

      /* Viewport mouse drag */
      if (viewportEl && viewportEl.addEventListener) {
        viewportEl.addEventListener("mousedown", function (e) {
          if (e.button !== 0) return;
          isDragging = true;
          startDragX = e.clientX - panX;
          startDragY = e.clientY - panY;
          if (viewportEl.classList) viewportEl.classList.add("grabbing");
        });

        if (global.addEventListener) {
          global.addEventListener("mousemove", function (e) {
            if (!isDragging) return;
            panX = e.clientX - startDragX;
            panY = e.clientY - startDragY;
            applyCanvasTransform();
          });
          global.addEventListener("mouseup", function () {
            if (!isDragging) return;
            isDragging = false;
            if (viewportEl && viewportEl.classList) viewportEl.classList.remove("grabbing");
          });
        }

        /* Mouse wheel zoom with focal point */
        viewportEl.addEventListener("wheel", function (e) {
          if (e.preventDefault) e.preventDefault();
          var rect = viewportEl.getBoundingClientRect ? viewportEl.getBoundingClientRect() : { left: 0, top: 0, width: 800, height: 600 };
          var focalX = e.clientX ? (e.clientX - rect.left - rect.width / 2) : 0;
          var focalY = e.clientY ? (e.clientY - rect.top - rect.height / 2) : 0;
          var delta = e.deltaY < 0 ? 0.2 : -0.2;
          setZoom(currentZoom + delta, focalX, focalY);
        }, { passive: false });

        /* Touch drag and pinch */
        var touchDist = 0;
        viewportEl.addEventListener("touchstart", function (e) {
          if (e.touches && e.touches.length === 1) {
            isDragging = true;
            startDragX = e.touches[0].clientX - panX;
            startDragY = e.touches[0].clientY - panY;
          } else if (e.touches && e.touches.length === 2) {
            isDragging = false;
            touchDist = Math.hypot(
              e.touches[0].clientX - e.touches[1].clientX,
              e.touches[0].clientY - e.touches[1].clientY
            );
          }
        }, { passive: true });

        viewportEl.addEventListener("touchmove", function (e) {
          if (isDragging && e.touches && e.touches.length === 1) {
            panX = e.touches[0].clientX - startDragX;
            panY = e.touches[0].clientY - startDragY;
            applyCanvasTransform();
          } else if (e.touches && e.touches.length === 2) {
            var newDist = Math.hypot(
              e.touches[0].clientX - e.touches[1].clientX,
              e.touches[0].clientY - e.touches[1].clientY
            );
            if (touchDist > 0) {
              var factor = newDist / touchDist;
              setZoom(currentZoom * factor);
            }
            touchDist = newDist;
          }
        }, { passive: true });

        viewportEl.addEventListener("touchend", function () {
          isDragging = false;
          touchDist = 0;
        }, { passive: true });
      }

      /* Keyboard Escape */
      if (global.addEventListener) {
        global.addEventListener("keydown", function (e) {
          if (e.key === "Escape" && modalEl && modalEl.classList && !modalEl.classList.contains("hidden")) {
            closeModal();
          }
        });
      }
    }

    function openModal(svgHtml, rawCode) {
      ensureModal();
      currentRawCode = rawCode || "";
      currentSvgHtml = svgHtml || "";

      if (canvasEl) {
        canvasEl.innerHTML = currentSvgHtml;
        var svgEl = canvasEl.querySelector ? canvasEl.querySelector("svg") : null;
        if (svgEl) {
          if (svgEl.style) svgEl.style.maxWidth = "none";
          if (svgEl.removeAttribute) svgEl.removeAttribute("height");
          var rawVb = (svgEl.getAttribute && svgEl.getAttribute("viewBox")) || "";
          var vb = rawVb.trim().split(/\s+/).map(Number);
          var vbW = (vb && vb.length >= 4 && vb[2]) ? vb[2] : (svgEl.clientWidth || 400);
          var vbH = (vb && vb.length >= 4 && vb[3]) ? vb[3] : (svgEl.clientHeight || 300);
          var vpW = (viewportEl && (viewportEl.clientWidth || (viewportEl.getBoundingClientRect && viewportEl.getBoundingClientRect().width))) || 800;
          var vpH = (viewportEl && (viewportEl.clientHeight || (viewportEl.getBoundingClientRect && viewportEl.getBoundingClientRect().height))) || 600;
          var pad = 48;
          var availW = Math.max(160, vpW - pad * 2);
          var availH = Math.max(160, vpH - pad * 2);
          var scale = Math.min(availW / vbW, availH / vbH);
          var fitScale = Math.max(scale, 1.0);
          currentBaseWidth = Math.round(vbW * fitScale);
          currentBaseHeight = Math.round(vbH * fitScale);
        }
      }
      resetZoom();

      if (modalEl && modalEl.classList) {
        modalEl.classList.remove("hidden");
        modalEl.removeAttribute("inert");
      }
      if (backdropEl && backdropEl.classList) {
        backdropEl.classList.remove("hidden");
      }
    }

    function closeModal() {
      if (modalEl && modalEl.classList) {
        modalEl.classList.add("hidden");
        modalEl.setAttribute("inert", "");
      }
      if (backdropEl && backdropEl.classList) {
        backdropEl.classList.add("hidden");
      }
    }

    /* Asynchronously render a card's diagram */
    function renderCard(card) {
      if (!card || !card.rawCode) return Promise.resolve(null);
      seqId += 1;
      var renderId = "mermaid-diag-" + seqId + "-" + Math.random().toString(36).slice(2, 6);

      if (!mermaid || !mermaid.render) {
        /* Mermaid library missing: fail-soft, keep code visible */
        if (card.codeWrap && card.codeWrap.classList) {
          card.codeWrap.classList.remove("hidden");
        }
        if (card.diagramWrap && card.diagramWrap.classList) {
          card.diagramWrap.classList.add("hidden");
        }
        return Promise.resolve(null);
      }

      initMermaid(getTheme());

      return mermaid.render(renderId, card.rawCode).then(function (result) {
        var svg = (result && result.svg) || "";
        card.svg = svg;
        if (card.diagramEl) {
          card.diagramEl.innerHTML = svg;
        }
        if (card.diagramWrap && card.diagramWrap.classList) {
          card.diagramWrap.classList.remove("hidden");
        }
        if (card.errorEl && card.errorEl.parentNode) {
          card.errorEl.parentNode.removeChild(card.errorEl);
          card.errorEl = null;
        }
        return svg;
      }).catch(function (err) {
        /* Fail-soft on syntax error */
        if (!card.errorEl && doc && doc.createElement) {
          card.errorEl = doc.createElement("div");
          card.errorEl.className = "mermaid-error";
          card.errorEl.textContent = "Error de sintaxis Mermaid: el código se muestra abajo.";
          if (card.cardEl && card.cardEl.insertBefore && card.codeWrap) {
            card.cardEl.insertBefore(card.errorEl, card.codeWrap);
          }
        }
        if (card.codeWrap && card.codeWrap.classList) {
          card.codeWrap.classList.remove("hidden");
        }
        if (card.diagramWrap && card.diagramWrap.classList) {
          card.diagramWrap.classList.add("hidden");
        }
        return null;
      });
    }

    /* Process all code.language-mermaid elements inside container */
    function renderIn(container) {
      if (!container || !container.querySelectorAll) return Promise.resolve(0);

      var candidates = container.querySelectorAll("code.language-mermaid, pre.language-mermaid");
      var list = [];
      for (var i = 0; i < candidates.length; i++) {
        list.push(candidates[i]);
      }

      var promises = [];
      for (var j = 0; j < list.length; j++) {
        var el = list[j];
        if (el.getAttribute && el.getAttribute("data-mermaid-processed") === "true") continue;
        if (findClassAncestor(el, "mermaid-card")) continue;

        var pre = findPreAncestor(el) || (el.tagName && el.tagName.toLowerCase() === "pre" ? el : el.parentNode);
        if (!pre || !pre.parentNode) continue;

        var codeEl = el.tagName && el.tagName.toLowerCase() === "code" ? el : (el.querySelector ? el.querySelector("code") : el);
        var rawCode = ((codeEl && codeEl.textContent) || el.textContent || "").trim();
        if (!rawCode) continue;

        if (el.setAttribute) el.setAttribute("data-mermaid-processed", "true");
        if (codeEl && codeEl.setAttribute) codeEl.setAttribute("data-mermaid-processed", "true");

        /* Build card DOM */
        var cardEl = doc.createElement("div");
        cardEl.className = "mermaid-card";

        var toolbar = doc.createElement("div");
        toolbar.className = "mermaid-toolbar";

        var badge = doc.createElement("span");
        badge.className = "mermaid-badge";
        badge.textContent = "Mermaid";

        var actions = doc.createElement("div");
        actions.className = "mermaid-toolbar-actions";

        var btnToggle = doc.createElement("button");
        btnToggle.type = "button";
        btnToggle.className = "mermaid-btn mermaid-btn-toggle";
        btnToggle.textContent = "Código";
        btnToggle.title = "Alternar entre código y diagrama";

        var btnCopy = doc.createElement("button");
        btnCopy.type = "button";
        btnCopy.className = "mermaid-btn mermaid-btn-copy";
        btnCopy.textContent = "Copiar";
        btnCopy.title = "Copiar código Mermaid";

        var btnZoom = doc.createElement("button");
        btnZoom.type = "button";
        btnZoom.className = "mermaid-btn mermaid-btn-zoom";
        btnZoom.textContent = "⛶ Visor";
        btnZoom.title = "Abrir en visor interactivo a pantalla completa";

        actions.appendChild(btnToggle);
        actions.appendChild(btnCopy);
        actions.appendChild(btnZoom);

        toolbar.appendChild(badge);
        toolbar.appendChild(actions);

        var diagramWrap = doc.createElement("div");
        diagramWrap.className = "mermaid-diagram-wrap";
        diagramWrap.title = "Haz clic para abrir el visor interactivo";

        var diagramEl = doc.createElement("div");
        diagramEl.className = "mermaid-diagram";
        diagramWrap.appendChild(diagramEl);

        var codeWrap = doc.createElement("div");
        codeWrap.className = "mermaid-code-wrap hidden";

        /* Transfer or clone the pre element */
        var preClone = pre.cloneNode ? pre.cloneNode(true) : pre;
        codeWrap.appendChild(preClone);

        cardEl.appendChild(toolbar);
        cardEl.appendChild(diagramWrap);
        cardEl.appendChild(codeWrap);

        pre.parentNode.replaceChild(cardEl, pre);

        var cardRecord = {
          cardEl: cardEl,
          diagramWrap: diagramWrap,
          diagramEl: diagramEl,
          codeWrap: codeWrap,
          rawCode: rawCode,
          svg: null,
          errorEl: null
        };
        cards.push(cardRecord);

        /* Card events */
        (function (rec, btnT, btnC) {
          if (btnT && btnT.addEventListener) {
            btnT.addEventListener("click", function (ev) {
              if (ev.stopPropagation) ev.stopPropagation();
              var isHidden = rec.codeWrap.classList.contains("hidden");
              if (isHidden) {
                rec.codeWrap.classList.remove("hidden");
                btnT.textContent = "Diagrama";
              } else {
                rec.codeWrap.classList.add("hidden");
                btnT.textContent = "Código";
              }
            });
          }
          if (btnC && btnC.addEventListener) {
            btnC.addEventListener("click", function (ev) {
              if (ev.stopPropagation) ev.stopPropagation();
              copyCode(rec.rawCode, btnC);
            });
          }
          if (btnZoom && btnZoom.addEventListener) {
            btnZoom.addEventListener("click", function (ev) {
              if (ev.stopPropagation) ev.stopPropagation();
              openModal(rec.svg, rec.rawCode);
            });
          }
          if (rec.diagramWrap && rec.diagramWrap.addEventListener) {
            rec.diagramWrap.addEventListener("click", function () {
              if (rec.svg) openModal(rec.svg, rec.rawCode);
            });
          }
        })(cardRecord, btnToggle, btnCopy);

        promises.push(renderCard(cardRecord));
      }

      return Promise.all(promises).then(function () {
        return promises.length;
      });
    }

    /* Change theme and re-render existing cards */
    function setTheme(themeName) {
      currentTheme = themeName === "light" ? "light" : "dark";
      if (doc && doc.documentElement && doc.documentElement.setAttribute) {
        try { doc.documentElement.setAttribute("data-theme", currentTheme); } catch (e) {}
      }
      initMermaid(currentTheme);

      /* Re-render active cards in place */
      var activeCards = [];
      for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        if (c.cardEl && c.cardEl.parentNode) {
          activeCards.push(c);
          renderCard(c);
        }
      }
      cards = activeCards;
    }

    return {
      renderIn: renderIn,
      openModal: openModal,
      closeModal: closeModal,
      setTheme: setTheme,
      getTheme: getTheme,
      setZoom: setZoom,
      resetZoom: resetZoom,
      getZoom: function () { return currentZoom; },
      copyCode: copyCode,
      getCards: function () { return cards.slice(); },
      initMermaid: initMermaid
    };
  }

  var api = {
    createMermaidViewer: createMermaidViewer
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.MermaidViewer = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
