# Feature: Autonomous Sequential Implementation Loop (autoloop)

**Repo**: `~/Code/personal/agent-tts`
**PRD**: `docs/prds/herdr-brain-on-demand-context.md` — FR-42..45, D10,
"Future Implementation Loop FSM" section (specified, not built — this feature
builds it; explicit user authorization given 2026-10-01: "levantemos el bucle
autonomo como sistema").
**Created**: 2026-10-01
**Status**: bootstrap (feature doc + branch only; slices not started)

## Objective

A dev-time system that executes an agreed task list autonomously and
sequentially — one writer at a time, in isolated Git worktree(s) under the
home directory — until all agreed features complete, with deterministic
milestone gates, verification-evidenced done, bounded retries, resume
reconciliation after interruption, and an actionable terminal blocked state.

## Problem / Why

D10 authorized this execution model; the PRD specified it as FR-42..45 but
never built it. Today the discipline exists only interactively (an orchestrator
agent following it by hand). This feature turns it into a runnable system.

## Verified planning facts (2026-10-01)

- **Executor**: `opencode run` CLI is installed (headless agent execution).
  Plan: one `opencode run` subprocess per task, cwd = the loop's worktree,
  prompt built from the ledger entry. Adapter-isolated behind an Executor
  protocol so the executor can be swapped/faked in tests.
- **Attempt authority**: `gentle-ai sdd-attempt` runtime operations
  (acquire/settle) are RETIRED in the installed build (only `grant` remains).
  Therefore the loop owns its finite attempt counters in its own ledger —
  per PRD D10: finite attempt defaults are implementation design, never
  invented native CLI commands. Documented here so nobody rediscovers it.
- **Conventions to mirror**: hosts/herdr/brain module (strict TDD, stdlib
  sqlite stores, frozen dataclasses, injectable clocks, bounded purges).

## Hard constraints (from FR-42..45 / D10 — non-negotiable)

- Worktrees under the home directory, NEVER `/tmp`; per-worktree own CodeGraph
  index if enabled; never copy/symlink indexes; never switch the source
  checkout.
- One writer at a time; isolated branch/session per loop run.
- Task/dependency ledger with checkpoints; done REQUIRES verification
  evidence (command + result recorded).
- Bounded retries/corrections with finite defaults; BLOCKED is terminal,
  actionable, never auto-retried, never skipped past.
- NO automatic merge/push/PR. No cleanup that could destroy user changes.
- Existing TDD config preserved; native RDD selection/consent stays
  user-owned; review is NEVER self-approved.
- Final state: deterministic milestone gates + all-features acceptance
  checklist, then HALT_AWAIT_USER.

## FSM (from the PRD, normative)

AUTHORIZE (explicit user authorization of worktree root, branch, session)
-> SELECT_NEXT_TASK (ledger order, one writer)
-> EXECUTE -> VERIFY (evidence required for done)
-> on success CHECKPOINT -> next task
-> on bounded correction EXECUTE (finite attempts)
-> on exhaustion/unrecoverable error BLOCKED (terminal, actionable)
MILESTONE_GATE between milestones; ALL_FEATURES_ACCEPTANCE then HALT_AWAIT_USER.

## Slices (each a work-unit commit, strict TDD)

- [ ] **L1** Ledger core: task/dependency records (task_id, depends_on,
  status pending|in_progress|done|blocked, attempts, max_attempts,
  evidence_ref, blocked_reason actionable), sqlite store mirroring
  reportstore conventions, checkpoint writes, cycle detection, one-writer
  invariant. Pure library + tests.
- [ ] **L2** Worktree manager: create/locate loop worktree under a
  configurable home root, branch from a pinned base, per-worktree state,
  safe-cleanup rules that refuse to destroy uncommitted user changes.
- [ ] **L3** Executor adapter: Executor protocol + OpencodeRunExecutor
  (subprocess, cwd = worktree, prompt from ledger entry, timeout) + Fake
  executor for tests.
- [ ] **L4** Verify + gates: verification runner (test command per task,
  evidence recorded), milestone gate evaluation, all-features acceptance
  checklist generation.
- [ ] **L5** Orchestrator: the FSM itself — resume reconciliation from
  checkpoints after restart, bounded correction loop, BLOCKED terminal
  state with actionable text, HALT_AWAIT_USER.
- [ ] **L6** CLI entry + docs: `python -m autoloop` (or repo-equivalent),
  authorization subcommand (records explicit user authorization),
  run/resume/status; README.

## Location decision (revisitable)

New repo-level Python package `tools/autoloop/` with its own tests directory,
stdlib-only, mirroring brain-module conventions. Chosen because the loop is
dev-time repo tooling, not brain runtime; revisit during L1 if a better home
emerges (e.g. scripts/).

## Open questions

- Executor prompt shape and per-task agent/model selection (user-owned per
  D10/RDD rules — the loop must not decide models silently).
- Whether `opencode run` needs explicit permission flags for headless use.
- Worktree root default (e.g. `~/Code/personal/agent-tts-worktrees/autoloop-<slug>`).
- Max attempts default per task (finite; propose 2 in L1, revisitable).

## Progress / Evidence

- 2026-10-01 bootstrap: branch feat/autoloop created from main@554e94a;
  this doc committed; no code yet.
