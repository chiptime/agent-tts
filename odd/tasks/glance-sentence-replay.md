# External reader sentence replay

## Objective and authority

Enable sentence activation in `#glance-turns .gturn.assistant .reader-content`
to synthesize and read from the selected sentence through the end of that
answer. The user explicitly selected fresh synthesis rather than approximate
seeking. This is a new scope, not a revision of the frozen main-Brain PRD.

The reported click target is `.tts-sent`, which currently has visual affordances
but no playback handler. Preserve rendered HTML, native interaction, main-Brain
karaoke, and unrelated queued audio. No backend, provider, installation,
deployment, real-service calls, or remote inspection. The user subsequently
authorized a local commit only, with author and committer dates set to
2026-10-06 19:01:00 Europe/Madrid (+02:00). No main integration or push before
19:00 Europe/Madrid; no automatic publication has been scheduled.

## Tasks and route

- [x] GSR-01: Extract ordered readable text from a selected rendered sentence
  through the end, including continuations and block separators without duplicate
  text. Test before implementing. Route: delegated; DOM mapping is preparation
  for a nontrivial multi-file write.
- [x] GSR-02: Connect click and keyboard activation to owned replay, starting
  synthesis at that sentence; make Escuchar and replacement/stop cleanup coherent.
  Preserve links, code controls, selection, drag and long-press behavior. Route:
  delegated; reuse the same writer for reader/audio integration and its tests.
- [x] GSR-03: Run isolated real-browser proofs and regressions, independently
  verify the candidate, and report physical audible/mobile acceptance pending.
  Route: delegated writer checks, parent spot check and independent verifier.

## Authorized edit surfaces

- `hosts/herdr/brain/src/herdr_brain/static/reader.js`
- `hosts/herdr/brain/src/herdr_brain/static/app.js`
- `hosts/herdr/brain/src/herdr_brain/static/index.html`
- `hosts/herdr/brain/src/herdr_brain/static/karaoke.js` (only necessary shared replay corrections)
- `hosts/herdr/brain/tests/js/reader.test.js`
- `hosts/herdr/brain/tests/js/karaoke.test.js`
- `hosts/herdr/brain/tests/browser/test_karaoke_harness.py` (changed-source digests only)
- `hosts/herdr/brain/tests/browser/test_glance_sentence_replay.py` (new)
- `hosts/herdr/brain/tests/browser/.runtime/` (temporary test output only)

The writer must stop for an out-of-surface dependency or design blocker. The
existing browser conftest, all other tests, backend, and frozen PRD are read-only.
Unrelated worktree changes must remain untouched. Implementation branch:
`feat/glance-sentence-replay`, starting at `9514e6c`.

## Acceptance and checks

The exact user sequence is external Escuchar, then selecting a later sentence.
The new `/tts` request must exclude preceding sentences and include the selected
sentence and remaining readable content. The previous owned reading must retire;
late results must not revive it. Clicking before playback must also start owned
reading from the selected sentence. Handle continuations/repeated text, multiple
turns with duplicate sentence IDs, remounts/polling, errors, and stop. Displayed
HTML must remain intact. Any necessary shared pause-race correction must have a
behavioral regression proving progress does not freeze after native reselection.

Test-first policy: observed behavioral RED, then GREEN, then refactor. Node uses
the existing built-in runner. Browser tests use committed fixtures, intercepted
traffic and the existing isolated runtime sandbox, never a live service.

From `hosts/herdr/brain`:

- `node --test tests/js/reader.test.js tests/js/karaoke.test.js`
- `node --test tests/js/`
- `node --check src/herdr_brain/static/reader.js && node --check src/herdr_brain/static/karaoke.js && node --check src/herdr_brain/static/app.js`
- Browser runner: `env -u E2E_JS_COVERAGE_DIR -u PYTEST_ADDOPTS -u PYTEST_PLUGINS TMPDIR=/home/bruno/Code/personal/agent-tts/hosts/herdr/brain/tests/browser/.runtime PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -c pyproject.toml --confcutdir=tests/browser -p no:cacheprovider tests/browser/test_glance_sentence_replay.py`
- Run the same browser runner separately for `test_karaoke_harness.py`,
  `test_karaoke_fragments.py`, and `test_karaoke_real_media.py`.

## Progress and delivery

Final writer checks passed: focused Node 116, full JS 356, external browser 56,
harness 12, main-chat browser 47, native media 13; no failures or skips.
Parent independently repeated the initial external suite (41 passed) and the
final focused Node suite (116 passed). Independent verification observed 56
browser passes and reran both prior defect probes unchanged: 2 passes.
The verifier found missing indexes incorrectly reading the whole answer and
natural audio completion discarding a delayed click. One scoped correction
added explicit whole-answer intent and a separate click-intent generation;
11 explicit-retirement cases remain passing. Both findings are resolved.
Observed RED: extraction Node 6 failures; unconnected external browser 26 failures;
correction regressions 4 browser failures, then GREEN 56/56.
Physical audible/mobile acceptance remains pending. No real provider was called.
RDD is clone-local off; no native receipt review or mode change. Native risk
assessment is unassessable because of untracked files; verification treats it as
high and requires a separate read-only verifier.
Forecast: approximately 350–650 authored changed lines, excluding generated
output; advisory only. Strategy: single local candidate, publication deferred
until separately authorized. Keep tests with the behavior; do not minimize
necessary evidence to meet a line heuristic.

Engram mirror topic: `odd/glance-sentence-replay/tasks`.
Actual implementation size: 926 additions and 30 deletions (956 authored changes),
including the isolated browser suite. Tests were kept complete rather than compressed
to match the advisory forecast. A single local work-unit commit is now authorized;
publication remains deferred and unrelated WIP is excluded.
Parent runtime root is still the owning repository; unrelated concurrent docs
commit `9c05992` advanced both branches without changing implementation inputs.
Next step: retain the local commit; main integration/publication may be considered
only after 2026-10-06 19:00 Europe/Madrid. Physical UAT remains pending.

## Authorized extension: external progress tracking

The user now explicitly authorizes implementation of external sentence highlight
tracking. This authorization supersedes the earlier local-commit permission:
deliver one local **uncommitted** candidate; no staging, commits, timestamp
changes, merge, push, publication, restart, deployment, installation, downloads,
or real services. RDD remains clone-local OFF. Earlier GSR-01–03 evidence above
is historical and remains intact; the frozen main-Brain contract is unchanged.

- [x] GSR-04: Add behavioral tests first, observe popup/globalIndex advancement
  leaving the external sentence behind, then implement ordered DOM raw ranges
  and progress-driven highlighting. Route: delegated direct; nontrivial multi-file
  write and DOM/audio preparation. Initial status: preparing behavioral RED.
- [x] GSR-05: Run the authorized Node and isolated Chromium regressions, including
  committed real-MP3 playback; record observed outcomes and hand off for parent
  independent verification. Initial status: pending. Physical-device UAT and
  parent independent verification remain pending, not delegated completion claims.

Objective: `.tts-selected` in the owning external answer follows the existing
popup chunk's approximate `globalIndex`, including hidden-popup playback. Build
sentence raw ranges from the same ordered readable extraction used for synthesis;
use the playback plan's raw ranges and piece/global offsets, never repeated-text
search or a second timing/duration model. Keep the original synthesis-start
`glanceReplay.sentIdx` fixed and store the active sentence separately. Rendered
Escuchar starts at the beginning of that readable extraction; unrendered Escuchar
retains its owned raw-text fallback.

Acceptance checks: intermediate click advances to following sentences; popup and
DOM use the same chunk; repeated sentences, continuations, nested anchors,
duplicate indices across answers, unanchored text/block separators, and >8000
characters map in order. Hidden popup, polling, and same-content remount restore
the active sentence. Identity/content changes, stop, completion, errors, and late
events retire tracking without disabling controls or affecting foreign queued
audio. Preserve native HTML/links/selection/copy, fresh suffix synthesis, response/
pane/session/generation ownership, and main-Brain karaoke.

Extension edit surfaces are limited to this tracker, `reader.js`, `app.js`,
`karaoke.js`, `tests/js/reader.test.js`, `tests/js/karaoke.test.js`,
`tests/browser/test_glance_sentence_replay.py`, corresponding changed-script
digests in `tests/browser/test_karaoke_harness.py`, and browser `.runtime/` output,
all under the existing `hosts/herdr/brain` paths. No `index.html`, conftest, other
suites, backend, provider, deployment, or historical PRD edits. Preserve unrelated
WIP and stop if an allowed file changes concurrently.

Starting branch/HEAD: `feat/glance-sentence-replay` /
`4618da2f6ba30e44154e165e850bd9ab230fcc7a`. Initial SHA-256 fingerprints captured
for all eight allowed tracker/source/test files and all six unrelated WIP files;
allowed files were unchanged immediately before this tracker append.
Forecast: approximately 550–850 authored changes, advisory only; retain complete
tests rather than compressing for a line heuristic. Delivery strategy: single
local uncommitted candidate, with parent-owned review and independent verification.
Validation uses the exact commands already listed above, each browser suite run
separately in the authorized `.runtime/` sandbox. No known environmental failures.

Extension tracking mirror: full initial file and locator saved/read back in Engram
observation #9916, topic `odd/glance-sentence-replay/tasks`; locator
`odd/tasks/glance-sentence-replay.md`. Outcome updates use the same full-file mirror.

Extension RED observed before implementation:
- Focused Node command: 116 passed, 9 failed, 0 skipped. All new raw-mapping
  regressions failed with `reader.readableReplay is not a function`.
- Exact external browser runner: 38 passed, 35 failed, 0 skipped. Both
  `test_intermediate_click_tracks_same_popup_global_index[chromium-visible-popup]`
  and `[chromium-hidden-popup]` first observed the expected popup advance, then
  failed `Popup advanced but external selected sentence stayed behind` with
  `['1'] != ['2']`. Repeated/nested progress, long-piece tracking, advanced
  retirement, and rendered-full-extraction assertions also failed as intended.
- GSR-04 status: implementing after observed behavioral RED. GSR-05 remains
  pending. Allowed source and unrelated WIP fingerprints still match baseline.

Extension GSR-04 GREEN observed: focused Node 125 passed, full JS 365 passed,
syntax checks passed, isolated external Chromium 73 passed; zero failures/skips
on these final runs. The first post-implementation external run was 72 passed /
1 failed: an 81-character unanchored-gap fixture created two popup windows,
contradicting its one-window assertion. Shortening only that fixture restored
the intended alternate-case proof; no implementation or splitter change was made.
`test_native_mp3_external_highlight_follows_popup_progress_when_hidden_and_remounted`
passed with committed MP3 decoding and native seek/timeupdate/ended events.
GSR-05 status: remaining three isolated regression suites are in progress;
parent independent verification and physical-device UAT remain pending.

Extension GSR-05 writer verification complete:
- `node --test tests/js/reader.test.js tests/js/karaoke.test.js`: 125 passed.
- `node --test tests/js/`: 365 passed.
- Exact three-script syntax command: passed, exit 0.
- Exact isolated browser runner, separately for each suite:
  `test_glance_sentence_replay.py`: 73 passed;
  `test_karaoke_harness.py`: 12 passed;
  `test_karaoke_fragments.py`: 47 passed;
  `test_karaoke_real_media.py`: 13 passed.
- All final required runs have 0 failures and 0 skips. Scoped diff whitespace
  inspection passed. Chromium was 153.0.8010.12; all browser traffic was intercepted
  and media proofs used committed fixtures. No real service or physical UAT.
- Ordered extraction emits the synthesis text and immutable UTF-16 sentence
  ranges together. Highlighting chooses the first overlapping sentence in the
  controller's existing global chunk raw range. Gaps/separators belong to the
  preceding sentence; windows spanning sentences are not independently timed.
  `glanceReplay.sentIdx` is read-only; `activeSentIdx` alone follows progress.
- Only `reader.js`, `app.js`, reader Node tests, the external browser suite, and
  the two corresponding harness digests changed, plus this appended tracker.
  `karaoke.js` and its Node suite remain byte-identical to baseline.
- Concurrency checks matched all expected allowed-file hashes after authored
  edits and all six unrelated WIP baseline hashes. Branch/HEAD remained
  `feat/glance-sentence-replay` / `4618da2f6ba30e44154e165e850bd9ab230fcc7a`.
  No index operations, commits, publication, restarts, or installations occurred.
- Delivery: one local uncommitted candidate. Parent independent verification,
  diff assessment, and physical audible/mobile acceptance remain **pending**.
   Engram full-file mirror remains observation #9916; verified fix observation #9932.

### Parent closure of the extension

Independent read-only verification completed: focused Node 125 passed and the
exact external Chromium runner 73 passed, both with zero failures/skips. The
verifier inspected native committed-MP3 decoding, hidden-popup progress, active
remount restoration and ended cleanup; no confirmed behavioral blockers found.
Candidate/frozen-file fingerprints, branch, HEAD and unrelated WIP remained
unchanged throughout independent verification. Historical RED was inspected,
not independently replayed; current GREEN was independently observed.

Parent spot check repeated the exact three-script syntax command: exit 0.
`git diff --check` passed. Native read-only assessment returned `medium`, but
covered the entire worktree including unrelated WIP; this is not a scoped
approval. RDD stayed clone-local OFF, so no native review transaction ran.
GSR-04/05 implementation and automated verification are complete; physical
audible/mobile UAT remains pending. No commits, staging, publication, timestamp
changes, installations, restart or deployment were performed. Delivery remains
one local uncommitted candidate. The next step is device UAT, then a separately
authorized delivery decision; passing the time restriction grants no permission.

## Authorized extension: reader-pipeline tail sentence anchors

The user reported that after clicking `Esta sesión queda protegida, junto con
Whisper y el servidor de síntesis.` the popup stayed correct but ALL remaining
text turned green. A read-only explorer traced the popup/reader frontend to the
cached reader map: alignment `coverage`, 12 sentences, where sent 10 carried
two fragments spanning TWO paragraphs (`[[1276,1360],[1362,1534]]`) and sent 11
was an empty virtual span. The frontend faithfully highlighted the defective
anchor input; the generator (`hosts/herdr/tts-plugin/lib/reader_pipeline.py`)
is the defect site. The user explicitly authorized a two-file scope expansion:
`reader_pipeline.py` plus a new `tests/test_reader_alignment.py`. Still no
commits, staging, merge, push, restarts, deployments, installs, downloads,
services, or cache mutations; RDD clone-local stays OFF; English artifacts;
frozen PRD unchanged.

- [x] GSR-06: Reproduce deterministically from the actual cached envelope
  (read-only) with a minimized standalone regression input, observe behavioral
  RED in the new Python suite first, then implement the smallest correct
  assignment fix in `_build`'s coverage block/fragment assignment so tail
  sentences receive correct, nonempty, distinct anchors. Route: delegated
  direct writer; nontrivial source write with strict TDD.
- [x] GSR-07: Run the exact parent-authorized Brain Node/browser checks and the
  new focused plugin suite; record fresh-render vs old-cache impact; update
  this tracker and the Engram mirror. Parent independent verification stays
  pending.

GSR-06 evidence (all observed, strict TDD):

- Root cause proven at `hosts/herdr/tts-plugin/lib/reader_pipeline.py:661-666`
  (pre-fix lines). In coverage mode, cumulative oracle word-count windows drift
  right when the global `clean_agent_text` glue differs from per-block
  projections (here: the markdown table's pipe→"—" conversion merges the table
  region into one oracle paragraph). The final question paragraph overlaps two
  windows — the previous sentence's 12-char drift sliver and the true owner's
  159-char intersection — while containing ONE raw sentence (closing `?**`
  shields `(?<=[.!?])\s+`). The counts-disagree branch handed the whole block
  to "the sentence covering its start" (the drift sliver's owner, sent 10),
  leaving the true owner (sent 11) an empty virtual anchor: exactly the cached
  envelope's `[[1276,1360],[1362,1534]]` + empty sent 11, reproduced
  byte-for-byte from the reconstructed input before any edit.
- Runner (repository-installed, no installs): from `hosts/herdr/tts-plugin`,
  `env PYTHONDONTWRITEBYTECODE=1 /home/bruno/Code/personal/agent-tts/engine/.venv/bin/python -m pytest -q -p no:cacheprovider <suite>`
  (engine venv: Python 3.14, pytest 9.1.1 — matches pre-existing
  `tests/__pycache__` tags).
- RED observed first (`tests/test_reader_alignment.py` vs unmodified source):
  2 failed, 4 passed. Behavioral failures on the minimized regression input:
  sent fragments `[(2, ((277,361),(363,535))), (3, ())]` — the tail sentence
  absorbed the whole final paragraph and the last oracle sentence had an empty
  virtual anchor; rendered HTML put `¿Cuáles` inside `#tts-sent-2` while
  `#tts-sent-3` was empty. The 4 passing guards (table-glue continuation,
  oracle parity, exact prose, determinism) stayed green throughout.
- Fix (smallest correct): the counts-disagree branch now assigns the whole
  block to the overlapping window with the LARGEST P-range intersection with
  the block's projection range (deterministic tie-break: earliest sentence),
  instead of the window covering the block start. Exact path, single-overlap
  branch, k_raw==overlaps split, oracle enumeration, indices and paragraph
  mapping untouched.
- GREEN: `tests/test_reader_alignment.py` 6 passed, 0 failed, 0 skipped. Full
  plugin suite `tests/` 139 passed, 0 failed, 0 skipped.
- Fresh render of the full reconstructed original input now yields
  sent 10 → `('b6',) ((1276,1360),)` ("Las terminales…") and
  sent 11 → `('b7',) ((1362,1534),)` (whole question paragraph); sents 0–9
  byte-identical to the cached map, including the legitimate b5+b6
  continuation of sent 8.

GSR-07 evidence (each browser suite run separately in the authorized
`.runtime/` sandbox, exact commands from this tracker):

- `node --test tests/js/reader.test.js tests/js/karaoke.test.js`: 125 passed.
- `node --test tests/js/`: 365 passed.
- Three-script `node --check`: exit 0.
- `test_glance_sentence_replay.py`: 73 passed; `test_karaoke_harness.py`:
  12 passed; `test_karaoke_fragments.py`: 47 passed;
  `test_karaoke_real_media.py`: 13 passed. Zero failures/skips everywhere.
- Cache impact (read-only assessment): the cached envelope
  `reader_cache/1/48780e66….json` (sha256 `b21d063a…a814`, unchanged) still
  serves the OLD defective html+map; its key is content-derived and its map
  carries no pipeline-version fingerprint (`engine` holds only
  lang/max_chars/summarize/lexicon_fp). [CORRECTED per parent verification]
  Cached entries have NO time-based expiry; eviction is size-based publish
  (brain reader lines 91–99, 156–181, 268–277). The earlier
  "natural eviction/expiry" wording over-promised and is withdrawn: a stale
  entry persists until size-based eviction or changed content (new key →
  fresh fixed render). No restart, deletion, invalidation, or version
  change performed (not authorized).
- Side effect disclosed [CORRECTED per parent verification; historical
  observation preserved]: the full-plugin-suite run refreshed five
  gitignored `lib/__pycache__/*.cpython-314.pyc` files at run time
  (17:27:36). Parent verification traced the writes to
  `tests/test_smoke_py.py:16-21`: `test_lib_modules_compile` calls
  `py_compile.compile(module, doraise=True)`, which writes `.pyc` explicitly
  regardless of `PYTHONDONTWRITEBYTECODE`. The earlier "some suite child
  lost the variable" attribution was unproven and is withdrawn. The
  write-causing smoke test is now deselected from full-suite runs (see the
  blocker-correction section). The files are regenerable cache artifacts
  invisible to `git status`; not deleted (no deletion authorized). No other
  out-of-surface writes occurred.
- End-state fingerprints: frozen PRD, previous frontend candidate
  (`app.js`, `reader.js`, both browser suites, `reader.test.js`), and all six
  unrelated WIP files byte-identical to the baselines recorded above; cached
  envelope unchanged. Changed files are exactly `reader_pipeline.py`
  (`eea51176…8c6e`), new `tests/test_reader_alignment.py`
  (`09790dda…c15a7`), and this tracker. Branch/HEAD unchanged
  (`feat/glance-sentence-replay` / `4618da2f…0fcc7a`); nothing staged or
  committed. Parent independent verification and physical-device UAT remain
  **pending**.

Baseline fingerprints before this extension (SHA-256):
`reader_pipeline.py` `484c451b…37797`; tracker `9a7727a8…d0fa`;
frozen `docs/prds/herdr-brain-karaoke-fragments.md` `176c8f1a…93e73`;
cached envelope `48780e66….json` `b21d063a…a814`. Previous frontend candidate
(`app.js` `43e0206a…d59c`, `reader.js` `64af1969…0c22`,
`test_glance_sentence_replay.py` `23e3e57f…7803`, `test_karaoke_harness.py`
`4a8217b9…6b8a`, `tests/js/reader.test.js` `681b4e90…f51f`) and unrelated WIP
fingerprints captured unchanged at extension start.
Branch/HEAD: `feat/glance-sentence-replay` /
`4618da2f6ba30e44154e165e850bd9ab230fcc7a`. Runner: repository
`engine/.venv` (Python 3.14, pytest 9.1.1), no installs.

### Blocker correction: exact-mode scope (GSR-06/07 reopened → re-closed)

Independent verifier BLOCKER (confirmed by writer reproduction before any
edit): the largest-overlap counts-disagree rule applied in EXACT alignment
too. For ``Read `foo.` Then test done.`` (literal backticks around foo.) the
raw line has ONE sentence — the "." inside the code span is followed by a
backtick, so `(?<=[.!?])\s+` cannot split there — while the cleaned
projection yields two exact spans. The candidate gave sent 0 an empty
anchor and sent 1 the whole fragment `((0,27),)`, flipping the historical
exact fallback (sent 0 whole fragment, sent 1 virtual). Offline frontend
extraction showed sentence 0's suffix wrongly empty and sentence 1 beginning
with the preceding sentence.

Correction (the ONLY change this round): the counts-disagree branch applies
largest-intersection ONLY when `alignment == "coverage"`; exact keeps the
prior deterministic earliest/start-owner fallback. No oracle splitter
refactor was undertaken — the question+request final paragraph legitimately
remains ONE oracle sentence due to `?**`; the user authorization targets the
generator grouping bug only.

Correction-round TDD and checks (cwd `hosts/herdr/tts-plugin`; runner
`env PYTHONDONTWRITEBYTECODE=1 TMPDIR=/home/bruno/Code/personal/agent-tts/hosts/herdr/brain/tests/browser/.runtime /home/bruno/Code/personal/agent-tts/engine/.venv/bin/python -m pytest -q -p no:cacheprovider …`):

- Regression added BEFORE the correction:
  `test_exact_code_span_keeps_start_owner_fallback`. RED observed:
  1 failed, 6 passed — `assert () == ((0, 27),)` on the exact-mode input
  (behavioral regression, not a missing API).
- GREEN after the correction: `tests/test_reader_alignment.py` 7 passed,
  0 failed, 0 skipped; all original coverage tests remained green.
- Full plugin suite by check-only selection:
  `--deselect tests/test_smoke_py.py::test_lib_modules_compile` — excluded
  ONLY that test because its explicit `py_compile.compile` writes `.pyc`
  regardless of `PYTHONDONTWRITEBYTECODE` (see corrected side-effect note
  above); its check-only sibling `test_bin_entrypoint_present` still ran.
  Result: 139 passed, 1 deselected — NOT a full-all claim.
  `lib/__pycache__` mtimes unchanged by this round's runs.
- Coverage fix re-verified on the full reconstructed original input:
  sents 0–9 unchanged; sent 10 → `('b6',) ((1276,1360),)` ("Las
  terminales…"); sent 11 → `('b7',) ((1362,1534),)`. Largest-overlap
  remains a coverage heuristic: the final paragraph stays a single oracle
  anchor (question + request are one oracle sentence due to `?**`) —
  accepted approximation per verifier.
- Frontend (no frontend changes this round; proportionate focused
  re-check): `node --test tests/js/reader.test.js tests/js/karaoke.test.js`
  125 passed / 0 failed / 0 skipped; exact external Chromium runner
  `test_glance_sentence_replay.py` 73 passed / 0 failed / 0 skipped.
- Parent verification of fresh original maps confirmed sents 0–9 unchanged
  with the tail sentences separated (parent observed sent 10 → paragraph 1,
  sent 11 → paragraph 2 in their render).

GSR-06/07 re-closed on corrected proof and self-checks. Changed files this
round: exactly `reader_pipeline.py` (`91a527dd…8000`) and
`tests/test_reader_alignment.py` (`095351134…9f9f`), plus this tracker.
Frozen PRD, previous frontend candidate, all six unrelated WIP files, and
the cached envelope byte-identical to baseline; `lib/__pycache__` untouched
this round; branch/HEAD unchanged (`feat/glance-sentence-replay` /
`4618da2f…0fcc7a`); nothing staged or committed. Parent independent recheck
and physical-device UAT remain **pending**.

### Parent generator-extension closure

Independent recheck verified the corrected source: 7 passed, 0 failed, 0
skipped, with the exact-code suffix probe restored to HEAD behavior. Fresh
original maps 0–9 stayed identical; maps 10 and 11 now own separate trailing
paragraphs. Nine of ten bounded HEAD comparisons stayed identical; only the
intended coverage correction differed. Parent independently repeated the new
suite before the scoped correction (6 passed) and checked diff whitespace.
RDD remained clone-local OFF. The read-only risk assessment was high/unassessable
because the new regression file is untracked; no native review was started.

Artifact disclosure correction: the first full plugin run explicitly compiled
five ignored `.pyc` files. A later correction-round write changed
`reader_pipeline.cpython-314.pyc`; contrary to the earlier unchanged-mtime
claims, that single generated artifact changed between independent snapshots.
Its exact writer/command is unknown and must not be attributed to tests without
evidence. Independent read-only comparison of 59 recursive code objects against
in-memory compilation of the current source verified matching guarded code;
the artifact is not a source blocker. It was preserved, not imported, executed,
deleted, or regenerated during that diagnostic. No monitored mutations occurred
during independent verification. No source changes outside authorized surfaces
or changes to unrelated WIP were observed.

GSR-06/07 implementation and independent technical verification are complete.
Physical-device UAT and installed/live renderer visibility remain pending.
The existing HTML cache has no time expiry and no source-version invalidation;
this response can still show old grouping. No cache deletion/invalidation,
restart, deployment, commit, staging or publication was authorized or performed.
The question/request paragraph still shares one existing oracle anchor; this
fix separates the two wrongly combined paragraphs, not every grammatical
sentence. Further granularity or cache recovery requires a separate decision.

## Authorized extension: reader cache profile invalidation

The user explicitly authorized the reader cache profile bump
(`hosts/herdr/brain/src/herdr_brain/reader.py` READER_PROFILE_VERSION 1->2
plus its behavioral regression in `hosts/herdr/brain/tests/test_reader.py`)
and a RESTART of the LOCAL Brain service ONLY, AFTER tests and verification.
This delegated phase is implementation and checks plus READ-ONLY
identification of the exact restart target; the restart itself is NOT
authorized in this phase and stays pending parent independent verification
and explicit authorization. Still prohibited everywhere: commits, staging,
push, merge, release, installs, downloads, providers, synthesis, audio
generation, cache deletion, and any edit to the six unrelated WIP files.
The cache schema/sidecar contract stays `reader-pipeline/anchors@1`; no
endpoint, backend, provider, deployment, or other version change. The
verified source renderer fix (GSR-06/07) and the previous frontend
candidate remain unchanged.

- [x] GSR-08: Strict TDD. Add the behavioral regression first and observe
  RED: a stale v1 cached entry (legacy disk envelope AND in-memory LRU
  identity) is REUSED under the current profile, versus the desired MISS
  plus fresh stub render; then bump READER_PROFILE_VERSION 1->2 (the
  deliberate, documented namespace invalidation reader.py Decision 11
  requires for a renderer upgrade), update only the existing tests that
  probe the bump with a now-colliding "2", and observe GREEN. Validate:
  the old v1 entry is preserved on disk (no deletion), the sidecar
  contract stays version 1, and a repeated v2 read is a warm hit with no
  extra render. Stub renderer only, no services. Route: delegated writer.
  Status: GREEN observed; parent independent verification pending (GSR-09).
- [x] GSR-09: Parent independent verification of the bump via the exact
  focused commands, then explicit restart authorization and activation for
  the LOCAL Brain service only. This phase records read-only
  identification of the active unit/process and whether its runtime uses
  the checkout `reader.py` or an installed copy; no restart, stop, kill,
  Whisper/synthesis interference, or deployment is performed here.
  Status: complete — see "GSR-09 outcome" below.

Baselines captured before any edit this round (SHA-256, full digests in
the delegated handoff): `reader.py`
`96d31b3811aa23a4a51eba60b71e045a0cc3a5f4b681513b01ecf8ee62bf599e`;
`tests/test_reader.py`
`4c9db10c6f28502abc6b70fccc5bb07673677b27efc0e732ef3ee99f8782fb01`;
this tracker
`7e799d9c5dbdbcaef3a14b37f2d660b46d2d5f3418e6450374c1664967e94f3a`;
cached envelope `reader_cache/1/48780e66….json`
`b21d063a579f03b5dcb6971fc0ffd752bc4e698351ff1d03662a81c3101da814`.
Renderer/frontend candidate and unrelated WIP hashed unchanged:
`reader_pipeline.py` `91a527dd…8000`,
`tests/test_reader_alignment.py` `095351134…9f9f`,
`app.js` `43e0206a…d59c`, `reader.js` `64af1969…0c22`,
`test_glance_sentence_replay.py` `23e3e57f…7803`,
`test_karaoke_harness.py` `4a8217b9…6b8a`,
`tests/js/reader.test.js` `681b4e90…f51f`,
`tests/js/karaoke.test.js` `5cea9281…c0032`,
`open-session-inventory.md` `77975480…96da`, `llm.py` `a4a48030…6ecc`,
`tools.py` `ceb6ace8…0c5c`, `test_consult.py` `e04b289f…0c67`,
`test_llm.py` `a83c4276…0bd8`, `test_tools.py` `bfb4e2a5…7a2e`.
Branch/HEAD: `feat/glance-sentence-replay` /
`4618da2f6ba30e44154e165e850bd9ab230fcc7a`. Forecast: minimal (~60-120
authored lines across source, tests, and this tracker); advisory only.
Runner: the existing installed Brain `.venv` built-in pytest with
`PYTHONDONTWRITEBYTECODE=1`, no installs; no smoke suites run this round,
so the known `py_compile` pyc side effect is not triggered.

GSR-08 outcome (all observed this round, cwd `hosts/herdr/brain`):

- RED, before the bump, exact command
  `env -u E2E_JS_COVERAGE_DIR -u PYTEST_ADDOPTS -u PYTEST_PLUGINS TMPDIR=…/tests/browser/.runtime PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -c pyproject.toml -p no:cacheprovider tests/test_reader.py`:
  3 failed, 24 passed, 0 skipped. Behavioral failures, not missing APIs:
  disk case reused the stale v1 envelope
  (`'<p>viejo</p>' != '<p>corregido</p>'` with zero fresh renders);
  memory case served the v1 LRU identity (`assert 1 == 2` render calls);
  constant pin failed `'1' != '1'`.
- GREEN, after the bump plus the two expected probe updates
  (`test_profile_version_changes_key` and `test_namespace_bump_invalidates`
  now monkeypatch "3" because the real profile is "2"): same command,
  27 passed, 0 failed, 0 skipped. The new regressions also prove: the
  legacy v1 envelope file is preserved (never deleted), the fresh v2
  envelope re-anchors `v: 1` / sidecar `version: 1`
  (reader-pipeline/anchors@1 unchanged), a repeated v2 read adds no
  render (warm hit), and v1/v2 artifacts live in separate disk
  namespaces.
- Focused Node `node --test tests/js/reader.test.js tests/js/karaoke.test.js`:
  125 passed, 0 failed, 0 skipped.
- Exact external browser runner
  (`… pytest -q -c pyproject.toml --confcutdir=tests/browser -p no:cacheprovider tests/browser/test_glance_sentence_replay.py`):
  73 passed, 0 failed, 0 skipped.
- Changed files this round: exactly `reader.py` (constant 1->2 with the
  documented namespace comment), `tests/test_reader.py` (new
  `TestRendererUpgradeInvalidation` class + the two probe updates), and
  this tracker. Renderer/frontend candidate, unrelated WIP, and the
  cached v1 envelope byte-identical to the baselines above; no
  `__pycache__` writes (`PYTHONDONTWRITEBYTECODE=1` everywhere, no smoke
  suites). Nothing staged or committed. GSR-09 remains pending: parent
  independent verification, read-only restart-target identification, then
  explicit restart authorization.

### GSR-09 correction round: isolated runner, scoped seeds, sweep wording

Independent verifier BLOCKED before executing the unit suite: the
repository `tests/conftest.py` session-autouse `_tts_backend_stubs`
fixture rewrites and chmods the hardcoded `/tmp/herdr-brain-test-tts/bin/
herdr-tts` stub regardless of TMPDIR, and the verifier runs without
authorization for out-of-repo /tmp writes. Accurate disclosure: every
earlier conftest-loaded unit run (this writer's RED/GREEN and the parent's
prior runs) already performed that same stub write; it is pre-existing
authorized test infra, and this round added NO further /tmp writes — the
isolated runner below does not load conftest, proven by stub fingerprints
before/after (sha256 `9792b9c9…`, mode 755, mtime unchanged, no new
files under the stub root). No conftest edits were made or authorized.

Isolated runner (parent-approved `--noconftest`; `tests/test_reader.py`
imports only the `SETTINGS_KWARGS` constant from `tests.conftest`, so the
module import resolves while no fixture — autouse stub or hermetic env —
ever loads), cwd `hosts/herdr/brain`:

    env -u E2E_JS_COVERAGE_DIR -u PYTEST_ADDOPTS -u PYTEST_PLUGINS TMPDIR=/home/bruno/Code/personal/agent-tts/hosts/herdr/brain/tests/browser/.runtime PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -c pyproject.toml -p no:cacheprovider --noconftest tests/test_reader.py

- PRE-fix (tests still seeding via module-wide `monkeypatch.undo()`):
  27 passed, 0 failed, 0 skipped.
- POST-fix: 27 passed, 0 failed, 0 skipped.

Mechanical corrections this round (test isolation and documentation only;
no behavior change, so no RED phase — pre/post isolated runs above are the
proportional check):

- `tests/test_reader.py`: both new GSR-08 tests now seed the legacy v1
  profile through a scoped `with monkeypatch.context() as legacy:` block
  instead of `monkeypatch.undo()`, which also tore down the shared
  hermetic-env fixture patches for the remainder of each test. The class
  docstring no longer says the orphaned v1 entry is "left for the
  oldest-first sweep": the old v1 entry is preserved with NO automatic
  cleanup, because `_sweep` bounds only the current profile's directory.
- `src/herdr_brain/reader.py`: the pre-existing RELEASE STEP docstring
  sentence claiming the oldest-first sweep reclaims the orphaned
  old-version tree is corrected to state the sweep bounds only the
  CURRENT profile directory (hand removal is safe but never required);
  comment-only edit, implementation and the profile "2" constant
  unchanged.
- End-state fingerprints: frozen frontend/renderer/browser suites, all six
  unrelated WIP files, the untracked plugin regression, the cached v1
  envelope, and `reader_cache/` contents byte-identical to the GSR-08
  baselines. Changed files are exactly `reader.py`
  (`8e02aa82…`, comment-only delta over `f4b27bbe…`),
  `tests/test_reader.py` (`cf482b4c…`), and this tracker. No `/tmp`
  writes, no services, no restart, nothing staged or committed.
- GSR-09 remains pending: parent independent verification via the
  isolated runner above, then explicit restart authorization. No restart
  is performed in this round.

### GSR-09 outcome: authorized restart and profile-2 activation proof

Independent verification of the profile bump had already PASSED (isolated
runner `--noconftest tests/test_reader.py`: 27 passed, 0 failed, 0 skipped,
no outside writes). The user then explicitly authorized the profile bump +
restart (confirmed twice) and continuation. This operational round executed
exactly one restart and gathered read-only proof only; no test reruns, no
broad smoke writes, no source edits beyond this tracker append.

- Pre-restart read-only precheck matched the previously identified target
  exactly: user unit `herdr-brain.service` (FragmentPath
  `~/.config/systemd/user/herdr-brain.service`), MainPID 573,
  active/running since Tue 2026-10-06 09:53:47 CEST, WorkingDirectory the
  checkout `hosts/herdr/brain`, ExecStart the checkout
  `.venv/bin/python -m herdr_brain.server` (editable path -> checkout
  `src`; a service-style import confirmed `READER_PROFILE_VERSION = "2"`
  from `reader.py` sha `8e02aa82…`).
- Command executed exactly once: `systemctl --user restart herdr-brain` —
  exit 0. New MainPID 3380640, active/running since Wed 2026-10-07
  00:32:29 CEST. No retries, no fallback, no other unit touched (only
  `herdr-brain.service` exists among `herdr*` user units; Whisper and the
  synthesis server are not systemd user units and were not scanned,
  stopped, or restarted).
- Read-only health GET `http://127.0.0.1:8741/health`:
  `{"status":"ok","version":"0.1.0","stt":"ready","tts":"ok"}`. No
  prompts/chat POST, no TTS/STT/provider call, no audio requested or
  generated by this round.
- Expected profile-2 key derived in memory BEFORE the restart: the old
  envelope's `fragment_texts` plus offset-derived gaps (gap 1 -> space,
  gap 2 -> `\n\n`) reconstruct the exact original input (proven: the v1
  framing reproduces the cached key `48780e66…` byte-for-byte), and the
  current `cache_key` with profile "2" yields
  `2f129d2054dbcf211fe1c73aebfab83270b27f9461b9ec930c2a21aa0f6fc3e3`.
  Computation used `PYTHONDONTWRITEBYTECODE=1`, in-memory only.
- Activation proof (the brain service's own UI poll warmed the fresh
  namespace `reader_cache/2/` within ~20 s of the restart; those entries
  are service-side writes, not this round's): the target envelope
  `reader_cache/2/2f129d20…f6fc3e3.json` exists at the derived key with
  envelope `v: 1`, map `version: 1`, contract `reader-pipeline/anchors@1`,
  engine unchanged (lang es / max_chars 0 / summarize false /
  lexicon_fp ""), total_sents 12, total_paras 6. Sentence-map proof:
  sents 0-9 fragment spans identical to the v1 envelope; sent 10 is a
  single-paragraph anchor `[1276,1360]` (block b6, para 4, 84 chars — was
  two fragments spanning two paragraphs); sent 11 is separate and
  NONEMPTY `[1362,1534]` (block b7, para 5, 172 chars — was an empty
  virtual span). The final question+request paragraph remains ONE anchor
  (accepted approximation), matching the GSR-06/07 fresh-render
  prediction exactly.
- Preservation evidence: the old v1 envelope
  `reader_cache/1/48780e66….json` is unchanged and undeleted (sha256
  `b21d063a…da814` before == after). All before/after fingerprints match:
  `reader.py` `8e02aa82…`, `reader_pipeline.py` `91a527dd…8000`,
  `tests/test_reader.py` `cf482b4c…`, the frontend candidate (`app.js`
  `43e0206a…`, `reader.js` `64af1969…`, browser suites, Node suites),
  untracked `tests/test_reader_alignment.py` `095351134…9f9f`, the frozen
  PRD `176c8f1a…93e73`, and all six unrelated WIP files
  (`open-session-inventory.md`, `llm.py`, `tools.py`, `test_consult.py`,
  `test_llm.py`, `test_tools.py`). Branch/HEAD unchanged
  (`feat/glance-sentence-replay` / `4618da2f…0fcc7a`); nothing staged or
  committed. `reader_cache/2/*` (nine envelopes at last inspection) are
  new service-side runtime writes after the restart and are the only
  observed state changes; no claim is made that other runtime state is
  unchanged.
- GSR-09 marked checked: restart + health + activation proof all
  observed. Physical-device UAT remains the next step; no physical
  acceptance is claimed. Engram full-file mirror (topic
  `odd/glance-sentence-replay/tasks`) PENDING: the MCP save was rejected
  because multiple active runtime sessions match this project and no
  authoritative session ID may be invented; this local tracker is the
  authoritative GSR-09 record until the parent retries the mirror under
  an unambiguous session.

## Authorized extension: shared popup-chunk DOM range green (GSR-10/11)

The user confirmed the cache-recovery paragraph separation (sent 10 "Las
terminales…ayudaría." and sent 11 question+request are now separate
anchors) and reports the remaining highlight defect: the popup correctly
shows one chunk window at a time, but the external green paints the ENTIRE
owning DOM anchor. One oracle anchor legitimately contains several
grammatical sentences — the engine splitter `(?<=[.!?])\s+` keeps the
closing `?**` glued to the request — so on the exact profile-2 tail the
last anchor owns popup chunks 3–6 and anchor-level `.tts-selected`
overspans the whole question+request through all its windows. No new
product decision: the popup is already correct; green must reflect the
SAME shared popup chunk raw interval within the owning anchor/content, not
the sentence attribute index alone. Approximate fragments are accepted; no
precise timestamps and no second timing model. The engine/oracle/backend/
providers stay frozen (21 callers); no further cache bump or restart — the
one authorized Brain restart (PID 3380640, profile 2) already ran, and the
source JS is served statically so only a browser reload exposes this
change. Goal revision for this extension only: from "whole owning anchor
green" to "shared popup window green", preserving all prior historical
evidence and the known oracle limitation without claiming earlier work was
wrong.

- [x] GSR-10: Strict TDD. Add the behavioral browser regression first and
  observe RED on the exact tail (the `conservar?` popup window painting
  `Indícame…` green, and the later window still painting the question,
  while the popup text itself stays correct), plus Node unit regressions
  for the raw-range-to-DOM mapping. Then refine the extractor so the SAME
  normalized synthesis raw range maps to DOM text-leaf UTF-16 positions
  (whitespace collapsed, unanchored separators, nested continuation
  anchors, repeated text, Unicode and >8000-character pieces included),
  and progress green paints only the chunk's exact visible portion(s)
  within the owning turn: minimally owned leaf marks reusing the existing
  `.tts-selected` CSS (no HTML flattening or replacement, no new CSS or
  index.html, no text-identity searches, no glyph alignment). Keep the
  immutable original `glanceReplay.sentIdx` boundary and the raw ownership
  checks; suffix synthesis clicks and whole-answer Escuchar stay unchanged.
  Marks must not invalidate the ownership extraction, must stay reentrancy
  safe under the existing observers, preserve native links/selection/copy/
  keyboard/audio controls and main-Brain karaoke, avoid duplicate indices
  across answers, paint only external owned content, keep a chunk spanning
  several anchors a contiguous raw range without painting unrelated
  whitespace-gap/block content, keep working with the popup hidden, rebind
  the CURRENT range (not the initial index) across polling/remount, retire
  on DOM content/identity/pane/session/generation changes, and clear only
  the owned range/highlight on stop/end/error with native controls usable.
  Route: delegated direct writer.
- [x] GSR-11: Run the exact authorized verification commands (focused and
  full Node suites including karaoke.test.js untouched, the three-script
  syntax check, and the four isolated browser suites run separately),
  update ONLY the two corresponding changed-script digests in
  `test_karaoke_harness.py`, record observed outcomes, and hand off for
  parent independent verification. Physical-device UAT and the manual
  browser reload remain pending; no restart, commit, staging, install, or
  provider call. Route: delegated direct writer; parent owns disposition.

Edit surfaces for this extension: this tracker, `reader.js`, `app.js`,
`tests/js/reader.test.js`, `tests/browser/test_glance_sentence_replay.py`,
the two corresponding changed-script digests in
`tests/browser/test_karaoke_harness.py`, and browser `.runtime/` output.
`karaoke.js`, `tests/js/karaoke.test.js`, `index.html`, conftest, backend,
engine, providers and all six unrelated WIP files remain frozen and
byte-identical. Starting branch/HEAD `feat/glance-sentence-replay` /
`4618da2f6ba30e44154e165e850bd9ab230fcc7a`; tracker baseline this round
`41247d82b1027367a2e101d2201e2ee1719e8b0385c962009dba3c030c5aa180` with
all allowed-file and unrelated-WIP fingerprints matching the GSR-09 end
state before this append. Forecast: approximately 350–650 authored lines
including complete tests; advisory only, never test golf.

GSR-10/11 evidence (all observed this round, strict TDD):

- Engram full-file mirror updated and read back BEFORE the first source
  write via observation #9916 (validated ID route; 46703 bytes untruncated,
  all prior history preserved verbatim).
- Node RED before implementation: `node --test tests/js/reader.test.js`
  48 tests, 41 passed, 7 failed — every new raw-range/paint regression
  failed with `TypeError: reader.rawRanges/paintRawRange is not a function`
  after the exact-tail window predicates were proven deterministic against
  the real splitter (windows: Esta / Las…poco; / cerrarlas…ayudaría. /
  ¿Cuáles…quieras / conservar? / Indícame…trabajando, / no cerraremos
  ninguna. — matching the parent-reported 7-chunk shape).
- Browser behavioral RED, exact runner, focused selection
  `test_shared_anchor_popup_windows_paint_only_their_own_visible_text`
  (both chromium parametrizations): the popup assertion PASSED (toast
  showed only `…trabajo que quieras`) while green failed equality by
  overspanning the whole anchor: `+ que quieras conservar? Indícame sus
  PID o terminales; … no cerraremos ninguna.` — the user's exact defect.
- Implementation: `readableReplay` now emits a third frozen field
  `sources` (per-raw-char `{node, off}` origin or null for synthetic
  separator/collapsed-whitespace chars) from the SAME walk that produces
  rawText/sentences — no second model. `rawRanges(replay, start, end)`
  maps a raw interval to per-leaf DOM runs `{node, start, end, raw}`
  (intra-node gaps merge because they are DOM-whitespace-only; cross-node
  gaps break runs so block separators paint nothing).
  `paintRawRange(container, replay, start, end)` wraps each run in an
  owned `<span class="tts-selected tts-range" data-tts-range="a:b">`
  (splitText leaf marks; reuses the existing `.tts-selected` green CSS —
  no new CSS, no index.html, no HTML flattening, no text searches). The
  raw-bounds attribute makes repainting the same window a no-op, so
  repeated timeupdate progress and the attribute-filtered MutationObserver
  re-syncs converge without loops; repainting a different window clears
  marks WITHOUT normalize (the next runs still reference those split
  leaves), while the healed teardown `clearPaintedRanges` unwraps and
  normalizes back to byte-identical HTML. `clearPaintedRanges` never
  touches anchor-level `.tts-selected` selections.
- app.js: `onProgress` stores `glanceReplay.activeRaw = [chunk.raw[0],
  chunk.raw[1]]` (plus the retained `activeSentIdx` bookkeeping);
  `glanceReplayTurn` re-adopts the FRESH extraction after boundary
  validation so polling/remounts rebind the CURRENT range onto current
  leaves; `syncGlanceReplay` paints the chunk interval instead of
  `selectSentence` and re-presses the owning anchors' `aria-pressed` after
  `prepareReaderControls`; `clearGlanceSelection` unwraps owned marks
  before class cleanup. The immutable `sentIdx` boundary, suffix
  synthesis clicks, whole-answer Escuchar, unrendered raw fallback,
  main-Brain karaoke, foreign queue audio and all retirement paths are
  unchanged.
- Green representation change (test updates are behavioral, not loosened):
  the browser `selected()` helper now asserts the DOM region spanned by
  the marks reconstructs the ACTIVE popup window's text (non-whitespace
  character equality — DOM block boundaries carry no whitespace node, so
  exact whitespace placement is the accepted approximation), scoped to the
  owning turn, with `.karaoke-active` idle; the intermediate-click test's
  `dataset.sentIdx`-on-selected assertion became a mark-ownership check
  (`closest('[data-sent-idx]')` ⊆ {active index, None}); `count=` args at
  all call sites dropped (mark count follows the window's leaf runs); the
  empty-replay Node deepEqual gained `sources: []` and the remount test
  now asserts mapping equality plus fresh-leaf rebinding. The fake DOM
  gained real-DOM-faithful `splitText`, `insertBefore` (detaches from the
  old parent) and recursive `normalize` (detaches merged-away nodes).
- GREEN, exact commands from cwd `hosts/herdr/brain`:
  `node --test tests/js/reader.test.js tests/js/karaoke.test.js`: 132
  passed / 0 failed / 0 skipped; `node --test tests/js/`: 372 passed /
  0 failed / 0 skipped; three-script `node --check`: exit 0. Browser
  suites, each run separately with the exact isolated runner:
  `test_glance_sentence_replay.py` 75 passed (73 prior + the 2 new
  exact-tail parametrizations), `test_karaoke_harness.py` 12 passed with
  only the two changed-script digests updated (`reader.js`
  `ec254df1…76cf`, `app.js` `b21779df…148e`), `test_karaoke_fragments.py`
  47 passed, `test_karaoke_real_media.py` 13 passed — zero failures and
  zero skips everywhere; Chromium 153.0.8010.12.
- Coverage delivered beyond the exact tail: multi-anchor windows paint
  contiguous runs (ul/table/pre + unanchored tail), repeated identical
  sentences paint only the clicked boundary's occurrence, nested
  continuation anchors, collapsed intra-node whitespace, 300-sentence
  Unicode (😀/áéíóú) multi-piece suffix painting the final window, >8000
  char piece mapping, idempotent repainting across repeated progress with
  ownership extraction stable under marks, anchor-selection survival,
  hidden-popup tracking, polling/remount rebinding of the current range,
  and full cleanup (idle HTML byte-identical after heal, structure and
  native controls intact).
- End-state fingerprints: changed files are exactly `reader.js`
  (`ec254df1…76cf`), `app.js` (`b21779df…148e`),
  `tests/js/reader.test.js` (`8daa3a13…9a5c`),
  `tests/browser/test_glance_sentence_replay.py` (`9bba1b63…1ce5`),
  `tests/browser/test_karaoke_harness.py` (`a5f462c1…4ad4`, two digests
  only) and this tracker (`3296ae93…bb1f` at outcome time). Frozen
  byte-identical: `karaoke.js`, `tests/js/karaoke.test.js` (`5cea9281…`),
  `index.html` (`b9947bc4…`), `reader.py` (`8e02aa82…`),
  `reader_pipeline.py` (`91a527dd…8000`), `tests/test_reader.py`
  (`cf482b4c…`), both other browser suites, and all six unrelated WIP
  files. Branch/HEAD unchanged (`feat/glance-sentence-replay` /
  `4618da2f…0fcc7a`); nothing staged or committed; no restart, service
  operation, install, download or provider call occurred. `git diff
  --check` passed.
- GSR-10 and GSR-11 checked. Parent independent verification, the manual
  browser reload that exposes the statically-served change, and
  physical-device UAT remain **pending**; no restart is needed or
  authorized (the single authorized restart already ran, PID 3380640,
  profile 2).

### GSR-10/11 reopened: independent-verifier findings (scoped correction)

Fresh verifier rerun of the previous candidate (Node 132, browser 75, all
green) BLOCKED on two findings, both accepted. Scope of this correction:
the same allowed surfaces only (`reader.js`, `app.js`,
`tests/js/reader.test.js`, `tests/browser/test_glance_sentence_replay.py`,
the corresponding reader digest in `test_karaoke_harness.py`, this
tracker, `.runtime/`). No backend, engine, profile version, restart, live
providers, audio, installs, staging or commits; previous renderer/cache-2,
all unrelated WIP and frozen files preserved. Baselines captured before
this append: `reader.js` `ec254df1…76cf`, `app.js` `b21779df…148e`,
`tests/js/reader.test.js` `8daa3a13…9a5c`, external suite
`9bba1b63…1ce5`, harness `a5f462c1…4ad4`, tracker `76e9aa5f…1bf9`; all
frozen fingerprints re-verified unchanged.

Finding 1 (test evidence): the external `selected()` helper compared the
DOM `Range` from the first to the last mark — an envelope that includes
UNPAINTED interior text, so edge-only paint with otherwise expected
endpoints/window could pass; and the `index`/`count` parameters were
ignored, so the repeated-identical-frases progress tests did not prove the
selected occurrence. Correction: compare the actual concatenation of the
selected leaf marks' `textContent` in DOM order (whitespace placement
approximate, non-whitespace characters exact — no envelope), and restore
owning-occurrence evidence for the provided expected index: no mark may
sit in an anchor of an earlier sentence, and the expected index's anchor
(or a fully unanchored window) must own the paint — identical text in a
different occurrence is rejected. The exact-tail `green()` assertion
(already actual marked text, visible+hidden, one anchor 11) is kept
unchanged. Two permanent behavioral negative probes added: edge-only
interior hole and wrong identical occurrence — each must make `selected()`
raise, and each was observed PASSING-for-the-wrong-reason (no raise) under
the old helper before the correction = observed RED.

Finding 2 (source): `paintRawRange` rejected detached nodes but not
ATTACHED leaf nodes extracted from a FOREIGN container (another response);
the normal app rebind path mitigates, but the exported painter's ownership
promise was untrue. Correction: validate containment of every run's node
inside the target container (ancestor walk over `parentNode`, native-faithful
in both real and fake DOM) BEFORE any split/wrap; foreign sources paint
nothing anywhere (owner and foreign container both untouched, return 0).
Deterministic Node behavioral RED added first: extract another attached
container's replay and paint it into the owner — the previous code painted
the foreign container.

Correction-round TDD and self-checks (all observed, cwd `hosts/herdr/brain`,
same isolated runner env; no Python unit conftest, no smoke py_compile):

- Node RED before the fix: `node --test tests/js/reader.test.js` 50 tests,
  48 passed, 2 failed — BEHAVIORAL failures: "foreign origins paint
  nothing" (`painted != 0`: the foreign attached container was painted)
  and "foreign container untouched" in the mixed-owner probe. No missing
  APIs.
- Browser RED before the fix (focused `-k selected_rejects`): both probes
  failed with `Failed: DID NOT RAISE AssertionError` — the old envelope
  helper ACCEPTED edge-only interior holes and the wrong identical
  occurrence. No missing APIs.
- GREEN after the two corrections (helper mark-concatenation + occurrence
  rules; reader.js `ownedByContainer` ancestor-walk guard before
  split/wrap): focused Node 134 passed / 0 failed / 0 skipped (132
  baseline + 2 new); full `node --test tests/js/` 374 passed / 0 failed /
  0 skipped (372 + 2); three-script `node --check` exit 0; browser suites
  run separately: external 77 passed (75 baseline + the 2 permanent
  probes), harness 12 passed with ONLY the reader digest updated
  (`28daec61…1a76`; `app.js` byte-identical `b21779df…148e` — no app
  change needed), fragments 47 passed, real media 13 passed. Zero
  failures/skips everywhere; Chromium 153.0.8010.12.
- Stronger old tests (not loosened): `selected()` now proves the painted
  content by ACTUAL mark text concatenation in DOM order (unpainted
  interior cannot pass; whitespace placement remains the accepted
  approximation, non-whitespace characters exact) and proves the selected
  occurrence for the expected index (no earlier-sentence anchor may be
  painted; the expected index — or a fully unanchored window — must own
  the paint; identical text in another occurrence is rejected). The
  repeated-identical-frases progress tests consequently prove the LATER
  occurrence, and the native-MP3 highlight tests now verify actual green
  interior text without any envelope reliance. The exact-tail `green()`
  assertion (actual marked text, visible+hidden) is unchanged and passing.
  All click-suffix / whole-beginning / HTML-structure-copy-links-controls
  / hidden-popup / remount / stop-end-error / shared-global-chunk progress
  and immutable-original-sentIdx behaviors remain covered by the unchanged
  existing suite.
- End-state fingerprints: changed files are exactly `reader.js`
  (`28daec61…1a76`), `tests/js/reader.test.js` (`8c53b9c4…3477`),
  `tests/browser/test_glance_sentence_replay.py` (`47176c26…21cf`),
  `tests/browser/test_karaoke_harness.py` (`9eb665f7…5a1`, reader digest
  only) and this tracker. `app.js` (`b21779df…148e`), `karaoke.js`,
  `tests/js/karaoke.test.js`, `index.html`, `reader.py`
  (`8e02aa82…`), `reader_pipeline.py` (`91a527dd…8000`),
  `tests/test_reader.py` (`cf482b4c…`), the frozen PRD path, and all six
  unrelated WIP files byte-identical to the round's baselines. Branch/HEAD
  unchanged (`feat/glance-sentence-replay` / `4618da2…0fcc7a`); `git
  diff --check` passed; nothing staged or committed; no restart, service
  operation, install, download or provider call.
- GSR-10/11 re-closed on corrected proof. Parent independent recheck, the
  manual browser reload, and physical-device UAT remain **pending**. Full
  Engram mirror #9916 is HONESTLY PENDING: the tracker now exceeds the
  50,000-byte observation cap (53,065 bytes before this correction round,
  larger after), so a full-file rewrite would truncate history; compact
  anchored addenda were used instead and no truncating save was attempted.

### Parent closure of the shared-window range correction (GSR-10/11)

The delegated independent recheck could not run because the model service
reported a usage limit; this is not a code or test failure. The parent therefore
repeated bounded checks inline, without claiming a delegated independent pass:
focused Node 134 passed; full JS 374 passed; three-script syntax exit 0;
external Chromium suite 77 passed; harness 12 passed; zero failures and zero
skips in those runs; `git diff --check` passed. The fragments (47) and native
media (13) suites were not repeated by the parent; they remain writer evidence.
The earlier independent verifier's two findings (edge-only helper envelope and
foreign attached nodes) were fixed with observed RED first, but the verifier did
not recheck those fixes. Branch/HEAD, Brain PID 3380640 (profile 2, no further
restart) and RDD clone-local OFF were unchanged. The browser needs a manual
reload to load the new static JS. Physical audible/mobile UAT remains pending.
The Engram full-file mirror is pending because the tracker exceeds the 50,000-byte
observation cap; this local tracker is authoritative.

### Final closure: merged, published, verified on device (2026-10-07)

The complete feature merged to local main (50c7e75) as one merge over 4618da2
plus three work-unit commits (698725e renderer anchors, 7f48075 cache profile 2,
4739b56 popup-range highlight), then pushed: origin/main is at 50c7e75. Device
acceptance confirmed by Bruno: audio and highlight work on the phone over
Tailscale HTTPS (serve 8443 -> localhost:8741, persistent --bg; the earlier
aborted serve command had dropped the listener). Everything above that described
an uncommitted, unpublished candidate is historical and now superseded. The six
unrelated WIP files remain uncommitted and untouched. Session closed.
