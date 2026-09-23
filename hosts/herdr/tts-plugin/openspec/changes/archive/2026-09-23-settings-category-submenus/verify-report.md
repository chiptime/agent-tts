```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:13f7451bb49b7e31733f75c22ba8358ddf572456961356084a4b8ec8fd6dbdfc
verdict: fail
blockers: 0
critical_findings: 1
requirements: 8/10
scenarios: 10/12
test_command: bash scripts/smoke-tests.sh
test_exit_code: 0
test_output_hash: sha256:423218e57c39931dd29b99c52f1281af126c8be68810b417f4c5a0a0c51a67c6
build_command: bash scripts/bootstrap.sh
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Verification Report

**Change**: settings-category-submenus
**Version**: N/A (retro-documentation; no versioned spec)
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 16 |
| Tasks complete | 15 |
| Tasks incomplete | 1 (4.3 chain-strategy decision — deferred cleanup/planning task) |

### Build & Tests Execution
**Build**: ✅ Passed (`bash scripts/bootstrap.sh` exit 0 — venv fast-path no-op; `bash -n bin/herdr-tts` and `bash -n scripts/smoke-tests.sh` both clean)
```text
==> (no output; healthy venv short-circuit)
```

**Tests**: ✅ 624 passed / ❌ 0 failed / ⚠️ 0 skipped
```text
═══ RESULT: 624 passed, 0 failed ═══
exit 0
```

**Coverage**: ➖ Not available (`coverage_command` unset in openspec/config.yaml)

### Spec Compliance Matrix
| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Two-Level Navigation Model | Index lists categories | 32a (four category rows, no raw knobs, R hint) | ✅ COMPLIANT |
| Entry Points | Entries land on the index | 32a (standalone `--voice-settings`), 26e (menu `aRq`) | ✅ COMPLIANT |
| Exit Semantics | Esc backs out to index | 32d (3 H-moves, index re-render) | ✅ COMPLIANT |
| Exit Semantics | Esc from index exits | 32e (1 H-move, exit 0) | ✅ COMPLIANT |
| Restart Placement | R restarts from index | 32f (stub invoked, inline note), 26e (note once) | ✅ COMPLIANT |
| Render Contract | Single frame, transient status | 26e (4 frame writes, note once) | ✅ COMPLIANT |
| Knob Grouping Completeness | Category views are exclusive | 32b (presence + cross-view `assert_no_grep`) | ✅ COMPLIANT |
| Scoped Key Dispatch | Unknown key warns scoped | 32g (`@` warns, Voz-only hints) | ✅ COMPLIANT |
| Persistence on Cycle | In-category cycle persists | 32c (`vpq` edge→openai via `config_set`) | ✅ COMPLIANT |
| Persistence on Cycle | Write failure keeps old value | (none found) | ❌ UNTESTED |
| No-Scroll, No-Cursor | Longest view fits clamp | 32a/32b (clamp exercised, no row-count assert) | ⚠️ PARTIAL |
| Retroactive Verification | Scenario 32 proves capability | full suite green (624/624) | ✅ COMPLIANT |

**Compliance summary**: 10/12 scenarios compliant (1 UNTESTED, 1 PARTIAL)

### Correctness (Static Evidence)
| Requirement | Status | Notes |
|------------|--------|-------|
| Two-Level Navigation Model | ✅ Implemented | `settings_render` index branch renders four rows + R hint (bin/herdr-tts L4029-4034) |
| Entry Points | ✅ Implemented | `run_voice_menu` `a\|A` → `SETTINGS_CATEGORY="index"` (L3560); `run_voice_settings` init (L4073) |
| Exit Semantics | ✅ Implemented | category back-out `q\|Q\|Esc\|""` → index, return 0 (L3709-3712); index → return 1 (L3685-3686). Enter handled via `""` read-arm (empirically confirmed) |
| Restart Placement | ✅ Implemented | `R` index-only (L3692-3701); Audio `r` retention lowercase (L3803-3813) |
| Render Contract | ✅ Implemented | one `menu_cap_rows $(( DASH_LINES - 1 ))` + single `printf` (L4056-4058); WARN/NOTE bottom rows only when set (L4041-4049), cleared after render (L4079-4080, L3539-3540) |
| Knob Grouping Completeness | ✅ Implemented | voz `p g n u i` (L3717-3767) · audio `d c r s` (L3775-3822) · notif `t f w` (L3830-3903) · lectura `v a b` (L3911-3952) — 15 knobs, no overlap |
| Scoped Key Dispatch | ✅ Implemented | per-view `*)` arms warn with that view's hints (L3769, L3824, L3905, L3954) |
| Persistence on Cycle | ✅ Implemented | cycle arm guards: `if config_set …; then VAR=next; else SETTINGS_WARN=…; fi` — old value retained on failure (e.g. L3719-3723) |
| No-Scroll, No-Cursor | ✅ Implemented | `menu_cap_rows` clamps every render; no cursor/scroll code path |

### Coherence (Design)
| Decision | Followed? | Notes |
|----------|-----------|-------|
| One variable `SETTINGS_CATEGORY` nested in the `view` machine | ✅ Yes | L3682/3974 dispatch+render on `${SETTINGS_CATEGORY:-index}` |
| `settings_handle_key` returns 1 only from index | ✅ Yes | L3685-3686 vs L3709-3712 |
| Case-sensitive `R` (restart) vs lowercase `r` (retention) in separate views | ✅ Yes | L3692 / L3803 |
| Single clamped write + bottom status rows | ✅ Yes | L4056-4058, L4041-4049 |
| Per-view reads (voice_map_refresh Voz-only, config_get_retention_raw Audio-only) | ✅ Yes | L3976, L3995 |

### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ⚠️ | apply-progress absent — retro-doc change, apply out of scope (tasks.md: "Apply will NOT run") |
| All tasks have tests | ✅ | 15 implementation/verification tasks map to scenario-32 + 26e assertions |
| RED confirmed (tests exist) | ✅ | smoke-tests.sh scenario 32a-32g + 26e present and exercising the code |
| GREEN confirmed (tests pass) | ✅ | 624/624 on re-run |
| Triangulation adequate | ✅ | 32b loops four views; 32d/32e count distinct H-move expectations |
| Safety Net for modified files | ⚠️ | no apply-progress "Files Changed" table; full-suite green stands in |

**TDD Compliance**: 4/6 checks passed (2 ⚠️ attributable to retro-doc apply-absence)

### Test Layer Distribution
| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Integration (scenario-based) | 32a-32g + 26e assertions | scripts/smoke-tests.sh | hermetic bash suite |
| Unit | 0 | — | no unit runner |
| E2E | 0 | — | no browser harness |
| **Total** | 624 assertions | 1 | — |

### Changed File Coverage
Coverage analysis skipped — no coverage tool detected (`coverage_command` unset).

### Assertion Quality
| File | Line | Assertion | Issue | Severity |
|------|------|-----------|-------|----------|
| scripts/smoke-tests.sh | 2251-2256 | category rows / no-raw-knob / R-hint greps | none — real content assertions | — |
| scripts/smoke-tests.sh | 2260-2280 | cross-view `assert_no_grep` | none — proves exclusivity | — |
| scripts/smoke-tests.sh | 2285-2286 | config file grep for persisted value | none — proves persistence | — |
| scripts/smoke-tests.sh | 2293-2302 | `esc_count` H-move frame counts | none — proves single-frame + back-out | — |

**Assertion quality**: ✅ All assertions verify real behavior (no tautologies, no smoke-only renders, no ghost loops)

### Quality Metrics
**Linter**: ➖ Not available (`lint_command` unset)
**Type Checker**: ➖ Not available (`typecheck_command` unset)
**Syntax**: ✅ `bash -n` clean on `bin/herdr-tts` and `scripts/smoke-tests.sh`

### Issues Found

**CRITICAL** (1):
1. Spec scenario "Write failure keeps old value" (spec L77-79) is UNTESTED — no covering smoke assertion. The implementation is correct by inspection (each cycle arm guards `if config_set …; then VAR=next; else SETTINGS_WARN=…; fi`, so the old value is retained and an inline warning is set on failure — bin/herdr-tts L3719-3723 and sibling arms), but strict TDD requires runtime proof ("source inspection alone is never verification"). The hermetic suite never induces a write failure (the config file is always writable in the temp env). This blocks a clean archive.

**WARNING** (3):
1. No-Scroll scenario "Longest view fits clamp" (spec L85-87) is PARTIAL: the `menu_cap_rows` clamp runs on every render, but no smoke assertion verifies that the longest view (Voz, including the conditional piper/kokoro install hint) keeps knobs + hints + status lines within the clamp without truncating content.
2. Task 4.3 "Chain-strategy decision" is unchecked (15/16). Deferred delivery-strategy decision, not a code-verification gap; must resolve before apply/commit.
3. TDD cycle evidence artifact (apply-progress) is absent — expected for a retro-documentation change where apply is out of scope, so the per-task RED/GREEN/REFACTOR table cannot be audited; TDD-in-spirit is evidenced only by the green suite and test-first ordering.

**SUGGESTION** (3):
1. Add a hermetic smoke assertion for the write-failure path (e.g. point `HERDR_TTS_CONFIG_FILE` at a read-only location, or stub `config_set` to return failure) to close the UNTESTED scenario and clear the sole CRITICAL.
2. Add an explicit row-count assertion to scenario 32 (frame line count ≤ `DASH_LINES - 1` for the Voz view with the piper install hint) to fully close the no-scroll scenario.
3. Add a smoke assertion for `Enter` (back-to-index from a category, exit from the index) — the requirement names Enter explicitly; the `""` read-arm covers it (empirically verified) but no automated assertion exercises it.

### Verdict
FAIL — one spec scenario (write-failure persistence) has no passing covering test, so strict-TDD verification cannot be marked clean. Implementation and full suite (624/624) are otherwise sound; remediation is a single additional smoke assertion (or an explicit manual-verification carve-out in config).

---

# Re-verification (2026-09-22, evidence-refresh)

```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:ac15fe85070fbf918f4f69375fa9f91dabd1efaf1b37926f0d2cbd5b3c26df24
verdict: pass
blockers: 0
critical_findings: 0
requirements: 9/10
scenarios: 11/12
test_command: bash scripts/smoke-tests.sh
test_exit_code: 0
test_output_hash: sha256:5d05fca125a54bc2a2cb4f645d4997855c61723a795cd4eb124e29d359ab7f54
build_command: bash scripts/bootstrap.sh
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Re-verification Report

**Change**: settings-category-submenus
**Mode**: Strict TDD
**Re-run**: 2026-09-22 (post-remediation evidence refresh; original FAIL section preserved above as history)

### Scope
Evidence-refresh after remediation of the prior FAIL's sole CRITICAL. Re-ran the full suite myself and re-checked the status of all 3 WARNING + 3 SUGGESTION findings.

### Prior CRITICAL — RESOLVED
Spec scenario "Write failure keeps old value" (spec L77-79) is now covered at runtime by smoke scenario 32h (scripts/smoke-tests.sh L2324-2339, 7 assertions), all passing:

| # | Assertion | Proves |
|---|-----------|--------|
| 1 | `32h failed-write run exits rc=0 (fail-open)` | `[[ $? -eq 0 ]]` |
| 2 | `32h failed save warns inline` | `⚠️.*No se pudo guardar TTS_PROVIDER` |
| 3 | `32h config_set rejected the unwritable dir` | `config directory is not writable` in err.log |
| 4 | `32h old provider value still renders` | `Proveedor TTS: +edge` |
| 5 | `32h cycled value never renders` | `assert_no_grep 'Proveedor TTS: +openai'` |
| 6 | `32h config keeps the old value` | `TTS_PROVIDER="edge"` in config file |
| 7 | `32h failed write never persisted openai` | `assert_no_grep_f 'TTS_PROVIDER="openai"'` |

Mechanism: `chmod 555` on the config's parent dir makes `config_set` fail at its writability guard (bin/herdr-tts L220: `if ! mkdir -p "$dir" || [[ ! -w "$dir" ]]`); the cycle arm (bin/herdr-tts L3719-3723) then keeps `TTS_PROVIDER` unchanged and sets `SETTINGS_WARN`. This satisfies the spec verbatim: GIVEN a failing write → WHEN a cycle is attempted → THEN the old value stays with an inline warning. Negative controls (assertions 4,5,7) prove the cycled value never renders or persists. Chmod 555→755 restore (L2339) is present so suite temp cleanup succeeds.

### Build & Tests Execution
**Build**: ✅ Passed (`bash scripts/bootstrap.sh` exit 0, empty output — healthy venv short-circuit; `bash -n` clean on `bin/herdr-tts` and `scripts/smoke-tests.sh`)

**Tests**: ✅ 631 passed / ❌ 0 failed / ⚠️ 0 skipped
```text
═══ RESULT: 631 passed, 0 failed ═══
exit 0
```

**Coverage**: ➖ Not available (`coverage_command` unset)

### Spec Compliance (delta)
| Prior | Now | Scenario | Covering test |
|-------|-----|----------|---------------|
| ❌ UNTESTED | ✅ COMPLIANT | Write failure keeps old value | 32h (7 assertions) |
| ⚠️ PARTIAL | ⚠️ PARTIAL | Longest view fits clamp | 32a/32b (clamp exercised, no row-count assert) |
| ✅ | ✅ | all others | unchanged |

**Compliance summary**: 11/12 scenarios compliant, 1 PARTIAL (no-scroll). Requirements 9/10 fully compliant (Persistence on Cycle now fully covered by 32c + 32h).

### Issue Status (re-check)
**CRITICAL**: 1 → **0** (resolved by 32h).

**WARNING (3, all still open)**:
1. No-Scroll "Longest view fits clamp" remains PARTIAL — `menu_cap_rows` runs on every render, but no smoke assertion proves the longest view (Voz with conditional piper/kokoro install hint) keeps knobs + hints + status lines within the clamp without truncation.
2. Task 4.3 "Chain-strategy decision" still unchecked (16/17) — deferred delivery/planning decision by maintainer, not a code-verification gap.
3. TDD RED/GREEN table — apply-progress now EXISTS (Engram obs 9320, remediation record), but the original retro build still has no per-task RED/GREEN/REFACTOR table; structural and unfixable post-hoc (apply was out of scope per tasks.md).

**SUGGESTION (2)**:
1. ~~Add write-failure smoke assertion~~ → **RESOLVED** (now 32h).
2. Add an explicit row-count assertion to scenario 32 (frame line count ≤ `DASH_LINES - 1` for the Voz view with install hint) to fully close the no-scroll PARTIAL.
3. Add a smoke assertion for `Enter` (back-to-index from a category; exit from the index) — the requirement names Enter explicitly; the `""` read-arm covers it (empirically verified) but no automated assertion exercises it.

### TDD Compliance (re-check)
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ⚠️ | apply-progress remediation record (obs 9320) present; no per-task RED/GREEN table for retro build |
| RED confirmed (test exists) | ✅ | scenario 32h present in scripts/smoke-tests.sh (L2324-2339) |
| GREEN confirmed (test passes) | ✅ | 631/631 on re-run; 7/7 32h assertions pass |
| Triangulation adequate | ✅ | 32h carries negative controls (openai never renders/persists) |

**TDD Compliance**: 3/4 checks passed (1 ⚠️ structural)

### Assertion Quality (32h)
| File | Line | Assertion | Issue | Severity |
|------|------|-----------|-------|----------|
| scripts/smoke-tests.sh | 2332 | rc=0 fail-open | none | — |
| scripts/smoke-tests.sh | 2333 | inline ⚠️ warn | none | — |
| scripts/smoke-tests.sh | 2334 | `config_set` dir guard in err.log | none | — |
| scripts/smoke-tests.sh | 2335-2338 | old value renders + persists; cycled value absent | none — negative controls present | — |

**Assertion quality**: ✅ real behavior with negative controls (no tautologies, no ghost loops, no smoke-only renders)

### Verdict
PASS WITH WARNINGS — 0 CRITICAL, 3 WARNING, 2 SUGGESTION. The sole prior CRITICAL is closed by scenario 32h; full suite 631/631 (exit 0). Remaining findings are non-blocking: 1 PARTIAL no-scroll assertion gap, 1 deferred delivery decision (task 4.3), 1 structural retro-doc TDD table.
