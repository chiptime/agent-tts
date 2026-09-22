**ID**: PRD-AT-07 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P3 · **Estado**: Aprobada
**Dependencias**: Retención de audio activada

# PRD-AT-07 — Compilado de audio / digest (`--digest`)

**Prioridad**: Media · **Esfuerzo**: S-M

### Resumen ejecutivo

El almacén de audio guarda turnos sueltos por día. Esta PRD añade `agent-tts --digest [--since 1d|--date YYYY-MM-DD]`: fusiona los audios almacenados que cumplen el filtro en un único fichero con capítulos por pane/turno, reutilizando `merge_chunks_to_audio`, y lo publica opcionalmente como episodio del feed podcast. Es la base del briefing matinal de herdr (HT-08).

### Problema y flujo actual

Con retención activada, cada turno suelto vive en `~/.local/share/agent-tts/audio/YYYY-MM-DD/<epoch>-<pane>.mp3`. Para repasar lo que dijo la flota durante la mañana hay que reproducir ficheros uno a uno; no existe forma de consumir el día como un programa continuo, y el feed podcast actual solo publica locuciones explícitas, no el histórico almacenado. Nota del código: el nombre `.mp3` es convencional, no promesa (kokoro/piper emiten bytes WAV); `merge_chunks_to_audio` ya normaliza contenedores al fusionar, así que el digest sobre mezcla WAV/MP3 es viable hoy.

### Propuesta

Subcomando `--digest` con ventana de selección: `--since Nd` (últimos N días) o `--date YYYY-MM-DD` (default: hoy). Selección: ficheros del almacén dentro de la ventana, orden cronológico por epoch del nombre de fichero, deduplicados. Fusión: reutilización de `merge_chunks_to_audio` sobre la secuencia ordenada, insertando silencio corto entre turnos (configurable, default 400 ms). Capítulos: metadatos de capítulo por turno o por pane (etiqueta con pane/agente y hora derivada del epoch), en CHAP de ID3v2 para MP3. Salida: fichero único (`--output` o nombre generado en el propio almacén) reproducible con `--play-file` (que ya respeta playback target remoto). Integración podcast: `--digest --podcast` publica el resultado como episodio "Digest YYYY-MM-DD" en el feed existente.

### Historias de usuario (US-AT-07-1, ...)

- US-AT-07-1: Como maintainer, quiero escuchar el día de la flota como un solo programa en el móvil durante el desplazamiento.
- US-AT-07-2: Como usuario, quiero saltar por capítulos entre panes/agentes dentro del digest.
- US-AT-07-3: Como herdr (HT-08), quiero generar el briefing matinal como digest del día anterior, reproducible via podcast.

### Requisitos funcionales (RF-AT-07-...)

- RF-AT-07-1: `--digest --since Nd|--date YYYY-MM-DD` selecciona exactamente los ficheros del almacén cuya fecha y epoch caen en la ventana; sin retención activada y almacén vacío, mensaje claro y salida no cero.
- RF-AT-07-2: La fusión ordena por epoch ascendente, tolera mezcla de contenedores WAV/MP3 (decode-normalize via `merge_chunks_to_audio`) y no modifica los ficheros origen.
- RF-AT-07-3: Capítulos ID3v2 CHAP por turno (o por pane con `--chapters pane`, default `turn`): título con identificador de pane y hora HH:MM derivada del epoch.
- RF-AT-07-4: Silencio inter-turno configurable (`--gap-ms`, default 400).
- RF-AT-07-5: `--digest --podcast` registra el digest como episodio del feed con título "Digest <fecha>" y lo sirve `--podcast-serve`.
- RF-AT-07-6: El digest respeta la limpieza de retención: si la ventana incluye particiones ya podadas, simplemente no hay ficheros (nunca error).

### Requisitos no funcionales (RNF-AT-07-...)

- RNF-AT-07-1: Un día con 150 turnos (aprox. 90 min de audio) compila en menos de 30 s en CPU de referencia.
- RNF-AT-07-2: Memoria acotada: la fusión procesa por streaming de chunks, pico por debajo de 300 MB para ese mismo día tipo.
- RNF-AT-07-3: El digest reproduce en apps móviles de podcast estándar (validación manual documentada con al menos dos apps).
- RNF-AT-07-4: Cero escrituras fuera del almacén y del `--output` explícito.

### Encaje en la arquitectura actual

Extensión de `audio_store.py` (que ya posee el layout de particiones, `retention_days` y `merge_chunks_to_audio`) con una función de selección por ventana. El CLI gana el flag en `cli.py`. La publicación reutiliza `PodcastFeed`/`PodcastEpisode` de `podcast.py` sin cambios de schema: el digest es un episodio más. La reproducción posterior pasa por `--play-file`, que ya resuelve targets remotos con fallback local documentado.

### Prior art y diferenciación

Ninguna herramienta del barrido compila histórico de audio de agentes; el feed podcast privado ya es diferencial del motor y esta PRD lo extiende de "locución explícita" a "historial navegable". La diferenciación secundaria (capítulos por pane) no existe en ningún prior art relevado. El criterio sigue siendo flujo: el maintainer recupera contexto de la flota en situaciones donde no puede leer (desplazamiento, tareas manuales).

### Dependencias

Retención de audio activada (`AGENT_TTS_AUDIO_RETENTION_DAYS` mayor que 0) como condición de uso, no de código. Ninguna dependencia de otras PRDs.

### Riesgos y mitigaciones

- Riesgo: digests largos consumen memoria en la fusión. Mitigación: fusión incremental por chunks y límite warning configurable.
- Riesgo: capítulos CHAP ignorados por algunos reproductores. Mitigación: fallback documentado a `--chapters none` y títulos de pista informativos.
- Riesgo: mezcla de sample rates entre turnos de proveedores distintos. Mitigación: remuestreo al rate canónico del merge (ya aplicado por el camino de streaming actual).

### Métricas de éxito

- Digest de día tipo generado sin error y con recuento de capítulos igual al recuento de turnos seleccionados.
- Reproducción correcta en dos apps móviles de podcast distintas.
- Tiempo de compilación dentro del RNF-07-1 en el hardware de referencia.

### Fuera de alcance

Transcripción/subtítulos del digest, edición de contenido (recortar turnos), subida a la nube, digest multi-máquina (agregar almacenes de varios hosts).

### Open questions

¿Capítulo por turno o por pane como default (el briefing HT-08 podría preferir pane)? ¿Nombre de fichero del digest con hash corto para evitar colisiones al regenerar?
