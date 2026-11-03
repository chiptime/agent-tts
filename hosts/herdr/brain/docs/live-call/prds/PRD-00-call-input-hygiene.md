# PRD-00: Call Input Hygiene — engine choice, settings split, no idle beep loop

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap Phase 0 — short-term) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | none (partly in progress in the working tree) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-00-call-input-hygiene.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

The call has two STT engines. "Servidor" (getUserMedia + `vad.js` energy VAD + 45 s webm/opus segments POSTed to `/transcribe`) is the default and the only option on Android (`app.js:186-197`). "Navegador" (Web Speech `SpeechRecognition`, `endpointing.js`) is the fallback. Today:

1. **Android cannot choose.** `initSpeech()` forces `voiceEngine = "servidor"` on Android and the toggle refuses to switch (`app.js:5817-5818`, `app.js:5860-5869`), because Android Chrome's Web Speech plays a system earcon on every recognizer start and ends sessions on short pauses — the restart loop "beeps nonstop" (code comment `app.js:192-197`). User report on the Pixel 7 Pro PWA: "comienza a sonar el permiso de micro sin parar" and "me corta mientras hablo, haciendo que tenga que hacer mas de un turno".
2. **Engine choice and UI reading are conflated in the settings surface.** The call engine is an in-call button (`app.js:137`, `app.js:5806`); UI reading is a separate module (`tts-engine.js:22`, browser `speechSynthesis` karaoke). The user wants both exposed as separate settings rows because "son cosas diferentes".
3. **The beep is only acceptable at turn boundaries.** User acceptance: a beep is acceptable only "si se detecta que ha parado la transcripcion pero nada mas" — i.e. when transcription actually stops/starts a turn, never in a loop while idle.
4. **Continuation window (in progress, uncommitted).** A 1.5 s continuation window after a silence close, so resumed speech joins the same turn, is implemented in the working tree (`app.js:224-230`, `app.js:5416-5551`) but not committed or covered by dedicated tests.

A web page cannot silence the Android Web Speech earcon; the only lever is to start the recognizer less often.

## 2. Objectives & Non-Objectives

### Objectives
- Allow the call STT engine choice (Servidor/Navegador) on every platform, persisted.
- Expose "call transcription" and "UI reading" as separate, independent settings rows.
- Stop the idle beep loop on Android: no repeated earcon while idle; earcon only when a turn's transcription actually stops or starts.
- Reduce mid-sentence turn cuts in Navegador on Android (fewer recognizer restarts).
- Land the continuation window with tests.

### Non-Objectives
- Streaming STT or semantic end of turn (PRD-04, PRD-05).
- Replacing the Servidor engine or changing `/transcribe`.
- Changing the UI reading engine itself (`tts-engine.js` behavior stays).
- iOS.

## 3. Functional Requirements

- **FR-01**: The settings view MUST expose two independent rows: "call transcription engine" (Servidor / Navegador) and "UI reading" (existing `tts-engine.js` options). Changing one MUST NOT change the other. (Source: user decision "son cosas diferentes"; today the call engine is only an in-call toggle, `app.js:5806-5830`, and reading is separate code, `tts-engine.js:22`.)
- **FR-02**: The call engine choice MUST be allowed and persist on every platform, including Android. The Android force-to-Servidor path (`app.js:5860-5869`) and the toggle refusal (`app.js:5817-5818`) MUST be removed; persistence continues through the existing `VOICE_KEY` localStorage entry (`app.js:191`). (Source: user request "engine toggle allowed on every platform".)
- **FR-03**: In Navegador on Android, idle listening MUST NOT cause repeated recognizer restarts; the system earcon MUST be audible at most when a turn's transcription stops or starts — "si se detecta que ha parado la transcripcion pero nada mas". (Source: user acceptance quote; system earcon documented at `app.js:192-197`.)
  - **Candidate approach (ASSUMPTION, to validate on device first)**: keep a silent `getUserMedia` stream open with the existing energy VAD (`vad.js`) as a gate while idle, and start `SpeechRecognition` only on speech onset. Risks: clipped first word (needs a pre-roll buffer or slight onset margin), and mic contention between `getUserMedia` and `SpeechRecognition` on Android.
- **FR-04**: When the gated recognizer stops at end of turn, the client MUST be able to restart it on the next speech onset without losing the turn boundary semantics already provided by `endpointing.js` (the endpointer already survives recognition restarts, `app.js:5109` — this behavior MUST be preserved).
- **FR-05**: The 1.5 s continuation window (silence close followed by resumed speech joining the same turn, `app.js:224-230`, `app.js:5416-5551`) MUST land committed, with tests covering: speech inside the window joins the turn; speech after the window starts a new turn; the window does not extend past the 10-minute utterance cap (`app.js:223`).
- **FR-06**: The Navegador mid-sentence cut ("me corta mientras hablo, haciendo que tenga que hacer mas de un turno") SHOULD be reduced as a side effect of FR-03: a recognizer started on speech onset and kept for the utterance should not end sessions at short pauses as the current always-restarting loop does. (ASSUMPTION: severity reduction, not elimination — Chrome may still end sessions on its own; the mic watchdog at `app.js:4625-4629` MUST keep covering that case.)
- **FR-07**: The debug panel's engine indicator ("motor de voz", `app.js:3843`) MUST keep reflecting the active engine.

## 4. Acceptance Criteria

1. On the Pixel 7 Pro PWA, the settings show two separate rows (call transcription / UI reading); switching the reading voice changes nothing about call STT and vice versa.
2. On the Pixel 7 Pro, Navegador can be selected and persists across app reloads.
3. In Navegador on the Pixel 7 Pro: 30 s of idle silence produces no repeated earcon; a beep at turn start/stop is acceptable per the user's stated tolerance.
4. A long utterance with a short mid-sentence pause stays one turn (continuation window).
5. All existing call-turn browser tests still pass.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (node)**: settings wiring (two rows, independent persistence) as a pure module test in `tests/js/`; continuation-window cases as time-based tests against the existing turn-state module (pattern of `tests/js/vad.test.js`, `tests/js/endpointing.test.js`).
- **V2 — integration (pytest + Playwright fake audio)**: engine switch and persistence via the browser harness (`tests/browser/conftest.py:496-517` fake capture); continuation window through the existing call-turn suite (`tests/browser/test_call_turns.py`); restart-count assertion: while idle, the number of `SpeechRecognition.start()` calls observed in the page stays flat. The earcon itself is a system sound and CANNOT be observed in Chromium — V2 proves the restart behavior, not the sound.
- **V3 — manual on Pixel 7 Pro**: idle 30 s listening for earcons; speak with pauses; verify turn joining and the user's acceptance threshold ("si se detecta que ha parado la transcripcion pero nada mas"). Signed checklist before the PRD closes.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | ✔ | ✔ | ✔ | Independent settings rows |
| FR-02 | — | ✔ | ✔ | Engine choice persists on Android |
| FR-03 | — | ✔ (restart count) | ✔ (earcon) | No idle beep loop |
| FR-04 | ✔ | ✔ | ✔ | Endpointing survives gated restarts |
| FR-05 | ✔ | ✔ | ✔ | Continuation window joins turns |
| FR-06 | — | — | ✔ | Mid-sentence pauses stay one turn |
| FR-07 | — | ✔ | — | Debug panel engine indicator |

## 6. Risks

- **Mic contention on Android** (getUserMedia + SpeechRecognition simultaneously): unvalidated; if the device refuses, FR-03's candidate approach fails and Navegador stays desktop-only — the fallback is documented degradation, not a hack.
- **Clipped first word** when STT starts on onset: mitigate with a small onset margin or pre-roll; acceptance listens for it explicitly.
- **OEM/browser variance**: behavior verified on Pixel 7 Pro Chrome only; other Androids are out of scope (noted, not chased).

## 7. Open Questions

1. On the Pixel 7 Pro, does Android Chrome allow a live `getUserMedia` stream and `SpeechRecognition` at the same time? (Blocks FR-03's approach; validate before implementation.)
2. Does the earcon fire on `start()` even when the recognizer is started on speech onset only — i.e. is the per-turn beep tolerable in practice?
3. Should the settings rows live in the existing settings sheet or a new "call" section? (UI placement, low risk.)

## 8. Execution Boundaries

Implementation is limited to FR-01..FR-07. Blocking issues found outside this scope (e.g. Web Speech bugs deeper than the restart loop) are documented under an `## Escalated / Out-of-Scope Issues` heading in the executing task, not fixed unilaterally. This PRD is a `draft`; launching implementation is a separate explicit decision.
