# plugin-bootstrap Specification

## Purpose

Correctness of `scripts/bootstrap.sh` — the plugin `[[build]]` hook, also re-used by the auto-bootstrap path — so fresh installs succeed on uv-only and python3-only machines, agent-tts installs reproducibly from an immutable ref, and dev-machine state never leaks into public installs.

## Requirements

### Requirement: uv venv pip seeding

The bootstrap MUST guarantee a usable pip entrypoint inside uv-created venvs, by seeding the venv at creation or installing via `uv pip` against the venv interpreter. It MUST NOT invoke a venv `bin/pip` that the creation tool did not guarantee to exist.

#### Scenario: Fresh uv-only machine

- GIVEN a hermetic env where `uv` exists, `python3 -m venv` is unavailable, and the `uv` stub creates a pip-less venv unless seeded
- WHEN bootstrap runs
- THEN the venv ends with a working pip path (seeded venv or `uv pip` equivalent)
- AND agent-tts installs successfully and bootstrap exits 0

### Requirement: python3-only fallback

When `uv` is absent, the bootstrap SHALL create the venv with `python3 -m venv`. When neither `uv` nor `python3` exists, it MUST fail with an actionable English message naming the missing prerequisite.

#### Scenario: Fresh python3-only machine

- GIVEN a hermetic env with `python3` present and `uv` absent
- WHEN bootstrap runs
- THEN the venv is created, agent-tts installs, and bootstrap exits 0

#### Scenario: No python tooling

- GIVEN a PATH with neither `uv` nor `python3`
- WHEN bootstrap runs
- THEN it exits non-zero with an actionable message and leaves no partial venv behind

### Requirement: agent-tts immutable pin

The bootstrap MUST install agent-tts from an immutable ref: a tag when agent-tts publishes tags, otherwise a full commit SHA. Unpinned mutable sources (bare `git+https://…agent-tts.git`) are prohibited.

#### Scenario: Pinned install source

- GIVEN a fresh hermetic env with a recorder pip stub
- WHEN bootstrap installs agent-tts
- THEN the recorded install source references the pinned tag or full SHA, never bare `main`

### Requirement: Dev-gated editable checkout

The editable install from `~/Code/personal/agent-tts` MUST run only when `HERDR_TTS_DEV=1` is explicitly set. Without it, the bootstrap SHALL ignore that directory even when it exists.

#### Scenario: Public install ignores the dev checkout

- GIVEN a hermetic HOME containing a decoy `~/Code/personal/agent-tts` and `HERDR_TTS_DEV` unset
- WHEN bootstrap runs
- THEN the recorded install uses the pinned remote source and never references the decoy path

#### Scenario: Explicit dev opt-in

- GIVEN the local checkout exists and `HERDR_TTS_DEV=1` is set
- WHEN bootstrap runs
- THEN agent-tts installs editable from the local checkout

### Requirement: Upgrade-capable re-run

The bootstrap SHALL provide a deliberate upgrade path that bypasses its never-upgrade gate: when invoked for upgrade, it MUST refresh agent-tts to the pinned ref even though a healthy venv with a working `import agent_tts` already exists.

#### Scenario: Re-run upgrades agent-tts

- GIVEN a healthy venv where `import agent_tts` already succeeds
- WHEN bootstrap re-runs in upgrade mode
- THEN an upgrade install for the pinned ref is recorded and bootstrap exits 0

### Requirement: Checkout-location agnostic

The bootstrap MUST NOT expect the plugin checkout at any specific path; behavior SHALL be identical for any clone location, managed install or linked dev checkout.

#### Scenario: Arbitrary clone location

- GIVEN the plugin checkout at a non-canonical directory inside the hermetic HOME
- WHEN bootstrap runs
- THEN it completes with the same result as from the canonical location
