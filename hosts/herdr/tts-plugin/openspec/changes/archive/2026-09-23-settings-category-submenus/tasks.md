# Tasks: Settings Category Submenus (Two-Level Ajustes Navigation)

Retro change: code exists green (624/624, 2026-09-22); verification tasks only. Apply will NOT run; commits deferred by maintainer. `[x]` = verified now.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~600 authored: bin ~370/813, smoke ~150/308, README ~80/83 (mixed tree) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 tests → PR 2 bin core → PR 3 README |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

Mixed tree (HT-02/pm-01): slice per unit, never whole-tree.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test | Runtime harness | Rollback boundary |
|------|------|-----------|--------------|-----------------|-------------------|
| 1 | Scenarios 32+26e | PR 1 (base: feature branch) | smoke suite | scenario 32 pipes | revert smoke slice |
| 2 | Two-level core | PR 2 (base PR 1) | smoke suite | `printf 'v\033q' \| bin/herdr-tts --voice-settings` | revert bin settings slice |
| 3 | README tables | PR 3 (base PR 2) | grep README | N/A (docs) | revert README section |

## Phase 1: Baseline & State

- [x] 1.1 Run `bash scripts/smoke-tests.sh` (read-only): 624 passed / 0 failed, exit 0.
- [x] 1.2 `SETTINGS_CATEGORY="index"` init at both entrypoints: `run_voice_menu` `a|A` (bin/herdr-tts L3483-3486), `run_voice_settings` (L3979-3993).

## Phase 2: Navigation & Dispatch

- [x] 2.1 Index model: four category rows, zero knob rows, `R` hint (32a, smoke-tests.sh L2248-2256).
- [x] 2.2 Entry points: standalone `--voice-settings` (32a); menu `a` → index via `aRq` (26e, L1858-1871).
- [x] 2.3 Exit semantics: category Esc → index, 3 H-moves (32d, L2288-2296); index Esc exits after 1 frame (32e, L2298-2302).
- [x] 2.4 Restart placement: `R` index-only (L3602-3611); Audio `r` retention lowercase (L3713-3715); 32f+26e stubs.
- [x] 2.5 Scoped dispatch: `settings_handle_key` rc=1 only from index (L3592-3617); back-out returns 0 (L3618-3623); unknown key warns scoped (32g, L2319-2322).

## Phase 3: Render & Persistence

- [x] 3.1 One clamped write/frame: `menu_cap_rows` (L3966), single printf (L3968); 26e = 4 writes.
- [x] 3.2 WARN/NOTE bottom rows only when set (L3951-3959), cleared after render (L3464-3465, L3989-3990); 26e note once.
- [x] 3.3 Grouping: 15 knobs partitioned voz `p g n u i` / audio `d c r s` / notif `t f w` / lectura `v a b`; cross-view `assert_no_grep` (32b, L2258-2280).
- [x] 3.4 Cycle persists: `vpq` edge→openai via `config_set` (32c, L2282-2286); write failure keeps old value + WARN (L3629-3633) — runtime evidence: 32h (chmod 555 config dir → `config_set` dir guard fails; ⚠️ warn inline, value stays `edge`, file unwritten).
- [x] 3.7 Remediation (2026-09-22): closed verify-report CRITICAL — spec L77-79 "Write failure keeps old value" now covered by scenario 32h in `scripts/smoke-tests.sh` (7 assertions; suite 631/631, 0 failed). Test-only change; no production edits.
- [x] 3.5 Per-view reads: `voice_map_refresh` Voz-only (L3886); `config_get_retention_raw` Audio-only (L3905).
- [x] 3.6 No-scroll: every view fits the clamp (32a/32b).

## Phase 4: Documentation & Forward Contract

- [x] 4.1 README settings section: index table + four category tables + navigation/`R`/managed-write bullets (README.md L659-709, read-only).
- [x] 4.2 Forward contract: future knobs go scenario-32 RED first, then dispatch+render pair in one edit (design.md).
- [x] 4.3 Chain-strategy decision: resolved 2026-09-23 as NOT APPLICABLE — code and change artifacts were already delivered via direct-main on `origin/main` before the decision came due (retro change, verified 631/631 green, no feature branches exist). Retroactive 3-PR chain rejected: it would require rewriting pushed history for zero benefit. Future changes set chain strategy at tasks phase, before apply.
