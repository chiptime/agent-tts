# PM-01 Research — Evidence Envelope

> **Change:** pm-01-install-packaging · **Phase:** research · **Date:** 2026-09-22
> **Provenance:** evidence collected by delegated research executors (`explore` agent substitution, agy-quota workaround), transcribed to the openspec store by the orchestrator. Engram mirror: `sdd/pm-01/research`.
> **Capability declaration (honest):** research executors had webfetch + read/grep/glob but NO shell; Lane B live tests were run by the orchestrator (bash) on the dev machine and are marked as such. Research status: **done for Lanes A and C; B resolved by source + partial live verification.**

---

## Lane A — Herdr host plugin lifecycle

Source of record: official docs, version **0.9.1** (herdr.dev/plugins.mdx, marketplace.mdx, cli-reference.mdx — "Edit page" links point to `github.com/herdrdev/herdr/blob/master/docs/versions/0.9.1/website/src/content/docs/*.mdx`). Host repo: `herdrdev/herdr` (`github.com/chiptime/herdr` is 404 — the host is a large independent project, 39,853 stars per herdr.dev).

**(1) Where does the host clone the plugin?**
Clones with `git` and stores the checkout **under a Herdr-managed directory**; the exact filesystem path is **UNANSWERED in public docs**. Quotes: "It clones with `git`, shows a preview in interactive terminals, runs supported build commands, then stores the checkout under Herdr-managed plugin data and registers it" (https://herdr.dev/docs/plugins/). Runtime escape hatch: `HERDR_PLUGIN_ROOT` = "the installed or linked plugin directory", injected into plugin processes. `herdr plugin config-dir <id>` prints the **config** dir (separate from the checkout, https://herdr.dev/docs/cli-reference/). `plugin list --json` plausibly includes the checkout path — inference, unverified.

**(2) Update semantics — reinstall re-runs `[[build]]`**
**No `plugin update` exists in v1**: "There is no separate `plugin update` in v1; reinstall from GitHub to refresh a managed plugin." "Reinstalling a GitHub-managed plugin replaces that managed checkout"; "Build commands run during GitHub `plugin install` after confirmation and before Herdr registers the plugin. If a build command fails, install aborts and the plugin is not registered" — **update = reinstall, and `[[build]]` re-runs every time** (all: https://herdr.dev/docs/plugins/). Also: "`plugin link` does not run build commands"; "changing `herdr-plugin.toml` after the install preview aborts install."

**(3) Uninstall/rollback — both exist host-side**
" `plugin uninstall <id-or-source>` unregisters the plugin. For GitHub-managed installs it also removes the managed checkout, and it accepts either the plugin id or the same `owner/repo[/subdir...]` shorthand used by install. `plugin unlink <id>` only unregisters and leaves files alone." Rollback: reinstall with `--ref <old-tag>` ("pin `--ref` when you want a specific revision", CLI reference).

**(4) Resolution — directly from GitHub; marketplace is discovery-only**
"plugin install accepts GitHub shorthand only, such as `owner/repo/subdir`" (https://herdr.dev/docs/cli-reference/). The marketplace (herdr.dev/plugins, 1,266 plugins) is an **unreviewed automatic index** of public repos tagged `herdr-plugin` with a parseable `herdr-plugin.toml`, refreshed every 30 min and on default-branch head change (https://herdr.dev/docs/marketplace/). "Discovery is automatic and unreviewed. A listing means a repository tagged itself, not that Herdr vetted it."

**(5) Manifest version semantics**
Top-level `id`, `name`, `version`, `min_herdr_version` are **required**; "Set `min_herdr_version` to the oldest Herdr version that supports the plugin APIs… **Herdr refuses to link or install a plugin when its minimum version is newer than the current binary**" (https://herdr.dev/docs/plugins/). Marketplace excludes "malformed manifest metadata" and records "`id`, `name`, `version`, `platforms`, `min_herdr_version` together with the exact default-branch commit." Whether `version` must be strict semver is **not specified** — partially open.

**Bonus findings relevant to the proposal:**
- Trust model: install previews "the source and the commands it will run" interactively; `--yes` skips review. Our `[[build]]` (network pip) will be visible in that preview.
- **Startup-hook contract mismatch (new):** docs say `[[startup]]` commands "run once for each enabled plugin after Herdr restores the session… **Startup hooks are one-shot initialization commands rather than supervised daemons**. A hook should… **exit**." They run async and "a startup failure does not stop the server" — `_daemon-supervised` is tolerated mechanically but contradicts the documented contract (future-host-version risk). https://herdr.dev/docs/plugins/
- "Installing over a locally linked plugin is refused; unlink or uninstall the local plugin first" — directly relevant to dev-machine ↔ public-install migration.
- Install/link work while no server runs; plugin state is global to the user across sessions.

## Lane B — Bootstrap verification

**Source-verified (research executors) + live-tested (orchestrator bash, dev machine):**

**(1) Fresh `uv venv` does NOT seed `pip` — by design.** uv's venv creator: `pub enum Seed { Enabled, #[default] Disabled }` — https://raw.githubusercontent.com/astral-sh/uv/main/crates/uv-virtualenv/src/virtualenv.rs. Created venvs contain only interpreter symlinks + activate scripts; no `bin/pip` unless `--seed`. Consequence in `scripts/bootstrap.sh`: with uv present, `uv venv "$VENV_DIR"` creates a pip-less venv, then `"${VENV_DIR}/bin/pip"` (L33/36) → "No such file or directory" under `set -euo pipefail` → non-zero exit → **the host aborts the whole `herdr plugin install`** (Lane A: build failure aborts install). The re-run gate (L10–12) skips creation once `bin/python` exists → cannot self-heal.
**Doc-backed fixes:** (a) `uv venv --seed`; (b) drop `bin/pip` and use **`uv pip install`** ("swapping out `pip install` for `uv pip install` should 'just work'" — https://docs.astral.sh/uv/pip/compatibility/; targets the venv via `--python "$VENV_DIR/bin/python"` — https://docs.astral.sh/uv/pip/environments/).

**(2) Live tests (orchestrator, dev machine, 2026-09-22):**
- `uv`: **not installed** on this machine → the uv-branch live test remains **pending** for a uv-equipped machine (source evidence stands).
- `python3 --version` → Python 3.14.7; `python3 -m venv /tmp/opencode/pm01-venv` → **succeeded with `bin/pip` present** (cleaned up). The non-uv branch works on this system.

**(3) `python3 -m venv` without python3-venv on Debian/Ubuntu — confirmed via package metadata.** Ubuntu noble's `python3.12-venv` is a separate package whose dependencies are `python3-pip-whl (>= 22.2)` + `python3-setuptools-whl` — ensurepip's wheels ship **only** with the -venv package (https://packages.ubuntu.com/noble/python3.12-venv). Minimal Debian/Ubuntu → bootstrap L23 fails. Exact error text unverified-verbatim (wiki.debian.org unreachable from research session).

**(4) Dev-machine leakage — exact lines (`scripts/bootstrap.sh` L30–L37):**
```bash
LOCAL_AGENT_TTS="${HOME}/Code/personal/agent-tts"
if [[ -d "$LOCAL_AGENT_TTS" ]]; then
  echo "  Instalando agent-tts desde ${LOCAL_AGENT_TTS}..."
  "${VENV_DIR}/bin/pip" install --quiet -e "$LOCAL_AGENT_TTS"
else
  echo "  Instalando agent-tts desde GitHub..."
  "${VENV_DIR}/bin/pip" install --quiet git+https://github.com/chiptime/agent-tts.git
fi
```
Unpinned `git+https://…` (mutable `main`); editable install from a personal checkout when it exists; combined with the never-upgrade gate L10–12.

## Lane C — Distribution precedents

**(1) curl|sh installers in established tools**

| Tool | Entry URL | Pinning | Checksums/verification | Dry-run/review | Source |
|---|---|---|---|---|---|
| **rustup** | `https://sh.rustup.rs` (vanity) | `rustup-init` from `static.rust-lang.org`, versioned archive URLs | None in-script (TLS-only); confirmation prompt | prompts via /dev/tty when piped | fetched live: https://sh.rustup.rs |
| **uv (astral)** | `https://astral.sh/uv/install.sh` + **version-pinned vanity** ("include it in the URL: `curl -LsSf https://astral.sh/uv/0.12.17/install.sh \| sh`") | script regenerated per release, URLs pinned to release artifacts + GitHub fallback | **Embedded per-artifact SHA-256** verified by `verify_checksum()` via `sha256sum -b` | "may be inspected before use: `curl -LsSf … \| less`"; no --dry-run flag; env knobs | fetched live: https://astral.sh/uv/install.sh + docs |
| **nvm** | **tag-pinned raw URL** `https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh` | tag is the pin; script hardcodes latest version | none (tag immutability only) | none; idempotent re-run | fetched live: URL above |

Pattern takeaway: the mature default is *vanity domain serving a version-pinned script + embedded SHA-256 of release artifacts* (uv is the strongest model); "@main raw" is the weak end.

**(2) npm thin-wrapper precedent.** npm lifecycle docs (https://docs.npmjs.com/cli/v11/using-npm/scripts): "Don't use `install`… **The only valid use of `install` or `preinstall` scripts is for compilation which must be done on the target architecture.**" npm v7 removed uninstall lifecycle scripts; npm v11 ships `npm approve-scripts`/`deny-scripts` (install-script approval = security surface). Conclusion: a **`bin` stub that installs on first run** fits documented norms; `postinstall → curl|sh` contradicts them. (No named published package case-study verified — stands on npm's documented norms.)

**(3) Homebrew.** Policy (https://docs.brew.sh/Acceptable-Formulae): "An install step must not fetch code from a moving default branch or an unversioned, unchecksummed archive… must not resolve a moving or otherwise unreproducible dependency set… Release archives are preferred to Git checkouts… Software without a stable release… is not eligible for `homebrew/core`." A formula needs stable tagged tarball + `sha256`, homepage, SPDX license, `test do` block, pass `brew audit` (https://docs.brew.sh/Formula-Cookbook). herdr-tts **cannot** be core-eligible while its build is `pip install git+https://…` from mutable `main`; a user **tap** is the later low-friction route — policy-cited confirmation of the explore verdict.

## Answers to the 10 explore open questions

| # | Question | Status | Answer |
|---|---|---|---|
| 1 | What does the Herdr host actually do? | **Resolved** (checkout path UNANSWERED) | Clone via git into Herdr-managed checkout; no `plugin update` — reinstall replaces checkout + re-runs `[[build]]`; **`plugin uninstall` exists**; rollback = reinstall `--ref <tag>`; install refuses over a linked plugin |
| 2 | `uv venv` + `bin/pip` on fresh machine? | **Resolved by source** (uv live test pending) | No — `Seed::Disabled` default → no pip → bootstrap aborts → install aborted. Fix: `uv venv --seed` or `uv pip install`. python3-venv branch live-verified OK on dev machine |
| 3 | Pin agent-tts how? | Evidence complete, **decision open** | Every audited channel pins immutable refs (uv SHA-256, brew policy, marketplace records exact commit). Tag/lock agent-tts; dev-shortcut behind explicit flag |
| 4 | Clone target for Path B | **Still-open (product)** | New fact: Path A checkout is host-managed; only Path B dirs are user-placed — the `~/Code/personal` coupling is Path-B-only |
| 5 | Keymap default policy | **Still-open (product)** | n/a this run |
| 6 | Installer UX language | **Still-open (product)** | n/a this run |
| 7 | Upgrade semantics | **Resolved (constraints)** | No host update command; reinstall re-runs build → hits never-upgrade gate → stale agent-tts persists. Upgrade path must bypass/extend bootstrap deliberately |
| 8 | Tag/backfill strategy | **Still-open (product)**, evidence complete | Tags prerequisite for: version-pinned installer URLs, tag-pinned raw URLs, `--ref` rollback, any future brew formula. Until then only `main` is pinnable |
| 9 | macOS claims | **UNANSWERED this run** | No macOS/WSL environment available; `/proc` degradation risk stands |
| 10 | Checksum/signing + npm wrapper choice | **Resolved by precedent** | Embedded per-artifact SHA-256 is the established strong model (uv); signing: no mainstream installer signs its script. npm: **bin-stub-on-first-run** is the doc-aligned choice |

## Risks discovered during research

| Sev | Risk |
|---|---|
| **HIGH** | **Lane-B gap is install-fatal on Path A**: on a uv-only fresh machine, `bootstrap.sh` fails (no `bin/pip`) → herdr aborts the whole `plugin install`. Fix before any installer work. |
| **MEDIUM (new)** | **Startup-hook contract mismatch**: Herdr documents `[[startup]]` as one-shot/should-exit; `_daemon-supervised` is a foreground watchdog. Tolerated today, future-host risk. |
| **MEDIUM (new)** | **Dev ↔ public transition friction**: install over a linked plugin is refused; installer must detect and handle the linked-checkout case. |
| **MEDIUM (new)** | **`--yes` + preview asymmetry**: noninteractive installs skip the trust preview while build does network pip from mutable refs — supply-chain window confirmed reachable via `--yes`. |
| **LOW (update)** | Explore's HIGH "no uninstall/rollback story" **downgraded**: `plugin uninstall` + `--ref` exist host-side. Remaining plugin-side work: venv removal, managed keymap block removal, daemon stop on uninstall. |
| **LOW** | Registry `version` format spec undocumented ("parseable required metadata"); verify empirically after tagging. |

## Next phase

Proposal (mandatory next per research gate). Product decisions the proposal must own: #3 (agent-tts pinning), #4 (Path B clone target), #5 (keymap default policy), #6 (installer UX language), #8 (tag/backfill strategy). Scoped follow-up: live uv test on a uv machine (belongs in design/tasks or verify).
