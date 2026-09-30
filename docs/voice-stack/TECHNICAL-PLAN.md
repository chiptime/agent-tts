# TECHNICAL PLAN — Decisiones técnicas LOCKED del paquete voice-stack

> **Estado: DISEÑO LOCKED (no requisitos de producto confirmados por el usuario).**
> Cada decisión aquí es diseño técnico dentro del alcance aprobado D1–D9, tomada contra el
> código fuente real citado. La sesión de implementación las sigue TAL CUAL: no hay menús,
> no hay "elegir la mejor herramienta". Si una decisión choca con el código vigente al
> lanzamiento, el outcome es **BLOCKER nombrado** (§T13) — nunca improvisar un cambio de
> producto. Los PRD delegan aquí su diseño preciso.

## T0. Invariantes de compatibilidad (transversales)

1. **Additivo:** todo comportamiento nuevo se activa por identificador/campo nuevo;
   ausencia = camino actual exacto (legacy byte-comparable en tests).
2. **Contrato normativo:** brain consume el host SOLO por la superficie CLI versionada
   (`contracts/tts-brain-v1.md` §1 "No private Python imports"; `config.py:19-24`). v1
   queda INTACTO; la extensión segmentada es `contracts/tts-brain-v2.md` nueva con
   negociación explícita (§T4). El host sí puede importar `agent_tts` (patrón existente
   `lib/tts_engine.py:68`). El motor NUNCA importa hosts (`test_monorepo_boundaries.py`).
3. **Stop ≠ cancelar agente:** ninguna cancelación toca LLM/herramientas/envíos aprobados
   (D1/D7). El hook de cancelación vive SOLO en la etapa render/reproducción.
4. **Sin coste cloud implícito:** providers reales solo si el operador los configuró
   explícitamente (D3); todos los tests usan fakes deterministas.
5. **Parcial nunca persiste** (regla vigente `cli.py:462-497`): toda vía nueva la hereda.
6. **Canales independientes:** nada silencia el PC ni impone exclusividad global (D2/D7).

---

## T1. Identidad de solicitud de voz (`speech_request_id`) — Hito 1 [PROPUESTA, LOCKED]

**Problema anclado:** `/ask` es síncrono (`server.py:720-741`, `speak_answer` `server.py:591`):
un id mintado por el brain y devuelto en la respuesta llega DEMASIADO TARDE para cancelar
mid-render. Por eso el id (y su capability token) los propone el cliente ANTES, y el job se
registra ANTES de invocar al LLM (fase `waiting_llm`).

| Regla | Valor LOCKED |
|---|---|
| Formato id | String opaco, charset `[A-Za-z0-9._-]`, longitud 8–64. Rechazo → HTTP 422 con detalle. |
| Turno conversacional | La PWA mints `crypto.randomUUID()` (id) MÁS un **capability token** criptográfico (`crypto.getRandomValues`, ≥256 bits) y envía AMBOS en el body de `/ask` como campos opcionales `speech_request_id` + `speech_cancel_token`. Sin campos ⇒ camino legacy exacto (sin job, sin cancelación). |
| Anuncios (brain) | Brain mints `ann-<uuidv4>` en `_build_announcement` (`watcher.py:194`); viaja como campo aditivo del payload SSE (`server.py:400`). **El id público de anuncio NO es autoridad para cancelar el job activo de otro**: cancelar exige el capability token del job (ver abajo). |
| Speech de approval replay | Identidad preasignada `apr-<gate_id>` + capability token entregado al PWA en campos aditivos de la respuesta del gate ANTES de lanzar el replay; `apr-<gate_id>` es identidad, NUNCA autoridad. |
| Capability token (autoridad de cancelación/lectura de scope) | El server guarda SOLO `sha256(token)` y compara con `hmac.compare_digest`; el token va en body/header de cancel/lectura de scope, JAMÁS en URL/query/logs/manifests/evidencia (redacción obligatoria). En memoria, por job, sin persistencia. `session_id` es ROUTING/separación accidental, NO auth — no se le llama autorización. Sin sistema de cuentas/auth nuevo (no-goal). |
| Registro | `app.state.speech_jobs: dict[id → SpeechJob]`. `SpeechJob = {id, token_hash, session_id, phase, cancel_event, created_ts, terminal_ts, watermarks (T5)}`. Fases: `waiting_llm → rendering → delivering → complete \| cancelled \| degraded \| failed \| expired-unconsumed` (los cinco últimos son terminales). |
| Cancel en `waiting_llm` | Registra el cancel ANTES del LLM: NO toca el LLM ni herramientas aprobadas (siguen); al volver el resultado textual se registra como siempre (historial intacto) y el speech simplemente NO se genera (`cancelled` sin audio). |
| Admisión acotada | `SPEECH_REGISTRY_MAX_JOBS=32`. Lleno en la vía identificable ⇒ **text-only degraded**: `/ask` responde `"speech": {"status": "degraded", "reason": "registry-full"}` SIN generar audio NUEVO y SIN caer al render legacy incancelable (prohibido el fallback silencioso a uncancellable). La vía legacy sin id queda intacta como hoy. |
| Conflicto de id | Id activo repetido en un nuevo `/ask` ⇒ HTTP 409 (no se reutiliza en vida del job). |
| Retención (sólo terminal) | El TTL `SPEECH_JOB_RETENTION_S=300` aplica EXCLUSIVAMENTE a jobs terminales (limpieza). Los jobs activos NO expiran por TTL: tienen presupuestos finitos separados — síntesis (`tts_timeout_s=240` vigente, `config.py:32`) y consumidor (unconsumed-timeout de T5) — y jamás se expulsa del registro un job legítimamente activo. Cancel tardía (post-expurgo) ⇒ `{"status":"unknown-or-expired"}` no-op idempotente. |
| Autorización de cancelación | `POST /speech/{id}/cancel` con body `{"session_id", "speech_cancel_token"}`; el job exige token válido (comparación constant-time); mismatch ⇒ 403. Contexto: herramienta personal LAN; el token por job evita cancelación cruzada SIN inventar auth global. |
| Carrera terminal/late | Cancel tras completar ⇒ `{"status":"already-complete"}`; durante render ⇒ abort; ambas idempotentes, sin efecto sobre otros jobs; resultados tardíos del runtime se suprimen vía estado/token del job (la acción del agente sigue viva). |

**FSM del job de speech** [PROPUESTA, LOCKED]:

```
registered(waiting_llm) ──llm ok──▶ rendering ──primer segmento──▶ delivering ──fin──▶ complete
        │                              │                              │
        ├── cancel (LLM sigue) ────────┼──────────────────────────────┴──▶ cancelled (sin audio; outcome textual intacto)
        │                              └─ cancel ──▶ cancelled (abort pgid; parcial NO persiste)
        └─ buffer/gap/límite/presupuesto ──▶ degraded (visible; sin replay automático)
delivering ──consumidor desaparecido (SPEECH_UNCONSUMED_TIMEOUT_S, T5)──▶ expired-unconsumed (terminal visible)
terminales {complete, cancelled, degraded, failed, expired-unconsumed} ──(TTL 300 s, limpieza)──▶ expunged (cancel posterior = unknown)
```

Regla dura: el TTL nunca dispara en estados no terminales; los presupuestos finitos de
síntesis/consumo son los únicos límites de vida activa. El conjunto terminal del enum de
fases y el del FSM es EL MISMO: `complete | cancelled | degraded | failed |
expired-unconsumed`.

---

## T2. Cancelación server-side del speech del brain — Hito 1 [PROPUESTA, LOCKED]

- Nueva `render_mp3_cancellable(settings, text, out_path, cancel_event, runner=None)` en
  `hosts/herdr/brain/src/herdr_brain/tts.py` (junto a `render_mp3:115-152`, que queda
  intacto para legacy): `subprocess.Popen` + drain de pipes + poll de `cancel_event` cada
  0.2 s dentro del presupuesto existente `tts_timeout_s=240` (`config.py:32`).
- **Cancel de procesos — alcance de plataforma LOCKED:** el brain se despliega en Linux
  (`deploy/herdr-brain.service`; deploy verificado). El Popen arranca con
  `start_new_session=True` (POSIX): el job es dueño de su grupo de procesos/pgid; cancel
  señala `SIGTERM` AL PGID PROPIO del job → gracia `SPEECH_CANCEL_TERM_S=5` → `SIGKILL` al
  mismo pgid → `wait()` con reaping de hijos del grupo (un terminate al padre Bash puede
  filtrar el hijo de síntesis: por eso grupo, no padre). Windows (portabilidad del MOTOR,
  no del brain): terminación scoped del handle/árbol propio del job si aplica la
  superficie vigente del engine. PROHIBIDO en toda plataforma: `pkill`/`taskkill` por
  nombre o cualquier señal a procesos fuera del pgid del job.
- Tras abort: borrar `out_path` parcial y tmp's inutilizables → `TTSError("cancelled")` →
  job `cancelled`. Nunca kill de procesos ajenos; nunca tocar el hilo LLM (en
  `waiting_llm` el cancel sólo desactiva el speech futuro, T1).
- **Redacción de argv/logs:** el camino segmentado nuevo pasa el texto POR FICHERO (T4) —
  el argv del subprocess sólo lleva rutas generadas por el server; los logs/evidencia del
  camino de cancelación JAMÁS incluyen texto de usuario ni tokens (T1).
- **PWA — fallo de red al cancelar (LOCKED):** el audio local se detiene INMEDIATAMENTE
  siempre; el `POST /speech/{id}/cancel` (con capability token) reintenta finito
  `SPEECH_CANCEL_NET_RETRY=2` (backoff 1 s); agotado ⇒ estado visible
  `"cancel-unconfirmed"` (diagnóstico en UI) — PROHIBIDO el catch silencioso "ya se
  consultará en el próximo poll" cuando el polling murió con el hangup, y PROHIBIDO
  reportar falsamente "server cancelado". Resultados tardíos del runtime se suprimen vía
  estado/token del job; la acción del agente/approval sigue viva (FR-07 PRD 01).
- `stopAudio` (`app.js:1404-1417`) gana: (1) si hay job activo con id+token conocidos →
  cancel POST (semántica arriba); (2) purge de la cola local POR id (los ítems de cola
  llevan `speech_request_id` desde T6). El hang-up ejecuta el mismo camino.
- **Anuncios en teléfono:** la cancelación de un job NO cancela registros ambientales
  (dominios separados, PRD 03 FR-08).

---

## T3. Cancelación dirigida en el daemon del motor — Hito 1 [PROPUESTA, LOCKED]

- **SUPERFICIES DISTINTAS, declarado:** los jobs de teléfono NO usan el daemon del motor
  (el render telefónico es subprocess CLI a fichero: `--render-text`, `bin/herdr-tts:255`).
  El daemon (cola `PREEMPT`/`COALESCE`, `queue_manager.py:593-643,713-742`) es la cola de
  reproducción del canal PC. El cancel dirigido aplica a ítems del daemon (anuncios PC).
- Nuevo comando IPC `cancel <json>` en `daemon.py` (dispatch `daemon.py:585-636`):
  payload `{"identifiers": ["…"]}`.
  1. Retirar ítems PENDING cuyo `identifiers` interseque el pedido.
  2. Ítem ACTIVO interseca ⇒ terminar por el bloque PREEMPT existente
     (`handle.terminate()` → `wait_stopped` → `STOPPED`, `queue_manager.py:617-636`).
  3. Sin match ⇒ `ok=true removed=0 active_stopped=0` (idempotente, silencio honesto).
  4. Reply tipado estilo vigente `ok=/err=` (`daemon.py:613-618,630-633`).
- `stop` global y las políticas de cola quedan intactas. Limpieza cooperativa vía
  `SessionHandle` existente (`queue_manager.py:180-202`) con terminate acotado — sin
  reaping exterior, sin matar sesiones ajenas.
- **Host:** el watcher pasa `identifiers` (id del registro/anuncio) en su camino de
  playback al motor y expone passthrough de cancel por la superficie CLI existente del
  host (delta Bash mínimo, ver T7/TASKS VS1.6).

---

## T4. Contrato segmentado v2 (host CLI ↔ brain) — Hito 2 [PROPUESTA, LOCKED]

- `contracts/tts-brain-v1.md` queda INTACTO y normativo para v1. Nueva
  `contracts/tts-brain-v2.md` (crea la tarea VS2.1) — extensión aditiva:
  - **`--contract-version` sigue imprimiendo EXACTAMENTE `1`** (v1 no se reescribe ni
    miente). Nueva flag aditiva `--contract-capabilities` que imprime JSON estricto
    `{"supported_protocols":[1,2]}` (+newline, exit 0). Negociación del brain: si
    `--contract-capabilities` responde JSON válido ⇒ usa la lista de protocolos;
    si no existe la flag ⇒ v1 legacy. Versiones desconocidas/mayores ⇒ fail-soft SIN
    habilitar protocolo no soportado (sólo se activan protocolos ≤ máximo soportado por
    el brain; v1-only ⇒ camino legacy + flag visible
    `speech.degraded: "segmented-unavailable"`; `/ask` textual nunca se bloquea).
  - `--render-text-segmented <out-dir> <input-text-file> --speech-request-id <ID>
    [--voice V] [--rate R]` — el TEXTO va por FICHERO (disciplina del reader
    `tts.py:165-204`) y el **`speech_request_id` es INPUT explícito del CLI** (no lo
    "sabe" mágicamente el manifest): viaja tal cual al manifest.
  - Producción por grupos del motor (`split_sentence_groups`, `cli.py:313`) vía
    implementación en `lib/segmented_render.py` (patrón puente `lib/tts_engine.py`);
    sin imports brain→engine (T0.2).
  - Ficheros `seg-<seq 04d>.mp3`: cada uno es un MP3 COMPLETO atómico (tmp+rename); no
    existe fusión parcial en ningún punto del protocolo.
  - **Manifest publicado POR SEGMENTO (no sólo al final — publicar sólo al final
    bloquearía el primer segmento hasta generar todo, violando M2):** tras CADA segmento
    completado se re-publica `manifest.json` ATÓMICAMENTE (tmp + `os.replace`) con
    `revision` monótona creciente e `is_complete:false`;
    `{speech_request_id, voice, rate, revision, is_complete, cancelled, error,
    segments:[{seq,file,bytes}]}` acumula la secuencia publicada. El manifest TERMINAL
    (`is_complete:true`, o `cancelled:true` / `error:"…"`) se publica al final SIN ocultar
    la metadata de secuencia ya publicada. Cancel/error del productor: borra tmp's
    inutilizables, publica estado terminal, deja intactos los segmentos ya publicados
    (sólo contabilidad).
  - Códigos de salida: `0` completo; `3` cancelado por productor; otro ≠0 fallo (en
    ambos casos el manifest terminal ya está publicado; sin manifest = fallo temprano).
  - Cancelación de productor: stop_checker del proveedor + abort entre grupos; nada se
    persiste como fusión (regla T0.5).
- El brain consume leyendo el manifest por `revision` creciente y sirve segmentos vía T5.
- `--render-text` v1 sigue operativo e intacto (compatibilidad, PRD 02 FR-07).

---

## T5. Transporte de segmentos brain→PWA — Hito 2 [PROPUESTA, LOCKED: cursor long-poll dual]

**Elección fija:** `GET /speech/{id}/next` (HTTP long-poll) con **DOS watermarks
distintos** — un cursor solo no basta: el server no puede liberar su búffer acotado si no
sabe qué se REPRODUJO, y el cliente con prefetch re-pediría el mismo segmento pendiente
para siempre. WebSocket y bytes-en-SSE siguen descartados (estado/reconexión nuevos; canal
de eventos ensuciado, +33%).

| Aspecto | LOCKED |
|---|---|
| Watermarks | `after` = mayor `seq` RECIBIDO contiguo; `ack` = mayor `seq` con reproducción COMPLETADA contigua (`ended`). Petición: `GET /speech/{id}/next?after=N&ack=M` con invariante `-1 <= ack <= after`; iniciales `after=ack=-1`, `seq` desde 0. |
| Liberación de búffer | El server sólo libera metadata/referencias de fichero de segmentos `<= ack` VÁLIDO y monótono: el ack es la única señal de liberación (el recibo HTTP NO es `ended`). `ack` imposible (>`after`, <anterior, no contiguo) ⇒ error tipado 422, jamás silencio. |
| Semántica de respuesta | Server espera hasta `SPEECH_NEXT_HOLD_S=10` un segmento `seq == after+1` listo. Hay ⇒ `200 {seq, audio_url, is_final, mime}` (URL `/audio/…` con `_SAFE_FILENAME`). No hay en 10 s ⇒ `200 {wait:true}` y re-poll. |
| Dedup | Contra estado RECIBIDO y CONSUMIDO: un `seq` ya recibido (≤ after) no se re-sirve en orden; un `seq` ya consumido (≤ ack) duplicado por HTTP se descarta en cliente Y server lo ignora para liberación. |
| In-flight/prefetch | Exactamente UN `next()` in flight por job + a lo sumo 1 segmento de prefetch pendiente además del que suena (`SPEECH_CLIENT_PREFETCH=1`). Fetch timeout `SPEECH_CLIENT_FETCH_TIMEOUT_S=15` (> hold 10). |
| Terminal ordering | El endpoint NO marca el job `complete` sólo porque la síntesis terminó: el marcador `is_final` viaja con el ÚLTIMO segmento recibido; el job server-side pasa a `complete` cuando el último segmento fue RECIBIDO, y su limpieza a `expunged` sigue la política de retención terminal (T1) + el unconsumed-timeout de abajo. El `ack` final queda registrado como evidencia de reproducción (no es condición del `complete` del server, sí del observable de aceptación D6). |
| Hueco de seq | El server NO produce huecos salvo degradación real: segmento no retenido/perdido ⇒ job `degraded` y `next()` devuelve el estado — NUNCA salto silencioso ni auto-regeneración (regenerar es dominio Hito 3). |
| Cancel/abort/reload | Cancel de conexión (abort del fetch) ≠ cancel de job (sólo POST cancel con token, T1). Reconexión: re-poll con los DOS watermarks persistidos en memoria de la sesión PWA. Recarga de PWA: id/token no persisten (M2) ⇒ consumer desaparece; job con segmentos no consumidos ⇒ `SPEECH_UNCONSUMED_TIMEOUT_S=300` → terminal visible `expired-unconsumed` (evidencia); SIN expulsión prematura de jobs activos del registro (T1). |
| Backpressure productor | Buffer server por job `SPEECH_SEGMENT_BUFFER=8` segmentos YA recibidos-no-ackeados; productor bloquea hasta `SPEECH_PRODUCER_BLOCK_S=30`; vencido ⇒ job `degraded` + cancel de render. Tope `SPEECH_MAX_SEGMENTS=512`. La presión SE LIBERA por acks (tests con >8 segmentos lo exigen). |
| Despacho asíncrono | `/ask` CON id responde al terminar el LLM con `{answer, approval?, speech:{id, status:"delivering"}}` SIN esperar render completo. `/ask` legacy (sin id) mantiene el comportamiento síncrono actual `audio_url` completo. |
| Memoria | Audio por segmento vive en fichero bajo `audio_dir`; el búffer en memoria es metadata + ruta, no bytes. |

**Tests de presión/carrera exigidos (VS2.5/VS2.6):** >8 segmentos con acks progresivos
demuestran liberación de búffer Y primer-segmento-antes-que-final; `ended` retardado
(ack tardío) no duplica ni libera doble; entrega HTTP duplicada del mismo `seq` se dedupea
(recibido y consumido); consumidor desconectado aborta finito (unconsumed-timeout), sin
quedar colgado ni re-petición infinita del prefetch.

---

## T6. Reproducción por segmentos en la PWA — Hito 2 [PROPUESTA, LOCKED]

- Cola `audioQueue` existente (`app.js:1290-1331`) recibe ítems `{speech_request_id, seq,
  url}`; orden = `seq` asc por job; el interleave con anuncios ajenos se conserva
  (secuencial global, orden por solicitud preservado).
- **Ack/dedup:** recibo HTTP ≠ audición. Un `seq` se marca consumido SOLO en `ended` del
  elemento `<audio>` (o stop explícito) y se comunica como `ack` (watermark contiguo, T5);
  el server libera búffer sólo con acks válidos. Replay de un `seq` ya consumido = descartar
  (dedup contra recibido Y consumido).
- **Error de fetch de segmento:** retry mismo `seq` hasta `SPEECH_SEGMENT_RETRY=2`
  (backoff fijo 1 s); agotado ⇒ playback del job `degraded` (toast visible + estado),
  sin salto silencioso, sin bucle.
- **Reconexión:** re-poll con los DOS watermarks (`after`/`ack`); job `cancelled` ⇒
  terminal, NADA se re-encola. Recarga de PWA a mitad: id/token no se persisten (M2); el
  job muere por unconsumed-timeout server (T5) y el texto queda en call-history — sin
  replay oculto.
- Sin barge-in acústico requerido (no-goal); el pipeline de medios del navegador es la
  única vía validada (fixtures decodificados reales, T12).

---

## T7. Cola pendiente del host (PC) — Hito 3 [PROPUESTA, LOCKED]

- **Dónde:** nuevo `hosts/herdr/tts-plugin/lib/pending_queue.py` (Python; el host es el
  árbitro, sin nueva "concha" global). **Wiring Bash = TRES puntos delgados explícitos**
  (no basta cambiar una línea del branch busy — el dispatcher necesita tick y callback):
  (1) `admit` en el sitio de omisión (`bin/herdr-tts:3197-3200`); (2) `tick` en el bucle
  del watcher (invocación por iteración, barata); (3) `completion` callback tras el
  playback local. Fallo de admisión ⇒ línea de log visible en el formato vigente + exit
  ≠0; NUNCA rompe el bucle del watcher. `is_playing` (`bin/herdr-tts:1377-1383`) queda
  como señal de diferir (lectura read-only de LOCK/PID; nadie escribe locks ajenos).
- **Dos niveles distintos (LOCKED):**
  - **Cola ACTIVA acotada** `PENDING_MAX_RECORDS=256`: los registros dispatchables
    (pending/announcing). Llena ⇒ el registro activo más viejo se DESPLAZA de la cola
    activa a estado `evicted` y PERMANECE en el ledger — desplazar no es perder.
  - **Ledger durable de evidencia** acotado `PENDING_MAX_DB_BYTES=8 MiB`: TODO lo
    admitido (texto+atribución+metadatos), incluidos los desplazados. SQLite WAL
    `~/.local/state/herdr-tts/pending.db` (consistente con la elección SQLite del brain,
    `history.py:34-36`).
- **Agotamiento del LEDGER/almacenamiento = condición dura visible `admission-blocked`:**
  preserva ÍNTEGROS los registros ya admitidos; el evento ENTRANTE NO se marca aceptado —
  se registra un sobre de emergencia acotado (sidecar `pending-overflow.json` + aggregate)
  con razón exacta y atribución, declarando EXPLÍCITAMENTE que no se puede prometer
  detalle recuperable cuando la escritura durable es imposible. Un contador SOLO nunca se
  presenta como "el evento queda preservado"; sin borrado/completación silenciosa; sin
  garantía de almacenamiento detallado infinito bajo disco finito — fallo de recurso =
  degradado/blocked honesto. La PWA/operador ven el diagnóstico (razón+atribución).
- **Backpressure de admisión JAMÁS mutea el PC:** la reproducción en curso no se toca;
  los registros siguen protegidos por regeneración.
- **Consolidación:** misma clave `(pane_id, status, epoch)` dentro de
  `CONSOLIDATION_WINDOW_S=60` ⇒ `repeat_count++`, `last_seen_ts`, SIN cambio de estado
  (ventana > settle 5 s y debounce 20 s del host para no doble-contar).
- **Época:** `pane_pid` capturado de tmux en la observación; evento posterior con mismo
  `pane_id` y `pane_pid` distinto ⇒ época nueva ⇒ registros no terminales del pane →
  `expired` (visible, no reproducible). `pane_pid` no disponible ⇒ flag
  `epoch_unverified=true` visible en el registro (sin inventar invalidación).
- **Repetición tras terminal (regla fijada):** terminales NUNCA se reabren; evento
  genuinamente nuevo (nueva transición observada fuera de ventana) crea registro NUEVO
  con `announce_seq` siguiente; repetición dentro de ventana durante `announcing` = sólo
  metadatos.
- **Dispatcher + mapa de completion (LOCKED):** en cada `tick`, si `!is_playing` y hay
  `pending` FIFO por `first_seen_ts` ⇒ **claim-before-enqueue**: el registro pasa a
  `announcing` con `claimed_ts` ANTES de encolar (claim impide doble dispatch en ticks
  concurrentes); enqueue por la superficie actual pasando `identifiers` — el daemon
  responde tipado `ok=true item=<id> queue_len=<n>` (`daemon.py:855`, verificado) y el
  registro guarda `item_id`; fallo de enqueue ⇒ reset del claim → `pending` (sin doble
  dispatch). El `completion` callback mapea por `item_id`/identifiers:
  finalización COMPLETED ⇒ `announced`; STOPPED/unknown ⇒ `uncertain` (visible). El
  estatus del host es FIFO **non-preempt**: encola sin política PREEMPT aunque el engine
  la tenga configurada — los pendientes JAMÁS cortan lo que suena.
- **Topes/valores:** ver T11. Los mp3 JAMÁS se retienen: la GC vigente `ann-*` >1 h
  (`watcher.py:260-274`) manda y la recuperación es REGENERAR desde texto con la cadena
  local y la política de proveedor EXPLÍCITA vigente del operador — sin red nueva
  implícita; no se afirma "local-only".
- **Crash:** `announcing` huérfano al reiniciar → `uncertain` (idempotente, visible);
  exactly-once audible NO se reclama; resolución sólo por acción deliberada (T8).
  Cancel de un job de respuesta NO toca registros ambientales (PRD 03 FR-08).

---

## T8. Superficie de acciones deliberadas + registros en PWA — Hito 3 [PROPUESTA, LOCKED]

- **Host CLI (operador):** `pending_queue.py list|status|retry <id>|resolve <id>
  {announced|expired}` — mínimo necesario para resolver `uncertain` en PC; cada acción
  queda en el registro (`resolved_by`, `resolved_ts`).
- **PWA:** sección estrecha "Pendientes" (lista) de registros del TELÉFONO, persistida en
  `localStorage["herdr.speech.pending.v1"]` (JSON), tope `PWA_PENDING_MAX_RECORDS=100`;
  desborde ⇒ contador agregado `overflow_count` mostrado (visible, no silencioso).
  **Excepciones de quota/escritura de localStorage (QuotaExceededError, modo privado)
  = condición visible de integridad de datos:** los registros no persistidos se marcan
  `uncertain` en la sesión viva con banner honesto ("no persistente hasta recargar");
  el scope de las acciones deliberadas cubre también estos registros de sesión.
  Acciones por registro: escuchar (regenera), marcar anunciado, descartar. Sin dashboard
  completo, sin sync cloud, sin borrado más allá de las reglas existentes (D7).

---

## T9. Fallback entre proveedores — Hito 4 [PROPUESTA, LOCKED]

- **Config (patrón genérico del MOTOR, sin dependencia de host):** env
  `AGENT_TTS_FALLBACK_CONFIG` con default `~/.config/agent-tts/fallback.json` — MISMO
  patrón verificado que el motor ya usa (`AGENT_TTS_*` en `constants.py:23-52`;
  `~/.config/agent-tts/lexicon.json` en `cleaner.py:206-210`). El host sólo CONSUME el
  motor; nada de rutas `.config/herdr-tts` del host dentro del motor. Fichero del
  operador; jamás commiteado; sin claves (siguen donde el motor las lee hoy). Schema
  estricto: `{fallback_enabled:false, chain:[{provider, voice, notes}]}` — `notes` NO
  vacío (obligatorio, D3); validación dura rechaza config inválida con error tipado.
  Ausencia de fichero o `fallback_enabled:false` ⇒ comportamiento actual idéntico.
- **Dónde:** la envoltura vive en la orquestación del motor (asiento actual:
  `use_pipelined_stream`/`_speak_pipelined` `cli.py:231-330` y `_build_engine`
  `daemon.py:649`); los `TTSProvider` no se reescriben (contrato `base.py:24-63`).
- **Presupuesto único por job:** `FALLBACK_TOTAL_ATTEMPTS=4` cubre JUNTOS el retry
  stream→batch del mismo proveedor (existente, `elevenlabs.py:141-168`) Y los intentos
  cross-provider. `FALLBACK_LINK_ATTEMPTS=2` por eslabón. Agotado ⇒ `failed_visible`.
- **Clasificación LOCKED:** reintentable-en-eslabón = error de red, HTTP 5xx, HTTP 429
  (backoff fijo 2 s luego 4 s), timeout de proveedor. Saltar-eslabón = HTTP 4xx (≠429),
  auth/clave ausente, voz no soportada. **Chequeo de clave ANTES de cualquier submit**
  (eslabón sin clave = no-configurado, skip honesto, FR-08).
- **Cancelación:** `stop_checker` consultado ANTES de cada intento (incluido entre
  eslabones); cancelado ⇒ cero intentos posteriores (FR-03). Nunca fallback por cancel.
- **Parcial audible (LOCKED — sin pretender alineación de texto imposible):**
  - El fallback cross-provider AUTOMÁTICO sólo re-sintetiza bytes que NUNCA se
    reprodujeron (nada audible aún: modo fichero/sin-play, o grupos producidos pero aún
    no reproducidos). No existe alineación "texto restante exacto mid-group" entre
    proveedores — NO se afirma tal cosa.
  - Tras un parcial YA audible: se detiene con estado `partial/uncertain` VISIBLE y la
    salida es REPLAY DELIBERADO (iniciado por el usuario) del texto pendiente, con
    POSIBLES DUPLICADOS EXPLÍCITAMENTE RECONOCIDOS en la UI/evidencia (re-leer desde el
    inicio del grupo fallido es lo honesto); nunca auto-retry, nunca re-lectura
    automática desde el inicio ni "resuma exacto" fingido.
  - Modo frames (PC): frontera no reconocible ⇒ tras parcial audible NO hay
    cross-fallback automático (abstención honesta + visible); sólo el replay deliberado.
- **Coste:** intentos acotados ⇒ coste extra acotado y visible en evidencia (por intento:
  provider, resultado, bytes); NUNCA se garantiza coste cero (D3).
- Cada intento se registra `{provider, outcome: ok|retryable-fail|skip|cancelled, ts}`
  para evidencia (FR-07).

---

## T10. Privacidad y configuración

- Sin claves en artefactos/logs/manifests (EXECUTION §4 lo exige en schema).
- Notas de privacidad/costo obligatorias por eslabón (D3, T9).
- Registros de anuncios: texto sanitizado/acotado como hoy
  (`announce_max_chars`, `watcher.py:223-250`); datos de dispositivo sin sync cloud.
- Regeneración usa la política de proveedor EXPLÍCITA del operador; los fakes de CI nunca
  autorizan red real.

---

## T11. Constantes de diseño (tuneables, con invariantes y boundary tests)

Todas son constantes técnicas ajustables (no promesas de producto); cada una tiene test de
frontera en su tarea (valor-1, valor, valor+1 donde aplica).

| Constante | Valor | Invariante que protege |
|---|---|---|
| `SPEECH_ID_*` charset/len | `[A-Za-z0-9._-]`, 8–64 | Ids opacos inyectables en URL/CLI sin escapados |
| `SPEECH_REGISTRY_MAX_JOBS` | 32 | Memoria acotada; rechazo visible, nunca OOM |
| `SPEECH_JOB_RETENTION_S` | 300 | Cancel tardía honesta sin fuga de entradas |
| `SPEECH_CANCEL_TERM_S` | 5 | Presupuesto acotado de terminación del pgid propio (FR-04 PRD 01) |
| `SPEECH_CANCEL_NET_RETRY` (backoff 1 s) | 2 | Fallo de red al cancelar: reintento finito + diagnóstico visible, nunca silencio ni falso "cancelado" |
| `SPEECH_UNCONSUMED_TIMEOUT_S` | 300 | Consumidor desaparecido: terminal visible finite, sin job colgado ni expulsión prematura de activos |
| `SPEECH_NEXT_HOLD_S` / `SPEECH_CLIENT_FETCH_TIMEOUT_S` | 10 / 15 | Long-poll saneado; cliente siempre > server hold |
| `SPEECH_SEGMENT_BUFFER` / `SPEECH_PRODUCER_BLOCK_S` | 8 / 30 | Backpressure productor-consumidor (FR-06 PRD 02) |
| `SPEECH_MAX_SEGMENTS` | 512 | Cota de sesgos infinitos |
| `SPEECH_SEGMENT_RETRY` (backoff 1 s) | 2 | Fallo finito, nunca bucle |
| `SPEECH_CLIENT_PREFETCH` | 1 | Un request in flight por job |
| `CONSOLIDATION_WINDOW_S` | 60 | > settle (5) + debounce (20) del host |
| `PENDING_MAX_RECORDS` / `PENDING_MAX_DB_BYTES` | 256 / 8 MiB | Almacenamiento acotado con degradación visible |
| `PWA_PENDING_MAX_RECORDS` | 100 | localStorage acotado + overflow visible |
| `FALLBACK_TOTAL_ATTEMPTS` / `FALLBACK_LINK_ATTEMPTS` | 4 / 2 | Coste/intentos finitos por job |
| `FALLBACK_BACKOFF_S` | 2, 4 | Backoff fijo determinista (test con fake clock) |

Timing en tests: fake clock/inyección de tiempos — JAMÁS SLO físico de rendimiento.

---

## T12. Tooling y gates (elección FIJA; los checkers son FUTURE con tarea creadora)

**Principio corregido:** `coverage run … && coverage json` NO exige el 90% — exit 0 sólo
dice "tests pasaron". El umbral lo aplica un comparador explícito, y TODO scope de
líneas/modificados/matriz compara **bytes del snapshot BASELINE inmutable ↔ candidato del
working tree (incluidos untracked previstos)** — JAMÁS `git diff base..HEAD` (los commits
están desautorizados: un commit-diff no vería la implementación). Flags fijos:
`--baseline-snapshot PATH --candidate-snapshot PATH`.

### T12.1 Comparador de cobertura — FUTURE `scripts/voice-stack/coverage_gate.py` (creador: VS0.9)

```
python3 scripts/voice-stack/coverage_gate.py \
  --lang python|js --component engine|brain|host|pwa \
  --baseline-snapshot "$RUN/baseline-snapshot.json" \
  --candidate-snapshot "$RUN/snapshot.json" \
  --coverage-json <coverage.py JSON | Node LCOV convertido> \
  --baseline-coverage "$RUN/baseline/<component>.json"
Exit: 0 = pass · 1 = FAIL numérico · 2 = blocked (input/mapeo no fiable)
```

- **Selección de alcance (D4, conservador aprobado):** módulos de producción COMPLETOS
  tocados (según baseline↔candidato) ⇒ ≥90% líneas Y ramas por módulo; además no-regresión
  del TOTAL contra la línea base. Sin pool Python/JS (por componente, independiente).
- **Exclusiones:** inventario EXPLÍCITO por fichero (tests, third-party, glue listado y
  medido) — nunca exclusión en bloque de sources ni `--source` salvaje.
- **Colección Python:** `coverage run --branch --source <raíz-src-del-cwd> -m pytest …`
  por cwd ⇒ el denominador es el código del componente, no pytest/librerías.
- **Colección JS:** LCOV nativo del runner de Node (flags verificados en `node --help`
  local v26.8.2: `--experimental-test-coverage`, `--test-reporter=lcov`,
  `--test-reporter-destination=<file>`) ⇒ MISMO comparador changed-module+total. El
  umbral nativo `--test-coverage-branches=90` sobre TODOS los scripts unitarios
  SOBREALCANZA (bloquearía la línea base) — no se usa como gate; la versión exacta de
  Node instalada se registra y `coverage_gate.py --check-env` la valida (incompatible ⇒
  exit 2 blocked, no degradar a líneas en silencio).
- **Líneas base (VS0.5):** captura + validación de instrumentación SÓLO — NO se exige
  ≥90% al código preexistente; la exigencia numérica aplica a módulos tocados del
  candidato TRAS implementar. Si el allowlist+diff confirman componente sin cambios ⇒
  resultado explícito `not_applicable` (nunca un 100 falso).
- **Auto-test de honestidad (VS0.9):** fixture a 89.99% DEBE fallar (exit 1) y a 90.0%
  pasar (exit 0); sin esto el gate no se considera instrumentado.

### T12.2 Bash — líneas + matriz de alternativas (D9)

- **Líneas:** colector PS4/xtrace NATIVO de bash (DECISIÓN PROPIETARIO 2026-09-30:
  kcov 42 prebuilt y kcov 43 brew no trazan bash 5.2 en Ubuntu 24.04 — su capa
  execve-redirector deja de reescribir execs hijos; `SHELLOPTS=xtrace` +
  `PS4='+${LINENO}@${BASH_SOURCE}@'` demostrado con propagación a hijos y eventos
  exactos por línea) sobre el harness Bash; denominador = líneas ejecutables
  MODIFICADAS según baseline↔candidato (FUTURE `scripts/voice-stack/bash_changed_lines.py --baseline-snapshot … --candidate-snapshot … --ps4-harness …`).
  ≥90% (D9). Smoke NO cuenta como cobertura.
- **Matriz de alternativas:** FUTURE `scripts/voice-stack/bash_matrix.py
  --baseline-snapshot … --candidate-snapshot … --table
  hosts/herdr/tts-plugin/tests/matrix/bash-decisions.json`. La matriz NO pasa porque el
  string del testcase exista: los casos NOMBRADOS SE EJECUTAN (harness
  `tests/host_cli_cases.sh`), con outcomes y trazas de cobertura ligadas al candidato;
  caso faltante/fallado/no-ejecutado/duplicado-no-coincidente ⇒ FAIL. El catálogo enumera
  las alternativas de decisión modificadas (if/elif/case/`&&`/`||`, true/false) con los
  constructos que el parser soporta; sintaxis no soportada en código modificado ⇒
  **blocked tipado**, nunca ignorado. Subconjunto permitido LOCKED: las decisiones Bash
  modificadas DEBEN usar if/case simples soportados y fail-closed en caso contrario —
  no se fuerza migrar el shell histórico fuera de alcance.
- La matriz es evidencia de alternativas cubiertas, NO métrica de ramas (D9).

### T12.3 Índice de gates (SIN comandos duplicados — la tabla única autoritativa de comandos exactos es **Gates** en `TASKS.md`)

| Gate | Propósito | Creador/estado |
|---|---|---|
| G-ENG-PY | Cobertura D4 del motor Python (módulos tocados ≥90 línea+rama + no-regresión total) | coverage VS0.3 · gate VS0.9 |
| G-BRN-PY | Cobertura D4 del brain Python | coverage VS0.3 · gate VS0.9 |
| G-HOST-PY | Cobertura D4 del Python del host (`lib/`) | coverage VS0.3 · gate VS0.9 |
| G-JS | Cobertura D4 de la PWA (LCOV nativo → comparador) | flags verificados v26.8.2 · gate VS0.9 |
| G-E2E | Navegador real + fixtures decodificados (D6); runner SEPARADO del unit | harness VS0.6 |
| G-BASH-LINES | Líneas ejecutables modificadas Bash ≥90 (D9) | VS0.7/VS0.8 |
| G-BASH-MATRIX | Matriz de alternativas de decisión Bash ejecutada (D9) | VS0.8 |
| G-BOUNDARY | Frontera monorepo motor→hosts | existe hoy (ci.yml) |
| G-SMOKE | Humo del host (NO cobertura) | existe hoy |
| G-X | Re-ejecución íntegra sobre bytes finales re-vinculados | VSX |

Los artefactos de colección (coverage JSON, LCOV, kcov) se escriben bajo `$RUN` (o cwd
del componente y se referencian desde el manifiesto) según el comando EXACTO de la tabla
Gates de TASKS.md — este documento NO reproduce variantes de comando (toda duplicación
aquí es fuente de divergencia). Las especificaciones de CLI de T12.1/T12.2 (flags y
semántica de los scripts FUTURE) siguen siendo la referencia de interfaz, no una tabla de
ejecución. Todos los gates son FUTURE no verificados en runtime hasta su fundación y
ejecución evidenciadas.

Reglas transversales: unit y E2E NUNCA comparten gate (pytest unit excluye `tests/e2e`);
ningún comando con elipses/`…`/`idem`/shorthand — lo que no es sintaxis literal no es un
comando; los casos pytest requeridos por la matriz FR son FUTURE hasta que su tarea los
escribe (no se afirma "el comando existe" para tests no creados).

**Instalaciones (versiones FIJAS, no "elige la última"):** `coverage>=7.6,<8` (Python),
`pytest-playwright>=0.5,<0.8` + Chromium bundle correspondiente — kcov DESCARTADO
2026-09-30 (decisión propietario: colector PS4 stdlib; evidencia de incompatibilidad
registrada en el run-dir) — la
versión EXACTA resuelta queda registrada en evidencia y lockfiles (la tarea de
instalación actualiza lockfiles dentro de su allowlist); `coverage_gate.py --check-env`
valida compatibilidad (Node ≥20 con reporter LCOV, Python del venv) y bloquea (exit 2) si
no. **Orden de línea base:** la infraestructura (VS0.3) modifica configuración ⇒ la línea
base se captura DESPUÉS de infra y ANTES de cualquier cambio de producción (VS0.5).

**Piso por módulo (D4 fallback):** si el mapeo diff→rama no es fiable con la toolería
elegida, el gate es ≥90% línea+rama sobre los MÓDULOS completos tocados + diff adjunto —
etiquetado `module-floor` en el manifiesto (aprobado por D4; no es pass falso). Glue de
integración mínimo se MIDE (allowlist estrecha por fichero), nunca exclusión en bloque.

**Aserciones negativas obligatorias** (presentes en los tests de cada hito): sin re-encola
de speech cancelado; sin cancelación de agente/aprobación; cero submissions duplicadas;
ningún estado terminal reabierto; ningún salto de seq silencioso; ningún pgid ajeno señalado.

### T12.4 Harness E2E navegador (creador: VS0.6)

`pytest-playwright` + Chromium (versiones fijas T12.3, registradas). Fakes deterministas:
LLM/STT/proveedor/permisos de micrófono. REAL: elementos `<audio>` del navegador con
fixtures mp3 decodificados commiteados (`tests/e2e/fixtures/`), eventos de medios reales
(`ended`/`error`/`timeupdate`) — JAMÁS `play()` moqueado. Launch de Chromium SOLO con
`--autoplay-policy=no-user-gesture-required`; PROHIBIDO `--no-sandbox` o permisos globales
en blanket. Los tests de presión de T5 (>8 segmentos, ended retardado, HTTP duplicado,
consumidor desconectado) viven aquí.

---

## T13. Blockers nombrados (si el código vigente contradice este plan)

1. **B-T1:** si al lanzamiento el `/ask` vigente ya no es síncrono o `speak_answer` cambió
   de asiento (`server.py:591`) ⇒ re-anclar T1/T2 antes de codificar (stop, no improvisar).
2. **B-T3:** si el IPC del daemon ganara cancel por ítem propio ⇒ reusar ESE mecanismo y
   borrar T3 (decisión técnica, documentada en manifiesto).
3. **B-T4:** si `contracts/` declara una v2 incompatible ⇒ stop para decisión (contrato
   normativo, no rutina).
4. **B-T12:** si el Node instalado no soporta umbrales nativos de cobertura ⇒ gate JS
   `blocked` visible — prohibido sustituir por líneas a ciegas.
5. **B-D8:** conflicto standalone↔canónico que exija cambiar comportamiento ⇒ stop
   (decisión del usuario, EXECUTION §2.3).

No hay otros blockers CONOCIDOS contra HEAD `384dd4f` en verificación read-only
(2026-09-30). Calificación honesta: este plan es diseño NO EJECUTADO — la implementación
puede descubrir fallos reales de validación (toolería, flags, comportamiento del código)
que al manifestarse son bloqueos a reportar, no a improvisar.
