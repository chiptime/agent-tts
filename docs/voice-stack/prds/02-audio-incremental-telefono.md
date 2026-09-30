# PRD 02 — Audio incremental en el teléfono

> **Estado: DRAFT.** Trazado a D1 (hito 2), D6, D7, D8, D9. Requiere Hito 1.
> Diseño técnico preciso: **LOCKED en `../TECHNICAL-PLAN.md` T4–T6** (contrato segmentado
> v2, transporte cursor long-poll, reproducción PWA); ejecución: `../TASKS.md` VS2.*.
> Cobertura/gates: README D4 (Python/JS) y D9 (Bash), tooling T12, evidencias `../EXECUTION.md` §4–§6.
> **Fuentes (D8):** brain `hosts/herdr/brain`; motor `engine/src/agent_tts`; host
> `hosts/herdr/tts-plugin`. Líneas re-verificadas en HEAD `384dd4f` (2ª revisión 2026-09-30);
> re-verificar vigente al lanzamiento. Contexto nuevo anclado: `/ask` es síncrono hoy
> (`server.py:720-741`, `speak_answer` `server.py:591`) — el id debe existir ANTES de la
> respuesta (T1) y el despacho asíncrono es parte de este hito (T5).

## 1. Contexto y problema

La respuesta hablada del teléfono espera a la síntesis COMPLETA antes de sonar:

- El brain renderiza con `bin/herdr-tts --render-text <out.mp3> <texto>` en un subprocess bloqueante
  y solo continúa cuando el MP3 completo existe (`herdr-brain/src/herdr_brain/tts.py:115-152`;
  uso del CLI en `herdr-tts/bin/herdr-tts:255`); sirve el fichero por `/audio/{name}`.
- La PWA descarga ese MP3 entero y lo encola (`static/app.js:1290-1331`, canónico); textos largos
  van a trozos ya renderizados (`speakText` encola piezas completas).

El motor YA tiene streaming pipelinado real (primer grupo suena mientras se sintetiza el resto —
`agent-tts/engine/src/agent_tts/cli.py:292-324`, decisión de modo en `cli.py:231-250`), pero esa
capacidad muere en la frontera brain↔PWA: la superficie actual del brain es "fichero completo".

Además, con `--render-text` el fichero de salida se materializa AL FINAL aunque la reproducción
local sea pipelinada (fusión única post-reproducción, `cli.py:239-250,462-497`) — útil saberlo para
no prometer progresividad de fichero donde no la hay.

## 2A. Objetivos y no-objetivos

**Objetivos**

1. El teléfono MUST empezar a reproducir el primer segmento de la respuesta ANTES de que termine la
   síntesis total (criterio de aceptación D6: observable, no un JSON).
2. Orden correcto de segmentos y ausencia de duplicados (D6) incluso con reintentos o reconexión.
3. La cancelación por solicitud (Hito 1) MUST aplicar a los segmentos en vuelo.

**No-objetivos (D7)**

- Cambiar la reproducción del PC ni el daemon de altavoces.
- Streaming de anuncios ambientales del watcher (su política es el Hito 3).
- Sustituir PortAudio/infraestructura de audio del motor; no se toca `ownership.py`.
- Retención/borrado de mp3 de chat (seguimiento aparte, D7).

## 2B. Arquitectura y límites de confianza

Reutilizar lo verificado:

- **Motor:** `synthesize_stream` por frames/grupos con `stop_checker` (`providers/base.py:47-63`);
  orquestación `_speak_pipelined` (`cli.py:292+`); parcial nunca persiste (`cli.py:462-497`).
- **Brain:** seam `tts_renderer` inyectable (`server.py:206-316`) — hoy firma fichero-completo.
- **PWA:** cola ordenada existente (`app.js:1290-1331`, canónico).

Arquitectura **[PROPUESTA — diseño LOCKED en T4/T5/T6; no hay menú abierto]**:

- **Superficie de síntesis segmentada:** extensión de contrato del host CLI
  `contracts/tts-brain-v2.md` (v1 intacto; `--contract-version` sigue imprimiendo `1`;
  negociación por `--contract-capabilities` fail-soft):
  `--render-text-segmented <out-dir> <input-text-file> --speech-request-id <ID>` produce
  grupos del motor (`split_sentence_groups`, `cli.py:313`) como ficheros MP3 atómicos +
  `manifest.json` re-publicado ATÓMICAMENTE tras CADA segmento (tmp+`os.replace`,
  `revision` monótona, `is_complete:false` hasta el terminal); texto POR FICHERO
  (disciplina del reader, `tts.py:165-204`).
- **Transporte:** cursor long-poll de DOBLE watermark `GET /speech/{id}/next?after=N&ack=M`
  (`after`=recibido contiguo, `ack`=reproducido completado contiguo; `-1<=ack<=after`) con `audio_url`
  estilo `/audio/…` vigente (UN transporte nuevo; descartados WebSocket y bytes-en-SSE,
  rationale en T5). Orden por `seq` monotónico; cancel de conexión ≠ cancel de job.
- **Despacho:** `/ask` con `speech_request_id` responde answer+speech SIN esperar el
  render completo; hilo productor llena buffer acotado (backpressure T5). `/ask` legacy
  mantiene el camino fichero-completo actual.
- La PWA reproduce con el pipeline de medios del navegador sobre la cola existente
  (`app.js:1290-1331`); el teleprompter por trozos se alimenta igual (`app.js:1300-1316`).
- **Hueco de `seq`/reconexión (LOCKED, T5/T6):** cursor de reanudación + dedup
  consume-una-vez (ack = `ended` del media); hueco o desborde de buffer ⇒ estado
  `degraded` VISIBLE — nunca salto silencioso ni auto-regeneración (regenerar es dominio
  del Hito 3); retry de fetch 2×/1 s y luego `degraded`; recarga de PWA = job no resumido
  (texto en call-history), sin replay oculto.

Límites de confianza: la PWA sigue sin confiar; el brain no expone claves de proveedor (la síntesis
la hace la cadena local herdr-tts/agent-tts, como hoy); los proveedores simulados en CI no
autorizan gasto cloud (D3).

## 2C. Esquema de datos y FSM

Segmento **[PROPUESTA — schema LOCKED en T5]**: `{speech_request_id, seq, audio_url, is_final,
mime}` (bytes vía `/audio/…` con `_SAFE_FILENAME` y chequeo de `session_id` en `next`;
sin esquema de firmas nuevo). FSM de reproducción por solicitud en la PWA **[LOCKED T6]**:

```
requesting ──primer segmento listo──▶ playing ──segmentos siguientes──▶ playing
    │ cancel (Hito 1)                     │ cancel
    ▼                                      ▼
cancelled                               cancelled (cola de segmentos purgada por solicitud)
playing ──is_final reproducido──▶ done (sin duplicados: `seq` único consumido una vez)
fetch-fail 2×/1s o gap o buffer ──▶ degraded (visible; sin salto ni replay)
```

Gap/reconnect: comportamiento determinista LOCKED en T5/T6 (cursor + dedup + `is_final`
+ `degraded` visible); nunca saltar silenciosamente ni duplicar.

## 2D. Seguridad y modelo de amenazas

- URLs de segmentos por el camino `/audio/{name}` vigente con `_SAFE_FILENAME` y 404
  (`server.py:834-841`, patrón `server.py:73`) + binding `session_id` en `next()` (T1/T5);
  sin esquema de firma nuevo.
- Sin texto de usuario en argv (patrón vigente: el texto completo va por fichero en el reader,
  `tts.py:165-204`; la superficie de streaming mantiene esa disciplina).
- Backpressure: el brain MUST acotar memoria por solicitud (búffer de segmentos con límite y
  política de bloqueo/drop visible, nunca crecimiento ilimitado).

## 2E. Métricas y criterios de aceptación

- **Aceptación (D6):** en E2E determinista, el evento de playback del primer segmento ocurre con la
  síntesis total AÚN en curso (marcador temporal del simulador); orden de `seq` exacto; cero `seq`
  reproducidos dos veces. Gate de latencia relativo a línea base medida con misma entrada/proveedor
  simulado — sin umbrales inventados.
- **No-regresión:** modo fichero-completo sigue operativo (fallback/compat) y cubierto.

## 3. Requisitos (RFC 2119) con trazabilidad y evidencia

| FR | Requisito | Traza | Evidencia / supuesto |
|----|-----------|-------|----------------------|
| FR-01 | El brain MUST entregar audio de respuesta al teléfono por segmentos antes de completar la síntesis total. | D1, D6 | Gap verificado: `tts.py:115-152` |
| FR-02 | La capacidad de streaming del motor MUST reutilizarse (frames/grupos + `stop_checker`), sin reimplementar un pipeline paralelo. | D1 | `cli.py:231-250,292+`; `providers/base.py:47-63` |
| FR-03 | Los segmentos MUST llevar `speech_request_id` y `seq`; la PWA MUST reproducirlos en orden y exactamente una vez. | D1, D6 | [PROPUESTA] sobre identificación del Hito 1 |
| FR-04 | La cancelación por solicitud MUST detener producción y reproducción de segmentos en vuelo. | D1 | Hito 1 prerrequisito |
| FR-05 | El diseño de transporte elegido MUST especificar el comportamiento ante hueco de `seq`/reconexión de forma determinista (cursor con ventana de dedup acotada, marcador `is_final`), sin saltar ni duplicar; la degradación a fichero completo es decisión de diseño explícita del hito, documentada con sus pruebas. | D6 | LOCKED en T5/T6: cursor long-poll, dedup consume-una-vez, `degraded` visible (sin auto-regeneración) |
| FR-06 | El búffer de segmentos del brain MUST tener límite de memoria con política explícita. | D5 | — |
| FR-07 | El modo fichero completo MUST permanecer como camino compatible y probado. | D1 | Compatibilidad con `app.js:1290-1331` vigente |
| FR-08 | La evidencia de aceptación MUST distinguir eventos decoded/playback del navegador de audibilidad física (esta última queda en checklist manual). | D6 | Restricción de verificación |

## 4. Escenarios de prueba y resultado observable esperado

- **Unit (brain):** el renderer-streaming emite segmentos ordenados con el fake del proveedor;
  límite de búffer se cumple bajo productor rápido.
- **Contract:** schema del transporte elegido (campos, `is_final`, errores tipados) fijado en tests
  de contrato al estilo de los existentes (`tests/test_tts.py` como referencia de estilo).
- **Integración:** brain + herdr-tts/agent-tts locales con proveedor simulado determinista:
  primer segmento entregado antes del `is_final`; cancelación a mitad no persiste parcial
  (regla `cli.py:462-497`).
- **E2E navegador (D6):** respuesta larga simulada con **fixtures de audio decodificados reales**
  reproducidos por el pipeline de medios del navegador (eventos de medios reales, no sólo callbacks
  simulados): playback del segmento 1 mientras el simulador sigue produciendo; orden exacto;
  interleave de un anuncio ajeno sin pérdida de orden por solicitud; reconexión de transporte a
  mitad → sin duplicados (cursor/ventana de dedup); fallo de red a mitad → comportamiento diseñado
  (reanudación o degradación honesta); concurrency: dos respuestas cruzadas con sus propias
  secuencias.
- **Rendimiento:** comparar contra línea base pre-cambio del modo fichero (misma entrada,
  proveedor simulado, misma máquina) — mejora relativa, no número absoluto inventado.
- **Manual físico:** latencia real de red móvil y auricular (`EXECUTION.md` §8).
