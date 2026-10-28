/* herdr-brain PWA device pairing.
 *
 * The phone's half of the credential the brain's write routes require:
 * spend the one-time code the operator reads off `herdr-brain pair`,
 * keep the returned bearer token in localStorage and attach it as
 * `Authorization: Bearer` on every write fetch (app.js wires
 * authHeaders into its wrappers). The code is minted on the TERMINAL,
 * never in this UI — a "pair this device" button would be authorised
 * by nothing, because the phone asking to be paired is the one party
 * that cannot yet prove anything.
 *
 * Pure DOM module in the toast.js/approval.js shape: no dependencies,
 * every seam (request, storage, doc, openSettings, onPaired) injected
 * so the node --test suite drives it with a fake DOM. UI copy matches
 * the PWA's Spanish. */
(function (global) {
  "use strict";

  var TOKEN_KEY = "herdr_brain_device_token";
  /* The exact 403 details the server's pairing gate answers with
   * (access.py). Byte-compared so a "not paired" refusal can be told
   * apart from "host not allowed" — only the pairing ones should open
   * the pairing flow — and an expired token (its own text, S11) can be
   * told apart from a missing one: the fix is the same flow, the words
   * to the operator are not. */
  var NOT_PAIRED_DETAIL = "dispositivo no emparejado";
  var EXPIRED_DETAIL = "dispositivo caducado";
  /* EventSource and <audio> cannot send headers; these two GET
   * transports carry the credential as a query parameter instead
   * (access.py confines it there — the list here mirrors the server's
   * allowlist for appending, not for accepting). */
  var QUERY_TOKEN_PATHS = [/^\/events$/, /^\/audio\/[^/]+$/];

  function createPairingUI(options) {
    var request = options.request;
    var storage = options.storage;
    var doc = options.doc || document;
    var openSettings = options.openSettings || function () {};
    var onPaired = options.onPaired || function () {};
    /* B1: fired when THIS device's local credential dies (self-revoke,
     * or a revoke response that leaves no current device while a token
     * was held) — app.js reopens the SSE transport on it. Revoking
     * ANOTHER device keeps ours, so the callback must stay silent. */
    var onRevoked = options.onRevoked || function () {};

    var state = { enforced: false, current: null, devices: [] };
    var offerPending = false;
    var expiredPending = false;

    function token() {
      try { return storage.getItem(TOKEN_KEY) || null; } catch (err) { return null; }
    }

    function isPaired() {
      return !!state.current;
    }

    function authHeaders(extra) {
      var headers = Object.assign({}, extra || {});
      var bearer = token();
      if (bearer && !headers.Authorization) headers.Authorization = "Bearer " + bearer;
      return headers;
    }

    /* The query-parameter credential for the two headerless transports
     * (S11). Appends only on the allowed paths, only with a token in
     * hand — and NEVER to a write: the server ignores the parameter
     * elsewhere, and the URL must not leak a token where the server
     * would not read it. */
    function withQueryToken(url) {
      var bearer = token();
      if (!bearer) return url;
      var path = url.split("?")[0];
      var allowed = QUERY_TOKEN_PATHS.some(function (pattern) {
        return pattern.test(path);
      });
      if (!allowed) return url;
      return url + (url.indexOf("?") === -1 ? "?" : "&") + "device_token=" + encodeURIComponent(bearer);
    }

    /* Callers hand over the PARSED 403 BODY ({detail}) — app.js's
     * maybeOfferPairing has already gated on the HTTP status itself —
     * or a {status, detail} pair. The detail is the byte-compared
     * part; a status, when the object carries one at all, must be the
     * 403 (T4 B3: requiring .status on a parsed body meant the hint
     * could never appear on any write route). */
    function isNotPairedResponse(resp) {
      if (!resp || resp.detail !== NOT_PAIRED_DETAIL) return false;
      return resp.status === undefined || resp.status === 403;
    }

    function isExpiredResponse(resp) {
      if (!resp || resp.detail !== EXPIRED_DETAIL) return false;
      return resp.status === undefined || resp.status === 403;
    }

    /* ---- rendering (the whole card lives in #pairing-box) ---- */

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.setAttribute("class", className);
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    }

    function clear(node) {
      while (node.firstChild) node.removeChild(node.firstChild);
    }

    function render() {
      var root = options.root || doc.getElementById("pairing-box");
      if (!root) return;
      clear(root);

      if (offerPending) {
        root.appendChild(el("div", "pair-hint",
          "Este brain exige un dispositivo emparejado para escribir. " +
          "Pide un código con `herdr-brain pair` en la máquina y empareja aquí."));
      }
      if (expiredPending) {
        root.appendChild(el("div", "pair-hint pair-hint-expired",
          "El token de este dispositivo caducó. Pide un código nuevo con " +
          "`herdr-brain pair` y vuelve a emparejar (o pide al operador ampliar " +
          "el plazo con `devices set-expiry`)."));
      }

      var status = el("div", "pair-status");
      if (state.current) {
        status.textContent = "📱 Emparejado como «" + state.current + "»";
      } else if (expiredPending) {
        status.textContent = "🔒 Token caducado: vuelve a emparejar";
      } else if (state.enforced) {
        status.textContent = "🔒 Bloqueado: dispositivo no emparejado";
      } else {
        status.textContent = "Sin emparejar (escritura y lectura siguen abiertas)";
      }
      root.appendChild(status);

      if (!state.current) {
        var form = el("form", "pair-form");
        var code = el("input", "pair-code");
        code.setAttribute("type", "text");
        code.setAttribute("inputmode", "latin");
        code.setAttribute("placeholder", "Código (8 caracteres)");
        code.setAttribute("maxlength", "12");
        code.setAttribute("autocomplete", "off");
        var label = el("input", "pair-label");
        label.setAttribute("type", "text");
        label.setAttribute("placeholder", "Nombre del dispositivo");
        label.setAttribute("maxlength", "48");
        var submit = el("button", "pair-submit", "Emparejar");
        submit.setAttribute("type", "submit");
        form.appendChild(code);
        form.appendChild(label);
        form.appendChild(submit);
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          pair(code.value || "", label.value || "").then(function (outcome) {
            if (outcome !== "ok") {
              var line = el("div", "pair-error", pairErrorText(outcome));
              root.insertBefore(line, form.nextSibling);
            }
          });
        });
        root.appendChild(form);
        return;
      }

      /* Paired: the device list with revoke buttons. Revoking yourself
       * (the "Desvincular" row) is how a device un-pairs. A device with
       * an expiresAt shows when its token runs out (S11). */
      var list = el("div", "pair-devices");
      (state.devices || []).forEach(function (device) {
        var row = el("div", "pair-device");
        var label = device.label + (device.current ? " (este dispositivo)" : "");
        if (typeof device.expiresAt === "number" && device.expiresAt > 0) {
          label += " · caduca " + new Date(device.expiresAt * 1000).toISOString().slice(0, 16).replace("T", " ");
        }
        row.appendChild(el("span", "pair-device-label", label));
        var revoke = el("button", "pair-revoke",
          device.current ? "Desvincular" : "Revocar");
        revoke.setAttribute("type", "button");
        revoke.addEventListener("click", function () {
          revoke.disabled = true;
          revokeDevice(device.label);
        });
        row.appendChild(revoke);
        list.appendChild(row);
      });
      root.appendChild(list);
    }

    function pairErrorText(reason) {
      var texts = {
        "no-pending": "No hay ningún emparejamiento pendiente — pide un código nuevo.",
        "expired": "El código caducó (dura 10 minutos) — pide otro.",
        "exhausted": "Demasiados intentos fallidos — pide otro código.",
        "bad-code": "Código incorrecto — revisa los caracteres.",
        "duplicate-label": "Ese nombre ya está en uso — elige otro."
      };
      return texts[reason] || "No se pudo emparejar (" + reason + ").";
    }

    /* ---- the wire ---- */

    function refresh() {
      return request("/devices", { headers: authHeaders(), cache: "no-store" })
        .then(function (resp) {
          if (resp.ok) {
            return resp.json().then(function (body) {
              expiredPending = false;
              return {
                enforced: !!body.enforced,
                current: body.current || null,
                devices: body.devices || []
              };
            });
          }
          if (resp.status === 403) {
            /* S11: /devices itself is gated once enforcement is on, so
             * the 403 IS the status payload — "unpaired" (or "your
             * token ran out") rendered locally, no device list needed.
             * The card shows the pairing form; no settings popup from
             * here: the boot poll must not yank Ajustes open. */
            return resp.json().then(function (body) {
              if (!body || (body.detail !== NOT_PAIRED_DETAIL &&
                            body.detail !== EXPIRED_DETAIL)) {
                return null;  /* a host/origin 403 is not ours to parse */
              }
              expiredPending = body.detail === EXPIRED_DETAIL;
              return { enforced: true, current: null, devices: [] };
            }, function () { return null; });
          }
          return null;  /* an older brain predates pairing */
        })
        .then(function (next) {
          if (!next) return null;
          state = next;
          render();
          return state;
        })
        .catch(function () { return null; });
    }

    function pair(code, label) {
      return request("/pair", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code: code, label: label })
      }).then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(
            function (body) { return (body && body.detail) || "error " + resp.status; },
            function () { return "error " + resp.status; }
          );
        }
        return resp.json().then(function (body) {
          /* The token exists exactly once, here: store it BEFORE any
           * further call and never log it. */
          try { storage.setItem(TOKEN_KEY, body.token); } catch (err) { /* private mode */ }
          state.current = body.label;
          state.enforced = true;
          offerPending = false;
          onPaired(body.label);
          return refresh().then(function () { return "ok"; });
        });
      });
    }

    function revokeDevice(label) {
      return request("/devices/revoke", {
        method: "POST",
        headers: authHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ label: label })
      }).then(function (resp) {
        if (!resp.ok) return false;
        /* The revoke response IS the devices payload; when current is
         * null but we still hold a token, our own credential is dead
         * (self-revoke, or someone revoked us) — drop it locally. */
        return resp.json().then(function (body) {
          state = {
            enforced: !!(body && body.enforced),
            current: (body && body.current) || null,
            devices: (body && body.devices) || []
          };
          if (!state.current && token()) {
            try { storage.removeItem(TOKEN_KEY); } catch (err) { /* noop */ }
            onRevoked();
          }
          render();
          return true;
        });
      }).catch(function () { return false; });
    }

    /* Offer the pairing flow when a route came back "not paired" (S11:
     * reads too): open Ajustes once and say what to do next (doc 01
     * §6). Idempotent on purpose — the 5s read polls each carry the
     * same 403, and reopening the sheet the user just closed on every
     * beat would be the offer making itself spam. */
    function offer(expired) {
      if (offerPending) return;
      offerPending = true;
      if (expired) expiredPending = true;
      openSettings();
      render();
    }

    return {
      token: token,
      isPaired: isPaired,
      authHeaders: authHeaders,
      withQueryToken: withQueryToken,
      isNotPairedResponse: isNotPairedResponse,
      isExpiredResponse: isExpiredResponse,
      refresh: refresh,
      pair: pair,
      revoke: revokeDevice,
      offer: offer,
      render: render
    };
  }

  var api = {
    createPairingUI: createPairingUI,
    TOKEN_KEY: TOKEN_KEY,
    NOT_PAIRED_DETAIL: NOT_PAIRED_DETAIL,
    EXPIRED_DETAIL: EXPIRED_DETAIL
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Pairing = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
