# Feature: Pending Notices in the Ajustes Sheet (herdr-brain)

**Repo:** `hosts/herdr/brain` (monorepo `agent-tts`)
**Created:** 2026-10-15
**Status:** implementation + tests green; docs done; pending commit
**Design:** `docs/voice-stack/prds/03-anuncios-pendientes-atribuidos.md` (PRD 03 —
el PRD no especifica la ubicación del panel en la UI; este doc la fija)
**Engram mirror:** `odd/pending-notices-in-settings-sheet/tasks`

## Objective

El panel de avisos pendientes (`📋 Pendientes`, el `#pending-panel` con
🔊 Escuchar / ✓ Anunciado / ✕ Descartar por registro) deja de mover el layout
del cockpit: sale del flujo del documento y pasa a una **bottom sheet** propia,
abierta desde el menú de Ajustes (⚙ → `🔔 Pendientes (N)`).

## Problem / Why

`app.js` insertaba `pendingSection` en el flujo del documento
(`consultPanel.parentNode.insertBefore(...)`), así que cada aparición o
desaparición de la lista empujaba la página entera — el salto de layout que
reportó el usuario. `#toast` y `#approval-float` ya son `position: fixed`; solo
el panel de pendientes vivía en el flujo.

## Scope / Constraints

- **In scope:** sacar el panel del flujo del documento a una bottom sheet
  dedicada; añadir el lanzador en Ajustes + contador; cablear
  abrir/cerrar/Escape/back.
- **Out of scope:** `#pending-banner` ("N agentes esperan tu OK") se queda
  igual; **sin** icono nuevo en el header; sin cambios en `speech.js` (store),
  en las acciones de fila ni en el servidor.
- Las filas y acciones no cambian: 🔊 Escuchar / ✓ Anunciado / ✕ Descartar.
- Estilo: código/comentarios/tests/commits en inglés; copy de UI en español.
- Runners: `node --test tests/js/` y
  `.venv/bin/python -m pytest tests/e2e/test_m3_pending.py -q`.
- Entrega: un commit como unidad de trabajo; **sin push**.

## Specs

- **S1** — El panel de pendientes MUST NOT insertarse en el flujo del
  documento; MUST montarse dentro de `#pending-sheet-body`.
- **S2** — El sheet de Ajustes (⚙) MUST llevar un lanzador
  `🔔 Pendientes (N)` (`#pending-open`) con contador vivo
  (`#pending-menu-count`).
- **S3** — Una bottom sheet dedicada (`#pending-sheet` + `#pending-backdrop`,
  misma forma que `#conv-sheet`) MUST alojar el panel y abrirse desde el
  lanzador.
- **S4** — Abrir el lanzador MUST ocultar Ajustes sin dejar una entrada de
  historial huérfana (reutiliza la entrada con `replaceState`).
- **S5** — ✕ / backdrop / Escape / back de Android MUST cerrar el sheet; el
  contenido del panel y sus acciones de fila MUST quedar sin cambios.
- **S6** — La lista de 100 filas MUST hacer scroll dentro de
  `#pending-sheet-body`, con su última fila despejada del footer fijo y su
  botón como objetivo real de puntero.

## Tasks

- **T1** (S1, S5) — `app.js`: montar el panel en el body del sheet; filas y
  acciones intactas. *Ruta:* inline. *Estado:* hecho.
- **T2** (S2, S3) — `index.html`: fila lanzadora + contador, sheet + backdrop +
  body + CSS. *Ruta:* inline. *Estado:* hecho.
- **T3** (S2, S4, S5) — `app.js`: `setPendingSheet` / `openPendingSheet` /
  `closePendingSheet`, listeners, `popstate` + Escape. *Ruta:* inline.
  *Estado:* hecho.
- **T4** (S1, S2, S3, S5) — `tests/js/pending-wiring.test.js` estructural
  RED→GREEN. *Ruta:* inline. *Estado:* hecho (`node --test tests/js/`
  387/387).
- **T5** (S6) — `tests/e2e/test_m3_pending.py`: `open_pending` + pin de
  geometría reescrito dentro del sheet. *Ruta:* inline. *Estado:* hecho
  (`pytest tests/e2e/test_m3_pending.py` 6/6).

## Log

- **L1** — petición original (verbatim, usuario): «los avisos que me aparecen
  con opcion de escuchar, aceptar y descartar me generan problemas en pantalla,
  me mueven la layout quiza lo meteria en una campanita en el header (o en un
  submenu) y ahi levantaria un popup con esos»
- **L2** — alcance (verbatim): «Solo el panel de Pendientes».
- **L3** — ubicación (verbatim): «🔔 dentro de Ajustes» + «botón 🔔 (mismo
  lenguaje que ⚙/🔊»; resuelto como: la entrada vive **dentro** del sheet de
  Ajustes, sin icono nuevo en el header.
- **L4** — contenedor (verbatim): «Bottom sheet aparte».
- **L5** — evidencia: `node --check app.js` OK; `node --test tests/js/`
  387/387; `.venv/bin/python -m pytest tests/e2e/test_m3_pending.py` 6/6.
- **L6** — gotcha: el pin de geometría falló primero midiendo en pleno slide
  (la transición de 280 ms `translateY` del sheet); `open_pending` ahora espera
  a que la transformación asiente antes de medir.
- **L7** — el diagnóstico se hizo con evidencia: se descartó un bug de CSS
  inyectando telemetría (`position: fixed`, `max-height: 506px` y
  `transform: matrix(...,538)` a la vez ⇒ transición en curso, no layout roto).
