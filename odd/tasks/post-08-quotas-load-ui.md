# POST-08 — Pantallita / tarjetas PWA on-demand: Cuotas + Carga de máquina

**Objective**: Two clean status cards ("Cuotas de IA" + "Carga del host") inside the
Ajustes (⚙) sheet, fetched strictly on-demand when the sheet opens (the
`pushUI.refresh()` pattern, via `readFetch`), zero new header buttons.
**Branch**: `feat/post-08-quotas-load-ui` (base `main` @ `48684b1`)
**Delivery strategy**: `ask-on-risk` (local work-unit commits; push/PR forbidden by brief)
**Test runner**: `node --test "tests/js/*.test.js"` + Playwright browser suite
(`PYTHONPATH=<wt>/hosts/herdr/brain/src <main-checkout>/.venv/bin/python -m pytest tests/browser/...`),
always in SEPARATE processes, always from `hosts/herdr/brain/`.

## Specs

- **S1 (on-demand, C3)**: The cards fetch `GET /quotas` and `GET /load` ONLY when
  the Ajustes sheet opens (same seam as `pushUI.refresh()` in `openSettings()`),
  through `readFetch` (device bearer + 403 pairing recovery). NEVER at boot,
  never polling. "estrictamente on-demand al abrir Ajustes, igual que
  `pushUI.refresh()`".
- **S2 (Cuotas card)**: For each provider record render
  `display_name || provider`, a `used_percent` progress bar (width clamped
  0-100, percent text shown), a `state` chip (ok/warning/critical/other),
  compact meta line with `label`, `detail`, `resets_at` formatted
  device-locally as `Thu 18:59` (front-side; backend ships raw ISO — approved
  default 1), and costs (`$X hoy`, `$Y mes`) when finite numbers are present
  (approved default 3). Honest states: `available:false` → honest empty
  message (+ `reason` when present), `stale:true` → cached-data hint,
  fetch failure → fail-soft status line, never a throw. Manual per-card
  ↻ refresh button (approved default 2), 44px tap target.
- **S3 (Carga card)**: Render `host`, CPU/RAM/Disk percent mini-bars + values,
  network `net_rx_bps`/`net_tx_bps` formatted (B/s → KB/s → MB/s), and
  `alerts.active/configured` line only when `configured > 0`. Honest states:
  `available:false` → message + `reason`; metrics `null` (before the first
  minute sample) → "waiting for first sample" message.
- **S4 (C1 security)**: Every server-provided string reaches the DOM through
  `textContent`/text nodes — never `innerHTML` with server data. Numeric
  fields render only when `typeof === "number"` and finite.
- **S5 (i18n)**: New `quotas.*` / `load.*` keys in `i18n/es.js` (source) and
  `i18n/en.js`; a locale switch repaints the cards (I18N subscribe).
- **S6 (module shape)**: `createStatusCards(deps)` factory exported from
  `settings.js` (`BrainSettings`, `module.exports`), with injected
  `request`/`doc`/`t` so the node suite drives a fake DOM. No new static
  files, no `server.py`/`sw.js`/backend edits (settings.js is already served
  by the `_static_js` list and cached in `PWA_STATIC_NAMES`).
- **S7 (C2 digests)**: After the final bytes of each feature commit, re-pin
  `EXPECTED_DIGESTS` in `tests/browser/test_karaoke_harness.py` for every
  touched served file: `/settings.js` (T2 commit), then `/`, `/app.js`,
  `/i18n/es.js`, `/i18n/en.js` (T3 commit).
- **S8 (tests)**: `tests/js/status-cards.test.js` (fake DOM: render shapes,
  bars, chips, resets/cost formatting, net formatting, stale/unavailable/
  fail-soft, refresh re-fetch, in-flight guard) and
  `tests/browser/test_status_cards.py` (opening Ajustes fires exactly
  `/quotas` + `/load` — nothing extra at boot; bars/chips/meta render;
  ↻ refetches; unavailable + stale + 500 fail-soft). `tests/browser/conftest.py`
  gains fail-soft default routes for `/quotas` + `/load` (the `/push/key`
  pattern) so legacy suites that open Ajustes never hit blocked traffic.
- **S9 (scope/governance)**: Edit ONLY `static/index.html`, `app.js`,
  `settings.js`, `i18n/*.js`, `tests/browser/**`, `tests/js/**` and this
  document. NO `ROADMAP.md`, NO `server.py`/backend Python. Local commits on
  `feat/post-08-quotas-load-ui` only; NO merge/push/PR. Independent
  verification is dispatched by the lead (`odd-verifier`) after `done`.

## Tasks

- **T1** — Feature document + Engram mirror (`odd/post-08-quotas-load-ui/tasks`). Route: inline. Status: done (this document).
- **T2** — `createStatusCards` pure module in `settings.js` + node unit tests
  (S2, S3, S4, S6; RED → GREEN) + re-pin `/settings.js` (S7). Route: inline. Commit: `8c3732a`. RED→GREEN observed.
- **T3** — Ajustes wiring: index.html containers + CSS (S2/S3), app.js
  `openSettings()` fetch + I18N repaint (S1, S5), both i18n dicts (S5),
  conftest defaults + Playwright suite (S8; RED → GREEN), re-pin `/`,
  `/app.js`, `/i18n/es.js`, `/i18n/en.js` (S7). Route: inline. Commit: `ff65cc1`. RED→GREEN observed.
- **T4** — Full verification in separate processes (JS suite + browser suite),
  digest spot-check, Log close-out, `done` report to lead (S8, S9). Route: inline. Commit: — (verification only). Status: done (L5).

## Log

- **L1** (2026-11-26): Original request verbatim (lead brief, herdr-fleet
  `parity-post -> quotasui`): «## 1. Contexto / Origen — Hito **POST-08
  (Pantallita / tarjetas PWA *on-demand* para Cuotas `GET /quotas` y Carga de
  máquina `GET /load`)** del roadmap `odd/roadmaps/parity-post/ROADMAP.md`
  (tipo: `feature`). Rama base `main` (`48684b1`); rama asignada
  `feat/post-08-quotas-load-ui»`. Contratos: C1 textContent-only, C2 re-pin
  `EXPECTED_DIGESTS` tras cualquier cambio de bytes en `static/` (salvo
  `sw.js`), C3 fail-closed ante fetch no declarado en arranque — PROHIBIDO
  pedir `/quotas` o `/load` en boot, solo al abrir Ajustes con `readFetch`;
  C4 tests JS y Playwright en procesos separados desde `hosts/herdr/brain/`;
  C5 operar solo en este worktree; C6 no escalar de modelo. Ubicación
  aprobada por el operador («dale»): dentro de Ajustes (⚙), tarjetas limpias
  con refresco manual, carga estrictamente on-demand. §3.2 del brief exige
  presentar el plan y esperar aprobación explícita antes de editar: plan
  presentado con 3 decisiones abiertas y recomendaciones; el operador
  respondió «dale» (2026-11-26) aceptando los defaults: (1) formateo de
  `resets_at` en front, (2) ↻ por tarjeta, (3) costes USD visibles.
- **L2** (2026-11-26): Investigation evidence: `/quotas` payload
  `{available, reason, stale, fetched_at, providers:[{provider, display_name,
  kind, label, used_percent, resets_at, state, status_indicator,
  status_summary, detail, cost_today_usd, cost_month_to_date_usd}]}` —
  `resets_at` ships raw ISO (backend `format_reset` is voice-tool-only), so
  the card formats it device-locally. `/load` payload `{host, available,
  reason?, ts, cpu_percent, ram_percent, disk_percent, net_rx_bps,
  net_tx_bps, alerts:{configured, active}}` with `null` metrics before the
  first minute sample. `settings.js` rides server `_static_js` list +
  `PWA_STATIC_NAMES`, so extending it adds no served files.
- **L3** (2026-11-26, T2): RED observed (`createStatusCards is not a
  function`, 15 tests), GREEN after implementation (17/17 in
  `tests/js/status-cards.test.js`; the fake DOM throws on any innerHTML
  use, pinning C1). Existing `tests/js/settings.test.js` still 15/15.
  `/settings.js` re-pinned → `tests/browser/test_karaoke_harness.py`
  12/12 green. Commit `8c3732a`.
- **L4** (2026-11-26, T3): Browser RED observed (9/9 fail without
  wiring). GREEN after wiring, conftest defaults and i18n; refined the
  approved per-card refresh into `refreshCard(which)` after the ↻ was
  caught refetching BOTH endpoints (the browser suite caught it).
  **Regression found and fixed**: with the two new cards the
  `#settings-sheet` overflowed the viewport (no max-height) and pushed
  `#theme-btn` offscreen (y=-63.5, measured boundingClientRect) —
  `test_theme`/`test_pwa_shell` clicks timed out. Fixed with the
  `#conv-sheet` recipe: sheet `max-height: 85dvh` + flex column,
  `.s-head` `flex-shrink: 0`, `.s-body` the one scroll surface
  (`flex: 1; min-height: 0; overflow-y: auto`).
- **L5** (2026-11-26, T4): Full verification (separate processes):
  `node --test "tests/js/*.test.js"` 848/848;
  `pytest tests/browser` 483 passed / 6 skipped / **4 failed — the
  pre-existing `test_pane_keys` + `test_pane_keys_races` failures,
  proven pre-existing by re-running them at `8c3732a` (my module
  commit, inert at runtime ≡ base `48684b1` behavior) where they fail
  identically while the theme/shell trio passes**. Digests re-pinned:
  `/`, `/app.js`, `/settings.js`, `/i18n/es.js`, `/i18n/en.js`.
