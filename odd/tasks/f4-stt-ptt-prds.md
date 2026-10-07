# F4 prep — rewrite AT-02 and HT-01 PRDs, reconcile dual-coverage notes

**Feature id:** `f4-stt-ptt-prds` · **Current worktree:** `agent-tts-worktrees/f4-engine` · **Branch:** `docs/f4-engine-stt` (from `main` @ `ed2fbfe`)
**Source:** `docs/voice-stack/ROADMAP.md` §3 (D1a-D1c) and §4-F4/F5; maintainer design decisions of 2026-10-07.

## Objective
Documentation only. Make the PRDs match the decided F4 design so implementation can start from a truthful, task-sliced plan. No code, tests or config.

## Binding design decisions (2026-10-07)
1. Latest owner decision supersedes the HTTP design: reusable STT and microphone capture belong to `agent-tts` engine. Reuse the existing faster-whisper implementation, not a second whisper.cpp stack. The plugin consumes a public engine interface; migration of the brain to the same engine is a later separate slice.
2. Microphone capture runs through PowerShell on the Windows host, following the playback targets `winhost` / `wsl-ps` pattern. WSL2 has only `parecord`.
3. Injection uses `herdr pane send-text <PANE_ID> <TEXT>` (literal, no Enter) followed by Enter per `TTS_PTT_ENTER` (`ask|always|never`). `send-text` does not append Enter; `herdr pane run` does and is NOT used. `send-keys` is NOT used for dictated text.
4. Dictation must not require the brain to run. Missing engine STT dependency/model or unavailable capture produces a visible warning and no injection. Model download remains explicit; no hidden service launch or network access.
5. Only `toggle` mode (press to start; same chord or a silence timeout stops). `hold` is dropped because the keymap fires on press only (unverified in Herdr code: state it as an assumption to verify during implementation).
6. AT-02 and HT-01 each have an independent on/off toggle; both off means behavior identical to today.
7. HT-04 stays alive and reframed: ntfy action buttons call the brain endpoints (`/approval/*`, `/ask`); no independent listener. Depends on F2.

## Tasks

The checked T1–T5 below describe the previous HTTP-design pass committed as `ed2fbfe`, not acceptance of the revised engine design. Current revision tasks:

- [x] **E1** Verify engine/host boundary contracts and reusable STT dependencies; separate capability ownership from any unchosen CLI/IPC transport.
- [x] **E2** Rewrite AT-02 for generic engine STT/capture with optional faster-whisper dependencies, explicit model acquisition, and host-independent configuration.
- [x] **E3** Rewrite HT-01 to consume the engine without brain availability; preserve PowerShell capture, literal injection, toggle-only UX and independent switches.
- [x] **E4** Reconcile both indexes, ROADMAP, and reconciliation notes; label previous designs historical and keep HT-04's separate brain endpoint decision intact.
- [x] **E5** Revise implementation breakdown; engine extraction/interface first, plugin adoption second, brain migration later. Keep unchosen transport/model lifetime decisions open, never silently lock them.
- [x] **E6** Verify Markdown-only scope, links, honest pending statuses, and diff whitespace.

Route: delegated documentation writer. This revision does not authorize code or remote changes. Delivery is local-only; commit/merge timing requires separate authorization. Forecast: approximately 250–350 authored documentation lines, checked after implementation rather than treated as a cap.

### Previous completed pass (historical)
- [x] **T1** Rewrite `docs/prds/AT-02-stt-whispercpp.md` as "STT client for the plugin over the brain STT": goals, flow, config (toggle + brain URL + timeouts, names proposed and marked as proposals), failure behavior, out of scope, open questions. Keep the original whisper.cpp text as a short historical appendix or note.
- [x] **T2** Rewrite the HT-01 body to the decided design: keymap id `ptt`, toggle mode only, overlay confirm (Enter injects, Esc cancels), guards (empty / under 2 chars), logging without persisting audio or transcript, send-text injection, brain-down behavior, toggle.
- [x] **T3** Reconcile HT-04 header/note, both PRD indexes, `RECONCILIATION-PLAN.md`: remove the 2026-10-06 "cobertura dual" wording that kept engine-local STT; state the 2026-10-07 decision with date.
- [x] **T4** Add an implementation task breakdown (stable IDs, one behavior each, files, verification, order: STT client and brain-down handling first, mic capture second, injection third, keymap and overlay last, toggles throughout) at the end of the HT-01 PRD or as a section in this document. Mark every item as pending; nothing is implemented.
- [x] **T5** Structural verification: links, `git diff --check`, only `.md` changed.

## Route
Delegated direct, one writer. Passive documentation: TDD not applicable. Native review: RDD is off for this clone.

## Progress
Created 2026-10-07. Ejecutado el 2026-10-07 por el writer delegado (T1-T5 completados):

- **T1:** `docs/prds/AT-02-stt-whispercpp.md` reescrita como "Cliente STT del plugin sobre el STT del brain". Contrato del endpoint verificado contra `server.py`/`stt.py`/`config.py` (multipart `audio`, 503 unavailable/loading, 400 vacío, `{"text"}`, host/puerto 127.0.0.1:8741). Config propuesta (`TTS_STT`, `TTS_STT_URL`, `TTS_STT_TIMEOUT`) marcada como propuesta. Preguntas abiertas (6) incluidas. Diseño whisper.cpp conservado como apéndice histórico corto. Nota 2026-10-06 marcada superada (historia conservada).
- **T2:** HT-01 reescrita al diseño decidido: id `ptt`, solo `toggle` (suposición press-only del keymap marcada como no verificada, sin evidencia en este repo), captura PowerShell por analogía `wsl-ps` (verificada en `powershell_playback.py`), overlay Enter/Esc/re-intento, guards vacío/<2 chars, logging sin persistir audio ni transcripción, inyección `send-text` literal + `TTS_PTT_ENTER` (verificado con `herdr 0.9.1` `--help`: `run` añade Enter y no se usa; `send-keys` no se usa), brain caído (aviso visible, sin inyección, plugin no gestiona el brain), interruptor propio. Env vars marcadas como propuestas.
- **T3:** HT-04: cabecera y nota reemplazadas por la decisión 2026-10-07 (viva, reenfocada: ntfy Actions → endpoints del brain, sin listener propio, depende de F2); cuerpo conservado como referencia histórica con descargo. `docs/prds/README.md` (bullet de resumen, filas AT-02/HT-01/HT-04, nota de BLOQUE-3, §5) y `hosts/herdr/tts-plugin/docs/prds/README.md` (filas HT-04/HT-01, entregas, fase 1, postergadas, nota de reconciliación) actualizados con la decisión fechada. `RECONCILIATION-PLAN.md` §R2: nota de superación fechada 2026-10-07 sobre la directiva de cobertura dual; tabla y casillas R2.1-R2.3 conservadas como registro histórico con la resolución vigente añadida por fila.
- **T4:** Desglose F4.1-F4.9 al final de HT-01: un comportamiento por ítem, ficheros esperados, verificación nombrando harnesses (`critical_cases.sh`, `config_cases.sh`, `keymap_cases.sh`, `all_bash_harnesses.sh`, `matrix/`), orden de dependencias (cliente STT y brain-down primero; captura segundo; inyección tercero; keymap y overlay al final; interruptores transversales), aceptación de interruptores (ambos `off` = idéntico a hoy) como F4.9, y nota de la regla del ROADMAP (un solo escritor de `bin/herdr-tts`). Progreso 0 de 9; nada implementado.
- **T5:** Verificación estructural ejecutada (ver evidencia).

## Verification evidence
Ejecutado en el worktree el 2026-10-07:

- `git status --short`: solo las 8 rutas permitidas + este task doc (que ya era untracked antes de empezar; árbol otherwise limpio al inicio: `?? odd/tasks/f4-stt-ptt-prds.md`).
- `git diff --name-only | grep -v '\.md$'`: sin salida (solo `.md`).
- `git diff --check`: sin salida.
- `grep -rn "cobertura dual\|Directiva de cobertura" docs hosts/herdr/tts-plugin/docs hosts/herdr/brain/docs`: solo apariciones históricas intencionadas (notas "superada", apéndices, registro R2 del RECONCILIATION-PLAN) y ficheros fuera de superficies (ver informe del writer).
- Enlaces relativos añadidos resueltos con `ls`; citas file:line verificadas contra lectura directa de `server.py`, `stt.py`, `config.py`, `bin/herdr-tts`, `powershell_playback.py`.

## E1-E6 execution log (2026-10-07, English — revision pass; Spanish log above is the historical T1-T5 pass)

- **E1 (boundary verification, read-only):** contracts reviewed. `contracts/README.md:8` governs hosts→engine via `ipc-v2.md` **and public artifacts**; `contracts/README.md:10` + `contracts/tts-brain-v1.md:11` govern brain→plugin (CLI surface) only — **no contract governs or prohibits plugin→brain HTTP** (the earlier categorical claim was an overclaim; a missing contract is not a prohibition). Boundary enforcement is one-way (`engine/tests/test_monorepo_boundaries.py:3-5`: engine must never import hosts; hosts may use "contracts and public interfaces"). The plugin already consumes the engine public Python API via the `lib/` bridge (`hosts/herdr/tts-plugin/lib/tts_engine.py:68`, normative in `contracts/tts-brain-v2.md:36-37`), so "all hosts must use CLI/IPC" is false; the engine-ownership decision is recorded as architectural preference, not contract obligation. Engine extras precedent: `engine/pyproject.toml:31-41` (only `kokoro`, `dev`; lazy PEP 562 import with actionable install hint at `providers/__init__.py:14-33`). Brain STT extraction sources verified: `stt.py:1-19` (model policy: never auto-download, explicit pull, warm-if-present local-only), `:33-37` (states, PULL_COMMAND), `:46-53` (size aliases), `:108-109` (lazy `WhisperModel`), `:154/165` (`transcribe_bytes`); `server.py:598-624`, `:594`, `:1229`; `config.py:24`, `:205-209`; `powershell_playback.py:78-80`, `:88`, `:490-500`. `ipc-v2.md:3-4` is frozen ("Post-freeze changes are breaking") — recorded as the reason the contract-surface choice is a real gate.
- **E2:** `docs/prds/AT-02-stt-whispercpp.md` rewritten: generic engine STT capability + PowerShell capture ownership, extraction-based reuse of brain faster-whisper (file:line cited), optional `agent-tts[stt]` extra per kokoro precedent, explicit model acquisition ported verbatim, host-independent engine config (`AGENT_TTS_*` names marked proposals), brain-independence and error semantics section, "decisiones NO tomadas" keeps transport/residency, contract surface, lifecycle/latency, silence thresholds, PowerShell format, newline handling, press-only assumption all open. No `POST /transcribe`/`TTS_STT_URL` in active design; both superseded designs kept as short dated historical appendices (A: whisper.cpp 2026-09-22; B: HTTP client 2026-10-07 `ed2fbfe`).
- **E3:** `hosts/herdr/tts-plugin/docs/prds/HT-01-push-to-talk-intercom.md` rewritten: dependency now AT-02 engine capability (not brain), responsibility split table (plugin: keymap/pane/overlay/injection; engine: capture/STT/model), dictation with brain off documented as intentional, switch interplay and error semantics explicit (either off → no capture; deps/model/capture missing → visible actionable warning, nothing injected, fail-open; both off = identical to today), verified keymap/send-text mechanics preserved with their citations. Full F4 task table lives only here (not duplicated across docs).
- **E4:** both README indexes (summary bullet, AT-02/HT-01 rows, entregas line, postergadas parenthetical, §5 coordination bullet, reconciliation note) updated to the engine-ownership decision with dated chronology; `RECONCILIATION-PLAN.md` R2 note extended with the same-day supersession chain (D-R2 dual coverage 2026-10-06 → HTTP client `ed2fbfe` → engine ownership, vigente) without touching the historical table/checkboxes; `ROADMAP.md` D1a/D1b keep the original 2026-10-05 decision text and append the dated ownership update (no retroactive misrepresentation); F4 section rewritten to the engine design and revised order. `HT-04` got one deslinde line only: engine-STT decision does not affect its separate ntfy→brain endpoints decision.
- **E5:** F4 breakdown revised with stable IDs F4.1-F4.10 plus an explicit mapping note (F4.1 HTTP client→extraction; F4.2 brain-down→optional deps; F4.3 toggle→test seam; old F4.4/F4.5 merged into new F4.5; new F4.4 is the no-code transport/residency/contract GATE; F4.6-F4.9 keep meaning; F4.10 new: optional brain migration, separate authorization, preserving browser/server behavior). Order: engine extraction/model policy/test seam → gate → mic/silence → plugin wiring/toggles/confirmation literal text → brain migration. All ten boxes unchecked; progress 0 of 10.
- **E6:** verification below.

## E1-E6 verification evidence (2026-10-07, worktree)

- `git diff --check`: clean (no whitespace errors).
- `git diff --stat`: 8 files, 134 insertions(+), 107 deletions(-) — within the ~400-line advisory heuristic without compression.
- `git status --short`: only the 8 allowed `.md` paths.
- `git diff --name-only | grep -v '\.md$'`: empty (Markdown only).
- Relative-link check of all changed files: all NEW link targets exist. One PRE-EXISTING broken link found, untouched by this revision and outside its changed lines: `docs/prds/README.md:65` links `descartadas/HT-09-espacializacion-estreo.md` but the file is `HT-09-espacializacion-estereo.md` (present at base `ed2fbfe`; reported, not drive-by fixed).
- Status readback: AT-02 REENFOCADA (engine capability, pending), HT-01 Active/design decided (pending, brain-independent), HT-04 Active/refocused (unchanged decision), all F4.1-F4.10 unchecked, ROADMAP D1a/D1b carry dated chronology, no document claims runtime work as done.
- TDD: not applicable (passive documentation; no suites or live services involved).

## Next step
Maintainer review of this revision; merge decision and F4 implementation start (F4.1-F4.3 first, then the F4.4 gate) remain separate authorizations. Engram mirror: see outcome in the session handoff (topic `odd/f4-stt-ptt-prds/tasks`, project `agent-tts`).
