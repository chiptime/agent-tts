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
- [x] **T2** `reportstore`: persistent DB, 24h retention, context/scope/
  interval/timezone keys, atomic publish, bounded cleanup. FR-27, 33, 34.
  Depends: T1 (interval normalization). Route: delegated writer. Done; commit on
  `feat/herdr-brain-ctx-02-reportstore`.
- [x] **T3** `evidence` inventory: open Herdr sessions + transcript readers with
  a historical cursor (OpenCode/Claude/Antigravity) + per-source revision
  tokens; absence vs failed coverage. FR-03..05, 11, 12, 40.
  Route: delegated mapper then writer. Split (from the mapping) into:
  - [x] **T3a** core types (Source/EvidenceItem/CoverageStatus with
    OK/SOURCE_ABSENT/COVERAGE_FAILED, Deadline budget) + `HerdrSessionProvider`
    (truthful status, no "unfinished" inference, revision tokens,
    ACTIVE_STATUSES rule for FR-12). Done; commit on
    `feat/herdr-brain-ctx-03a-evidence-core`.
  - [x] **T3b** `OpencodeEvidenceProvider`: historical enumeration via SQLite,
    epoch-ms timestamps, `time_updated` revision tokens. Done; commit on
    `feat/herdr-brain-ctx-03b-evidence-opencode`.
  - [ ] **T3c** `ClaudeEvidenceProvider` + `AntigravityEvidenceProvider`:
    JSONL discovery, ISO timestamps, stat-based tokens.
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
  ../agent-tts-worktrees/herdr-brain-ctx main`;   baseline 571 passed.
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

- T1 commit: `eb5d2b0` on `feat/herdr-brain-ctx-01-periods`. Native review
  assessment: medium risk (`executable_change`), `review_due` true
  (`slice_budget_reached`), but RDD is OFF for this clone (`clone_local`, user
  decision) so no review was started; ordinary repository policy applies.
- 2026-09-30 T2 done (strict TDD, auto mode). First writer launch returned an
  empty message and wrote nothing (verified on disk); resumed once, then
  completed. RED-1: `ModuleNotFoundError: No module named
  'herdr_brain.reportstore'`. GREEN-1 36 passed. RED-2: 9 failed / 38 passed
  (missing `cancel`/`mark_refresh_failed`, `DID NOT RAISE StaleBuildError`; two
  cycle-2 tests were invariant guards that passed in RED-2, reported honestly).
  GREEN-2 47 passed. Full suite re-run by the parent: 691 passed
  (644 + 47). Files: `src/herdr_brain/reportstore.py` (736),
  `tests/test_reportstore.py` (722): 1458 lines, over the ~400 heuristic (heavy
  required coverage + docs, advisory only).
- T2 rule decisions: sequence = AUTOINCREMENT id; `superseded` is a column,
  `expired` is derived (clock >= retention_expires_at), never stored; stale
  publish discards its building row; purge is bounded and opportunistic
  (`retention_expires_at <= now`); `timezone` kept as an independent key part.
- Open, deliberately not decided: purge cadence/owner, followup-anchor behavior
  on StaleBuildError (T8), body/reference size caps (T7 concern), history/
  diagnostics read API for superseded chains, default DB location wiring.
- Slice 02 `feat/herdr-brain-ctx-02-reportstore`: T2 (~1.46k lines) —
  `size:exception` recommendation recorded.
- 2026-09-30 T3 mapping (explore agent, read-only): anchors at
  `herdr.py:158` (list_agents, `AgentInfo` fields, HerdrError 15s timeout),
  `tools.py:154/302`, `watcher.py:161`; transcripts expose windowed tails only
  with no timestamps/ids/cursor (historical reads need new queries);
  trustworthy message timestamps: OpenCode `message.time_created` (epoch ms),
  Claude `event["timestamp"]` (ISO), Antigravity `event["created_at"]` (ISO);
  project identity: Herdr cwd, OpenCode `session.directory`, Claude munged dir
  + event cwd, Antigravity cwd or `conversation_summaries.db workspace_uris`
  (open question whether that db is in configured authority — deferred, cwd
  fallback first). Fixtures: StubHerdr/make_stub/opencode_db/claude_root/
  antigravity_root. Mapper report archived in the session transcript.
- 2026-09-30 T3a done (strict TDD, auto). RED-1: `ModuleNotFoundError: No
  module named 'herdr_brain.evidence'` (19 tests cycle 1). RED-2: `ImportError:
  cannot import name 'ACTIVE_STATUSES'` (cycle 2). GREEN 46 passed. Full suite
  re-run by the parent: 737 passed (691 + 46). Files: `src/herdr_brain/evidence.py`
  (440), `tests/test_evidence.py` (483): 923 lines, over the ~400 heuristic
  (advisory only).
- T3a rule decisions: revision token = sha256(json of [status, session_value,
  focused, title])[:16], observed_at excluded (re-observation is not a
  revision); empty herdr inventory is OK (not SOURCE_ABSENT); deadline checked
  BEFORE touching the client; project_filter = exact cwd or under-path; no mtime
  field anywhere (None timestamps classify UNKNOWN); untrusted-data docstring.
- Open: ACTIVE_STATUSES ownership (orchestration may revisit), cwd->project
  normalization, mid-acquisition re-validate token (T5 concern), Antigravity
  summaries-db authority.
- Slice 03a `feat/herdr-brain-ctx-03a-evidence-core`: T3a (~923 lines) —
  `size:exception` recommendation recorded.

## Next step

T3b `OpencodeEvidenceProvider` on `feat/herdr-brain-ctx-03b-evidence-opencode`
from 03a.
