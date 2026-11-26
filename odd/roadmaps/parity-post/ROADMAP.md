# Roadmap: Paridad Post-Corte (Doc 13 + Hallazgos Operativos)

- **Slug:** `parity-post`
- **Repo:** `/home/bruno/Code/personal/agent-tts`
- **Rama base:** `main` (`975c1ac`)
- **Fleet (`herdr-fleet`):** `parity-post`
- **Worktree root:** `/home/bruno/Code/personal/agent-tts-worktrees/`
- **Política de integración:** `manual-per-milestone` *(verificar ≠ integrar; merge y push solo bajo autorización explícita del operador y fuera de horario laboral 09:00–18:00 L-V)*
- **Última actualización:** `2026-11-26`

> **Regla Single-Writer:** SOLO la sesión Orquestador (`roadmap`) edita este archivo. Los workers escriben únicamente en su propio `odd/tasks/<hito>.md` (o informe de `research`) dentro de su rama/worktree.

---

## Objetivo y No-Objetivos

- **Objetivo:** Profundizar, diseñar e implementar de forma incremental y verificada (ODD + verificador independiente) las capacidades seleccionadas de fases posteriores (`docs/paridad/13-fases-posteriores.md`) y los hallazgos operativos inmediatos (captura real `claude-multi.txt` v2.1.328).
- **No-Objetivos:**
  - Launcher y creación de worktrees desde móvil (`docs/paridad/13-fases-posteriores.md` §4): pospuesto explícitamente por el operador.
  - Windows como host (§6), canales stable/edge (§7), Cloudflare Access (§8), distribución de skills (§10), reporte de problemas (§12), líneas enviadas (§13), purga dispositivo (§14): fuera de este lote.
  - Cierre de Issue #1 (`bcc7a0dd`) y pushes a GitHub: solo fuera de horario laboral.
  - Autoloop (`9793377e`): **descartado** explícitamente por el operador (2026-11-26) por solapamiento con el flujo `/roadmap` + `herdr-fleet`.
  - Evals (`2f71ed37`): aceptado como test bajo demanda (permanece en backlog para cambios futuros de prompts/modelo de voz).

---

## Contratos transversales e invariantes

1. **C1 — Seguridad y Enmascarado (`secrets_mask.py`):** Cualquier salida HTTP/SSE o respuesta hacia el LLM pasa por enmascarado de secretos ANTES de truncar. Prohibido persistir o loguear tokens/credenciales de proveedores.
2. **C2 — Digests PWA (`test_karaoke_harness.py`):** Todo cambio de bytes en `hosts/herdr/brain/src/herdr_brain/static/` (excepto `sw.js`) debe re-pinear su SHA-256 en `EXPECTED_DIGESTS`.
3. **C3 — Tráfico fail-closed en boot (`tests/browser/conftest.py`):** No añadir `fetch()` en el arranque de la PWA sin registrar ruta por defecto en `conftest.py` (preferir carga on-demand al abrir pestaña/panel/ajustes).
4. **C4 — Separación de suites de test (`AGENTS.md`):** Ejecutar tests unit/API (`--ignore=tests/browser --ignore=tests/e2e`), browser (`tests/browser`), e2e (`tests/e2e`) y JS (`node --test tests/js/`) siempre en procesos separados desde `hosts/herdr/brain/` vía `uv run --extra dev python -m pytest`.
5. **C5 — Worktrees hermanos y CodeGraph:** Todos los worktrees viven bajo `/home/bruno/Code/personal/agent-tts-worktrees/<hito>` (nunca `/tmp`). Cada worktree tiene su propio `.codegraph/` si lo usa.
6. **C6 — Modelo configurado:** Los workers mantienen el modelo configurado (`zai-coding-plan/glm-5.3-flash` por defecto en `opencode`); nunca auto-escalan modelo sin aprobación explícita del operador a través del lead.

---

## Tablero de hitos

**Estados válidos:** `backlog` → `ready` → `in_progress` → `in_review` → `verified` → `integrated` | `blocked` | `dropped`  
**Tipos válidos:** `feature` | `bug` | `refactor` *(todos exigen ODD + `odd-verifier`)* | `research` *(solo lectura)*

| ID | Título | Tipo | Estado | Depende de | Zona (`owns`) | Rama / Worktree | Worker | ODD / Evidencia |
|---|---|---|---|---|---|---|---|---|
| POST-01 | Carga de máquinas (`loadmon.py`, `/load`, umbrales) | `feature` | `integrated` | — | `hosts/herdr/brain/src/herdr_brain/loadmon.py`, ruta `/load*` en `server.py`, `tests/test_loadmon.py`, `tests/conftest.py` *(1 línea `HERDR_BRAIN_LOADMON=off`)* | `main` (`a2daeca`) | — *(cerrado)* | `odd/tasks/post-01-loadmon.md` · `odd-verifier PASS` S1–S6, integrado |
| POST-02 | Cuotas de proveedores (`quotas.py`, `/quotas`, integración `ai-quotas`) | `feature` | `integrated` | — | `hosts/herdr/brain/src/herdr_brain/quotas.py`, tool `check_quotas`, ruta `/quotas`, `tests/test_quotas.py` | `main` (`38fe7f1`) | — *(cerrado)* | `odd/tasks/post-02-quotas.md` · `odd-verifier PASS` S1–S6, integrado |
| POST-06 | Re-pin `claude-multi.txt` + soporte Claude v2.1.328 multiselect real | `bug` | `integrated` | — | `hosts/herdr/brain/src/herdr_brain/dialogs/adapters/claude.py`, `dialogs/numbered.py`, `tests/fixtures/dialogs/claude-multi*`, `tests/test_harness_dialogs.py` | `main` (`a71c754`) | — *(cerrado)* | `odd/tasks/post-06-claude-multi.md` · `odd-verifier PASS` S1–S8, integrado |
| POST-03 | Layout adaptativo para tablet, PC y plegables | `feature` | `blocked` | `POST-07` *(serializado por zona PWA)* | `hosts/herdr/brain/src/herdr_brain/static/index.html`, `app.js`, `tests/browser/` | `feat/post-03-adaptive-layout` | — | `odd/tasks/post-03-adaptive-layout.md` |
| POST-04 | Temas importables con control de contraste WCAG 4.5:1 *(fase A backend + contrato WCAG primero; UI tras POST-03)* | `feature` | `ready` | — *(backend `themes.py` + `/themes` disjunto de `static/`)* | `hosts/herdr/brain/src/herdr_brain/themes.py`, `tests/test_themes.py`, rutas `/themes*` en `server.py` | `feat/post-04-themes` | — | `odd/tasks/post-04-themes.md` |
| POST-05 | Superficie GLSDK / sesiones sin pantalla (`/sdk/v1`) | `feature` | `integrated` | — | `hosts/herdr/brain/src/herdr_brain/sdk.py`, `access.py` *(solo 2 reglas write `/sdk/v1/*`)*, subcomando `sdk` en `ops_cli.py`/`bin/herdr-brain` | `main` (`ad13afb`) | — *(cerrado)* | `odd/tasks/post-05-sdk.md` · `odd-verifier PASS` S1–S8, integrado |
| POST-07 | Reorganización visual subcabecera y composer cockpit PWA (`6d33e979`) | `refactor` | `ready` | — | `hosts/herdr/brain/src/herdr_brain/static/index.html`, `app.js`, `tests/browser/` | `feat/post-07-cockpit-header-ux` | — | `odd/tasks/post-07-cockpit-header-ux.md` |

*(Nota de concurrencia: POST-01, POST-02 y POST-06 tienen zonas de backend completamente disjuntas salvo enganches mínimos en `server.py` entre POST-01/POST-02; los hitos de frontend PWA `POST-03`, `POST-04` y `POST-07` se serializan para evitar conflictos en `index.html`/`app.js` y `EXPECTED_DIGESTS`.)*

---

## Relevo de sesión

- **Estado global:** **4/7 integrados** en `main` local (`POST-06` `a71c754`, `POST-05` `ad13afb`, `POST-02` `38fe7f1`, `POST-01` `a2daeca`), 2 lanzándose (`POST-07` cockpit UX + `POST-04` temas backend/WCAG), 1 `blocked` (`POST-03` tras `POST-07`).
- **Workers activos:** lanzando `cockpitux` (`POST-07`) y `themes` (`POST-04` backend+WCAG).
- **Pendiente del operador:** push a `origin/main` + cierre Issue #1 (`bcc7a0dd`) autorizados a partir de las **19:00 h**; revisar planes de `cockpitux` (`POST-07`) y `themes` (`POST-04`) en sus pestañas.
- **Siguiente lote `ready`:** `POST-03` (layout adaptativo tablet/PC/plegables) + UI de `POST-04` tras integrar `POST-07`.
- **Para retomar en una pestaña nueva:** `Carga la skill roadmap y retoma odd/roadmaps/parity-post/ROADMAP.md`

---

## Log de decisiones e integraciones

- **2026-11-26:** Roadmap `parity-post` inicializado tras aprobación del operador. Captura real de `AskUserQuestion` multiselect de Claude Code v2.1.328 guardada desde `w11:p1X` en `odd/roadmaps/parity-post/briefs/claude-multi-v21328.{ansi,txt}` (verificado que el detector actual devuelve `None` frente a la captura real → añadido `POST-06`). Creada tarea `6d33e979` (`POST-07`) para reorganizar visualmente la subcabecera/composer del cockpit PWA.
- **2026-11-26:** `POST-06` (`feat/post-06-claude-multi` @ `a156a07`) pasa a **`verified`** tras ronda 2 de `odd-verifier` independiente (**PASS** en S1–S8, 0 bloqueantes, C4 `237 passed`, `ruff` limpio). Follow-up anotado: single-select real de Claude v2.1.328 con descripciones en fila propia.
- **2026-11-26:** `POST-05` (`feat/post-05-sdk` @ `8d98e7d`) pasa a **`verified`** tras ronda 2 de `odd-verifier` independiente (**PASS** en S1–S8, 0 bloqueantes, `118/118` en `test_sdk` + `test_pairing_api` + `test_approval_ntfy` + `test_audit_routes`, pruebas en vivo de symlink-safety/SSRF y coexistencia `approval_token`/AC8 OK).
- **2026-11-26:** `POST-02` (`feat/post-02-quotas` @ `24fc531`) pasa a **`verified`** tras ronda 2 de `odd-verifier` independiente (**PASS** en S1–S6, 0 bloqueantes, `276/276` en `test_quotas` + `test_tools` + `test_consult` + `test_llm`, `ruff` limpio, `QuotaSource`/`AiQuotasSource` + masking C1 + SWR thread-safe + `isfinite` OK).
- **2026-11-26:** `POST-01` (`feat/post-01-loadmon` @ `2d9c926`) pasa a **`verified`** tras ronda 2 de `odd-verifier` independiente (**PASS** en S1–S6, 0 bloqueantes, `80/80` en `test_loadmon` + `test_access`, `ruff` limpio, alertas disco no-raíz + tolerancia `ENOSPC`/UTF-8 + `HERDR_BRAIN_LOADMON=off` + `router.on_shutdown` OK). Lote backend 4/4 completo y verificado.
- **2026-11-26 (18:05):** Integrados en `main` local los 4 bloques previos (`6ea43d3` Punto A, `e498162` `MANUAL-TESTS.md`, `3d3d93f` `#tab-model-tag`, `29f4ac1` roadmap) + los 4 hitos backend verificados (`a71c754` `POST-06`, `ad13afb` `POST-05`, `38fe7f1` `POST-02`, `a2daeca` `POST-01`). Verificación integrada verde: unit/API `775/775`, JS `831/831`, browser `15/15`; `herdr-brain.service` reiniciado y `doctor` 100% PASS. Push reservado a partir de las 19:00 h. Arranca lote PWA (`POST-07` + `POST-04` backend/WCAG en paralelo; `POST-03` serializado tras `POST-07`).
