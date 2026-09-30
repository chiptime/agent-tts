# Feature: herdr-brain On-Demand Context and Consolidated Reports

**Repo**: `~/Code/personal/agent-tts` (module `hosts/herdr/brain/`)
**Worktree**: `~/Code/personal/agent-tts-worktrees/herdr-brain-ctx`
**Branch**: `feat/herdr-brain-on-demand-context` (from `main` @ `4ed549a`)
**PRD**: `docs/prds/herdr-brain-on-demand-context.md` (FR-01..45, D01..D10)
**Created**: 2026-09-30
**Status**: in progress (T0, T1 done; next T2 `reportstore`)

## Objective

Give the voice brain honest on-demand global/historical context: period-correct,
freshness-validated, persistently cached consolidated reports, without changing
the default selected-session conversation model.

## Problem / Why

See PRD "Context & Problem": no honest global view, no freshness semantics, no
bounded delivery guarantee.

## Scope / Constraints

- In scope: FR-01..41 (runtime brain). Read-only retrieval tooling; existing
  approval gates and the two write paths (`send_to_session`, `create_session`)
  stay untouched.
- Out of scope: FR-42..45 (future autonomous implementation loop) are a
  specification only. This execution is a manual, sequential, single-writer
  task flow; it does NOT build that loop.
- No push, PR, merge. Work-unit commits on the feature branch only.
- Single-model tool loop and provider adapters preserved; no new framework.
- Local data only; no new remote access.
- Planning heuristic: ~400 authored changed lines per task (advisory only).

## Execution settings

- Test runner (verified 2026-09-30): from `hosts/herdr/brain`,
  `uv run --extra dev python -m pytest -q` -> **571 passed** baseline.
  (`python` is not on PATH; bare `pytest` fails on `tests` imports.)
- TDD mode: **ON — strict RED -> GREEN -> REFACTOR**, explicitly selected by the
  user on 2026-09-30 for this feature (source: user choice; older ODD docs say
  OFF and `announcements-without-call` ON for that feature only, neither
  overrides this). Observed RED evidence is required before each GREEN.
- Delivery strategy: `ask-on-risk` (forecast far over ~400 lines) ->
  chain strategy chosen by the user on 2026-09-30: **`feature-branch-chain`**.
  Tracker branch = `feat/herdr-brain-on-demand-context` (draft/no-merge tracker
  PR, created later only on explicit user request; it needs at least one
  commit of its own, e.g. this feature doc, before a PR can be opened).
  Child branches: `feat/herdr-brain-ctx-NN-<slice>`. Child 01 branches from the
  tracker; each later child branches from the previous child. PR #1 targets the
  tracker, later PRs target the immediate parent. No push/PR without explicit
  request. Skills applied: `work-unit-commits`, `chained-pr`.
- Slice boundaries (running count of authored lines, additions + deletions):
  - 01 `feat/herdr-brain-ctx-01-periods`: T1 + this doc (~1.0k lines; slightly
    over budget because tests/docs are irreducible; `size:exception`
    recommendation recorded, no shrinking).
- Engram project: `herdr-brain`; mirror topic `odd/herdr-brain-on-demand-context/tasks`.

## Tasks

Ordered by dependency. Route column is the planned topology.

- [x] **T0** Isolated worktree + branch + baseline. Evidence: worktree list,
  571 passed. Route: inline.
- [x] **T1** `periods`: timezone detect/validate (ask fallback, never guess),
  natural/explicit period resolution, DST-safe math. FR-08..10, FR-37.
  Route: delegated writer (module + tests; writer trigger: 2 non-trivial files).
  Done. Commit on `feat/herdr-brain-ctx-01-periods` (identity recorded below).
- [ ] **T2** `reportstore`: persistent DB, 24h retention, context/scope/
  interval/timezone keys, atomic publish, bounded cleanup. FR-27, 33, 34.
  Depends: T1 (interval normalization). Route: delegated writer.
- [ ] **T3** `evidence` inventory: open Herdr sessions + transcript readers with
  a historical cursor (OpenCode/Claude/Antigravity) + per-source revision
  tokens; absence vs failed coverage. FR-03..05, 11, 12, 40.
  Route: delegated mapper then writer.
- [ ] **T4** Read-only Engram adapter (access mechanism to be verified, A-2).
  FR-05, 31, 38, 39. Depends: T3.
- [ ] **T5** `freshness`: manifest comparison, reuse-after-check, full rebuild,
  bounded full scan, moving windows. FR-28..32, 41. Depends: T2, T3, T4.
- [ ] **T6** `report` consolidation: per-project advances/pending/blockers,
  conflict surfacing, references, grounded completion, empty vs failed.
  FR-13..16, 22, 25. Depends: T3..T5.
- [ ] **T7** Query FSM + server-enforced 60s budget + routing + narrow/
  clarify/unable states. FR-01..03, 17, 19..21, 26, 37. Depends: T1, T5, T6.
- [ ] **T8** `followup` persistent context, selection isolation, expiry.
  FR-07, 23..25. Depends: T2, T7.
- [ ] **T9** Tool/LLM wiring: read-only retrieval tools, injection isolation,
  approval-gate regression. FR-36, 38..41. Depends: T7.
- [ ] **T10** UI: Consulting state + on-screen references (never spoken).
  FR-14, 18. Depends: T7.
- [ ] **T11** Acceptance: Requirement-to-Test Matrix coverage, Verification
  Checklist, docs. FR-45-style gate for this work. Depends: all.

## Acceptance criteria

PRD Metrics & Acceptance table and Verification Checklist; each task closes with
its FRs covered by tests and the full suite green.

## Progress / Evidence

- 2026-09-30 T0: `git worktree add -b feat/herdr-brain-on-demand-context
  ../agent-tts-worktrees/herdr-brain-ctx main`; baseline 571 passed.

- 2026-09-30 T1 started: route = delegated writer (2 non-trivial files:
  `periods.py` + `test_periods.py`; writer trigger). Uncommitted until the
  chain strategy is chosen.

- 2026-09-30 T1 done (strict TDD). RED: `ModuleNotFoundError: No module named
  'herdr_brain.periods'` (1 error). GREEN: 73 passed. Full suite re-run by the
  parent: 644 passed (571 + 73), 0 failures. Files: `src/herdr_brain/periods.py`
  (336 lines), `tests/test_periods.py` (526 lines). 862 authored lines: above the
  ~400 heuristic because the required DST/boundary coverage and rule docs are
  irreducible (advisory only, no rework).
- T1 rule decisions: last-7-days = same local wall-clock time 7 calendar days
  back (167h/169h across DST); gap -> first valid instant after, fold -> earliest
  (fold=0); intervals half-open [start, end); no mtime parameter on
  `classify_timestamp`; naive/fixed-offset explicit bounds rejected; POSIX TZ
  strings rejected; `future_tolerance` default 0.
- Product decisions still open (deliberately not made): `max_span` default
  (None), nonzero default `future_tolerance`, POSIX TZ support.

## Next step

Start T2 `reportstore` on a new child branch `feat/herdr-brain-ctx-02-reportstore`
from child 01.
