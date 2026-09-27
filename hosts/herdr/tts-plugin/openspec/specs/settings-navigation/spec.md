# Settings Navigation Specification

## Purpose

Two-level Ajustes navigation — a category index plus one view per category — replaces the flat 15-knob list that brushed the 24-row edge; retro-documentation of shipped behavior, proven by smoke scenario 32.

## Requirements

### Requirement: Two-Level Navigation Model (Smoke: 32a, 32b, 38c)

The index MUST list exactly four categories — `v` Voz, `a` Audio, `n` Notificaciones, `r` Lectura automática (case-insensitive) — with no raw knob rows; each key MUST open its view. The global language row `l` and restart `R` MUST remain on the index; `t` MUST warn unknown (no appearance category exists — `TTS_THEME` is a config-only managed key, see terminal-theme).
(Previously: five categories including `t` Apariencia with a theme cycle knob; the UI surface was removed 2026-09-24 because the terminal owns the popup background, so the knob was confusing surface area. The listing correction stands: `r` is Lectura's key, `l` is the language row.)

#### Scenario: Index lists categories

- GIVEN the index, WHEN the frame renders, THEN four category rows plus the `l`/`R` hints appear, no raw knobs, no Apariencia row, AND `v` opens Voz

#### Scenario: t warns unknown on the index

- GIVEN the index, WHEN `t`, THEN the unknown-key hint fires without an appearance segment AND no category view opens

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

- GIVEN `HERDR_TTS_LANG=es` then `en`, WHEN the index renders, THEN copy is Spanish ("Ajustes de voz y audio", "Idioma") then English ("Voice & Audio Settings", "Language")

### Requirement: Knob Grouping Completeness (Smoke: 32a, 32b, 38c, 43d)

The views MUST partition exactly the 16 knobs — Voz `p g n u i` (5), Audio `d c r s` (4), Notificaciones `t f w` (3), Lectura `v a b p` (4) — each rendering ONLY its own. No theme knob exists: `TTS_THEME` is config-only. Lectura's `p` cycles `TTS_READER_AUTO` (`off`↔`on`): with `on`, every live read auto-opens the reader popup (HT-16; replays of `--play-file` never do).
(Previously: 15 knobs after the Apariencia `t` knob was removed with its category; 2026-09-24 added Lectura's `p`, returning the count to 16 across four categories.)

#### Scenario: Category views are exclusive

- GIVEN any category view, WHEN the frame renders, THEN only that category's knob rows appear

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

