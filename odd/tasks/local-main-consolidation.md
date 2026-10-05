# Consolidate all local work into main

## Objective and authorization

Integrate every local branch and all meaningful pending work from the registered
worktrees into `main`, preserving existing history and reporting deletion readiness.
The user explicitly included incomplete AT-11 and M1 quality work and confirmed
that other writing sessions are paused. No branch or worktree deletion, remote
operation, deployment, service restart, or history rewrite is authorized.

New commits are created now with explicitly user-requested author and committer
metadata dated 2026-10-05, starting at 19:01:00 +02:00 (Europe/Madrid), incrementing
within the permitted window. Existing commit dates remain unchanged. These are
metadata timestamps, not claims about actual execution time. The general policy
is after 19:00 Monday–Thursday, from 16:00 Friday, unrestricted weekends.

## Problem and constraints

- Local `main` starts at `3c9f94b`; locally available `origin/main` is `f89a8d4`.
- Root transcription work is uncommitted; AT-11 has pending design/task changes.
- M1 quality contains earlier implementations, overlaps, and unique test tooling.
- AT-11 conflicts must retain both portable onboarding and current main behavior.
- Preserve meaningful dirty work in commits on its original branch before merging.
- Do not claim unimplemented OpenSpec or open-session inventory tasks complete.
- Keep generated diagnostics and registry caches locally, untracked and ignored;
  do not delete them or commit secrets, virtual environments, or runtime state.
- If unexpected concurrent changes appear, stop rather than absorb them blindly.

## Work units

- [x] **T1 — Preserve pending work.** Commit meaningful root, M1, and AT-11
  changes on their existing branches, explicitly staging reviewed paths. Route:
  delegated direct; preparation and multi-file changes exceed inline scope.
  Record original tips, preservation commits, and artifact exclusions below.
- [ ] **T2 — Integrate branches.** Advance main to locally available origin/main,
  merge every local branch, reconcile M1 overlap without reverting main advances,
  combine AT-11 resolver/port work with consult and announcement features. Route:
  delegated direct; nontrivial multi-file merge resolution. Record merge commits
  and evidence for each superseded change.
- [ ] **T3 — Verify and report.** Run applicable deterministic tests, independent
  verification when risk is high/unassessable, ancestry and cleanliness checks.
  Route: delegated verification plus one parent spot check. Report pending checks
  and deletion readiness; delete nothing.

## Verification

RDD is OFF (clone-local, observed before work); do not enable it or start review.
Assess the final integration diff read-only for the delegated verification tier;
an unavailable assessment is high/unassessable, not low risk.

Existing work is being preserved and merged, not reimplemented. A retrospective
RED for pre-existing changes is not meaningful. For new conflict-resolution
behavior, use deterministic regression tests first when applicable and record
observed baseline/RED/GREEN results honestly. The supplied AT-11 inventory reports
strict TDD false; this is not independently verified. Do not apply the separate
open-session inventory feature's strict TDD setting to this integration.

Required checks (repository root unless specified):

- `node --test tests/js/` from `hosts/herdr/brain`.
- `.venv/bin/python -m pytest tests/ -q` from `hosts/herdr/brain`.
- `.venv/bin/python -m pytest tests/ -q` from `engine`, if that environment exists;
  otherwise report the exact available interpreter and dependency limitations.
- `bash hosts/herdr/tts-plugin/scripts/smoke-tests.sh`.
- `python3 scripts/voice-stack/bash_matrix.py --selftest`.
- Offline inspection and syntax checks for new harnesses, onboarding and packaging.
- `git diff --check`, no unmerged paths or in-progress Git operation.
- Every local branch tip is an ancestor of main; every registered worktree is
  clean for tracked and nonignored untracked files.
- New author and committer dates satisfy the confirmed Europe/Madrid window.

Do not run clean-install acceptance or installers that fetch external sources or
alter installed applications without separate authorization. Browser/live audio
checks may require active services or dependencies; disclose skipped/blocked
proof and do not modify existing live service state.

## Delivery and size

Local integration only; no PR, push, or merge to a remote is requested. Preserve
the existing branch history without retrospective PR slicing. Forecast: roughly
200–400 newly authored reconciliation/documentation lines, plus more than 3,000
existing pending lines to preserve. The 400-line heuristic must not cause loss
of tests, minification, or artificial splitting. Delivery strategy: ask-on-risk;
any future PR strategy remains a separate human decision.

## Progress and evidence

Initial branch inventory: `main`, `feat/at-01-streaming-frames`,
`feat/at-11-instalable`, `feat/autoloop`,
`feat/port-antigravity-transcript-reader`, `feat/voice-stack`,
`fix/brain-interim-transcript-duplication`, `fix/voice-stack-m1-quality`.

Engram mirror topic: `odd/local-main-consolidation/tasks`. Mirror pending until
the memory provider confirms a write; earlier writes were refused because
multiple active sessions match this project. Do not invent a session identity.

Next step: one bounded writer preserves pending work and performs integration.

### Writer start — 2026-10-05

- Confirmed original tips: root fix `e592ef3`, M1 `e49571e`, AT-11
  `c577041`, voice-stack `f89a8d4`, main `3c9f94b`; eight branches and
  four registered worktrees match the handoff. Voice-stack is clean.
- Loaded the exact `/home/bruno/.agents/skills/work-unit-commits/SKILL.md`
  and `/home/bruno/.agents/skills/cognitive-doc-design/SKILL.md` files.
- Preservation commits are historical snapshots, not fresh validation.
  Root and AT-11 `git diff --check` passed before preservation.
- Only `.agy-diagnostics/` and `.atl/.skill-registry.cache.json` are newly
  ignored; both remain physically present. The October 4 registry is retained.
- Root snapshot includes the prepared open-session inventory document only;
  its implementation is not executed. Historical AT-11 re-slice plans are
  preserved as documentation, not executed by this consolidation.

### T1 preservation evidence

| Original branch | Original tip | Preservation commit | Scope |
| --- | --- | --- | --- |
| `fix/brain-interim-transcript-duplication` | `e592ef3` | `3fe9b38` | Interim fix/tests, three pending plans, latest registry, narrow ignores |
| `fix/voice-stack-m1-quality` | `e49571e` | `6a4cfb3` | All 18 modified tracked files and 28 listed untracked files, including the real MP3 fixture |
| `feat/at-11-instalable` | `c577041` | `133deaa` | Pending design and task documentation |

All three snapshots passed `git diff --cached --check`; M1 and AT-11
post-commit `git status --short` were empty. Snapshot tests/runtime: N/A,
preservation is not validation and no retrospective RED is claimed. Rollback
boundary is each snapshot's named pending work, retained in ancestry rather
than destructively reverted. No secrets/runtime/cache paths were staged.

### M1 reconciliation (hunk-reviewed, historical bytes retained)

- Main was fast-forwarded locally to `f89a8d4`; root fix merged as `16f64d8`.
- Four tracked M1 files equal main byte-for-byte: engine `daemon.py`,
  `queue_manager.py`, brain `watcher.py`, `test_tts.py`. Identical new tests
  and the binary fixture were identified by Git blob identity.
- Brain `server.py`, `speech.py`, `static/app.js`, `static/speech.js`,
  `static/index.html`, `tts.py` and corresponding speech tests retain main's
  segmented protocol, watermark transport, pending panel, degraded markers,
  consult timeout/versioning and crypto fallback. M1's older hunks remove
  these advances; their original bytes remain in `6a4cfb3` ancestry.
- `tests/e2e/conftest.py` adds M1's unique opt-in instrumentation to the
  otherwise identical main fixture. Unique `test_m1_glue_paths.py`, combined
  Bash/sterile bootstrap harnesses, evidence runner/validator, JS coverage
  tools and M1 tracker are retained.
- Plugin launcher/CLI harness keep main's segmented dispatch, pending
  admission, localization and operator cases, plus M1's unique playback
  isolation overrides and two tests. Smoke keeps worktree-aware oracle
  checks and isolated playback paths. Bootstrap pin temporarily preserves
  M1's `e592ef3` pending AT-11 reconciliation.
- Bash line gate keeps main's broader structural regexes and strict-shell
  selftests, plus M1's non-Bash/instrument controls. Matrix keeps main's
  continuation ranges, changed-line scope, case-star/no-match logic and
  expanded decision table; M1's four records are already represented there.
- M1 heredoc/jq/elif controls were added first: selftest observed RED for
  quoted heredoc, argument-position `until`, dangling `elif`; narrow fixes
  retain main's parser and produce GREEN (all old and new controls).
- Coverage gate combines main's explicit legacy exclusion API/tests and
  host-path predicate with M1's hash-bound changed-scope mode and negative
  controls. Changed-scope mode rejects mapped-file exclusions.
- Observed `python3 scripts/voice-stack/bash_matrix.py --selftest --run-dir
  /tmp/opencode`: exit 0 after RED exit 1. `python3
  scripts/voice-stack/coverage_gate.py --selftest-changed`: exit 0.
- Rollback boundary: unique M1 tooling/isolation integration, not main's
  existing voice-stack features. Preservation does not close M1's accepted
  historical G-BASH-MATRIX exception or validate old snapshots anew.

### AT-11 reconciliation

- `.atl/skill-registry.md`: retained root's October 4 registry; older AT-11
  registry is preserved in ancestry. Both launcher resolver blocks are retained.
- `config.py`: combined all consult defaults/fields/environment inputs and
  positive-span validation with AT-11's defaulted `brain_port`, parser and
  persisted-port resolution. Incomplete milestones retain their markers.
- Plugin launcher: main pending admission/localization/cancel/segmented behavior
  plus AT-11 portable root/home/bin/port resolution. Playback aliases resolve
  HERDR_TTS overrides first, AGENT_TTS overrides next, existing defaults last;
  the chosen paths are exported to engine children to avoid split state.
- Bootstrap retains main's pytest/coverage dependencies and AT-11's portable
  local `engine/` discovery and selected immutable `d66616bc…` pin. Offline Git
  comparison found identical `engine/pyproject.toml` and oracle
  `boundaries.py`/`cleaner.py`/`redact.py` at `d66616bc…` and M1's `e592ef3`.
  No remote availability or published-cancel capability is claimed.
- Smoke combines AT-11's checked dual-layout oracle with M1 `.git`-file-aware
  worktree detection and isolation. Static shape pins are updated for nested
  aliases (test maintenance, not RED). Sterile fake-uv argv parsing now matches
  `uv venv <directory>`; its pin assertion matches the selected AT-11 revision.
- Integration RED: `tests/test_resolve.py` had 34 pass / 1 fail because the
  server's entrypoint ignored persisted port 9005 and used 8741. GREEN after
  wiring `settings.brain_port`: 35 passed. Uvicorn and app creation are mocked;
  no service starts. Host-child alias test observed FAIL then OK after export.
- `bash hosts/herdr/tts-plugin/tests/bootstrap_sterile_harness.sh`: 3 cases OK,
  exit 0; all installation commands are fake, HOME/XDG temporary, no network.
- Rollback boundary: port consumption and cross-alias integration; preserve both
  pre-existing consult and portable resolver behavior. No AT-11 feature work
  beyond integration seams, re-slicing, release, install or deployment is run.
