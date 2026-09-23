# Verification Report — HT-14 Configurable Light Theme

- **Change**: `ht-14-light-theme`
- **Branch**: `feature/ht-14-light-theme` (commits `b1c11d4` Slice A + `2f1c013` Slice B + README)
- **Mode**: Strict TDD (runner: `bash scripts/smoke-tests.sh`)
- **Verdict**: **PASS WITH WARNINGS** (0 CRITICAL, 3 WARNING, 3 SUGGESTION)
- **Verified**: 2026-09-23, sub-agent `sdd-verify` (read-only on production code)

## 1. Suite Result (from my run)

| Command | Exit | Result |
|---|---|---|
| `bash scripts/smoke-tests.sh` | `0` | **779 passed, 0 failed** |
| `bash scripts/bootstrap.sh` | `0` | no output (empty) |

- `test_output_hash`: `445f3132fbef7b7b3cf9d624af802b56f3d75197d06ad1df64175a3e2f8d3da6`
- `build_output_hash`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty)
- 699 baseline + 80 new (scenario 38a–38e) = 779. Exact count of `ok 38a/38b/38c/38d/38e` lines: 80.

## 2. Requirement Compliance (RF / RNF)

| Requirement | Proving assertion(s) | Observed |
|---|---|---|
| RF-HT-14-1 managed key | 38a `config_set accepts dark (rc 0)` / `accepts light (rc 0)` / `solarized rejected (rc 1)` / `rejected call leaves the stored config byte-unchanged` / `rejected call creates no .bak` | ✅ green |
| RF-HT-14-2 sole emitter, dark byte-identity | 38d `no literal color SGR anywhere`; 38b four dark byte pins + `dark frame carries no other foreground color` | ✅ green (38d = 0 hits) |
| RF-HT-14-3 light map minimums | 38e resolver byte map (light `muted=30 accent=34 warn=1;33`) + `light frame: muted 30 / accent 34 / warn bold 1;33` | ✅ green |
| RF-HT-14-4 Apariencia knob `t` | 38c cycle unit, `t persisted TTS_THEME=light`, `same-popup re-render shows Light`, `failed save warns inline` / `old theme still renders`, `unknown key warns scoped` | ✅ green |
| RF-HT-14-5 surfaces + plain text/resets untouched | 38b `plain text untouched (dark vs light differ only in color)`; roster rows 38e | ✅ green |
| RF-HT-14-6 bilingual strings | 38c `ES view title` / `ES knob row` / `ES dark label`; i18n grep (8 keys × EN+ES) | ✅ green |
| RF-HT-14-7 smoke tests RED-first | RED verified independently: pre-change `f2be6e8` has exactly 8 literal color SGR at L3852–3855/L3919–3922 | ✅ verified |
| RF-HT-14-8 basic SGR only | 38d `no 256-color/truecolor SGR` (0 hits); diff shows no new files/deps | ✅ green |
| RNF-HT-14-1 zero cost | `theme_color` is fork-free `printf -v`; grep `$(theme_color` = 0 in loops | ✅ verified |
| RNF-HT-14-2 born off | 38b `unset ≡ dark (born-off default)` + `round-trip restores the dark baseline` | ✅ green |
| RNF-HT-14-3 auditability (grep gate) | 38d whole-file gate, no exemption list | ✅ green |
| RNF-HT-14-4 suite stays green | RESULT 779/0 with default dark | ✅ green |

## 3. Spec Scenario Mapping (22 scenarios)

### terminal-theme (12)

| # | Scenario | Executed assertion(s) | Result |
|---|---|---|---|
| 1 | Accept managed values | 38a accept dark/light rc 0, persist, markers, `.bak` preserved, file sourceable | ✅ proven |
| 2 | Reject unmanaged values | 38a `solarized rejected (rc 1)`, `rejection names the allowed values`, config byte-unchanged, no `.bak` | ✅ proven |
| 3 | Dark snapshot identity | 38b four byte pins + `no other foreground color` | ✅ proven |
| 4 | Grep gate | 38d `no literal color SGR anywhere` (0 hits) | ✅ proven |
| 5 | Light emission | 38e `32m`/`30m`/`34m`/`1;33m` in light frame | ✅ proven |
| 6 | Compound warn width-safe | 38e `compound 1;33 strips cleanly (visible-width parity)` | ✅ proven |
| 7 | Roster adopts theme | 38e roster working/done/idle rows carry light map | ✅ proven |
| 8 | Plain text untouched | 38b `plain text untouched (dark vs light differ only in color)` | ✅ proven |
| 9 | No wide-gamut codes | 38d `no 256-color/truecolor SGR` (0 hits) | ✅ proven |
| 10 | No per-cell forks | static: frame locals + grep `$(theme_color` = 0 (verified by inspection) | ✅ verified (inspection) |
| 11 | Round-trip restores baseline | 38b `round-trip restores the dark baseline` | ✅ proven |
| 12 | Suite stays green | RESULT 779/0 | ✅ proven |

### settings-navigation (10)

| # | Scenario | Executed assertion(s) | Result |
|---|---|---|---|
| 1 | Index lists categories | 38c `t opens the Appearance view` + 32a index lists Voice/Audio/Notifications/Auto-read/language | ✅ proven |
| 2 | t opens Apariencia | 38c `t opens the Appearance view`, `index → appearance → index (3 H-moves)` | ✅ proven |
| 3 | Category views are exclusive | 32b voice/audio/notif/reading views free of foreign rows | ✅ proven |
| 4 | t keys are view-scoped | 38c `scoped warning names no other category's keys` (notifications `t` = ntfy topic untouched) | ✅ proven |
| 5 | Single frame, transient status | 38c `note on the post-cycle frame` + `note is transient` (count 1) | ✅ proven |
| 6 | Copy follows interface language | 38c ES `Ajustes · Apariencia` / `Tema` / `Oscuro` | ✅ proven |
| 7 | Cycle persists across restart | 38c `t persisted TTS_THEME=light` + `10 cold starts alternate dark↔light` | ✅ proven |
| 8 | Immediate re-render | 38c `same-popup re-render shows Light`, 4 H-moves | ✅ proven |
| 9 | Write failure keeps old value | 38c `failed save warns inline` + `old theme still renders` + `cycled value never renders` + config unchanged | ✅ proven |
| 10 | Unknown key warns scoped | 38c `unknown key warns scoped to the theme knob` + `names no other category's keys` | ✅ proven |

## 4. Design Hard Constraints

| Constraint | Check | Result |
|---|---|---|
| `theme_color` sole emitter | grep comment-stripped `bin/herdr-tts` for `\(033\|e\)\[[0-9;]*(3[0-7]\|9[0-7])m` → **0** hits, no exemption list | ✅ |
| byte-untouched `roster_fmt`/`dash.engine`/resets/icons | `git diff` shows only `e_color=`/`color=` lines changed; `roster_fmt` assignment, `dash.engine` template, `\033[0m` resets, `▶ ✔ ·` icons unchanged | ✅ |
| pre-I/O value gate | gate sits between quote guard and `mkdir -p`; 38a `rejected call creates no .bak` | ✅ |
| precedence config.env > env > dark | 38b `precedence: config.env light outranks env dark` | ✅ |
| zero-fork render loops | `$(theme_color` = 0 occurrences; 4 frame locals resolved once | ✅ |
| no truecolor/256 | `(38\|48);(5\|2);` = 0 hits | ✅ |
| hermetic env | suite uses `new_env` + `LINES=40 COLUMNS=110`; no host-tty dependence | ✅ |
| out-vars hardcoded internal names | 4 call sites: `c_ok c_warn c_accent c_muted` | ✅ |

## 5. Tasks Audit (29/29)

All 29 tasks `[x]` in `tasks.md`. Independently re-verified:

1. **1.1 baseline 699** — design/tasks record 699/699; my run yields 779 = 699 + 80 (consistent).
2. **2.1 / 3.6–3.7 RED→GREEN 38d** — pre-change `f2be6e8` = 8 literal hits (L3852–3855, L3919–3922); current = 0. ✅
3. **3.4 `theme_color` resolver** — `theme_color v accent` → `$'\E[36m'`; `TTS_THEME=light … muted` → `$'\E[30m'`; unknown → `$'\E[0m'`. ✅
4. **5.4 `settings_cycle_value theme`** — dark→light, light→dark, bogus→dark. ✅
5. **5.1/5.2 i18n** — 8 new keys each present exactly once in `TT_EN` and `TT_ES`. ✅

## 6. Regression Surfaces

32a, 32b, 32d, 32g, 36e, 37c all green in my run (grep of `smoke-out.txt` for those labels → all `ok`). No regressions.

## 7. README

- Category table = exactly 5 categories (`v` Voz, `a` Audio, `n` Notificaciones, `r` Lectura automática, `t` 🎨 Apariencia) + global `l`/`R`.
- Managed-keys bullet includes `TTS_THEME` (L794); live-vs-restart bullet classifies `t` (L790/L796).
- Grep for stale `four categor|4 categor|15 knob|fifteen` → **no matches**.

## 8. TDD Compliance (Strict Mode)

- **RED evidence**: present and independently verified. Pre-change baseline has exactly 8 literal color SGR at the predicted lines; suite progression recorded (699/699 → RED 722/67 → Slice A 746/0 → final 779/0).
- **GREEN evidence**: independently re-confirmed (my run = 779/0, exit 0).
- **Format deviation**: apply-progress reports TDD evidence as a narrative suite-progression, not the structured per-task `RED/GREEN/TRIANGULATE/SAFETY-NET/REFACTOR` table the strict-tdd module expects. Substantive evidence is complete and verified; only the table format is absent. → WARNING (format), not CRITICAL (evidence exists).

## Findings

### CRITICAL
None.

### WARNING

1. **Review budget overage: 407 changed lines vs 400** (forecast ≈300). Overage entirely in `scripts/smoke-tests.sh` (268 vs ≈190): scenario 38 carries 80 assertions (10-run cold-start loop, chmod-555 write-failure harness, bilingual run, scoped-warn extraction). Maintainer already resolved to a single PR; a `size:exception` or chaining the two existing slice commits are the open options.
2. **GREEN-time harness corrections (production was right)**: (a) bash 5.2+ `%q` renders ESC as `\E` not `\033` → both sides re-encoded; (b) EN label pins needed value-column padding; (c) cold-start keys must be `ttq` not `tq`. Test-harness friction only; no production defect.
3. **Design line anchors ~30 lines stale** (DEVIATION 3): placed by anchor content per design intent; 38c block was temporarily excised from Slice A so the boundary landed green. No functional impact.

### SUGGESTION

1. **Pre-existing harness quirk (not from HT-14)**: `scripts/smoke-tests.sh` scenario 20 calls `lib_run` at L236/L242 *before* `lib_run()` is defined at L260, emitting two `lib_run: command not found` stderr lines. Present on `f2be6e8` too; the assertions still pass but scenario 20's takeover path is effectively not exercised through `lib_run`. Consider moving the `lib_run` definition above scenario 20.
2. **Deferred light values** (design "Open Questions"): `ok`, `error`, `title` light values may be tuned during the deliberate light-scheme review week — the map is the single edit point.
3. **Column padding** of the new index/knob rows is frozen by `-F` assertions (32a/38c) — if copy length changes, those pins must move in lockstep.
