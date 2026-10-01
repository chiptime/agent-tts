# AT-11 apply progress — cumulative through task 1.7 (M1, persistent V1 evidence closed)

Branch `feat/at-11-instalable` in worktree `/home/bruno/Code/personal/agent-tts-worktrees/at-11-instalable`.
This file is the OpenSpec-side apply-progress artifact (native locator discovered by `gentle-ai sdd-status`);
the Engram mirror at topic `sdd/at-11-instalable/apply-progress` (project `agent-tts`) carries the same
cumulative content plus exact commit hashes (this file ships inside its own work-unit commit and cannot
contain that hash).

Hash-only branch rewrite verified earlier: refreshed mapping supersedes pre-rewrite IDs
(a022c44→d89abc7, 3712ced→aae51ea, b30cd0a→0682845, b037b6f→401a4b8). Cumulative state: **7/26 tasks complete**.

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

- Mode: **Standard** (workspace `strict_tdd: false`; tasks 1.6 and 1.7 are not plugin-strict-TDD tasks — no
  `bin/`-side change, no smoke scenario).
- Delivery: auto-chain, **feature-branch-chain**; task 1.7 is slice 7 / PR 7, targeting the immediately
  preceding slice's branch context; apply creates work-unit commits only — no push, no PR, no remote git.

## Full plugin suite status (unchanged by this task)

`bash scripts/smoke-tests.sh` currently reports `1001 passed, 1 failed (40e)` — the known baseline failure
fixed by task 1.11 (16n determinism by 1.10). The full plugin suite is NOT claimed green by task 1.7 and is
not a 1.7 gate; M1 closure remains strict after tasks 1.10/1.11.

## Next

Task 1.9 (scenario 3 activation correction 1→2, slice 3a, PR 8 — depends only on 1.3, may precede 1.5 per
task order), then 1.10/1.11 as dependencies allow, then 1.8; M1 closure needs the full plugin suite green
after 1.10+1.11 (no baseline exception).
