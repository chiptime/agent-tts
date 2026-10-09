# AT-11 apply progress — cumulative through task 2.6 COMPLETE (M1 CLOSED + M2 slices 9–14, closure conditions observed — see Task 2.6; 17/26 tasks)

Branch `feat/at-11-instalable` in worktree `/home/bruno/Code/personal/agent-tts-worktrees/at-11-instalable`.
This file is the OpenSpec-side apply-progress artifact (native locator discovered by `gentle-ai sdd-status`);
the Engram mirror at topic `sdd/at-11-instalable/apply-progress` (project `agent-tts`) carries the same
cumulative content plus exact commit hashes (this file ships inside its own work-unit commit and cannot
contain that hash).

Hash-only branch rewrite verified earlier: refreshed mapping supersedes pre-rewrite IDs
(a022c44→d89abc7, 3712ced→aae51ea, b30cd0a→0682845, b037b6f→401a4b8). Cumulative state: **12/26 tasks
complete** (M1 closed with no baseline exception; M2 opened by task 2.1 — the shared resolver layer).

## Task 2.1 — `resolve.py` + bash resolvers: monorepo root, `HERDR_BIN`, port, `HERDR_TTS_HOME` (slice 9, PR 12) — COMPLETE

**What**: `tools/herdr_onboarding/` created (stdlib-only, design Decision 1 — no venv gains a dependency):
`resolve.py` implements the four Decision-4 orders. Root: `HERDR_PLUGIN_ROOT` → ascend ≤6 levels from the
resolved real path of the executing script (monorepo marker `hosts/herdr/tts-plugin`). `HERDR_TTS_HOME`
(OQ-2, Engram #9681 — binding): set-and-valid wins **even when a sibling `tts-plugin` exists**; unset or
unusable derives `<brain-root>/../tts-plugin`; a hardcoded default is never returned or exported; neither
candidate usable → `ResolutionError` naming both. `HERDR_BIN`: six steps — env → PATH →
`$HOMEBREW_PREFIX/bin/herdr` → `brew --prefix` probe (gated on `brew` being on the environment's PATH, so
hermetic sandboxes never invoke it) → `$HOME/.local/bin/herdr` → bare `herdr`. Port: `HERDR_BRAIN_PORT` →
persisted config (`<XDG_CONFIG_HOME or ~/.config>/herdr-brain/config.env`, key `HERDR_BRAIN_PORT`,
sourcable `KEY=VALUE` so bash and Python read one knob natively) → `8741`; invalid set values fail closed.
Both launchers carry the **byte-identical** `herdr_resolve_*` bash mirror block (marker-delimited
`# >>> herdr portable resolvers … <<<`), self-anchored at `dirname "$(readlink -f "${BASH_SOURCE[0]}")"`;
the plugin wires root → `PLUGIN_ROOT`; the brain wires root → `REPO_DIR` and `load_env`'s port / tts-home /
`HERDR_BIN` through the resolvers. `hosts/herdr/brain/src/herdr_brain/config.py` plumbs `Settings.brain_port`
mirroring the port order locally (the installed package cannot import the tools module without adding a
runtime dependency — design Decision 1).

**Scope boundary honored**: task 2.2's remaining repairs are untouched (dotfiles scrape, personal tailnet
reference, parity test module, hygiene-scan scope extension, scenario-3 assertions). Delivering the 2.1
mandates necessarily removed two defect lines from `bin/herdr-brain` — the dead `HERDR_TTS_HOME` default
export (A1: "never export a hardcoded default") and the literal `/home/linuxbrew/.linuxbrew/bin/herdr`
fallback (replaced by six-step discovery) — both asserted by the new 44f checks; 2.2 completes the launcher
repair (A1's parity proof, C1/C2/C4).

**Why**: design Decision 4 / slice 9 — machine-path decoupling starts with one shared resolution layer;
OQ-2 precedence binding (Engram #9681).

**Where**: `tools/herdr_onboarding/{__init__.py,resolve.py}` (create), `hosts/herdr/tts-plugin/bin/herdr-tts`
and `hosts/herdr/brain/bin/herdr-brain` (resolver block + wiring), `hosts/herdr/brain/src/herdr_brain/config.py`
(port knob), `hosts/herdr/brain/tests/test_resolve.py` (create), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh`
(section 44).

### Work Unit Evidence (task 2.1)

| Evidence | Result |
|---|---|
| Focused brain test | `cd hosts/herdr/brain && UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-brain-venv uv run --locked --offline --extra dev python -m pytest tests/test_resolve.py -q` → **34 passed** (0.20s; re-run green after the final launcher tweak) |
| Full brain V1 (config.py changed) | same environment, `python -m pytest tests/ -q` → **605 passed** (571 baseline + 34 new, 12.75s); `node --test tests/js/` → **179 pass / 0 fail** (no JS surface touched) |
| Plugin RED (strict TDD — section 44 authored first, launchers untouched) | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` → **`1023 passed, 31 failed`**, exit 1 — all 32 new section-44 checks RED (31 FAIL + the diff-of-two-absent-blocks parity check vacuously identical, gated by the presence checks that failed); baseline 1022 untouched; 16s host-state invariant green |
| Plugin GREEN | `bash scripts/smoke-tests.sh` → **`1054 passed, 0 failed`**, exit 0 (1022 + 32 new) — run twice: after implementation and after the final cd-noise hardening; 16s host playback state invariant green both times (no signal ever sent to the host daemon; no host-state change observed) |
| Runtime harness — launcher resolver drill in source layout | Shipped block extracted from each real launcher into a temporary `.drill-resolvers.sh` beside it (deleted immediately; worktree clean afterwards): brain root → `<worktree>/hosts/herdr/brain`; tts sibling → `<worktree>/hosts/herdr/tts-plugin`; set-and-valid `HERDR_TTS_HOME` override wins; port default 8741 / `config.env` 8799 / env 8801; unusable override + no sibling → single actionable ERROR naming both candidates (a `cd` noise line exposed by the drill was hardened and both suites re-run green); plugin root → `<worktree>/hosts/herdr/tts-plugin`; `HERDR_PLUGIN_ROOT=/opt/keg` honored; `bash -n` both launchers OK; `bin/herdr-brain help` rc 0 |
| Rollback boundary | Revert the task-2.1 work-unit commit: `tools/herdr_onboarding/` disappears, both launchers return to their pre-resolver headers (plugin: inline `PLUGIN_ROOT` one-liner; brain: dead `HERDR_TTS_HOME` default + 4-step `HERDR_BIN` with the linuxbrew literal — exactly the pre-repair state task 2.2 will finish repairing), `config.py` loses `brain_port`, brain test module and smoke section 44 removed. No other work unit consumes the resolver yet (2.2+ are the next consumers). |
| Changed lines | code 946 additions + 17 deletions = **963 authored** (launchers 223+17, `resolve.py` 214, `__init__.py` 8, brain tests 286, `config.py` 56, smoke section 160) + tasks.md checkbox 1+1 — **over the 400-line budget** (tasks.md estimated ~300): the slice mandates a new module, its dual-launcher byte-identical mirror, brain pytest AND plugin strict-TDD smoke surfaces, and config plumbing. Implemented honestly without cutting tests, assertions or comments; `size:exception` recommended for PR 12 (feature-branch-chain child of the tracker branch, per the confirmed chain strategy) |

### TDD Cycle Evidence (plugin-local strict TDD — `hosts/herdr/tts-plugin/openspec/config.yaml: strict_tdd: true`; workspace Standard mode governs the brain side)

| Slice | RED | GREEN | REFACTOR |
|---|---|---|---|
| 44a block presence + four functions | FAIL (block absent from both launchers) | ok ×5 | None needed |
| 44b root: derive / override / 3-level ascent / beyond-6 actionable failure | FAIL (`herdr_resolve_root: command not found`; no error text) | ok ×4 | None needed |
| 44c OQ-2 tts-home precedence (override-wins-over-sibling THE assertion) | FAIL | ok ×3 | `cd` noise on the missing-root edge hardened after the drill; both suites re-run green |
| 44d six-step `HERDR_BIN` (PATH beats prefix; brew via stubbed `--prefix`) | FAIL ×6 | ok ×6 | None needed |
| 44e port order env → config.env (quoted/noisy) → 8741 | FAIL ×4 | ok ×4 | None needed |
| 44f parity: byte-identical blocks, wiring, no dead default, no literal brew prefix | FAIL ×10 | ok ×10 | None needed |

## Task 1.8 — Scenarios 1+2 `plugin-fresh-clone`, `plugin-subdir-install` (slice 8, PR 11) — COMPLETE via the authorized candidate branch (design Decision 11)

**First pass (preserved history)**: authored and run truthfully BLOCKED against the then-unpublished
stable route (`d5df787` shim fix + `ce2afed` scenarios) — that blocked evidence for the STABLE route
stays recorded below and in `/tmp/opencode/at11-m1`: the remote `v0.16.0` tag is absent and public
`main` still carries legacy URLs + pin `32e9bafb`. Nothing in the candidate run repairs, publishes, or
validates the stable route.

**Completion (Decision 11, this batch)**: SDD planning docs committed first as two work-unit commits
(scope/acceptance: proposal + independent-installation/installation-diagnostics deltas; branch
decision/threat model/task order: design Decision 11 + tasks rework), then one code work unit
(README `id=install-plugin-curl` parameterization + both scenario reworks + embedded Decision-11 RED
tests a–f). After the local commit, the authorized validation branch was created via the named `gh`
session `chiptime` over HTTPS with a per-command credential helper (no global git config change, no
SSH): explicit refspec `feat/at-11-instalable:refs/heads/validation/at-11-instalable` to
`https://github.com/chiptime/agent-tts.git`, fast-forward-only (the remote ref was ABSENT → create;
`main` untouched at `e592ef31`). Remote ref verified resolving to the pushed commit
`63d58714ae003efaf1c0f2cfe633818900db0082` before the V2 run. No tag, PR, merge, release, deletion,
or force operation was performed; V3 remains the human gate.

**What changed (code work unit)**: `hosts/herdr/tts-plugin/README.md` — the `id=install-plugin-curl`
block now carries exactly one `${HERDR_TTS_REF:-v0.16.0}` expansion covering BOTH the raw installer
URL and (via `install.sh`'s existing knob, reached because the scenario exports the variable) the
piped installer's clone; the stable default `v0.16.0` is preserved (unset ⇒ byte-identical documented
URL, and `install.sh`'s own default is untouched — smoke 34 series still green); the escape-hatch
example was CORRECTED from `HERDR_TTS_REF=main curl …` (prefix assignment reaches only curl, never
the piped bash) to `export HERDR_TTS_REF=main` + one parameterized URL. Scenario 01: Decision-11
static gate (tests a+b: exactly-one expansion, no hardcoded stable ref outside it, byte-stable unset
default), candidate resolution by `git ls-remote` on the documented origin recorded to
`candidate.json` + assert lines (test d), `export HERDR_TTS_REF=validation/at-11-instalable` before
the LITERAL `run_doc_block`, then HEAD == resolved candidate commit, installer-output ref check, and
a fetched-script byte-identity probe (`git hash-object` vs `HEAD:…install.sh`) proving the override
selected both halves (test c); OQ-6 legs B (by-SHA fetch) and C (`direct_url.json` == pin +
`#subdirectory=engine`, no overrides) unchanged and independent (test f); no-claim self-scan (test e).
Scenario 02: evidence leg is now herdr's supported `herdr plugin install
chiptime/agent-tts/hosts/herdr/tts-plugin --ref validation/at-11-instalable --yes`; the registration's
`resolved_commit` must equal the candidate branch commit (test d); OQ-1 assertions on the registry's
own fields plus `direct_url.json` engine-pin checks (test f); the former `HERDR_AGENT_TTS_REF`
differential leg is DEMOTED to a diagnostic fallback that only runs to classify a failed candidate
leg (publication/prerequisite gap ⇒ BLOCKED vs real defect ⇒ FAIL), with its evidence recorded as
diagnostics, never as the candidate route. Both scenarios: BLOCKED-vs-FAIL discipline extended to the
candidate-resolution gate (an unresolvable candidate ref blocks ONLY when every executed assertion is
green; earlier real failures stay FAIL — a masking flaw found and fixed during the RED pass because
`run_scenario` zeroes assertion counts once `blocked.reason` exists).

### Work Unit Evidence (task 1.8 completion — Decision 11 candidate branch)

| Evidence | Result |
|---|---|
| RED pass (harness at `27ecb68`, old README still HEAD, scenarios from the working tree) | Scenario 1 **FAIL — 1 ok / 3 fail** (test a RED: zero ref expansions found; hardcoded v0.16.0 outside the expansion; candidate unresolvable recorded as fail, not masked). Scenario 2 **BLOCKED** — "authorized candidate ref validation/at-11-instalable does not resolve to a commit at the documented origin … no substitute ref, M1 stays open" (the ref was not yet pushed). Exit 1 |
| Authorized remote write | `git -c credential.helper='!gh auth git-credential' push https://github.com/chiptime/agent-tts.git feat/at-11-instalable:refs/heads/validation/at-11-instalable` → `* [new branch]` (remote ref was absent ⇒ create is the fast-forward); `gh auth status` confirmed the ACTIVE session is the named `chiptime`; post-push `git ls-remote` shows `63d58714ae003efaf1c0f2cfe633818900db0082 refs/heads/validation/at-11-instalable` == local HEAD. No token material printed; no PR opened (GitHub's PR suggestion ignored) |
| Harness (GREEN, `--milestone 1 --record --record-dir /tmp/opencode/at11-m1-v2`) | **exit 0** — `2 PASS · 0 FAIL · 0 BLOCKED · 7 NOT-YET-ACTIVATED (activated: 2)` at commit `63d5871`; journal `/tmp/opencode/at11-m1-v2/clean-install-63d5871.json`; scenario evidence preserved under `/tmp/opencode/at11-m1-v2/evidence/` (a `--keep` re-run, identical results, provided the assert.log/candidate.json artifacts) |
| Scenario 1 state | **PASS — 20 ok / 0 fail / 0 stubbed**: static tests a+b green; candidate `validation/at-11-instalable` → `63d58714ae003efaf1c0f2cfe633818900db0082`; literal documented curl block completed at the candidate ref; installer reports cloning at the ref; installed checkout HEAD == candidate commit; fetched installer script byte-identical to the candidate tree; OQ-6 legs green — exact SHA `d66616bc…` fetched BY FULL ID from `https://github.com/chiptime/agent-tts.git`, resolves to exactly the pin, pinned tree materializes `hosts/herdr/tts-plugin/` + `engine/`; no-override bootstrap installs engine from the pinned public ref; `direct_url.json` records `commit_id == d66616bc…`, `subdirectory == engine`, documented url; no-claim scan green |
| Scenario 2 state | **PASS — 16 ok / 0 fail / 0 stubbed**: real herdr 0.9.1; candidate resolved; verbatim `id=install-plugin-github` block refused non-interactively (observed); candidate leg `--ref validation/at-11-instalable --yes` completed in the clean environment; registration `plugin_root == managed_path/hosts/herdr/tts-plugin` (`…/plugins/github/herdr.tts-683bf97d4464/hosts/herdr/tts-plugin`), `resolved_commit == 63d58714…` (attribution), full monorepo incl. `engine/`, managed_path a git checkout root, clone origin == documented HTTPS origin; `[[build]]` venv imports `agent_tts`; `direct_url.json` `commit_id == d66616bc…` + `subdirectory == engine`; no-claim scan green. Diagnostic fallback leg NOT triggered (candidate leg passed). **OQ-1 CONFIRMED on the candidate route — vendoring fallback not needed** |
| Engine V1 suite | `UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-engine-venv uv run --locked --offline --extra dev python -m pytest tests/ -q` → run 1: 1 failed (`test_chain_playback.py::test_chain_honors_stop_flag_mid_file_remaining_files_not_played`, a timing-sensitive test; no engine file was touched by this batch) + 806 passed, 11 skipped; isolated re-run of that module: 9 passed; **full re-run: 807 passed, 11 skipped** — matching the M1 baseline; the single first-run failure is reported as flaky, not waived |
| Brain V1 suite | worktree-local `UV_PROJECT_ENVIRONMENT=/tmp/opencode/at11-brain-venv`: `python -m pytest tests/ -q` → **571 passed**; `node --test tests/js/` → **179 pass / 0 fail** |
| Plugin V1 suite | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` → **`1022 passed, 0 failed`**, exit 0, `16s host playback state invariant across the full suite run` green (no signal ever sent to the host daemon; no external host-state change observed) |
| Rollback boundary | README `id=`-block edits + the two scenario files + this evidence revert as one work-unit commit; the remote candidate ref is push-only and never rewritten (revert does NOT delete it); docs commits revert as their own two units |
| Changed lines (this batch) | docs A: 184+5 (proposal + 2 spec deltas); docs B: 309+35 (design + tasks); code: 295+74 (README 12, scenario 01 ~+99, scenario 02 ~+110 net) — code unit **369 authored lines** (within the 400-line budget; tasks.md estimated ~285) |

### M1 closure decision: CLOSED — candidate-branch V2 green, all three V1 suites green, no baseline exception

V2 scenarios 1 and 2 PASS from genuine installs against the authorized candidate branch
`validation/at-11-instalable` @ `63d58714ae003efaf1c0f2cfe633818900db0082`, with journal evidence naming
the branch ref, its resolved commit, the engine SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be`,
`#subdirectory=engine`, and `plugin_root = managed_path/hosts/herdr/tts-plugin`. Decision-11 RED tests
a–f green. All three V1 suites green (engine 807/11s, brain 571 + 179 JS, plugin 1022/0) with the 16n/40e
baseline fixes from tasks 1.10/1.11 intact — no baseline exception. Scenario 3 correctly
`NOT-YET-ACTIVATED` (activates at M2 per Decision 10); scenarios 4–9 `NOT-YET-ACTIVATED` (full-suite
gate applies only at M4). The stable documented route (`v0.16.0` tag, public `main`) remains UNVALIDATED
and its preserved BLOCKED evidence below stands — closure of M1 via the candidate branch per design
Decision 11 explicitly neither requires nor claims stable publication; stable publication stays
maintainer-owned after V3. Next: task 2.1 (slice 9, PR 12).

### First-pass authoring record (preserved history — stable-route BLOCKED evidence stands)

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

### First-pass M1 decision (PRESERVED — superseded by the Decision-11 closure above; the stable-route findings remain true and unclaimed)

All three V1 suites are green and scenario 3 reports `NOT-YET-ACTIVATED` correctly, but the STABLE documented
routes were BLOCKED at the first pass — publication gaps, not code defects (blockers returned to the
maintainer, who answered with the validation-branch authorization, Engram #9783, instead of an early
stable release):

1. Tag `v0.16.0` is not published on `github.com/chiptime/agent-tts` (the origin serves no tags at all) —
   the documented curl|sh route 404s for a public user.
2. No public default-branch revision carries the corrected bootstrap pin — public main's [[build]] still
   pins `32e9bafb…` (no `engine/`), so a genuinely fresh `herdr plugin install` fails its build hook.

Maintainer answer to these blockers (recorded outcome): the authorized validation branch
`validation/at-11-instalable` (Engram #9783) instead of an early stable release — implemented by the
Decision-11 completion above. The scenarios were written to need no substitute SHA, no moving branch as
an engine pin, and no cache-only pass; the stable-route re-validation after a real publication remains a
post-V3 maintainer-owned step (re-run `bash scripts/acceptance/clean-install.sh --milestone 1` with
`HERDR_TTS_REF` unset).

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
  change; see the TDD Cycle Evidence tables above). Tasks 1.6/1.7/1.9/1.8 were not plugin-strict-TDD
  tasks (1.8's Decision-11 RED tests a–f are embedded harness assertions, proven RED at `27ecb68`
  before the code commit).
- Delivery: auto-chain, **feature-branch-chain**; task 1.8 is slice 8 / PR 11; apply creates
  work-unit commits only. The ONE authorized remote exception (design Decision 11, Engram #9783) is
  the push-only candidate ref `validation/at-11-instalable` (created at `63d58714…`, fast-forward-only,
  gh session `chiptime`, HTTPS) — never a PR, never part of the chain; M2–M4 V2 updates of that same
  ref follow the same narrow authorization.

## Full plugin suite status (re-verified by the task-1.8 M1 closure)

`bash scripts/smoke-tests.sh` reports **`1022 passed, 0 failed` (exit 0)** — re-verified in the M1
closure run of this batch. Both M1 baseline failures are genuinely fixed (16n by task 1.10, 40e by
task 1.11). **The full plugin V1 suite is green and claimable.**

## Next

M1 is CLOSED (see the Decision-11 closure record above); M2 is open with slice 9 delivered: **12/26 tasks
complete**. **Task 2.2** (slice 10, PR 13 — brain launcher repair A1-parity/C1/C2/C4 + hygiene-scope
extension + scenario-3 assertion finalization) continues M2 and consumes the resolver layer delivered here.
The 2.1 commit was NOT pushed: no M2 V2 scenario runs until tasks 2.2/2.6; if an M2+ V2 run needs the newer
local commits, first fast-forward the authorized candidate branch `validation/at-11-instalable` per the
same narrow authorization (divergent ⇒ `BLOCKED`, never force; never `main`/tag/PR). Stable publication
(tag `v0.16.0`, repaired `main`) remains maintainer-owned after V3; the stable route stays unvalidated and
its BLOCKED evidence stands.

## Tasks 2.2–2.5 (M2 slices 10–13, PRs 15–18) — COMPLETE

Branch `feat/at-11-onboarding-m2` (worktree `agent-tts-worktrees/at11-onboarding`, based on `a12e727`);
local commits only, nothing pushed. **Task 2.1 state at start**: present and green on this branch
(`c577041` is an ancestor of `HEAD`; `tools/herdr_onboarding/resolve.py` and the byte-identical bash
`herdr_resolve_*` blocks exist; `tests/test_resolve.py` 35 passed). The Decision-12 local re-slice
(9a/9b/9c, backup ref `backup/at-11-task-2.1-c577041`) has **not** been performed here (the backup ref does
not exist) and was out of scope; the resolver block was not modified by any of these tasks.

| Task | Commit | Focused verification (observed) |
|------|--------|---------------------------------|
| 2.2 launcher repair | `4ba2efb` | RED: `test_launcher_repair.py` 6 failed/11 passed + hygiene `brain-launcher` scope failing (3 findings) → GREEN: 17 passed + hygiene 13 passed |
| 2.3 port policy | `66ce2ad` | RED: `test_port_policy.py` 12 failed/7 passed → GREEN: 19 passed (foreign listener survives, exit 98) |
| 2.4 systemd template | `8f58172` | RED: `test_systemd_template.py` 20 failed/3 passed → GREEN: 23 passed; `clean-install.sh --milestone 2` scenario 3 FAIL(5) → `PASS` |
| 2.5 CLI exposure | `425deef` | RED: smoke 34k–34q 17 failures (`1061 passed, 19 failed`) → GREEN `1078 passed, 2 failed` (17a/17c = base failures) |

**Task 2.2** — removed from `bin/herdr-brain`: the dotfiles key scrape (C2), the personal tailnet domain and
the Tailscale probing (C4; the optional remote URL now comes only from `HERDR_BRAIN_REMOTE_URL`, env or
`config.env`), the machine-path comment. The literal brew prefix and dead `HERDR_TTS_HOME` default were
already gone from 2.1. Parity proof: copies of the real launcher run in sandboxes — corrected monorepo layout
resolves the sibling `tts-plugin`; legacy-shaped layout honours an exported `HERDR_TTS_HOME`; both observably
expose the TTS surface. Hygiene scan gained scope `brain-launcher`; scenario 3 scans `herdr-plugin.toml`;
brain README documents the env-file key flow; `herdr-plugin.toml` `url` action description reworded
(no action added/removed).

**Task 2.3** — ownership proof = pidfile naming a live `herdr_brain` process, or the unit's `MainPID`,
re-verified inside `signal_owned` immediately before every signal. Foreign listener → exit `98` with PID,
process name, port and two remedies; stale pidfile dropped without signalling; unit-owned → `systemctl --user
stop`; the supervisor does not restart-loop on 98; `cmd_stop`/`daemon_pid` no longer kill arbitrary listeners;
`deploy/install.sh` delegates to `bin/herdr-brain _claim-port installer`.

**Task 2.4** — `deploy/herdr-brain.service` removed (`git rm`), `deploy/herdr-brain.service.tmpl` created,
root `.gitignore` ignores `hosts/herdr/brain/deploy/*.service`, `deploy/install.sh` rewritten in English
(`--generate-only` drill; leftover `@…@` ⇒ hard failure before any write; only a marker-bearing or legacy
signature unit is replaced; upgrade = stop → regenerate → reload → restart; `GLM_API_KEY` from the installer's
environment merged into the mode-600 env file, never printed). Six placeholders, not five (`@TTS_HOME@` added —
see tasks.md 2.4).

**Task 2.5** — `hosts/herdr/tts-plugin/scripts/install.sh`: preflight refuses an unmanaged `~/.local/bin/herdr-tts`
or a non-directory `~/.local/bin` before any mutation; new stage links the launcher, warns with the exact
`export PATH=…` fix, refreshes its own link; uninstall print gained steps 5 (managed CLI link) and 6
(first-run marker `~/.config/herdr-tts/first-run.done`). README updated.

### Verification of record (final tree `425deef`)

- `cd hosts/herdr/brain && uv run pytest tests/test_resolve.py -q` → `35 passed`.
- `cd hosts/herdr/brain && uv run python -m pytest tests/test_resolve.py tests/test_launcher_repair.py tests/test_port_policy.py tests/test_systemd_template.py -q` → `94 passed`.
- `cd hosts/herdr/brain && uv run python -m pytest tests/ -q --ignore=tests/browser` → `5 failed, 1467 passed`; the 5 failures (`tests/e2e/test_m2_stream.py` ×3, `tests/e2e/test_m4_fallback.py` ×2, chromium) are identical on the base tree (`5 failed, 1408 passed` before this work). `tests/browser` (Chromium harness) fails/errors on the base too (79 failed + 5 errors on base) — environmental; `node --test tests/js/` not run (`config.py`/static untouched). Plain `pytest tests/` (no `python -m`) fails collection with `No module named 'tests'` on base too.
- `cd engine && uv run --with pytest pytest tests/test_versioned_tree_hygiene.py -q` → `13 passed`.
- `bash scripts/acceptance/clean-install.sh --milestone 2` → scenarios 1, 2, 3 `PASS`, exit 0 (scenarios 1/2 install from the remote candidate ref, so they do not exercise these local commits until `validation/at-11-instalable` is fast-forwarded).
- `bash hosts/herdr/tts-plugin/scripts/smoke-tests.sh` → `1078 passed, 2 failed` (17a, 17c — also `1052 passed, 2 failed` on a `git archive a12e727` export; environmental).
- `rg -n "linuxbrew|\.dotfiles|tail2640fd" hosts/herdr/brain/bin/herdr-brain hosts/herdr/brain/deploy` → no matches.

Work-unit commit for this evidence: the `docs(odd)` commit following `425deef` (cannot name its own hash).

### Risks left for the maintainer

- `hosts/herdr/brain/src/herdr_brain/config.py` still hard-codes `DEFAULT_TTS_HOME = "~/Code/personal/agent-tts/…"` and `llm.py` still tells users to use `~/.dotfiles/shell/private-env.sh` — both outside this unit's edit surface; the unit works around the first via `@TTS_HOME@`.
- Brain README still contains maintainer paths in unrelated prose (`cd ~/Code/personal/agent-tts/…`) — task 4.3 (docs final pass).
- Task 2.1 local re-slice (Decision 12) APPLIED on branch `feat/at-11-slices-9abc` (9a `f88b06e`, 9b `7c6a6fe`, 9c `7d21cc8`; union diff vs `backup/at-11-task-2.1-c577041` EMPTY; per-slice verification green: 17 → 30 → parity 4 + smoke 1054). `main` keeps `c577041` un-rewritten: ~106 evidence-bearing commits sit on top of it (F4, HT-03, M2, F5), so a rewrite would invalidate their recorded hashes — no rebasing of the M2 commits is needed.

## Task 2.6 — Scenario 7 `reinstall-idempotent` (slice 14, PR 19) — COMPLETE

Branch `feat/at-11-completion` (worktree `/home/bruno/Code/personal/agent-tts`); local commit `4abad10`
("test(acceptance): scenario 7 reinstall-idempotent"), 268 lines, nothing pushed. Registry row already
shipped with task 1.1 (`reinstall-idempotent`, `activates_at_milestone 2`) — no registry, harness or
hygiene-manifest change was needed (`scripts/acceptance` was already a seeded hygiene scope; the scenario
references no `id=` doc blocks).

**What the scenario verifies** (offline, no network, no stubs — `ok_stubbed` never used; 27 ok / 0 fail):
against the repo under test (`$CHECKOUT`, `git archive HEAD`):

1. `deploy/install.sh --generate-only` first run writes the marked unit (no unsubstituted placeholders,
   persisted port preference `config.env` honored, `EnvironmentFile=` points at the env file) and a re-run
   is a byte-identical no-op ("already up to date").
2. Broken-install repair: a marker-bearing corrupted unit and a deleted unit (with user state present) are
   regenerated byte-identically by a re-run.
3. Foreign-unit refusal: a user-owned unit at the destination is refused non-zero and left byte-identical.
4. Full-route user-state preservation up to the systemd boundary: the seeded env file (comments, unknown
   key, old `GLM_API_KEY`) survives a key rotation with non-key lines byte-identical and mode 600; a re-run
   with the same key is a no-op ("already holds this key"); `config.env` is never rewritten.

**Honest scope limitation (wizard is M3)**: there is no credential-capture step, keymap step or completion
marker to preserve yet, and the sandbox has no systemd user bus — the full route is EXPECTED to stop at
`systemctl --user daemon-reload` (observed rc=1, "Failed to connect to bus"), which the scenario records as
an environmental boundary after the stages under test. Keymap, completion state and live-service state
await M3/M4 and are not claimed. The scenario seeds the documented user state itself (env file, port
preference) because no wizard exists to capture it. A `.venv/bin/python` stand-in is created inside the
sandbox checkout so the real full-route preflight passes; nothing executes it (scaffolding, not a stubbed
assertion — the journal records `"stubbed": false`).

**RED-equivalent sensitivity evidence**: with the installer's env-merge mutated to drop non-key lines
(scratch checkout, never committed), the scenario reports `FAIL credentials/preferences: the merge altered
the user's non-key lines` — the preservation assertion provably detects the broken property.

### Verification of record (final tree `4abad10`)

- `bash scripts/acceptance/clean-install.sh --milestone 2` → scenarios 1, 2, 3, 7 `PASS` (7: 27 ok / 0
  fail / 0 stubbed), exit 0.
- Same command run again, immediately (idempotence of the harness itself): identical — 4 PASS, exit 0
  (scenario 7 again 27 ok / 0 fail).
- `bash scripts/acceptance/clean-install.sh --milestone 1` → scenarios 1, 2 `PASS`, scenario 7 correctly
  `NOT-YET-ACTIVATED (activates at milestone 2)`, exit 0 — no regression of earlier milestones.
- `uv run --with pytest pytest engine/tests/test_versioned_tree_hygiene.py -q` (repo root) → `13 passed`.

### Milestone-2 closure status (tasks.md "Milestone-2 closure")

The closure condition is "V1 green + `--milestone 2` (scenarios 3, 7 `PASS`; scenario 3 newly activated —
no regression on previously green scenarios 1, 2) + work-unit commit(s)". Observed on `4abad10`:
`--milestone 2` reports scenarios 3 and 7 `PASS` with scenarios 1 and 2 still `PASS` (no regression,
exit 0), and the work-unit commits exist (`4abad10` + this docs commit). The candidate-branch update clause
("if a V2 run needs the new local commits") did not trigger: scenarios 1/2 install from the remote candidate
ref and passed unchanged, while scenario 7 exercises the local checkout — no push was needed (and none was
made). V1 state: engine hygiene green as verified above; the brain and plugin suites were not re-run by
this task (no plugin/brain code changed) and ride on the task-2.5 verification of record, which carries the
two known environmental smoke failures (`17a`, `17c`) that fail identically on base. **M2's automated
closure conditions are observed and recorded; formal milestone bookkeeping remains maintainer-owned.**

## Task 3.1 — Wizard skeleton (slice 15, PR 20) — COMPLETE

Branch `feat/at-11-completion`. Files: `tools/herdr_onboarding/{__init__.py,__main__.py,cli.py,wizard.py,steps/__init__.py}`
(`cli.py`/`wizard.py`/`steps/` per the bounded-writer delegation's edit surfaces; `resolve.py` NOT extended —
its primitives are imported and reused, nothing duplicated) + `hosts/herdr/brain/tests/test_first_run.py` (create).
`smoke-tests.sh` extension moved to 3.5 with the launchers that dispatch to the wizard (recorded rescope).

**What shipped**: `python -m herdr_onboarding` CLI (`--role {plugin,brain}` required, `--no-first-run` → exit 0
with no marker, `--non-interactive`, `--json`); exit contract 0/10/20/30/40 with `10` = "completed with no marker";
completion marker `<XDG_CONFIG_HOME or ~/.config>/herdr-tts/first-run.done` (JSON: version/completed_at/role +
step preferences, mode 644) written ONLY after the health gate; TTY detection with the actionable noninteractive
hint; echo-free argv parser (unknown tokens — e.g. a fat-fingered `--glm-key <value>` — are refused with a fixed
message that never reflects the token; no secret-carrying flag exists). Reachability: `resolve_onboarding_lib()`
honours `HERDR_ONBOARDING_HOME`, then ascends ≤6 levels to the first `D` with `D/tools/herdr_onboarding/__main__.py`,
returning `D/tools` — verified against hermetic fixtures of all three supported layouts (source checkout,
curl-route full clone, subdirectory managed install), each also *invoked* via `PYTHONPATH=<lib> python -m
herdr_onboarding` with the venv interpreter, plus the honest Homebrew-keg failure naming the override.

**Health-gate seam left for 3.5**: `Wizard(..., health_gate=Callable[[RunContext], bool])` / `cli.main(...,
health_gate=...)`. Default `None` → steps complete, NO marker, exit 10 with the diagnostic "health gate not wired
yet (task 3.5)". 3.5 passes the real gate (`/health` `tts: ok` + `herdr plugin list` clean); a gate returning
`False` or raising maps to exit 30 with no marker. Every diagnostic (human and `--json`) already routes through the
single `_scrub` seam in `wizard.py`, which task 3.2 re-bodies as `herdr_onboarding.secrets.redact`.

**Recorded discrepancy**: `design.md` "Wizard CLI" exit table assigns `10` to "completed with no marker
(skip/no-first-run)" while `tasks.md` 3.1 and the delegation's verification contract require `--no-first-run` →
exit `0`. Implemented per tasks.md (exit 0); `10` is used only for the no-gate-wired completion state (and is the
natural code for 3.5's noninteractive-hint "start daemon anyway" path). Flagged for the maintainer.

### Verification of record (work-unit commit "feat(onboarding): wizard skeleton with exit contract and marker lifecycle")

- RED (observed before implementation): `uv run python -m pytest tests/test_first_run.py -q` →
  `ImportError: cannot import name 'wizard' from 'herdr_onboarding'` (collection error).
- GREEN: same command → `40 passed` (exit contract ×7, marker lifecycle ×4, TTY ×6, interactive prompt ×2,
  CLI surface ×4, JSON shape, reachability resolution ×6 + invocation ×7 across the three layouts).
- No regression: `uv run python -m pytest tests/test_first_run.py tests/test_resolve.py -q` → `75 passed`.
- Hermetic CLI smoke: `HOME=$(mktemp -d) PYTHONPATH=<root>/tools python3 -m herdr_onboarding --role plugin
  --no-first-run` → `herdr-onboarding: skipped (exit 0)`, rc=0, no `~/.config/herdr-tts/first-run.done`.

## Task 3.2 — Credential capture (slice 16, PR 21) — COMPLETE

Branch `feat/at-11-completion`, on top of 3.1 skeleton `dc072c9` (re-verified: `tests/test_first_run.py` 40 passed
inside the 87-test focused run below; its seam, exit contract and marker lifecycle untouched). Resumed after a
provider usage limit interrupted the previous writer, which had left four untracked files
(`tools/herdr_onboarding/{prompts.py,secrets.py,steps/credentials.py}` + `hosts/herdr/brain/tests/test_credentials.py`);
they were inspected and finished, not rewritten.

**What shipped**: `prompts.read_secret` — three ranked channels (`HERDR_ONBOARDING_SECRET_FD` read once and closed;
`HERDR_ONBOARDING_SECRET_FILE` mode-600 regular file read once with optional `HERDR_ONBOARDING_SECRET_UNLINK`;
`getpass.getpass()` interactive only), no argv flag, an ambient `GLM_API_KEY` deliberately NOT a channel, value-free
actionable `SecretIntakeError` messages. `secrets.merge_env_file` — `mkstemp` in the target directory → fsync →
`chmod 600` → `os.replace`, dir forced 700, unknown lines/comments byte-identical, tmp removed on any failure.
`secrets.redact` is the single diagnostic boundary: `wizard._scrub` now delegates to it, so every human line, `--json`
summary and exception text (step failure, crashing gate) passes it; the captured value is registered on the context
before any later failure can occur. `CredentialsStep` registered for the `brain` role only — `steps_for_role("plugin")`
has no credentials step, so a plugin-only run completes keyless. **Closing fixes in this resume**: (1) the step now
consults FD/FILE first so a supplied value rotates the key while an existing key with no new value is kept untouched
and never re-asked (the two inherited tests encoded exactly this and contradicted the earlier ordering);
(2) `cli.py`'s docstring contained the literal `--glm-key` token that the static hygiene test (correctly) flags — reworded
to a neutral `<flag> <value>`; (3) `steps_for_role` registers the step.

**Rescope, recorded**: the `set -x` static assertion lives in `test_credentials.py` (active shell lines of the
brain/plugin/acceptance entry points + package sources) because `engine/tests/test_versioned_tree_hygiene.py` was
outside the delegation's edit surfaces; the engine hygiene suite was NOT extended or re-run here.

### Verification of record

- RED (observed by this resume, before any production edit; the earlier writer's RED was never captured and is not
  claimed): `uv run python -m pytest tests/test_credentials.py -q` (hosts/herdr/brain) → `11 failed, 30 passed`
  (credentials step unregistered, `_scrub` identity, `--glm-key` literal in `cli.py`).
- GREEN after registry + `_scrub` delegation: 1 residual failure (rotation vs existing key) — fixed by the
  channel-first ordering. Triangulation tests added (real `getpass` seam persisted/never echoed, empty answer aborts
  with no partial state, interactive re-run with an existing key never prompts, secret inside a gate exception is
  redacted to `***`, plugin role never consumes the FD, no shell script enables xtrace).
- `uv run python -m pytest tests/test_first_run.py tests/test_credentials.py -q` → `87 passed, 1 warning`
  (the warning is stdlib `GetPassWarning` from the pre-existing fallback-tty test).
- Threat matrix: (a) live-process `argv` + `/proc/<pid>/environ` + `ps -eo args` snapshot during a blocked FD read
  carries no secret; (b) failed subprocess run (destination blocked) exit 40 with secret-free stdout/stderr, plus
  in-process gate-failure `--json` runs; (c) `os.replace` crash seam leaves the target byte-identical and no `*.tmp`.
- Hermetic CLI (HOME=tmp, `env -u XDG_CONFIG_HOME`): `--role brain --no-first-run` → exit 0, no files;
  `--role brain --non-interactive` with no channel → exit 20 naming FD/FILE/`--no-first-run`, no files.
  **Disclosure**: a first CLI probe ran with the ambient `XDG_CONFIG_HOME=/home/bruno/.config` still set and so
  consulted the real brain env path for key *existence* only (exit 10, nothing printed or written — the existing-key
  branch returns before any write; file contents were never read into output). Re-run hermetically as above.
- Full: `uv run python -m pytest tests/ -q --ignore=tests/browser` → `5 failed, 1593 passed`; the 5 are exactly the known
  chromium e2e base failures (`test_m2_stream` ×3, `test_m4_fallback` ×2).

### Seam for task 3.5 (health gate) — still PENDING

`Wizard(..., health_gate=Callable[[RunContext], bool])` / `cli.main(..., health_gate=...)` is unchanged. With no gate the
CLI still exits `10` / no marker (the brain `--non-interactive` subprocess tests assert it). 3.5 must pass the real gate
(`/health` `tts: ok` + `herdr plugin list` without manifest warnings), wire both launchers' dispatch, and may then
tighten the exit-10 subprocess assertions. Any gate exception text already passes the redaction boundary.

## Task 3.3 — Voice + keymap steps (slice 17, PR 22) — COMPLETE (plugin-suite column not exercised)

Branch `feat/at-11-completion`, on top of 3.2 `4512eb8`. Files: `tools/herdr_onboarding/steps/{voice.py,keymap.py,__init__.py}`,
`tools/herdr_onboarding/{wizard.py,cli.py,prompts.py}`, tests `hosts/herdr/brain/tests/{test_onboarding_voice.py,
test_onboarding_keymap.py,test_first_run.py}`. No plugin, brain-runtime, engine or contract file was touched.

**What shipped**
- Answers: flag → `HERDR_ONBOARDING_*` env → one interactive question (`ctx.prompt_optional`: blank = documented default,
  EOF = leave things as they are, never aborts; under `--json` the question goes to stderr). New echo-free CLI flags
  `--voice-provider {edge,openai,elevenlabs,piper}`, `--voice NAME`, `--keymap-style {menu,direct,ctrlalt,none}`,
  `--replace-keymap`, `--stt` (3.4); an invalid value is refused with a fixed message that never echoes it.
- Voice: provider (and optional default voice) persisted to `<config>/herdr-tts/config.env` (`HERDR_TTS_CONFIG_FILE`
  honoured like the launcher). The launcher's `config_set` is not on its CLI, so `voice.upsert_config_value` mirrors its
  managed-block upsert (first occurrence replaced in place, duplicates dropped, unknown lines byte-identical, atomic
  `mkstemp`+`os.replace`, one `.bak`, quoted values, new file mode 600, existing mode kept, idempotent no-op rewrite).
  Existing provider + no explicit answer → preserved, never re-asked.
- Keymap: reuses the installer's mechanism through the launcher — `bash <tts-plugin>/bin/herdr-tts keymap adopt --style S`
  → `keymap apply` → `herdr server reload-config` (automatic reload). Existing keymap is never touched without consent
  (flag/env/interactive yes → the launcher's own `--force`); `none` runs nothing; no explicit style + existing keymap →
  preserved and the user is told. Failures are installer-parity warnings naming `herdr-tts keymap init`, recorded as
  `keymap: "failed"` (exit stays 0, nothing half-written: the launcher writes atomically and rolls back).
- Registry order: credentials → voice → keymap. Marker preferences: `voice`, `voice_name`, `keymap`, `keymap_reloaded`.
- Non-interactive + no explicit answer changes NOTHING (voice file absent, no keymap command run) — also what keeps the
  existing 3.1/3.2 default-registry runs byte-for-byte unchanged.

### Verification of record

- Process disclosure: `voice.py` and `keymap.py` were drafted before their tests, so RED was reproduced by moving the
  module aside (to `/tmp/opencode`, restored immediately): `uv run python -m pytest tests/test_onboarding_voice.py -q`
  → collection error `ImportError: cannot import name 'voice' from 'herdr_onboarding.steps'`; same for keymap →
  `ModuleNotFoundError: No module named 'herdr_onboarding.steps.keymap'`. This is reproduced RED, not first-draft RED.
- GREEN: voice `21 passed`; keymap `27 passed` (fake-runner command-sequence tests + 2 REAL-launcher sandbox drills + registry); `test_first_run.py` gains `TestPreferenceFlags` (flag parsing, echo-free refusal).
- Triangulation encoded: flag > env, duplicate collapse, managed-block insert, idempotent re-run (marker deleted between
  runs — the documented "run again" action; a first draft of these tests silently hit the `already-completed` early exit
  and was corrected), crash mid-write intact, quote/newline refusal, adopt-failure / reload-failure / missing executable
  degradation, consent via flag/env/interactive, interactive no/blank/EOF keep the file, `none` with consent keeps the file.
- `env -u XDG_CONFIG_HOME uv run python -m pytest tests/test_onboarding_voice.py tests/test_onboarding_keymap.py
  tests/test_first_run.py tests/test_credentials.py -q` (hosts/herdr/brain) → `141 passed, 1 warning`.
- `uv run --with pytest pytest engine/tests/test_versioned_tree_hygiene.py -q` (root) → `13 passed`.
- Sandbox drill honesty: the launcher ran for real; `herdr` is a stub that appends its argv to a log (it saw the
  launcher's `config check` then the wizard's `server reload-config`); the venv interpreter is an empty executable that only
  satisfies the launcher's bootstrap guard. No real herdr server, audio device, network or user config was touched.
- NOT run by this unit: `smoke-tests.sh` ("plugin suite green" column) — no plugin file changed and the launcher dispatch
  that reaches the wizard is 3.5's.
