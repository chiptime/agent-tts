# F5 — HT-04 refocused: ntfy action buttons over the Herdr Brain approval API

**Status**: DONE (code + tests + docs; physical phone validation pending = F2) · **Branch**: `feat/f5-ntfy-actions` (worktree `~/Code/personal/agent-tts-worktrees/f5-ntfy`, base `main` = `a12e727`)
**Engram topic**: `odd/f5-ntfy-actions/tasks` (project `agent-tts`)

## Intent

Let the operator approve or stop a pending Brain approval gate straight from the ntfy
notification on the phone, with no second HTTP listener on the host. ntfy `http` actions
call the Brain's REST API directly (ROADMAP D1c / F5, PRD-HT-04 refocused 2026-10-07).

Out of scope (unchanged): free-text replies, a host-side listener, physical validation on
a phone (ROADMAP F2 remains pending).

## Verified contract (brief assumptions vs. real code)

| Brief assumption | Real code | Decision |
|---|---|---|
| `POST /approval/{gate_id}/resolve` takes `approve`/`reject` | `/resolve` takes `{utterance}` and runs the voice lexicon; approve/reject live at `POST /approval/{id}/approve` and `/reject` (no body) | Add a dedicated `POST /approval/{gate_id}/action` (ntfy-shaped: decision via raw text, JSON, form or `?decision=`), reusing the same store and replay path. Existing routes untouched |
| Brain has an approval secret | No auth anywhere in the Brain today | New optional `HERDR_BRAIN_APPROVAL_TOKEN`; empty = no auth (current behaviour) |
| Plugin knows a gate id | Gate ids exist only inside the Brain (`/ask` freezes a gate); the watcher's `blocked` event has none | `send_ntfy_push` resolves the live gate with `GET {local brain}/approval/current` (best effort) or takes it as an optional 7th arg |
| "web URL" is the Brain | `WEB_URL` may be Collie / a `{pane_id}` template | New `NTFY_APPROVAL_URL` (phone-reachable Brain base URL); buttons only when it is set |
| Up to N buttons | ntfy accepts at most 3 actions per message (extra actions make the push invalid) | With a gate the header is exactly Approve / Stop / Open; the old view/copy pair is dropped for that push |

## Checklist

- [x] T0 Read ROADMAP D1c/F5, PRD-HT-04, PRD action-approval-gate, `approval.py`, `server.py`, `send_ntfy_push`
- [x] T1 RED: brain tests `test_approval_ntfy.py` (approve/reject, token matrix, unknown/resolved gate, unblock) — 30 failed / 1 passed on a clean base export (`git archive HEAD`), 31 passed in the worktree
- [x] T2 GREEN: `config.py` token setting + `server.py` `/approval/{id}/action` (constant-time compare, never logged)
- [x] T3 RED/GREEN: bash cases for the `Actions` header (blocked + gate, no URL, no gate, non-blocked, unsafe token) — RED: 3 new-behavior cases FAIL at base (4 degradation guards already hold); GREEN: 7/7 OK after the builder
- [x] T4 GREEN: `send_ntfy_push` Actions builder + gate lookup helper
- [x] T5 Docs: HT-04 PRD, brain PRD, BLOQUE-3, ROADMAP F5 status
- [x] T6 Verification run + handoff

## Evidence

- T1/T2 (brain): `uv run python -m pytest tests/test_approval_ntfy.py -q` → 31 passed (worktree);
  same file on clean base export → 30 failed / 1 passed (RED observed 2026-10-08).
  ntfy `http` action grammar verified against docs.ntfy.sh (publish §action-buttons):
  `http, <label>, <url>[, method=POST][, headers.X=Y][, body=...][, clear=true]`, `;` between
  actions, max 3 actions per message, values with `,`/`;` quoted with `"`.
- T3/T4 (plugin): `CASES="ntfy_*" bash tests/host_cli_cases.sh` → RED 3 FAIL + 4 OK at base,
  GREEN 7/7 exit 0 after `send_ntfy_push` gate branch; full `tests/all_bash_harnesses.sh`
  → exit 0, 126 cases OK, no FAIL (regression-free in the shared TT/env/template regions).
- T6 verification (2026-10-08, all in the worktree):
  - `cd hosts/herdr/brain && uv run python -m pytest tests/ -q --ignore=tests/browser` →
    7 failed, 1437 passed. All 7 pre-existing environmental, each reproduced identically on
    a clean `git archive HEAD` base export: 6× `tests/e2e` `[chromium]` playwright failures
    (test_m2_stream ×3, test_m4_fallback ×3 — same class as the ignored `tests/browser`),
    1× `test_m3_pending.py::test_kill9_host_pending_recovers_uncertain_no_replay`
    (`the tick exited before the claim could be observed: rc=1`, needs engine venv).
  - `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` → exit 0, 126 CASE OK, no FAIL.
  - `PYTHONPATH=engine/src uv run --with pytest pytest hosts/herdr/tts-plugin/tests -q` →
    7 failed (the named `test_segmented_render.py` miniaudio failures), 132 passed, 1 skipped.
  - Curl demonstration (real uvicorn socket, real curl, ntfy-shaped POST): bad Bearer → 401
    with gate untouched; `curl -X POST -H "Authorization: Bearer …" -d approve
    http://127.0.0.1:18399/approval/{id}/action` → 200 approved; background replay dispatched
    the exact frozen args (`send_to_session`, `corre los tests`, 300000 ms) exactly once;
    second press → 404 with no second dispatch; `/approval/current` → `{"approval": null}`
    after resolution. Script: `/tmp/opencode/f5-ntfy-demo.py` (ephemeral, not a repo artifact).

## Commits

- `2ef657b` feat(brain): accept ntfy action resolve requests
- `13ff28c` feat(tts-plugin): add approve/stop ntfy action buttons
- `<this commit>` docs(roadmap): mark F5 ntfy buttons implemented pending physical validation

## Notes / risks

- Merge surface in `bin/herdr-tts` is small and localized: DEFAULT_* block (+4 lines), env
  resolution (+2), config template (+1), TT_EN/TT_ES ntfy keys (+6), and `send_ntfy_push`
  plus its three new helpers above it. The ht03-radio branch touches other regions; the
  only plausible friction is nearby-line context in the DEFAULT/env blocks.
- Gate pairing is best-effort and DEFAULT-session only: a PWA session other than the default
  is invisible to the lookup (push degrades to classic buttons). The 7th-arg seam exists
  for future callers that know the gate id.
