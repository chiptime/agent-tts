# AT-11 V3 Checklist — Timed Clean-Machine UAT + RF↔Evidence Traceability

> Change: `at-11-instalable` · Verification tier: **V3** (human, timed, clean machine).
> This document is the human acceptance gate for RF-AT-11-* / RNF-AT-11-*. It is
> **not** executed by CI; the automated V1 (pytest) and V2 (acceptance harness)
> tiers are prerequisites and their results are cited below.

**Status (2026-10-10): deferred at the maintainer's request.** No clean-machine
human UAT has been performed or signed. Resume using §7; tasks 3.5 and 4.2 remain
open. The existing automated evidence does not establish a V3 pass.

## 1. Scope and what V3 proves

V3 proves that a person who has **only the published documentation** can install,
run, and first-run-onboard the Herdr voice stack on a clean machine, within the
target time, with no maintainer-only knowledge. It also times the flow against
RNF-AT-11-3.

Automated evidence before starting (full closure is still pending):

| Tier | Command | Expected |
|---|---|---|
| V1 | `uv run --with pytest pytest engine/tests/test_versioned_tree_hygiene.py -q` | 13 passed |
| V1 | `cd hosts/herdr/brain && uv run python -m pytest tests/ -q --ignore=tests/browser` | pass except the known Chromium e2e set |
| V1 | `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` | exit 0 |
| V1 | `bash hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (with a UTF-8 locale and the real engine venv) | exit 0 |
| V2 | `bash scripts/acceptance/clean-install.sh --milestone 4` with explicitly authorized model download and the brain interpreter | Observed: **8 PASS / 0 FAIL / 1 BLOCKED**, exit 2. Scenario 9 remains pending; this is not a green milestone closure. |

The authorized V2 run exercised scenarios 1–8, including a real Hugging Face
`base` model pull and real STT `ready` handler in scenario 5 (daemon doubled).
Some scenarios use explicitly recorded external test doubles. Per the maintainer
decision recorded in `openspec/changes/at-11-instalable/apply-progress.md`, scenario
9's real post-wizard health proof will be obtained through this human UAT, not
through additional sandbox automation. Tasks 3.5 and 4.2 remain open until that
proof is recorded; a signed UAT does not retroactively change the harness result.

## 2. Prerequisites (clean machine)

- A machine (or VM) with **no** prior agent-tts/herdr install and **no** developer dotfiles.
- Supported OS for the stack: **Linux or WSL2**, or **macOS**. Native Windows is **not** a claimed target for the Herdr stack.
- Network access to the documented origins only (see `scripts/acceptance/allowed-origins.txt`).
- A Herdr installation and a personal CLI/LLM key to enter through the wizard.

## 3. Timed procedure (publish-only, no repo knowledge)

Follow `hosts/herdr/tts-plugin/README.md` § 📦 Installation and `hosts/herdr/brain/README.md` § Deployment verbatim. Record wall-clock time at each step.

1. Install the plugin (registry route, then optionally the curl route).
2. First interactive launch → the onboarding wizard runs (credentials, voice, keymap, STT consent).
3. Choose an STT size (or `none`) and let the model download if chosen.
4. Confirm the completion marker exists and `herdr-tts doctor` is clean.
5. Start the brain (plugin route, or optional systemd user unit) and confirm `/health`.

### Timing readings (both required — RNF-AT-11-3)

RNF-AT-11-3 states the target with the **host already present** (< 10 min including the `base` STT model on home broadband). The UAT scenario's clock **includes** host preparation. Both must be stated so the gate is unambiguous.

| Reading | Definition | Target | Observed | Date / operator |
|---|---|---|---|---|
| A — host-preinstalled (RNF-AT-11-3) | Clock starts with Herdr/deps already installed; ends at a working stack incl. `base` STT | < 10 min | _pending_ | _human_ |
| B — host-included (UAT scenario) | Clock starts on the bare machine; includes installing prerequisites | reported, not gated | _pending_ | _human_ |

## 4. Friction log

Template — one row per friction point encountered while following the docs:

| # | Step | What happened | Class | Routing |
|---|---|---|---|---|
| 1 | | | doc-gap / bug / wrong-command / missing-prereq / other | doc-fix (README) or issue (code) |

Routing rule: **doc-gap / wrong-command / missing-prereq → fix the README**; **bug → open an issue with the failing command and evidence**. A friction point that does not change any documented step is still logged.

## 5. RF↔Evidence traceability matrix

Evidence sources: **V1** = unit/hygiene tests; **V2** = acceptance harness scenarios (numbers per `scripts/acceptance/scenarios/registry.conf`); **V3** = this checklist. Status reflects what is **observed**, not what is intended; gaps are stated explicitly.

| RF | Title (short) | V1 | V2 | V3 | Evidence / gap |
|---|---|---|---|---|---|
| RF-AT-11-1 | Documented fresh-clone install routes | ✔ | scen 1, 2 PASS | pending | Documented-origin network installs observed; see apply-progress for the candidate-ref scope and limits |
| RF-AT-11-2 | CLI on `~/.local/bin`, no manual symlink | ✔ | scen 1, 2 PASS | pending | Installer exposure tested (2.5); automated route evidence observed |
| RF-AT-11-3 | First-run wizard on both entry points | ✔ | scen 4 (stubbed), 6 | pending | Wizard + launcher dispatch tested; real services unproven |
| RF-AT-11-4 | Credential capture into standard 600 paths | ✔ | scen 4 (stubbed) | pending | Threat-matrix tests (argv/ps/failed-run/crash) pass |
| RF-AT-11-5 | STT download with explicit consent + size | ✔ | scen 5 PASS | pending | Real `base` model pull, speech-surface contract check and STT `ready` handler observed; daemon doubled |
| RF-AT-11-6 | No machine paths; discovery + configurable port | ✔ | scen 3 (PASS) | pending | Hygiene scope green; scenario 3 PASS |
| RF-AT-11-7 | systemd unit from versioned template | ✔ | scen 3, 7 | pending | Template tests green; scenario 7 PASS |
| RF-AT-11-8 | Portable `bin/herdr-brain` resolution | ✔ | scen 3 | pending | Launcher parity tests green |
| RF-AT-11-9 | Optional remote exposure, no personal domain | ✔ | scen 3 | pending | Allowlist↔docs equivalence green; no personal domains in docs |
| RF-AT-11-10 | `doctor` diagnoses with remediation | ✔ | scen 8 (stubbed) | pending | Check-table + remediation tests green; simulated breakage only |
| RF-AT-11-11 | Idempotent reinstall preserving state | ✔ | scen 7 (PASS) | pending | env/preferences/keymap/marker preservation asserted |
| RF-AT-11-12 | Post-wizard `/health` + clean plugin list | ✔ | scen 9 BLOCKED; local simulated leg tested | pending | Real services remain unproven; obtain this evidence during the signed clean-machine UAT |

| RNF | Title (short) | Coverage |
|---|---|---|
| RNF-AT-11-1 | No new runtime deps (stdin/stdout only) | V1 (dependency-free text interaction tests) |
| RNF-AT-11-2 | No key printed/logged; `getpass`, never re-echoed | V1 (redaction + threat-matrix tests) |
| RNF-AT-11-3 | Full install < 10 min (host present) | V3 timing reading **A** (pending, human) |
| RNF-AT-11-4 | Every audit fix ships with test-verified compatibility | V1 (parity/regression tests per fix) |

### Frozen-contract byte-identity check

```
git diff e9aab2a -- contracts/tts-brain-v1.md contracts/ipc-v2.md   # → empty
```

Result observed during this draft: **empty (no diff)**. Record any future drift here.

## 6. Human sign-off

| Field | Value |
|---|---|
| Operator | |
| Machine / OS | |
| Date | |
| Reading A (host-preinstalled) | |
| Reading B (host-included) | |
| Friction points opened | |
| **Verdict** (pass / pass-with-frictions / fail) | |
| Notes | |

> Sign-off is **human-owned**. An agent must never fill this section or claim V3 passed. Until a human signs, RF/V3 columns stay `pending`.

## 7. Resume the deferred installation UAT

### Prepare the run

- [ ] Choose a clean Linux, WSL2 or macOS machine/VM without an existing voice-stack installation or inherited configuration. Do not clear the maintainer's working installation to simulate a clean machine.
- [ ] Record the OS, prerequisites already present, selected install route and exact revision/ref under test. An older published default ref must not be presented as proof of the current implementation.
- [ ] Read the root installation section and the plugin/brain README instructions for that revision. Record unclear or missing instructions as friction; do not silently replace them with maintainer knowledge.
- [ ] Confirm authorization for the documented installation/model-download origins and for running the local services on that machine. Remote execution requires separate authorization for its destination and access method.
- [ ] Prepare any required brain/provider credential privately. Use the supported interactive, FD or protected-file channel; never paste credentials into chat, command arguments, logs or the evidence report.
- [ ] Record the current automated test results and their unresolved failures. The last recorded V2 result is 8 PASS / 0 FAIL / 1 BLOCKED, not a fully passing milestone.

### Execute and collect evidence

- [ ] Measure both timing readings in §3. For the host-preinstalled target, use the `base` STT model; choosing `none` does not demonstrate the model-inclusive timing requirement. Never estimate one reading from the other.
- [ ] Install and onboard by following only the published instructions. Record each deviation or failure in the friction log.
- [ ] Confirm the expected voice/keymap choices and that credentials remain in the documented mode-600 location. Record paths and permissions, not secret contents.
- [ ] Confirm that the completion marker is present after the successful health gate. Record only non-sensitive marker information.
- [ ] With the real local brain, TTS daemon and Herdr running, record `/health` with `tts: ok` and the STT state consistent with the selected option. A simulated daemon or handler is insufficient for this evidence.
- [ ] Record the real `herdr plugin list` result and verify that it contains no manifest warnings.
- [ ] Run the applicable `doctor` dispatcher; record its check outcomes and any remediation followed. Explain role-specific or intentionally unavailable components rather than claiming every check passed.
- [ ] Re-launch to confirm completed onboarding is not repeated. Keep credential values out of screenshots and logs.

### Record the outcome and close only what was proved

Append a dated run report to this checklist with the tested revision, machine,
commands followed, both measured timings, sanitized health/plugin/doctor results,
friction points and the operator's verdict. Leave unperformed checks pending.

After the operator explicitly supplies a sign-off, reconcile tasks 3.5 and 4.2
against the observed real post-wizard evidence and update `apply-progress.md`.
Do not retroactively relabel scenario 9's automated BLOCKED result as PASS. An
incomplete or failed UAT does not close AT-11.

**Separate validation:** this installation UAT does not establish F2 phone/audio
acceptance (Android playback, audible cancellation latency, incremental audio,
mobile approvals or physical microphone/PTT). Those checks remain under
`docs/voice-stack/MANUAL-TESTS.md`.
