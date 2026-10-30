# herdr-tts — PRDs 2026 (roadmap consolidado)

**Fecha:** 22 de septiembre de 2026; estado HT-05 actualizado el 2026-10-11.
**Alcance:** Plugin host `herdr-tts` (capa de orquestación Herdr). Motor y brain viven en el mismo monorepo; las PRDs del motor usan prefijo AT.
**Metodología:** flow-first, núcleo-primero — cada feature debe mejorar el flujo diario real del operador de flota, y el núcleo de voz se consolida antes que el perímetro; la diferenciación de mercado es un bonus, nunca el motivo. Toda feature nace opt-in y ninguna rompe el comportamiento existente.

El orden vigente es [ROADMAP.md](../../../../../docs/voice-stack/ROADMAP.md). El [índice consolidado](../../../../../docs/prds/README.md) cubre las 20 features originales (AT-01–AT-08 + HT-01–HT-12) y extras: AT-09/10, HT-13–16, podcast RSS, contexto on-demand, approval-gate y VS0–VS4/VSX. Las notas de revisión del 22/09 se conservan como historia, no sustituyen D1–D5.

## PRDs activas

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| HT-04 | [HT-04-control-movil-bidireccional.md](HT-04-control-movil-bidireccional.md) | Control bidireccional desde el móvil | P1 | **Implementada (F5 fusionada en main)**: botones ntfy Approve/Stop/Open hacia /approval/action; validación física F2 pendiente | L |
| HT-05 | [HT-05-recordatorios-escalados.md](HT-05-recordatorios-escalados.md) | Recordatorios escalados de atención | P1 | **Implementada (Bloque 3 Hito 2)**: backoff, voz/push prioritario final, resets y cuenta atrás; entrega en `feat/ht-05-recordatorios-escalados`, integración en main y validación física F2 pendientes | M |
| HT-01 | [HT-01-push-to-talk-intercom.md](HT-01-push-to-talk-intercom.md) | Push-to-talk intercom (hablar al agente) | P4 | **Implementada (F4 fusionada en main)**: modo toggle con engine STT/PTT; validación en hardware F2 pendiente | M-L |
| HT-06 | [HT-06-auto-snooze-reunion.md](HT-06-auto-snooze-reunion.md) | Auto-snooze contextual (modo reunión) | P4 | Postergada | M |
| HT-07 | [HT-07-filtro-semantico.md](HT-07-filtro-semantico.md) | Filtro semántico de importancia | P4 | Postergada | M |
| HT-08 | [HT-08-briefing-matinal.md](HT-08-briefing-matinal.md) | Briefing matinal automático | P4 | Postergada | M |
| HT-10 | [HT-10-chain-replay.md](HT-10-chain-replay.md) | Chain replay contextual del host (no la cadena AT-08 del motor) | P5 | Aprobada (baja), sin ejecutar | S-M |

**Entregas adicionales:** podcast RSS implementado (`7cb01c7`); brain on-demand-context implementado (`f7c5e94`, `ab3ace8`); approval-gate implementada con tests (`a579cd8`), Chrome Android pendiente. VS0–VS4/VSX cerrados `automated_complete` (validación física pendiente). AT-01/03/04/08/09/10 ejecutadas; AT-02 e HT-01 implementadas (STT + captura en motor y plugin); HT-03 radio core implementado (`97bc94d`); HT-04 botones ntfy implementados (`ee09756`); AT-11 M1–M4 completados y fusionados en main (`c16e398`); AT-05/06/07 en backlog.

## PRDs archivadas / implementadas

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| HT-02 | [archivadas/HT-02-voces-por-agente.md](archivadas/HT-02-voces-por-agente.md) | Voces por agente (identidad vocal) | P1 | **Implementada** (Bloque 2 Hito 0) | S-M |
| HT-03 | [archivadas/HT-03-radio-mode.md](archivadas/HT-03-radio-mode.md) | Radio mode (triaje por voz) | P2 | **Implementada** (Bloque 4 Hito 1; RF-HT-03-9 LLM experimental pendiente) | M |
| HT-11 | [archivadas/HT-11-audicion-voces.md](archivadas/HT-11-audicion-voces.md) | Audición de voces en la paleta | P1 | **Implementada** (Bloque 2 Hito 1) | S |
| HT-13 | [archivadas/HT-13-consumo-api-publica-motor.md](archivadas/HT-13-consumo-api-publica-motor.md) | Consumo de la API pública del motor (de-duplicación del host) | P1 | **Implementada** | M |
| HT-14 | [archivadas/HT-14-tema-claro.md](archivadas/HT-14-tema-claro.md) | Tema claro configurable (Ajustes → Apariencia) | P2 | **Implementada** | S |
| HT-15 | [archivadas/HT-15-markdown-html-pipeline.md](archivadas/HT-15-markdown-html-pipeline.md) | Transformación Markdown/HTML para lectura sincronizada | P2 | **Implementada** | M |
| HT-16 | [archivadas/HT-16-reader-popup.md](archivadas/HT-16-reader-popup.md) | Popup de lectura en vivo (karaoke sobre la reproducción) | P2 | **Implementada** | S |

## Descartadas

| ID | Fichero | Feature | Motivo |
|---|---|---|---|
| HT-09 | [descartadas/HT-09-espacializacion-estereo.md](descartadas/HT-09-espacializacion-estereo.md) | Espacialización estéreo por pane | Diferencial pequeño y exige extensión del motor (`--pan`); no refuerza el flujo del operador. |
| HT-12 | [descartadas/HT-12-watchers-texto.md](descartadas/HT-12-watchers-texto.md) | Watchers personalizados de texto | Amplía el perímetro más allá del núcleo de voz de agentes sin reforzar el flujo prioritario. |

## Orden de ataque

**Estado de bloques:** BLOQUE-1 completado; BLOQUE-2 completado (2026-10-06, HT-02 e HT-11 hechas); BLOQUE-3 implementado en software (Hito 1 ntfy fusionado en main; Hito 2 HT-05 entregado en su rama, integración y validación física F2 pendientes); BLOQUE-4 parcial (AT-03 hecha, radio core hecho — Hito 1 —; Hito 2 mejoras progresivas / métricas pendiente).

**Fase 1 — host P1:** HT-02 + HT-11 completadas (identidad vocal completa); HT-04 completada (botones ntfy sobre el brain, validación física pendiente); HT-05 implementada sobre `pending_queue.py` (BLOQUE-3 Hito 2), apagada por defecto.

**Entrega HT-05:** estado de atención en SQLite, separado de los anuncios VS3; primer recordatorio a los 5 minutos y siguientes a los 7 y 12 minutos por defecto (gaps `2,5,15`, máximo 3). El debounce conserva la progresión; foco observado durante >2 s, respuesta PTT, working, mute y snooze reinician el estado. El dashboard muestra `⏰ mm:ss` junto al snooze mediante `reminder-status`, una lectura sin mutación. Las decisiones vigentes están en el [pack HT-05](../../../../../odd/tasks/ht-05-recordatorios-escalados.md); el cuerpo del PRD conserva la propuesta histórica del ledger JSON. La entrega de código no cierra la validación física F2 ni las métricas de uso real.

**HT-03:** radio core implementada (2026-10-08, Bloque 4 Hito 1) sobre la cola prioritaria AT-08 con fallback secuencial y la identidad vocal de HT-02; queda el modo experimental RF-HT-03-9 (Hito 2c) y métricas de uso real.

**Motor:** paquete P1, AT-01/03/10 cerrados; AT-02 STT Core implementada; AT-05/06/07 siguen en backlog.

**Postergadas (P4/P5):** HT-01 (diseño decidido 2026-10-07, 2.ª revisión: modo toggle, STT y captura como capacidad del motor vía AT-02, inyección `send-text`, independiente del brain; pendiente de implementar como F4), HT-06, HT-07 y HT-08 quedan en P4; HT-10 (aprobada baja) en P5.

**Regla transversal:** toda feature nace apagada por defecto (opt-in) salvo que sustituya explícitamente un comportamiento existente; ninguna deja el flujo actual roto si se desactiva. Las métricas de cada PRD se miden sobre el uso real del maintainer, no sobre escenarios sintéticos.

## Nota de revisión (2026-09-22)

Registro histórico; los estados actuales están en la tabla y el ROADMAP (D1–D5).

Veredictos del maintainer aplicados el 22 de septiembre de 2026 sobre el borrador original (`docs/features-prds.md`, dividido en este directorio):

- **Aprobadas P1:** HT-02, HT-04, HT-05, HT-11 (identidad vocal completa + loop móvil con recordatorios).
- **Aprobada P2:** HT-03, sin requisito de LLM para existir (resumen heurístico offline por defecto; LLM solo experimental, RF-HT-03-9).
- **Aprobada P5 (baja):** HT-10.
- **Postergadas P4:** HT-01 (doble candado: AT-02 postergada + `pane send-keys` inexistente), HT-06, HT-07, HT-08.
- **Descartadas:** HT-09 y HT-12.

## Nota de reconciliación (2026-10-07 — decisiones F4/F5 del maintainer; actualización 2.ª del mismo día)

> Existió una nota de "cobertura dual" (2026-10-06, Decisión D-R2) que mantenía listener HTTP propio y capa STT local en roadmap; quedó **superada** por las decisiones del 2026-10-07. La primera revisión del día (cliente STT del plugin sobre `POST /transcribe` del brain, commit `51d5bb7`) quedó **superada a su vez** por la decisión vigente: el STT y la captura pertenecen al motor.

Alineación decidida:
- **HT-01 (Push-to-Talk intercom):** Activa (P4), diseño cerrado 2026-10-07 (2.ª revisión): command id `ptt`, modo `toggle` únicamente, STT y captura de micrófono como capacidad del motor (AT-02) consumida por la interfaz pública, overlay de confirmación e inyección con `herdr pane send-text` + Enter opcional. Interruptor propio; el dictado **no requiere el brain** (puede estar apagado); motor sin dependencia/modelo = aviso visible sin inyección. Pendiente de implementar (ver plan F4.1-F4.10 en su PRD).
- **HT-04 (Control bidireccional móvil):** Activa (P1), reenfocada: ntfy Actions que llaman a los endpoints del brain (`/approval/*`, `/ask`); sin listener propio. Depende de F2 (validación de `/approval` en Chrome Android). **Decisión separada** de la ida del STT al motor: los endpoints del brain siguen siendo la vía del control móvil.

La prioridad del borrador original queda sustituida por la columna "Prioridad final" de la tabla y de la cabecera de cada fichero.
