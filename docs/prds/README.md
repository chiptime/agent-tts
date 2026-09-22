# agent-tts — PRDs 2026 (roadmap consolidado)

Fecha: 22 de septiembre de 2026.

Metodología: cada PRD se justifica primero por cómo mejora el flujo diario del maintainer (dirigir flotas de agentes de código por voz en Herdr bajo WSL2, audio al host Windows via `winhost`) y solo después por su valor competitivo, anclada en el código real del motor y contrastada con el prior art del ecosistema. Las referencias `HT-0X` apuntan a las PRDs de herdr-tts, el orquestador que consume este motor.

## Índice de PRDs

| ID | Fichero | Feature | Prioridad final | Estado | Esfuerzo |
|---|---|---|---|---|---|
| AT-09 | [AT-09-canal-de-control.md](AT-09-canal-de-control.md) | Propiedad del canal de control (socket, lock y framing) | P1 | Aprobada | S-M |
| AT-04 | [AT-04-daemon-persistente.md](AT-04-daemon-persistente.md) | Modo daemon persistente del motor (`--serve`) | P1 | Aprobada | L |
| AT-08 | [AT-08-cola-prioridades.md](AT-08-cola-prioridades.md) | Cola con prioridades y reproducción encadenada | P1 | Aprobada | M-L |
| AT-03 | [AT-03-conectores-autodeteccion.md](AT-03-conectores-autodeteccion.md) | Conectores restantes y auto-detección del agente | P2 | Recortada | M |
| AT-07 | [AT-07-digest-audio.md](AT-07-digest-audio.md) | Compilado de audio / digest (`--digest`) | P3 | Aprobada | S-M |
| AT-06 | [AT-06-ducking-audio.md](AT-06-ducking-audio.md) | Ducking y prioridad de audio | P3 | Aprobada | M |
| AT-01 | [AT-01-streaming-frames-mp3.md](AT-01-streaming-frames-mp3.md) | Streaming incremental por frames de MP3 | P3 | Postergada | L |
| AT-02 | [AT-02-stt-whispercpp.md](AT-02-stt-whispercpp.md) | Capa STT local (`--transcribe`) con whisper.cpp | P4 | Postergada | M |
| AT-05 | [AT-05-prosodia-estado.md](AT-05-prosodia-estado.md) | Prosodia consciente de estado | P4 | Postergada | M |

## Orden de ataque

1. **Paquete P1: AT-09 + AT-04 + AT-08**, en tres sub-bloques secuenciales (véase `bloques/BLOQUE-1-paquete-motor.md`): 1.1 canal de control (AT-09), 1.2 daemon por vía única (AT-04), 1.3 cola y cadena (AT-08). El daemon nace poseyendo la cola desde el día uno: una sola migración de semántica de playback, no dos.
2. **AT-03 recortada** (solo verificación opencode web, ~10 min): validar con un id de sesión web real que el conector actual resuelve; sin código nuevo.
3. **P3**: AT-07 primero (mejor ratio valor/coste del lote) → AT-06 → AT-01 al final (mayor esfuerzo y riesgo de regresión en karaoke/boundaries).
4. **Postergadas**: AT-02 y AT-05.

## Coordinación con herdr-tts

- **AT-08 habilita HT-03/HT-10**: la cola con `enqueue` + `play-chain` sostiene el radio mode (HT-03) y las capacidades de orquestación de audio de herdr (HT-10).
- **AT-07 habilita HT-08**: el digest del día anterior es la base del briefing matinal de herdr.
- **AT-02 habilita HT-01** (postergado): la capa STT es la llave del intercom de herdr-tts cuando llegue el momento.

## Nota de revisión (22/09/2026)

Las prioridades finales y estados de esta tabla reflejan los veredictos del maintainer del 22/09/2026, aplicados con dos criterios: **flow-first** (cada feature se justifica por el flujo diario de dirigir flotas por voz, no por diferenciación de mercado) y **núcleo-primero** (primero el núcleo de salida potente y componentizable — daemon y cola —; la bidireccionalidad y el refinamiento prosódico llegan en fases posteriores). El detalle de cada veredicto vive en la cabecera y las notas de revisión del fichero de cada PRD. Criterio transversal, revisado para la vía única: cada PRD debe aterrizar con su garantía de compatibilidad con el comportamiento actual verificada por test. Donde la PRD conserva un camino de degradación, ese camino se verifica por test; donde la PRD elimina el camino alternativo por diseño (AT-04 vía única: no existe modo sin daemon), la garantía equivalente es la paridad observable — `agent-tts "texto"` funciona idéntico haya o no daemon corriendo (US-AT-04-2) — junto al cero cambio observable de la capa de transporte (RNF-AT-09-1).
