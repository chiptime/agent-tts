# Exploration: ht-14-light-theme (Configurable Light Theme for herdr-tts)

### Current State

herdr-tts is a single pure-bash monolith executable (`bin/herdr-tts`) backed by an isolated Python bridge (`lib/tts_engine.py`) and validated by a hermetic smoke-test suite (`scripts/smoke-tests.sh`).

In the current codebase, all terminal render surfaces that present color use hardcoded ANSI Select Graphic Rendition (SGR) escape sequences optimized exclusively for dark-background terminals. On light-background terminal emulators, dimmed text rendered with SGR `90` (bright black / dark gray) is virtually unreadable against white or light gray backgrounds, and cyan SGR `36` exhibits insufficient contrast.

#### 1. Drift Analysis vs. PRD HT-14 Citations

The PRD (`docs/prds/HT-14-tema-claro.md`) cites line numbers from an earlier revision (~5,406 lines). The current codebase has grown to 6,862 lines (+1,456 lines) due to recent features landed on `main`:
- UI language cycle and English-first i18n (`HERDR_TTS_LANG`, scenario 36)
- Agent skill distribution (`herdr-tts skill install`, scenario 35)
- Package installer and bootstrap pinning (`scripts/install.sh`, `scripts/bootstrap.sh`, scenarios 33-34)
- Dynamic provider catalog lookup from engine (`RF-HT-13`, scenario 37)

Despite the overall line growth, the core functions and blocks cited in PRD HT-14 have experienced **zero internal drift**:

| Feature / Location | PRD Citation | Current Tree Location | Drift Status |
|---|---|---|---|
| Dashboard engine state colors | `bin/herdr-tts` L3852–3855 | `bin/herdr-tts` L3852–3855 | **Exact match (0 lines drift)** |
| Roster agent state colors | `bin/herdr-tts` L3919–3922 | `bin/herdr-tts` L3919–3922 | **Exact match (0 lines drift)** |
| i18n bold title strings (`TT_EN`) | `bin/herdr-tts` L433–437 | `bin/herdr-tts` L433–437 | **Exact match (0 lines drift)** |
| i18n bold title strings (`TT_ES`) | `bin/herdr-tts` L910–914 | `bin/herdr-tts` L910–914 | **Exact match (0 lines drift)** |
| Menu title strings | `bin/herdr-tts` L393 (EN), L870 (ES) | `bin/herdr-tts` L393 (EN), L870 (ES) | **Exact match (0 lines drift)** |
| `config_set` mechanism | `bin/herdr-tts` L1195–1270 | `bin/herdr-tts` L1195–1270 | **Exact match (0 lines drift)** |
| `config_set` whitelist | `bin/herdr-tts` L1197–1203 | `bin/herdr-tts` L1197–1203 | **Exact match (0 lines drift)** |
| Provider knob `p` reference pattern | `bin/herdr-tts` L4788–4795 | `bin/herdr-tts` L4788–4795 | **Exact match (0 lines drift)** |
| `PALETTE_*` dimension variables | `bin/herdr-tts` L4159–4161 | `bin/herdr-tts` L4159–4161 | **Exact match (0 lines drift)** |
| Monolith line count | ~5,406 lines | 6,863 lines | +1,457 lines (new features on main) |
| Smoke test scenario count | Up to scenario 32 | Scenarios 0 to 37c | +5 scenario blocks (699 total assertions) |

#### 2. Verified Complete Inventory of ANSI SGR Sequences in `bin/herdr-tts`

An exhaustive AST and regex scan (`\[([0-9]+;)*[0-9]+m`) across all 6,863 lines of `bin/herdr-tts` reveals that literal color SGR sequences appear in **only two functional locations** in the entire codebase (dashboard engine state and dashboard roster state), plus standard reset sequences (`\033[0m`) and title bold attributes (`\e[1m`).

| File:Line | Code Snippet | Semantic Token | Current SGR Code | Description |
|---|---|---|---|---|
| `bin/herdr-tts:3852` | `playing) e_color=$'\033[32m' ;;` | `ok` | `\033[32m` (green) | Dashboard engine playing state |
| `bin/herdr-tts:3853` | `paused) e_color=$'\033[33m' ;;` | `warn` | `\033[33m` (yellow) | Dashboard engine paused state |
| `bin/herdr-tts:3854` | `synthesizing) e_color=$'\033[36m' ;;` | `accent` | `\033[36m` (cyan) | Dashboard engine synthesizing state |
| `bin/herdr-tts:3855` | `*) e_color=$'\033[90m' ;;` | `muted` | `\033[90m` (bright black) | Dashboard engine idle / stopped / unknown |
| `bin/herdr-tts:3919` | `working) color=$'\033[32m'; icon="▶" ;;` | `ok` | `\033[32m` (green) | Roster agent working state |
| `bin/herdr-tts:3920` | `done\|blocked) color=$'\033[33m'; icon="✔" ;;` | `warn` | `\033[33m` (yellow) | Roster agent done or blocked state |
| `bin/herdr-tts:3921` | `idle) color=$'\033[90m'; icon="·" ;;` | `muted` | `\033[90m` (bright black) | Roster agent idle state |
| `bin/herdr-tts:3922` | `*) color=$'\033[90m'; icon="$st" ;;` | `muted` | `\033[90m` (bright black) | Roster agent unknown state |
| `bin/herdr-tts:343` | `TT_EN[dash.engine]="...%.*s\033[0m · auto-read %s"` | `reset` | `\033[0m` (reset) | Closing SGR reset for engine line |
| `bin/herdr-tts:820` | `TT_ES[dash.engine]="...%.*s\033[0m · auto-lectura %s"` | `reset` | `\033[0m` (reset) | Closing SGR reset for engine line |
| `bin/herdr-tts:3792` | `roster_fmt="║ %s%s%s %s %-${DASH_W_AGENT}s%s\033[0m"` | `reset` | `\033[0m` (reset) | Closing SGR reset per roster chat row |
| `bin/herdr-tts:3437` | `printf '\033[0m\033[?25h\033[2J\033[H'` | `reset` | `\033[0m` (reset) | Dashboard cleanup on exit |
| `bin/herdr-tts:393` | `TT_EN[menu.title]="\e[1m⌨️  herdr-tts · Voice Menu\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Menu popup header title |
| `bin/herdr-tts:870` | `TT_ES[menu.title]="\e[1m⌨️  herdr-tts · Menú de voz\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Menu popup header title |
| `bin/herdr-tts:433` | `TT_EN[settings.view.voice]="\e[1m🎙  herdr-tts · Settings · Voice\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Voz header title |
| `bin/herdr-tts:910` | `TT_ES[settings.view.voice]="\e[1m🎙  herdr-tts · Ajustes · Voz\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Voz header title |
| `bin/herdr-tts:434` | `TT_EN[settings.view.audio]="\e[1m🔊  herdr-tts · Settings · Audio\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Audio header title |
| `bin/herdr-tts:911` | `TT_ES[settings.view.audio]="\e[1m🔊  herdr-tts · Ajustes · Audio\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Audio header title |
| `bin/herdr-tts:435` | `TT_EN[settings.view.notifications]="\e[1m🔔  herdr-tts · Settings · Notifications\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Notificaciones header |
| `bin/herdr-tts:912` | `TT_ES[settings.view.notifications]="\e[1m🔔  herdr-tts · Ajustes · Notificaciones\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Notificaciones header |
| `bin/herdr-tts:436` | `TT_EN[settings.view.reading]="\e[1m⚙️  herdr-tts · Settings · Auto-read\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Lectura header |
| `bin/herdr-tts:913` | `TT_ES[settings.view.reading]="\e[1m⚙️  herdr-tts · Ajustes · Lectura automática\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Lectura header |
| `bin/herdr-tts:437` | `TT_EN[settings.view.index]="\e[1m⚙️  herdr-tts · Voice & Audio Settings\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Index header |
| `bin/herdr-tts:914` | `TT_ES[settings.view.index]="\e[1m⚙️  herdr-tts · Ajustes de voz y audio\e[0m"` | `title` | `\e[1m` (bold) + `\e[0m` | Settings Index header |

#### 3. Render Surfaces Audit

1. **Dashboard (`run_dashboard` / `dashboard_render`, L3782–4040)**:
   - Employs ANSI SGR colors for live engine state (`e_color`) and per-chat agent status in the roster (`color`).
   - Line-clamped to `$(( DASH_LINES - 1 ))`.
   - Uses terminal cursor and erase escapes: `\033[H` (home), `\033[J` (clear to bottom), `\033[K` (clear line), and cursor hide `\033[?25l`.
2. **Voice Palette (`run_voice_palette` / `palette_preview`, L4261–4370)**:
   - Delegates the interactive list directly to `fzf` (`fzf --delimiter '\t' --with-nth 1 ...`).
   - Generates plain text tab-separated rows without ANSI escape codes.
   - `palette_preview` outputs Unicode symbols and plain text via `tt` templates; contains zero inline ANSI color codes.
3. **Voice Menu (`run_voice_menu` / `voice_menu_render`, L4410–4565)**:
   - Zero color SGR codes. Uses `\e[1m` for title bold. Frame buffered and written via single clamped write `printf '\033[H%s\033[K\033[J'`.
4. **Voice Settings (`run_voice_settings` / `settings_render`, L5045–5160)**:
   - Zero color SGR codes. Uses `\e[1m` for view titles. Clamped to `$(( DASH_LINES - 1 ))` and written via single physical write.
5. **CLI Status Output (`show_status`, L2228–2320)**:
   - Evaluated by `herdr-tts --status`.
   - Zero ANSI escape sequences; standard plain text formatting (`echo` with `tt status.*` keys).
6. **Agent Output Roster**:
   - The roster rendered within the terminal is the dashboard's roster section (L3905–3947). Maintainer decision 2026-09-23 resolved that this roster adopts `TTS_THEME`.

#### 4. Confirmation of `PALETTE_*` Variables

Lines 4159–4161 define:
```bash
PALETTE_TITLE_CHARS=35         # chat title cap in the entry line
PALETTE_SNIPPET_CHARS=60       # snippet cap in the entry line
PALETTE_HISTORY_SCAN_LINES=400 # ledger tail scanned per palette open
```
These are strictly layout width caps and ledger history scan limits. They have no relationship to color or palette themes.

#### 5. Configuration Mechanism (`config_set`)

The `config_set` function (`bin/herdr-tts` L1195–1270) manages persistent configuration key-value pairs stored in `$CONFIG_FILE` (`~/.config/herdr-tts/config.env`):
- **Whitelist Enforcement (L1197–1203)**:
  ```bash
  case "$key" in
    TTS_PROVIDER|TTS_PLAYBACK|HERDR_TTS_AUDIO_RETENTION_DAYS|TTS_SETTLE_SECONDS|WEB_URL|WEB_LABEL|CLICK_REDIRECT|COLLIE_URL|TTS_AUTO_SCOPE|TTS_AUTO_LANG|PODCAST_ENABLED|TTS_DEBOUNCE_SECONDS|NTFY_TOPIC|TTS_VOICE|HERDR_TTS_LANG) ;;
    *)
      echo "config_set: unmanaged key '${key}' (allowed: ...)" >&2
      return 1
      ;;
  esac
  ```
  `TTS_THEME` must be added to this `case` pattern (L1198) and to the error diagnostic list (L1200).
- **Validation**: Rejects values containing double quotes (L1204–1207). For `TTS_THEME`, values must be strictly `dark` or `light` (RF-HT-14-1).
- **Single Backup per Process**: `CONFIG_SET_BACKED_UP` flag creates a `.bak` snapshot on the first write of an invocation (L1218–1221).
- **Streaming Upsert & Atomic Write**: Writes through a PID-tagged temporary file `${CONFIG_FILE}.tmp.$$` within `# >>> herdr-tts settings >>>` markers, collapsing duplicates to one line, and replaces atomically via `mv -f` (L1264).

#### 6. Settings Submenus Extension Contract in Practice

The canonical specification (`openspec/specs/settings-navigation/spec.md`) and retro-design (`openspec/changes/archive/2026-09-23-settings-category-submenus/design.md`) document the two-level navigation architecture:
- State variable: `SETTINGS_CATEGORY` (`index`, `voice`, `audio`, `notifications`, `reading`, or new category).
- Index frame (L5100–5114): Lists categories, the global interface language row (`l`), and daemon restart (`R`).
- Category dispatch (`settings_handle_key`, L4785–5034): Each category handles its own keys. Unknown keys trigger scoped hints (`*) SETTINGS_WARN="$(tt settings.unknown_key.<view> "$key")"`).
- Key handling reference (knob `p`, L4788–4795):
  ```bash
  p|P)
    next="$(settings_cycle_value provider "$TTS_PROVIDER")"
    if config_set TTS_PROVIDER "$next"; then
      TTS_PROVIDER="$next"
    else
      SETTINGS_WARN="$(tt settings.save_failed TTS_PROVIDER)"
    fi
    ;;
  ```
- Back-out contract (L4780–4783): Inside any category, `q`, `Q`, `Esc`, or Enter sets `SETTINGS_CATEGORY="index"` and returns 0 (re-renders index). On index, returns 1 (exits settings).

#### 7. Smoke Test Suite Structure

The test runner `scripts/smoke-tests.sh` contains 2,890 lines and 699 assertions structured across numbered scenarios:
- `lib_run`: Runs snippets in an isolated library-mode subshell sourcing `bin/herdr-tts`.
- `assert_grep` / `assert_no_grep` / `assert_no_grep_f`: Regex and fixed-string output verifiers.
- `esc_count`: Counts escape sequences (`\033[H`) using Python to verify frame counts without screen flicker.
- Scenario 32a–32h: Proves the settings two-level navigation model, category isolation, persistence on cycle, Esc back-out, and write-failure resilience.
- Scenario 36d–36e: Proves UI language cycle (`l`) and `config_set` allowlist validation.
- Scenario 37c: Establishes the static grep gate pattern (`grep -v '^[[:space:]]*#' "$SCRIPT" > "$T/code-only.sh"`, followed by `assert_no_grep`).

---

### Affected Areas

- `bin/herdr-tts`:
  - **Defaults block** (L84–86 and L160–164): Declare `DEFAULT_THEME="dark"` and initialize `TTS_THEME="${TTS_THEME:-$DEFAULT_THEME}"`.
  - **`config_set` allowlist** (L1198, L1200): Add `TTS_THEME` to managed keys and allowed error message. Add value validation rejecting anything other than `dark` and `light`.
  - **Color resolution helper `theme_color`** (new function, ~L1275): Maps semantic tokens (`ok`, `warn`, `error`, `accent`, `muted`, `title`, `reset`) to SGR sequences according to active `TTS_THEME`.
  - **Dashboard render** (`dashboard_render`, L3851–3856 and L3918–3923): Replace hardcoded SGR string literals with calls to `theme_color`.
  - **Settings key dispatch** (`settings_handle_key`, L4735–5034): Add category navigation and knob `t` dispatch.
  - **Settings frame render** (`settings_render`, L5045–5138): Add view rendering for the theme setting.
  - **i18n lookup tables** (`TT_EN` ~L440 and `TT_ES` ~L920): Add localized strings for the new category name, knob row, theme values (`Dark` / `Oscuro`, `Light` / `Claro`), and status notifications.
- `scripts/smoke-tests.sh`:
  - New test block (scenario 38a–38e):
    - `38a`: `config_set` accepts `TTS_THEME` with `dark` and `light` (rc 0), rejects unmanaged or invalid values like `solarized` (rc 1).
    - `38b`: Dark theme snapshot bit-identity verification (raw byte comparison against baseline).
    - `38c`: Settings knob `t` cycle persists `dark ↔ light` into `config.env` and triggers immediate re-render.
    - `38d`: Static grep gate forbidding literal SGR color codes outside `theme_color`.
    - `38e`: Light theme SGR emission verification (`34m`, `30m`, `1;33m`).
- `docs/prds/HT-14-tema-claro.md`: PRD is already authoritative; no repo doc edits needed during exploration.

---

### Approaches

#### Comparison of Architectural Approaches

| Approach | Summary | Pros | Cons | Complexity |
|---|---|---|---|---|
| **Approach 1: Dedicated Category "Apariencia" (Maintainer Decision 2026-09-23)** | Add a 5th category view `appearance` to `SETTINGS_CATEGORY`. Index gains category row `t` (or `p`/`c`). Inside `appearance`, knob `t` cycles `dark ↔ light`. | - Strictly honors the maintainer's closed product decision.<br>- Decouples theme from unrelated audio/reading categories.<br>- Establishes an extensible category for future visual settings (e.g., density, glyph sets). | - Requires +1 extra keypress to change theme (`t` -> `t` -> `Esc`).<br>- Submenu currently contains only 1 knob.<br>- Adds a 5th category row to the index view. | Low-Medium |
| **Approach 2: Index-Level Knob `t` (Direct Cycle, Pattern of Language `l`)** | Place knob `t` directly on the index alongside `l` (Language) and `R` (Restart). Pressing `t` on the index immediately cycles `dark ↔ light` and re-renders. | - Instant 1-keypress feedback (cycles and re-renders immediately in the new theme).<br>- Avoids an empty single-knob submenu.<br>- Matches `l` (Language) as a global ambient interface setting. | - Deviates from closed PRD decision ("categoría nueva Apariencia confirmada frente a knob dentro de una categoría existente").<br>- Adds an active knob row to the index footer. | Low |
| **Approach 3: Submenu Knob Inside Existing Category (e.g., Reading / Lectura or Audio)** | Attach knob `t` to `reading` (`r`) or `audio` (`a`). | - Zero changes to index rows.<br>- Follows original `settings-category-submenus` strict 4-category index. | - Poor semantic cohesion (theme has nothing to do with audio retention or pane reading).<br>- Explicitly rejected by maintainer on 2026-09-23. | Low |

#### Detailed Analysis of Approach 1 vs. Approach 2

1. **Approach 1 (Dedicated Category "Apariencia")**:
   - The maintainer resolved Open Question 1 on 2026-09-23 specifically affirming: *"categoría nueva 'Apariencia' confirmada frente a knob dentro de una categoría existente"*.
   - **Index Key Assignment**: On the index, existing keys are `v` (Voz), `a` (Audio), `n` (Notificaciones), `r` (Lectura), `l` (Language), `R` (Restart), and `q`/`Esc` (Exit). The letter `t` (Tema / Theme) is completely available on the index.
   - **Row Budget**: The index currently uses 10 lines (title, subtitle, blank, 4 category rows, blank, language row, restart row). The terminal clamp is `$(( LINES - 1 ))` (minimum 23 rows on standard 24-row terminals). Adding an `appearance` category row increases the index to 11 lines, which easily fits within the 24-row clamp without scrolling or truncation.
   - Inside the `appearance` view, knob `t` cycles `dark ↔ light`.

2. **Resolution Mechanism for `theme_color`**:
   To satisfy RNF-HT-14-1 (zero memory/startup overhead, no subshells), `theme_color` must not be invoked via command substitution `$(theme_color ...)` inside high-frequency render loops.
   Two valid zero-fork implementations exist:
   - **Option A (In-place variable assignment via `printf -v`)**:
     `theme_color <token> <var_name>` assigns the ANSI sequence directly to the named variable without a subshell fork.
   - **Option B (Frame-level resolution)**:
     At the start of `dashboard_render`, call `theme_color` once per token into local variables (`local c_ok c_warn c_accent c_muted`). The loop then references `$c_ok`, `$c_warn`, etc., directly.

---

### Recommendation

We recommend **Approach 1 (Dedicated Category "Apariencia")** coupled with **Frame-Level Token Resolution (Option B)**:

1. **Category Structure**:
   - Respect the maintainer's closed decision (2026-09-23).
   - Wire `t` on the index to open `SETTINGS_CATEGORY="appearance"`.
   - In the `appearance` view:
     - Render header `settings.view.appearance` (`\e[1m🎨 herdr-tts · Settings · Appearance\e[0m`).
     - Render knob row `settings.row.theme` (` t Theme: dark` / ` t Tema: oscuro`).
     - Include note documenting that `TTS_THEME` governs plugin rendering and that the roster adopts the theme.
     - Dispatch `t|T` to cycle `dark ↔ light`, write via `config_set TTS_THEME "$next"`, and flip active `TTS_THEME`.
     - Scoped `*)` unknown key warning hints `t theme`.
2. **Zero-Fork Color Resolver**:
   Implement `theme_color` with explicit token mapping:
   - `dark` (default): `ok=32`, `warn=33`, `error=31`, `accent=36`, `muted=90`, `title=1`, `reset=0`.
   - `light`: `ok=32`, `warn=1;33` (bold+33, maintainer decision), `error=31`, `accent=34`, `muted=30`, `title=1`, `reset=0`.
   - In `dashboard_render`, resolve tokens into local variables (`c_ok`, `c_warn`, `c_accent`, `c_muted`) once per frame, then assign `$c_ok`, etc., inside the engine and roster `case` blocks.
3. **Strict Bit-Identity**:
   With `TTS_THEME=dark` (or unset), the resolver outputs identical bytes (`\033[32m`, `\033[33m`, `\033[36m`, `\033[90m`), guaranteeing zero regression against existing baseline captures.

---

### Risks

1. **Index Key Collision and Two-Level Extension Contract**:
   - *Risk*: `design.md` L55 stated *"Never add index rows"*, which applied to flat knob additions inside the 4 initial categories. Adding a 5th category view adds one row to the index view.
   - *Mitigation*: The index clamp budget currently sits at 10 rows out of 24. Adding `t` Apariencia expands it to 11 rows, well below the clamp threshold. Key `t` is available on the index (existing index keys: `v`, `a`, `n`, `r`, `l`, `R`, `q`).
2. **Grep Gate False Positives in Smoke Tests**:
   - *Risk*: A static grep gate that naively checks for `\033` or `\e` would falsely match non-color ANSI controls: cursor positioning (`\033[H`), screen clear (`\033[J`, `\033[2J`), line erase (`\033[K`), cursor visibility (`\033[?25l`, `\033[?25h`), escape key matching (`$'\e'`), visible-width regex (`DASH_ANSI_RE`), or literal bold titles in i18n (`\e[1m`).
   - *Mitigation*: Design the grep gate in scenario 38d to strip comments and explicitly target literal foreground color SGR patterns: `assert_no_grep "no raw color SGR" '\033\[(3[0-7]|9[0-7])m' "$T/code-without-theme.sh"`.
3. **Compound SGR Parsing (`1;33` for `warn` in Light Theme)**:
   - *Risk*: `warn` in light theme uses compound parameters (`1;33` = bold + yellow). If visible width calculations or terminal slicing functions expect single-number parameters, width math could break.
   - *Mitigation*: `DASH_ANSI_RE` is defined as `$'\e\[[0-9;?]*[a-zA-Z]'`, which natively handles semicolon-separated compound codes (`1;33`). Slicing routines in `dashboard_fit` use `tt_visible_len`, which relies on `DASH_ANSI_RE`. Verified that compound codes strip cleanly with zero visible length distortion.
4. **Performance Overhead in High-Frequency Render Loop**:
   - *Risk*: Calling a subshell `$(theme_color ...)` for every chat in a 30-chat roster loop would spawn 30+ subshells per dashboard frame (2–5 FPS drop).
   - *Mitigation*: Resolve the 4 active tokens (`ok`, `warn`, `accent`, `muted`) once per frame before the roster loop into local shell variables. Zero subshells are spawned during frame composition.
5. **Bit-Identity Regressions with `TTS_THEME=dark`**:
   - *Risk*: Modifying `dash.engine` or `roster_fmt` string formats could inadvertently change whitespace, reset sequences (`\033[0m`), or line feeds.
   - *Mitigation*: Preserve `roster_fmt` and `dash.engine` formats verbatim. The token resolver with `dark` must emit the exact 5-byte sequences `$'\033[32m'`, `$'\033[33m'`, `$'\033[36m'`, `$'\033[90m'`. Smoke test 38b will assert 100% byte equivalence against a golden snapshot.
6. **Roster Terminal Ambiguity**:
   - *Risk*: The operator's main terminal might be light, but agent panes inside Herdr might use a different terminal profile.
   - *Mitigation*: Follow closed maintainer decision (2026-09-23): the roster adopts `TTS_THEME`. Document in the Settings row hint that `TTS_THEME` governs what the plugin renders, regardless of individual pane profiles.
7. **Baseline (VERIFIED 2026-09-23 by orchestrator gatekeeper)**:
    - *Verified*: Full suite run in this environment: **699 passed, 0 failed** — the earlier claim of 4 failing assertions in scenarios 0–10 (tty sensitivity) does NOT reproduce here and is RETRACTED.
    - *Standing convention*: New tests in scenario 38 must still use hermetic test environments (`new_env`) with fixed `LINES=40 COLUMNS=110` stubs, so the suite never depends on the host tty.

---

### Ready for Proposal

All preconditions for the proposal phase have been satisfied:
- Complete SGR inventory and drift analysis verified against the live tree.
- `config_set` insertion points and validation rules mapped.
- Settings dispatch, render, and extension contract confirmed.
- Smoke test scenario 38 specification prepared.
- Architectural trade-offs evaluated and aligned with the maintainer's closed decisions.

Yes
