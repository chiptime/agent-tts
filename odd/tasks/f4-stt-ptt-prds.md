# F4 prep — rewrite AT-02 and HT-01 PRDs, reconcile dual-coverage notes

**Feature id:** `f4-stt-ptt-prds` · **Worktree:** `agent-tts-worktrees/f4-prds` · **Branch:** `docs/f4-stt-ptt-prds` (from `main` @ `440838c`)
**Source:** `docs/voice-stack/ROADMAP.md` §3 (D1a-D1c) and §4-F4/F5; maintainer design decisions of 2026-10-07.

## Objective
Documentation only. Make the PRDs match the decided F4 design so implementation can start from a truthful, task-sliced plan. No code, tests or config.

## Binding design decisions (2026-10-07)
1. STT is reused from the brain: `POST /transcribe` (multipart field `audio`, returns `{"text": ...}`). No whisper.cpp, no engine STT layer.
2. Microphone capture runs through PowerShell on the Windows host, following the playback targets `winhost` / `wsl-ps` pattern. WSL2 has only `parecord`.
3. Injection uses `herdr pane send-text <PANE_ID> <TEXT>` (literal, no Enter) followed by Enter per `TTS_PTT_ENTER` (`ask|always|never`). `send-text` does not append Enter; `herdr pane run` does and is NOT used. `send-keys` is NOT used for dictated text.
4. Brain unavailable: visible warning in the overlay, nothing injected, the plugin never starts or manages the brain.
5. Only `toggle` mode (press to start; same chord or a silence timeout stops). `hold` is dropped because the keymap fires on press only (unverified in Herdr code: state it as an assumption to verify during implementation).
6. AT-02 and HT-01 each have an independent on/off toggle; both off means behavior identical to today.
7. HT-04 stays alive and reframed: ntfy action buttons call the brain endpoints (`/approval/*`, `/ask`); no independent listener. Depends on F2.

## Tasks
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

## Next step
Run the writer for T1-T5. (Hecho; revisión por parte del maintainer y merge deciden el siguiente paso: implementación F4.1-F4.9.)
