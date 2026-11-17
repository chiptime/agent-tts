**ID**: PRD-HT-18 · **Proyecto**: agent-tts (motor / daemon)
**Prioridad**: P3 · **Estado**: Borrador (documento de intención; NO autoriza implementación ni commits)
**Dependencias**: ntfy server ≥ v2.18 (sequence IDs sobre mensajes programados). Sin dependencia con HT-17 (comparten la familia sequence-ID pero no código: HT-17 es bash en el plugin, esto es Python en el daemon).

# PRD-HT-18 — Heartbeat del daemon como dead man's switch (ntfy)

### Resumen ejecutivo

Las peores fallas de una flota desatendida son silenciosas: si el daemon (o el host) muere de noche, el proceso que te avisaba era el que murió. Esta PRD propone un loop de heartbeat **dentro del proceso persistente del daemon** (AT-04) que mantiene una notificación ntfy permanentemente programada en el futuro; si el daemon deja de latir, la notificación se entrega sola. La vigilancia vive en el server ntfy: sin cron externo, sin watcher propio que también puede morir, sin estado local.

### Mecánica (verificada en docs.ntfy.sh)

1. **Programar**: publicar con header `In: <window>` (p. ej. `In: 5m`) programa el mensaje para dentro de `window` — aún no se entrega.
2. **Re-programar**: publicar de nuevo con el **mismo sequence ID** reemplaza el mensaje programado anterior (se borra; el nuevo queda a `window` vista). Requerimiento: ntfy ≥ v2.18.
3. **Silencio = alerta**: si nadie re-publica, el último mensaje entregado transcurrida la ventana. El texto de la alerta lo escribe el propio emisor.

Mientras el daemon vive, el mensaje nunca se entrega (siempre queda escapándose hacia el futuro). Costo: un POST por intervalo.

### Problema y flujo actual

El daemon es la pieza persistente que hace funcionar voz y notificaciones de la flota, pero nada supervisa al supervisor: un OOM, un crash o un host colgado dejan la flota muda hasta que el operador lo descubre por ausencia (típicamente a la mañana, con horas perdidas). Los eventos one-shot del plugin (done/blocked/idle) presuponen un daemon vivo.

### Propuesta

- **Loop en el daemon**: thread dedicado (el daemon ya corre con `threading` — `daemon.py` tiene threads de trabajo y `Event` de stop) que cada `interval` publica:

  ```
  POST <server>/<topic>/heartbeat-<host_sanitizado>
  Headers: In: <window>, Priority: high
  Body: "⚠️ agent-tts daemon en <host> sin latido <window> — daemon caído o host inalcanzable"
  ```

- **Identidad**: sequence ID `heartbeat-<hostname>` saneado a `[-_A-Za-z0-9]` (≤ 64). Un latido por host; reinicios del daemon no cambian la identidad.
- **Antifalsas alarmas**: ventana ≥ 3× intervalo (validado al cargar config): con 60 s / 5 min, se toleran ~4 fallos de publicación consecutivos antes de alarmar. La pérdida de red del host **es** una condición alarmable (la flota quedó muda); el texto lo dice explícito ("caído o inalcanzable").
- **Knob opt-in** (convención del repo, como HT-05):
  - `TTS_HEARTBEAT=off` (default) | `on`
  - `TTS_HEARTBEAT_INTERVAL_S=60`, `TTS_HEARTBEAT_WINDOW_S=300`
  - `TTS_HEARTBEAT_PRIORITY=high` (4) — configurable
- **Gate de capacidad**: al armar, probe del server (soporta reemplazo de mensajes programados por sequence ID). Server viejo → no armar y una línea de log: en un server sin soporte, cada publish crearía un mensaje programado nuevo y la "alerta" sonaría cada ventana (spam). El doctor de AT-11 puede exponer el mismo chequeo.
- **Cliente HTTP**: helper mínimo stdlib (`urllib.request`) en el daemon — el cliente ntfy existente es bash en el plugin (`bin/herdr-tts`), no reutilizable desde Python. Sin dependencias nuevas.
- **Visibilidad**: `agent-tts status` muestra heartbeat activo, intervalo/ventana y próximo latido.

### Historias de usuario (US-HT-18-...)

- US-HT-18-1: Como operador con flota desatendida de noche, quiero enterarme en minutos de que un daemon/host dejó de latir, no a la mañana siguiente.
- US-HT-18-2: Como operador, quiero cero falsas alarmas por blips de red puntuales (ventana con margen sobre el intervalo).
- US-HT-18-3: Como maintainer, quiero opt-in con `off` por defecto y negativa explícita ante servers sin soporte, para nunca degradar en spam.

### Requisitos funcionales (RF-HT-18-...)

- RF-1: Con `TTS_HEARTBEAT=on`, el daemon arma un thread que publica cada `interval` con `In: <window>` y sequence ID `heartbeat-<host_sanitizado>`; el thread muere limpio con el `Event` de stop del shutdown existente.
- RF-2: Validación al cargar config: `window >= 3 * interval`; config inválida = no armar + error claro.
- RF-3: Gate de capacidad del server antes de armar; sin soporte, no armar (nunca publicar en modo que genere entregas repetidas).
- RF-4: `TTS_HEARTBEAT=off` reproduce el comportamiento actual exacto (cero publicaciones).
- RF-5: Fallos de publicación aislados no matan el loop (retry simple en el próximo intervalo); el loop nunca rompe la reproducción de audio ni el IPC.
- RF-6: `status` reporta estado del heartbeat.

### Requisitos no funcionales (RNF-HT-18-...)

- RNF-1: Sin dependencias nuevas (stdlib `urllib`).
- RNF-2: Presupuesto: 1 msg por intervalo (1440/día a 60 s). El intervalo es configurable para servers/tiers con límites diarios ajustados (v2.36+ cuenta mensajes/e-mails/llamadas contra límites diarios del tier).
- RNF-3: Tests del engine cubren: armado/no-armado por config, validación de ventana, saneado del hostname, cuerpo/headers del POST (mock del HTTP), shutdown limpio del thread.

### Encaje en la arquitectura actual

`engine/src/agent_tts/daemon.py` (threads + `Event` de stop existentes), config del daemon (`engine/src/agent_tts/` — junto al resto de `TTS_*`), CLI `status` existente. No toca plugin ni brain. Independiente de HT-17 (familia sequence-ID, distinto proceso y lenguaje).

### Prior art y diferenciación

Ninguna herramienta del barrido (agentvoice, claude-code-tts, opencode-smart-voice-notify) supervisa su propia infraestructura de notificación. El patrón dead man's switch sobre ntfy está documentado ([docs: Updating scheduled notifications](https://docs.ntfy.sh/publish/#updating-scheduled-notifications)) pero orientado a scripts; aplicarlo al daemon de una flota de agentes es la pieza nueva.

### Riesgos y mitigaciones

- **Server sin soporte v2.18** → gate RF-3 (el escenario sin gate es spam por ventana; inaceptable).
- **Host sin red** → alarmable por diseño (flota muda); texto explícito, prioridad configurable para no quemar al operador.
- **Límites diarios del tier ntfy.sh** → RNF-2: intervalo configurable; documentar el volumen en la ayuda.
- **Reinicio limpio del daemon entre latidos** → sin problema: al arrancar re-publica y reinicia la ventana; brevedad del gap << ventana.
- **Reloj del host** → la ventana la calcula el **server** (programación relativa `In:`); el drift local solo adelanta/atrasa el próximo publish, no la entrega.

### Plan de validación (F2)

Sesión física: armar heartbeat con ventana de 2 min en un host de prueba, matar el daemon (`kill -9`), verificar entrega de la alerta en el teléfono dentro de la ventana + margen; verificar que con el daemon vivo no se entrega NADA en 30 min; verificar silencio del loop en logs y `status`.
