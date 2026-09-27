# Tasks: PM-01 — One-Line Install & Packaging

Runner owns s1–s32; s16g pins 0.15.0 (smoke-tests.sh:913). New scenarios: **33 bootstrap, 34 installer**. Req IDs: pb-1..6, in-1..7.

## Review Workload Forecast

Est. lines ~415. Split: S1 U1+U2 (~150) → S2 U3+U4+U5 (~265). Delivery: ask-on-risk.

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| U1 | bootstrap.sh + s33 | S1 | `bash scripts/smoke-tests.sh` | recorder stubs | revert bootstrap.sh + s33 |
| U2 | manifest 0.16.0 + tag | S1 | as U1 | manual tag push | delete tag; revert 1 ln |
| U3 | install.sh + s34 | S2 | `bash scripts/smoke-tests.sh` | N/A — live = 5.3 | delete install.sh + s34 |
| U4 | README rewrite | S2 | as U1 (green) | N/A — docs only | revert README section |
| U5 | design doc fixes | S2 | N/A — doc only | N/A — docs only | revert edits |

## Phase 1: U1 — bootstrap.sh (TDD)

- [x] 1.1 RED scripts/smoke-tests.sh s33: recorder-stub harness per design (absence = restricted PATH). Cases: uv-only via uv pip (pb-1); python3-only (pb-2); no-tooling aborts clean (pb-2); pinned source (pb-3); decoy HOME ignored (pb-4); dev opt-in (pb-4); upgrade re-run (pb-5); arbitrary location (pb-6). Fails.
- [x] 1.2 GREEN scripts/bootstrap.sh: `py_install()` (uv pip, else `python -m pip`); never `${VENV_DIR}/bin/pip`; tooling check before `mkdir -p`; English strings.
- [x] 1.3 GREEN scripts/bootstrap.sh: `AGENT_TTS_REF` pin (tag else full SHA; `HERDR_AGENT_TTS_REF` override); `HERDR_TTS_DEV=1` gate; `HERDR_TTS_UPGRADE=1`/`--upgrade` bypass. s33 green; suite green.
- [x] 1.4 Verify bin/herdr-tts untouched (`git diff`).

## Phase 2: U2 — manifest + first tag

- [x] 2.1 herdr-plugin.toml: version 0.16.0; update s16g assertion (smoke-tests.sh:913) to 0.16.0; suite green.
- [x] 2.2 Non-code: maintainer pins agent-tts ref (tag else full main SHA, decision (d)).
- [x] 2.3 Non-code: maintainer cuts/pushes tag `v0.16.0` after suite green (Migration step 2). — DONE 2026-09-22: annotated tag cut locally on f6a23df; push deferred to out-of-hours window (maintainer rule).

## Phase 3: U3 — install.sh (TDD)

- [x] 3.1 RED scripts/smoke-tests.sh s34: jq missing aborts clean (in-1); linked-checkout refusal, kind==local (in-1); fresh e2e order + status pointer (in-2); remote match upgrades / mismatch aborts (in-3); keymap: adopt+reload / never overwrite / --no-keymap clean (in-4); uninstall print complete (in-5); English-only failure (in-6); default pins v0.16.0 (in-7); HERDR_TTS_REF=main hatch (in-7); unrelated-cwd clone to TARGET via `git -C`. Fails.
- [x] 3.2 GREEN scripts/install.sh (new): preflight→obtain→bootstrap→keymap→daemon-verify→uninstall-print; `HERDR_TTS_REF` default v0.16.0; `--no-keymap`; English output; no mutation before aborts. s34 green; suite green.

## Phase 4: U4 — README

- [x] 4.1 README.md install rewrite: registry channel, curl|sh fallback, `HERDR_TTS_REF`/`@main` hatch, uninstall steps as printed, dev workflow (HERDR_TTS_DEV=1, plugin link). Spanish untouched.

## Phase 5: U5 — artifact fixes + verify items

- [x] 5.1 openspec/changes/pm-01-install-packaging/design.md: row #10 add keymap rationale cite (Flow step 4 + proposal D3) (nit 1).
- [x] 5.2 openspec/changes/pm-01-install-packaging/design.md: renumber planned scenarios 32/33 → 33/34 (nit 2).
- [x] 5.3 Non-code verify: run decision (f) uv-machine script on a uv host; record in verify report. — DONE 2026-09-22: uv 0.12.17 installed in user space; live run PASS (bootstrap rc=0, no bin/pip yet uv-pip route OK, dev checkout ignored, pinned SHA install + import OK, upgrade rc=0). Transcript in verify-report.md addendum.
