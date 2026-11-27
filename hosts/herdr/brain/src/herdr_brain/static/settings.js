/* Ajustes appearance controls (S4, doc 09) — own conception.
 *
 * Per-device settings in localStorage, one JSON blob next to the
 * pre-existing theme key (the theme stays where it was: app.js owns
 * applyTheme because the mermaid viewer and the theme-color meta ride
 * it; this module only supplies the pure 3-state cycle so the first
 * tap from auto flips the system resolution — the toggle contract the
 * browser suite has pinned since before "auto" existed).
 *
 * Text size is three steps over a --font-scale CSS variable on :root;
 * Zen hides the header/chips via a body class and keeps the call
 * button; haptics vibrate on gate/receipt events only where
 * navigator.vibrate exists (the row is hidden elsewhere, the reference
 * panel's own honest pattern). The language row cycles locales
 * through the injected i18n and publishes the choice to
 * POST /call/context {locale} so the SERVER can speak the same
 * language the operator reads (the watcher's announce lexicon). */

(function (global) {
  "use strict";

  var SETTINGS_KEY = "herdr-brain-settings-v1";
  var THEME_KEY = "herdr-brain-theme";

  var FONT_STEPS = ["small", "normal", "large"];
  var FONT_SCALE = { small: "0.9", normal: "1", large: "1.15" };
  var HAPTIC_PATTERNS = {
    gate: [60, 40, 60],      /* an agent is waiting on you */
    receipt: [30],           /* the terminal answered */
    update: [20],            /* a toast you can act on */
  };

  function defaults() {
    return { font: "normal", zen: false, haptics: true };
  }

  function loadSettings(storage) {
    var merged = defaults();
    if (!storage) return merged;
    try {
      var raw = storage.getItem(SETTINGS_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      if (parsed && typeof parsed === "object") {
        if (FONT_STEPS.indexOf(parsed.font) !== -1) merged.font = parsed.font;
        if (typeof parsed.zen === "boolean") merged.zen = parsed.zen;
        if (typeof parsed.haptics === "boolean") merged.haptics = parsed.haptics;
      }
    } catch (err) { /* corrupt payload: the defaults above hold */ }
    return merged;
  }

  function saveSettings(storage, settings) {
    if (!storage) return;
    try { storage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (err) { /* private mode */ }
  }

  /* ---- theme (pure): the stored mode + the system resolution ---- */

  function resolveThemeMode(stored, systemDark) {
    if (stored === "light" || stored === "dark") return stored;
    return "auto";
  }

  /* The 3-state cycle, entered so the FIRST tap from auto flips the
   * system resolution (light system → dark), exactly the pre-S4
   * toggle. From an explicit choice the cycle visits the other
   * explicit theme and then auto. */
  function nextThemeMode(mode, systemDark) {
    var cycle = systemDark ? ["light", "dark", "auto"] : ["dark", "light", "auto"];
    var at = cycle.indexOf(mode);
    if (at === -1) at = 2;  /* auto (or unknown) enters at the end */
    return cycle[(at + 1) % cycle.length];
  }

  /* The data-theme the CSS actually consumes. */
  function resolvedTheme(mode, systemDark) {
    if (mode === "light" || mode === "dark") return mode;
    return systemDark ? "dark" : "light";
  }

  function systemPrefersDark(win) {
    return !!(win && win.matchMedia && win.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  /* ---- haptics ---- */

  function hapticsSupported(navigatorObj) {
    return !!(navigatorObj && typeof navigatorObj.vibrate === "function");
  }

  /* ---- the settings panel wiring ---- */

  function createSettings(deps) {
    deps = deps || {};
    var doc = deps.doc;
    var storage = deps.storage || null;
    var navigatorObj = deps.navigator || (typeof navigator !== "undefined" ? navigator : null);
    var i18n = deps.i18n || null;
    var rows = deps.rows || {};
    var labels = deps.labels || {};
    var postLocale = deps.postLocale || function () { return Promise.resolve(); };
    var settings = loadSettings(storage);
    var hapticsOn = settings.haptics && hapticsSupported(navigatorObj);

    function fontLabel() {
      var key = { small: "settings.font_small", normal: "settings.font_normal", large: "settings.font_large" }[settings.font];
      return labels.font ? labels.font(key) : key;
    }

    function applyFont() {
      if (doc && doc.documentElement && doc.documentElement.style) {
        doc.documentElement.style.setProperty("--font-scale", FONT_SCALE[settings.font]);
      }
      if (rows.font) rows.font.textContent = fontLabel();
    }

    function applyZen() {
      if (doc && doc.body && doc.body.classList) {
        doc.body.classList.toggle("zen", settings.zen);
      }
      if (rows.zen) rows.zen.textContent = labels.zen
        ? labels.zen(settings.zen ? "settings.zen_on" : "settings.zen_off")
        : "zen";
    }

    function applyHaptics() {
      if (!rows.haptics) return;
      /* The whole row hides where the device cannot buzz — the label
       * without its control would be noise. */
      var hideTarget = rows.hapticsRow || rows.haptics;
      if (!hapticsSupported(navigatorObj)) {
        hideTarget.hidden = true;
        return;
      }
      hideTarget.hidden = false;
      rows.haptics.textContent = labels.haptics
        ? labels.haptics(hapticsOn ? "settings.haptics_on" : "settings.haptics_off")
        : "haptics";
    }

    function applyLanguage() {
      if (!rows.language || !i18n) return;
      var code = i18n.getLocale();
      rows.language.textContent = labels.language
        ? labels.language(code === "en" ? "settings.language_en" : "settings.language_es")
        : code;
    }

    function persist() {
      saveSettings(storage, settings);
    }

    if (rows.font && rows.font.addEventListener) {
      rows.font.addEventListener("click", function () {
        var at = FONT_STEPS.indexOf(settings.font);
        settings.font = FONT_STEPS[(at + 1) % FONT_STEPS.length];
        persist();
        applyFont();
      });
    }
    if (rows.zen && rows.zen.addEventListener) {
      rows.zen.addEventListener("click", function () {
        settings.zen = !settings.zen;
        persist();
        applyZen();
      });
    }
    if (rows.zenExit && rows.zenExit.addEventListener) {
      rows.zenExit.addEventListener("click", function () {
        settings.zen = false;
        persist();
        applyZen();
      });
    }
    if (rows.haptics && rows.haptics.addEventListener && hapticsSupported(navigatorObj)) {
      rows.haptics.addEventListener("click", function () {
        hapticsOn = !hapticsOn;
        settings.haptics = hapticsOn;
        persist();
        applyHaptics();
      });
    }
    if (rows.language && rows.language.addEventListener && i18n) {
      rows.language.addEventListener("click", function () {
        var order = i18n.locales ? i18n.locales() : [i18n.getLocale()];
        var at = order.indexOf(i18n.getLocale());
        var next = order[(at + 1) % order.length];
        if (i18n.setLocale(next)) postLocale(next);
      });
      if (i18n.subscribe) i18n.subscribe(applyLanguage);
    }

    /* Initial paint. */
    applyFont();
    applyZen();
    applyHaptics();
    applyLanguage();

    return {
      /* Event hook for app.js: gate/receipt buzzes. Silent where the
       * device has no vibrate or the operator turned haptics off. */
      buzz: function buzz(kind) {
        if (!hapticsOn || !hapticsSupported(navigatorObj)) return false;
        var pattern = HAPTIC_PATTERNS[kind];
        if (!pattern) return false;
        try { return navigatorObj.vibrate(pattern) === true || true; } catch (err) { return false; }
      },
      get: function () { return Object.assign({}, settings); },
      setZen: function (val) {
        settings.zen = !!val;
        persist();
        applyZen();
      },
    };
  }

  /* ---- status cards (POST-08): Cuotas de IA + Carga del host ----
   *
   * Two honest, read-only cards inside the Ajustes sheet, rendered by
   * createStatusCards (same seams-injected shape as push.js). app.js
   * calls refresh() when the sheet OPENS — never at boot, never on a
   * timer — through readFetch (device bearer + the 403 pairing
   * recovery). Every server string lands in a text node (C1); numbers
   * render only when finite. The resets stamp formats device-locally
   * because the backend ships raw ISO (its format_reset is voice-tool
   * only) — the "Thu 18:59" shape matches the spoken line. */

  var RESET_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function formatResetLocal(iso) {
    if (typeof iso !== "string" || !iso) return null;
    var moment = new Date(iso);
    if (isNaN(moment.getTime())) return null;
    var hh = String(moment.getHours()).padStart(2, "0");
    var mm = String(moment.getMinutes()).padStart(2, "0");
    return RESET_DAYS[moment.getDay()] + " " + hh + ":" + mm;
  }

  function formatBps(bps) {
    if (typeof bps !== "number" || !isFinite(bps) || bps < 0) return null;
    if (bps < 1024) return Math.round(bps) + " B/s";
    var units = ["KB/s", "MB/s", "GB/s"];
    var value = bps;
    var at = -1;
    while (value >= 1024 && at < units.length - 1) { value /= 1024; at++; }
    return value.toFixed(1) + " " + units[at];
  }

  function clampPercent(value) {
    if (typeof value !== "number" || !isFinite(value)) return null;
    if (value < 0) return 0;
    if (value > 100) return 100;
    return value;
  }

  function createStatusCards(deps) {
    deps = deps || {};
    var request = deps.request || function () {
      return Promise.reject(new Error("no transport"));
    };
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var translate = deps.t || null;

    function tr(key, fallback) {
      if (!translate) return fallback;
      var value = translate(key);
      return value && value !== key ? value : fallback;
    }

    var busy = false;
    var cards = {
      quotas: { payload: null, failed: false },
      load: { payload: null, failed: false },
    };

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.setAttribute("class", className);
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    }

    function clear(node) {
      while (node.firstChild) node.removeChild(node.firstChild);
    }

    /* One meta fragment per trusted shape; everything else stays out. */
    function quotaMeta(record) {
      var parts = [];
      var pct = clampPercent(record.used_percent);
      if (pct !== null) parts.push(Math.round(pct) + "%");
      if (typeof record.label === "string" && record.label) parts.push(record.label);
      if (typeof record.detail === "string" && record.detail) parts.push(record.detail);
      var reset = formatResetLocal(record.resets_at);
      if (reset) parts.push(tr("quotas.resets", "Renueva {when}").replace("{when}", reset));
      if (typeof record.cost_today_usd === "number" && isFinite(record.cost_today_usd)) {
        parts.push(tr("quotas.cost_today", "{amount} hoy")
          .replace("{amount}", "$" + record.cost_today_usd.toFixed(2)));
      }
      if (typeof record.cost_month_to_date_usd === "number" && isFinite(record.cost_month_to_date_usd)) {
        parts.push(tr("quotas.cost_month", "{amount} mes")
          .replace("{amount}", "$" + record.cost_month_to_date_usd.toFixed(2)));
      }
      return parts.join(" · ");
    }

    function quotaRecord(record) {
      var name = (typeof record.display_name === "string" && record.display_name) ||
        (typeof record.provider === "string" && record.provider) || null;
      if (!name) return null;
      var state = typeof record.state === "string" && record.state ? record.state : "unknown";
      var row = el("div", "quota-row");
      var head = el("div", "quota-head");
      head.appendChild(el("div", "quota-name", name));
      head.appendChild(el("div", "quota-state " + state, tr("quotas.state_" + state, state)));
      row.appendChild(head);
      var pct = clampPercent(record.used_percent);
      if (pct !== null) {
        var fill = el("div", "quota-bar-fill " + state);
        fill.style.width = pct + "%";
        var bar = el("div", "quota-bar");
        bar.appendChild(fill);
        row.appendChild(bar);
      }
      row.appendChild(el("div", "quota-meta", quotaMeta(record)));
      return row;
    }

    function renderQuotas() {
      var root = doc.getElementById("quotas-box");
      if (!root) return;
      clear(root);
      root.appendChild(titleRow("quotas.title", "Cuotas de IA",
        "quotas.refresh_title", "Actualizar cuotas",
        function () { return refreshCard("quotas"); }));
      var card = cards.quotas;
      if (card.failed && !card.payload) {
        root.appendChild(el("div", "push-status",
          tr("quotas.fail", "No se pudieron cargar las cuotas — sin conexión")));
        return;
      }
      if (!card.payload) {
        root.appendChild(el("div", "push-status", tr("common.loading", "Cargando…")));
        return;
      }
      var payload = card.payload;
      var providers = Array.isArray(payload.providers) ? payload.providers : [];
      if (!payload.available || providers.length === 0) {
        root.appendChild(el("div", "push-status",
          tr("quotas.unavailable", "Sin datos de cuotas ahora mismo")));
        if (typeof payload.reason === "string" && payload.reason) {
          root.appendChild(el("div", "quota-reason", payload.reason));
        }
        return;
      }
      if (payload.stale) {
        root.appendChild(el("div", "quota-stale",
          tr("quotas.stale", "Datos en caché — pueden estar desactualizados")));
      }
      for (var i = 0; i < providers.length; i++) {
        var row = providers[i] && typeof providers[i] === "object" ? quotaRecord(providers[i]) : null;
        if (row) root.appendChild(row);
      }
    }

    function metricRow(labelKey, labelFallback, percent) {
      var row = el("div", "load-row");
      row.appendChild(el("div", "load-label", tr(labelKey, labelFallback)));
      var fill = el("div", "quota-bar-fill");
      fill.style.width = percent + "%";
      var bar = el("div", "quota-bar");
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("div", "load-value", Math.round(percent) + "%"));
      return row;
    }

    function renderLoad() {
      var root = doc.getElementById("load-box");
      if (!root) return;
      clear(root);
      root.appendChild(titleRow("load.title", "Carga del host",
        "load.refresh_title", "Actualizar carga",
        function () { return refreshCard("load"); }));
      var card = cards.load;
      if (card.failed && !card.payload) {
        root.appendChild(el("div", "push-status",
          tr("load.fail", "No se pudo cargar la carga del host — sin conexión")));
        return;
      }
      if (!card.payload) {
        root.appendChild(el("div", "push-status", tr("common.loading", "Cargando…")));
        return;
      }
      var payload = card.payload;
      if (!payload.available) {
        root.appendChild(el("div", "push-status",
          tr("load.unavailable", "Carga del host no disponible en este equipo")));
        if (typeof payload.reason === "string" && payload.reason) {
          root.appendChild(el("div", "quota-reason", payload.reason));
        }
        return;
      }
      var alerts = payload.alerts;
      if (typeof payload.host === "string" && payload.host) {
        root.appendChild(el("div", "push-status", payload.host));
      }
      if (alerts && typeof alerts.configured === "number" && alerts.configured > 0) {
        root.appendChild(el("div", "load-alerts",
          tr("load.alerts", "⚠ {active}/{configured} alertas activas")
            .replace("{active}", String(typeof alerts.active === "number" ? alerts.active : 0))
            .replace("{configured}", String(alerts.configured))));
      }
      if (typeof payload.cpu_percent !== "number" || !isFinite(payload.cpu_percent)) {
        root.appendChild(el("div", "load-waiting",
          tr("load.waiting", "Esperando la primera muestra del monitor…")));
        return;
      }
      root.appendChild(metricRow("load.cpu", "CPU", clampPercent(payload.cpu_percent)));
      var ram = clampPercent(payload.ram_percent);
      if (ram !== null) root.appendChild(metricRow("load.ram", "RAM", ram));
      var disk = clampPercent(payload.disk_percent);
      if (disk !== null) root.appendChild(metricRow("load.disk", "Disco", disk));
      var rx = formatBps(payload.net_rx_bps);
      var tx = formatBps(payload.net_tx_bps);
      if (rx !== null && tx !== null) {
        root.appendChild(el("div", "load-net", "↓ " + rx + " · ↑ " + tx));
      }
    }

    function titleRow(titleKey, titleFallback, refreshTitleKey, refreshTitleFallback, onRefresh) {
      var row = el("div", "cards-title");
      row.appendChild(el("div", "push-title", tr(titleKey, titleFallback)));
      var button = el("button", "cards-refresh", "↻");
      button.setAttribute("type", "button");
      button.setAttribute("title", tr(refreshTitleKey, refreshTitleFallback));
      button.disabled = busy;
      button.addEventListener("click", function () { onRefresh(); });
      row.appendChild(button);
      return row;
    }

    function fetchJson(path) {
      return Promise.resolve(request(path, { cache: "no-store" })).then(
        function (resp) {
          if (!resp || !resp.ok) return null;
          return resp.json().then(function (body) {
            return body && typeof body === "object" ? body : null;
          }, function () { return null; });
        },
        function () { return null; }
      );
    }

    function render() {
      renderQuotas();
      renderLoad();
    }

    /* One card's ↻ refetches ITS endpoint only (approved default: the
     * cards refresh independently); the shared busy guard keeps the
     * wire free of overlapping fetches. */
    function refreshCard(which) {
      if (busy) return Promise.resolve(null);
      busy = true;
      render();  /* paints Cargando… and disables the ↻ buttons */
      return fetchJson(which === "quotas" ? "/quotas" : "/load").then(function (payload) {
        var card = which === "quotas" ? cards.quotas : cards.load;
        card.failed = payload === null;
        if (payload !== null) card.payload = payload;
        busy = false;
        render();
        return state();
      });
    }

    function refresh() {
      if (busy) return Promise.resolve(null);
      busy = true;
      render();  /* paints Cargando… and disables the ↻ buttons */
      return Promise.all([fetchJson("/quotas"), fetchJson("/load")]).then(function (results) {
        for (var i = 0; i < 2; i++) {
          var card = i === 0 ? cards.quotas : cards.load;
          var payload = results[i];
          card.failed = payload === null;
          if (payload !== null) card.payload = payload;
        }
        busy = false;
        render();
        return state();
      });
    }

    function state() {
      return {
        busy: busy,
        quotas: { payload: cards.quotas.payload, failed: cards.quotas.failed },
        load: { payload: cards.load.payload, failed: cards.load.failed },
      };
    }

    return {
      refresh: refresh,
      render: render,
      state: state,
    };
  }

  var api = {
    createSettings: createSettings,
    createStatusCards: createStatusCards,
    formatResetLocal: formatResetLocal,
    formatBps: formatBps,
    clampPercent: clampPercent,
    nextThemeMode: nextThemeMode,
    resolveThemeMode: resolveThemeMode,
    resolvedTheme: resolvedTheme,
    systemPrefersDark: systemPrefersDark,
    hapticsSupported: hapticsSupported,
    HAPTIC_PATTERNS: HAPTIC_PATTERNS,
    FONT_STEPS: FONT_STEPS,
    SETTINGS_KEY: SETTINGS_KEY,
    THEME_KEY: THEME_KEY,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.BrainSettings = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
