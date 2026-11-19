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

Derivation: `re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r", "", raw)`
then keep from the picker's first own row (the `▔` modal edge, the
`─N─` count rule, or the `╭─ Switch Model` box top) to the end, with
the trailing blank run dropped. This mirrors what brain's styled wide
read hands the adapters (escape-stripped text).

The interaction contract these fixtures pin (ADR 0009): a model
picker is answered with ARROWS + Enter, NEVER with a digit — in these
lists a digit confirms and rewrites the default model.
