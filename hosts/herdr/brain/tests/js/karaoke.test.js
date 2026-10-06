"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Toast = require("../../src/herdr_brain/static/toast.js");
const Karaoke = require("../../src/herdr_brain/static/karaoke.js");
const STATIC = path.join(__dirname, "../../src/herdr_brain/static");

function verifyPlan(text) {
  const plan = Karaoke.createPlan("turn-1", text);
  const pieces = Toast.splitForTts(text, 8000);
  assert.equal(plan.turnId, "turn-1");
  assert.equal(plan.rawText, text);
  assert.deepEqual(plan.pieces.map(piece => piece.text), pieces);
  let offset = 0;
  let cursor = 0;
  for (const [pieceIndex, piece] of plan.pieces.entries()) {
    const windows = Toast.chunkifyText(pieces[pieceIndex]);
    assert.equal(piece.pieceIndex, pieceIndex);
    assert.equal(piece.chunkOffset, offset);
    assert.deepEqual(piece.chunks, windows);
    for (const [localIndex, popupText] of windows.entries()) {
      const chunk = plan.chunks[offset + localIndex];
      assert.deepEqual({ ...chunk, raw: undefined }, {
        globalIndex: offset + localIndex, ordinal: offset + localIndex + 1,
        pieceIndex, localIndex, popupText, raw: undefined
      });
      assert.equal(chunk.raw[0], cursor, "raw ranges are contiguous");
      assert.ok(chunk.raw[1] > cursor && chunk.raw[1] <= text.length);
      const raw = text.slice(...chunk.raw);
      assert.equal(raw.replace(/\s+/g, " ").trim(), popupText);
      cursor = chunk.raw[1];
    }
    offset += windows.length;
  }
  assert.equal(plan.chunks.length, offset);
  if (plan.chunks.length) {
    assert.equal(cursor, text.length);
    assert.equal(plan.chunks.map(chunk => text.slice(...chunk.raw)).join(""), text);
  } else {
    assert.equal(text.trim(), "", "only whitespace may have no audio windows");
  }
  return plan;
}

const rawCases = [
  ["empty answer", ""],
  ["whitespace-only answer", " \t\n\r\u00a0  "],
  ["one normalized window", "  short\tanswer\n  "],
  ["one overlong unbroken window", "word".repeat(100)],
  ["repeated equal substrings", "Echo.\tEcho.\n".repeat(40)],
  ["tabs, newlines and Unicode whitespace", "  first\tline\n\nsecond  line;\u00a0".repeat(20)],
  ["literal code and HTML", ' \n<script>throw new Error("literal");</script>\n```js\nconst x = "<b>";\n```\n'.repeat(4)],
  ["Unicode surrogate pairs and combining marks", "  😀🧠 cafe\u0301.\t\n".repeat(100)],
  ["exactly 8000 UTF-16 units", "x".repeat(8000)],
  ["8001 UTF-16 units", "x".repeat(8001)],
  ["hard split inside a surrogate pair", "x".repeat(7999) + "😀end"],
  ["multiple pieces with trimmed separators", " \n" + "Repeated sentence.\t \n".repeat(1200) + " \t"]
];

for (const [name, text] of rawCases) {
  test("plan preserves " + name, () => verifyPlan(text));
}

test("inter-window whitespace belongs to the preceding raw range", () => {
  const text = " \t" + "A".repeat(79) + ".\n\t  " + "B".repeat(78) + "! \n";
  const plan = verifyPlan(text);
  assert.equal(plan.chunks.length, 2);
  assert.deepEqual(plan.chunks[0].raw, [0, text.indexOf("B")]);
  assert.deepEqual(plan.chunks[1].raw, [text.indexOf("B"), text.length]);
});

test("piece separators belong to the preceding window and ordinals restart per turn", () => {
  const text = " \t" + "x".repeat(7998) + ".\n\t  tail \n";
  const plan = verifyPlan(text);
  assert.equal(plan.pieces.length, 2);
  assert.equal(plan.chunks[0].raw[1], text.indexOf("tail"));
  assert.equal(plan.chunks[1].ordinal, 2);
  assert.equal(Karaoke.createPlan("other-turn", "tail").chunks[0].ordinal, 1);
});

test("plan invokes the real splitters once, in split-then-window order", () => {
  const calls = [];
  const helpers = {
    splitForTts(text, limit) {
      calls.push(["split", text, limit]);
      return Toast.splitForTts(text, limit);
    },
    chunkifyText(piece) {
      calls.push(["window", piece]);
      return Toast.chunkifyText(piece);
    }
  };
  const text = "text. ".repeat(3000);
  const plan = Karaoke.createPlan("counted", text, helpers);
  assert.deepEqual(calls, [["split", text, 8000],
    ...plan.pieces.map(piece => ["window", piece.text])]);
});

test("plan and every nested record are immutable", () => {
  const plan = Karaoke.createPlan("immutable", "literal text");
  assert.equal(plan.pieces.length, 1);
  assert.equal(plan.chunks.length, 1);
  const records = [plan, plan.pieces, plan.chunks, plan.pieces[0],
    plan.pieces[0].chunks, plan.chunks[0], plan.chunks[0].raw];
  for (const record of records) assert.ok(Object.isFrozen(record));
  assert.throws(() => { plan.rawText = "changed"; }, TypeError);
  assert.throws(() => { plan.chunks[0].raw[1] = 0; }, TypeError);
});

test("unmappable splitter output stops with an explicit A-01 error", () => {
  const helpers = { splitForTts: Toast.splitForTts,
    chunkifyText: piece => Toast.chunkifyText(piece).map(text => "wrong " + text) };
  assert.throws(() => Karaoke.createPlan("bad-map", "original", helpers),
    { code: "KARAOKE_RAW_MAPPING" });
});

test("plan rejects missing owner identity and non-string turn text", () => {
  assert.throws(() => Karaoke.createPlan("", "text"), TypeError);
  assert.throws(() => Karaoke.createPlan("turn", null), TypeError);
});

test("browser-global export composes the actual Toast module without a DOM", () => {
  const context = vm.createContext({ window: {} });
  vm.runInContext(fs.readFileSync(path.join(STATIC, "toast.js"), "utf8"), context);
  vm.runInContext(fs.readFileSync(path.join(STATIC, "karaoke.js"), "utf8"), context);
  const plan = context.window.Karaoke.createPlan("browser-turn", "  literal\ttext  ");
  assert.equal(plan.chunks.length, 1);
  assert.equal(plan.chunks[0].popupText, "literal text");
  assert.equal(plan.rawText.slice(...plan.chunks[0].raw), plan.rawText);
});

/* Event-driven doubles: no DOM, real audio, timers, stores, or network. */
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function drain() {
  for (let i = 0; i < 12; i++) await Promise.resolve();
}

function response(url = "/audio/fixture.mp3") {
  return { ok: true, status: 200, json: async () => ({ audio_url: url }) };
}

function harness(options = {}) {
  const h = { requests: [], loads: [], progress: [], failures: [], states: [], aborts: [] };
  const foreign = { url: "/audio/announcement.mp3", announcement: true };
  h.queue = [foreign];
  h.foreign = foreign;
  const deps = {
    fetch(url, init) {
      const pending = deferred();
      h.requests.push({ url, init, ...pending });
      return pending.promise;
    },
    player: {
      load(item, events) {
        if (options.loadError) throw new Error("load failed");
        let time = 0;
        const audio = {
          item, events, seeks: [], plays: [], pauses: 0, disposals: 0,
          startAtZero: options.startAtZero === true,
          duration: Object.hasOwn(options, "duration") ? options.duration : 100,
          get currentTime() { return time; },
          set currentTime(value) {
            if (options.seekError) throw new Error("seek failed");
            time = value;
            this.seeks.push(value);
          },
          play() {
            if (options.playError) throw new Error("play failed");
            const pending = deferred();
            this.plays.push(pending);
            if (!options.pendingPlay) pending.resolve();
            return pending.promise;
          },
          pause() { this.pauses++; },
          dispose() {
            this.disposals++;
            h.queue = h.queue.filter(queued => queued !== item);
          },
          tick(value) { time = value; events.timeupdate(); }
        };
        h.queue.push(item);
        h.loads.push(audio);
        if (options.onLoad) options.onLoad(audio, h);
        return audio;
      }
    },
    onProgress(event) {
      h.progress.push(event);
      if (options.onProgress) options.onProgress(event, h);
    },
    onFailure(event) {
      h.failures.push(event);
      if (options.onFailure) options.onFailure(event, h);
    },
    onState(state) {
      h.states.push(state);
      if (options.onState) options.onState(state, h);
    }
  };
  if (options.abort !== false) {
    deps.createAbortController = () => {
      const controller = { signal: { aborted: false },
        abort() { this.signal.aborted = true; } };
      h.aborts.push(controller);
      return controller;
    };
  }
  h.ctl = Karaoke.createController(deps);
  h.select = (plan, index = 0) => assert.equal(h.ctl.select(plan, index), true);
  h.ready = async (plan, index = 0) => {
    h.select(plan, index);
    assert.equal(h.requests.length, 1, "selection starts one injected fetch");
    h.requests[0].resolve(response());
    await drain();
    assert.equal(h.loads.length, 1, "matching synthesis loads owned audio");
    return h.loads[0];
  };
  return h;
}

const windowsPlan = () => Karaoke.createPlan("windows", "A sentence with several words. ".repeat(12));
const longPlan = () => Karaoke.createPlan("long", "A sentence with several words. \t\n".repeat(1000));

function assertIdle(h) {
  const state = h.ctl.state();
  assert.equal(state.phase, "idle");
  assert.equal(state.ownerTurnId, null);
  assert.equal(state.activeGlobalIndex, null);
  assert.equal(state.pendingTarget, null);
  assert.equal(state.plan, null);
}

test("selection is immediate but synthesis does not claim playback", () => {
  const h = harness();
  const plan = windowsPlan();
  h.select(plan, 2);
  const state = h.ctl.state();
  assert.equal(state.phase, "preparing");
  assert.equal(state.ownerTurnId, plan.turnId);
  assert.equal(state.activeGlobalIndex, 2);
  assert.equal(state.pendingTarget, 2);
  assert.equal(state.plan, plan);
  assert.ok(Object.isFrozen(state));
  assert.equal(h.progress.at(-1).chunk, plan.chunks[2]);
  assert.equal(h.requests[0].url, "/tts");
  assert.equal(h.requests[0].init.method, "POST");
  assert.deepEqual(JSON.parse(h.requests[0].init.body), { text: plan.pieces[0].text });
  assert.equal(h.loads.length, 0);
});

test("ready audio seeks proportionally and enters playing only on play success", async () => {
  const h = harness({ pendingPlay: true });
  const plan = windowsPlan();
  const audio = await h.ready(plan, 2);
  assert.deepEqual(audio.seeks, [2 / plan.pieces[0].chunks.length * 100]);
  assert.equal(h.ctl.state().phase, "awaiting_metadata");
  audio.plays[0].resolve();
  await drain();
  assert.equal(h.ctl.state().phase, "playing");
  assert.equal(h.ctl.state().pendingTarget, null);
  assert.equal(audio.item.plan, plan);
  assert.equal(audio.item.ownerTurnId, plan.turnId);
  assert.equal(audio.item.generation, h.ctl.state().generation);
  assert.equal(audio.item.pieceIndex, 0);
});

test("jump synthesizes only the selected piece using its local seek index", async () => {
  const h = harness();
  const plan = longPlan();
  const piece = plan.pieces[1];
  const target = piece.chunkOffset + 2;
  const audio = await h.ready(plan, target);
  assert.deepEqual(JSON.parse(h.requests[0].init.body), { text: piece.text });
  assert.deepEqual(audio.seeks, [2 / piece.chunks.length * 100]);
  assert.equal(audio.item.pieceIndex, 1);
  assert.equal(h.ctl.state().activeGlobalIndex, target);
});

for (const duration of [0, -1, NaN, Infinity, undefined, "100"]) {
  test("invalid duration waits without seeking or playing: " + String(duration), async () => {
    const h = harness({ duration, pendingPlay: true });
    const plan = windowsPlan();
    const audio = await h.ready(plan, 1);
    assert.equal(h.ctl.state().phase, "awaiting_metadata");
    assert.deepEqual(audio.seeks, []);
    assert.equal(audio.plays.length, 0);
    audio.events.loadedmetadata();
    audio.events.durationchange();
    assert.equal(audio.plays.length, 0);
    audio.duration = 60;
    audio.events.durationchange();
    audio.events.loadedmetadata();
    assert.deepEqual(audio.seeks, [1 / plan.pieces[0].chunks.length * 60]);
    assert.equal(audio.plays.length, 1, "duplicate metadata cannot request play twice");
    assert.equal(h.ctl.state().phase, "awaiting_metadata");
    audio.plays[0].resolve();
    await drain();
    assert.equal(h.ctl.state().phase, "playing");
  });
}

test("progress uses one clamped global index for both consumers", async () => {
  const h = harness();
  const plan = longPlan();
  const piece = plan.pieces[1];
  const audio = await h.ready(plan, piece.chunkOffset);
  for (const time of [-5, 0, 1, 49, 99, 100, 200]) {
    audio.tick(time);
    const expected = piece.chunkOffset + Math.max(0,
      Math.min(piece.chunks.length - 1, Math.floor(time / 100 * piece.chunks.length)));
    assert.equal(h.ctl.state().activeGlobalIndex, expected);
    const event = h.progress.at(-1);
    assert.equal(event.activeGlobalIndex, expected);
    assert.equal(event.chunk, plan.chunks[expected]);
    assert.equal(event.chunk.popupText, piece.chunks[expected - piece.chunkOffset]);
  }
  const count = h.progress.length;
  for (const time of [NaN, Infinity, undefined, "50"]) audio.tick(time);
  audio.duration = 0;
  audio.tick(10);
  assert.equal(h.progress.length, count, "invalid media numbers cannot repaint");
});

test("one-window pieces still report progress and advance across piece boundaries", async () => {
  const h = harness();
  const plan = Karaoke.createPlan("one-window-pieces", "x".repeat(8001));
  const audio = await h.ready(plan);
  const count = h.progress.length;
  audio.tick(99);
  assert.equal(h.progress.length, count + 1, "one window is not a progress exemption");
  assert.equal(h.progress.at(-1).activeGlobalIndex, 0);
  const generation = h.ctl.state().generation;
  audio.events.ended();
  assert.equal(h.ctl.state().activeGlobalIndex, 1);
  assert.equal(h.ctl.state().generation, generation);
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(response("/audio/second.mp3"));
  await drain();
  h.loads[1].tick(50);
  assert.equal(h.progress.at(-1).activeGlobalIndex, 1);
  assert.equal(h.progress.at(-1).chunk, plan.chunks[1]);
  h.loads[1].events.ended();
  assertIdle(h);
  assert.equal(h.progress.at(-1).chunk, null);
});

test("same-piece selection and paused resume reuse session audio, not text identity", async () => {
  const h = harness();
  const plan = windowsPlan();
  const old = await h.ready(plan, 1);
  assert.equal(h.ctl.pause(), true);
  assert.equal(h.ctl.state().phase, "paused");
  assert.equal(old.pauses, 1);
  const generation = h.ctl.state().generation;
  h.select(plan, 3);
  await drain();
  assert.equal(h.requests.length, 1);
  assert.equal(h.loads.length, 2);
  assert.equal(h.loads[1].item.url, old.item.url);
  assert.deepEqual(h.loads[1].seeks, [3 / plan.pieces[0].chunks.length * 100]);
  assert.equal(h.ctl.state().phase, "playing");
  assert.ok(h.ctl.state().generation > generation);
  assert.equal(old.disposals, 1);
  const events = h.progress.length;
  old.tick(90);
  old.events.ended();
  old.events.error(new Error("obsolete source"));
  assert.equal(h.progress.length, events);
  assert.equal(h.failures.length, 0);
  assert.deepEqual(h.queue, [h.foreign, h.loads[1].item]);
});

test("Escuchar replay selects index zero through the shared plan", async () => {
  const h = harness();
  const plan = windowsPlan();
  await h.ready(plan, 3);
  assert.equal(h.ctl.replay(plan), true);
  await drain();
  assert.equal(h.ctl.state().activeGlobalIndex, 0);
  assert.deepEqual(h.loads[1].seeks, [0]);
  assert.equal(h.requests.length, 1);
});

for (const action of ["stop", "reset", "detach"]) {
  test(action + " retires pending fetch and never revives selection", async () => {
    const h = harness();
    const plan = windowsPlan();
    h.select(plan, 1);
    const generation = h.ctl.state().generation;
    h.ctl[action](plan.turnId);
    assertIdle(h);
    assert.ok(h.ctl.state().generation > generation);
    assert.equal(h.requests[0].init.signal.aborted, true);
    assert.equal(h.progress.at(-1).chunk, null);
    h.requests[0].resolve(response());
    await drain();
    assert.equal(h.loads.length, 0);
    assert.equal(h.failures.length, 0);
    assert.deepEqual(h.queue, [h.foreign]);
  });
}

test("detaching a different turn cannot retire the current owner", () => {
  const h = harness();
  const plan = windowsPlan();
  h.select(plan);
  const before = h.ctl.state();
  h.ctl.detach("unrelated");
  assert.deepEqual(h.ctl.state(), before);
});

test("stale synthesis is rejected even when abort is unavailable", async () => {
  const h = harness({ abort: false });
  h.select(windowsPlan());
  h.ctl.stop();
  h.requests[0].resolve(response());
  await drain();
  assertIdle(h);
  assert.equal(h.loads.length, 0);
  assert.equal(h.failures.length, 0);
});

test("A to B to A with equal text is generation-owned, never text-cached", async () => {
  const h = harness();
  const a = Karaoke.createPlan("A", "same answer");
  const b = Karaoke.createPlan("B", "same answer");
  h.select(a);
  const first = h.ctl.state().generation;
  h.select(b);
  const second = h.ctl.state().generation;
  h.select(a);
  const third = h.ctl.state().generation;
  assert.ok(first < second && second < third);
  assert.equal(h.requests.length, 3);
  h.requests[1].resolve(response("/audio/obsolete-b.mp3"));
  h.requests[0].resolve(response("/audio/obsolete-a.mp3"));
  await drain();
  assert.equal(h.loads.length, 0);
  h.requests[2].resolve(response("/audio/current-a.mp3"));
  await drain();
  assert.equal(h.loads.length, 1);
  assert.equal(h.loads[0].item.generation, third);
  assert.equal(h.loads[0].item.plan, a);
  assert.equal(h.ctl.state().ownerTurnId, "A");
});

test("late JSON completion cannot load audio after replacement", async () => {
  const h = harness();
  h.select(windowsPlan());
  const json = deferred();
  h.requests[0].resolve({ ok: true, json: () => json.promise });
  await drain();
  const replacement = Karaoke.createPlan("replacement", "new answer");
  h.select(replacement);
  json.resolve({ audio_url: "/audio/obsolete.mp3" });
  await drain();
  assert.equal(h.loads.length, 0);
  h.requests[1].resolve(response());
  await drain();
  assert.equal(h.loads[0].item.plan, replacement);
});

for (const settle of ["resolve", "reject"]) {
  test("stale play " + settle + " cannot mutate a new pending owner", async () => {
    const h = harness({ pendingPlay: true });
    const old = await h.ready(windowsPlan());
    const replacement = Karaoke.createPlan("replacement", "different answer");
    h.select(replacement);
    const before = h.ctl.state();
    old.plays[0][settle](new Error("obsolete play"));
    await drain();
    assert.deepEqual(h.ctl.state(), before);
    assert.equal(h.failures.length, 0);
    assert.equal(old.disposals, 1);
  });
}

test("all old media callbacks are no-ops after stop", async () => {
  const h = harness();
  const old = await h.ready(windowsPlan());
  h.ctl.stop();
  const before = h.ctl.state();
  const events = h.progress.length;
  for (const name of ["loadedmetadata", "durationchange", "timeupdate", "pause", "ended", "error"]) {
    old.events[name](new Error("obsolete event"));
  }
  assert.deepEqual(h.ctl.state(), before);
  assert.equal(h.progress.length, events);
  assert.equal(h.requests.length, 1);
  assert.equal(h.failures.length, 0);
  assert.deepEqual(h.queue, [h.foreign]);
});

test("old metadata cannot seek another chunk or another piece", async () => {
  const h = harness({ duration: NaN });
  const plan = longPlan();
  const old = await h.ready(plan, 1);
  h.select(plan, plan.pieces[1].chunkOffset);
  old.duration = 100;
  old.events.loadedmetadata();
  old.events.durationchange();
  assert.deepEqual(old.seeks, []);
  assert.equal(old.plays.length, 0);
  assert.equal(h.loads.length, 1);
  assert.equal(h.ctl.state().activeGlobalIndex, plan.pieces[1].chunkOffset);
});

test("failed middle synthesis is reported and later pieces keep original global indexes", async () => {
  const h = harness();
  const plan = longPlan();
  const first = await h.ready(plan);
  first.events.ended();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({ ok: false, status: 503 });
  await drain();
  assert.equal(h.failures.length, 1);
  assert.equal(h.failures[0].pieceIndex, 1);
  assert.equal(h.failures[0].plan, plan);
  assert.equal(h.failures[0].stage, "synthesis");
  assert.ok(h.progress.some(event => event.activeGlobalIndex === null));
  assert.equal(h.requests.length, 3);
  assert.equal(h.ctl.state().activeGlobalIndex, plan.pieces[2].chunkOffset);
  h.requests[2].resolve(response("/audio/third.mp3"));
  await drain();
  const later = h.loads[1];
  later.tick(50);
  const expected = plan.pieces[2].chunkOffset + Math.floor(plan.pieces[2].chunks.length / 2);
  assert.equal(h.progress.at(-1).activeGlobalIndex, expected);
  assert.equal(h.progress.at(-1).chunk.ordinal, expected + 1);
});

for (const firstFailure of ["error", "rejection"]) {
  test("error and rejected play claim failure once: " + firstFailure + " first", async () => {
    const h = harness({ pendingPlay: true });
    const audio = await h.ready(windowsPlan());
    if (firstFailure === "error") audio.events.error(new Error("media error"));
    audio.plays[0].reject(new Error("play rejected"));
    await drain();
    audio.events.error(new Error("duplicate media error"));
    assert.equal(h.failures.length, 1);
    assert.equal(h.failures[0].pieceIndex, 0);
    assert.equal(audio.disposals, 1);
    assertIdle(h);
    assert.equal(h.progress.at(-1).chunk, null);
  });
}

test("media failure while awaiting metadata clears the pending selection", async () => {
  const h = harness({ duration: 0 });
  const audio = await h.ready(windowsPlan(), 2);
  audio.events.error(new Error("metadata failed"));
  assert.equal(h.failures.length, 1);
  assert.equal(h.failures[0].stage, "media");
  assertIdle(h);
  audio.duration = 100;
  audio.events.loadedmetadata();
  assert.deepEqual(audio.seeks, []);
});

const synthesisFailures = [
  ["fetch rejection", call => call.reject(new Error("offline"))],
  ["HTTP failure", call => call.resolve({ ok: false, status: 422 })],
  ["JSON failure", call => call.resolve({ ok: true, json: async () => { throw new Error("bad JSON"); } })],
  ["missing audio URL", call => call.resolve({ ok: true, json: async () => ({}) })],
  ["external URL", call => call.resolve(response("https://outside.invalid/audio.mp3"))],
  ["protocol-relative URL", call => call.resolve(response("//outside.invalid/audio.mp3"))],
  ["backslash URL", call => call.resolve(response("/\\outside.invalid/audio.mp3"))]
];

for (const [name, fail] of synthesisFailures) {
  test(name + " is visible and cannot load unowned or external audio", async () => {
    const h = harness();
    h.select(windowsPlan());
    fail(h.requests[0]);
    await drain();
    assert.equal(h.failures.length, 1);
    assert.equal(h.failures[0].stage, "synthesis");
    assert.equal(h.loads.length, 0);
    assertIdle(h);
  });
}

for (const option of ["loadError", "seekError", "playError"]) {
  test("synchronous " + option + " restores idle controls", async () => {
    const h = harness({ [option]: true });
    h.select(windowsPlan());
    h.requests[0].resolve(response());
    await drain();
    assert.equal(h.failures.length, 1);
    assertIdle(h);
  });
}

test("terminal end clears ephemeral audio so replay starts a fresh session", async () => {
  const h = harness();
  const plan = windowsPlan();
  const audio = await h.ready(plan);
  const generation = h.ctl.state().generation;
  audio.events.ended();
  assertIdle(h);
  assert.ok(h.ctl.state().generation > generation);
  h.select(plan);
  assert.equal(h.requests.length, 2, "no cross-session cache survives retirement");
  assert.deepEqual(h.queue, [h.foreign]);
});

test("rebuilding a plan with the same ID and text retires the previous plan", async () => {
  const h = harness();
  const original = windowsPlan();
  const old = await h.ready(original);
  const replacement = Karaoke.createPlan(original.turnId, original.rawText);
  h.select(replacement);
  assert.equal(h.requests.length, 2);
  assert.equal(old.disposals, 1);
  assert.equal(h.ctl.state().plan, replacement);
});

test("empty answers and invalid indexes do not synthesize or disturb a current owner", () => {
  const h = harness();
  for (const text of ["", " \t\n "]) {
    assert.equal(h.ctl.replay(Karaoke.createPlan("empty", text)), false);
  }
  assertIdle(h);
  const plan = windowsPlan();
  h.select(plan);
  const before = h.ctl.state();
  for (const index of [-1, plan.chunks.length, NaN, Infinity, 1.5, "1"]) {
    assert.equal(h.ctl.select(plan, index), false);
  }
  assert.deepEqual(h.ctl.state(), before);
  assert.equal(h.requests.length, 1);
});

test("a host retiring during selection notification prevents any subsequent fetch", () => {
  const h = harness({ onProgress(event, current) {
    if (event.phase === "preparing") current.ctl.stop();
  } });
  h.select(windowsPlan());
  assertIdle(h);
  assert.equal(h.requests.length, 0);
});

test("a host retiring during failure notification prevents continuation", async () => {
  const h = harness({ onFailure(event, current) { current.ctl.stop(); } });
  h.select(longPlan());
  h.requests[0].reject(new Error("offline"));
  await drain();
  assert.equal(h.failures.length, 1);
  assert.equal(h.requests.length, 1);
  assertIdle(h);
});

test("deterministic mixed literals retain exact raw ranges over short and long plans", () => {
  const tokens = ["echo", " ", "\t\n", "\u00a0", "😀", "🧠", "cafe\u0301", ".", ": ",
    "<script>", "</script>", "```", "x".repeat(90), "!", "\r\n\t"];
  let seed = 17;
  for (let sample = 0; sample < 30; sample++) {
    let text = " \t";
    const size = sample % 3 === 0 ? 2000 : 80;
    for (let i = 0; i < size; i++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      text += tokens[seed % tokens.length];
    }
    verifyPlan(text + " \n");
  }
});

for (const action of ["stop", "reset", "detach"]) {
  test(action + " rejects late play and every media callback after retirement", async () => {
    const h = harness({ pendingPlay: true });
    const plan = windowsPlan();
    const audio = await h.ready(plan);
    h.ctl[action](plan.turnId);
    const before = h.ctl.state();
    const events = h.progress.length;
    audio.plays[0].reject(new Error("retired play"));
    audio.duration = 200;
    for (const name of ["loadedmetadata", "durationchange", "timeupdate", "pause", "ended", "error"]) {
      audio.events[name](new Error("retired media"));
    }
    await drain();
    assert.deepEqual(h.ctl.state(), before);
    assert.equal(h.progress.length, events);
    assert.equal(h.failures.length, 0);
    assert.equal(audio.disposals, 1);
    assert.deepEqual(h.queue, [h.foreign]);
  });
}

for (const action of ["stop", "reset", "detach"]) {
  test(action + " rejects an obsolete synthesis failure without notifying", async () => {
    const h = harness();
    const plan = windowsPlan();
    h.select(plan);
    h.ctl[action](plan.turnId);
    h.requests[0].reject(new Error("obsolete offline result"));
    await drain();
    assertIdle(h);
    assert.equal(h.failures.length, 0);
    assert.equal(h.loads.length, 0);
  });
}

test("old metadata never seeks a new turn's loaded but pending source", async () => {
  const h = harness({ duration: NaN });
  const old = await h.ready(windowsPlan(), 2);
  const plan = Karaoke.createPlan("new-turn", "different sentence. ".repeat(20));
  h.select(plan, 1);
  h.requests[1].resolve(response("/audio/new-turn.mp3"));
  await drain();
  assert.equal(h.loads.length, 2);
  const fresh = h.loads[1];
  const before = h.ctl.state();
  old.duration = 100;
  old.events.loadedmetadata();
  old.events.durationchange();
  assert.deepEqual(old.seeks, []);
  assert.deepEqual(fresh.seeks, []);
  assert.deepEqual(h.ctl.state(), before);
  fresh.duration = 200;
  fresh.events.loadedmetadata();
  await drain();
  assert.deepEqual(fresh.seeks, [1 / plan.pieces[0].chunks.length * 200]);
  assert.equal(h.ctl.state().phase, "playing");
});

test("one short window reports matching inline and popup progress", async () => {
  const h = harness();
  const plan = Karaoke.createPlan("short", " \tjust one window\n");
  const audio = await h.ready(plan);
  assert.equal(plan.chunks.length, 1);
  audio.tick(80);
  assert.equal(h.progress.at(-1).activeGlobalIndex, 0);
  assert.equal(h.progress.at(-1).chunk, plan.chunks[0]);
  assert.equal(h.progress.at(-1).chunk.popupText, "just one window");
});

for (const firstFailure of ["error", "rejection"]) {
  test("failed middle media retains later indexes and ignores its twin: " + firstFailure, async () => {
    const h = harness({ pendingPlay: true });
    const plan = longPlan();
    const middle = plan.pieces[1];
    const audio = await h.ready(plan, middle.chunkOffset + 1);
    if (firstFailure === "error") audio.events.error(new Error("middle media error"));
    audio.plays[0].reject(new Error("middle play rejected"));
    await drain();
    audio.events.error(new Error("duplicate error"));
    audio.events.ended();
    assert.equal(h.failures.length, 1);
    assert.equal(h.failures[0].pieceIndex, 1);
    assert.equal(h.requests.length, 2, "a twin cannot skip a second piece");
    assert.equal(h.ctl.state().activeGlobalIndex, plan.pieces[2].chunkOffset);
    h.requests[1].resolve(response("/audio/after-gap.mp3"));
    await drain();
    const later = h.loads[1];
    later.plays[0].resolve();
    await drain();
    later.tick(90);
    assert.equal(h.progress.at(-1).chunk.pieceIndex, 2);
    assert.ok(h.progress.at(-1).activeGlobalIndex >= plan.pieces[2].chunkOffset);
  });
}

test("pause while play is pending prevents late success from reviving playback", async () => {
  const h = harness({ pendingPlay: true });
  const plan = windowsPlan();
  const audio = await h.ready(plan, 2);
  audio.events.pause();
  assert.equal(h.ctl.state().phase, "paused");
  audio.plays[0].resolve();
  await drain();
  assert.equal(h.ctl.state().phase, "paused");
  const count = h.progress.length;
  audio.tick(90);
  assert.equal(h.progress.length, count);
  h.select(plan, 2);
  h.loads[1].plays[0].resolve();
  await drain();
  assert.equal(h.ctl.state().phase, "playing");
  assert.equal(h.requests.length, 1);
});

test("same-piece reselection supersedes an unresolved synthesis request", async () => {
  const h = harness();
  const plan = windowsPlan();
  h.select(plan, 1);
  const previous = h.ctl.state().generation;
  h.select(plan, 3);
  assert.ok(h.ctl.state().generation > previous);
  assert.equal(h.requests[0].init.signal.aborted, true);
  h.requests[0].resolve(response("/audio/stale-window.mp3"));
  await drain();
  assert.equal(h.loads.length, 0);
  h.requests[1].resolve(response("/audio/current-window.mp3"));
  await drain();
  assert.equal(h.loads.length, 1);
  assert.equal(h.ctl.state().activeGlobalIndex, 3);
});

test("a host stopping in awaiting_metadata prevents the adapter from loading", async () => {
  const h = harness({ onState(state, current) {
    if (state.phase === "awaiting_metadata") current.ctl.stop();
  } });
  h.select(windowsPlan());
  h.requests[0].resolve(response());
  await drain();
  assertIdle(h);
  assert.equal(h.loads.length, 0);
});

test("matching metadata during adapter load is consumed after the handle is installed", async () => {
  const h = harness({ duration: NaN, onLoad(audio) {
    audio.duration = 80;
    audio.events.loadedmetadata();
    audio.events.durationchange();
  } });
  const plan = windowsPlan();
  const audio = await h.ready(plan, 2);
  assert.deepEqual(audio.seeks, [2 / plan.pieces[0].chunks.length * 80]);
  assert.equal(audio.plays.length, 1);
  assert.equal(h.ctl.state().phase, "playing");
});

test("a host retiring inside adapter load disposes the returned item only once", async () => {
  const h = harness({ onLoad(audio, current) { current.ctl.stop(); } });
  const audio = await h.ready(windowsPlan());
  assertIdle(h);
  assert.equal(audio.disposals, 1);
  assert.deepEqual(audio.seeks, []);
  assert.equal(audio.plays.length, 0);
  assert.deepEqual(h.queue, [h.foreign]);
});

test("a queued owned handle waits until its own source metadata becomes available", async () => {
  const h = harness({ duration: NaN });
  const plan = longPlan();
  const piece = plan.pieces[1];
  const audio = await h.ready(plan, piece.chunkOffset + 2);
  assert.deepEqual(audio.seeks, []);
  audio.tick(999); // unrelated/unknown-duration progress cannot select another chunk.
  assert.equal(h.ctl.state().activeGlobalIndex, piece.chunkOffset + 2);
  audio.duration = 60;
  audio.events.loadedmetadata();
  await drain();
  assert.deepEqual(audio.seeks, [2 / piece.chunks.length * 60]);
  assert.equal(audio.plays.length, 1);
  assert.deepEqual(h.queue, [h.foreign, audio.item]);
});

test("identical audio URLs never transfer old callback ownership to another turn", async () => {
  const h = harness({ duration: NaN });
  const old = await h.ready(windowsPlan(), 2);
  const replacement = Karaoke.createPlan("equal-url-owner", "Different answer. ".repeat(20));
  h.select(replacement, 1);
  h.requests[1].resolve(response(old.item.url));
  await drain();
  const fresh = h.loads[1];
  assert.equal(fresh.item.url, old.item.url);
  const before = h.ctl.state();
  old.duration = 200;
  for (const event of ["loadedmetadata", "durationchange", "timeupdate", "pause", "ended", "error"]) {
    old.events[event](new Error("obsolete equal URL"));
  }
  assert.deepEqual(h.ctl.state(), before);
  assert.deepEqual(fresh.seeks, []);
  assert.equal(fresh.plays.length, 0);
  assert.equal(h.failures.length, 0);
  fresh.duration = 60;
  fresh.events.loadedmetadata();
  await drain();
  assert.equal(h.ctl.state().phase, "playing");
  assert.deepEqual(fresh.seeks, [1 / replacement.pieces[0].chunks.length * 60]);
});

test("queued pause from disposed same-source replay cannot freeze current progress", async () => {
  const h = harness();
  const plan = windowsPlan();
  await h.ready(plan, 1);
  h.select(plan, 2);
  await drain();
  const audio = h.loads[1];
  audio.paused = false; // native pause was queued before replacement play().
  audio.events.pause();
  assert.equal(h.ctl.state().phase, "playing");
  audio.tick(90);
  assert.equal(h.ctl.state().activeGlobalIndex, Math.floor(plan.chunks.length * 0.9));
  assert.equal(h.requests.length, 1);
});

test("native pause is still honored and suppresses late play completion", async () => {
  const h = harness({ pendingPlay: true });
  const audio = await h.ready(windowsPlan(), 2);
  audio.paused = true;
  audio.events.pause();
  audio.plays[0].resolve();
  await drain();
  assert.equal(h.ctl.state().phase, "paused");
  const count = h.progress.length;
  audio.tick(90);
  assert.equal(h.progress.length, count);
});

test("explicit zero-start fallback plays once without waiting for metadata", async () => {
  const h = harness({ startAtZero: true, duration: NaN });
  const audio = await h.ready(windowsPlan());
  assert.deepEqual(audio.seeks, [0]);
  assert.equal(audio.plays.length, 1);
  assert.equal(h.ctl.state().phase, "playing");
  audio.duration = 100;
  audio.events.loadedmetadata();
  audio.events.durationchange();
  assert.equal(audio.plays.length, 1);
  assert.deepEqual(audio.seeks, [0]);
});

test("zero-start fallback permission cannot bypass a nonzero proportional seek", async () => {
  const h = harness({ startAtZero: true, duration: NaN });
  const audio = await h.ready(windowsPlan(), 2);
  assert.deepEqual(audio.seeks, []);
  assert.equal(audio.plays.length, 0);
  assert.equal(h.ctl.state().phase, "awaiting_metadata");
});
