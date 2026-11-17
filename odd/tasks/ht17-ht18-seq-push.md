# HT-17+HT-18 — Notificaciones push secuenciales (update in-place + heartbeat)

Objective: implementar HT-17 (sequence IDs en pushes ntfy del plugin) y HT-18 (heartbeat del daemon como dead man's switch) según sus PRDs, con tests, y entregar con merge a `main` + push (autorizado explícitamente por el maintainer).
Branch: `feat/ht17-ht18-seq-push` (worktree `~/Code/personal/agent-tts-worktrees/ht17-ht18`), base `main` @ `1b71a25`. El branch `feat/live-call-latency` (7 commits propios) queda fuera de este trabajo.
Delivery strategy: `ask-on-risk` → cierre con un merge a `main` y push al final (ya autorizado).
Test runner: plugin → `bash hosts/herdr/tts-plugin/tests/host_cli_cases.sh` + `python -m pytest hosts/herdr/tts-plugin/tests -q`; engine → `python -m pytest engine/tests -q`.

## Specs

S1 (HT-17 completa — PRD: `hosts/herdr/tts-plugin/docs/prds/HT-17-notificaciones-actualizables.md`):
- RF-1: identidad de secuencia derivada del pane/agent id, saneada a `[-_A-Za-z0-9]` (≤ 64), estable entre eventos del mismo agente.
- RF-2: cada evento publica con ese sequence ID (`POST /<topic>/<seq>` o header `X-Sequence-ID`); el cliente reemplaza la notificación previa.
- RF-3: `clear`/`delete` tras `done` solo en modo `update+clear`, y solo si hubo notificación previa del mismo seq.
- RF-4: botones HT-04 y pushes HT-05 preservan comportamiento (los `http` actions van dentro del mensaje update; el push de prioridad alta de HT-05 puede no usar seq).
- RF-5: ante rechazo del seq por el server, fallback silencioso a one-shot con una línea de log.
- RF-6: `NTFY_SEQUENCE=off` reproduce el comportamiento actual exacto (default `off`).
- RNF-3: casos `ntfy_*` de `tests/host_cli_cases.sh` cubren seq, fallback y knob.

S2 (HT-18 completa — PRD: `hosts/herdr/tts-plugin/docs/prds/HT-18-heartbeat-dead-mans-switch.md`):
- RF-1: con `TTS_HEARTBEAT=on`, thread en el daemon que publica cada `interval` con `In: <window>` y seq `heartbeat-<host_sanitizado>`; muerte limpia con el `Event` de stop existente.
- RF-2: validación `window >= 3 * interval` al cargar config; inválida = no armar + error claro.
- RF-3: gate de capacidad del server antes de armar; sin soporte, no armar.
- RF-4: `TTS_HEARTBEAT=off` (default) = cero publicaciones, comportamiento actual exacto.
- RF-5: fallos de publicación aislados no matan el loop; el loop nunca rompe audio ni IPC.
- RF-6: `status` reporta estado del heartbeat.
- RNF-1: sin dependencias nuevas (stdlib `urllib`). RNF-3: tests engine cubren armado/no-armado, validación de ventana, saneado, cuerpo/headers (mock HTTP), shutdown limpio.

## Tasks

| ID | Spec | Route | Status | Commit / evidencia |
|---|---|---|---|---|
| T0 | docs | inline | done | `4f3daf5` |
| T1 | S1 | delegated (gentle-ai-worker) | done | `59c3ba4` — 58/58 casos plugin, diferencial off byte-idéntico |
| T2 | S2 | delegated (gentle-ai-worker) | done | `8c331c8` + fix `7f194de` — 21/21 focused, daemon suites 85 OK, engine suite 1513 OK |
| T3 | seam: suites completas plugin+engine en el worktree | inline | done | plugin 58/58 (parent re-run); engine 1513 passed/2 skipped (worker, forma CI `cd engine`) |
| T4 | verificación independiente HT-18 (Risk item 4: concurrency) | gentle-ai-verify | done | not-ready → 2 blockers → corrección `7f194de` → recheck 21/21; 2 advisories documentados |
| T5 | review assess por unidad (RDD) | inline | done | assess `high` (shell signals) review_due=true, pero RDD **off (clone_local)** → sin ciclo nativo por decisión del maintainer |
| T6 | merge a `main` + push + update ESTADO-Y-PENDIENTES | inline | done | merge + push origin main (autorizado L1) |

## Log

L1 (2026-11-17, verbatim): "abordalo, cunado termines mergea a master y pushea"
L2: Antes de arrancar se detectó el checkout sobre `feat/live-call-latency` (7 commits propios del maintainer); se creó worktree aislado desde `main`; el checkout original quedó limpio. `main` = `origin/main` (`1b71a25` de antemano, 0 commits sin publicar) — el push final publica únicamente este merge.
L3: Decisión de diseño heredada de las PRDs: defaults `NTFY_SEQUENCE=off` y `TTS_HEARTBEAT=off` (convención opt-in del repo, como HT-05); nadie cambia comportamiento existente sin opt-in explícito.
L4 (verificación T4): el verificador independiente devolvió not-ready con 2 blockers reproducibles: (1) bypass del gate — un fallo transitorio seguido de rechazos permanentes (404) dejaba el loop reintentando en `probing` infinito (RF-3); (2) `TTS_HEARTBEAT_SERVER` malformado (`http://[invalid`) lanzaba `ValueError` post-IPC y tumbaba el startup del daemon (RF-2/RF-5). Advisories documentados SIN fix en este ciclo: resultado del join ignorado (thread puede sobrevivir ≤6 s a teardown) y `_shutting_down` seteado después del join (ventana de admisión). El fallo `test_providers_not_rewritten` en scratch sin .git es idéntico en el baseline (pre-existente).
L5 (corrección, batch único): `7f194de` — `_disarm()` desde cualquier rechazo permanente (gate o beat), `load_heartbeat_settings` nunca raise (config malformada → no armar + error que nombra la var), 2 tests de regresión añadidos. RED→GREEN: 2 failed → 21 passed.
L6 (RDD): assess sobre el diff dio `high` (señales shell_process) con review_due=true, pero `gentle-ai review mode status` reports **off (clone_local)** — decisión previa del maintainer; no se arrancó ciclo de review. Verificación ordinaria completa: focused + daemon suites + diferenciales + verificador independiente.
L7 (entrega): merge `feat/ht17-ht18-seq-push` → `main` y push a origin (autorizado en L1). El branch `feat/live-call-latency` del maintainer quedó intocado (7 commits propios). Riesgo residual compartido: validación física F2 pendiente para ambas features (replace in-place real en Android, entrega de la alerta del heartbeat con daemon muerto).
