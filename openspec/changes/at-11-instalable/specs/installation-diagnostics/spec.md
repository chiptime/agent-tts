# installation-diagnostics Specification

## Purpose

Diagnose an existing installation with actionable output (`doctor`), and
define the three verification levels that prove installation behavior the
way a real user experiences it: V1 automated compatibility tests, V2 the
clean-install acceptance harness, and V3 the separate timed human UAT
checklist. This capability also owns the honest evidence rules: what counts
as green, what counts as blocked, and what may never be claimed.

**Supplementary M1 verification obligations (authorized extension, Engram
#9711/#9713/#9715):** this capability also carries the V1 regression-suite
hermeticity and host non-interference proofs (smoke 16n), the checked
dual-layout oracle-identity evidence (smoke 40e), and the no-baseline-
exception M1 closure gate. These are supplementary V1/V2 verification
obligations; the nine approved PRD Gherkin scenarios remain unchanged,
unrenumbered, and primary.

Traceability: RF-AT-11-10; RNF-AT-11-3, RNF-AT-11-4; cross-cutting
verification contract; PRD acceptance scenarios "Doctor diagnostica rotura
simulada", "Salud final post-asistente" (V3 obligation), "UAT cronometrado
como usuario real" (docs/prds/AT-11-instalable-first-run.md, "Escenarios de
aceptación").

## Requirements

### Requirement: Doctor verifies installation health

A `doctor` command (reachable from the plugin CLI or equivalent brain
entry point) MUST verify: the `herdr-tts` CLI is reachable on PATH, an audio
backend is available, expected credentials are present, the daemon is alive,
the speech-surface contract v1 check passes, and the STT model state. A
healthy installation passes all checks.

#### Scenario: Healthy installation passes

- GIVEN a complete, correctly configured installation
- WHEN doctor runs
- THEN all checks pass and no repair guidance is printed

#### Scenario: Each mandatory area is checked

- GIVEN doctor's check list
- WHEN doctor executes on any installation
- THEN it covers PATH, audio backend, credentials, daemon liveness, contract v1, and STT model state

(PRD anchor: RF-AT-11-10; scenario "Doctor diagnostica rotura simulada".)

### Requirement: Actionable failure output

For every failed check, doctor MUST report the concrete problem and the exact
command (or step) that repairs it. Vague messages without a repair action do
not satisfy this requirement.

#### Scenario: Broken CLI PATH is named with its fix

- GIVEN a healthy installation whose `herdr-tts` PATH coverage is deliberately broken
- WHEN doctor runs
- THEN it reports the specific PATH problem and the exact repair command

#### Scenario: Each failure names its repair

- GIVEN doctor with any failing check (credentials, daemon, audio, STT, contract)
- WHEN the failure is printed
- THEN the output contains the exact remediation command for that failure

(PRD anchor: "Escenario: Doctor diagnostica rotura simulada"
[RF-AT-11-10, US-AT-11-4] [V2].)

### Requirement: V1 compatibility evidence per correction

Every change touching the installation surface (installers, `deploy/`,
`scripts/`, `bin/`, first-run, installation docs) MUST run the V1 automated
tests. Every audit correction MUST ship with a test-verified compatibility
guarantee (for example, observable parity of `bin/herdr-brain` behavior
before/after the `HERDR_TTS_HOME` fix). The three existing project suites are
preserved and each is run from its own working directory:

| Working directory | Command |
|---|---|
| `engine/` | `python -m pytest tests/ -q` |
| `hosts/herdr/brain/` | `python -m pytest tests/ -q && node --test tests/js/` |
| `hosts/herdr/tts-plugin/` | `bash scripts/smoke-tests.sh` |

All three project suites run for integrated milestone closure, plus the
installation-specific V1 checks. No coverage, lint, or type-check pass is
implied or claimed.

#### Scenario: Correction ships with compatibility proof

- GIVEN an audit correction (e.g., A1 `HERDR_TTS_HOME`)
- WHEN the change is delivered
- THEN a V1 test demonstrates equivalent observable behavior for previously working setups

#### Scenario: Documentation-only change still runs V1

- GIVEN a change that only edits installation documentation
- WHEN it is delivered
- THEN V1 runs for the installation surface, including the documented-steps checks

#### Scenario: Project suites preserved

- GIVEN the three project test commands above
- WHEN milestone closure is evaluated
- THEN each touched project's own command ran green and none was replaced or skipped

(PRD anchor: RNF-AT-11-4; proposal Verification Contract; testing
capabilities registry — strict_tdd false at workspace root, per-project
commands authoritative.)

### Requirement: V1 smoke-suite hermeticity and host non-interference

The plugin V1 smoke suite MUST be hermetic with respect to the host: a suite
run MUST NOT contact, signal, or change the state of any live host daemon or
the user's real playback state. To enable this, the plugin launcher's
playback lock, PID file, and IPC socket paths MUST be environment-overridable
while retaining byte-identical defaults when the overrides are unset, and the
smoke suite's `new_env()` MUST set all three — plus the engine IPC socket —
to sandbox-local paths inside the test environment. Isolated fixtures or
recorders stand in for daemon state; a live host daemon MUST NOT be used as a
test fixture. The section-16 read confirmation assertion MUST be preserved:
`r` MUST demonstrably follow the transcribe/read branch and its confirmation
assertion remains intact and passing.

(Supplementary V1/V2 verification obligation from the authorized M1
extension [Engram #9711/#9713]. It adds regression-suite safety proofs; it
does not replace or renumber any of the nine approved PRD Gherkin
scenarios.)

#### Scenario: Playback paths resolve inside the sandbox

- GIVEN the smoke suite creates a fresh test environment via `new_env()`
- WHEN any smoke test exercising playback runs
- THEN the playback lock, PID file, IPC socket, and engine socket all resolve to sandbox-local paths inside that environment

#### Scenario: Unset overrides keep the exact production defaults

- GIVEN the launcher runs without the new environment overrides
- WHEN playback starts
- THEN the lock, PID, and socket paths are exactly `/tmp/herdr-tts-playing.lock`, `/tmp/herdr-tts-current.pid`, and `/tmp/herdr-tts-player.sock`, and production toggle behavior is unchanged

#### Scenario: Suite run cannot disturb a live host daemon

- GIVEN a host with a live herdr-tts daemon — or an isolated recorder standing in for one — whose state is recorded before and after
- WHEN the full smoke suite runs
- THEN no suite process contacts, signals, or alters the live daemon or host playback state, and the recorded state is unchanged

#### Scenario: Read confirmation assertion preserved

- GIVEN smoke section 16's flow where `r` follows the transcribe/read branch
- WHEN the test executes in the sandbox
- THEN the existing read confirmation assertion runs unchanged and passes

### Requirement: Smoke 40e checked oracle identity

The smoke suite's engine-oracle identity check (section 40e) MUST resolve the
pinned immutable revision's files by a return-code-checked tree lookup that
supports both layouts — monorepo `engine/src/agent_tts/<file>` and legacy
`src/agent_tts/<file>` — at the same immutable revision. A missing lookup,
revision, or oracle file MUST fail explicitly with an actionable error; it
MUST NEVER pass by comparing empty output. Strict byte identity MUST be
retained for `boundaries.py`, `cleaner.py`, and `redact.py`, together with
the full existing assertion matrix; no assertion is weakened, deleted, or
made to pass by changing engine bytes to fit a stale oracle. Layout
compatibility MUST NOT be used as a revision fallback.

(Supplementary V1/V2 verification obligation from the authorized M1 extension
[Engram #9713/#9715]; the authoritative pin value is specified by the
`plugin-bootstrap` delta. The nine approved PRD Gherkin scenarios remain
unchanged.)

#### Scenario: Dual-layout lookup at one revision

- GIVEN the pinned immutable revision whose tree uses either the monorepo `engine/src/...` layout or the legacy `src/...` layout
- WHEN the oracle lookup resolves `boundaries.py`, `cleaner.py`, and `redact.py`
- THEN each file is found via a return-code-checked lookup under the matching layout at that same single revision

#### Scenario: Missing oracle fails explicitly

- GIVEN a checked lookup or a required oracle file that is missing or unreadable at the pinned revision
- WHEN section 40e runs
- THEN it fails explicitly naming the unresolved file or revision
- AND it never passes by comparing empty output

#### Scenario: Byte identity retained for all three files

- GIVEN successfully resolved oracle files at the pinned revision
- WHEN the comparison executes
- THEN the engine's `boundaries.py`, `cleaner.py`, and `redact.py` are byte-identical to the pinned revision's files
- AND every existing assertion still runs with none weakened or deleted

### Requirement: M1 closes with both baseline V1 failures fixed

Milestone 1 closure MUST require that both confirmed baseline V1 failures —
smoke 16n (read confirmation corrupted by a live-daemon environment leak) and
smoke 40e (oracle identity against the stale pre-monorepo pin) — are
genuinely fixed, and that all three V1 project suites run green, alongside
the binding milestone-scoped V2 activation gate. Neither failure MAY be
waived, skipped, disabled, relabeled as green, or accepted as a pre-existing
baseline exception. Failing or blocked evidence MUST be preserved until
corrected.

(Supplementary V1/V2 verification obligation from the authorized M1 extension
[Engram #9711]; it does not replace or renumber the nine approved PRD Gherkin
scenarios. V2 scenarios for functionality not yet delivered by M1 remain
`NOT-YET-ACTIVATED` per the milestone-scoped activation gate — never green
skips.)

#### Scenario: M1 closure evidence

- GIVEN milestone 1 closure is evaluated
- WHEN the evidence is reviewed
- THEN smoke 16n passes with host-safe sandbox isolation and its preserved read confirmation assertion
- AND smoke 40e passes with checked dual-layout lookup and strict three-file identity at the selected pin
- AND all three V1 project suites are green together with the active-scenario V2 gate

#### Scenario: No baseline exception

- GIVEN that both failures already existed unchanged at the base revision
- WHEN closure is attempted while either still fails
- THEN closure is refused, the failure is neither waived nor relabeled green, and the failing evidence remains recorded

#### Scenario: Later V2 scenarios are not green skips

- GIVEN V2 scenarios for functionality not delivered by M1
- WHEN M1 closure evidence is recorded
- THEN those scenarios are reported as `NOT-YET-ACTIVATED`, never as green or skipped-green

### Requirement: V2 clean-install harness is the authoritative gate

The acceptance harness `scripts/acceptance/clean-install.sh` MUST be built
complete as milestone 1, task 1, before other AT-11 work. It MUST execute the
literal documented installation steps (not shortcuts) from an arbitrary
checkout path, inside a sandbox with: an empty temporary HOME (no dotfiles,
no brew, no Tailscale, no preexisting secrets), isolated configuration and
state, and a minimal PATH. Network access MUST be restricted to the origins
the documentation cites. The harness MUST distinguish stubbed V1-style
contract checks from real installation evidence. Unavailable host, network,
or audio prerequisites are blockers to be reported as such — never
successful skips and never reported green.

#### Scenario: Harness runs the documented steps in a sanitized environment

- GIVEN the harness is invoked from the workspace root
- WHEN it executes
- THEN it performs the literal documented installation steps inside an empty-HOME, minimal-PATH, isolated-state sandbox and asserts the documented outcomes

#### Scenario: Documented-origin network boundary enforced

- GIVEN the harness sandbox
- WHEN installation steps run
- THEN network access is limited to the origins cited by the documentation and any other access attempt fails

#### Scenario: Missing prerequisites block, not skip

- GIVEN a clean-room run where host, network, or audio prerequisites are unavailable
- WHEN the affected harness scenario reaches its blocked point
- THEN the harness reports the scenario as blocked with the missing prerequisite named
- AND the overall run is not reported green

#### Scenario: Stubbed checks are labeled

- GIVEN harness assertions that exercise contracts via stubs rather than real installed components
- WHEN evidence is recorded
- THEN those assertions are identified as stubbed V1-style checks, distinct from real installation evidence

(PRD anchor: V2 level definition, "Principio de verificación" and
"Verificación" sections; proposal Verification Contract.)

### Requirement: Milestone-scoped V2 activation gate

V2 milestone closure uses the resolved "active scenarios per milestone"
interpretation: the full harness exists from milestone 1, but each
milestone's closure requires green only for the V2 scenarios whose
functionality is already delivered, with no regressions in any previously
green scenario. Scenario activation follows delivered functionality. The
complete V2 suite in green is mandatory at milestone-4 closure. A V2 scenario
whose functionality is not yet delivered MUST NOT be reported as green or as
a "green skip"; its state is not-yet-activated.

#### Scenario: Mid-stream milestone closure

- GIVEN milestone 2 closure (decoupling delivered; first-run and doctor not yet delivered)
- WHEN closure evidence is evaluated
- THEN all V2 scenarios for delivered functionality are green, no previously green scenario regressed, and undelivered-functionality scenarios are reported as not-yet-activated (not skipped-green)

#### Scenario: Milestone-4 full-suite gate

- GIVEN milestone 4 closure
- WHEN acceptance is evaluated
- THEN the complete V2 suite (all scenarios) is green, alongside V1 and the three project suites

#### Scenario: No silent smoke redefinition

- GIVEN any milestone where full V2 cannot be green yet
- WHEN closure is recorded
- THEN the evidence retains a truthful full-suite result and the gate interpretation applied, with no disabled or renamed-away scenarios

(Resolved maintainer decision 2026-09-30, Engram topic
`sdd/at-11-instalable/v2-gate`; binding. The proposal's gate-tension
escalation requirement is satisfied by this resolution.)

### Requirement: V3 human acceptance gate

Final acceptance includes a manual, timed clean-machine UAT (V3): a human
follows only the published documentation on a clean machine, times the
installation, and records every friction point found as an issue or doc-fix.
V3 requires human sign-off before merge and is outside the automated
evidence loop; autonomous completion never substitutes for it. Scenario #9
(timed human UAT) and the final health checks retain this V3 obligation.
The under-ten-minute target is retained, with both timing readings recorded
for the human checklist: RNF-AT-11-3 measures the installation with the host
already present, while the UAT scenario's wording includes installing host +
plugin + brain within the window.

#### Scenario: Signed checklist precedes merge

- GIVEN all automated evidence is green
- WHEN merge readiness is evaluated
- THEN a completed, signed V3 checklist with recorded timing and friction exists

#### Scenario: Friction becomes tracked work

- GIVEN a V3 run where the tester hits friction
- WHEN the checklist is signed off
- THEN each friction point is recorded as an issue or doc-fix candidate

#### Scenario: Timing readings documented

- GIVEN the V3 checklist document
- WHEN it is written
- THEN it states both timing readings (host-preinstalled RNF-AT-11-3 vs host-included UAT scenario) so the human gate is unambiguous

(PRD anchor: "Escenario: UAT cronometrado como usuario real" [RNF-AT-11-3,
US-AT-11-1] [V3]; "Escenario: Salud final post-asistente" [V2+V3].)

### Requirement: Honest evidence claims

Recorded evidence MUST NOT claim quality tooling that does not exist (no
coverage, lint, or type-check claims for this workspace), MUST NOT substitute
hermetic mocks for the full user-installation proof where V2 requires real
evidence, and MUST NOT report V2 green for network-dependent checks without
the explicit authorization required to execute them. Blocked prerequisites
are recorded as blocked evidence with what was missing.

#### Scenario: Evidence report distinguishes levels

- GIVEN milestone closure evidence
- WHEN it is reviewed
- THEN it separates V1 results, V2 active-scenario results, stubbed checks, and blocked items, and claims no coverage/lint/type-check tooling

#### Scenario: Unauthorized network execution not claimed green

- GIVEN a V2 scenario requiring documented network origins that were not authorized in the execution environment
- WHEN the run ends
- THEN the scenario is recorded as blocked pending authorization, never green

(PRD anchor: verification principle; proposal Verification Contract and
Out-of-Scope network constraints.)

## Recorded Clarifications (not resolved in this spec)

- **V3 timing wording.** RNF-AT-11-3 (< 10 minutes with host already
  present) and the UAT scenario (< 10 minutes installing host + plugin +
  brain) differ. Both readings belong to the human V3 checklist; this spec
  does not pick one. See the V3 requirement above.
- The audit document (`docs/prds/AT-11-auditoria-instalacion.md`) is defect
  evidence for traceability, not a substitute for current source inspection;
  tasks and apply phases verify audit claims against current files.
