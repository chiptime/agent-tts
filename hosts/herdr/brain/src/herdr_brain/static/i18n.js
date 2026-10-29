/* Brain i18n runtime (S4, doc 09) — own conception, vanilla JS.
 *
 * A dictionary store the size of this UI needs: `t(key, vars)` with
 * {slot} interpolation by split/join, a subscription list the dynamic
 * renderers re-paint on, persistence in localStorage and <html lang>
 * kept in sync. Spanish is the SOURCE language: the es dictionary
 * carries the exact strings the app rendered before i18n existed, so
 * the default boot is byte-identical to the pre-i18n UI; other locales
 * are full siblings, and a key missing from the active locale falls
 * back to es before it ever shows a raw key to the operator.
 *
 * House pattern (reader.js/toast.js): no browser global is touched
 * outside the injected surface {doc, storage}, so the node suite
 * drives the runtime against a plain fake DOM. In the browser the
 * dictionary scripts (i18n/es.js, i18n/en.js) register themselves into
 * the global registry this file exposes; the first registration
 * instantiates window.I18N and re-paints the data-i18n chrome.
 *
 * Dictionary loading is deliberately NOT lazy (doc 09, open question
 * 3): both catalogs are a few KB each and load as plain scripts — one
 * round trip saved per switch, no bundler needed. */

(function (global) {
  "use strict";

  var STORAGE_KEY = "brain:locale:v1";
  var SOURCE_LOCALE = "es";

  function interpolate(template, vars) {
    if (!vars) return template;
    var out = template;
    for (var slot in vars) {
      if (!Object.prototype.hasOwnProperty.call(vars, slot)) continue;
      out = out.split("{" + slot + "}").join(String(vars[slot]));
    }
    return out;
  }

  function createI18n(deps) {
    deps = deps || {};
    var doc = deps.doc || null;
    var storage = deps.storage || null;
    var dicts = deps.dicts || {};
    /* The registry object itself: later registrations (the dictionary
     * <script> files load one after another) extend a live instance. */
    var order = function () { return Object.keys(dicts); };
    var current = null;

    function available(code) {
      return Object.prototype.hasOwnProperty.call(dicts, code);
    }

    function storedLocale() {
      if (!storage) return null;
      try {
        var value = storage.getItem(STORAGE_KEY);
        return value && available(value) ? value : null;
      } catch (err) { return null; }
    }

    var initial = order();
    current = storedLocale() || (available(SOURCE_LOCALE) ? SOURCE_LOCALE : initial[0]);

    var subscribers = [];

    function t(key, vars) {
      var dict = dicts[current];
      var template = dict && Object.prototype.hasOwnProperty.call(dict, key)
        ? dict[key]
        : null;
      if (template == null) {
        var source = dicts[SOURCE_LOCALE];
        template = source && Object.prototype.hasOwnProperty.call(source, key)
          ? source[key]
          : null;
      }
      if (template == null) return key;  /* debuggable, never silent */
      return interpolate(template, vars);
    }

    function getLocale() { return current; }

    function locales() { return order(); }

    function setLocale(code) {
      if (!available(code) || code === current) return available(code);
      current = code;
      if (storage) {
        try { storage.setItem(STORAGE_KEY, code); } catch (err) { /* private mode */ }
      }
      if (doc && doc.documentElement) {
        try { doc.documentElement.setAttribute("lang", code); } catch (err) { /* no attr */ }
      }
      applyDom(doc);
      for (var i = 0; i < subscribers.length; i++) subscribers[i](code);
      return true;
    }

    function subscribe(fn) {
      subscribers.push(fn);
      return function unsubscribe() {
        var at = subscribers.indexOf(fn);
        if (at !== -1) subscribers.splice(at, 1);
      };
    }

    /* Static chrome re-paint: elements the JS never touches carry
     * data-i18n (text), data-i18n-placeholder / -title / -aria-label
     * (attributes). JS-managed labels re-render through their own
     * subscription; this pass never fights them. */
    function applyDom(root) {
      if (!root || !root.querySelectorAll) return;
      paint(root, "[data-i18n]", null);
      paint(root, "[data-i18n-placeholder]", "placeholder");
      paint(root, "[data-i18n-title]", "title");
      paint(root, "[data-i18n-aria-label]", "aria-label");
    }

    function paint(root, selector, attribute) {
      var nodes = root.querySelectorAll(selector);
      for (var i = 0; i < nodes.length; i++) {
        var node = nodes[i];
        var key = attribute
          ? node.getAttribute("data-i18n-" + attribute)
          : node.getAttribute("data-i18n");
        if (!key) continue;
        var text = t(key);
        if (attribute) node.setAttribute(attribute, text);
        else node.textContent = text;
      }
    }

    /* A locale registered AFTER this instance was created (the
     * dictionary <script> files load one by one) may be the one the
     * device stored — adopt it now that it exists. */
    function adoptStoredLocale() {
      var stored = storedLocale();
      if (stored && stored !== current) setLocale(stored);
    }

    var api = {
      t: t,
      getLocale: getLocale,
      setLocale: setLocale,
      locales: locales,
      subscribe: subscribe,
      applyDom: applyDom,
      _adoptStoredLocale: adoptStoredLocale,
    };
    /* Test seam: the suite's fake doc keeps its own state. */
    if (deps.exposeDeps) api._deps = deps;
    return api;
  }

  /* ---- browser boot: the dictionary scripts register here ---- */

  var registry = {};
  var instance = null;

  function register(code, dict) {
    registry[code] = dict;
    if (!instance && typeof global.document !== "undefined") {
      instance = createI18n({
        doc: global.document,
        storage: global.localStorage,
        dicts: registry,
      });
      global.I18N = instance;
      instance.applyDom(global.document);
    } else if (instance) {
      /* A later-registered locale may be the stored choice — adopt it
       * before repainting (no-op when it is not). */
      if (instance._adoptStoredLocale) instance._adoptStoredLocale();
      instance.applyDom(global.document);
    }
  }

  var api = {
    createI18n: createI18n,
    register: register,
    STORAGE_KEY: STORAGE_KEY,
    SOURCE_LOCALE: SOURCE_LOCALE,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.BrainI18n = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
