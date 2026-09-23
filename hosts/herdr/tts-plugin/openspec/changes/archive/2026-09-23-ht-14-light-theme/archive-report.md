# Archive Report — ht-14-light-theme

- **Archived**: 2026-09-23 (inline archive by orchestrator: sub-agent dispatch was deterministically refused by the runtime gate after 3 identical attempts; all content operations were native — `gentle-ai sdd-archive-compose` exit 0 for the settings-navigation merge, shell `cp`/`mv` with mandatory `diff -r` readbacks, both empty — no artifact bytes passed through a model path)
- **Artifact store**: hybrid (repo + Engram)

## Final State at Close

- **Implementation**: complete — 29/29 tasks checked in `tasks.md` (rank-1 source). Commits on branch `feature/ht-14-light-theme`: `b1c11d4` (Slice A: `TTS_THEME` managed key + `theme_color` sole color-SGR emitter, numeric maps + `printf -v`) and `2f1c013` (Slice B: Apariencia category, knob `t` cycle, i18n EN/ES, README). `main` untouched; delivery = single PR (maintainer-resolved 2026-09-23).
- **Verification**: PASS WITH WARNINGS — 0 CRITICAL, 3 WARNING, 3 SUGGESTION (per `verify-report.md` / Engram #9408, at verification time). Suite **779 passed / 0 failed** (699 baseline + 80 new, scenarios 38a–38e), independently reproduced 3× (apply run, orchestrator run, verify run). RED evidence: pre-change `f2be6e8` held exactly 8 literal color SGR at L3852–3855 / L3919–3922; post-change 0 hits with no exemption list.
- **Budget**: size:exception APPROVED by maintainer 2026-09-23 — actual 407 changed lines (388+/19−), 7 over the 400-line budget; overage entirely in test assertions (smoke scenario 38 carries 80 checks; file grew 268 vs ≈190 forecast).
- **Delivery status at archive time**: PR not yet created (orchestrator handles push + PR after archive, per maintainer's single-PR decision).

## Deferred by Design (not blockers)

- Final light values for `ok` (32), `error` (31), `title` (1) are provisional; spec fixes only the contrast minimums (`muted=30`, `accent=34`, `warn=1;33`). Single edit point: the light `case` in `theme_color`. Gated on the maintainer's deliberate light-scheme week before marking "Implementada" (PRD metric).
- Index/knob column padding frozen by the 38c `-F` assertions as implemented.

## Open Findings at Close

- WARNING: 407 vs 400 lines (resolved by approved exception, recorded above).
- WARNING: three GREEN-time test-harness corrections (production was correct): bash 5.2+ `%q` renders ESC as `\E` (both sides `%q`-encoded), EN label padding (15 spaces), cold-start keys `'ttq'` not `'tq'`.
- WARNING: design line anchors ~30 lines stale vs live tree (placements done by anchor content; verified empirically).
- SUGGESTION (pre-existing, NOT HT-14): `smoke-tests.sh` scenario 20 calls `lib_run` before its definition, so its takeover path is not exercised through `lib_run` (present at `f2be6e8`).

## Specs Synced (source of truth updated)

| Domain | Action | Evidence |
|---|---|---|
| `terminal-theme` | Created — full spec, 8 requirements / 12 scenarios | mechanical `cp` via mktemp + `diff -r` empty |
| `settings-navigation` | Updated — 3 MODIFIED + 1 ADDED requirement (10 → 11 total); untouched requirements preserved byte-for-byte | native `gentle-ai sdd-archive-compose` exit 0 |

Composition note: the delta's three MODIFIED headings originally extended the canonical smoke annotations in the requirement names (`32a, 32b` → `32a, 32b, 38c`), which the name-matching composer correctly refused; headings were aligned back to canonical names (smoke-38c coverage remains in the scenario bodies) and composition then applied cleanly. The ADDED requirement "Appearance Category and Theme Knob (Smoke: 38c, 38e)" kept its heading.

## Archive Contents (moved via snapshot-verified move, `diff -r` empty)

- proposal.md — present
- exploration.md — present (gatekeeper-corrected: baseline 699/699 verified; tty-failure claim retracted)
- specs/terminal-theme/spec.md, specs/settings-navigation/spec.md — present
- design.md — present
- tasks.md — present, 29/29 complete, 0 pending; chain strategy resolved (single PR); size:exception recorded
- verify-report.md — present (intermediate snapshot; see Final State above for close state)

## Engram Traceability

Observations read for this report: #9386 (explore), #9387 (proposal), #9389 (spec), #9390 (design), #9393 (tasks), #9408 (verify-report). This archive report: `sdd/ht-14-light-theme/archive-report`.

## SDD Cycle Complete

Implementation: complete and verified (779/779 ×3, PASS WITH WARNINGS). Unfinished tasks: none. Unresolved findings: deferred light-value tuning (by design, gated on the light-scheme week) and the pre-existing scenario-20 quirk (out of scope, unowned by this change).
