# PRD 01 — Cancelación de habla por solicitud

> **Estado: DRAFT.** Trazado a D1 (hito 1), D5, D6, D7, D8, D9. Este PRD no autoriza implementación.
> Diseño técnico preciso: **LOCKED en `../TECHNICAL-PLAN.md` T1–T3** (identidad/registro,
> cancelación server-side, cancelación dirigida del daemon); ejecución: `../TASKS.md` VS1.*.
> Cobertura/gates: README D4 (Python/JS) y D9 (Bash), tooling en TECHNICAL-PLAN T12,
> evidencias/cierre en `../EXECUTION.md` §4–§6.
> **Fuentes (D8):** brain canónico en `agent-tts/hosts/herdr/brain`; motor `engine/src/agent_tts`;
> host `hosts/herdr/tts-plugin`. Líneas citadas re-verificadas en HEAD `384dd4f` (2ª revisión
> 2026-09-30): la sesión de implementación re-verifica el estado vigente al lanzamiento.

## 1. Contexto y problema

El usuario habla con el cerebro desde el teléfono (PWA herdr-brain) y recibe voz por dos canales
independientes y siempre activos por diseño (PC vía daemon herdr-tts, teléfono vía SSE + `/audio`)
— `herdr-brain/src/herdr_brain/server.py:273-276`, `tts_daemon.py:1-27`.

Hoy "parar la voz" no está alcanceado a la solicitud:

- El botón stop / colgar del teléfono ejecuta `stopAudio`, puramente local: vacía la cola del
  navegador y pausa el player. **No avisa al servidor** — `static/app.js:1404-1417` (canónico).
- La síntesis del lado servidor es un subprocess bloqueante que solo termina con el MP3 completo
  (`tts.py:115-152`); una síntesis en curso continúa aunque el usuario pare o re-pregunte.
- El motor/daemon tiene `stop` **global de la sesión activa** (`agent-tts/engine/src/agent_tts/daemon.py:588-613`)
  y la cola no ofrece cancelación por ítem ni por solicitud (los ítems ya aceptan `identifiers`,
  `queue_manager.py:593-643`, pero nadie los usa para cancelar).
- No existe cancelación dirigida: ningún mecanismo actual dice "cállele a ESTA respuesta" sin
  aplicar a todo lo demás. Si una nueva pregunta debe además sustituir (cancelar) la voz de la
  anterior, eso NO está aprobado como default de producto — se trata de una opción abierta y
  explícita (ver FR-03).

Resultado: no hay forma de cancelar la voz de una solicitud concreta en ambos canales sin afectar
a las demás.

## 2A. Objetivos y no-objetivos

**Objetivos**

1. Cada solicitud de voz (turno de conversación o anuncio atribuido) lleva un identificador de
   solicitud **[PROPUESTA: `speech_request_id`]** que fluye teléfono → brain → canales (SSE, daemon,
   superficie herdr-tts).
2. Una acción de cancelación del usuario (stop explícito o colgar) cancela la voz de ESA solicitud
   en ambos canales: síntesis en vuelo, segmentos pendientes y cola. Los ids son aislados: una
   solicitud no cancela implícitamente la voz de otra (la sustitución automática por nueva solicitud
   es opción abierta, no default — FR-03).
3. La cancelación es precisa: no toca voz de otras solicitudes ni anuncios ajenos.

**No-objetivos (D1/D7)**

- Cancelar la ejecución del agente/LLM/herramientas: el stop de voz SOLO cancela voz.
- Silenciar canales globalmente ni exclusividad multi-dispositivo.
- Mover el mutex del host herdr-tts ni rediseñar la propiedad de canal del motor
  (`ownership.py` se reutiliza tal cual).
- Cambiar la política de anuncios pendientes (Hito 3).

## 2B. Arquitectura y límites de confianza

Componentes y superficies EXISTENTES que se reutilizan (no se inventa otro árbitro global):

- **PWA (teléfono):** cola secuencial `audioQueue`/`pumpAudio` (`app.js:1290-1331`, canónico) y `stopAudio`.
- **herdr-brain:** seams inyectables `tts_renderer`, `watcher`, `daemon_probe` (`server.py:206-316`);
  SSE atribuido (`server.py:396-433`).
- **herdr-tts:** CLI `bin/herdr-tts` (`--render-text` en `bin/herdr-tts:255`) y daemon de PC con
  comandos `ping/shutdown/play/enqueue/status/stop` (`daemon.py:588-613`).
- **agent-tts:** cola con `identifiers` por ítem y políticas `PREEMPT`/`COALESCE`
  (`queue_manager.py:593-643,713-738`); `stop_checker` cooperativo en proveedores
  (`providers/base.py:24-63`).

Límites de confianza **[PROPUESTA — diseño LOCKED en T1/T2/T3]**:

- La PWA es cliente no confiable: el `speech_request_id` lo mints la PWA (UUID) y el brain
  LO VALIDA (charset/longitud); la autoridad de cancelación es un **capability token
  criptográfico por job** generado por la PWA ANTES de `/ask` (comparación constant-time
  server-side; jamás en URL/query/logs; T1). `session_id` es routing, no auth.
- Endpoint nuevo en el brain: `POST /speech/{id}/cancel` con `session_id` +
  `speech_cancel_token`; alcance: solo voz de esa solicitud (token inválido ⇒ 403; id
  desconocido ⇒ no-op honesto sin enumeración).
- Extensión del IPC del daemon: `cancel {identifiers}` dirigido a ítems/identifiers — sin
  matar sesiones ajenas, sin `stop` global nuevo. **No** un `POST /stop` genérico del brain
  que mate LLM/herramientas.
- herdr-tts recibe la cancelación por su superficie de CLI/daemon existente; ningún
  componente escribe en los ficheros de lock/PID/socket de otro (`audio.py:42-76` es la
  única dueña).
- Los jobs de teléfono NO usan el daemon del motor (render a fichero por CLI): la
  cancelación telefónica es brain-side (T2); la cancelación del daemon es canal PC (T3).

## 2C. Esquema de datos y máquina de estados

`speech_request_id` **[PROPUESTA — reglas LOCKED en T1]**: string opaco validado
(charset `[A-Za-z0-9._-]`, 8–64); mints la PWA por turno conversacional, el brain para
anuncios (`ann-<uuid>`) y preasignado determinista para speech de approval replay
(`apr-<gate_id>`); registro en proceso acotado (32, retención 300 s post-terminal).

FSM de la voz de una solicitud **[PROPUESTA — FSM completa LOCKED en T1, incl. `degraded`
y expiración]**:

```
sintetizando ──cancel──▶ cancelada (abort subprocess / stop_checker=true; audio parcial NO se persista)
    │ ok                     (regla vigente: parcial nunca persiste — cli.py:462-497)
    ▼
encolada ──cancel──▶ cancelada (ítem retirado de cola por identifiers)
    │ dispatch
    ▼
reproduciendo ──cancel──▶ cortada (stop de ESA sesión/ítem; sin drain)
    │ fin natural
    ▼
terminada
```

Transiciones cancelares son idempotentes y convergen a `cancelada`/`cortada` en ambos canales.

## 2D. Seguridad y modelo de amenazas

- El endpoint de cancelación MUST validar que el `id` corresponde a voz activa del propio brain;
  un id desconocido resulta no-op con respuesta honesta (sin enumeración).
- La cancelación MUST propagarse con presupuestos acotados (timeout corto en subprocess
  terminate→kill) para no colgar hilos del watcher (`watcher.py` corre en daemon threads).
- Nada de credenciales nuevas: se reusan las rutas/pidfiles locales ya verificadas
  (`tts_daemon.py:44,76-86`).
- La PWA no gana capacidad de callarse el PC de otros: cancel acota a la solicitud.

## 2E. Métricas y criterios de aceptación

- **Aceptación funcional (D6):** tras cancelar, en <1 intervalo de observación de prueba no se
  emiten nuevos segmentos de ESA solicitud en ningún canal, las demás voces siguen su curso, y el
  proceso de agente sigue vivo (evidencia de proceso/turno intacto). La evidencia ES el
  comportamiento observado en tests/E2E, no un JSON de respuesta.
- **No-regresión:** coberturas y gates según README D4/D9 + TECHNICAL-PLAN T12 + TASKS
  (tabla de gates); evidencias en `EXECUTION.md` §4.

## 3. Requisitos (RFC 2119) con trazabilidad y evidencia

| FR | Requisito | Traza | Evidencia / supuesto |
|----|-----------|-------|----------------------|
| FR-01 | El sistema MUST asignar un identificador de solicitud a cada voz generada por el brain y propagarlo a ambos canales y a la PWA. | D1 | Base: payloads atribuidos existen (`server.py:400`, `queue_manager.py:600`). Campo nuevo: [PROPUESTA/ASSUMPTION] |
| FR-02 | El botón stop y el colgado del teléfono MUST cancelar en el servidor la voz de la solicitud activa, no solo el player local. | D1 | Gap verificado: `app.js:1404-1417` (canónico) es local |
| FR-03 | Los ids de solicitud MUST ser aislados: una nueva solicitud MUST NOT cancelar implícitamente la voz de otra solicitud o sesión. La sustitución automática de la respuesta anterior MAY implementarse SOLO como opción que el usuario apruebe expresamente en su momento (OFF por defecto); su ausencia no es defecto ni bloquea el hito. | D1 | Corrección 2026-09-30: la sustitución automática no fue aprobada como default |
| FR-04 | La cancelación MUST abortar la síntesis en vuelo con presupuesto acotado y sin persistir audio parcial. | D1, D5 | `tts.py:115-152` (subprocess); regla de parcial vigente `cli.py:462-497` |
| FR-05 | El daemon MUST ofrecer cancelación dirigida por solicitud/identifiers sin cortar ítems ajenos. | D1 | Hoy no existe: `daemon.py:588-613`; `identifiers` sí existen `queue_manager.py:600` |
| FR-06 | La cancelación MUST ser idempotente y convergente en ambos canales. | D1 | Precedente de idempotencia: stop en daemon idle (`daemon.py:613-618`) |
| FR-07 | La cancelación de voz MUST NOT cancelar ni alterar la ejecución del agente, LLM o herramientas aprobadas. | D1, D5, D7 | Restricción de producto |
| FR-08 | La cancelación MUST NOT afectar voz ni anuncios de otras solicitudes o sesiones. | D1, D2 | — |
| FR-09 | El endpoint/CLI de cancelación SHOULD responder con estado honesto (cancelado/ya-terminado/desconocido) sin enumeración. | D5 | [PROPUESTA] |

## 4. Escenarios de prueba y resultado observable esperado

- **Unit (brain):** cancel con id activo aborta el renderer inyectado (fake `tts_renderer`) y no
  escribe fichero; id inexistente → respuesta honesta sin efecto.
- **Unit (motor):** `cancel` por identifiers retira ítems pendientes y corta SOLO el ítem activo
  coincidente; ítems ajenos finalizan `COMPLETED` (reusar dobles de `engine/tests/test_queue_manager.py`).
- **Contract:** IPC del daemon responde `cancel` con schema tipado ok/err como los existentes
  (`daemon.py:622-634` para el patrón de errores); SSE incluye el campo nuevo sin romper el payload.
- **Integración:** brain + daemon reales locales con TTS simulado: cancelar a mitad de síntesis →
  sin fichero de audio persistido, sin segmentos nuevos, siguiente anuncio ajeno se reproduce.
- **E2E navegador (D6):** PWA con /ask y TTS simulados deterministas: stop a mitad → `player`
  silencioso, cola de ESA solicitud vacía, nueva solicitud reproduce completa; un anuncio SSE ajeno
  llega y suena DESPUÉS sin pérdida. Concurrency: dos solicitudes cruzadas, cada cancelación sólo
  silencia la suya (sin sustitución automática por defecto: la voz de la anterior NO se corta por
  llegar la nueva — FR-03). Reconexión SSE durante cancelación → estado final consistente.
- **Manual físico:** `EXECUTION.md` §8 (auricular/Bluetooth).
