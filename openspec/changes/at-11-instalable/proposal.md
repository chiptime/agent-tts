# Proposal: AT-11 — Independent Installation and First-Run Onboarding

## Intent

Make the agent-tts monorepo installable by a new user without the maintainer's directory layout, dotfiles, secrets, or manual configuration edits. Implement the approved [PRD](../../../docs/prds/AT-11-instalable-first-run.md) at `1b404ad`, using the [installation audit](../../../docs/prds/AT-11-auditoria-instalacion.md) as defect evidence, not as a substitute for current source inspection.

The maintainer has now explicitly authorized development. The PRD's historical hold does not block this change. Its four resolved decisions remain binding: plugin `[[startup]]` is primary; generated systemd deployment is supported; one shared wizard serves all entry points; Tailscale belongs in advanced documentation; naming remains `agent-tts` with `herdr.*` plugin IDs.

## Scope

### In Scope

- **Installation hygiene:** repair monorepo source URLs and subdirectory handling, deliver the missing brain manifest, correct bootstrap development installs to `engine/`, and remove active legacy installation guidance. Document GitHub-subdirectory and local-repository installation; do not require a root plugin manifest.
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
| 1. Hygiene | **Task 1: build `scripts/acceptance/clean-install.sh`**, then audit A/B bug and active legacy-reference corrections, including bounded OpenSpec context correction | V1 + V2 green and a verified work-unit commit |
| 2. Decoupling | Generic path/binary/port/config resolution, CLI exposure, generated service option, and compatibility proofs | V1 + V2 green and a verified work-unit commit |
| 3. First-run | Shared wizard, credentials/preferences, noninteractive handling, STT consent/setup, keymap integration | V1 + V2 green and a verified work-unit commit |
| 4. Doctor + docs | Actionable diagnosis, complete installation/advanced docs, final acceptance traceability | V1 + V2 green and a verified work-unit commit |

Plan cohesive auto-chain review slices at no more than 400 authored additions plus deletions per PR, keeping tests and user-facing docs with each work unit. Milestones may require multiple slices; do not equate one milestone with one PR or shrink tests to meet the budget. This proposal creates no commits or PRs.

### Verification Contract

Reference, rather than reproduce, all **nine Gherkin scenarios** in the approved PRD's “Escenarios de aceptación” section and its RF-to-verification matrix. The spec phase must preserve their traceability and distinguish automated evidence from the ninth, timed human UAT scenario; final health also retains its V3 obligation.

V1 runs for each installation-surface change, including installation docs, and proves compatibility for each audit correction. Preserve the existing project commands:

| Working directory | Command |
|---|---|
| `engine/` | `python -m pytest tests/ -q` |
| `hosts/herdr/brain/` | `python -m pytest tests/ -q && node --test tests/js/` |
| `hosts/herdr/tts-plugin/` | `bash scripts/smoke-tests.sh` |

Run all three project suites for integrated milestone closure, plus the installation-specific V1 checks and `bash scripts/acceptance/clean-install.sh` from the workspace root. No coverage, lint, or type-check pass is implied.

V2 must exercise the literal documented installation steps from an arbitrary checkout path with empty temporary HOME, isolated configuration/state, minimal PATH, no dotfiles, brew, Tailscale, or preexisting secrets. It must enforce the PRD's documented-origin network boundary and distinguish stubbed V1 contracts from real installation evidence. Unavailable host/network/audio prerequisites are blockers, not successful skips. The current no-remote authorization permits proposal/planning only for network-dependent checks; later execution needs explicit authorization, and V2 must not be reported green without it when required.

**Milestone-gate tension:** the complete V2 suite includes first-run and doctor behavior delivered only in milestones 3/4, yet every milestone requires V2 green. Design/tasks must expose this sequencing conflict and obtain an explicit gate interpretation from the orchestrator/maintainer before dependent closure; do not silently redefine V2 as a smoke subset, disable failing scenarios, or declare unfinished acceptance passed. Building the harness first remains mandatory regardless.

V3 is a manual, timed clean-machine UAT with recorded friction and human sign-off before merge; it is outside the autonomous loop. Retain the PRD's under-ten-minute target, while flagging its differing host-preinstalled versus host-included timing language for the human checklist.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `scripts/acceptance/clean-install.sh` | New | First task; reproducible installation gate |
| `hosts/herdr/tts-plugin/scripts/{install,bootstrap}.sh`, `bin/herdr-tts`, `herdr-plugin.toml` | Modified | Monorepo installation, CLI exposure, wizard entry points, build/startup integration |
| `hosts/herdr/brain/herdr-plugin.toml` | New | Tracked brain plugin manifest and primary startup route |
| `hosts/herdr/brain/bin/herdr-brain`, `deploy/install.sh`, `src/herdr_brain/` | Modified | Portable launcher/config, secure onboarding integration and diagnostics |
| `hosts/herdr/brain/deploy/herdr-brain.service` / `herdr-brain.service.tmpl` | Replaced / New | Static machine-bound unit replaced by generated option |
| Shared wizard module, location selected in design | New | Single implementation reachable from both installed entry points |
| `hosts/herdr/tts-plugin/packaging/{npm,homebrew}/` | Modified, bounded | Correct active legacy references/layout assumptions; no publication expansion |
| `engine/tests/`, `hosts/herdr/brain/tests/`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | Modified as needed | Compatibility and installation proofs, preserving frozen contracts |
| `README.md`, `hosts/herdr/{brain,tts-plugin}/README.md`, active installation docs | Modified | Canonical installation, recovery, supported deployment paths and V3 checklist |
| Root `openspec/` and plugin `openspec/config.yaml`, `specs/{installer,plugin-bootstrap}/` | New / Modified | Workspace artifacts and explicit bounded subproject updates |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Milestone V2 closure conflicts with later feature delivery | High | Surface gate interpretation before closure; retain a truthful full-suite result and harness-first ordering |
| Approved STT refusal says `unavailable`, while RF-12 says `ready\|degraded` | High | Spec phase records discrepancy against the frozen health contract; obtain clarification for conflicting assertions without inventing a schema change |
| RF-6 allows known brew prefixes, but the zero-path scenario forbids `/home/linuxbrew` | Medium | Design an environment-derived discovery strategy; escalate any remaining literal-prefix need instead of weakening acceptance silently |
| Shell/packaging indirection prevents both entry points finding the shared wizard | Medium | Test installed layouts and arbitrary paths, not only source-tree imports |
| npm/Homebrew wrappers appear more supported than evidence warrants | High | Bound corrections to approved installation hygiene; document limitations and test affected routes without claiming publication or cross-platform validation |
| Missing clean-room host/network/audio prerequisites or forbidden remote operations block V2 | High | Record prerequisites and blocked evidence; do not substitute hermetic mocks for the full user installation proof |
| Secrets leak or reinstall replaces user settings | Medium | Non-argv secret input, mode-600 storage, output/permission assertions, retry and preservation tests |
| Two deployment modes compete or a port collision kills an unrelated process | Medium | Shared configuration, ownership-aware service handling, actionable failure and parity tests |
| Broad monorepo change lacks a workspace quality net | High | All three suites plus V1/V2; bounded work units; no unsupported coverage/lint claims |
| Nested spec routing or audit drift causes duplicated/stale requirements | Medium | Explicit archive destinations; preserve subproject safeguards; verify audit claims against current files |
| Human UAT timing and live registration repair exceed autonomous permissions | Medium | Human checklist resolves timing and validates live state; no direct changes outside the worktree |

## Rollback Plan

Keep each work unit independently revertible with its tests/docs. Revert affected implementation commits in reverse dependency order, retaining the acceptance harness and evidence where compatible. Restore previous launcher/manifest/bootstrap behavior together rather than leaving mixed entry points. For a deployed installation, stop only the AT-11-owned service before restoring its prior registration or generated unit; restore managed CLI/keymap artifacts from installation-time backups without touching unrelated processes or user entries. Preserve mode-600 credentials, preferences, and model data; never delete them as a rollback shortcut. Reset only AT-11 completion state when needed for a safe retry. Live rollback commands require separate authorization and are not executed by this phase. Revert bounded OpenSpec updates with their behavior changes without deleting registry history.

## Dependencies

- Approved PRD at `1b404ad`, completed AT-10 monorepo migration, and unchanged frozen IPC/speech contracts.
- A compatible Herdr host and the existing Python/uv, shell, git, jq, and project-specific runtime prerequisites; the wizard adds no runtime dependency.
- Design of shared-module installation reachability, secure unattended inputs, and honest clean-room execution; explicit resolution of the listed acceptance/gate ambiguities before dependent work.
- Authorized documented network origins when real installation/STT downloads require them, appropriate platform/audio environments, and a human for V3.

## Success Criteria

- [ ] All RF-AT-11-1 through RF-AT-11-12 and applicable RNFs trace to specs and evidence, with all nine approved Gherkin scenarios referenced rather than silently rewritten.
- [ ] The harness is implemented as milestone 1 task 1; every milestone closes only with the agreed V1/V2 gate genuinely green and a work-unit commit.
- [ ] Documented GitHub/local installation works without personal paths, dotfiles, manual symlinks, or manual configuration edits; manifests are tracked and warning-free.
- [ ] One shared wizard protects secrets, preserves configuration on reinstall, supports unattended/skip paths, and handles explicit STT acceptance/refusal consistently with the resolved health contract.
- [ ] Primary plugin startup and optional generated systemd deployment use the same configuration; doctor identifies simulated failures with actionable repair commands.
- [ ] Three project suites and V1/V2 evidence are recorded without claiming nonexistent quality tooling; frozen contracts remain unchanged.
- [ ] Auto-chain work units respect the 400-line review budget, with no remote operation inferred from that strategy.
- [ ] Human V3 sign-off, including timing and remaining installation friction, is obtained before merge; autonomous completion never substitutes for it.
