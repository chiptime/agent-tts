# POST-06 — Re-pin `claude-multi.txt` + soporte Claude Code v2.1.328 multiselect real

Objective: make `ClaudeAdapter` recognize the REAL Claude Code v2.1.328
AskUserQuestion multiselect (today: `None`), feeding the existing
multiselect answer choreography unchanged. Branch
`feat/post-06-claude-multi` (base `main` @ `975c1ac`). Delivery:
single work-unit commit on the branch (no push/PR/merge). Test runner:
C4 (below).

## Specs

- S1: The real capture must be detected as a multiselect. Brief quote
  (verbatim): "Ejecutado `ClaudeAdapter().detect(stripped.splitlines(), None)` contra la captura real `claude-multi-v21328.txt` → devolvió **`None`**!"
  Acceptance: `detect("claude", fixture claude-multi-v21328.txt)` →
  `kind="multiselect"`, options Engine/Herdr Brain/TTS plugin/Type
  something (checkbox rows, digit-only keys) + unnumbered `Submit`
  (walk-class), descriptions attached, `selected=0`,
  `nav=("up","down")`, `commit=("enter",)`, hint verbatim.
- S2: Fail-closed discipline. Brief quote (verbatim): "Mantener disciplina fail-closed: nunca confundir un `select` normal con `multiselect`, ni romper ninguno de los tests existentes en `tests/test_harness_dialogs.py`, `tests/test_dialog_answer.py`, `tests/test_dialog_guard.py`, `tests/test_view_dialog.py`."
  Acceptance: all four suites green, byte-identical legacy behavior
  (flags default off; v2 gate requires the select footer + full shape).
- S3: Key recipe verified BEFORE implementing. Brief quote (verbatim):
  "Verificar cuidadosamente la receta de teclas real (¿cómo se marca un checkbox en Claude v2.1.328: dígito o Space/Enter, y cómo se hace Submit en la fila no numerada?) antes de implementarlo".
  Verified from the installed bundle (exactly v2.1.328,
  `~/.local/share/claude/versions/2.1.328`): digit 1-9 toggles its own
  box (`/^[0-9]$/ → toggleValue`); ↓ from the last option focuses the
  unnumbered submit row (`f===K → isSubmitFocused`); Enter there
  commits (`onSubmit`); `Type something` is a `type:"input"` row with
  its own checkbox; intermediate wizard steps draw `Next`; the
  `Chat about this` digit (= options+2) fires `onRespondToClaude`.
- S4: C4 tests green. Brief quote (verbatim): "Tests desde `hosts/herdr/brain/` vía pytest (`PYTHONPATH=<wt>/hosts/herdr/brain/src /home/bruno/Code/personal/agent-tts/hosts/herdr/brain/.venv/bin/python -m pytest tests/test_harness_dialogs.py tests/test_dialog_answer.py tests/test_dialog_guard.py tests/test_view_dialog.py`)".
- S5: Real fixture + provenance; legacy fixture CONSERVED (decision
  approved by the operator: legacy pins the word-glyph/`Space to
  select` grammar of older versions; the new fixture
  `claude-multi-v21328.txt` is the live capture, ANSI-stripped, from
  the tab header to the footer INCLUDING the `───` rule and
  `5. Chat about this`).
- S6: Fingerprint normalization covers the answered-state header chip:
  the tab chip flips `☐`(U+2610)→`☒`(U+2612) when the question becomes
  answered (`checkboxOff/checkboxOn` in the harness's tab strip), and
  that row sits INSIDE `dialog_region` (lookback 9) — without
  normalizing it, `_await_box` breaks the choreography after the first
  toggle. `_state_normalized` erases `☐☒` and `[×]` (TERM=linux form)
  too.
- S7 (assumption, marked): modeling decisions approved by the operator
  ("dale"): `Type something` = checkbox row (NOT free_text — the digit
  toggles in multiselect; the S3 typing choreography does not apply);
  `Chat about this` NOT modeled (fail-closed; consequential
  decline-the-question action — follow-up); submit tail accepts
  `Submit|Next` (bundle-verified labels of the same row).
- S8: Out of scope (explicit): real v2.1.328 single-select (same
  own-row-description parser gap, left for a roadmap item), notes
  (`n`), question switching (tab), modeling `Chat about this`.

## Tasks

- T1 (S5): fixture `claude-multi-v21328.txt` byte-exact from
  `odd/roadmaps/parity-post/briefs/claude-multi-v21328.txt` lines
  10-25 + provenance.md row. Route: inline. Commit: `eebda5f`. ✅
- T2 (S1,S2,S4,S6): RED tests `TestClaudeMultiV2` in
  `tests/test_harness_dialogs.py`. Route: inline. Observed RED:
  4 failed (positive detection) / 227 passed (all legacy). Commit:
  `eebda5f`. ✅
- T3 (S1,S2,S6): implement `collect_numbered_block` opt-in flags
  (`keep_descriptions`, `submit_tail`) in `dialogs/numbered.py`;
  `_multiselect_v2` + `_families` restructure + `_state_normalized`
  extension + module docstring in `dialogs/adapters/claude.py`.
  Route: inline. Observed GREEN: C4 suites 231 passed. Commit:
  `eebda5f`. ✅
- T4 (S2,S4): C4 suites green (231 passed, 0 failed); full
  non-browser brain suite regression green (4865 passed, 0 failed —
  the 14 documented env-dependent baseline failures pass in this
  worktree environment). Differential check: payloads compared for
  all 21 claude fixtures, base `975c1ac` vs branch — only
  `claude-multi-v21328.txt` changes (None → multiselect); every
  legacy payload byte-identical. Route: inline. ✅
- T5: independent verifier pass dispatched by the LEAD (odd-verifier);
  this tab reports done with the diff + acceptance checks, does NOT
  spawn the verifier itself. Verdict round 1: NEEDS_FIX (S2 FAIL,
  S6 PARTIAL). Fix round T6 below; re-report done for re-verification.
  🔲 (re-verification pending)
- T6 (S2,S6 — verifier round 1 fixes): (1) CRITICAL: try
  `_multiselect_v2` under `_SELECT_FOOTER` BEFORE the select family
  (descriptions are optional; without them the default block exists
  and box-row screens were misclassified as `select` with a
  digit+enter recipe; truncated multi without Submit fell to select
  instead of fail-closed None) + select declines when any row carries
  a checkbox glyph; (2) HIGH: `_multiselect_v2` requires
  `rows[0][0] == "1"` (a run starting mid-list is a truncated/
  scrolled fragment with a fabricated question); (3) MEDIUM: TERM=linux
  glyphs — `[√]`/`[×]`/`[X]` added to `_CHECKED_GLYPHS`, `√` added to
  the `_state_normalized` bracket class; (4) cleanups: wide-gap
  recombine in `_multiselect_v2` (never an empty label; bare box
  declines), unused `unread` import removed (ruff F401), fixture size
  corrected to 702 bytes UTF-8 (502 chars). Route: inline. Observed
  RED: 6 failed / 8 passed (new tests only). Observed GREEN: C4 suites
  237 passed; ruff F/E9 clean (the file's UP-style findings are
  pre-existing, uniform across the file; the two new-code style hits
  follow the file's own List/Optional convention). Commit: (see L8).

## Log

- L1 (verbatim user request): the fleet brief "TASK" section of
  2026-11-26 (POST-06, hito `bbb56b43`): investigate read-only,
  present plan, on approval execute ODD with strict TDD; worktree
  `/home/bruno/Code/personal/agent-tts-worktrees/post-06-claude-multi`;
  prohibited: ROADMAP.md edits, merge/push/PR.
- L2 (2026-11-26): investigation findings — root cause (own-row
  descriptions break every numbered run → block None → known-family
  footer refuses the generic menu → None); key recipe verified from
  the installed v2.1.328 bundle (digit toggles, ↓-to-Submit + Enter,
  `Type something` = input row with box, `Next` on intermediate steps,
  Chat digit → onRespondToClaude); header chip ☐→☒ answered-flip
  discovered (needs normalization, S6). Plan presented; operator
  approved with "dale" — all recommended defaults accepted (S7).
- L3 (2026-11-26): T1 fixture written (702 bytes UTF-8 / 502 chars,
  16 lines, byte-exact slice). T2 RED: `4 failed, 227 passed` —
  exactly the 4 positive detection tests fail (detect → None on the
  v2 capture), the 4 fail-closed tests already pass, all legacy green.
- L4 (2026-11-26): T3 GREEN: implementation landed (numbered.py
  opt-in flags; claude.py `_multiselect_v2` gated on the select
  footer + full shape; `_state_normalized` erases `☐☒` and `[×]`;
  docstrings updated). C4 suites: `231 passed`.
- L5 (2026-11-26): T4: full non-browser brain suite `4865 passed,
  0 failed`. Differential (ODD): all 21 claude fixtures' payloads
  compared base `975c1ac` vs branch — only the new fixture changes.
  Committed as `eebda5f` (402+/16−, one work-unit commit).
- L6 (2026-11-26): Verdicts — S1 ✅ (detect → multiselect, all fields
  asserted), S2 ✅ (fail-closed tests + 4 suites + full suite green),
  S3 ✅ (recipe verified from the installed bundle BEFORE
  implementing; recorded in claude.py docstring), S4 ✅ (C4 command
  green), S5 ✅ (fixture + provenance row; legacy conserved),
  S6 ✅ (chip flip normalization pinned by
  test_the_fingerprint_ignores_its_own_choreography), S7 ✅ (approved
  defaults implemented as approved), S8 respected (no single-select/
  notes/tab/Chat work). T5 verifier pending with the lead.
- L7 (2026-11-26): reported done to the lead via herdr-fleet with
  diff/commit, acceptance checks and observed results; worktree
  clean.
- L8 (2026-11-26): verifier round 1 verdict NEEDS_FIX (S2 FAIL,
  S6 PARTIAL; S1/S3/S4/S5/S7/S8 PASS). T6 executed with strict TDD:
  6 new tests RED (`6 failed, 8 passed` within the class), fixes
  landed, C4 suites `237 passed`; full non-browser brain suite
  re-run green; ruff `--select F,E9` clean on numbered.py and
  claude.py (base's F401 `unread` gone; remaining findings are the
  file's pre-existing UP-style conventions). Updated S2/S6 verdicts:
  S2 ✅ (all three misclassification paths now decline or claim the
  multi correctly), S6 ✅ (`[√]`/`[×]`/`[X]` read as checked; `√`
  normalized; fingerprint invariant under the TERM=linux glyph flip).
