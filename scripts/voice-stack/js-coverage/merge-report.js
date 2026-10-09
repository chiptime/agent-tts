/* Merge collected istanbul coverage JSON files (node hook output + browser
 * window.__coverage__ dumps) into one report (MQ-03).
 *
 * Usage: node merge-report.js <out-detail.json> <out-lcov.info> <in.json>...
 *
 * - detail JSON: { files: { <repo-rel-path>: fileCoverage } } with full
 *   istanbul maps and per-statement/fn/branch hit counters, plus the
 *   instrumenter content hash per file.
 * - LCOV: FN/FNDA/FNH, DA/DLH..., BRDA/BRF/BRH — original source
 *   coordinates, matching the format of the immutable baseline pwa.lcov.
 * Repo-relative file keys: pass --root <dir> to strip that prefix.
 */

"use strict";

const fs = require("fs");
const path = require("path");

// Istanbul toolchain location: VOICE_STACK_JS_TOOLS (a directory holding
// node_modules, exported by run-pwa-gate.sh) wins; without it, standard
// Node module resolution applies (NODE_PATH / node_modules lookup from
// this script). No machine path is embedded.
function toolRequire(name) {
  return require(
    process.env.VOICE_STACK_JS_TOOLS
      ? path.join(process.env.VOICE_STACK_JS_TOOLS, "node_modules", name)
      : name
  );
}
const libCoverage = toolRequire("istanbul-lib-coverage");
const libReport = toolRequire("istanbul-lib-report");
const libReports = toolRequire("istanbul-reports");

function main() {
  const argv = process.argv.slice(2);
  let root = "";
  const rootIdx = argv.indexOf("--root");
  if (rootIdx >= 0) { root = argv[rootIdx + 1]; argv.splice(rootIdx, 2); }
  const [outDetail, outLcov, ...inputs] = argv;
  if (!outDetail || !outLcov || !inputs.length) {
    console.error("usage: merge-report.js [--root dir] <out-detail> <out-lcov> <in.json>...");
    process.exit(2);
  }
  const map = libCoverage.createCoverageMap({});
  let records = 0;
  for (const inp of inputs) {
    const entries = JSON.parse(fs.readFileSync(inp, "utf8"));
    let arr;
    if (Array.isArray(entries)) {
      arr = entries;
    } else if (typeof entries.path === "string") {
      arr = [entries]; // single fileCoverage
    } else {
      // browser dump: {name: [fileCoverage, ...]} — flatten per-name arrays
      arr = [];
      for (const v of Object.values(entries)) arr = arr.concat(v);
    }
    const normalized = arr.map((fc) => {
      const clone = JSON.parse(JSON.stringify(fc));
      if (root && clone.path.startsWith(root + "/")) {
        clone.path = clone.path.slice(root.length + 1);
      }
      return clone;
    });
    for (const fc of normalized) { map.merge([fc]); records++; }
  }
  fs.mkdirSync(path.dirname(outDetail), { recursive: true });
  const files = {};
  map.files().forEach((f) => { files[f] = map.fileCoverageFor(f).toJSON(); });
  fs.writeFileSync(outDetail, JSON.stringify({
    schema: 1,
    records_merged: records,
    files,
  }, null, 1));

  const tree = libCoverage.createCoverageMap(files);
  const context = libReport.createContext({
    dir: path.dirname(outLcov),
    coverageMap: tree,
    watermarks: libReport.getDefaultWatermarks(),
  });
  libReports.create("lcovonly").execute(context);
  // istanbul writes lcov.info next to the context dir
  const produced = path.join(path.dirname(outLcov), "lcov.info");
  fs.copyFileSync(produced, outLcov);
  fs.unlinkSync(produced);

  const totals = tree.getCoverageSummary();
  console.log(
    `merged records=${records} files=${tree.files().length} ` +
    `lines=${totals.lines.pct}% branches=${totals.branches.pct}%`
  );
}

main();
