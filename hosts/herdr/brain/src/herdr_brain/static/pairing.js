/* herdr-brain PWA device pairing (Panel parity F1, doc 01 §6).
 *
 * The phone's half of pairing: spend the one-time code the operator
 * reads off `herdr-brain pair`, keep the returned bearer token in
 * localStorage and attach it as `Authorization: Bearer` on every write
 * fetch (app.js wires authHeaders into its wrappers). The code is
 * minted on the TERMINAL, never in this UI — a "pair this device"
 * button would be authorised by nothing.
 *
 * Pure DOM module in the toast.js/approval.js shape: no dependencies,
 * every seam (request, storage, doc, openSettings, onPaired) injected
 * so the node --test suite drives it with a fake DOM. UI copy matches
 * the PWA's Spanish. */
(function (global) {
  "use strict";

  var TOKEN_KEY = "herdr_brain_device_token";
  /* The exact 403 detail the server's pairing gate answers with
   * (access.py DEVICE_NOT_PAIRED). Byte-compared so a "not paired"
   * refusal can be told apart from "host not allowed" — only the first
   * one should open the pairing flow. */
  var NOT_PAIRED_DETAIL = "dispositivo no emparejado";

  function createPairingUI(options) {
    var request = options.request;
    var storage = options.storage;
    var doc = options.doc || document;
    var openSettings = options.openSettings || function () {};
    var onPaired = options.onPaired || function () {};

    var state = { enforced: false, current: null, devices: [] };
    var offerPending = false;

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

      var status = el("div", "pair-status");
      if (state.current) {
        status.textContent = "📱 Emparejado como «" + state.current + "»";
      } else if (state.enforced) {
        status.textContent = "🔒 Escritura bloqueada: dispositivo no emparejado";
      } else {
        status.textContent = "Sin emparejar (la escritura sigue abierta)";
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
       * (the "Desvincular" row) is how a device un-pairs. */
      var list = el("div", "pair-devices");
      (state.devices || []).forEach(function (device) {
        var row = el("div", "pair-device");
        row.appendChild(el("span", "pair-device-label", device.label +
          (device.current ? " (este dispositivo)" : "")));
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
          if (!resp.ok) return null;  /* an older brain predates pairing */
          return resp.json();
        })
        .then(function (data) {
          if (!data) return null;
          state = {
            enforced: !!data.enforced,
            current: data.current || null,
            devices: data.devices || []
          };
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
          }
          render();
          return true;
        });
      }).catch(function () { return false; });
    }

    /* Offer the pairing flow when a write came back "not paired": open
     * Ajustes and say what to do next (doc 01 §6). */
    function offer() {
      offerPending = true;
      openSettings();
      render();
    }

    return {
      token: token,
      isPaired: isPaired,
      authHeaders: authHeaders,
      isNotPairedResponse: isNotPairedResponse,
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
    NOT_PAIRED_DETAIL: NOT_PAIRED_DETAIL
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.Pairing = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
