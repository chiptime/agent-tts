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

Derivation: `re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r", "", raw)`
then keep from the picker's first own row (the `▔` modal edge, the
`─N─` count rule, the `╭─ Switch Model` box top, or the `Select
model` title) to the end, with the trailing blank run dropped. This
mirrors what brain's styled wide read hands the adapters
(escape-stripped text).

The interaction contract these fixtures pin (ADR 0009): a model
picker is answered with ARROWS + Enter, NEVER with a digit — in these
lists a digit confirms and rewrites the default model.
