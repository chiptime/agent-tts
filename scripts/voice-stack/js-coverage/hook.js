/* Node require-hook that instruments the voice-stack browser modules for
 * coverage when the Node test suite requires them (MQ-03).
 *
 * Loaded with `node --require`. Instruments every .js load whose resolved
 * path lives under src/herdr_brain/static/ (the production browser modules),
 * records into the standard `__coverage__` object, and flushes one JSON file
 * per process into $JS_COVERAGE_DIR on exit. External packages and the test
 * files themselves are never instrumented.
 *
 * The instrumenter configuration is identical to instrument-file.js and the
 * browser route-swap: coverageVariable __coverage__, original coordinates.
 */

"use strict";

const fs = require("fs");
const Module = require("module");
const path = require("path");

const OUT_DIR = process.env.JS_COVERAGE_DIR;
const STATIC_PREFIX = path.resolve(__dirname, "../../../hosts/herdr/brain/src/herdr_brain/static") + path.sep;

if (!OUT_DIR) {
  process.exitCode = 2;
  console.error("hook: JS_COVERAGE_DIR must be set");
} else {
  // Istanbul toolchain location: VOICE_STACK_JS_TOOLS (a directory
  // holding node_modules, exported by run-pwa-gate.sh) wins; without it,
  // standard Node module resolution applies (NODE_PATH / node_modules
  // lookup from this script). No machine path is embedded.
  const instrumenterPath = process.env.VOICE_STACK_JS_TOOLS
    ? path.join(process.env.VOICE_STACK_JS_TOOLS, "node_modules",
                "istanbul-lib-instrument")
    : "istanbul-lib-instrument";
  const { createInstrumenter } = require(instrumenterPath);
  const instrumenter = createInstrumenter({
    coverageVariable: "__coverage__",
    // Same coordinate system as the browser instrumentation; globalThis is
    // exact under Node CJS regardless of caller strictness.
    coverageGlobalScope: "globalThis",
    coverageGlobalScopeFunc: false,
    produceSourceMap: false,
    preserveComments: true,
    compact: false,
    esModules: false,
  });

  const origCompile = Module.prototype._compile;
  Module.prototype._compile = function (content, filename, ...rest) {
    if (filename.startsWith(STATIC_PREFIX) && filename.endsWith(".js")) {
      content = instrumenter.instrumentSync(content, path.basename(filename));
    }
    return origCompile.call(this, content, filename, ...rest);
  };

  const flush = () => {
    try {
      const cov = global.__coverage__ || {};
      const out = Object.values(cov).map((fc) => ({
        path: fc.path,
        b: fc.b,
        f: fc.f,
        s: fc.s,
        statementMap: fc.statementMap,
        fnMap: fc.fnMap,
        branchMap: fc.branchMap,
        hash: fc.hash,
      }));
      if (out.length) {
        fs.mkdirSync(OUT_DIR, { recursive: true });
        fs.writeFileSync(
          path.join(OUT_DIR, `node-${process.pid}-${Date.now()}.json`),
          JSON.stringify(out)
        );
      }
    } catch (_e) { /* coverage flush must never fail the suite */ }
  };
  process.on("exit", flush);
}
