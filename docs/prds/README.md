# agent-tts — PRDs 2026 (roadmap consolidado)

Fecha: 22 de septiembre de 2026. Estado reconciliado: 5 de octubre de 2026.

Metodología: cada PRD se justifica primero por cómo mejora el flujo diario del maintainer (dirigir flotas de agentes de código por voz en Herdr bajo WSL2, audio al host Windows via `winhost`) y solo después por su valor competitivo, anclada en el código real del motor y contrastada con el prior art del ecosistema. Las referencias `HT-0X` apuntan a las PRDs de herdr-tts, el orquestador que consume este motor.

El orden vigente y las decisiones D1–D5 están en [ROADMAP.md](../voice-stack/ROADMAP.md). Las prioridades del 22/09 son históricas cuando ese roadmap las modifica; los cuerpos de PRD se conservan como diseño, no como evidencia de ejecución. El detalle del host vive en el [índice del plugin](../../hosts/herdr/tts-plugin/docs/prds/README.md).

## Índice de PRDs

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| AT-11 | [AT-11-instalable-first-run.md](AT-11-instalable-first-run.md) | Producto instalable independiente con first-run onboarding (auditoría: [AT-11-auditoria-instalacion.md](AT-11-auditoria-instalacion.md)) | P1 | **PARCIAL** (M1 y tarea 2.1 hechas; 14 tareas pendientes de M2–M4) | L |
| AT-09 | [archivadas/AT-09-canal-de-control.md](archivadas/AT-09-canal-de-control.md) | Propiedad del canal de control (socket, lock y framing) | P1 | **Ejecutada** (BLOQUE 1.1) | S-M |
| AT-04 | [archivadas/AT-04-daemon-persistente.md](archivadas/AT-04-daemon-persistente.md) | Modo daemon persistente del motor (`--serve`) | P1 | **Ejecutada** (BLOQUE 1.2) | L |
| AT-08 | [archivadas/AT-08-cola-prioridades.md](archivadas/AT-08-cola-prioridades.md) | Cola con prioridades y reproducción encadenada | P1 | **Ejecutada** (BLOQUE 1.3) | M-L |
| AT-10 | [archivadas/AT-10-monorepo-ecosistema.md](archivadas/AT-10-monorepo-ecosistema.md) | Monorepo del ecosistema: engine + contracts + hosts | P2 | **Ejecutada** | L |
| AT-03 | [archivadas/AT-03-conectores-autodeteccion.md](archivadas/AT-03-conectores-autodeteccion.md) | Conectores restantes y auto-detección del agente | P2 | **Ejecutada** (recortada) | M |
| AT-07 | [AT-07-digest-audio.md](AT-07-digest-audio.md) | Compilado de audio / digest (`--digest`) | P3 | Aprobada | S-M |
| AT-06 | [AT-06-ducking-audio.md](AT-06-ducking-audio.md) | Ducking y prioridad de audio | P3 | Aprobada | M |
| AT-01 | [archivadas/AT-01-streaming-frames-mp3.md](archivadas/AT-01-streaming-frames-mp3.md) | Streaming incremental por frames de MP3 | P3 | **Ejecutada** | L |
| AT-02 | [AT-02-stt-whispercpp.md](AT-02-stt-whispercpp.md) | Cliente del STT existente del brain (`POST /transcribe`), con interruptor on/off | P4 histórica; F4 | **REENFOCADA** (reescritura e implementación pendientes) | M |
| AT-05 | [AT-05-prosodia-estado.md](AT-05-prosodia-estado.md) | Prosodia consciente de estado | P4 | Postergada | M |

### Estado del host y entregas adicionales

El roadmap original contiene 20 features: AT-01–AT-08 (tabla anterior) y HT-01–HT-12 (resumen siguiente). AT-09/AT-10 y las entregas adicionales no se cuentan dentro de esas 20.

| Features del host | Estado real al 2026-10-05 |
|---|---|
| HT-02 | EJECUTADA (2026-09-22; `d94edf7`); identidad vocal, sin el preview HT-11 |
| HT-01 | VIVA; push-to-talk del plugin pendiente, sobre AT-02 reenfocada y con on/off |
| HT-04 | VIVA, REENFOCADA; atajo ntfy pendiente sobre el brain; PWA principal, Chrome Android pendiente |
| HT-05 | BACKLOG; recordatorios escalados sin implementar, separados de VS3 y sobre `pending_queue.py` |
| HT-03, HT-10, HT-11 | Sin ejecutar; radio, chain replay del host y audición de voces (HT-11: prioridad baja, D4) |
| HT-06, HT-07, HT-08 | Postergadas; sin ejecutar |
| HT-09, HT-12 | Descartadas |

| Entrega fuera del roadmap original | Estado y evidencia |
|---|---|
| HT-13–HT-16 | EJECUTADAS: API pública (`d94edf7`), tema config-only sin selector de Apariencia (`d0de97d`, `e75d55e`), HTML (`f9be95a`), reader popup (`dd44f49`) |
| Podcast RSS | Implementado en engine y plugin (`94f220d`, `ab4ef32`); no equivale al digest AT-07 |
| Brain: [on-demand-context](herdr-brain-on-demand-context.md) | PARCIAL: contexto e informes implementados (`d7de194`, `2cb3a50`); bucle autónomo futuro, no habilitado |
| Brain: [approval-gate](../../hosts/herdr/brain/docs/PRD-action-approval-gate.md) | Implementada con tests (`2abb17e`, `22e2fc2`); validación Chrome Android pendiente |
| [Voice-stack VS0–VS4/VSX](../voice-stack/README.md) | `automated_complete` (2026-10-01); validación física pendiente |

## Orden de ataque

0. **AT-11 (P1, parcial)** — M1 y tarea 2.1 ejecutadas; quedan 14 tareas de M2–M4. Evidencia y secuencia propias en `openspec/changes/at-11-instalable/tasks.md`; implementación de resolución en `c577041`. No se declara completo el onboarding ni validada la publicación estable.
1. **Paquete P1: AT-09 + AT-04 + AT-08** — **COMPLETADO (2026-09-25)**, en tres sub-bloques secuenciales (archivados en [`archivadas/bloques/`](archivadas/bloques/)): 1.1 canal de control (AT-09), 1.2 daemon por vía única (AT-04), 1.3 cola y cadena (AT-08). El daemon nace poseyendo la cola desde el día uno: una sola migración de semántica de playback, no dos. Contrato IPC congelado: [`../../contracts/ipc-v2.md`](../../contracts/ipc-v2.md). Deuda, pendientes y residuales aceptados: [`../deuda-tecnica.md`](../deuda-tecnica.md).
2. **AT-03 recortada** — **COMPLETADO (2026-09-28)**: validado con ID de sesión web real en la base de datos de producción OpenCode (0.62 ms, extracción limpia y autodetección CLI). PRD archivada.
3. **P3**: AT-07 primero (mejor ratio valor/coste del lote) → AT-06 → AT-01 **COMPLETADO (2026-09-29)**: streaming incremental por frames de MP3 con decoder continuo via miniaudio, fallback graceful a groups, karaoke via WordBoundary events. PRD archivada.
4. **Destino vigente**: AT-02 reenfocada para F4 junto a HT-01; AT-05 postergada. La secuencia de trabajo actual es la del ROADMAP, no el orden histórico de este apartado.

## Coordinación con herdr-tts

- **AT-08 habilita HT-03/HT-10**: la cola con `enqueue` + `play-chain` sostiene el radio mode (HT-03) y las capacidades de orquestación de audio de herdr (HT-10).
- **AT-07 habilita HT-08**: el digest del día anterior es la base del briefing matinal de herdr.
- **AT-02 reenfocada habilita HT-01**: el plugin consumirá el STT del brain; ambas funciones requieren interruptores on/off y aún no están implementadas en el plugin.

## Nota de revisión (22/09/2026)

Las prioridades finales y estados de esta tabla reflejan los veredictos del maintainer del 22/09/2026, aplicados con dos criterios: **flow-first** (cada feature se justifica por el flujo diario de dirigir flotas por voz, no por diferenciación de mercado) y **núcleo-primero** (primero el núcleo de salida potente y componentizable — daemon y cola —; la bidireccionalidad y el refinamiento prosódico llegan en fases posteriores). El detalle de cada veredicto vive en la cabecera y las notas de revisión del fichero de cada PRD. Criterio transversal, revisado para la vía única: cada PRD debe aterrizar con su garantía de compatibilidad con el comportamiento actual verificada por test. Donde la PRD conserva un camino de degradación, ese camino se verifica por test; donde la PRD elimina el camino alternativo por diseño (AT-04 vía única: no existe modo sin daemon), la garantía equivalente es la paridad observable — `agent-tts "texto"` funciona idéntico haya o no daemon corriendo (US-AT-04-2) — junto al cero cambio observable de la capa de transporte (RNF-AT-09-1).
