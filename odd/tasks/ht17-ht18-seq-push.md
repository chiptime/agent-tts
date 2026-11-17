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
| T0 | docs | inline | done | PRDs HT-17/18 + índice README + este doc |
| T1 | S1 | delegated (gentle-ai-worker) | pending | — |
| T2 | S2 | delegated (gentle-ai-worker) | pending | — |
| T3 | seam: suites completas plugin+engine en el worktree | inline | pending | — |
| T4 | verificación independiente HT-18 (Risk item 4: concurrency) | gentle-ai-verify | pending | — |
| T5 | review assess por unidad (RDD) | inline | pending | — |
| T6 | merge a `main` + push + update ESTADO-Y-PENDIENTES | inline | pending | — |

## Log

L1 (2026-11-17, verbatim): "abordalo, cunado termines mergea a master y pushea"
L2: Antes de arrancar se detectó el checkout sobre `feat/live-call-latency` (7 commits propios del maintainer); se creó worktree aislado desde `main`; el checkout original quedó limpio. `main` = `origin/main` (`1b71a25` de antemano, 0 commits sin publicar) — el push final publica únicamente este merge.
L3: Decisión de diseño heredada de las PRDs: defaults `NTFY_SEQUENCE=off` y `TTS_HEARTBEAT=off` (convención opt-in del repo, como HT-05); nadie cambia comportamiento existente sin opt-in explícito.
