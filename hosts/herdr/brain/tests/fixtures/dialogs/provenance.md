# Dialog fixture provenance

Synthesized fixtures in this folder were authored by hand in each
harness's layout. The model-picker fixtures added by parity-f7d (S2)
are DIFFERENT: they are derived from REAL captures of the reference
product's corpus (ANSI escapes stripped, leading context trimmed to
the picker's own region, interior bytes untouched).

| Fixture | Derived from (reference capture) |
|---|---|
| `claude-model-picker.txt` | `web/src/fixtures/panes/claude--menu-model-picker.txt` (Claude Code `/model` list) |
| `claude-model-picker-moved.txt` | `web/src/fixtures/panes/claude--menu-model-picker-moved.txt` |
| `claude-switch-model-yes.txt` | `web/src/fixtures/panes/claude--v2289-switch-model-yes.txt` (Claude Code 2.1.289 "Switch model?" confirmation, pointer on row 1) |
| `claude-switch-model-no.txt` | `web/src/fixtures/panes/claude--v2289-switch-model-no.txt` (pointer on row 2) |
| `grok-model-picker.txt` | `web/src/fixtures/panes/grok--model-picker.txt` |
| `grok-model-picker-moved.txt` | `web/src/fixtures/panes/grok--model-picker-moved.txt` |
| `omp-switch-model.txt` | `web/src/fixtures/panes/omp--v18-4-switch.txt` (omp 18.4 `/switch` compact model picker) |
| `opencode-models-picker.txt` | LIVE capture 2026-11-19, opencode 1.19.5 (this machine, real pane): the `/models` composer command opens the `Select model` modal. Same derivation, kept from the `Select model` title row. The modal has no box borders: the underlying TUI's text bleeds at both margins (``┃ /mode``, ``commands``) and the `ON` default-model badge sits outside it — interior bytes untouched. The `●` row is the CURRENT model; the pointer is ANSI paint, invisible here. |
| `opencode-models-picker-over-chat.txt` | LIVE capture 2026-11-19, opencode 1.19.5 (this machine, real pane WITH a running chat): the same `/models` modal floating MID-SCREEN over existing conversation text — chat bleeds LEFT of every modal column and the MCP status list bleeds RIGHT of it, so the grammar must anchor by COLUMN (the title/footer column window), never by clean rows. Same derivation, kept from the `Select model` title row. |
| `opencode-models-search-wide.ansi` | LIVE capture 2026-11-19, opencode 1.19.5 (scratch tmux pane, 160x50, the operator's own config; `tmux capture-pane -p -e` after typing `GLM 5.3` into Search, no Enter sent): the FILTERED wide shape — no group headers, the provider in its own right-hand column, and the POINTER as paint (`48;2;250;178;131` on the first row, `GLM 5.3 Flash`, ahead of the exact `GLM-5.3` rows: opencode ranks free models and favourites first). The scratch path in the status row is replaced by `~/project`. |
| `opencode-variant-picker.ansi` | LIVE capture 2026-11-19, opencode 1.19.5 (same scratch pane; `tmux capture-pane -p -e` after ctrl+p → "Switch model variant" on `GLM-5.3 Highspeed`, no Enter sent): the `Select variant` list opencode also opens right after a model switch whose variants do not hold the current one. Title + `esc` hint, `Search`, `Default/low/high/max`, the pointer as paint on `Default`, NO footer; the composer under it already names the new model. Scratch path replaced by `~/project`. |
| `hermes-approval.txt` | `web/src/fixtures/panes/hermes--approval.txt` (Hermes Agent v0.22.2 classic CLI dangerous-command approval, `❯` on Allow once) |
| `hermes-approval-moved.txt` | `web/src/fixtures/panes/hermes--approval-moved.txt` (the same panel after Down, `❯` on Allow for this session) |
| `hermes-approval-long.txt` | `web/src/fixtures/panes/hermes--approval-long.txt` (a 120-character command wrapped to three rows; `5. Show full command` present) |
| `hermes-approval-long-full.txt` | `web/src/fixtures/panes/hermes--approval-long-full.txt` (after `5`: the Show-full row gone, `❯` on Deny) |
| `cursor-permission-shell.txt` | `web/src/fixtures/panes/cursor--permission-shell.txt` (Cursor Agent 2026.10.14 shell-command approval) |
| `cursor-ask-question.txt` | `web/src/fixtures/panes/cursor--ask-question.txt` (Cursor Agent 2026.10.14 agent question box, `›` on the first option) |

Derivation: `re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r", "", raw)`
then keep from the picker's first own row (the `▔` modal edge, the
`─N─` count rule, the `╭─ Switch Model` box top, or the `Select
model` title) to the end, with the trailing blank run dropped. The
hermes approval panels keep from the panel's own `╭` box top (the
transcript above is cut); the cursor dialogs keep from the dialog's
own first row (the full-width rule over the statusline row, or the
`┌` box top). The hermes captures' lab path token is renamed to a
same-length neutral one (`refcap`), so every wrap position inside the
wide command rows is unchanged. This
mirrors what brain's styled wide read hands the adapters
(escape-stripped text).

The interaction contract these fixtures pin (ADR 0009): a model
picker is answered with ARROWS + Enter, NEVER with a digit — in these
lists a digit confirms and rewrites the default model.

## Generic menu family (T10/S11)

Derived like the parity-f7d rows above: real captures of the
reference product's corpus, ANSI escapes stripped, leading context
trimmed to the last rows of the screen (the tail anchoring the
detection is what matters), interior bytes untouched. No live harness
capture was possible in this unit (no network / no credentials —
common rule), so the row names the reference capture each fixture
derives from.

| Fixture | Derived from (reference capture) | Shape it pins |
|---|---|---|
| `claude-menu-effort-slider.txt` | `claude-lab--menu-effort-slider--w82.txt` (Claude Code `/effort`) | `─` rule region top, titled panel, footer `←/→ to adjust · Enter to confirm · s for this session only · Esc to cancel` — the `s` hint is `for`-form (validates the group, yields no action) and `←/→` is skipped (footer row is outside the body scan) |
| `claude-menu-effort-slider-wrapped.txt` | `claude--menu-effort-slider--w40-ultracode.txt` | the TERMINAL soft-wrap: the footer's last word starts at column 0 because the row above had no room; the group must join BEFORE parsing |
| `claude-menu-config-panel.txt` | `claude-lab--menu-config-panel--w82.txt` (Claude Code `/config`) | tall modal whose region top is its `─` rule (tabs row + boxed search inside the region); footer `Type to filter · Enter/↓ to select · ↑ to tabs · Esc to clear` — compound `Enter/↓` skipped |
| `claude-menu-rewind.txt` | `claude-lab--menu-rewind--w82.txt` (Claude Code `/rewind`) | `▔` modal edge (region top), pointer row inside, footer `Enter to continue · Esc to cancel` |
| `claude-menu-resume-picker.txt` | `claude-lab--menu-resume-picker--w83.txt` (Claude Code `/resume`) | `▔` edge, boxed search, session rows; footer names `Ctrl+A`/`Ctrl+B`/`Ctrl+R`/`Space`/`Type` — every one OUTSIDE the guarded dialect, so the only button is `Esc` |
| `claude-menu-status-screen.txt` | `claude-lab--menu-status-screen--w82.txt` (Claude Code `/status`) | NEGATIVE: a single `Esc to cancel` footer and NO `▔` edge anywhere — one hint is never a claim |
| `claude-live-composer.txt` | `claude-lab--statusline-3row--w82.txt` | NEGATIVE: a live composer at the tail (`─` rule / `❯` draft / `─` rule sandwich under a hint-like statusline row) must never become a menu |
| `claude-menu-marketplaces.txt` | `claude--v2283-fullscreen-plugin-marketplaces-add--w82.txt` (Claude Code 2.1.283 plugin marketplaces panel) | the letter recipe: footer `Enter to select · u to update · d to remove · Esc to go back` — TWO lowercase letters the footer itself names (`u`/`d`), the narrow S3 channel's real capture |
