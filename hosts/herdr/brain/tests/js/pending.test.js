/* Pending announcements store (voice-stack VS3.6, PRD03/T8).
 *
 * speech.js's createPendingStore is a pure UMD module (storage, clock,
 * id factory, callbacks injected), so its behavior is EXECUTED here
 * against a scripted localStorage double — exactly the discipline of
 * speech.test.js. The four names pinned by TASKS.md VS3.6 run first;
 * the load/roundtrip and consolidation tests below them cover the
 * remaining FR-08/FR-10 behavior (stop keeps records, resolution is
 * deliberate, metadata-only consolidation mirrors T7).
 */

const test = require("node:test");
const assert = require("node:assert");

const Speech = require("../../src/herdr_brain/static/speech.js");

/* ---- doubles ---------------------------------------------------------- */

/* localStorage double: an in-memory map whose setItem can be switched
 * to throw a real-shaped QuotaExceededError (quota / private mode). */
function scriptedStorage() {
  const store = new Map();
  const calls = { getItem: [], setItem: [], quotaFailures: 0 };
  let failSetItem = false;
  const fn = {
    getItem(key) {
      calls.getItem.push(key);
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      calls.setItem.push({ key, value });
      if (failSetItem) {
        calls.quotaFailures += 1;
        throw Object.assign(new Error("The quota has been exceeded."), {
          name: "QuotaExceededError",
        });
      }
      store.set(key, value);
    },
    failNextSets(on) { failSetItem = on; },
    dump() { return store; },
  };
  fn.calls = calls;
  return fn;
}

/* One SSE transition announcement, server-shaped (watcher.py). */
function announcement(overrides) {
  return Object.assign({
    type: "transition",
    pane_id: "pane-alpha",
    agent: "writer",
    status: "blocked",
    label: "writer",
    text: "writer necesita tu atención",
    audio_url: "/audio/ann-1.mp3",
    speech_request_id: "ann-00000000-0000-4000-8000-000000000001",
  }, overrides || {});
}

/* Store harness: deterministic clock and ids, recorded callbacks. */
function storeHarness(storage, extra) {
  const statuses = [];
  const changes = [];
  const listened = [];
  let tick = 0;
  let n = 0;
  const store = Speech.createPendingStore(Object.assign({
    storage,
    now: () => 1700000000000 + (tick++ * 1000),
    makeId: () => "rec-" + String(++n).padStart(3, "0"),
    onStatus: (s) => statuses.push(s),
    onChange: (snap) => changes.push(snap),
    onListen: (text, rec) => listened.push({ text, rec }),
  }, extra || {}));
  return { store, statuses, changes, listened };
}

/* ---- TASKS.md VS3.6 pinned names --------------------------------------- */

test("test_stop_purges_speech_keeps_records", () => {
  const storage = scriptedStorage();
  const { store } = storeHarness(storage);
  // ambient announcements the phone observed over SSE
  store.observe(announcement({ pane_id: "pane-a", status: "blocked",
    label: "writer", text: "writer necesita tu atención" }));
  store.observe(announcement({ pane_id: "pane-b", status: "done",
    label: "deva", text: "deva terminó" }));
  const before = store.snapshot();
  assert.strictEqual(before.records.length, 2);

  // the audio queue mixes a response job's segments with ambient audio
  const queue = [
    { url: "/seg0.mp3", speechRequestId: "job-00000001", seq: 0, announcement: null },
    { url: "/a1.mp3", announcement: { label: "writer", text: "writer necesita tu atención" },
      speechRequestId: null, seq: null },
    { url: "/seg1.mp3", speechRequestId: "job-00000001", seq: 1, announcement: null },
    { url: "/a2.mp3", announcement: { label: "deva", text: "deva terminó" },
      speechRequestId: null, seq: null },
  ];

  // the REAL purge — exactly what stopAudio runs for an identified job
  const removed = Speech.purgeQueueById(queue, "job-00000001");

  assert.strictEqual(removed, 2, "the job's queue items are purged");
  assert.deepStrictEqual(queue.map((i) => i.url), ["/a1.mp3", "/a2.mp3"],
    "ambient audio keeps its turn; nothing of the job is re-enqueued");
  // FR-08: the stop purged SPEECH only — the pending records are intact
  assert.deepStrictEqual(store.snapshot(), before,
    "the ledger is untouched by the stop (domains separated)");
});

test("test_actions_listen_mark_discard", () => {
  const storage = scriptedStorage();
  const { store, listened } = storeHarness(storage);
  store.observe(announcement({ text: "writer necesita tu atención: revisar el correo" }));
  const rec = store.snapshot().records[0];

  // listen hands the record's TEXT to the caller (normal pipeline)
  assert.strictEqual(store.listen(rec.id), true);
  assert.strictEqual(listened.length, 1);
  assert.strictEqual(listened[0].text, "writer necesita tu atención: revisar el correo");
  assert.ok(listened[0].rec && listened[0].rec.id === rec.id,
    "the record travels along for labeling");
  assert.strictEqual(store.listen("no-such-id"), false, "an unknown id never fires onListen");
  assert.strictEqual(listened.length, 1);
  assert.strictEqual(store.snapshot().records[0].state, "pending",
    "listening alone marks nothing — resolution is deliberate");

  // mark announced: the state is visible and persisted
  assert.strictEqual(store.mark_announced(rec.id), true);
  assert.strictEqual(store.snapshot().records[0].state, "announced");
  assert.strictEqual(store.mark_announced("no-such-id"), false);

  // discard: removes from the list AND from persistence on the next save
  assert.strictEqual(store.discard(rec.id), true);
  assert.strictEqual(store.snapshot().records.length, 0);
  const persisted = JSON.parse(storage.dump().get(Speech.PENDING_STORAGE_KEY));
  assert.deepStrictEqual(persisted.records, [], "the discarded record left storage");
  const reopened = storeHarness(storage).store;
  assert.strictEqual(reopened.snapshot().records.length, 0);
  assert.strictEqual(store.discard(rec.id), false, "discard is not repeatable");
});

test("test_overflow_count_visible", () => {
  const storage = scriptedStorage();
  const { store, statuses } = storeHarness(storage);
  for (let i = 0; i < 105; i++) {
    store.observe(announcement({ pane_id: "pane-" + i, status: "blocked",
      label: "agent-" + i, text: "aviso " + i }));
  }

  const snap = store.snapshot();
  assert.strictEqual(Speech.PENDING_MAX_RECORDS, 100);
  assert.strictEqual(snap.records.length, 100, "the list is capped at 100");
  assert.strictEqual(snap.count, 100);
  assert.strictEqual(snap.overflow_count, 5,
    "the 5 oldest beyond the cap are COUNTED — never fake-preserved, never silent");
  assert.ok(!snap.records.some((r) => r.pane_id === "pane-0"),
    "the oldest dropped record is gone from the list");
  assert.ok(snap.records.some((r) => r.pane_id === "pane-104"),
    "the newest record is present");

  const overflows = statuses.filter((s) => s.state === "overflow");
  assert.strictEqual(overflows.length, 5, "each drop is surfaced");
  assert.strictEqual(overflows[overflows.length - 1].overflow_count, 5,
    "the aggregate rides the status channel for the badge");

  const persisted = JSON.parse(storage.dump().get(Speech.PENDING_STORAGE_KEY));
  assert.strictEqual(persisted.records.length, 100);
  assert.strictEqual(persisted.overflow_count, 5,
    "the overflow aggregate persists — a reload never silently forgets it");
});

test("test_quota_failure_visible_uncertain", () => {
  const storage = scriptedStorage();
  storage.failNextSets(true);
  const { store, statuses } = storeHarness(storage);

  // QuotaExceededError on setItem: the record survives in memory…
  store.observe(announcement({ pane_id: "pane-a", text: "writer necesita tu atención" }));
  let snap = store.snapshot();
  assert.strictEqual(snap.records.length, 1, "the record stays in memory");
  assert.strictEqual(snap.records[0].persist_uncertain, true,
    "flagged: this exact state was never persisted");
  assert.strictEqual(snap.records[0].state, "uncertain",
    "visible uncertain state in the live session");
  assert.strictEqual(snap.persist_uncertain, true,
    "the snapshot exposes the aggregate for the banner slot");
  assert.ok(statuses.some((s) => s.state === "persist-uncertain"),
    "the condition is surfaced so app.js can show the honest banner");

  // an ANNOUNCED record under a failing save keeps its state; only the
  // uncertainty flag speaks (the action outcome is known in memory)
  assert.strictEqual(store.mark_announced(snap.records[0].id), true);
  let mid = store.snapshot().records[0];
  assert.strictEqual(mid.state, "announced");
  assert.strictEqual(mid.persist_uncertain, true);
  // a NEW record under the failing save lands as uncertain too
  store.observe(announcement({ pane_id: "pane-c", status: "done", text: "deva terminó" }));
  assert.strictEqual(store.snapshot().records[1].state, "uncertain");

  // a later successful save clears the flag — the list is durable again
  storage.failNextSets(false);
  store.observe(announcement({ pane_id: "pane-b", status: "done", text: "control terminó" }));
  snap = store.snapshot();
  assert.strictEqual(snap.records.length, 3);
  assert.ok(snap.records.every((r) => r.persist_uncertain === false),
    "a successful save clears the flag on every record");
  assert.strictEqual(snap.records[0].state, "announced", "the known outcome is kept");
  assert.strictEqual(snap.records[1].state, "pending",
    "uncertain degrades back to pending once the record is durable");
  assert.strictEqual(snap.persist_uncertain, false);
  assert.ok(statuses.some((s) => s.state === "persist-restored"),
    "the repair is surfaced so the banner can hide");
  const persisted = JSON.parse(storage.dump().get(Speech.PENDING_STORAGE_KEY));
  assert.ok(persisted.records.every((r) => r.persist_uncertain !== true),
    "the durable payload never carries the uncertainty flag");

  // storage entirely absent/denied: the same visible condition, no crash
  const bare = Speech.createPendingStore({});
  bare.observe(announcement({ text: "sin almacenamiento" }));
  assert.strictEqual(bare.snapshot().records[0].persist_uncertain, true);
  assert.strictEqual(bare.snapshot().records[0].state, "uncertain");
});

/* ---- load / roundtrip --------------------------------------------------- */

test("records load back from localStorage: roundtrip, exact key, honest on corruption", () => {
  const storage = scriptedStorage();
  const a = storeHarness(storage);
  a.store.observe(announcement({ pane_id: "pane-a", status: "blocked",
    text: "writer necesita tu atención" }));
  a.store.observe(announcement({ pane_id: "pane-b", status: "done", text: "deva terminó: listo" }));
  a.store.mark_announced(a.store.snapshot().records[1].id);

  assert.strictEqual(Speech.PENDING_STORAGE_KEY, "herdr.speech.pending.v1");
  assert.ok(storage.calls.setItem.length > 0);
  assert.ok(storage.calls.setItem.every((c) => c.key === Speech.PENDING_STORAGE_KEY),
    "the locked storage key is the only one touched");

  // reload: a fresh store over the same storage restores the records
  const b = storeHarness(storage).store;
  const snap = b.snapshot();
  assert.strictEqual(snap.records.length, 2);
  assert.deepStrictEqual(snap.records.map((r) => [r.pane_id, r.status, r.state]),
    [["pane-a", "blocked", "pending"], ["pane-b", "done", "announced"]]);
  assert.strictEqual(snap.records[0].text, "writer necesita tu atención");
  assert.strictEqual(snap.records[0].persist_uncertain, false,
    "a loaded record is durable by construction");
  // ids survive the roundtrip, so actions taken after a reload target
  // the very records the user sees
  assert.deepStrictEqual(snap.records.map((r) => r.id),
    a.store.snapshot().records.map((r) => r.id));

  // legacy/hand-written payloads are sanitized, never trusted blindly
  const odd = scriptedStorage();
  odd.dump().set(Speech.PENDING_STORAGE_KEY, JSON.stringify({
    records: [
      { id: "x1", pane_id: "p1", status: "blocked", text: "ok", state: "uncertain" },
      { id: "x2", pane_id: "p2", status: "done", state: "discarded", text: 123 },
      { pane_id: "p3", status: "done", text: "sin id" },
      { id: "x4", text: 5 },  // every identifying field missing/non-string
      null,
    ],
    overflow_count: 2,
  }));
  const c = storeHarness(odd).store;
  const cs = c.snapshot();
  assert.strictEqual(cs.records.length, 4);
  assert.deepStrictEqual(cs.records.map((r) => r.state), ["pending", "pending", "pending", "pending"],
    "uncertain/discarded never survive a reload — resolution stays deliberate");
  assert.strictEqual(cs.records[1].text, "", "non-string text degrades to empty");
  assert.deepStrictEqual([cs.records[3].pane_id, cs.records[3].status, cs.records[3].label],
    ["", "", ""], "missing fields degrade to empty strings");
  assert.ok(cs.records[2].id, "a missing id is minted on load");
  assert.strictEqual(cs.overflow_count, 2, "the aggregate rides along");

  // an oversized payload is honestly accounted at load, not silently kept
  const big = scriptedStorage();
  const rows = [];
  for (let i = 0; i < 103; i++) {
    rows.push({ id: "y" + i, pane_id: "p" + i, status: "blocked", text: "t" + i });
  }
  big.dump().set(Speech.PENDING_STORAGE_KEY, JSON.stringify({ records: rows }));
  const d = storeHarness(big);
  const ds = d.store.snapshot();
  assert.strictEqual(ds.records.length, 100);
  assert.strictEqual(ds.overflow_count, 3, "capped at load with visible accounting");
  assert.ok(d.statuses.some((s) => s.state === "overflow"));

  // corrupt JSON, a non-record payload and unreadable storage (private
  // mode): an empty, working store — never a crash
  const corrupt = scriptedStorage();
  corrupt.dump().set(Speech.PENDING_STORAGE_KEY, "{not json");
  assert.strictEqual(storeHarness(corrupt).store.snapshot().records.length, 0);
  const notRecords = scriptedStorage();
  notRecords.dump().set(Speech.PENDING_STORAGE_KEY, "{}");
  assert.strictEqual(storeHarness(notRecords).store.snapshot().records.length, 0);
  const unreadable = scriptedStorage();
  unreadable.getItem = () => { throw new Error("SecurityError"); };
  assert.strictEqual(storeHarness(unreadable).store.snapshot().records.length, 0);
  const fresh = storeHarness(scriptedStorage());
  assert.strictEqual(fresh.store.snapshot().records.length, 0);
  assert.strictEqual(fresh.store.snapshot().overflow_count, 0);

  // garbage intake is ignored; a record without text still lands (the
  // label/pane carry the row); non-string fields degrade to empty
  assert.strictEqual(fresh.store.observe(null), null);
  assert.strictEqual(fresh.store.observe("transition"), null);
  fresh.store.observe({ pane_id: "p", status: "s", label: "agente" });
  assert.strictEqual(fresh.store.snapshot().records.length, 1);
  assert.strictEqual(fresh.store.snapshot().records[0].text, "");
  fresh.store.observe({ pane_id: 7, status: 8, agent: 9, label: 10, text: "campos rotos" });
  const malformed = fresh.store.snapshot().records[1];
  assert.deepStrictEqual([malformed.pane_id, malformed.status, malformed.agent, malformed.label],
    ["", "", "", ""],
    "non-string fields degrade to empty — the key consolidation never crashes");
  assert.strictEqual(malformed.text, "campos rotos", "the honest string still lands");
});

/* ---- consolidation (mirrors T7: metadata-only) -------------------------- */

test("consolidation is metadata-only: same pane+status in-session => repeat_count++, no new record", () => {
  const storage = scriptedStorage();
  const { store } = storeHarness(storage);
  store.observe(announcement({ pane_id: "pane-a", status: "blocked",
    text: "primer aviso", label: "writer" }));
  store.observe(announcement({ pane_id: "pane-a", status: "blocked",
    text: "segundo aviso", label: "writer" }));
  store.observe(announcement({ pane_id: "pane-a", status: "blocked",
    text: "tercer aviso", label: "writer" }));

  const snap = store.snapshot();
  assert.strictEqual(snap.records.length, 1, "no new record for the same key");
  assert.strictEqual(snap.records[0].repeat_count, 3);
  assert.strictEqual(snap.records[0].text, "tercer aviso",
    "the latest observation refreshes the text listen() will regenerate");
  assert.ok(snap.records[0].last_seen_ts > snap.records[0].first_seen_ts,
    "last_seen advances with each re-observation");

  // a different status (or pane) is a DIFFERENT record
  store.observe(announcement({ pane_id: "pane-a", status: "done", text: "writer terminó" }));
  store.observe(announcement({ pane_id: "pane-b", status: "blocked",
    text: "deva necesita atención" }));
  assert.strictEqual(store.snapshot().records.length, 3);

  // bounded text: a pathological announcement stores a bounded prefix
  store.observe(announcement({ pane_id: "pane-c", status: "blocked",
    text: "x".repeat(Speech.PENDING_TEXT_MAX + 500) }));
  const bounded = store.snapshot().records[3].text;
  assert.strictEqual(bounded.length, Speech.PENDING_TEXT_MAX);
  assert.ok(bounded.endsWith("…"), "the bound is visible, not a silent crop");

  // a discarded record's key is free again: the next occurrence of the
  // same pane+status is a fresh record (the user resolved the old one)
  store.discard(store.snapshot().records[0].id);
  store.observe(announcement({ pane_id: "pane-a", status: "blocked", text: "reincidencia" }));
  const after = store.snapshot();
  assert.strictEqual(after.records.length, 4);
  assert.strictEqual(after.records[3].repeat_count, 1);

  // a NEW session (reopened store) consolidates only against its own
  // observations: a fresh occurrence after reload is a fresh record
  const reopened = storeHarness(storage).store;
  reopened.observe(announcement({ pane_id: "pane-a", status: "blocked",
    text: "aviso tras recarga" }));
  const rs = reopened.snapshot();
  assert.strictEqual(rs.records.length, 5);
  assert.strictEqual(rs.records[rs.records.length - 1].repeat_count, 1);
});

/* ---- injected-callback plumbing ---------------------------------------- */

test("every mutation re-renders through onChange; defaults work without injected clock/ids", () => {
  const storage = scriptedStorage();
  const h = storeHarness(storage);
  assert.strictEqual(h.changes.length, 0);
  h.store.observe(announcement({ text: "primer cambio" }));
  assert.strictEqual(h.changes.length, 1, "append fires onChange with the snapshot");
  assert.strictEqual(h.changes[0].records[0].text, "primer cambio");
  h.store.mark_announced(h.changes[0].records[0].id);
  assert.strictEqual(h.changes.length, 2);
  assert.strictEqual(h.changes[1].records[0].state, "announced");
  h.store.observe(announcement({ pane_id: "pane-a", status: "blocked", text: "consolidado" }));
  assert.strictEqual(h.changes.length, 3, "consolidation fires onChange too");
  h.store.discard(h.changes[1].records[0].id);
  assert.strictEqual(h.changes.length, 4);
  assert.strictEqual(h.changes[3].records.length, 1);
  // listen is not a mutation of the ledger: no change event
  h.store.listen(h.changes[3].records[0].id);
  assert.strictEqual(h.changes.length, 4);

  // defaults: Date.now + internal ids, no onStatus/onChange/onListen
  const bare = Speech.createPendingStore({ storage: scriptedStorage() });
  const created = bare.observe(announcement({ text: "sin reloj inyectado" }));
  assert.ok(created && typeof created.id === "string" && created.id);
  assert.ok(created.first_seen_ts <= Date.now() + 1000);
  assert.strictEqual(bare.snapshot().records.length, 1);
  assert.strictEqual(bare.listen(created.id), true,
    "listen without an onListen dep still resolves the action");

  // zero-arg construction (storage missing entirely): alive, uncertain, no crash
  const none = Speech.createPendingStore();
  none.observe(announcement({ text: "sin deps en absoluto" }));
  assert.strictEqual(none.snapshot().records[0].persist_uncertain, true);
});
