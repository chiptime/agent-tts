# Tasks: AT-11 — Independent Installation and First-Run Onboarding

Change: `at-11-instalable` · Branch: `feat/at-11-instalable` @ `4ed549a` · Store: hybrid (this file + Engram mirror)
Traceability backbone: design **Decision 7** slice mapping. Every task carries its design slice number as `(slice N)`.
Testing contract: workspace `strict_tdd: false` — tests ship **with** each work unit; the plugin subproject's local `strict_tdd: true` governs `smoke-tests.sh` scenarios (RED scenario first, then `bin/herdr-tts`). No coverage/lint/type-check claims anywhere.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines (total) | ~5,590 authored additions+deletions — M1 ~1,610 · M2 ~1,470 · M3 ~1,460 · M4 ~1,050; largest single slice ~380 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | 23 PR slices, one per design slice (each ≤ ~380 lines), grouped M1 (8) → M2 (6) → M3 (5) → M4 (4); milestone = ordered PR group |
| Delivery strategy | auto-chain |
| Chain strategy | pending — maintainer confirms stacked-to-main vs feature-branch-chain before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

Exactly which decisions are needed before apply:

1. **Chain strategy** (maintainer) — required before the first chained PR is created. Task 1.1 itself is unblocked; only the PR/branch topology waits on this.
2. **OQ-2** — `HERDR_TTS_HOME` precedence (RF-AT-11-8). Blocks task 2.1 (slice 9). Recommendation on record (env-set wins, unset → own-location derive); tests MUST NOT bake the recommendation before maintainer confirmation.
3. **OQ-3** — STT `/health` vocabulary. Blocks task 3.4's **assertions only** (implementation proceeds; tests assert frozen-contract consistency, neither literal baked).
4. **OQ-5** — network authorization. V2 scenarios 1, 2, 5, 9 run `BLOCKED` (exit 2) under current local-only authorization; milestone-1, milestone-3, and milestone-4 closure on those scenarios requires explicit maintainer network authorization. Marks tasks 1.8, 3.4, 3.5 (conditional), 4.2 and the M1/M3/M4 closures.

In-task decision (explicit, not a maintainer gate): **validator note A** — the local-repository installation route either gets an `id=`-tagged documented step (harness-covered) or is recorded explicitly as docs-covered-only. Decided inside task 1.4; never left implicit.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1.1 | Harness core (sandbox, registry, activation, journal, exit codes) | PR 1 | `bash scripts/acceptance/clean-install.sh` self-drills (exit 0/1/4) | Harness dogfood (itself) | Delete `scripts/acceptance/` — nothing else depends on it yet |
| 1.2 | Origin allowlist + shim + doc-block extractor + V1 hygiene framework | PR 2 | `cd engine && python -m pytest tests/test_versioned_tree_hygiene.py -q` | Harness shim drill (blocked-origin) | Revert harness extension + delete allowlist/hygiene test |
| 1.3 | Scenario 3 authored + scan scope seeded | PR 3 | engine hygiene pytest | `clean-install.sh --milestone 1` (scenario 3 active) | Revert scenario file + manifest seed |
| 1.4 | Installer monorepo corrections + `id=` blocks + smoke scenarios | PR 4 | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` | Scenario 1 pre-run (BLOCKED pending OQ-5) | Revert installer + README + smoke additions |
| 1.5 | Bootstrap dev-mode `engine/` derivation | PR 5 | plugin smoke suite | N/A — proven by hermetic smoke stubs; no runtime harness route is unblocked before OQ-5 | Revert bootstrap + smoke additions |
| 1.6 | Packaging wrappers legacy refs + honest wording | PR 6 | engine hygiene pytest (packaging scope) | N/A — wrapper metadata only; npm/Homebrew routes not executable in sandbox and claims are docs | Revert the packaging files |
| 1.7 | Bounded OpenSpec config corrections | PR 7 | engine hygiene pytest still green | N/A — metadata-only, no executable behavior | Revert the two `config.yaml` edits |
| 1.8 | Scenarios 1+2 (OQ-1 probe) | PR 8 | `clean-install.sh --milestone 1` (scenarios 1, 2) | Itself — real install route; BLOCKED pending OQ-5 | Revert the two scenario files |
| 2.1 | `resolve.py` + bash resolvers (root, `HERDR_BIN`, port, `HERDR_TTS_HOME`) | PR 9 | `cd hosts/herdr/brain && python -m pytest tests/test_resolve.py -q` | Launcher resolver drill in source layout | Revert `resolve.py` + resolver edits |
| 2.2 | Brain launcher repair (A1/C1/C2/C4) + parity tests | PR 10 | brain pytest parity module | `--milestone 2` → scenario 3 `PASS` | Revert launcher repair + parity tests (launcher returns to pre-repair `main` state) |
| 2.3 | Ownership-aware port policy (C5) | PR 11 | brain pytest port-policy module | Port-conflict drill (foreign listener stays alive) | Revert port-policy edits in launcher + installer |
| 2.4 | Systemd template + generation + `deploy/install.sh` rewrite | PR 12 | brain pytest template tests | Sandbox generation drill (systemd route optional; drill only generates/inspects) | Revert template/install.sh/.gitignore; restore static unit |
| 2.5 | CLI exposure + PATH warning + complete uninstall print | PR 13 | plugin smoke suite | Exposure drill in sandbox `HOME` | Revert installer exposure + smoke additions |
| 2.6 | Scenario 7 `reinstall-idempotent` | PR 14 | `--milestone 2` (scenario 7) | Itself | Revert scenario file |
| 3.1 | Wizard skeleton: CLI, marker, skip, noninteractive, reachability | PR 15 | brain pytest `tests/test_first_run.py` | Reachability matrix across sandbox layouts | Revert wizard skeleton files |
| 3.2 | Credential capture + atomic writer + redaction | PR 16 | brain pytest secrets modules | Non-interactive secret drill (ps/logs/crash cases) | Revert `prompts.py`/`secrets.py`/`steps/credentials.py` |
| 3.3 | Voice + keymap steps (adopt/apply/auto-reload, preservation) | PR 17 | brain pytest step tests | Keymap adopt+reload drill in sandbox | Revert the two step modules |
| 3.4 | STT consent step + refusal path | PR 18 | brain pytest STT-step tests | `--milestone 3`: scenario 6 `PASS` (offline), scenario 5 BLOCKED pending OQ-5 | Revert `steps/stt.py` |
| 3.5 | Entry-point wiring + health gate + scenario 4 | PR 19 | plugin smoke suite | `--milestone 3` → scenario 4 | Revert entry-point dispatch + scenario file |
| 4.1 | Doctor: six checks + remediation + two dispatchers | PR 20 | brain pytest `tests/test_doctor.py` | Doctor breakage drill (simulated failure per check) | Revert doctor module + dispatchers |
| 4.2 | Scenarios 8+9 | PR 21 | `--milestone 4` (scenarios 8, 9) | Itself (9 BLOCKED pending OQ-5) | Revert the two scenario files |
| 4.3 | Documentation final pass (canonical flow, systemd, remote, tagged blocks) | PR 22 | engine hygiene allowlist↔docs tests | Docs-only — the V1 documented-steps checks are the runtime boundary | Revert the three READMEs |
| 4.4 | V3 checklist + RF↔evidence traceability matrix | PR 23 | `git diff contracts/` empty + traceability review | N/A — human V3 gate, outside the automated loop | Revert checklist + matrix |

## Preconditions (satisfied before task 1)

- **Slice 0 (rebase, Decision 0) — DONE.** Worktree HEAD is `4ed549a` (fast-forwarded to `main`). `hosts/herdr/brain/bin/herdr-brain` (279 lines) and `hosts/herdr/brain/herdr-plugin.toml` (43 lines) exist as tracked files. Tasks the design framed as "create" for those two files are re-scoped as **repair/extend** (task 2.2). OQ-4 is resolved; audit finding A3 is re-scored as *resolved on `main`*, not re-delivered.
- Engram ground truth honored: #9630 (`strict_tdd: false`, per-project commands authoritative), #9636 (V2 active-scenarios gate — binding), #9643 (STT vocabulary is brain runtime behavior, not frozen-contract surface).

---

## Milestone 1: Hygiene — harness first, then bugs + legacy refs (design slices 1–8)

- [x] 1.1 **Harness core: build `scripts/acceptance/clean-install.sh` complete** (slice 1) — MANDATORY FIRST TASK; every later stop criterion depends on it. Sandbox (empty `HOME`, XDG dirs under `$SANDBOX`, `env -i` base, allowlisted `PATH` of `bash git jq curl python3 uv` + stubs), non-standard checkout path under `mktemp -d`, `trap` cleanup + `--keep`; scenario registry + four-state activation model (`PASS / FAIL / BLOCKED / NOT-YET-ACTIVATED`, deliberately no `SKIP`), `--milestone N` with journal-based default, `--record`; evidence artifacts (`journal.json`, `summary.md`, per-scenario argv-redacted `cmd.log` / `stdout.log` / `assert.log`, `"stubbed": true` tagging); exit codes 0/1/2/3/4 with distinct semantics; regression guard against the journal; reuse the `ok()`/`bad()`/`assert_grep` vocabulary from the plugin smoke suite.
  - Files: `scripts/acceptance/clean-install.sh` (create), `scripts/acceptance/scenarios/` (registry skeleton).
  - Verify: harness end-to-end with zero authored scenarios → exit 0, truthful empty report; forced-failure drill → exit 1; sandbox-build-failure drill → exit 4; `--keep`/`--record` produce journal + summary. All three project suites untouched-green.
  - OQ: none.
  - Est: ~380 lines.

- [x] 1.2 **Origin allowlist + shim + `id=` block extractor + V1 hygiene-test framework** (slice 2) — create `scripts/acceptance/allowed-origins.txt` (documented origins only); `curl`/`git` shim placed first on sandbox PATH resolves the final host and exits non-zero with `BLOCKED-ORIGIN: <host>` for non-allowlisted origins, with the harness stating in its output that this is a policy boundary, not a namespace; implement the `id=`-tagged fenced-block extractor executing blocks **by id from a pinned file allowlist only**, never scan-and-run; create `engine/tests/test_versioned_tree_hygiene.py` with: allowlist↔docs equivalence in both directions, every harness-referenced id exists exactly once in its expected file, and the static scan framework (pattern engine + scope manifest) that later repair tasks extend. Threat-matrix coverage authored in this unit: (a) unknown block id → non-zero, nothing executed; (b) duplicate id in one file → non-zero; (c) block added to a non-allowlisted file → never executed; (d) request to a non-allowlisted host → blocked, scenario `BLOCKED` not `PASS`.
  - Files: `scripts/acceptance/allowed-origins.txt` (create), `scripts/acceptance/clean-install.sh` (extend), `engine/tests/test_versioned_tree_hygiene.py` (create).
  - Verify: `cd engine && python -m pytest tests/test_versioned_tree_hygiene.py -q` green; harness shim drill blocks a non-allowlisted host with `BLOCKED-ORIGIN`.
  - OQ: none (OQ-5 affects later scenario runs, not this authoring unit).
  - Est: ~220 lines.

- [ ] 1.3 **Scenario 3 `zero-machine-paths` + V1 scan scope seeded** (slice 3) — author harness scenario 3 (V1+V2 level; registry activation at M1 per design); seed the hygiene scan's scope manifest with tree areas already clean. Scenario 3's full-green completion lands with task 2.2 (design slice 10 also carries "Activates V2: 3"); milestone-1 closure reports its truthful state under the active-scenarios gate — never a green skip.
  - Files: `scripts/acceptance/scenarios/03-zero-machine-paths.sh` (create), `engine/tests/test_versioned_tree_hygiene.py` (extend).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 1` reports scenario 3 activated; engine pytest green.
  - OQ: none. Closure note: V2 scenario 3 green completes at task 2.2.
  - Est: ~180 lines.

- [ ] 1.4 **Installer monorepo corrections (B1–B4) + README `id=` blocks + smoke scenarios** (slice 4) — re-anchor `hosts/herdr/tts-plugin/scripts/install.sh` to the monorepo `CANONICAL_URL`/subdirectory source and layout; remove every active `chiptime/herdr-tts` / `chiptime/herdr-brain` reference from code, URLs, and output; rewrite the `hosts/herdr/tts-plugin/README.md` install section removing B1–B4 legacy commands and adding `id=`-tagged install blocks: the GitHub-subdirectory route plus an **explicit decision on the local-repository route** — an `id=`-tagged block (harness-covered) or a recorded docs-covered-only entry in the scenario registry (validator note A; never implicit). Plugin-strict-TDD applies: RED smoke scenario first, then installer change. Threat-matrix git-selection coverage: (a) installer run with cwd inside a foreign git repo → foreign repo untouched, `git status` clean; (b) relative `TARGET` rejected; (c) mismatched origin aborts writing nothing (retained safeguards proven against the rewrite).
  - Files: `hosts/herdr/tts-plugin/scripts/install.sh`, `hosts/herdr/tts-plugin/README.md`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend).
  - Verify: `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` green including new scenarios; engine hygiene scan green over installer + README (legacy-ref patterns).
  - OQ: none. Provides the `id=` blocks consumed by task 1.8.
  - Est: ~260 lines.

- [ ] 1.5 **Bootstrap dev-mode `engine/` derivation (audit B5)** (slice 5) — replace the hardcoded `~/Code/personal/agent-tts` dev checkout with own-location-derived `<checkout>/engine/`; actionable non-zero failure when `HERDR_TTS_DEV=1` but no discoverable `engine/`; public install ignores any decoy checkout; retained: uv/Python fallback, immutable pin, upgrade behavior, checkout independence. Plugin-strict-TDD: RED scenarios first.
  - Files: `hosts/herdr/tts-plugin/scripts/bootstrap.sh`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend).
  - Verify: plugin smoke suite green including the `plugin-bootstrap` delta scenarios (public install ignores decoy; dev opt-in derives `engine/` from own location; missing-engine fails actionably).
  - OQ: none.
  - Est: ~150 lines.

- [ ] 1.6 **Packaging wrappers: legacy corrections + honest support wording** (slice 6) — npm `package.json` + `bin/herdr-tts` shim and Homebrew `herdr-tts.rb`: monorepo `url`/`homepage`, zero legacy repo references, no unsupported claims (no registry publication, no native Windows, no validated cross-platform coverage); the keg route documents honestly that the first-run wizard is not available there.
  - Files: `hosts/herdr/tts-plugin/packaging/npm/package.json`, `hosts/herdr/tts-plugin/packaging/npm/bin/herdr-tts`, `hosts/herdr/tts-plugin/packaging/npm/README.md`, `hosts/herdr/tts-plugin/packaging/homebrew/herdr-tts.rb`, `hosts/herdr/tts-plugin/packaging/homebrew/README.md`.
  - Verify: engine hygiene scan green over packaging scope; grep proves zero legacy refs; wrapper claims reviewed against the `independent-installation` honest-wrapper-support scenarios.
  - OQ: none.
  - Est: ~140 lines.

- [ ] 1.7 **Bounded OpenSpec config corrections** (slice 7) — root `openspec/config.yaml`: registry-scope wording clarified to permit this change's bounded metadata correction and explicit delta routing; `hosts/herdr/tts-plugin/openspec/config.yaml`: correct the stale "engine is a separate external repository" statement. No registry merger, no ownership transfer, no `strict_tdd` rewrite.
  - Files: `openspec/config.yaml`, `hosts/herdr/tts-plugin/openspec/config.yaml`.
  - Verify: N/A runtime boundary (metadata-only, no executable behavior); engine hygiene suite still green; diff reviewed against proposal §Registry Ownership and Spec Routing.
  - OQ: none.
  - Est: ~40 lines.

- [ ] 1.8 **Scenarios 1+2: `plugin-fresh-clone`, `plugin-subdir-install` (OQ-1 probe)** (slice 8) — author both V2 scenarios executing the literal `id=` blocks from task 1.4 inside the sandbox; scenario 2 is the OQ-1 subdirectory-materialization probe (evidence for `plugin_root = managed_path/<subdir>`).
  - Files: `scripts/acceptance/scenarios/01-plugin-fresh-clone.sh`, `scripts/acceptance/scenarios/02-plugin-subdir-install.sh` (create).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 1` runs both; **both report `BLOCKED` (exit 2) under current local-only authorization**; journal records blocked-pending-authorization with the missing prerequisite named.
  - OQ: **OQ-5 gates green** (not authoring). **OQ-1** is resolved by scenario 2's outcome — if falsified, adopt the pre-designed vendoring fallback (design Decision 1) as one additional slice.
  - Est: ~240 lines.

**Milestone-1 closure**: V1 green (engine + brain + plugin suites) + harness `--milestone 1` truthful report + work-unit commit(s). **Affected by OQ-5**: scenarios 1 and 2 stay `BLOCKED` — closing M1 on them requires explicit maintainer network authorization; scenario 3's green completes at task 2.2 (truthful-state reporting, no green skip).

---

## Milestone 2: Machine-path decoupling (design slices 9–14)

- [ ] 2.1 **`resolve.py` + bash resolvers: monorepo root, `HERDR_BIN`, port, `HERDR_TTS_HOME`** (slice 9) — create `tools/herdr_onboarding/resolve.py` implementing the Decision-4 resolution orders (root: `HERDR_PLUGIN_ROOT` → ascend from the resolved real path of the executing script; `HERDR_TTS_HOME`; `HERDR_BIN` six-step discovery; port: `HERDR_BRAIN_PORT` → persisted config → `8741`); identical minimal bash resolver functions in both launchers for pre-Python use (ascend from `dirname "$(readlink -f "${BASH_SOURCE[0]}")"`, up to 6 levels); port knob plumbing in brain config.
  - **PRECONDITION — OQ-2 (maintainer) blocks this task.** Recommended reading: `HERDR_TTS_HOME` set-and-valid wins (override semantics preserved); unset → derive from launcher location; never export a hardcoded default. Tests MUST NOT bake the recommendation before confirmation — until then, assert only consistency with `config.py`'s existing override contract.
  - Files: `tools/herdr_onboarding/__init__.py` + `tools/herdr_onboarding/resolve.py` (create), `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/tts-plugin/bin/herdr-tts` (extend), `hosts/herdr/brain/src/herdr_brain/config.py` (port knob), `hosts/herdr/brain/tests/test_resolve.py` (create).
  - Verify: `cd hosts/herdr/brain && python -m pytest tests/test_resolve.py -q` green (resolution table with injected env dicts + `tmp_path`).
  - OQ: **OQ-2 blocks.**
  - Est: ~300 lines.

- [ ] 2.2 **Brain launcher repair: A1/C1/C2/C4 removal + parity tests (RNF-4)** (slice 10) — **REPAIR, not create**: `hosts/herdr/brain/bin/herdr-brain` exists (279 lines, tracked since `8de8a34`) and carries every defect verbatim. Remove the dead `HERDR_TTS_HOME` default export, the `$HOME/.dotfiles/shell/private-env.sh` scrape, the literal `/home/linuxbrew/.linuxbrew/bin/herdr` fallback, and the personal `tail2640fd.ts.net:8443` reference; optional remote-exposure domain from config/env only (RF-AT-11-9); paired compatibility assertions — legacy-shaped and corrected layouts both observably resolve the TTS surface (parity proof). Completes scenario 3's green (with 1.4–1.7 and 2.4).
  - Files: `hosts/herdr/brain/bin/herdr-brain` (repair), `hosts/herdr/brain/herdr-plugin.toml` (extend only if actions change), `hosts/herdr/brain/tests/` (parity module — create), `engine/tests/test_versioned_tree_hygiene.py` (scope manifest extends to the launcher in this unit), `scripts/acceptance/scenarios/03-zero-machine-paths.sh` (assertions finalized).
  - Verify: brain pytest parity green; `bash scripts/acceptance/clean-install.sh --milestone 2` → scenario 3 `PASS`.
  - OQ: OQ-2 must be resolved (task 2.1) — the launcher implements the confirmed precedence.
  - Est: ~280 lines.

- [ ] 2.3 **Ownership-aware port policy (audit C5)** (slice 11) — determine the listener PID; signal only proven-owned processes (our pidfile, or the systemd unit's `MainPID`), re-verified immediately before signalling; otherwise exit non-zero naming PID, process name, port, and two remediations (`HERDR_BRAIN_PORT=…` or stop it yourself). Applies to `bin/herdr-brain` and `hosts/herdr/brain/deploy/install.sh`. Threat-matrix coverage: (a) foreign listener on the port → non-zero, **process still alive**; (b) own stale pidfile → cleaned without signalling a reused PID; (c) unit-owned listener → managed via `systemctl`, not `kill`.
  - Files: `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/brain/deploy/install.sh` (extend), `hosts/herdr/brain/tests/` (port-policy tests — create), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend if the plugin side carries the policy).
  - Verify: brain pytest port-policy green; plugin suite green.
  - OQ: none.
  - Est: ~200 lines.

- [ ] 2.4 **Systemd template + generation + `deploy/install.sh` rewrite** (slice 12) — create `hosts/herdr/brain/deploy/herdr-brain.service.tmpl` (placeholders `@HERDR_BIN@ @INSTALL_DIR@ @PYTHON@ @PORT@ @ENV_FILE@`); delete `hosts/herdr/brain/deploy/herdr-brain.service`; add generated `deploy/*.service` to `.gitignore`; rewrite `hosts/herdr/brain/deploy/install.sh`: drop the dotfiles dependency, substitute discovered values into the template at install time (Decision-5 discovery order for `@HERDR_BIN@`), unsubstituted `@…@` remaining after generation = hard install failure, English output, upgrade replaces only the AT-11-owned unit (stop → regenerate → reload → restart).
  - Files: `hosts/herdr/brain/deploy/herdr-brain.service.tmpl` (create), `hosts/herdr/brain/deploy/herdr-brain.service` (delete), `hosts/herdr/brain/deploy/install.sh` (rewrite), `.gitignore` (extend), `hosts/herdr/brain/tests/` (template-substitution tests — create), `engine/tests/test_versioned_tree_hygiene.py` (scope manifest extends to `deploy/` in this unit).
  - Verify: brain pytest template tests green (substitution of discovered values, leftover-placeholder hard failure, generated unit untracked); systemd route exercised as optional in a sandbox generation drill.
  - OQ: none.
  - Est: ~340 lines.

- [ ] 2.5 **CLI exposure `~/.local/bin` + PATH warning + complete uninstall print** (slice 13) — installer exposes `bin/herdr-tts` as `~/.local/bin/herdr-tts` (directory created when missing), warns naming the remediation when PATH lacks `~/.local/bin`, refreshes managed artifacts on re-run, and prints complete uninstall steps (daemon stop, venv removal, managed keymap block removal, managed CLI artifact removal, first-run marker location). Threat-matrix coverage: (a) pre-existing unmanaged `herdr-tts` → refuse, exit non-zero, name it; (b) `~/.local/bin` exists as a regular file → actionable failure; (c) managed artifact from a previous install → refreshed in place.
  - Files: `hosts/herdr/tts-plugin/scripts/install.sh` (extend), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend). Plugin-strict-TDD: RED scenarios first.
  - Verify: plugin smoke suite green including exposure/threat scenarios (installer delta "Managed CLI exposure"; `independent-installation` CLI-exposure scenarios).
  - OQ: none.
  - Est: ~190 lines.

- [ ] 2.6 **Scenario 7 `reinstall-idempotent`** (slice 14) — full install → capture credentials/preferences → full re-run: env-file content, preferences, keymap, and completion state preserved; services in the same functional state; plus the broken-install repair case (re-run repairs without destroying user state).
  - Files: `scripts/acceptance/scenarios/07-reinstall-idempotent.sh` (create).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 2` → scenario 7 `PASS`, no regression on scenario 3.
  - OQ: none (scenario stays within the sandbox; not in the OQ-5 list).
  - Est: ~160 lines.

**Milestone-2 closure**: V1 green + `--milestone 2` (scenarios 3, 7 `PASS`) + work-unit commit(s). **Affected by OQ-5**: scenarios 1, 2 remain `BLOCKED` (truthful) — their green still awaits network authorization.

---

## Milestone 3: First-run wizard + keys + STT (design slices 15–19)

- [ ] 3.1 **Wizard skeleton: package, CLI, marker, skip, noninteractive, reachability matrix** (slice 15) — `tools/herdr_onboarding` CLI per the design contract: `--role {plugin,brain}`, `--no-first-run` (exit 0, no marker), `--non-interactive` (consume flags/env, fail `20` if an answer is missing), `--json`; exit contract 0/10/20/30/40; completion marker `~/.config/herdr-tts/first-run.done` (JSON, written only after the health gate — gate wired in 3.5, marker lifecycle unit-tested here; failed run leaves no marker); TTY detection with the noninteractive hint; reachability tests for the three supported layouts (source checkout, curl-route full clone, subdirectory managed install) and the honest Homebrew-unavailable case; `PYTHONPATH` + `-m` invocation through each entry point's venv Python.
  - Files: `tools/herdr_onboarding/__init__.py`, `tools/herdr_onboarding/__main__.py` (create), `tools/herdr_onboarding/resolve.py` (extend), `hosts/herdr/brain/tests/test_first_run.py` (create), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend; plugin-strict-TDD: RED first).
  - Verify: brain pytest `test_first_run.py` green (exit contract, marker lifecycle, noninteractive); plugin smoke reachability matrix green.
  - OQ: none.
  - Est: ~360 lines.

- [ ] 3.2 **Credential capture: getpass, fd/file intake, atomic mode-600 writer, redaction** (slice 16) — three ranked channels: `getpass.getpass()` interactive; `HERDR_ONBOARDING_SECRET_FD=<n>` primary unattended (read once, close); `HERDR_ONBOARDING_SECRET_FILE=<path>` fallback (mode-600, read once, optional unlink); argv excluded at every level (no `--glm-key <value>` ever); merge-write `~/.config/herdr-brain/env` via `mkstemp` same-directory → `chmod 600` → `os.replace`; unknown lines and comments preserved byte-identical; single `redact()` boundary wrapping every diagnostic path; secrets never formatted into exception messages. Threat-matrix coverage: (a) `ps` snapshot during a non-interactive run contains no secret; (b) every log/stdout byte of a **failed** run is secret-free; (c) simulated crash mid-write leaves no readable partial env file. Static V1: no `set -x` in any secret-handling script. **Validator note B: `--role plugin` runs MUST NOT demand the GLM key — a plugin-only run completes keyless** (spec scenario "GLM key optional without the brain").
  - Files: `tools/herdr_onboarding/prompts.py`, `tools/herdr_onboarding/secrets.py`, `tools/herdr_onboarding/steps/credentials.py` (create), `engine/tests/test_versioned_tree_hygiene.py` (set -x static assertion), `hosts/herdr/brain/tests/` (secret-handling tests — create).
  - Verify: brain pytest secrets/redaction/atomicity/role-optionality green; engine hygiene green.
  - OQ: none.
  - Est: ~320 lines.

- [ ] 3.3 **Voice + keymap steps: adopt/apply/auto-reload + existing-keymap preservation** (slice 17) — voice provider selection persisted to the plugin config; keymap via the existing adoption mechanism followed by automatic configuration reload (no manual reload step); an existing user keymap is never overwritten without explicit consent; explicit opt-out honored (`none` style); re-run against existing preferences preserves them. The installer's own "Keymap adoption policy" remains authoritative for its artifacts; the wizard reuses the same mechanism.
  - Files: `tools/herdr_onboarding/steps/voice.py`, `tools/herdr_onboarding/steps/keymap.py` (create), `hosts/herdr/brain/tests/` (step tests — create), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend where plugin-mechanism integration is exercised).
  - Verify: brain pytest step tests green; plugin suite green.
  - OQ: none.
  - Est: ~240 lines.

- [ ] 3.4 **STT consent step: size selection, download, contract verify, refusal path** (slice 18) — explicit `tiny|base|small` consent (`none` = explicit refusal); nothing downloads without consent; on consent the model downloads to the standard store and the speech-surface contract check is verified afterwards; refusal completes onboarding successfully with `/ask`/`audio_url` behavior per the frozen contract (including the documented null case) and no later step failing on the missing model; `contracts/tts-brain-v1` and `contracts/ipc-v2` (read-only) stay byte-identical.
  - **PRECONDITION — OQ-3 (maintainer) blocks this task's ASSERTIONS only; implementation is not blocked.** Recommended vocabulary: `loading | ready | unavailable`, `unavailable` after refusal; `degraded` stays a `tts`-field-only value. Until confirmed, tests assert consistency with the frozen contract and the user's choice — neither literal baked.
  - Files: `tools/herdr_onboarding/steps/stt.py` (create), `hosts/herdr/brain/tests/` (step tests — create).
  - Verify: brain pytest STT-step green (contract-consistency assertions); `--milestone 3`: scenario 6 `PASS` (offline refusal), scenario 5 runs **BLOCKED pending OQ-5** (real HuggingFace download).
  - OQ: **OQ-3 (assertions)**; **OQ-5 (scenario 5 green)**.
  - Est: ~260 lines.

- [ ] 3.5 **Entry-point wiring both sides + health gate + scenario 4** (slice 19) — `hosts/herdr/tts-plugin/bin/herdr-tts`: first-run marker check, TTY detection, noninteractive hint, wizard hand-off via the resolver function; `hosts/herdr/brain/bin/herdr-brain`: the same dispatch; health gate before marker write: `/health` reports `tts: ok` and `herdr plugin list` emits no manifest warnings; failed gate → no marker, next launch retries; wizard failure/abort → install stays healthy, no partial state (exit 40 path). Scenario 4 `first-run-keys`: non-argv key capture end-to-end in the sandbox.
  - Files: `hosts/herdr/tts-plugin/bin/herdr-tts` (extend), `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend; plugin-strict-TDD: RED first), `scripts/acceptance/scenarios/04-first-run-keys.sh` (create).
  - Verify: plugin suite green (dispatch, skip, nonblocking-unattended scenarios); `--milestone 3` → scenario 4 `PASS`, scenario 6 `PASS`, no regression on 3, 7; scenario 5 truthful per OQ-5.
  - OQ: **OQ-5 conditionally** — if scenario 4's run proves to require real network access, it reports `BLOCKED`, never green without authorization.
  - Est: ~280 lines.

**Milestone-3 closure**: V1 green + `--milestone 3` (scenarios 4, 6 `PASS`; scenario 5 `BLOCKED` pending OQ-5) + work-unit commit(s).

---

## Milestone 4: Doctor + docs (design slices 20–23)

- [ ] 4.1 **Doctor: six checks + actionable remediation + two dispatchers** (slice 20) — checks implemented once in `tools/herdr_onboarding/`, exposed twice: (1) CLI on PATH (`command -v herdr-tts` + `~/.local/bin` coverage), (2) audio backend platform probe, (3) credentials (`~/.config/herdr-brain/env` present, mode 600, `GLM_API_KEY` non-empty; `--fix-credentials` re-runs capture only), (4) daemon liveness (pidfile + `kill -0`, then `/health`), (5) contract v1 (`herdr-tts --contract-version` ≥ 1), (6) STT model state via offline `stt.model_is_cached()` (never downloads). Every failure line carries a copy-pasteable remediation command; a check with no remediation is a specification failure, asserted by a V1 test walking the check table. Dispatchers: `herdr-tts doctor`, `herdr-brain doctor`; `--doctor` on the wizard runs checks only, no mutation.
  - Files: `tools/herdr_onboarding/report.py` + checks module (create), `hosts/herdr/tts-plugin/bin/herdr-tts` (extend), `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/brain/tests/test_doctor.py` (create).
  - Verify: brain pytest `test_doctor.py` green including the remediation-completeness walk; doctor breakage drill (simulated failure per check names its repair).
  - OQ: none.
  - Est: ~330 lines.

- [ ] 4.2 **Scenarios 8+9: `doctor-diagnoses-break`, `post-wizard-health`** (slice 21) — scenario 8: simulated breakage per check → doctor output names the exact repair command; scenario 9: complete first-run → `/health` `tts: ok`, plugin list clean, marker present; scenario 9's timed-human half stays V3 and is never asserted by the harness.
  - Files: `scripts/acceptance/scenarios/08-doctor-diagnoses-break.sh`, `scripts/acceptance/scenarios/09-post-wizard-health.sh` (create).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 4` → scenario 8 `PASS`; scenario 9 **BLOCKED pending OQ-5** under current authorization.
  - OQ: **OQ-5 (scenario 9)**.
  - Est: ~220 lines.

- [ ] 4.3 **Documentation final pass: canonical flow, optional systemd, advanced remote exposure, final tagged blocks** (slice 22) — root `README.md`, `hosts/herdr/brain/README.md`, `hosts/herdr/tts-plugin/README.md`: one canonical primary installation flow (Linux, WSL2, macOS documented; native Windows not claimed), optional systemd deployment section, advanced remote-exposure guidance (user-configured, never automated, no personal domains), the documented `herdr plugin unlink` + reinstall repair sequence for stale registrations (audit A2 — documented, never executed against the maintainer's live config), all installation commands in `id=`-tagged blocks matching the allowlist exactly.
  - Files: `README.md`, `hosts/herdr/brain/README.md`, `hosts/herdr/tts-plugin/README.md`.
  - Verify: engine hygiene allowlist↔docs equivalence green (final state); documented-steps V1 checks green (docs-only change still runs V1).
  - OQ: none.
  - Est: ~300 lines.

- [ ] 4.4 **V3 checklist + final RF↔evidence traceability matrix** (slice 23) — create `docs/installation/V3-checklist.md`: timed clean-machine UAT following only the published documentation, **both timing readings recorded** (RNF-AT-11-3 host-preinstalled vs UAT-scenario host-included), friction-log template with issue/doc-fix routing, human sign-off section; compile the RF-AT-11-1..12 + applicable-RNF traceability matrix mapping every requirement to its V1/V2/V3 evidence; record the frozen-contract byte-identity check.
  - Files: `docs/installation/V3-checklist.md` (create), traceability matrix (change-directory appendix), `contracts/ipc-v2` + `contracts/tts-brain-v1` (read-only — byte-identity verified).
  - Verify: human gate — outside the automated loop; autonomous completion never substitutes for V3 sign-off. `git diff contracts/` must be empty.
  - OQ: none (execution requires a human).
  - Est: ~200 lines.

**Milestone-4 closure**: `clean-install.sh --milestone 4` full-suite green is the mechanically unavoidable final gate — scenarios 1, 2, 5, 9 (all network-dependent) require explicit OQ-5 authorization to report green; V3 human sign-off is required before merge.

---

## Dependency and order notes

1. **Harness-first is binding**: task 1.1 precedes every other task — the loop's stop criterion (exit codes, journal, activation model) depends on it. 1.2 extends the harness and seeds the V1 hygiene framework; 1.3–1.8 add scenarios and the first repairs.
2. **Scenario 3 activation/green split** (traceable to the design's own slice table): authored at 1.3 (slice 3, M1 activation); full green at 2.2 (slice 10 also carries "Activates V2: 3"). Milestone-1 closure reports its truthful state under the binding active-scenarios gate (Engram #9636) — never a green skip. The hygiene scan's scope manifest travels with each repair (1.4, 1.5, 1.6, 2.2, 2.4 extend it in the same work unit) so the three project suites stay green at every unit boundary.
3. **Installer corrections (1.4) provide the `id=` blocks** consumed by scenarios 1–2 (1.8). Bootstrap (1.5), packaging (1.6), and config (1.7) are independent of 1.4 but sequence after 1.3.
4. **M2 chain**: 2.1 (OQ-2 gate) → 2.2 → 2.3 → 2.4; 2.5 follows 1.4 (same installer); 2.6 follows 2.5.
5. **M3 chain**: 3.1 → 3.2 → 3.3 → 3.4 → 3.5 (wiring last); OQ-3 gates 3.4's assertions only; validator note B lands in 3.2.
6. **M4 chain**: 4.1 → 4.2 → 4.3 → 4.4; the doctor needs the M3 wizard detection layer (Decision 7).
7. **OQ-5 blocks green, not authoring**, on scenarios 1, 2, 5, 9 — closure evidence stays truthful (`BLOCKED`, exit 2, journal records the missing prerequisite) until the maintainer grants network authorization.
8. **Plugin-strict-TDD tasks** (local `strict_tdd: true` governs `smoke-tests.sh`): 1.4, 1.5, 2.5, 3.1, 3.5 author the RED smoke scenario before the `bin/`-side change. All other tasks follow workspace `strict_tdd: false` — tests ship with the behavior.
9. **Frozen contracts** `contracts/ipc-v2` and `contracts/tts-brain-v1` are read-only for every task; byte-identity is asserted at 4.4.
10. **Base correction honored**: 2.2 is a repair of files already tracked on `main`; no task recreates `hosts/herdr/brain/bin/herdr-brain` or `hosts/herdr/brain/herdr-plugin.toml` from scratch.
