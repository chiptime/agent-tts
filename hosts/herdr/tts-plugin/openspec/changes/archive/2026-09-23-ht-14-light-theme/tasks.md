# Tasks: HT-14 Configurable Light Theme

RED-first per the design's dependency order: author scenario 38 (38d → 38a → 38b → 38e → 38c), then implement (defaults → config_set → theme_color → dashboard_render → i18n → cycle → dispatch → render/index). Baseline 699/699 (verified 2026-09-23). Suite: `bash scripts/smoke-tests.sh` from repo root. Scenario 38 lives at `scripts/smoke-tests.sh` ~L2886, before the RESULT footer; all new scenarios use `new_env` with hermetic `LINES=40 COLUMNS=110`.

README decision: included, by repo convention — README.md documents every settings category (table L741-746) and the managed-key list (bullet L787); a fifth category with a new managed key would leave it factually stale (same reasoning as the archived settings-category-submenus task 4.1). The PRD demands no README edits; convention does.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ≈300 (280–360): bin/herdr-tts ≈110, smoke-tests ≈190, README ≈12 |
| Per slice | Slice A ≈200 (bin 58 + smoke 120); Slice B ≈140 (bin 52 + smoke 75 + README 12) |
| 400-line budget risk | Medium — whole change fits one PR at midpoint; within ~10% of budget at the top end |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 Slice A (resolver + dark identity) → PR 2 Slice B (settings surface + README) |
| Delivery strategy | ask-on-risk |
| Chain strategy | resolved 2026-09-23 by maintainer: **single PR** (≈300 lines fit the 400 budget; no chain, no size:exception). Slices remain work-unit commit boundaries inside the one PR. |

Decision needed before apply: No — resolved 2026-09-23 (single PR). size:exception APPROVED by maintainer 2026-09-23: actual 407 lines (388+/19-), 7 over the 400 budget, overage entirely in test assertions (80 new checks in scenario 38).
Chained PRs recommended: Yes (slices stay revertible work-units, delivered in one PR)
Chain strategy: N/A — single PR
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Slice A — resolver + dark identity: 38a/38b/38d/38e + core wiring | PR 1 (base: tracker branch or main, per pending chain decision) | `bash scripts/smoke-tests.sh` — assertions labeled 38a/38b/38d/38e | `lib_run 'theme_color x accent; printf "%q\n" "$x"'` byte pins; dashboard capture under `new_env` | Revert the Slice A commit (bin core hunks + smoke helpers + 38a/b/d/e blocks); feature invisible to users — only `config_set` can set light |
| 2 | Slice B — settings surface: 38c + i18n + dispatch/render + README | PR 2 (base: PR 1 branch) | `bash scripts/smoke-tests.sh` — assertions labeled 38c | `printf 'ttq' \| bin/herdr-tts --voice-settings`; 10 sequential cold-start runs | Revert settings slice (i18n/dispatch/render hunks + 38c block + README section); runtime rollback: `config_set TTS_THEME dark` |

## Phase 1: Baseline & Hermetic Harness (Slice A)

- [x] 1.1 Run `bash scripts/smoke-tests.sh` (no edits): confirm 699 passed / 0 failed, exit 0 before touching anything.
- [x] 1.2 Add `strip_ansi <file>` and `norm_frame <file>` helpers (blanks `HH:MM:SS` and `Nm ago`) beside the `visible_stats`/`esc_count` python heredocs in `scripts/smoke-tests.sh` (~L145). Verify: suite still 699/699 (helpers unused = zero delta).

## Phase 2: RED — Author Scenario 38 (design order; authoring ≠ commit order)

The 38c block is authored here but committed with PR 2 so each chained PR lands green.

- [x] 2.1 38d static gate: 37c comment-strip (`grep -v '^[[:space:]]*#'`) + `assert_no_grep` for `\(033|e)\[[0-9;]*(3[0-7]|9[0-7])m` and for `(38|48);(5|2);`. RED proof: first pattern fails with exactly 8 hits (`bin/herdr-tts` L3852-3855, L3919-3922), second matches 0.
- [x] 2.2 38a managed key: `lib_run` `config_set TTS_THEME dark|light` → rc 0, value persists (markers + per-process `.bak` preserved, 36e pattern); `solarized` → rc 1, stderr names 'dark'/'light', stored config byte-unchanged, no `.bak` from the rejected call, file still bash-sourceable. RED: unmanaged-key rejection.
- [x] 2.3 38b dark byte-identity, differential capture in one hermetic run (unset / dark / light): `grep -F` pins `$'\033[32m'`, `$'\033[33m'`, `$'\033[36m'`, `$'\033[90m'` in the dark capture; `assert_no_grep` proves no other foreground color; `diff` of `norm_frame` unset-vs-dark empty; `diff` of `strip_ansi` dark-vs-light empty (plain text untouched); round-trip after `config_set TTS_THEME dark` equals the baseline captures. RED: `theme_color: command not found`.
- [x] 2.4 38e light map: `lib_run` exact `%q` byte assertions, 6 tokens × 2 themes (dark 32/33/31/36/90/1; light 32/1;33/31/34/30/1; unknown token → `0`; unknown theme value → dark branch); light dashboard capture contains `32m`, `30m`, `34m`, `1;33m`; roster rows carry the light map; `visible_stats` max-width parity dark vs light (compound `1;33m` strips cleanly). RED: resolver absent.
- [x] 2.5 38c navigation & copy: index `t` opens Apariencia (`settings.view.appearance` title renders); index row after the reading row; exit hint ` q/Esc quit` still on the index frame (11 rows ≪ 23-row clamp — no-scroll proof); ES run asserts `Apariencia`/`Tema`; in-view `t@q` warns scoped, names only the theme knob and no other category's keys. RED: `t` hits the index `*)` unknown-key arm.
- [x] 2.6 38c cycle & persistence: `printf 'ttq' | "$SCRIPT" --voice-settings` shows both `Theme: Dark` and `Theme: Light` in one capture (same-popup re-render); `esc_count $'\033[H'` = 4 frames; `config.env` stores `TTS_THEME=light`; `SETTINGS_NOTE` on the post-cycle frame, cleared on the next; write failure via `chmod 555` config dir (32h technique): old value kept + `SETTINGS_WARN`; 10 sequential cold-start `--voice-settings` runs alternate dark↔light (restart persistence). RED: cycle assertions fail.

## Phase 3: Slice A GREEN — Core Implementation (bin/herdr-tts)

- [x] 3.1 Defaults: `DEFAULT_THEME="dark"` with comment beside `DEFAULT_LANG` (L84-85); `TTS_THEME="${TTS_THEME:-$DEFAULT_THEME}"` + `[[ "$TTS_THEME" == "light" ]] || TTS_THEME="dark"` at ~L150 (style of L149/L175). Prove: 38b unset≡dark diff green; precedence config.env > env > dark.
- [x] 3.2 `config_set` admission: add `TTS_THEME` to the managed-key pattern (L1198) and the allowed-list diagnostic (L1200). Prove: 38a accept rc 0.
- [x] 3.3 `config_set` value gate: second `case` between the quote guard (ends L1207) and the dir-writability check (starts L1209) — reject non-dark/light with `config_set: refused: TTS_THEME must be 'dark' or 'light' (got '<v>')`, rc 1, before `mkdir -p`/`.bak`/tmp. Prove: 38a reject (config unchanged, no `.bak`).
- [x] 3.4 Create `theme_color()` after `config_set` closes (L1270), before `is_playing` (~L1275): numeric-only maps, `printf -v "$1" '\033[%sm'`, unknown token → `0`, unknown theme → dark branch; call sites use hardcoded internal var names only (review-blocking constraint). Prove: 38e resolver assertions green.
- [x] 3.5 `dashboard_render` locals: extend the L3791 local declaration with `c_ok c_warn c_accent c_muted` + 4 `theme_color` calls before any loop; `roster_fmt` and `dash.engine` templates byte-untouched. Prove: 38b dark pins green.
- [x] 3.6 Engine-state case L3852-3855 → `"$c_ok"` `"$c_warn"` `"$c_accent"` `"$c_muted"` (reset positions untouched). Prove: 38d hits drop 8 → 4.
- [x] 3.7 Roster-state case L3919-3922 → same locals (icons `▶ ✔ ·` untouched). Prove: 38d hits 4 → 0, gate green.

## Phase 4: Slice A Gate (PR 1 boundary)

- [x] 4.1 `bash scripts/smoke-tests.sh`: 38a/38b/38d/38e green; full suite = 699 baseline + new, 0 failed; dark byte-identity held.
- [x] 4.2 Boundary check: feature invisible in UI (38c still RED until Slice B, as expected); Slice A commit contains only bin core hunks + smoke helpers + 38a/38b/38d/38e blocks.

## Phase 5: Slice B GREEN — Settings Surface (bin/herdr-tts)

- [x] 5.1 TT_EN keys (~L437, ~L465), exact copy from the i18n table in `openspec/changes/ht-14-light-theme/design.md` (read-only): `settings.index.appearance`, `settings.view.appearance`, `settings.row.theme`, `settings.theme.label.dark`, `settings.theme.label.light`, `settings.row.theme_note` (roster-adoption disclosure), `settings.appearance.theme_note`, `settings.unknown_key.appearance`. Prove: 38c EN assertions.
- [x] 5.2 TT_ES mirrors (~L914, ~L942): `Apariencia`/`Tema`/`Oscuro`/`Claro` + roster note. Prove: 38c ES run.
- [x] 5.3 `settings.unknown_key.index` EN (L401) + ES (L878) gain `t appearance` / `t apariencia`. Prove: 32g stays green with the enriched hint.
- [x] 5.4 `SETTINGS_THEMES=(dark light)` at L4651; `theme)` arm at L4684; `theme` added to the `settings_cycle_value` unknown-key diagnostic (L4699). Prove: `settings_cycle_value theme` alternates dark↔light, unknown current wraps to dark (unit via `lib_run`).
- [x] 5.5 Index dispatch `t|T) SETTINGS_CATEGORY="appearance" ;;` after the `r)` arm (~L4748); `T` must not shadow the `R` restart chord. Prove: 38c `t` opens the view.
- [x] 5.6 `appearance)` arm in the category dispatch (~L5031): knob `t|T` → `settings_cycle_value theme`, `config_set TTS_THEME "$next"`, flip `TTS_THEME` + `SETTINGS_NOTE` on success; `SETTINGS_WARN` keeps old value on failure (knob-p pattern L4788-4795). Prove: 38c cycle + write-failure assertions.
- [x] 5.7 `appearance)` arm in `settings_render` (~L5099) + index row `settings.index.appearance` after the reading row (~L5110), padding aligned with sibling rows; dynamic label lookup like `settings.lang.label.*`. Prove: 38c render, no-scroll, bilingual copy.

## Phase 6: Full-Suite Gate

- [x] 6.1 `bash scripts/smoke-tests.sh`: 0 failed; total = 699 + all scenario-38 assertions; 38a-38e all green.
- [x] 6.2 Regression surfaces: 32a/32b/32d/32g/36e/37c assertions green; hermetic invariants hold (`new_env` stubs intact, no host-tty dependence).

## Phase 7: Documentation (README, convention-driven)

- [x] 7.1 `README.md` category table (L741-746): add the `t` 🎨 Apariencia row; add `### 🎨 Apariencia (t)` subsection with the knob table (cycles dark↔light; roster adopts the plugin theme regardless of pane profiles), shaped like the sibling subsections.
- [x] 7.2 `README.md` managed-keys bullet (L787) gains `TTS_THEME`; live-vs-restart bullet (L789) classifies `t` per behavior verified during apply (popup re-renders instantly; dashboard adopts on daemon restart) — write what the code does.
- [x] 7.3 Consistency sweep: `grep -n` README.md for stale four-category / 15-knob claims about Ajustes; none remain (five categories / 16 knobs). Prove: grep returns nothing about the old counts.
