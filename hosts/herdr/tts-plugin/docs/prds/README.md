# herdr-tts — PRDs 2026 (roadmap consolidado)

**Fecha:** 22 de septiembre de 2026; estado reconciliado el 2026-10-05.
**Alcance:** Plugin host `herdr-tts` (capa de orquestación Herdr). Motor y brain viven en el mismo monorepo; las PRDs del motor usan prefijo AT.
**Metodología:** flow-first, núcleo-primero — cada feature debe mejorar el flujo diario real del operador de flota, y el núcleo de voz se consolida antes que el perímetro; la diferenciación de mercado es un bonus, nunca el motivo. Toda feature nace opt-in y ninguna rompe el comportamiento existente.

El orden vigente es [ROADMAP.md](../../../../../docs/voice-stack/ROADMAP.md). El [índice consolidado](../../../../../docs/prds/README.md) cubre las 20 features originales (AT-01–AT-08 + HT-01–HT-12) y extras: AT-09/10, HT-13–16, podcast RSS, contexto on-demand, approval-gate y VS0–VS4/VSX. Las notas de revisión del 22/09 se conservan como historia, no sustituyen D1–D5.

## PRDs activas

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| HT-02 | [HT-02-voces-por-agente.md](HT-02-voces-por-agente.md) | Voces por agente (identidad vocal) | P1 | **EJECUTADA** (2026-09-22; `d94edf7`) | S-M |
| HT-04 | [HT-04-control-movil-bidireccional.md](HT-04-control-movil-bidireccional.md) | Atajo ntfy sobre endpoints del brain | F5 | **VIVA, REENFOCADA** (sin implementar; Chrome Android pendiente en F2) | L |
| HT-05 | [HT-05-recordatorios-escalados.md](HT-05-recordatorios-escalados.md) | Recordatorios escalados sobre `pending_queue.py`, distintos de VS3 | Backlog | **BACKLOG** (sin implementar) | M |
| HT-11 | [HT-11-audicion-voces.md](HT-11-audicion-voces.md) | Audición de voces en la paleta | Baja (D4, 2026-10-05); F6 | Aprobada, sin ejecutar | S |
| HT-13 | [HT-13-consumo-api-publica-motor.md](HT-13-consumo-api-publica-motor.md) | Consumo de la API pública del motor (de-duplicación del host) | P1 | Implementada | M |
| HT-03 | [HT-03-radio-mode.md](HT-03-radio-mode.md) | Radio mode (triaje por voz) | P2 | Aprobada, sin ejecutar | M |
| HT-14 | [HT-14-tema-claro.md](HT-14-tema-claro.md) | Tema claro configurable (`TTS_THEME`, sin knob de Apariencia) | P2 | **EJECUTADA** (config-only, 2026-09-24; `d0de97d`, `e75d55e`) | S |
| HT-15 | [HT-15-markdown-html-pipeline.md](HT-15-markdown-html-pipeline.md) | Transformación Markdown/HTML para lectura sincronizada | Pendiente | Implementada | M |
| HT-16 | [HT-16-reader-popup.md](HT-16-reader-popup.md) | Popup de lectura en vivo (karaoke sobre la reproducción) | P2 | Implementada | S |
| HT-01 | [HT-01-push-to-talk-intercom.md](HT-01-push-to-talk-intercom.md) | Push-to-talk del plugin sobre AT-02 reenfocada, con on/off | F4 | **VIVA** (sin implementar) | M-L |
| HT-06 | [HT-06-auto-snooze-reunion.md](HT-06-auto-snooze-reunion.md) | Auto-snooze contextual (modo reunión) | P4 | Postergada | M |
| HT-07 | [HT-07-filtro-semantico.md](HT-07-filtro-semantico.md) | Filtro semántico de importancia | P4 | Postergada | M |
| HT-08 | [HT-08-briefing-matinal.md](HT-08-briefing-matinal.md) | Briefing matinal automático | P4 | Postergada | M |
| HT-10 | [HT-10-chain-replay.md](HT-10-chain-replay.md) | Chain replay contextual del host (no la cadena AT-08 del motor) | P5 | Aprobada (baja), sin ejecutar | S-M |

**Entregas adicionales:** podcast RSS implementado (`ab4ef32`); brain on-demand-context implementado (`d7de194`, `2cb3a50`); approval-gate implementada con tests (`2abb17e`), Chrome Android pendiente. VS0–VS4/VSX cerrados `automated_complete` el 2026-10-01 (validación física pendiente). AT-01/03/04/08/09/10 ejecutadas; AT-02 reenfocada; AT-05/06/07 sin ejecutar; AT-11 parcial (M1 y tarea 2.1 hechas).

## Descartadas

| ID | Fichero | Feature | Motivo |
|---|---|---|---|
| HT-09 | [descartadas/HT-09-espacializacion-estereo.md](descartadas/HT-09-espacializacion-estereo.md) | Espacialización estéreo por pane | Diferencial pequeño y exige extensión del motor (`--pan`); no refuerza el flujo del operador. |
| HT-12 | [descartadas/HT-12-watchers-texto.md](descartadas/HT-12-watchers-texto.md) | Watchers personalizados de texto | Amplía el perímetro más allá del núcleo de voz de agentes sin reforzar el flujo prioritario. |

## Orden de ataque

**Estado de bloques:** BLOQUE-1 completado; BLOQUE-2 parcial (HT-02 hecha, HT-11 no); BLOQUE-3 sin ejecutar; BLOQUE-4 parcial (AT-03 hecha, radio pendiente).

**Secuencia vigente:** F1 documentación → F3 red de tests → F4 AT-02/HT-01 → F5 HT-04 → F6 HT-11. F2 validación física puede ir en paralelo, pero debe cerrar antes de F5. HT-05 permanece en backlog.

**HT-03:** radio no implementada; AT-08 y HT-02 habilitan su futura integración, no la ejecutan.

**Motor:** paquete P1 y AT-01/03/10 cerrados; AT-05/06/07 siguen pendientes.

**Diferidas:** HT-06/07/08 postergadas; HT-10 aprobada de prioridad baja, sin ejecutar. El verbo de inyección de HT-01 (`send-keys` frente al `pane run` usado por el brain) se resuelve al planificar F4; no es razón para archivar HT-01 ni para duplicar STT/listeners.

**Regla transversal:** toda feature nace apagada por defecto (opt-in) salvo que sustituya explícitamente un comportamiento existente; ninguna deja el flujo actual roto si se desactiva. Las métricas de cada PRD se miden sobre el uso real del maintainer, no sobre escenarios sintéticos.

## Nota de revisión (2026-09-22)

Registro histórico; los estados actuales están en la tabla y el ROADMAP (D1–D5).

Veredictos del maintainer aplicados el 22 de septiembre de 2026 sobre el borrador original (`docs/features-prds.md`, dividido en este directorio):

- **Aprobadas P1:** HT-02, HT-04, HT-05, HT-11 (identidad vocal completa + loop móvil con recordatorios).
- **Aprobada P2:** HT-03, sin requisito de LLM para existir (resumen heurístico offline por defecto; LLM solo experimental, RF-HT-03-9).
- **Aprobada P5 (baja):** HT-10.
- **Postergadas P4:** HT-01 (doble candado: AT-02 postergada + `pane send-keys` inexistente), HT-06, HT-07, HT-08.
- **Descartadas:** HT-09 y HT-12.

La prioridad del borrador original queda sustituida por la columna "Prioridad final" de la tabla y de la cabecera de cada fichero.
