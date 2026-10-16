# Feature: Cockpit Body as Tabs — Vista del agente / Pantalla (herdr-brain)

**Repo:** `hosts/herdr/brain` (monorepo `agent-tts`)
**Created:** 2026-10-15 · **Reconstruido:** 2026-10-16 (ver L2)
**Status:** implementado + tests verdes + commiteado (`a3cd8d8`); **sin push**
**Design:** amplía `docs/PRD-call-drawer-redesign.md` §4.1 (el PRD describía un
cockpit de un solo cuerpo con el terminal en una franja ~110px; ver FR3
enmendado)
**Engram mirror:** `odd/cockpit-tabs/tasks`

## Objective

El cuerpo del cockpit deja de apilar el transcript sobre el terminal en un solo
scroll: pasa a ser **UNA superficie con DOS tabs** — "Vista del agente"
(`#glance-turns`) y "Pantalla" (`#view-screen`) — con un solo panel visible a la
vez, de modo que cada superficie gana altura real.

## Problem / Why

El terminal vivía como una franja permanente de ~110px bajo el transcript
(FR3 del PRD). Con los títulos de sesión largos y las 20 turns del transcript,
ninguna de las dos superficies tenía altura útil en un móvil de ~640px. El
usuario pidió explícitamente moverse entre ambas mediante tabs.

## Scope / Constraints

- **In scope:** los dos tabs y su cableado (click + teclado), persistencia de la
  elección, y la reasignación de altura del terminal.
- **Out of scope:** el drawer de llamada, la pill, el footer, el pending banner
  y las actualizaciones keyed del poll — **sin cambios**. Sin cambios de
  servidor: `/view` y `/conversation` se reutilizan tal cual.
- El copy de UI va en español ("Vista del agente", "Pantalla"); código,
  comentarios, tests y commit en inglés.
- Runners: `node --test tests/js/` y
  `uv run python -m pytest tests/browser/test_agent_tabs.py -q`.
- Entrega: una unidad de trabajo (commit `a3cd8d8`); **sin push**.

## Specs

- **S1** — El cuerpo del cockpit MUST ser UNA superficie con DOS tabs:
  "Vista del agente" (`#glance-turns`) y "Pantalla" (`#view-screen`).
- **S2** — Exactamente UN panel MUST estar visible a la vez (`#agent-panel` XOR
  `#screen-panel`); el panel oculto MUST llevar la clase `hidden`.
- **S3** — La elección de tab MUST sobrevivir a un reload, vía `localStorage`
  (`herdr-brain-agent-tab`); el tab por defecto es "Vista del agente".
- **S4** — El tablist MUST exponer semántica ARIA
  (`role="tablist"` / `role="tab"` / `role="tabpanel"`, `aria-selected`,
  `aria-controls`) con **roving tabindex** y navegación por
  `ArrowLeft` / `ArrowRight`.
- **S5** — Cada superficie MUST recibir altura real del cuerpo: el terminal
  MUST NOT volver a ser una franja fija de ~110px bajo el transcript.

## Tasks

- **T1** (S1, S2, S5) — `index.html`: markup del tablist + `#agent-panel` /
  `#screen-panel`, CSS `.agent-tabs` / `.agent-tab` / `.agent-panel` con
  ellipsis defensivo. *Ruta:* inline. *Estado:* hecho.
- **T2** (S2, S3, S4) — `app.js`: `applyAgentTab` / `selectAgentTab` /
  `agentTabKeydown`, listeners de click + flechas, restauración desde
  `localStorage`. *Ruta:* inline. *Estado:* hecho.
- **T3** (S1–S5) — tests: `tests/browser/test_agent_tabs.py` (comportamiento
  real en Chromium), `tests/js/agent-tabs-wiring.test.js` (forma estructural),
  `tests/test_server.py::TestStatic::test_index_agent_view_tabs` (contrato del
  markup servido). *Ruta:* inline. *Estado:* hecho.

## Log

- **L1** — petición original (verbatim, usuario): «quiero que en brain,
  juntemos pantalla y vista del agente a tabs, para moverme entre los dos
  mediante tabs»
- **L2** — reconstrucción (2026-10-16): este doc se escribió **después** de que
  la feature existiera ya en el árbol de trabajo **sin commitear**. Se
  descubrió al preparar commits de otro arreglo (timeout de `herdr agent prompt`
  + input de respuesta), con el que estaba **entrelazada** en `app.js` y en el
  pin de digests de `tests/browser/test_karaoke_harness.py`. Se aisló como
  unidad propia: se revirtieron temporalmente los hunks ajenos y se verificó
  **por hash** que la reconstrucción era byte-exacta (`app.js` volvió a
  `b57c7b93…`, el valor que esta feature había pinneado) antes de commitear
  `a3cd8d8`. Los `S#` de arriba describen lo que el código hace, no un plan
  previo.
- **L3** — evidencia: `node --test tests/js/` 391/391; `uv run python -m pytest
  tests/browser tests/js -q` 156/156; `uv run python -m pytest tests
  --ignore=tests/e2e -q` 1947/1947 sobre el HEAD commiteado.
- **L4** — gotcha: `EXPECTED_DIGESTS` en `tests/browser/test_karaoke_harness.py`
  pinna los bytes **servidos**, así que cualquier edición de un fichero
  estático invalida su hash; reaplicar una edición con un wrapping de comentario
  distinto cambia los bytes. Recalcular desde disco y re-verificar tras cada
  reaplicado.
