**ID**: PRD-AT-01 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P3 · **Estado**: Postergada
**Dependencias**: Ninguna

# PRD-AT-01 — Streaming incremental por frames de MP3 (byte-level)

**Prioridad**: Media · **Esfuerzo**: L

### Resumen ejecutivo

El streaming actual corta por grupos de frase (~250 caracteres): la reproducción empieza cuando el primer grupo completo está sintetizado (~300–600 ms). Esta PRD lo reemplaza por streaming frame-accurate de MP3 para edge/openai/elevenlabs: decodificar frames a medida que llegan y alimentar el buffer PCM sin esperar el grupo. TTFA objetivo: menor de 300 ms. Es el item abierto "Incremental MP3 Frame-Accurate Byte Streaming" del roadmap.

### Problema y flujo actual

`_speak_pipelined` ya consume HTTP incrementalmente en openai/elevenlabs (chunks MP3 llegan temprano), pero cada chunk se acumula hasta completar el grupo y se decodifica entero con `miniaudio.decode()` antes de entrar al buffer PCM: el usuario espera al primer grupo completo aunque los primeros frames ya estuvieran en memoria. Con textos de 400+ caracteres (umbral `STREAM_AUTO_MIN_CHARS`), ese primer grupo fija el suelo de TTFA en 300–600 ms con edge y más con red lenta.

### Propuesta

Capa de decodificación incremental: parser de frames MP3 por sync word (0xFFE) que acumula bytes del stream HTTP, extrae frames completos en cuanto están disponibles y los decodifica a PCM parcial para alimentar la sesión de audio de forma continua, independiente del corte por grupos. La segmentación por grupos sigue existiendo para orquestación (prioridad de stop, reintentos), pero deja ser la unidad de espera. Karaoke: el mapeo índice-grupo actual (`group_for_chunk`) se rompe con audio que llega antes que el grupo cierra; se sustituye por estimación temporal de palabras: los frames decodificados dan el reloj PCM real y `estimate_boundaries_from_text` se refina cuando el grupo cierra. Fallback: cualquier fallo de parseo (frame corrupto, header Xing/ID3 inesperado) degrada al camino actual de grupo completo con una línea en stderr, sin abortar la reproducción.

### Historias de usuario (US-AT-01-1, ...)

- US-AT-01-1: Como usuario de notificaciones largas por voz, quiero que la voz empiece en menos de 300 ms aunque el texto tenga 2000 caracteres.
- US-AT-01-2: Como usuario de karaoke (--highlight), quiero que el resaltado siga siendo preciso aunque el audio llegue por frames.
- US-AT-01-3: Como usuario con red irregular, quiero que un frame corrupto no corte la reproducción: se salta y sigue.

### Requisitos funcionales (RF-AT-01-...)

- RF-AT-01-1: Con edge y un texto de 800 caracteres, el primer PCM audible entra al dispositivo antes de 300 ms desde el inicio de la petición (medido en red doméstica estándar).
- RF-AT-01-2: El parser maneja tags ID3v2 iniciales, header Xing/Info y cambios de bitrate; los frames no válidos se descartan con contador en stderr, nunca abortan.
- RF-AT-01-3: El watchdog de stall existente (`STREAM_PRODUCER_STALL_SEC`) sigue operando sobre el flujo de frames.
- RF-AT-01-4: El karaoke mantiene el contrato: error de alineación palabra-audio menor de 150 ms p95, usando el reloj PCM real como fuente de verdad.
- RF-AT-01-5: `--stream frames|groups|auto`: `auto` elige frames para edge/openai/elevenlabs y groups para piper (cuyo contrato `stream_yields_group_chunks` sigue intacto) y kokoro (whole-utterance, fuera de alcance).
- RF-AT-01-6: La persistencia post-stream (`merge_chunks_to_audio` al final limpio) no cambia: los frames crudos se concatenan igual que los chunks actuales.

### Requisitos no funcionales (RNF-AT-01-...)

- RNF-AT-01-1: Cero glitch audible en las transiciones frame-a-frame (buffer de underrun mínimo documentado, p. ej. 100 ms de colchón antes de arrancar el dispositivo).
- RNF-AT-01-2: CPU de decodificación incremental por debajo de 5% de un núcleo en el hardware de referencia.
- RNF-AT-01-3: El fallback a streaming por grupos se activa en menos de un segundo ante stream no parseable y es observable en stderr.
- RNF-AT-01-4: Ninguna regresión en la suite de streaming existente (test_stream, test_stream_drain, test_stream_persist).

### Encaje en la arquitectura actual

Vive dentro del path productor de `_speak_pipelined`: entre `pull_chunk` y el buffer de sesión se inserta un `Mp3FrameFeeder` que posee la cola de bytes y va entregando PCM. `miniaudio.decode` (whole-buffer) se usa por frame individual; el descubrimiento clave del código es que el decode por-buffer ya existe por chunk, así que la unidad de decode baja de chunk-grupo a frame sin cambiar de librería. `merge_group`/`shift_boundary_map` se conservan para el cierre de grupo; la estimación de límites pasa a guiarse por el contador de frames decodificados. OpenAI/ElevenLabs ya streamean HTTP por chunks: no necesitan cambios de proveedor; edge sí expone su stream de forma que los bytes tempranos lleguen al feeder (verificar su wrapper actual).

### Prior art y diferenciación

El prior art corta por frase o por worker-pool (agentvoice, claude-code-tts) o streamea HTTP sin decodificación incremental visible (ninguno documenta frame-accuracy). El diferencial es técnico y medible: TTFA menor de 300 ms con karaoke preservado. No es ventaja de marketing sino del flujo: en notificaciones de flota, cada 300 ms cuenta cuando el evento es urgente.

### Dependencias

Ninguna dura. Se beneficia de AT-05 (la prosodia por grupo define dónde puede cortar el feeder sin romper la entonación).

### Riesgos y mitigaciones

- Riesgo: complejidad del mapeo frames-a-palabras degrada el karaoke. Mitigación: el reloj PCM es fuente de verdad; si la estimación no converge, karaoke se desactiva para esa reproducción con aviso, audio intacto.
- Riesgo: MP3 con bitrate variable dificulta la duración total. Mitigación: `total` pasa a estimación que se corrige al cierre de cada grupo (ya ocurre así con streaming).
- Riesgo: underruns audibles con red irregular. Mitigación: colchón inicial configurable (default 100 ms) y degradación a groups si hay más de N underruns.

### Métricas de éxito

- TTFA p50 menor de 300 ms y p95 menor de 450 ms con edge en textos de 800+ caracteres.
- Error de alineamiento karaoke p95 menor de 150 ms.
- Tasa de fallback a groups menor de 1% en uso normal.

### Fuera de alcance

Piper (contrato de chunks por grupo vigente) y kokoro (PCM whole-utterance); streaming de texto aún no terminado (la fuente sigue siendo el turno completo); Opus/other codecs.

### Open questions

¿Exponer el colchón de underrun como flag o dejarlo interno? ¿El feeder debería también servir a `--play-file` remoto (winhost) para reducir el TTFA del lado del host?
