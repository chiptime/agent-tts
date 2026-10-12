# AT-06 — Ducking y prioridad de audio · Execution pack (compact)

> Prepared by the `prd-loop` skill (planning only — nothing here launches implementation).
> Single canonical execution entrypoint. Every other doc is referenced by exact relative path.

- **Root (canonical):** `/home/bruno/Code/personal/agent-tts` — work lives in `engine/`
- **Entrypoint (this doc):** `odd/tasks/at-06-ducking-audio.md`
- **Source PRD:** `docs/prds/AT-06-ducking-audio.md` — sha256 `ae7db58012f399bc105f2f1542a94ea4dde8191d3b2e96dc813e788d6e5355f0`
- **`prd` skill dependency (resolved):** `/home/bruno/.config/opencode/skills/prd/SKILL.md`
- **Baseline:** `HEAD=705d8b2240ecd6220b53ac62d0cf9874fb36ec46`, clean tree on `main`
- **Readiness:** `ready_for_bootstrap` — D4 + D2 confirmed (2026-10-11); D3/M2 stays a Windows-only milestone (see **Gaps**)

---

## Locked decisions (planner, after repo evidence)

- **D1 — Local duck gain does not exist and must be added.** `AudioSession._stream_generator` slices raw PCM with **no attenuation** (`audio.py:490-529`). Add a software gain (dB→linear) applied to the engine's own stream so `duck` attenuates only our output, never other apps (RF-AT-06-3).
- **D2 — Winhost gain travels in the existing v1 header.** Add `gain_db` to the `play` header (unknown keys are already tolerated, `winhost.py:249-258`) and apply it in `PlayStream` (`winhost.py:118-138`) — **no v2 bump** (`PROTOCOL_VERSION=1`, strict version check `winhost.py:247-248`). Resolves the PRD open question. **Confirmed (2026-10-11).**
- **D3 — Two detection backends.** Local: parse `pactl` sink-inputs for role `communication` + active capture (feasible; fail-open to `ignore`). Winhost: a new **`activity` request-reply command** on the control channel (today one-way, no response — `winhost.py:216-248`, `winhost_client.py:129-156`) backed by **WASAPI enumeration on the Windows server**. The WASAPI part is Windows-only and **not verifiable in this Linux environment** (labelled MANUAL).
- **D4 — Default policy.** `AGENT_TTS_DUCK_POLICY` default **`ignore`** (opt-in; the project's "features are born off" rule). `AGENT_TTS_DUCK_DB` default `12.0`; per-target overrides `AGENT_TTS_DUCK_POLICY_<TARGET>` (LOCAL/WINHOST/…). **Confirmed (2026-10-11).**
- **D5 — Policy semantics.** `duck` = apply the gain while the source is active, restore exactly on release (RNF-AT-06-4); `pause` = reuse the existing session pause + smart auto-rewind (`audio.py:234-253`) and resume on release; `ignore` = today's behavior.
- **D6 — Status.** Expose `comm_active=true/false` via `_inject_daemon_fields` (`daemon.py:261-272`); `ipc_reply_json` parses `key=value` before `text=` automatically (RF-AT-06-5).
- **D7 — Fail-open.** Any detection failure (no `pactl`, bus down, winhost unreachable) degrades to `ignore` with ONE warning line; never blocks playback (RF-AT-06-6).
- **D8 — Loop prevention.** Exclude the engine's own stream by application/process match (RF-AT-06-6 risk).
- **D9 — Sweep cadence.** Detect once before creating the `AudioSession` (`cli._build_playback_session`, `cli.py:979-1060` / `daemon._build_queue_session`, `daemon.py:1105-1162`) and a light ≤1 Hz check only while playing (RNF-AT-06-1, zero activity at rest).

## Assumptions (visible)

- **A1.** `duck` uses a software gain on the local stream; on winhost the server applies `gain_db` (server-side). No OS-level volume mutation (zero system config — PRD hard requirement).
- **A2.** `pause` reuses the existing internal pause path (no new transport).
- **A3.** Detection snapshot TTL = the sweep interval; no per-frame polling.

---

## Milestones

- **M1 — Local (verifiable here):** T-01, T-02.
- **M2 — Winhost (Windows-only, mock-tested here + manual on the host):** T-03.

---

## Task registry (single authority)

### T-01 — Ducking core (`ducking.py`)
- Intent: local detection + policy resolution + gain math, fail-open. Traces to RF-AT-06-1/3/6, RNF-AT-06-4.
- Depends: none
- Edit allowlist: `engine/src/agent_tts/ducking.py` (new), `engine/tests/test_ducking.py` (new)
- Contract: `detect_local() -> dict` (pactl sink-input/capture parse → `{comm_active, capture_active}`, fail-open), `resolve_policy(target, env) -> str`, `gain_linear(db) -> float`, `should_duck(state, policy) -> action`. stdlib only; no config of the system.
- Test creator: this task creates `engine/tests/test_ducking.py` (FUTURE until done)
- Evidence: policy matrix; `pactl` fixtures (comm/media/mixed/absent/malformed → fail-open); exact dB↔linear; loop exclusion
- Gate: G-01

### T-02 — Local integration (gain, hook, status)
- Intent: wire detection + `duck`/`pause`/`ignore` into playback and status. Traces to RF-AT-06-3/4/5, RNF-AT-06-1/3.
- Depends: T-01
- Edit allowlist: `engine/src/agent_tts/audio.py`, `engine/src/agent_tts/cli.py`, `engine/src/agent_tts/daemon.py`, `engine/tests/test_ducking.py`
- Contract: add the software gain in `AudioSession._stream_generator` (D1); pre-play detection in `_build_playback_session`/`_build_queue_session`; light periodic check while playing; `comm_active` in status (D6); restore exact gain (RNF-AT-06-4).
- Test creator: existing — `engine/tests/test_ducking.py` (T-01)
- Evidence: gain applied/restored exactly; pause+resume via existing path; status field present; no-op when policy=ignore; zero rest activity
- Gate: G-01

### T-03 — Winhost detection + gain (M2, Windows-only)
- Intent: `activity` query + `gain_db` transport. Traces to RF-AT-06-2.
- Depends: T-01
- Edit allowlist: `engine/src/agent_tts/winhost.py`, `engine/src/agent_tts/winhost_client.py`, `engine/tests/test_ducking.py`, `engine/tests/test_winhost.py`
- Contract: server `activity` command → `{"ok":true,"communication_active":…,"capture_active":…}` (WASAPI enumeration); client `query_activity()`; `gain_db` in the v1 `play` header applied by `PlayStream` (D2).
- Test creator: existing — `engine/tests/test_ducking.py` / `test_winhost.py`
- Evidence: protocol/round-trip against a **mock** server; **real WASAPI detection is MANUAL on the Windows host** (cannot be exercised here)
- Gate: G-01 (mock) + MANUAL (real host)

### T-04 — Docs/index
- Intent: reflect AT-06 delivered.
- Depends: T-02 (T-03 for the winhost note)
- Edit allowlist: `docs/prds/README.md`, `docs/ESTADO-Y-PENDIENTES.md`
- Gate: manual (docs readback)

---

## Gate registry (single authority)

| Gate | Command (literal) | cwd | Source policy | Exit meaning |
|---|---|---|---|---|
| G-01 | `PYTHONPATH=src ../.venv/bin/python -m pytest tests/ -q` | `/home/bruno/Code/personal/agent-tts/engine` | the engine suite (CI-equivalent: `.github/workflows/ci.yml:51-54`). | 0 = pass; nonzero = fail |
| M-01 | — (manual) | Windows host | Real WASAPI `communication_active`/`capture_active` on the Windows host + RNF-AT-06-2/3 metrics | human sign-off on the real host |

The engine suite has PRE-EXISTING intermittent load flakes (`tests/test_daemon.py::test_play_registering_during_shutdown...`, `tests/test_chain_playback.py::test_chain_honors_stop_flag_mid_file...`); both pass in isolation.

---

## Traceability (FR → task → gate)

| FR / RNF | Task(s) | Gate(s) |
|---|---|---|
| RF-AT-06-1 | T-01, T-02 | G-01 |
| RF-AT-06-2 | T-03 | G-01 (mock), M-01 |
| RF-AT-06-3 | T-01, T-02 | G-01 |
| RF-AT-06-4 | T-02 | G-01 |
| RF-AT-06-5 | T-02 | G-01 |
| RF-AT-06-6 | T-01 | G-01 |
| RNF-AT-06-1 | T-02 | G-01 |
| RNF-AT-06-4 | T-01, T-02 | G-01 |

Counts (verified by grep 2026-10-11): **4 tasks**, **1 machine gate (G-01)** + **1 manual (M-01)**, **8 requirements (6 RF + RNF-AT-06-1/4)** across **8 traceability rows**.

---

## Candidate binding (baseline; pre-work)

| File | sha256 |
|---|---|
| `docs/prds/AT-06-ducking-audio.md` | `ae7db58012f399bc105f2f1542a94ea4dde8191d3b2e96dc813e788d6e5355f0` |
| `engine/src/agent_tts/audio.py` | `588cbdc2cc8e2665d2a77eadcce2758e34ea7dfdec48d10b42375034754dac4c` |
| `engine/src/agent_tts/playback_target.py` | `4734c08d79fe67d2ccf7e2659d558053ff0568e8a7e4abc4badd4220807dd7e0` |
| `engine/src/agent_tts/winhost.py` | `2466e44bd74e7696c6907d1bb001bb3eb5bca188f8fe00c4041083c04f899b8d` |
| `engine/src/agent_tts/winhost_client.py` | `291baa3a1c8b14fe9bd83c860097f3826cc523262bed15f8dd504ca4208049b8` |
| `engine/src/agent_tts/daemon.py` | `c5556201ace4fa7ecd8a36aaaf301612ed0937dabd7dc63a965d41c8c405cc24` |
| `engine/src/agent_tts/cli.py` | `cb7791882aec8b1e3c2888efeda9419f1f41bbb32c037961280b9f69e860f36a` |
| `engine/src/agent_tts/constants.py` | `f58e7f8dac216a3b2dddfeb016b82b5ea3d59154266aeba437952aee5f27a853` |

`engine/src/agent_tts/ducking.py` and `engine/tests/test_ducking.py` do not exist yet (FUTURE — created by T-01).

## Checks run vs pending

- **Run:** `git rev-parse HEAD`, `sha256sum` of the bound files, read of the mapped surfaces.
- **Pending:** every gate (no ducking code exists).

## Gaps blocking `ready_for_execution`

1. **M2 is not verifiable in this environment:** the winhost/WASAPI backend is Windows-only; T-03 is mock-tested here and validated on the real host (M-01).
2. **FUTURE gate on the critical path:** `engine/tests/test_ducking.py` (T-01).

Resolved — D4 and D2 confirmed by the maintainer (2026-10-11). The pack is `ready_for_bootstrap`, not yet `ready_for_execution`.

## Resume bookmark (maintained by the future executor)

```markdown
## Resume bookmark (maintained by the future executor)
- Pack identity: odd/tasks/at-06-ducking-audio.md — sha256 pending finalization (pack is ready_for_bootstrap)
- Completed: (none — pack is ready_for_bootstrap)
- Active task: (none) — budget remaining: 2 remediation rounds per milestone
- Next action: T-01 (ducking core: `pactl` parse + policy + gain math)
```

## Unresolved choices

- None for M1. D4 and D2 confirmed (2026-10-11).

## Launch request

- Repository root: `/home/bruno/Code/personal/agent-tts` (canonical)
- Pack entrypoint: `odd/tasks/at-06-ducking-audio.md`
- Readiness: `ready_for_bootstrap`
- Milestones: M1 (local), M2 (winhost)
- FRs covered: RF-AT-06-1..6 (count 6) + RNF-AT-06-1/4; RNF-AT-06-2/3 are manual
- Tasks: 4 — Gates: 1 machine + 1 manual
- Non-goals: third-party app ducking, Bluetooth profile changes, multi-zone mixing
- Remediation budget: 2 functional rounds per milestone
- Limitations: this pack authorizes nothing; nothing runs until the user explicitly launches it.
