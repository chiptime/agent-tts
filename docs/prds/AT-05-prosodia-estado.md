**ID**: PRD-AT-05 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P4 · **Estado**: Postergada
**Dependencias**: Ninguna

> **Nota de revisión (22/09/2026)**: Postergada en revisión: requiere dedicación continua (tablas, corpus, tests A/B); el núcleo no la necesita.

# PRD-AT-05 — Prosodia consciente de estado

**Prioridad**: Media-Alta · **Esfuerzo**: M

### Resumen ejecutivo

Todo se lee con la misma entonación: un stack trace y una lista de tests en verde suenan idénticos. Esta PRD añade una pasada de anotación prosódica en el cleaning stage que clasifica segmentos (error/stack, pregunta, warning, código vs prosa) y traduce la clasificación a rate, pitch y pausas por grupo de frase. La voz del agente pasa de locutor plano a informe de estado utilizable sin mirar la pantalla.

### Problema y flujo actual

El pipeline actual (`clean_agent_text` + `split_sentence_groups` + síntesis) trata el texto limpio como prosa uniforme: un único `rate`/`pitch` global por ejecución. En la práctica de flota, el usuario escucha de espaldas a la pantalla y no distingue "todo bien" de "todo roto". Los filtros de contenido del ecosistema (claude-code-tts omite bloques de código) atacan el problema de qué se lee, no el de cómo suena; nadie del prior art cambia entonación según estado.

### Propuesta

Pasada `annotate_prosody(text) -> List[Segment]` tras la limpieza y antes del split por grupos: cada segmento lleva etiqueta y parámetros sugeridos. Clasificadores heurísticos: error/stack trace (Traceback, ERROR, FAILED, exception), pregunta (interrogación, incluyendo apertura española), aviso (warning, blocked, deprecated), código vs prosa (cerca de fences ya detectados por el cleaner), y normal. Traducción por proveedor: edge recibe rate/pitch por grupo (la firma `synthesize` ya los acepta por llamada); proveedores SSML-capaces reciben marcas prosódicas equivalentes; piper/kokoro aplican rate por segmento (y en kokoro, ajuste del length-scale por segmento). Pausas: silencio explícito entre grupos clasificados como error o antes de un stack. Knob `--prosody on|off|subtle` (default `subtle` el primer ciclo).

### Historias de usuario (US-AT-05-1, ...)

- US-AT-05-1: Como usuario que escucha sin mirar, quiero distinguir auditivamente un error de un éxito sin esperar al contenido.
- US-AT-05-2: Como usuario, quiero que las preguntas del agente suenen a pregunta (entonación ascendente) y no a declaración.
- US-AT-05-3: Como maintainer, quiero prosodia sobria por defecto: informativa, nunca teatral.

### Requisitos funcionales (RF-AT-05-...)

- RF-AT-05-1: `annotate_prosody` etiqueta cada segmento con exactamente una clase de {error, question, warning, code, normal} y devuelve parámetros de rate/pitch/pausa derivados de tabla configurable.
- RF-AT-05-2: La tabla de mapeo clase-a-parámetros es configurable vía `~/.config/agent-tts/prosody.json` con valores por defecto documentados.
- RF-AT-05-3: En edge, cada grupo se sintetiza con su rate/pitch propio; el resto de proveedores recibe al menos rate por segmento; SSML donde el proveedor lo soporte.
- RF-AT-05-4: Inserción de pausa configurable antes de segmento error/stack (default 300 ms) y entre código y prosa (default 150 ms).
- RF-AT-05-5: `--prosody off` restaura el comportamiento actual byte-a-byte en la síntesis (regresión imposible).
- RF-AT-05-6: La clasificación no altera el texto hablado (solo cómo suena); redacción de secretos y lexicon intactos.

### Requisitos no funcionales (RNF-AT-05-...)

- RNF-AT-05-1: La pasada de anotación añade menos de 5 ms a pipeline de texto para 10 KB.
- RNF-AT-05-2: Ningún impacto en TTFA: la anotación del primer grupo completa antes de que la primera petición de síntesis salga.
- RNF-AT-05-3: Determinismo: mismo input produce misma clasificación (tests golden).
- RNF-AT-05-4: Con proveedores que solo aceptan rate global, la degradación es pausas + rate del segmento dominante, nunca un error.

### Encaje en la arquitectura actual

La pasada se inserta en `clean_agent_text` como paso opcional posterior a `redact_secrets` (el texto ya está saneado y sin credenciales) o como función hermana invocada desde el orquestador de streaming; los grupos salen de `split_sentence_groups` con metadatos. `_speak_pipelined`/`synthesize_group` ya sintetizan por grupo: el cambio es pasar rate/pitch por grupo en lugar de globales. La BoundaryMap y el karaoke no cambian (la pausa se expresa como silencio en el timeline).

### Prior art y diferenciación

Ninguna herramienta del barrido (agentvoice, claude-code-tts, opencode-smart-voice-notify, TalkToCursor) modifica prosodia según estado: filtran contenido o eligen momento. La diferenciación aquí es genuina y barata de mantener porque se apoya en parámetros que la firma de proveedores ya expone. El riesgo simétrico: al ser terreno sin prior art, no hay patrón validado; por eso `subtle` por defecto y knob de apagado.

### Dependencias

Ninguna dura. Beneficio ampliado por AT-04 (daemon) al evitar re-negociar voz por grupo.

### Riesgos y mitigaciones

- Riesgo: sobreactuación molesta. Mitigación: modo `subtle` por defecto, tabla user-editable, `--prosody off` garantizado.
- Riesgo: clasificador dispara en falso (la palabra "error" en prosa normal). Mitigación: clasificación por evidencia de formato (fence, indentación de trace) además de keywords; medir falsos positivos en corpus real.
- Riesgo: inconsistencia entre proveedores (rate de edge no es lineal). Mitigación: tabla por proveedor y tests de contrato por grupo.

### Métricas de éxito

- Test A/B ciego: identificación correcta de error vs éxito por audio solamente mayor o igual a 90% (hoy, azar).
- Falsos positivos de clasificación menores de 5% en corpus de 100 salidas reales de agentes.
- TTFA sin regresión (delta menor de 20 ms frente a baseline).

### Fuera de alcance

Voces emocionales (tags de ElevenLabs v3), énfasis por palabra, prosodia por rol de agente, música/stingers de estado.

### Open questions

¿Rango de pitch de edge suficiente para preguntas en español o hace falta SSML completo? ¿Exponer la clase de prosodia en el `status` IPC para dashboards?
