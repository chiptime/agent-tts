# installer Specification

## Purpose

Behavior of `scripts/install.sh`, the non-registry curl|sh fallback: preflight, install/upgrade idempotency, keymap policy, uninstall guidance, English output, tag pinning with a mutable-ref escape hatch.

## Requirements

### Requirement: Preflight before mutation

The installer MUST verify bash, `git`, `jq`, `herdr`, and (`uv` or `python3`) before mutating anything, failing in English naming the missing tool. It MUST refuse install over a linked plugin checkout, guiding `plugin unlink`/`plugin uninstall` (dev→public migration).

#### Scenario: jq missing fails clean

- GIVEN a hermetic PATH with `git`, `python3`, and `herdr` stubs but no `jq`
- WHEN the installer runs
- THEN it exits non-zero naming `jq`
- AND no clone target, venv, or keymap artifact exists

#### Scenario: Linked checkout refusal

- GIVEN a linked dev checkout is registered
- WHEN the installer runs
- THEN it exits non-zero guiding `plugin unlink`/`plugin uninstall`, mutating nothing

### Requirement: Fresh install

With no prior install, the installer SHALL place the checkout at `~/.local/share/herdr-tts/plugin`, run the bootstrap, verify the daemon came up, and print the `--status` pointer.

#### Scenario: Fresh end-to-end install

- GIVEN a hermetic env with `git`, `uv`, `jq`, `herdr` stubs and empty XDG dirs
- WHEN the installer runs
- THEN checkout, venv bootstrap, and daemon verification succeed in order and the status pointer prints

### Requirement: Idempotent re-run and remote guard

A re-run SHALL distinguish fresh vs upgrade by target-dir existence plus git remote match; a matching remote upgrades in place, refreshing agent-tts beyond the never-upgrade gate; a mismatched remote MUST abort cleanly.

#### Scenario: Matching remote upgrades

- GIVEN an existing target dir whose git remote matches the canonical URL
- WHEN the installer re-runs
- THEN the checkout refreshes and agent-tts is upgraded to the pinned ref

#### Scenario: Mismatched remote aborts

- GIVEN an existing target dir whose git remote differs from the canonical URL
- WHEN the installer re-runs
- THEN it exits non-zero naming the mismatch, writing nothing

### Requirement: Keymap adoption policy

The installer MAY initialize and adopt the collision-free `menu` style. It MUST NOT overwrite an existing `keymap.json`, MUST honor `HERDR_TTS_KEYMAP_FILE`/`HERDR_CONFIG_DIR`, SHALL run `herdr server reload-config` after adoption, and MUST offer a `--no-keymap` opt-out.

#### Scenario: Default adoption is safe

- GIVEN a hermetic config dir with no `keymap.json`
- WHEN the installer runs with defaults
- THEN the `menu`-style keymap is adopted, `reload-config` runs, and the managed block lands in the resolved config

#### Scenario: Never overwrite an existing keymap

- GIVEN `keymap.json` exists at the resolved keymap path
- WHEN the installer runs with defaults
- THEN the existing file remains byte-identical

#### Scenario: --no-keymap leaves zero artifacts

- GIVEN a fresh hermetic config dir
- WHEN the installer runs with `--no-keymap`
- THEN no `keymap.json`, managed keymap block, or keymap/apply invocation is recorded

### Requirement: Uninstall guidance

The installer MUST print complete manual uninstall steps at install end: stop the daemon, remove the venv under `~/.local/share/herdr-tts`, remove the managed keymap block. The README MUST document the same steps.

#### Scenario: Printed uninstall steps are complete

- GIVEN a successful install run
- WHEN its output is captured
- THEN it contains the daemon-stop, venv-removal, and managed-keymap-block-removal steps

### Requirement: English output

All installer output (progress, success, failure) MUST be English.

#### Scenario: English failure output

- GIVEN a preflight failure
- WHEN the installer exits non-zero
- THEN the output contains no Spanish strings

### Requirement: Tag-pinned default with escape hatch

The installer MUST default to the tag-pinned source (`v0.16.0` or later) and MUST honor an escape hatch (`HERDR_TTS_REF` or an `@main` URL) targeting a mutable ref.

#### Scenario: Default pins the tag

- GIVEN default env with no `HERDR_TTS_REF`
- WHEN the installer runs
- THEN every recorded source/URL references the pinned tag

#### Scenario: Escape hatch targets main

- GIVEN `HERDR_TTS_REF=main` (or an `@main` URL)
- WHEN the installer runs
- THEN the recorded source/URL references `main`
