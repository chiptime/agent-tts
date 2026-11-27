# POST-09 — agy model detection & switch (parity with opencode)

**Objective:** implement full model detection (`last-model`) and model switching
(`/model` picker, catalog, PWA, voice) for the Antigravity CLI (`agy`), at parity
with the opencode flow. Worktree `feat/post-09-agy-model-switch`.
**Test runners:** engine `cd engine && uv run --extra dev --extra kokoro python -m pytest`;
brain unit `PYTHONPATH=src .venv/bin/python -m pytest tests/...`; JS `node --test tests/js/`; browser suites in separate processes (C4).

## Specs (operator decisions D1–D5 approved 2026-11-26, "dale")

- **S1 (engine):** replace the `probe_antigravity` stub (`engine/src/agent_tts/cache_probe.py`)
  so it reads the real `model` + token counts (`input_tokens`, `cache_read_tokens`,
  `output_tokens`, `created_at`) from `PLANNER_RESPONSE` events in
  `<root>/<sid>/.system_generated/logs/transcript.jsonl`. Sessions whose requests carry
  no `model` field (pre-2026-11-17 transcripts) answer honestly
  `"Error: no model recorded"` from `last-model` — never guessed from `settings.json` (D5).
  Update/add tests in `engine/tests/test_cache_probe.py` + `engine/tests/test_last_model.py`.
- **S2 (brain catalog):** new `AntigravityModelCatalog` in `hosts/herdr/brain/src/herdr_brain/pane_model.py`
  parsing `agy models` (`id\tNombre` per line), TTL + failure-TTL cache, fail-closed
  reasons, injectable runner (same pattern as `OpencodeModelCatalog`). Chip label is the
  human name via the catalog (`Gemini 4 Neon (High)`) with fallback to the raw id (D4).
  Context chip = model + used tokens only; `limit=None` (D3 — `_limit_for` untouched).
  `BrainTools.pane_models` serves `agy`/`antigravity` panes too (picker rows derived from
  the catalog the way the real picker paints them).
- **S3 (brain picker adapter):** `AntigravityAdapter` recognizes the real
  `Switch Model` picker — title `Switch Model`, `Search:` row, unnumbered rows with
  pointer `>` at column 0, one `(current)` mark, Effort slider row, and the single
  footer `Keyboard: ↑/↓ Navigate  ←/→ Effort  enter Select  esc Go Back` — as
  `kind="model_picker"` with `nav_keys=("up","down")`, `commit_keys=("enter",)`, exact
  `selected`, stable fingerprint, fail-closed (D1 captures). Real sanitized fixtures
  `tests/fixtures/dialogs/agy-model-picker*.txt` + `provenance.md` rows.
- **S4 (commands + PWA):** `antigravity`/`agy` added to `MODEL_COMMANDS` (`tools.py`) and
  `MODEL_SWITCH_COMMANDS` (`static/dialogs.js`); `EXPECTED_DIGESTS["/dialogs.js"]`
  re-pinned in `tests/browser/test_karaoke_harness.py` (C2). Unit + JS + browser tests.
  Voice/PWA switch flows work through the generic walk (never a digit, ADR 0009);
  `/effort` (←/→) is explicitly OUT of scope for v1 (D2).

## Constraints

- C1: read-only on `agy models` + `transcript.jsonl`; `mask_text` on any free-form
  output toward PWA/LLM; no real PII/emails in fixtures.
- C2: re-pin `EXPECTED_DIGESTS["/dialogs.js"]` after any `static/dialogs.js` byte; NEVER
  touch `static/index.html`, `static/app.js`, `static/settings.js`, `static/i18n/*.js`.
- C3: zero new boot-time fetches in the PWA.
- C5: operate only in this worktree. C6: keep the configured model; never escalate.

## Tasks

- **T1** [S1, D5] Engine probe rewrite + tests — route: inline — **commit `98ee3ad`**.
- **T2** [S2, D3, D4] `AntigravityModelCatalog` + `name_for` + picker-row grouping +
  `pane_models` branch + `_model_of` label + tests — route: inline — **commit `126707d`**.
- **T3** [S3, D1] `detect_model_picker` in `AntigravityAdapter` + real fixtures +
  provenance + `test_harness_dialogs.py` cases — route: inline — **commit `1e7d5a6`**.
- **T4** [S4] `MODEL_COMMANDS`/`MODEL_SWITCH_COMMANDS` + digest re-pin + voice switch
  unit tests + catalog answer-by-name for antigravity + JS tests + browser tests —
  route: inline — **commit `180389b`** (model_name antigravity walk tests live in
  `test_dialog_button_endpoint.py`, next to the opencode model_name suite;
  `test_dialog_answer.py` needed no change — it pins the harness-agnostic walk guard).
- **T5** All C4 suites (separate processes) — route: inline — evidence below.
- **T6** [R2] Verifier blockers 1–3 + quick improvements (fail-closed no-pointer,
  lock-free SWR catalog + non-blocking `_model_of`, probe hardening, mask_text,
  claude-only confirm wait, `_PICKER_WINDOW` 40) — route: inline — commit: see
  `git log` (r2 round).

## Verification evidence (T5, 2026-11-26)

- Engine: `python -m pytest tests` (engine/, engine venv, PYTHONPATH=wt/src) —
  **1557 passed, 2 skipped** (powershell-only), 0 failed (169.7s).
- Brain unit/API: `python -m pytest tests/ --ignore=tests/browser --ignore=tests/e2e`
  (brain venv, PYTHONPATH=wt/src) — **5083 passed, 0 failed** (168.3s).
- Brain JS: `node --test tests/js/*.test.js` — **831 passed, 0 failed**.
- Brain browser (separate processes): `tests/browser/test_model_switch_flow.py +
  tests/browser/test_pane_model_tag.py` — **14 passed**;
  `tests/browser/test_karaoke_harness.py` — **12 passed** incl. the re-pinned
  `EXPECTED_DIGESTS["/dialogs.js"]` = `2390a1a9…30e6d9` over the POST-09 bytes.

- L3 (2026-11-26, R2 — verificador independiente NEEDS_FIX, todo corregido con RED→GREEN):
  1. [S3] `_detect_model_picker` fail-closed completo: un frame sin puntero `>`
     (`selected is None`) ya no devuelve un dialog medio — devuelve `None`
     (`test_a_picker_without_a_pointer_fails_closed`). `_PICKER_WINDOW` 24 → 40.
  2. [S2] `AntigravityModelCatalog` ya no retiene el lock durante el subprocess:
     `list_models` reclama un `_inflight` bajo lock y corre el CLI fuera de él;
     hot polls usan `cached_name_for` (SWR no bloqueante): caché fría → `None`
     al instante + 1 refresco en background (spawn inyectable, daemon thread);
     caché stale → respuesta stale al instante + 1 refresco; nunca duplica runs.
     `BrainTools._model_of` usa `cached_name_for` — `/state`/`/herd`/`/view` jamás
     esperan al CLI (tests de concurrencia con runner bloqueante por gate).
  3. [S1] `probe_antigravity` endurecido: session_id no-seguro (vacío, `.`, `..`,
     `/`, `\`, basename ≠ id, y `resolve().is_relative_to(root)`) → `STATUS_MISSING`;
     `input_tokens >= 0` exigido (turno saltado si negativo), `cached_tokens` solo
     int no-bool `>= 0` (si no, 0); apertura `utf-8/errors=replace` + `OSError`
     capturado. Tests: traversal (`../outside` + archivo fuera del root), tokens
     negativos, bytes UTF-8 inválidos. Nota: `--session-id ""` en la CLI significa
     "omitida → última sesión" (semántica preexistente de todos los harness); el
     rechazo de vacío se prueba a nivel probe directamente.
  4. [C1] `mask_text` en los nombres parseados de `agy models` (catálogo) y en el
     `detail` de `_name_candidates` (wanted + labels).
  5. `switch_model` por voz: la espera de confirmación `"Switch model?"` ahora es
     exclusiva de `claude` — `agy` (y omp) no queman los ~3s del poll acotado.
  Verificación R2 (C4, procesos separados): engine focused 46 passed + ruff clean;
  brain unit 5089 passed; JS 831 passed; browser 26 passed (los 3 ficheros).
  Commit de esta ronda: ver `## Tasks` T6.

## Log

- L1 (2026-11-26, verbatim): fleet brief POST-09 — "Implementación completa de detección
  y cambio de modelo para `agy` / Antigravity CLI, paridad con `opencode`", decisions
  D1–D5 approved ("dale"), TDD estricto en work-unit commits, C1–C6 constraints, zones
  as listed in §5 of the brief.
- L2 (2026-11-26): design decisions taken in-scope:
  (a) catalog `list_models()` returns the RAW `agy models` entries `{id, name}`;
      the picker-selectable rows served by `/pane/models` are derived by the observed
      grouping rule pinned by the live captures (names sharing a base after stripping a
      trailing ` (Word)` collapse to the bare base when the group has >1 entries —
      reproduces the 9 painted rows exactly, incl. `GPT-OSS 120B (Medium)` staying suffixed);
  (b) `/pane/models` antigravity entries carry `{id, name, ids, current}` where `ids`
      are the family member ids and `current` marks the row whose ids contain the
      pane's transcript id;
  (c) D4 chip label resolution lives in `BrainTools._model_of` (antigravity harnesses
      only), so opencode chips keep today's raw-id behavior byte-for-byte;
  (d) catalog taps answered by NAME for antigravity ride the generic guarded walk
      (`_match_picker_option` + arrows), NOT search-first — the agy pointer is a real
      glyph, and typing into its Search is unverified;
  (e) fingerprint region bounded at the picker footer `[title..footer]` so the churning
      quota statusline below never destabilizes the race guard.
