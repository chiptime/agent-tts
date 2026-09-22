# BLOQUE 1.2 — Daemon por vía única con auto-arranque transparente (AT-04)

**Alcance**: PRD-AT-04 · **Prioridad**: P1 · **Esfuerzo**: L
**Repositorio**: agent-tts · **Estado**: Aprobado (reestructuración vía única 22/09/2026)

> Capa de orquestación del segundo sub-bloque del paquete P1 (`BLOQUE-1-paquete-motor.md`). El detalle funcional y no funcional vive en `../AT-04-daemon-persistente.md`, referenciado aquí por su identificador (RF/RNF/US).

### Objetivo del sub-bloque

Entregar el daemon persistente (`agent-tts --serve`) por vía única: el CLI es siempre un cliente y, si no hay daemon alcanzable, lo arranca y delega (auto-arranque transparente). No existe un segundo camino semántico: `--no-daemon` desaparece y `--foreground` queda únicamente como opción de despliegue y depuración (proceso adjunto a la terminal, pensado para systemd/journalctl), no como un comportamiento aparte bajo test. El daemon se construye desde el primer commit como dueño del estado de playback — el punto de montaje de la cola vive encima de `AudioSession` desde el día uno — para que la cola del BLOQUE 1.3 encaje sin una segunda migración de semántica de playback.

### Alcance (cita a PRD fuente)

- RF-AT-04-1: `--serve` arranca el daemon, escribe PID_FILE con su pid y limpia socket/pid al recibir SIGTERM/SIGINT.
- RF-AT-04-2: el daemon sirve los comandos IPC actuales (status/pause/resume/stop/seek/navegación por frases) sobre la sesión de playback activa, sin cambios de protocolo para clientes existentes.
- RF-AT-04-3: comandos nuevos `play <payload>` (texto y opciones de síntesis en una línea JSON), `ping` (versión y uptime) y `shutdown`.
- RF-AT-04-4: retirada; absorbida por RF-AT-09-1 y RF-AT-09-2 del BLOQUE 1.1 (elección atómica por `flock`; el huérfano solo se reclama tras ganar la elección; la colisión con otro daemon vivo se resuelve perdiendo la elección).
- RF-AT-04-5 (reescrita, vía única): el cliente CLI es siempre cliente; si `ping` no responde en 200 ms, arranca un daemon, verifica de nuevo y delega; si el arranque o el reintento fallan, error explícito. No existe ejecución CLI clásica como fallback.
- RF-AT-04-6: el daemon respeta AGENT_TTS_PLAYBACK en el arranque y por solicitud (un evento puede forzar target).
- RF-AT-04-7: política de vida del daemon: tras un periodo configurable sin solicitudes (idle timeout) termina ordenadamente y libera el canal según la propiedad del BLOQUE 1.1. El valor por defecto del timeout debe definirse en implementación; no existe número previo en estas PRDs que reutilizar.
- RF-AT-04-8: health check en la conexión del cliente: un daemon colgado (canal vivo pero `ping` sin respuesta) se mata y se rearranca antes de delegar (kill-and-respawn).
- RNF-AT-04-1: latencia evento-audio con daemon caliente menor de 250 ms p95 con edge para textos de menos de 200 caracteres.
- RNF-AT-04-2: RAM en reposo con kokoro caliente por debajo de 700 MB; sin modelo local cargado, por debajo de 80 MB.
- RNF-AT-04-3 (reescrita, vía única): un fallo del daemon nunca deja al usuario sin voz: kill-and-respawn (RF-AT-04-8) y reintento; si persiste, error claro y registrado.
- RNF-AT-04-4: 8 h en reposo sin crecimiento de RAM ni fugas de sesiones de audio (prueba de humo automatizada).
- RNF-AT-04-5: la caché de proveedores es trabajo de ciclo de vida nuevo, no reutilización (hoy `get_provider()` construye una instancia por llamada; mantener instancias vivas en el daemon es esfuerzo adicional que no debe subestimarse).
- US-AT-04-1 (daemon sin pagar arranque de Python por notificación), US-AT-04-2 (reescrita: `agent-tts "algo"` funciona idéntico haya o no daemon corriendo, por delegación transparente con auto-arranque), US-AT-04-3 (`status` con proveedor cargado y uptime; la cola pendiente llega con RF-AT-08-5 en el BLOQUE 1.3).

Fuera de alcance (sección correspondiente de AT-04): autenticación multiusuario, escucha en red, balanceo de múltiples daemons; y la cola con prioridades, que entrega en el BLOQUE 1.3.

### Criterios de aceptación

- p95 evento-audio por debajo de 250 ms con edge, textos de menos de 200 caracteres, daemon caliente (RNF-AT-04-1); la medición se registra junto con la comparación frente a spawn CLI (objetivo: reducción igual o mayor al 40%, métrica de éxito de AT-04).
- Delegación transparente verificada por test: una invocación con daemon corriendo y la misma invocación tras matar al daemon producen el mismo comportamiento observable para el usuario (US-AT-04-2); el arranque automático respeta la puerta de 200 ms de RF-AT-04-5.
- Daemon colgado (canal vivo, `ping` sin respuesta): kill-and-respawn verificado por test; el usuario no se queda sin voz (RNF-AT-04-3, RF-AT-04-8).
- 8 h en reposo sin fuga de RAM ni de sesiones de audio (RNF-AT-04-4), con cotas de RAM en reposo verificadas (RNF-AT-04-2).
- Suite IPC/playback existente en verde, en la vía única (RNF heredado de la suite; no existe un segundo modo contra el que ejecutarla).

### Dependencias y prerrequisitos

- Dependencia dura: BLOQUE 1.1 (AT-09). El auto-arranque transparente delega la exclusión del canal en la elección de propiedad: sin ella, dos clientes que auto-arrancan en paralelo compiten por el socket con el bug de robo de canal que 1.1 corrige.
- Prerrequisitos sobre el código actual, citados en el encaje de AT-04: `IPCServer` (ipc.py) con dispatch por línea, `AudioSession.handle_ipc_command`, `send_ipc_command` para la delegación del cliente, `playback_target.resolve_target` y el par LOCK_FILE/PID_FILE (ya con propiedad real tras 1.1).
- Prerrequisito de proceso: BLOQUE 1.1 cerrado con su definición de done cumplida.

### Superficies compartidas y zonas de conflicto

- **Dispatch IPC** (`AudioSession.handle_ipc_command`): este sub-bloque añade `play`/`ping`/`shutdown`; el BLOQUE 1.3 añadirá `enqueue` y los campos de cola en `status`. Adiciones aditivas y no rompentes para clientes existentes (RF-AT-04-2); el punto de congelación del contrato es el cierre del BLOQUE 1.3 (véase el paraguas del paquete).
- **LOCK_FILE/PID_FILE y socket**: la propiedad ya pertenece al BLOQUE 1.1; el daemon la hereda como dueño del canal y del estado de playback.
- **`AudioSession`**: se instancia por playback como hoy, pero dentro del proceso daemon y bajo el punto de montaje de la cola (vacío en este sub-bloque, ocupado por el `QueueManager` en 1.3).
- **Comando `status`**: se extiende con proveedor cargado y uptime (US-AT-04-3); los campos de cola llegan en 1.3 (RF-AT-08-5).
- **Caché de proveedores**: kokoro cargado en memoria y proceso piper vivo entre solicitudes; trabajo de ciclo de vida nuevo (RNF-AT-04-5), no una reutilización del código por-evento actual.

### Riesgos

- **Daemon wedged reteniendo el dispositivo de audio**, agudizado por la vía única: sin camino alternativo, un daemon colgado bloquea toda invocación; en wsl-ps la sesión PowerShell persistente y el dispositivo de audio de Windows viven mientras el daemon viva. Mitigación: health check con kill-and-respawn (RF-AT-04-8), idle timeout (RF-AT-04-7), liberación del dispositivo tras cada playback, comando `shutdown` y watchdog de systemd en la unit de ejemplo.
- **Deriva de estado entre daemon y clientes** (target cambiado en vivo). Mitigación: el estado expuesto por `status` se extiende y los clientes siempre re-leen `status` antes de actuar.
- **Regresión para los usuarios actuales** al pasar de spawn por evento a vía única. Mitigación: la superficie de regresión es el handshake de auto-arranque y delegación — un solo camino, una sola suite — y la capa de transporte mantiene la garantía de cero cambio de comportamiento heredada del BLOQUE 1.1 (RNF-AT-09-1).
- **Subestimación del trabajo de caché de proveedores** (RNF-AT-04-5): hoy no existe instancia viva que aprovechar; planificarlo como trabajo nuevo, no como reutilización.

### Definición de done

1. Criterios de aceptación anteriores medidos y registrados: p95 evento-audio y reducción frente a spawn CLI, delta de RAM a 8 h, cotas de RAM en reposo, tests de delegación transparente y de kill-and-respawn.
2. Suite IPC/playback en verde, en la vía única.
3. Trazabilidad del sub-bloque: RF-AT-04-1 a RF-AT-04-8, RNF-AT-04-1 a RNF-AT-04-5 y US-AT-04-1 a US-AT-04-3 cubiertos según la tabla del paraguas (RF-AT-04-4 retirada y absorbida por 1.1).
4. El valor por defecto del idle timeout de RF-AT-04-7 queda definido y documentado, o el aplazamiento de esa decisión queda registrado explícitamente.
5. Repositorio estable: el BLOQUE 1.3 puede arrancar sobre este suelo.

### Referencias

- `../AT-04-daemon-persistente.md` — PRD fuente: RF/RNF/US de detalle, riesgos y métricas.
- `BLOQUE-1-paquete-motor.md` — paraguas del paquete P1: secuencia, trazabilidad global y punto de congelación del contrato IPC.
- `BLOQUE-1.1-canal-de-control.md` — prerrequisito duro: propiedad del canal de control.
