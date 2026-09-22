**ID**: PRD-AT-04 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: AT-09 (dura, prerrequisito); sinergia con AT-08

> **Nota de revisión (22/09/2026)**: Se aborda JUNTO a AT-08 como paquete P1: el daemon posee la cola desde el día uno (cierra la open question del propio doc) para evitar una segunda migración de semántica de playback.

> **Nota de revisión (22/09/2026, vía única)**: La revisión contra el código real declara inviable el camino dual daemon/modo clásico: el CLI es SIEMPRE un cliente; si no hay daemon alcanzable, lo arranca y delega (vía única con auto-arranque transparente; precedente: tmux, emacsclient, gpg-agent, ssh ControlMaster). `--no-daemon` desaparece como semántica alternativa y `--foreground` queda solo como opción de despliegue/debug, no como segundo comportamiento bajo test. RF-AT-04-4 se retira (absorbida por RF-AT-09-1/RF-AT-09-2 de AT-09: elección por `flock` con liberación por kernel a la muerte del proceso); RF-AT-04-5 y RNF-AT-04-3 se reescriben para la vía única; se añaden RF-AT-04-7, RF-AT-04-8 y RNF-AT-04-5; la dependencia de AT-09 pasa a ser dura. El esfuerzo se mantiene en L: crece el trabajo de ciclo de vida (caché de proveedores nueva, health check, idle timeout) pero se retira la superficie del camino clásico.

# PRD-AT-04 — Modo daemon persistente del motor (`--serve`)

**Prioridad**: Alta · **Esfuerzo**: L

### Resumen ejecutivo

Cada turno hablado hoy paga el arranque completo de un proceso Python: intérprete, imports (miniaudio, proveedores) y, con kokoro, una carga fría del modelo de ~1.75 s. Esta PRD convierte el motor en un daemon persistente (`agent-tts --serve`) que reutiliza el Unix socket IPC existente para aceptar comandos de síntesis (play/queue/stop/status) y mantiene modelos calientes. herdr-tts deja de spawnear un CLI por evento.

### Problema y flujo actual

En una flota de N agentes que notifica por voz, cada evento (done, blocked, working) lanza `agent-tts` desde cero. El coste no es solo la latencia percibida (300 ms a 2 s según proveedor): es la varianza. Con piper el proceso persiste por CLI, con kokoro el modelo se recarga por CLI, y el lock de fichero LOCK_FILE/PID_FILE se crea y destruye por evento. El resultado es un motor de notificación con pico de latencia impredecible justo cuando el evento es urgente.

### Propuesta

`agent-tts --serve` arranca un proceso largo que: (1) abre el mismo socket IPC que hoy usa el playback interactivo y acepta los comandos existentes más `play`, `enqueue`, `ping`, `shutdown`; (2) resuelve el playback target una vez (local/winhost/wsl-ps) y lo reutiliza; (3) mantiene en caché el último proveedor usado, con kokoro cargado en memoria y el proceso piper vivo entre solicitudes (trabajo de ciclo de vida nuevo, no reutilización; ver RNF-AT-04-5). El cliente `agent-tts "texto"` es siempre cliente: verifica el canal de control (propiedad garantizada por AT-09) y la salud del daemon (`ping`); si no hay daemon alcanzable, lo arranca y delega (auto-arranque transparente). No existe un segundo camino semántico: `--no-daemon` desaparece; `--foreground` queda únicamente como opción de despliegue/debug (proceso adjunto a la terminal, pensado para systemd/journalctl y depuración), no como un comportamiento aparte bajo test. Se documenta una unit de systemd user como ejemplo de arranque.

### Historias de usuario (US-AT-04-1, ...)

- US-AT-04-1: Como maintainer de herdr, quiero un daemon al que enviar eventos de voz sin pagar arranque de Python por notificación.
- US-AT-04-2: Como usuario, quiero que `agent-tts "algo"` funcione idéntico haya o no daemon corriendo (delegación transparente con auto-arranque).
- US-AT-04-3: Como usuario, quiero `--ipc-cmd status` del daemon mostrando proveedor cargado, uptime y cola pendiente.

### Requisitos funcionales (RF-AT-04-...)

- RF-AT-04-1: `--serve` arranca el daemon, escribe PID_FILE con su pid y limpia socket/pid al recibir SIGTERM/SIGINT.
- RF-AT-04-2: El daemon sirve los comandos IPC actuales (status/pause/resume/stop/seek/navegación por frases) sobre la sesión de playback activa, sin cambios de protocolo para clientes existentes.
- RF-AT-04-3: Comandos nuevos `play <payload>` (texto + opciones de síntesis), `ping` (responde versión y uptime) y `shutdown`; el payload viaja en una línea JSON.
- RF-AT-04-4: ~~Detección de socket huérfano: si el pid del PID_FILE no vive, el daemon lo reclama; si vive otro daemon, error claro y no arranque.~~ **RETIRADA (22/09/2026)**: absorbida por RF-AT-09-1 y RF-AT-09-2 de AT-09 — la elección atómica por `flock` hace innecesaria la comprobación de vida del pid (el kernel libera el lock a la muerte del dueño, incluido SIGKILL), el socket huérfano solo se reclama tras ganar la elección, y la colisión con otro daemon vivo se resuelve perdiendo la elección.
- RF-AT-04-5 (reescrita 22/09/2026, vía única): El cliente CLI es siempre cliente: si `ping` no responde en 200 ms, arranca un daemon (auto-arranque transparente), verifica de nuevo y delega; si el arranque o el reintento fallan, error explícito — ya no existe ejecución CLI clásica como fallback.
- RF-AT-04-6: El daemon respeta AGENT_TTS_PLAYBACK en el arranque y por solicitud (un evento puede forzar target).
- RF-AT-04-7: Política de vida del daemon: tras un periodo configurable sin solicitudes (idle timeout) el daemon termina ordenadamente y libera el canal según la propiedad de AT-09; el valor por defecto del timeout debe definirse en implementación (no existe número previo en estas PRDs que reutilizar).
- RF-AT-04-8: Health check en la conexión del cliente: un daemon colgado (canal vivo pero `ping` sin respuesta) se mata y se rearranca antes de delegar (kill-and-respawn), motivado por el riesgo de daemon wedged reteniendo el dispositivo de audio que la vía única introduce.

### Requisitos no funcionales (RNF-AT-04-...)

- RNF-AT-04-1: Latencia evento-a-audio con daemon caliente menor de 250 ms p95 con edge para textos cortos (menos de 200 caracteres).
- RNF-AT-04-2: RAM en reposo con kokoro caliente por debajo de 700 MB; sin modelo local cargado, por debajo de 80 MB.
- RNF-AT-04-3 (reescrita 22/09/2026, vía única): Un fallo del daemon nunca deja al usuario sin voz: kill-and-respawn (RF-AT-04-8) y reintento; si persiste, error claro y registrado — no hay camino clásico al que caer.
- RNF-AT-04-4: Estabilidad: 8 h en reposo sin crecimiento de RAM ni fugas de sesiones de audio (prueba de humo automatizada).
- RNF-AT-04-5: La caché de proveedores es trabajo de ciclo de vida NUEVO, no reutilización: hoy `get_provider()` (providers/__init__.py) construye una instancia por llamada — kokoro cachea su sesión ONNX por instancia, no globalmente, así que la instancia nueva recarga el modelo — y piper spawnea un subproceso por llamada que muere en el `finally` de `synthesize_stream()`. Mantener instancias vivas en el daemon es esfuerzo adicional que no debe subestimarse.

### Encaje en la arquitectura actual

`IPCServer` (ipc.py) ya es un servidor de socket con handler de comandos por línea: el daemon lo reutiliza y extiende el dispatch. `AudioSession` se instancia por playback igual que hoy, pero dentro del proceso daemon. La resolución de target (`playback_target.resolve_target`) se hace una vez por solicitud. La delegación del cliente reutiliza `send_ipc_command`. La propiedad del canal (LOCK_FILE/PID_FILE/socket) deja de ser trabajo de esta PRD: la resuelve AT-09 con elección atómica por `flock`, y el daemon la hereda como dueño de la cola en AT-08. Nota de realidad del código para la caché de proveedores: no existe reutilización que aprovechar (ver RNF-AT-04-5); recordar además que los targets remotos solo soportan status/pause/resume/toggle-pause/stop — seek y navegación por frases devuelven ERR (winhost_client.py, powershell_playback.py) — y que wsl-ps mantiene una sesión PowerShell persistente (y el dispositivo de audio de Windows) durante toda la vida del target, no por playback.

### Prior art y diferenciación

Los MCP servers de TTS (mcp-tts en Go con say/ElevenLabs/Gemini/OpenAI, el oficial de ElevenLabs con OAuth, tts-mcp con OpenAI) exponen TTS como servicio, pero sobre JSON-RPC por stdio/HTTP pensado para que un modelo los llame. Aquí el servicio es IPC local de ultra baja latencia pensado para que un orquestador de flota (herdr) lo llame cientos de veces por hora; el CLI sigue siendo cliente first-class y hay exactamente un camino: vía única con auto-arranque transparente (precedente: tmux, emacsclient, gpg-agent, ssh ControlMaster).

### Dependencias

Dependencia dura: AT-09 (propiedad del canal de control: socket, lock y framing). La dependencia es dura porque el auto-arranque transparente delega la exclusión del canal en la elección de AT-09: sin ella, dos clientes que auto-arrancan en paralelo compiten por el socket con el bug actual de robo de canal. Sinergia directa con AT-08: la cola con prioridades necesita un dueño persistente y este daemon lo es.

### Riesgos y mitigaciones

- Riesgo: deriva de estado entre daemon y clientes (target cambiado en vivo). Mitigación: el estado de sesión ya expuesto por `status` se extiende con campos de daemon y los clientes siempre re-leen.
- Riesgo: daemon wedged (colgado) reteniendo el dispositivo de audio — agudizado por la vía única: sin camino clásico, un daemon wedged bloquea toda invocación; en wsl-ps la sesión PowerShell persistente y el dispositivo de audio de Windows viven mientras el daemon viva. Mitigación: health check con kill-and-respawn en la conexión del cliente (RF-AT-04-8), idle timeout (RF-AT-04-7), liberación de dispositivo tras cada playback y comando `shutdown` + watchdog de systemd en la unit ejemplo.
- Riesgo: regresión para los usuarios actuales al pasar de spawn por evento a vía única. ~~Mitigación: camino clásico intacto tras flag de detección; suite de tests de IPC existente corre contra ambos modos.~~ **RETIRADA (22/09/2026)**: no existen "ambos modos" ni usuarios sin daemon — toda invocación es cliente y auto-arranca si hace falta. Mitigación: la superficie de regresión es el handshake de auto-arranque y delegación (un solo camino, una sola suite) y la capa de transporte mantiene la garantía de cero cambio de comportamiento de AT-09 (RNF-AT-09-1).

### Métricas de éxito

- Reducción de latencia evento-a-audio p95 mayor o igual a 40% frente a spawn CLI, medida con edge y kokoro.
- Cero regresiones en la suite IPC/playback actual.
- RAM estable (delta menor de 5%) tras 8 h de operación simulada.

### Fuera de alcance

Autenticación multiusuario, escucha en red (el socket sigue siendo local), balanceo de múltiples daemons.

### Open questions

¿Protocolo de recarga de modelo kokoro sin reinicio (comando `reload`)? ¿Se mantienen `--serve` y `--foreground` como flags separados (arranque canónico del daemon frente a despliegue adjunto) o se fusionan en uno solo?

Resuelta (22/09/2026) — ¿debería el daemon poseer ya la cola AT-08 desde el día uno para evitar dos migraciones?: sí. El daemon se construye desde el inicio como dueño de la cola para que AT-08 encaje sin una segunda migración de semántica de playback; la cola en sí entrega en el bloque posterior del paquete P1.
