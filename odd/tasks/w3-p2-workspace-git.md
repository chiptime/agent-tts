# W3-P2 — Workspace File & Git Explorer

Branch: `feat/w3-p2-workspace-git`; base: `35a537a`; runner: pytest + node from `hosts/herdr/brain`.

## Specs
- S1: Módulo `src/herdr_brain/workspace.py` de solo lectura: árbol de ficheros acotado en cwd del pane, tamaño y mtime, lectura de fichero segura con límite de tamaño (64 KB), rechazo estricto de escapes de ruta (`..`, symlinks fuera de cwd).
- S2: Endpoints REST con autenticación/device-bearer:
  - `GET /workspace/tree?pane_id=`
  - `GET /workspace/file?pane_id=&path=`
  - `GET /workspace/changes?pane_id=` (`git status` y `git diff` en el cwd del pane).
- S3: PWA frontend: Pestaña «Ficheros» en el drawer del agente con explorador de árbol y visor de diffs para inspeccionar cambios de cada pane.
- S4: Herramienta LLM de voz `read_workspace_changes` conectada al mismo módulo.
- S5: Tests unitarios de seguridad de rutas (traversal guard), endpoints y componentes UI.

## Invariantes transversales
- C1: Seguridad y auth intactos; control estricto de ruta (nunca escapar del cwd del pane); SW sin credenciales.
- C2: Repinear `EXPECTED_DIGESTS` en `test_karaoke_harness.py` si se modifica `static/`.
- C3: Harnesses browser fail-closed en tráfico no declarado.
- C4: Direct-child CSS only (`> span:not(.who)`).
- C5: Verificador independiente obligatorio al finalizar.

## Tasks
- [x] T1 S1: `src/herdr_brain/workspace.py` read-only module with traversal guards and git status/diff. Route: inline. Commit: `d593809` (37 tests).
- [x] T2 S2: REST endpoints (`/workspace/tree`, `/workspace/file`, `/workspace/changes`) + authentication. Route: inline. Commit: `32b6c4e` (9 tests, incl. access-class pin).
- [x] T3 S3: PWA «Ficheros» drawer tab and diff viewer + digest repin. Route: inline. Commit: `5161e8f` (15 node tests + browser/e2e suites).
- [x] T4 S4: LLM tool `read_workspace_changes`. Route: inline. Commit: `f1a0fbe` (8 tests).
- [x] T5 S5: Unit, security, and browser tests. Route: inline. Evidence: unit 4809 passed / browser 467 passed (5 pre-existing at base, proven via scratch worktree) / e2e 28 passed (4 pre-existing at base, proven) / js 827 passed.
- [ ] T6 S1-S5: Independent verification and per-S# verdict. Route: delegated (`gentle-ai-verify`, C5). **BLOQUEADO (2026-11-26)**: dos lanzamientos del subagente fallaron con "The usage limit has reached" (límite del runtime, reintentado 2x). Verificación inline de respaldo hecha: suites completas verdes (unit 4809 / browser 467+5 pre-base / e2e 28+4 pre-base / js 827) y digests repineados verificados contra los bytes reales en disco (sha256sum OK x4). Pendiente: relanzar `gentle-ai-verify` cuando el límite lo permita y registrar el veredicto por S#.

## Log
- L1: Hito **W3-P2 (Explorador de ficheros y cambios Git del workspace)** de Ola 3. Rama `feat/w3-p2-workspace-git` en worktree `../agent-tts-worktrees/w3-p2-workspace-git`.
- L2 (2026-11-26): Implementación completa T1-T5 en 4 work-unit commits. Decisiones: (a) crew panes remotos rechazados explícitamente (superficie local-only, sin proxy — el módulo no puede leer un FS remoto y un fallback local leería el workspace equivocado); (b) auth por clasificación de lectura por defecto en access.py — `/workspace/*` queda gated por device-bearer, con test que lo pinea (`TestAccessClass`); (c) caps: 64 KB lectura, 2000 entradas/profundidad 12 árbol, 256 KB diff; (d) `files.js` módulo puro + wiring en app.js (createElement/textContent only), fetch solo on-demand (boot traffic sin cambios, C3); (e) `files.js` añadido a `PWA_STATIC_NAMES` (pwa_static.py) y al precache de `sw.js` (SHELL_ASSETS); (f) `EXPECTED_DIGESTS` repineados para `/`, `/app.js`, `/i18n/es.js`, `/i18n/en.js` (C2); (g) 3 pins de superficie de tools actualizados con el nuevo nombre (`test_tools.py`, `test_consult.py`, `test_llm.py`). Fallos pre-existentes en base verificados con worktree scratch en `35a537a`: 5 browser (test_live_engine, test_pane_keys, test_pane_keys_races) y 4 e2e (test_m3_pending, test_m4_fallback) — idénticos en base, no atribuibles a esta rama. Ruff del engine no aplica a brain (tools.py ya arrastra 136 errores con esa config); estilo local seguido.
- L3 (2026-11-26): T6 bloqueado — el runtime de subagentes devolvió "The usage limit has been reached" en dos lanzamientos de `gentle-ai-verify` (ses_dea805334ffeU3q7mj6UZCAb0b, ses_dea7f15f0ffeLJZUVf3h3SmD2h). Bloqueo externo, no de la rama. Verificación inline de respaldo completada y registrada en T6.
