# M1 quality repair — bounded technical maintenance

## Objective and authorization

Close M1 acceptance without exceptions: required smoke checks pass, modified UI
integration is measured against the agreed coverage policy, and gate evidence is
derived from actual execution rather than prose.

- User selected `Mantener criterios, sin excepciones (Recommended)` and then
  `Autorizar reparación acotada (Recommended)` in the parent native questions.
- Owned worktree: `agent-tts-worktrees/m1-quality`; branch:
  `fix/voice-stack-m1-quality`. Do not edit the original voice-stack worktree,
  its branch/index/locks or its active run/checkpoint records.
- Scope: recover the immutable post-M1 source; compatibility-verified bootstrap
  pin repair; diagnosed minimal smoke/voice-menu correction; UI glue coverage
  without functional change; deterministic gate manifest validation; related
  verification tools, test harnesses, fixtures and local test dependencies.
- Authorized Git network scope: checks/fetch of `origin` in `chiptime/agent-tts`
  through its configured SSH connection. No credential/agent discovery.
- No commits, push, paid providers, model/permission changes, new product
  behavior or weakened quality gates. Preserve existing published dates;
  publication requires separate authorization and the user's time policy.
- Broader functional incompatibility or an unapproved operation stops the unit
  with exact evidence and the smallest required owner decision.

## Budget and route

- Delegated direct, not SDD: source materialization and repairs span multiple
  non-trivial files. One writer in this worktree, fresh bounded checking actors.
- One planned initial work cycle plus at most **two corrective rounds** for
  this maintenance unit. Record rounds honestly; do not reset consumed VS1
  functional rounds or any native attempt/review budget.
- A planned failing regression test or negative verifier selftest is not a
  corrective-round failure. A real failed post-fix gate consumes a corrective
  round when its repair is attempted; changing its name does not reset limits.
- Forecast: approximately 450–600 authored changed lines, mostly tests and
  verification; recovering unchanged snapshot bytes is not authored work.
  Focused/full Python, Node and Chromium suites are local and use fake providers.
- Resolve effective TDD/runner configuration from the actual recovered project
  before runtime work; do not infer TDD from test presence or another SDD change.
- RDD was off on the source project/worktree. Read effective mode here without
  changing it. Follow only applicable native continuations if mode differs.

## Immutable input and baselines

- Post-M1 source: machine-local run `20261001T065148Z-vs2`, file
  `baseline-snapshot.json`, content-addressed `blobs/`.
- Recorded input binding:
  `aad7d6c95e1a8863e45a6a9d7837f0870e64e833d47995cc5172c00816462b92`.
- Preparation verified 436/436 blob references (217 unique content blobs).
  Verify unique normalized paths, bytes and modes before materialization;
  reject traversal/absolute paths and conflicting duplicate entries.
- Preserve this recovery document; do not overwrite it during source import.
- New root identity may differ: prove source equivalence and record a derived
  binding, never pretend that a worktree-bound/native authorization transferred.
- Retain genesis B0 from `20260930T214105Z-vs1` for M1 comparison and final VSX.
  Do not replace it with a post-feature baseline to hide cumulative changes.
- Put new evidence in a distinct maintenance run namespace. Original manifests
  are read-only historical inputs, not files to edit into a green result.

## Tasks

- [x] MQ-00 Prepare the isolated worktree and locate immutable M1 source.
  Evidence: preparation task `ses_f0905a991ffebMdxN4BJasO6WK`; clean container
  at `e49571eae8ea17004307f18d673ce7f72a1dbe2c`, no source fixes/materialization.
- [x] MQ-01 Recover source, prove equivalence and establish own environment/run.
  Evidence: maintenance run `20261001T162700Z-m1q` (own namespace
  `~/.local/state/voice-stack-maintenance-runs/`): 30 blob writes (18 missing
  + 12 modified), 183 pre-matching, 0 extras/deletions, ODD book preserved;
  canonical source payload rebuilt from disk == recorded
  (`1808bbbb…`); derived own binding `aad7d6c9…b92` (local env identical to
  capture env). Engine/brain venvs `uv sync --offline`; host venv imports
  agent_tts editable from THIS worktree. Recovery record: `RECOVERY.md`.
- [x] MQ-02 Reproduce and repair smoke cases 40e/16n; prove compatible parity.
  Evidence: RED `987 ok / 1 FAIL` each (16n machine-global `/tmp` playback
  trio collided with the live real daemon — now `AGENT_TTS_*`-overridable,
  scenario-local in `new_env`; 40e identity safeguard was silently skipping
  worktree checkouts — `.git`-file-aware, plus pin repaired to published
  origin/main `e592ef31c828c5c637b3737604f173b1e0a07b80` after blob-identity
  + F1–F9 parity proof). Final full smoke: **exit 0, 988/988**; 40e = 63
  checks green, oracle files == pin. Logs under the maintenance run
  `evidence/{red,red2,green}/`. Authored repair delta ~24 lines / 3 files.
- [x] MQ-03 Measure modified UI integration without blanket app.js exclusion.
  Evidence: `scripts/voice-stack/js-coverage/run-pwa-gate.sh` (run
  `evidence/mq03-gate-final/`, GATE_EXIT=0): D4 changed-production metric —
  real-browser istanbul-instrumented app.js/speech.js served at their real
  URLs under the E2E harness (CSP-safe `globalThis` prologue) + node
  require-hook lane. app.js changed scope (56 changed lines → 38 executable
  statements + 14 in-scope branches of 540, machine-mapped vs immutable B0
  blob `41d04b17…`): **lines 100.00% (38/38), branches 92.86% (26/28 arcs)**
  (2 uncovered arcs: late-twin defensive arm + crypto-absent `||` fallback,
  unreachable in a real browser). speech.js (new file ⇒ full scope): **100%
  / 100%** (75 stmts, 37/37 arcs). Former blanket exclusion REJECTED live by
  the gate. Component totals on COMPARABLE denominators (raw-V8 lane,
  baseline-identical semantics; 7 common modules byte-identical tests):
  candidate 97.13% == baseline 97.13% (1521/1566) — zero regression; reader
  branches improved. Controls: gate `--selftest` + `--selftest-changed`
  (uncovered-line/arc FAIL, stale-hash BLOCKED, tampered-maps BLOCKED,
  exclusion-rejection) and mapper `--selftest` (shift/multiline/added-body)
  all green. E2E 16/16 with AND without instrumentation; node 213/213; all
  static sources hash-identical to the materialized snapshot (no seam needed
  — zero production-code change). Authored delta ~+391/−2 modified
  (conftest +82, coverage_gate +309) + 1042 new tool/test lines — above the
  450–600 guidance; the honest metric needed the mapper + negative-control
  tooling (allowed scope), not code-golf.
  *Recording corrections (MQ-04, after independent challenger check
  `ses_f06eccf5bffe2GjJEcYY22unqd` confirmed the metric):* (a) the
  "node 213/213" count above was NOT recorded in this run's own logs — the
  pipeline's `tail -4` destroyed the TAP summary and "213" coincided with
  the materialization PER_FILE_VERIFIED number; the challenger's run has
  the real log. The pipeline logger now captures FULL stdout/stderr + sha256
  (`run-pwa-gate.sh`, MQ-04) — the JS gate must be and will be re-run under
  it in MQ-05. (b) "E2E 16/16 without instrumentation" was true-by-
  construction, not recorded evidence — reclassified PENDING a fresh MQ-05
  run in BOTH modes (instrumented + normal) on the same candidate; source
  hash preservation is not a runtime-equivalence proof. (c) Metric labels
  precise: app.js 100/92.86 is the D4 mapped CHANGED-production scope
  (38 statement-backed line units, 28 in-scope arcs), NOT whole-app.js ≥90
  (whole-file ≈59% disclosed); 97.13% is the 7 COMMON legacy modules on
  baseline-comparable V8 semantics (1521/1566); the node full-candidate set
  is 8 files at 97.31%; the browser lane measures app.js/speech.js
  separately. (d) Toolchain provenance was incomplete (manifest held only
  the 5-package V8 chain) — superseded by the complete pinned manifest
  (66 packages, all origin-pinned) recorded under MQ-04.
- [x] MQ-04 Add deterministic manifest validation/writing and negative controls.
  Evidence: `scripts/voice-stack/gate_evidence.py` (own run
  `evidence/mq04/`): (a) writer scaffolds an own-run manifest whose run_id
  MUST equal its dirname, records genesis-B0 (vs1, `b8128eef…`, never
  replaced) separately from milestone-B1 post-M1 materialized binding
  (`aad7d6c9…`); gate evidence records literal argv (never shell strings),
  cwd constrained to the owned root, FULL raw stdout/stderr + sha256,
  returncode + declared exit contract, artifact hashes, source bindings and
  env NAMES only (no values/secrets). (b) validator re-derives everything:
  required-gate list parsed from the DOC authority (TASKS.md gates table —
  9 gates, G-X excluded; parser handles digit IDs like G-E2E), statuses
  pass|fail|blocked only, rc=1+pass rejected, ellipsis/cwd-escape/log-
  traversal/missing-log/tampered-hash rejected, coverage passes require a
  real PASS marker + numeric metrics + non-blank artifacts (not_applicable/
  zero-scope prose cannot pass), suite counts must RE-PARSE from the raw
  logs (a materialization counter pasted as a test count is rejected), E2E
  pass without a raw pytest summary log rejected; verdicts: reject
  (dishonest) vs blocked (honest incomplete/failing — verified on a real
  partial run: `smoke-run` with G-BOUNDARY actually executed rc=0/2 passed
  validates `blocked` with exactly the 8 pending gates). (c) selftest
  16/16 green: healthy fixture passes + N1–N14 negative controls (exit1+
  pass, omitted gate, wrong run_id, ellipsis, cwd escape, log traversal,
  missing log, tampered log, blank coverage, pasted counts, E2E-without-
  log, unknown returncode, declared≠authority, zero-scope) + authority-doc
  parse. (d) ledger (`ledger.json`): 34 modified entries classified —
  11 production/operational ALL mapped to metric gates (6 python →
  G-ENG/BRN/HOST-PY, 3 js → G-JS mapped/new-file, 1 bash → G-BASH-*),
  bootstrap.sh = operational producer pin (declared-gap note: functional
  proof published-pin + F1–F9; ps4 line measurement = MQ-05 scope
  decision), smoke-tests.sh = explicit test-program classification,
  0 unclassified. (e) complete pinned toolchain manifest
  (`tools-manifest-full.json`, sha256 `1d087d1e…`): 66 packages — versions
  READ from disk (istanbul-lib-instrument@5.2.1, coverage@3.2.2,
  report@3.0.1, reports@3.1.7, source-maps@4.0.1, v8-to-istanbul@9.3.0,
  @babel/* @7.24.7), per-package content digests + npm-cacache origin
  blobs (offline reproducibility; no global installs, no new endpoints).
  (f) `run-pwa-gate.sh` logger fixed: full stdout/stderr capture + sha256
  for every stage + evidence.sha256 manifest (historical MQ-03 logs
  preserved untouched) — JS gate re-run due in MQ-05.
- [x] MQ-05 Run every required M1 gate on one final candidate and report results.
  Evidence: final frozen attempt `evidence/mq05-gates4/` (candidate snapshot
  binding `a167549…`, genesis B0 `b8128eef…` + post-M1 B1 `aad7d6c9…`
  recorded distinctly; freeze-before/after hashed; drift = only the
  bootstrap-harness instrument trace passthrough, G-BASH-LINES re-recorded
  after it). ALL 9 TASKS.md authority gates recorded via gate_evidence.py
  with literal argv + full raw logs + sha256 and VALIDATED `pass`
  (VALIDATION_OK): G-BOUNDARY 2 passed · G-SMOKE **988 passed / 0 failed**
  (exit 0) · G-E2E **16 passed** normal browser mode AND 16 passed
  instrumented inside G-JS (both raw logs; env toggle recorded) · G-ENG-PY
  daemon 91.93/90.74 + queue_manager 95.12/94.23 (842 tests) · G-BRN-PY 4
  touched modules 96.67/92.16, 98.97/90.91, 97.35/97.73, 98.88/95.83 (1213
  tests) · G-HOST-PY pending_queue 100/100 (41 tests) · G-JS app.js changed
  scope **100/92.86** (38 stmts, 26/28 arcs) + speech.js **100/100** (75,
  37/37), former exclusion REJECTED, common-set 97.13==97.13 (node full set
  8 files 97.31), Node **213/213 from the fresh raw TAP log** · G-BASH-LINES
  product denominator 20/20 = **100.00%** (bin/herdr-tts M1+trio lines via
  PS4 xtrace; instruments excluded per D9) · G-BASH-MATRIX decisions=0
  (product delta carries no decision constructs; harness = instrument,
  printed explicitly; 9/9 cases OK) + bootstrap.sh pin line measured by the
  dedicated sterile mini-gate: **1/1 = 100%** (fake HOME/uv/git-free, no
  network; pin `e592ef31…` asserted in the recorded install argv).
  Tooling repairs this phase (each RED/GREEN with selftests, all green:
  coverage_gate is_prod `/lib/`, bash_changed_lines `PS4 ${BASH_SOURCE:-}` +
  is_bash_file product-only + defs/labels, bash_matrix string-blanking +
  diff-scoped decisions + harness-instrument rule; run-pwa-gate logger from
  MQ-04; runner env bugs: NODE_TOOLS path, SMOKE_ROOT 108-char unix-socket
  limit). Maintenance correction counter: **2/2 corrective extras used**
  (round 1: MQ-03 first full-gate comparability FAIL 95.22<97.13,
  istanbul-vs-v8 denominators — log `evidence/mq03-gate/gate.log`,
  unplanned post-fix gate failure repaired by the v8 lane; round 2: MQ-05
  first gate battery failures — SMOKE 982/6 socket-path, JS runner env,
  HOST-PY false not_applicable, BASH-LINES scope — repaired as above). No
  preexisting-FAIL waiver applied anywhere: the vs1d-era smoke failures were
  FIXED (MQ-02), not waived. Deployment note (owner decision, not blocking
  source acceptance): bootstrap pin `e592ef31…` is the published PRE-cancel
  engine (oracle parity); a fresh install from bootstrap would NOT contain
  the M1 cancel IPC (uncommitted in C). SOURCE_ACCEPTED ≠
  PUBLISHED_INSTALL — release-pin coordination stays with the owner.
  Handoff checkpoint: `evidence/mq05-gates4/HANDOFF.json`
  (`7bbc3ab3…`).

## Acceptance and rollback

- No required failed, missing or unmeasured gate can yield `complete`.
- Python/JS: agreed >=90% lines and branches on changed production, with the
  existing conservative touched-module floor when reliable delta mapping is
  unavailable; component-total non-regression is measured comparably.
- Bash: >=90% modified executable lines and executed decision-alternative
  matrix. No fabricated numerical Bash branch coverage.
- Glue counts as modified production. If measurement cannot meet the policy,
  report the precise gap; do not exclude the file or reduce the denominator
  merely to pass. Existing permitted exclusions still require evidence.
- Browser E2E exercises real decoded media and integration; it does not claim
  physical audibility or paid-provider behavior.
- Every gate has literal command, cwd, observed exit, exact log path and source
  binding; run_id matches its directory. Zero exit alone is not coverage proof.
- Rollback boundary: this worktree's repair delta and new maintenance evidence;
  retain recovered M1 and all original-worktree work. No destructive rollback
  or resetting another actor's locks/counters.

## Progress

- At the initial MQ-05 close on 2026-10-01, corrective extras used were
  **2/2** (the MQ-03 first-gate comparability FAIL and MQ-05 first battery
  failures, classified with log references above). The initial MQ-01..MQ-05
  cycle was marked complete based on the evidence then recorded. That closure
  and exhausted-budget status were superseded on 2026-10-02 by the corrective
  authorization and read-only finding below; the original gate records remain
  historical evidence, not proof that the zero-decision matrix was valid.
- Engram state at the initial close: observation #9787 is a closure summary,
  not a full-document mirror. A prior progress-metadata update was rejected for
  owning-project context, so the complete mirror remained pending. This
  addendum is being synchronized in full before the first source edit; no
  session identity will be invented.
- Commit evidence: none; not authorized (delivery/commit is a separate
  owner decision).
- At the initial 2026-10-01 close, the next action was the independent
  functional check and handoff for the next ready task (VS2..VS4 source work
  stays with the original session's writer):
  `evidence/mq05-gates4/HANDOFF.json`. Parent performs the independent
  final bounded functional check. Parent native `gentle-ai review mode
  status --cwd` check confirms off, decided by clone_local. Absence of a
  marker is not proof of off (the unset default is on). Nothing was
  toggled. Bootstrap pin note: e592ef31 proves ORACLE parity only
  (published pre-cancel engine); final M1 proof runs on C's editable
  engine — deployment-pin coordination stays with the owner.

## Corrective addendum — one extra round authorized

### Authorization and route

- On 2026-10-02, the user explicitly authorized one bounded corrective round after
  the original 2/2 corrective extras were exhausted. After the required read-only
  inspection exposed two additional parser blockers, the user explicitly
  authorized including those blockers in this same extra round. This is one new
  bounded round (round 3 overall), not a reset or a fourth round.
- Route: delegated direct, one source writer, followed by one fresh bounded
  gate/evidence action. No SDD. No commits or push.
- Effective TDD mode is disabled (`strict_tdd: false`) per project testing
  capabilities observation `sdd/agent-tts/testing-capabilities` (#9630,
  recorded 2026-09-30); this is not inferred from test presence. Use the
  focused functional runner `python3 scripts/voice-stack/bash_matrix.py
  --selftest`, then the exact authorized `G-BASH-MATRIX` gate. No strict
  red/green/refactor sequence is required.
- Existing scope and guardrails remain binding: work only in this isolated
  worktree and its `20261001T162700Z-m1q` maintenance evidence namespace; do not
  touch the original voice-stack worktree, `main`, or historical
  `~/.local/state/voice-stack-runs/` data; do not change RDD; do not continue
  VS2–VS4; stop if a broader functional incompatibility appears.

### Read-only finding that invalidated the prior matrix PASS

- The recorded MQ-05 `G-BASH-MATRIX` result (`decisions=0`, 9/9 cases) passed
  vacuously: `_heredoc_spans()` applied `_HEREDOC_RE` to raw source and matched
  the string literal at `herdr-tts:1238`, masking lines 1239–7228, including all
  four current M1 decisions. The previous gate record is historical evidence
  of that output, not valid proof of decision coverage.
- Additional parser blockers discovered before editing: `UNSUPPORTED_KEYWORDS`
  treats the `until` argument in `jq --argjson until` at `herdr-tts:2665` as a
  Bash loop; `_IF_RE` treats `elif` as a new `if` frame and leaves the parser
  stack unbalanced. The user approved these two narrowly scoped fixes as part of
  the same extra round.
- The four decision entries originate from immutable baseline blob
  `3f683b37…`; current source lines after the +4 path-override shift are
  1754 (`if`), 1771 (`if`), 1772 (`||`), and 6834 (`case-arm`). The existing
  harness cases are already present; target is four decisions, seven
  alternatives, and all 9/9 matrix cases executed successfully.
- The required documentation and all 358 files under
  `evidence/mq05-gates4/` were read before implementation. CodeGraph has no
  index for this worktree; exploration used read-only filesystem tools instead.

### Authorized corrective checklist

(Status of the items below: see "Outcome of round 3".)

- [~] MQ-06.1 Fix Bash parsing in `scripts/voice-stack/bash_matrix.py`: blank
  strings/comments before heredoc detection, ignore non-statement `until` uses
  such as the jq argument, and keep `elif` within its owning `if` frame.
- [~] MQ-06.2 Restore the four decision records in
  `hosts/herdr/tts-plugin/tests/matrix/bash-decisions.json` from baseline blob
  `3f683b37…`, line-adjusted to 1754/1771/1772/6834; preserve the existing
  decision alternatives and cases.
- [-] MQ-06.3 Rebuild the current candidate snapshot/binding in the same
  maintenance run, preserving immutable genesis B0 and milestone B1 references;
  execute and record only `G-BASH-MATRIX` against that current snapshot. Keep
  the other eight gates and their logs/digests untouched. Validate the run with
  `scripts/voice-stack/gate_evidence.py validate`.
- [x] MQ-06.4 Regularize the final evidence honestly: report the post-freeze
  `gate_evidence.py` edit (mtime 2026-10-01T22:37:30Z, after the recorded
  freeze-after at 22:36:55Z) in the handoff; set the run-level manifest status
  to the schema-valid terminal value only after all required gates validate;
  bind the final handoff to the actual current candidate. Preserve the earlier
  freeze evidence or explicitly retain its old hashes/timestamps in the
  handoff when recording the corrected final boundary.

### Outcome of round 3 (2026-10-02) — stopped, exception accepted

- MQ-06.1 / MQ-06.2: partially done, NOT verified. A delegated writer changed
  only `scripts/voice-stack/bash_matrix.py` (heredoc string/comment fix,
  command-position `until`/`select`, `elif` without extra frame, selftests
  h/i/j added) and restored the 4 decisions / 7 alternatives in
  `bash-decisions.json` (lines 1754/1771/1772/6834). No selftest or gate run.
- With the heredoc fix, the parser reads the whole file and blocks on 33
  unbalanced frames: single-line `if …; then …; fi` (e.g. 3724–3727, 4094,
  4127–4132, 4731/4735, 5127/5137, 6024/6039/6049, 6240, 6300, 6325) and
  Python `if` lines inside multiline `"$VENV_PYTHON" -c '…'` blocks (3848+,
  4431+). Fixing this is a parser redesign, outside the authorized scope.
- Owner decision 1: do not expand the round further ("Detener sin ampliar").
- Owner decision 2: leave the Bash decision matrix untested — accepted
  exception for `G-BASH-MATRIX` only; keep the partial unverified changes.
  This explicitly overrides the earlier "no exceptions" criterion for this gate.
- MQ-06.3: cancelled (no snapshot re-bind, no gate re-run).
- MQ-06.4: done as evidence regularization only — `manifest.json` status
  `in_progress` → `partial` with `accepted_exceptions`; `HANDOFF.json` records
  the exception, the post-freeze `gate_evidence.py` edit (mtime
  2026-10-01T22:37:30Z after freeze-after 22:36:55Z) and that candidate
  binding `a167549…` predates the 2 unverified files. Pre-edit copies kept as
  `manifest.pre-exception.json` / `HANDOFF.pre-exception.json`. The historical
  G-BASH-MATRIX gate entry is unchanged and is NOT valid coverage proof;
  `gate_evidence.py validate` still prints `pass` mechanically.
- G-BASH-LINES (20/20 = 100%) and the other 7 gates are unaffected.
- **M1 status: PARTIAL with accepted exception (G-BASH-MATRIX), not complete.**
  No commits, no push. VS2–VS4 stay with the other session.
