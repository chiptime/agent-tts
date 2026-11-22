/* node --test suite for the stop-refusal copy (parity wave 2, S3).
 *
 * A stop refused because the pane waits at a dialog must tell the
 * operator WHY in their own locale — «Hay un diálogo abierto;
 * respóndelo primero.» — driven by the server's stable ``reason``
 * field, never by interpolating the raw Spanish detail into the
 * generic failure copy. The pure decision (which toast a stop failure
 * produces) lives in one explicitly marked block of app.js that this
 * file extracts and evaluates — the same discipline as
 * spaces-switch.test.js — and the dictionaries are asserted to carry
 * the operator-approved copy verbatim in BOTH locales.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const staticDir = path.join(__dirname, "../../src/herdr_brain/static");
const appSrc = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");
const esDict = require(path.join(staticDir, "i18n/es.js"));
const enDict = require(path.join(staticDir, "i18n/en.js"));

/* ---- extract and evaluate the pure block ---- */

function loadModel() {
  const START = "/* @stop-pure-start */";
  const END = "/* @stop-pure-end */";
  const start = appSrc.indexOf(START);
  const end = appSrc.indexOf(END);
  assert.notEqual(start, -1, "app.js must mark the stop pure block");
  assert.notEqual(end, -1, "app.js must close the stop pure block");
  const block = appSrc.slice(start + START.length, end);
  const factory = new Function(block + "\nreturn stopFailureToast;");
  return factory();
}

const stopFailureToast = loadModel();

/* ---- the pure decision ---- */

test("a dialog_open refusal rides the localized stop.dialog_open key", () => {
  const toast = stopFailureToast(
    { detail: "el agente espera en un diálogo", reason: "dialog_open" },
    502,
  );
  assert.equal(toast.key, "stop.dialog_open");
  assert.deepEqual(toast.params, {});
});

test("a plain string detail keeps the legacy stop.fail copy", () => {
  const toast = stopFailureToast({ detail: "la razón" }, 502);
  assert.equal(toast.key, "stop.fail");
  assert.deepEqual(toast.params, { detail: "la razón" });
});

test("a non-string detail and a missing body degrade to error N as before", () => {
  assert.deepEqual(
    stopFailureToast({ detail: { nested: true } }, 502),
    { key: "stop.fail", params: { detail: "error 502" } },
  );
  assert.deepEqual(
    stopFailureToast(null, 502),
    { key: "stop.fail", params: { detail: "error 502" } },
  );
});

/* ---- wiring shape: the failure path must go through the model ---- */

test("the stop click handler toasts the model's decision on failure", () => {
  const start = appSrc.indexOf('fetch("/pane/stop"');
  assert.notEqual(start, -1, "the stop handler must post /pane/stop");
  const region = appSrc.slice(start, start + 900);
  assert.ok(
    region.includes("stopFailureToast("),
    "the stop failure path must decide the toast through stopFailureToast",
  );
  assert.ok(
    region.includes("showToast(t("),
    "the stop failure path must toast through the i18n runtime",
  );
});

/* ---- i18n: the operator-approved copy exists in BOTH locales ---- */

test("the dictionaries carry the agreed dialog-open copy verbatim", () => {
  assert.equal(
    esDict["stop.dialog_open"],
    "Hay un diálogo abierto; respóndelo primero.",
  );
  assert.equal(
    enDict["stop.dialog_open"],
    "There's an open dialog; answer it first.",
  );
});
