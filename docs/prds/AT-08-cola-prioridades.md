**ID**: PRD-AT-08 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: AT-04 recomendada

> **Nota de revisión (22/09/2026)**: Se aborda JUNTO a AT-04 como paquete P1: el daemon posee la cola desde el día uno, decisión que cierra la open question de AT-04 y evita una segunda migración de semántica de playback.

> **Nota de revisión (22/09/2026, vía única)**: La decisión de vía única con auto-arranque transparente en AT-04 elimina el modo "sin daemon": el CLI es siempre cliente. RNF-AT-08-4 se retira con puntero (no hay degradación sin daemon que diseñar ni testear) y las menciones a `--play-chain` y flags de prioridad "en proceso único sin daemon" se reformulan en consecuencia.

# PRD-AT-08 — Cola con prioridades y reproducción encadenada

**Prioridad**: Alta · **Esfuerzo**: M-L

### Resumen ejecutivo

Hoy la exclusión mutua es simple: lock de fichero por proceso y, en winhost, "un nuevo PLAY pisa al actual". En flota, un `done` trivial corta un `blocked` crítico a mitad de frase. Esta PRD introduce una cola con prioridades (blocked > done > working), política por evento (`preempt|queue|coalesce`), coalescing de avisos repetidos y reproducción encadenada gapless. Sostiene el "radio mode" de herdr (HT-03).

### Problema y flujo actual

El mutex vive en dos sitios: LOCK_FILE/PID_FILE como marcadores best-effort por proceso CLI, y el servidor winhost v1 que mantiene una sola reproducción activa donde un PLAY nuevo hace preemption de la anterior. No existe cola en ninguna parte. Con cinco agentes terminando a la vez, se disparan cinco play y solo sobrevive el último; con winhost, el que llega último pisa al que estaba sonando, sin importar cuál importa más. El usuario pierde exactamente los mensajes que necesitaba escuchar.

### Propuesta

Tres piezas. (1) Cola con prioridades: cada solicitud de playback lleva prioridad derivada del evento (blocked > done > working; ampliable); ante finalize de la reproducción activa, sale el siguiente ítem de mayor prioridad, y un ítem de prioridad estrictamente superior puede preemptar solo según política. (2) Política por evento: `preempt` (comportamiento actual), `queue` (espera su turno), `coalesce` (los `done` múltiples dentro de una ventana configurable, p. ej. 5 s, se funden en un solo anuncio: "tres agentes terminaron"). (3) Reproducción encadenada: `agent-tts --play-chain f1 f2 f3` e IPC `enqueue` reproducen secuenciales gapless reutilizando el buffer PCM y las BoundaryMap combinadas. La cola vive en el daemon (AT-04); el CLI es siempre cliente y delega en él (auto-arranque transparente si no está corriendo).

### Historias de usuario (US-AT-08-1, ...)

- US-AT-08-1: Como maintainer de flota, quiero que un `blocked` crítico nunca sea pisado por un `done` trivial.
- US-AT-08-2: Como maintainer de flota, quiero que diez `done` simultáneos suenen como un único anuncio coalescido, no como diez cortes.
- US-AT-08-3: Como usuario, quiero `agent-tts --play-chain a.mp3 b.mp3` con transición sin hueco audible.
- US-AT-08-4: Como orquestador (herdr), quiero `enqueue` via IPC con prioridad y poder consultar la cola en `status`.

### Requisitos funcionales (RF-AT-08-...)

- RF-AT-08-1: `--priority blocked|done|working` en CLI y campo `priority` en `enqueue` IPC; mapeo por defecto documentado y sobreescrible.
- RF-AT-08-2: Política por evento configurable (`--policy preempt|queue|coalesce` y equivalente IPC): `preempt` solo se permite desde prioridad estrictamente superior; iguales o menores hacen `queue`.
- RF-AT-08-3: Coalescing: múltiples ítems coalescibles encolados dentro de la ventana se funden en un anuncio único que resume la cantidad y el tipo de evento; la fusión es visible en `status` antes de sonar.
- RF-AT-08-4: `--play-chain FILE...` reproduce los ficheros en orden con silencio intermedio configurable (default 0 ms) y una sola sesión de audio (seek/frases operan sobre la cadena completa).
- RF-AT-08-5: `status` expone `queue_len`, cola pendiente con prioridades y `coalesced=N` cuando aplica.
- RF-AT-08-6: La reproducción activa nunca se solapa con otra (la exclusión mutua actual se conserva como invariante).

### Requisitos no funcionales (RNF-AT-08-...)

- RNF-AT-08-1: La cola despacha el siguiente ítem en menos de 50 ms tras finalizar el anterior (gapless percibido).
- RNF-AT-08-2: Un `blocked` encolado mientras suena un `done` alcanza el altavoz en menos de 1.5 s desde su evento incluso con cola ocupada.
- RNF-AT-08-3: La semántica se respeta por playback target: local, winhost (protocolo v2 con cola o encolado cliente), y wsl-ps.
- RNF-AT-08-4: ~~Sin daemon, la degradación es explícita: `--play-chain` y flags de prioridad funcionan en proceso único; la cola multi-evento requiere daemon y el error lo dice.~~ **RETIRADA (22/09/2026)**: ya no existe modo sin daemon (AT-04, vía única con auto-arranque transparente): `--play-chain` y los flags de prioridad se usan como cualquier otro comando, delegando siempre en el daemon; no hay degradación que diseñar.

### Encaje en la arquitectura actual

Un `QueueManager` por encima de `AudioSession` dentro del daemon (AT-04): serializa sesiones, aplica prioridad y coalescing. El dispatch IPC (`AudioSession.handle_ipc_command`) gana `enqueue` y campos de cola en `status`. Para winhost, el protocolo v1 (una sesión, PLAY pisa) evoluciona a v2: o la cola corre en el host (el server Windows encola PCM) o el cliente no envía hasta obtener turno; la decisión es open question, pero el contrato visible (un stream, sin solapes) no cambia. `--play-chain` reutiliza `merge_chunks_to_audio` y la composición de BoundaryMap ya existente en streaming.

### Prior art y diferenciación

agentvoice (marketplace de Developers Digest para Claude Code) resuelve el solapamiento con una cola + worker pool para notificaciones de un agente. claude-code-tts aporta worker-pool y filtrado de bloques de código. Aquí la diferenciación es doble: semántica de prioridad por tipo de evento en el motor (no en el cliente), y coalescing con resumen verbal; además es agnóstica del agente, no Claude-locked. El "radio mode" de herdr (HT-03) se construye sobre `enqueue` + `play-chain`.

### Dependencias

AT-04 (daemon) recomendada fuerte: la cola multi-evento necesita un proceso dueño persistente. `--play-chain` no depende del QueueManager completo: es encadenamiento dentro de una sesión de playback y puede entregarse antes que la cola multi-evento.

### Riesgos y mitigaciones

- Riesgo: inanición de `working` por lluvia de `blocked`. Mitigación: envejecimiento de prioridad (un ítem espera demasiado sube un nivel) documentado como RFC inicial.
- Riesgo: coalescing confunde ("tres agentes" pero eran cinco paneles). Mitigación: el anuncio coalescido lista hasta tres identificadores y resume el resto.
- Riesgo: doble semántica local/winhost divergen. Mitigación: tests de contrato que ejecutan la misma secuencia de eventos en ambos targets y comparan orden resultante.

### Métricas de éxito

- Cero solapamientos de audio en una simulación de 50 eventos concurrentes.
- 100% de eventos `blocked` reproducidos completos (no pisados) en la simulación.
- Ratio de coalescing mayor o igual a 3:1 en ráfagas de `done` (10 eventos, una sola locución).

### Fuera de alcance

Mezcla simultánea de voces (ducking entre voces es AT-06), persistencia de cola entre reinicios, prioridades configurables por agente individual (futuro).

### Open questions

¿Cola winhost en el server (v2) o en el cliente pre-send? ¿Ventana de coalescing fija o adaptativa por cadencia de la flota?
