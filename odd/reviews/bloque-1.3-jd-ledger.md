# Judgment Day ledger — BLOQUE 1.3 (AT-08) · ROUND 1

- **Target (immutable)**: worktree `agent-tts-worktrees/bloque-1.3`, branch `feat/at-08-cola-y-cadena`, range `885442f..7086555` (14 commits, 32 files, +9076/−567).
- **target_identity (sha256 of diff)**: `1f545453f784c94522ab1826134796b7966cf6388a4e1de30d695c1eaa9c5d99`
- **Method**: blind dual judges (`jd-judge-a`, `jd-judge-b`), read-only, parallel, identical scope/criteria; JSON native result shape; no refuter (dual agreement = corroboration).
- **Counts**: confirmed-severe **2** · confirmed-non-severe **1** · suspect (single-judge) **4** · info **6** · contradictions **0**.
- Round-1 verdict: **fix round pending user approval** (gate: ask before round-one correction).

## Confirmed severe (both judges → round-1 fix candidates)

- **R1-01 · [A:WARNING + B:CRITICAL] Preempt/watchdog dispatch without waiting the terminated session** — `fixed (round 1)` — `src/agent_tts/queue_manager.py:477-489,515-527,650-656` (+ `daemon.py:942-963`, `audio.py:552-621`). terminate() only sets flags; device closes asynchronously on the playback thread; manager clears `_active` and dispatches next immediately. No-overlap (RF-AT-08-6) enforced only on the natural-finish path; milestone tests exclude the preempted interval. Deterministic reasoning chain; B graded deterministic.
- **R1-02 · [A:WARNING + B:CRITICAL] Watchdog wedge never releases the blocking-play waiter nor decrements `_inflight`** — `fixed (round 1)` — `src/agent_tts/queue_manager.py:477-489` + `daemon.py:705,871-874,942-965,443-448`. `waiter.event.wait()` without timeout; waiter released only in the wedged worker's `finally`; shutdown's stranded-waiter sweep can't reach it (removed at dispatch). Client blocks forever; `_idle_expired()` can never fire again.

## Confirmed non-severe (both judges, WARNING → info per protocol; flagged pre-freeze)

- **R1-03 · [A2+B5] Frozen contract vs code: `interrupted_count` semantics** — contract says "cut by stop-or-preempt"; code finalizes user `stop` as `completed_count` (preempt/shutdown increment `interrupted`). Pinned by tests. `docs/ipc-contract-v2.md:263-278`.

## Suspect (single-judge → user acts as corroborator, como en 1.2)

- **R1-04 · [A, CRITICAL] Coalesced chain item discards the merged announcement** — `fixed (round 1)` — `daemon.py:875-910`: coalesce override replaces `text`/`file` but leaves `chain`; worker tests `chain` first → owner's chain audio plays, the synthesized summary (visible in snapshot/ack) is silently dropped. Combination is a supported wire shape (`_chain_payload_error` doesn't forbid it).
- **R1-05 · [A, WARNING/security] `enqueue` forwards client `_waiter` key** — `fixed (round 1)` — `daemon.py:762-822`: `_waiter` not stripped from client envelope; guessable sequential token (itertools.count) → premature release of another connection's blocking play. Play path overrides it; enqueue doesn't.
- **R1-06 · [B, WARNING] Sentence/paragraph queries during chain inter-item gap resolve to the final chain item** — `chain.py:179-216` + `boundaries.py:132-175` (fallback to last entry when no interval matches).
- **R1-07 · [B, WARNING] Contract promises trailing `text=` in status; remote/PowerShell sessions omit it** — `winhost_client.py:331-343`, `powershell_playback.py:296-309` vs `docs/ipc-contract-v2.md:152-163` + `ipc.py:313-345`: multi-word sentence truncates to first token via `--ipc-json` on those targets.

## Info (single-judge WARNING/SUGGESTION; no fix by protocol)

- **R1-08 · [A]** trace `finalize outcome=` vocabulary in contract (`completed|failed|interrupted|wedged`) vs real (`completed|stopped|failed`; interrupted/wedged never emitted).
- **R1-09 · [A]** `scripts/chain_metrics.py` US-AT-08-3 metric hardcoded `True` (property genuinely proven in CI; harness claim is redundant fabrication).
- **R1-10 · [A]** `scripts/queue_metrics.py` `"long_done_interrupted": True` hardcoded, value available but unused.
- **R1-11 · [A]** wsl-ps natural end answers `status=stopped` (`finish()` sets `state["stop"]` on natural drain); disposition unknown (judge had no git access to BASE).
- **R1-12 · [A]** `--no-play` silently ignored with `--play-chain` (daemon's typed error unreachable from CLI).
- **R1-13 · [A]** inverted comment `queue_manager.py:705-713` (describes skip branch inside the register branch).

## Judge evidence (merged)

A: full reads of queue_manager.py (761 l), daemon.py (1512 l), chain.py, ipc.py, cli.py (918-1609), audio.py (480-629), powershell_playback.py (400-499), ipc-contract-v2.md (428 l, field-by-field vs code), block PRD evidence tables, 4 test files (888+614+262+367 l), both metrics scripts. B: queue scheduling/watchdog, daemon adapter/waiter/chain dispatch/shutdown/status, chain+boundaries, framing/reply parsing, local/remote/PS session stop/status semantics, normative docs, queue/IPC/CLI/milestone/chain tests. Both confirmed the listed intentional decisions and excluded them.

## Budget state

Rounds used: 1 fix · 1 re-judgment. Remaining: 1 fix · 1 re-judgment. Terminal verdicts only: APPROVED | ESCALATED.

---

# ROUND 1 RE-JUDGMENT (scoped: frozen ledger + delta 7086555..d3ef981, delta_identity b5a98ef04852b59fe7f7047684b428a3f2ee0af4d4a41184b410aae4aa332a5a)

Both judges verified the four fixes land (mechanisms, idempotency, tests sound and strengthened — the milestone no-overlap assertions now INCLUDE the preempted interval). No dual-confirmed findings. Single-judge findings:

- **R2-01 · [B, CRITICAL, fix-caused, deterministic] Watchdog-finalized blocking play can answer `status=stopped` instead of the watchdog failure** — `queue_manager.py:571-573` + `daemon.py:973-1003,1031-1034`: a stop-responsive worker releases the waiter with STOPPED during the manager's bounded wait; the delayed hook no-ops (record already popped) so the FAILED watchdog outcome never reaches the client. Test gap: only the ignore-terminate worker is pinned (`tests/test_queue_ipc.py:532-576`). **→ fixed (round 2).**
- **R2-02 · [A, SUGGESTION→promoted, fix-caused] Finalize hook discards the session from `_sessions` while its worker may still be alive** — `daemon.py:1031-1043` vs `_shutdown()` stop-set (`daemon.py:541-548`): a wedged/expired session loses its second stop attempt (RS-1 "no audio outlives the daemon" edge). **→ fixed (round 2).**
- **R2-03 · [A, WARNING, fix-caused, RESIDUAL ACCEPTED by user]** Preempting-enqueue reply worst case on remote/winhost targets can exceed `CLIENT_TIMEOUT_SEC=1.0` (termination wait ≤0.5 s + dispatch gateway subprocess up to 2.0 s + connect timeouts inside the held dispatch section). Daemon-side preempt+dispatch succeed; client-side ack may time out. Documented residual; local targets unaffected (measured healthy unwind tens of ms). **→ residual accepted by user.**

Budget state: rounds used 1 fix + 1 re-judgment; round 2 (final) authorized by user for R2-01 + R2-02.

---

# ROUND 2 RE-JUDGMENT (FINAL; scoped: ledger + delta d3ef981..eb21ad6, delta2_identity 97b611da2e3f55bcb8439c40ec46d1bcf31a390f18c19eb23375ff1ca54a5554)

Both judges confirm R2-01's stop-responsive case is FIXED and R2-02 lands correctly (tests sound, non-tautological). Residual findings after the final round:

- **R3-01 · [B:CRITICAL + A:WARNING — same finding, severity DISPUTED] Narrow spontaneous-end window between the watchdog verdict and the finalize hook** — `queue_manager.py:566-579` + `daemon.py:983-1005,1041-1044`: a worker that ends SPONTANEOUSLY inside the `_finalize_active_locked`→`_notify_finalize` gap releases its waiter first with its own (natural) outcome; the delayed hook no-ops. Client-facing outcome in that window is the worker's truthful natural end (done/stopped); the queue snapshot nevertheless counts failed+wedged. No overlap, no hang; degenerates to a queue-counter miscount in a microsecond-scale race. R2-01's stop-responsive window is closed (pinned by test); this is the narrower remainder. Test gap: no test drives a spontaneous end inside 566→579. **→ RESIDUAL ACCEPTED BY USER (2026-09-25): documented, no fix in this block.**
- **R3-02 · [A, SUGGESTION] `worker is None` disjunct in the liveness gate has inverted rationale and guards the one case where a worker is about to run** — `daemon.py:1010-1014,1059-1060`; harmless today solely because `Daemon._shutdown()` snapshots `_sessions` before `queue_manager.shutdown()` fires its hook.
- **Budget**: 2/2 fix rounds and 2/2 re-judgments used. Protocol: remaining corroborated finding after round two with disputed severity → **JUDGMENT: ESCALATED ⚠️** (explicit human decision required; no delivery authority either way).
- **Closure (2026-09-25)**: user accepted R3-01 as a documented residual; R3-02 remains informational. JD closed. Block proceeds with residuals: R2-03, R3-01, R3-02, suite-flake robustness note, kokoro legs pending, smoke 8h launched post-decision. Merge/push remain the maintainer's decision.

## Post-JD residuals (registered, user-visible)

- R2-03 (accepted): preempting-enqueue reply worst case on remote/winhost targets can exceed the 1 s client timeout.
- Suite robustness: rare load-dependent single-test flakes in daemon lifecycle/queue tests (idle-clock, shutdown-registration); every observed instance passes in isolation; product invariants unaffected per both judges' analyses.
- Pending metrics: RAM kokoro (not installed), smoke 8h (harness launch pending user's post-JD decision).
