# herdr-tts — PRDs 2026 (roadmap consolidado)

**Fecha:** 22 de septiembre de 2026
**Alcance:** Plugin host `herdr-tts` (capa de orquestación Herdr). Las capacidades del motor se referencian como PRDs del repo hermano `agent-tts` con prefijo AT (PRD-AT-02, PRD-AT-07, PRD-AT-08).
**Metodología:** flow-first, núcleo-primero — cada feature debe mejorar el flujo diario real del operador de flota, y el núcleo de voz se consolida antes que el perímetro; la diferenciación de mercado es un bonus, nunca el motivo. Toda feature nace opt-in y ninguna rompe el comportamiento existente.

## PRDs activas

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| HT-02 | [HT-02-voces-por-agente.md](HT-02-voces-por-agente.md) | Voces por agente (identidad vocal) | P1 | Aprobada | S-M |
| HT-04 | [HT-04-control-movil-bidireccional.md](HT-04-control-movil-bidireccional.md) | Control bidireccional desde el móvil | P1 | Aprobada | L |
| HT-05 | [HT-05-recordatorios-escalados.md](HT-05-recordatorios-escalados.md) | Recordatorios escalados de atención | P1 | Aprobada | M |
| HT-11 | [HT-11-audicion-voces.md](HT-11-audicion-voces.md) | Audición de voces en la paleta | P1 | Aprobada | S |
| HT-13 | [HT-13-consumo-api-publica-motor.md](HT-13-consumo-api-publica-motor.md) | Consumo de la API pública del motor (de-duplicación del host) | P1 | Implementada | M |
| HT-03 | [HT-03-radio-mode.md](HT-03-radio-mode.md) | Radio mode (triaje por voz) | P2 | Aprobada | M |
| HT-14 | [HT-14-tema-claro.md](HT-14-tema-claro.md) | Tema claro configurable (Ajustes → Apariencia) | P2 | Borrador | S |
| HT-15 | [HT-15-markdown-html-pipeline.md](HT-15-markdown-html-pipeline.md) | Transformación Markdown/HTML para lectura sincronizada | Pendiente | Implementada | M |
| HT-16 | [HT-16-reader-popup.md](HT-16-reader-popup.md) | Popup de lectura en vivo (karaoke sobre la reproducción) | P2 | Implementada | S |
| HT-01 | [HT-01-push-to-talk-intercom.md](HT-01-push-to-talk-intercom.md) | Push-to-Talk intercom (hablar al agente) | P4 | Postergada | M-L |
| HT-06 | [HT-06-auto-snooze-reunion.md](HT-06-auto-snooze-reunion.md) | Auto-snooze contextual (modo reunión) | P4 | Postergada | M |
| HT-07 | [HT-07-filtro-semantico.md](HT-07-filtro-semantico.md) | Filtro semántico de importancia | P4 | Postergada | M |
| HT-08 | [HT-08-briefing-matinal.md](HT-08-briefing-matinal.md) | Briefing matinal automático | P4 | Postergada | M |
| HT-10 | [HT-10-chain-replay.md](HT-10-chain-replay.md) | Chain replay contextual | P5 | Aprobada (baja) | S-M |

## Descartadas

| ID | Fichero | Feature | Motivo |
|---|---|---|---|
| HT-09 | [descartadas/HT-09-espacializacion-estereo.md](descartadas/HT-09-espacializacion-estereo.md) | Espacialización estéreo por pane | Diferencial pequeño y exige extensión del motor (`--pan`); no refuerza el flujo del operador. |
| HT-12 | [descartadas/HT-12-watchers-texto.md](descartadas/HT-12-watchers-texto.md) | Watchers personalizados de texto | Amplía el perímetro más allá del núcleo de voz de agentes sin reforzar el flujo prioritario. |

## Orden de ataque

**Fase 0 — motor hermano (repo `agent-tts`, en paralelo a todo lo demás):** AT-04 + AT-08 como paquete P1 del motor.

**Fase 1 — host P1:** HT-02 + HT-11 primero (identidad vocal completa); HT-04 → HT-05 después (loop móvil + recordatorios).

**Fase 2:** HT-03 (P2; funciona ya con el fallback secuencial y mejora sola al aterrizar AT-08).

**Fase 3 — motor (P3):** AT-07, AT-06, AT-01.

**Postergadas (P4/P5):** HT-01, HT-06, HT-07 y HT-08 quedan en P4; HT-10 (aprobada baja) en P5. Prerrequisitos externos: el verbo `send-keys` de Herdr core (`herdr pane send-keys` o equivalente) bloquea HT-01 completa y la parte de inyección de texto de HT-04 — a negociar con Herdr core; el subconjunto simple de HT-04 (botón Detener vía `--stop`) puede aterrizar antes. AT-02 (STT del motor) está postergada a P4 y arrastra consigo a HT-01.

**Regla transversal:** toda feature nace apagada por defecto (opt-in) salvo que sustituya explícitamente un comportamiento existente; ninguna deja el flujo actual roto si se desactiva. Las métricas de cada PRD se miden sobre el uso real del maintainer, no sobre escenarios sintéticos.

## Nota de revisión (2026-09-22)

Veredictos del maintainer aplicados el 22 de septiembre de 2026 sobre el borrador original (`docs/features-prds.md`, dividido en este directorio):

- **Aprobadas P1:** HT-02, HT-04, HT-05, HT-11 (identidad vocal completa + loop móvil con recordatorios).
- **Aprobada P2:** HT-03, sin requisito de LLM para existir (resumen heurístico offline por defecto; LLM solo experimental, RF-HT-03-9).
- **Aprobada P5 (baja):** HT-10.
- **Postergadas P4:** HT-01 (doble candado: AT-02 postergada + `pane send-keys` inexistente), HT-06, HT-07, HT-08.
- **Descartadas:** HT-09 y HT-12.

La prioridad del borrador original queda sustituida por la columna "Prioridad final" de la tabla y de la cabecera de cada fichero.
