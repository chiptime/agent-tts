# AT-11 V3 Checklist — Timed Clean-Machine UAT + RF↔Evidence Traceability

> Change: `at-11-instalable` · Verification tier: **V3** (human, timed, clean machine).
> This document is the human acceptance gate for RF-AT-11-* / RNF-AT-11-*. It is
> **not** executed by CI; the automated V1 (pytest) and V2 (acceptance harness)
> tiers are prerequisites and their results are cited below.

## 1. Scope and what V3 proves

V3 proves that a person who has **only the published documentation** can install,
run, and first-run-onboard the Herdr voice stack on a clean machine, within the
target time, with no maintainer-only knowledge. It also times the flow against
RNF-AT-11-3.

Automated tiers (must be green *before* starting):

| Tier | Command | Expected |
|---|---|---|
| V1 | `uv run --with pytest pytest engine/tests/test_versioned_tree_hygiene.py -q` | 13 passed |
| V1 | `cd hosts/herdr/brain && uv run python -m pytest tests/ -q --ignore=tests/browser` | pass except the known Chromium e2e set |
| V1 | `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` | exit 0 |
| V1 | `bash hosts/herdr/tts-plugin/scripts/smoke-tests.sh` (with a UTF-8 locale and the real engine venv) | exit 0 |
| V2 | `bash scripts/acceptance/clean-install.sh --milestone 4` | see §5 — **UNRUN as of this draft** (network installs in scenarios 1/2 are operator-authorized) |

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
| RF-AT-11-1 | Documented fresh-clone install routes | ✔ | scen 1, 2 | pending | Docs + id=-blocks exist; scenarios 1/2 network installs **UNRUN** this session |
| RF-AT-11-2 | CLI on `~/.local/bin`, no manual symlink | ✔ | scen 1, 2 | pending | Installer exposure tested (2.5); V2 pending |
| RF-AT-11-3 | First-run wizard on both entry points | ✔ | scen 4 (stubbed), 6 | pending | Wizard + launcher dispatch tested; real services unproven |
| RF-AT-11-4 | Credential capture into standard 600 paths | ✔ | scen 4 (stubbed) | pending | Threat-matrix tests (argv/ps/failed-run/crash) pass |
| RF-AT-11-5 | STT download with explicit consent + size | ✔ | scen 5 **authored, UNRUN** | pending | Refusal path tested; **real download not executed** |
| RF-AT-11-6 | No machine paths; discovery + configurable port | ✔ | scen 3 (PASS) | pending | Hygiene scope green; scenario 3 PASS |
| RF-AT-11-7 | systemd unit from versioned template | ✔ | scen 3, 7 | pending | Template tests green; scenario 7 PASS |
| RF-AT-11-8 | Portable `bin/herdr-brain` resolution | ✔ | scen 3 | pending | Launcher parity tests green |
| RF-AT-11-9 | Optional remote exposure, no personal domain | ✔ | scen 3 | pending | Allowlist↔docs equivalence green; no personal domains in docs |
| RF-AT-11-10 | `doctor` diagnoses with remediation | ✔ | scen 8 (stubbed) | pending | Check-table + remediation tests green; simulated breakage only |
| RF-AT-11-11 | Idempotent reinstall preserving state | ✔ | scen 7 (PASS) | pending | env/preferences/keymap/marker preservation asserted |
| RF-AT-11-12 | Post-wizard `/health` + clean plugin list | ✔ | scen 9 (stubbed) | pending | Marker after health gate tested; **real services unproven** |

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
