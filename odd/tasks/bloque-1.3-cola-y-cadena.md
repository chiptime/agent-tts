# BLOQUE 1.3 — Cola con prioridades y reproducción encadenada (AT-08)

**Estado**: COMPLETADO e integrado (2026-09-25) · **Rama histórica**: `feat/at-08-cola-y-cadena` (base original `885442f`)
**Referencias vigentes**: `3a9c59e` (cola), `ec1b541` (cadena), `7231f9e` (contrato), `0d3f340` (merge). Documento versionado; el registro original también tiene espejo Engram `odd/bloque-1.3-cola-y-cadena/tasks`.

> **Reconciliación (2026-10-05)**: Código presente en `engine/src/agent_tts/{queue_manager,chain,daemon,cli}.py`, contrato en `contracts/ipc-v2.md`. Se conserva el cierre de sesión anterior a la integración como historia, no como estado actual de entrega. Las métricas incumplidas y residuales aceptados no se declaran resueltos; su mantenimiento vive en `docs/deuda-tecnica.md`. F1 no repite mediciones ni revisión.

## Objetivo

Cola con prioridades (`blocked > done > working`, políticas `preempt|queue|coalesce`) y `--play-chain` gapless sobre el daemon del BLOQUE 1.2, en dos hitos: Cola primero, Cadena después. El cierre congela el contrato IPC (base HT-03/HT-10 de herdr-tts).

## Problema / Por qué

Hoy la segunda reproducción recibe `ERR: playback already in progress` (y RS-5: una sesión colgada wedgia el daemon sin watchdog); un `done` trivial pisa un `blocked` crítico; no existe cola. PRD-AT-08 aprobada; el paraguas P1 exige este cierre para congelar el contrato.

## Decisiones del usuario (2026-09-24 · engram `agent-tts/bloque-1.3/decisions`)

- **D1**: framing v2 length-prefixed SÍ (cierra A1'', A2''=B1'', RI-1; excepción consciente a la aditividad de RF-AT-04-2; verificar A8 en el camino).
- **A3**: errores de control por stderr + exit≠0. **A5**: fallo de provider visible en `status` (estado error + `last_error`). **A7**: limitación documentada, sin cambios. **A3''**: incluido (provider/voice reales de speak in-process en `status`).
- **D3**: mediciones DoD 1.2 (edge p95, RAM kokoro, smoke 8h) DENTRO de 1.3.
- **D4**: `play` manual encola por defecto (`working`/`queue`; desaparece el error busy).
- **RS-5**: watchdog/liveness del QueueManager desde el día uno (mandato).

## Alcance autorizado

- Hito Cola: RF-AT-08-1/2/3/5/6, RNF-AT-08-1 (cola) y -2, US-AT-08-1/2/4.
- Hito Cadena: RF-AT-08-4, RNF-AT-08-1 (cadena), US-AT-08-3.
- Deuda heredada: framing v2 + verificación A8, A3, A5, A3'', watchdog RS-5.
- Métricas: 50 eventos concurrentes → 0 solapes (local + wsl-ps; winhost v1 fuera, limitación documentada), 100% `blocked` completos y <1.5 s, coalescing ≥3:1, despacho <50 ms (cola y cadena).
- Fuera de alcance: winhost v2 (aplazado por el paraguas), aging RFC, ventana de coalescing adaptativa, mezcla de voces (AT-06), persistencia de cola entre reinicios.

## Restricciones

- Sin push ni merge sin autorización explícita; tras el JD: STOP; la decisión de merge es del usuario.
- RDD OFF clone-local (defecto #4805 gentle-ai, sin fix publicado) → revisión = Judgment Day dual, jueces ciegos read-only en paralelo, máx. 2 rondas de fix.
- WIP paralelo en el checkout principal (AT-09/AT-10): NO tocar ni commitear; `docs/prds/AT-10-monorepo-ecosistema.md` queda como está.

## Entorno / checks

- Runner: `PYTHONPATH=src .venv/bin/python -m pytest` (`.venv` enlazado al checkout principal).
- Baseline en este entorno: **627 passed / 10 skipped / 0 failed** (equivale al 628/9 verificado por el usuario: aquí falta `powershell.exe` en el PATH de la shell no-interactiva y el test de WSL interop salta).
- Flake pre-existente anotado: `tests/test_daemon_lifecycle.py::test_requests_reset_the_idle_clock` (falló 1 de 2 ejecuciones, timing). Vigilar en el cierre; NO arreglar en este bloque sin aprobación.
- TDD: RED→GREEN por work unit en el mismo commit (mandato del usuario); conventional commits sin atribución AI.

## Tareas

- [x] **T1** (delegado) Framing v2 length-prefixed: formato de wire versionado en `ipc.py` (cliente+daemon), eliminar `_probe_past_cap`/drain heurístico, chunks de lectura mayores, literales 8192/16 MiB → constantes de `ipc.py` (A8); tests que matan A1'', A2''=B1'' y RI-1. ✅ `2acbc42` — suite 636/10/0 verificada; A1''/A2''=B1''/RI-1/A8 cubiertos por tests nombrados por defecto; mismatch → kill-and-respawn reutilizado (ping sin byte 0x0A pineado por test).
- [x] **T2** (delegado) QueueManager core dentro del daemon, sobre `AudioSession`: prioridades `blocked>done>working`, políticas `preempt` (solo desde prioridad estrictamente superior) / `queue` / `coalesce` (ventana configurable, 5 s de ejemplo), invariante no-solape (RF-08-6), despacho <50 ms, watchdog RS-5 (liveness por progreso de sesión; pausa lo suspende; acción: marcar ítem fallido y seguir, configurable). ✅ `1b5af16` — `queue_manager.py` (755 líneas) + 45 tests deterministas con reloj inyectable; runner placeholder documenta el seam de T3; suite 681/10/0 verificada.
- [x] **T3** (delegado) IPC: comando `enqueue` (priority/policy), `status` extendido (`queue_len`, cola pendiente con prioridades, `coalesced=N`, estado error + `last_error` (A5), provider/voice reales (A3'')), flag de error explícito en respuestas (A3 lado daemon). ✅ `2dd9550` — runner real montado (una sola sesion, unmount antes de finalizar), reply schema congelado (`ok=false error=` / `ok=true …`), `queue=` = snapshot JSON con claves de freeze; play→enqueue(working,queue) con guard busy eliminado (D4); flags `--coalesce-window`/`--wedged-timeout`; suite 695/10/0 verificada + harness live daemon.
- [x] **T4** (delegado) CLI: `--priority`, `--policy`; `play` manual encola por defecto (D4); errores por stderr + exit≠0 (A3 lado cliente); anuncio coalescido (lista hasta 3 identificadores, resume el resto) visible en `status` antes de sonar. ✅ `8113468` + `01cd244` — disciplina de error congelada (ok=false/ERR: → stderr + exit 1; misuse exit 2), ack `queued: item= position= queue_len= [coalesced=]`, flags en speak/--play-file, status humano renderiza cola; suite 718/10/0 verificada.
- [x] **T5** (delegado) Hito Cola: tests de contrato + métricas con evidencia (50 eventos 0 solapes en local y wsl-ps, 100% blocked, <1.5 s, coalescing ≥3:1, despacho <50 ms). ✅ `7c6f2a7` + `337d702` — **las 6 métricas en verde en AMBOS targets** (wsl-ps con los 50 eventos reales, sin reducción): 0 solapes (50 intervalos), 100% blocked (51/0/0), blocked→despacho 0.18/0.20 ms (<1500), coalescing 10:1, despacho max 0.045/0.086 ms p95 0.044/0.047 (<50), orden de contrato idéntico local=wsl-ps. Evidencia: `metrics/queue/*.json` + tabla en el PRD del bloque (estilo 1.2). Fixes habilitantes registrados honestamente: terminate = stop-flag (si no, wsl-ps drenaba el grupo entero al preemptar), backlog IPC 5→128 (ECONNREFUSED con 10 hilos), trazas `_queue_trace` del daemon. Nota de método: el <1.5 s mide evento→inicio de reproducción con audio pre-renderizado (sin latencia de síntesis).
- [x] **T6** (delegado) Hito Cadena: `--play-chain` + entrada como ítem de cola; sesión única; `merge_chunks_to_audio` + `BoundaryMap` combinada; silencio intermedio configurable (default 0 ms); seek/pausa/frases sobre la cadena completa. ✅ `28942d4`+`453c1b7`+`5c9eb16` — `agent_tts/chain.py` (BoundaryMap combinada; `shift_boundary_map` → `boundaries.py`), cadena = 1 ítem de cola con campos wire `chain`/`chain_gap_ms` (freeze-critical, documentados), watcher 5 ms emite `chain-item` (seam de traza definido), controles de cadena sobre mapa global (local), despacho entre ítems max 9.8 ms (<50), concatenación byte-exacta con gap 0; suite 750/11/0.
- [x] **T7** (delegado) Hito Cadena: tests — **absorbido por T6** (12 unit + 8 e2e daemon + 8 CLI + harness `scripts/chain_metrics.py` + evidencia `metrics/chain/*.json`); lo único que quedaba fuera (controles de cadena en wsl-ps) es limitación documentada, fuera de PRD.
- [x] **T5.1** (delegado) Fix flake 50-eventos. ✅ `13431e1` + `b68b0c7` — Diagnóstico: candidato C (manejo IPC del harness sensible a carga: `send_ipc_command`→None ante connect-refused en la ventana bind→listen y lecturas post-teardown; el test desreferenciaba sin guard). NUNCA fue violación del invariante: 0-solapes sólido por construcción (slot único + CLOCK_MONOTONIC end→start con happens-before; candidatos A/B descartados). Fix test-only con reintentos acotados a deadline; enqueue SIN reintento (ambigüedad de duplicado). **Desviación a ratificar por el usuario**: el flake heredado `test_requests_reset_the_idle_clock` disparó 2/10 en el bucle de reproducción e impedía la prueba de estabilidad → se ampliaron márgenes del test (ventana 1.0→2.0 s, retry acotado, join 6 s; solo test, sin semántica de producto). Prueba: 4/4 pasadas completas verdes + solitario. Hallazgos de producto registrados (post-fix, para JD/informe): ventana bind→listen (connect refused transitorio afecta clientes reales), `send_ipc_command` opaco (None).
- [x] **T8** Métricas DoD + docs: README y anuncio de contrato IPC congelado (nota para herdr HT-03/HT-10), trazabilidad RF-AT-08-1..6 / RNF-AT-08-1..3 / US-AT-08-1..4; RAM kokoro caliente, edge p95 real (requiere kokoro), lanzar harness smoke 8h; registrar evidencia. ✅ `faf71d8` + `7086555` — `docs/ipc-contract-v2.md` (contrato congelado 2026-09-25: framing v2, comandos/respuestas, catálogo de errores, claves de snapshot, disciplina de cliente, limitaciones conocidas), tabla de trazabilidad completa en el PRD del bloque, README cerrado. **edge p95 real MEDIDO: 584.1 ms p95 vs presupuesto 250 ms — NO CUMPLE, registrado honestamente** (`metrics/edge/edge-p95-local-20260925T072309Z.json`; la síntesis de red edge domina: p50 376 ms; el despacho daemon se mantiene ≤0.086 ms; decisión de producto pendiente: re-escalar presupuesto / semántica de primer chunk / aceptar). RAM kokoro: pendiente documentada (kokoro no instalado; prohibido instalar en .venv compartido). Smoke 8h: se lanza tras el JD, antes del STOP.
- [x] **T9** (completado) Judgment Day dual — **JUDGMENT: ESCALATED ⚠️** (protocolo: hallazgo corroborado persistente tras ronda 2 con gravedad disputada entre jueces; sin autoridad de entrega). Detalle: ledger `odd/reviews/bloque-1.3-jd-ledger.md` + engram. Ronda 1: R1-01/R1-02 (severos confirmados) + R1-04/R1-05 (corroborados por el usuario) → fixes `96664ad`/`c0b5179`/`7f08ae2`/`d3ef981`; re-juicio 1: R2-01 [B CRITICAL] + R2-02 [A SUG] → fixes `aec4b73`/`eb21ad6`; re-juicio final: R3-01 ventana espontánea (B CRITICAL / A WARNING, disputa), R3-02 sugerencia. Suite final 767/11/0.
- [x] **T10** (completado) STOP: informe final entregado; decisiones pendientes en manos del usuario (merge/push, edge p95, stop-flush, ratificar fix idle-clock, comunicar freeze a herdr). R3-01 aceptado como residual documentado; smoke 8h lanzado (PID 534917 → `metrics/smoke/smoke-8h-20260925T0935Z.log`, ETA ~17:35 CEST).

## Progreso (cierre de sesión original, anterior a la integración)

- 2026-09-25: JD dual cerrado — **ESCALATED ⚠️ por protocolo** (R3-01 con gravedad disputada entre jueces tras agotar 2 fixes + 2 re-juicios), **residual aceptado por el usuario**. 6 fixes aplicados en total (`96664ad`, `c0b5179`, `7f08ae2`, `d3ef981`, `aec4b73`, `eb21ad6`). Suite final **767 passed / 11 skipped / 0 failed**. Ledger completo: `odd/reviews/bloque-1.3-jd-ledger.md` + engram `agent-tts/bloque-1.3/judgment-day-ledger`.
- **Estado de entrega**: 16 commits (`2acbc42..eb21ad6`) en `feat/at-08-cola-y-cadena`, worktree `~/Code/personal/agent-tts-worktrees/bloque-1.3`. SIN push, SIN merge. Un PR (size:exception aprobado). El JD no otorga autoridad de entrega: merge = decisión del usuario.
- **Decisiones abiertas del usuario**: (1) merge/push; (2) edge p95 584 ms vs 250 ms (re-escalar / primer chunk / aceptar); (3) `stop`-con-flush pre-freeze o dejar como está; (4) ratificar o revertir el fix test-only del idle-clock (T5.1); (5) comunicar el contrato congelado a herdr-tts (HT-03/HT-10).

## Forecast de entrega (estrategia resuelta)

Estimación authored lines (T1–T8, tests incluidos): ~1200–1900 → supera las 400 de revisión. **Decisión del usuario (2026-09-24): UN solo PR con `size:exception` aprobado explícitamente** (una frontera de PR/rollback, como 1.2). Nada se pushea sin autorización; merge = decisión del usuario.

## Progreso

- 2026-09-24: preflight de sesión (Automatic/Both/ask-on-risk); 6 decisiones fijadas; worktree montado y `.venv` enlazado; baseline verificado (627/10 local; flake idle-clock anotado); doc creado; slicing decidido (1 PR, size:exception).
- 2026-09-24: T1 completada (`2acbc42`, framing v2: header `ATTS`+ver+u32be len, MAX_PAYLOAD 16 MiB, READ_CHUNK 64 KiB, sin probe/drain; suite 636/10/0 verificada por orquestador).
- 2026-09-24: T2 completada (`1b5af16`, QueueManager core + watchdog RS-5; 681/10/0 verificada).
- 2026-09-24: T3 completada (`2dd9550`, IPC enqueue/status/play-en-cola, reply schema freeze-ready, runner real; 695/10/0 verificada).
- 2026-09-24: T4 completada (`8113468`+`01cd244`, CLI flags + disciplina de error; 718/10/0 verificada).
- 2026-09-24: T5 completada (`7c6f2a7`+`337d702`, métricas hito Cola en verde en local y wsl-ps; 722/11/0 default / 724/9/0 con PATH de powershell — verificada).
- **Punto abierto para el informe final (pre-freeze)**: semántica de `stop` — hoy para solo el anuncio activo y los pendientes siguen (documentado en README por T3); decidir si hace falta `stop`-con-flush (o comando aparte) ANTES de congelar contrato.

## Próximo paso

Bloque integrado; consultar el registro vivo `docs/deuda-tecnica.md` para residuales. No reabrir T4/T8 como implementación pendiente por las notas históricas.
