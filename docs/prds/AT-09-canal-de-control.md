**ID**: PRD-AT-09 · **Proyecto**: agent-tts
**Prioridad final (revisión 2026-09-22)**: P1 · **Estado**: Aprobada
**Dependencias**: Ninguna (prerrequisito duro de AT-04)

> **Nota de revisión (22/09/2026)**: Nace desgajada de AT-04: el bug de propiedad del canal existe HOY sin ningún daemon involucrado, así que su corrección se aísla en una PRD propia que no depende del paquete P1 y le sirve de suelo. Cero cambio de comportamiento visible para el usuario.

# PRD-AT-09 — Propiedad del canal de control (socket, lock y framing)

**Prioridad**: Alta · **Esfuerzo**: S-M

### Resumen ejecutivo

Hoy el canal de control se roba en lugar de ganarse: `server_socket()` elimina sin condiciones un socket existente antes de bindear y `cleanup_locks()` borra ficheros cuyo dueño es otro proceso. El resultado es un bug de concurrencia reproducible hoy mismo, sin daemon. Esta PRD convierte LOCK_FILE en un mutex real mediante elección atómica por `flock`, condiciona la limpieza y la reclamación del socket a ser dueño, y completa el framing por línea del lado servidor. Su valor es corregir el bug actual y dejar el suelo firme para AT-04; su coste visible para el usuario es cero.

### Problema y flujo actual

El mutex declarado es un marcador best-effort que nadie valida: `_write_player_locks()` (src/agent_tts/audio.py) escribe solo el pid en LOCK_FILE/PID_FILE y no existe comprobación de vida en todo src/ (ningún `os.kill(pid, 0)`). Sobre ese suelo, dos funciones borran sin preguntar: `cleanup_locks()` (src/agent_tts/audio.py) elimina incondicionalmente LOCK_FILE, PID_FILE e IPC_SOCKET — con seis call sites, incluidas la salida normal del CLI y el manejador de señal — y `server_socket()` (src/agent_tts/ipc.py) hace `os.remove` del path existente antes del bind; `IPCServer.stop()` repite el patrón con el marcador de transporte. Bug reproducible hoy, sin daemon: el proceso A reproduce y bindea el socket; el proceso B arranca, elimina el socket de A y bindea el suyo; los comandos de control llegan ahora a B mientras A sigue sonando y queda permanentemente incontrolable; al salir B, `cleanup_locks` elimina el socket. LOCK_FILE no evita nada de esto porque es un marcador que nadie valida. Además, el framing es asimétrico: el cliente ya lee hasta newline con tope MAX_REPLY_BYTES (8192), pero el servidor sigue en un único `recv(1024)`.

### Propuesta

El mutex pasa de marcador a elección atómica: obtener `flock` sobre LOCK_FILE es lo que convierte a un proceso en dueño del canal de control; el kernel libera el lock a la muerte del proceso, incluido SIGKILL, de modo que la propiedad nunca queda huérfana ni depende de terminación limpia. Sobre esa elección se ordenan las demás piezas: `server_socket` solo reclama un socket existente tras ganar el flock y tras detectar el huérfano por fallo de conexión (no por borrado preventivo); `cleanup_locks` se vuelve consciente de propiedad (un proceso solo borra lo que posee); y el servidor lee comandos con el mismo framing por línea y tope explícito que el cliente ya usa, que es lo que hace seguro un futuro payload de comando JSON en una línea. En Windows, donde no existe AF_UNIX y `server_socket` ya bindea un puerto TCP efímero persistido en IPC_PORT_FILE, la misma semántica de propiedad se aplica al marcador con el mecanismo equivalente de plataforma.

### Historias de usuario (US-AT-09-1, ...)

- US-AT-09-1: Como usuario que lanza dos procesos mientras uno reproduce, quiero que el recién llegado nunca robe el canal de control del que está sonando.
- US-AT-09-2: Como usuario, quiero que un crash del proceso dueño (incluso SIGKILL) deje el canal reclamable por el siguiente arranque, sin limpieza manual.
- US-AT-09-3: Como maintainer, quiero un framing simétrico cliente-servidor para que un payload de comando JSON de una línea (el `play` de AT-04) no pueda truncarse.

### Requisitos funcionales (RF-AT-09-...)

- RF-AT-09-1: Elección atómica de propiedad por `flock` sobre LOCK_FILE: el proceso que mantiene el flock es el dueño del canal de control; el kernel libera el lock a la muerte del proceso, incluido SIGKILL. Sustituye al pid best-effort como mutex.
- RF-AT-09-2: `server_socket` deja de eliminar incondicionalmente un socket existente: un socket huérfano se detecta por fallo de conexión y solo se reclama tras ganar el flock de RF-AT-09-1.
- RF-AT-09-3: `cleanup_locks` consciente de propiedad: un proceso solo elimina los ficheros del canal cuya propiedad mantiene (LOCK_FILE, PID_FILE e IPC_SOCKET — o IPC_PORT_FILE en Windows — en su forma actual); la limpieza de transporte de `IPCServer.stop()` sigue la misma regla.
- RF-AT-09-4: Framing por línea en el servidor con tope explícito simétrico al MAX_REPLY_BYTES del cliente (8192 bytes), sustituyendo el único `recv(1024)` del `_listen_loop`: leer hasta newline o tope. Es exactamente lo que hace seguro un futuro payload de comando JSON en una línea.
- RF-AT-09-5: Paridad en Windows: la misma semántica de propiedad para el marcador IPC_PORT_FILE usando el mecanismo equivalente de plataforma (bloqueo exclusivo del fichero), reutilizando la ramificación por plataforma que ya existe en `server_socket`.

### Requisitos no funcionales (RNF-AT-09-...)

- RNF-AT-09-1: Cero cambio de comportamiento observable para los usuarios actuales: mismos comandos, mismas respuestas, mismos ficheros en las mismas rutas.
- RNF-AT-09-2: Ninguna regresión en la suite IPC/playback existente.
- RNF-AT-09-3: La liberación de la propiedad no depende de terminación limpia: SIGKILL, crash o muerte del dueño dejan el canal reclamable por el siguiente arranque sin intervención manual.

### Encaje en la arquitectura actual

Las piezas ya existen y solo cambian sus invariantes: `_write_player_locks()` gana la llamada a `flock` (hoy escribe el pid y nada más); `cleanup_locks()` y `IPCServer.stop()` (que también borra el marcador de transporte sin comprobar propiedad) pasan a limpiar solo lo propio; `server_socket()` ya tiene la ramificación POSIX/Windows que sirve de precedente para RF-AT-09-5 (constants.py define IPC_PORT_FILE para el flujo TCP de Windows); y el cliente ya lee hasta newline con tope MAX_REPLY_BYTES, así que RF-AT-09-4 cierra la mitad que falta de un cambio ya empezado, no abre uno nuevo.

### Prior art y diferenciación

El prior art relevante no está en el ecosistema TTS — ninguno de los proyectos contrastados en las PRDs hermanas documenta la propiedad de su canal de control local — sino en la tradición de daemons Unix: flock sobre fichero de lock es el patrón estándar de mutex entre procesos porque la garantía la da el kernel, no la cooperación del proceso. La alternativa de socket activation (systemd) resuelve el mismo problema pero introduce una dependencia de sistema que esta PRD evita; queda fuera de alcance.

### Dependencias

Ninguna dura. Es prerrequisito de AT-04: no puede construirse un daemon al que todo cliente delega sobre un canal que cualquier proceso puede robar con un `os.remove`.

### Riesgos y mitigaciones

- Riesgo: flock no es fiable en filesystems de red (p. ej. NFS). Mitigación: por defecto LOCK_FILE vive en el directorio temporal local (constants.py); documentar la localidad como requisito del override por entorno.
- Riesgo: ficheros que antes se borraban quedan huérfanos en disco. Mitigación: son inertes por diseño — el socket huérfano se detecta por fallo de conexión y el pid carece de poder de mutex — y la suite lo cubre.
- Riesgo: divergencia semántica entre POSIX y Windows. Mitigación: test de contrato que ejecuta el mismo escenario de elección sobre ambos transportes (AF_UNIX y TCP loopback).

### Métricas de éxito

- El bug de concurrencia A/B deviene imposible: test de regresión que reproduce el escenario del problema (A reproduciendo y dueño; B arranca) y verifica que los comandos de control siguen llegando a A hasta el final y que B jamás expone el canal mientras A vive.
- Cero regresiones en la suite IPC/playback actual.
- Recuperación sin intervención tras `kill -9` del dueño: el siguiente arranque gana la elección y reclama el canal (test).

### Fuera de alcance

El daemon (AT-04) y la cola (AT-08); cambios de protocolo o comandos nuevos; el lado cliente del framing (ya implementado); la política del perdedor de la elección (esperar, degradar sin canal o fallar: se define en AT-04); autenticación del canal.

### Open questions

¿`flock` reemplaza por completo a PID_FILE, o este se conserva como dato informativo para consumidores externos (herdr-tts podría leerlo)? ¿Primitiva exacta en Windows para la paridad de RF-AT-09-5 (LockFileEx sobre el marcador frente a mutex nombrado)?
