# Settings Navigation Specification

## Purpose

Two-level Ajustes navigation — a category index plus one view per category — replaces the flat 15-knob list that brushed the 24-row edge; retro-documentation of shipped behavior, proven by smoke scenario 32.

## Requirements

### Requirement: Two-Level Navigation Model (Smoke: 32a, 32b)

The index MUST list exactly five categories — `v` Voz, `a` Audio, `n` Notificaciones, `r` Lectura automática, `t` Apariencia (case-insensitive) — with no raw knob rows; each key MUST open its view. The global language row `l` and restart `R` MUST remain on the index.
(Previously: four categories; the listing incorrectly named `l` as Lectura's key — runtime truth is `r` for Lectura, `l` is the language row.)

#### Scenario: Index lists categories

- GIVEN the index, WHEN the frame renders, THEN five category rows plus the `R` hint appear, no raw knobs, AND `v` opens Voz

#### Scenario: t opens Apariencia

- GIVEN the index, WHEN `t`, THEN the Apariencia view renders as the current category

### Requirement: Entry Points (Smoke: 32a, 26e)

Voice-menu `a` and standalone `--voice-settings` MUST land on the index; menu-Main `a` (Ajustes) and in-settings `a` (Audio) MUST NOT collide — different views.

#### Scenario: Entries land on the index

- GIVEN `--voice-settings` or menu main view plus `a`, WHEN the settings UI opens, THEN the first frame is the index

### Requirement: Exit Semantics (Smoke: 32d, 32e)

In a category, `q`/`Q`/Esc/Enter MUST return to the index (re-render, not exit); on the index they MUST exit the settings UI (standalone: full exit; menu: main view).

#### Scenario: Esc backs out to index

- GIVEN the Voz view, WHEN Esc then `q`, THEN the index re-renders and the index consumes `q`

#### Scenario: Esc from index exits

- GIVEN the standalone index, WHEN Esc, THEN exit 0 after exactly one rendered frame

### Requirement: Restart Placement (Smoke: 32f, 26e)

Uppercase `R` (case-sensitive) MUST restart the daemon from the index only; category views MUST NOT dispatch it (Audio's lowercase `r` is the retention cycle).

#### Scenario: R restarts from index

- GIVEN the index, WHEN `R`, THEN the restart path runs with an inline confirmation; a failed spawn warns inline

### Requirement: Render Contract (Smoke: 26e, 32d)

Each render MUST be ONE physical clamped write (`menu_cap_rows`); UI copy MUST be bilingual EN/ES resolved through the interface-language lookup, English default; `SETTINGS_WARN`/`SETTINGS_NOTE` are bottom status rows, only when set, cleared once shown.
(Previously: copy was required to be neutral Spanish only; the runtime has shipped bilingual copy since the language cycle landed.)

#### Scenario: Single frame, transient status

- GIVEN a warn/note set, WHEN the next frame renders, THEN one clamped write shows it at the bottom, AND the following frame is clean

#### Scenario: Copy follows interface language

- GIVEN `HERDR_TTS_LANG=es` then `en`, WHEN the Apariencia view renders, THEN copy is Spanish ("Apariencia", "Tema") then English ("Appearance", "Theme")

### Requirement: Knob Grouping Completeness (Smoke: 32a, 32b)

The views MUST partition exactly the 16 knobs — Voz `p g n u i` (5), Audio `d c r s` (4), Notificaciones `t f w` (3), Lectura `v a b` (3), Apariencia `t` (1) — each rendering ONLY its own.
(Previously: 15 knobs across four categories.)

#### Scenario: Category views are exclusive

- GIVEN any category view, WHEN the frame renders, THEN only that category's knob rows appear

#### Scenario: t keys are view-scoped

- GIVEN Notificaciones and Apariencia, WHEN `t` is pressed in each view, THEN each dispatches its own semantics (notifications knob vs theme cycle)

### Requirement: Scoped Key Dispatch (Smoke: 32b, 32g)

A category view MUST dispatch only its own knob keys; an unrecognized key MUST warn inline with the current view's hints and MUST NOT change knobs.

#### Scenario: Unknown key warns scoped

- GIVEN the Voz view, WHEN `@`, THEN the inline warning names the key and hints only Voz keys

### Requirement: Persistence on Cycle (Smoke: 32c)

A cycle SHALL advance to the next table entry, persisting via the managed writers (`config_set`, voices.json flags, auto-mute marker); write failure MUST keep the old value with an inline warning; `settings_cycle_value` MUST stay unchanged.

#### Scenario: In-category cycle persists

- GIVEN provider `edge` in Voz, WHEN `p`, THEN `openai` persists to config and the view re-renders

#### Scenario: Write failure keeps old value

- GIVEN a failing write, WHEN a cycle is attempted, THEN the old value stays with an inline warning

### Requirement: No-Scroll, No-Cursor Navigation (Smoke: 32a–32b)

The UI MUST NOT scroll or cursor-navigate; every view SHALL fit the row clamp (truncation, never scroll).

#### Scenario: Longest view fits clamp

- GIVEN Voz on a 24-row terminal, WHEN clamped, THEN knobs, hints and status lines fit the clamp

### Requirement: Retroactive Verification (Smoke: 32a–32g)

Smoke scenario 32 SHALL remain the executable proof of every requirement above; the full suite MUST stay green.

#### Scenario: Scenario 32 proves capability

- GIVEN the hermetic suite runs, WHEN scenario 32 executes, THEN every requirement's cited assertions pass
### Requirement: Appearance Category and Theme Knob (Smoke: 38c, 38e)

Inside Apariencia, knob `t`/`T` MUST cycle `dark ↔ light`, update the live variable, persist via `config_set TTS_THEME`, and re-render immediately in the same popup. Success MUST surface `SETTINGS_NOTE`; write failure MUST keep the prior value and surface `SETTINGS_WARN`. An unrecognized key MUST warn scoped to the theme knob. Category, knob, value, and note copy MUST exist in both EN and ES tables, noting the roster adopts the plugin theme regardless of pane profiles.

#### Scenario: Cycle persists across restart

- GIVEN `TTS_THEME=dark`, WHEN the knob cycles to light, THEN `config.env` stores `TTS_THEME=light` AND the value survives a daemon restart (≥10 attempts)

#### Scenario: Immediate re-render

- GIVEN the Apariencia view, WHEN `t` cycles the theme, THEN the same popup re-renders showing the new value without exiting

#### Scenario: Write failure keeps old value

- GIVEN a failing config write, WHEN `t`, THEN the old theme stays active and an inline warning appears

#### Scenario: Unknown key warns scoped

- GIVEN the Apariencia view, WHEN `@`, THEN the inline warning names the key and hints only the theme knob
