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
