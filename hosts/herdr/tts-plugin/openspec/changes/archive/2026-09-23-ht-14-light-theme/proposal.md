# Proposal: HT-14 Configurable Light Theme

## Intent
Improve light-terminal legibility; preserve byte-identical existing dark output and default-off behavior.

## Scope
### In Scope
- Managed `TTS_THEME=dark|light`, default `dark`; invalid writes return rc 1 with descriptive stderr; retain atomic writes and backup.
- New Appearance/Apariencia category: index `t` opens it; category `t` cycles, persists, and immediately re-renders. Preserve value on failure with `SETTINGS_WARN`; use `SETTINGS_NOTE` on success.
- Theme dashboard/roster; audit palette/menu/settings/`--status` without coloring plain text. Preserve resets and title bold.
- Bilingual EN/ES strings, including the row note: roster adopts the plugin theme even when agent panes use different profiles.
- RED-first smoke scenarios 38a–38e and deliberate light-background validation.

### Out of Scope
- Background detection, extra/per-surface themes, remote surfaces, truecolor/256 colors, dependencies, engine changes, roadmap reprioritization.

## Capabilities
### New Capabilities
- `terminal-theme`: global theme configuration, semantic resolution, output compatibility, persistence, and contrast.

### Modified Capabilities
- `settings-navigation`: fifth category, scoped knob, bilingual copy, persistence feedback; retain navigation/clamping. Update four-category/15-knob requirements without unrelated cleanup.

## Approach
Use `theme_color` as the sole color-SGR emitter. Resolve locals `c_ok/c_warn/c_accent/c_muted` once per frame, zero forks; never command substitution inside render loops. Tokens: ok/warn/error/accent/muted/title. Dark: 32/33/31/36/90/1; light initially: 32/1;33/31/34/30/1. Bold+33 is fixed; muted 90→30 and accent 36→34 are minimum changes; remaining light values receive visual review before “Implementada.” Update dispatch, render, and scoped hints together.

## Affected Areas
| Area | Impact | Description |
|---|---|---|
| `bin/herdr-tts` | Modified | Defaults, config validation, resolver, frames, settings, i18n |
| `scripts/smoke-tests.sh` | Modified | 38a–38e regression coverage |
| `openspec/specs/settings-navigation/spec.md` | Modified at archive | Category extension |

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| Dark regression | Medium | Raw-byte snapshots |
| Contrast/profile mismatch | Medium | Visual trial; roster note |
| Compound SGR escapes gate | Medium | Audit compound colors; exempt bold/reset/control codes |

## Rollback Plan
Persist `dark` to disable; if necessary revert implementation/tests together, removing only managed `TTS_THEME`, preserving other settings and backups.

## Dependencies
- Existing settings-category navigation; HT-14 PRD and corrected exploration. No external dependencies.

## Success Criteria
- [ ] Verified 2026-09-23 baseline 699/699 remains green (supersedes 631); 38a–38e pass validation, byte-identity, cycle, grep-gate, light-emission checks.
- [ ] Ten restart-persistence attempts succeed; one deliberate week on a light scheme validates contrast.

## Review Workload Forecast
Estimate 500–600 authored changed lines: ~350–400 implementation/i18n, ~150–200 tests; planning additional.

400-line budget risk: High

Chained PRs recommended: Yes

Decision needed before apply: Yes

Delivery strategy: `ask-on-risk`; decide chain strategy at tasks, before apply, not here.
