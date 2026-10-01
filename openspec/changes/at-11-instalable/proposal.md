# Proposal: AT-11 — Independent Installation and First-Run Onboarding

## Intent

Make the agent-tts monorepo installable by a new user without the maintainer's directory layout, dotfiles, secrets, or manual configuration edits. Implement the approved [PRD](../../../docs/prds/AT-11-instalable-first-run.md) at `1b404ad`, using the [installation audit](../../../docs/prds/AT-11-auditoria-instalacion.md) as defect evidence, not as a substitute for current source inspection.

The maintainer has now explicitly authorized development. The PRD's historical hold does not block this change. Its four resolved decisions remain binding: plugin `[[startup]]` is primary; generated systemd deployment is supported; one shared wizard serves all entry points; Tailscale belongs in advanced documentation; naming remains `agent-tts` with `herdr.*` plugin IDs.

## Scope

### In Scope

- **Installation hygiene:** repair monorepo source URLs and subdirectory handling, repair/extend the already-tracked brain manifest, correct bootstrap development installs to `engine/`, and remove active legacy installation guidance. Document GitHub-subdirectory and local-repository installation; do not require a root plugin manifest.
  - **Authorized M1 extension (Engram #9711/#9713/#9715):** fix both baseline V1 failures, smoke 16n and 40e, without a baseline exception. This revision authorizes bounded scope only; specs/design/tasks must carry it forward before implementation. The already-tracked brain manifest remains repair/extend work, not a new delivery (resolved OQ-4).
  - **16n — hermeticity and host safety:** make the playback lock, PID file, and IPC socket environment-overridable while retaining the identical defaults `/tmp/herdr-tts-playing.lock`, `/tmp/herdr-tts-current.pid`, and `/tmp/herdr-tts-player.sock`. Set all three to sandbox-local paths in smoke-suite `new_env()` and isolate the engine socket through `AGENT_TTS_SOCKET`. Preserve production toggle behavior and the existing read confirmation assertion; prove `r` follows the transcribe/read branch without touching or stopping a real host daemon. This corrects a host-state leak and genuine safety hazard, not an incorrect assertion.
  - **40e — immutable oracle identity:** replace stale pre-monorepo `AGENT_TTS_REF=32e9bafb` with the maintainer-selected `d66616b` (full SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be`). Resolve oracle files from either monorepo `engine/src/agent_tts/<file>` or legacy `src/agent_tts/<file>` using return-code-checked tree lookup. Preserve strict byte identity for `boundaries.py`, `cleaner.py`, and `redact.py`; never weaken/delete assertions or change engine bytes to fit a stale oracle. V2 must prove retrieval of this exact pin from the documented GitHub origin; unavailable means `BLOCKED`, never another revision.
- **Machine independence:** derive paths from installed scripts, discover Herdr, make the port configurable, remove personal tailnet/dotfiles assumptions, expose `herdr-tts` in `~/.local/bin`, and generate optional systemd units from a versioned template. Preserve existing configuration and avoid disrupting unrelated processes.
- **Shared first-run onboarding:** dependency-free text interaction and noninteractive configuration, `--no-first-run`, configuration markers, secure GLM/provider credential capture, voice/keymap preferences, keymap adoption/application and reload, explicit tiny/base/small STT download consent, and final health/contract checks.
- **Diagnosis and documentation:** actionable doctor checks for PATH, audio, credentials, daemon, speech surface contract v1, and STT; one documented primary installation flow plus optional systemd and advanced remote-exposure guidance. Document local plugin-registration repair without mutating the maintainer's live configuration.
- **Verification:** establish the V2 harness first, add V1 compatibility tests alongside each correction, and trace the approved PRD's nine Gherkin scenarios and RF verification matrix into specs and implementation evidence.

### Out of Scope

- Engine redesign, changes to frozen `contracts/ipc-v2` or `contracts/tts-brain-v1`, and renaming packages or plugin IDs.
- Central plugin registry publication, native Windows support, or turning npm/Homebrew stubs into a new supported distribution product. Existing wrappers receive only necessary monorepo/legacy-reference corrections and honest support documentation.
- Tailscale automation, maintainer dotfiles migration, live plugin-registration changes, or any writes outside this worktree during autonomous work.
- Repository-wide linting, coverage, type checking, formatting infrastructure, or a mandatory strict-TDD conversion.
- Autonomous V3 execution/sign-off, release publication, push, remote operations, or PR creation. Auto-chain describes future review slicing, not permission to perform remote actions.

## Capabilities

### New Capabilities

The workspace `openspec/specs/` contains no existing capabilities. Create full workspace specs for:

- `independent-installation`: cross-project GitHub/local installation, brain manifest/startup, portable path and binary discovery, secure shared configuration locations, generated systemd option, reinstall preservation, and documented supported routes (RF-1/2/6/7/8/9/11).
- `first-run-onboarding`: shared wizard, interactive/noninteractive execution, skip/state behavior, credential protection, voice/keymap setup, explicit STT consent/refusal, and completion health (RF-3/4/5/12).
- `installation-diagnostics`: doctor behavior and actionable failures, reproducible V1/V2 installation evidence, and the separate human V3 checklist (RF-10 and cross-cutting verification/RNF requirements).

### Modified Capabilities

These names belong to the **plugin subproject**, not to the empty workspace registry:

- `installer`: extend fresh-install and reinstall requirements for the monorepo layout, automatic CLI exposure, and shared onboarding integration; retain preflight-before-mutation, remote mismatch refusal, existing keymap preservation/opt-out, reload, uninstall guidance, and pinned-source safeguards.
- `plugin-bootstrap`: replace the hardcoded development checkout requirement with location-derived `engine/` installation, adapt immutable sources to the monorepo package subdirectory, and preserve explicit dev opt-in, uv/Python fallback, upgrade behavior, and checkout independence.

**M1 extension routing:** extend `installation-diagnostics` with smoke 16n sandbox/non-interference proofs, smoke 40e checked dual-layout lookup and strict three-file identity, and no-baseline-exception V1 closure. Extend `plugin-bootstrap` with the selected full immutable SHA and `independent-installation` with documented-GitHub-origin retrieval evidence and `BLOCKED` on unavailability. Keep the same three new capabilities and two plugin deltas; existing reader parity requirements are preserved, not relaxed.

### Registry Ownership and Spec Routing

AT-11 is coordinated exclusively by root `openspec/changes/at-11-instalable/`. The spec phase writes the three new full specs and the two named delta specs under that change's `specs/<name>/spec.md`. Each delta must explicitly identify its existing canonical target:

| Change spec | Archive target |
|---|---|
| `independent-installation`, `first-run-onboarding`, `installation-diagnostics` | Root `openspec/specs/<name>/spec.md` |
| `installer` | `hosts/herdr/tts-plugin/openspec/specs/installer/spec.md` |
| `plugin-bootstrap` | `hosts/herdr/tts-plugin/openspec/specs/plugin-bootstrap/spec.md` |

Do not silently archive plugin deltas into new root copies. Preserve both subproject registries, histories, skill indexes, and local testing policy. The root configuration governs this workspace change (`strict_tdd: false`); the plugin's existing strict-TDD setting remains local and is not rewritten. Correct only the stale statement in `hosts/herdr/tts-plugin/openspec/config.yaml` that the engine is a separate external repository, and clarify root configuration's registry-scope wording to permit this bounded metadata correction and explicit delta routing. This is not a registry merger or ownership transfer.

## Approach

Use existing installer/bootstrap and launcher entry points rather than adding a parallel installation framework. Wire one shared wizard into both plugin and brain installation/first launch; its precise module location and packaging reachability belong to design. Both deployment modes consume the same persisted configuration. Secret input uses `getpass` interactively and a non-argv mechanism for unattended runs; no key may appear in logs or output. Reinstallation must preserve keys and preferences, and failed onboarding must remain safely retryable.

Keep the primary plugin startup path nonblocking in unattended contexts. STT download remains opt-in; refusal must preserve the approved degraded user journey without changing the frozen contract. Optional systemd rendering substitutes discovered installation values into a committed template, never committing a machine-specific generated unit. Supported targets remain Linux, WSL2, and macOS; systemd is an optional platform-specific route, not a prerequisite.

### Binding Milestones

| Milestone | Deliverable | Closure |
|---|---|---|
| 1. Hygiene | **Task 1: build `scripts/acceptance/clean-install.sh`**, then audit A/B bug and active legacy-reference corrections, including bounded OpenSpec context correction, smoke 16n host-safe isolation, and smoke 40e checked oracle lookup with immutable pin `d66616b` | V1 + V2 green and a verified work-unit commit; no baseline exception |
| 2. Decoupling | Generic path/binary/port/config resolution, CLI exposure, generated service option, and compatibility proofs | V1 + V2 green and a verified work-unit commit |
| 3. First-run | Shared wizard, credentials/preferences, noninteractive handling, STT consent/setup, keymap integration | V1 + V2 green and a verified work-unit commit |
| 4. Doctor + docs | Actionable diagnosis, complete installation/advanced docs, final acceptance traceability | V1 + V2 green and a verified work-unit commit |

Plan cohesive auto-chain review slices at no more than 400 authored additions plus deletions per PR, keeping tests and user-facing docs with each work unit. Milestones may require multiple slices; do not equate one milestone with one PR or shrink tests to meet the budget. This proposal creates no commits or PRs.

The selected chain strategy is **feature-branch-chain**. PR creation remains deferred to the maintainer after V3; this update performs no push, remote operation, or implementation. The next planning phases allocate the two M1 fixes into bounded work units without adding or renumbering milestones.

### Verification Contract

Reference, rather than reproduce, all **nine Gherkin scenarios** in the approved PRD's “Escenarios de aceptación” section and its RF-to-verification matrix. The spec phase must preserve their traceability and distinguish automated evidence from the ninth, timed human UAT scenario; final health also retains its V3 obligation.

V1 runs for each installation-surface change, including installation docs, and proves compatibility for each audit correction. Preserve the existing project commands:

| Working directory | Command |
|---|---|
| `engine/` | `python -m pytest tests/ -q` |
| `hosts/herdr/brain/` | `python -m pytest tests/ -q && node --test tests/js/` |
| `hosts/herdr/tts-plugin/` | `bash scripts/smoke-tests.sh` |

Run all three project suites for integrated milestone closure, plus the installation-specific V1 checks and `bash scripts/acceptance/clean-install.sh` from the workspace root. No coverage, lint, or type-check pass is implied.

V2 must exercise the literal documented installation steps from an arbitrary checkout path with empty temporary HOME, isolated configuration/state, minimal PATH, no dotfiles, brew, Tailscale, or preexisting secrets. It must enforce the PRD's documented-origin network boundary and distinguish stubbed V1 contracts from real installation evidence. Unavailable host/network/audio prerequisites are blockers, not successful skips. V2 network access is already authorized for documented PyPI/uv, GitHub, and M3 model origins (Engram #9681); unlisted origins remain `BLOCKED`. No-remote means no push/PR/remote git, not that documented V2 checks lack permission.

**Resolved milestone gate (Engram #9636):** the maintainer already resolved the V2 active-scenario gate: activation follows delivered functionality, all active scenarios must be green, and previously green scenarios cannot regress. Later scenarios are `NOT-YET-ACTIVATED`, never green skips; an active `FAIL` or `BLOCKED` prevents closure. M4 requires the full suite green. Building the harness first remains mandatory regardless.

**Binding resolution record — retained, not reopened:** the historical gate/authorization/clarification statements above and in Risks/Dependencies are read with the following already-approved resolutions:

- **V2 active-scenario gate (Engram #9636):** each milestone requires all active scenarios green, activation follows delivered functionality, and previously green scenarios must not regress. Report later scenarios as `NOT-YET-ACTIVATED`, never successful skips. An active `FAIL` or `BLOCKED` prevents closure; M4 requires the full suite green. Harness-first and all nine PRD scenarios remain binding.
- **OQ-2/OQ-3/OQ-5 (Engram #9681):** explicit `HERDR_TTS_HOME` wins over own-location discovery; STT refusal reports `unavailable`, with `degraded` reserved for `tts` and RF-12 treated as vocabulary errata, not a frozen-contract change. Prior V2 authorization is limited to documented PyPI/uv, GitHub, and M3 STT-model origins; outside-allowlist access is `BLOCKED`. This proposal-only invocation performs no network check and does not expand that authorization.
- **OQ-4:** the base correction is already completed as recorded in tasks; the brain launcher/manifest are repaired/extended, not recreated. OQ-1 remains an evidence question for the existing subdirectory-install probe, not a newly reopened product decision.

**Additional M1 verification obligations:**

| Proof | Required evidence |
|---|---|
| 16n isolation and compatibility | All three playback paths and the engine socket resolve within each `new_env()` sandbox; unset overrides retain the exact old defaults. `r` reaches transcribe/read and preserves its confirmation assertion; no real host daemon is contacted/signalled and no host playback state is changed. Use isolated fixtures/recorders, never a live host daemon as the test fixture. |
| 40e fail-closed identity | Tree lookup checks return codes for both supported layouts at the same immutable revision; missing/unreadable files fail explicitly rather than comparing empty output. Exact bytes of all three files must match; retain the full existing assertion matrix. Layout compatibility is not a revision fallback. |
| Public pin retrieval | The V2 documented-origin installation retrieves full SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be` from `https://github.com/chiptime/agent-tts.git` and installs `engine/`. Local object availability and stubbed V1 success are not public-retrievability evidence. Missing revision, access, or required authorization yields `BLOCKED` with a recorded reason; no substitute SHA, moving branch, cache-only pass, or silent fallback. |
| M1 closure | Both baseline failures are fixed and all three V1 project suites pass, alongside the binding active-scenario V2 gate. Preserve failing/blocked evidence until corrected; do not waive, skip, or relabel the baseline failures as green. |

V3 is a manual, timed clean-machine UAT with recorded friction and human sign-off before merge; it is outside the autonomous loop. Retain the PRD's under-ten-minute target, while flagging its differing host-preinstalled versus host-included timing language for the human checklist.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `scripts/acceptance/clean-install.sh` | New | First task; reproducible installation gate |
| `hosts/herdr/tts-plugin/scripts/{install,bootstrap}.sh`, `bin/herdr-tts`, `herdr-plugin.toml` | Modified | Monorepo installation, CLI exposure, wizard entry points, build/startup integration |
| `hosts/herdr/brain/herdr-plugin.toml` | Modified | Tracked brain plugin manifest repair/extension and primary startup route |
| `hosts/herdr/brain/bin/herdr-brain`, `deploy/install.sh`, `src/herdr_brain/` | Modified | Portable launcher/config, secure onboarding integration and diagnostics |
| `hosts/herdr/brain/deploy/herdr-brain.service` / `herdr-brain.service.tmpl` | Replaced / New | Static machine-bound unit replaced by generated option |
| Shared wizard module, location selected in design | New | Single implementation reachable from both installed entry points |
| `hosts/herdr/tts-plugin/packaging/{npm,homebrew}/` | Modified, bounded | Correct active legacy references/layout assumptions; no publication expansion |
| `engine/tests/`, `hosts/herdr/brain/tests/`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | Modified as needed | Compatibility and installation proofs, preserving frozen contracts |
| `README.md`, `hosts/herdr/{brain,tts-plugin}/README.md`, active installation docs | Modified | Canonical installation, recovery, supported deployment paths and V3 checklist |
| Root `openspec/` and plugin `openspec/config.yaml`, `specs/{installer,plugin-bootstrap}/` | New / Modified | Workspace artifacts and explicit bounded subproject updates |
| `hosts/herdr/tts-plugin/bin/herdr-tts` | Modified, M1 extension | Environment overrides for playback lock/PID/socket; identical production defaults and toggle behavior |
| `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | Modified, M1 extension | `new_env()` isolates playback paths and engine socket; 16n non-interference proof; 40e return-code-checked dual-layout lookup with strict three-file identity |
| `hosts/herdr/tts-plugin/scripts/bootstrap.sh` | Modified, M1 extension | Full immutable `d66616b` engine pin, retaining monorepo `engine/` source handling |
| `scripts/acceptance/scenarios/{01-plugin-fresh-clone,02-plugin-subdir-install}.sh` | New / Modified as planned | V2 documented-origin installation evidence for selected-pin retrieval; explicit `BLOCKED` on unavailability |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Milestone V2 closure conflicts with later feature delivery | High | Apply the resolved active-scenario gate (Engram #9636); retain a truthful full-suite result and harness-first ordering |
| Approved PRD STT wording conflicts (`unavailable` vs `ready\|degraded`) | High | Apply the maintainer's resolved runtime vocabulary: `unavailable` when no model is present; treat RF-12 as vocabulary errata and leave frozen contracts unchanged |
| RF-6 allows known brew prefixes, but the zero-path scenario forbids `/home/linuxbrew` | Medium | Design an environment-derived discovery strategy; escalate any remaining literal-prefix need instead of weakening acceptance silently |
| Shell/packaging indirection prevents both entry points finding the shared wizard | Medium | Test installed layouts and arbitrary paths, not only source-tree imports |
| npm/Homebrew wrappers appear more supported than evidence warrants | High | Bound corrections to approved installation hygiene; document limitations and test affected routes without claiming publication or cross-platform validation |
| Missing clean-room host/network/audio prerequisites or forbidden remote operations block V2 | High | Record prerequisites and blocked evidence; do not substitute hermetic mocks for the full user installation proof |
| Secrets leak or reinstall replaces user settings | Medium | Non-argv secret input, mode-600 storage, output/permission assertions, retry and preservation tests |
| Two deployment modes compete or a port collision kills an unrelated process | Medium | Shared configuration, ownership-aware service handling, actionable failure and parity tests |
| Broad monorepo change lacks a workspace quality net | High | All three suites plus V1/V2; bounded work units; no unsupported coverage/lint claims |
| Nested spec routing or audit drift causes duplicated/stale requirements | Medium | Explicit archive destinations; preserve subproject safeguards; verify audit claims against current files |
| Human UAT timing and live registration repair exceed autonomous permissions | Medium | Human checklist resolves timing and validates live state; no direct changes outside the worktree |
| Smoke 16n leaks host playback state and can stop a real daemon | High | Isolate lock, PID, IPC socket, and engine socket together in every `new_env()`; prove non-interference with sandbox fixtures and preserve defaults/behavior |
| Oracle tree lookup masks missing files or a pin change weakens parity | Medium | Check lookup return codes across both layouts at the same SHA; retain strict `boundaries.py`/`cleaner.py`/`redact.py` identity and all existing assertions |
| Locally available `d66616b` cannot be retrieved from the documented GitHub origin | Medium | Require real V2 retrieval evidence for the selected full SHA; report `BLOCKED` and return the blocker to the maintainer, never silently select another ref |

## Rollback Plan

Keep each work unit independently revertible with its tests/docs. Revert affected implementation commits in reverse dependency order, retaining the acceptance harness and evidence where compatible. Restore previous launcher/manifest/bootstrap behavior together rather than leaving mixed entry points. For a deployed installation, stop only the AT-11-owned service before restoring its prior registration or generated unit; restore managed CLI/keymap artifacts from installation-time backups without touching unrelated processes or user entries. Preserve mode-600 credentials, preferences, and model data; never delete them as a rollback shortcut. Reset only AT-11 completion state when needed for a safe retry. Live rollback commands require separate authorization and are not executed by this phase. Revert bounded OpenSpec updates with their behavior changes without deleting registry history.

For the M1 extension, keep playback overrides and `new_env()` isolation as one rollback boundary: never leave tests pointing at host defaults after a partial revert. If rollback restores the unsafe baseline, do not run that suite against a live host and keep M1 unclosed until safe isolation is restored. Revert the pin/identity-driver unit coherently with its tests and evidence; restoring `32e9bafb` restores the known baseline failure, not a passing release. Public unavailability is not permission to roll forward to a different SHA. Preserve strict assertions, invalidate affected green evidence, and re-run the required V1/V2 gates before closure.

## Dependencies

- Approved PRD at `1b404ad`, completed AT-10 monorepo migration, and unchanged frozen IPC/speech contracts.
- A compatible Herdr host and the existing Python/uv, shell, git, jq, and project-specific runtime prerequisites; the wizard adds no runtime dependency.
- Design of shared-module installation reachability, secure unattended inputs, and honest clean-room execution; carry the resolved gate (Engram #9636) and resolve only genuinely remaining acceptance ambiguities before dependent work, without reopening the settled gate.
- Authorized documented network origins when real installation/STT downloads require them, appropriate platform/audio environments, and a human for V3.
- M1 remediation authorization (#9711), causal mapping (#9713), and selected immutable pin (#9715); downstream spec/design/task updates must preserve these decisions. Public retrieval remains unproven by local Git resolution alone.

## Success Criteria

- [ ] All RF-AT-11-1 through RF-AT-11-12 and applicable RNFs trace to specs and evidence, with all nine approved Gherkin scenarios referenced rather than silently rewritten.
- [ ] The harness is implemented as milestone 1 task 1; every milestone closes only with the agreed V1/V2 gate genuinely green and a work-unit commit.
- [ ] Documented GitHub/local installation works without personal paths, dotfiles, manual symlinks, or manual configuration edits; manifests are tracked and warning-free.
- [ ] One shared wizard protects secrets, preserves configuration on reinstall, supports unattended/skip paths, and handles explicit STT acceptance/refusal consistently with the resolved health contract.
- [ ] Primary plugin startup and optional generated systemd deployment use the same configuration; doctor identifies simulated failures with actionable repair commands.
- [ ] Three project suites and V1/V2 evidence are recorded without claiming nonexistent quality tooling; frozen contracts remain unchanged.
- [ ] Auto-chain work units respect the 400-line review budget, with no remote operation inferred from that strategy.
- [ ] Human V3 sign-off, including timing and remaining installation friction, is obtained before merge; autonomous completion never substitutes for it.
- [ ] Smoke 16n retains its read confirmation assertion, proves `r` follows transcribe/read, and cannot contact/stop a real host daemon: all three playback paths and the engine socket are sandbox-local, with unchanged production defaults.
- [ ] Smoke 40e resolves monorepo and legacy oracle paths with checked return codes and retains exact byte identity for `boundaries.py`, `cleaner.py`, and `redact.py`; missing objects/files fail explicitly and no assertion is weakened or deleted.
- [ ] `AGENT_TTS_REF` uses full SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be`; V2 proves retrieval from the documented GitHub origin or records `BLOCKED` without fallback. Neither a local object nor a stubbed install counts as that proof.
- [ ] M1 closes with both baseline failures fixed, all three V1 suites green, and the resolved V2 active-scenario gate satisfied; no baseline exception, new milestone, or frozen-contract change is introduced.
