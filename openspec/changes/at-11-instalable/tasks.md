# Tasks: AT-11 — Independent Installation and First-Run Onboarding

Change: `at-11-instalable` · Branch: `feat/at-11-instalable` @ `401a4b8` · Store: hybrid (this file + Engram mirror)
Traceability backbone: design **Decision 7** slice mapping, extended by **Decisions 8–10** (slices 3a, 8a, 8b). Every task carries its design slice number as `(slice N)`.
Testing contract: workspace `strict_tdd: false` — tests ship **with** each work unit; the plugin subproject's local `strict_tdd: true` governs `smoke-tests.sh` scenarios (RED scenario first, then `bin/herdr-tts`). No coverage/lint/type-check claims anywhere.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines (total) | ~5,875 authored additions+deletions — M1 ~1,895 · M2 ~1,470 · M3 ~1,460 · M4 ~1,050; largest single slice ~380 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | 26 PR slices, one per design slice (each ≤ ~380 lines), grouped M1 (11) → M2 (6) → M3 (5) → M4 (4); milestone = ordered PR group |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain (confirmed) — PR 1 targets the tracker branch `feat/at-11-instalable`; each later PR targets the immediately preceding PR's branch, so every child diff shows only its own slice. PR creation is the maintainer's action after V3; apply creates work-unit commits only |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

Resolved decision record (nothing awaits a maintainer answer — do not re-open or mark pending):

- **Chain strategy**: feature-branch-chain, confirmed. All known user decisions are resolved.
- **OQ-2 → design Decision 4** (Engram #9681): explicit `HERDR_TTS_HOME` wins; own-location is the discovery default only. Task 2.1 implements and asserts this directly.
- **OQ-3 → design Decision 6** (Engram #9681): `/health` `stt` is `loading | ready | unavailable`; `unavailable` after refusal; `degraded` reserved for the `tts` field; RF-12 is vocabulary errata; frozen contracts untouched. Task 3.4 asserts the literal resolved vocabulary.
- **OQ-5 → Engram #9681**: V2 network authorized for documented PyPI/uv, GitHub, and M3 STT origins; anything unlisted reports `BLOCKED`, never green. No push/PR/remote git.
- **M1 baseline extension** (Engram #9711/#9713/#9715): smoke 16n and 40e are fixed in M1 with **no baseline exception**; the engine pin is the exact immutable SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be`, retrieved from the documented origin or `BLOCKED` — never a substitute or fallback.
- **V2 gate** (Engram #9636): activation follows delivered functionality; active scenarios green, no regressions; later scenarios `NOT-YET-ACTIVATED`; full suite green only at M4.

Remaining evidence gates (answered by running a test, not by a decision): **OQ-6** — public retrieval of the exact pin (task 1.8, scenario 1; unavailable ⇒ `BLOCKED` with recorded reason, M1 stays open, no substitute). **OQ-1** — subdirectory materialization (task 1.8, scenario 2; falsified ⇒ the pre-designed vendoring fallback of design Decision 1 as one additional slice).

In-task decision (explicit, not a maintainer gate): **validator note A** — the local-repository installation route either gets an `id=`-tagged documented step (harness-covered) or is recorded explicitly as docs-covered-only. **Already decided in task 1.4** and recorded in `scripts/acceptance/scenarios/registry.conf`: the local-repository route is DOCS-COVERED-ONLY.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1.1 | Harness core (sandbox, registry, activation, journal, exit codes) | PR 1 | `bash scripts/acceptance/clean-install.sh` self-drills (exit 0/1/4) | Harness dogfood (itself) | Delete `scripts/acceptance/` — nothing else depends on it yet |
| 1.2 | Origin allowlist + shim + doc-block extractor + V1 hygiene framework | PR 2 | `cd engine && python -m pytest tests/test_versioned_tree_hygiene.py -q` | Harness shim drill (blocked-origin) | Revert harness extension + delete allowlist/hygiene test |
| 1.3 | Scenario 3 authored + scan scope seeded | PR 3 | engine hygiene pytest | `clean-install.sh --milestone 1` (scenario 3 authored; NOT-YET-ACTIVATED after 1.9) | Revert scenario file + manifest seed |
| 1.4 | Installer monorepo corrections + `id=` blocks + smoke scenarios | PR 4 | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` — new install cases 34a–34j green (full suite: `993 passed, 2 failed (16n/40e)` — baseline failures fixed by 1.10/1.11, not this unit) | Scenario 1 pre-run (documented-origin network authorized — Engram #9681) | Revert installer + README + smoke additions |
| 1.5 | Bootstrap dev-mode `engine/` derivation | PR 5 | plugin smoke suite — focused `plugin-bootstrap` delta scenarios | N/A — proven by hermetic smoke stubs; no runtime harness route is needed for this unit | Revert bootstrap + smoke additions |
| 1.6 | Packaging wrappers legacy refs + honest wording | PR 6 | engine hygiene pytest (packaging scope) | N/A — wrapper metadata only; npm/Homebrew routes not executable in sandbox and claims are docs | Revert the packaging files |
| 1.7 | Bounded OpenSpec config corrections | PR 7 | engine hygiene pytest still green | N/A — metadata-only, no executable behavior | Revert the two `config.yaml` edits |
| 1.9 | Scenario 3 activation correction 1→2 (Decision 10) | PR 8 | `bash scripts/acceptance/clean-install.sh --milestone 1` (scenario 3 = `NOT-YET-ACTIVATED`) | Harness registry semantics drill (itself) | Single registry field; never reverted alone — reverting re-exposes the active-FAIL inconsistency |
| 1.10 | Smoke 16n host-safety isolation (Decision 8) | PR 9 | `cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` — Decision-8 RED tests + section 16 green; whole-suite run recorded only as the host-state-invariance snapshot (`993 passed, 2 failed (16n/40e)` until 1.11, never called green) | Read-only host-state invariance snapshot before/after a full suite run | Launcher overrides + `new_env()` exports revert together; a partial revert leaving the suite on host defaults is forbidden |
| 1.11 | Smoke 40e exact pin + checked dual-layout oracle (Decision 9) | PR 10 | plugin smoke suite (sections 40e + 40g + Decision-9 RED tests); full plugin suite green is claimable only AFTER this task fixes 40e (16n already fixed by 1.10) | Hermetic suite only — public retrieval is task 1.8's separate V2 gate (OQ-6) | Pin + 40e driver revert together; restoring `32e9bafb` restores the baseline failure, not a release |
| 1.8 | Scenarios 1+2 (OQ-6 exact-SHA retrieval, OQ-1 probe) — runs AFTER 1.11 | PR 11 | `bash scripts/acceptance/clean-install.sh --milestone 1` (scenarios 1, 2) | Itself — real documented-origin install route; exact pin retrieved or truthful `BLOCKED` | Revert the two scenario files |
| 2.1 | `resolve.py` + bash resolvers (root, `HERDR_BIN`, port, `HERDR_TTS_HOME`) | PR 12 | `cd hosts/herdr/brain && python -m pytest tests/test_resolve.py -q` | Launcher resolver drill in source layout | Revert `resolve.py` + resolver edits |
| 2.2 | Brain launcher repair (A1/C1/C2/C4) + parity tests | PR 13 | brain pytest parity module | `--milestone 2` → scenario 3 `PASS` | Revert launcher repair + parity tests (launcher returns to pre-repair `main` state) |
| 2.3 | Ownership-aware port policy (C5) | PR 14 | brain pytest port-policy module | Port-conflict drill (foreign listener stays alive) | Revert port-policy edits in launcher + installer |
| 2.4 | Systemd template + generation + `deploy/install.sh` rewrite | PR 15 | brain pytest template tests | Sandbox generation drill (systemd route optional; drill only generates/inspects) | Revert template/install.sh/.gitignore; restore static unit |
| 2.5 | CLI exposure + PATH warning + complete uninstall print | PR 16 | plugin smoke suite | Exposure drill in sandbox `HOME` | Revert installer exposure + smoke additions |
| 2.6 | Scenario 7 `reinstall-idempotent` | PR 17 | `--milestone 2` (scenario 7) | Itself | Revert scenario file |
| 3.1 | Wizard skeleton: CLI, marker, skip, noninteractive, reachability | PR 18 | brain pytest `tests/test_first_run.py` | Reachability matrix across sandbox layouts | Revert wizard skeleton files |
| 3.2 | Credential capture + atomic writer + redaction | PR 19 | brain pytest secrets modules | Non-interactive secret drill (ps/logs/crash cases) | Revert `prompts.py`/`secrets.py`/`steps/credentials.py` |
| 3.3 | Voice + keymap steps (adopt/apply/auto-reload, preservation) | PR 20 | brain pytest step tests | Keymap adopt+reload drill in sandbox | Revert the two step modules |
| 3.4 | STT consent step + refusal path | PR 21 | brain pytest STT-step tests | `--milestone 3`: scenario 6 `PASS` (offline), scenario 5 green on the documented model origin (truthful `BLOCKED` if its prerequisite is unavailable) | Revert `steps/stt.py` |
| 3.5 | Entry-point wiring + health gate + scenario 4 | PR 22 | plugin smoke suite | `--milestone 3` → scenario 4 | Revert entry-point dispatch + scenario file |
| 4.1 | Doctor: six checks + remediation + two dispatchers | PR 23 | brain pytest `tests/test_doctor.py` | Doctor breakage drill (simulated failure per check) | Revert doctor module + dispatchers |
| 4.2 | Scenarios 8+9 | PR 24 | `--milestone 4` (scenarios 8, 9) | Itself (9 on documented origins; truthful `BLOCKED` if unavailable) | Revert the two scenario files |
| 4.3 | Documentation final pass (canonical flow, systemd, remote, tagged blocks) | PR 25 | engine hygiene allowlist↔docs tests | Docs-only — the V1 documented-steps checks are the runtime boundary | Revert the three READMEs |
| 4.4 | V3 checklist + RF↔evidence traceability matrix | PR 26 | `git diff contracts/` empty + traceability review | N/A — human V3 gate, outside the automated loop | Revert checklist + matrix |

## Preconditions (satisfied before task 1)

- **Slice 0 (rebase, Decision 0) — DONE.** Worktree HEAD is `401a4b8` (fast-forwarded past `main` `4ed549a`). `hosts/herdr/brain/bin/herdr-brain` (279 lines) and `hosts/herdr/brain/herdr-plugin.toml` (43 lines) exist as tracked files. Tasks the design framed as "create" for those two files are re-scoped as **repair/extend** (task 2.2). OQ-4 is resolved; audit finding A3 is re-scored as *resolved on `main`*, not re-delivered.
- Engram ground truth honored: #9630 (`strict_tdd: false`, per-project commands authoritative), #9636 (V2 active-scenarios gate — binding), #9643 (STT vocabulary is brain runtime behavior, not frozen-contract surface), #9681 (OQ-2/OQ-3/OQ-5 resolved — binding), #9711/#9713/#9715 (M1 extension authorized: smoke 16n/40e fixes with no baseline exception, selected pin `d66616bce3ad8193f11ae615bd58bb4508eb65be`). Revised design Decisions 8–10 govern slices 3a/8a/8b (scenario 3 activates at M2).

---

## Milestone 1: Hygiene — harness first, then bugs + legacy refs (design slices 1–8, 3a, 8a, 8b)

- [x] 1.1 **Harness core: build `scripts/acceptance/clean-install.sh` complete** (slice 1) — MANDATORY FIRST TASK; every later stop criterion depends on it. Sandbox (empty `HOME`, XDG dirs under `$SANDBOX`, `env -i` base, allowlisted `PATH` of `bash git jq curl python3 uv` + stubs), non-standard checkout path under `mktemp -d`, `trap` cleanup + `--keep`; scenario registry + four-state activation model (`PASS / FAIL / BLOCKED / NOT-YET-ACTIVATED`, deliberately no `SKIP`), `--milestone N` with journal-based default, `--record`; evidence artifacts (`journal.json`, `summary.md`, per-scenario argv-redacted `cmd.log` / `stdout.log` / `assert.log`, `"stubbed": true` tagging); exit codes 0/1/2/3/4 with distinct semantics; regression guard against the journal; reuse the `ok()`/`bad()`/`assert_grep` vocabulary from the plugin smoke suite.
  - Files: `scripts/acceptance/clean-install.sh` (create), `scripts/acceptance/scenarios/` (registry skeleton).
  - Verify: harness end-to-end with zero authored scenarios → exit 0, truthful empty report; forced-failure drill → exit 1; sandbox-build-failure drill → exit 4; `--keep`/`--record` produce journal + summary. All three project suites untouched-green.
  - OQ: none.
  - Est: ~380 lines.

- [x] 1.2 **Origin allowlist + shim + `id=` block extractor + V1 hygiene-test framework** (slice 2) — create `scripts/acceptance/allowed-origins.txt` (documented origins only); `curl`/`git` shim placed first on sandbox PATH resolves the final host and exits non-zero with `BLOCKED-ORIGIN: <host>` for non-allowlisted origins, with the harness stating in its output that this is a policy boundary, not a namespace; implement the `id=`-tagged fenced-block extractor executing blocks **by id from a pinned file allowlist only**, never scan-and-run; create `engine/tests/test_versioned_tree_hygiene.py` with: allowlist↔docs equivalence in both directions, every harness-referenced id exists exactly once in its expected file, and the static scan framework (pattern engine + scope manifest) that later repair tasks extend. Threat-matrix coverage authored in this unit: (a) unknown block id → non-zero, nothing executed; (b) duplicate id in one file → non-zero; (c) block added to a non-allowlisted file → never executed; (d) request to a non-allowlisted host → blocked, scenario `BLOCKED` not `PASS`.
  - Files: `scripts/acceptance/allowed-origins.txt` (create), `scripts/acceptance/clean-install.sh` (extend), `engine/tests/test_versioned_tree_hygiene.py` (create).
  - Verify: `cd engine && python -m pytest tests/test_versioned_tree_hygiene.py -q` green; harness shim drill blocks a non-allowlisted host with `BLOCKED-ORIGIN`.
  - OQ: none (network authorization affects later scenario runs, not this authoring unit).
  - Est: ~220 lines.

- [x] 1.3 **Scenario 3 `zero-machine-paths` + V1 scan scope seeded** (slice 3) — author harness scenario 3 (V1+V2 level); seed the hygiene scan's scope manifest with tree areas already clean. **Activation correction (design Decision 10 — wording revised after delivery):** the scenario is authored in M1 but its registry `activates_at_milestone` is corrected to 2 by task 1.9 (slice 3a) — it reports `NOT-YET-ACTIVATED` throughout M1 and activates only when M2 task 2.2 delivers the machine-path behavior. Never an active FAIL at M1, never a green skip.
  - Files: `scripts/acceptance/scenarios/03-zero-machine-paths.sh` (create), `engine/tests/test_versioned_tree_hygiene.py` (extend).
  - Verify (as delivered, corrected reading): `bash scripts/acceptance/clean-install.sh --milestone 1` runs with scenario 3 authored; after task 1.9 it reports scenario 3 `NOT-YET-ACTIVATED`, not active. Engine pytest green.
  - OQ: none. Closure note: V2 scenario 3 activates and turns green at task 2.2 (Decision 10). The V1 half (static hygiene scan) is an engine pytest and stays green from this unit onward.
  - Est: ~180 lines.

- [x] 1.4 **Installer monorepo corrections (B1–B4) + README `id=` blocks + smoke scenarios** (slice 4) — re-anchor `hosts/herdr/tts-plugin/scripts/install.sh` to the monorepo `CANONICAL_URL`/subdirectory source and layout; remove every active `chiptime/herdr-tts` / `chiptime/herdr-brain` reference from code, URLs, and output; rewrite the `hosts/herdr/tts-plugin/README.md` install section removing B1–B4 legacy commands and adding `id=`-tagged install blocks: the GitHub-subdirectory route plus an **explicit decision on the local-repository route** — an `id=`-tagged block (harness-covered) or a recorded docs-covered-only entry in the scenario registry (validator note A; never implicit). Plugin-strict-TDD applies: RED smoke scenario first, then installer change. Threat-matrix git-selection coverage: (a) installer run with cwd inside a foreign git repo → foreign repo untouched, `git status` clean; (b) relative `TARGET` rejected; (c) mismatched origin aborts writing nothing (retained safeguards proven against the rewrite).
  - Files: `hosts/herdr/tts-plugin/scripts/install.sh`, `hosts/herdr/tts-plugin/README.md`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend).
  - Verify: new install-specific smoke cases `34a`–`34j` pass (`cd hosts/herdr/tts-plugin && bash scripts/smoke-tests.sh` — the new scenarios are green); engine hygiene scan green over installer + README (legacy-ref patterns). Full-suite result honestly recorded: `993 passed, 2 failed (16n, 40e)` — both are known pre-existing baseline failures proven at base, fixed by tasks 1.10 and 1.11. This does NOT satisfy M1 closure: the full plugin smoke suite is NOT claimed green by this task.
  - OQ: none. Provides the `id=` blocks consumed by task 1.8.
  - Est: ~260 lines.

- [x] 1.5 **Bootstrap dev-mode `engine/` derivation (audit B5)** (slice 5) — replace the hardcoded `~/Code/personal/agent-tts` dev checkout with own-location-derived `<checkout>/engine/`; actionable non-zero failure when `HERDR_TTS_DEV=1` but no discoverable `engine/`; public install ignores any decoy checkout; retained: uv/Python fallback, immutable pin, upgrade behavior, checkout independence. Plugin-strict-TDD: RED scenarios first.
  - Files: `hosts/herdr/tts-plugin/scripts/bootstrap.sh`, `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend).
  - Verify: plugin smoke suite green for the focused `plugin-bootstrap` delta scenarios (public install ignores decoy; dev opt-in derives `engine/` from own location; missing-engine fails actionably). The FULL plugin smoke suite is claimed green only at M1 closure, after tasks 1.10 and 1.11 fix the known 16n/40e baseline failures — never prematurely before those fixes.
  - OQ: none. Ordering note: task 1.11 depends on this task (both edit `bootstrap.sh`; sequenced, never merged, separate rollbacks).
  - Est: ~150 lines.

- [x] 1.6 **Packaging wrappers: legacy corrections + honest support wording** (slice 6) — npm `package.json` + `bin/herdr-tts` shim and Homebrew `herdr-tts.rb`: monorepo `url`/`homepage`, zero legacy repo references, no unsupported claims (no registry publication, no native Windows, no validated cross-platform coverage); the keg route documents honestly that the first-run wizard is not available there.
  - Files: `hosts/herdr/tts-plugin/packaging/npm/package.json`, `hosts/herdr/tts-plugin/packaging/npm/bin/herdr-tts`, `hosts/herdr/tts-plugin/packaging/npm/README.md`, `hosts/herdr/tts-plugin/packaging/homebrew/herdr-tts.rb`, `hosts/herdr/tts-plugin/packaging/homebrew/README.md`, `engine/tests/test_versioned_tree_hygiene.py` (extend: `LEGACY_FREE_FILES` gains the five packaging wrappers as an exact file list — never a blanket packaging scan, which would falsely flag the out-of-scope `PUBLISH.md` scoped npm alias).
  - Verify: engine hygiene scan green over packaging scope; grep proves zero legacy refs; wrapper claims reviewed against the `independent-installation` honest-wrapper-support scenarios.
  - OQ: none.
  - Est: ~140 lines.

- [x] 1.7 **Bounded OpenSpec config corrections** (slice 7) — root `openspec/config.yaml`: registry-scope wording clarified to permit this change's bounded metadata correction and explicit delta routing; `hosts/herdr/tts-plugin/openspec/config.yaml`: correct the stale "engine is a separate external repository" statement. No registry merger, no ownership transfer, no `strict_tdd` rewrite.
  - Files: `openspec/config.yaml`, `hosts/herdr/tts-plugin/openspec/config.yaml`.
  - Verify: N/A runtime boundary (metadata-only, no executable behavior); engine hygiene suite still green; diff reviewed against proposal §Registry Ownership and Spec Routing.
  - OQ: none.
  - Est: ~40 lines.

- [ ] 1.8 **Scenarios 1+2: `plugin-fresh-clone`, `plugin-subdir-install` (OQ-6 retrieval + OQ-1 probe)** (slice 8) — author both V2 scenarios executing the literal `id=` blocks from task 1.4 inside the sandbox. Scenario 1 is the **OQ-6 evidence**: the documented GitHub route must retrieve exactly `d66616bce3ad8193f11ae615bd58bb4508eb65be` from `https://github.com/chiptime/agent-tts.git` (with `#subdirectory=engine`) and materialize `hosts/herdr/tts-plugin/` + `engine/`; local object availability or a stubbed install is NOT retrieval evidence. Scenario 2 is the **OQ-1** subdirectory-materialization probe (evidence for `plugin_root = managed_path/<subdir>`) — an evidence question, not a user decision. **Runs AFTER task 1.11**: both scenarios need the corrected pin and the documented-origin retrieval semantics.
  - Files: `scripts/acceptance/scenarios/01-plugin-fresh-clone.sh`, `scripts/acceptance/scenarios/02-plugin-subdir-install.sh` (create).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 1` runs both against the real documented origins (network authorized — Engram #9681); both report `PASS` from genuine installs. If the exact SHA is unavailable from the origin, scenario 1 reports `BLOCKED` (exit 2) with the recorded reason and M1 stays open with evidence — never a substitute SHA, moving branch, cache-only pass, or silent fallback.
  - OQ: none blocks authoring or running (documented origins authorized). **OQ-6** is resolved by scenario 1's outcome: unavailable ⇒ `BLOCKED`, M1 remains blocked with evidence, no substitute. **OQ-1** is resolved by scenario 2's outcome — if falsified, adopt the pre-designed vendoring fallback (design Decision 1) as one additional slice.
  - Est: ~240 lines.

- [x] 1.9 **Scenario 3 activation correction: `activates_at_milestone` 1 → 2** (slice 3a) — design **Decision 10**: edit the `zero-machine-paths` row of `scripts/acceptance/scenarios/registry.conf` from `1` to `2` (tab-separated, one field — the exact one-line diff in the design). The registry's own semantics then produce the intended state with no special case: authored (file present from task 1.3) **and** `current < 2` ⇒ `NOT-YET-ACTIVATED` throughout M1; the scenario activates when M2 task 2.2 delivers the behavior. **Depends only on task 1.3 (delivered)** — it may run before task 1.5 — and **must land in M1 before closure**: with the field still at 1, an active scenario 3 fails under the binding gate (Engram #9636) and M1 cannot close.
  - Files: `scripts/acceptance/scenarios/registry.conf` (one field).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 1` reports scenario 3 `NOT-YET-ACTIVATED` — not an active `FAIL`, not a green skip; after task 2.2, `--milestone 2` reports scenario 3 `PASS`.
  - OQ: none. Commit: small separate work-unit commit (~15 lines). Rollback boundary: the single registry field; never reverted alone — reverting re-exposes the active-FAIL inconsistency.
  - Est: ~15 lines.

- [ ] 1.10 **Smoke 16n host-safety isolation: playback lock/PID/IPC + engine socket** (slice 8a) — design **Decision 8** safety fix. `hosts/herdr/tts-plugin/bin/herdr-tts` lines 26–28 become environment-overridable with byte-identical defaults: `${HERDR_TTS_LOCK_FILE:-/tmp/herdr-tts-playing.lock}`, `${HERDR_TTS_PID_FILE:-/tmp/herdr-tts-current.pid}`, `${HERDR_TTS_IPC_SOCKET:-/tmp/herdr-tts-player.sock}` — no other launcher line changes. `new_env()` in `smoke-tests.sh` additionally exports the three overrides plus `AGENT_TTS_SOCKET` to sandbox-local `$T/run/…` paths, isolating every scenario (not only section 16). Production toggle behavior and the existing read confirmation assertion are preserved. Plugin strict-TDD: RED tests first — (a) defaults-preserved static assertion in the proven `assert_grep` style; (b) with no sandbox lock, `r` reaches transcribe/read and the section-16 confirmation assertion runs verbatim; (c) a planted sandbox lock holding a live `sleep` PID → `r` prints `player.stopped`, that PID receives the TERM, and only the three sandbox files are removed; (d) suite-level host-state invariance: presence/inode/mtime/content of the three host defaults plus the engine default socket unchanged across a full suite run (snapshot only reads; never creates host state). Isolated fixtures/recorders only — never a live host daemon as a test fixture; no assertion weakened.
  - Files: `hosts/herdr/tts-plugin/bin/herdr-tts` (3 lines), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend).
  - Verify: RED→GREEN for the four Decision-8 tests; section 16 passes with the `▶️ Transcribing and reading…` confirmation intact; a whole-suite run may be recorded solely for the read-only host-state-invariance snapshot — while 40e remains unfixed the suite reports `993 passed, 2 failed (16n/40e)` and must NOT be called green (marking this task complete on a false full-suite pass would violate the no-baseline-exception policy). Full plugin suite green is deferred to task 1.11 / M1 closure.
  - OQ: none. Rollback boundary: launcher overrides and `new_env()` exports revert together — a partial revert leaving the suite pointing at host defaults restores the hazard and is forbidden.
  - Est: ~130 lines.

- [ ] 1.11 **Smoke 40e oracle identity: exact pin + checked dual-layout lookup** (slice 8b) — design **Decision 9**. `hosts/herdr/tts-plugin/scripts/bootstrap.sh`: `AGENT_TTS_REF` re-pinned to the exact full SHA `d66616bce3ad8193f11ae615bd58bb4508eb65be` (`HERDR_AGENT_TTS_REF` override retained; the existing `40g` SHA-pinning assertion keeps passing unchanged). Rewrite the 40e identity driver in `smoke-tests.sh` to be return-code-checked and dual-layout at that one revision: (1) revision pre-check `git cat-file -e <pin>^{commit}` — an unknown pin fails as "revision absent", distinct from a layout miss, with no layout retry; (2) dual-layout probe `engine/src/agent_tts/` (monorepo) then `src/agent_tts/` (legacy) at that single revision, the layout selected **as a unit** (all three oracle files or none; layout compatibility is never a revision fallback); (3) every lookup asserts returncode == 0 **and** `^[0-9a-f]{40}$` on the resolved value — `git rev-parse` echoes its argument to stdout on failure (rc 128), so emptiness-based guards never suffice; (4) strict byte identity retained for `boundaries.py`, `cleaner.py`, `redact.py` with the full existing F1–F9 assertion matrix — no assertion weakened, deleted, or made to pass by changing engine bytes. Local resolution is not public-retrievability evidence; that proof belongs to task 1.8 (OQ-6). Plugin strict-TDD: RED tests a–e of Decision 9 first. **Depends on task 1.5** — both edit `bootstrap.sh`; sequenced, never merged, separate rollbacks.
  - Files: `hosts/herdr/tts-plugin/scripts/bootstrap.sh` (pin), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (40e driver rewrite).
  - Verify: RED→GREEN for the five Decision-9 tests; 40e green at the selected pin (monorepo layout, blob ids verified locally); `40g` still passes; an absent path yields a hard named failure whose echoed-argument stdout is never compared as a blob id; a bogus revision fails as "revision absent".
  - OQ: none locally — public retrieval of the pin is task 1.8's separate V2 evidence gate (OQ-6). Rollback boundary: pin and 40e driver revert together with their evidence; restoring `32e9bafb` restores the known baseline failure, not a passing release.
  - Est: ~140 lines.

**Milestone-1 closure**: all three project V1 suites green (engine + brain + plugin — the plugin suite counts green only after tasks 1.10 and 1.11 have fixed smoke 16n and 40e; **no baseline exception**: neither failure may be waived, skipped, disabled, or relabelled green) + harness `--milestone 1` truthful report + work-unit commit(s). V2 scenarios 1 and 2 green from documented-origin installs — exact-pin retrieval proven (OQ-6) or M1 remains `BLOCKED` with recorded evidence and no substitute. Scenario 3 reported `NOT-YET-ACTIVATED` at M1 (activates at M2 via tasks 1.9 + 2.2); scenarios 4–9 likewise `NOT-YET-ACTIVATED`; the full-suite gate applies only at M4.

---

## Milestone 2: Machine-path decoupling (design slices 9–14)

- [ ] 2.1 **`resolve.py` + bash resolvers: monorepo root, `HERDR_BIN`, port, `HERDR_TTS_HOME`** (slice 9) — create `tools/herdr_onboarding/resolve.py` implementing the Decision-4 resolution orders (root: `HERDR_PLUGIN_ROOT` → ascend from the resolved real path of the executing script; `HERDR_TTS_HOME`; `HERDR_BIN` six-step discovery; port: `HERDR_BRAIN_PORT` → persisted config → `8741`); identical minimal bash resolver functions in both launchers for pre-Python use (ascend from `dirname "$(readlink -f "${BASH_SOURCE[0]}")"`, up to 6 levels); port knob plumbing in brain config.
  - **OQ-2 RESOLVED (Engram #9681 → design Decision 4) — no gate remains.** Implement the confirmed precedence: `HERDR_TTS_HOME` set-and-valid wins (override semantics preserved); unset → derive from launcher location; never export a hardcoded default. Tests assert the resolved choice, including that an exported `HERDR_TTS_HOME` is honoured even when a sibling `tts-plugin` directory exists.
  - Files: `tools/herdr_onboarding/__init__.py` + `tools/herdr_onboarding/resolve.py` (create), `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/tts-plugin/bin/herdr-tts` (extend), `hosts/herdr/brain/src/herdr_brain/config.py` (port knob), `hosts/herdr/brain/tests/test_resolve.py` (create).
  - Verify: `cd hosts/herdr/brain && python -m pytest tests/test_resolve.py -q` green (resolution table with injected env dicts + `tmp_path`).
  - OQ: none (OQ-2 resolved — Engram #9681, design Decision 4).
  - Est: ~300 lines.

- [ ] 2.2 **Brain launcher repair: A1/C1/C2/C4 removal + parity tests (RNF-4)** (slice 10) — **REPAIR, not create**: `hosts/herdr/brain/bin/herdr-brain` exists (279 lines, tracked since `8de8a34`) and carries every defect verbatim. Remove the dead `HERDR_TTS_HOME` default export, the `$HOME/.dotfiles/shell/private-env.sh` scrape, the literal `/home/linuxbrew/.linuxbrew/bin/herdr` fallback, and the personal `tail2640fd.ts.net:8443` reference; optional remote-exposure domain from config/env only (RF-AT-11-9); paired compatibility assertions — legacy-shaped and corrected layouts both observably resolve the TTS surface (parity proof). Activates scenario 3 (design Decision 10: M2 activation) and completes its green (with 1.4–1.7 and 2.4).
  - Files: `hosts/herdr/brain/bin/herdr-brain` (repair), `hosts/herdr/brain/herdr-plugin.toml` (extend only if actions change), `hosts/herdr/brain/tests/` (parity module — create), `engine/tests/test_versioned_tree_hygiene.py` (scope manifest extends to the launcher in this unit), `scripts/acceptance/scenarios/03-zero-machine-paths.sh` (assertions finalized).
  - Verify: brain pytest parity green; `bash scripts/acceptance/clean-install.sh --milestone 2` → scenario 3 `PASS`.
  - OQ: none (OQ-2 resolved — the launcher implements the confirmed precedence delivered by task 2.1).
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
  - OQ: none (scenario stays within the sandbox; no network).
  - Est: ~160 lines.

**Milestone-2 closure**: V1 green + `--milestone 2` (scenarios 3, 7 `PASS`; scenario 3 newly activated — no regression on previously green scenarios 1, 2) + work-unit commit(s).

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
  - **OQ-3 RESOLVED (Engram #9681 → design Decision 6) — assertions unblocked.** Assert the literal resolved vocabulary: `/health` `stt` is `loading | ready | unavailable`, `unavailable` after refusal; `degraded` stays a `tts`-field-only value; RF-12 is vocabulary errata; frozen contracts stay byte-identical.
  - Files: `tools/herdr_onboarding/steps/stt.py` (create), `hosts/herdr/brain/tests/` (step tests — create).
  - Verify: brain pytest STT-step green asserting the resolved `unavailable` vocabulary; `--milestone 3`: scenario 6 `PASS` (offline refusal), scenario 5 runs on the authorized documented model origin (Engram #9681) and reports `PASS`, or truthful `BLOCKED` (exit 2) if the host/network prerequisite is genuinely unavailable.
  - OQ: none — OQ-3 resolved (Decision 6); network authorized for the documented STT origin (Engram #9681); unlisted origins stay `BLOCKED`.
  - Est: ~260 lines.

- [ ] 3.5 **Entry-point wiring both sides + health gate + scenario 4** (slice 19) — `hosts/herdr/tts-plugin/bin/herdr-tts`: first-run marker check, TTY detection, noninteractive hint, wizard hand-off via the resolver function; `hosts/herdr/brain/bin/herdr-brain`: the same dispatch; health gate before marker write: `/health` reports `tts: ok` and `herdr plugin list` emits no manifest warnings; failed gate → no marker, next launch retries; wizard failure/abort → install stays healthy, no partial state (exit 40 path). Scenario 4 `first-run-keys`: non-argv key capture end-to-end in the sandbox.
  - Files: `hosts/herdr/tts-plugin/bin/herdr-tts` (extend), `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (extend; plugin-strict-TDD: RED first), `scripts/acceptance/scenarios/04-first-run-keys.sh` (create).
  - Verify: plugin suite green (dispatch, skip, nonblocking-unattended scenarios); `--milestone 3` → scenario 4 `PASS`, scenario 6 `PASS`, no regression on 3, 7; scenario 5 truthful per its real prerequisites.
  - OQ: none — network authorization is bounded to documented origins (Engram #9681); if scenario 4's run proves to require a non-documented origin, it reports `BLOCKED`, never green.
  - Est: ~280 lines.

**Milestone-3 closure**: V1 green + `--milestone 3` (scenarios 4, 5, 6 `PASS` — scenario 5 on the authorized documented model origin; truthful `BLOCKED` only if a real prerequisite is unavailable) + work-unit commit(s).

---

## Milestone 4: Doctor + docs (design slices 20–23)

- [ ] 4.1 **Doctor: six checks + actionable remediation + two dispatchers** (slice 20) — checks implemented once in `tools/herdr_onboarding/`, exposed twice: (1) CLI on PATH (`command -v herdr-tts` + `~/.local/bin` coverage), (2) audio backend platform probe, (3) credentials (`~/.config/herdr-brain/env` present, mode 600, `GLM_API_KEY` non-empty; `--fix-credentials` re-runs capture only), (4) daemon liveness (pidfile + `kill -0`, then `/health`), (5) contract v1 (`herdr-tts --contract-version` ≥ 1), (6) STT model state via offline `stt.model_is_cached()` (never downloads). Every failure line carries a copy-pasteable remediation command; a check with no remediation is a specification failure, asserted by a V1 test walking the check table. Dispatchers: `herdr-tts doctor`, `herdr-brain doctor`; `--doctor` on the wizard runs checks only, no mutation.
  - Files: `tools/herdr_onboarding/report.py` + checks module (create), `hosts/herdr/tts-plugin/bin/herdr-tts` (extend), `hosts/herdr/brain/bin/herdr-brain` (extend), `hosts/herdr/brain/tests/test_doctor.py` (create).
  - Verify: brain pytest `test_doctor.py` green including the remediation-completeness walk; doctor breakage drill (simulated failure per check names its repair).
  - OQ: none.
  - Est: ~330 lines.

- [ ] 4.2 **Scenarios 8+9: `doctor-diagnoses-break`, `post-wizard-health`** (slice 21) — scenario 8: simulated breakage per check → doctor output names the exact repair command; scenario 9: complete first-run → `/health` `tts: ok`, plugin list clean, marker present; scenario 9's timed-human half stays V3 and is never asserted by the harness.
  - Files: `scripts/acceptance/scenarios/08-doctor-diagnoses-break.sh`, `scripts/acceptance/scenarios/09-post-wizard-health.sh` (create).
  - Verify: `bash scripts/acceptance/clean-install.sh --milestone 4` → scenario 8 `PASS`; scenario 9 runs on the authorized documented origins and reports `PASS`, or truthful `BLOCKED` if a real prerequisite is unavailable.
  - OQ: none (network authorized for documented origins — Engram #9681; truthfulness rules apply).
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

**Milestone-4 closure**: `clean-install.sh --milestone 4` full-suite green (all nine scenarios) is the mechanically unavoidable final gate — network-dependent scenarios run on the authorized documented origins (Engram #9681); any genuinely unavailable prerequisite is truthful `BLOCKED`, which prevents closure. V3 human sign-off is required before merge.

---

## Dependency and order notes

1. **Harness-first is binding**: task 1.1 precedes every other task — the loop's stop criterion (exit codes, journal, activation model) depends on it. 1.2 extends the harness and seeds the V1 hygiene framework; 1.3–1.8 plus 1.9–1.11 add scenarios and the first repairs.
2. **Scenario 3 activation/green split (design Decision 10)**: authored at 1.3 (slice 3); its registry activation is corrected 1 → 2 by task 1.9 (slice 3a), so it reports `NOT-YET-ACTIVATED` throughout M1; it activates and turns green at 2.2 (slice 10, "Activates V2: 3") under the binding active-scenarios gate (Engram #9636) — never an active FAIL at M1, never a green skip. The hygiene scan's scope manifest travels with each repair (1.4, 1.5, 1.6, 2.2, 2.4 extend it in the same work unit) so the three project suites stay green at every unit boundary.
3. **M1 continuation order**: 1.5 → 1.6 → 1.7; then 1.9, 1.10, 1.11 as dependencies allow; then 1.8; then M1 closure. **1.9 depends only on 1.3 (delivered)** and may run before 1.5 — but it must land before M1 closure, or an active scenario 3 fails the gate. **1.10 has no M1 dependencies** (launcher lines 26–28 + `new_env()` are untouched by other M1 slices). **1.11 depends on 1.5** (both edit `bootstrap.sh`; sequenced, never merged — the pin is a supply-chain decision, the dev-mode derivation a path fix, and they keep separate rollbacks). **1.8 runs after 1.11** (scenarios 1+2 need the corrected pin and documented-origin retrieval). **1.10 and 1.11 both land before M1 closure** — the plugin suite counts green only after both.
4. **Installer corrections (1.4) provide the `id=` blocks** consumed by scenarios 1–2 (1.8). Bootstrap (1.5), packaging (1.6), and config (1.7) are independent of 1.4 but sequence after 1.3.
5. **M2 chain**: 2.1 (resolved precedence) → 2.2 → 2.3 → 2.4; 2.5 follows 1.4 (same installer); 2.6 follows 2.5.
6. **M3 chain**: 3.1 → 3.2 → 3.3 → 3.4 → 3.5 (wiring last); 3.4 asserts the resolved `unavailable` vocabulary (design Decision 6); validator note B lands in 3.2.
7. **M4 chain**: 4.1 → 4.2 → 4.3 → 4.4; the doctor needs the M3 wizard detection layer (Decision 7).
8. **Network authorization granted** (Engram #9681) for documented PyPI/uv, GitHub, and the M3 STT model origin; anything unlisted stays `BLOCKED`, never green. The only remaining gates are evidence questions answered by running scenarios: **OQ-6** (exact-SHA public retrieval, scenario 1 — unavailable ⇒ M1 blocked with recorded evidence, no substitute) and **OQ-1** (subdirectory materialization, scenario 2 — falsified ⇒ pre-designed vendoring fallback as one extra slice). Closure evidence always stays truthful (`BLOCKED`, exit 2, journal names the missing prerequisite).
9. **Plugin-strict-TDD tasks** (local `strict_tdd: true` governs `smoke-tests.sh`): 1.4, 1.5, 1.10, 1.11, 2.5, 3.1, 3.5 author the RED smoke scenario before the `bin/`-side change. All other tasks follow workspace `strict_tdd: false` — tests ship with the behavior.
10. **Frozen contracts** `contracts/ipc-v2` and `contracts/tts-brain-v1` are read-only for every task; byte-identity is asserted at 4.4.
11. **Base correction honored**: 2.2 is a repair of files already tracked on `main`; no task recreates `hosts/herdr/brain/bin/herdr-brain` or `hosts/herdr/brain/herdr-plugin.toml` from scratch.
12. **No implementation in this planning phase**: apply creates work-unit commits only; the maintainer creates PRs (feature-branch-chain) after V3 — apply never pushes, never opens PRs, never runs remote git.
