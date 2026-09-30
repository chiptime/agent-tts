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
  - [x] **T3c** `ClaudeEvidenceProvider` + `AntigravityEvidenceProvider`:
    JSONL discovery, ISO timestamps, stat-based tokens. Done; commit on
    `feat/herdr-brain-ctx-03c-evidence-transcripts`.
- [ ] **T4** Read-only Engram adapter (access mechanism to be verified, A-2).
  FR-05, 31, 38, 39. Depends: T3. Done; commit on
  `feat/herdr-brain-ctx-04-engram-adapter`. Verified access: SQLite at
  `~/.engram/engram.db` opened `file:...?mode=ro` (no subprocess, no writes;
  write refusal empirically confirmed).
- [x] **T5** `freshness`: manifest comparison, reuse-after-check, full rebuild,
  bounded full scan, moving windows. FR-28..32, 41. Depends: T2, T3, T4.
  Done; commit on `feat/herdr-brain-ctx-05-freshness`.
- [x] **T6** `report` consolidation: per-project advances/pending/blockers,
  conflict surfacing, references, grounded completion, empty vs failed.
  FR-13..16, 22, 25. Depends: T3..T5. Done; commit on
  `feat/herdr-brain-ctx-06-report`.
- [x] **T7** Query FSM + server-enforced 60s budget + routing + narrow/
  clarify/unable states. FR-01..03, 17, 19..21, 26, 37. Depends: T1, T5, T6.
  Done; commit on `feat/herdr-brain-ctx-07-fsm`.
- [x] **T8** `followup` persistent context, selection isolation, expiry.
  FR-07, 23..25. Depends: T2, T7. Done; commit on
  `feat/herdr-brain-ctx-08-followup`.
- [x] **T9** Tool/LLM wiring: read-only retrieval tools, injection isolation,
  approval-gate regression. FR-36, 38..41. Depends: T7. Done; commit on
  `feat/herdr-brain-ctx-09-wiring`.
- [x] **T10** UI: Consulting state + on-screen references (never spoken).
  FR-14, 18. Depends: T7. Done; commit on `feat/herdr-brain-ctx-10-ui`.
  Also closed the T9 integration finding: ReportStore.get_by_report_id +
  store-backed followup fallback (restart-resilient summaries).
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
  `size:exception` recommendation recorded. Post-commit native assessment of
  the T3a commit: high risk (`high_risk`), review_due true; RDD remains OFF
  (clone-local user decision) so no review started, ordinary policy applies.
- 2026-09-30 T3b done (strict TDD, auto). RED: `ModuleNotFoundError: No module
  named 'herdr_brain.evidence_opencode'`. First GREEN run 3 failed / 24 passed
  — test expectations wrongly assumed NULL-time sorts last (SQLite sorts NULL
  first); expectations fixed, not code. GREEN 27 passed. Full suite re-run by
  the parent: 764 passed (737 + 27). Files: `src/herdr_brain/evidence_opencode.py`
  (472), `tests/test_evidence_opencode.py` (552).
- T3b rule decisions: no-period collect = newest max_turns=200 tail with
  CollectStats.truncated flag (OpencodeCoverageResult subclass); period-bounded
  reads uncapped (interval + deadline bound them); UNKNOWN timestamps excluded
  from period reads but counted in stats; zero-byte db = SOURCE_ABSENT;
  token = sha256-16 of [session_id, time_updated, last_message_id, count];
  one re-read on mid-read token change, then COVERAGE_FAILED; NULL-time sorts
  oldest (engine-deterministic, documented).
- Open: cap on period-bounded reads, kind-check on collect, non-user/assistant
  roles filtered (mirrors transcript reader), float epoch ms support.
- Slice 03b `feat/herdr-brain-ctx-03b-evidence-opencode`: T3b (~1.02k lines) —
  `size:exception` recommendation recorded. (T3b progress notes landed in the
  03c commit: the doc edit raced the parallel commit command — noted as a
  process lesson: never edit and commit in the same parallel batch.)
- 2026-09-30 T3c done (strict TDD, auto). RED: `ModuleNotFoundError: No module
  named 'herdr_brain.evidence_transcripts'`. First GREEN run 1 failed / 54
  passed — test fixture bug (wrong transcript filename preference), fixed in
  the fixture, not the code. GREEN 55 passed. Full suite re-run by the parent:
  819 passed (764 + 55). Files: `src/herdr_brain/evidence_transcripts.py`
  (894), `tests/test_evidence_transcripts.py` (1208).
- T3c rule decisions: Claude munged-dir inverse heuristic (`-`-prefixed ->
  `/` + `-`->`/`, else verbatim; lossy, documented); Antigravity project = ""
  (summaries db NOT read — deferred authority question; empty project matches
  only an absent filter); any non-vanishing OSError fails the WHOLE inventory
  (no partial manifests, FR-41), vanished files skip; no-period read = newest
  max_turns tail via chunked reverse scan capped at scan_cap 8MB (truncated
  conservative flag); period reads = full forward scan, uncapped; malformed
  lines skipped + counted; token = sha256-16 of (mtime_ns, size) — mtime never
  a message time; one re-read on race, then COVERAGE_FAILED.
- Open: Antigravity summaries-db authority (would give real project identity),
  Claude munge cross-check against live cwds, kind-check on collect, byte
  ceiling on period reads.
- Slice 03c `feat/herdr-brain-ctx-03c-evidence-transcripts`: T3c (~2.10k
  lines) — `size:exception` recommendation recorded.
- 2026-09-30 T4 done (strict TDD, auto). RED: `ModuleNotFoundError: No module
  named 'herdr_brain.evidence_engram'`. GREEN 31 passed. Full suite re-run by
  the parent: 850 passed (819 + 31). Files: `src/herdr_brain/evidence_engram.py`
  (543), `tests/test_evidence_engram.py` (658).
- T4 rule decisions: token = sha256-16 over full row skeleton [(id, updated_at,
  deleted_at)] ordered by id (max-aggregates cannot see edits below the max —
  id inclusion prevents purge/reinsert collisions); projects with only
  soft-deleted rows stay listed (deletions detectable, FR-31); zero-row project
  = SOURCE_ABSENT; collect on only-deleted = OK-empty; content excerpt to
  max_chars=4000 with visible `... [truncated]` marker; naive TEXT timestamps
  read as UTC by documented convention; project_filter exact-match only;
  missing observations table = COVERAGE_FAILED (open); period-bounded reads
  uncapped (open); no env override (open); no truncated bool in stats (open).
- Slice 04 `feat/herdr-brain-ctx-04-engram-adapter`: T4 (~1.20k lines) —
  `size:exception` recommendation recorded.
- 2026-09-30 T5 done (strict TDD, auto). RED-1: `ModuleNotFoundError: No
  module named 'herdr_brain.freshness'` (21 tests). RED-2: `ImportError:
  cannot import name 'FreshnessChecker'`. GREEN 46 passed. Full suite re-run
  by the parent: 896 passed (850 + 46). Files: `src/herdr_brain/freshness.py`
  (483), `tests/test_freshness.py` (515).
- T5 rule decisions: sealed outcome family Reuse/Rebuild/Unable; token equality
  the ONLY change signal; full_scan=True permanent (providers give full
  inventories, deltas never exist — FR-32); pipeline authority -> health ->
  stored report -> manifest; UNABLE on any COVERAGE_FAILED (never serve stale);
  window movement beats manifest identity; interval comparison never trusts the
  store key coupling; malformed manifest rows fail loud; empty/duplicate
  configured_kinds rejected; RebuildPlan carries the deadline.
- Open: per-source kind vs provider kind check, SOURCE_ABSENT-with-sources
  rejection, FSM ownership of mark_refresh_failed.
- Slice 05 `feat/herdr-brain-ctx-05-freshness`: T5 (~1.0k lines) —
  `size:exception` recommendation recorded.
- 2026-09-30 T6 done (strict TDD, auto; 4 RED cycles: module, filter, brief
  model, renderers — RED-2 included a genuine empty-reason logic bug in mixed
  absent/OK bundles, fixed in code). GREEN 76 passed. Full suite re-run by the
  parent: 972 passed (896 + 76). Files: `src/herdr_brain/report.py` (671),
  `tests/test_report.py` (873).
- T6 rule decisions (deterministic layer only — semantics stay with the model):
  failed sources never bundle (their items are not evidence); empty_reason
  precedence items > all-absent > no_evidence; undated items visible not
  excluded; incompleteness = "incomplete" marker in generated_note with
  spoken-safe canonical note (no ids spoken, FR-14/20); conflict notes
  screen-only, must cite real source_ids; render_spoken emits no references by
  construction; citations validated scaffold-wide; provenance mismatch raises.
- Open: require no_work on empty bundles, per-section citations, TTS prosody
  wording, undated in rendered output, size caps (T7).
- Slice 06 `feat/herdr-brain-ctx-06-report`: T6 (~1.54k lines) —
  `size:exception` recommendation recorded.
- 2026-09-30 T7 done (strict TDD, auto; 5 cycles, cycle-5 supplementary tests
  honestly reported as characterization not RED). GREEN 69 passed. Full suite
  re-run by the parent: 1041 passed (972 + 69). Files:
  `src/herdr_brain/queryfsm.py` (1055), `tests/test_queryfsm.py` (1430).
  report.py untouched (body codec lives in queryfsm).
- T7 rule decisions: stored body = {version, brief JSON, captured screen} —
  reuse re-renders spoken deterministically, serves captured screen verbatim
  (scaffold not reconstructible); unqualified-global sentinels (interval key
  "unqualified|epoch|epoch|unqualified", tz "unqualified") so snapshots reuse
  on tokens alone without blocking on timezone; active_only passed ONLY to
  herdr_session provider and ONLY for date-scoped global (FR-12); budget
  checked at accept/freshness/per-collect+post-acquire/consolidate/publish;
  FR-14 leak double-check runs BEFORE begin_build; all-or-nothing acquisition
  makes incomplete scaffolds unreachable through handle(); multi-project
  intents rejected structurally (T9 fan-out concern); thresholds provisional
  (4000 items / 500k chars); StaleBuildError -> unable, no retry; focus
  intents rejected without touching providers/store (false-routing guard).
- Open: multi-project fan-out, threshold tuning, max_span/future_tolerance
  values, natural-period reuse granularity (Period.key embeds now — server may
  normalize), narrow_ask not demoting prior report, BriefDocument.context_id
  ownership.
- Slice 07 `feat/herdr-brain-ctx-07-fsm`: T7 (~2.49k lines) —
  `size:exception` recommendation recorded.
- 2026-09-30 T8 done (strict TDD, auto). RED: `ModuleNotFoundError: No module
  named 'herdr_brain.followup'`. First GREEN run 1 failed / 47 passed — test
  harness defect (missing Row factory), fixed in harness. GREEN 48 passed.
  Full suite re-run by the parent: 1089 passed (1041 + 48). Files:
  `src/herdr_brain/followup.py` (407), `tests/test_followup.py` (540).
- T8 rule decisions: expiry derived at read (no restart reconciliation; filter
  on read, purge physical); retention from created_at — touch never extends
  life (anchor cannot outlive its report horizon); expire = DELETE; single-
  statement upsert (newer anchor wins); forced-6-digit-micros ISO keeps SQL
  lexical order chronological; matches_topic exact equality (semantics stay in
  T9); fingerprint_from_text = deterministic normalization convenience only;
  no pane fields by construction (tested via dataclass inspection).
- Open: anchor id verification against reportstore (T9), fingerprint choice
  (T9), purge scheduling, absent-vs-expired diagnostics.
- Slice 08 `feat/herdr-brain-ctx-08-followup`: T8 (~0.95k lines) —
  `size:exception` recommendation recorded.
- 2026-09-30 T9 done (strict TDD, auto; registration-only test updates
  disclosed as not-RED, T7 precedent). GREEN 58 new tests. Full suite re-run
  by the parent: 1147 passed (1089 + 58), approval/llm/server/tools
  regressions green. Files: NEW `consult.py` (796) + `test_consult.py` (1126);
  MODIFIED config.py (+37), llm.py (+16/-9: build_openai_client extraction +
  context_id pass-through — verified surgical by the parent), server.py (+20),
  tools.py (+299), test_llm/test_tools (+3 each).
- T9 rule decisions: registration in server.py via attach_consult (existing
  duck-type compat convention); ContextVar carries Deadline+context_id from
  ConsultService.consult to LLMSummarizer (timeout = remaining budget); event
  seam publishes consulting start/end on the watcher hub SSE channel (T10
  renders); tz candidates = explicit -> TZ env -> empty (ask_tz), never
  guessed; followup fingerprint = fingerprint_from_text("kind|projects|period")
  surfaced to the model for exact-match re-supply; rendered tool results carry
  spoken only, get_followup_context returns screen text with screen-only
  instruction; model client lazy (boots without GLM_API_KEY); new tools
  approval-free (read-only), old gates untouched (regression test added).
- Integration finding (reported, NOT fixed): ReportStore lacks get_by_report_id
  — followup summaries use a bounded in-memory registry (cap 16); after
  restart the anchor survives but the summary degrades to "re-consult".
  T10/T11 candidate.
- Open: semantic followup continuity, threshold tuning via env, restart-
  resilient followup summaries.
- Slice 09 `feat/herdr-brain-ctx-09-wiring`: T9 (~2.0k new + 378 modified
  lines) — `size:exception` recommendation recorded.
- 2026-09-30 T10 done (strict TDD both stacks; one existing test adapted with
  intent preserved — filtered to consulting events after the new report event,
  parent reviewed the diff). GREEN: pytest 11 new (1158 total), node --test
  192/192. Files: NEW static/consult.js (170), tests/js/consult.test.js (285),
  tests/test_consult_ui.py (393); MODIFIED consult.py (+79: report event +
  store fallback), reportstore.py (+34: get_by_report_id — mirrors get_latest
  inclusion except superseded-by-id IS returned, documented), app.js (+21),
  index.html (+57), test_consult.py (adapted assertion). server.py untouched.
- T10 rule decisions: outcome notices via existing toast (notify injected);
  textContent/createElement ONLY, innerHTML never (tested with malicious
  payload); event order start -> end -> consult_report; panel latest-wins with
  .stale dim on re-consult (FR-33 spirit); consult.js has no audio seam
  (tested); get_by_report_id: published+refresh_failed, never building/expired;
  registry miss -> store fallback re-decodes per call (never cached);
  queryfsm._decode_body imported with justification comment.
- Open: cache-busting for consult.js (announce.js precedent), notice duration
  per terminal state, panel dismissal UX, fallback memoization.
- Slice 10 `feat/herdr-brain-ctx-10-ui`: T10 (~0.85k lines incl. tests) —
  within budget for production code (~347), tests over; `size:exception`
  recommendation recorded only for the test volume.

## Next step

T11 acceptance on `feat/herdr-brain-ctx-11-acceptance` from 10: map the
Requirement-to-Test Matrix to the actual suites, run the Verification
Checklist, write the feature README/docs, and record remaining gaps honestly.
