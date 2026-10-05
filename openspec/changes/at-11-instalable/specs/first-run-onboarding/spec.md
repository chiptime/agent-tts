# first-run-onboarding Specification

## Purpose

A single shared first-run wizard (one implementation reachable from both the
plugin and the brain installation/first-launch entry points) that captures
credentials and preferences, offers explicit STT model consent, applies keymap
preferences, verifies completion health, and leaves a retryable, safely
skippable state. Interactive runs are dependency-free text interaction;
noninteractive runs consume flags/env answers. Secrets are protected end to
end. Frozen contracts `contracts/ipc-v2` and `contracts/tts-brain-v1` are
consumed as-is and are not modified by this capability.

Traceability: RF-AT-11-3, RF-AT-11-4, RF-AT-11-5, RF-AT-11-12,
RNF-AT-11-1, RNF-AT-11-2; PRD acceptance scenarios "Primera ejecución con
claves", "Descarga de modelo STT con consentimiento", "Rechazo de la descarga
degrada sin romper", "Salud final post-asistente"
(docs/prds/AT-11-instalable-first-run.md, "Escenarios de aceptación").

## Requirements

### Requirement: First-run wizard triggers on both entry points

The first launch of `herdr-tts` and the first launch of the brain MUST run the
shared first-run wizard when no completion marker (state file under the
user's resolved `~/.config/`) exists. The same wizard behavior MUST be
reached from both entry points, including from installed layouts (not only
source-tree imports). The completion marker MUST suppress all future
first-run wizard launches once written.

#### Scenario: Wizard runs when marker is missing

- GIVEN a clean installation with no completion marker
- WHEN `herdr-tts` is launched for the first time
- THEN the shared wizard starts

#### Scenario: Marker suppresses future first-run

- GIVEN a completed onboarding with its marker written
- WHEN either entry point launches again
- THEN the wizard does not run

#### Scenario: Wizard reachable from installed layout

- GIVEN the plugin installed via the documented installation route (not a source-tree checkout)
- WHEN first launch occurs
- THEN the shared wizard is found and runs from the installed files

(PRD anchor: RF-AT-11-3, part of "Escenario: Primera ejecución con claves".)

### Requirement: Noninteractive execution and explicit skip

The wizard MUST be skippable with an explicit `--no-first-run` flag. Both
entry points MUST support a noninteractive first-run mode that consumes
answers provided via flags or environment variables and never blocks waiting
on a terminal. The primary plugin startup path MUST remain nonblocking in
unattended contexts.

#### Scenario: --no-first-run bypasses cleanly

- GIVEN an installation without a completion marker
- WHEN either entry point is launched with `--no-first-run`
- THEN the wizard does not run, startup proceeds, and no marker is written

#### Scenario: Noninteractive completion from flags/env

- GIVEN answers provided via flags or environment variables in an unattended context
- WHEN the wizard runs in noninteractive mode
- THEN it completes without prompting and persists the same artifacts as an interactive run

#### Scenario: Unattended startup never blocks

- GIVEN a non-interactive environment (no TTY) with no completion marker and no explicit answers
- WHEN the entry point starts
- THEN it does not hang and surfaces how to configure noninteractively

(PRD anchor: RF-AT-11-3 and part of "Escenario: Primera ejecución con
claves" [V2 noninteractive mode]; proposal nonblocking clause.)

### Requirement: Credential capture and protection

The wizard MUST capture and persist the GLM API key (required only when the
brain is being configured), optional provider keys, the voice provider
preference, and keymap preferences, writing to standard locations outside any
repository (`~/.config/herdr-brain/env`, mode 600). Interactively, secrets
MUST be read without terminal echo (e.g., `getpass`); unattended runs MUST
provide secrets through a non-argv mechanism. No key may ever be printed,
logged, echoed, or appear in visible argv, assistant output, or logs.

#### Scenario: Interactive capture without echo

- GIVEN an interactive terminal
- WHEN the wizard prompts for the GLM key
- THEN the input is not echoed back to the terminal and is never re-displayed afterwards

#### Scenario: Noninteractive capture without argv exposure

- GIVEN the GLM key supplied through the supported non-argv mechanism
- WHEN the wizard completes
- THEN the key is persisted to `~/.config/herdr-brain/env` with mode 600
- AND the key does not appear in process listings, logs, or wizard output

#### Scenario: Key never appears in output or logs

- GIVEN a completed or failed wizard run of either mode
- WHEN all produced output and logs are inspected
- THEN no secret value appears anywhere

#### Scenario: GLM key optional without the brain

- GIVEN a plugin-only installation (no brain configuration selected)
- WHEN the wizard runs to completion
- THEN no GLM key is demanded and onboarding succeeds

(PRD anchor: "Escenario: Primera ejecución con claves" [RF-AT-11-3,
RF-AT-11-4, US-AT-11-2] [V2]; RNF-AT-11-2.)

### Requirement: Voice and keymap preferences

The wizard MUST capture the voice provider preference and keymap preferences,
persist them to the standard user configuration locations, and apply the
keymap through the existing adoption mechanism followed by an automatic
configuration reload. An existing user keymap MUST NOT be overwritten
without explicit consent; an explicit opt-out MUST be honored. A re-run
against existing preferences MUST preserve them.

#### Scenario: Preferences captured and persisted

- GIVEN a wizard run where the user selects a voice provider and keymap style
- WHEN onboarding completes
- THEN both preferences are persisted at the standard config locations and are honored by subsequent launches

#### Scenario: Keymap adopted with automatic reload

- GIVEN no existing keymap conflicts
- WHEN the wizard applies keymap preferences
- THEN adoption runs and the configuration reload is executed automatically (no manual reload step)

#### Scenario: Existing keymap preserved

- GIVEN a pre-existing user keymap
- WHEN the wizard runs without explicit consent to replace it
- THEN the existing keymap remains unchanged and the user is informed

(PRD anchor: RF-AT-11-4; audit D4. Overlap with the plugin installer
"Keymap adoption policy" requirement is intentional: the installer's policy
remains authoritative for its own artifacts; the wizard reuses the same
mechanism.)

### Requirement: Explicit STT model consent

The brain-side wizard MUST offer the STT (Whisper) model download with
explicit user consent and size selection among `tiny`, `base`, and `small`.
Nothing is downloaded without consent. On consent, the model downloads to
the standard model store and the speech-surface contract check is verified
afterwards.

#### Scenario: Accepted consent downloads and verifies

- GIVEN a clean-room environment with no preloaded models
- WHEN the wizard offers the STT model and the user accepts with size `base`
- THEN the model downloads to the standard store
- AND the contract verification passes afterwards
- AND `/health` reports STT coherent with the accepted choice

#### Scenario: No consent, no download

- GIVEN a user who has not accepted the STT download
- WHEN onboarding completes
- THEN no model download was initiated

(PRD anchor: "Escenario: Descarga de modelo STT con consentimiento"
[RF-AT-11-5, US-AT-11-3] [V2].)

### Requirement: STT refusal preserves the degraded journey

Refusing the STT model download MUST end onboarding successfully. The
installation remains functional: `/ask` works with functional `audio_url`
behavior (or its documented `null` case), and the TTS journey is unaffected.
The STT status reported by `/health` after refusal MUST remain consistent
with the frozen speech-surface contract (`contracts/tts-brain-v1`), which
this change MUST NOT modify.

#### Scenario: Refusal completes without breakage

- GIVEN the wizard offers the STT model download
- WHEN the user refuses
- THEN onboarding finishes successfully
- AND `/ask` remains functional with `audio_url` behavior per its documented contract (including the documented null case)
- AND no later wizard or launch step fails because of the missing model

#### Scenario: Frozen contract untouched

- GIVEN the STT refusal flow
- WHEN the change's artifacts are reviewed
- THEN `contracts/tts-brain-v1` and `contracts/ipc-v2` are byte-identical to before the change

(PRD anchor: "Escenario: Rechazo de la descarga degrada sin romper"
[RF-AT-11-5] [V2].)

**Recorded discrepancy (flagged, not resolved here):** the approved PRD's
refusal scenario states `/health` reports `stt: unavailable`, while
RF-AT-11-12 states `stt: ready|degraded` coherent with the user's choice.
The frozen health/speech contract governs; any change to the reported
vocabulary would be a contract change and is out of scope. This conflicting
assertion MUST be clarified by design/maintainer before implementation bakes
in either reading; tests in this change assert consistency with the frozen
contract, not with either PRD sentence in isolation.

### Requirement: Completion health and safe retryability

Successful wizard completion MUST verify final health: the brain `/health`
reports `tts: ok`, and `herdr plugin list` emits no manifest warnings. The
completion marker is written only after this verification passes. A failed
onboarding MUST leave the system safely retryable: no half-written
credentials or corrupt marker, and a re-run of the wizard (or installer)
recovers cleanly.

#### Scenario: Healthy completion asserts

- GIVEN a wizard run finishing all its steps
- WHEN completion checks execute
- THEN the brain `/health` reports `tts: ok`
- AND `herdr plugin list` emits no warnings
- AND the completion marker exists

#### Scenario: Completion marker withheld on failed health

- GIVEN a wizard run whose final health check fails
- WHEN onboarding ends
- THEN the completion marker is not written and the next launch retries onboarding

#### Scenario: Failed onboarding is safely retryable

- GIVEN a wizard run interrupted or failed mid-way
- WHEN the wizard or installer re-runs
- THEN it completes cleanly without duplicating or corrupting credentials and preferences

(PRD anchor: "Escenario: Salud final post-asistente" [RF-AT-11-12]
[V2+V3]; proposal retryability clause. The timed human UAT layer of this
scenario belongs to the V3 gate in `installation-diagnostics`.)

### Requirement: Dependency-free text interaction

The wizard MUST NOT add new runtime dependencies: interaction is plain text
over stdin/stdout, usable in minimal environments. No network, TUI framework,
or extra package is required beyond what the installation already needs.

#### Scenario: Wizard runs in a minimal environment

- GIVEN an environment with only the runtime prerequisites already required by the installation
- WHEN the wizard runs interactively
- THEN it completes using plain stdin/stdout interaction without requesting any additional package

(PRD anchor: RNF-AT-11-1.)

## Recorded Clarifications (not resolved in this spec)

- The STT refusal status discrepancy (`unavailable` vs `ready|degraded`) is
  recorded under the refusal requirement above; it is a frozen-contract
  consistency question, not new behavior to spec.
- The precise module location and packaging reachability of the shared wizard
  module is a design-phase decision (per the approved proposal); this spec
  fixes the shared behavior, not its packaging location.
