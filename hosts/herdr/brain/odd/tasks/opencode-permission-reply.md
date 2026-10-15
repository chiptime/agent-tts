# Feature: OpenCode permission reply from the PWA (herdr-brain)

**Repo**: `agent-tts/hosts/herdr/brain`
**Branch**: `feat/opencode-permission-reply` (worktree `agent-tts-worktrees/opencode-permission-reply`)
**Created**: 2026-10-15
**Delivery**: single PR, PWA-only endpoints, no LLM exposure

## Objective

When an agent waits at an opencode approval dialog, the phone can ANSWER it:
three buttons (permitir una vez / permitir siempre / rechazar) that reply
through opencode's own permission API, so "the app asks for action" always
comes with a way to act.

## Problem / Why

Today the brain cannot answer a dialog at all: `herdr agent prompt` refuses a
blocked agent (`agent_blocked`) BEFORE writing any input, so the typed-reply
input built earlier is hidden for a blocked pane and nothing gets delivered.
Verified: `herdr --skill` — "It rejects an agent already waiting at an
approval or question dialog with `agent_blocked` before sending any input."

User's words: "si me piden accion y no puedo ejecutarla es un problema" and
"puedo escribir si o no?".

Answer: not "sí/no". opencode's permission system replies with exactly three
values, verified in the live OpenAPI spec (`GET /doc`, opencode 1.18.32):

```
GET  /permission                   -> 200, [PermissionRequest]
POST /permission/{requestID}/reply -> {"reply":"once"|"always"|"reject"} -> boolean
```

`PermissionRequest`: `{id (^per), sessionID (^ses), permission, patterns[],
metadata{}, always[], tool{messageID, callID}}`.

## Specs

- **S1** — Brain reaches the local opencode server over HTTP. Server observed:
  `opencode web --hostname 0.0.0.0 --port 4096`, version `1.18.32`. Base URL
  is a setting, default `http://127.0.0.1:4096`.
- **S2** — Pending permissions are listed with `GET /permission` (optional
  `directory` query) and answered with
  `POST /permission/{requestID}/reply` body `{"reply": "once" | "always" | "reject"}`
  exactly as the spec defines. No key-press simulation, no `send-keys`.
- **S3** — The pane is matched to its request by `sessionID`: herdr already
  gives the brain each pane's `ses_...` id, and `PermissionRequest.sessionID`
  uses the same shape.
- **S4** — The three replies map to the user words: `once` = "Permitir una
  vez", `always` = "Permitir siempre", `reject` = "Rechazar".
- **S5** — **The LLM must never reach this.** These capabilities are HTTP
  endpoints for the PWA only; they are NOT added to `TOOLS_SCHEMA` /
  `BrainTools`. A model able to approve its own permission request would
  defeat the gate entirely.
- **S6** — `always` persists a rule, so its button is labelled explicitly
  ("Permitir siempre") and is not the primary/default action.
- **S7** — Fail-soft: an unreachable opencode server, a vanished request or a
  non-2xx reply never raises into the PWA; the user is told what happened.
- **S8** — A request that is gone (already answered elsewhere, or expired)
  is `404`-shaped, never a silent success.
- **S9** — No behaviour change for agents whose dialogs are not opencode's
  (claude/codex/agy): the affordance is opencode-only, and their blocked panes
  keep the existing notice.

## Assumptions (marked as such)

- The v1 routes (`/permission`, `/permission/{id}/reply`) are the ones a
  running 1.18.32 answers; the v2 mirrors under `/api/...` exist too and
  returned 200 at probe time. Verify against a LIVE pending request before
  calling this done.
- A real `reply` was NOT exercised at investigation time (no dialog was
  pending; triggering one would touch the user's live sessions).
- **Resolved 2026-10-15**: the route's `directory` scope is NO LONGER sent.
  It was unverified against a live request and can only remove results, so it
  risked hiding a real dialog; `session_id` is the exact filter and is applied
  in-process. Verified live: `GET /api/permission/saved` answers
  `{"data":[]}` with schema `{id, projectID, action, resource}`, so saved
  rules are real persisted entities — but whether `always` populates that
  list is still UNVERIFIED (the list was empty). A live `always` (T4) settles
  it, and that is what decides whether an undo UI would undo a phone mis-tap.

## Tasks

- [x] T1 (S1,S2,S3) — `opencode_url` setting + `OpenCodePermissions` client
      (list/reply) with an injectable transport. Tests: `tests/test_permissions.py`.
      **Done**: 15 tests green (RED seen first: ModuleNotFoundError).
- [x] T2 (S4,S5,S7,S8,S9) — PWA-only endpoints `GET /pane/permission` and
      `POST /pane/permission/reply`; assert absent from `TOOLS_SCHEMA`.
      Tests: `tests/test_server_permission.py`.
      **Done**: 12 tests green. Two defects found by running them: the route
      used `Path` for a query param (422 on every call) and the S5 test
      first asserted on prose, where an unrelated "once" in `create_session`'s
      description made it a false positive — it now asserts on tool names,
      parameter names and the exact reply enum.
- [x] T3 (S4,S6) — PWA: three buttons replace the blocked-pane notice.
      Browser test with a mocked pending permission + re-pinned digests.
      **Done**: `tests/browser/test_permission_buttons.py` (3 tests, one of
      them clicks and asserts the exact posted body). The keys notice now
      shows only when a blocked pane has NO opencode request pending —
      another agent kind, or one already answered (S9).
- [x] T4 (S2,S3,S8) — Live verification against a real pending request, on a
      session the user nominates. Evidence recorded here before merge.
      **Done, and it caught two shipping bugs** (see the Log): the client read
      the legacy `/permission` list, which does NOT expose live requests, and
      it expected a JSON boolean from a reply route that answers `204 No
      Content`. Both are fixed; the lab now round-trips a real request.
      Still worth one confirmation on a dialog a REAL agent raised, rather
      than one created through the API.

## Superseded by the Panel port (2026-10-15)

The HTTP permission API is NOT how a real dialog is answered in opencode
1.18.32: opencode runs **two coexisting permission systems** — a V1 registry
owned by the CLI/TUI and a V2 one owned by the service layer — and a real
dialog lives in the TUI's, which no HTTP route exposes (all three lists were
empty while a real dialog waited). The code below stays on the branch because
it is a correct client for V2, and it becomes useful again if that change
lands; it is NOT the shape this feature needs today.

The working reference is **Panel**, a Herdr plugin already on this machine
(`~/.config/herdr/plugins/github/herdr.panel-plugin`, bridge on
127.0.0.1:8787). It has no opencode recipe yet (its verified families are
claude, codex, omp, agy, grok; opencode is explicitly "tomorrow"), but its
MECHANISM is the thing to port:

- `web/src/lib/harness/guard.ts` — the safety skeleton, which is what this
  feature was missing:
  - **fresh read before every key**: re-read the pane and re-derive the
    dialog model; never act on the render the user tapped.
  - **the signature is the load-bearing check**, not the revision: Herdr's
    `pane.read.revision` is a stub upstream (always 0, verified live
    2026-07-05), so equality of the derived region — highlight included — is
    what refuses a keystroke aimed at a dialog that has since been replaced.
  - **two comparators**: a committing key (enter) requires strict equality;
    a navigation key (arrow) uses a weaker identity check, because a moved
    highlight is the EXPECTED result of the previous arrow, not drift.
  - **three-valued polling** (8 × 350 ms) between steps: `ok` / `drifted`
    (identity changed or dialog gone ⇒ NO further key — a blind stroke would
    hit whatever replaced it) / `timeout` (same dialog, state never came ⇒ a
    bounded retry of the same key is safe).
  - **never synthesise a digit**: `.adr/0009` — the keys sent are always the
    ones the screen's own footer named.
- `web/src/lib/harness/menu-model.ts` — the neutral model: actions carry the
  footer's own verb and the exact keys it named; the `signature` must be
  non-empty and must change when the region's text changes; `nav` is set only
  where the screen itself advertised the arrows.
- `sanitizeTypedText` — collapse whitespace, strip C0/C1 controls before
  typing into a focused TUI (ESC would cancel the dialog, BEL/ETX worse).

The opencode dialog observed live: options `Allow once / Always allow /
Reject`, footer `⇆ select   enter confirm`, and a bare `enter` opened the
**Always allow** confirmation page — so the default highlight is NOT the
safest option and a naive pad would commit the riskiest one.

### Tasks for the port

- [x] T5 — port the guard: fresh read, signature equality (commit) vs
      identity (nav), three-valued polling, sanitised text.
      **Done, in brain's own terms**: `src/herdr_brain/dialog.py` holds the
      model (`DialogModel` / `DialogAction`), the two comparators, the entry
      guard and the three-valued poll; `HerdrClient.send_keys` carries the
      verified key dialect (`DIALOG_KEYS`, refused locally before the CLI is
      spawned) and `read_screen(..., ansi=True)` keeps the escape codes the
      signature needs. Text sanitising was deliberately left out: nothing
      calls it yet, and it belongs with the reply-into-a-dialog path (T7).
      Tests: `tests/test_dialog_guard.py`, `tests/test_herdr_send_keys.py`.
- [ ] T6 — an opencode dialog adapter: parse the footer's own keys, derive
      the option rows WITH the highlight marker, expose a signature.
- [ ] T7 — closed-loop choreography (walk to the target row, re-reading each
      step) + a PWA-only endpoint; `send-keys` keys are `left`/`right`/`enter`
      (`arrowleft`/`ArrowLeft` are rejected by herdr with `invalid_key`).
- [ ] T8 — live recipe derivation with Panel's method: ONE key at a time,
      capturing the buffer, never committing until the mapping is known.

## Log

- **L1** (verbatim, 2026-10-15): "necesito añadir en brain un input para escribir respuestas"
- **L2** (verbatim): "no voy a subir nada aun, quedaria bien el añadir el input? se enviarian bien a back y llegarian a opencode po rejemplo?"
- **L3** (verbatim): "entonces si no se puede interactuar no es un visual valido no? si me piden accion y no puedo ejecutarla es un problema"
- **L4** (verbatim): "puedo escribir si o no?"
- **L5** (verbatim): "1" → chosen branch: three buttons against opencode's permission API.
- **L6** (verbatim): "worktree" → isolated worktree, never the shared `main` tree
  (another session was merging into `main` while this was investigated).
- **Investigation** (2026-10-15): `GET /permission` → `[]` HTTP 200;
  `GET /api/permission/request` → `{"location":{...},"data":[]}` HTTP 200;
  opencode 1.18.32. Security notes: the opencode server binds `0.0.0.0:4096`
  and these routes carry no auth in the spec — pre-existing exposure, reported
  to the user, not changed here.
- **T1+T2 evidence** (2026-10-15): `tests/test_permissions.py` 15 passed;
  `tests/test_server_permission.py` 12 passed. Full non-e2e suite: 1815 passed
  in the worktree venv + `tests/browser` 151 passed (run with the main repo
  venv: this worktree's playwright 1.64 wants chromium 1248 and the machine
  has 1243, an environment gap, not a code failure) + `node --test tests/js`
  383 passed. No commit in `main`; work stays on `feat/opencode-permission-reply`.
- **T3 evidence** (2026-10-15): browser suite 154 passed, 0 errors; JS 383
  passed. The harness's "Undeclared traffic was blocked" guard caught that
  `test_pane_reply_visibility.py` had no `/pane/permission` route declared
  once the blocked pane started asking for its dialog — a teardown error
  from an incomplete mock, not a gating bug. Visual check at a 390px
  viewport: the three buttons render under the pending banner; "Permitir
  una vez" takes its own row because three do not fit the phone width.
- **T4 forcing experiment** (2026-10-15) — created a throwaway opencode
  session with a ruleset forcing `ask` for edits, then created a real request
  with `POST /api/session/{sid}/permission` (`{"action":"edit","resources":
  [...]}` → `{"id":"per_…","effect":"ask"}`).
  - **BUG 1 (would have shipped)**: `GET /permission` (the legacy list this
    client used) returned `[]` while the request was live; the v2 routes
    (`/api/session/{sid}/permission`, `/api/permission/request`) listed it.
    The buttons would never have appeared for a real dialog.
  - **BUG 2 (would have shipped)**: `POST …/reply` answers **204 No Content**.
    The client parsed a JSON body and returned `bool(None)`, so a perfectly
    good answer would have reported `{"ok": false}`.
  - **Fix**: the client is v2-only, session-scoped (mapping opencode's
    `action`/`resources` onto the phone's permission/patterns), success is the
    2xx status, and `POST /pane/permission/reply` now takes `pane_id` because
    opencode's reply URL is session-scoped.
  - **After the fix, live PASS**: pending seen (`per_1400b4a44001…`),
    `reply(reject)` → True, and the session's pending list empty afterwards.
  - Lab sessions were deleted with `DELETE /session/{id}`.
  - Still unconfirmed by a live agent: that a dialog an AGENT raises (rather
    than one created through the API) lands in the same registry. The API used
    here is the one its own permission check calls, so it should — but that is
    inference, not observation.

- **T4 live evidence, part 1** (2026-10-15, opencode 1.18.32, real server).
  Recorded before the forcing experiment below rewrote the client to v2; the
  legacy route findings here are what BUG 1 above disproves as a read path.
  The reply body gained `pane_id` afterwards.
  - `POST /permission/per_nonexistent/reply {"reply":"once"}` → **404**
    `PermissionNotFoundError` — the route is live AND the body shape is
    accepted, so a 404-not-found (not a 400-invalid-payload) confirms the
    contract.
  - Same call with `{"reply":"yes"}` → **400** `Expected "once" | "always" |
    "reject", got "yes"`. opencode enforces the exact enum, independently
    confirming the three-button design.
  - Brain started from this worktree on a spare port, with its own audio dir
    and STT warmup off, against the real herdr and the real opencode server:
    - `GET /pane/permission?pane_id=wM:pA` (a real opencode pane) →
      `{"pending": null}` **200** — pane resolved by herdr, opencode session
      matched, real opencode queried.
    - Unknown pane → **404** `pane no encontrado`, never the focused pane.
    - `POST /pane/permission/reply {"request_id":"per_nonexistent","reply":"once"}`
      → **404** with opencode's own message propagated — the write path runs
      end to end.
    - `{"reply":"yes"}` → **422** from the edge validator: an invented answer
      never reaches opencode.
  - Still unverified: answering a request that is actually PENDING. That is
    the only remaining step and it needs a dialog waiting on purpose.
- **e2e attribution** (2026-10-15): the full e2e run reported 4 failed /
  28 passed. NOT caused by this work: the same tests fail identically on a
  detached worktree of the branch base (`7bd7c47`, no permission commits).
  Reproduced in isolation on both trees —
  `test_m4_fallback::test_cancel_during_fallback_zero_further_attempts`
  ("submit evidence never satisfied … : []"),
  `test_m4_fallback::test_no_duplicate_submissions` (45s page timeout) and
  `test_m3_pending::test_kill9_host_pending_recovers_uncertain_no_replay`
  ("the tick exited before the claim could be observed", rc=1). The host-side
  kill9 test is a subprocess/daemon-socket race and the other two wait on
  /ask submits, so this is baseline flakiness on this machine, not a
  regression — but it is unverified against a quiet machine.
