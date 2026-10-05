# Voice Stack — Roadmap transversal de voz (monorepo agent-tts: engine · hosts/herdr/brain · hosts/herdr/tts-plugin)

> **Estado: `automated_complete` (VS0→VSX, 2026-10-01).** Implementación y tests presentes;
> queda la validación manual física, no reconstruir VS1–VS4. Fuente del cierre:
> [MANUAL-TESTS.md](MANUAL-TESTS.md), cabecera. Reconciliación documental: 2026-10-05,
> sin repetir gates ni acceder a los artefactos locales de ejecución.
> El orden vigente es [ROADMAP.md](ROADMAP.md); D1–D5 de ese roadmap definen el trabajo
> post-consolidación, distintos del ledger D1–D9 de este paquete.

Roadmap de cuatro hitos sobre la pila de voz personal: cancelación de habla por solicitud,
audio incremental en el teléfono, anuncios pendientes sin pérdida silenciosa y fallback
entre proveedores — con los canales PC y teléfono independientes y siempre activos por diseño.

## Mapa del paquete (orden de lectura)

| Doc | Contenido | Rol |
|---|---|---|
| `README.md` (este) | Roadmap, ledger D1–D9, hechos verificados del código | Entrada |
| `EXECUTION.md` | Contrato del bucle: presupuestos, stop, manifiestos/checkpoints, lock de ejecución | Cómo se ejecuta |
| `TECHNICAL-PLAN.md` | Decisiones técnicas LOCKED (protocolos, APIs, FSMs, constantes, tooling) | Diseño sin menús |
| `TASKS.md` | IDs estables VS0–VSX, DAG, allowlists, gates, matriz de trazabilidad 38 FR | Qué hacer, en qué orden |
| `prds/01..04*.md` | Requisitos de producto (FR) por hito; el diseño preciso delega en TECHNICAL-PLAN | Producto |
| `MANUAL-TESTS.md` | Cierre automatizado y verificaciones funcionales/físicas | Evidencia histórica y siguiente validación |
| `ROADMAP.md` | Fases F1–F7 y decisiones post-consolidación | Orden vigente |

## Estado del roadmap ampliado (2026-10-05)

Inventario de las 20 features originales (AT-01–AT-08 + HT-01–HT-12) y extras, con detalle en el [índice consolidado](../prds/README.md) y el [índice del plugin](../../hosts/herdr/tts-plugin/docs/prds/README.md):

| Features | Estado actual |
|---|---|
| AT-01/03/04/08; HT-02 | EJECUTADAS; AT-03 solo en su alcance OpenCode recortado |
| AT-02; HT-01; HT-04 | REENFOCADA; VIVA; VIVA, REENFOCADA, respectivamente: integración del plugin pendiente, no sustituidas por el brain |
| AT-05/06/07; HT-03/05/06/07/08/10/11 | Sin ejecutar; HT-05 en BACKLOG sobre `pending_queue.py`, HT-11 prioridad baja |
| HT-09/12 | Descartadas |
| AT-09/10; HT-13–HT-16; podcast RSS | Implementadas; HT-14 config-only, sin knob de Apariencia |
| AT-11 | PARCIAL: M1 y tarea 2.1 hechas (`c577041`); resto M2–M4 pendiente |
| Brain on-demand-context; approval-gate | Implementadas; bucle autónomo no habilitado; approval Chrome Android pendiente |
| VS0; VS1–VS4; VSX | Fundación y cuatro hitos implementados; cierre `automated_complete`, validación física pendiente |

Evidencia de voice-stack: `8d9a1a1` (fundación), `182ed76` (cancelación motor),
`def986a` (host), `962e542` (brain), `36a8997` (fallback), `507e761` (E2E).
La fundación se registró el 30/09; los commits de producción y E2E son del 03/10,
posteriores al cierre de ejecución registrado el 01/10. No se confunden las fechas.
VS3 entrega anuncios pendientes, no los recordatorios escalados HT-05.

El monorepo canónico (D8): `/home/bruno/Code/personal/agent-tts` — `engine/src/agent_tts`
(motor), `hosts/herdr/brain` (cerebro + PWA), `hosts/herdr/tts-plugin` (host CLI/daemon PC).
La sesión de implementación trabaja SIEMPRE en el monorepo con rutas relativas al repo.

## Mapa de hitos

| # | Hito | PRD | Depende de | Estado | DoD resumido |
|---|------|-----|-----------|--------|--------------|
| 1 | Cancelación de habla por solicitud | prds/01 | VS0 completo + prerrequisito externo D8 confirmado | Implementado | `stop` cancela la voz de ESA solicitud en teléfono y PC sin tocar otros audios ni la ejecución del agente |
| 2 | Audio incremental en el teléfono | prds/02 | Hito 1 | Implementado | Primer segmento sonando ANTES de terminar la síntesis total; orden; sin duplicados |
| 3 | Anuncios pendientes atribuidos | prds/03 | Hito 1 | Implementado | Cero descartes silenciosos: pending→consolidado/anunciado/expirado-visible, con atribución y recuperación ante caída |
| 4 | Fallback entre proveedores | prds/04 | Hitos 1–3 | Implementado | Con fallback explícitamente configurado (OFF por defecto), fallo no-cancelación degrada al siguiente sin repetir voz ya oída |

Orden aprobado (D1): 1→2→3→4. Ejecución tarea a tarea: ver `TASKS.md` (DAG VS0→VS1→…→VSX).

## Ledger de decisiones aprobadas (fuente única de producto)

| ID | Decisión (resumen fiel) |
|----|------------------------|
| D1 | Cuatro hitos y orden 1→2→3→4. Fundaciones mínimas (instrumentación, harness) = subpasos VS0 del hito que las necesita, no scope creep. |
| D2 | Anuncios: conservar eventos pendientes de fin/bloqueo sin interrumpir reproducción activa; consolidar repeticiones de la misma sesión; a capacidad, registro visible recuperable (nunca descarte silencioso). Orden, épocas obsoletas y recuperación ante caída DEBEN especificarse. Sin exclusividad multi-dispositivo ni silenciar el PC. |
| D3 | Fallback OFF por defecto; solo proveedores/voces/privacidad-costo explícitamente configurados; la cancelación NUNCA dispara fallback. Configs físicas las suministra el operador; mocks de CI no autorizan gasto cloud. Reutilizar el retry stream→batch del MISMO proveedor. Evitar audio/coste duplicado tras parciales audibles. |
| D4 | Cobertura **Python y JS**: ≥90% de línea Y rama del código de producción nuevo/modificado, por componente INDEPENDIENTE (sin pool conjunto); total y no-regresión contra línea base inmutable pre-cambio; exclusiones estrechas justificadas. Si el mapeo diferencia→rama no es fiable: piso auditable ≥90% línea+rama por módulo Python/JS tocado + diff como evidencia. Conflictos de denominador = bloqueo visible, nunca pass falso. |
| D5 | Bucle autónomo por hito con evidencia y reanudación; techo TOTAL ≤2 rondas de remediación funcional por hito (no se reinicia por cambiar el fallo de nombre ni al reanudar sesión). Parar ante fallos persistentes, crash de runtime, verificación indisponible, credenciales ausentes, decisiones reales o verificación no medible. Review nativa y entrega del usuario. |
| D6 | E2E de navegador con STT/TTS/LLM/red/proveedores simulados deterministas y fixtures de audio decodificados reales reproducidos por el pipeline de medios del navegador; distinguir eventos decoded/playback de audibilidad física (manual final). Gates de rendimiento relativos a línea base medida. Aceptación = comportamiento observable. |
| D7 | No-objetivos: mover el mutex del host; silenciar el escritorio; Orca/opencode-web; OpenClaw; política de retención/borrado; catálogo de proveedores nuevo o sustituir PortAudio. |
| D8 | Fuente canónica del brain: `agent-tts/hosts/herdr/brain`. La reconciliación del WIP divergente del standalone es prerrequisito EXTERNO paralelo: la sesión solo lo valida read-only en Fase 0 o se detiene y reporta; su COMPLETION exige confirmación/evidencia del usuario/propietario externo — nunca se asume por ausencia de conflictos. Sin merge/cherry-pick/copy/redeploy propio. Conflicto que exija cambiar comportamiento = parada. |
| D9 | **Bash (policia distinta de D4, no un sustituto silencioso):** ≥90% de LÍNEAS EJECUTABLES MODIFICADAS con instrumentación validada (kcov, líneas — verificado en doc de la herramienta: kcov NO mide ramas en Bash) MÁS una **matriz automatizada de casos** que cubra TODAS las alternativas de decisión del código Bash modificado (if/elif/case/`&&`/`||`): cada alternativa enumerada true/false y casos por resultado (cancelación, busy, cola, error), con testcase exacto referenciado; alternativa sin caso ⇒ gate FAIL. La matriz es evidencia de alternativas cubiertas, **NO se llama ni computa como "cobertura de ramas" numérica** — prohibido presentarla como métrica porcentual de ramas. Python/JS siguen D4 íntegro. |

Los PRD trazan cada FR a D1–D9. Ninguna decisión de producto nueva se toma en implementación:
las que surgan vuelven al usuario (parada, no invención).

## Hechos verificados (snapshot histórico del 2026-09-30, anterior a VS0–VS4)

Esta sección conserva el inventario preimplementación y sus citas, no describe el estado actual. El código actual, los commits anteriores y MANUAL-TESTS prevalecen; las ausencias de cancelación, E2E e instrumentación que siguen eran hechos de aquel snapshot.

Separados de toda propuesta. Referencias `archivo:línea` verificadas sobre HEAD `384dd4f`
(árbol con SOLO ficheros untracked: este paquete, `hosts/herdr/brain/bin/herdr-brain` +
`herdr-plugin.toml` en WIP de packaging, `metrics/smoke/`; sin modificaciones tracked —
las citas reflejan bytes de HEAD). La sesión de implementación re-verifica el estado vigente
al lanzamiento: si una cita dejó de valer, manda el código real y se re-ancla la cita en el
manifiesto; nunca se edita código para que una cita vuelva a ser cierta.

### Estado de la reconciliación externa D8 (hechos, no conclusiones)

- Los standalone `~/Code/personal/herdr-brain` y `~/Code/personal/herdr-tts` **ya no existen
  en disco** (2ª revisión). NO es un error ni orden de recrearlos: el gate de Fase 0 trata
  "standalone ausente" como estado legítimo del trabajo externo.
- `hosts/herdr/brain/deploy/herdr-brain.service:17-18` YA apunta al canónico
  (`%h/Code/personal/agent-tts/hosts/herdr/brain`, venv `.venv/bin/python -m herdr_brain.server`).
- El WIP de packaging untracked (`bin/herdr-brain`, `herdr-plugin.toml`) y la rama
  `feat/port-antigravity-transcript-reader` son evidencia consistente de trabajo externo
  ACTIVO o reciente en el árbol. El gate de Fase 0 (EXECUTION.md §2.3) decide con el estado
  vigente; con escritores activos ⇒ STOP y reporte. La completion de D8 la confirma el
  usuario/propietario con evidencia — este paquete no la declara.

### herdr-brain — `hosts/herdr/brain`

| Hecho | Evidencia |
|-------|-----------|
| `/ask` es síncrono: LLM y render completan ANTES de responder; `speak_answer` renderiza dentro del handler | `src/herdr_brain/server.py:720-741` (`ask`), `server.py:591` (`speak_answer`), `server.py:605` (`shape_ask_response`) |
| TTS teléfono = `render_mp3` subprocess bloqueante `bin/herdr-tts --render-text <out.mp3> <texto>` (el texto SÍ va en argv hoy) espera fichero completo; timeout `tts_timeout_s=240` | `src/herdr_brain/tts.py:115-152`; `config.py:32` |
| Contrato de superficie verificado con `--contract-version` al arrancar (fail-soft) | `src/herdr_brain/tts.py:66-107` |
| Reader: el texto completo va por FICHERO, argv sólo rutas (contraste con render_mp3) | `src/herdr_brain/tts.py:165-204` |
| Seams inyectables: `create_app(settings, llm_factory, tts_renderer, reader_renderer, watcher, transcriber, daemon_probe, …)` | `src/herdr_brain/server.py:206-316` |
| SSE `/events` payload atribuido `{type, pane_id, agent, status, label, text, audio_url}` | `src/herdr_brain/server.py:396-433`; `watcher.py:194-221` (`_build_announcement`), `watcher.py:40` (`ANNOUNCEMENT_PREFIX="ann-"`) |
| `stopAudio` PWA puramente local: vacía cola, `pause()`, quita `src`; sin llamada al servidor | `static/app.js:1404-1417` |
| Cola de audio secuencial del navegador (`audioQueue`, `pumpAudio`); camino de error conserva texto pero descarta anuncios en cola | `static/app.js:1290-1331`, `app.js:1339-1361,1419-1434` |
| Canales siempre activos POR DISEÑO (fail-noisy); sonda daemon pidfile `/tmp/herdr-tts-daemon.pid` + `/proc/<pid>/cmdline` | `src/herdr_brain/server.py:273-276`; `tts_daemon.py:1-27,44,76-86` |
| Historial SQLite `call_history.db`; `/call-history` paginado; STT `/transcribe` faster-whisper (nunca descarga); approval gates con copy determinista | `history.py:34-36`; `server.py:491-505`; `server.py:455-481`; `server.py:82-89`; `static/approval.js:421-438` |
| `/audio/{name}` con `_SAFE_FILENAME` (`server.py:73`) y 404; audio dir `~/.local/state/herdr-brain/audio` | `server.py:834-841`; `config.py` (`DEFAULT_AUDIO_DIR`) |
| Watcher borra mp3 `ann-*` >1h; audio de chat intacto | `src/herdr_brain/watcher.py:260-274` |
| Tests: **20** ficheros pytest (`tests/`) + **7** `node --test` (`tests/js/`) — conteo 2ª revisión | listado 2026-09-30 (2ª) |

### agent-tts — motor `engine/`

| Hecho | Evidencia |
|-------|-----------|
| `use_pipelined_stream`: `no_play`/podcast rechazan streaming; `output_file` NO — el fusionado se escribe AL FINAL | `engine/src/agent_tts/cli.py:231-250` |
| Streaming pipelinado real por grupos (`split_sentence_groups`, `cli.py:313`); parcial NUNCA persiste (`persist_rendered`) | `engine/src/agent_tts/cli.py:292-330,462-497` |
| Cola del daemon: sin rechazo por ocupado; `PREEMPT` corta el activo (terminate→`wait_stopped`→STOPPED); `COALESCE` fusiona en ventana; ítems con `identifiers` | `engine/src/agent_tts/queue_manager.py:593-643,713-742` |
| IPC daemon: `ping, shutdown, play, enqueue, status, stop` (+delegación a sesión activa); `stop` global de sesión; NO existe cancel por ítem/solicitud; enqueue en `_handle_enqueue` `daemon.py:773` | `engine/src/agent_tts/daemon.py:585-636,773` |
| Propiedad de canal: flock POSIX / byte-range Windows; `audio.py` única dueña de LOCK/PID | `engine/src/agent_tts/ownership.py:1-13,43-59`; `audio.py:60-76` |
| Contrato proveedor `TTSProvider` (`synthesize`/`synthesize_stream`, `stop_checker`) | `engine/src/agent_tts/providers/base.py:24-63` |
| Stream falla → `None` → retry respuesta COMPLETA mismo proveedor; cancelación → `b""`; SIN fallback cross-provider | `providers/elevenlabs.py:141-168`; `providers/openai.py:131-158` |
| Daemon construye proveedor por solicitud (`_build_engine`) | `engine/src/agent_tts/daemon.py:649` |
| Tests motor: **48** ficheros pytest (`engine/tests/`); CI ubuntu+windows `pytest -q` + test de fronteras | listado 2026-09-30 (2ª); `.github/workflows/ci.yml` |
| Frontera NORMATIVA: `hosts/*` puede depender de engine vía contrato/superficie pública; engine NUNCA importa hosts (test lo exige); `contracts/tts-brain-v1.md` declara "No private Python imports" brain↔host y es la fuente normativa de la superficie CLI v1 (brain consume el host SOLO por CLI: `config.py:19-24`) | `engine/tests/test_monorepo_boundaries.py`; `contracts/tts-brain-v1.md` §1 |
| Resto del árbol: `src/agent_tts/` en la raíz contiene SOLO `__pycache__` obsoleto (0 ficheros .py) — no confundir con `engine/src/agent_tts` | listado 2ª revisión |

### herdr-tts — `hosts/herdr/tts-plugin`

| Hecho | Evidencia |
|-------|-----------|
| Watcher: settle window descarta "done" intermedios (`DEFAULT_SETTLE_SECONDS=5`) | `bin/herdr-tts:77-82,331,3096` |
| Busy local (`is_playing` = LOCK existe + PID vivo) ⇒ OMITE el anuncio localmente ("another chat is already speaking locally"); si no busy ⇒ `play_audio_file "$render_target"` | `bin/herdr-tts:3197-3200,3203-3205`; `is_playing` `bin/herdr-tts:1377-1383` |
| Rutas de canal: `/tmp/herdr-tts-playing.lock`, `/tmp/herdr-tts-current.pid`, `/tmp/herdr-tts-player.sock` | `lib/tts_engine.py:11-13` |
| Puente host→motor en Python: `lib/tts_engine.py` importa `agent_tts` (patrón permitido host→engine) | `lib/tts_engine.py:3-10,68` |
| Sin suite pytest propia; `scripts/smoke-tests.sh` (humo, NO cobertura); `--render-text` v1: `<out.mp3> <texto>` | listado; `bin/herdr-tts:255` |

### Instrumentación (verificado, no afirmación global)

En `engine/pyproject.toml` (dev: `pytest>=7.0.0`), brain `pyproject.toml` (dev: `pytest>=8.0`),
ambos `uv.lock` y `.github/workflows/ci.yml` **no hay herramienta ni gate de cobertura
declarados** (ni `coverage`, ni `pytest-cov`, ni kcov — tampoco instalados en el sistema).
Node local: v26.8.2 (expone `--test-coverage-branches`); CI fija node 20. Toda
instrumentación es trabajo FUTURO de VS0 (TASKS.md) con versiones registradas en evidencia;
este paquete NO afirma que ningún gate haya pasado ni que exista harness E2E hoy.

## Propuestas vs lock (registro previo a la implementación)

Las etiquetas **[PROPUESTA]** de los documentos de diseño distinguen API previa y nueva
en el snapshot original. Ya no significan «sin implementar»: VS0–VS4 están construidos.
`TECHNICAL-PLAN.md` conserva las decisiones LOCKED originales como contrato histórico.

## Reglas duras del paquete

1. Los PRD nacen DRAFT y nunca autorizan código; la autorización es la solicitud de `EXECUTION.md` §1.
2. La implementación, si se autoriza, arranca leyendo TODO el paquete y ejecuta `TASKS.md` bajo `EXECUTION.md`.
3. Ninguna verificación se declara pasada sin evidencia en manifiesto (EXECUTION.md §4).
4. Commit/push/PR/merge: solo con autorización expresa y separada. **El paquete NO requiere
   commit para tener identidad**: la identidad es el inventario ordenado + hashes de
   contenido (EXECUTION.md §4.1); nunca se hace commit prerrequisito.
5. Este paquete es un contrato ejecutado por prompt, no un servicio del bucle en background.
   Los scripts VS0 y tests VS1–VS4 referenciados en TASKS existen; su presencia no acredita
   una nueva pasada de gates ni la instalación del tooling en otro entorno.

## Siguiente paso

Completar F2 del ROADMAP: validación física de EXECUTION §8 / MANUAL-TESTS y approval-gate
en Chrome Android en la misma sesión de teléfono. La solicitud original de EXECUTION §1
se conserva como historia, no como trabajo pendiente de autorizar ni orden de reejecución.
