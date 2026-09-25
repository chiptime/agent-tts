# Feature: Transcription Duplication Fix (herdr-brain)

**Repo**: `~/Code/personal/herdr-brain`
**Created**: 2026-09-25
**Status**: in progress

## Objective

A spoken utterance never appears twice in the call transcript — not as
doubled text inside one bubble, and not as two identical bubbles.

## Problem / Why

User reports user messages duplicated when transcribed (2026-09-25).
Journal evidence: ~30 POST /ask on Sep 25, ZERO POST /transcribe → the
phone runs the BROWSER SpeechRecognition engine. Chrome restarts
recognition sessions mid-utterance and re-emits the whole utterance as a
growing final. `mergeOverlap` (endpointing.js:70-89) dedupes only when
the normalized suffix==prefix match holds; `normWord` (:59-61) folds
case/punctuation but NOT diacritics, so Spanish drift (`qué` vs `que`,
`sí` vs `si`) defeats the match at k>=1 and the full re-emission is
appended → doubled text inside one bubble. Secondary vector: after
finalize()+dispatch the endpointer resets; Chrome re-emits the final
utterance in the fresh session, commit() stores it on empty committed,
silence elapses → second dispatch → duplicate bubble. Today's journal
shows no 2-5s /ask bursts, but the hole is real and cheap to close.

## Scope / Constraints

- Pure logic lives in `endpointing.js` (testable via node --test);
  app.js untouched unless a hole has no pure seam.
- Raw transcribed text is NEVER rewritten for display — folding is for
  MATCHING only.
- Over-merge tradeoff documented (same as existing mergeOverlap header):
  a genuine whole-utterance repeat within the dedupe window is collapsed.
- TDD mode: OFF (strict) — repo convention is tests alongside code in
  the same work-unit commit.
- Delivery: work-unit commits straight to local `master`, never push
  (USER DECISION 2026-09-24, cached from action-approval-gate).

## Delivery Forecast

- Estimated authored lines: ~120 (tests included). Single PR-sized,
  no chain needed.

## Tasks

### [x] T1 — Diacritic-insensitive overlap merge
Files: `src/herdr_brain/static/endpointing.js` (normWord),
`tests/js/endpointing.test.js`.
normWord folds Unicode diacritics (NFD + strip combining marks) before
compare, so `qué`==`que`, `sí`==`si` in overlap matching. Raw text in
committed stays untouched. Tests: growing-final re-emission with accent
drift merges to single copy; genuine distinct words not over-merged.
Route: delegated-direct (writer).
Checks: `node --test tests/js/` full suite green. Commit: `fix(voice)`.

### [x] T2 — Post-dispatch re-emission dedupe
Files: `src/herdr_brain/static/endpointing.js` (finalize/commit + now),
`tests/js/endpointing.test.js`.
finalize() remembers the finalized text + timestamp; commit() IGNORES a
final whose folded word sequence equals the last finalized text when it
arrives within DUPLICATE_WINDOW_MS (default 5000, injectable). Closes
the identical-duplicate-bubble vector. Genuine new speech unaffected.
Tests: re-emitted final within window ignored; same text after window
kept; different text within window kept.
Route: delegated-direct (writer).
Checks: `node --test tests/js/` full suite green. Commit: `fix(voice)`.

## Acceptance Criteria

- Growing-final re-emission with Spanish accent drift yields ONE copy.
- Chrome late re-emission after dispatch does not create a second bubble.
- Full js suite (109+ tests) passes.

## Progress / Evidence

- 2026-09-25 T1 commit `8900bb9` (endpointing.js + tests, +66/-8).
- 2026-09-25 T2 commit `45da1e3` (+91/-4); window 5000 ms, memory
  survives reset, in-window match ignores with zero mutation.
- Render deviation accepted: accent-drift keeps the committed word,
  case/punct-drift keeps the final's word (hybrid preserves the
  pre-existing case/punct rendering test). Suite 117/117 (was 109).
- Status: COMPLETE. Both duplication vectors closed at the pure-logic
  seam; app.js untouched.

