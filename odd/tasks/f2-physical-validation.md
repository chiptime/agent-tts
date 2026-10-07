# F2 — Physical validation on a real phone (consolidated session)

**Feature id:** `f2-physical-validation` · **Worktree:** `agent-tts-worktrees/roadmap` · **Branch:** `docs/voice-stack-roadmap`
**Sources:** `docs/voice-stack/ROADMAP.md` §2.3/§4-F2 · `docs/voice-stack/EXECUTION.md` §8 · `docs/voice-stack/MANUAL-TESTS.md` (manual-física) · brain `odd/tasks/action-approval-gate.md` (T8 re-smoke) · brain `odd/tasks/announcements-without-call.md` (T5, AC1–AC6) · brain `docs/PRD-action-approval-gate.md` · brain `docs/PRD-announcements-without-call.md`

## Status

**PREPARED — not executed.** This document prepares the single phone session
authorized by the maintainer (ROADMAP idea 3: one session, three
beneficiaries). No device was available when it was written; **every phone
check below is unchecked and no result is claimed**. Historical automated
evidence is referenced as history only; nothing is recertified here.

## Objective

One physical session on the owner's Android phone closes three pending
validations at once: the voice-stack physical layer (EXECUTION §8), the
`/approval` PWA re-smoke in Chrome Android, and `announcements-without-call`
T5 (AC1–AC6). Order within the session is free; all three need the same
running brain and the same phone.

## Scope and constraints

- Documentation only in this task. No service launches, installs, network,
  provider calls, or credential/LAN discovery were performed to prepare it.
- Launch paths below were verified against this worktree and the canonical
  checkout on 2026-10-07 (see Prerequisites). Nothing was started.
- Items that depend on owner configuration (real TTS providers, fallback
  chain) are marked **BLOCKED (config-dependent)**; they stay unchecked until
  the owner configures them. No real-provider fallback proof is promised.
- The automated evidence in `MANUAL-TESTS.md` (run-dir
  `20261001T084919Z-vs2c`, from the now-deleted `voice-stack` worktree) is
  history: it is not re-run, re-verified, or invalidated by this session.

## Prerequisites (verified 2026-10-07, no service started)

| # | Prerequisite | Verified fact |
|---|---|---|
| P1 | Code base contains the whole voice-stack | `main` @ `5e833fe` is an ancestor of this branch; `feat/voice-stack` is merged into `main`. Old worktree `agent-tts-worktrees/voice-stack` no longer exists on disk. |
| P2 | v2 host capability | `bin/herdr-tts:7038` handles `--render-text-segmented` in BOTH the canonical checkout and this worktree; brain negotiates via `host_supported_protocols` (`speech.py:98-108`). |
| P3 | Brain launch env vars | `HERDR_BRAIN_HOST` (default `127.0.0.1`, `server.py:1229`), `HERDR_BRAIN_PORT` (default 8741, `config.py:24`), `GLM_API_KEY` (`config.py:220`, optional; without it `/ask` returns 503). |
| P4 | Default TTS host | `DEFAULT_TTS_HOME = ~/Code/personal/agent-tts/hosts/herdr/tts-plugin` (`config.py:30`): canonical plugin is the default and speaks protocol-2 — no override needed for v2. |
| P5 | Python environments | Canonical brain has `.venv` (`~/Code/personal/agent-tts/hosts/herdr/brain/.venv`); this worktree's brain does NOT. Host venv `~/.local/share/herdr-tts/venv` exists. |
| P6 | Pending-queue operator CLI | `hosts/herdr/tts-plugin/lib/pending_queue.py` subcommands `list/status/retry/resolve/regenerate` present in this worktree. |
| P7 | Fallback config (optional) | `~/.config/agent-tts/fallback.json` (`engine/src/agent_tts/fallback.py:114`), OFF by default; without it behavior is identical to today. |
| P8 | Phone URL | Use the connection you already use for the PWA (same host placeholder as `MANUAL-TESTS.md`; served over your usual Tailscale HTTPS or LAN address, port 8741 unless configured otherwise). Do not scan or discover addresses. |

## Session launch (for the session day — pick ONE)

- **Option A (simplest):** run the brain from the canonical checkout
  (`~/Code/personal/agent-tts/hosts/herdr/brain`). It contains the merged
  voice-stack and defaults to the canonical v2 plugin (P4). Set
  `HERDR_BRAIN_HOST=0.0.0.0` (or your usual binding) + `GLM_API_KEY`.
- **Option B (this roadmap worktree):** export
  `HERDR_TTS_HOME=<worktree>/hosts/herdr/tts-plugin` and run the worktree
  brain with the canonical venv and `PYTHONPATH` pointing at the worktree
  `src/` (this worktree has no brain `.venv`, P5). Only needed if the
  session must exercise worktree bytes specifically.

## Checklist — PREPARED vs PHYSICALLY TESTED

Everything below is **prepared** (steps + expected observations written).
Nothing is physically tested yet: each row is unchecked until observed on the
phone by the owner. Record one row per case in the Record template.

### A. Voice-stack physical layer (EXECUTION §8)

| # | Case | Steps | Expected observation | Status |
|---|---|---|---|---|
| A1 | Headset/Bluetooth cancellation | Connect a Bluetooth headset; start a long answer; press Detener mid-answer | THAT voice stops instantly; other phone audio (e.g. music) is NOT cut | ☐ prepared |
| A2 | Real acoustics PC + phone | With a PC-side answer playing, make a watched agent finish (announcement) | Announcement waits (pending queue), then plays in order — no overlap, no duplicates | ☐ prepared |
| A3 | Real-provider fallback | Only if the owner configures `fallback.json` (P7) with real providers | Audible degradation to the secondary provider without replaying already-heard audio | ☐ BLOCKED (config-dependent) |
| A4 | First-segment latency, real network | Ask a long question over mobile network (not LAN) | Subjective: first segment audible in seconds, not after full render | ☐ prepared |
| A5 | Voice stop ≠ agent cancel | Approve an agent action; then use voice stop on a playing answer | The approved action is NOT cancelled; LLM/approvals keep running | ☐ prepared |

### B. `/approval` PWA re-smoke on Chrome Android (approval T8 remaining)

Defect rounds 1–5 and the floating-popup change fixed real-device defects;
the re-smoke is still owed (`action-approval-gate.md` Next Step).

| # | Case | Steps | Expected observation | Status |
|---|---|---|---|---|
| B1 | Gate opens — voice mode | During a voice call, dictate work that triggers `send_to_session` | Floating approval popup appears (also with drawer closed), full prompt text shown, countdown running | ☐ prepared |
| B2 | Gate opens — text mode | Same via typed input | Gate surfaces in text mode too (floating popup + "Confirmar ▲" pill) | ☐ prepared |
| B3 | Voice approve | Say "sí"/approve | Exact frozen args execute once; agent receives the prompt | ☐ prepared |
| B4 | Voice reject | Say "no" | Nothing is sent; reprompt/expiry path behaves | ☐ prepared |
| B5 | Voice re-dictate | Replace the text by voice | New text replaces the proposal; timer restarts | ☐ prepared |
| B6 | Manual edit | Edit the text field and save | PATCH persists; timer restarts; approve sends the edited text | ☐ prepared |
| B7 | Expiry | Let the gate time out (60 s default) | Silent auto-reject; expiry turn visible in the drawer | ☐ prepared |
| B8 | Reload recovery | Reload the page mid-gate | Boot `GET /approval/current` restores the live gate | ☐ prepared |
| B9 | Long agent task report | Approve a task that outlives the reply timeout | Report says delivered + agent still working; NO retry offer | ☐ prepared |
| B10 | Server-engine (v2) mic path | Switch phone STT to the server engine; hold a call | Mic keeps working; no corrupt captures ("Invalid data found" 503 gone) | ☐ prepared |

### C. `announcements-without-call` T5 (AC1–AC6, Chrome Android)

From brain `odd/tasks/announcements-without-call.md`; record whether
ASSUMPTION-1 (home-screen autoplay exception) holds.

| # | Case | Steps | Expected observation | Status |
|---|---|---|---|---|
| C1 | Home-screen PWA, no call/touch | Install/open PWA from home screen; finish an agent task | Immediate voice + toast; record ASSUMPTION-1 result | ☐ prepared |
| C2 | Regular tab, no prior gesture | Trigger an announcement in a plain tab | Persistent toast + "🔊 Activar voz"; after tap, the NEXT announcement sounds (no replay of the old one) | ☐ prepared |
| C3 | Forced rejection | Force `play()` rejection / media error | Announcement text stays visible; activation control available | ☐ prepared |
| C4 | Mute | Mute, trigger an announcement | Toast only — no voice, no activation control | ☐ prepared |
| C5 | In-call regression | Trigger an announcement during a call | Existing playback, teleprompter, mute and mic-resume unchanged | ☐ prepared |
| C6 | Reload single SSE | Reload; start a call | Exactly one `/events` connection per page load | ☐ prepared |

## Record template (fill per case; keep in this file or copy to the archive)

| Date | Revision (git sha of served code) | Device | Browser | Case | Result (pass/fail/blocked) | Notes / evidence pointer |
|---|---|---|---|---|---|---|
| | | | | | | |

- `blocked` must name the missing prerequisite (e.g. A3 without
  `fallback.json`).
- Per ROADMAP F2, local run evidence lives in
  `~/.local/state/voice-stack-runs/`; if the session produces a run-dir,
  copy only the summary into the repo under `docs/voice-stack/archives/`
  (separate authorized write, not this task).

## Prepared / physically tested summary

- Prepared: A1–A5, B1–B10, C1–C6 (this document, 2026-10-07).
- Physically tested: **none**. No phone was available; no claim is made.
- Blocked at prepare time: A3 (needs owner-configured `fallback.json` with
  real providers).

## Next step (shortest)

Owner, from the phone: open the PWA the way you normally do, start one long
`/ask` answer, press Detener mid-answer → record A1 (headset on/off) and A5
(approval alive) in the table above.
