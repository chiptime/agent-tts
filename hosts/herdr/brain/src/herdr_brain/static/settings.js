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
    };
  }

  var api = {
    createSettings: createSettings,
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
