/* Calls UI module (T5) — per-call history list and detail transcript view.
 *
 * Dependency-injected pure component:
 * - deps.doc: DOM document with createElement
 * - deps.http: async request function (url) => Promise<Response | json>
 * - deps.t: i18n translator function (key, vars) => string
 *
 * Exposes createCallsUI(deps) -> { mount(container), refresh() }.
 * NO direct window.fetch or literal UI strings.
 * Security: UNTRUSTED transcript text mounts through createElement + textContent ONLY.
 */

(function (global) {
  "use strict";

  function formatTime(isoStr) {
    if (!isoStr || typeof isoStr !== "string") return "";
    try {
      var d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
      var month = pad(d.getMonth() + 1);
      var day = pad(d.getDate());
      var hours = pad(d.getHours());
      var mins = pad(d.getMinutes());
      return day + "/" + month + " " + hours + ":" + mins;
    } catch (_) {
      return isoStr;
    }
  }

  function formatDuration(seconds) {
    if (seconds == null || isNaN(seconds)) return "";
    var s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60);
    var rem = s % 60;
    return m + "m " + (rem < 10 ? "0" : "") + rem + "s";
  }

  function unwrapJson(res) {
    if (res && typeof res.json === "function") {
      if (res.ok === false) {
        throw new Error("HTTP " + res.status);
      }
      return res.json();
    }
    return Promise.resolve(res);
  }

  function createCallsUI(deps) {
    deps = deps || {};
    var doc = deps.doc;
    var http = deps.http;
    var t = deps.t;

    if (!doc || typeof doc.createElement !== "function") {
      throw new Error("createCallsUI: deps.doc with createElement is required");
    }
    if (!http || typeof http !== "function") {
      throw new Error("createCallsUI: deps.http function is required");
    }
    if (!t || typeof t !== "function") {
      throw new Error("createCallsUI: deps.t function is required");
    }

    var container = null;
    var currentView = "list"; // "list" | "detail"
    var selectedCall = null;

    // Elements
    var root = doc.createElement("div");
    root.classList.add("calls-root");

    // List view elements
    var listView = doc.createElement("div");
    listView.classList.add("calls-list-view");

    var listHead = doc.createElement("div");
    listHead.classList.add("calls-list-header");
    var listTitle = doc.createElement("h3");
    listTitle.classList.add("calls-title");
    listTitle.textContent = t("calls.title");
    listHead.appendChild(listTitle);

    var listLoading = doc.createElement("div");
    listLoading.classList.add("calls-loading", "hidden");
    listLoading.textContent = t("calls.loading");

    var listError = doc.createElement("div");
    listError.classList.add("calls-error", "hidden");
    listError.textContent = t("calls.error");

    var listEmpty = doc.createElement("div");
    listEmpty.classList.add("calls-empty", "hidden");
    listEmpty.textContent = t("calls.empty");

    var listItemsContainer = doc.createElement("div");
    listItemsContainer.classList.add("calls-list");

    listView.appendChild(listHead);
    listView.appendChild(listLoading);
    listView.appendChild(listError);
    listView.appendChild(listEmpty);
    listView.appendChild(listItemsContainer);

    // Detail view elements
    var detailView = doc.createElement("div");
    detailView.classList.add("calls-detail-view", "hidden");

    var detailNav = doc.createElement("div");
    detailNav.classList.add("calls-detail-nav");
    var backBtn = doc.createElement("button");
    backBtn.setAttribute("type", "button");
    backBtn.classList.add("calls-back-btn");
    backBtn.textContent = t("calls.back");
    backBtn.addEventListener("click", function () {
      showList();
    });

    var detailTitle = doc.createElement("span");
    detailTitle.classList.add("calls-detail-title");
    detailTitle.textContent = t("calls.detail_title");

    detailNav.appendChild(backBtn);
    detailNav.appendChild(detailTitle);

    var detailHeader = doc.createElement("div");
    detailHeader.classList.add("calls-detail-header");

    var detailLoading = doc.createElement("div");
    detailLoading.classList.add("calls-detail-loading", "hidden");
    detailLoading.textContent = t("calls.detail_loading");

    var detailError = doc.createElement("div");
    detailError.classList.add("calls-detail-error", "hidden");
    detailError.textContent = t("calls.detail_error");

    var detailEmpty = doc.createElement("div");
    detailEmpty.classList.add("calls-detail-empty", "hidden");
    detailEmpty.textContent = t("calls.detail_empty");

    var detailTurnsContainer = doc.createElement("div");
    detailTurnsContainer.classList.add("calls-detail-turns");

    detailView.appendChild(detailNav);
    detailView.appendChild(detailHeader);
    detailView.appendChild(detailLoading);
    detailView.appendChild(detailError);
    detailView.appendChild(detailEmpty);
    detailView.appendChild(detailTurnsContainer);

    root.appendChild(listView);
    root.appendChild(detailView);

    function showList() {
      currentView = "list";
      listView.classList.remove("hidden");
      detailView.classList.add("hidden");
    }

    function showDetail(call) {
      currentView = "detail";
      selectedCall = call;
      listView.classList.add("hidden");
      detailView.classList.remove("hidden");

      // Render detail header
      while (detailHeader.children && detailHeader.children.length > 0) {
        detailHeader.removeChild(detailHeader.children[0]);
      }
      var metaTime = doc.createElement("span");
      metaTime.classList.add("call-meta-time");
      metaTime.textContent = formatTime(call.started_at);

      var metaStatus = doc.createElement("span");
      metaStatus.classList.add("call-status", "status-" + (call.status || "open"));
      metaStatus.textContent = t("calls.status_" + (call.status || "open"));

      var metaDur = doc.createElement("span");
      metaDur.classList.add("call-meta-dur");
      var durStr = formatDuration(call.duration_s);
      metaDur.textContent = durStr ? t("calls.duration", { duration: durStr }) : "";

      detailHeader.appendChild(metaTime);
      detailHeader.appendChild(metaStatus);
      if (durStr) detailHeader.appendChild(metaDur);

      fetchDetail(call.call_id);
    }

    function fetchList() {
      listLoading.classList.remove("hidden");
      listError.classList.add("hidden");
      listEmpty.classList.add("hidden");
      while (listItemsContainer.children && listItemsContainer.children.length > 0) {
        listItemsContainer.removeChild(listItemsContainer.children[0]);
      }

      http("/calls")
        .then(unwrapJson)
        .then(function (data) {
          listLoading.classList.add("hidden");
          var calls = (data && data.calls) || [];
          if (calls.length === 0) {
            listEmpty.classList.remove("hidden");
            return;
          }
          for (var i = 0; i < calls.length; i++) {
            var item = renderCallItem(calls[i]);
            listItemsContainer.appendChild(item);
          }
        })
        .catch(function (err) {
          listLoading.classList.add("hidden");
          listError.classList.remove("hidden");
        });
    }

    function renderCallItem(call) {
      var item = doc.createElement("div");
      item.classList.add("call-item");
      item.setAttribute("data-call-id", call.call_id);

      var row1 = doc.createElement("div");
      row1.classList.add("call-item-main");

      var dateEl = doc.createElement("span");
      dateEl.classList.add("call-date");
      dateEl.textContent = formatTime(call.started_at);

      var statusEl = doc.createElement("span");
      statusEl.classList.add("call-status", "status-" + (call.status || "open"));
      statusEl.textContent = t("calls.status_" + (call.status || "open"));

      row1.appendChild(dateEl);
      row1.appendChild(statusEl);

      var row2 = doc.createElement("div");
      row2.classList.add("call-item-sub");

      var turnsEl = doc.createElement("span");
      turnsEl.classList.add("call-turns");
      turnsEl.textContent = t("calls.turns_count", { count: call.turn_count || 0 });

      var durEl = doc.createElement("span");
      durEl.classList.add("call-dur");
      var durStr = formatDuration(call.duration_s);
      durEl.textContent = durStr ? t("calls.duration", { duration: durStr }) : "";

      row2.appendChild(turnsEl);
      if (durStr) row2.appendChild(durEl);

      item.appendChild(row1);
      item.appendChild(row2);

      item.addEventListener("click", function () {
        showDetail(call);
      });

      return item;
    }

    function fetchDetail(callId) {
      detailLoading.classList.remove("hidden");
      detailError.classList.add("hidden");
      detailEmpty.classList.add("hidden");
      while (detailTurnsContainer.children && detailTurnsContainer.children.length > 0) {
        detailTurnsContainer.removeChild(detailTurnsContainer.children[0]);
      }

      http("/calls/" + encodeURIComponent(callId))
        .then(unwrapJson)
        .then(function (data) {
          detailLoading.classList.add("hidden");
          var turns = (data && data.turns) || [];
          if (turns.length === 0) {
            detailEmpty.classList.remove("hidden");
            return;
          }
          for (var i = 0; i < turns.length; i++) {
            var turnEl = renderTurnItem(turns[i]);
            detailTurnsContainer.appendChild(turnEl);
          }
        })
        .catch(function (err) {
          detailLoading.classList.add("hidden");
          detailError.classList.remove("hidden");
        });
    }

    function renderTurnItem(turn) {
      var turnEl = doc.createElement("div");
      var role = turn.role === "assistant" ? "assistant" : "user";
      turnEl.classList.add("call-turn", role);

      var headEl = doc.createElement("div");
      headEl.classList.add("turn-head");

      var roleLabel = doc.createElement("span");
      roleLabel.classList.add("turn-role");
      roleLabel.textContent = t("calls.role_" + role);

      var timeLabel = doc.createElement("span");
      timeLabel.classList.add("turn-time");
      timeLabel.textContent = formatTime(turn.ts);

      headEl.appendChild(roleLabel);
      if (turn.ts) headEl.appendChild(timeLabel);

      var textEl = doc.createElement("div");
      textEl.classList.add("turn-text");
      textEl.textContent = turn.text || "";

      turnEl.appendChild(headEl);
      turnEl.appendChild(textEl);
      return turnEl;
    }

    function mount(targetContainer) {
      container = targetContainer;
      while (container.children && container.children.length > 0) {
        container.removeChild(container.children[0]);
      }
      container.appendChild(root);
      fetchList();
      return api;
    }

    function refresh() {
      if (currentView === "detail" && selectedCall) {
        fetchDetail(selectedCall.call_id);
      } else {
        fetchList();
      }
    }

    var api = {
      mount: mount,
      refresh: refresh,
      showList: showList,
      showDetail: showDetail,
    };

    return api;
  }

  var api = {
    createCallsUI: createCallsUI,
    formatTime: formatTime,
    formatDuration: formatDuration,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  global.Calls = api;
})(typeof window !== "undefined" ? window : globalThis);
