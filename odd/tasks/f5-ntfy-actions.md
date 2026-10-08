# F5 — HT-04 refocused: ntfy action buttons over the Herdr Brain approval API

**Status**: IN PROGRESS · **Branch**: `feat/f5-ntfy-actions` (worktree `~/Code/personal/agent-tts-worktrees/f5-ntfy`, base `main` = `a12e727`)
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
- [ ] T3 RED/GREEN: bash cases for the `Actions` header (blocked + gate, no URL, no gate, non-blocked, unsafe token)
- [ ] T4 GREEN: `send_ntfy_push` Actions builder + gate lookup helper
- [ ] T5 Docs: HT-04 PRD, brain PRD, BLOQUE-3, ROADMAP F5 status
- [ ] T6 Verification run + handoff

## Evidence

- T1/T2 (brain): `uv run python -m pytest tests/test_approval_ntfy.py -q` → 31 passed (worktree);
  same file on clean base export → 30 failed / 1 passed (RED observed 2026-10-08).
  ntfy `http` action grammar verified against docs.ntfy.sh (publish §action-buttons):
  `http, <label>, <url>[, method=POST][, headers.X=Y][, body=...][, clear=true]`, `;` between
  actions, max 3 actions per message, values with `,`/`;` quoted with `"`.

## Commits

Recorded by the writer at close (this brief authorizes work-unit commits on
`feat/f5-ntfy-actions`): see list below.
