# HT-05 — Recordatorios escalados de atención · Execution pack (compact)

> Prepared by the `prd-loop` skill (planning only — nothing here launches implementation).
> Single canonical execution entrypoint. Every other doc is referenced by exact relative path.

- **Root (canonical):** `/home/bruno/Code/personal/agent-tts` — monorepo; the work lives in `hosts/herdr/tts-plugin/`
- **Entrypoint (this doc):** `odd/tasks/ht-05-recordatorios-escalados.md`
- **Source PRD:** `hosts/herdr/tts-plugin/docs/prds/HT-05-recordatorios-escalados.md` — sha256 `94d3a923a8de25fb71ac51eeb5ac40fd3302e2461f9d96bfc81dcfb3283d1803`
- **Block plan:** `hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-3-loop-movil.md`
- **`prd` skill dependency (resolved):** `/home/bruno/.config/opencode/skills/prd/SKILL.md`
- **Baseline:** `HEAD=6ff5d0a3e8de4fd47fda95cf6b66baab2023edf9`, clean working tree on `main`
- **Readiness:** `ready_for_bootstrap` — D1–D3 confirmed by the maintainer (2026-10-11); see **Gaps** below (unverified pytest runner; FUTURE harness gate)

---

## Locked decisions (planner, after repo evidence)

- **D1 — Attention-state store.** Reminder per-pane state lives in the `pending_queue` SQLite DB as a new table `reminder_state` — NOT a second ledger file. Evidence: PRD destination note (HT-05 PRD line 5, "sobre `pending_queue.py`, sin un segundo ledger"); `pane_id` already an indexed dimension (`hosts/herdr/tts-plugin/lib/pending_queue.py:442-447`); durable across daemon restarts (RF-HT-05-2). *Conflict recorded:* RF-HT-05-2's wording ("fichero JSON del gating ledger") is superseded by this destination — the JSON gating ledger still owns mute/snooze/debounce only.
- **D2 — Escalation lives in Python.** Due-computation runs in `pending_queue.py`, invoked from the bash sweep at `bin/herdr-tts:4016` (right after `daemon_title_sync`, before per-pane watcher spawn at `4018-4034`) through a new CLI subcommand. Keeps the O(chats) sweep cost (RNF-HT-05-1) and reuses the loaded agent list (`bin/herdr-tts:4009`).
- **D3 — Delivery reuses existing paths.** Reminder voice goes through the existing speak path with the global debounce; the last reminder adds `-H "Priority: high"` to `send_ntfy_push` (`bin/herdr-tts:2374`). Reminders are NOT admitted as `pending_records` — this protects VS3's capacity invariant `test_no_silent_loss_no_fake_accept` (`hosts/herdr/tts-plugin/tests/test_pending_queue.py:374`).
- **D4 — Reset hooks.** Focus via `get_focused_pane()` (`bin/herdr-tts:2400-2425`); injected response at the PTT `herdr pane send-text` site (`bin/herdr-tts:6342`); agent state→`working` via the parse in `watch_agent_pane` (`bin/herdr-tts:3618-3633`); mute/snooze filtered by the existing gating before the reminder call.
- **D5 — Config surface.** Keys land in the `config_set` whitelist (`bin/herdr-tts:1586`), the per-key validation `case` (`bin/herdr-tts:1600+`), and the defaults block (`bin/herdr-tts:255-306`).
- **D6 — Snippet redaction (corrected 2026-10-11).** The ~120-char actionable snippet (RF-HT-05-8) MUST pass through the engine's secret redactor `agent_tts.redact_secrets` (fail-closed) — NOT `_sanitize_text` (which only clips/collapses whitespace and does not redact). Import lazily inside `reminder_due`; if the engine is unavailable, OMIT the snippet (fail-closed degradation) while the reminder still fires. Precedent: `lib/reader_pipeline.py:39-43,135` imports `redact_secrets` and runs it before any structural decision.
- **D7 — Reminder schedule (locked reading of an ambiguous PRD).** A pane observed in `done`/`blocked` and eligible arms at `first_attention_ts` = first observation. Reminder `k` (1-indexed) fires at `first_attention_ts + (START_MIN + Σ BACKOFF[0..k-2]) · 60` seconds — i.e. #1 at `START_MIN`, then each next gap taken in order from `BACKOFF`. Defaults: **#1 at 5m, #2 at 7m, #3 at 12m**, stop after `MAX`=3. Rationale: `START_MIN` = delay to the first reminder; `BACKOFF="2,5,15"` = the subsequent gaps. *Flag:* the PRD's "2m→5m→15m" phrasing is ambiguous; override if the maintainer wants the gap sequence to start at 2m.

## Assumptions (visible — from PRD open questions; not blockers)

- **A1.** Max reminders `M` is per-chat (per-pane), matching RF-HT-05-2. *(PRD open Q2)*
- **A2.** Focus from the palette/dashboard counts as attention and resets state. *(PRD open Q3, US-HT-05-3)*
- **A3.** Reminder voice = a short prefixed phrase + sanitized snippet using the existing voice; a distinct tone/voice is out of scope (would need engine support). *(PRD open Q1)*
- **A4.** The success metric (median bloqueo→respuesta) needs a real-usage baseline measured by the maintainer BEFORE enabling — the pack cannot fabricate it.

---

## Task registry (single authority)

### T-01 — Reminder state + escalation core (Python)
- Intent: per-pane `reminder_state` + due-computation. Traces to RF-HT-05-1/2/3, RNF-HT-05-1/3.
- Depends: none
- Edit allowlist: `hosts/herdr/tts-plugin/lib/pending_queue.py`, `hosts/herdr/tts-plugin/tests/test_pending_queue.py`
- Contract: add table `reminder_state(pane_id TEXT PRIMARY KEY, first_attention_ts REAL NOT NULL, reminder_count INTEGER NOT NULL DEFAULT 0, next_reminder_ts REAL NOT NULL, last_reminder_ts REAL)` in `_ensure_schema` (`lib/pending_queue.py:1194-1210`); methods `reminder_due(now, candidates, *, start_min, backoff, max_reminders) -> list[dict]` and `reminder_reset(pane_id) -> None`; CLI subcommands `reminders` and `reminder-reset` in `main()` (`lib/pending_queue.py:1657-1756`). Snippet via `_sanitize_text(text, 120)`.
- Test creator: this task adds unit cases to `tests/test_pending_queue.py`
- Evidence: pytest cases for the backoff sequence (2m→5m→15m), max M, reset, persistence across reopen, and O(chats) behavior
- Gate: G-02
- Status: ✅ DONE — commit `56b32bb`. G-02 rc=0 (184 passed); G-01 rc=0 (all suites OK). Independent verification caught a reproducible PEM leak; the fix makes `admit` redact before truncation and `reminder_due` fail closed (snippet omitted on any secret-opener residue).

### T-02 — Config surface
- Intent: `TTS_REMINDERS`, `TTS_REMINDER_START_MIN`, `TTS_REMINDER_BACKOFF`, `TTS_REMINDER_MAX` (defaults off/5/"2,5,15"/3). Traces to RF-HT-05-1. (D5)
- Depends: none
- Edit allowlist: `hosts/herdr/tts-plugin/bin/herdr-tts`, `hosts/herdr/tts-plugin/tests/config_cases.sh`
- Test creator: this task adds accept/reject cases to `tests/config_cases.sh`
- Evidence: `config_cases.sh` shows valid values stored and invalid values refused with no side effect
- Gate: G-01

### T-03 — Sweep integration + delivery
- Intent: invoke reminders from the sweep and emit voice/push. Traces to RF-HT-05-4/5/8, RNF-HT-05-2. (D2, D3, D6)
- Depends: T-01, T-02
- Edit allowlist: `hosts/herdr/tts-plugin/bin/herdr-tts`, `hosts/herdr/tts-plugin/tests/reminder_cases.sh`, `hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh`
- Contract: call the new reminder subcommand after `daemon_title_sync` (`bin/herdr-tts:4016`) with the agent list; emit the short phrase; last reminder adds `Priority: high` (`bin/herdr-tts:2374`); register the new harness in `all_bash_harnesses.sh`.
- Test creator: this task creates `tests/reminder_cases.sh` (FUTURE until done), with stubbed `herdr` and engine
- Evidence: `reminder_cases.sh` (CASE protocol `CASE <name> OK`) covering due→voice, last→high push, and debounce/no-overlap
- Gate: G-04

### T-04 — Reset hooks
- Intent: reset state on attention/response/state-change. Traces to RF-HT-05-3/6. (D4)
- Depends: T-03
- Edit allowlist: `hosts/herdr/tts-plugin/bin/herdr-tts`, `hosts/herdr/tts-plugin/tests/reminder_cases.sh`
- Test creator: existing — `tests/reminder_cases.sh` (created by T-03)
- Evidence: harness cases: focus resets; PTT injection resets; state→working resets; mute/snooze suppresses (RF-HT-05-6)
- Gate: G-04

### T-05 — Dashboard countdown + index/docs
- Intent: per-chat reminder countdown overlay next to snooze. Traces to RF-HT-05-7.
- Depends: T-03, T-04
- Edit allowlist: `hosts/herdr/tts-plugin/bin/herdr-tts`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh`, `hosts/herdr/tts-plugin/docs/prds/README.md`, `docs/ESTADO-Y-PENDIENTES.md`
- Test creator: this task adds a smoke scenario to `scripts/smoke-tests.sh`
- Evidence: `dashboard_render` overlay (`bin/herdr-tts:4690-4709`) shows the countdown; smoke scenario asserts it; PRD index/status updated
- Gate: G-03, G-01

---

## Gate registry (single authority)

| Gate | Command (literal) | cwd | Source policy | Exit meaning |
|---|---|---|---|---|
| G-01 | `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` | `/home/bruno/Code/personal/agent-tts` | the nine plugin bash suites (host_cli, critical, bootstrap, voice, config, lifecycle, keymap, ptt, radio; + reminder after T-03) | 0 = every CASE OK; nonzero = a suite failed |
| G-02 | `PYTHONPATH=engine/src .venv/bin/python -m pytest hosts/herdr/tts-plugin/tests/ -q` | `/home/bruno/Code/personal/agent-tts` | the plugin Python suite (needs the engine on `PYTHONPATH`; runner `.venv/bin/python`, py3.14.8). Verified 2026-10-11: 167 tests collect with `PYTHONPATH=engine/src`; without it `test_reader_alignment.py` errors. Not in CI → local gate. | 0 = pass; nonzero = fail |
| G-03 | `bash hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | `/home/bruno/Code/personal/agent-tts` | hermetic smoke incl. dashboard scenarios | 0 = pass; nonzero = fail |
| G-04 | `bash hosts/herdr/tts-plugin/tests/reminder_cases.sh` | `/home/bruno/Code/personal/agent-tts` | **FUTURE** — created by T-03 | 0 = every CASE OK; nonzero = fail |

CI reference (authoritative for the bash gate): `.github/workflows/ci.yml:22-25` runs `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh`. The plugin pytest is NOT exercised by CI.

---

## Traceability (FR → task → gate)

| FR / RNF | Task(s) | Gate(s) |
|---|---|---|
| RF-HT-05-1 | T-02 | G-01 |
| RF-HT-05-2 | T-01 | G-02 |
| RF-HT-05-3 | T-01, T-04 | G-02, G-04 |
| RF-HT-05-4 | T-03 | G-04 |
| RF-HT-05-5 | T-03 | G-04 |
| RF-HT-05-6 | T-04 | G-04 |
| RF-HT-05-7 | T-05 | G-03, G-01 |
| RF-HT-05-8 | T-03 | G-04 |
| RNF-HT-05-1 | T-01, T-03 | G-02, G-04 |
| RNF-HT-05-2 | T-03 | G-04 |
| RNF-HT-05-3 | T-01 | G-02 |

Every FR maps to ≥1 task; every task maps to ≥1 gate. Counts: **5 tasks**, **4 gates**, **11 requirements** (8 RF + 3 RNF).

---

## Candidate binding (baseline; pre-work)

| File | sha256 |
|---|---|
| `hosts/herdr/tts-plugin/bin/herdr-tts` | `de2fe7c591a2fc7b7645ca67e4d028efbecf12f478f4b4eece6eafb44cd75230` |
| `hosts/herdr/tts-plugin/lib/pending_queue.py` | `9e6d79d84096413bc712956d39c2f6c5d4001230f56b179d513b50b46b33b7ca` |
| `hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` | `f5eb3e3d8b323fb775fb9170dd9fbf4383ef3d49126ecadcf869b1974114296b` |
| `hosts/herdr/tts-plugin/tests/host_cli_cases.sh` | `b6982153dd1326f7b60608da2567a188a031569c6a9af5aaeff063e0051db908` |
| `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | `61a7c6a3537abc265dfe8a8d6f63f376a7897ae75ec010177b94a141e8cbab66` |
| `hosts/herdr/tts-plugin/docs/prds/HT-05-recordatorios-escalados.md` | `94d3a923a8de25fb71ac51eeb5ac40fd3302e2461f9d96bfc81dcfb3283d1803` |

`tests/reminder_cases.sh` does not exist yet (FUTURE — created by T-03). Its gate (G-04) is FUTURE until then. WIP is preserved: the binding diff is against the working tree, not HEAD-only.

## Checks run vs pending

- **Run:** `git rev-parse HEAD`, `sha256sum` of the bound files, `ls tests/`, read of `run_daemon()` / `config_set()` / CI workflow.
- **Pending:** every gate (no reminder code exists yet); discovery of the working pytest runner for G-02.

## Gaps blocking `ready_for_execution`

1. **FUTURE gate on the critical path** — `tests/reminder_cases.sh` (G-04) is created by T-03.

Resolved — D1–D3 confirmed by the maintainer (2026-10-11). G-02 runner resolved and verified (repo-root `.venv`, 2026-10-11). The pack is `ready_for_bootstrap`, not yet `ready_for_execution`.

## Resume bookmark (maintained by the future executor)

```markdown
## Resume bookmark (maintained by the future executor)
- Pack identity: odd/tasks/ht-05-recordatorios-escalados.md — sha256 pending finalization (pack is draft)
- Completed: T-01 (commit `56b32bb`; G-02 184 passed, G-01 all OK)
- Active task: (none) — budget remaining: 2 remediation rounds per milestone
- Next action: T-02 (config surface: `TTS_REMINDER*` in `config_set` whitelist + validation + defaults)
```

## Unresolved choices

- None. The ledger location was locked to `pending_queue` (destination note) and D1–D3 are confirmed by the maintainer (2026-10-11).

## Launch request

See `assets/launch-request-template.md` (filled fields):

- Repository root: `/home/bruno/Code/personal/agent-tts` (canonical; not a fork)
- Pack entrypoint: `odd/tasks/ht-05-recordatorios-escalados.md`
- Pack identity: pending finalization (recompute the entrypoint hash when the pack stops being `draft`)
- Readiness: `ready_for_bootstrap`
- Milestones: BLOQUE-3 Hito 2
- FRs covered: RF-HT-05-1..8, RNF-HT-05-1..3 (count 11)
- Tasks: 5 — Gates: 4
- Non-goals: llamadas telefónicas, integración con calendario, recordatorios por hora del día (HT-08); no engine changes; no `pending_records` admission
- Remediation budget: 2 functional rounds per milestone
- Stop conditions: crash, unknown error class, wrong source (hash mismatch), unavailable gate, native human consent, budget exhaustion
- Candidate binding: baseline hashes above
- Checks already run: static (hashes, paths, CI read) — pending: all gates
- Limitations: this pack authorizes nothing; nothing runs until the user explicitly launches it.
