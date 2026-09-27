# Design: PM-01 — One-Line Install & Packaging

> **Change:** pm-01-install-packaging · **Phase:** design · **Date:** 2026-09-22
> **Satisfies:** `specs/plugin-bootstrap/spec.md` (6 requirements) + `specs/installer/spec.md` (7 requirements) = 13 requirements / 20 scenarios.
> **Note:** the launch brief said 19 scenarios; the specs as written contain 20 (8 bootstrap + 12 installer). The tasks phase should plan for 20.

## Technical Approach

Two shell artifacts, one shared contract. `scripts/bootstrap.sh` stays the single Python-environment entrypoint — invoked identically by the host `[[build]]` hook, by `bin/herdr-tts`'s auto-bootstrap (L40–43), and by `scripts/install.sh` — and gains four surgical fixes: a pip-free install indirection, an immutable agent-tts pin, a `HERDR_TTS_DEV` gate, and an env-triggered upgrade mode. `scripts/install.sh` is new and additive: it never reimplements venv logic, it *calls* bootstrap. All state stays under the existing XDG dirs; `bin/herdr-tts` is untouched.

## Architecture Decisions

### (a) Venv seeding — unified installer indirection

| Option | Tradeoff | Decision |
|---|---|---|
| `uv venv --seed` | Works, but keeps the `${VENV_DIR}/bin/pip` coupling and pays a pip bootstrap on every uv venv | Rejected |
| `uv pip install --python <venv>/bin/python` | Astral documents drop-in `pip install` compatibility; the tool that created the venv owns installs into it | **Chosen** |
| Mixed ad-hoc branches | Two divergent install call sites to keep in sync | Rejected |

**Choice:** one `py_install()` function. uv present → `uv pip install --python "$VENV_DIR/bin/python" …`; otherwise → `"$VENV_DIR/bin/python" -m pip install …`. **`${VENV_DIR}/bin/pip` is never invoked again** (spec: plugin-bootstrap/uv venv pip seeding). `python -m pip` also survives venvs whose `bin/pip` console script is absent but whose `pip` module is present. Python-tooling detection moves **before** `mkdir -p "$DATA_DIR"` so the no-tooling abort leaves nothing behind.

### (b) Upgrade invocation — env-gated in-place refresh

| Option | Tradeoff | Decision |
|---|---|---|
| CLI flag only (`--upgrade`) | Host `[[build]]` argv is fixed (`["bash","scripts/bootstrap.sh"]`) and the auto-bootstrap call passes no args — a flag-only design is unreachable from two of three callers | Rejected as sole channel |
| Venv recreate | Re-downloads the whole dependency tree to move one ref; slower with no convergence benefit | Rejected |
| `HERDR_TTS_UPGRADE=1` env + `--upgrade` synonym, in-place `--upgrade` install of the pinned ref | Reachable from every caller, leaves fresh-install semantics untouched (gate still short-circuits when unset) | **Chosen** |

Upgrade mode skips the healthy-venv gate and re-runs `py_install --upgrade <pinned-src>`, re-resolving the direct VCS reference. `install.sh` sets the env var on its upgrade branch only.

### (c) Linked-checkout detection — `herdr plugin list --json`

| Option | Tradeoff | Decision |
|---|---|---|
| Probe candidate dirs | Guesses a host-managed layout the docs never specify | Rejected |
| `HERDR_PLUGIN_ROOT` | Injected into *plugin* processes, not into a curl\|sh script | Rejected |
| `herdr plugin list --json` | **Verified live on this machine**: emits `.result.plugins[].source.kind` = `local` vs `github`, plus `plugin_id` and `plugin_root` | **Chosen** |

`herdr plugin list --json | jq -r '.result.plugins[]|select(.plugin_id=="herdr.tts")|.source.kind'` → `local` aborts with the `plugin unlink` / `plugin uninstall` migration message, before any mutation. jq is already proven by preflight, so this probe runs after it (no circularity).

### (d) agent-tts pin source — one variable in bootstrap.sh

Single `AGENT_TTS_REF` (override: `HERDR_AGENT_TTS_REF`) at the top of `bootstrap.sh`, composed into `git+https://github.com/chiptime/agent-tts.git@${AGENT_TTS_REF}`. A separate `versions.env` was rejected: it adds a sourced path dependency to a hook the host runs from an arbitrary checkout root, for exactly one value. **Resolution order at implementation time:** newest agent-tts tag if any exists → otherwise the full 40-char commit SHA of its current `main`. Short SHAs and bare `main` are prohibited.

### (e) Installer default ref — hardcoded at release

| Option | Tradeoff | Decision |
|---|---|---|
| `git ls-remote --tags` | Network round-trip; "latest" makes the same script bytes non-reproducible over time | Rejected |
| GitHub API | Rate limits, auth, and needs jq **before** preflight proves jq — circular | Rejected |
| Hardcode `HERDR_TTS_REF="${HERDR_TTS_REF:-v0.16.0}"` | nvm's precedent; deterministic, hermetically testable, escape hatch is one env var | **Chosen** |

The script is regenerated (one-line bump) per release, matching the uv model's spirit without its checksum machinery.

### (f) Live uv verification — manual verify-phase step

This dev machine has no `uv` (confirmed), so the uv branch stays source-verified only. The verify phase runs this on a uv-equipped machine and records the output:

```bash
export XDG_DATA_HOME="$(mktemp -d)"            # hermetic venv target
bash scripts/bootstrap.sh; echo "bootstrap rc=$?"
V="$XDG_DATA_HOME/herdr-tts/venv"
ls "$V/bin"                                     # bin/pip MAY be absent — that is fine
"$V/bin/python" -c 'import agent_tts; print(agent_tts.__file__)'
HERDR_TTS_UPGRADE=1 bash scripts/bootstrap.sh; echo "upgrade rc=$?"
rm -rf "$XDG_DATA_HOME"
```
Pass = both `rc=0` and a successful `import agent_tts`.

## Data Flow — `scripts/install.sh`

```
                     ┌──────────────────────────────────────────┐
  curl | sh ────────►│ 1 PREFLIGHT (read-only, zero mutation)   │
                     │  bash · git · jq · herdr · (uv|python3)  │──abort─► "missing <tool>" rc=1
                     │  herdr plugin list --json → kind==local? │──abort─► "unlink/uninstall first" rc=1
                     └───────────────────┬──────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────┐
                     │ 2 OBTAIN  target=~/.local/share/         │
                     │           herdr-tts/plugin               │
                     │  absent → git clone --branch $REF        │──fail──► rc=1
                     │  present → remote == canonical ?         │──no────► "remote mismatch" rc=1
                     │            yes → fetch + checkout $REF   │          (nothing written)
                     └───────────────────┬──────────────────────┘
                                 fresh ──┴── upgrade
                                         ▼
                     ┌──────────────────────────────────────────┐
                     │ 3 BOOTSTRAP  bash scripts/bootstrap.sh   │──fail──► rc=1
                     │  upgrade branch: HERDR_TTS_UPGRADE=1     │
                     └───────────────────┬──────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────┐
                     │ 4 KEYMAP (skipped entirely: --no-keymap) │
                     │  keymap.json exists → leave untouched    │
                     │  else adopt --style menu → apply         │
                     │       → herdr server reload-config       │──fail──► warn, continue
                     └───────────────────┬──────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────┐
                     │ 5 DAEMON VERIFY  pidfile + live pid      │──absent─► warn + `--status` hint
                     └───────────────────┬──────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────┐
                     │ 6 POST-INSTALL PRINT  (always on success)│
                     │  status pointer · uninstall steps:       │
                     │   a) herdr plugin uninstall herdr.tts    │
                     │   b) stop the daemon                     │
                     │   c) rm -rf ~/.local/share/herdr-tts     │
                     │   d) remove the managed keymap block     │
                     └──────────────────────────────────────────┘
```

Steps 1–2 are strictly non-mutating until every abort path has been cleared: the jq-missing and linked-checkout scenarios both assert zero clone/venv/keymap artifacts.

## Host-Compatibility Constraints (bootstrap.sh)

1. **Same entrypoint, same argv.** The host runs `["bash","scripts/bootstrap.sh"]` on every install *and every reinstall* (there is no `plugin update`). New behavior must be reachable without arguments.
2. **Zero interactive prompts, zero TTY assumptions.** Build hooks run non-interactively; a prompt would hang the install. No `read`, no `/dev/tty`.
3. **Build failure aborts the whole install** — so every non-fatal condition (e.g. an unexpected local checkout while `HERDR_TTS_DEV` is unset) must warn, not exit non-zero.
4. **Checkout-location agnostic.** No `$HOME`-relative or repo-relative path assumptions beyond `$0`'s own directory; `~/Code/personal/*` is referenced only behind the dev gate.
5. **Idempotent.** Re-runs converge; the healthy-venv gate remains the default fast path.

## English Output Convention

All new/edited strings in `bootstrap.sh` and all of `install.sh` are English, inline literals (no i18n layer — consistent with the existing single-script style). Tone: imperative, lowercase prose after a status glyph, one line per step. Prefixes: `==>` step, `✓` success, `!` warning, `Error:` failure (stderr) naming the missing tool and the remedy command. Spanish remains untouched everywhere in `bin/herdr-tts`.

## File Changes

| File | Action | Description |
|---|---|---|
| `scripts/bootstrap.sh` | Modify | `py_install()` indirection, `AGENT_TTS_REF` pin, `HERDR_TTS_DEV` gate, `HERDR_TTS_UPGRADE` mode, tooling check before `mkdir`, English strings (~25–35 ln) |
| `scripts/install.sh` | Create | 6-stage installer per the flow above (~180–220 ln) |
| `herdr-plugin.toml` | Modify | `version = "0.16.0"` (1 ln) |
| `README.md` | Modify | Install section: channels, pinning, `@main` hatch, uninstall, dev workflow (~70–90 ln) |
| `scripts/smoke-tests.sh` | Modify | Scenarios 33 (bootstrap) + 34 (installer) (~110–150 ln) |
| `bin/herdr-tts` | Untouched | jq guard lives in installer preflight; `daemon_keymap_autostart` reused as-is |

## Interfaces / Contracts

```bash
# scripts/bootstrap.sh — public contract
# env in:  HERDR_TTS_DEV=1        editable install from ~/Code/personal/agent-tts
#          HERDR_TTS_UPGRADE=1    bypass the healthy-venv gate, refresh the pin
#          HERDR_AGENT_TTS_REF    override the pinned agent-tts ref
#          XDG_DATA_HOME          venv root (existing)
# argv in: [--upgrade]            synonym for HERDR_TTS_UPGRADE=1
# exit:    0 ready · 1 no python tooling / install failure
py_install() { # "$@" = pip args; routes through uv pip or python -m pip
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "${VENV_DIR}/bin/python" "$@"
  else
    "${VENV_DIR}/bin/python" -m pip install "$@"
  fi
}

# scripts/install.sh — public contract
# env in:  HERDR_TTS_REF (default v0.16.0) · HERDR_CONFIG_DIR · HERDR_TTS_KEYMAP_FILE · XDG_*
# argv in: [--no-keymap]
# exit:    0 installed/upgraded · 1 preflight, linked-checkout, remote-mismatch or bootstrap failure
```

## Testing Strategy

Strict TDD against the single hermetic runner `bash scripts/smoke-tests.sh`. RED scenarios land before any implementation line.

| Layer | What to test | Approach |
|---|---|---|
| Integration (scenario 33, bootstrap) | uv seeding, python3 fallback, no-tooling abort, pin, dev gate both ways, upgrade re-run, arbitrary checkout location | `new_env s33` + PATH-level recorder stubs + hermetic `HOME` |
| Integration (scenario 34, installer) | preflight (jq/linked), fresh install, remote match/mismatch, keymap default/existing/`--no-keymap`, uninstall print, English-only output, tag default + `@main` hatch | `new_env s34` + stub matrix, assertions over captured stdout and the recorder logs |
| Manual (verify phase) | uv branch on real uv | decision (f) script, output recorded in the verify report |

**Stub design (extends the existing `new_env` harness):** every external binary becomes an executable recorder in `$T/bin` (already first on `PATH`) that appends its full argv to a per-tool log and exits 0 — the same shape as `write_stateful_herdr_stub`.

| Stub | Records | Behavior |
|---|---|---|
| `uv` | `$T/uv.log` | `uv venv` creates `bin/python` **and deliberately no `bin/pip`** (reproducing `Seed::Disabled`); `uv pip install` logs the source string |
| `python3` | `$T/py.log` | `-m venv` creates `bin/python` **plus** a `bin/pip`; the venv `bin/python` delegates to the real `python3` so `import agent_tts` is scriptable, and `-m pip` logs the source |
| `git` | `$T/git.log` | `clone` materializes the target dir; `remote get-url` echoes a fixture URL (canonical or mismatched per sub-case) |
| `jq` | absent / real | omitted from `$T/bin` **and** masked out of `PATH` for the jq-missing case |
| `herdr` | `$T/herdr.log` | `plugin list --json` emits a fixture with `source.kind` = `github` or `local`; `server reload-config` logs only |

Tooling absence is expressed by a restricted `PATH="$T/bin"` (no system fallthrough), so "no uv", "no python3", "no jq" are all PATH facts, not mocks.

**Hermetic HOME decoy** (dev-gate scenarios): `export HOME="$T/home"; mkdir -p "$T/home/Code/personal/agent-tts"` with a marker file. With `HERDR_TTS_DEV` unset, assert the recorded install source contains the pinned ref and `assert_no_grep_f` the decoy path in `$T/uv.log`/`$T/py.log`. With `HERDR_TTS_DEV=1`, assert `-e <decoy>` is recorded. `HOME` must be exported *before* `bootstrap.sh` runs and restored after the scenario, since `new_env` does not currently touch it.

## Threat Matrix

| Boundary | Minimum adversarial cases | Applicability | Design response | Planned RED tests |
|---|---|---|---|---|
| Documentation-like paths | `requirements.txt`, executable Markdown, `README.sh` | **N/A** — nothing in this change classifies or executes discovered files; the only executed paths are two fixed repo scripts | — | — |
| Git repository selection | `git -C`, relative vs absolute paths | **Applicable** — installer runs git against a clone target | All git calls use an absolute `TARGET` with explicit `git -C "$TARGET"`; never the caller's cwd. Relative/`$PWD` inference is prohibited | Scenario 34: installer invoked from an unrelated cwd still clones to `TARGET`; `$T/git.log` asserts `-C "$TARGET"` |
| Commit state | staged, `commit -a`, empty index | **N/A** — the installer never commits; it only clones/fetches/checkouts | — | — |
| Push state | tracking branch, first push, refspec | **N/A** — no push, no write to any remote | — | — |
| PR commands | `--head`, env prefix, composed commands | **N/A** — no PR automation in either script | — | — |
| *(added)* Remote identity | attacker-controlled or drifted `origin` | **Applicable** — an existing target dir is only reused when its remote matches | Exact string compare against the canonical URL; mismatch aborts before any write | Scenario 34 "mismatched remote aborts": asserts rc≠0, the mismatch message, and zero writes |
| *(added)* Ref immutability | bare `main`, short SHA, empty ref | **Applicable** — both scripts compose refs into network install sources | Defaults are a tag (`v0.16.0`) and a tag/full-SHA agent-tts pin; mutable refs only via explicit `HERDR_TTS_REF`/`@main` | Scenario 33 "Pinned install source" + scenario 34 "Default pins the tag" / "Escape hatch targets main" |

## Migration / Rollout

Sequence (each step independently revertable): (1) bootstrap fixes + scenario 33 — unblocks Path A immediately and ships standalone; (2) manifest bump to 0.16.0 and tag `v0.16.0`, pushed only after the suite is green; (3) `install.sh` + scenario 34 defaulting to the tag; (4) README rewrite. Dev machines migrating to a public install must run `herdr plugin unlink herdr.tts` (or `uninstall`) first — the installer detects and instructs rather than forcing. No data migration: existing venvs keep working and converge on the pin at the next upgrade run.

## Open Questions

- [ ] Does agent-tts publish a tag today? If not, the implementer resolves and records a full `main` SHA (decision (d) fallback) — blocks nothing, but the exact pin value is unknown until implementation.
- [ ] Same-tag-moved-SHA is not re-fetched by `--upgrade`; accepted because tags are treated as immutable by policy. Revisit only if agent-tts retags.
- [ ] Spec scenario count is 20, not the 19 stated in the launch brief — tasks should plan against the specs.

## Traceability

| # | Requirement | Spec | Design sections |
|---|---|---|---|
| 1 | uv venv pip seeding | plugin-bootstrap | Decision (a); Interfaces (`py_install`); Testing (scenario 33, `uv` stub); Decision (f) |
| 2 | python3-only fallback | plugin-bootstrap | Decision (a); Host-compat #3; Testing (`python3` stub, restricted PATH) |
| 3 | agent-tts immutable pin | plugin-bootstrap | Decision (d); Threat matrix "Ref immutability"; File Changes (bootstrap) |
| 4 | Dev-gated editable checkout | plugin-bootstrap | Decision (d); Host-compat #4; Testing (hermetic HOME decoy) |
| 5 | Upgrade-capable re-run | plugin-bootstrap | Decision (b); Interfaces (env contract); Flow step 3 |
| 6 | Checkout-location agnostic | plugin-bootstrap | Host-compat #1/#4; Testing (scenario 33 arbitrary location) |
| 7 | Preflight before mutation | installer | Flow step 1; Decision (c); Threat matrix (git selection) |
| 8 | Fresh install | installer | Flow steps 2–6; Interfaces (install.sh contract) |
| 9 | Idempotent re-run and remote guard | installer | Flow step 2; Decision (b); Threat matrix "Remote identity" |
| 10 | Keymap adoption policy | installer | Flow step 4 (keymap rationale); proposal D3 (trust argument: never-overwrite, collision-free `menu`, `--no-keymap`, inherited backup/rollback); Migration (reuses `keymap adopt`'s no-overwrite + `apply` backup/rollback) |
| 11 | Uninstall guidance | installer | Flow step 6; Migration / Rollout |
| 12 | English output | installer | English Output Convention; File Changes (README, bootstrap strings) |
| 13 | Tag-pinned default with escape hatch | installer | Decision (e); Threat matrix "Ref immutability"; Migration step 2 |
