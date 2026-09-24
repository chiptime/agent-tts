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

---

## Hito Cola — implementation traceability and registered measurements (T5, 24/09/2026)

> Section written in English per unit instructions; structure follows the
> BLOQUE 1.2 traceability section. Scope: the **Hito Cola** acceptance
> criteria, measured and registered. The **Hito Cadena** (`--play-chain`)
> is not covered here — its DoD metrics remain pending for the chain unit.

Implementation landed in `feat/at-08-cola-y-cadena` (T1–T4): framing v2
wire + typed replies (`ipc.py`), the in-daemon `QueueManager` with
priority/policies/coalescing and the RS-5 watchdog (`queue_manager.py`),
the `enqueue`/`status` IPC surface (`daemon.py`), and the CLI
priority/policy flags with error discipline (`cli.py`). T5 adds the
contract tests, the measurement harness, and this registration.

| Requirement | Implementation | Verification |
|---|---|---|
| RF-AT-08-1 | `Priority` (blocked > done > working) wired from CLI `--priority` and the `enqueue` payload; FIFO per level by arrival seq | `test_queue_ipc.py::test_status_exposes_pending_priorities_in_dispatch_order`; class order + FIFO-by-item-id asserted over the 50-event simulation |
| RF-AT-08-2 | `Policy` (preempt/queue/coalesce) per event; preempt only from strictly higher priority, else degrades to queue; no busy rejection (D4) | `test_queue_manager.py` (T2); `test_queue_milestone.py::test_blocked_preempts_playing_done_and_reaches_speaker_complete` |
| RF-AT-08-3 | Coalescing window anchored at the first pending item per event type (5 s default, `--coalesce-window`/`AGENT_TTS_COALESCE_WINDOW`); one synthesized announcement (≤3 identifiers + count) | `test_coalesce_burst_of_10_done_is_one_announcement`; measured 10:1 below |
| RF-AT-08-5 | `status` carries `queue_len=` + `queue=<snapshot JSON>` (whitespace `\uXXXX`-escaped); `--ipc-json` for machine output | `test_queue_ipc.py` status tests; the harness itself polls `status` |
| RF-AT-08-6 | Single active slot in `QueueManager`; session unmounted before finalize→dispatch (order is load-bearing in the runner worker) | 50-event simulation: 0 overlaps on both targets (below) |
| RNF-AT-08-1 | Event-driven dispatch: the finalize callback dispatches inline on the finishing thread | Measured: max 0.086 ms « 50 ms (below) |
| RNF-AT-08-2 | Blocked preempt cuts the active done and dispatches immediately (see fix 2 below) | Measured: max 0.204 ms « 1500 ms (below) |
| RNF-AT-08-3 | Same queue semantics per target; winhost v1 **excluded by design** (see below) | Contract test same sequence → same order, local and wsl-ps (below) |
| US-AT-08-1 | Blocked never trampled: strictly higher priority preempts; blocked items always play complete | Simulation: 10/10 blocked completed, 0 interrupted on both targets |
| US-AT-08-2 | Ten simultaneous `done` → ONE coalesced announcement | Measured 10:1, `coalesced=10` recorded in the enqueue replies |
| US-AT-08-4 | `enqueue` via IPC with priority + queue visibility in `status` | `test_queue_ipc.py` (T3); harness drives everything through real `enqueue` IPC |

### Registered measurements (DoD — Hito Cola)

Method (how every number below was obtained — observable, not
tautological): the harness `scripts/queue_metrics.py` drives a REAL
daemon subprocess over the real IPC channel. Every event carries a
label; the daemon prints lifecycle trace lines on stderr
(`agent-tts-queue: dispatch|finalize|audio-start|audio-end`, added in
T5) with `CLOCK_MONOTONIC` timestamps, which are comparable across
processes on Linux; enqueue timestamps are taken client-side
immediately before the IPC send. *Dispatch* = `QueueManager` runner
invocation (item leaves pending); *finalize* = the audio path fully
ended (printed immediately before the queue finalizes, so
finalize→next-dispatch is exactly the RNF-AT-08-1 budget); *speaker
intervals* = entry/exit of the session play seam per item. Fidelity:
synthesis is stubbed inside the daemon (engine returns the text bytes;
decode maps them to deterministic silent PCM); the **local** target
stubs the audio device at `AudioSession.play` (no audio device exists
in the measurement WSL environment — same labeling discipline as the
1.2 bench); **wsl-ps performs REAL PowerShell playback** (a real
powershell.exe per announcement, silent PCM payloads). For wsl-ps,
"audio-start" is the play-seam entry (immediately before the Windows
process spawn); the Windows-side spawn-to-sound latency lives inside
the play call and is not separately observable from Linux.

| Metric | Threshold | local | wsl-ps |
|---|---|---|---|
| RF-AT-08-6 audio overlaps (50-event simulation) | 0 | **0** (50 intervals) | **0** (50 intervals, real playback) |
| Blocked items played complete | 100% | **100%** (10/10; queue counters: 51 completed, 0 failed, 0 interrupted) | **100%** (10/10; 51/0/0) |
| RNF-AT-08-2 blocked → dispatch (preempt probe: long `done` playing + busy queue) | < 1500 ms | **0.177 ms** (play seam 0.619 ms) | **0.204 ms** (play seam 1.179 ms) |
| RNF-AT-08-2 (supplementary, queue policy: short `done` playing + fillers) | info | 231.0 ms (waits out the current item) | 664.4 ms |
| US-AT-08-2 coalescing (burst of 10 `done`) | ≥ 3:1 | **10:1** — 1 announcement, `coalesced=10` recorded | **10:1** |
| RNF-AT-08-1 queue dispatch (finalize → next dispatch, n=50) | < 50 ms | **max 0.045 / p95 0.044 ms** | **max 0.086 / p95 0.047 ms** |
| RNF-AT-08-1 (supplementary: audible-seam handoff, prev play-exit → next play-entry) | info | max 0.75 ms | max 9.52 ms |
| RNF-AT-08-3 contract: same sequence → same resulting order | equal | `S-B1 S-B2 S-B3P S-D1 S-C1 S-D2 S-W1` | **identical** |

The full 50-event simulation ran on BOTH targets (no reduction): the
wsl-ps leg performed 50 real PowerShell announcements plus the probe,
coalesce and contract scenarios in one daemon lifetime. The simulation
anchors the speaker with one holding item, fires all 50 events from 10
concurrent client threads (observing `queue_len=50` before release),
then lets the queue drain: class order (blocked → done → working) and
FIFO-by-arrival within each class are asserted from the recorded
speaker order and enqueue-granted item ids.

Environment: WSL2, kernel 6.6.87.2-microsoft-standard-WSL2, 28 CPUs,
Python 3.14.7, 2026-09-24 (UTC dates in the records). Reproduce with:

    PYTHONPATH=src .venv/bin/python scripts/queue_metrics.py --target local
    PYTHONPATH=src .venv/bin/python scripts/queue_metrics.py --target wsl-ps

(the harness prefixes the daemon's PATH with the Windows PowerShell
directory itself). Machine-readable records: `metrics/queue/
queue-metrics-local-20260924T210808Z.json` and `metrics/queue/
queue-metrics-wsl-ps-20260924T210739Z.json`.

CI enforcement (timing-free by flake discipline — thresholds live in
the harness, structure lives in the suite): `tests/test_queue_milestone.py`
adds the 50-event zero-overlap simulation, blocked completeness, the
10→1 coalescing burst, the preempt story, and the order contract on
local; the wsl-ps contract leg runs the SAME canonical sequence
(`SCRIPTED_SEQUENCE`, shared with the harness by import so CI and
registered evidence cannot drift) with REAL PowerShell playback under
the same skip condition as `tests/test_powershell_playback.py`.

**winhost v1 exclusion (by design, per zonas de conflicto):** winhost
v1's "un PLAY nuevo pisa al actual" contradicts queue semantics; the v2
decision is an open question deferred for the whole package. Contract
tests and the 0-overlap metric are scoped to local + wsl-ps; nothing
winhost-related was added or measured.

### Fixes made during T5 (reported, not hidden)

1. **`IPCServer` listen backlog 5 → 128** (`ipc.py`): the 50-event
   concurrent arrival (10 parallel enqueue connections) could hit
   transient connection refusals at backlog 5, making the DoD scenario
   itself unmeasurable. One-line fix; per-connection threading is
   unchanged.
2. **`AudioSessionHandle.terminate` now sets the session stop flag
   before `stop()`** (`queue_manager.py`): without it, a preempted
   wsl-ps announcement took the *drain* path
   (`PowershellSession.stop` only hard-stops when the flag is already
   set) — preemption waited out the whole playing group (seconds),
   blocking the preempting enqueue past the 1 s IPC client timeout and
   making RNF-AT-08-2 unmeasurable on wsl-ps. A queue-side termination
   (preempt or watchdog) semantically IS a cut; now it hard-stops on
   wsl-ps and is a no-op change for the local target.
3. **Daemon queue trace lines** (`daemon.py`, `_queue_trace`): two
   stderr lines per item (dispatch/finalize) — the only addition to the
   daemon, needed because polling `status` cannot observe finalize
   timestamps or short-lived items. Audio-start/end lines are printed by
   the harness launcher's play-seam instrumentation, not by the daemon.

### Notes and known noise

- On wsl-ps the runner-level finalize outcome of a NATURAL end reads
  `stopped`: `PowershellSession.finish()` sets the session stop flag by
  design while draining. The queue records both `completed` and
  `stopped` runner outcomes as normal ends — the simulation's queue
  counters (51 completed / 0 failed / 0 interrupted) are the ground
  truth, and the harness counts accordingly.
- A preempted wsl-ps session logs `Playback error: PowerShell playback
  failed (exit code -9)` on stderr: the killed session's losing
  natural-finish report. The queue's id guard ignores it (the interrupt
  finalization won); it is cosmetic noise, not a failure.
- The blocked+queue-policy probe (supplementary) deliberately shows the
  semantic boundary: a queue-policy blocked event waits for the current
  item's natural end even with pending fillers behind it (231–664 ms
  with a 250 ms item). The < 1.5 s DoD story is the preempt probe — the
  configuration a critical blocked event actually uses.
- Suite: **722 passed / 11 skipped** default (baseline 718/10 + 4 new
  local milestone tests + 1 platform-dependent wsl-ps skip), and
  **724 passed / 9 skipped** with the PowerShell PATH prefix (both
  wsl-ps legs live). All green.

### Pending for the Hito Cadena (T6)

- `--play-chain` end-to-end and its DoD metrics: gapless item-to-item
  dispatch (< 50 ms, same criterion), controls (seek/pause/phrases) over
  the combined `BoundaryMap`, chain-as-one-queue-item policy.
- Contract freeze communication to herdr-tts (HT-03/HT-10) at sub-bloque
  closure.
