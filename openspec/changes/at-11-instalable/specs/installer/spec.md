# installer — Delta for at-11-instalable

**Canonical archive target:** `hosts/herdr/tts-plugin/openspec/specs/installer/spec.md`
(plugin subproject registry). This delta MUST be archived into that file by
`sdd-archive`. It MUST NOT be archived into a new root
`openspec/specs/installer/spec.md` copy — the workspace root registry stays
empty of plugin capabilities.

**Change context:** the plugin now lives in the agent-tts monorepo
(`hosts/herdr/tts-plugin`). The installer's source corrections, automatic CLI
exposure, and shared first-run onboarding integration extend this
specification; all preflight, keymap, English-output, and pinned-source
safeguards are retained.

## MODIFIED Requirements

### Requirement: Fresh install

With no prior install, the installer SHALL obtain the plugin source from the
agent-tts monorepo (the documented GitHub-subdirectory route or a local
monorepo repository), place the checkout at `~/.local/share/herdr-tts/plugin`,
run the bootstrap, expose the `herdr-tts` CLI per the Managed CLI exposure
requirement, verify the daemon came up, and print the `--status` pointer. The
installer MUST NOT reference the archived standalone repositories
(`chiptime/herdr-tts`, `chiptime/herdr-brain`) in active code, URLs, or
output. On an interactive completion it MUST hand off to the shared first-run
wizard per the Shared onboarding integration requirement.
(Previously: installed a clone of the standalone `chiptime/herdr-tts`
repository with no CLI exposure and no onboarding integration.)

#### Scenario: Fresh end-to-end install

- GIVEN a hermetic env with `git`, `uv`, `jq`, `herdr` stubs and empty XDG dirs
- WHEN the installer runs
- THEN monorepo checkout, venv bootstrap, CLI exposure, and daemon verification succeed in order and the status pointer prints

#### Scenario: Source is the monorepo subdirectory

- GIVEN a fresh install run
- WHEN the recorded source/URL and checkout layout are inspected
- THEN the source is the agent-tts monorepo operating on `hosts/herdr/tts-plugin`
- AND no archived-repository URL appears in commands, output, or recorded state

### Requirement: Idempotent re-run and remote guard

A re-run SHALL distinguish fresh vs upgrade by target-dir existence plus git
remote match against the canonical agent-tts monorepo URL; a matching remote
upgrades in place, refreshing agent-tts beyond the never-upgrade gate, and
MUST preserve existing user state: mode-600 credentials, voice/keymap
preferences, existing keymap file, and first-run completion state. The
exposed `~/.local/bin/herdr-tts` artifact is refreshed to the upgraded
checkout. A mismatched remote MUST abort cleanly.
(Previously: guard keyed on the legacy standalone-repo canonical URL, with
no user-state preservation guarantee.)

#### Scenario: Matching remote upgrades

- GIVEN an existing target dir whose git remote matches the canonical agent-tts monorepo URL
- WHEN the installer re-runs
- THEN the checkout refreshes, agent-tts is upgraded to the pinned ref, and the exposed CLI artifact points at the upgraded checkout

#### Scenario: Upgrade preserves user state

- GIVEN an existing installation with credentials, preferences, and an existing keymap
- WHEN the installer re-runs over it
- THEN those credentials, preferences, and the keymap file remain unchanged

#### Scenario: Mismatched remote aborts

- GIVEN an existing target dir whose git remote differs from the canonical agent-tts monorepo URL
- WHEN the installer re-runs
- THEN it exits non-zero naming the mismatch, writing nothing

### Requirement: Uninstall guidance

The installer MUST print complete manual uninstall steps at install end: stop
the daemon, remove the venv under `~/.local/share/herdr-tts`, remove the
managed keymap block, and remove the managed `~/.local/bin/herdr-tts`
artifact. It SHOULD also state where the first-run completion marker lives so
a user can reset it for a fresh first-run after reinstalling. The README MUST
document the same steps.
(Previously: guidance covered daemon, venv, and keymap block only — no CLI
artifact or completion-marker guidance.)

#### Scenario: Printed uninstall steps are complete

- GIVEN a successful install run
- WHEN its output is captured
- THEN it contains the daemon-stop, venv-removal, managed-keymap-block-removal, and managed-CLI-artifact-removal steps

## ADDED Requirements

### Requirement: Managed CLI exposure

The installer MUST expose the plugin's `bin/herdr-tts` as `herdr-tts` in
`~/.local/bin`, creating that directory when missing. It MUST warn when the
resolved PATH does not include `~/.local/bin`. No manual symlink step may be
required in any documented flow. Re-runs and upgrades refresh the exposed
artifact to remain consistent with the checkout.

#### Scenario: Missing ~/.local/bin is created

- GIVEN a hermetic HOME without `~/.local/bin`
- WHEN the installer completes
- THEN `~/.local/bin/herdr-tts` exists, is executable, and runs

#### Scenario: PATH gap warns

- GIVEN a PATH without `~/.local/bin`
- WHEN the installer completes
- THEN the output warns about the PATH gap and names the remediation

#### Scenario: No manual symlink required

- GIVEN the documented installation steps
- WHEN they are followed on a clean machine
- THEN `herdr-tts` is invocable from `~/.local/bin` without any user-created symlink

(PRD anchor: RF-AT-11-2; PRD scenario "Instalación del plugin desde clon
fresco" [V2].)

### Requirement: Shared onboarding integration

On an interactive successful install, the installer MUST launch the shared
first-run wizard at completion. In a noninteractive context (no TTY, or an
explicit skip such as `--no-first-run`), the installer MUST NOT block: the
wizard hand-off defers to the first `herdr-tts` launch per the
`first-run-onboarding` capability. A wizard failure MUST NOT corrupt or
invalidate the completed install; the install stays healthy and the wizard
remains safely retryable.

#### Scenario: Interactive install launches the wizard

- GIVEN a successful install on an interactive terminal
- WHEN the installer finishes
- THEN the shared first-run wizard starts

#### Scenario: Noninteractive install defers the wizard

- GIVEN an install running without a TTY or with an explicit skip flag
- WHEN the installer finishes
- THEN no wizard runs and startup is not blocked; the first interactive launch triggers the wizard

#### Scenario: Wizard failure leaves a healthy install

- GIVEN the wizard fails or is aborted mid-run
- WHEN the install state is inspected
- THEN the plugin installation itself is complete and functional, and re-running the wizard is safe

(PRD anchor: RF-AT-11-3; proposal nonblocking-unattended clause.)

## Retained (unchanged by this delta)

The following existing requirements are preserved as-is and MUST survive
archival without edits: **Preflight before mutation** (including the linked
checkout refusal), **Keymap adoption policy** (never overwrite, env-var
overrides, reload, `--no-keymap`), **English output**, and **Tag-pinned
default with escape hatch** (pinned-source safeguards; the pinned tag now
refers to the agent-tts monorepo via the corrected canonical URL handled in
the modified requirements above).

## REMOVED Requirements

- None.

## RENAMED Requirements

- None.
