/* herdr-brain PWA Web Push card (S5): "Avisos push" inside Ajustes.
 *
 * The phone's half of the push channel: ask the brain for its VAPID
 * public key, subscribe through the service worker's PushManager, and
 * hand the subscription to POST /push/subscribe (the device bearer
 * rides the request wrapper app.js injects — the server attributes the
 * subscription to this paired device). Disable unsubscribes both ends.
 * "Probar" drives POST /push/test and reports how many devices the
 * round trip reached.
 *
 * Pure DOM module in the pairing.js/toast.js shape: no dependencies,
 * every seam (request, getRegistration, notificationPermission, doc,
 * showToast, storage) injected so the node --test suite drives it with
 * a fake DOM. UI copy matches the PWA's Spanish; text nodes only. */
(function (global) {
  "use strict";

  function createPushUI(options) {
    var request = options.request;
    var getRegistration = options.getRegistration;
    var notificationPermission = options.notificationPermission || function () {
      return (typeof Notification !== "undefined" && Notification.permission) || "denied";
    };
    var requestPermission = options.requestPermission || function () {
      return (typeof Notification !== "undefined" && Notification.requestPermission)
        ? Notification.requestPermission()
        : Promise.resolve("denied");
    };
    var doc = options.doc || document;
    var showToast = options.showToast || function () {};
    /* i18n (S4): the page injects the dictionary runtime's t(); the
     * built-in Spanish lines stay the fallback (and the node suite's
     * single source). */
    var translate = options.t || null;

    function tr(key, fallback) {
      if (!translate) return fallback;
      var value = translate(key);
      return value && value !== key ? value : fallback;
    }

    /* State as the card renders it: serverKeys = the brain has VAPID
     * keys; subscription = this browser's endpoint (kept locally so the
     * card survives reloads without asking the server who we are). */
    var state = { serverKeys: null, enabled: false, endpoint: null, busy: false };

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.setAttribute("class", className);
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    }

    function clear(node) {
      while (node.firstChild) node.removeChild(node.firstChild);
    }

    /* VAPID keys arrive base64url; PushManager wants the raw bytes as a
     * BufferSource. Standard conversion, char-exact. */
    function applicationServerKey(b64url) {
      var padding = "=".repeat((4 - (b64url.length % 4)) % 4);
      var base64 = (b64url + padding).replace(/-/g, "+").replace(/_/g, "/");
      var raw = atob(base64);
      var bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      return bytes;
    }

    /* ---- rendering (the whole card lives in #push-box) ---- */

    function render() {
      var root = options.root || doc.getElementById("push-box");
      if (!root) return;
      clear(root);

      root.appendChild(el("div", "push-title", tr("push.title", "Avisos push")));

      var status;
      if (state.serverKeys === false) {
        status = tr("push.no_keys",
          "Sin claves en el servidor — pide al operador importarlas " +
          "(`herdr-brain push import`) o generar un par nuevo.");
      } else if (notificationPermission() === "denied") {
        status = tr("push.blocked",
          "El navegador bloquea las notificaciones; actívalas en los " +
          "ajustes del sitio y vuelve a intentarlo.");
      } else if (state.endpoint) {
        status = tr("push.active", "🔔 Avisos activos en este dispositivo");
      } else {
        status = tr("push.off", "Sin avisos push en este dispositivo");
      }
      root.appendChild(el("div", "push-status", status));

      if (state.serverKeys === false) return;

      var row = el("div", "push-actions");
      var enable = el("button", "push-enable",
          state.endpoint ? tr("push.reenable", "Reactivar") : tr("push.enable", "Activar"));
      enable.setAttribute("type", "button");
      enable.disabled = state.busy || notificationPermission() === "denied";
      enable.addEventListener("click", function () { enablePush(enable); });
      row.appendChild(enable);

      if (state.endpoint) {
        var test = el("button", "push-test", tr("push.test", "Probar"));
        test.setAttribute("type", "button");
        test.disabled = state.busy;
        test.addEventListener("click", function () { sendTest(test); });
        row.appendChild(test);

        var disable = el("button", "push-disable", tr("push.disable", "Desactivar"));
        disable.setAttribute("type", "button");
        disable.disabled = state.busy;
        disable.addEventListener("click", function () { disablePush(disable); });
        row.appendChild(disable);
      }
      root.appendChild(row);
    }

    /* ---- the wire ---- */

    function refresh() {
      return request("/push/key", { cache: "no-store" })
        .then(function (resp) {
          if (!resp.ok) return null;
          return resp.json().then(function (body) {
            return { enabled: !!body.enabled, publicKey: body.public_key || null };
          });
        })
        .then(function (info) {
          if (info === null) return null;  /* an older brain predates push */
          state.serverKeys = info.enabled;
          state.publicKey = info.publicKey;
          render();
          return state;
        })
        .catch(function () { return null; });
    }

    function subscribeOnServer(subscription, replaces) {
      var raw = subscription.toJSON ? subscription.toJSON() : subscription;
      var userAgent = (typeof navigator !== "undefined" && navigator.userAgent) || "";
      return request("/push/subscribe", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          endpoint: raw.endpoint,
          keys: raw.keys,
          user_agent: userAgent,
          replaces: replaces || null
        })
      }).then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(
            function (body) { return (body && body.detail) || ("error " + resp.status); },
            function () { return "error " + resp.status; }
          );
        }
        return resp.json().then(function () { return "ok"; });
      });
    }

    function enablePush(button) {
      if (state.busy || !state.publicKey) return Promise.resolve(null);
      state.busy = true;
      button.disabled = true;
      var replaces = state.endpoint || null;
      return Promise.resolve(requestPermission())
        .then(function (permission) {
          if (permission !== "granted") {
            throw new Error("permiso de notificaciones denegado");
          }
          return getRegistration();
        })
        .then(function (registration) {
          if (!registration || !registration.pushManager) {
            throw new Error("este navegador no soporta Web Push");
          }
          return registration.pushManager.getSubscription().then(function (existing) {
            if (existing) return existing;
            return registration.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: applicationServerKey(state.publicKey)
            });
          });
        })
        .then(function (subscription) {
          return subscribeOnServer(subscription, replaces).then(function (outcome) {
            if (outcome !== "ok") throw new Error(String(outcome));
            state.endpoint = subscription.endpoint;
            state.enabled = true;
            showToast(tr("push.enabled_toast", "Avisos push activados"), 4000);
            return "ok";
          });
        })
        .catch(function (err) {
          showToast(tr("push.enable_failed", "No se pudieron activar los avisos: {error}")
              .replace("{error}", err && err.message ? err.message : err), 6000);
          return null;
        })
        .then(function (outcome) {
          state.busy = false;
          render();
          return outcome;
        });
    }

    function disablePush(button) {
      if (state.busy || !state.endpoint) return Promise.resolve(null);
      state.busy = true;
      button.disabled = true;
      var endpoint = state.endpoint;
      return getRegistration()
        .then(function (registration) {
          return registration && registration.pushManager
            ? registration.pushManager.getSubscription()
            : null;
        })
        .then(function (subscription) {
          /* Server first: if the browser half fails, the server stops
           * sending and the next Activar re-subscribes cleanly. */
          return request("/push/unsubscribe", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ endpoint: endpoint })
          }).then(function () {
            return subscription ? subscription.unsubscribe() : true;
          });
        })
        .then(function () {
          state.endpoint = null;
          state.enabled = false;
          showToast(tr("push.disabled_toast", "Avisos push desactivados"), 4000);
          return "ok";
        })
        .catch(function () {
          showToast(tr("push.disable_failed", "No se pudieron desactivar los avisos — prueba otra vez"), 6000);
          return null;
        })
        .then(function (outcome) {
          state.busy = false;
          render();
          return outcome;
        });
    }

    function sendTest(button) {
      if (state.busy) return Promise.resolve(null);
      state.busy = true;
      button.disabled = true;
      return request("/push/test", { method: "POST" })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.json().then(
              function (body) { return (body && body.detail) || ("error " + resp.status); },
              function () { return "error " + resp.status; }
            );
          }
          return resp.json().then(function (body) {
            var sent = body && typeof body.sent === "number" ? body.sent : 0;
            showToast(
              sent > 0
                ? tr("push.test_sent", "Notificación de prueba enviada ({sent})").replace("{sent}", sent)
                : tr("push.test_none", "No se envió nada — ¿hay otro dispositivo suscrito?"),
              5000
            );
            return "ok";
          });
        })
        .catch(function () {
          showToast(tr("push.test_failed", "La prueba no llegó al brain — sin conexión"), 6000);
          return null;
        })
        .then(function (outcome) {
          state.busy = false;
          render();
          return outcome;
        });
    }

    return {
      refresh: refresh,
      enable: enablePush,
      disable: disablePush,
      sendTest: sendTest,
      render: render,
      state: function () { return state; },
      applicationServerKey: applicationServerKey
    };
  }

  var api = { createPushUI: createPushUI };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Push = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
