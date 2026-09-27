# PM-01 Explore — One-Line Install & Packaging (herdr-tts)

> **Change:** pm-01-install-packaging · **Phase:** explore · **Date:** 2026-09-22
> **Provenance:** investigation performed by the `explore` agent (read-only handoff), transcribed to the openspec store by the orchestrator. Engram mirror: `sdd/pm-01/explore`.
> **Status:** exploration complete — feeds the proposal phase.

---

## Ground truth (current install reality)

**Repo shape** (all verified): `bin/herdr-tts` (5075 lines, bash monolith), `lib/tts_engine.py` (55-line bridge → `agent_tts` pip package), `scripts/bootstrap.sh` (39 lines), `scripts/smoke-tests.sh` (2128 lines, hermetic, 30 scenarios / 487 assertions), `herdr-plugin.toml` (196 lines), README (English) + Spanish `docs/`. No CI, no `.github/`, no CHANGELOG, no git tags.

**Path A — `herdr plugin install chiptime/herdr-tts`** (README, "Recommended"):
- `herdr-plugin.toml` declares `id = "herdr.tts"`, `version = "0.15.0"`, `min_herdr_version = "0.7.0"`, `platforms = ["linux","macos"]`.
- `[[build]] command = ["bash", "scripts/bootstrap.sh"]` — the host runs this after obtaining the repo (clone mechanics are host-side, not in this repo — see open question #1).
- `bootstrap.sh` in full:
  - Venv at `${XDG_DATA_HOME:-~/.local/share}/herdr-tts/venv`.
  - Idempotency gate: exits 0 if venv python exists **and** `import agent_tts` succeeds → **never upgrades an existing agent-tts**.
  - Creates venv with `uv venv` if present, else `python3 -m venv`, else hard error (Spanish).
  - **Dev-machine leakage:** prefers an *editable install* from `~/Code/personal/agent-tts` if that dir exists; otherwise installs unpinned `git+https://github.com/chiptime/agent-tts.git` (whatever `main` is at install time — no lock).
- `[[startup]]` runs `bin/herdr-tts _daemon-supervised` — foreground watchdog relaunching `_daemon` on unplanned death (5s backoff, stop-flag coordination, `daemon.log`).
- **Resilience:** `bin/herdr-tts` auto-runs `scripts/bootstrap.sh` on any invocation if the venv python is missing (self-heals on first start after python appears).

**Path B — `git clone + herdr plugin link`**: same build hook never runs automatically; the auto-bootstrap at script top fires on first CLI/daemon invocation. Clone location is user-chosen (README suggests `~/Code/personal/herdr-tts` — exactly the path bootstrap's local-agent-tts shortcut expects; a coupling to resolve).

**Failure points per path**: no network → clone/pip-from-git fails; no `python3` and no `uv` → explicit Spanish error; Debian/Ubuntu `python3 -m venv` requires `python3-venv` (ensurepip) — unhandled; **`uv venv` does not seed `pip` by default** while bootstrap then calls `${VENV_DIR}/bin/pip` — latent breakage on fresh uv-created venvs (needs live test); existing-but-broken venv → repair path works; existing healthy venv → stale agent-tts silently kept.

**Post-install manual steps for a new user**:
1. Keymap: `keymap init → (adopt --style) → check → apply → herdr server reload-config`. Apply is production-grade: managed block, byte-preserving, timestamped backups (last 3), atomic write, post-write `herdr config check` with automatic rollback, `--dry-run`, idempotent.
2. **Keymap already auto-applies at every daemon start** (`daemon_keymap_autostart`): silent no-op when `keymap.json` missing, never auto-inits; hard errors refuse without touching config; shadow warnings non-fatal. The plugin has already decided startup-time auto-apply is safe — an installer adding `adopt`+`apply` only crosses the "choose a map for the user" line.
3. `config.env` is **not created at install**; zero-config defaults work (edge provider, no keys). Created lazily by `save_config`/`config_set`.
4. ntfy topic: fully optional, manual; topic is the bearer credential.
5. `herdr server reload-config`: **only printed as a hint** — the plugin never runs it.
6. Optional: `fzf` (palette only), `agent-tts voice install kokoro` (~325 MB, explicit decision).

**Dependency guards found**: `setsid` (nohup fallback), `tput`, `fzf`, `herdr`. **`jq` has NO guard** despite heavy use in keymap and watcher — silent breakage on machines without jq.

## What a one-line installer must wrap (ordered steps + safety notes)

1. **Preflight**: bash, `git`, (`uv`|`python3`), `jq`, `herdr` presence; detect WSL2/Linux/macOS. Fail with actionable messages before mutating anything.
2. **Obtain repo**: if the host's `herdr plugin install` already clones + runs `[[build]]`, the installer's clone step is redundant — its real added value is steps 4–6. Otherwise clone to a stable target (`~/.local/share/herdr-tts/plugin` suggested).
3. **Venv bootstrap**: **reuse `scripts/bootstrap.sh` as-is** (idempotent, self-healing). Upgrade path must go beyond it (bootstrap never upgrades agent-tts): upgrade = `git pull --ff-only` + `pip install -U` + daemon restart. Fix before shipping: pin agent-tts (tag/ref) and decide the `~/Code/personal/agent-tts` precedence for non-dev machines.
4. **Keymap** (safety-critical): acceptable to auto-run **only if**: never overwrite an existing `keymap.json`, use a collision-free style (`menu` binds one free core letter `u`; `direct` shadows core keys — never choose it for users), honor `HERDR_TTS_KEYMAP_FILE`/`HERDR_CONFIG_DIR`, offer `--no-keymap` opt-out, rely on `daemon_keymap_autostart` or call `apply` (backup+rollback+dry-run meets the safety bar), then `herdr server reload-config`. Creating the keymap *file* during install is a new decision the proposal must own.
5. **Config**: skip. Defaults are zero-config by design; print the 2–3 knobs instead.
6. **ntfy**: skip or interactive-only prompt (credential hygiene). Never generate/persist a topic silently.
7. **Daemon**: verify `_daemon-supervised` came up (pidfile) and print `--status` pointer.
8. **Idempotency**: re-run detection via target-dir git remote match → upgrade path; healthy venv+import → bootstrap no-ops; existing keymap → untouched; fresh vs upgrade distinguished by dir-exists + remote-match; mismatched remote → abort with message.

## Channel feasibility (verdict + caveats)

- **Homebrew formula — not primary (weak fit)**: the product is a git-sourced plugin whose build is a network pip install from `git+https://` — hostile to brew's bottle/audit model; brew prefix doesn't match where the host expects plugin roots. Viable only later as a convenience formula wrapping a tagged tarball.
- **Thin npm wrapper — viable, low value per effort (good for discoverability)**: zero binaries, so the package can only delegate (postinstall→curl|sh is a security smell; consider a `bin` stub that installs on first run). Real value = search placement.
- **Herdr plugin registry — the actual primary channel, but half-external**: manifest id is `herdr.tts`; the install arg is `chiptime/herdr-tts` (owner/repo GitHub shorthand). Whether Herdr resolves that from GitHub or a registry index cannot be answered from this repo (host is a separate project). What the repo owes: real semver in `version`, `min_herdr_version` discipline, pinned dependency installs.
- **curl|sh — viable with guardrails (good default for non-registry users)**: placement `scripts/install.sh` served via `raw.githubusercontent.com/chiptime/herdr-tts/<ref>/scripts/install.sh` or a stable vanity URL. Caveats: no tags exist → today the only pin is `main` (mutable); no checksum/signing story; mitigate by printing each step, pinning to tags once they exist, offering dry-run, keeping `herdr plugin install` as the headline.

## Versioning & release mechanics findings

- **No git tags** (only `heads/main`), **no CHANGELOG**, no CI.
- **Versions live in commit messages** (v0.9.0 … v0.15.0) plus one explicit "bump plugin to 0.8.0". 59 commits, ~1 week of history.
- **Manifest drift**: `herdr-plugin.toml version = "0.15.0"` but code comments reference v0.16-era features landed after the last versioned commit — manifest lags main.
- **README's v-numbers are feature-scoped, not global** (v3/v3.1 = dashboard revisions; v0.11/v0.12 = palette/menu). No single version story, **no `--version` flag**.
- **The installer can pin only `main` today.** Proposal should introduce tags cut from the manifest version as source of truth, pin installers to tags with a `@main` escape hatch.

## Open questions the proposal must answer

1. What does the Herdr host actually do? (clone where? re-run `[[build]]` on update? update command? is there a plugin uninstall?) — host-side, blocks installer design; install.sh may only need to wrap keymap/reload if the host already handles clone+build.
2. Does `uv venv` + `${VENV_DIR}/bin/pip` actually work on a fresh machine? (uv venvs don't seed pip by default.) Needs live test; fix trivial (`uv venv --seed` or `uv pip install`).
3. Pin agent-tts how? Tag/ref/lock; should the `~/Code/personal/agent-tts` editable shortcut stay or move behind an env flag for public installs?
4. Clone target for Path B installs: keep `~/Code/personal/herdr-tts` (collides with dev-shortcut path) or move to an XDG dir?
5. Keymap default policy: adopt `menu` by default, only `init`, or prompt? Opt-out flag name?
6. Installer UX language: English (adoption-facing) vs bilingual vs Spanish-strings convention?
7. Upgrade semantics: installer re-run vs `herdr-tts --upgrade` subcommand vs documented `git pull`? Bootstrap's never-upgrade gate needs a deliberate counterpart.
8. Tag/backfill strategy: cut v0.15.x-style tags retroactively, or ride `main` until first tagged release?
9. macOS claims: manifest says macOS but daemon lifecycle uses `/proc` — verification matrix (WSL2/Linux/macOS) and honest platform messaging.
10. Checksum/signing for curl|sh (or accept raw-URL HTTPS trust model), and npm wrapper: postinstall vs first-run.

## Risks

| Sev | Risk |
|---|---|
| HIGH | Unpinned dependency chain: agent-tts from mutable `main` over network + mutable `main` install refs + no checksums → every fresh install is a supply-chain roll of the dice. |
| HIGH | No uninstall/rollback story: no documented `herdr plugin uninstall`; installer must define removal (dir + venv + managed keymap block + daemon stop). |
| MEDIUM | `uv venv` pip gap (bootstrap uses `${VENV_DIR}/bin/pip` on a venv uv didn't seed) — plausible fresh-install breakage; verify then fix. |
| MEDIUM | Missing `jq` guard: keymap/watcher use jq with no `command -v` check; fails cryptically on minimal systems — preflight must catch it. |
| MEDIUM | `python3 -m venv` on Debian/Ubuntu needs `python3-venv`; bootstrap's error won't say so. |
| MEDIUM | Keymap auto-apply at install: mechanics are safe (backup/rollback/dry-run precedent) but choosing a style *for* the user is a trust decision — collision-free style + opt-out + never-overwrite required. |
| MEDIUM | Dev-machine leakage: `~/Code/personal/agent-tts` editable preference makes public installs environment-dependent. |
| LOW | Manifest version drift (0.15.0 vs v0.16-era main) + README doc drift ("edge-tts and miniaudio" vs actually installing agent-tts). |
| LOW | macOS `/proc` degradation in daemon pidfile/liveness paths (fail-open, diagnostics weaken). |
| LOW | No CI: installer changes only covered by the hermetic suite, which has **no bootstrap scenario at all**. |

## Key files referenced

`herdr-plugin.toml`, `scripts/bootstrap.sh`, `bin/herdr-tts` (L1–43, L156–260, L1842–1860, L2046, L2123–2168, L4271–4436, L4930–5075), `lib/tts_engine.py`, `scripts/smoke-tests.sh`, `README.md` (install/keymap sections), `.git/refs` (versioning evidence), `openspec/config.yaml`.
