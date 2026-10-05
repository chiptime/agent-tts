# Feature: Transcription Duplication Fix (herdr-brain)

**Repo**: `agent-tts/hosts/herdr/brain`
**Created**: 2026-09-25
**Status**: reopened for residual interim-transcript duplication (2026-10-01)

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
- Current authorization (2026-10-01): fix overlapping provisional text and
  post-dispatch provisional echoes with regression tests. Background and
  screen-lock behavior are excluded. Historical delivery preference above
  is retained as history; this run uses branch
  `fix/brain-interim-transcript-duplication`, with no commit, push, or live
  deployment unless explicitly requested.
- Effective testing mode: strict TDD OFF per this existing feature document;
  regression tests and ordinary functional checks remain required. JavaScript
  runner: `node --test tests/js/` from `hosts/herdr/brain`.
- RDD: OFF, decided by clone-local mode; no review transaction is started.

## Delivery Forecast

- Estimated authored lines: ~120 (tests included). Single PR-sized,
  no chain needed.
- Residual correction forecast: approximately 90-120 authored lines,
  including tests and this tracker. Strategy: `ask-on-risk`; no remote
  delivery is authorized. The 400-line task heuristic is advisory only.

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

### [x] T3 — Merge overlapping interim text and reject post-dispatch echoes
Files: `src/herdr_brain/static/endpointing.js`,
`tests/js/endpointing.test.js`, this feature document.
Reuse the existing overlap and echo helpers at the provisional-text seam.
Cover both duplicate text inside one turn and an interim echo reviving an
already-dispatched turn. Preserve intentional repetition inside a single
transcript, genuinely different speech, and repetition after window expiry.
Do not add recognition lifecycle changes without evidence they are needed.
Route: delegated-direct; preparation and non-trivial source/test edits
require one bounded writer. Existing T1/T2 remain valid for final results.
Checks: focused regression tests, full JavaScript suite, available brain
Python suite, `git diff --check`. Real mobile validation remains pending.
Rollback boundary: the T3 endpointing/test changes; no deployment or agent
session writes. Commit evidence: pending explicit commit authorization.
Implemented (2026-10-01, corrected same day after scope review):
`combined()` now returns `mergeOverlap(committed, interim)` instead of
blind concatenation — same fold/accent/case/punct rules and repetition
guarantees as the finals path. `push()` rejects ONLY an interim whose
folded words EQUAL the dispatched signature within `duplicateWindowMs`,
reusing the existing `isReemittedFinal` guard (no prefix-wide
suppression: after "hola mundo" a genuine "hola" is accepted). Because
Chrome's echo arrives growing, such shorter prefixes may already be
buffered when the full echo identifies the utterance; on that full
match (via push OR commit) the new `dropEchoInterim` helper clears a
buffered interim that is a folded prefix of the identified echo, so
silence cannot dispatch the partial ghost; when the echo was the ONLY
buffered speech the ghost utterance timing (speechStartedAt/
lastChangeAt) is cleared with it so a later genuine phrase gets its
own silence/hard-cap clocks, while independent committed speech keeps
its timing untouched. Unrelated interim and committed text are
untouched; prefix matching is used ONLY for this post-identification
cleanup, never to gate new speech. app.js
untouched: it already ignores push/commit return values and reads
text()/finalize()/snapshot()/hasSpeech()/shouldFinalize(). Tradeoffs:
full within-window repeat suppression is the PRE-EXISTING accepted
tradeoff (T2); an interim EXTENDING the signature is kept whole, like a
longer final; while an echo is still growing the merged view can
transiently show its short tail before converging. Honest limitation: a
STANDALONE partial echo that never reaches the full signature is
accepted as speech (indistinguishable from a genuine shorter command;
fixing it would require the unapproved prefix suppression).

## Acceptance Criteria

- Growing-final re-emission with Spanish accent drift yields ONE copy.
- Chrome late re-emission after dispatch does not create a second bubble.
- Full js suite (109+ tests) passes.
- Committed prefix plus overlapping interim produces a single phrase in
  display, diagnostics, and finalization.
- A matching interim echo inside the existing 5-second window does not
  revive speech or become a second dispatch; unrelated speech and text
  after window expiry remain accepted.

## Progress / Evidence

- 2026-09-25 T1 commit `8900bb9` (endpointing.js + tests, +66/-8).
- 2026-09-25 T2 commit `45da1e3` (+91/-4); window 5000 ms, memory
  survives reset, in-window match ignores with zero mutation.
- Render deviation accepted: accent-drift keeps the committed word,
  case/punct-drift keeps the final's word (hybrid preserves the
  pre-existing case/punct rendering test). Suite 117/117 (was 109).
- Historical status (2026-09-25): COMPLETE for final-result duplication;
  app.js untouched. Reopened on 2026-10-01 for provisional-result paths.
- 2026-10-01: reproduced committed/interim overlap and post-dispatch
  interim echo with Node against the current module. Baseline mapper
  reports 203/203 JavaScript tests passing before regression additions.
- 2026-10-01 T3 verified (RED observed pre-fix on e592ef3, then GREEN):
  repro `commit('cuántas sesiones');push('cuántas sesiones tengo')`
  returned "cuántas sesiones cuántas sesiones tengo" before, single
  phrase after; full echo `push('hola mundo')` at +1000ms after
  dispatch returned true/hasSpeech true/second dispatch "hola mundo"
  before, rejected with the buffered-echo prefix cleaned after. A first
  implementation wrongly rejected any folded PREFIX of the dispatched
  signature; scope review rejected that as unapproved prefix
  suppression of legitimate shorter commands, and it was corrected the
  same day to full-signature-only rejection plus post-identification
  cleanup (`dropEchoInterim`, push and commit paths). Corrected
  behavior verified by node repro: `push('hola')` after "hola mundo"
  and `push('abre spotify')` after "abre spotify y pon rock" accepted
  and dispatchable; growing prefixes accepted mid-grow then cleaned
  when the full echo arrives (interim and final paths); unrelated
  buffered speech and timing preserved across a full-echo rejection.
  Checks after the scope correction (timing fix still pending):
  `node --test tests/js/endpointing.test.js` 52/52 pass
  (34 prior + 18 new); `node --test tests/js/` 221/221 pass;
  `.venv/bin/python -m pytest tests/ -q` 1201 passed;
  `git diff --check` clean. No source-mutating JS normalizer is
  configured in this repo (verified: no package.json/prettier/eslint/
  biome in brain or repo root). Authored T3 lines: ~55 in
  endpointing.js (comments included), ~200 in endpointing.test.js,
  plus this tracker — above the 90-120 forecast, natural growth from
  the required-scenario tests; no tests were shrunk to fit.
- 2026-10-01 final verification found one state-consistency gap: echo
  cleanup cleared the interim but left speechStartedAt/lastChangeAt
  from the removed ghost, so a genuine new phrase could inherit the
  ghost's hard-cap age and finalize early (reproduced RED with fake
  clock: new speech 4400ms old finalized at the ghost's 5000ms cap).
  Fixed in the same bounded seam: `dropEchoInterim` clears utterance
  timing only when no independent committed speech remains; committed
  timing is preserved; lastFinalSignature/lastFinalAt always survive
  (window expiry still owns them). New fake-clock regressions:
  no-committed cleanup resets the snapshot to the fully-idle no-speech
  shape and a following phrase gets its own hard cap (ghost cap
  provably does not fire); committed-speech cleanup preserves
  speechStartedAt/lastChangeAt and dispatch timing. Final checks:
  `node --test tests/js/endpointing.test.js` 55/55 pass (34 prior +
  21 new); `node --test tests/js/` 224/224 pass;
  `.venv/bin/python -m pytest tests/ -q` 1201 passed;
  `git diff --check` clean.
- Not fixed by design: a standalone partial echo that never grows to
  the full dispatched signature is accepted as speech and can dispatch
  a partial duplicate turn; closing it requires prefix suppression,
  which lacks product authorization.
- Pending: real mobile validation (no handset session run), commit
  (awaits explicit authorization), deployment (not authorized).
- 2026-10-05 local consolidation authorization supersedes the commit wait:
  pending T3 bytes preserved in `3fe9b38`, integrated into main by `16f64d8`.
  Fresh consolidated JavaScript suite: 266 passed, 0 failed; brain Python suite:
  1363 passed. Real handset validation and deployment remain pending.
