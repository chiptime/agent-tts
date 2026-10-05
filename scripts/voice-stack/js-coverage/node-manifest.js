#!/usr/bin/env node
/* Complete offline-toolchain manifest for the voice-stack JS coverage lane
 * (MQ-04): walks the ACTUAL node_modules directory and records every
 * installed package with version read from its own package.json, per-package
 * content integrity, dependency closure, and the npm-cacache origin blob
 * (offline provenance). No version is hardcoded: the manifest is derived
 * from what is on disk.
 *
 * Usage: node node-manifest.js <node_modules-dir> <inventory.json> <out.json>
 */

"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

function sha256File(p) {
  const h = crypto.createHash("sha256");
  h.update(fs.readFileSync(p));
  return h.digest("hex");
}

function dirDigest(root) {
  // Deterministic content digest: sorted (relpath, file-sha256) pairs.
  const entries = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(p);
      else if (ent.isFile() && ent.name !== ".DS_Store") {
        entries.push([path.relative(root, p), sha256File(p)]);
      }
    }
  };
  walk(root);
  const h = crypto.createHash("sha256");
  for (const [rel, sha] of entries) h.update(rel + "\0" + sha + "\0");
  return { files: entries.length, content_sha256: h.digest("hex") };
}

function listPackages(nmDir) {
  /* Enumerate packages at one node_modules level (handles @scope/),
   * recursing into nested <pkg>/node_modules — the offline installer uses
   * nested placement for transitive dependencies. */
  const found = [];
  const scan = (dir) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (!ent.isDirectory() || ent.name.startsWith(".")) continue;
      if (ent.name.startsWith("@")) {
        for (const sub of fs.readdirSync(path.join(dir, ent.name), { withFileTypes: true })) {
          if (sub.isDirectory()) found.push(`${ent.name}/${sub.name}`);
        }
      } else {
        found.push(ent.name);
      }
    }
  };
  scan(nmDir);
  const result = [];
  const seen = new Set();
  const recurse = (pkgName) => {
    if (seen.has(pkgName)) return;
    seen.add(pkgName);
    result.push(pkgName);
    const nested = path.join(nmDir, pkgName, "node_modules");
    if (fs.existsSync(nested)) {
      for (const sub of listPackages(nested)) {
        result.push(`${pkgName}/node_modules/${sub}`);
      }
    }
  };
  for (const p of found) recurse(p);
  return result;
}

function main() {
  const [, , nmDir, inventoryPath, outPath] = process.argv;
  if (!nmDir || !inventoryPath || !outPath) {
    console.error("usage: node-manifest.js <node_modules> <inventory.json> <out.json>");
    process.exit(2);
  }
  const inventory = JSON.parse(fs.readFileSync(inventoryPath, "utf8"));
  const byId = new Map(inventory.map((e) => [`${e.name}@${e.version}`, e.path]));

  const packages = {};
  for (const name of listPackages(nmDir)) {
    const pkgDir = path.join(nmDir, name);
    const pjPath = path.join(pkgDir, "package.json");
    if (!fs.existsSync(pjPath)) continue;
    const pj = JSON.parse(fs.readFileSync(pjPath, "utf8"));
    const version = pj.version || "(none)";
    const origin = byId.get(`${pj.name || name.split("/node_modules/").pop()}@${version}`) || null;
    packages[name] = {
      name: pj.name || name,
      version,                                       // READ from disk
      package_json_sha256: sha256File(pjPath),
      dependencies: Object.keys(pj.dependencies || {}),
      origin_blob: origin,                            // npm cacache content path
      integrity: dirDigest(pkgDir),
    };
  }
  const manifest = {
    schema: 1,
    generated_utc: new Date().toISOString().replace(/:\d\d\.\d+Z$/, "Z"),
    tool: "node-manifest.js",
    node_version: process.version,
    package_count: Object.keys(packages).length,
    packages,
  };
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(manifest, null, 1) + "\n");
  const mustHave = ["istanbul-lib-instrument", "istanbul-lib-coverage",
                    "istanbul-lib-report", "istanbul-reports",
                    "istanbul-lib-source-maps", "v8-to-istanbul"];
  const missing = mustHave.filter((m) => !packages[m]);
  console.log(`manifest: ${Object.keys(packages).length} packages -> ${outPath}`);
  console.log("coverage chain present:", missing.length === 0,
              missing.length ? `MISSING: ${missing}` : "");
  process.exit(missing.length ? 1 : 0);
}

main();
