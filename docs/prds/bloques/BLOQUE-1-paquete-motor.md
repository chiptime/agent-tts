# BLOQUE 1 — Paquete motor: canal de control, daemon vía única y cola con prioridades (AT-09 + AT-04 + AT-08)

**Alcance**: PRD-AT-09 + PRD-AT-04 + PRD-AT-08 · **Prioridad**: P1 · **Esfuerzo agregado**: S-M + L + M-L
**Repositorio**: agent-tts · **Estado**: Aprobado (reestructuración vía única 22/09/2026)

> Este documento es la capa de orquestación del paquete P1 definido en `../README.md`. No reescribe los requisitos de las PRDs fuente: el detalle funcional y no funcional vive en `../AT-09-canal-de-control.md`, `../AT-04-daemon-persistente.md` y `../AT-08-cola-prioridades.md`, referenciadas aquí por su identificador (RF/RNF/US). Su valor es la secuencia, la trazabilidad global y la definición de done del paquete; los criterios de aceptación de cada tramo viven en el documento de su sub-bloque y aquí solo se apuntan.

### Objetivo del paquete

Aterrizar el núcleo de salida del motor en tres sub-bloques secuenciales: primero un canal de control con propiedad real (AT-09), después un daemon persistente por vía única con auto-arranque transparente (AT-04), y por último la cola con prioridades y la reproducción encadenada sobre ese daemon (AT-08). Al cerrar el paquete, herdr-tts deja de spawnear un CLI por evento (latencia evento-audio p95 por debajo de 250 ms con daemon caliente), ningún `blocked` crítico vuelve a ser pisado por un `done` trivial, las ráfagas de `done` suenan como un único anuncio coalescido y el bug hoy reproducible de robo de canal de control deviene imposible. El paquete es además el prerrequisito directo del radio mode de herdr (HT-03/HT-10, véase la sección de contrato IPC más abajo).

### Por qué tres bloques (rationale de la secuencia)

La revisión contra el código real del 22/09/2026 descartó el camino dual daemon/modo clásico y con él la forma del paquete original. Lo que sigue es por qué la secuencia en tres sub-bloques, no una lista de ventajas de un solo merge:

1. **Límite de revisión por PR.** El proyecto acota cada PR a 400 líneas cambiadas. El bloque original (AT-04 en esfuerzo L más AT-08 en M-L, más el suelo de AT-09) superaba con mucho ese límite en un solo frente de revisión. Tres sub-bloques son tres fronteras de PR y de rollback independientes: cada uno se revisa, se mergea y, si hace falta, se revierte sin arrastrar a los demás.

2. **Perfiles de riesgo distintos y separables.** AT-09 es una corrección de bug con cero cambio observable (RNF-AT-09-1) que tiene valor por sí misma: el bug existe hoy sin ningún daemon involucrado. AT-04 es trabajo nuevo de ciclo de vida (caché de proveedores, health check, idle timeout; véase RNF-AT-04-5). AT-08 es semántica de scheduling sobre un dueño ya existente. Mezclarlos obligaba a revisar tres perfiles de riesgo en un solo diff.

3. **La semántica de playback migra exactamente una vez.** Es el argumento de secuenciación que sobrevive del rationale original, y lo expresa `../README.md`: "una sola migración de semántica de playback, no dos". El daemon del sub-bloque 1.2 se construye desde el primer commit como el dueño del estado de playback (el punto de montaje de la cola vive encima de `AudioSession` desde el día uno, aunque la cola entregue en 1.3): la semántica pasa de por-proceso-CLI a dueño-persistente una sola vez, y la cola de 1.3 encaja sobre ese dueño sin una segunda migración.

4. **La cola aterriza sobre un dueño de estado real.** Los tests de contrato de AT-08 ("la misma secuencia de eventos produce el mismo orden resultante en cada playback target") solo tienen sentido si existe un proceso que posee el estado de la cola. Sin daemon no hay nada contra lo que contratar; con el daemon de 1.2, el contrato se define una vez en 1.3.

Efecto conservado del paquete original: queda cerrada la open question de AT-04 (¿debería el daemon poseer la cola AT-08 desde el día uno?). Respuesta: sí, como arquitectura desde 1.2; la cola entrega en 1.3.

### Secuencia de ejecución: tres sub-bloques secuenciales

Orden estricto, dictado por la cadena de dependencias duras de las PRDs fuente (AT-09 es prerrequisito duro de AT-04; AT-08 necesita el dueño persistente de AT-04):

1. **BLOQUE 1.1 — Canal de control** (`BLOQUE-1.1-canal-de-control.md`, AT-09): corrección del bug de propiedad más el suelo de framing. Cero cambio de comportamiento observable.
2. **BLOQUE 1.2 — Daemon por vía única** (`BLOQUE-1.2-daemon-via-unica.md`, AT-04): el daemon con auto-arranque transparente y el trabajo de ciclo de vida.
3. **BLOQUE 1.3 — Cola y cadena** (`BLOQUE-1.3-cola-y-cadena.md`, AT-08): la cola con prioridades y `--play-chain` sobre el daemon, en dos hitos internos.

Regla dura del paquete: cada sub-bloque deja el repositorio estable y la suite IPC/playback en verde antes de que arranque el siguiente. No existe un segundo camino semántico que mantener en paralelo: la vía única es el único comportamiento bajo test desde 1.2 en adelante.

### Trazabilidad global

Tabla completa de cada RF, RNF y US de las tres PRDs fuente a su sub-bloque, retiro o aplazamiento. Los US se trazan con el mismo detalle que los RF/RNF; no queda ninguna historia de usuario fuera de la tabla.

| Requisito | Destino |
|---|---|
| RF-AT-09-1 | BLOQUE 1.1 |
| RF-AT-09-2 | BLOQUE 1.1 |
| RF-AT-09-3 | BLOQUE 1.1 |
| RF-AT-09-4 | BLOQUE 1.1 |
| RF-AT-09-5 | BLOQUE 1.1 |
| RNF-AT-09-1 | BLOQUE 1.1 |
| RNF-AT-09-2 | BLOQUE 1.1 (y verificación de no-regresión en 1.2 y 1.3) |
| RNF-AT-09-3 | BLOQUE 1.1 |
| US-AT-09-1 | BLOQUE 1.1 |
| US-AT-09-2 | BLOQUE 1.1 |
| US-AT-09-3 | BLOQUE 1.1 |
| RF-AT-04-1 | BLOQUE 1.2 |
| RF-AT-04-2 | BLOQUE 1.2 |
| RF-AT-04-3 | BLOQUE 1.2 |
| RF-AT-04-4 | Retirada: absorbida por RF-AT-09-1 y RF-AT-09-2 (BLOQUE 1.1) |
| RF-AT-04-5 | BLOQUE 1.2 |
| RF-AT-04-6 | BLOQUE 1.2 |
| RF-AT-04-7 | BLOQUE 1.2 (idle timeout por defecto: 30 min en auto-arranque implícito; sin timeout en arranque explícito) |
| RF-AT-04-8 | BLOQUE 1.2 |
| RNF-AT-04-1 | BLOQUE 1.2 |
| RNF-AT-04-2 | BLOQUE 1.2 |
| RNF-AT-04-3 | BLOQUE 1.2 |
| RNF-AT-04-4 | BLOQUE 1.2 |
| RNF-AT-04-5 | BLOQUE 1.2 |
| US-AT-04-1 | BLOQUE 1.2 |
| US-AT-04-2 | BLOQUE 1.2 |
| US-AT-04-3 | BLOQUE 1.2 (proveedor y uptime); la cola pendiente se completa en BLOQUE 1.3 con RF-AT-08-5 |
| RF-AT-08-1 | BLOQUE 1.3, hito Cola |
| RF-AT-08-2 | BLOQUE 1.3, hito Cola |
| RF-AT-08-3 | BLOQUE 1.3, hito Cola |
| RF-AT-08-4 | BLOQUE 1.3, hito Cadena |
| RF-AT-08-5 | BLOQUE 1.3, hito Cola |
| RF-AT-08-6 | BLOQUE 1.3, hito Cola; se conserva como invariante en el hito Cadena |
| RNF-AT-08-1 | BLOQUE 1.3: aplica al despacho de cola (hito Cola) y al despacho entre ítems de la cadena (hito Cadena); medido como criterio en ambos |
| RNF-AT-08-2 | BLOQUE 1.3, hito Cola |
| RNF-AT-08-3 | BLOQUE 1.3, hito Cola: local y wsl-ps completos; tramo winhost v2 aplazado (véase aplazados) |
| RNF-AT-08-4 | Retirada: no existe modo sin daemon (AT-04, vía única) |
| US-AT-08-1 | BLOQUE 1.3, hito Cola |
| US-AT-08-2 | BLOQUE 1.3, hito Cola |
| US-AT-08-3 | BLOQUE 1.3, hito Cadena |
| US-AT-08-4 | BLOQUE 1.3, hito Cola |

### Qué se aplaza deliberadamente en el paquete

- **Decisión de cola winhost v2** (¿cola en el server Windows o encolado en cliente pre-send?): open question de AT-08. winhost queda en protocolo v1 con la preemption documentada como limitación conocida; el contrato visible (un stream, sin solapes) no cambia y los tests de contrato cubren local y wsl-ps.
- **Envejecimiento de prioridad (aging)**: mitigación del riesgo de inanición de `working`, documentada como RFC posterior en AT-08; no es una open question del paquete ni se implementa aquí.
- **Ventana de coalescing adaptativa por cadencia de la flota**: la ventana es fija y configurable (AT-08 la ejemplifica en 5 s); la variante adaptativa queda como open question de AT-08.
- **Comando `reload` de kokoro sin reinicio**: open question de AT-04 ajena a los sub-bloques; si el modelo debe refrescarse, se reinicia el daemon.

Ninguno de estos aplazamientos bloquea 1.1, 1.2 ni 1.3.

### Contrato IPC: evolución y punto de congelación

La superficie IPC evoluciona a lo largo de los tres sub-bloques: 1.1 endurece el transporte (framing simétrico y propiedad; mismos comandos y mismas respuestas, RNF-AT-09-1), 1.2 añade `play`/`ping`/`shutdown` (RF-AT-04-3) y 1.3 añade `enqueue` y los campos de cola en `status` (RF-AT-08-1, RF-AT-08-5). Toda adición es aditiva y no rompente para los clientes existentes (RF-AT-04-2 exige cero cambios de protocolo para ellos).

Hay un único punto de congelación del contrato: **el cierre del BLOQUE 1.3, es decir, el cierre del paquete P1**. Es el primer momento en que la superficie consumida por herdr-tts existe completa, de modo que congelar antes sería congelar un contrato a medias o romper la congelación de inmediato. Mientras tanto, herdr-tts puede apoyarse en garantías intermedias estables: desde el cierre de 1.1, las garantías de transporte (framing, propiedad del canal, recuperación tras muerte del dueño); desde el cierre de 1.2, los comandos existentes más `play`/`ping`/`shutdown`. Desde el cierre de 1.3, el contrato completo — incluidos `enqueue` y el `status` extendido que consumen HT-03 (radio mode) y HT-10 (orquestación de audio) — queda congelado. Cerrar el paquete no exige cambios en herdr, pero el contrato congelado es la condición para construir HT-03/HT-10 sobre él.

### Definición de done del paquete

1. Trazabilidad completa RF/RNF/US a sub-bloque, retiro o aplazado, sin huecos (tabla anterior).
2. Cada sub-bloque cumple su propia definición de done, con sus criterios de aceptación medidos y registrados (viven en su documento; este paquete no los duplica).
3. Suite IPC/playback en verde al cierre de cada sub-bloque, en la vía única.
4. Contrato IPC congelado al cierre de 1.3 y comunicado a herdr-tts como base de HT-03/HT-10.
5. `../README.md` actualizado marcando el paquete P1 como completado.

### Referencias

- `BLOQUE-1.1-canal-de-control.md` — sub-bloque 1: AT-09, corrección del bug de propiedad y suelo del paquete.
- `BLOQUE-1.2-daemon-via-unica.md` — sub-bloque 2: AT-04, daemon por vía única con auto-arranque transparente.
- `BLOQUE-1.3-cola-y-cadena.md` — sub-bloque 3: AT-08, cola con prioridades y `--play-chain`.
- `../AT-09-canal-de-control.md`, `../AT-04-daemon-persistente.md`, `../AT-08-cola-prioridades.md` — PRDs fuente: RF/RNF/US de detalle, riesgos y métricas.
- `../README.md` — índice del roadmap y orden de ataque (paquete P1).
- herdr-tts HT-03 (radio mode) y HT-10 (orquestación de audio) — consumidores previstos de `enqueue` + `--play-chain`.
