/* node --test suite for the conversation-sheet grouping (pure module, no
 * DOM, no fetch). Product decision 2026-10-15: the sheet groups by STATE
 * (bloqueado first: it is the one waiting for the human) and, inside a
 * state, activity in the last 30 minutes opens a "Últimos 30 min" subgroup
 * BEFORE the rest. A pane whose time is unknown is NEVER recent — the
 * backend sends last_turn_age_s: null for it, and guessing it into the
 * recent bucket would lie to the user.
 */

const test = require("node:test");
const assert = require("node:assert");
const {
  groupHerd,
  RECENT_WINDOW_S
} = require("../../src/herdr_brain/static/herd-group.js");

function row(pane, status, ageS) {
  return { pane_id: pane, agent_status: status, last_turn_age_s: ageS };
}

function paneIds(rows) {
  return rows.map(function (r) { return r.pane_id; });
}

test("an empty or non-list herd has no sections", () => {
  assert.deepEqual(groupHerd([]), []);
  assert.deepEqual(groupHerd(null), []);
  assert.deepEqual(groupHerd(undefined), []);
});

test("sections follow the product order: bloqueado, trabajando, listo, inactivo", () => {
  const sections = groupHerd([
    row("p-idle", "idle", 10),
    row("p-done", "done", 10),
    row("p-working", "working", 10),
    row("p-blocked", "blocked", 10)
  ]);
  assert.deepEqual(
    sections.map(function (s) { return s.key; }),
    ["blocked", "working", "done", "idle"]
  );
  assert.deepEqual(
    sections.map(function (s) { return s.label; }),
    ["Bloqueado", "Trabajando", "Listo", "Inactivo"]
  );
});

test("a state nobody is in produces no section", () => {
  const sections = groupHerd([row("p1", "working", 10)]);
  assert.deepEqual(
    sections.map(function (s) { return s.key; }),
    ["working"]
  );
});

test("an unrecognised or empty status lands in the trailing Otros section", () => {
  const sections = groupHerd([
    row("p-unknown", "unknown", 10),
    row("p-blank", "", 10),
    row("p-working", "working", 10)
  ]);
  assert.deepEqual(
    sections.map(function (s) { return s.key; }),
    ["working", "other"]
  );
  assert.equal(sections[1].label, "Otros");
  assert.deepEqual(paneIds(sections[1].subgroups[0].rows), ["p-unknown", "p-blank"]);
});

test("activity inside the window opens a labelled recent subgroup before the rest", () => {
  const sections = groupHerd([
    row("old-1", "working", 5000),
    row("new-1", "working", 60)
  ]);
  const working = sections[0];
  assert.deepEqual(
    working.subgroups.map(function (g) { return g.key; }),
    ["recent", "older"]
  );
  assert.equal(working.subgroups[0].label, "Últimos 30 min");
  assert.equal(working.subgroups[1].label, "");
  assert.deepEqual(paneIds(working.subgroups[0].rows), ["new-1"]);
  assert.deepEqual(paneIds(working.subgroups[1].rows), ["old-1"]);
});

test("the 30-minute edge is inclusive", () => {
  const sections = groupHerd([
    row("edge", "working", RECENT_WINDOW_S),
    row("over", "working", RECENT_WINDOW_S + 1)
  ]);
  const groups = sections[0].subgroups;
  assert.deepEqual(paneIds(groups[0].rows), ["edge"]);
  assert.deepEqual(paneIds(groups[1].rows), ["over"]);
});

test("an unknown age is never recent (null, missing or non-numeric)", () => {
  const sections = groupHerd([
    row("no-time", "blocked", null),
    row("missing-time", "blocked", undefined),
    row("NaN-time", "blocked", "soon")
  ]);
  const groups = sections[0].subgroups;
  assert.deepEqual(
    groups.map(function (g) { return g.key; }),
    ["older"]
  );
  assert.deepEqual(paneIds(groups[0].rows), ["no-time", "missing-time", "NaN-time"]);
});

test("a state with only stale panes has one unlabelled subgroup", () => {
  const sections = groupHerd([
    row("a", "done", 99999),
    row("b", "done", 88888)
  ]);
  assert.deepEqual(
    sections[0].subgroups.map(function (g) { return g.key; }),
    ["older"]
  );
  assert.equal(sections[0].subgroups[0].label, "");
  assert.deepEqual(paneIds(sections[0].subgroups[0].rows), ["a", "b"]);
});

test("order inside a subgroup follows the herd order (stable for keyed updates)", () => {
  const sections = groupHerd([
    row("x1", "working", 10),
    row("x2", "working", 20),
    row("x3", "working", 30)
  ]);
  assert.deepEqual(paneIds(sections[0].subgroups[0].rows), ["x1", "x2", "x3"]);
});
