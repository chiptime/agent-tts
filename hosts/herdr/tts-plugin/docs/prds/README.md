# herdr-tts — PRDs 2026 (roadmap consolidado)

**Fecha:** 22 de septiembre de 2026; estado reconciliado el 2026-10-05.
**Alcance:** Plugin host `herdr-tts` (capa de orquestación Herdr). Motor y brain viven en el mismo monorepo; las PRDs del motor usan prefijo AT.
**Metodología:** flow-first, núcleo-primero — cada feature debe mejorar el flujo diario real del operador de flota, y el núcleo de voz se consolida antes que el perímetro; la diferenciación de mercado es un bonus, nunca el motivo. Toda feature nace opt-in y ninguna rompe el comportamiento existente.

El orden vigente es [ROADMAP.md](../../../../../docs/voice-stack/ROADMAP.md). El [índice consolidado](../../../../../docs/prds/README.md) cubre las 20 features originales (AT-01–AT-08 + HT-01–HT-12) y extras: AT-09/10, HT-13–16, podcast RSS, contexto on-demand, approval-gate y VS0–VS4/VSX. Las notas de revisión del 22/09 se conservan como historia, no sustituyen D1–D5.

## PRDs activas

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| HT-04 | [HT-04-control-movil-bidireccional.md](HT-04-control-movil-bidireccional.md) | Control bidireccional desde el móvil | P1 | **Activa, reenfocada (2026-10-07):** ntfy Actions → endpoints del brain, sin listener propio; depende de F2 | L |
| HT-05 | [HT-05-recordatorios-escalados.md](HT-05-recordatorios-escalados.md) | Recordatorios escalados de atención | P1 | Aprobada | M |
| HT-01 | [HT-01-push-to-talk-intercom.md](HT-01-push-to-talk-intercom.md) | Push-to-talk intercom (hablar al agente) | P4 | **Activa, diseño decidido (2026-10-07, 2.ª revisión)**, pendiente de implementar; consume la capacidad del motor (AT-02), sin depender del brain | M-L |
| HT-06 | [HT-06-auto-snooze-reunion.md](HT-06-auto-snooze-reunion.md) | Auto-snooze contextual (modo reunión) | P4 | Postergada | M |
| HT-07 | [HT-07-filtro-semantico.md](HT-07-filtro-semantico.md) | Filtro semántico de importancia | P4 | Postergada | M |
| HT-08 | [HT-08-briefing-matinal.md](HT-08-briefing-matinal.md) | Briefing matinal automático | P4 | Postergada | M |
| HT-10 | [HT-10-chain-replay.md](HT-10-chain-replay.md) | Chain replay contextual del host (no la cadena AT-08 del motor) | P5 | Aprobada (baja), sin ejecutar | S-M |

**Entregas adicionales:** podcast RSS implementado (`ab4ef32`); brain on-demand-context implementado (`d7de194`, `2cb3a50`); approval-gate implementada con tests (`2abb17e`), Chrome Android pendiente. VS0–VS4/VSX cerrados `automated_complete` el 2026-10-01 (validación física pendiente). AT-01/03/04/08/09/10 ejecutadas; AT-02 reenfocada como capacidad STT + captura del motor (decisión 2026-10-07, 2.ª revisión: `faster-whisper` reutilizado por extracción del brain, extra opcional; antes whisper.cpp y antes cliente HTTP del brain); AT-05/06/07 sin ejecutar; AT-11 parcial (M1 y tarea 2.1 hechas, `c577041`; M2–M4 pendientes).

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

**Estado de bloques:** BLOQUE-1 completado; BLOQUE-2 completado (2026-10-06, HT-02 e HT-11 hechas); BLOQUE-3 sin ejecutar; BLOQUE-4 parcial (AT-03 hecha, radio core hecho — Hito 1 —; Hito 2 pendiente).

**Fase 1 — host P1:** HT-02 + HT-11 primero (identidad vocal completa); HT-05 (recordatorios) y HT-04 (control móvil: ntfy Actions que llaman a los endpoints del brain, sin listener propio; depende de F2).

**HT-03:** radio core implementada (2026-10-08, Bloque 4 Hito 1) sobre la cola prioritaria AT-08 con fallback secuencial y la identidad vocal de HT-02; queda el modo experimental RF-HT-03-9 (Hito 2c).

**Motor:** paquete P1 y AT-01/03/10 cerrados; AT-05/06/07 siguen pendientes.

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

> Existió una nota de "cobertura dual" (2026-10-06, Decisión D-R2) que mantenía listener HTTP propio y capa STT local en roadmap; quedó **superada** por las decisiones del 2026-10-07. La primera revisión del día (cliente STT del plugin sobre `POST /transcribe` del brain, commit `ed2fbfe`) quedó **superada a su vez** por la decisión vigente: el STT y la captura pertenecen al motor.

Alineación decidida:
- **HT-01 (Push-to-Talk intercom):** Activa (P4), diseño cerrado 2026-10-07 (2.ª revisión): command id `ptt`, modo `toggle` únicamente, STT y captura de micrófono como capacidad del motor (AT-02) consumida por la interfaz pública, overlay de confirmación e inyección con `herdr pane send-text` + Enter opcional. Interruptor propio; el dictado **no requiere el brain** (puede estar apagado); motor sin dependencia/modelo = aviso visible sin inyección. Pendiente de implementar (ver plan F4.1-F4.10 en su PRD).
- **HT-04 (Control bidireccional móvil):** Activa (P1), reenfocada: ntfy Actions que llaman a los endpoints del brain (`/approval/*`, `/ask`); sin listener propio. Depende de F2 (validación de `/approval` en Chrome Android). **Decisión separada** de la ida del STT al motor: los endpoints del brain siguen siendo la vía del control móvil.

La prioridad del borrador original queda sustituida por la columna "Prioridad final" de la tabla y de la cabecera de cada fichero.
