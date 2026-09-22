# Proposal: PM-01 — One-Line Install & Packaging

## Intent

Fresh installs are fragile and non-reproducible: `bootstrap.sh` installs `agent-tts` from mutable `main` (or a developer's personal checkout), a uv-created venv aborts the host's `[[build]]` (no seeded pip → install-fatal on Path A), `jq` is unguarded, no git tags exist to pin, and there is no documented uninstall story. Goal: make the recommended channel (`herdr plugin install chiptime/herdr-tts`) reliably reproducible and give non-registry users a guarded curl|sh path.

## Scope

### In Scope
- **Prerequisite fixes (research-mandated)**: `uv venv --seed` (or `uv pip install` fallback) in `bootstrap.sh`; agent-tts pinned to an immutable ref; `jq` guard in installer preflight; linked-plugin detection (install refuses over a linked checkout — guide `plugin unlink`/`uninstall` on dev→public migration).
- `scripts/install.sh` (new): preflight (bash/git/herdr, `uv`|`python3`, `jq`), idempotent install + upgrade (re-run upgrades, bypassing the never-upgrade gate; remote mismatch aborts), keymap adoption, daemon verification, uninstall steps, English output.
- Versioning: manifest bump + first real tag; installer pins tags with an explicit `@main` escape hatch.
- README install-section rewrite: channels, pinning, uninstall, dev workflow.

### Out of Scope
- CI creation, Homebrew formula, npm package (future channels).
- Startup-hook contract migration — **logged follow-up**: host docs define `[[startup]]` as one-shot/should-exit; `_daemon-supervised` is tolerated mechanically today (future-host risk, not this change).
- agent-tts repo changes; live uv-machine test (design/verify task); keymap `unapply` subcommand.

## Decisions (stance + rationale)

| # | Decision | Stance |
|---|---|---|
| D1 | agent-tts pinning | Pin to immutable ref — tag preferred, full commit SHA fallback if agent-tts has no tags. Editable `~/Code/personal/agent-tts` shortcut only under explicit `HERDR_TTS_DEV=1`; never active for public installs. Rationale: every audited channel (uv SHA-256, brew policy, marketplace) pins immutable refs; the editable-path preference makes public installs environment-dependent. |
| D2 | Path B clone target | Non-dev users: installer clones to `~/.local/share/herdr-tts/plugin` (XDG). README keeps `~/Code/personal/herdr-tts` only as the dev/`plugin link` workflow. The coupling dissolves once D1 gates the shortcut: bootstrap no longer expects any particular clone location. |
| D3 | Keymap default | Installer MAY init+adopt `menu` style (binds one free core letter `u`, zero core collisions), with `--no-keymap` opt-out and a never-overwrite guarantee for an existing `keymap.json`; honors `HERDR_TTS_KEYMAP_FILE`/`HERDR_CONFIG_DIR`; then runs `herdr server reload-config`. Trust is acceptable: the daemon already auto-applies keymap at start (silent no-op without a file) and `apply` has backup/rollback/dry-run — the installer only crosses the "choose a map" line, with a collision-free choice. |
| D4 | Installer UX language | English output. It is the adoption-facing first touchpoint and matches the English README/code convention; Spanish remains for plugin CLI strings. |
| D5 | Tags/backfill | Reconcile drift by releasing current main as v0.16.0 (manifest 0.15.0 lags v0.16-era features already shipped), cut tag `v0.16.0`, no retroactive backfill. Tags are the pin source of truth; `HERDR_TTS_REF=main` / `@main` URL documented as escape hatch only. |

## Uninstall Story

**Documented manual steps (README + printed at install end) — not an `--uninstall` mode.** Host `plugin uninstall` already removes checkout + registration (Lane A). Remaining user steps: stop the daemon, remove the venv under `~/.local/share/herdr-tts`, remove the managed keymap block (markers make hand-removal safe). Justification: duplicating host uninstall inside a curl|sh script adds trust surface and line budget without user value.

## Channel Priority

`herdr plugin install` (GitHub) stays the headline. `install.sh` is the non-registry fallback, tag-pinned once D5 lands. npm bin-stub wrapper and Homebrew tap explicitly out of scope (research: bin-stub-on-first-run is the npm-doc-aligned shape; brew core requires tagged, checksummed release tarballs).

## Capabilities

> Contract with sdd-spec. `openspec/specs/` is currently empty — no existing capability names.

### New Capabilities
- `plugin-bootstrap`: bootstrap correctness — uv venv seeding, immutable agent-tts ref, dev-only shortcut flag, upgrade-capable re-run.
- `installer`: install.sh behavior — preflight (incl. jq), idempotency/upgrade, keymap policy, uninstall steps, English output, tag pinning + `@main` hatch.

### Modified Capabilities
None (no main specs exist yet).

## Approach

Sequence: (1) bootstrap + pin fixes with smoke scenarios first (unblocks Path A immediately); (2) manifest bump + tag; (3) `install.sh` defaulting to the tag-pinned URL; (4) README rewrite. Strict TDD per config: failing smoke scenarios before implementation.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `scripts/bootstrap.sh` | Modified | `--seed`/uv-pip fix, pinned ref, dev flag (~25 ln) |
| `scripts/install.sh` | New | installer (~180–220 ln) |
| `herdr-plugin.toml` | Modified | version → 0.16.0 (1 ln) |
| `README.md` | Modified | install section rewrite (~70–90 ln) |
| `scripts/smoke-tests.sh` | Modified | bootstrap + installer scenarios, strict TDD (~100–140 ln) |
| `bin/herdr-tts` | Untouched | jq guard lives in preflight; `daemon_keymap_autostart` reused as-is |

**Changed-lines estimate: ~380–470 (midpoint ~420) → 400-line budget risk: Medium.** The tasks phase must re-forecast; if it lands High, split into two slices: bootstrap+manifest (~150 ln) then installer+README (~270 ln).

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Choosing a keymap for users erodes trust | Med | never-overwrite, `--no-keymap`, collision-free `menu` style, inherited backup/rollback |
| `@main` hatch keeps a mutable-ref window | Med | default is tag; hatch documented as escape only |
| uv branch unverified live | Med | mandatory uv-machine test in design/verify |
| macOS unverified (`/proc` degradation) | Low | honest platform messaging; no new macOS claims |
| agent-tts lacks tags (external repo) | Med | pin full commit SHA fallback |

## Rollback Plan

All changes are additive or single-commit revertable: delete `install.sh`; `git revert` the bootstrap/manifest commits; do not push the tag until verified. Keymap adoption is reversible via `apply`'s timestamped backups; users roll back plugins via `plugin uninstall` / reinstall `--ref <old-tag>`.

## Dependencies

- agent-tts (external repo) must expose a tag or a fixed SHA to pin.
- Tag `v0.16.0` must exist before the installer defaults to a tag-pinned URL.

## Success Criteria

- [ ] Fresh uv-only and python3-only machines complete `herdr plugin install` (bootstrap no longer install-fatal).
- [ ] Public installs never touch `~/Code/personal/agent-tts` (proven by a smoke scenario).
- [ ] `install.sh` re-run upgrades agent-tts (deliberately bypasses the never-upgrade gate); remote mismatch aborts.
- [ ] `--no-keymap` leaves zero keymap artifacts; default adoption never overwrites an existing keymap.
- [ ] `v0.16.0` tag exists; installer default pinned to it; `@main` hatch and uninstall steps documented.
- [ ] `bash scripts/smoke-tests.sh` green (487 existing + new assertions).
