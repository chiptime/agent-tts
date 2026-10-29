/* Operator dock (S3, doc 06 — the PWA half) — own conception.
 *
 * The dock renders the operator's own TOML catalog behind the FIXED
 * read contract:
 *   GET /operator/config?agent=<family> →
 *   { quick_replies:[{id,label,text}], commands:[{id,label,command}],
 *     key_presets:[{id,label,keys}], launchers:[{id,label,no_prompts}] }
 * The client NEVER sends text of its own invention beyond the row's
 * own field: a quick reply or command sends/fills through the existing
 * verified reply path (destructive guard included — the operator's
 * allowlist does not replace the guard), a key preset posts only
 * {pane_id, id} (the server resolves the chord against its rows), and
 * a launch posts only {id, confirm: true} (the client names a row, it
 * never composes a command line).
 *
 * Launchers with no_prompts start shells that may destroy state: they
 * need a SECOND tap. The first tap arms the row (client-side), the
 * second sends; a server 409 confirmation_required re-arms the row the
 * same way, and 409 cwd_missing is an honest error toast. Every label
 * from the TOML renders as a TEXT node — a label is data about a
 * button, never markup. A missing endpoint (404) keeps the dock
 * hidden: nothing to operate, nothing to show. */

(function (global) {
  "use strict";

  var ARM_TIMEOUT_MS = 3000;

  /* ---- pure row validation (the server already validated; the
   * client re-checks so a broken proxy can never mount a broken
   * button) ---- */

  function validRow(row, field) {
    if (!row || typeof row !== "object") return false;
    if (typeof row.id !== "string" || !row.id) return false;
    if (typeof row.label !== "string" || !row.label) return false;
    if (field && (typeof row[field] !== "string" || !row[field])) return false;
    return true;
  }

  function cleanSection(rows, field, extra) {
    if (!Array.isArray(rows)) return [];
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      if (!validRow(rows[i], field)) continue;
      var row = { id: rows[i].id, label: rows[i].label };
      if (field) row[field] = rows[i][field];
      if (extra) extra(row, rows[i]);
      out.push(row);
    }
    return out;
  }

  function dockModel(config) {
    if (!config || typeof config !== "object") return null;
    var model = {
      quick_replies: cleanSection(config.quick_replies, "text"),
      commands: cleanSection(config.commands, "command"),
      key_presets: cleanSection(config.key_presets, "keys"),
      launchers: cleanSection(config.launchers, null, function (out, row) {
        out.no_prompts = row.no_prompts === true;
      }),
    };
    var any = model.quick_replies.length || model.commands.length ||
      model.key_presets.length || model.launchers.length;
    return any ? model : null;
  }

  /* A command with a trailing space is an argument hint: fill the
   * composer and let the operator finish; a complete command sends. */
  function commandAction(command) {
    if (typeof command !== "string") return { fill: false, value: "" };
    return { fill: /\s$/.test(command), value: command };
  }

  function launcherTap(row, phase) {
    if (row && row.no_prompts && phase !== "armed") return { next: "armed" };
    return { next: "send" };
  }

  function launchOutcome(status, body) {
    if (status === 200) return { ok: true };
    if (status === 409 && body && body.detail === "confirmation_required") {
      return { retry: "confirmation_required" };
    }
    if (status === 409 && body && body.detail === "cwd_missing") {
      return { error: "cwd_missing" };
    }
    return { error: "http" };
  }

  /* ---- the dock itself (DOM against the injected surface) ---- */

  function createDock(deps) {
    deps = deps || {};
    var doc = deps.doc;
    var root = deps.root;
    var t = deps.t || function (key) { return key; };
    var toast = deps.toast || function () {};
    var sendReply = deps.sendReply || function () {};
    var fillComposer = deps.fillComposer || function () {};
    var post = deps.post || function () { return Promise.resolve({ status: 200, json: function () { return Promise.resolve({}); } }); };
    var show = deps.show || function () {};
    var hide = deps.hide || function () {};

    var current = null;        /* the rendered model */
    var agentKey = null;       /* "agent-family|pane" the render answers to */
    var armed = {};            /* launcher id → disarm timer */

    function button(kind, id, label, title) {
      var btn = doc.createElement("button");
      btn.type = "button";
      btn.className = "dock-btn";
      btn.setAttribute("data-row", kind + ":" + id);
      /* Text node only: operator labels are data, never markup. */
      btn.textContent = label;
      if (title) btn.title = title;
      btn.addEventListener("click", function () { onRow(kind, id); });
      return btn;
    }

    function section(name, rows, kind, titleKey) {
      if (!rows.length) return null;
      var box = doc.createElement("div");
      box.className = "dock-section dock-" + name;
      var head = doc.createElement("span");
      head.className = "dock-label";
      head.textContent = t(titleKey);
      box.appendChild(head);
      for (var i = 0; i < rows.length; i++) {
        box.appendChild(button(kind, rows[i].id, rows[i].label));
      }
      return box;
    }

    function render(model) {
      while (root.firstChild) root.removeChild(root.firstChild);
      var groups = [
        section("replies", model.quick_replies, "qr", "dock.replies"),
        section("commands", model.commands, "cmd", "dock.commands"),
        section("presets", model.key_presets, "preset", "dock.presets"),
        section("launchers", model.launchers, "launch", "dock.launchers"),
      ];
      for (var i = 0; i < groups.length; i++) {
        if (groups[i]) root.appendChild(groups[i]);
      }
    }

    function find(kind, id) {
      var list = { qr: "quick_replies", cmd: "commands", preset: "key_presets", launch: "launchers" }[kind];
      var rows = current && current[list] || [];
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].id === id) return rows[i];
      }
      return null;
    }

    function findButton(kind, id) {
      var nodes = root.querySelectorAll('button');
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].getAttribute("data-row") === kind + ":" + id) return nodes[i];
      }
      return null;
    }

    function armLauncher(row) {
      var btn = findButton("launch", row.id);
      if (!btn) return;
      btn.classList.add("dock-armed");
      btn.title = t("dock.confirm_title");
      if (armed[row.id]) global.clearTimeout(armed[row.id]);
      armed[row.id] = global.setTimeout(function () { disarmLauncher(row.id); }, ARM_TIMEOUT_MS);
    }

    function disarmLauncher(id) {
      if (armed[id]) {
        global.clearTimeout(armed[id]);
        delete armed[id];
      }
      var btn = findButton("launch", id);
      if (btn) {
        btn.classList.remove("dock-armed");
        btn.title = "";
      }
    }

    function onRow(kind, id) {
      var row = find(kind, id);
      if (!row) return;
      if (kind === "qr") {
        sendReply(row.text);
        return;
      }
      if (kind === "cmd") {
        var action = commandAction(row.command);
        if (action.fill) fillComposer(action.value);
        else sendReply(action.value);
        return;
      }
      if (kind === "preset") {
        post("/operator/preset", paneBody(id));
        return;
      }
      if (kind === "launch") {
        var plan = launcherTap(row, armed[id] ? "armed" : "idle");
        if (plan.next === "armed") {
          armLauncher(row);
          return;
        }
        disarmLauncher(id);
        post("/operator/launch", { id: row.id, confirm: true }).then(function (resp) {
          return resp.json().then(function (body) { return { status: resp.status, body: body }; });
        }).then(function (outcome) {
          var plan2 = launchOutcome(outcome.status, outcome.body);
          if (plan2.ok) return;
          if (plan2.retry === "confirmation_required") {
            armLauncher(row);
            return;
          }
          if (plan2.error === "cwd_missing") {
            toast(t("dock.cwd_missing"));
            return;
          }
          toast(t("dock.launch_failed"));
        }).catch(function () { toast(t("dock.launch_failed")); });
      }
    }

    function paneBody(presetId) {
      return { pane_id: paneId, id: presetId };
    }

    var paneId = null;

    return {
      /* Refetch on every call: the caller (app.js) decides WHEN — on a
       * family or pane change, never on the plain 5s poll. */
      update: function update(family, pane) {
        paneId = pane || null;
        var key = String(family || "");
        return deps.getConfig(key ? "/operator/config?agent=" + encodeURIComponent(key) : null)
          .then(function (config) {
            var model = dockModel(config);
            if (!model) {
              current = null;
              agentKey = key || null;
              hide();
              return false;
            }
            current = model;
            agentKey = key;
            render(model);
            show();
            return true;
          })
          .catch(function () { hide(); return false; });
      },
    };
  }

  var api = {
    createDock: createDock,
    dockModel: dockModel,
    commandAction: commandAction,
    launcherTap: launcherTap,
    launchOutcome: launchOutcome,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.OperatorDock = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
