# POST-07 — Cockpit header/composer UX: dock inferior único (Opción C)

Reorganiza la zona composer+tabs del cockpit PWA: tabs Vista/Terminal/Ficheros + modelo +
🔍 + ⛶ y el composer (input responder + Detener + Enviar) se docks al FONDO como un dock
único de dos filas; arriba queda solo el header. Prioridad del operador: minimizar altura
vertical para lectura limpia en móvil (360-412px) y desktop.

- Branch: `feat/post-07-cockpit-header-ux` (base `main` @ 8c16668, incluye #tab-model-tag)
- Delivery: single commit on feature branch; push/PR = decisión del usuario
- Runner: `cd hosts/herdr/brain && uv run --extra dev python -m pytest tests/browser <target>`
  (browser tests en proceso separado de unit/API)

## Specs

- **S1** (REV 2, 2026-11-26: operador rechazó el dock al piso — "no me convence, mejor
  todo arriba"): zona compacta ARRIBA en orden de DOM — composer (fila 1) + tabs
  (fila 2) bajo el header, panel bg + hairlines, sin `order`.
- **S2**: alturas objetivo: fila composer ≈ 50-52px (padding 2px + input 44px), fila
  tabs ≈ 42px (tabs `min-height: 40px`), zona total ≤100px (hoy medido ~94px; antes
  113px).
- **S3** (REV 2): overlays fijos SIN cambios respecto de base (anclados al footer;
  la var `--dock-height` y sus reglas de estado se eliminaron al revertir el dock).
- **S4** (REV 2, desktop ≥900px): composer+tabs como UNA tarjeta centrada de 760px
  arriba (composer con radio superior, border-top de tabs como hairline interna).
- **S5** (C2): cualquier byte de `static/index.html` re-pinea su SHA-256 en
  `EXPECTED_DIGESTS` (`tests/browser/test_karaoke_harness.py`). app.js y herd-group.js
  NO se modifican.
- **S6** (C3): cero fetches nuevos en boot (cambio solo CSS); el CSS direct-child
  `#conversation .turn.has-rendered > span:not(.who)` queda intacto; IDs/roles/atributos
  i18n de tabs y composer sin cambios. Validación con `boundingClientRect` en Playwright
  móvil 360×740 y desktop 1280×800. PROHIBIDO editar ROADMAP.md o backend Python.
  (Herencia REV 1 mantenida: tabs display:block + line-height 40 con ellipsis real,
  #glance-label display:block, tag modelo ≤480px max 84px.)

## Tasks

- T1 (S6): ✅ RED `tests/browser/test_cockpit_dock_layout.py` — 2 tests (móvil 360×740,
  desktop 1280×800). RED observado: tabs.top=128, dock=113px, desktop width=1280.
- T2 (S1,S2): ✅ CSS dock — `.agent-tabs` order:9 / `#pane-reply` order:10, tabs
  min-height 40px, composer padding 2px, fondos panel + hairlines. GREEN: dock=92px
  (tabs 41 + composer 51), lectura arranca en y=66 (antes y=179).
- T3 (S3,S4): ✅ `--dock-height` 92px + estados (:has composer.hidden→41, zen→51,
  zen+hidden→0, agent-view.hidden→0); offsets de #tts-fab/#tts-bar/#state-pill/
  #voice-unlock; desktop tarjeta 760px centrada con radio superior. Corrección post-
  screenshot móvil: tabs display:block + line-height 40 (ellipsis real, antes recorte
  centrado "ERMINAI ICHERO:"), #glance-label display:block, tag modelo ≤480px max 84px.
- T4 (S5): ✅ `EXPECTED_DIGESTS["/"]` re-pineado ×2 (ef26cffd… tras la corrección;
  solo index.html cambia; app.js/herd-group.js intactos).
- T5 (S6): ✅ GREEN — 45 passed (dock+agent_tabs+model_tag+reply_visibility+destructive+
  operator_dock), 12 passed karaoke_harness (digests), 831 passed node --test tests/js/.
  Screenshots verificadas por el operador en /tmp/opencode/post-07-dock-*.png.
  Sin cambios en ROADMAP.md ni backend Python; direct-child CSS intacto; cero fetches nuevos.

## Log

- L1 (2026-11-26): petición verbatim del operador (herdr-fleet parity-post → cockpitux):
  "el chip del modelo ocupa poco pero la zona de tabs Vista/Terminal/Ficheros + modelo +
  lupa + fullscreen + input responder + Detener + Enviar queda visualmente fea/desordenada;
  prioridad: minimizar altura vertical para lectura limpia en móvil (360-412px) y desktop".
- L2 (2026-11-26): operador elige Opción C ("te pedire el c y vemos com oqueda") tras ver
  A/B/C con mockups ASCII y px. B descartada (ahorraba menos), A descartada (mínima).
- L3 (2026-11-26): investigación previa mapeada (mem obs 14042): composer hoy renderiza
  ARRIBA de tabs (orden DOM), presupuesto 65+60+51+81=257px.
- L4 (2026-11-26): RED inicial móvil falló por aserción mía mal escrita (tabs.top en vez
  de turns.top — en C los tabs van al piso); corregido el test, no el CSS.
- L5 (2026-11-26): screenshot móvil v1 mostró tabs colapsados ("ERMINAI ICHERO:" — texto
  centrado recortado en tabs flex con raw text) mientras el chip comía 110px. Fix CSS:
  .agent-tab display:block + line-height 40px (text-overflow: ellipsis ahora sí aplica),
  chip capado a 84px en ≤480px. Desktop verificado bien en v1. Re-pin digest necesario ×2.
- L6 (2026-11-26): estado final medido móvil 360×740: header 65 → lectura y=66, dock 92px
  al piso (tabs 41 + composer 51), flush con footer (659). Desktop: tarjeta 760px centrada.
- L7 (2026-11-26, REV 2): operador vio las screenshots del dock y dijo verbatim:
  "no me convence, mejor todo arriba". Se revierte la posición (composer+tabs vuelven
  arriba en orden DOM) manteniendo compactación, ellipsis real, chip acotado y tarjeta
  desktop. Test re-escrito a las nuevas expectativas (RED contra el dock → GREEN).
- L8 (2026-11-26, REV 2 estado final): móvil 360×740 — composer y=66 (bajo header),
  zona 94px (composer ~52 + tabs 42), lectura ~y=160 (antes de POST-07: 179). Desktop
  1280×800 — tarjeta 760px centrada arriba. Overlays otra vez como base (sin var).
  Digest re-pineado a 163dc282… 58 passed (suite enfocada + karaoke).
