/* Conversation-sheet grouping (2026-10-15, product decision).
 *
 * Pure UMD module following the consult.js/toast.js house pattern: it
 * touches NO browser global and NO DOM, so the Node suite drives it with
 * plain objects and the sheet's renderer stays the only place that owns
 * nodes.
 *
 * Why two levels. The sheet used to be a flat list, which made the one
 * agent that is WAITING FOR THE HUMAN as hard to spot as the seven that
 * are idle. So the first level is the agent state, ordered by urgency
 * (bloqueado -> trabajando -> listo -> inactivo); the second level is
 * recency, and it only exists where it carries information: activity in
 * the last 30 minutes opens a "Últimos 30 min" subgroup BEFORE the rest.
 *
 * An undefined state ("unknown" is what herdr's parser defaults to, "" is
 * what a partially populated payload yields) is NOT dropped: it goes to a
 * trailing "Otros" section, because a silently missing row in a session
 * switcher looks like a bug.
 *
 * An unknown time is never recent. The backend sends last_turn_age_s:
 * null for a pane whose transcript is unreadable or absent, and bucketing
 * that into "Últimos 30 min" would assert something we do not know.
 */

(function (root) {
  "use strict";

  /* One product threshold, exported so the header text can never drift
   * from the rule that fills it. */
  var RECENT_WINDOW_S = 30 * 60;

  var RECENT_LABEL = "Últimos 30 min";

  /* Section order = urgency, not alphabet. "other" is the trailing
   * catch-all for a state this build does not know. */
  var SECTIONS = [
    { key: "blocked", label: "Bloqueado" },
    { key: "working", label: "Trabajando" },
    { key: "done", label: "Listo" },
    { key: "idle", label: "Inactivo" },
    { key: "other", label: "Otros" }
  ];

  /* i18n (S4): the page injects t() after boot; without it the
   * built-in Spanish labels are the single source (node suite). */
  var translator = null;

  function useTranslator(tFn) {
    translator = typeof tFn === "function" ? tFn : null;
  }

  function tr(key, fallback) {
    if (!translator) return fallback;
    var value = translator(key);
    return value && value !== key ? value : fallback;
  }

  function labels() {
    return {
      recent: tr("herd.recent", RECENT_LABEL),
      blocked: tr("herd.g_blocked", "Bloqueado"),
      working: tr("herd.g_working", "Trabajando"),
      done: tr("herd.g_done", "Listo"),
      idle: tr("herd.g_idle", "Inactivo"),
      other: tr("herd.g_other", "Otros"),
    };
  }
  var OTHER_KEY = "other";

  function sectionKeyOf(agent) {
    var status = String((agent && agent.agent_status) || "").toLowerCase();
    for (var i = 0; i < SECTIONS.length; i++) {
      if (SECTIONS[i].key === status && SECTIONS[i].key !== OTHER_KEY) {
        return status;
      }
    }
    return OTHER_KEY;
  }

  function isRecent(agent) {
    var age = agent && agent.last_turn_age_s;
    return typeof age === "number" && isFinite(age) && age <= RECENT_WINDOW_S;
  }

  /* Buckets the herd into ordered sections -> ordered subgroups -> rows.
   * Row order inside a subgroup is the herd's own order (a stable input
   * order is what lets the renderer keep reusing one node per pane_id
   * across the 5 s poll without swallowing a tap or resetting scroll). */
  function groupHerd(herd) {
    if (!Array.isArray(herd) || !herd.length) return [];

    var buckets = {};
    for (var i = 0; i < herd.length; i++) {
      var agent = herd[i];
      var key = sectionKeyOf(agent);
      if (!buckets[key]) buckets[key] = { recent: [], older: [] };
      (isRecent(agent) ? buckets[key].recent : buckets[key].older).push(agent);
    }

    var sections = [];
    for (var s = 0; s < SECTIONS.length; s++) {
      var section = SECTIONS[s];
      var bucket = buckets[section.key];
      if (!bucket) continue;
      var subgroups = [];
      if (bucket.recent.length) {
        subgroups.push({ key: "recent", label: labels().recent, rows: bucket.recent });
      }
      if (bucket.older.length) {
        /* Empty label: the renderer draws a header only when there is
         * one, so "the rest" stays a continuation of the same state. */
        subgroups.push({ key: "older", label: "", rows: bucket.older });
      }
      sections.push({ key: section.key, label: labels()[section.key] || section.label, subgroups: subgroups });
    }
    return sections;
  }

  var api = {
    groupHerd: groupHerd,
    RECENT_WINDOW_S: RECENT_WINDOW_S,
    RECENT_LABEL: RECENT_LABEL,
    useTranslator: useTranslator,
    SECTIONS: SECTIONS
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.HerdGroup = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
