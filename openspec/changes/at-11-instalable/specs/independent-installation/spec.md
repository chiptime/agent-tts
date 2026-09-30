# independent-installation Specification

## Purpose

Make the agent-tts monorepo installable by a new user without the maintainer's
directory layout, dotfiles, secrets, or manual configuration edits. This
capability owns installation hygiene (fresh-clone routes, tracked manifests,
zero legacy references), machine independence (environment-derived paths and
binary discovery, configurable port, generated systemd option, portable brain
launcher), secure standard configuration locations, idempotent reinstallation,
and the documented supported routes. Frozen contracts `contracts/ipc-v2` and
`contracts/tts-brain-v1` are consumed as-is and are not modified by this
capability.

Traceability: RF-AT-11-1, RF-AT-11-2, RF-AT-11-6, RF-AT-11-7, RF-AT-11-8,
RF-AT-11-9, RF-AT-11-11; PRD acceptance scenarios "Instalación del plugin
desde clon fresco", "Cero rutas de máquina", "Reinstalación idempotente
conserva estado" (docs/prds/AT-11-instalable-first-run.md, "Escenarios de
aceptación").

## Requirements

### Requirement: Documented fresh-clone installation routes

The documented GitHub-subdirectory installation and the local-repository
installation MUST both succeed from a fresh clone of the monorepo placed at an
arbitrary, non-standard filesystem path, without editing any file by hand. The
documentation MUST show the subdirectory-based plugin install command and the
local-repository route, and MUST NOT present any archived-repository
(`chiptime/herdr-tts`, `chiptime/herdr-brain`) command as active guidance. The
installation flow MUST NOT require a plugin manifest at the monorepo root. The
brain plugin manifest (`hosts/herdr/brain/herdr-plugin.toml`) MUST be tracked
in version control so a fresh clone can register the brain as a plugin with
its `[[startup]]` entry intact.

#### Scenario: Fresh clone install at an arbitrary path

- GIVEN a clean HOME with no traces of the herdr ecosystem
- AND the monorepo cloned at a non-standard path
- WHEN the documented plugin installation commands are executed
- THEN `herdr plugin list` shows `herdr.tts` without warnings
- AND `herdr-tts --contract-version` prints a version >= 1 when invoked from `~/.local/bin`
- AND no step required editing files by hand

(PRD anchor: "Escenario: Instalación del plugin desde clon fresco"
[RF-AT-11-1, US-AT-11-1] [V2].)

#### Scenario: Local-repository installation route

- GIVEN the monorepo available locally (no network install involved)
- WHEN the documented local-repository installation route is followed
- THEN the plugin registers and starts without warnings and without manual configuration edits

#### Scenario: Brain manifest is tracked

- GIVEN a fresh clone of the monorepo
- WHEN the brain is installed following the documented plugin route
- THEN the brain registers from its tracked `herdr-plugin.toml` and `herdr plugin list` emits no manifest warnings for `herdr.brain`

#### Scenario: No active legacy references

- GIVEN the active installation documentation and installer sources
- WHEN they are searched for archived-repository install commands and URLs
- THEN no active reference to the legacy standalone repositories remains (historical/archived documents excepted)

### Requirement: Automatic CLI exposure without manual symlinks

The installation MUST expose the `herdr-tts` CLI in `~/.local/bin`, creating
that directory if it is missing. It MUST warn the user when the resolved PATH
does not include `~/.local/bin`. Manual symlinks MUST NOT be required as part
of any documented flow.

#### Scenario: Exposure with missing ~/.local/bin

- GIVEN a clean HOME where `~/.local/bin` does not exist
- WHEN the installation completes
- THEN `~/.local/bin/herdr-tts` exists and is executable

#### Scenario: PATH coverage warning

- GIVEN a PATH that does not include `~/.local/bin`
- WHEN the installation completes
- THEN the output warns that `~/.local/bin` is not on PATH and names the remediation

(PRD anchor: RF-AT-11-2, traces D1/C6; covered together with the plugin
installer delta "Managed CLI exposure" requirement.)

### Requirement: Environment-derived path and binary discovery

Installation scripts, launchers, units, and templates MUST NOT contain
absolute machine-specific paths. The brain's `HERDR_BIN` MUST be discovered
from the environment: PATH first, then standard environment-derived locations
resolved at runtime (e.g., from the detected prefix of the user's tool
environment), never a literal maintainer-machine prefix. The brain service
port MUST be configurable with a sane default. Every repository path consumed
at runtime MUST be derived from the location of the executing script or from
the environment, never from a hardcoded clone location.

#### Scenario: Binary discovered from PATH

- GIVEN `herdr` is available on PATH
- WHEN the brain launcher or generated unit resolves `HERDR_BIN`
- THEN it uses the PATH entry and records/uses no absolute machine-specific fallback

#### Scenario: Binary discovered without PATH entry

- GIVEN `herdr` is not on PATH
- WHEN the brain launcher resolves `HERDR_BIN`
- THEN it discovers the binary from runtime-derived standard locations of the actual environment (for example the detected brew prefix) and never from a literal `/home/linuxbrew` string in versioned sources

#### Scenario: Arbitrary checkout path honored

- GIVEN the monorepo installed at a non-canonical path
- WHEN installation and first launch run
- THEN every repo-relative resolution derives from the actual script/install location and succeeds

(PRD anchor: part of "Escenario: Cero rutas de máquina" [RF-AT-11-6].)

**Recorded clarification (do not resolve here):** RF-AT-11-6 mentions "known
brew prefixes" while the PRD's zero-path scenario forbids the literal
`/home/linuxbrew`. This spec requires environment-derived discovery; any
residual need for a literal prefix list MUST be escalated to design/maintainer
clarification instead of silently weakening the zero-path scenario.

### Requirement: Zero machine-specific coupling in versioned sources

The versioned monorepo tree (installers, service templates, launchers,
packaging wrappers, active installation docs) MUST NOT contain: the
maintainer's dotfiles path (`~/.dotfiles`), the literal brew prefix
`/home/linuxbrew`, personal remote-exposure domains (e.g., a personal tailnet
hostname), or absolute paths of the maintainer's clones. Detecting this is a
mechanical, testable assertion over the versioned tree.

#### Scenario: Machine-coupling pattern scan

- GIVEN the versioned monorepo tree
- WHEN coupling patterns (dotfiles path, literal brew prefix, personal domains, maintainer clone paths) are searched in installers, units, and templates
- THEN no active occurrence exists

(PRD anchor: "Escenario: Cero rutas de máquina" [RF-AT-11-6/7/8/9] [V1+V2].)

### Requirement: Portable brain launcher resolution

`bin/herdr-brain` MUST NOT export `HERDR_TTS_HOME` containing a legacy or
machine-specific path. It SHALL resolve the tts-plugin home relative to its
own location (the installed monorepo layout) or from the environment, in that
order, consistent with RF-AT-11-8. A correct launcher MUST be observably
equivalent before/after the correction for a working setup (compatibility
parity, RNF-AT-11-4).

#### Scenario: Launcher resolves plugin relative to itself

- GIVEN a working monorepo installation and `HERDR_TTS_HOME` unset in the environment
- WHEN `bin/herdr-brain` starts
- THEN it resolves the tts-plugin relative to its own location and `/health` reports the TTS side as found

#### Scenario: No legacy path exported

- GIVEN the versioned `bin/herdr-brain`
- WHEN its exports are inspected
- THEN no `~/Code/personal/herdr-tts` (or equivalent archived-clone) default exists

(PRD anchor: "Escenario: Cero rutas de máquina" [RF-AT-11-8]; audit A1.)

### Requirement: Configurable port with ownership-aware startup

The brain service port MUST be configurable (environment or persisted
configuration) with a documented default. Installers, units, and the
first-run flow MUST NOT kill processes they do not own to free the port. When
the configured port is occupied by an unrelated process, startup or
deployment MUST fail with an actionable message identifying the conflict and
the remediation, leaving the unrelated process untouched.

#### Scenario: Custom port honored

- GIVEN a non-default port configured before service start
- WHEN the brain service starts
- THEN it listens on the configured port

#### Scenario: Occupied port fails without collateral damage

- GIVEN the configured port is held by a process not owned by this installation
- WHEN service startup or deployment runs
- THEN it exits with an actionable failure naming the port conflict
- AND the unrelated process is still running

(PRD anchor: RF-AT-11-6 port clause; audit C5.)

### Requirement: Generated systemd unit from a versioned template

The systemd user unit MUST be generated at installation time by substituting
discovered values (install location, `HERDR_BIN`, port, environment file
path) into a committed template (`herdr-brain.service.tmpl`). The generated
unit lives under `~/.config/systemd/user/` and MUST NOT be committed to
version control. The template itself MUST be free of machine-specific
absolute paths. Systemd deployment is an OPTIONAL supported route, never a
prerequisite: the plugin `[[startup]]` route remains the primary deployment
path.

#### Scenario: Generation substitutes discovered values

- GIVEN an installation at an arbitrary path with discovered `HERDR_BIN` and port
- WHEN the systemd option is selected
- THEN the generated unit under `~/.config/systemd/user/` references exactly those discovered values and starts the brain successfully

#### Scenario: Template and generated unit hygiene

- GIVEN the versioned template and the generated unit
- WHEN they are inspected
- THEN the template contains no absolute machine-specific paths and the generated unit is not tracked in git

#### Scenario: systemd is optional

- GIVEN a system without systemd (e.g., macOS) or a user who declines the service
- WHEN installation completes
- THEN the documented primary route works and no systemd artifacts are required or created

(PRD anchor: "Escenario: Cero rutas de máquina" [RF-AT-11-7]; audit C3.)

### Requirement: Secure standard configuration locations

Credentials and user preferences MUST live at standard, user-scoped locations
outside any repository (the brain environment file at `~/.config/herdr-brain/env`
with mode 600; plugin preferences under the user's resolved config directory).
Installation MUST NOT read or depend on the maintainer's dotfiles; an install
on a machine without the maintainer's personal environment files MUST
succeed.

#### Scenario: Env file mode and location

- WHEN the installation or first-run writes the brain environment file
- THEN it is created at `~/.config/herdr-brain/env` with mode 600, outside any git repository

#### Scenario: Install succeeds without personal dotfiles

- GIVEN a machine with no `~/.dotfiles` and no maintainer environment files
- WHEN the documented installation runs
- THEN it completes without aborting on missing personal files

(PRD anchor: RF-AT-11-4 storage clauses; audit C1. Credential capture
behavior is specified in `first-run-onboarding`.)

### Requirement: Idempotent reinstall preserving user state

Installation MUST be idempotent and safely re-executable. Re-running the full
install over an existing installation MUST preserve captured credentials,
voice/keymap preferences, and completion state, and MUST leave services in
the same functional state as before the re-run.

#### Scenario: Reinstall preserves keys and preferences

- GIVEN a completed installation with credentials and preferences
- WHEN the installer is re-executed in full
- THEN the environment file content and preferences are preserved
- AND services remain in the same functional state as before

#### Scenario: Reinstall over a broken install repairs it

- GIVEN a partially broken installation
- WHEN the installer re-runs
- THEN it repairs the installation without destroying user state

(PRD anchor: "Escenario: Reinstalación idempotente conserva estado"
[RF-AT-11-11] [V2].)

### Requirement: Optional remote exposure without personal defaults

Remote exposure of the brain (e.g., via Tailscale) MUST be optional and
configured by the user through the first-run wizard or environment; no
personal domain may exist in versioned code. Remote exposure is documented as
advanced guidance, not automated.

#### Scenario: Local-only by default

- GIVEN no remote-exposure configuration
- WHEN the installation completes
- THEN the brain serves locally and no remote domain is referenced

#### Scenario: Explicit remote configuration

- GIVEN a user-provided remote domain via wizard or environment
- WHEN the brain starts with that configuration
- THEN the configured domain is used and no personal domain was introduced into versioned sources

(PRD anchor: RF-AT-11-9, part of "Escenario: Cero rutas de máquina"; audit C4.)

### Requirement: Documented supported routes with honest wrapper support

The documentation MUST present one canonical primary installation flow, plus
optional systemd deployment and advanced remote-exposure guidance, for the
supported targets (Linux, WSL2, macOS). The existing npm/Homebrew wrapper
packages receive only necessary monorepo/legacy-reference corrections and
honest support documentation: documentation MUST NOT claim registry
publication, native Windows support, or validated cross-platform coverage
that does not exist.

#### Scenario: Wrapper corrections stay bounded

- GIVEN the npm and Homebrew wrapper packages and their docs
- WHEN they are inspected after the change
- THEN they reference the monorepo sources (no legacy repos) and make no unsupported distribution claims

#### Scenario: Unsupported platform not claimed

- GIVEN the installation documentation
- WHEN support claims are reviewed
- THEN Linux, WSL2, and macOS are the documented targets and native Windows is not claimed

## Recorded Clarifications (not resolved in this spec)

1. **STT refusal status vocabulary.** The approved PRD's refusal scenario
   reports `stt: unavailable` while RF-AT-11-12 states `stt: ready|degraded`.
   Behavior MUST stay consistent with the frozen speech-surface contract
   (`contracts/tts-brain-v1`, unchanged by this change). The conflicting
   assertion is flagged for design/maintainer clarification; this spec does
   not invent a schema change. See `first-run-onboarding`.
2. **Literal brew prefixes.** RF-AT-11-6's "known brew prefixes" versus the
   zero-path scenario's prohibition of `/home/linuxbrew` is resolved as
   environment-derived discovery; any remaining literal-prefix need MUST be
   flagged (see the discovery requirement above).
