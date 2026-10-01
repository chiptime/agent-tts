# AT-11 apply progress — cumulative through task 1.8 run (M1, scenarios 1+2 authored; BLOCKED on publication — 1.8 not complete)

Branch `feat/at-11-instalable` in worktree `/home/bruno/Code/personal/agent-tts-worktrees/at-11-instalable`.
This file is the OpenSpec-side apply-progress artifact (native locator discovered by `gentle-ai sdd-status`);
the Engram mirror at topic `sdd/at-11-instalable/apply-progress` (project `agent-tts`) carries the same
cumulative content plus exact commit hashes (this file ships inside its own work-unit commit and cannot
contain that hash).

Hash-only branch rewrite verified earlier: refreshed mapping supersedes pre-rewrite IDs
(a022c44→d89abc7, 3712ced→aae51ea, b30cd0a→0682845, b037b6f→401a4b8). Cumulative state: **10/26 tasks complete**
(task 1.8 authored and run with truthful BLOCKED evidence — NOT marked complete per the M1 gate).

## Task 1.8 — Scenarios 1+2 `plugin-fresh-clone`, `plugin-subdir-install` (slice 8, PR 11) — AUTHORED, RUN TRUTHFULLY BLOCKED; NOT COMPLETE

**What**: `scripts/acceptance/scenarios/01-plugin-fresh-clone.sh` and `02-plugin-subdir-install.sh` authored
(177 + 199 lines). Both execute the literal task-1.4 `id=` blocks from `hosts/herdr/tts-plugin/README.md`
via `run_doc_block` (unquoted file args so the hygiene id↔docs tracker sees them). Scenario 1 legs:
(A) literal `install-plugin-curl` block — outcome classified from recorded output because `curl|bash`
exits 0 on a failed fetch; (B) OQ-6 exact-pin retrieval — fresh-sandbox clone of the documented HTTPS
origin, `git fetch origin <full-sha>` (explicit remote name so the shim resolves and checks the URL),
`rev-parse <pin>^{commit}` equality, tree materialization of BOTH `hosts/herdr/tts-plugin/` and `engine/`
at the pin; (C) `#subdirectory=engine` install — repo-under-test bootstrap with NO overrides (the DEFAULT
pin is what must be installable), venv import, and `direct_url.json` asserting `commit_id == pin`,
`subdirectory == engine`, url == documented origin. Scenario 2 legs: (1a) literal `install-plugin-github`
block verbatim — herdr 0.9.1 refuses non-interactively (rc=2, "requires --yes"), asserted as observed
behavior; (1b) same documented command + herdr's documented `--yes`, clean env; (2) differential leg with
bootstrap's own documented `HERDR_AGENT_TTS_REF` test override — completes the [[build]] so the
registration materializes and the OQ-1 predicate is asserted on the registry's own fields
(`plugin_root == managed_path/<subdir>`), plus full-monorepo materialization (`engine/` present), git
checkout root, and post-hoc origin verification (herdr clones via its own transport the PATH shim cannot
see, so the managed checkout's `origin` remote is asserted equal to the documented HTTPS origin). Both
scenarios are self-contained: each clears `$XDG_DATA_HOME/herdr-tts` before build legs so bootstrap's
healthy-venv fast path can never mask the public default revision's real build state (an intra-sandbox
leakage found and eliminated during verification — the first run's scenario-2 PASS was an artifact of
scenario 1's venv). Real herdr is discovered by probing only directories the harness itself allowlisted
onto the sandbox PATH (derived, never hardcoded; no brew literal — the hygiene scan covers
`scripts/acceptance`). BLOCKED vs FAIL discipline: unavailable documented prerequisites (policy refusal,
HTTP 404, pin not fetchable) → `block()` with the precise reason, only when every executed assertion is
green; any real failure stays FAIL; consequences of an already-blocked prerequisite are skipped, never
bad-lined.

**Defect found and fixed (separate commit `d5df787`)**: `scripts/acceptance/origin-shim` treated every
non-URL first positional as a remote NAME, so uv's local cache→checkout clones (`git clone --no-hardlinks
<db> <checkout>`, absolute paths — no network origin by construction) died with exit 30 "cannot resolve
git remote". One-line fix: `/*|./*|../*` → local-only passthrough, matching the shim's own documented
semantics. Without it every engine install through uv failed inside the sandbox. Hygiene suite (including
all shim policy tests) green after the fix: 13 passed.

**Public-origin findings (read-only diagnostics + scenario evidence)**: the origin
`https://github.com/chiptime/agent-tts.git` currently serves NO tags — the documented curl route's
`v0.16.0` raw URL answers HTTP 404; public main (`e592ef31`) still pins `32e9bafbb113…` (no `engine/`
subdirectory — fetch succeeds, subdirectory missing); the exact pin `d66616bc…` IS served by full-SHA
fetch (GitHub retains it although it is on no public ref) — proven inside the sandbox by scenario 1 leg B.

### Work Unit Evidence (task 1.8)

| Evidence | Result |
|---|---|
| Harness (`--milestone 1`, real documented origins, shim active) | exit **2** — `0 PASS · 0 FAIL · 2 BLOCKED · 7 NOT-YET-ACTIVATED (activated: 2)`; scenario 3 `NOT-YET-ACTIVATED (activates at milestone 2)` per Decision 10; scenarios 4–9 `not authored` |
| Scenario 1 state + OQ-6 evidence | **BLOCKED** — "documented installer artifact unavailable at the origin: the tag-pinned URL answers HTTP 404 (tag v0.16.0 is not published)". All 10 assert.log lines green: origin cloned into clean sandbox; exact SHA fetched by full id; rev-parse == exact pin; pinned tree materializes `hosts/herdr/tts-plugin/` + `engine/`; bootstrap installed engine from the pinned public ref with NO overrides (local dev checkout ignored); venv imports `agent_tts`; `direct_url.json` records `commit_id == d66616bc…`, `subdirectory == engine`, documented url. **OQ-6: the exact pin IS publicly retrievable** |
| Scenario 2 state + OQ-1 evidence | **BLOCKED** — "documented subdirectory route cannot complete at the public default revision: its [[build]] bootstrap still pins 32e9bafbb113… with no engine/ subdirectory, while the corrected pin … is authored in this change and unpublished (proven by the differential leg)". All 10 assert.log lines green: real herdr 0.9.1 resolved on the sandbox PATH; verbatim command refused non-interactively (observed, not worked around); differential override leg completed; `plugin_root == managed_path/<subdir>` (`…/plugins/github/herdr.tts-683bf97d4464/hosts/herdr/tts-plugin`); full monorepo materialized incl. `engine/`; managed_path is a git checkout root; clone origin == documented HTTPS origin (post-hoc policy verification); [[build]] venv imports `agent_tts`. **OQ-1: CONFIRMED, not falsified — the vendoring fallback is NOT needed** |
| Engine V1 suite (M1 closure leg) | `cd engine && UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-engine-venv uv run --locked --offline --extra dev python -m pytest tests/ -q` → **807 passed, 11 skipped** (124.95s) |
| Brain V1 suite (M1 closure leg) | `hosts/herdr/brain` with worktree-local `UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-brain-venv` (main-checkout venv is editable-bound to main's src — not usable for worktree truth): `python -m pytest tests/ -q` → **571 passed**; `node --test tests/js/` → **179 pass, 0 fail** |
| Plugin V1 suite (M1 closure leg) | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` → **`1022 passed, 0 failed`**, exit 0, `16s host playback state invariant across the full suite run` green (no live-daemon interference; no signal ever sent to the host daemon) |
| Rollback boundary | Scenario files + this evidence revert as one work-unit commit (`test(acceptance): add real clean-install scenarios`); the shim local-path fix reverts separately (`d5df787`) but doing so re-breaks every uv install inside the sandbox; tasks.md checkbox intentionally untouched (task not complete) |
| Changed lines | 01-plugin-fresh-clone.sh 177; 02-plugin-subdir-install.sh 199; origin-shim +1 (separate commit) — **~377 authored lines** (within the 400-line budget; tasks.md estimated ~240) |

### M1 closure decision: NOT CLOSED — truthful BLOCKED, blocker returned to the maintainer

All three V1 suites are green and scenario 3 reports `NOT-YET-ACTIVATED` correctly, but V2 scenarios 1+2
are BLOCKED: the documented public origins do not currently serve what the documented routes require.
**Both blockers are publication gaps, not code defects** — the at-11-corrected state is local-only (no
push authorized):

1. Tag `v0.16.0` is not published on `github.com/chiptime/agent-tts` (the origin serves no tags at all) —
   the documented curl|sh route 404s for a public user.
2. No public default-branch revision carries the corrected bootstrap pin — public main's [[build]] still
   pins `32e9bafb…` (no `engine/`), so a genuinely fresh `herdr plugin install` fails its build hook.

Maintainer action to close M1: publish tag `v0.16.0` (or update the README block to a published tag) and
push a default-branch revision whose `bootstrap.sh` pins `d66616bce3ad8193f11ae615bd58bb4508eb65be` (or
any published ref that installs). The scenarios are written to turn green without edits once publication
catches up — no substitute SHA, no moving branch, no cache-only pass was used anywhere. Re-run
`bash scripts/acceptance/clean-install.sh --milestone 1 --record-dir /tmp/opencode/at11-m1` after
publication; M1 closes when it exits 0.

## Task 1.11 — Smoke 40e oracle identity: exact pin + checked dual-layout lookup (slice 8b, PR 10)

**What**: `hosts/herdr/tts-plugin/scripts/bootstrap.sh` `AGENT_TTS_REF` default re-pinned from the stale
pre-monorepo `32e9bafbb113df847d7cd9b635b0e848ee182f6f` to the exact maintainer-selected full SHA
`d66616bce3ad8193f11ae615bd58bb4508eb65be` (`HERDR_AGENT_TTS_REF` override retained; comment records the
Decision-9 rationale; no other bootstrap behavior touched). The 40e identity driver in `smoke-tests.sh`
was rewritten per design Decision 9: (1) revision precheck `git cat-file -e <pin>^{commit}` — an unknown
pin fails as `revision-absent` (`pinned revision <rev> not present`) before any layout is probed, distinct
from a layout miss, no layout retry; (2) dual-layout probe `engine/src/agent_tts/` (monorepo) then legacy
`src/agent_tts/` at that single revision, the layout selected **as a unit** (all three files or the next
layout — layout compatibility is never a revision fallback); (3) every `git rev-parse`/`hash-object`
lookup asserts returncode == 0 **and** `^[0-9a-f]{40}$` blob shape — `git rev-parse` echoes its failed
argument to stdout with rc 128, so emptiness-based guards never fire; (4) strict byte identity retained
for `boundaries.py`, `cleaner.py`, `redact.py` with the full existing F1–F9 + sidecar + redaction
assertion matrix verbatim — no assertion weakened, deleted, or satisfied by changing engine bytes. New
assertions: bash-level `40e pin is the exact selected full SHA d66616bc` static check (40g style),
in-driver `40e pin is the exact selected SHA (Decision 9)`, and five hermetic Decision-9 safeguards on a
throwaway sandbox git repo (monorepo-only fixture commit resolves via `engine/src`, legacy-only fixture
commit resolves via `src`, bogus revision → `revision-absent` with no layout paths in the detail,
absent-path rev-parse decoy never matches blob shape, absent layout → hard `layout-miss` naming every
probed path).

**Why**: design Decision 9 — three defects made 40e the deterministic baseline failure: the stale pin
(32e9bafb has no `engine/`), a monorepo-only probe (could never resolve a legacy-layout pin), and unchecked
return codes (rev-parse's echoed-argument stdout is a decoy that emptiness guards cannot catch). The
post-fix state is genuinely green, verified read-only: the pin commit exists in the shared object store
(the worktree is a linked worktree of `/home/bruno/Code/personal/agent-tts`), and the three oracle blobs at
`d66616bc:engine/src/agent_tts/` equal the working-tree blobs in BOTH checkouts
(`boundaries.py` `7879e17c…`, `cleaner.py` `a856e8b3…`, `redact.py` `0b46b42d…`). Local resolution is NOT
public-retrievability evidence — that proof belongs to task 1.8 (OQ-6, documented GitHub origin, `BLOCKED`
on unavailability, never a substitute SHA).

**Where**: `hosts/herdr/tts-plugin/scripts/bootstrap.sh` (pin + comment), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh`
(header comment, 40e comment + static pin assert, driver identity-safeguard rewrite + sandbox safeguards).

### Work Unit Evidence (task 1.11)

| Evidence | Result |
|---|---|
| Safe RED (structural, pre-edit greps over the pristine tree) | `cat-file` occurrences in smoke-tests.sh: **0**; `returncode` checks in the 40e driver: **0**; legacy-layout probe `"src/agent_tts"`: **0**; exact-pin string anywhere: **0** — Decision-9 tests b/c/d structurally absent; the decoy re-confirmed live: `git rev-parse 32e9bafb:engine/src/agent_tts/boundaries.py` → stdout = the echoed argument, **rc=128** |
| Focused RED (new driver + assertions authored, pin still stale, suite-faithful env: `AGENT_TTS_LEXICON` fixture + isolated HOME) | Extracted 40e driver run against the real venv: **67 OK, exactly 2 ERR** — `40e pin is the exact selected SHA (Decision 9) :: 32e9bafbb113…` (test a) and `40e oracle file identity == pinned ref :: boundaries.py drifts from pin 32e9bafb (src/agent_tts)` (test e — the dual-layout resolver selected the LEGACY layout at the stale pin and failed on content, proving resolution works and the stale pin genuinely drifts); bash static pin assert: **NO MATCH** (test a) |
| Focused GREEN | Same extracted driver after the re-pin: **69 OK, 0 ERR** — `40e oracle file identity == pinned ref :: editable install, oracle files == engine/src/agent_tts @ pin d66616bc`; static exact-pin assert MATCH; `40g bootstrap pin still SHA-pinned` regex MATCH (generic SHA safeguard intact — new pin is full 40-hex) |
| Full plugin suite (runtime harness — the suite IS the hermetic runtime) | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` — run 1: **exit 0, `1022 passed, 0 failed`** (2m21s); run 2: **exit 0, `1022 passed, 0 failed`** (deterministic). **First genuine full-suite green** (baseline `1014 passed, 1 failed (40e)` → +7 new checks, 0 failures): all 40e F1–F9/sidecar/redaction assertions verbatim-green, 40e matrix count 69, 40g all four green, 16n x5 green, 16s host-state invariance green |
| Engine hygiene (offline, no-regression confirmation) | `cd engine && UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-engine-venv uv run --locked --offline --extra dev python -m pytest tests/test_versioned_tree_hygiene.py -q` → **13 passed** (0.43s) |
| Rollback boundary | Pin + 40e driver rewrite + all new assertions + this evidence revert together as one work-unit commit; restoring `32e9bafb` restores the known baseline failure (`boundaries.py drifts` via the legacy layout), not a passing release; no engine byte was touched |
| Changed lines | bootstrap.sh 5+2; smoke-tests.sh 108+18 — **133 authored implementation lines** (within the 400-line budget; tasks.md estimated ~140) |

### TDD Cycle Evidence (plugin-local strict TDD — `hosts/herdr/tts-plugin/openspec/config.yaml: strict_tdd: true`)

| Task slice | RED | GREEN | REFACTOR |
|---|---|---|---|
| (a) pin value exact | Static assert + in-driver check FAIL against stale pin (focused run: 2 ERR) | Both PASS post-re-pin; 40g generic SHA assertion passes unchanged | None needed |
| (b) dual-layout resolution | Structural: no legacy-layout probe existed (grep 0); monorepo-only probe = the baseline failure mechanism | Hermetic sandbox fixtures: monorepo-only commit → `engine/src` selected, legacy-only commit → `src` selected, each as a unit; real pin resolves via `engine/src/agent_tts @ d66616bc` | None needed |
| (c) checked return codes, no decoy | Structural: zero `returncode` checks in the old driver; decoy proven live (rc 128, echoed argument on stdout) | Every lookup guarded rc==0 AND `^[0-9a-f]{40}$`; decoy check asserts rc!=0 + shape mismatch; absent path → hard named `layout-miss` | None needed |
| (d) unknown revision distinct | Structural: no `cat-file` revision precheck existed | Bogus 40-hex rev → `revision-absent` with no layout paths in the detail (no layout retry) | None needed |
| (e) strict identity at the pin | Baseline failure reproduced in focused RED: `boundaries.py drifts from pin 32e9bafb (src/agent_tts)` | `editable install, oracle files == engine/src/agent_tts @ pin d66616bc`; full F1–F9 matrix green twice in-suite | Existing assertions untouched |

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

- Mode: **Standard** at workspace level; tasks 1.10 and 1.11 are **plugin-strict-TDD** tasks (local
  `strict_tdd: true` governs `smoke-tests.sh` — RED smoke assertions authored before the production
  change; see the TDD Cycle Evidence tables above). Tasks 1.6/1.7/1.9 were not plugin-strict-TDD tasks.
- Delivery: auto-chain, **feature-branch-chain**; task 1.11 is slice 8b / PR 10, targeting the immediately
  preceding slice's branch context; apply creates work-unit commits only — no push, no PR, no remote git.

## Full plugin suite status (re-verified by the task-1.8 M1 closure attempt)

`bash scripts/smoke-tests.sh` reports **`1022 passed, 0 failed` (exit 0)** on two consecutive runs —
both M1 baseline failures are now genuinely fixed (16n by task 1.10, 40e by task 1.11: exact pin
`d66616bce3ad8193f11ae615bd58bb4508eb65be`, checked dual-layout resolution, strict three-file byte
identity at the monorepo layout). **The full plugin V1 suite is green and claimable from this task on.**
M1 closure still requires task 1.8 (V2 scenarios 1+2: documented-origin exact-pin retrieval — OQ-6 —
or truthful `BLOCKED`) plus the other two project suites at integrated closure; no baseline exception
was used anywhere.

## Next

Task 1.8 stays open pending maintainer publication (see the M1 closure decision above): publish tag
`v0.16.0` and a default-branch revision pinning `d66616bc…`, then re-run
`bash scripts/acceptance/clean-install.sh --milestone 1 --record-dir /tmp/opencode/at11-m1` — scenarios 1+2
are expected to flip to PASS without edits, closing M1 (all three V1 suites already green). No baseline
exception was used anywhere; OQ-6 (exact-pin public retrieval) and OQ-1 (subdirectory materialization)
are both answered with green evidence. After M1 closes, task 2.1 (slice 9, PR 12) starts M2.
