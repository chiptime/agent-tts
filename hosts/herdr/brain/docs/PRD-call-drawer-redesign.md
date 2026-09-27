# PRD — Cockpit-First Layout + Call Drawer

| | |
|---|---|
| **Product** | herdr-brain (voice-assistant PWA) |
| **Status** | Approved for implementation (design validated via interactive mockup) |
| **Date** | 2026-09-23 |
| **Effort** | M (~600–800 changed lines, 2 files, no server changes) |
| **Mockup** | `docs/mockups/call-drawer-v2.html` (open in browser; "Actual vs Propuesta v2" switch) |

---

## 1. Background & Problem

The current UI puts two different transcripts in one column and gives the voice conversation whatever space is left:

1. **Voice chat is squeezed to ~2 visible lines.** `#conversation` scrolls fine, but header (~56px) + herd strip + the unbounded agent glance panel (3 turns × 300 chars + 132px terminal preview) + 150px footer padding consume a ~640px viewport (`index.html` CSS 40–48, 114–195; `app.js` `renderViewTail`).
2. **The top "chat" cannot be read properly.** The agent glance panel has no `max-height`/`overflow` (grows unbounded) while turn texts are truncated **server-side at 300 chars** (`view.py:18`) and the terminal preview is hard-clipped (`max-height:132px; overflow:hidden`). The only escape is a full-screen modal sheet (z-20) that covers the footer (z-9), state pill (z-8) and toasts (z-12) — with it open the user **cannot hang up, pause, barge-in, or see announcements**, and the Android back button exits the PWA (no `popstate` handler).
3. **No sense of "where am I" during a call.** With the default `servidor` engine there is no interim transcript at all (`app.js:131` hides `#interim`; batch STT produces none), the glance panel and the live chat render with near-identical styling, and nothing re-layouts when the call starts.

Full audit evidence: §Appendix A.

## 2. Goals

- **G1** — Make the agent cockpit (herd + agent transcript + terminal) the primary, readable base screen: real scrolling, full-text expansion, no dead-end modal.
- **G2** — Move the voice call into a bottom drawer that rotates over the cockpit: auto-opens on call start, closes without ending the call, re-opens from the state pill.
- **G3** — Make call state permanently legible hands-free: pending banner and toasts never hidden by the drawer; pill/footer always reachable.
- **G4** — Fix the structural defects the redesign touches: poll re-renders destroying interaction state, toast z-order, Android back button, safe-area math, touch targets.

**Non-goals:** new voice features, server/API changes, multi-call/multi-agent simultaneous conversations, tablets/desktop layouts, swipe-to-close gesture (backlog), haptics (backlog).

## 3. Users & Context

Phone held or lying on the kitchen counter; wet/gloved hands; one-handed use; screen at arm's length; Spanish UI (existing EN/ES mix). Consequences: touch targets ≥ 48px, high contrast, zero invisible state, no state-dependent tap semantics (a wet thumb cannot read the pill before tapping).

## 4. Approved Design (v2)

```
┌──────────────────────────────┐
│ Header (status · title · 🔊 ↺)│
│ ⚠ Pending banner  (always)   │   ← never covered by drawer on ≥600px portrait
│ Herd strip (chips)           │
│ AGENT COCKPIT (primary)      │   ← turn list w/ scroll + "ver más" expansion
│   · transcript turns         │     terminal preview w/ real scroll
│   · terminal screen          │
│ [pill: call state ▲]         │   ← only while calling & drawer closed
├──────────────────────────────┤
│ Footer  ⏸  📞/🔴  ⏹          │   ← always visible & tappable
└──────────────────────────────┘
   Drawer (the CALL) slides over
   cockpit, anchored ABOVE footer
   · live dot + timer + [engine] + ▼
   · interim strip (engine-dependent)
   · user ↔ brain bubbles
```

The existing full-screen sheet (`#sheet`, "Conversación"/"Pantalla" tabs) is **removed** — the cockpit absorbs both functions.

## 5. Functional Requirements

### 5.1 Base screen — cockpit

- **FR1.** Glance panel turn list: `overflow-y: auto` inside a bounded flex area; renders the **agent's session turns from the existing `GET /conversation` endpoint** (20 turns, 4000-char clip) instead of the 3-turn `/view` tail. `/view` continues to feed status chip, pending banner and terminal preview.
- **FR2.** Turns longer than ~4 lines render clamped with a full-width **"ver más" `<button aria-expanded>`** row (never an inline span). Expansion fetches/uses the already-delivered 4000-char text and expands inline; "ver menos" collapses. Expansion + panel scroll position **survive the 5s poll** (see FR10).
- **FR3.** Terminal preview: real scroll (~110px), fade mask on top edge. No hard clip.
- **FR4.** Pending banner: keeps the three current kinds (`question`/`error`/`permission`, distinct colors). When more than one agent is pending, banner aggregates ("2 agentes esperan tu OK"); tapping selects that agent. While the drawer is open on short screens (see FR13), a compact pending strip renders inside the drawer header area.
- **FR5.** The full-screen sheet, its tabs, `openSheet`/`loadSheetConversation`/`renderSheetConversation` and CSS are deleted. Vestigial `lastViewJson` cache is deleted.

### 5.2 Call drawer

- **FR6.** Drawer: fixed, anchored at `bottom: calc(env(safe-area-inset-bottom, 0px) + 96px)`; height `min(58%, calc(100dvh − 260px))` with a sane `min-height`; slide-up transition ≤300ms, gated by `prefers-reduced-motion`. `role="dialog"`, `aria-modal="false"`, `aria-label="Llamada con brain"`.
- **FR7.** Drawer header: live dot + "Llamada con brain" + call timer (`aria-hidden`) + **voice-engine toggle** (moved here — it is a call property; mid-call switch keeps working) + close button ▼ (≥44px). The version chip stays in the main header.
- **FR8.** Interim strip lives at the drawer top and is **engine-dependent**: `navegador` → live interim text as today; `servidor` → recording indicator + live input-level meter (from the existing VAD RMS sampling) and elapsed utterance time — **never** simulated text. Finalized utterances always appear as user bubbles.
- **FR9.** Empty first call: ghost bubble "Di algo — te escucho" until the first turn; interim strip visible so liveness is obvious.
- **FR10 (prerequisite).** Replace wipe-and-rebuild rendering of herd strip and glance panel with **keyed in-place updates** (reuse DOM nodes per turn/chip id). The 5s poll must never: swallow taps (AC8), reset glance scroll, or collapse an expanded turn (AC7). Delete-on-change only when content actually changed.
- **FR11.** The current text-fallback keyboard form stays in the **footer**; entering text mode (mic dead / user choice) **force-closes the drawer**. Drawer and soft keyboard never coexist; verified under `visualViewport` resize.

### 5.3 State pill & footer

- **FR12.** Pill exists **only while calling and drawer closed** ("● Grabando — habla ▲" / "⏳ Entendiendo… ▲" / "🔊 Hablando ▲"). Tap = **reopen drawer**, always — never state-dependent. Barge-in remains the footer ⏹ (labelled, ≥48px). Diagnostics long-press moves to **idle only** (≥800ms) and never fires during a call. Hang-up closes the drawer, releases the mic, pill disappears (idle is pill-free, matching today). Call button uses **distinct glyph+label** ("📞 Llamar" / "🔴 Colgar" text labels as today).

### 5.4 Overlays & robustness

- **FR13.** Z-order: toast (announcements + errors) above drawer, above sheet remnants, above footer (`z: toast 25 > diag 30*` exception: diagnostics stays top). During a call, an SSE announcement: pauses mic (existing behavior), shows toast above drawer, and appends a **visually distinct "🔊 anuncio" bubble** to the drawer conversation; interim strip freezes while announcing.
- **FR14.** Errors (`/ask`, `/transcribe`, mic denial) surface via toast/banner above the drawer; mic-denied additionally shows the remediation hint and switches to text mode (AC11). No phantom "Grabando" pill without a live mic.
- **FR15.** Reload mid-call: `sessionStorage` persists call intent; boot renders pill "Llamada cortada — ¿Volver a llamar?" and **never auto-resumes the mic** (AC13).
- **FR16.** Android back: `history.pushState` on drawer open; `popstate` closes the drawer. Back never exits the PWA while the drawer is open.
- **FR17.** Wake Lock: request `navigator.wakeLock('screen')` on call start, release on end; silent-fail when unsupported (kitchen-counter screen dimming kills the hands-free flow).

## 6. UI State Machine

| # | State | Pill | Drawer | Notes |
|---|---|---|---|---|
| 1 | `idle` | hidden | closed | post-boot, post-hangup |
| 2–7 | `calling + drawer-open` × {listening, recording, transcribing, thinking, speaking, paused} | hidden | open | recording/transcribing = servidor only |
| 8–13 | `calling + drawer-closed` × same 6 | state text + "▲" | closed | pill tap reopens |
| 14 | `textMode` | hidden | **forced closed** | keyboard in footer |
| 15 | announcement overlay (any calling) | "🔊 Hablando" | either | distinct bubble appended |
| 16 | error (mic denied / API fail) | per FR14 | either | toast/banner above drawer |
| 17 | `call-interrupted` (reload) | "Llamada cortada…" | closed | tap = new call |

Transitions that must be explicit in code: speaking→listening via ⏹; any→textMode closes drawer; engine switch mid-call restarts listening (drawer stays); announcement during drawer-open; reload from any calling state.

## 7. Acceptance Criteria

1. **AC1 — Start call:** Given cockpit at idle, when I tap 📞, the drawer slides up ≤300ms, timer starts at 00:00, bubbles auto-scroll to bottom, button becomes "🔴 Colgar".
2. **AC2 — Close ≠ hang up:** Given calling + drawer open, when I tap ▼, the drawer closes, the mic keeps recording, the pill shows state + "▲", and reopening shows the timer still counting.
3. **AC3 — Pill re-entry:** Given calling + drawer closed, when I tap the pill, the drawer reopens without audio glitch, scrolled to the newest turn.
4. **AC4 — Hang up:** Given calling, when I tap hang-up, drawer closes, Android mic notification disappears, pill disappears, timer stops.
5. **AC5 — Barge-in:** Given "🔊 Hablando", when I tap ⏹, TTS stops ≤300ms and pill returns to listening within 1s.
6. **AC6 — Announcement mid-call:** Given calling (drawer either way), when an announcement arrives, mic pauses, toast appears above drawer, a distinct anuncio bubble is appended, and the pill returns to listening after playback.
7. **AC7 — Expansion survives poll:** Given an expanded glance turn, when the 5s poll fires with changed content, the turn stays expanded and scroll position is unchanged.
8. **AC8 — Poll doesn't swallow taps:** Given the 5s poll, 10 consecutive taps on herd chips all register.
9. **AC9 — Servidor engine:** Given engine=Servidor + drawer open, when I speak, the strip shows the recording meter (never fake text), and my utterance appears as a user bubble after transcription.
10. **AC10 — Navegador interims:** Given engine=Navegador + drawer open, the strip live-updates word-by-word and clears on dispatch.
11. **AC11 — Mic denied:** when the browser blocks the mic, banner explains remediation, keyboard fallback appears in footer, drawer stays closed, no phantom pill.
12. **AC12 — Pending reachable:** Given a pending agent + drawer open on ≥600px portrait, the pending banner is fully visible; on shorter/landscape screens the in-drawer pending strip is visible.
13. **AC13 — Reload mid-call:** after reload the app boots idle with "Llamada cortada — ¿Volver a llamar?" and never resumes the mic without a tap.
14. **AC14 — Back button:** Given drawer open, Android back closes the drawer (not the PWA); second back at idle exits normally.

## 8. Non-Functional Requirements

- **Touch targets** ≥48×48px for all interactive elements (header buttons, herd chips, ▼, pill, "ver más", engine toggle). Current 34–36px controls are regressed-to-fix, not copied.
- **Safe areas:** every bottom-anchored offset (drawer, pill, footer clearance) uses `calc(env(safe-area-inset-bottom, 0px) + Npx)`. Verified with inset ≥28px.
- **Contrast:** call-button green darkened to ≥3:1 for the glyph (≈#1f9e56 or darker); user-bubble text ≥4.5:1.
- **Reduced motion:** pill pulse, interim caret and drawer slide gated by `prefers-reduced-motion`.
- **ARIA:** finalized turns announced politely (`aria-live="polite"`); interim strip `aria-live="off"`; pill `role="status"`; timer `aria-hidden`; drawer dialog semantics with focus moved to ▼ on open and restored to pill on close; "ver más" is a `<button aria-expanded>`.
- **Performance:** keyed DOM updates (FR10); no full-subtree wipes on poll; drawer animation is transform-only.

## 9. Technical Notes

- **Files:** `src/herdr_brain/static/index.html` (DOM + inline CSS, ~520L) and `app.js` (~1308L). **No server changes** — `/conversation`, `/view`, `/events`, `/ask`, `/transcribe` are reused as-is.
- Deleted: `#sheet` + tabs + sheet CSS + `openSheet`/`loadSheetConversation`/`renderSheetConversation`, `lastViewJson`, `#interim` on the base screen (moves into drawer).
- Engine states, VAD, endpointing, SSE handling, audio queue: **unchanged** — only their presentation moves.

## 10. Scope Decisions

| Item | Decision | Rationale |
|---|---|---|
| Wake Lock | **In scope** | Counter phone dims = invisible pill; ~15 lines, silent-fail |
| Keyed DOM updates | **In scope** | Prerequisite: poll destroys scroll/expansion/taps (AC7/AC8) |
| Android back (popstate) | **In scope** | Back must not exit the PWA mid-call; ~10 lines |
| Toast z-index above drawer | **In scope** | Announcements-during-drawer becomes the norm |
| Cockpit tap-to-expand | **In scope** | It is the answer to the 300-char truncation; ships with AC7 |
| Sheet removal | **In scope** | Absorbed by cockpit; kills the modal trap class of bugs |
| Haptics | Backlog | Phone often not in hand at state change; tuning cost > value |
| Swipe-down-to-close | Backlog | Conflicts with drawer scroll; ▼ + back button suffice |
| Diagnostics panel redesign | Backlog | Only re-homed (idle-only long-press) in this change |

## 11. Rollout

Suggested work units (each independently verifiable):

1. **Cockpit:** FR1–FR5, FR10, AC7/AC8 (+ sheet removal).
2. **Call drawer:** FR6–FR9, FR12, AC1–AC5, AC9/AC10.
3. **Robustness:** FR11, FR13–FR17, AC6, AC11–AC14, safe-area/landscape.
4. **A11y & polish:** §8 items (contrast, reduced-motion, ARIA, wake lock if not landed in 3).

Estimated diff ~600–800 lines → likely above the 400-line review budget: plan either two chained PRs (units 1–2, then 3–4) or `size:exception` on a single PR.

## 12. Open Questions

- None blocking. (Resolved during design: idle pill hidden; engine toggle lives in drawer header; barge-in stays on ⏹ only; diagnostics long-press idle-only.)

## Appendix A — Audit evidence (current app)

| Finding | Evidence |
|---|---|
| No live transcript on default engine | `app.js:131` (interim hidden unless navegador); batch STT in `dispatchServerUtterance` |
| Interim capped 2 lines / 120 chars | `index.html:222–232`; `app.js:749–751, 780` |
| Glance unbounded / server 300-char clip | `index.html:172–175`; `view.py:18`, `tools.py:151–166` |
| Terminal hard clip | `index.html:176–187` |
| Sheet covers controls & toasts | z: sheet 20 > toast 12 > footer 9 > pill 8 (`index.html:288, 316, 365`) |
| No `popstate`; back exits PWA | zero matches in `app.js` |
| Poll wipe-and-rebuild swallows taps | `renderHerd` `app.js:290–306`; `renderViewTail` `app.js:348–365`; dead `lastViewJson` `app.js:394` |
| Mic not stopped during mid-utterance announcements | `app.js:443–446` |
| Sticky error banner (servidor) | `hideBanner` only in navegador paths `app.js:742, 1126, 1175` |
| Sub-48px targets | `.head-btn` ≈35px, replay 38px, engine pill 36px, sheet ✕ 35px |
