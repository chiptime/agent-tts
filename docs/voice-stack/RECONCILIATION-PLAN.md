# PLAN DE RECONCILIACIÓN — voice-stack (post-migración)

**Fecha:** 2 de octubre de 2026
**Origen:** auditoría de reconciliación entre el roadmap de 20 features (revisión 22/09/2026) y el estado real del monorepo tras la migración (AT-10).
**Alcance:** únicamente los huecos detectados. No ejecuta features nuevas salvo HT-11 (P1 huérfana del Bloque 2).

---

## Contexto en una línea

La migración a monorepo absorbió tres repos (agent-tts, herdr-tts, herdr-brain), implementó 5 de las 20 features del roadmap y ~10 nuevas fuera de él, pero dejó la documentación, los bloques y la cobertura de tests desalineados de la realidad.

## Inventario de huecos (con evidencia)

| # | Hueco | Evidencia |
|---|---|---|
| H1 | Cabeceras de PRDs archivadas desactualizadas (AT-01 dice "Postergada" estando ejecutada; ídem AT-04/AT-08 "Aprobada") | `docs/prds/archivadas/AT-0{1,4,8}-*.md` |
| H2 | Bloques sin marca de ejecución: BLOQUE-1 cerrado pero sin estado en ficha; BLOQUE-2 parcial (HT-02 hecha, HT-11 no); BLOQUE-3 sin ejecutar; BLOQUE-4 parcial (AT-03 sí, HT-03 no) | `docs/prds/archivadas/bloques/` |
| H3 | Roadmap HT desalineado: HT-01 y HT-04 se están entregando por la vía brain (faster-whisper + PWA `/approval`), no por las vías de sus PRDs (whisper.cpp + send-keys / ntfy Actions) | `hosts/herdr/brain/src/herdr_brain/{stt,tools,server}.py` |
| H4 | PRDs del brain con status stale: approval-gate dice "planning pending" pero `/approval/*` está implementado con tests | brain PRD vs `server.py` |
| H5 | Cobertura de tests perdida: los ~520 tests bash del viejo herdr-tts no migraron; `hosts/herdr/tts-plugin/tests` tiene solo 4 entradas | `hosts/herdr/tts-plugin/tests/` |
| H6 | HT-11 (P1, Bloque 2 hito 1) sin ningún código: picker fzf sin binds de audición | `bin/herdr-tts` (`voice.picker.*` solo apply/cancel) |
| H7 | Residuo de layout: `src/` y `dist/` en la raíz (egg-info 0.1.0, wheel vieja, ignorados por git) confunden el mapa | raíz del monorepo |
| H8 | `EXECUTION.md` en DRAFT esperando autorización §1; VS1-VS4 sin empezar; VS3 solapa con HT-05/HT-04 sin cross-referencia | `docs/voice-stack/EXECUTION.md` |

---

## Paquetes de reconciliación

### R1 — Reconciliación documental (COMPLETADA — 2026-10-05)

- [x] R1.1 Corregir cabeceras de las 6 PRDs archivadas (AT-01, AT-03, AT-04, AT-08, AT-09, AT-10): `Estado: EJECUTADA` + fecha de cierre + commit de referencia. El estado vivo sigue viviendo en los README índice; las cabeceras dejan de mentir.
- [x] R1.2 Marcar las fichas de bloques: BLOQUE-1 `COMPLETADO`, BLOQUE-2 `PARCIAL (1 de 2 hitos)`, BLOQUE-3 `SIN EJECUTAR`, BLOQUE-4 `PARCIAL (hito 0 hecho, radio pendiente)`.
- [x] R1.3 Actualizar `docs/prds/README.md` y el índice de voice-stack con el estado real de las 20 + las 10 features nuevas fuera de roadmap (AT-09, AT-10, HT-13..HT-16, podcast RSS, brain on-demand-context, brain approval-gate, VS0).
- [x] R1.4 Corregir el status de la PRD brain approval-gate (implementada con tests, no "planning pending").

**Esfuerzo:** 1–2 h · **Riesgo:** cero · **DoD:** ningún documento dice de una feature ejecutada que está pendiente, ni viceversa. (Cumplido).

### R2 — Decisión de arquitectura HT: brain frente a PRDs originales (COMPLETADA — 2026-10-06)

Las tres PRDs afectadas cuentan con nota de destino formalizada y directiva de cobertura dual:

| PRD | Vía original | Vía real hoy | Resolución (D-R2 confirmada 2026-10-06) |
|---|---|---|---|
| HT-01 push-to-talk | whisper.cpp + `send-keys` en el host | brain: faster-whisper + VAD/endpointing + dispatch a sesión | **COBERTURA DUAL** (cubierta en brain y planificada en plugin-tts) |
| HT-04 control móvil | ntfy Actions + listener HTTP | brain: PWA `/approval/*` + `/ask` | **COBERTURA DUAL** (cubierta en brain PWA y planificada en plugin-tts) |
| AT-02 STT whisper.cpp | capa STT en el motor | brain: faster-whisper ya cubre la capacidad | **EN ROADMAP MOTOR** (para dar servicio a HT-01 en plugin-tts) |

- [x] R2.1 Notas de destino añadidas en `HT-01`, `HT-04` y `AT-02` (directiva de cobertura dual en plugin-tts).
- [x] R2.2 Índices `docs/prds/README.md` y `hosts/herdr/tts-plugin/docs/prds/README.md` actualizados reflejando cobertura dual y mantenimiento en plugin-tts.
- [x] R2.3 Decisión D-R2 confirmada y registrada (2026-10-06) con directiva de cobertura dual del usuario.

### R3 — HT-11 audición de voces (el P1 huérfano; cierra el Bloque 2) (COMPLETADA — 2026-10-06)

- [x] Ejecutado el hito 1 del BLOQUE-2 (vista de voces en paleta, preview bilingüe con frase fija, auto-stop <0.3 s en focus/ctrl-s, caché LRU 20 MB con `voice_cache_purge_lru`, asignación global con `set_global_voice` y `--set-global-voice`).
- [x] Picker fzf actualizado con binds interactivos (`space`/`ctrl-p` preview, `focus` auto-stop, `ctrl-s` stop, `ctrl-g` set global, `enter` set chat/global).
- [x] Paleta `--voice-palette` con bind de tecla `v` para audición de voces global (`--voice-audition`).
- [x] Flags CLI `--preview-voice <voice>`, `--voice-audition`, `--set-global-voice <voice>`.
- [x] Cobertura de tests en `hosts/herdr/tts-plugin/tests/voice_cases.sh` con 3 casos automatizados:
  - `case__voice_preview_creates_cached_sample`
  - `case__voice_cache_lru_purges_over_limit`
  - `case__voice_audition_assigns_global`
- [x] BLOQUE-2 marcado como COMPLETADO y PRD HT-11 archivada e índices sincronizados.

**DoD:** flujo "escuchar → asignar → oírla hablada" end-to-end + batería ampliada. (Cumplido).

### R4 — Recuperación de cobertura de tests (COMPLETADA — 2026-10-06)

Ejecutada Opción B (portar-crítico + matrix G-BASH-MATRIX):
- [x] R4a Auditoría de escenarios críticos de la batería antigua.
- [x] R4b Port de los críticos a `hosts/herdr/tts-plugin/tests/`:
  - `voice_cases.sh`: resolución jerárquica (`pane > agent > global`), fail-open ante `voices.json` malformado, `voice_map_set` (asignar/limpiar), auto-asignación determinista.
  - `config_cases.sh`: reemplazo in-place, deduplicación en upsert, preservación de comentarios/claves ajenas, rechazo de comillas/inyección, creación de `.bak`.
  - `lifecycle_cases.sh`: protección ante PIDs no relacionados en takeover, parada limpia, supervisión respetando stop flag, estrangulamiento horario del prune de audio.
  - `keymap_cases.sh`: detección de shadowing sobre Herdr core, inyección y backup de bloque gestionado en `config.toml`, rollback ante fallo de validación de config.
- [x] R4c Integración en `all_bash_harnesses.sh` con protocolo unificado `CASE <name> OK`.

**Estado:** R4 completado con éxito con salida 0 en suite combinada.

### R5 — Limpieza estructural (COMPLETADA — 2026-10-06)

- [x] Verificada la ausencia de residuos de `src/` y `dist/` en la raíz (absorbidos en `engine/` y cubiertos en `.gitignore`).
- **Riesgo:** cero. **Esfuerzo:** 15 min. (Cumplido).

### R6 — Desbloqueo voice-stack (decisión del maintainer)

- Revisar y autorizar `EXECUTION.md` §1 para que VS1-VS4 tengan plan ejecutable.
- Añadir cross-referencia VS3 (anuncios pendientes) ↔ HT-05/HT-04: decidir si VS3 absorbe los recordatorios (HT-05) o quedan separados, para no construir dos sistemas de "avisos pendientes".
- **Decisión requerida:** autorizar §1 + resolver el solape VS3/HT-05.

---

## Orden recomendado y por qué

```
R1 (doc, 1-2h) ──► R2 (decisión) ──► R4-lite (red de tests) ──► R3 (HT-11) ──► R5/R6
                        │                                              ▲
                        └── R2 bloquea CÓMO se documenta lo futuro      │
                            (brain vs vías originales)                  └── R4 antes de R3:
                                                                           no tocar el bash
                                                                           sin red
```

1. **R1 primero** porque la documentación mentirosa es la que causa los errores del próximo agente o humano que lea el repo.
2. **R2 segundo** porque es decisión, no trabajo: define el marco en que se reescribirá todo lo demás.
3. **R4-lite antes de R3** porque HT-11 vive dentro de un bash de 7.189 líneas: sin red de tests primero, cada cambio es apuesta ciega.
4. **R3 después**: primera feature nueva del roadmap post-migración, y cierra tu P1 huérfana.
5. **R5/R6** rellenables en cualquier hueco.

## Tabla de decisiones requeridas del maintainer

| # | Decisión | Recomendación | Estado |
|---|---|---|---|
| D-R2 | ¿Supersede HT-01/HT-04 hacia brain y AT-02 marcada cubierta? | Sí | **Sí, confirmada 2026-10-06** |
| D-R4 | ¿Port de tests 1:1 o port-crítico + G-BASH-MATRIX? | Port-crítico | **Port-crítico (Ejecutado R4b, 2026-10-06)** |
| D-R6a | ¿Autorizar EXECUTION.md §1 (VS1-VS4)? | Revisar y autorizar | Pendiente |
| D-R6b | ¿VS3 absorbe HT-05 o quedan separados? | Decidir al planificar VS3 | Pendiente |
| D-R3 | ¿HT-11 sigue siendo P1 tras la migración? | Sí (era la compañera UX de HT-02, ya implementada) | **Sí, ejecutada y cerrada (2026-10-06)** |

## Fuera de alcance de este plan

Ejecutar VS1-VS4, HT-03 (radio), HT-05 (recordatorios), AT-05/06/07 y el resto del roadmap postergado — esos viven en sus PRDs y en los bloques; este plan solo deja el terreno veraz y seguro para ellos.
