# BLOQUE 1.3 — Cola con prioridades y reproducción encadenada (AT-08)

**Alcance**: PRD-AT-08 · **Prioridad**: P1 · **Esfuerzo**: M-L
**Repositorio**: agent-tts · **Estado**: Aprobado (reestructuración vía única 22/09/2026)

> Capa de orquestación del tercer sub-bloque del paquete P1 (`BLOQUE-1-paquete-motor.md`). El detalle funcional y no funcional vive en `../AT-08-cola-prioridades.md`, referenciado aquí por su identificador (RF/RNF/US).

### Objetivo del sub-bloque

Entregar la cola con prioridades y la reproducción encadenada gapless sobre el daemon del BLOQUE 1.2, en dos hitos internos: primero la Cola (scheduling multi-evento) y después la Cadena (`--play-chain`). Todo llega por la vía única: `--play-chain` y los flags de prioridad se usan como cualquier otro comando, delegando siempre en el daemon; no existe un modo sin daemon que diseñar ni testear (RNF-AT-08-4 retirada en AT-04, vía única con auto-arranque transparente).

### Hito Cola — cola con prioridades sobre el daemon

Alcance (detalle en `../AT-08-cola-prioridades.md`):

- RF-AT-08-1: `--priority blocked|done|working` en CLI y campo `priority` en `enqueue` IPC; mapeo por defecto documentado y sobreescrible.
- RF-AT-08-2: política por evento configurable (`--policy preempt|queue|coalesce` y equivalente IPC): `preempt` solo se permite desde prioridad estrictamente superior; iguales o menores hacen `queue`.
- RF-AT-08-3: coalescing de ítems coalescibles encolados dentro de una ventana configurable (AT-08 la ejemplifica en 5 s), fundidos en un anuncio único que resume cantidad y tipo de evento; la fusión es visible en `status` antes de sonar.
- RF-AT-08-5: `status` expone `queue_len`, cola pendiente con prioridades y `coalesced=N` cuando aplica (completa el `status` de US-AT-04-3).
- RF-AT-08-6: la reproducción activa nunca se solapa con otra; la exclusión mutua actual se conserva como invariante.
- RNF-AT-08-2: un `blocked` encolado mientras suena un `done` alcanza el altavoz en menos de 1.5 s desde su evento incluso con cola ocupada.
- RNF-AT-08-3: la semántica se respeta por playback target: local y wsl-ps completos; el tramo winhost v2 queda aplazado (véase riesgos y aplazados del paraguas).
- US-AT-08-1 (un `blocked` crítico nunca pisado por un `done` trivial), US-AT-08-2 (diez `done` simultáneos como un único anuncio coalescido), US-AT-08-4 (`enqueue` via IPC con prioridad y consulta de la cola en `status`).
- El `QueueManager` vive dentro del proceso daemon, por encima de `AudioSession`, en el punto de montaje que el BLOQUE 1.2 dejó preparado.

Criterios de aceptación:

- Simulación de 50 eventos concurrentes con 0 solapamientos de audio, medida en los targets bajo test de contrato (local y wsl-ps); winhost v1 queda fuera de esa métrica con la limitación documentada (RNF-AT-08-3, véase zonas de conflicto).
- 100% de eventos `blocked` reproducidos completos (no pisados) en esa simulación; un `blocked` encolado con cola ocupada alcanza el altavoz en menos de 1.5 s desde su evento (RNF-AT-08-2), medido.
- Ratio de coalescing igual o mayor a 3:1 en ráfagas de `done` (10 eventos, una sola locución), medido.
- Despacho de cola: el siguiente ítem sale en menos de 50 ms tras finalizar la reproducción activa (RNF-AT-08-1, aplicación de cola), medido.
- Tests de contrato: la misma secuencia de eventos produce el mismo orden resultante en los targets bajo contrato (local y wsl-ps; winhost v1 queda fuera con la limitación documentada más arriba en este mismo hito).

### Hito Cadena — `--play-chain`: reproducción encadenada gapless

Alcance (detalle en `../AT-08-cola-prioridades.md`):

- RF-AT-08-4: `--play-chain FILE...` reproduce los ficheros en orden con silencio intermedio configurable (default 0 ms) en una sola sesión de audio, reutilizando `merge_chunks_to_audio` y la composición de `BoundaryMap` del streaming; seek, pausa y navegación por frases operan sobre la cadena completa (`BoundaryMap` combinada).
- RNF-AT-08-1 (aplicación de cadena): el despacho del siguiente ítem de la cadena tras finalizar el anterior se mide con el mismo criterio que el despacho de cola.
- US-AT-08-3 (`agent-tts --play-chain a.mp3 b.mp3` con transición sin hueco audible).
- En la vía única, la cadena entra en la cola del daemon como un ítem y se despacha con la misma política que el resto.

Criterios de aceptación:

- Transición entre ítems sin hueco audible, con despacho del siguiente ítem en menos de 50 ms tras finalizar el anterior (RNF-AT-08-1, aplicación de cadena), medido.
- Operación de los controles (seek/pausa/frases) sobre la cadena completa verificada por test.
- La cadena encolada respeta la política del hito Cola (RF-AT-08-2) y el invariante de RF-AT-08-6 se conserva.

### Dependencias y prerrequisitos

- Dependencia: BLOQUE 1.2 (AT-04). La cola multi-evento necesita el proceso dueño persistente; AT-08 declara AT-04 como dependencia recomendada fuerte y el paquete la convierte en orden interna de ejecución. El hito Cadena no depende del `QueueManager` completo — es encadenamiento dentro de una sesión de playback — pero aterriza después de la Cola para consumir su política de despacho.
- Prerrequisitos sobre el código actual, citados en el encaje de AT-08: el dispatch IPC (`AudioSession.handle_ipc_command`) extendido por 1.2, `merge_chunks_to_audio` y la composición de `BoundaryMap` ya existente en streaming.
- Prerrequisito de proceso: BLOQUE 1.2 cerrado con su definición de done cumplida.

### Superficies compartidas y zonas de conflicto

- **Dispatch IPC**: este sub-bloque añade `enqueue` y los campos de cola en `status` sobre los `play`/`ping`/`shutdown` de 1.2; adición aditiva y no rompente para clientes existentes. El cierre de este sub-bloque es el punto de congelación del contrato IPC del paquete (véase el paraguas): herdr-tts construye HT-03/HT-10 sobre `enqueue` + `--play-chain` desde aquí.
- **winhost v1**: "un PLAY nuevo pisa al actual" contradice la semántica de cola. La decisión v2 (¿cola en el server Windows o encolado en cliente pre-send?) es open question de AT-08 y está aplazada para todo el paquete; la limitación se documenta y los tests de contrato de este sub-bloque no exigen winhost — por eso la métrica de 0 solapamientos está acotada a local y wsl-ps en el propio hito Cola.

### Riesgos

- **Inanición de `working` por lluvia de `blocked`.** Mitigación: envejecimiento de prioridad documentado como RFC posterior (riesgo de AT-08); la política por evento deja la extensión preparada. No es una open question de este bloque.
- **Coalescing engañoso** ("tres agentes" cuando eran cinco paneles). Mitigación heredada de AT-08: el anuncio coalescido lista hasta tres identificadores y resume el resto.
- **Divergencia semántica local/winhost.** Mitigación: tests de contrato que ejecutan la misma secuencia de eventos en los targets bajo contrato (local y wsl-ps) y comparan el orden resultante; el tramo winhost v2 aplazado mantiene el contrato visible (un stream, sin solapes).

### Definición de done

1. Criterios de aceptación de los dos hitos medidos y registrados: solapes (0, acotado a los targets bajo test de contrato), `blocked` completos (100%), ratio de coalescing, despacho de cola y despacho entre ítems de la cadena (ambos bajo el criterio de RNF-AT-08-1).
2. Suite IPC/playback en verde, en la vía única.
3. Trazabilidad del sub-bloque: RF-AT-08-1 a RF-AT-08-6, RNF-AT-08-1, RNF-AT-08-2, RNF-AT-08-3 y US-AT-08-1 a US-AT-08-4 cubiertos según la tabla del paraguas (RNF-AT-08-4 retirada en la PRD fuente).
4. Contrato IPC congelado y comunicado a herdr-tts como base de HT-03/HT-10 (punto de congelación definido en el paraguas del paquete).

### Referencias

- `../AT-08-cola-prioridades.md` — PRD fuente: RF/RNF/US de detalle, riesgos y métricas.
- `BLOQUE-1-paquete-motor.md` — paraguas del paquete P1: secuencia, trazabilidad global y punto de congelación del contrato IPC.
- `BLOQUE-1.2-daemon-via-unica.md` — prerrequisito: el dueño persistente de la cola.
