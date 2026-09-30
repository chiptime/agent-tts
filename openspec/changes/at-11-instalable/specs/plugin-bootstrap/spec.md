# plugin-bootstrap — Delta for at-11-instalable

**Canonical archive target:** `hosts/herdr/tts-plugin/openspec/specs/plugin-bootstrap/spec.md`
(plugin subproject registry). This delta MUST be archived into that file by
`sdd-archive`. It MUST NOT be archived into a new root
`openspec/specs/plugin-bootstrap/spec.md` copy — the workspace root registry
stays empty of plugin capabilities.

**Change context:** AT-10 moved the Python package into the monorepo
(`engine/`), which broke the bootstrap's dev mode (audit B5) and left the
pinned install source anchored at the old standalone repository root. This
delta replaces the hardcoded development checkout with location-derived
`engine/` installation and re-anchors immutable sources to the monorepo
package subdirectory. The uv/Python fallbacks, immutable-pinning safeguard,
upgrade path, and checkout independence are retained.

## MODIFIED Requirements

### Requirement: agent-tts immutable pin

The bootstrap MUST install agent-tts from an immutable ref anchored at the
monorepo Python package subdirectory `engine/`: a tag when agent-tts
publishes tags, otherwise a full commit SHA, with the install source pointing
at the `engine/` package subdirectory (not the repository root). Unpinned
mutable sources (bare `git+https://…agent-tts.git`) are prohibited.
(Previously: pinned the standalone agent-tts repository root, which no
longer contains the Python package.)

#### Scenario: Pinned install source

- GIVEN a fresh hermetic env with a recorder pip stub
- WHEN bootstrap installs agent-tts
- THEN the recorded install source references the pinned tag or full SHA anchored at the monorepo `engine/` package subdirectory, never bare `main` and never the repository root

#### Scenario: Installed package imports from the monorepo layout

- GIVEN a completed bootstrap against the pinned monorepo source
- WHEN the venv interpreter imports `agent_tts`
- THEN the import succeeds from the installed `engine/` package

### Requirement: Dev-gated editable checkout

The editable install of agent-tts MUST run only when `HERDR_TTS_DEV=1` is
explicitly set. When set, the bootstrap MUST locate the monorepo's `engine/`
directory by deriving it from the bootstrap script's own checkout location
(the containing monorepo checkout), never from a hardcoded personal path such
as `~/Code/personal/agent-tts`. Without `HERDR_TTS_DEV=1`, the bootstrap
SHALL ignore any local checkout even when one exists.
(Previously: hardcoded `~/Code/personal/agent-tts` as the dev checkout,
which broke under the monorepo layout — audit B5.)

#### Scenario: Public install ignores the dev checkout

- GIVEN a hermetic HOME containing a decoy agent-tts monorepo checkout and `HERDR_TTS_DEV` unset
- WHEN bootstrap runs
- THEN the recorded install uses the pinned remote source and never references the decoy path

#### Scenario: Explicit dev opt-in derives engine from the checkout

- GIVEN a local agent-tts monorepo checkout at an arbitrary, non-canonical path and `HERDR_TTS_DEV=1` set
- WHEN bootstrap runs
- THEN agent-tts installs editable from the `engine/` directory derived from the checkout's own location
- AND the recorded install command contains no personal hardcoded path

#### Scenario: Dev opt-in without a discoverable engine fails actionably

- GIVEN `HERDR_TTS_DEV=1` set but no monorepo `engine/` directory discoverable from the bootstrap location
- WHEN bootstrap runs
- THEN it exits non-zero with an actionable English message naming the missing `engine/` checkout

## Retained (unchanged by this delta)

The following existing requirements are preserved as-is and MUST survive
archival without edits: **uv venv pip seeding**, **python3-only fallback**,
**Upgrade-capable re-run**, and **Checkout-location agnostic**.

## REMOVED Requirements

- None.

## RENAMED Requirements

- None.
