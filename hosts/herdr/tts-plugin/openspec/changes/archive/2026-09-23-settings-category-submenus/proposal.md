# Proposal: Settings Category Submenus (Two-Level Ajustes Navigation)

## Intent

The Ajustes (voice settings) view was a flat list of 15 cycle knobs plus hints, already brushing the 24-line terminal edge because `menu_cap_rows` truncates (no scroll). Upcoming PRDs (HT-02 per-chat voices, HT-10 chain turns, HT-12 custom watchers) add more knobs; the root cause is organizational. Scroll was rejected (bash cursor/escape-sequence complexity, no organizational value); two-level navigation preserves the one-keypress → action paradigm.

**Status — retro-documentation**: the implementation already exists uncommitted in the working tree (same-day build; suite green 624/624 including smoke scenario 32). Downstream phases validate the existing code, not a greenfield build.

## Scope

### In Scope
- Level-1 category index (v Voz · a Audio · n Notificaciones · l Lectura automática) from voice menu `a` and `--voice-settings`
- Level-2 views render only their knobs (voz p/g/n/u/i · audio d/c/r/s · notif t/f/w · lectura v/a/b); cycle-knob UX unchanged
- Esc/q: category → index → exit; R (daemon restart) index-only
- View-aware `settings_handle_key` dispatch on `SETTINGS_CATEGORY`; scoped unknown-key warnings
- Smoke scenario 32; README settings section as category tables

### Out of Scope
- New knobs or knob behavior changes; `settings_cycle_value` untouched
- HT-02 voice identity (Voz n/u call `voice_map_set_flag`/`voice_map_refresh` from that WIP — pre-existing dependency)
- pm-01 packaging (parallel WIP, same tree); scroll/cursor navigation; main-menu changes

## Capabilities

> `openspec/specs/` is empty — no existing capabilities.

### New Capabilities
- `settings-navigation`: two-level Ajustes navigation — index and category views, key routing, Esc/q semantics, R placement, WARN/NOTE status lines, single-frame render contract

### Modified Capabilities

None.

## Approach

`SETTINGS_CATEGORY` drives `settings_render` (index/category branches) and `settings_handle_key` (view-aware dispatch); both entrypoints (`run_voice_settings`, `run_voice_menu`) land on the index. Render discipline unchanged: `_dash_add` buffering, single `printf` frame, `menu_cap_rows` clamp. Persistence keeps the existing `config_set`/`voice_map_set_flag` arms; write failures keep the old value and set `SETTINGS_WARN`. UI copy in Spanish (neutral).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `bin/herdr-tts` (~3440–3990) | Modified | `settings_render`, `settings_handle_key`, entrypoint init |
| `scripts/smoke-tests.sh` | Modified | Scenario 32 + navigation pipes |
| `README.md` | Modified | Settings section as category tables |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Key collisions (`a` ajustes vs audio; `r` retention vs `R` restart) | Low | Distinct views; `R` case-sensitive, index-only; foreign keys warn per view |
| Tree mixes HT-02 / pm-01 WIP | Medium | Commit as per-work-unit slices |
| Small-terminal truncation | Low | Longest view ≈ 12 rows; `menu_cap_rows` clamps |

## Rollback Plan

Revert this change's slices of `bin/herdr-tts`, `scripts/smoke-tests.sh`, `README.md` to HEAD — per work-unit slice (tree holds parallel WIP), never whole-tree. The flat list returns; written `config.env`/`voices.json` values stay valid (same keys, same writer).

## Dependencies

- HT-02 WIP `voice_map_set_flag` / `voice_map_refresh` (Voz n/u rows) must land with or before this change.

## Success Criteria

- [ ] Suite green: 624/624; scenario 32 covers index, categories, cycling, Esc, R
- [ ] All 15 knobs reachable, zero behavior change
- [ ] Every category view fits 24 rows including hints and status lines
