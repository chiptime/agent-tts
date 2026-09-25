# BLOQUE 1.1 — Canal de control: propiedad del socket, lock y framing (AT-09)

**Alcance**: PRD-AT-09 · **Prioridad**: P1 · **Esfuerzo**: S-M
**Repositorio**: agent-tts · **Estado**: Aprobado (reestructuración vía única 22/09/2026)

> Capa de orquestación del primer sub-bloque del paquete P1 (`BLOQUE-1-paquete-motor.md`). El detalle funcional y no funcional vive en `../AT-09-canal-de-control.md`, referenciado aquí por su identificador (RF/RNF/US).

### Objetivo del sub-bloque

Corregir el bug de concurrencia que existe hoy en el canal de control y dejar el suelo firme sobre el que se construye el daemon del BLOQUE 1.2. Es la corrección de bug más el suelo del paquete: cero cambio de comportamiento observable para el usuario (RNF-AT-09-1) y ninguna dependencia dura (AT-09 no depende de ninguna otra PRD).

### El bug que justifica este sub-bloque

Reproducible hoy, sin ningún daemon involucrado: el proceso A reproduce y bindea el socket; el proceso B arranca, elimina sin condiciones el socket de A (src/agent_tts/ipc.py:41-47) y bindea el suyo; los comandos de control llegan ahora a B mientras A sigue sonando y queda permanentemente incontrolable; al salir B, `cleanup_locks` (src/agent_tts/audio.py:37-44) elimina el socket. LOCK_FILE no evita nada de esto porque es un marcador best-effort que nadie valida (src/agent_tts/audio.py:47-55; no existe comprobación de vida en todo src/). La corrección no puede esperar al daemon: el bug es independiente de él y su PRD (AT-09) nació desgajada de AT-04 precisamente por eso.

### Alcance (cita a PRD fuente)

- RF-AT-09-1: elección atómica de propiedad por `flock` sobre LOCK_FILE; el kernel libera el lock a la muerte del proceso, incluido SIGKILL.
- RF-AT-09-2: `server_socket` deja de eliminar incondicionalmente un socket existente; el huérfano se reclama solo tras ganar el flock.
- RF-AT-09-3: `cleanup_locks` consciente de propiedad, incluida la limpieza de transporte de `IPCServer.stop()`.
- RF-AT-09-4: framing por línea en el servidor con tope simétrico al MAX_REPLY_BYTES del cliente (8192 bytes).
- RF-AT-09-5: paridad en Windows para la propiedad del marcador IPC_PORT_FILE.
- RNF-AT-09-1, RNF-AT-09-2, RNF-AT-09-3.
- US-AT-09-1 (el recién llegado nunca roba el canal del que está sonando), US-AT-09-2 (crash del dueño, incluso SIGKILL, deja el canal reclamable), US-AT-09-3 (framing simétrico para el payload JSON de una línea de AT-04).

Fuera de alcance (sección correspondiente de AT-09): el daemon, la cola, cambios de protocolo o comandos nuevos, el lado cliente del framing (ya implementado), la política del perdedor de la elección (se define en AT-04) y la autenticación del canal.

### Criterios de aceptación

- El escenario A/B del bug deviene imposible: test de regresión que reproduce el problema (A reproduciendo y dueño; B arranca) y verifica que los comandos de control siguen llegando a A hasta el final y que B jamás expone el canal mientras A vive (US-AT-09-1).
- Recuperación sin intervención tras `kill -9` del dueño: el siguiente arranque gana la elección y reclama el canal, por test (US-AT-09-2, RNF-AT-09-3).
- Cero regresiones en la suite IPC/playback actual (RNF-AT-09-2).
- Cero cambio de comportamiento observable: mismos comandos, mismas respuestas, mismos ficheros en las mismas rutas, verificado por la suite existente (RNF-AT-09-1).
- Test de contrato que ejecuta el mismo escenario de elección sobre ambos transportes (AF_UNIX y TCP loopback) para cubrir la paridad de RF-AT-09-5.

### Dependencias y prerrequisitos

- Ninguna dependencia dura: AT-09 no depende de ninguna otra PRD. Es prerrequisito duro de AT-04 y, por tanto, del BLOQUE 1.2: no puede construirse un daemon al que todo cliente delega sobre un canal que cualquier proceso puede robar con un `os.remove`.
- Prerrequisito de proceso: suite de tests IPC/playback actual en verde antes de empezar; es la red de regresión que exige RNF-AT-09-2.

### Superficies compartidas y zonas de conflicto

- **LOCK_FILE/PID_FILE**: el lock pasa de marcador best-effort a mutex real por `flock` (RF-AT-09-1). PID_FILE queda como dato (AT-09 deja abierta la pregunta de si se conserva con valor informativo para consumidores externos).
- **`server_socket` (ipc.py)**: rama POSIX (AF_UNIX) y rama Windows (puerto TCP efímero persistido en IPC_PORT_FILE); la ramificación existente es el precedente de RF-AT-09-5.
- **`cleanup_locks` y `IPCServer.stop()` (audio.py, ipc.py)**: ambas limpiezas pasan a borrar solo lo que el proceso posee (RF-AT-09-3).
- **`_listen_loop` (ipc.py)**: pasa del único `recv(1024)` al framing por línea con tope de 8192 bytes (RF-AT-09-4), cerrando la mitad servidor de un cambio cuyo lado cliente ya existe.

### Riesgos

Heredados de AT-09, con su mitigación:

- **`flock` no fiable en filesystems de red (p. ej. NFS)**: LOCK_FILE vive por defecto en el directorio temporal local; la localidad se documenta como requisito del override por entorno.
- **Ficheros que antes se borraban quedan huérfanos en disco**: son inertes por diseño (el socket huérfano se detecta por fallo de conexión; el pid no tiene poder de mutex) y la suite lo cubre.
- **Divergencia semántica POSIX/Windows**: test de contrato del mismo escenario de elección sobre ambos transportes.

### Definición de done

1. Criterios de aceptación anteriores medidos y registrados (tests de regresión del escenario A/B y de `kill -9` en verde).
2. Suite IPC/playback en verde.
3. Trazabilidad del sub-bloque: RF-AT-09-1 a RF-AT-09-5, RNF-AT-09-1 a RNF-AT-09-3 y US-AT-09-1 a US-AT-09-3 cubiertos; ninguna pieza de AT-09 queda fuera, con la excepción registrada en el punto 5.
4. Repositorio estable: el BLOQUE 1.2 puede arrancar sobre este suelo.
5. **Cobertura pendiente y aplazada — primitiva de bloqueo en Windows (RF-AT-09-5)**: el criterio de aceptación del test de contrato cubre la mitad de *transporte* (la rama Windows de `server_socket` se ejerce desde Linux parcheando `_is_windows`), pero **no** la mitad de *primitiva*: `msvcrt.locking` con `LK_NBLCK` sobre un byte centinela en el offset 4096 de `IPC_PORT_FILE` no se ejecuta nunca fuera de Windows, y el job Windows de CI es `continue-on-error`, así que tampoco avisa. Decisión (22/09/2026): se aplaza a una verificación posterior; el bloque cierra con esta cobertura explícitamente pendiente en lugar de darla por satisfecha. Verificación requerida antes de confiar en el canal bajo Windows: smoke manual de elección concurrente en Windows real. La elección entre `LockFileEx` y un named mutex sigue siendo open question de AT-09; se implementó la variante de bloqueo de fichero por ser la lectura literal de RF-AT-09-5.

### Referencias

- `../AT-09-canal-de-control.md` — PRD fuente: RF/RNF/US de detalle, riesgos y métricas.
- `BLOQUE-1-paquete-motor.md` — paraguas del paquete P1: secuencia y trazabilidad global.
