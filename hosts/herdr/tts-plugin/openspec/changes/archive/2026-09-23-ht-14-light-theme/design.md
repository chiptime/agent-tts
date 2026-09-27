# Design: HT-14 Configurable Light Theme

## Technical Approach

One managed config key (`TTS_THEME=dark|light`, default `dark`) feeds one pure bash resolver
(`theme_color`) that is the **sole** emitter of color SGR bytes. `dashboard_render` resolves the four
live tokens into frame locals once per frame (zero forks), and the two existing `case` blocks
(engine state L3852–3855, roster state L3919–3922) switch from string literals to those locals.
A fifth settings category (`Apariencia`, index key `t`) hosts knob `t`, which cycles, persists
through `config_set`, flips the live variable, and re-renders in the same popup.

The maps hold **numeric SGR parameters only** (`32`, `1;33`, `90`…); `theme_color` composes the
escape with `printf -v`. That single choice makes literal color SGR provably absent from the whole
script, which collapses the 38d grep gate into a whole-file assertion with no exemption list
(verified below), and keeps dark output byte-identical because `printf -v v '\033[%sm' 32` produces
the same five bytes as `$'\033[32m'`.

Implements `specs/terminal-theme/spec.md` (all seven requirements) and
`specs/settings-navigation/spec.md` (three MODIFIED, one ADDED).

## Architecture Decisions

### Decision: `theme_color <out-var> <token>` with `printf -v`, numeric-only maps

**Choice**

```bash
# Sole color-SGR emitter (RF-HT-14-2). Out-var first, like dashboard_fit /
# config_get_retention_raw. printf -v keeps it fork-free: safe per frame.
# The maps carry SGR PARAMETERS only; the escape is composed here and
# nowhere else, so no literal color sequence exists anywhere in the script.
theme_color() { # <out-var> <ok|warn|error|accent|muted|title|reset>
  local __tc
  if [[ "${TTS_THEME:-$DEFAULT_THEME}" == "light" ]]; then
    case "$2" in
      ok) __tc=32 ;; warn) __tc='1;33' ;; error) __tc=31 ;;
      accent) __tc=34 ;; muted) __tc=30 ;; title) __tc=1 ;; *) __tc=0 ;;
    esac
  else
    case "$2" in
      ok) __tc=32 ;; warn) __tc=33 ;; error) __tc=31 ;;
      accent) __tc=36 ;; muted) __tc=90 ;; title) __tc=1 ;; *) __tc=0 ;;
    esac
  fi
  printf -v "$1" '\033[%sm' "$__tc"
}
```

**Alternatives considered**

| Option | Tradeoff | Verdict |
|---|---|---|
| `theme_color <token>` → stdout | Every resolution is a `$( )` fork; 4 forks/frame even with frame-level caching. Violates "zero forks". | Rejected |
| Two `declare -A THEME_DARK/THEME_LIGHT` | Needs `declare -n` or `${!ref}` indirection to pick the map; no existing precedent in the file. | Rejected |
| Full literal escapes in the maps (`$'\033[32m'`) | Gate must carve the `theme_color` body out of the file (awk line-range) before grepping — fragile, breaks when the function moves. | Rejected |
| Numeric params + `printf -v` composition | Two nested `case` blocks (house style: `config_set`, `settings_cycle_value`), zero forks, zero literals to exempt. | **Chosen** |

**Rationale**: `case` is the established branching idiom in this file. Out-var-first matches
`dashboard_fit title "$title" "$W"` and `config_get_retention_raw ret`. Numeric maps turn the
"sole emitter" requirement from a convention the gate must police with exemptions into a structural
property: there is nothing else to exempt.

**Fail-safe defaults** (both explicit, both required by RF-HT-14-1's spirit):
- Unknown **token** → `0` (reset). Never invent a color; reset is the identity that leaves plain text
  plain. Failing to a color could be unreadable on the very background this feature exists to fix.
- Unknown **theme value** → the `else` branch = dark. Same style as `HERDR_TTS_LANG` (`!= es → en`,
  L175) and `TTS_SETTLE_SECONDS` (L149). A hand-edited `TTS_THEME=solarized` can never alter shipped
  output, and dark is exactly the byte-identical baseline.

**Placement**: immediately after `config_set` closes (L1270), before `is_playing` (~L1275 hunk).
`theme_color` is a **config-value resolver**, not a render routine: its only input is `TTS_THEME`,
resolved in the config block at L137–164. Grouping it with the config accessors keeps the
write path (`config_set`) and the read path (`theme_color`) adjacent, and puts the definition
~2,500 lines above its first consumer (L3852) with zero forward references.

**Safety constraint**: `printf -v "$1"` is an indirect write primitive. `theme_color` MUST only ever
be called with hardcoded internal variable names — never with user or config input. All call sites
are static and enumerated in *File Changes*; a future dynamic call site is a review-blocking defect.

### Decision: frame-level resolution in `dashboard_render` (Option B, not per-call)

**Choice**: extend the existing local declaration at L3791 and resolve once, before any loop:

```bash
local frame="" roster_fmt c_ok c_warn c_accent c_muted
roster_fmt="║ %s%s%s %s %-${DASH_W_AGENT}s%s\033[0m"   # UNCHANGED
theme_color c_ok     ok
theme_color c_warn   warn
theme_color c_accent accent
theme_color c_muted  muted
```

Then the two `case` blocks become pure variable reads:

| Line | Before | After |
|---|---|---|
| 3852 | `playing) e_color=$'\033[32m' ;;` | `playing) e_color="$c_ok" ;;` |
| 3853 | `paused) e_color=$'\033[33m' ;;` | `paused) e_color="$c_warn" ;;` |
| 3854 | `synthesizing) e_color=$'\033[36m' ;;` | `synthesizing) e_color="$c_accent" ;;` |
| 3855 | `*) e_color=$'\033[90m' ;;` | `*) e_color="$c_muted" ;;` |
| 3919 | `working) color=$'\033[32m'; icon="▶" ;;` | `working) color="$c_ok"; icon="▶" ;;` |
| 3920 | `done\|blocked) color=$'\033[33m'; icon="✔" ;;` | `done\|blocked) color="$c_warn"; icon="✔" ;;` |
| 3921 | `idle) color=$'\033[90m'; icon="·" ;;` | `idle) color="$c_muted"; icon="·" ;;` |
| 3922 | `*) color=$'\033[90m'; icon="$st" ;;` | `*) color="$c_muted"; icon="$st" ;;` |

**Alternatives considered**: resolving inside each `case` arm (4 extra builtin calls per roster row
— correct but pointless work, and it invites a later `$( )` regression); resolving into globals
(leaks frame state across renders, breaks the function's self-contained style).

**Rationale**: 4 builtin calls per frame regardless of roster size. Nothing else moves — `roster_fmt`,
the `dash.engine` template (which owns its trailing `\033[0m`), spacing, and reset positions are
byte-for-byte untouched, which is what makes the 38b identity assertion meaningful rather than
tautological. `error` and `title` stay defined-but-unconsumed: the spec enumerates the token
vocabulary, and titles keep their literal `\e[1m` inside the i18n tables (a bold attribute, not a
color, and outside the gate regex by construction).

### Decision: value validation as a second `case`, after the quote guard, before any I/O

**Choice**: in `config_set`, add `TTS_THEME` to the whitelist (L1198) and to the diagnostic list
(L1200), then insert a key-specific value gate between the double-quote guard (ends L1207) and the
directory writability check (starts L1209):

```bash
  case "$key" in
    TTS_THEME)
      if [[ "$value" != "dark" && "$value" != "light" ]]; then
        echo "config_set: refused: TTS_THEME must be 'dark' or 'light' (got '${value}')" >&2
        return 1
      fi
      ;;
  esac
```

**Alternatives considered**: folding the check into the whitelist `case` at L1197 (its arms are
empty key-admission markers; adding a body there mixes key admission with value admission and forces
every future key to grow the same statement); validating at the call site in `settings_handle_key`
(leaves the CLI/library path unguarded — spec 38a exercises `config_set` directly).

**Rationale**: two readable stages — key admission, then value admission. Placement is load-bearing:
it returns **before** `mkdir -p`, **before** the one-per-process `.bak` snapshot (L1218–1221), and
before the tmp file, so a rejected value leaves the stored config and the backup byte-unchanged,
exactly as the spec's *Reject unmanaged values* scenario demands. The `config_set: refused:` prefix
mirrors the existing quote-rejection message at L1205, and the text names the allowed values.

### Decision: `settings_cycle_value theme` over an inline two-value toggle

**Choice**: `SETTINGS_THEMES=(dark light)` next to `SETTINGS_UI_LANGS` (L4651), a
`theme) vals=("${SETTINGS_THEMES[@]}") ;;` arm at L4684, and `theme` added to the
`settings_cycle_value` unknown-key diagnostic at L4699.

**Alternatives considered**: an inline `if [[ "$TTS_THEME" == dark ]]; then next=light; else next=dark; fi`
(fork-free, used by the `n|N` / `u|U` voices.json flags at L4811/L4821).

**Rationale**: the nearest analogue is the interface-language knob `l|L` (L4755) — also two-valued,
also a global, also persisted via `config_set` into `config.env` — and it uses
`settings_cycle_value ui_lang`. The inline form is reserved in this file for flags that never touch
`config.env`. A named table also keeps the cycle vocabulary greppable in one place. The `$( )` fork
here is **per keypress**, not per frame; the spec's zero-fork rule is scoped to render loops.

### Decision: 38b proves byte-identity by differential capture, not by a committed golden

**Choice**: within a single hermetic run, capture the dashboard from one fixture three ways
(`TTS_THEME` absent / `dark` / `light`) and assert:

1. **Exact dark bytes** — `grep -F` for `$'\033[32m'`, `$'\033[33m'`, `$'\033[36m'`, `$'\033[90m'`
   in the dark capture, plus `assert_no_grep` proving **no other** color SGR appears.
2. **Unset ≡ dark** — `diff` of the two normalized captures is empty.
3. **Plain text untouched** — strip every SGR from the dark and light captures; `diff` is empty.
4. **Round-trip** — after `config_set TTS_THEME dark`, the normalized capture again equals (1)/(2).

**Alternatives considered**

| Option | Tradeoff | Verdict |
|---|---|---|
| Committed golden snapshot file | The frame embeds `date +%H:%M:%S` (L3794) and relative ages (`2m ago`). A raw golden is flaky; a normalized golden is no longer "byte-identical". | Rejected |
| Capture from `git show HEAD:bin/herdr-tts` inside the test | Same wall-clock drift between the two captures, plus the suite would depend on git state — the suite is otherwise hermetic. | Rejected |
| Differential capture within one run + fixed-string byte pins | Fully deterministic on everything that is deterministic; the four byte strings are transcribed from the shipped L3852–3855 / L3919–3922, so a regression cannot pass. | **Chosen** |

**Rationale**: the honest, stable unit of byte-identity in a clock-bearing frame is the color byte
sequence plus structural equality of the rest. Assertion (1) pins the pre-change bytes literally;
(3) is the strongest possible reading of the spec's *Plain text untouched* scenario and is impossible
to satisfy accidentally. Two helpers are added beside the existing `visible_stats`/`esc_count` python
heredocs: `strip_ansi <file>` and `norm_frame <file>` (blanks `HH:MM:SS` and `Nm ago`).

### Decision: 38d needs no exemption list

**Choice**: reuse the 37c pattern verbatim, with no carve-outs:

```bash
grep -v '^[[:space:]]*#' "$SCRIPT" > "$T/code-only.sh"
assert_no_grep "38d no literal color SGR anywhere" '\\(033|e)\[[0-9;]*(3[0-7]|9[0-7])m' "$T/code-only.sh"
assert_no_grep "38d no 256-color/truecolor SGR"    '(38|48);(5|2);'                     "$T/code-only.sh"
```

**Verified against the current tree (2026-09-23)**: the first pattern matches **exactly 8 lines** —
3852, 3853, 3854, 3855, 3919, 3920, 3921, 3922 — and nothing else. The second matches **0**.
Zero false positives on `\033[0m` (L343, L820, L3792, L3437), `\e[1m` (L393, L433–437, L870, L910–914),
`\033[H` / `\033[J` / `\033[2J` / `\033[K` / `\033[?25l` / `\033[?25h`, or `DASH_ANSI_RE` (L3380) —
their SGR parameter is never in `30–37`/`90–97`, and `DASH_ANSI_RE` writes `\e\[` (escaped bracket),
which the pattern's literal `\[` cannot match.

**Rationale**: the numeric-map decision earns this. The gate is therefore also the RED proof — it
MUST fail with 8 hits before implementation and pass after, with no list to keep in sync.

### Decision: `Apariencia` as a fifth index category, key `t`

**Choice**: `t|T) SETTINGS_CATEGORY="appearance" ;;` in the index dispatch (after the `r)` arm at
L4748, before `l|L)`), plus an `appearance)` arm in `settings_handle_key`'s category dispatch and in
`settings_render`.

**Alternatives considered**: index-level knob like `l` (1 keypress, but contradicts the maintainer's
closed 2026-09-23 decision); attaching `t` to `reading` or `audio` (explicitly rejected, poor
cohesion). Recorded in `exploration.md`; not re-litigated here.

**Key-collision check**: index keys today are `q`/`Q`/`Esc`/`""`, `v`/`V`, `a`/`A`, `n`/`N`, `r`
(lowercase-only by design), `l`/`L`, `R` (uppercase-only restart chord). Both `t` and `T` are free;
`T` does not shadow `R`. Inside `notifications`, `t` remains the ntfy topic — the dispatch is
already scoped per category, so the two `t` semantics cannot collide (spec: *t keys are view-scoped*).

**No-scroll verification**: the index grows 10 → 11 rows. `settings_render` clamps with
`menu_cap_rows $(( DASH_LINES - 1 ))` — 39 under the hermetic `LINES=40`, 23 on a minimum 24-row
terminal. 11 ≪ 23. Proof obligation, not assumption: 38c asserts the exit hint ` q/Esc quit` still
renders on the index frame, which can only happen if nothing was clamped off the bottom.

## Data Flow

```
  config.env  ──source (L128)──▶  TTS_THEME  ──normalize (L150 hunk)──▶ dark|light
       ▲                                                   │
       │                                                   ▼
  config_set TTS_THEME <v>                          theme_color <out> <token>
   ├─ whitelist  (L1198)                             (numeric map → printf -v)
   ├─ quote guard(L1204)                                     │
   ├─ VALUE gate (new, pre-I/O)  ──reject rc1──▶ stderr      │
   ├─ .bak       (L1218)                                     │
   └─ tmp + mv   (L1264)                                     ▼
       ▲                                    dashboard_render: 4 calls / frame
       │                                      c_ok  c_warn  c_accent  c_muted
       │                                          │                 │
  settings_handle_key  appearance/t|T             ▼                 ▼
   ├─ settings_cycle_value theme            engine case        roster loop
   ├─ config_set TTS_THEME "$next" ─ok──▶ TTS_THEME="$next"  (reads locals,
   │                                       + SETTINGS_NOTE     zero forks)
   └────────────────────────────────fail─▶ SETTINGS_WARN (old value kept)
```

Knob keypress sequence (one popup, no exit):

```
  index frame ──'t'──▶ appearance frame (Theme: Dark)
                          │
                         't' ─▶ cycle → config_set → TTS_THEME=light → SETTINGS_NOTE
                          │
                          └──▶ appearance frame (Theme: Light) ──'q'──▶ index frame
```

**Precedence note (load-bearing for tests)**: `source "$CONFIG_FILE"` at L130 runs *before* the
`${VAR:-$DEFAULT}` block, and `TTS_THEME` is **not** one of the two snapshotted knobs
(`HERDR_TTS_*_RETENTION_DAYS`, `HERDR_TTS_LANG`). So precedence is **config.env > env > `dark`**,
like every other ordinary knob. Scenario 38 MUST NOT expect an env `TTS_THEME` to override a value
already written in `config.env`.

## File Changes

| File | Action | Description |
|---|---|---|
| `bin/herdr-tts` L84–85 | Modify | Add `DEFAULT_THEME="dark"` with a comment beside `DEFAULT_LANG` |
| `bin/herdr-tts` ~L150 | Modify | `TTS_THEME="${TTS_THEME:-$DEFAULT_THEME}"` + `[[ "$TTS_THEME" == "light" ]] \|\| TTS_THEME="dark"` (same validation style as L149/L175) |
| `bin/herdr-tts` L1198, L1200 | Modify | Add `TTS_THEME` to the managed-key pattern and to the diagnostic list |
| `bin/herdr-tts` L1207→L1209 | Modify | Insert the `TTS_THEME` value gate, pre-`mkdir`/pre-`.bak` |
| `bin/herdr-tts` ~L1275 | Create | `theme_color()` — sole color-SGR emitter |
| `bin/herdr-tts` L3791 | Modify | Extend locals with `c_ok c_warn c_accent c_muted` + 4 `theme_color` calls |
| `bin/herdr-tts` L3852–3855 | Modify | Engine-state case → locals |
| `bin/herdr-tts` L3919–3922 | Modify | Roster-state case → locals |
| `bin/herdr-tts` L401, L878 | Modify | `settings.unknown_key.index` gains `t appearance` / `t apariencia` |
| `bin/herdr-tts` ~L437, ~L465 | Modify | New `TT_EN` keys (table below) |
| `bin/herdr-tts` ~L914, ~L942 | Modify | New `TT_ES` keys (table below) |
| `bin/herdr-tts` L4651 | Modify | `SETTINGS_THEMES=(dark light)` |
| `bin/herdr-tts` L4684, L4699 | Modify | `theme)` cycle arm + diagnostic text |
| `bin/herdr-tts` ~L4748 | Modify | Index dispatch `t\|T) SETTINGS_CATEGORY="appearance" ;;` |
| `bin/herdr-tts` ~L5031 | Modify | `appearance)` arm in the category dispatch |
| `bin/herdr-tts` ~L5099 | Modify | `appearance)` arm in `settings_render` |
| `bin/herdr-tts` ~L5110 | Modify | Index row `settings.index.appearance` after the reading row |
| `scripts/smoke-tests.sh` ~L145 | Modify | Add `strip_ansi` / `norm_frame` helpers beside `esc_count` |
| `scripts/smoke-tests.sh` L2886 | Create | Scenario 38a–38e block, before the RESULT footer |

No deletions. No new files. No new dependencies.

## Interfaces / Contracts

```bash
# theme_color <out-var> <token> → 0 always; writes the SGR sequence into <out-var>.
#   tokens: ok | warn | error | accent | muted | title   (unknown → reset, 0)
#   <out-var> MUST be a hardcoded internal name (printf -v is an indirect write).
#   Fork-free: safe to call per frame. NEVER call as $(theme_color ...) in a loop.

# Token → SGR parameter map (the whole contract):
#   token   dark     light
#   ok      32       32
#   warn    33       1;33     (bold+yellow — maintainer decision 2026-09-23)
#   error   31       31
#   accent  36       34
#   muted   90       30
#   title   1        1
#   (unknown / reset) 0      0

# config_set TTS_THEME <dark|light> → 0 on persist, 1 on rejection or write failure.
#   Rejection message: config_set: refused: TTS_THEME must be 'dark' or 'light' (got '<v>')
#   A rejected value performs NO filesystem action (no .bak, no tmp, no mv).

# settings_cycle_value theme <current> → next value on stdout (dark ⇄ light);
#   unknown current restarts at dark (existing wrap-to-first semantics).
```

### i18n keys

| Key | EN | ES |
|---|---|---|
| `settings.index.appearance` | ` t  🎨 Appearance      theme (dark / light)` | ` t  🎨 Apariencia            tema (oscuro / claro)` |
| `settings.view.appearance` | `\e[1m🎨  herdr-tts · Settings · Appearance\e[0m` | `\e[1m🎨  herdr-tts · Ajustes · Apariencia\e[0m` |
| `settings.row.theme` | ` t  Theme:               %s   (dark = default)` | ` t  Tema:                 %s   (oscuro = por defecto)` |
| `settings.theme.label.dark` | `Dark` | `Oscuro` |
| `settings.theme.label.light` | `Light` | `Claro` |
| `settings.row.theme_note` | ` t  cycles the theme · the chat roster adopts it too, whatever profile each agent pane uses` | ` t  cicla el tema · el roster de chats también lo adopta, use el perfil que use cada panel de agente` |
| `settings.appearance.theme_note` | `✓ Theme: %s (applies to every surface herdr-tts renders)` | `✓ Tema: %s (se aplica a todo lo que dibuja herdr-tts)` |
| `settings.unknown_key.appearance` | `Unrecognized key (%s) — t theme, q back to the index` | `Tecla no reconocida (%s) — t tema, q vuelve al índice` |
| `settings.unknown_key.index` *(modified)* | `… r auto-read, t appearance, l language, R restarts the daemon, q quits` | `… r lectura automática, t apariencia, l idioma, R reinicia el daemon, q sale` |

`settings.theme.label.$TTS_THEME` uses the same dynamic-key lookup as
`settings.lang.label.$HERDR_TTS_LANG` (L4758, L5112). Column padding on the index and knob rows must
visually align with its sibling rows; the exact padding is pinned by the `-F` fixed-string assertions
in 32a/38c, so it is verified rather than assumed. The roster-adoption disclosure required by the
spec lives in `settings.row.theme_note`.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit (`lib_run`) | `theme_color` token→byte map, both themes, unknown token, unknown theme value | `lib_run 'theme_color x accent; printf "%q\n" "$x"'` — exact byte assertions for all 6 tokens × 2 themes; covers `error`/`title`/`accent` without an engine stub |
| Unit (`lib_run`) | `config_set TTS_THEME` accept/reject, config unchanged on reject, file stays sourceable | Mirrors 36e verbatim |
| Integration (capture) | Dark byte-identity, unset≡dark, light emission, plain-text-untouched, round-trip, compound-width safety | Differential captures + `strip_ansi`/`norm_frame` diffs; `visible_stats` max-width equality proves `1;33m` strips cleanly |
| Integration (capture) | Category open, cycle, same-popup re-render, note/warn, scoped unknown key, no-scroll | `printf 'ttq' \| "$SCRIPT" --voice-settings`; `esc_count $'\033[H'` = 4 frames |
| Integration (cold start) | Persistence across restart, ≥10 attempts | 10 sequential `--voice-settings` processes; each is a cold start that re-sources `config.env`, so alternation dark↔light over 10 runs *is* the restart proof |
| Static | Sole-emitter gate, no wide-gamut codes | 37c comment-strip pattern, no exemption list |

### Scenario 38 mapping

| Scenario | Proves (spec requirement) | Mechanics |
|---|---|---|
| **38a** | Managed Theme Key | `lib_run` `config_set` rc 0 for `dark`/`light`, rc 1 for `solarized`; stderr names allowed values; config byte-unchanged after reject; `.bak` absent; file re-sources |
| **38b** | Sole Color Emitter / Dark Byte-Identity / Born-Off Default | `-F` pins on the four dark sequences; `assert_no_grep` for any other color; `diff` unset vs dark; `diff` ANSI-stripped dark vs light; round-trip after `config_set TTS_THEME dark` |
| **38c** | Appearance Category and Theme Knob + Two-Level Navigation | `t` opens the view; `t` cycles and persists; both `Theme: Dark` and `Theme: Light` in one capture (same-popup re-render); 4 H-moves; `SETTINGS_NOTE` shown then cleared; read-only-dir write failure keeps the old value (32h technique); `t@q` warns scoped and names no other category's keys; exit hint present (no-scroll); ES run asserts `Apariencia`/`Tema`; 10 cold-start cycles alternate |
| **38d** | Sole Color Emitter (static) + Basic-SGR Restraint | Comment-strip + two `assert_no_grep`; RED = 8 hits pre-change |
| **38e** | Light Contrast Minimums + Surface Coverage | Resolver byte assertions for all light tokens; light dashboard capture contains `32m`, `30m`, `1;33m`; roster rows carry the light map; `visible_stats` width parity with dark |

### RED-first authoring order

1. **38d** — fails immediately with 8 literal hits (cheapest, most unambiguous RED).
2. **38a** — fails on the unmanaged-key rejection.
3. **38b/38e resolver assertions** — fail with `theme_color: command not found`.
4. **38c** — fails because `t` on the index hits the `*)` unknown-key arm.

Only then implement, in dependency order: defaults/normalization → `config_set` → `theme_color` →
`dashboard_render` wiring → i18n tables → `settings_cycle_value` → settings dispatch → settings
render/index row. Baseline to hold: **699/699** (verified 2026-09-23); target 699 + scenario 38.
All new scenarios use `new_env` with the standard hermetic `LINES=40 COLUMNS=110`.

## Threat Matrix

The change adds no routing, no new subprocess, no VCS/PR automation, and no executable-file
classification. Every row is explicit `N/A`; **no tasks or tests are generated from this table.**

| Boundary | Applicability | Reason |
|---|---|---|
| Documentation-like paths | N/A | No path is classified or executed; the change touches one bash monolith and one test script, both already executable by design |
| Git repository selection | N/A | No `git` invocation is added; the suite stays hermetic and git-independent (an explicit reason the `git show HEAD:` snapshot option was rejected) |
| Commit state | N/A | No index or worktree operation is performed by the feature or its tests |
| Push state | N/A | No remote or ref is resolved anywhere in this change |
| PR commands | N/A | No PR tooling, argument composition, or command delegation is introduced |

**Adjacent boundary handled outside the matrix** (recorded so it is not lost): `TTS_THEME` is written
into a file that is later `source`d as bash (L130), and its value reaches an indirect
`printf -v`. Three layers contain it — the existing double-quote guard (L1204), the new
`dark|light` value gate (pre-I/O), and startup normalization (`!= light → dark`) — so a hand-edited
or hostile value can neither break `source` nor reach `printf`'s format. 38a asserts the file stays
bash-sourceable after the write, exactly as 36e does for `HERDR_TTS_LANG`.

## Migration / Rollout

No migration required. The feature ships inactive: absent key ≡ `dark` ≡ current bytes.

**Rollback — runtime (no code change)**: cycle the knob back to Dark, or run
`config_set TTS_THEME dark`, or delete the `TTS_THEME=` line from the managed block. Output returns
to the 38b baseline; this path is itself covered by the 38b round-trip assertion, so rollback is a
tested behavior rather than a promise.

**Rollback — code**: revert the implementation and test commits **together** — a reverted
`bin/herdr-tts` re-introduces the 8 literals and would fail the 38d gate, so a half revert is
self-detecting. After a revert, `TTS_THEME` leaves the whitelist and any stale
`TTS_THEME="light"` line in `config.env` degrades to an ordinary sourced-but-unused variable:
harmless, still bash-sourceable, no startup impact. Optional cleanup removes **only** that one line
(`config_set` rewrites line-wise, so the managed-block markers, every other key, and the `.bak`
snapshot are preserved).

## Work-Unit Boundaries (hint for `sdd-tasks`, not a plan)

The change splits along a natural seam — the resolver is independently shippable and provable
without the settings UI:

- **Slice A — resolver + dark identity**: 38a/38b/38d/38e RED, defaults/normalization,
  `config_set` gate, `theme_color`, `dashboard_render` wiring. Finish line: dark byte-identity
  proved and the gate green. Autonomous; rollback = revert one commit; the feature is still
  invisible to users (no way to set `light` from the UI, only from `config_set`).
- **Slice B — settings surface**: 38c RED, i18n tables, `SETTINGS_THEMES`/cycle arm, index and
  category dispatch, `settings_render` arm and index row. Finish line: cycle persists across 10 cold
  starts, no scroll, bilingual copy.

The proposal forecasts 500–600 authored lines against a 400-line budget (`Chained PRs recommended:
Yes`, `Decision needed before apply: Yes`) with delivery strategy `ask-on-risk`. Sizing the slices
and resolving that decision belong to `sdd-tasks` and the pre-apply confirmation — not here.

## Open Questions

None blocking. Two deliberately deferred, both already bound by the proposal:

- [ ] Final light values for `ok`, `error`, and `title` — the spec fixes only the contrast minimums
      (`muted=30`, `accent=34`, `warn=1;33`) and explicitly allows tuning during the deliberate
      light-scheme week before "Implementada". The map location is the single edit point.
- [ ] Exact column padding of the new index/knob rows — settled visually during implementation and
      then frozen by the `-F` assertions in 38c.
