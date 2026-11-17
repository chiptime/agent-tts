# HT-19 ejecución — F2 (N2 atención) + F2b (medición de caché) + F3 (N3 pre-enfriamiento)

Objective: completar el desarrollo aprobado de la matriz HT-19 en `feat/ht19-notification-matrix` (worktree `~/Code/personal/agent-tts-worktrees/ht19`), sin merge ni push (el maintainer publicará más tarde).
Branch: `feat/ht19-notification-matrix`, base `995be66`; F1 ya commiteada (`55c96d6`, `c8761ca`).
Delivery strategy: `ask-on-risk` — commits por unidad en el branch; merge/push HELD por decisión del maintainer ("no lo pushe todavía, lo hacemos más tarde").
Test runner: plugin → `bash hosts/herdr/tts-plugin/tests/host_cli_cases.sh`; engine → `cd engine && python -m pytest tests -q` (la forma CI; desde la raíz falla por namespace package).

## Specs

S1 (F2 = N2, PRD HT-19 RF-2 + Decisiones de ejecución): push de `done` gated por atención con `NTFY_DONE_UNATTENDED_ONLY=on` default; `off` = request byte-idéntica a la de hoy; `blocked` (N1) jamás se gatea; la voz no se toca.
S2 (F2b, PRD fase F2b + Decisiones): sondeo read-only de usage/caché desde los transcripts (motor, dueño del transcript); reporte por sesión de hit/miss tras gaps y TTL observado; degradación honesta "no medible para <agente>"; sin push, sin daemon, sin red.
S3 (F3 = N3, PRD RF-3 + Decisiones): `NTFY_PRECOOL=off` default; `on` = a `T_cold − T_warn` UNA publicación con seq del pane que de ahí solo se actualiza; UNA escalada a prioridad 5 en `T_cold`; atender corta la secuencia; con `on` el push final de alta de HT-05 difiere a N3; con `off` HT-05 byte-idéntico.

## Tasks

| ID | Spec | Route | Status | Commit / evidencia |
|---|---|---|---|---|
| T0 | PRD F2b + decisiones + este pack | inline | done | `92ddfd0` |
| T1 | S1+S3 (plugin bash) | delegated (gentle-ai-worker) | done | `a667560` — 72/72 host_cli (11 casos nuevos), goldens off/default byte-idénticos |
| T2 | S2 (engine python) | delegated (gentle-ai-worker) | done | `d6be49e` — 19/19 focused, suite 1534 OK, ruff limpio |
| T3 | seam: harnesses completos del plugin + engine suite | inline | done | `all_bash_harnesses.sh` **203 OK · 0 FAIL** (parent re-run); engine full 1534/2 skip (T2) + spot 19/19 (parent) |
| T4 | verificación independiente T1 (Risk item 3) | gentle-ai-verify | **unavailable** | límite de uso del proveedor del subagent; ver abajo L4 — decisión del maintainer pendiente |
| T5 | cierre docs + commits | inline | done | docs final + 5 commits en el branch; **merge/push HELD** (L2) |

## Log

L1 (2026-11-17, verbatim): "Me cierra, dejémoslo así y termina el desarrollo."
L2: El maintainer retuvo el push explícitamente ("no lo pushe todavía, lo hacemos más tarde") — este pack NO publica. La aprobación cubre la matriz tal como quedó (N3 = reloj de atención; lo de caché pasa a medición F2b, sin diseño de push sobre TTLs supuestos).
L3: Reparto: T1 y T2 son unidades independientes con surfaces disjuntos (plugin vs engine) → dos writers en paralelo en el mismo worktree; el parent es dueño de git.
L4 (verificación T4 — cerrada en dos rondas): primer intento unavailable (usage limit); relanzado a pedido del maintainer. Veredicto: **2 blockers** — (1) gate de done con muestra única de foco (RF-2 exige sostenido >2 s; glancería breve no debe silenciar un push), (2) precool on solo colapsaba el push final de HT-05; los fallbacks intermedios (muteado/ocupado) seguían emitiendo pushes standalone. Batch único de corrección: `FOCUS_SINCE` + `focus_sustain_sample` (muestreo en wake + post-settle del watcher; sin historia → push; stricto `> 2`), y colapso TOTAL del lado push de HT-05 bajo precool (`NTFY_PRECOOL != on && (priority || !speak_local)`). RED→GREEN 4 casos nuevos; parent recheck 76/76 y all_bash_harnesses 207/0. Voice de HT-05 intacta; goldens off/default intactos.
L5 (entrega): ciclo cerrado en `feat/ht19-notification-matrix` con 5 commits (`55c96d6` docs PRD, `c8761ca` F1 idle, `92ddfd0` pack F2b, `a667560` F2+F3, `d6be49e` F2b, + docs final). **Sin merge ni push** — el maintainer publicará más tarde (L2). Knobs: `NTFY_DONE_UNATTENDED_ONLY=on` (default), `NTFY_PRECOOL=off` (default; requiere `TTS_REMINDERS=on` — monta sobre el sweep de HT-05), `agent-tts cache-report` para medición.
L6 (hallazgos F2b, valiosos más allá del código): opencode.db SÍ registra cache tokens por request (`message.data.tokens.cache.{read,write}`) para todos los proveedores; claude JSONL agrupa chunks por `message.id` (sin agrupar, el gap se corrompe con ruido de ~1s) y expone los tiers `ephemeral_1h/5m`; en la máquina real: sesión opencode de 178 requests, 98.9% hit ratio, **hits a 251.9s y misses a 221.3s simultáneos → TTL de GLM inconcluyente por gap solo** (el reporte lo declara honestamente). Codex deduplica snapshots idénticos; aider/antigravity: no medible.
L7 (cambio de requisito N3, 2026-11-17, verbatim): "Lo que quedaría el TTL en 24 minutos o más y que la notificación debería llegar para cuando esos 24 ya se hayan cumplido, es decir, unos 15 minutos después de la última conversación para que haya un margen a que al minuto 24 ya se enfríe la conversación." → El reloj de N3 pasa de atención pura a **ancla de caché**: nueva medición sobre sesión continua (108 requests, 95.4% hits, miss solo tras 2.3 h) dio ventana limpia de TTL **1451.8s–3922.7s** — la ventana "inconcluyente" anterior era contaminación por sesión intercalada (invalidación de contenido, no expiración). Defaults retuneados: `COLD_S 600→1440`, `WARN_S 180→540` (aviso ~15 min, escalada ~24 min). RED→GREEN del caso `precool_defaults_are_cache_anchored`; suite 77/0. Caveats documentados en la PRD: TTL por proveedor y mutable sin aviso; para Anthropic (5 min) configurar COLD_S aparte.
