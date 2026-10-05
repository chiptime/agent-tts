/* v8-to-istanbul lane for the voice-stack JS coverage pipeline (MQ-03).
 *
 * Converts raw NODE_V8_COVERAGE output (the same semantics the immutable
 * baseline pwa.lcov was produced from) into an istanbul-style LCOV for the
 * static modules. This lane exists so the component-total non-regression
 * compares COMPARABLE denominators against the baseline (per-line v8
 * semantics), while the changed-scope metric keeps the precise
 * istanbul-instrumented statement/branch-arc lane.
 *
 * Usage: node v8-to-lcov.js --static-dir <dir> --out-lcov <file> <v8.json>...
 */

"use strict";

const fs = require("fs");
const path = require("path");

const TOOLCHAIN = path.join(
  process.env.VOICE_STACK_JS_TOOLS ||
    path.join(require("os").homedir(), ".local/state/voice-stack-maintenance-runs/20261001T162700Z-m1q/tools/nodejs"),
  "node_modules"
);
const libCoverage = require(path.join(TOOLCHAIN, "istanbul-lib-coverage"));
const libReport = require(path.join(TOOLCHAIN, "istanbul-lib-report"));
const libReports = require(path.join(TOOLCHAIN, "istanbul-reports"));
const V8ToIstanbul = require(path.join(TOOLCHAIN, "v8-to-istanbul/lib/v8-to-istanbul"));

async function main() {
  const argv = process.argv.slice(2);
  const opt = (name) => {
    const i = argv.indexOf(name);
    const v = i >= 0 ? argv[i + 1] : null;
    if (i >= 0) argv.splice(i, 2);
    return v;
  };
  const staticDir = opt("--static-dir");
  const outLcov = opt("--out-lcov");
  const inputs = argv.filter((a) => !a.startsWith("--"));
  if (!staticDir || !outLcov || !inputs.length) {
    console.error("usage: v8-to-lcov.js --static-dir <dir> --out-lcov <file> <v8.json>...");
    process.exit(2);
  }

  const merged = libCoverage.createCoverageMap({});
  const sources = {};
  let converted = 0, skipped = 0, failed = 0;
  for (const inp of inputs) {
    const raw = JSON.parse(fs.readFileSync(inp, "utf8"));
    for (const entry of raw.result || []) {
      const m = /\/([^/]+\.js)$/.exec(entry.url || "");
      if (!m || !entry.url.startsWith("file:")) { skipped++; continue; }
      const name = m[1];
      const srcPath = path.join(staticDir, name);
      if (!fs.existsSync(srcPath)) { skipped++; continue; }
      if (!sources[name]) sources[name] = fs.readFileSync(srcPath, "utf8");
      try {
        const conv = new V8ToIstanbul(srcPath, 0, { source: sources[name] });
        await conv.load();
        conv.applyCoverage(entry.functions);
        // toIstanbul() returns { <abs-path>: fileCoverage, ... }
        const byPath = conv.toIstanbul();
        const records = Object.values(byPath);
        if (!records.length) { failed++; continue; }
        for (const fc of records) {
          fc.path = name; // basename key, aligned with the istanbul lane
          merged.merge([fc]);
        }
        converted++;
      } catch (_e) { failed++; /* un-mappable entry: skip */ }
    }
  }
  const context = libReport.createContext({
    dir: path.dirname(outLcov),
    coverageMap: merged,
    watermarks: libReport.getDefaultWatermarks(),
  });
  libReports.create("lcovonly").execute(context);
  fs.copyFileSync(path.join(path.dirname(outLcov), "lcov.info"), outLcov);
  fs.unlinkSync(path.join(path.dirname(outLcov), "lcov.info"));

  const totals = merged.getCoverageSummary();
  console.log(
    `v8 lane: files=${merged.files().length} converted=${converted} ` +
    `skipped=${skipped} failed=${failed} ` +
    `lines=${totals.lines.pct}% branches=${totals.branches.pct}%`
  );
}

main().catch((e) => { console.error(e); process.exit(1); });
