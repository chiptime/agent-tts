# BLOQUE 1.3 — Cola con prioridades y reproducción encadenada (AT-08)

**Alcance**: PRD-AT-08 · **Prioridad**: P1 · **Esfuerzo**: M-L
**Repositorio**: agent-tts · **Estado**: COMPLETADO (Cola y Cadena, 2026-09-25)
**Referencias Git**: `3a9c59e`, `ec1b541`, `7231f9e`, `0d3f340`.

> **Estado reconciliado (2026-10-05)**: Cola, cadena y contrato presentes en `engine/src/agent_tts/` y `contracts/ipc-v2.md`. Aprobado el 22/09; las secciones de pendientes anteriores a T6/T8 son históricas. Se conservan métricas incumplidas y residuales; el cierre no los convierte en pruebas satisfactorias.

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
- Flake note (T5.1, 25/09/2026): the 50-event milestone test flaked
  once under full-suite load (24/09/2026; never solo) on a transient
  IPC failure — `send_ipc_command` returned None (refused connect or
  the 1 s client timeout under thread starvation) and the test helpers
  crashed dereferencing it. NOT an invariant violation: dispatch
  serialization (single active slot) makes a false speaker overlap
  impossible by construction. Hardening in
  `tests/test_queue_milestone.py`: idempotent status reads retry
  transient failures, polling predicates are None-tolerant, enqueue
  stays no-retry (ambiguous None → loud diagnostic failure).
- Flake note 2 (T5.1 verification, 25/09/2026): the same load class
  flaked `tests/test_daemon_lifecycle.py::test_requests_reset_the_idle_clock`
  twice in 10 full-suite runs (never solo) — under GIL starvation the
  daemon-observed gap between the test's 0.4 s pings exceeded the 1.0 s
  idle window, the daemon exited and removed its socket, and the next
  single-shot ping returned None. NOT a daemon defect: the idle clock
  semantics are correct; the test's margin (2.5x) was too thin for
  full-suite load. Fix: idle window 1.0 → 2.0 s plus one bounded ping
  retry (transient refuses in the bind->listen startup window were
  observed ~2x per full run); a daemon that truly idled out still
  fails loudly.

### Pending for the Hito Cadena (T6)

- `--play-chain` end-to-end and its DoD metrics: gapless item-to-item
  dispatch (< 50 ms, same criterion), controls (seek/pause/phrases) over
  the combined `BoundaryMap`, chain-as-one-queue-item policy.
- Contract freeze communication to herdr-tts (HT-03/HT-10) at sub-bloque
  closure.

---

## Hito Cadena — implementation traceability and registered measurements (T6, 24/09/2026)

> Section written in English per unit instructions; structure follows the
> Hito Cola (T5) section above. Scope: the **Hito Cadena** acceptance
> criteria, measured and registered. Chain is a local-target milestone
> (T6): wsl-ps/winhost play the chain as one stream but keep their
> documented pre-existing `ERR` for position-jump/phrase controls, so no
> chain-specific work or measurement exists for them here.

Implementation landed in `feat/at-08-cola-y-cadena` (T6): the chain
assembly module (`agent_tts/chain.py`) with the combined `BoundaryMap`,
the chain wire fields on `play`/`enqueue` plus the one-session runner
path and the `chain-item` trace seam (`daemon.py`), the
`--play-chain`/`--chain-gap` CLI flags (`cli.py`), and the measurement
harness (`scripts/chain_metrics.py`). The boundary-composition primitive
`shift_boundary_map` moved from `cli.py` to `boundaries.py` (re-exported
from `cli`; the streaming path and its tests are unchanged) so the chain
composition and the streaming merge share one primitive instead of two.

| Requirement | Implementation | Verification |
|---|---|---|
| RF-AT-08-4 | `--play-chain FILE...` + `chain`/`chain_gap_ms` play/enqueue payload fields (mutually exclusive with `text`/`file`, never `no_play`); files decode through the `--play-file` decode path into ONE continuous PCM stream with configurable inter-item silence (default 0 ms) in one session | `tests/test_chain.py` assembly + session + daemon suites; `tests/test_cli_chain_flags.py` |
| RF-AT-08-4 (combined map) | `assemble_chain` composes per-file maps (or one synthetic navigation unit per metadata-less file) onto chain-global positions via `boundaries.shift_boundary_map`; seek/pause/phrase navigation address chain-global positions | `test_combined_map_rebases_sentences_words_paragraphs_chain_globally`, `test_seek_addresses_chain_global_positions`, `test_phrase_navigation_crosses_file_boundaries`, `test_chain_controls_over_the_whole_chain_via_ipc` |
| RF-AT-08-2 applied to the chain | The chain enters the queue as ONE item with the same priority/policy machinery (D4 mapping for a plain `play` with a chain) | `test_chain_queued_behind_active_item_dispatches_by_policy`; `--priority`/`--policy` composition in `tests/test_cli_chain_flags.py` |
| RF-AT-08-6 (invariant kept) | One session build, one `play()` call per chain; session unmounted before finalize (unchanged load-bearing order) | `test_chain_plays_once_in_order_gapless_as_one_queue_item` (one session, one start, byte-exact stream) |
| T5 terminate convention | One continuous buffer means one stop flag: a queue-side terminate cuts mid-stream, remaining files never play | `test_chain_honors_stop_flag_mid_file_remaining_files_not_played` |
| RNF-AT-08-1 (chain application) | `chain-item` trace seam: the daemon's watcher observes the playback cursor crossing each item's chain-global start (5 ms poll) | Measured: max 9.803 ms « 50 ms (below) |
| US-AT-08-3 | Default 0 ms inserts nothing: the device stream is the byte-exact concatenation of the decoded files, one session | `test_chain_plays_once_in_order_gapless_as_one_queue_item`; harness `us_at_08_3_nothing_inserted` |

### Registered measurements (DoD — Hito Cadena)

Method (same discipline as the queue metric — observable, not
tautological): `scripts/chain_metrics.py` drives a REAL daemon
subprocess over the real IPC channel. The files are real WAVs
(44100 Hz/stereo — the decode-native passthrough format), decoded by
the real miniaudio path, dispatched by the real queue, assembled by the
real chain code, and observed by the daemon's real chain-boundary
watcher. Only the audio DEVICE is stubbed (no device exists in the
measurement WSL environment): the local play seam simulates a device
consuming the continuous buffer at the real sample rate, advancing the
same session cursor the watcher polls.

Seam definition (the T5 open point, now precise): the daemon prints
`agent-tts-queue: chain-item item=<id> index=<i> pos=<sec> t=<CLOCK_MONOTONIC>`
when the playback cursor crosses chain item i's chain-global start
(item 0 included: chain playback began). "The previous chain item ends"
when the cursor leaves its audio; "the next item starts" at the
crossing of its start. The measured slack is
`(t_item[i+1] − t_item[i]) − (start[i+1] − start[i])` — the wall-clock
hole between consecutive items minus the audio time between their
starts (item duration + gap). On one continuous buffer this is zero by
construction; it would grow exactly by a per-file session
teardown/rebuild if the chain were played as N separate sessions (the
approach this milestone replaces), which is what the < 50 ms budget
bounds.

| Metric | Threshold | local |
|---|---|---|
| RNF-AT-08-1 chain item-to-item slack (10 files, gap 0 ms, n=9 boundaries) | < 50 ms | **max 9.803 / p95 9.803 ms** (mean 7.343 ms) |
| RNF-AT-08-1 (supplementary: 5 files, gap 120 ms, n=4) | info | max 11.174 / p95 11.174 ms |
| One audio session per whole chain (both scenarios) | 1 | **1** (one audio-start/-end pair per chain) |
| US-AT-08-3 nothing inserted at gap 0 | byte-exact | **pass** — stream == decoded(a) + decoded(b) |
| enqueue → dispatch (idle queue) | info | 0.381 ms / 0.370 ms (gap 0 / gap 120 scenarios) |

Environment: WSL2, kernel 6.6.87.2-microsoft-standard-WSL2, 28 CPUs,
Python 3.14.7, 2026-09-24. Reproduce with:

    PYTHONPATH=src .venv/bin/python scripts/chain_metrics.py --target local

Machine-readable record: `metrics/chain/chain-metrics-local-20260924T214218Z.json`.

CI enforcement (timing-free, same flake discipline as T5):
`tests/test_chain.py` asserts the structural contract — byte-exact
gapless stream, one session, combined-map navigation through the real
IPC channel, stop-flag semantics, queue-policy application, and the
`chain-item` trace sequence — while the < 50 ms thresholds live only in
the harness.

### Wire surface added (freeze-relevant, for T7/T8)

- `play`/`enqueue` payloads: `chain` (non-empty list of file path
  strings, exclusive with `text`/`file`) and `chain_gap_ms`
  (non-negative number, default 0). Typed errors:
  `chain must be a list of file paths`, `play accepts one of text,
  file, or chain, not a combination`, `chain_gap_ms must be a
  non-negative number of milliseconds: <value>`, `no_play cannot
  combine with chain (a chain owns no synthesis)`.
- The empty-payload error text changed from `play requires text or
  file` to `play requires text, file, or chain` (one pinned assertion
  updated; taken before the T8 freeze, same `ok=false error=` schema).
- Daemon stderr gained one trace kind: `agent-tts-queue: chain-item
  item= index= pos= t=` (documentation lives in `_queue_trace`).

---

## T8 — Contract freeze, full traceability and DoD closure (25/09/2026)

> Section written in English per unit instructions. This closes the
> block: the IPC contract freeze document (DoD #4), the consolidated
> requirement→implementation→test→measurement traceability (DoD #3),
> the real-edge RNF-AT-04-1 leg folded into this unit by decision D3
> (pending from BLOQUE 1.2), and the DoD checklist state.

### Contract freeze (DoD #4)

The frozen contract lives in **`docs/ipc-contract-v2.md`** (referenced
from the README): framing v2 layout and constants, every command with
its request fields and exact reply shapes, the full error-text
catalogue (including the T6 change `play requires text, file, or
chain`), the queue snapshot key reference, the client-side contract
(stderr/exit codes, ack line, exit 130), the chain fields, daemon
tunables, the stderr trace seams, and the known limitations at freeze
(winhost v1 preemption, wsl-ps/winhost navigation `ERR`, transient
bind→listen refusal, opaque `None` on transport failure — the two T5.1
PRODUCT findings —, kokoro-dependent metrics pending, 8 h smoke
pending). Freeze statement: frozen at BLOQUE 1.3 close (2026-09-25),
base for herdr-tts HT-03/HT-10; post-freeze changes are breaking.
Physical communication of the freeze to the herdr-tts team is the
maintainer's action; the document is the artifact.

### Consolidated traceability (DoD #3)

Every RF/RNF/US of `../AT-08-cola-prioridades.md` mapped to its
implementation (module + commits on `feat/at-08-cola-y-cadena`),
covering tests, and registered measurement (records under
`metrics/queue/`, `metrics/chain/`; tables in the T5/T6 sections
above). Style follows the paraguas table.

| Requirement | Implementation | Tests | Registered measurement |
|---|---|---|---|
| RF-AT-08-1 | `queue_manager.Priority` (blocked > done > working, FIFO per level); CLI `--priority`/`--policy` (`cli.py`, 01cd244); `priority` wire field (`daemon.py`, 2dd9550; enum 1b5af16). Default mapping `working`/`queue` documented in README + contract doc, overridable per invocation | `test_cli_queue_flags.py`; `test_queue_ipc.py::test_status_exposes_pending_priorities_in_dispatch_order` | Contract order row below (T5 table) |
| RF-AT-08-2 | `queue_manager.Policy` — preempt only from strictly higher priority, equal/lower degrade to queue, no busy rejection (1b5af16); `policy` wire field (2dd9550); CLI flag (01cd244) | `test_queue_manager.py`; `test_queue_milestone.py::test_blocked_preempts_playing_done_and_reaches_speaker_complete` | Preempt probe 0.177/0.204 ms (T5 table) |
| RF-AT-08-3 | Coalescing window anchored at the first pending item per `event_type`, default 5 s, `--coalesce-window`/`AGENT_TTS_COALESCE_WINDOW` (`queue_manager.py`, `daemon.py`) | `test_coalesce_burst_of_10_done_is_one_announcement` | 10:1 on both targets (T5 table) |
| RF-AT-08-4 | `agent_tts/chain.py` + `boundaries.shift_boundary_map` (28942d4); `chain`/`chain_gap_ms` wire fields, one-session runner, `chain-item` seam (453c1b7); `--play-chain`/`--chain-gap` (5c9eb16) | `test_chain.py` (assembly/session/daemon); `test_cli_chain_flags.py`; `test_chain_playback.py` | Chain table (T6 section): slack max 9.803 ms, byte-exact gapless |
| RF-AT-08-5 | `status` `queue_len=` + `queue=` snapshot fields, whitespace `\uXXXX`-escaped (`daemon.encode_queue_fields`, 2dd9550) | `test_queue_ipc.py` status suite | The harness itself polls `status`; snapshots in the JSON records |
| RF-AT-08-6 | Single active slot in `QueueManager`; session unmounted before finalize→dispatch (order is load-bearing; kept for the chain in 453c1b7) | `test_queue_milestone.py` 50-event zero-overlap simulation; `test_chain_plays_once_in_order_gapless_as_one_queue_item` | 0 overlaps, 50 intervals, both targets (T5 table) |
| RNF-AT-08-1 | Event-driven dispatch (finalize callback dispatches inline); chain-boundary watcher (453c1b7) | Structure asserted in the suite; thresholds live in the harnesses (flake discipline) | Queue dispatch max 0.045/0.086 ms; chain item-to-item max 9.803 ms (T5/T6 tables) |
| RNF-AT-08-2 | Preempt cuts the active item and dispatches immediately; `terminate` sets the stop flag first (T5 fix 2, 7c6f2a7) | `test_blocked_preempts_playing_done_and_reaches_speaker_complete` | 0.177 ms local / 0.204 ms wsl-ps (T5 table) |
| RNF-AT-08-3 | Same queue semantics per target; winhost v1 excluded by design (zonas de conflicto; v2 decision deferred for the package) | wsl-ps contract leg (same `SCRIPTED_SEQUENCE`, real PowerShell playback) | Same sequence → identical resulting order, local and wsl-ps (T5 table) |
| RNF-AT-08-4 | **Retired** in the source PRD (AT-04 vía única: no daemon-less mode exists to design or test) | — | — |
| US-AT-08-1 | Strictly-higher preemption + blocked items always play complete (1b5af16, 7c6f2a7) | 50-event simulation asserts 10/10 blocked complete, 0 interrupted | Both targets (T5 table) |
| US-AT-08-2 | One coalesced announcement for a 10-`done` burst (≤3 identifiers + count) | `test_coalesce_burst_of_10_done_is_one_announcement` | 10:1, `coalesced=10` in replies (T5 table) |
| US-AT-08-3 | Default 0 ms inserts nothing; byte-exact concatenation, one session (28942d4/453c1b7) | `test_chain_plays_once_in_order_gapless_as_one_queue_item`; harness `us_at_08_3_nothing_inserted` | Byte-exact pass (T6 table) |
| US-AT-08-4 | `enqueue` via IPC with priority + queue visibility in `status` (2dd9550) | `test_queue_ipc.py` | The measurement harness drives everything through real `enqueue` IPC |

### RNF-AT-04-1 real-edge leg (DoD 1.2 pending, folded into T8 by D3)

The BLOQUE 1.2 registration left the real-provider measurement
pending ("medición con edge real ... en máquina con proveedor y
dispositivo"). This environment has network access to the edge
endpoint (verified: real `edge_tts` stream, 0.57 s), so the campaign
ran here (25/09/2026) with the new harness
`scripts/edge_p95_metrics.py`: N=25 speak events (46-char text,
RNF-AT-04-1 scope) through a WARM daemon started by the REAL
auto-start path on an isolated channel. Fidelity: REAL edge network
synthesis, real MP3 decode, real framed IPC, real queue dispatch;
only the audio DEVICE is stubbed at the `AudioSession.play` seam (no
device in this WSL environment — audio-start is the play-seam entry,
same seam discipline as `metrics/queue`).

| Metric | Threshold | Measured |
|---|---|---|
| event → audio-start p50 | info | **376.4 ms** |
| event → audio-start p95 (n=25) | < 250 ms (RNF-AT-04-1) | **584.1 ms — NOT met** |
| max / mean | info | 606.8 / 402.6 ms |

Honest reading, for the JD: with a real network provider the budget
is dominated by the edge synthesis round trip itself (WSS connect +
stream + full-MP3 collect — a `<200`-char text is a single group, so
`--stream` would not change it: classic and pipelined paths coincide
at this length). The 250 ms budget assumed daemon-warmth removes the
per-event cost — it removes the spawn cost (the stub leg measured
0.7 ms warm), not the network synthesis. The daemon-side budget holds
(dispatch ≤ 0.086 ms); the provider leg does not, from this network.
Record: `metrics/edge/edge-p95-local-20260925T072309Z.json`
(reproduce: `PYTHONPATH=src .venv/bin/python scripts/edge_p95_metrics.py`).
The kokoro leg stays pending (kokoro not installed; deliberate — no
packages installed for this measurement).

### DoD checklist state (closed by this section)

1. **Acceptance criteria measured and registered** — DONE. Hito Cola:
   T5 section (0 overlaps, 100% blocked, 10:1 coalescing, dispatch
   max 0.045/0.086 ms, contract order identical) with records
   `metrics/queue/*.json`. Hito Cadena: T6 section (item-to-item max
   9.803 ms, byte-exact gapless, one session) with record
   `metrics/chain/*.json`. The folded RNF-AT-04-1 real-edge leg:
   measured above (p95 584.1 ms — threshold NOT met, honestly
   registered for the JD), record `metrics/edge/*.json`.
2. **Suite green** — DONE. `PYTHONPATH=src .venv/bin/python -m pytest`
   at the freeze commit: **750 passed / 11 skipped / 0 failed**
   (baseline unchanged; docs + measurement harness only).
3. **Traceability** — DONE. The consolidated table above covers
   RF-AT-08-1..6, RNF-AT-08-1..3 and US-AT-08-1..4 with module,
   commit, tests and registered measurement; RNF-AT-08-4 is retired
   in the source PRD (noted in the table).
4. **Contract frozen and communicated** — DONE as artifact:
   `docs/ipc-contract-v2.md` (freeze statement dated 2026-09-25,
   referenced from the README). The physical communication to the
   herdr-tts team (HT-03/HT-10 owners) is the maintainer's action,
   registered here as the remaining human step.

> **Nota de archivo (2026-09-25)**: PRD ejecutada y archivada. Las
> secciones de pendientes que vivían aquí (post-freeze pending y
> post-close decisions/residuals) migraron al registro vivo
> `docs/deuda-tecnica.md`, que es su ubicación de mantenimiento.
