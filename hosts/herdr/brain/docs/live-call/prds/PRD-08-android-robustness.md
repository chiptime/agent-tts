# PRD-08: Android Robustness — Screen Off, Background, Bluetooth, Audio Focus, Wake Lock

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap — cross-cutting hardening) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-00 (engine choice landed); hardens whatever transport is current (segment POST today; WebSocket after PRD-03 — dotted edge in ROADMAP §5) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-08-android-robustness.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

The call's primary device is a Pixel 7 Pro running the PWA, and several Android behaviors sit outside what the code controls or the harness can simulate:

- **Audio routing**: the code already documents that Android Chrome's `SpeechRecognition` ignores Bluetooth headset mics while `getUserMedia` honors them — a reason Servidor is the Android default (`app.js:186-197`, `app.js:5225`).
- **Audio focus / silent kills**: a mic watchdog exists because Android can kill `SpeechRecognition` (audio focus, silent crash) without a usable `onend` (`app.js:4625-4629`).
- **Screen off / backgrounding**: PWA audio capture and timers are throttled or suspended when backgrounded or screen-off (ASSUMPTION about exact behavior on current Chrome — verified, not assumed, in this PRD's first task); no wake lock is requested today.
- **Test harness boundary**: the deterministic harness proves logic with Chromium fake audio (`tests/browser/conftest.py:496-517`); none of these OS-level behaviors are observable in it. This PRD's evidence is therefore heavily V3 (device) plus V1 for the policy logic.

## 2. Objectives & Non-Objectives

### Objectives
- A verified behavior matrix for screen-off, background, Bluetooth routing, and audio focus on the target device.
- A call that survives screen-off for a documented duration via wake lock (if the platform permits).
- Graceful degradation with explicit user-visible state, never silent call death.
- Bluetooth headset support documented and verified for Servidor mode.

### Non-Objectives
- iOS.
- A native Android app or foreground service.
- Fixing Web Speech's Bluetooth limitation (browser behavior; documented, not fixed).

## 3. Functional Requirements

- **FR-01**: The call MUST acquire a screen wake lock (`navigator.wakeLock`, ASSUMPTION: available in installed PWA on current Chrome) while a call is active, and re-acquire it after visibility changes when the call is still live. If wake lock is unavailable, the limitation MUST be surfaced (settings note), not hidden.
- **FR-02**: The behavior of audio capture (getUserMedia stream), the VAD, and (post-PRD-03) the WebSocket under screen-off and backgrounding MUST be measured on the target device and recorded in the behavior matrix (§4); whatever dies MUST trigger the existing recovery paths (mic watchdog `app.js:4625-4629`; PRD-03 reconnect) rather than a silent hang.
- **FR-03**: Bluetooth headset routing MUST be verified for Servidor mode: capture from the headset mic, playback to the headset, and a documented note for Navegador's limitation (`app.js:187`).
- **FR-04**: Audio focus interplay MUST be characterized: what happens to an active call when another app takes audio (navigation prompt, phone ring, music). Playback SHOULD duck or pause per platform behavior, and capture MUST recover after the interruption ends; results recorded in the matrix.
- **FR-05**: Every degradation (wake lock denied, BT mic lost mid-call, focus stolen) MUST be user-visible in the call UI (state pill/banner), with a defined recovery action where one exists.
- **FR-06**: The behavior matrix MUST be checked into the docs (this folder) and referenced by the ROADMAP, so later PRDs design against measured reality, not folklore.

## 4. Acceptance Criteria

1. Behavior matrix exists with measured results for: screen-off 10 min, backgrounded 5 min, BT connect/disconnect mid-call, audio-focus steal by navigation and by phone call — each with observed behavior and the recovery that fired.
2. With wake lock granted, a 10-minute screen-off call keeps transcribing (or the matrix documents exactly when it cannot, with the user-visible state).
3. BT headset: a full call (speak, hear answer) without touching the phone, in Servidor mode.
4. After a phone call steals audio focus mid-brain-call, the brain call recovers to a listening state, not a zombie.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (node)**: the policy logic that is testable — wake-lock re-acquisition state machine (visibility events scripted), degradation-to-banner mapping — as pure module tests in `tests/js/`.
- **V2 — integration**: limited by design: the browser harness can prove the banner/state logic and that call state survives synthetic `visibilitychange` events; it CANNOT prove OS audio behavior. This limitation is stated rather than papered over.
- **V3 — manual on Pixel 7 Pro**: the entire §4 checklist is the core verification; each row signed with date and Chrome version.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | ✔ | ✔ (state logic) | ✔ | Wake lock + re-acquire |
| FR-02 | — | — | ✔ | Screen-off/background matrix |
| FR-03 | — | — | ✔ | BT routing, Servidor mode |
| FR-04 | — | — | ✔ | Audio focus steal + recovery |
| FR-05 | ✔ | ✔ | ✔ | Visible degradation states |
| FR-06 | — | — | ✔ | Matrix checked in |

## 6. Risks

- **Chrome may suspend capture entirely when screen-off** in a PWA (no foreground service): if measured so, the honest outcome is a documented limit ("keep screen on / lock later"), not a workaround hack; FR-01's note exists for this.
- **OEM variance** beyond the Pixel 7 Pro: out of scope; matrix records the tested device and Chrome version.
- **Wake lock and Play-store-less PWA policy drift**: re-verify per Chrome major version; matrix carries the version.

## 7. Open Questions

1. Does current Android Chrome keep a `getUserMedia` stream alive for an installed PWA with screen off + wake lock? (First measured task; several FRs depend on the answer.)
2. Is `speechSynthesis` (UI reading, `tts-engine.js:22`) affected by the same focus/background rules? (Matrix row; affects reading UX, not the call.)

## 8. Execution Boundaries

Hardening and measurement only (FR-01..FR-06); no transport redesign, no new features. Out-of-scope discoveries go to `## Escalated / Out-of-Scope Issues`. This PRD is a `draft`; launching implementation is a separate explicit decision.
