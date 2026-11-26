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

- **S1** (Opción C, aprobada 2026-11-26): dock inferior único — `#agent-tabs` y `#pane-reply`
  se reposicionan al fondo de `#agent-view` (tabs arriba del composer) SIN mover DOM:
  `order` de CSS. Arriba queda solo `header` (+ banners contextuales).
- **S2**: alturas objetivo móvil: fila tabs ≈ 40px (tabs `min-height: 40px`), fila composer
  ≈ 48px (padding 2px + input 44px), dock total ≈ 90px (hoy: 111px). Chrome total
  257px → ≈ 234px.
- **S3**: overlays fijos anclados al footer (`#tts-fab`, `#tts-bar`, `#state-pill`,
  `#voice-unlock`) no deben chocar con el dock: offset extra vía `--dock-height`
  (90px por defecto; 41px si composer `.hidden`; 49px en zen; 0px zen+composer oculto).
- **S4** (desktop ≥900px): dock centrado en la columna de 760px con look de tarjeta
  (tabs con radio superior, hairline interna entre filas), no full-width.
- **S5** (C2): cualquier byte de `static/index.html` re-pinea su SHA-256 en
  `EXPECTED_DIGESTS` (`tests/browser/test_karaoke_harness.py`). app.js y herd-group.js
  NO se modifican.
- **S6** (C3): cero fetches nuevos en boot (cambio solo CSS); el CSS direct-child
  `#conversation .turn.has-rendered > span:not(.who)` queda intacto; IDs/roles/atributos
  i18n de tabs y composer sin cambios. Validación con `boundingClientRect` en Playwright
  móvil 360×740 y desktop 1280×800. PROHIBIDO editar ROADMAP.md o backend Python.

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
