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
| T0 | PRD F2b + decisiones + este pack | inline | done | pendiente de commit (docs) |
| T1 | S1+S3 (plugin bash) | delegated (gentle-ai-worker) | pending | — |
| T2 | S2 (engine python) | delegated (gentle-ai-worker) | pending | — |
| T3 | seam: host_cli completo + engine suite en el worktree | inline | pending | — |
| T4 | cierre docs (PRD/ESTADO/README) + commits | inline | pending | — |

## Log

L1 (2026-11-17, verbatim): "Me cierra, dejémoslo así y termina el desarrollo."
L2: El maintainer retuvo el push explícitamente ("no lo pushe todavía, lo hacemos más tarde") — este pack NO publica. La aprobación cubre la matriz tal como quedó (N3 = reloj de atención; lo de caché pasa a medición F2b, sin diseño de push sobre TTLs supuestos).
L3: Reparto: T1 y T2 son unidades independientes con surfaces disjuntos (plugin vs engine) → dos writers en paralelo en el mismo worktree; el parent es dueño de git.
