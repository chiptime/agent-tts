# Archive Report: settings-category-submenus

**Change**: Settings Category Submenus (Two-Level Ajustes Navigation)
**Archived**: 2026-09-23 → `openspec/changes/archive/2026-09-23-settings-category-submenus/`
**Artifact store**: hybrid (OpenSpec files in repo + Engram report `sdd/settings-category-submenus/archive-report`, project `herdr-tts`)

## Final Task State

**17/17 tasks complete** (persisted `tasks.md`, all `[x]`; corroborated by native status `gentle-ai.sdd-status/v2`: `taskProgress 17/17, allComplete: true`, `dependencies.archive: ready`, `blockedReasons: []`).

Note on counts: the orchestrator launch prompt stated "18/18"; both the persisted tasks artifact and native status count 17. Per the final-state hierarchy the persisted tasks artifact is the rank-1 source for completion visibility — 17/17 is the authoritative figure; the launch prompt's 18 is recorded here as a superseded count, not an unresolved contradiction (both sources agree all tasks are complete).

- Task 4.3 (chain-strategy decision): resolved 2026-09-23 as **NOT APPLICABLE** — code and change artifacts were delivered via direct-main on `origin/main` before the decision came due (retro change, no feature branches exist). Retroactive 3-PR chain rejected: it would rewrite pushed history. Future changes set chain strategy at the tasks phase, before apply. This is the final state per the launch prompt and the tasks artifact.
- `apply-progress` does not exist: apply was explicitly declared out of scope (retro change; code existed green before formalization). Its absence is structural, not a gap.

## Verification State

Attributed to `verify-report.md` (preserved in the archive, both sections):

- Original verification 2026-09-22: **FAIL** — 1 CRITICAL (spec scenario "Write failure keeps old value" untested).
- Remediation: smoke scenario 32h added (scripts/smoke-tests.sh, 7 assertions with negative controls); test-only change.
- Re-verification 2026-09-22 (evidence refresh): **PASS WITH WARNINGS** — 0 CRITICAL, suite **631/631 green** (exit 0), build clean, 11/12 scenarios compliant.

Open findings at close (from the re-verification snapshot; non-blocking, still unresolved):

1. WARNING — No-Scroll scenario remains PARTIAL: clamp runs on every render, but no assertion proves the longest view (Voz with install hint) fits without truncation. Suggested fix: explicit row-count assertion.
2. WARNING — No per-task RED/GREEN/REFACTOR table for the retro build (structural, unfixable post-hoc).
3. SUGGESTION — Add a smoke assertion for the `Enter` key (covered by the `""` read-arm, empirically verified, not automated).

## Delta Spec Sync Outcome

- Domain: `settings-navigation` — new capability; no main spec existed, so the delta spec (a full spec: 10 requirements, 12 scenarios) was copied mechanically to `openspec/specs/settings-navigation/spec.md`.
- Composition command not required (no `sdd-archive-compose` path: canonical spec absent). Native composition remains mandatory for future changes that MODIFY/REMOVE existing requirements.
- Mechanical evidence: `diff -r` delta vs. staged copy — **empty** (no differences). No existing main spec touched (`installer`, `plugin-bootstrap` unchanged).
- Non-destructive merge: no requirements removed or modified anywhere.

## Mechanical Archive Evidence

- Move: `git mv` of the tracked change folder to `openspec/changes/archive/2026-09-23-settings-category-submenus/` (rename staged; no commit created — maintainer commits explicitly).
- Readback: recursive pre-move snapshot vs. archived tree — **empty** `diff -r` (no differences).
- Active changes directory no longer contains this change.

## Artifacts in Archive

- `proposal.md` — present
- `specs/settings-navigation/spec.md` — present (delta copy, byte-identical to synced main spec)
- `design.md` — present
- `tasks.md` — present, 17/17 complete
- `verify-report.md` — present (original FAIL + re-verification PASS preserved as history)

## Source of Truth Updated

- `openspec/specs/settings-navigation/spec.md` — created (new capability spec)
- `openspec/specs/installer/spec.md`, `openspec/specs/plugin-bootstrap/spec.md` — untouched

## Engram Traceability

Archive report mirrored to Engram: project `herdr-tts`, topic_key `sdd/settings-category-submenus/archive-report`. No Engram observations were read as source material during this phase — all artifacts were read from the OpenSpec file store (file locators resolved by native status).
