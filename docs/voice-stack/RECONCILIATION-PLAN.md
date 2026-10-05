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

Las tres PRDs afectadas cuentan con nota de destino formalizada (no borrado):

| PRD | Vía original | Vía real hoy | Resolución (D-R2 confirmada 2026-10-06) |
|---|---|---|---|
| HT-01 push-to-talk | whisper.cpp + `send-keys` en el host | brain: faster-whisper + VAD/endpointing + dispatch a sesión | **SUPERSEDIDA** por brain conversation-mode |
| HT-04 control móvil | ntfy Actions + listener HTTP | brain: PWA `/approval/*` + `/ask` | **SUPERSEDIDA** por brain PWA (`/approval/*` + `/ask`) |
| AT-02 STT whisper.cpp | capa STT en el motor | brain: faster-whisper ya cubre la capacidad | **CUBIERTA POR BRAIN**; se conserva como referencia técnica |

- [x] R2.1 Notas de destino añadidas en `HT-01`, `HT-04` y `AT-02`.
- [x] R2.2 Índices `docs/prds/README.md` y `hosts/herdr/tts-plugin/docs/prds/README.md` actualizados reflejando el supersede/cubierta.
- [x] R2.3 Decisión D-R2 confirmada y registrada (2026-10-06).

### R3 — HT-11 audición de voces (el P1 huérfano; cierra el Bloque 2)

- Ejecutar el hito 1 del BLOQUE-2 tal cual está definido (vista de voces en paleta, preview bilingüe, auto-stop <0.3 s, caché LRU 20 MB, asignación global/chat), adaptando las rutas al monorepo (`hosts/herdr/tts-plugin/bin/herdr-tts`).
- El picker `voice.picker.*` ya existe: esto es añadir binds de preview/asignación, no UI nueva.
- **Prerrequisito:** R4-lite (abajo) — no tocar el bash de 7.189 líneas sin red de tests.

**DoD:** flujo "escuchar → asignar → oírla hablada" end-to-end + batería ampliada.

### R4 — Recuperación de cobertura de tests (decisión con tradeoff)

Los ~520 tests bash del plugin viejo existen en la historia (último commit pre-migración y remoto `chiptime/herdr-tts`). Dos opciones:

- **Opción A — portar 1:1:** máxima cobertura, máximo coste, y mucho test frágil de bash heredado.
- **Opción B — portar-crítico + matrix (recomendada):** auditar la batería antigua, portar solo los escenarios de seguridad del comportamiento (gating/snooze/mute/debounce, voces, keymap apply/rollback, daemon lifecycle) y dejar el resto al G-BASH-MATRIX de voice-stack.

**Secuencia:** R4a auditoría de la batería vieja (qué cubría, qué sigue siendo crítico) → R4b port de los críticos a `hosts/herdr/tts-plugin/tests/` en el formato que defina G-BASH-MATRIX.

**Decisión requerida:** A o B. **Esfuerzo:** B ≈ 1 día; A ≈ 3–5 días.

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
| D-R4 | ¿Port de tests 1:1 o port-crítico + G-BASH-MATRIX? | Port-crítico | Pendiente |
| D-R6a | ¿Autorizar EXECUTION.md §1 (VS1-VS4)? | Revisar y autorizar | Pendiente |
| D-R6b | ¿VS3 absorbe HT-05 o quedan separados? | Decidir al planificar VS3 | Pendiente |
| D-R3 | ¿HT-11 sigue siendo P1 tras la migración? | Sí (era la compañera UX de HT-02, ya implementada) | Pendiente |

## Fuera de alcance de este plan

Ejecutar VS1-VS4, HT-03 (radio), HT-05 (recordatorios), AT-05/06/07 y el resto del roadmap postergado — esos viven en sus PRDs y en los bloques; este plan solo deja el terreno veraz y seguro para ellos.
