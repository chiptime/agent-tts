/* istanbul-lib-instrument one-file CLI for the voice-stack JS coverage
 * pipeline (MQ-03). Instruments a source file with the SAME instrumenter
 * configuration the browser and node hooks use, so the emitted maps are the
 * canonical coordinate system every later proof binds to.
 *
 * Usage: node instrument-file.js <source-file> <out-file> <out-maps-json>
 * Emits:
 *   <out-file>     instrumented JavaScript (coverageVariable __coverage__)
 *   <out-maps-json> {path, hash, statementMap, branchMap, fnMap} for the
 *                   ORIGINAL source coordinates — the binding artifact.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const TOOLCHAIN = path.join(
  process.env.VOICE_STACK_JS_TOOLS ||
    path.join(require("os").homedir(), ".local/state/voice-stack-maintenance-runs/20261001T162700Z-m1q/tools/nodejs"),
  "node_modules"
);
const { createInstrumenter } = require(path.join(TOOLCHAIN, "istanbul-lib-instrument"));

function main() {
  const [, , src, outFile, mapsFile] = process.argv;
  if (!src || !outFile || !mapsFile) {
    console.error("usage: instrument-file.js <source> <out> <maps-json>");
    process.exit(2);
  }
  const code = fs.readFileSync(src, "utf8");
  const inst = createInstrumenter({
    coverageVariable: "__coverage__",
    // The real app ships a Content-Security-Policy without unsafe-eval (so
    // no `new Function("return this")()`), and app.js opens with a
    // top-level "use strict" (so a plain `this` resolves to undefined in
    // the injected cov initializer). `globalThis` is exact on both counts.
    coverageGlobalScope: "globalThis",
    coverageGlobalScopeFunc: false,
    produceSourceMap: false,
    preserveComments: true,
    compact: false,
    esModules: false,
  });
  const instrumented = inst.instrumentSync(code, path.basename(src));
  fs.writeFileSync(outFile, instrumented);
  const cov = inst.lastFileCoverage();
  fs.writeFileSync(
    mapsFile,
    JSON.stringify(
      {
        path: path.basename(src),
        source_sha256: crypto.createHash("sha256").update(code).digest("hex"),
        instrumenter: "istanbul-lib-instrument@" +
          require(path.join(TOOLCHAIN, "istanbul-lib-instrument/package.json")).version,
        statementMap: cov.statementMap,
        branchMap: cov.branchMap,
        fnMap: cov.fnMap,
      },
      null,
      1
    ) + "\n"
  );
}

main();
