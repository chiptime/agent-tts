# Design: AT-11 — Independent Installation and First-Run Onboarding

This design answers **how** the five AT-11 specs are implemented: where the one shared
first-run wizard lives and how both installed entry points reach it, how secrets are
captured without ever touching argv, how the V2 clean-install harness is built (milestone 1,
task 1), how paths/ports/binaries stop being machine-specific, how the two authorized
milestone-1 baseline V1 repairs (smoke 16n host safety, smoke 40e oracle identity) are
built, and how the four milestones decompose into review slices under the 400-line budget.

### Decision status

**Every product decision in this document is resolved.** Nothing here waits on a maintainer
answer. Two items remain *evidence*-pending — they need a test run, not a decision.

| Item | State |
|---|---|
| Decisions 1, 2, 3, 5, 7 | Resolved in this document |
| Decision 0 (base correction) | **Completed** — the worktree is fast-forwarded; OQ-4 resolved (see Decision 0) |
| Decision 4 (`HERDR_TTS_HOME` precedence) | **Resolved** — explicit override wins (Engram #9681, ex-OQ-2) |
| Decision 6 (STT health vocabulary) | **Resolved** — `unavailable` (Engram #9681, ex-OQ-3) |
| Decision 8 (smoke 16n host safety) | Resolved here — new, authorized M1 extension |
| Decision 9 (smoke 40e oracle identity) | Resolved here — new, authorized M1 extension |
| Decision 10 (scenario 3 activation milestone) | Resolved here — registry correction |
| V2 network authorization (ex-OQ-5) | **Granted**, bounded to documented origins (Engram #9681) |
| V2 active-scenario gate | **Resolved** (Engram #9636) |
| OQ-1 subdirectory-install materialization | **Evidence-pending** — resolved by V2 scenario 2, with a pre-designed fallback |
| OQ-6 public retrieval of the selected pin | **Evidence-pending** — resolved by V2 scenario 1; `BLOCKED` with no substitute if unavailable |

An earlier revision of this header referred to a "Decision 9" awaiting a maintainer answer.
No such pending decision ever existed: the STT vocabulary question lived in Decision 6 and is
now resolved. Decision 9 below is a *new, resolved* decision on smoke 40e.

## Technical approach

Three structural moves, in dependency order:

1. **Prove first.** `scripts/acceptance/clean-install.sh` is written before any production
   change. It owns the sandbox, the scenario registry, the activation model, and the evidence
   journal. Every later slice adds or activates scenarios in it — nothing else.
2. **Derive, never hardcode.** Every path, binary, and port is resolved at runtime from the
   executing script's own location or from the environment, and the one place that still needs
   an absolute value (the systemd unit) gets it by *substituting a discovered value into a
   versioned template at install time*. No versioned file contains a machine-specific string.
3. **One wizard, two bash callers.** A dependency-free Python package at the monorepo root,
   located by ascending from whichever entry point invoked it, executed through the venv Python
   each entry point already owns. No new package, no new dependency, no duplicated logic.

The existing installer/bootstrap/launcher entry points are extended. No parallel installation
framework is introduced. Frozen `contracts/ipc-v2` and `contracts/tts-brain-v1` are read-only.

---

## Decision 0: Worktree base predates two files the plan assumes are missing

**Choice**: Rebase `feat/at-11-instalable` onto `main` before milestone 1 begins, and re-scope
milestone 1 from "create the brain launcher and manifest" to "repair the brain launcher and
manifest that already exist".

**Evidence**:

| Fact | Command |
|---|---|
| Worktree HEAD is `1b404ad`; `main` is `4ed549a` | `git rev-parse HEAD` in each checkout |
| `main` commit `8de8a34 feat(brain): add herdr plugin manifest and brain launcher CLI` added `hosts/herdr/brain/bin/herdr-brain` (279 lines) and `hosts/herdr/brain/herdr-plugin.toml` (43 lines) | `git log --oneline 1b404ad..4ed549a`, `git diff --stat` |
| Neither file exists in this worktree | `git ls-files hosts/herdr/brain` |

**Alternatives considered**:

- *Author both files fresh in the worktree.* Rejected: it recreates work already on `main` and
  guarantees a conflict at merge, on the two files most central to the change.
- *Cherry-pick `8de8a34` only.* Rejected: it drags in a partial history and leaves the branch
  still behind on the other three commits, which are harmless to take — `e2f207c` and `3b83770`
  add documentation only, and `4ed549a` is a `chore` that tracks a single **empty** metrics log
  placeholder (`metrics/smoke/smoke-8h-20260925T0935Z.log`, 0 bytes). Describing all three as
  "documentation-only" was imprecise: one is a chore, not documentation. None touches code.
- *Proceed and reconcile later.* Rejected: milestone-1 slices would be written against a tree
  that does not match the delivery target.

**Rationale**: the audit's A3 ("brain manifest not committed") is **already fixed on `main`** —
the manifest is tracked and carries the `[[startup]]` entry AT-11 depends on. What is *not*
fixed is everything else the audit names, and the newly committed launcher still contains every
defect verbatim:

| Audit finding | Still present in `main:hosts/herdr/brain/bin/herdr-brain` |
|---|---|
| A1 dead TTS path | `export HERDR_TTS_HOME="${HERDR_TTS_HOME:-$HOME/Code/personal/herdr-tts}"` |
| C1 dotfiles key scrape | reads `$HOME/.dotfiles/shell/private-env.sh` |
| C2 literal brew prefix | `/home/linuxbrew/.linuxbrew/bin/herdr` fallback |
| C4 personal tailnet | `tail2640fd.ts.net:8443` |
| C5 aggressive port policy | `kill -TERM "$existing_pid"` on an unowned listener |

So the corrective work is real and unchanged; only its *starting point* moves. Rebasing costs
one operation and removes a guaranteed merge conflict. This is a local `git rebase` in the
worktree — no remote operation.

> **Blocker until actioned.** Milestone-1 slices that touch the brain launcher or manifest MUST
> NOT start before the rebase. Audit finding A3 must be re-scored as *resolved on `main`* in the
> traceability evidence rather than re-delivered.

**Status: completed.** The worktree is fast-forwarded past `4ed549a` (current HEAD `401a4b8`),
and `hosts/herdr/brain/bin/herdr-brain` (279 lines) plus `hosts/herdr/brain/herdr-plugin.toml`
(43 lines) are tracked files. Consequently:

- The blocker is discharged; slices 10–12 are unblocked.
- Every task the original text framed as "create the brain launcher/manifest" is **repair or
  extend** work (task 2.2), never a fresh delivery.
- **OQ-4 is resolved**, not an open question. It is retained in the Open questions section only
  as a resolution record.

---

## Decision 1: Shared wizard module — location and packaging reachability

**Choice**: one dependency-free, standard-library-only Python package at the monorepo root:

```
tools/herdr_onboarding/
├── __init__.py
├── __main__.py        # CLI entry: --role {plugin,brain}, --no-first-run, --doctor
├── resolve.py         # XDG paths, monorepo root, herdr binary, port
├── prompts.py         # dependency-free text I/O + getpass + non-argv secret intake
├── secrets.py         # mode-600 env-file read/merge/write
├── steps/             # credentials, voice, keymap, stt, health
└── report.py          # doctor output + actionable remediation strings
```

Invoked by each bash entry point through the venv Python it already owns:

```bash
PYTHONPATH="$ONBOARDING_LIB" "$VENV_PY" -m herdr_onboarding --role plugin "$@"
```

**Alternatives considered**:

| Option | Why rejected |
|---|---|
| Ship it inside `engine/` (the `agent_tts` package) | `hosts/herdr/brain/pyproject.toml` does **not** depend on `agent-tts`. Placing the wizard there forces a new mandatory runtime dependency on the brain — a hard constraint violation. It also drags host concerns (keymap, `herdr` CLI, brain env file) toward a package that `engine/tests/test_monorepo_boundaries.py` keeps host-free. |
| Add it as a path dependency to both `pyproject.toml` files | Requires the monorepo root to exist at *pip install* time. The plugin's public install path is a pinned remote `git+…#subdirectory=engine`, where the root is not a local path. Breaks the pinned-source contract. |
| Deliberate duplication in each subproject | Explicitly rejected by the PRD's resolved decision #2 ("módulo compartido … no duplicación"). |
| Bash library sourced by both launchers | The brain launcher is bash but all its logic (env file merge, HF cache check, `/health` parse, JSON keymap) already lives in Python. A bash wizard would reimplement `getpass`, JSON handling, and the STT cache probe by hand. |

**Rationale — why plain `PYTHONPATH` + `-m` and not an installed package**: it adds nothing to
either venv, so "no new runtime dependency" is satisfied literally, and it cannot drift out of
sync with the checkout because there is no copy — both entry points execute the same file.

### Reachability mechanism

`ONBOARDING_LIB` is resolved by a small, identical shell function in both entry points:

```
1. $HERDR_ONBOARDING_HOME              (explicit override; tests and packagers)
2. ascend from the entry point's own resolved location, up to 6 levels,
   taking the first directory D where D/herdr_onboarding/__main__.py exists
3. fail with an actionable English message naming step 1
```

Ascension starts from `$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")` — the convention
`bin/herdr-tts` and `bin/herdr-brain` already use — so a symlinked `~/.local/bin/herdr-tts`
resolves to its real checkout, not to `~/.local/bin`.

### Why ascension reaches the module in every installed layout

| Layout | Entry point location | `tools/` reachable? |
|---|---|---|
| Source checkout / worktree | `<root>/hosts/herdr/{tts-plugin,brain}/bin/` | Yes — 3 levels up |
| `scripts/install.sh` (curl route) | `~/.local/share/herdr-tts/plugin/hosts/herdr/tts-plugin/bin/` — the installer clones the **whole monorepo** | Yes — 3 levels up |
| `herdr plugin install chiptime/agent-tts/hosts/herdr/tts-plugin` | `<managed_path>/hosts/herdr/tts-plugin/bin/` | Yes, **if** herdr materializes the full repo and points `plugin_root` at the subdirectory |
| Homebrew keg | `<prefix>/bin/` — `prefix.install Dir["*"]` stages only the plugin subdirectory | **No** — see below |

The third row rests on evidence, not assumption: `herdr 0.9.1`'s `InstalledPluginInfo` carries
**both** `managed_path` and `plugin_root` as distinct fields alongside `owner`/`subdir`
(observed in the binary's serde type table), and an existing GitHub-installed plugin
(`~/.config/herdr/plugins/github/herdr.collie-*`) is a **full git checkout** — `git rev-parse
--show-toplevel` resolves to the plugin root. Two separate path fields only make sense when
`plugin_root = managed_path/<subdir>`.

This is a high-confidence inference from a read-only inspection, not a direct observation of a
subdirectory install (performing one would mutate the live plugin registry, which this change
forbids). It is therefore recorded as **Open question OQ-1**, and V2 scenario `plugin-subdir-install`
is the thing that proves or falsifies it.

**Pre-designed fallback (only if OQ-1 falsifies, or to support Homebrew):** keep
`tools/herdr_onboarding/` as the single authored source and add a `vendor/herdr_onboarding/`
copy inside each subproject, materialized by a `make sync-onboarding` step and guarded by a V1
byte-identity test modelled on the existing `engine/tests/test_monorepo_boundaries.py` static
guard. Resolution step 2 then also accepts `<entry-root>/vendor/`. This stays "one authored
implementation with mechanically generated distribution copies", which satisfies the PRD's
no-duplication decision in substance; it is *not* adopted pre-emptively because an unnecessary
tracked copy is a drift risk with no benefit while row 3 holds.

The Homebrew row is out of scope for behavioral support: the formula receives only the bounded
legacy-reference corrections the proposal allows, and the docs state honestly that the wizard
is not available on the keg route.

---

## Decision 2: Secure secret input

**Choice**: three intake channels, ranked, with argv excluded at every level.

| Mode | Mechanism | Guarantee |
|---|---|---|
| Interactive | `getpass.getpass()` | No terminal echo, never re-displayed |
| Unattended (primary) | `HERDR_ONBOARDING_SECRET_FD=<n>` — the wizard reads the secret from inherited file descriptor *n* and closes it | Not visible in `ps`, not in `/proc/<pid>/environ` (only the small integer is), not inherited past the intended child |
| Unattended (fallback) | `HERDR_ONBOARDING_SECRET_FILE=<path>` — mode-600 file, read once, optionally unlinked | Works in shells and CI runners that cannot pass an extra fd |

Non-secret answers (voice provider, keymap style, STT size, yes/no consents) are accepted
through flags and environment variables, which is what the PRD's noninteractive scenario
("respuestas por flag/env") actually needs.

**Alternatives considered**:

- *Secret value in an environment variable.* Technically non-argv and spec-compliant, but the
  value is readable from `/proc/<pid>/environ` by any same-user process, is inherited by every
  child (including `herdr`, `git`, and the STT downloader), and reappears in `systemctl --user
  show-environment` dumps. Demoted to "supported for compatibility, documented as weaker".
- *`--glm-key <value>` flag.* Rejected outright: `ps` exposes argv process-wide. The spec
  forbids it and PRD scenario 2 asserts against it.
- *stdin.* Rejected as the primary: stdin is already the wizard's text-interaction channel, so
  consuming it for the secret forecloses interactive prompts in mixed runs.

**Rationale**: an fd is the only channel in this set that is neither enumerable by another
process nor inherited beyond the intended callee. The file fallback exists because passing an
extra fd from a Makefile or a minimal CI shell is awkward, and a mode-600 file under the user's
own config dir is the same trust boundary the persisted env file already uses.

### Storage and non-leak discipline

- Written to `~/.config/herdr-brain/env`, directory `700`, file `600`, via
  `tempfile.mkstemp` in the **same directory** → `chmod 600` → `os.replace` (atomic; no partial
  file, no mode window).
- Re-runs **merge**: existing keys are preserved unless the user supplies a new value; unknown
  lines and comments are kept byte-identical (same discipline as the plugin's existing
  `config_set` managed-key writer).
- A single `redact()` boundary wraps every diagnostic path. Secrets are never formatted into
  exception messages; the wizard catches and re-raises with a value-free message.
- `set -x` is prohibited in any script that handles the key — inherited from the existing
  `deploy/install.sh` discipline comment and enforced by a V1 static assertion.

---

## Decision 3: V2 clean-install harness architecture

**Choice**: `scripts/acceptance/clean-install.sh` — a single bash entry point implementing a
scenario registry with an explicit four-state activation model. Written complete as
**milestone 1, task 1**, before any production change.

### Clean-room model

| Dimension | Enforcement |
|---|---|
| Checkout path | Harness clones/copies the repo under test into `$(mktemp -d)/checkout-$RANDOM`, a non-standard path outside `$HOME` |
| HOME | `export HOME="$SANDBOX/home"` — created empty. No `~/.dotfiles`, no brew, no Tailscale, no keys |
| XDG | `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` all under `$SANDBOX` |
| PATH | Rebuilt from an explicit allowlist (`bash git jq curl python3 uv`, plus stubs). The host PATH is discarded, so a leaked brew prefix is structurally impossible |
| Secrets | `env -i` base; only the variables the harness sets survive |
| Network | See boundary below |
| Cleanup | `trap … EXIT` removes `$SANDBOX`; `--keep` preserves it for debugging |

The harness never runs `sudo`, never writes outside `$SANDBOX`, and never touches the operator's
real `~/.config/herdr/plugins.json`.

### Documented-origin network boundary

The PRD restricts network access to "los orígenes que la doc cita". Implementation:

1. A versioned allowlist `scripts/acceptance/allowed-origins.txt` (`github.com`,
   `raw.githubusercontent.com`, `codeload.github.com`, `huggingface.co`, `cdn-lfs*.huggingface.co`,
   `pypi.org`, `files.pythonhosted.org`).
2. A `curl`/`git` wrapper shim placed **first** on the sandbox PATH resolves the target host and
   exits non-zero with `BLOCKED-ORIGIN: <host>` when it is not on the allowlist.
3. A V1 test asserts every origin in the allowlist appears in the installation documentation,
   and that every URL in the installation documentation appears in the allowlist — so the
   allowlist cannot silently drift wider than the docs.

This is a *policy* boundary enforced at the tool call, not a kernel namespace. The harness says
so in its own output; it does not claim network isolation it cannot provide.

### Executing the *literal* documented steps

To make "literally the documented steps" mechanical rather than a hand-copied transcript, each
installation command in the docs lives in a fenced block tagged with a stable id:

    ```bash id=install-plugin-github
    herdr plugin install chiptime/agent-tts/hosts/herdr/tts-plugin
    ```

The harness extracts blocks **by id from a pinned file allowlist** and executes those. It never
scans a document and runs whatever it finds. A V1 test asserts every id referenced by the
harness exists exactly once in the expected file, so a doc edit that breaks the flow fails the
suite instead of silently diverging.

### Scenario registry and the activation model

Nine scenarios, one per PRD Gherkin scenario, mapped one-to-one:

| # | Scenario id | PRD scenario | Level | Activates at |
|---|---|---|---|---|
| 1 | `plugin-fresh-clone` | Instalación del plugin desde clon fresco | V2 | M1 |
| 2 | `plugin-subdir-install` | (same scenario, subdirectory route — OQ-1 probe) | V2 | M1 |
| 3 | `zero-machine-paths` | Cero rutas de máquina | V1+V2 | **M2** (authored in M1) |
| 4 | `first-run-keys` | Primera ejecución con claves | V2 | M3 |
| 5 | `stt-consent-download` | Descarga de modelo STT con consentimiento | V2 | M3 |
| 6 | `stt-refusal-degrades` | Rechazo de la descarga degrada sin romper | V2 | M3 |
| 7 | `reinstall-idempotent` | Reinstalación idempotente conserva estado | V2 | M2 |
| 8 | `doctor-diagnoses-break` | Doctor diagnostica rotura simulada | V2 | M4 |
| 9 | `post-wizard-health` | Salud final post-asistente | V2 (+V3) | M4 |

Scenario 3 is the one split case: its script is **authored in M1** but its registry
`activates_at_milestone` is `2`, so it reports `NOT-YET-ACTIVATED` throughout M1 and only
activates once M2 task 2.2 delivers the behavior. It is never red at M1 (see Decision 10).

Scenario 9's timed-human half stays V3 and is never asserted by the harness.
(PRD scenario 1 is covered by harness scenarios 1 and 2: the two documented routes that the
single Gherkin scenario describes. That keeps "nine scenarios" traceable while still proving
both routes.)

Each scenario declares `activates_at_milestone`. The harness reads the current milestone from
`--milestone N` (default: the highest milestone recorded as closed in the journal) and assigns
every scenario exactly one of four states:

| State | Meaning | Counts as |
|---|---|---|
| `PASS` | Activated and green | Green |
| `FAIL` | Activated and red | **Red** |
| `BLOCKED` | Activated, but a host/network/audio prerequisite is unavailable | **Red** — never green |
| `NOT-YET-ACTIVATED` | `activates_at_milestone > current` | Neither — reported separately |

This implements the resolved V2 gate (Engram `sdd/at-11-instalable/v2-gate`) exactly:
activation follows delivered functionality; a previously-`PASS` scenario that turns `FAIL`
fails the run at any milestone (regression guard, enforced by comparing against the journal);
and `--milestone 4` activates all nine, so the full-suite gate is mechanically unavoidable at
final closure.

There is deliberately **no `SKIP` state**. A missing prerequisite is `BLOCKED`, not a skip.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every activated scenario `PASS`, no regression against the journal |
| 1 | At least one activated scenario `FAIL` |
| 2 | At least one activated scenario `BLOCKED` (prerequisite or authorization missing) |
| 3 | A previously-green scenario regressed |
| 4 | Harness self-check failed (sandbox could not be built) |

Distinct codes matter: `2` tells a reviewer "we do not know yet", which is categorically
different from `1` ("we know it is broken"). Collapsing them into one code is how a blocked run
gets mistaken for a passing one.

### Evidence artifacts

```
$SANDBOX/evidence/
├── journal.json          # scenario → state, duration, milestone, run timestamp, commit
├── summary.md            # human-readable table, pasteable into a PR
├── <scenario>/cmd.log    # every command executed, argv-redacted
├── <scenario>/stdout.log
└── <scenario>/assert.log # assertion-level pass/fail, smoke-tests.sh ok/bad convention
```

`journal.json` is copied to `metrics/acceptance/clean-install-<commit>.json` on request
(`--record`) so milestone closure has a durable artifact and the regression guard has a
baseline. Assertions that ride stubs rather than real installed components are tagged
`"stubbed": true` in the journal, satisfying the spec's requirement to distinguish stubbed V1-style
checks from real installation evidence.

### Reuse of an existing convention

The harness reuses the `ok()`/`bad()`/`assert_grep`/`PASS`/`FAIL` vocabulary already proven in
`hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (4185 lines at worktree HEAD `401a4b8`) rather than
inventing a second assertion dialect. Sandbox construction is new; assertion style is not.

---

## Decision 4: Path, port, binary, and configuration resolution

**Choice**: one resolution order per concern, implemented once in `tools/herdr_onboarding/resolve.py`
and mirrored by the minimal bash equivalents the launchers need before Python is available.

| Concern | Resolution order |
|---|---|
| Monorepo / plugin root | `HERDR_PLUGIN_ROOT` → ascend from the resolved real path of the executing script |
| `HERDR_TTS_HOME` (brain → plugin) | `HERDR_TTS_HOME` if set → `<brain-root>/../tts-plugin` → error naming both |
| `HERDR_BIN` | `HERDR_BIN` if set → `command -v herdr` → `$HOMEBREW_PREFIX/bin/herdr` → `brew --prefix`/bin/herdr → `$HOME/.local/bin/herdr` → bare `herdr` |
| Port | `HERDR_BRAIN_PORT` → persisted config → default `8741` |
| Credentials | `GLM_API_KEY` in env → `~/.config/herdr-brain/env` → plugin config dir → *(dotfiles scrape removed)* |
| CLI exposure | `~/.local/bin/herdr-tts`, created if missing, PATH-coverage warning if uncovered |

### The `HERDR_TTS_HOME` precedence question (RF-AT-11-8) — RESOLVED (ex-OQ-2)

RF-AT-11-8 reads: *"la resuelve relativa a su propia ubicación (`../tts-plugin`) **o del
entorno, en ese orden**"* — literally, own-location first, environment second.

Taking that literally would make an explicitly exported `HERDR_TTS_HOME` **ignored** whenever a
sibling `tts-plugin` directory exists. That contradicts `hosts/herdr/brain/src/herdr_brain/config.py:122`,
which treats the variable as an override, and it removes the only escape hatch for a split
deployment.

**Resolution (Engram #9681 — binding, not reopenable)**: read "in that order" as describing
*which default replaces the legacy hardcoded one*, not as inverting override precedence.
An explicitly exported `HERDR_TTS_HOME` **wins**; own-location derivation is only the discovery
default. The literal RF-8 reading was rejected because it would silently break existing
installs. Concretely:

- `HERDR_TTS_HOME` set and valid → use it (unchanged override semantics).
- Unset → derive from the launcher's own location.
- Never export a hardcoded default.

This is exactly what repairs audit A1: the defect is not that the environment wins, it is that
`bin/herdr-brain` *exported a dead default*, overriding `config.py`'s better one. Deleting the
bad default fixes the bug without inverting a working contract.

No maintainer confirmation remains outstanding. Slice 9 is unblocked and implements this
precedence directly; its tests assert that an exported `HERDR_TTS_HOME` is honoured even when a
sibling `tts-plugin` directory exists.

### Port ownership (audit C5)

Current `deploy/install.sh:88-100` and `main:bin/herdr-brain` both `kill -TERM` whatever holds
the port. Replacement policy:

1. Determine the listener PID.
2. If it is this installation's own artifact (our pidfile, or the systemd unit's `MainPID`),
   manage it normally.
3. Otherwise **do not signal it**. Exit non-zero with the PID, the process name, the port, and
   two remediations: choose another port (`HERDR_BRAIN_PORT=…`) or stop that process yourself.

Ownership is proven, never assumed. This is the difference between an installer and a hazard.

---

## Decision 5: Environment-derived Homebrew discovery

**Choice**: discover at *installation time* in the user's full environment, then bake the
discovered absolute path into the generated unit. The versioned tree holds only the placeholder.

```
deploy/herdr-brain.service.tmpl   (versioned)   Environment=HERDR_BIN=@HERDR_BIN@
                                                 WorkingDirectory=@INSTALL_DIR@
                                                 ExecStart=@PYTHON@ -m herdr_brain.server
                                                 Environment=HERDR_BRAIN_PORT=@PORT@
                                                 EnvironmentFile=@ENV_FILE@
        │  install-time substitution of discovered values
        ▼
~/.config/systemd/user/herdr-brain.service       (generated, never committed)
```

Discovery order for `@HERDR_BIN@`: `$HERDR_BIN` → `command -v herdr` → `$HOMEBREW_PREFIX/bin/herdr`
→ `$(brew --prefix)/bin/herdr` → `$HOME/.local/bin/herdr`. `HOMEBREW_PREFIX` is exported by
`brew shellenv`, so it is present in any shell where brew is usable — which is precisely the
shell the installer runs in.

**Rationale**: this resolves the RF-AT-11-6 / zero-path-scenario tension noted as a proposal
risk without weakening either. RF-6's "known brew prefixes" is satisfied *dynamically* (the
prefix is discovered, and on a standard Linux machine it will be `/home/linuxbrew/.linuxbrew`),
while the zero-path scenario's prohibition is satisfied *literally* (the string never appears in
a versioned file). The systemd minimal-PATH problem that motivated the original hardcode is
solved by substitution, not by a literal.

`deploy/herdr-brain.service` is deleted and `*.service` under `deploy/` is added to
`.gitignore`, so a regenerated unit cannot be committed by accident.

---

## Decision 6: STT health vocabulary — RESOLVED (ex-OQ-3)

**Choice (Engram #9681 — binding, not reopenable)**: keep `unavailable`. Report `/health` `stt` as
**`loading | ready | unavailable`**, with `unavailable` after refusal. Do **not** introduce
`degraded` for `stt`; `degraded` stays reserved for the `tts` field. RF-AT-11-12 is treated as
vocabulary errata. Frozen contracts are untouched.

### Grounding

Per Engram `sdd/at-11-instalable/health-vocabulary-grounding` (#9643), neither frozen contract
defines this vocabulary — it is brain runtime behavior. Current source confirms the shape:

| Source | Fact |
|---|---|
| `src/herdr_brain/stt.py:33-35` | `STATE_LOADING = "loading"`, `STATE_READY = "ready"`, `STATE_UNAVAILABLE = "unavailable"` |
| `src/herdr_brain/server.py:451` | `"stt": transcriber.state` — the health field *is* the transcriber state, verbatim |
| `src/herdr_brain/server.py:441-447` | `degraded` belongs to the **`tts`** field only, and means "contract present, daemon dead" |
| `src/herdr_brain/stt.py:127-130` | No model → state flips to `unavailable`; no `degraded` state exists |

### Technical rationale

`degraded` and `unavailable` are not synonyms in this codebase, and the distinction is load-bearing:

- **`degraded`** (tts) = the service works, one channel is down. `/ask` still returns audio; only
  the PC-speaker announcement path is dead. Partial function.
- **`unavailable`** (stt) = the capability is absent. `/transcribe` returns 503 unconditionally
  (`server.py:460-463`). Zero function.

A declined STT download is a **refusal**, not a degradation: the user chose not to install a
capability, and the endpoint that needs it cannot serve any request. Calling that `degraded`
would tell a monitoring consumer "partially working" about an endpoint that answers 503 to
everything, and would collapse a distinction `server.py` maintains deliberately.

Adopting `degraded` would also require adding a fourth state to `Transcriber`, changing an
already-tested value (`tests/test_stt.py`, `tests/test_server.py`), for no observable user
benefit.

### Reading the conflict

PRD acceptance scenario 4 (`stt: unavailable`) matches the implemented runtime exactly.
RF-AT-11-12's `stt: ready|degraded` appears to have borrowed the **`tts`** field's vocabulary:
`degraded` is not now, and has never been, a value `stt` can take. The coherent reading of
RF-12's intent — "`/health` reports stt coherent with what the user chose" — is fully satisfied
by `ready` after acceptance and `unavailable` after refusal.

**Confirmed.** Slice 18's assertions are unblocked and MUST assert the literal `unavailable`
after an STT refusal. Both frozen contracts remain byte-identical.

---

## Decision 7: Doctor is the wizard's detection layer, exposed twice

**Choice**: implement every check once in `tools/herdr_onboarding/` (the wizard already needs
all six to decide what to ask), and expose two thin dispatchers: `herdr-tts doctor` and
`herdr-brain doctor`.

| Check | Probe | Remediation printed on failure |
|---|---|---|
| CLI on PATH | `command -v herdr-tts` + `~/.local/bin` in `$PATH` | `export PATH="$HOME/.local/bin:$PATH"` (and the shell-rc line) |
| Audio backend | platform probe (ALSA/PulseAudio/CoreAudio/WSL) | platform-specific install command |
| Credentials | `~/.config/herdr-brain/env` present, mode 600, `GLM_API_KEY` non-empty | `herdr-brain doctor --fix-credentials` (re-runs the capture step only) |
| Daemon alive | pidfile + `kill -0`, then `/health` | `herdr-tts --restart-daemon` / `systemctl --user restart herdr-brain` |
| Contract v1 | `herdr-tts --contract-version` ≥ 1 | reinstall command for the plugin |
| STT model | `stt.model_is_cached()` (offline, never downloads) | `python -m herdr_brain.stt pull` |

Every failure line carries a copy-pasteable command. A check with no remediation is a
specification failure, asserted by a V1 test that walks the check table and requires a non-empty
remediation for each.

**Rationale**: RF-AT-11-10 allows "doctor o equivalente en el brain". One detection
implementation with two dispatchers avoids two diagnoses drifting apart — the exact failure mode
that makes doctors untrustworthy.

---

## Decision 8: Smoke 16n — playback-state isolation as a host-safety boundary

**Choice**: make the launcher's three hardcoded playback-state paths environment-overridable with
byte-identical defaults, and isolate all three plus the engine's own control socket in the smoke
suite's `new_env()`. This is a **safety fix**, not an assertion fix: the production behavior and
the 16n assertion are both already correct.

### The defect, traced to exact lines

`hosts/herdr/tts-plugin/bin/herdr-tts` lines 26–28 are the only state paths in that header block
with **no** environment override:

```bash
LOCK_FILE="/tmp/herdr-tts-playing.lock"
PID_FILE="/tmp/herdr-tts-current.pid"
IPC_SOCKET="/tmp/herdr-tts-player.sock"
```

Every neighbouring path *is* overridable (`CONFIG_FILE:24`, `SNOOZE_STATE_FILE:31`,
`HISTORY_FILE:34`, `KEYMAP_FILE:37`, `DAEMON_PID_FILE:3253`). These three were missed.

The consequence is a complete causal chain, verified in source:

| Step | Location | Behavior |
|---|---|---|
| 1 | smoke 16n, `smoke-tests.sh:1109-1130` | feeds `r` to `--voice-menu`, expects the `read.start` confirmation |
| 2 | `bin/herdr-tts` voice-menu dispatch | `r) out=$(toggle_play …)` |
| 3 | `toggle_play`, `bin/herdr-tts:2233-2237` | `if is_playing; then stop_audio; else read_current_pane; fi` |
| 4 | `is_playing`, `bin/herdr-tts:1377-1387` | reads the **host** `/tmp/herdr-tts-playing.lock`; returns true when its PID is alive |
| 5 | `stop_audio`, `bin/herdr-tts:1389-1416` | `--ipc-cmd stop` over the engine socket (1392-1393), `kill -TERM` the PID from the **host** PID file (1397-1403), `rm -f` all three **host** paths (1410), prints `player.stopped` |

So when the operator has audio playing, the test suite takes the *stop* branch: the asserted
`▶️ Transcribing and reading the response in w4:p4` becomes `⏹️ Audio stopped` (the observed
baseline failure), **and the suite terminates the operator's live player and deletes its state**.
A test suite that signals an unrelated live process is a hazard, not a flaky test.

A fourth path completes the boundary: `stop_audio` reaches the engine, and
`engine/src/agent_tts/constants.py:25` resolves its control socket as
`os.environ.get("AGENT_TTS_SOCKET", <tmpdir>/agent-tts-player.sock)`. Isolating only the three
bash paths would still let the engine leg touch a live daemon's socket.

### Override names

Derived mechanically from the file's own convention — `HERDR_TTS_` + the existing variable name,
exactly as `HERDR_TTS_DAEMON_PID_FILE` ↔ `DAEMON_PID_FILE`:

| Variable | New form | Default when unset (byte-identical to today) |
|---|---|---|
| `LOCK_FILE` | `${HERDR_TTS_LOCK_FILE:-…}` | `/tmp/herdr-tts-playing.lock` |
| `PID_FILE` | `${HERDR_TTS_PID_FILE:-…}` | `/tmp/herdr-tts-current.pid` |
| `IPC_SOCKET` | `${HERDR_TTS_IPC_SOCKET:-…}` | `/tmp/herdr-tts-player.sock` |
| *(engine)* | `AGENT_TTS_SOCKET` | already exists; set by the suite, untouched in production |

No other line changes. `is_playing`, `stop_audio`, and both writer sites (1707-1708, 1760-1761)
already reference the variables, never the literals.

**Alternatives considered**:

| Option | Why rejected |
|---|---|
| One `HERDR_TTS_RUNTIME_DIR` prefix for all three | Changes the default paths. The three defaults are sibling files directly in `/tmp` with unrelated basenames, not a shared directory; introducing a directory layer breaks the byte-identical-default requirement and silently orphans any running player. |
| Override `TMPDIR` in the suite | Does nothing — lines 26–28 are literal `/tmp/…`, not `$TMPDIR`-derived. It would move only the *engine's* socket. |
| Relax the 16n assertion to accept `Audio stopped` | Rejected outright. It would encode the hazard as expected behavior and delete the only proof that `r` reaches the read branch. The proposal and spec both forbid weakening this assertion. |
| Make the suite stop the host daemon first | Rejected: a test suite must never mutate host state to make itself pass. |

### Sandbox wiring

`new_env()` (`smoke-tests.sh:194-207`) gains four exports beside the two it already sets:

```bash
mkdir -p "$T/run"
export HERDR_TTS_LOCK_FILE="$T/run/playing.lock"
export HERDR_TTS_PID_FILE="$T/run/current.pid"
export HERDR_TTS_IPC_SOCKET="$T/run/player.sock"
export AGENT_TTS_SOCKET="$T/run/agent-tts-player.sock"
```

Because `new_env()` runs at the head of every scenario, this isolates the whole suite, not only
section 16. All four live in one rollback boundary with the launcher change: a partial revert that
leaves the suite pointing at host defaults is forbidden.

### Planned RED tests

Isolated fixtures and recorders only. A live host daemon is never a fixture.

| # | Test | RED before the fix |
|---|---|---|
| a | **Defaults preserved.** Static assertion over `bin/herdr-tts` that each default branch is byte-identical to today's literal, in the proven `assert_grep` style already used by `40g bootstrap pin still SHA-pinned` | Fails until the override form exists |
| b | **Read branch reached (16n, unchanged).** With no sandbox lock, `r` must produce `▶️ Transcribing and reading the response in w4:p4`; the existing frame-count and ordering assertions stay verbatim | Fails on a host with audio playing |
| c | **Stop branch stops only the sandbox.** Plant a sandbox lock/PID holding a live `sleep` PID; `r` prints `player.stopped`, that PID receives the TERM, and the three sandbox files are the ones removed | Fails — today the stop path reads and deletes host paths |
| d | **Host-state invariance.** Snapshot presence, inode, mtime, and content of the three host defaults plus the engine default socket before and after the **full** suite; assert unchanged, including "still absent if it was absent". The snapshot only reads; it never creates host state | Fails whenever a host player is active |

Test (d) is the non-interference proof the spec requires. It is deliberately suite-level: the
hazard is not specific to 16n, only its symptom was.

---

## Decision 9: Smoke 40e — immutable oracle identity across both engine layouts

**Choice**: re-anchor `AGENT_TTS_REF` to the maintainer-selected full SHA
`d66616bce3ad8193f11ae615bd58bb4508eb65be`, and rewrite the 40e identity driver's tree lookup to
be **return-code-checked** and **dual-layout** at that one revision. Strict byte identity for
`boundaries.py`, `cleaner.py`, and `redact.py` is retained; no assertion is weakened or deleted,
and engine bytes are never changed to satisfy a stale oracle.

### Three defects, one failure

| # | Defect | Evidence |
|---|---|---|
| 1 | **Stale pin.** `scripts/bootstrap.sh:12` pins `32e9bafbb113df847d7cd9b635b0e848ee182f6f`, a **pre-monorepo** tree. `32e9bafb:src/agent_tts/boundaries.py` resolves; `engine/src/agent_tts/…` does not exist there | `git rev-parse` at each path |
| 2 | **Single-layout lookup.** `smoke-tests.sh:3548` computes `rel = os.path.relpath(pkg_dir, repo)` from the *installed* package — `engine/src/agent_tts` in this monorepo — and probes only `{pin}:{rel}/{fname}`. Against a legacy-layout pin that path can never resolve | `smoke-tests.sh:3548-3556` |
| 3 | **Unchecked return codes.** Lines 3551-3556 take `subprocess.run(...).stdout.strip()` with no `returncode` check | `smoke-tests.sh:3551-3556` |

Defects 1 and 2 together are the deterministic baseline failure: the probe always misses, so
`pinned != local` always, so `40e oracle file identity == pinned ref` always reports `ERR`.

Defect 3 deserves its own emphasis because the obvious hardening is also wrong. **`git rev-parse`
does not fail quietly — it fails deceptively.** Verified in this repository:

```
$ git rev-parse d66616b…:src/agent_tts/boundaries.py ; echo rc=$?
fatal: path 'src/agent_tts/boundaries.py' does not exist in 'd66616b…'   # stderr
d66616b…:src/agent_tts/boundaries.py                                     # stdout
rc=128
```

On failure stdout is **not empty** — it echoes the argument back. So a guard written as "fail if
the output is empty" would never fire, and the only thing that saves the current code is that a
path string happens never to equal a 40-hex blob id. The guard must be the **return code**, plus a
shape assertion on the resolved value. The spec's "MUST NEVER pass by comparing empty output" is
therefore necessary but not sufficient on its own; this design satisfies both halves.

### Resolution algorithm

```
1. Verify the revision exists:  git cat-file -e <pin>^{commit}
      rc != 0  →  HARD FAILURE "pinned revision <pin> not present"  (no layout retry)
2. For each candidate layout, in order:
      L1 = engine/src/agent_tts     (monorepo)
      L2 = src/agent_tts            (legacy, pre-AT-10)
   probe all three files with  git rev-parse <pin>:<L>/<fname>
      every rc == 0 and every value matches ^[0-9a-f]{40}$  →  layout selected
      any rc != 0                                            →  try the next layout
3. No layout resolved all three  →  HARD FAILURE naming each unresolved file
   and BOTH probed paths.
4. Compare: git hash-object <pkg_dir>/<fname>  (rc checked, shape checked)
   against the selected layout's blob id, for all three files.
```

Design constraints this encodes:

- **One revision, two layouts.** The layout loop never changes the revision. Layout compatibility
  is a tree-shape accommodation, never a revision fallback — stated because the two are easy to
  conflate in code review.
- **All three from the same layout.** Mixing `boundaries.py` from L1 with `cleaner.py` from L2
  would compare against a tree that never existed. The layout is selected as a unit.
- **Revision miss ≠ layout miss.** Step 1 separates them, so an unknown pin reports "revision
  absent" rather than wandering into a misleading "file not found in either layout".
- **Fail closed, loudly.** Every failure path is an explicit `check(...)` with the file name and
  the probed paths. There is no branch on which a missing object yields a pass.

### Post-fix state is genuinely green

Verified read-only in this worktree, which is why this is a repair and not a weakening:

| File | Blob at `d66616b:engine/src/agent_tts/` | Working tree |
|---|---|---|
| `boundaries.py` | `7879e17c7fa5e019dcf999a830f06ee179e87a6f` | identical |
| `cleaner.py` | `a856e8b3e9b991ddc3f18d30ad5be9066c1b6aa5` | identical |
| `redact.py` | `0b46b42d53419387691ca971b460aaf65fffb9d6` | identical |

The existing `40g bootstrap pin still SHA-pinned` assertion
(`AGENT_TTS_REF="\$\{HERDR_AGENT_TTS_REF:-[0-9a-f]{40}\}"`) keeps passing unchanged: the new pin
is a full 40-hex SHA, and SHA pinning is preserved, never relaxed to a branch.

**Alternatives considered**:

| Option | Why rejected |
|---|---|
| Keep `32e9bafb` and probe only the legacy layout | The pin's engine source is stale relative to the current tree, so identity would fail on content instead of on path. It also leaves the public subdirectory install anchored to a revision with no `engine/` directory. |
| Drop the identity check for the drifting file | Deletes the assertion that makes the oracle trustworthy. Explicitly forbidden by the spec and proposal. |
| Edit engine bytes to match the stale oracle | Inverts the direction of truth: the oracle exists to verify the engine, not the reverse. |
| Pin a branch (`main`) so layout/content always match | Destroys immutability; a moving ref makes the oracle unfalsifiable. |
| Resolve via the local object database only | That is what the current code does, and it is why local success proves nothing about the public install (see below). |

### Local resolution is not public-retrievability evidence

Every check above runs against the **local** object database. That proves tree shape and byte
identity; it proves nothing about whether a fresh user can fetch this revision. Public retrieval of
`d66616bce3ad8193f11ae615bd58bb4508eb65be` from `https://github.com/chiptime/agent-tts.git` with
`#subdirectory=engine` is a **V2** obligation, carried by scenario 1 and tracked as **OQ-6**. If
that revision is not retrievable, the scenario reports `BLOCKED` with a recorded reason and the
blocker returns to the maintainer. No substitute SHA, no moving branch, no cache-only pass, and no
silent fallback — a locally resolvable object is explicitly *not* permission to roll forward.

### Planned RED tests

| # | Test | RED before the fix |
|---|---|---|
| a | **Pin value.** `bootstrap.sh` carries the full selected SHA; `40g`'s SHA-pinning assertion still passes | Fails on the stale pin |
| b | **Dual-layout resolution.** The driver resolves all three oracle files at the pinned revision under whichever of the two layouts that revision uses | Fails — monorepo-only probe |
| c | **Checked return codes, no decoy.** A deliberately absent path yields a hard, named failure; the driver never treats `git rev-parse`'s echoed-argument stdout as a blob id | Fails — rc ignored, decoy string silently compared |
| d | **Unknown revision.** A bogus revision fails as "revision absent", distinct from a layout miss, and never retries layouts | Fails — no revision pre-check exists |
| e | **Strict identity.** All three files byte-identical at the selected pin; the full existing F1–F9 assertion matrix runs unchanged | Fails via the mismatch above |

---

## Decision 10: Scenario 3 activates at milestone 2, not milestone 1

**Choice**: change `zero-machine-paths` in `scripts/acceptance/scenarios/registry.conf` from
`activates_at_milestone = 1` to `= 2`. The scenario stays **authored in M1** (already delivered by
task 1.3) and reports `NOT-YET-ACTIVATED` throughout M1.

### The inconsistency being repaired

Three statements currently disagree:

| Source | Claim |
|---|---|
| `registry.conf` row 3 | `activates_at_milestone = 1`, and `03-zero-machine-paths.sh` exists → scenario 3 is **ACTIVE at M1** |
| `tasks.md` 1.3 / note 2 | "activation at M1 per design … milestone-1 closure reports its truthful state" |
| `tasks.md` 2.2 | the machine-path defects (A1/C1/C2/C4 in the brain launcher) are removed **at M2** |
| Binding gate (Engram #9636) | an **active** `FAIL` or `BLOCKED` **prevents closure** |

Composing them: at M1 scenario 3 is active and must fail, because the behavior it asserts is not
delivered until M2. "Reporting its truthful state" does not rescue this — under the gate, a
truthful active `FAIL` is precisely what blocks the milestone. The previous wording tried to
occupy a middle position the gate does not offer.

**Resolution**: apply the gate's own rule — *activation follows delivered functionality*. Since
task 2.2 delivers the tested behavior, M2 is the correct activation milestone.

### Exact registry correction

```diff
-zero-machine-paths	1	03-zero-machine-paths.sh	V1+V2	No machine-specific paths leak from a clean install
+zero-machine-paths	2	03-zero-machine-paths.sh	V1+V2	No machine-specific paths leak from a clean install
```

Tab-separated, one field changed. The registry's own semantics then produce the intended state
without any special case: authored (file present) **and** `current < 2` → `NOT-YET-ACTIVATED`.

### What M1 still proves about machine paths

Activation moving to M2 removes no M1 coverage, because scenario 3 is `V1+V2` and only its V2 half
is milestone-gated:

- The **V1 half** — `engine/tests/test_versioned_tree_hygiene.py`, the static zero-machine-path
  scan — is an engine pytest, not a harness scenario. It runs and must be green at M1.
- Its scope manifest is seeded in task 1.3 over areas already clean and **extended inside each
  repair's own work unit** (1.4, 1.5, 1.6, then 2.2, 2.4), so the scan grows with the fixes and
  the three project suites stay green at every unit boundary.
- The **V2 half** activates at M2 together with task 2.2, which is the unit that finally makes it
  pass.

### Non-negotiables this decision does not relax

- M1 MUST NOT close with an active scenario 3 failing.
- Scenario 3 MUST NOT be labelled green, passed, or skipped at M1. `NOT-YET-ACTIVATED` is neither
  green nor red; it is reported in its own column.
- There is still no `SKIP` state (Decision 3).
- Slice/table corrections that follow from this: **slice 3 activates no V2 scenario**; **slice 10
  (task 2.2, M2) activates scenario 3**. Both are corrected in the slice table below.

**Alternatives considered**:

| Option | Why rejected |
|---|---|
| Keep M1 activation and accept a failing scenario 3 at M1 closure | Violates the binding gate; M1 could never close. |
| Keep M1 activation and call it `BLOCKED` | `BLOCKED` means a *prerequisite* is unavailable, and it is also red (exit 2). Undelivered functionality is exactly what `NOT-YET-ACTIVATED` is for; conflating them would make `BLOCKED` meaningless. |
| Add a `SKIP`/`EXPECTED-FAIL` state | Decision 3 rejected `SKIP` deliberately; an expected-fail state is how a real regression gets ignored. |
| Move the machine-path repair into M1 | Would mean relocating the brain launcher repair (task 2.2) and its parity tests out of M2 — a larger, unauthorized milestone reshuffle. Milestone count and content are frozen. |
| De-author scenario 3 until M2 | Discards delivered work and loses the authored-vs-activated distinction the registry exists to express. |

---

## Data flow

First run, plugin entry point:

    herdr plugin install …/tts-plugin
             │
             ▼
    [[build]] scripts/bootstrap.sh ──► venv + agent_tts (pinned engine/)
             │
    [[startup]] bin/herdr-tts _daemon-supervised
             │
             ├─ resolve PLUGIN_ROOT (readlink -f, own location)
             ├─ marker ~/.config/herdr-tts/first-run.done exists? ──yes──► start daemon
             │                        │no
             │                        ▼
             │              TTY? ──no──► start daemon + print noninteractive hint
             │               │yes
             │               ▼
             └────► PYTHONPATH=<root>/tools  venv/bin/python -m herdr_onboarding --role plugin
                                 │
                    ┌────────────┴─────────────┬──────────────┬─────────────┐
                    ▼                          ▼              ▼             ▼
              credentials                   voice        keymap adopt     health
              getpass / fd                 provider      + apply          /health tts:ok
                    │                          │         + reload         plugin list clean
                    ▼                          ▼              │             │
        ~/.config/herdr-brain/env       plugin config.env     │             │
              (mode 600, atomic)                              │             │
                    └──────────────────┬───────────────────────┴─────────────┘
                                       ▼
                        all green? ──yes──► write completion marker
                                    └─no──► NO marker written; next launch retries

Both deployment modes read the **same** persisted configuration, so the plugin `[[startup]]`
route and the optional systemd route cannot diverge:

    ~/.config/herdr-brain/env   ◄────────┬──────── bin/herdr-brain (plugin [[startup]])
    ~/.config/herdr-tts/config.env       └──────── generated systemd unit (EnvironmentFile=)

---

## File changes

| File | Action | Description |
|---|---|---|
| `scripts/acceptance/clean-install.sh` | Create | V2 harness: sandbox, scenario registry, activation model, journal, exit codes. **Milestone 1, task 1** |
| `scripts/acceptance/allowed-origins.txt` | Create | Documented-origin allowlist, cross-checked against docs by a V1 test |
| `scripts/acceptance/scenarios/*.sh` | Create | One file per scenario, sourced by the harness |
| `scripts/acceptance/scenarios/registry.conf` | Modify | **Decision 10**: `zero-machine-paths` `activates_at_milestone` `1` → `2`. One field, tab-separated |
| `tools/herdr_onboarding/` | Create | Shared wizard + doctor detection (stdlib only) |
| `hosts/herdr/tts-plugin/scripts/install.sh` | Modify | Monorepo `CANONICAL_URL`/source, `~/.local/bin` exposure, PATH warning, wizard hand-off, extended uninstall print |
| `hosts/herdr/tts-plugin/scripts/bootstrap.sh` | Modify | Dev checkout derived from own location → `engine/`; drop `~/Code/personal/agent-tts`. **Decision 9**: `AGENT_TTS_REF` → full SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be`, SHA pinning preserved |
| `hosts/herdr/tts-plugin/bin/herdr-tts` | Modify | `--no-first-run`, first-run marker check, `doctor` dispatch, onboarding resolver. **Decision 8**: lines 26–28 gain `HERDR_TTS_LOCK_FILE` / `HERDR_TTS_PID_FILE` / `HERDR_TTS_IPC_SOCKET` overrides with byte-identical defaults; no other line changes |
| `hosts/herdr/brain/bin/herdr-brain` | Modify *(after rebase)* | Remove dead `HERDR_TTS_HOME` default, dotfiles scrape, literal brew prefix, personal tailnet; ownership-aware port handling; `doctor` + first-run dispatch |
| `hosts/herdr/brain/herdr-plugin.toml` | Modify *(after rebase)* | Already tracked on `main`; adjust only if actions change |
| `hosts/herdr/brain/deploy/install.sh` | Modify | Drop the dotfiles dependency, generate the unit from the template, ownership-aware port policy, English output |
| `hosts/herdr/brain/deploy/herdr-brain.service` | Delete | Replaced by the template |
| `hosts/herdr/brain/deploy/herdr-brain.service.tmpl` | Create | Placeholders `@HERDR_BIN@ @INSTALL_DIR@ @PYTHON@ @PORT@ @ENV_FILE@` |
| `hosts/herdr/brain/src/herdr_brain/config.py` | Modify | Port knob plumbing; optional remote-exposure domain from config/env |
| `hosts/herdr/tts-plugin/packaging/npm/{package.json,bin/herdr-tts}` | Modify | Legacy `chiptime/herdr-tts` URLs → monorepo; honest support wording |
| `hosts/herdr/tts-plugin/packaging/homebrew/herdr-tts.rb` | Modify | Monorepo url/homepage; document the wizard limitation on the keg route |
| `hosts/herdr/tts-plugin/README.md` | Modify | Remove B1–B4 legacy commands; tagged `id=` install blocks |
| `hosts/herdr/brain/README.md`, `README.md` | Modify | Canonical flow, optional systemd, advanced remote exposure, tagged blocks |
| `docs/installation/V3-checklist.md` | Create | Timed human UAT checklist with both timing readings |
| `hosts/herdr/brain/tests/test_first_run.py`, `test_doctor.py`, `test_resolve.py` | Create | V1 for wizard, doctor, resolution |
| `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | Modify | New scenarios: CLI exposure, marker/skip, resolver reachability, doctor dispatch. **Decision 8**: `new_env()` exports the three playback overrides + `AGENT_TTS_SOCKET`; default-preservation, sandbox-stop, and host-state-invariance assertions. **Decision 9**: 40e driver gains revision pre-check, dual-layout probe, checked return codes |
| `engine/tests/test_versioned_tree_hygiene.py` | Create | Static zero-machine-path scan over the versioned tree (V1 half of scenario 3) |
| `.gitignore` | Modify | Ignore generated `deploy/*.service` |
| `openspec/config.yaml`, `hosts/herdr/tts-plugin/openspec/config.yaml` | Modify | Bounded corrections: registry-scope wording; stale "engine is a separate external repo" statement |

---

## Interfaces / contracts

### Wizard CLI (internal contract between bash entry points and the shared module)

```
python -m herdr_onboarding
  --role {plugin,brain}        required; selects which steps are offered
  --no-first-run               exit 0 immediately, write no marker
  --non-interactive            never prompt; consume flags/env; fail if an answer is missing
  --doctor                     run checks only, no mutation
  --voice-provider {edge,openai,elevenlabs,piper}
  --keymap-style {menu,direct,ctrlalt,none}
  --stt {tiny,base,small,none} none = explicit refusal
  --json                       machine-readable result for the harness

exit: 0 completed (marker written) · 10 completed with no marker (skip/no-first-run)
      20 answer missing in non-interactive mode · 30 health gate failed (retryable)
      40 user aborted (no partial state)
```

Secrets never appear in this surface.

### Completion marker

`~/.config/herdr-tts/first-run.done` (mode 644) — JSON:

```json
{"version": 1, "completed_at": "2026-09-30T18:00:00Z", "role": "plugin",
 "stt": "base", "voice": "edge", "keymap": "menu"}
```

Written **only** after the health gate passes. A failed run leaves no marker, so the next
launch retries. Deleting the file is the documented "run onboarding again" action.

### Systemd template placeholders

| Placeholder | Source |
|---|---|
| `@INSTALL_DIR@` | resolved brain root (own-location derived) |
| `@PYTHON@` | `<install_dir>/.venv/bin/python` |
| `@HERDR_BIN@` | Decision 5 discovery order |
| `@PORT@` | `HERDR_BRAIN_PORT` → config → `8741` |
| `@ENV_FILE@` | `${XDG_CONFIG_HOME:-$HOME/.config}/herdr-brain/env` |

Unsubstituted `@…@` remaining after generation is a hard install failure, asserted by a V1 test.

### Playback-state environment overrides (Decision 8)

New public knobs on `bin/herdr-tts`. Production behavior with all four unset is byte-identical to
today; they exist so a test environment — or a packager running two installations — can relocate
runtime state without touching the host.

| Variable | Overrides | Default when unset |
|---|---|---|
| `HERDR_TTS_LOCK_FILE` | playback lock (`is_playing` gate) | `/tmp/herdr-tts-playing.lock` |
| `HERDR_TTS_PID_FILE` | current player PID (`stop_audio` target) | `/tmp/herdr-tts-current.pid` |
| `HERDR_TTS_IPC_SOCKET` | launcher-side player control socket | `/tmp/herdr-tts-player.sock` |
| `AGENT_TTS_SOCKET` | engine control socket (pre-existing, `engine/src/agent_tts/constants.py:25`) | `<tmpdir>/agent-tts-player.sock` |

The toggle semantics are unchanged: a live PID in the lock still means "playing", and `r` still
stops rather than reads. Only *which files* are consulted becomes configurable.

### Immutable engine pin (Decision 9)

| Field | Value |
|---|---|
| `AGENT_TTS_REF` | `d66616bce3ad8193f11ae615bd58bb4508eb65be` (full 40-hex SHA; `HERDR_AGENT_TTS_REF` override retained for testing) |
| Install source | `git+https://github.com/chiptime/agent-tts.git@<SHA>#subdirectory=engine` |
| Oracle layouts probed, in order | `engine/src/agent_tts/` then `src/agent_tts/`, both at that one revision |
| Oracle files, strict byte identity | `boundaries.py`, `cleaner.py`, `redact.py` |

---

## Testing strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit (V1) | Resolution order (root, `HERDR_BIN`, port, `HERDR_TTS_HOME`), template substitution, env-file merge/permissions, redaction, doctor remediation completeness, marker lifecycle | `hosts/herdr/brain/tests/` pytest; pure functions with injected env dicts and `tmp_path` |
| Static (V1) | Zero machine-specific strings in the versioned tree; allowlist ↔ docs equivalence; harness block ids exist; no `set -x` in secret-handling scripts | `engine/tests/test_versioned_tree_hygiene.py`, modelled on the existing `test_monorepo_boundaries.py` guard |
| Integration (V1) | Installer/bootstrap/launcher behavior with stubbed `herdr`/`git`/`uv`; CLI exposure; keymap preservation; wizard reachability from three simulated installed layouts | `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` new scenarios (hermetic stubs, existing convention) |
| **Suite hermeticity (V1, Decision 8)** | Playback lock/PID/socket + engine socket resolve inside `new_env()`; defaults byte-identical when unset; `r` reaches transcribe/read with its confirmation assertion intact; the stop branch signals only a sandbox PID; host playback state and the engine default socket are unchanged across a full suite run | `smoke-tests.sh` — section 16 (unchanged assertions) plus the four RED tests in Decision 8; isolated fixtures/recorders only, never a live host daemon |
| **Oracle identity (V1, Decision 9)** | Revision pre-check; dual-layout resolution at one revision; checked return codes with a shape assertion on every resolved blob id; explicit named failure for a missing revision or file; strict byte identity for all three oracle files with the full F1–F9 matrix intact | `smoke-tests.sh` section 40e driver + the existing `40g` SHA-pinning assertion |
| **Public pin retrieval (V2, OQ-6)** | The documented GitHub route fetches exactly `d66616bce3ad8193f11ae615bd58bb4508eb65be` and installs `engine/`; unavailability is `BLOCKED` with a recorded reason and no substitute | `clean-install.sh` scenario 1; local object resolution is explicitly **not** accepted as this evidence |
| Compatibility (V1, RNF-4) | Observable parity before/after each audit fix — notably `bin/herdr-brain` resolving the plugin with `HERDR_TTS_HOME` unset | Paired assertions: legacy-shaped layout and corrected layout both reach a found TTS surface |
| Acceptance (V2) | The nine scenarios, clean-room, literal documented steps | `scripts/acceptance/clean-install.sh --milestone N` |
| Human (V3) | Timed clean-machine UAT, friction log, sign-off | `docs/installation/V3-checklist.md`; outside the automated loop |

Project suites are preserved and run from their own working directories:

| Working directory | Command |
|---|---|
| `engine/` | `python -m pytest tests/ -q` |
| `hosts/herdr/brain/` | `python -m pytest tests/ -q && node --test tests/js/` |
| `hosts/herdr/tts-plugin/` | `bash scripts/smoke-tests.sh` |

`strict_tdd: false` at the workspace root: tests ship **with** each work unit, not strictly
before. The plugin subproject's local `strict_tdd: true` still governs `smoke-tests.sh`
scenarios — RED scenario first, then `bin/herdr-tts`. No coverage, lint, or type-check claim is
made anywhere; none of those tools exist in this workspace.

---

## Threat matrix

This change modifies shell commands, subprocesses, VCS operations, executable-file exposure, and
process integration. The matrix is **applicable**.

| Boundary | Adversarial cases | Applicability | Design response | Planned RED tests |
|---|---|---|---|---|
| Documentation-like paths | Fenced blocks in `README.md` treated as executable; an attacker-or-accident-added block; `id=` collision | **Applicable** | Harness executes blocks **by id from a pinned file allowlist** only; never scans-and-runs; duplicate or missing id is a hard failure | (a) unknown id → non-zero, nothing executed; (b) duplicate id in one file → non-zero; (c) block added to a non-allowlisted file → never executed |
| Git repository selection | `git -C` vs cwd; installer run from inside an unrelated repo; relative vs absolute target | **Applicable** | Every git call passes an absolute `-C`; target derived from `XDG_DATA_HOME`, never cwd; remote-mismatch abort retained | (a) run installer with cwd inside a foreign git repo → foreign repo untouched, `git status` clean; (b) relative `TARGET` rejected; (c) mismatched origin aborts writing nothing |
| Commit state | staged / `commit -a` / empty index | **N/A** — this change creates no commits programmatically. Commits are authored by the developer through the normal flow | — | — |
| Push state | tracking branch, first push, explicit refspec | **N/A** — no push or remote-write automation exists or is added; explicitly forbidden by the change constraints | — | — |
| PR commands | `--head`, env prefix, composed commands | **N/A** — no PR automation in this change | — | — |
| **Process / port ownership** *(change-specific)* | Foreign process on the configured port; systemd `MainPID` vs stray `nohup`; PID reuse between probe and signal | **Applicable** | Signal only proven-owned processes (our pidfile, or the unit's `MainPID`); otherwise exit non-zero naming PID, port, and two remediations. Re-verify ownership immediately before signalling | (a) foreign listener on the port → non-zero, **process still alive**; (b) own stale pidfile → cleaned without signalling a reused PID; (c) unit-owned listener → managed via `systemctl`, not `kill` |
| **Executable-file exposure** *(change-specific)* | `~/.local/bin/herdr-tts` already exists as a user file; exists as a symlink to something else; `~/.local/bin` is a file not a directory | **Applicable** | Create the directory only when absent; refuse to clobber a non-managed file; managed artifacts carry a recognisable marker and are refreshed, not blindly overwritten | (a) pre-existing unmanaged `herdr-tts` → refuse, exit non-zero, name it; (b) `~/.local/bin` exists as a regular file → actionable failure; (c) managed artifact from a previous install → refreshed in place |
| **Network origin boundary** *(change-specific)* | Redirect to a non-allowlisted host; scheme-relative URL; origin present in docs but absent from the allowlist | **Applicable** | Wrapper shim resolves the final host and refuses non-allowlisted origins (`BLOCKED-ORIGIN:`); a V1 test enforces allowlist ↔ documentation equivalence | (a) request to a non-allowlisted host → blocked, scenario `BLOCKED` not `PASS`; (b) doc URL missing from allowlist → V1 fails; (c) allowlist entry absent from docs → V1 fails |
| **Secret handling** *(change-specific)* | Secret in argv; in `/proc/environ`; in logs; in an exception message; partial env file after a crash | **Applicable** | fd/file intake, never argv; single `redact()` boundary; atomic `mkstemp`+`chmod 600`+`os.replace`; `set -x` prohibited and statically asserted | (a) `ps` snapshot during a non-interactive run contains no secret; (b) every log/stdout byte of a **failed** run contains no secret; (c) simulated crash mid-write leaves no readable partial env file |
| **Test-suite host interference** *(Decision 8)* | A test run reads a **live** player's lock and takes the stop branch; `stop_audio` sends `kill -TERM` to a PID the suite does not own; `rm -f` deletes a live installation's lock/PID/socket; the engine leg reaches a live daemon over the default `AGENT_TTS_SOCKET`; a PID in the lock is reused between read and signal | **Applicable** | All three launcher state paths become env-overridable with byte-identical defaults and are set sandbox-local in `new_env()`, together with `AGENT_TTS_SOCKET`; isolated fixtures/recorders stand in for daemon state; the suite never uses a live daemon as a fixture and never writes to a host default path | (a) with no sandbox lock, `r` reaches transcribe/read and the existing confirmation assertion passes; (b) a planted **sandbox** lock with a live PID → stop branch signals only that PID and removes only the sandbox files; (c) full-suite host-state invariance: presence, inode, mtime, and content of the three host defaults and the engine default socket unchanged, including still-absent when absent; (d) with overrides unset, the three defaults are byte-identical to today's literals |
| **Immutable dependency pin / oracle identity** *(Decision 9)* | `git rev-parse` fails with rc≠0 while echoing its argument to **stdout**, so an emptiness-based guard never fires; a layout miss is mistaken for a revision miss; oracle files resolved from two different layouts (a tree that never existed); layout compatibility silently used as a revision fallback; a locally present object mistaken for public retrievability; pin relaxed to a moving branch | **Applicable** | Revision existence pre-checked once (`cat-file -e <pin>^{commit}`) and separated from layout misses; dual-layout probe selects a layout **as a unit**, all three files or none; every lookup asserts `returncode == 0` **and** `^[0-9a-f]{40}$` on the value; full SHA pinning retained and statically asserted by `40g`; public retrieval is a separate V2 obligation (OQ-6) that cannot be satisfied locally | (a) absent path → hard named failure, echoed-argument stdout never compared as a blob id; (b) bogus revision → "revision absent", distinct from a layout miss, with no layout retry; (c) all three files resolve under one layout at one revision; (d) strict byte identity for all three with the F1–F9 matrix unchanged; (e) V2 retrieval of the exact SHA from the documented origin, or `BLOCKED` with a recorded reason and no substitute |

Applicable rows carry into `tasks.md` unchanged; each becomes a RED test before its production
change. `N/A` rows generate no tasks.

The two new rows do not change the `N/A` verdicts above them: Decision 8 and Decision 9 still
create no commits, no pushes, and no PR automation.

---

## Migration / rollout

- **No data migration.** Existing `~/.config/herdr-brain/env`, plugin `config.env`, keymaps, and
  the STT model cache are read and preserved. The wizard merges; it never rewrites wholesale.
- **No feature flags.** `--no-first-run` and the completion marker already provide the opt-out
  surface; a second flag system would be redundant.
- **Maintainer's machine.** Its `plugins.json` points at deleted `~/Code/personal/herdr-{brain,tts}`
  (audit A2, confirmed still present). The repair is **documented, not executed** — this change
  makes no live plugin-registration change. A documented `herdr plugin unlink` + reinstall
  sequence goes into the installation docs; running it is the maintainer's explicit action.
- **Unit replacement.** On upgrade, the generated unit replaces the previously copied static
  unit. The installer stops only the AT-11-owned unit, regenerates, reloads, and restarts.
- **Rollout order** is the milestone order; each slice is independently revertible with its
  tests and docs.

---

## Milestone and slice mapping

Auto-chain, ≤400 authored additions + deletions per slice, tests and user-facing docs travel
with their code. Harness-first is mandatory.

| # | Slice | Milestone | Est. lines | Activates V2 |
|---|---|---|---|---|
| 0 | Rebase onto `main` (Decision 0) — no authored lines | pre-M1 | 0 | — |
| 1 | **Harness core**: sandbox, PATH/HOME/XDG isolation, registry, activation model, journal, exit codes | M1 | ~380 | — |
| 2 | Harness: origin allowlist + shim, doc-block `id=` extractor, allowlist↔docs V1 test | M1 | ~220 | — |
| 3 | Scenario 3 `zero-machine-paths` **authored** + static hygiene test (V1 half) | M1 | ~180 | — *(authored, not activated — Decision 10)* |
| 3a | **Scenario 3 activation correction** (Decision 10): `registry.conf` `activates_at_milestone` `1` → `2` | M1 | ~15 | — |
| 4 | Installer source corrections (B1–B4, `CANONICAL_URL`, monorepo route) + smoke scenarios | M1 | ~260 | 1 |
| 5 | Bootstrap dev-mode `engine/` derivation (B5) + scenarios | M1 | ~150 | — |
| 6 | Packaging wrappers (npm, homebrew) legacy refs + honest support docs | M1 | ~140 | — |
| 7 | Bounded OpenSpec config corrections | M1 | ~40 | — |
| 8 | Scenarios 1 + 2 `plugin-fresh-clone`, `plugin-subdir-install` (**OQ-1 probe**, **OQ-6 pin retrieval**) | M1 | ~240 | 1, 2 |
| 8a | **Smoke 16n host-safety isolation** (Decision 8): three launcher overrides + `new_env()` wiring + four RED tests | M1 | ~130 | — |
| 8b | **Smoke 40e oracle identity** (Decision 9): selected pin + revision pre-check, dual-layout probe, checked return codes | M1 | ~140 | — |
| 9 | `resolve.py` + bash resolvers: root, `HERDR_BIN`, port, `HERDR_TTS_HOME` (ex-OQ-2 **resolved**) | M2 | ~300 | — |
| 10 | Brain launcher repair: A1/C1/C2/C4 removal + parity tests (RNF-4) | M2 | ~280 | **3 (activates here)** |
| 11 | Ownership-aware port policy (C5) in launcher and installer | M2 | ~200 | — |
| 12 | Systemd template + generation + `deploy/install.sh` rewrite | M2 | ~340 | — |
| 13 | CLI exposure `~/.local/bin` + PATH warning + uninstall print | M2 | ~190 | — |
| 14 | Scenario 7 `reinstall-idempotent` | M2 | ~160 | 7 |
| 15 | Wizard skeleton: package, resolver, CLI, marker, `--no-first-run`, noninteractive; reachability matrix tests | M3 | ~360 | — |
| 16 | Credential capture: getpass, fd/file intake, atomic 600 writer, redaction | M3 | ~320 | — |
| 17 | Voice + keymap steps: adopt/apply/auto-reload, existing-keymap preservation | M3 | ~240 | — |
| 18 | STT consent step: size selection, download, contract verify, refusal path (ex-OQ-3 **resolved**: assert `unavailable`) | M3 | ~260 | 5, 6 |
| 19 | Entry-point wiring both sides + health gate + scenario 4 | M3 | ~280 | 4 |
| 20 | Doctor: six checks + actionable remediation + two dispatchers | M4 | ~330 | — |
| 21 | Scenarios 8 + 9 `doctor-diagnoses-break`, `post-wizard-health` | M4 | ~220 | 8, 9 |
| 22 | Documentation: canonical flow, systemd, advanced remote exposure, tagged blocks | M4 | ~300 | — |
| 23 | V3 checklist + final RF↔evidence traceability matrix | M4 | ~200 | — |

### Milestone-1 slice dependencies

The three new M1 slices are cohesive and independently revertible. Their ordering constraints:

```
    slice 3 (scenario 3 authored)
         │
         ▼
    slice 3a  registry activation 1 → 2        ── must land in M1, before M1 closure
                                                   (otherwise an active scenario 3 fails)
    slice 5 (bootstrap dev-mode engine/)
         │  same file, different concern — do NOT combine
         ▼
    slice 8b  40e: pin + oracle driver  ───┐
                                            ├──►  plugin suite green  ──►  M1 closure
    slice 8a  16n: playback isolation  ────┘
```

| Slice | Depends on | Why | Rollback boundary |
|---|---|---|---|
| 3a | slice 3 | The row it edits is only meaningful once the scenario file exists | Single registry field; reverting re-exposes the inconsistency, so it must not be reverted alone |
| 8a | none | Touches `bin/herdr-tts:26-28` and `new_env()`; no M1 slice shares those lines | Launcher overrides **and** `new_env()` exports revert together — a partial revert leaving the suite on host defaults restores the hazard and is forbidden |
| 8b | slice 5 | Both edit `scripts/bootstrap.sh`. Sequenced, never merged: the pin is a supply-chain decision, the dev-mode derivation is a path fix, and they need separate rollback | Pin **and** 40e driver revert together with their evidence. Restoring `32e9bafb` restores the known baseline failure, not a release |

**M1 closure gate.** The plugin V1 suite counts as green only after **both** 8a and 8b land. Until
then the suite has a known red (40e) and a known hazard (16n), and M1 cannot close. Additionally:

- No baseline exception exists. Neither failure may be waived, skipped, disabled, or relabelled.
- Scenario 3 is reported `NOT-YET-ACTIVATED` at M1 — never green, never passed, never skipped.
- Scenarios 1 and 2 require V2 network access, which is authorized for documented origins. OQ-6
  (public retrieval of the selected pin) is proven there or recorded `BLOCKED` with no substitute.

**Guard lines** (forecast; `sdd-tasks` owns the binding values):
`Decision needed before apply: No` — `Chained PRs recommended: Yes` — `400-line budget risk: High`

No decision gate remains before apply: every product decision is resolved (see *Decision status*).
OQ-1 and OQ-6 are evidence questions answered **by** running V2 scenarios, not gates that block
starting them. The budget risk stays High because the change spans ~26 slices across four
milestones.

Milestone 4 closes only with `clean-install.sh --milestone 4` green across all nine scenarios,
plus the three project suites and the V1 set.

---

## Open questions

**No open question blocks implementation.** Both remaining items are answered by *running a test*,
not by a decision, and each has a pre-designed response to either outcome.

- [ ] **OQ-1 — Subdirectory install materialization** *(evidence)*. Does `herdr plugin install
      chiptime/agent-tts/hosts/herdr/tts-plugin` clone the whole monorepo (`managed_path`) and
      point `plugin_root` at the subdirectory? Binary-symbol and installed-plugin evidence say
      yes; a subdirectory install has not been observed, and observing one would mutate the live
      plugin registry, which this change forbids. *Resolved by:* slice 8, V2 scenario
      `plugin-subdir-install`. *If falsified:* adopt the pre-designed vendoring fallback in
      Decision 1 — no redesign, one extra slice.
- [ ] **OQ-6 — Public retrieval of the selected engine pin** *(evidence; was implicit in
      Decision 9)*. Is `d66616bce3ad8193f11ae615bd58bb4508eb65be` retrievable from
      `https://github.com/chiptime/agent-tts.git` with `#subdirectory=engine`? The revision and
      its three oracle blobs are present **locally**, which proves tree shape and byte identity
      and nothing about public reachability. *Resolved by:* slice 8, V2 scenario
      `plugin-fresh-clone`. *If unavailable:* report `BLOCKED` with a recorded reason and return
      the blocker to the maintainer. No substitute SHA, no moving branch, no cache-only pass, no
      silent fallback.

### Resolution record (closed — do not reopen)

| Was | Resolution | Source |
|---|---|---|
| OQ-2 — `HERDR_TTS_HOME` precedence | Explicit export **wins**; own-location is the discovery default only. Literal RF-8 reading rejected | Engram #9681 → Decision 4 |
| OQ-3 — STT health vocabulary | `/health` `stt` is `loading \| ready \| unavailable`; `unavailable` after refusal; `degraded` reserved for `tts`; RF-12 is errata | Engram #9681 → Decision 6 |
| OQ-4 — Base correction | Authorized **and completed**; the worktree is fast-forwarded. Audit A3 re-scored *resolved on `main`*; the launcher/manifest are repaired, not recreated | Decision 0 |
| OQ-5 — V2 network authorization | **Granted**, bounded to documented PyPI/uv, GitHub, and the M3 STT model origin. Anything outside the allowlist is `BLOCKED`. No push/PR/remote git | Engram #9681 |
| V2 milestone gate | Active-scenario interpretation: activation follows delivered functionality; all active scenarios green; no regression of previously green scenarios; full suite green at M4 | Engram #9636 → Decisions 3 and 10 |
| Baseline V1 failures | No baseline exception. 16n and 40e are fixed in M1 | Decisions 8 and 9 |
| "Decision 9 awaits a maintainer answer" | Never existed. The STT question lived in Decision 6; Decision 9 below is new and resolved | this document |
