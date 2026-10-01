# AT-11 apply progress — cumulative through task 1.10 (M1, smoke 16n host-safety isolation)

Branch `feat/at-11-instalable` in worktree `/home/bruno/Code/personal/agent-tts-worktrees/at-11-instalable`.
This file is the OpenSpec-side apply-progress artifact (native locator discovered by `gentle-ai sdd-status`);
the Engram mirror at topic `sdd/at-11-instalable/apply-progress` (project `agent-tts`) carries the same
cumulative content plus exact commit hashes (this file ships inside its own work-unit commit and cannot
contain that hash).

Hash-only branch rewrite verified earlier: refreshed mapping supersedes pre-rewrite IDs
(a022c44→d89abc7, 3712ced→aae51ea, b30cd0a→0682845, b037b6f→401a4b8). Cumulative state: **9/26 tasks complete**.

## Task 1.10 — Smoke 16n host-safety isolation: playback lock/PID/IPC + engine socket (slice 8a, PR 9)

**What**: `hosts/herdr/tts-plugin/bin/herdr-tts` lines 26–28 became environment-overridable with
byte-identical defaults — `LOCK_FILE="${HERDR_TTS_LOCK_FILE:-/tmp/herdr-tts-playing.lock}"`,
`PID_FILE="${HERDR_TTS_PID_FILE:-/tmp/herdr-tts-current.pid}"`,
`IPC_SOCKET="${HERDR_TTS_IPC_SOCKET:-/tmp/herdr-tts-player.sock}"` — plus one explanatory comment
line matching the file's neighbouring-path convention; no other launcher line changed (production toggle
behavior identical, defaults identical when overrides unset). `new_env()` in `smoke-tests.sh` now creates
`$T/run` and exports the three overrides plus `AGENT_TTS_SOCKET` to sandbox-local paths
(`$T/run/playing.lock`, `$T/run/current.pid`, `$T/run/player.sock`, `$T/run/agent-tts-player.sock`),
isolating every scenario, not only section 16. Four Decision-8 tests shipped: 16p (static
defaults-preserved safeguard, `assert_grep` over the source à la 40g, x3), 16q (sandbox wiring proven
behaviorally after `new_env`, x5), 16r (stop-branch isolation: planted sandbox lock/PID holding a live
`sleep` → `r` prints the player.stopped confirmation, exactly that PID receives TERM — `wait` rc=143 —
and only the three sandbox files are removed; fails closed with a refusal if the overrides are missing,
x4), and 16s (suite-level read-only host-state fingerprint — presence/inode/mtime/content of the three
host defaults plus `/tmp/agent-tts-player.sock`, or ABSENT — compared across the whole suite run, x1).
16n itself is untouched; its assertions (including `16n read.start is the confirmation line`) ran
verbatim and green in both full-suite runs.

**Why**: design Decision 8 — the three playback-state paths were the only state paths in the launcher
header with no env override, so a suite run on a host with a live player took the stop branch: the 16n
confirmation was corrupted AND the suite TERM'd the operator's real player and deleted its state. A live
host daemon was running during this whole work unit (`herdr-tts _daemon-supervised` with live
`/tmp/herdr-tts-{playing.lock,current.pid,player.sock}`), so the unisolated suite was never executed:
RED was captured only via safe static source greps, per the safety-critical ordering.

**Where**: `hosts/herdr/tts-plugin/bin/herdr-tts` (3 lines + comment), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh`
(header bullet, `host_state_snap` + pre-suite snapshot, `new_env()` exports, 16p/16q/16r scenarios, 16s end-of-suite compare).

### Work Unit Evidence (task 1.10)

| Evidence | Result |
|---|---|
| Safe RED (static, source-reading only — suite NOT run pre-fix) | 7 standalone `grep -qE` checks mirroring 16p/16q exactly, run against the unfixed tree: **all 7 FAIL** (three launcher-override patterns absent from `bin/herdr-tts`; four `new_env` export patterns absent from `smoke-tests.sh`). Runtime RED for 16r/16s was NOT executed pre-fix — running it would have exercised the unisolated stop path against the live host daemon (the hazard itself); the failing static prerequisites stand as their RED evidence. After the fix the same 7 checks PASS |
| Focused test (Decision-8 RED tests + section 16) | Full suite run 1: all 18 new+16n assertions green — 16n x5 (incl. `read.start is the confirmation line`), 16p x3, 16q x5, 16r x4 (TERM proven via `wait` rc=143), 16s x1. Run 2 identical: 18/18 again — 16n deterministic (previously timing/host-flaky) |
| Runtime harness | `bash scripts/smoke-tests.sh` twice: run 1 **`1014 passed, 1 failed`**, run 2 **`1014 passed, 1 failed`** — the only failure both times is the known 40e baseline: `40e oracle file identity == pinned ref (boundaries.py drifts from pin 32e9bafb)`, owned by task 1.11. The suite is NOT claimed green. External read-only host-state snapshots (same fingerprint logic, outside the suite): run1-before == run1-after == run2-after — live daemon lock/PID/socket inodes+mtimes+contents unchanged, engine socket still ABSENT. The in-suite 16s assertion additionally passed in both runs |
| Rollback boundary | Launcher override lines + `new_env()` exports + 16p/16q/16r/16s assertions + `host_state_snap` revert together as one work-unit commit; a partial revert leaving the suite on host defaults restores the hazard and is forbidden |
| Changed lines | bin/herdr-tts 3+3 (+2 comment); smoke-tests.sh ~100 — **~108 authored lines** (within the 400-line budget; tasks.md estimated ~130) |

### TDD Cycle Evidence (plugin-local strict TDD — `hosts/herdr/tts-plugin/openspec/config.yaml: strict_tdd: true`)

| Task slice | RED | GREEN | REFACTOR |
|---|---|---|---|
| 16p defaults preserved (a) | Static greps over `bin/herdr-tts` FAIL (override form absent) — captured standalone, suite not run | Same greps inside suite PASS both runs | None needed |
| 16q sandbox wiring | Static greps over `smoke-tests.sh` new_env FAIL (exports absent) — captured standalone | Behavioral env assertions PASS both runs | None needed |
| 16r stop-branch isolation (c) | Not executable pre-fix (would signal the live host daemon); RED carried by the failing 16p/16q static prerequisites — documented, not simulated | 4/4 PASS both runs (TERM rc=143, sandbox-only removal) | None needed |
| 16s host-state invariance (d) | Same as 16r — suite-level RED unsafe pre-fix | PASS both runs + external snapshots invariant | None needed |
| (b) read branch via 16n | 16n unchanged (assertions verbatim); pre-fix run forbidden — host-dependent failure documented at task 1.5 as timing/host-flaky | 16n x5 green in BOTH full-suite runs — deterministic | 16n untouched |

## Task 1.9 — Scenario 3 activation correction: `activates_at_milestone` 1 → 2 (slice 3a, PR 8)

**What**: One field in one row of `scripts/acceptance/scenarios/registry.conf`: the `zero-machine-paths`
row's `activates_at_milestone` changed from `1` to `2` (tab-separated, one-line diff — exactly design
Decision 10's correction). No harness logic changed, no scenario script touched: the registry's existing
four-state semantics alone produce the intended state (authored file present from task 1.3 **and**
`current(1) < 2` ⇒ `NOT-YET-ACTIVATED` with reason "activates at milestone 2"). Scenario 3 stays authored
with its V1 static scan available; it activates when M2 task 2.2 delivers the machine-path behavior.

**Why**: Design Decision 10 resolved the M1 inconsistency — with the field at `1`, scenario 3 was ACTIVE
at M1 while the behavior it asserts is delivered at M2 (task 2.2), so a truthful active FAIL would block
M1 closure under the binding V2 gate (Engram #9636). Activation follows delivered functionality; M2 is
the correct activation milestone. Removing no M1 coverage: the V1 half (engine hygiene static scan) is an
engine pytest that stays green from task 1.3 onward.

### Work Unit Evidence (task 1.9)

| Evidence | Result |
|---|---|
| Focused test | `bash scripts/acceptance/clean-install.sh --milestone 1` → **exit 0**; scenario 3 `zero-machine-paths` = `NOT-YET-ACTIVATED (activates at milestone 2)` — not an active FAIL, not a green skip; scenarios 1/2 `NOT-YET-ACTIVATED (not authored)` until task 1.8; result line `0 PASS · 0 FAIL · 0 BLOCKED · 9 NOT-YET-ACTIVATED (activated: 0)` |
| Registry listing | `bash scripts/acceptance/clean-install.sh --list` → exit 0; row `3  zero-machine-paths  M2  V1+V2  No machine-specific paths leak from a clean install` — activation milestone 2 visible |
| Runtime harness | The clean-install V2 registry behavior itself — journal.json: `"zero-machine-paths" … "activates_at_milestone": 2, "state": "NOT-YET-ACTIVATED", "reason": "activates at milestone 2"`; run header `"milestone": 1, "closed": false, "activated": 0, "exit_code": 0` (empty run, never recorded as milestone closure) |
| Diff integrity | `git diff` shows exactly the one-line, one-field change; `cat -A` confirms tab separators preserved (`zero-machine-paths^I2^I03-…`) |
| Rollback boundary | The single registry field — but **never reverted alone**: reverting re-exposes an active M1 FAIL for scenario 3 and violates the resolved V2 gate (Engram #9636). Revert only together with an explicit gate re-decision |
| Changed lines | 1 addition + 1 deletion = **2 authored lines** (registry field; within the 400-line budget; tasks.md estimated ~15) |

Plugin smoke suite deliberately NOT run or claimed green — the `1001 passed, 1 failed (40e)` baseline is
untouched by this unit; 16n/40e fixes belong to tasks 1.10/1.11.

## Task 1.7 — Bounded OpenSpec config corrections (slice 7, PR 7)

**What**: Two bounded metadata corrections, nothing else. Root `openspec/config.yaml` context: the
"subproject-scoped, out of scope here" phrase replaced by wording that preserves the plugin subproject
registry ("no merger, no ownership transfer"), permits the root change at-11-instalable to route named
delta specs to it with explicit archive targets and make bounded metadata corrections there, and states
the plugin's local `strict_tdd` policy stays local and is not rewritten.
`hosts/herdr/tts-plugin/openspec/config.yaml` context: the stale "agent-tts pip package (separate repo,
external dependency, out of scope)" statement corrected to "agent-tts engine (engine/ subproject of this
same agent-tts monorepo; installed from the pinned monorepo source, not a separate external repo)".

**Why**: AT-11 M1 hygiene — the post-monorepo-migration reality contradicted both stale statements, and
the proposal §Registry Ownership and Spec Routing explicitly authorizes exactly this bounded correction
plus explicit delta routing; it is not a registry merger or ownership transfer. Only the `context: |`
blocks changed; no `strict_tdd`, `projects`, `testing`, or `rules` section was touched in either file,
and no other source/spec file was modified.

**Where**: `openspec/config.yaml`, `hosts/herdr/tts-plugin/openspec/config.yaml` only.

### Work Unit Evidence (task 1.7)

| Evidence | Result |
|---|---|
| Focused test | `cd engine && UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-engine-venv uv run --locked --offline --extra dev python -m pytest tests/test_versioned_tree_hygiene.py -q` → **13 passed** (0.11s) |
| Diff review vs proposal §Registry Ownership and Spec Routing | Stale external-repo claim gone from the plugin config; root wording matches "clarify … registry-scope wording to permit this bounded metadata correction and explicit delta routing … not a registry merger or ownership transfer"; both `strict_tdd` settings unchanged |
| YAML sanity | Both config files parse via `yaml.safe_load` (structure untouched — only `context: \|` block content) |
| Runtime harness | **N/A** per tasks.md work-unit table — metadata-only, no executable behavior boundary |
| Rollback boundary | Revert this work-unit commit alone — only the two `config.yaml` context blocks (+ tasks.md checkbox + this artifact); no registry history removed, no other work unit depends on the wording |
| Changed lines | 6 additions + 2 deletions = **8 authored lines** (config corrections; within the 400-line budget; tasks.md estimated ~40) |

## Task 1.6 — Packaging wrappers: legacy corrections + honest support wording (slice 6, PR 6)

**What**: All five packaging files re-anchored to the agent-tts monorepo with honest support wording:
`package.json` homepage/bugs → monorepo; npm `bin/herdr-tts` installer URL →
`raw.githubusercontent.com/chiptime/agent-tts/<ref>/hosts/herdr/tts-plugin/scripts/install.sh`;
npm README gains an explicit "not published to the npm registry" status section and a support-scope note
(Linux/WSL2/macOS targets, no native Windows, no validated cross-platform coverage); Homebrew formula
url/homepage → monorepo with tag `v0.16.0` = revision `516db8f72ed834f0c09f9570ebbaf7c4d2a0d530` (verified
via `git rev-parse v0.16.0` in this worktree), stages only `hosts/herdr/tts-plugin/*` into the keg, and
documents that first-run onboarding is NOT available on the keg route; Homebrew README mirrors the
monorepo staging + wizard limitation and fixes the recommended install command to
`herdr plugin install chiptime/agent-tts/hosts/herdr-tts-plugin`.

**Why**: AT-11 M1 hygiene — zero active legacy `chiptime/herdr-tts`/`chiptime/herdr-brain` references and
no unsupported distribution claims (spec `independent-installation` "Documented supported routes with
honest wrapper support"; proposal bounds wrappers to necessary corrections, never a new supported product).

**Where**: the five files under `hosts/herdr/tts-plugin/packaging/{npm,homebrew}/` only.

**Design note**: `prefix.install Dir["hosts/herdr/tts-plugin/*"]` (was `Dir["*"]`) is a necessary consequence
of the monorepo url correction — with a monorepo clone, `#{prefix}/scripts/bootstrap.sh` cannot exist unless
only the plugin subdirectory is staged. It preserves the original keg layout (bin/, lib/, scripts/ at prefix
root) and keeps `tools/herdr_onboarding` (wizard) out of the keg, consistent with the documented limitation.

### Work Unit Evidence (task 1.6)

| Evidence | Result |
|---|---|
| Focused test | `cd engine && UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-engine-venv uv run --locked --offline --extra dev python -m pytest tests/test_versioned_tree_hygiene.py -q` → **13 passed** (0.07s) |
| Packaging-scope scan | **Persistent** since the corrective follow-up: `LEGACY_FREE_FILES` in `engine/tests/test_versioned_tree_hygiene.py` lists the five packaging wrappers as an exact file list; `test_legacy_free_files_have_no_archived_repo_references` scans them on every run (13 passed). No blanket `packaging/` scan by design — it would substring-match the scoped npm alias `@chiptime/herdr-tts` in the out-of-scope `PUBLISH.md` (preserved, not modified) |
| Runtime harness | **N/A** per tasks.md work-unit table — wrapper metadata/docs only; npm/Homebrew routes not executable in the sandbox and no network package installs are run |
| Syntax checks | `bash -n` on npm bin wrapper OK; `package.json` parses as JSON; formula reviewed line-by-line (ruby interpreter unavailable on host) |
| Rollback boundary | Revert this work-unit commit alone — only the five packaging files (+ this artifact and the tasks.md checkbox); no other work unit touches them |
| Changed lines | 72 additions + 34 deletions = **106 authored lines** (within the 400-line budget; tasks.md estimated ~140) |

**Corrective follow-up (validator retry — persistent V1 evidence closed)**: the fresh phase-contract
validator rejected the scratch-runner proof: `engine/tests/test_versioned_tree_hygiene.py` did not include
the five task-1.6 packaging files in its versioned `LEGACY_FREE_FILES` manifest, and a scratch runner is not
persistent V1 evidence. Follow-up work unit (separate commit, `a2f56dc` NOT amended) extended
`LEGACY_FREE_FILES` with exactly the five packaging paths — `packaging/npm/package.json`,
`packaging/npm/bin/herdr-tts`, `packaging/npm/README.md`, `packaging/homebrew/herdr-tts.rb`,
`packaging/homebrew/README.md` — as an exact file list with an inline comment explaining why it is not a
blanket `packaging/` scan: `PUBLISH.md` (outside task 1.6's five-file scope, publication expansion
out-of-scope) legitimately documents the scoped npm alias `@chiptime/herdr-tts`, which the
`chiptime/herdr-tts` pattern substring-matches; the alias is preserved untouched. Task 1.6's `Files:` line
in tasks.md now includes the hygiene test. Manifest verified: 8 entries, all 5 packaging paths present,
none for `PUBLISH.md`, no blanket directory entry. Existing scan semantics unchanged (same patterns, same
exempt marker, same per-file loop). Follow-up evidence: focused suite 13 passed (0.11s) with the packaging
files inside the persistent manifest; runtime N/A (static hygiene boundary — wrapper metadata only, same
reason as the original unit); rollback boundary: revert the follow-up commit alone (hygiene test manifest +
tasks.md Files line + this artifact) — no other work unit's behavior depends on it.

## Earlier tasks (1.1–1.5, evidence carried forward from Engram #9678)

- **1.1** harness core `0a6e9fd`; **1.2** origin shim/extractor/hygiene framework `aae51ea`; **1.3** scenario 3
  + scan scope `0682845`; **1.4** installer monorepo corrections + smoke 34a–34j `401a4b8`
  (pre-rewrite IDs: 3712ced, b30cd0a, b037b6f). Planning artifacts base `d89abc7` (pre-rewrite a022c44);
  planning-scope docs expansion `673c49b` (6 openspec files, 922 authored lines).
- **1.5** bootstrap dev-mode `engine/` derivation (audit B5) `1ed9d3f`. RED: full suite `994 passed, 8 failed`
  (6 focused 33d/33e/33h + baseline 16n/40e). GREEN: `1001 passed, 1 failed (40e)` twice; 16n timing-flaky
  under load (task 1.10 owns determinism); suite NOT claimed green while 40e remains. Engine hygiene 13 passed.
  Runtime harness N/A per work-unit table. Rollback: revert `1ed9d3f` alone; 1.11 sequenced after (pin untouched,
  `AGENT_TTS_REF` still `32e9bafb…` until task 1.11).

## Mode and delivery

- Mode: **Standard** at workspace level; task 1.10 is a **plugin-strict-TDD** task (local `strict_tdd: true`
  governs `smoke-tests.sh` — RED smoke assertions authored before the `bin/herdr-tts` change; see the TDD
  Cycle Evidence table above). Tasks 1.6/1.7/1.9 were not plugin-strict-TDD tasks.
- Delivery: auto-chain, **feature-branch-chain**; task 1.10 is slice 8a / PR 9, targeting the immediately
  preceding slice's branch context; apply creates work-unit commits only — no push, no PR, no remote git.

## Full plugin suite status (updated by task 1.10)

`bash scripts/smoke-tests.sh` now reports **`1014 passed, 1 failed (40e)`** on two consecutive runs —
16n is fixed and deterministic (task 1.10); 40e remains the known baseline failure owned by task 1.11
(`boundaries.py drifts from pin 32e9bafb`). The full plugin suite is NOT claimed green by task 1.10;
M1 closure still requires task 1.11 (then 1.8) — no baseline exception.

## Next

Task 1.11 (40e exact pin `d66616bce3ad8193f11ae615bd58bb4508eb65be` + checked dual-layout oracle driver,
slice 8b, PR 10 — depends on 1.5, both edit `bootstrap.sh`, sequenced), then 1.8 (scenarios 1+2, depends
on 1.11); M1 closure needs the full plugin suite green after 1.10+1.11 (no baseline exception).
