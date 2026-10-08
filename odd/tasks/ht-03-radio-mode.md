# HT-03 — Radio mode (voice triage bulletin)

**Status**: ✅ Implemented, uncommitted (commit left to the parent controller; see Evidence) · **Branch**: `feat/ht-03-radio-mode` (worktree `~/Code/personal/agent-tts-worktrees/ht03-radio`, base `main` = `a12e727`)
**Source of truth**: `hosts/herdr/tts-plugin/docs/prds/HT-03-radio-mode.md` (RF-HT-03-1..8) and `hosts/herdr/tts-plugin/docs/prds/bloques/BLOQUE-4-radio-y-verificacion.md` (Hito 1).
**Engram topic**: `odd/ht-03-radio-mode/tasks` (project `agent-tts`).

## Objective

One key (`herdr-tts --radio`, keymap command id `radio`, no default chord) plays a spoken, prioritized bulletin of every chat that asks for attention: `blocked` first, then `done` by recency, each as "agent + chat title (<= 40 chars)" followed by a 1-2 sentence offline heuristic TL;DR, closed by "N chats más silenciosos omitidos". It is interruptible (`--stop` cuts it, re-pressing restarts it with fresh data) and debounced (10 s).

## Scope

In scope (Hito 1 + the Hito 2a/2b plumbing that falls out of the core):

- `herdr-tts --radio` (+ `radio` alias) and keymap id `radio` (`check/emit/apply/adopt` treat it like any other id).
- Selection from `herdr agent list` merged with the snooze/mute ledger and the audio-history recency used by the dashboard; cap `TTS_RADIO_MAX_CHATS` (default 6).
- Per-chat header (HT-02 agent name + voice when active) and `--tldr` body rendered through the engine CLI.
- Dispatch through the engine priority queue (`pending_queue.py enqueue-file --priority blocked|done|working`), sequential fallback under the playback mutex when the queue is unavailable.
- Cancel by identifiers on `--stop`/`stop_audio` and on re-press; 10 s debounce.
- "radio in progress" marker on the dashboard config line.

Out of scope (per PRD): text/screen bulletin, ntfy delivery (HT-08), user-defined order, and RF-HT-03-9 (`TTS_RADIO_LLM_SUMMARY`, Hito 2c — experimental, follow-up).

## Design notes

- All new bash lives in ONE block in `bin/herdr-tts` (between the push-to-talk section and the keymap section) plus: a `radio_cancel` hook at the top of `stop_audio`, the `radio` keymap catalog entries, the `--radio|radio` dispatch case, i18n keys, and a suffix on the dashboard config line. `send_ntfy_push` is NOT touched (parallel work in another worktree).
- The bulletin is produced by a detached background worker so the key press returns immediately. Items are enqueued in play order; because blocked items are enqueued before done items, queue priority and play order agree.
- Cancellation uses deterministic identifiers `radio-<runid>-<n>` (n <= 2*cap+1), so cancel needs no per-item bookkeeping and stays idempotent. State lives in `$STATE_DIR/radio.state` (key=value, no jq on the dashboard path).

## Checklist

- [x] **R0** Read PRD, BLOQUE-4, harness pattern, ledgers/mute/snooze/stop/queue plumbing.
- [x] **R1** RED: `tests/radio_cases.sh` written first, registered in `all_bash_harnesses.sh`, failing for the intended reason (no `run_radio`).
- [x] **R2** GREEN: selection/order + header/TL;DR rendering + queue dispatch (`radio_orders_blocked_before_done`, `radio_respects_mute_and_snooze`, `radio_omitted_count_formula`).
- [x] **R3** GREEN: stop/cancel + debounce/restart (`radio_stop_cancels_playback`, debounce case).
- [x] **R4** Sequential fallback, empty/global-snooze paths, HT-02 header, CLI dispatch, keymap id.
- [x] **R5** REFACTOR with harness green; README + keymap docs; dashboard marker.
- [x] **R6** Docs: PRD HT-03 -> `archivadas/` (EXECUTED), BLOQUE-4 Hito 1 COMPLETADO, PRD index links.
- [x] **R7** Verification: `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh`, `PYTHONPATH=engine/src uv run --with pytest pytest hosts/herdr/tts-plugin/tests -q`.

## Route

R0 -> R1 (RED) -> R2/R3/R4 (GREEN, one behavior commit unit) -> R5 -> R6 -> R7.
Single work unit: `feat(tts-plugin): add radio mode triage bulletin` (code + tests + docs together).

## Evidence

- **RED**: `tests/radio_cases.sh` written first and registered in `all_bash_harnesses.sh`; initial run failed because `run_radio` did not exist (R1).
- **GREEN**: `bash hosts/herdr/tts-plugin/tests/radio_cases.sh` -> 11 cases `CASE radio_* OK` (orders_blocked_before_done, respects_mute_and_snooze, stop_cancels_playback [<0.3 s asserted], omitted_count_formula, debounce_and_restart, sequential_fallback_without_queue, header_title_truncation_and_ht02_identity, keymap_command_id, cli_flag_dispatch, expired_state_is_swept_without_cancel, without_attention_chats_is_silent).
- `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` -> exit 0, 130 `OK` lines, no `FAIL`.
- `PYTHONPATH=engine/src uv run --with pytest pytest hosts/herdr/tts-plugin/tests -q` -> 132 passed, 1 skipped, 7 failed. The 7 failures are all in `test_segmented_render.py` (`No module named 'miniaudio'`) and are IDENTICAL on a clean `git archive HEAD` export of the base (`a12e727`): pre-existing environmental, not caused by this change.
- **Commit**: `aa835d8` `feat(tts-plugin): add radio mode triage bulletin (HT-03)` — created by the parent after spot-checking the harness (exit 0, 130 OK / 0 FAIL, all radio cases OK). Known environmental: 7 pytest failures in `test_segmented_render.py` (`ModuleNotFoundError: miniaudio`), identical on base `a12e727` — pre-existing, not introduced by this change.
- **PRD vs brief discrepancies**: engine queue labels are `blocked|done|working` (no `normal`), so `done` chats use `done` and the closing phrase uses `working`; PRD closing wording is Spanish ("N chats mas silenciosos omitidos"), kept for ES and mirrored in EN; "omitted" counts every roster chat not in the bulletin (superset of PRD working/idle), documented in the archived PRD.
- **Merge surface in `bin/herdr-tts`**: i18n keys (EN/ES) after `help.ptt`, `radio_cancel` call at top of `stop_audio`, dashboard config line suffix, one radio block before "CLI Entrypoint", keymap catalog entries, `--radio|radio` dispatch, help line. `send_ntfy_push` untouched.
