# Terminal Theme Specification

## Purpose

Global `TTS_THEME` (`dark` | `light`) governing every color the plugin renders. It centralizes all color-SGR emission in one auditable resolver, keeps dark output byte-identical to the shipped baseline, and raises light-background legibility using basic SGR only.

## Requirements

### Requirement: Managed Theme Key (Smoke: 38a)

`TTS_THEME` MUST be a managed `config_set` key accepting exactly `dark` or `light`; any other value MUST be rejected with rc 1 and descriptive stderr, leaving stored config unchanged. An unset key MUST behave as `dark`.

#### Scenario: Accept managed values

- GIVEN the managed whitelist, WHEN `config_set TTS_THEME light` (or `dark`), THEN rc 0 and the value persists atomically (markers, per-process `.bak` preserved)

#### Scenario: Reject unmanaged values

- WHEN `config_set TTS_THEME solarized`, THEN rc 1 with descriptive stderr naming allowed values, AND the stored config is unchanged

### Requirement: Sole Color Emitter with Dark Byte-Identity (Smoke: 38b, 38d)

`theme_color` MUST be the only code path emitting color SGR sequences. With `dark` it MUST resolve `ok=32 warn=33 error=31 accent=36 muted=90 title=1` byte-identically to current output. Resets `\033[0m` and title bold `\e[1m` MUST be preserved.

#### Scenario: Dark snapshot identity

- GIVEN `TTS_THEME=dark` or unset, WHEN the dashboard renders, THEN raw output equals the pre-change baseline snapshot byte for byte

#### Scenario: Grep gate

- GIVEN the comment-stripped script, WHEN scanned, THEN no literal foreground color SGR (`\033\[(3[0-7]|9[0-7])m`) exists outside `theme_color`; exempt: `\033[0m` reset, `\e[1m` bold, non-color controls (`\033[H` `\033[J` `\033[2J` `\033[K` `\033[?25l/h`)

### Requirement: Light Contrast Minimums (Smoke: 38e)

The light map MUST at minimum set `muted=30`, `accent=34`, and `warn=1;33` (bold+33, maintainer decision 2026-09-23); no token MAY resolve to insufficient contrast on a standard light background. `ok=32`, `error=31`, `title=1` carry over; remaining values MAY be tuned during the deliberate light-scheme review before "Implementada".

#### Scenario: Light emission

- GIVEN `TTS_THEME=light`, WHEN the dashboard renders, THEN output contains `32m`, `30m`, `34m`, and `1;33m`

#### Scenario: Compound warn is width-safe

- GIVEN light theme, WHEN visible-width slicing runs over `1;33m`, THEN the compound SGR strips cleanly with zero visible-length distortion

### Requirement: Surface Coverage

Every plugin-rendered colored surface MUST resolve through `theme_color`: dashboard engine state and roster. The roster MUST adopt `TTS_THEME` even when agent panes use different profiles. Plain text, layout, and reset positions MUST NOT change. Palette, menu, settings, and `--status` MUST stay colorless (no coloring of plain text).

#### Scenario: Roster adopts theme

- GIVEN `TTS_THEME=light`, WHEN the roster renders, THEN agent state colors come from the light map

#### Scenario: Plain text untouched

- GIVEN dark baseline output, WHEN comparing light output, THEN only color SGR bytes differ; wording, spacing, and reset positions are identical

### Requirement: Basic-SGR Restraint

Only basic SGR codes MUST be used; truecolor and 256-color codes MUST NOT be introduced; no new dependencies.

#### Scenario: No wide-gamut codes

- WHEN the grep gate runs, THEN no `38;5;`, `48;5;`, or `38;2;` sequences appear anywhere in the script

### Requirement: Zero-Cost Resolution

Resolution MUST be one function over two literal maps with zero forks. Render loops MUST NOT use command substitution for colors; tokens SHALL resolve once per frame into locals.

#### Scenario: No per-cell forks

- WHEN the render path is scanned, THEN dashboard/roster loops reference pre-resolved locals and no `$(theme_color ...)` appears inside any loop

### Requirement: Born-Off Default

The feature MUST ship inactive: default `dark`; writing `dark` MUST restore byte-identical baseline output without breaking the flow.

#### Scenario: Round-trip restores baseline

- GIVEN `TTS_THEME=light` was set, WHEN `config_set TTS_THEME dark`, THEN output again equals the 38b baseline snapshot

### Requirement: Regression Gate (Smoke: 38a–38e)

The full hermetic suite MUST stay green against the verified 699/699 baseline (2026-09-23, supersedes the PRD's 631). Scenarios 38a–38e MUST be authored RED-first, in hermetic environments (`new_env`, fixed `LINES=40 COLUMNS=110`).

#### Scenario: Suite stays green

- GIVEN the hermetic suite runs, WHEN all scenarios execute, THEN every assertion passes with default `dark`
