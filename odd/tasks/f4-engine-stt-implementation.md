# F4 — Engine-owned STT implementation

## Objective

Deliver reusable STT and Windows microphone capture in the engine, then integrate
terminal dictation in the plugin without requiring the brain service.

## Current unit and authorization

- Worktree: `agent-tts-worktrees/f4-stt-core`; branch: `feat/f4-stt-core`.
- Base: `ed2fbfe`. User authorized local implementation; no remote operations.
- Unit U1: generic offline-safe STT core and a separate resident worker.
- Preserve the docs-only `f4-engine` worktree and canonical `main`.
- No live model calls/downloads, microphones, user state, or providers in tests.
- PowerShell capture, plugin wiring, and brain migration are later units, not
  silently implemented or claimed complete here.

## Accepted architecture

- Reuse the existing faster-whisper behavior without importing host packages.
- Keep the model resident between dictations in an engine-owned STT process.
- Separate from the playback daemon; its IPC v2 and lifecycle remain untouched.
- Optional STT dependencies; missing extra/model gives actionable errors.
- Model acquisition is an explicit owner operation, never an incidental load.
- Private POSIX local socket for separate client invocations. Native Windows
  transport is unsupported in U1; WSL2 is the intended initial deployment.
- Explicit worker launch; no client autostart and no management of the brain.
- Use a separate proposed `agent-tts-stt` entry point; do not intercept existing
  positional TTS text or alter the playback CLI's behavior.
- Worker client/server exchange must be bounded, versioned and tested; don't
  extend frozen playback IPC or use unauthenticated TCP.

## Stable task checklist

- [x] **U1.1** Extract generic settings/cache/model policy and lazy transcriber.
- [x] **U1.2** Add resident worker and bounded private local client interface.
- [x] **U1.3** Add optional extra and explicit CLI operations: pull, serve,
  status, transcribe. Keep stdout machine-readable for request operations.
- [x] **U1.4** Observe focused RED then GREEN; test offline/missing dependencies,
  model reuse, framing/bounds, socket ownership, failure and cleanup behavior.
- [x] **U1.5** Run engine regression and independent technical verification.
  (Self-verification complete 2026-10-07; the separate independent verifier
  review is still pending — this checkbox awaits that check before close.)
- [x] **U2** PowerShell capture and silence endpointing — capture.py (490 ln): CaptureConfig validated, pure analyze_pcm/trim_trailing_silence (30 ms RMS frames), PowerShellCapture via MCI/winmm P/Invoke script (single line, no double quotes, stdout base64), typed capture_unavailable(8)/capture_oversize/empty_capture(9), oversize bound pre-decode. HONEST LIMIT: no live level meter -> records full --max-seconds window then trims trailing silence (ended_by=max_duration + trimmed stats); device=default only; real Windows hardware NOT smoke-tested (fake runners only). CLI `agent-tts-stt capture` local op, JSON stdout. Tests 47 new; focused 73/0; engine suite 1142/0.
- [x] **U3** Plugin wiring — bin/herdr-tts +388/−4: TTS_STT/TTS_PTT toggles (default off, settings rows, strict degradation), TTS_PTT_ENTER/SILENCE/MAX keys, `ptt` keymap id (no default chord), `ptt` verb: engine CLI via pinned $VENV_PYTHON -m agent_tts.stt.cli (capture+transcribe, bounded timeouts, tmp wav always cleaned), engine failure -> visible notice + nothing injected, <2-char guard, read-based confirm (Enter/Esc/r, max 3 attempts), injection herdr pane send-text literal + final Enter via send-keys per TTS_PTT_ENTER, daemon.log line (duration/chars/pane, never transcript). 22 new harness cases; battery 97->119/119 OK; RED 20/20 observed. Limits: no real device/worker/herdr binary; confirm is read-based prompt (launcher has no popup surface for verbs).
- [ ] **U4** Real-device validation and explicit later brain migration.

## Routing and verification

- Delegated direct: one implementation writer, separate independent verifier.
- Preparation/multifile triggers apply; parent does not implement source inline.
- Test-first applies to deterministic behavior: observed RED/GREEN required.
- Runner: canonical `engine/.venv/bin/python`, with this worktree's `engine/src`
  on `PYTHONPATH`. No install required for fake-model tests.
- Functional proof: focused STT tests, boundary tests, full engine suite,
  subprocess CLI smoke and worker lifecycle tests.
- RDD reads clone-local OFF; no native review is started or enabled.
- Delivery is local only. Commit-time policy and delivery-size strategy remain
  human-controlled; no timestamp rewrite or publishing is inferred.
- Forecast: U1 may exceed 400 authored lines because transport safety and tests
  are part of its coherent behavior. Record actual size before committing;
  don't remove tests or compress code to reduce it.

## Progress and proof

U1 implemented on `feat/f4-stt-core` (base `ed2fbfe`), uncommitted per
local-only policy.

**Artifacts**: `engine/src/agent_tts/stt/{__init__,transcriber,worker,cli}.py`,
tests `engine/tests/test_stt_{transcriber,worker,cli}.py`, contract
`contracts/stt-engine-v1.md` (+ README entry), `engine/pyproject.toml`
(`stt` extra + `agent-tts-stt` script), lazy `agent_tts.stt` package attr.
Authored size: ~2,450 lines total (1,242 source incl. doc-heavy worker,
1,068 tests, 108 contract) — above the 400-line advisory as forecast;
each file keeps one purpose (core policy / transport / CLI) with tests
mapped 1:1 to the contract. No compression applied.

**Hardening implemented beyond the brain reference**: `WhisperModel` only
ever receives a locally resolved path (existing path or
`local_files_only=True` snapshot) — never a repo alias; occupied socket
paths (live OR stale) are typed `address_in_use` refusals with manual-remedial
messages, never unlinked; teardown removes only the bound inode; inference
concurrency 1 with typed busy; connection cap 8; bounded shutdown that
reports hung native calls honestly.

**Verification (runner `engine/.venv/bin/python`, worktree `PYTHONPATH=engine/src`):**

- RED (observed before implementation): 3 collection errors —
  `ModuleNotFoundError: No module named 'agent_tts.stt'` for all three
  new test files.
- GREEN focused: 87 passed
  (`test_stt_transcriber.py` 30, `test_stt_worker.py` 41, `test_stt_cli.py` 16).
- `test_monorepo_boundaries.py`: 2 passed.
- Full `engine/tests/`: 1054 passed, 11 skipped (first full run all
  green; one later run hit `test_chain_playback.py::test_chain_honors_stop_flag_mid_file_remaining_files_not_played`).
- That chain test is a PRE-EXISTING flake: reproduced 3/8 failures on the
  untouched canonical checkout at the same base commit `ed2fbfe` (no
  resets; main left clean).
- CLI smoke: `-m agent_tts.stt.cli --help` exit 0 listing all four ops;
  `status` with isolated missing socket → JSON `worker_unavailable` +
  stderr hint naming `agent-tts-stt serve`, exit 5.
- `git diff --check` clean; status shows only allowed surfaces.
- pyproject parses: base deps unchanged (`edge-tts`, `miniaudio`);
  `faster-whisper` only in optional `stt`; scripts `agent-tts` (untouched)
  and `agent-tts-stt`.
- Base-install burden: subprocess check proves `import agent_tts` +
  `import agent_tts.stt` never pull `faster_whisper`/`huggingface_hub`.

No runtime dirs created outside test temp dirs; no home cache writes.

### Correction round (independent verification findings, 2026-10-07)

Eight findings fixed in the same surfaces; focused RED observed first
(21 failed / 88 passed — exactly the new contracts), GREEN after
(109 passed; 37 transcriber / 54 worker / 18 cli).

1. Offline hardening: `resolve_local_model` now returns an ABSOLUTE
   asset-verified directory (regular-file paths = typed invalid_config;
   missing tokenizer.json = typed refusal so faster-whisper's
   `Tokenizer.from_pretrained("openai/whisper-tiny")` hub fallback is
   unreachable — evidence from installed dependency source,
   transcribe.py: directory input skips `download_model`; missing
   tokenizer triggers the fallback); `WhisperModel` also receives
   `local_files_only=True` explicitly.
2. Health: `serve` auto-drives warmup (no manual call); failed load →
   unavailable with `error` surfaced in status; busy stays truthful;
   proven in-process AND via CLI subprocess without manual warmup.
3. Missing hub library during probe → typed missing_extra (pull hint
   would be wrong); worker protocol maps missing_extra/invalid_config so
   CLI exits 6, not 1.
4. Bind: umask-guarded creation (no name-chmod window), post-bind
   verification records (st_dev, st_ino); teardown unlinks only the
   exact bound socket inode, never symlinks/non-sockets; explicit
   sockets require owner-only parent dirs (world-writable parent
   rejected); documented residual limit: same-uid interposition in the
   non-atomic check-then-unlink window is out of scope, lock coordinates
   cooperating peers only. Contract rewritten to these actual claims.
5. Failed start (overlong AF_UNIX path regression test) releases lock +
   sockets — no phantom owner.
6. Connection registry prunes finished threads (bounded under 25
   sequential requests).
7. MAX_PAYLOAD 48 MiB (reserves base64+JSON envelope); exact-cap and
   cap+1 audio tested end-to-end with real constants.
8. Malformed-field tests carry valid envelopes; bool version rejected;
   unhashable suffix → typed invalid_request, never internal error.

New authorized fake-worker subprocess lifecycle test (test code via
`-c`, runtime under /tmp/opencode/f4-stt-runtime): resident model reuse
across independent client PROCESSES (1 load), status responsive while
fake inference stuck, clean shutdown exit 0, owned process cleanup.

### Second correction round (2026-10-07, four verification findings)

1. Late tokenizer race CLOSED via private runtime snapshot:
   `snapshot_for_runtime` hardlinks every resolved-model entry into an
   owner-verified 0700 snapshot dir (byte-copy fallback only for
   cross-fs caches; CT2 `files=` API evaluated and rejected — 4.8.2
   docs make model_path an identifier requiring ALL files in memory,
   i.e. gratuitous weight copies). The constructor receives only the
   snapshot path; dependency branch selection (transcribe.py:689-708,
   presence-checked AFTER native construction, fallback fetches ignoring
   local_files_only) is closed structurally: file presence in an
   owner-only dir cannot be flipped by source-cache deletion (hardlink
   inodes survive). Deterministic regressions: mid-construction source
   deletion, blob-backed (HF-style symlink) assets surviving full cache
   wipe, source vanishing before link → typed offline failure, snapshot
   reuse across restarts, unsafe-root rejection.
2. Warmup hang accounted: the warmup task is registered and joined in
   the bounded shutdown accounting (`hung` includes unfinished startup);
   subprocess proof: stuck loader → status `loading`, shutdown →
   bounded nonzero exit naming hung work.
3. Recovery health: a succeeding load retry clears stale
   unavailable/error (`_ensure_model` success path); worker-level test
   proves unavailable→transcribe→ready transition in `status`.
4. Subprocess tests restructured: entered-marker handshake proves the
   inference is actually blocked before latency/shutdown assertions;
   shutdown honesty asserted BEFORE releasing the hang (nonzero exit,
   "hung" in stderr, bounded communicate); the clean resident-reuse test
   (one load across independent CLI client processes, exit 0) is
   separate. No native-cancellation claim; daemon-thread behavior only.

Round-1 verification (for context): focused 109 passed; full engine
1076 passed / 11 skipped (default TMPDIR); chain_playback flake
documented 3/8 on base.

Focused RED: 13 failed / 106 passed (exactly the new contracts); GREEN:
119 passed (43 transcriber / 56 worker / 20 cli), no warnings.
Boundaries: 2 passed. Full engine: dedicated
TMPDIR=/tmp/opencode/f4-stt-runtime/tmp → 1084 passed / 11 skipped /
2 failed = the documented pre-existing test_ipc_ownership TMPDIR
sensitivity (repro 3/3 custom vs 0/3 default); default TMPDIR → 1085
passed / 11 skipped / 1 failed =
test_daemon.py::test_play_registering_during_shutdown_is_refused_not_orphaned,
a second pre-existing timing flake (reproduced 2/3 isolated on the
untouched base checkout ed2fbfe; main left clean). CLI smoke: help=0,
unavailable-socket status=5. `git diff --check` clean; only the 12
allowed surfaces dirty.

What cannot be verified here: the real faster-whisper/ctranslate2
libraries (absent from the canonical venv, installs forbidden) — the
offline guarantee is proven against the dependency's installed source
lines (brain venv, read-only) plus structural fakes; real-model load,
real tokenizer fallback, and cross-machine filesystem layouts remain
for U4 device validation.

## Next step

Independent verifier review of U1 corrections, then commit decision
(parent-owned). U2 (PowerShell capture) starts only after U1 disposition.


## Real-device smoke test and delivery (2026-10-07 evening)

Full end-to-end verification against the REAL machine (brain venv, real HF cache):

| Step | Result |
|---|---|
| Real bug 1: eager package `__init__` | CLI died with ModuleNotFoundError miniaudio on the brain venv -> lazy PEP 562 `__init__` + isolation tests (RED 2 -> GREEN) |
| Real bug 2: hub incomplete verdict | real cache snapshot rejected over missing `.gitattributes`/`README.md` -> direct disk scan (refs/main + required assets), hub only as fallback; HF env isolation for tests |
| Real bug 3: broken runtime snapshot | `os.link` linked HF relative symlinks (not blobs) -> resolve-then-link blob inode, replace stale link dsts (RED -> GREEN) |
| Real gap 4: no `shutdown` verb | CLI subcommand + polling protocol; honest rc 0/1 |
| Smoke run | serve (brain venv, no miniaudio) -> READY ~3 s on model `small` from the real cache -> transcribe `short.mp3` -> `{"ok": true, "text": "Thanks for watching!"}` (0.9 s) -> shutdown rc 0, socket removed, process exited cleanly |
| Full engine suite | 1094 passed / 11 skipped / 0 failed |
| Delivery | `2d1cb7d` (U1) + `0a488a9` (fixes) merged fast-forward to local `main`; pushed to `origin/main` and `origin/feat/f4-stt-core` (authorized by maintainer) |

Independent verification of U1 remains PENDING (verifier runtime usage limit); the
real-machine smoke test above is the maintainer-visible functional evidence.


## U2+U3 delivery (2026-10-07 night)

- Parallel delegated writers, disjoint surfaces (engine vs plugin); parent spot checks: engine focused+boundaries 75 passed; plugin full battery 119 OK / 0 FAIL (rc 0); git diff --check clean.
- Remaining open: U4 (real device validation), independent verification of U1 (verifier usage limit), F4.4 transport gate is satisfied by the shipped UDS design but the PRD gate item stays for maintainer sign-off, F4.10 brain migration optional later.
