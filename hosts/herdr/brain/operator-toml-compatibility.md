# Operator TOML — herdr-brain vs Collie compatibility

Answer to the ficha-06 auditor question "the TOML formats are not directly
compatible": what the two readers share, exactly how the four files differ,
what happens today if you copy a Collie file into brain, and the proposal the
operator must decide on. Reference product: Collie v1.24.2 (commit `744faa49`); the
reference checkout lives under the operator's herdr plugin directory
(cited below as `ref:path:line`).

## What both readers share

| Mechanism | Collie | herdr-brain |
|---|---|---|
| File location | next to the plugin `.env` (its config dir) | `~/.config/herdr-brain/` (or `HERDR_BRAIN_CONFIG_DIR`) |
| File names | `quick-replies.toml`, `commands.toml`, `keys.toml`, `launchers.toml` | same, plus brain-only `roots.toml` |
| Table names | `[[replies]]`, `[[commands]]`, `[[keys]]`, `[[launchers]]` | same |
| Live reload | mtime checked at request time, no restart (`ref:bridge/operator-file.ts:28`) | same (one `stat` per surface per request) |
| Malformed row | dropped with one warning, never the whole file (`ref:bridge/operator-keys.ts:94`) | same, one issue per row |
| Broken TOML (parse failure) | holds the last good rows for every file; empty if it never parsed; warned once per file version (`ref:bridge/operator-file.ts:35,48-49`) | same as Collie for `quick-replies`, `commands` and `keys` (the held-catalog issue is annotated `; keeping the last valid catalog`), but `launchers.toml` and brain-only `roots.toml` fail closed instead: both are execution authorizations, and a stale allowlist row must not outlive an unobserved removal edit |
| Missing file | ordinary empty, no error (`ref:bridge/operator-file.ts:56`) | same |
| Scope substitution | rows for an agent REPLACE the global catalog there (ADR 0018) (`ref:bridge/operator-keys.ts:20`) | same (REPLACE, not fusion, per agent family) |
| Launcher argv forbidden code points | ASCII+C1 controls, U+2028/2029, U+202A-202E, U+2066-2069, U+200E/200F/061C; the row is dropped, never sanitized (`ref:bridge/launcher-recipes.ts:220`, `ref:bridge/operator-launchers.ts:28`) | identical set: `Cc` category plus the `_TYPED_LINE_FORBIDDEN` constants (`src/herdr_brain/operator_config.py`) |
| Shell line from input | never — the client names a row (`ref:bridge/operator-launchers.ts:21`) | never — the client names a row id (`launch_plan` derives `command_line` only from operator argv) |

## Per-file differences

### quick-replies.toml

| Aspect | Collie | herdr-brain |
|---|---|---|
| Row shape | one row = a GROUP: `title` (heading and identity) + `items` (array of phrases) (`ref:bridge/operator-quick-replies.ts:29`) | one row = ONE button: `label` + `text` (a single verbatim phrase) |
| Group heading | the `title` | none — the dock is a flat list |
| Extra fields | unknown fields are ignored by the reader | `id` optional; any unknown field DROPS the row |

Copying a Collie file over: every row fails (`items` is an unknown field,
`label` missing) → empty dock. Nothing dangerous; simply incompatible shape.

### commands.toml

| Aspect | Collie | herdr-brain |
|---|---|---|
| Required fields | `command` (leading slash; text after the first space is the arg hint, `/model <name>`) (`ref:bridge/operator-commands.ts:32`) | `label` + `command` (leading slash; a TRAILING space is the arg hint, `/grep `) |
| Arg hint encoding | free text after the first space | only a trailing space (no hint text) |
| `description` | supported, shown in the palette | unknown field → row dropped |
| `confirm = true` | two-tap before the row fires (`ref:bridge/operator-commands.ts:37`) | not supported (no per-command confirmation) |
| `bar`, `bar_label` | optional harness-bar placement | not supported |
| Identity / duplicates | `scope`+`command`; a later redefinition replaces the earlier row IN PLACE (`ref:bridge/operator-commands.ts:47`) | explicit `id` required to be unique — a duplicate id DROPS the later row; label-derived ids get a `-2` suffix instead |

Copying a Collie file over: rows with `description`/`confirm`/`bar` all drop;
a bare `command`+`scope` row also drops (no `label`). Empty palette, no risk.

### keys.toml

| Aspect | Collie | herdr-brain |
|---|---|---|
| Chord grammar | `{ctrl,shift,alt,cmd,super}+{named \| F1-F12 \| any single character}`; `bs` aliases Backspace; `ctrl++` is ctrl plus the `+` key; case-insensitive (`ref:bridge/operator-keys.ts:24,39,54,75`) | closed `SEND_KEY_ALLOWLIST`: `up/down/left/right/enter/esc/tab/ctrl+u/ctrl+k/0-9`, max 8 chords per batch (the dialog-answer discipline) |
| Spelling normalization | trims, lowercases modifiers/named keys, dedupes modifiers, emits canonical `Up`/`Escape`/... | same treatment since this unit, plus `Escape`→`esc`; then the allowlist still decides — normalization cannot ADMIT a foreign key |
| `danger = true` | optional two-tap button (`ref:bridge/operator-keys.ts:103`) | unknown field → row dropped |
| `PageUp`, `Home`, `End`, `Delete`, tmux `C-c` | rejected at load (`ref:bridge/operator-keys.ts:41`) | rejected (outside the allowlist) |
| Chords herdr-brain rejects that Collie accepts | — | `ctrl+c`, `ctrl+d`, `ctrl+z` and any other letter chord, `space`, `backspace`, F-keys, any single non-digit character |

The allowlist is deliberately narrower: these rows answer dialogs from a
phone, and admitting `ctrl+c`/`ctrl+d` into that channel changes a hard-won
discipline — the replay surface is the PWA dock, not a safe-tray. Copying a
Collie file over: `danger` rows drop; chord rows outside the dialect drop;
rows like `keys = [" Down ", "Escape", "CTRL +U"]` now parse thanks to
normalization but any base outside the nine-key dialect still drops the row.

### launchers.toml

| Aspect | Collie | herdr-brain |
|---|---|---|
| Command expression | `command`: one shell LINE typed verbatim into a fresh pane (`ref:bridge/operator-launchers.ts:64`) | `argv`: an array of non-empty strings; a plain string is rejected by shape — no shell parsing happens in brain |
| Allowlist key | the exact `command` string | the row `id` the PWA sends |
| `label` | optional; defaults to the first token (`ref:bridge/operator-launchers.ts:65`) | required |
| `cwd` | optional; absent means "here" (home from the dashboard, the pane's cwd from a pane); `~` expanded | optional but MUST be absolute; existence is checked at launch time with a 409 |
| `harness` | optional agent name (ADR 0094) | not supported |
| `no_prompts` | optional | supported (two-tap confirmation in the PWA) |
| `[phone]` table | per-file switches for phone-added rows (`ref:bridge/operator-launchers.ts:199`) | not supported (unknown table) |

Copying a Collie file over: every row drops (`command`/`harness` are unknown
fields, `argv` is missing). This is the file where an alias reader would be
hardest: mapping a shell line to `argv` mechanically would need a POSIX-word
splitter at load time — exactly the shell-parsing step brain's argv design
exists to avoid inside the security surface.

### roots.toml (brain only)

No Collie counterpart. It is cwd authorization, not a dock catalog, and a
parse failure clears it (fail closed) instead of holding stale rows.

## What stays incompatible after this unit (by design, listed)

1. Unknown fields and the `[phone]` table drop rows in brain instead of being
   ignored (fail-closed schema; Collie's readers simply never look).
2. Duplicate identity: Collie later-row-wins-in-place; brain drops a duplicate
   explicit id (an issue reports it).
3. No `description`, `confirm`, `bar`, `danger`, `harness` surfaces in brain.
4. Launcher rows are argv, not shell lines; labels are required; cwd must be
   absolute.
5. Quick replies are flat buttons, not groups.
6. The key dialect is closed to the dialog-answer set; reference rows outside
   it are dropped with a reason.
7. Collie's "as shipped" default catalogs (ADR 0018) have no brain equivalent:
   an empty brain file simply serves no rows.

## Proposal for the operator (a decision, not part of this unit)

- **A. Alias reader** — brain accepts the Collie schemas in place (map
  `title`+`items`, `description`, `Escape`, ignore unknown fields). Cost: the
  launcher mapping requires load-time shell parsing of the reference
  `command` line, weakening the argv-only posture; unknown-field tolerance
  also hides typos (`no_promtss = true` would pass silently).
- **B. Offline converter (recommended)** — a small `config convert-from-collie`
  verb that reads Collie files and WRITES brain files, failing loudly per row
  it cannot map (shell-line launchers, out-of-dialect chords) so the operator
  resolves each one by hand. Runtime schemas and the fail-closed posture stay
  untouched; the load-time normalization shipped in this unit is exactly the
  amount of tolerance that is safe at runtime.
- **C. Keep as-is** — brain schemas are documented to differ; nobody copies
  files blindly.

## References

- `ref:quick-replies.toml.example:1`, `ref:commands.toml.example:1`,
  `ref:keys.toml.example:1`, `ref:launchers.toml.example:1` — reference
  schemas and operator contract.
- `ref:bridge/operator-file.ts:35` — hold-last-good failure posture.
- `ref:bridge/operator-keys.ts:24,54` — chord `NAMED` map and `normalizeChord`.
- `ref:bridge/operator-launchers.ts:43`, `ref:bridge/launcher-recipes.ts:220`
  — the forbidden code points.
