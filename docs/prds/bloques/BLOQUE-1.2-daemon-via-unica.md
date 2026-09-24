# BLOQUE 1.2 — Daemon por vía única con auto-arranque transparente (AT-04)

**Alcance**: PRD-AT-04 · **Prioridad**: P1 · **Esfuerzo**: L
**Repositorio**: agent-tts · **Estado**: Aprobado (reestructuración vía única 22/09/2026)

> Capa de orquestación del segundo sub-bloque del paquete P1 (`BLOQUE-1-paquete-motor.md`). El detalle funcional y no funcional vive en `../AT-04-daemon-persistente.md`, referenciado aquí por su identificador (RF/RNF/US).

### Objetivo del sub-bloque

Entregar el daemon persistente (`agent-tts --serve`) por vía única: el CLI es siempre un cliente y, si no hay daemon alcanzable, lo arranca y delega (auto-arranque transparente). No existe un segundo camino semántico: `--no-daemon` desaparece y `--foreground` queda como opción de despliegue y depuración (proceso adjunto a la terminal, pensado para systemd/journalctl), no como un comportamiento aparte bajo test; los dos arranques difieren únicamente en la política de vida del daemon (RF-AT-04-7): idle timeout para el daemon que el cliente auto-arranca implícitamente, ninguno para el arranque explícito — una diferencia de ciclo de vida, no un segundo camino de ejecución. El daemon se construye desde el primer commit como dueño del estado de playback — el punto de montaje de la cola vive encima de `AudioSession` desde el día uno — para que la cola del BLOQUE 1.3 encaje sin una segunda migración de semántica de playback.

### Alcance (cita a PRD fuente)

- RF-AT-04-1: `--serve` arranca el daemon, escribe PID_FILE con su pid y limpia socket/pid al recibir SIGTERM/SIGINT.
- RF-AT-04-2: el daemon sirve los comandos IPC actuales (status/pause/resume/stop/seek/navegación por frases) sobre la sesión de playback activa, sin cambios de protocolo para clientes existentes.
- RF-AT-04-3: comandos nuevos `play <payload>` (texto y opciones de síntesis en una línea JSON), `ping` (versión y uptime) y `shutdown`.
- RF-AT-04-4: retirada; absorbida por RF-AT-09-1 y RF-AT-09-2 del BLOQUE 1.1 (elección atómica por `flock`; el huérfano solo se reclama tras ganar la elección; la colisión con otro daemon vivo se resuelve perdiendo la elección).
- RF-AT-04-5 (reescrita, vía única): el cliente CLI es siempre cliente; si `ping` no responde en 200 ms, arranca un daemon, verifica de nuevo y delega; si el arranque o el reintento fallan, error explícito. No existe ejecución CLI clásica como fallback.
- RF-AT-04-6: el daemon respeta AGENT_TTS_PLAYBACK en el arranque y por solicitud (un evento puede forzar target).
- RF-AT-04-7: política de vida del daemon: tras un periodo configurable sin solicitudes (idle timeout) termina ordenadamente y libera el canal según la propiedad del BLOQUE 1.1. Valor por defecto (definido 22/09/2026): 30 minutos para el daemon arrancado implícitamente por el auto-arranque del cliente; sin timeout para el arranque explícito (`--serve`/`--foreground`); ambos configurables.
- RF-AT-04-8: health check en la conexión del cliente: un daemon colgado (canal vivo pero `ping` sin respuesta) se mata y se rearranca antes de delegar (kill-and-respawn).
- RNF-AT-04-1: latencia evento-audio con daemon caliente menor de 250 ms p95 con edge para textos de menos de 200 caracteres.
- RNF-AT-04-2: RAM en reposo con kokoro caliente por debajo de 700 MB; sin modelo local cargado, por debajo de 80 MB.
- RNF-AT-04-3 (reescrita, vía única): un fallo del daemon nunca deja al usuario sin voz: kill-and-respawn (RF-AT-04-8) y reintento; si persiste, error claro y registrado.
- RNF-AT-04-4 (reacotada 22/09/2026): 8 h en reposo sin crecimiento de RAM ni fugas de sesiones de audio (prueba de humo automatizada), sobre un daemon de arranque explícito — el auto-arrancado termina a los 30 minutos por defecto de RF-AT-04-7 y no vive lo suficiente para completar la prueba.
- RNF-AT-04-5: la caché de proveedores es trabajo de ciclo de vida nuevo, no reutilización (hoy `get_provider()` construye una instancia por llamada; mantener instancias vivas en el daemon es esfuerzo adicional que no debe subestimarse).
- US-AT-04-1 (daemon sin pagar arranque de Python por notificación), US-AT-04-2 (reescrita: `agent-tts "algo"` funciona idéntico haya o no daemon corriendo, por delegación transparente con auto-arranque), US-AT-04-3 (`status` con proveedor cargado y uptime; la cola pendiente llega con RF-AT-08-5 en el BLOQUE 1.3).

Fuera de alcance (sección correspondiente de AT-04): autenticación multiusuario, escucha en red, balanceo de múltiples daemons; y la cola con prioridades, que entrega en el BLOQUE 1.3.

### Criterios de aceptación

- p95 evento-audio por debajo de 250 ms con edge, textos de menos de 200 caracteres, daemon caliente (RNF-AT-04-1); la medición se registra junto con la comparación frente a spawn CLI (objetivo: reducción igual o mayor al 40%, métrica de éxito de AT-04).
- Delegación transparente verificada por test: una invocación con daemon corriendo y la misma invocación tras matar al daemon producen el mismo comportamiento observable para el usuario (US-AT-04-2); el arranque automático respeta la puerta de 200 ms de RF-AT-04-5.
- Daemon colgado (canal vivo, `ping` sin respuesta): kill-and-respawn verificado por test; el usuario no se queda sin voz (RNF-AT-04-3, RF-AT-04-8).
- 8 h en reposo sin fuga de RAM ni de sesiones de audio (RNF-AT-04-4), sobre un daemon de arranque explícito (el auto-arrancado termina a los 30 minutos por defecto), con cotas de RAM en reposo verificadas (RNF-AT-04-2).
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

1. Criterios de aceptación anteriores medidos y registrados: p95 evento-audio y reducción frente a spawn CLI, delta de RAM a 8 h sobre el daemon de arranque explícito, cotas de RAM en reposo, tests de delegación transparente y de kill-and-respawn.
2. Suite IPC/playback en verde, en la vía única.
3. Trazabilidad del sub-bloque: RF-AT-04-1 a RF-AT-04-8, RNF-AT-04-1 a RNF-AT-04-5 y US-AT-04-1 a US-AT-04-3 cubiertos según la tabla del paraguas (RF-AT-04-4 retirada y absorbida por 1.1).
4. Políticas de vida de RF-AT-04-7 verificadas por test: el daemon arrancado implícitamente por auto-arranque termina ordenadamente al expirar el timeout por defecto (30 minutos) liberando el canal según el BLOQUE 1.1, y el daemon de arranque explícito (`--serve`/`--foreground`), sin timeout por defecto, sigue vivo tras el periodo de inactividad; el timeout configurable se respeta en ambos modos.
5. Repositorio estable: el BLOQUE 1.3 puede arrancar sobre este suelo.

### Referencias

- `../AT-04-daemon-persistente.md` — PRD fuente: RF/RNF/US de detalle, riesgos y métricas.
- `BLOQUE-1-paquete-motor.md` — paraguas del paquete P1: secuencia, trazabilidad global y punto de congelación del contrato IPC.
- `BLOQUE-1.1-canal-de-control.md` — prerrequisito duro: propiedad del canal de control.

---

## Trazabilidad de implementación (cierre del sub-bloque, 24/09/2026)

Implementación en `feat/at-04-daemon-via-unica`. Habilitadores previos al daemon: `IPCServer` atiende cada conexión en su propio hilo (un `play` bloqueante no privaba a `status`/`pause` de ser servidos) y `cli._build_playback_session`/`cli._play_speech` extraídos de `speak()` para reutilizar el pipeline sin duplicarlo.

| Requisito | Implementación | Verificación |
|---|---|---|
| RF-AT-04-1 | `daemon.Daemon.run`: elección flock heredada de 1.1 + PID_FILE informativo escrito solo por el dueño (`audio._write_player_locks`); `_shutdown` limpia socket/pid/lock solo mientras posee el canal (`cleanup_locks` consciente de propiedad) | `test_daemon.py::test_shutdown_command_stops_daemon_and_frees_channel`; salidas idle de `test_daemon_lifecycle.py` liberan el canal |
| RF-AT-04-2 | `Daemon.handle_command` delega los comandos existentes en `active_session.handle_ipc_command` sin cambio de protocolo; sin sesión, respuestas claras (`status=idle ...`, `stop` idempotente, ERR para el resto) | `test_existing_commands_delegate_to_active_session`, `test_idle_commands_without_session_answer_clearly`, `test_status_during_playback_extends_session_status_with_uptime` |
| RF-AT-04-3 | `play <payload-json>` (`Daemon._handle_play`, responde `status=done/stopped/ERR` al terminar), `ping` (versión + uptime), `shutdown` | `test_play_*`, `test_ping_*`, `test_shutdown_*` en `test_daemon.py` |
| RF-AT-04-4 | **RETIRADA** — absorbida por RF-AT-09-1/RF-AT-09-2 (BLOQUE 1.1): elección atómica por flock, huérfano reclamado solo tras ganar, colisión resuelta perdiendo la elección. Sin código nuevo en 1.2 | Cobierta por la suite de 1.1 (`test_ipc_ownership.py`); el checkpoint de PID_FILE (marcador informativo, no consumido por herdr-tts) quedó resuelto por el código mergeado de 1.1 |
| RF-AT-04-5 | `daemon.probe_daemon` (puerta de 200 ms, clasificación ok/unreachable/wedged/foreign) + `ensure_daemon` (auto-arranque implícito detached, re-verificación, error explícito `DaemonUnavailableError`); `cli._delegate_and_exit` — no existe camino CLI clásico | `test_client_delegation.py::test_ensure_daemon_*`, `test_cli_*`; `test_ping_gate_is_200_ms` |
| RF-AT-04-6 | `Daemon.startup_target` (AGENT_TTS_PLAYBACK al arranque) + `_request_target` por payload (un evento fuerza target; valor inválido degrada a local con aviso) | `test_playback_target_resolved_at_startup_from_env`, `test_invalid_requested_target_fails_open_to_local` |
| RF-AT-04-7 | `Daemon.idle_timeout_sec` + `autostart_idle_timeout_sec()` (AGENT_TTS_IDLE_TIMEOUT, 30 min por defecto, 0 deshabilita) + `--idle-timeout` en ambos modos; sin timeout por defecto en arranque explícito | `test_daemon_lifecycle.py` completo (expiración ordenada, reloj reseteado por solicitudes, playback activo bloquea la salida, explícito sobrevive la ventana, wiring de flags) |
| RF-AT-04-8 | `probe_daemon` detecta wedged (canal vivo, ping sin respuesta); `_kill_wedged_daemon` (PID_FILE + sanity de cmdline antes del SIGKILL) + respawn antes de delegar | `test_wedged_daemon_is_killed_and_respawned_before_delegating` (proceso real wedgeado, SIGKILL, reclamación del huérfano por el respawn), `test_probe_wedged_when_channel_alive_but_silent`, `test_control_command_recovers_from_wedged_daemon` |
| RNF-AT-04-1 | `scripts/bench_daemon_vs_spawn.py` (medición reproducible) | Medición stub registrada abajo; medición real con edge pendiente de entorno con proveedor y dispositivo |
| RNF-AT-04-2 | `scripts/daemon_smoke.py` reporta RSS en reposo | 43,8 MB en reposo sin modelo local (real, límite 80 MB en verde); cota kokoro < 700 MB pendiente de entorno con kokoro instalado |
| RNF-AT-04-3 | kill-and-respawn (RF-AT-04-8) + reintento + `DaemonUnavailableError` con mensaje único que nombra el log del daemon (`AGENT_TTS_DAEMON_LOG`) | `test_ensure_daemon_fails_with_clear_error_when_start_fails`, `test_wedged_daemon_is_killed_and_respawned_before_delegating` |
| RNF-AT-04-4 | `scripts/daemon_smoke.py` (duración configurable; muestrea RSS/ping/status de un daemon de arranque explícito real) + `tests/test_daemon_smoke.py` (modo corto CI) | Modo corto en verde (0,0% de crecimiento en la ventana); la corrida completa de 8 h queda pendiente de ejecución manual/CI nocturna — condición explícita del DoD |
| RNF-AT-04-5 | `ProviderCache` (instancias vivas por configuración de proveedor; no evicción) + parámetro `engine` en `synthesize`/`_speak_pipelined` (comportamiento por defecto intacto) | `test_provider_cache_reuses_instance_per_configuration`, `test_daemon_reuses_warm_engine_across_plays` |
| US-AT-04-1 | Daemon persistente con caché de proveedores: la notificación delega `play` sin pagar arranque de Python por evento | Cobertura de RNF-AT-04-5 + el bench frente a spawn |
| US-AT-04-2 | Delegación transparente: la misma invocación con daemon corriendo y tras matarlo produce el mismo comportamiento observable | `test_same_invocation_behaves_identically_with_and_without_daemon` |
| US-AT-04-3 | `status` extendido con uptime (inyectado antes del campo de texto libre) y `status=idle uptime=... provider=... playback=...`; la cola pendiente llega con RF-AT-08-5 en 1.3 (punto de montaje `Daemon.active_session` ya reservado) | `test_idle_status_reports_idle_uptime_provider_and_target`, `test_status_during_playback_extends_session_status_with_uptime`, `test_inject_daemon_fields_*` |

### Mediciones registradas

- **RNF-AT-04-1 (stub, etiquetada)**: warm-daemon evento-a-audio p95 = **0,7 ms** frente a spawn CLI p95 = **162,3 ms** → **reducción del 99,6%** (métrica de éxito ≥ 40%: en verde). Entorno: worktree, Python 3.14, 30 eventos, texto de 46 chars, síntesis y dispositivo stubbed — el delta mide el coste real de spawn de intérprete+imports que el daemon elimina más la ronda IPC real. **Pendiente**: medición con edge real (y kokoro) en máquina con proveedor y dispositivo de audio.
- **RNF-AT-04-2 (real, sin modelo local)**: RSS en reposo del daemon de arranque explícito = **43,8 MB** < 80 MB (en verde). **Pendiente**: cota < 700 MB con kokoro caliente.
- **RNF-AT-04-4 (modo corto, real)**: 4 s/1 s de muestreo: 0,0% de crecimiento de RSS, ping estable, `status=idle` coherente en cada muestra. **Pendiente**: corrida completa de 8 h (manual o CI nocturna) con registro del delta.

### Notas de cierre

- La suite completa: **472 passed / 10 skipped** (base del worktree: 420/10; el brief esperaba ~421/9 — la diferencia de un skip es dependiente de plataforma, no una regresión).
- Punto de congelación del contrato IPC: sigue siendo el cierre del BLOQUE 1.3; 1.2 añade `play`/`ping`/`shutdown` de forma aditiva.
- Los flags de vista interactiva (`--highlight`/`--zen`/`--autoscroll`/`--bionic`) viajan en el payload y se renderizan donde corre el daemon (útiles con `--foreground`; inertes en un daemon detached) — documentado en README.
- `--no-daemon` nunca existió en el código (la revisión vía única de 22/09/2026 retiró el concepto antes de implementarlo); las menciones en las PRDs son registro histórico de la decisión. No quedó superficie que limpiar en código ni tests.
- Unit de systemd user de ejemplo: `docs/examples/agent-tts.service` (arranque explícito `--foreground`, sin idle timeout: el supervisor es el dueño del ciclo de vida).
