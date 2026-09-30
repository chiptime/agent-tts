# Design: AT-11 — Independent Installation and First-Run Onboarding

This design answers **how** the five AT-11 specs are implemented: where the one shared
first-run wizard lives and how both installed entry points reach it, how secrets are
captured without ever touching argv, how the V2 clean-install harness is built (milestone 1,
task 1), how paths/ports/binaries stop being machine-specific, and how the four milestones
decompose into review slices under the 400-line budget.

**Read first:** Decision 0 (worktree base divergence) is a blocker-class finding that changes
the milestone-1 plan. Decisions 6 and 9 need a maintainer answer before the affected slices
are implemented; every other decision is resolved here.

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
  still behind on the other three commits, which are documentation-only and harmless to take.
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
| 3 | `zero-machine-paths` | Cero rutas de máquina | V1+V2 | M1 |
| 4 | `first-run-keys` | Primera ejecución con claves | V2 | M3 |
| 5 | `stt-consent-download` | Descarga de modelo STT con consentimiento | V2 | M3 |
| 6 | `stt-refusal-degrades` | Rechazo de la descarga degrada sin romper | V2 | M3 |
| 7 | `reinstall-idempotent` | Reinstalación idempotente conserva estado | V2 | M2 |
| 8 | `doctor-diagnoses-break` | Doctor diagnostica rotura simulada | V2 | M4 |
| 9 | `post-wizard-health` | Salud final post-asistente | V2 (+V3) | M4 |

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
`hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (4143 lines, 487 assertions) rather than
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

### The `HERDR_TTS_HOME` precedence question (RF-AT-11-8) — Open question OQ-2

RF-AT-11-8 reads: *"la resuelve relativa a su propia ubicación (`../tts-plugin`) **o del
entorno, en ese orden**"* — literally, own-location first, environment second.

Taking that literally would make an explicitly exported `HERDR_TTS_HOME` **ignored** whenever a
sibling `tts-plugin` directory exists. That contradicts `hosts/herdr/brain/src/herdr_brain/config.py:122`,
which treats the variable as an override, and it removes the only escape hatch for a split
deployment.

**Recommendation**: read "in that order" as describing *which default replaces the legacy
hardcoded one*, not as inverting override precedence. Concretely:

- `HERDR_TTS_HOME` set and valid → use it (unchanged override semantics).
- Unset → derive from the launcher's own location.
- Never export a hardcoded default.

This is exactly what repairs audit A1: the defect is not that the environment wins, it is that
`bin/herdr-brain` *exported a dead default*, overriding `config.py`'s better one. Deleting the
bad default fixes the bug without inverting a working contract.

**Maintainer confirmation required** before the milestone-2 launcher slice lands, because the
literal reading and the recommended reading produce observably different behavior.

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

## Decision 6: STT health vocabulary recommendation — Open question OQ-3

**Recommendation**: keep `unavailable`. Report `/health` `stt` as **`loading | ready | unavailable`**,
with `unavailable` after refusal. Do **not** introduce `degraded` for `stt`.

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

**Maintainer confirmation required.** Until it is given, tests assert only that the value is
consistent with the frozen contract and with the user's choice, and no test bakes in either
literal. Both frozen contracts remain byte-identical either way.

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
| `tools/herdr_onboarding/` | Create | Shared wizard + doctor detection (stdlib only) |
| `hosts/herdr/tts-plugin/scripts/install.sh` | Modify | Monorepo `CANONICAL_URL`/source, `~/.local/bin` exposure, PATH warning, wizard hand-off, extended uninstall print |
| `hosts/herdr/tts-plugin/scripts/bootstrap.sh` | Modify | Dev checkout derived from own location → `engine/`; drop `~/Code/personal/agent-tts` |
| `hosts/herdr/tts-plugin/bin/herdr-tts` | Modify | `--no-first-run`, first-run marker check, `doctor` dispatch, onboarding resolver |
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
| `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` | Modify | New scenarios: CLI exposure, marker/skip, resolver reachability, doctor dispatch |
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

---

## Testing strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit (V1) | Resolution order (root, `HERDR_BIN`, port, `HERDR_TTS_HOME`), template substitution, env-file merge/permissions, redaction, doctor remediation completeness, marker lifecycle | `hosts/herdr/brain/tests/` pytest; pure functions with injected env dicts and `tmp_path` |
| Static (V1) | Zero machine-specific strings in the versioned tree; allowlist ↔ docs equivalence; harness block ids exist; no `set -x` in secret-handling scripts | `engine/tests/test_versioned_tree_hygiene.py`, modelled on the existing `test_monorepo_boundaries.py` guard |
| Integration (V1) | Installer/bootstrap/launcher behavior with stubbed `herdr`/`git`/`uv`; CLI exposure; keymap preservation; wizard reachability from three simulated installed layouts | `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` new scenarios (hermetic stubs, existing convention) |
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

Applicable rows carry into `tasks.md` unchanged; each becomes a RED test before its production
change. `N/A` rows generate no tasks.

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
| 3 | Scenario 3 `zero-machine-paths` + static hygiene test (V1 half) | M1 | ~180 | 3 |
| 4 | Installer source corrections (B1–B4, `CANONICAL_URL`, monorepo route) + smoke scenarios | M1 | ~260 | 1 |
| 5 | Bootstrap dev-mode `engine/` derivation (B5) + scenarios | M1 | ~150 | — |
| 6 | Packaging wrappers (npm, homebrew) legacy refs + honest support docs | M1 | ~140 | — |
| 7 | Bounded OpenSpec config corrections | M1 | ~40 | — |
| 8 | Scenarios 1 + 2 `plugin-fresh-clone`, `plugin-subdir-install` (**OQ-1 probe**) | M1 | ~240 | 1, 2 |
| 9 | `resolve.py` + bash resolvers: root, `HERDR_BIN`, port, `HERDR_TTS_HOME` (**needs OQ-2**) | M2 | ~300 | — |
| 10 | Brain launcher repair: A1/C1/C2/C4 removal + parity tests (RNF-4) | M2 | ~280 | 3 |
| 11 | Ownership-aware port policy (C5) in launcher and installer | M2 | ~200 | — |
| 12 | Systemd template + generation + `deploy/install.sh` rewrite | M2 | ~340 | — |
| 13 | CLI exposure `~/.local/bin` + PATH warning + uninstall print | M2 | ~190 | — |
| 14 | Scenario 7 `reinstall-idempotent` | M2 | ~160 | 7 |
| 15 | Wizard skeleton: package, resolver, CLI, marker, `--no-first-run`, noninteractive; reachability matrix tests | M3 | ~360 | — |
| 16 | Credential capture: getpass, fd/file intake, atomic 600 writer, redaction | M3 | ~320 | — |
| 17 | Voice + keymap steps: adopt/apply/auto-reload, existing-keymap preservation | M3 | ~240 | — |
| 18 | STT consent step: size selection, download, contract verify, refusal path (**needs OQ-3**) | M3 | ~260 | 5, 6 |
| 19 | Entry-point wiring both sides + health gate + scenario 4 | M3 | ~280 | 4 |
| 20 | Doctor: six checks + actionable remediation + two dispatchers | M4 | ~330 | — |
| 21 | Scenarios 8 + 9 `doctor-diagnoses-break`, `post-wizard-health` | M4 | ~220 | 8, 9 |
| 22 | Documentation: canonical flow, systemd, advanced remote exposure, tagged blocks | M4 | ~300 | — |
| 23 | V3 checklist + final RF↔evidence traceability matrix | M4 | ~200 | — |

**Guard lines** (forecast; `sdd-tasks` owns the binding values):
`Decision needed before apply: Yes` — `Chained PRs recommended: Yes` — `400-line budget risk: High`

Milestone 4 closes only with `clean-install.sh --milestone 4` green across all nine scenarios,
plus the three project suites and the V1 set.

---

## Open questions

- [ ] **OQ-1 — Subdirectory install materialization.** Does `herdr plugin install
      chiptime/agent-tts/hosts/herdr/tts-plugin` clone the whole monorepo (`managed_path`) and
      point `plugin_root` at the subdirectory? Binary-symbol and installed-plugin evidence say
      yes; a subdirectory install has not been observed. *Resolved by:* slice 8, V2 scenario
      `plugin-subdir-install`. *If falsified:* adopt the pre-designed vendoring fallback in
      Decision 1 — no redesign, one extra slice.
- [ ] **OQ-2 — RF-AT-11-8 precedence (maintainer).** Confirm that an explicitly exported
      `HERDR_TTS_HOME` continues to win over own-location derivation (recommended), rather than
      the literal own-location-first reading. **Blocks slice 9.**
- [ ] **OQ-3 — STT health vocabulary (maintainer).** Confirm `loading | ready | unavailable` for
      `/health` `stt`, with RF-AT-11-12's `degraded` treated as a carry-over from the `tts`
      field. **Blocks slice 18's assertions** (not its implementation).
- [ ] **OQ-4 — Rebase authorization (Decision 0).** Confirm rebasing `feat/at-11-instalable`
      onto `main` before milestone 1. **Blocks slices 10–12.**
- [ ] **OQ-5 — Network authorization for V2.** Scenarios 1, 2, 5, and 9 require real network
      access to documented origins. Current authorization is local-only, so those scenarios
      report `BLOCKED` (exit 2), never green. Explicit authorization is needed before any
      milestone closes on them.
