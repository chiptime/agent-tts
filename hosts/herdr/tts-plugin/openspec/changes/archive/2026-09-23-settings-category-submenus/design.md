# Design: Settings Category Submenus (Two-Level Ajustes Navigation)

## Context

Retro-design: the implementation exists uncommitted in `bin/herdr-tts` (~3440–3997), green under smoke scenario 32. Records HOW and WHY so later PRDs (HT-02/HT-10/HT-12) extend it without re-litigating the shape.

**Goals**: partition the 15 knobs into four views; keep one-keypress→action and the single-frame render contract; scope dispatch so foreign keys warn instead of mutating invisible knobs.

**Non-Goals**: scroll/cursor navigation, `settings_cycle_value` changes, new knobs, main-menu changes, HT-02 voice identity, pm-01 packaging.

## Architecture

One added variable, `SETTINGS_CATEGORY` (`index|voz|audio|notif|lectura`), nested inside the existing `run_voice_menu` `view` machine — both read by render, written by dispatch; no stack, no cursor.

```
run_voice_menu view=main ──a/A──→ view=settings, CATEGORY=index  (L3483-3486)
run_voice_settings ─────────────→ CATEGORY=index                 (L3979-3983)

   ┌──────── index ────────┐        rc=1
   │ v→voz   a→audio    R  │ ──q/Q/Esc/Enter──→ exit (standalone) | view=main (menu)
   │ n→notif l→lectura     │
   └───────────┬───────────┘
     q/Q/Esc/Enter│ rc=0, only flips the variable
   ┌────────────┴──────────────────┐
   │ category view: own knobs only │
   └───────────────────────────────┘
```

Key contract: `settings_handle_key` returns `1` **only** from the index. A category back-out returns `0` after setting `SETTINGS_CATEGORY="index"` (L3618-3623), so both caller loops (L3469-3473, L3994) stay untouched.

Partition (exactly 15 knobs, no overlap): Voz `p g n u i` · Audio `d c r s` · Notificaciones `t f w` · Lectura `v a b`.

## Detailed Design

| Concern | Where | Behavior |
|---|---|---|
| Index dispatch | L3592-3617 | `v/a/n/l` (case-insensitive) set the category; `R` (case-sensitive) calls `daemon_restart` → NOTE/WARN; unknown key warns with index hints; early `return 0` keeps knob arms unreachable |
| Category dispatch | L3618-3869 | Shared back-out arm, then `case "$SETTINGS_CATEGORY"` — four arms, knob bodies verbatim from the flat list; each `*)` warns naming only that view's keys |
| Render | L3881-3969 | `case "${SETTINGS_CATEGORY:-index}"` — four branches plus `*)` as the index; `_dash_add` buffers, `menu_cap_rows $(( DASH_LINES - 1 ))` clamps, one `printf '\033[H%s\033[K\033[J'` write with per-line `ESC[K` erase |
| Per-view reads | L3886, L3905 | `voice_map_refresh` inside the Voz branch (mtime-gated), `config_get_retention_raw` inside Audio — neither runs for other views |
| Status lines | L3951-3965 | `SETTINGS_WARN`/`SETTINGS_NOTE` at the bottom only when set, cleared by the caller *after* the render; exit hint view-aware |

**Key-collision discipline** drives the groupings: menu-level `a` (Ajustes) and settings-level `a` (Audio) never share a view; lowercase `r` (retention) and uppercase `R` (restart) are case-sensitive arms in separate views.

## Rationale / Alternatives Rejected

| Option | Tradeoff | Decision |
|---|---|---|
| Two-level categories | +1 keypress per knob | **Chosen** — keeps one-key→action, fixes the root cause, views fit the clamp |
| Scroll / cursor | Cursor state + escape sequences under `read -rsn1`; no organizational gain | Rejected — paradigm break, bash complexity |
| Deeper nesting / flat+pagination | More keypresses, or scroll renamed with arbitrary splits | Rejected — no gain at 15 knobs |

## Extension Contract

A new knob attaches to exactly one category: add its `case` arm to that category's dispatch block **and** one `_dash_add` row to the matching render branch, updating that view's `*)` hint in the same edit. Never add index rows; keys need only be unique within their view. Per strict TDD, the scenario-32 assertions go RED first, then the dispatch+render pair.

## Dependencies & Risks

- **HT-02 WIP**: Voz `n`/`u` call `voice_map_set_flag` (L1149); the Voz render calls `voice_map_refresh` (L1070) — same uncommitted tree. Pre-existing dependency, out of scope; must land with or before this change.
- **Mixed tree**: HT-02 and pm-01 WIP share the worktree → per-work-unit slices; rollback per-slice, never whole-tree.
- **Truncation**: longest view (Voz, ≈12 rows with the conditional install hint) stays inside the clamp.

## Threat Matrix

N/A — no VCS/PR automation, executable-file classification, or shell-argument composition; dispatch routes to unchanged in-process bodies. The adjacent `R → daemon_restart` spawn is byte-unchanged, its failure path asserted by 32f/26e.

## Test Alignment

| Requirement | Proof |
|---|---|
| Two-Level Navigation Model | 32a: four rows, no knob rows, `R` hint |
| Entry Points | 32a standalone; 26e menu `aRq` |
| Exit Semantics | 32d: 3 H-moves; 32e: 1 H-move, exit 0 |
| Restart Placement | 32f index; 26e note rendered once |
| Render Contract | 26e: 4 frame writes; 32d: H-moves = frames |
| Knob Grouping Completeness | 32b: presence + cross-view `assert_no_grep` |
| Scoped Key Dispatch | 32g: `@` warns with Voz-only hints |
| Persistence on Cycle | 32c: `p` edge→openai via `config_set` |
| No-Scroll | 32a–32b: frames fit the clamp |
| Retroactive Verification | full suite via `bash scripts/smoke-tests.sh` |

## File Changes

| File | Action | Description |
|---|---|---|
| `bin/herdr-tts` (~3440–3997) | Modify | `SETTINGS_CATEGORY` init at both entrypoints; view-aware `settings_handle_key`; branched `settings_render` |
| `scripts/smoke-tests.sh` | Modify | Scenario 32a–32g; 26e pipes |
| `README.md` | Modify | Settings section as per-category tables |

## Open Questions

None.
