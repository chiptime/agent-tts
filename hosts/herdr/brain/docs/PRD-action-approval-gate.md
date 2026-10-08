# PRD — Action Approval Gate

| | |
|---|---|
| **Product** | herdr-brain (voice-assistant PWA) |
| **Status** | Implementada con tests (Implementada en `server.py` `/approval/*`, `approval.py`, con tests en `tests/test_approval.py`, `tests/test_approval_gate_create.py`, `tests/js/approval.test.js`); ruta ntfy `POST /approval/{id}/action` + token opcional `HERDR_BRAIN_APPROVAL_TOKEN` añadidos en F5/HT-04 (`tests/test_approval_ntfy.py`); validación Chrome Android pendiente (ROADMAP F2) |
| **Date** | 2026-09-24 |
| **Effort** | Implementación servidor + UI entregada; cierre manual pendiente |

> **Estado reconciliado (2026-10-05)**: `/approval/current`, approve/reject/resolve y PATCH están implementados en `src/herdr_brain/server.py`, con `tests/test_approval.py`, tests de rutas y JS. Referencias del monorepo: `2abb17e` (endpoints), `22e2fc2` (popup flotante, fuera del drawer). El cuerpo conserva el diseño original, incluida la ubicación inicial de la tarjeta. No se declara validación física: T8 sigue abierto para la repetición de pruebas en Chrome Android (ROADMAP F2).

---

## 1. Background & Problem

During a voice call, the brain's only mutating tool (`send_to_session`,
`tools.py:301`) executes **immediately server-side with no confirmation**.
The tool loop in `BrainLLM.ask` (`llm.py`) dispatches whatever the model
decides the moment it decides it.

The input to that decision is an STT transcription, and STT mishears. When
the user dictates new work for an agent, two failure modes reach production
with zero friction:

1. **Misheard intent** — the user asked a question; the brain forwards work
   to the agent instead (or vice versa).
2. **Misheard content** — right intent, garbled message text; the agent
   receives a prompt the user never said.

The existing honesty guardrails in `SYSTEM_PROMPT` cover reading agent
pending prompts before answering them ("dile que sí" flow), but nothing
stands between the brain's own decision to send and the send itself.

Constraint discovered during contract design: `send_to_session` **blocks
until the agent finishes** (`herdr.send_prompt(pane_id, text, timeout_ms)`)
and returns a completion status plus output excerpt. The gate must freeze
the **exact dispatch arguments** (`text`, `timeout_ms`, resolved target),
not just the prompt text, so an approval replays the identical call.

## 2. Approved Design Notes

### D1 — Hard gate at the tool boundary, never prompt engineering

Asking the model (via system prompt) to confirm before sending is
**unreliable by construction**: the model can skip the request, and it will
fail precisely on the day the transcription fails — the correlated failure
is the whole point of the gate.

The gate is a **structural interception**: when the tool loop resolves a
`send_to_session` call, the server does NOT execute it. It freezes the call
into a `proposed_action` and returns it to the client. Execution happens
only through the approval endpoint, which re-enters the tool loop with the
frozen arguments.

Scope consequence: `send_to_session` is the **only** mutating tool of the
four (`get_status`, `read_transcript`, `read_screen` are read-only), so one
boundary covers every write path — including the existing "dile que sí"
pending-prompt flow, which also routes through `send_to_session`. Read-only
tools are never gated: they answer questions constantly and gating them
would destroy call latency for zero risk reduction.

### D2 — Binary voice confirmation, with spoken echo

The approval question is binary ("¿Se envía?") — mishearing a yes/no has a
bounded cost and can itself be re-confirmed, unlike the free-form original
request. Before asking, the brain **speaks the proposed action aloud**
(target agent + prompt text): eyes-free verification for the primary
hands-free use case (washing dishes, phone in pocket).

The proposed action is also **always rendered as a visual card** in the call
drawer, so the user can silently read what the transcription actually
understood — the visible ground truth that voice alone cannot provide.

## 3. Product Decisions (user-approved)

1. **Approval surface: visual modal + both channels.** The pending action
   shows as a modal/card in the call drawer (drawer v2 anatomy); approval
   works by **voice ("sí" / "no") and by UI buttons**. Neither channel is
   privileged: voice for hands-busy, tap for quick confirm.
2. **The proposed prompt is editable before send.** When the frozen action
   is a prompt for the agent, the user can fix it:
   - **Manual edit** — editable text field in the modal; saving re-enters
     confirmation.
   - **Voice re-dictation** — a "re-dictar" affordance makes the next
     utterance REPLACE the proposed prompt text (not a yes/no), then
     re-enters confirmation.

## 4. Proposed Flow

```
user utterance → STT → /ask → tool loop
    └─ model calls send_to_session(text, timeout_ms)
         └─ GATE: server freezes ApprovalGate{gate_id, action args}
            → /ask responds with approval{} (NO execution)
              → drawer card + TTS echo + "¿Se envía?"
                client callState → "confirming" (mic routes to gate, not /ask)
                   ├─ "sí…" / [✓ Enviar]   → POST approve → frozen call replays
                   │                          → tool loop continues on result
                   │                          → report answer + audio (same shape as /ask)
                   ├─ "no…" / [✕ Cancelar] → POST reject → gate rejected
                   │                          → callState back to "listening"
                   ├─ [✏️ Editar]           → PATCH text → re-confirm (timer restarts)
                   └─ [🎙 Re-dictar] / "cambia el texto"
                                           → one dictation round → PATCH text → re-confirm
```

Failsafe rules (always resolve toward the safe direction — cancel):

- Ambiguous utterance while confirming → one spoken re-prompt ("¿Sí o no?");
  a second consecutive ambiguous utterance auto-rejects.
- Expiry auto-rejects (§6). Reload mid-gate re-recovers the live gate (§5).
- A new `/ask` while a gate is live marks the old gate `superseded` and may
  open a new one — never two live gates per session.

## 5. API & State Contracts

### Server-side gate store (in-memory, mirrors `ConversationStore` patterns)

```python
ApprovalGate = {
  "gate_id":    str,          # uuid4 hex, short form
  "session_id": str,          # conversation session (ConversationStore keying)
  "state":      str,          # proposed → approved | rejected | expired | superseded
  "created_at": float,        # epoch seconds; reset on text revision
  "tool":       "send_to_session",
  "action": {                 # FROZEN exact dispatch arguments
    "text":       str,
    "timeout_ms": int | None, # as the model passed it
    "pane_id":    str | None, # resolved target at freeze time
    "agent":      str | None, # agent name for display/echo
  },
  "reprompt_count": int,      # ambiguous-utterance budget (max 1)
}
```

One live gate per `session_id`; guarded by a lock like the conversation
ring. Terminal states are final; `approved` executes exactly once
(idempotency guard on `gate_id`). Server restart drops gates (no
persistence, same as conversation memory).

### Endpoints

| Endpoint | Body | Returns | Notes |
|---|---|---|---|
| `POST /ask` | unchanged | `approval` field added | `approval: null` when no gate opened |
| `POST /approval/{gate_id}/approve` | — | same shape as `/ask` result | Replays frozen `send_to_session`, re-enters tool loop, returns report answer + `audio_url`. Can be slow (agent completion) — same 30s client budget. |
| `POST /approval/{gate_id}/reject` | — | `{ok, state}` | No TTS generated server-side. |
| `POST /approval/{gate_id}/resolve` | `{utterance}` | `{decision, answer?, audio_url?}` | Voice path. `decision ∈ approve / reject / listen_replace / reprompt`. STT transcript is matched server-side (§ voice lexicon). |
| `POST /approval/{gate_id}/action` | `approve` \| `reject` (raw word, JSON `{"decision": ...}`, form, or `?decision=`) | `{ok, decision, state}` | ntfy `http` action buttons (HT-04/F5). Auth via `Authorization: Bearer` or `?token=` against `HERDR_BRAIN_APPROVAL_TOKEN` (constant-time; 401 before any gate lookup when configured, empty = open like the PWA routes). Approve replays the frozen send in the background (the phone's HTTP window is short) and renders no TTS. Dead/unknown gates 404 like the PWA routes. |
| `PATCH /approval/{gate_id}` | `{text}` | updated gate | Manual edit OR voice re-dictation result. Resets `created_at` (timer restarts). |
| `GET /approval/current` | query `session_id` | gate or `null` | Reload recovery: PWA boot checks and re-enters `confirming` if a live gate exists (drawer PRD's reload-interrupted state). Also the best-effort lookup the ntfy push uses to find the default session's live gate. |

### `/ask` response extension

```json
{
  "answer": "…", "audio_url": "…", "pane_id": "…", "agent": "…", "session_id": "…",
  "approval": {
    "gate_id": "a1b2c3d4e5f6",
    "tool": "send_to_session",
    "pane_id": "3", "agent": "opencode",
    "text": "arregla el bug del login… (FULL text)",
    "timeout_ms": 300000,
    "expires_in_s": 60
  }
}
```

The card renders `approval` verbatim (full-text ground truth). The client
computes its own countdown from `expires_in_s`.

### State machines

- **Server gate:** `proposed → approved | rejected | expired | superseded`.
- **Client `callState`:** adds `"confirming"` to the existing
  `idle / listening / recording / transcribing / thinking / paused`. In
  `confirming`, STT results route to `/approval/{id}/resolve` (never
  `/ask`); PILL_TEXT gains a `confirming` entry ("Confirmar ▲"); after
  resolution the call returns to `listening` (approve also passes through
  `thinking` while the replayed call runs).

### Voice resolve lexicon (server-side matching, es + en)

- **approve:** sí, si, confirmo, vale, dale, adelante, hazlo, envíalo,
  mándalo, yes, confirm, go ahead
- **reject:** no, cancela, cancelar, para, stop, espera, no lo envíes
- **replace-intent:** cambia, cambia el texto, re-dicta, redicta, otro
  texto, mejor dile → `listen_replace`: client runs ONE dictation round,
  then PATCHes the text and re-enters confirmation.
- **Matching rule:** trimmed-lowercase utterance equals a keyword, or starts
  with one while staying short (≤ 24 chars) — protects "no" appearing
  inside longer sentences. "No sé" resolves to reject (safe direction).
- **Ambiguous/unknown:** first occurrence → `reprompt` (spoken "¿Sí o no?",
  card stays); second consecutive → reject.

## 6. Timing & Expiry

- **Window:** 60 s default, configurable via
  `HERDR_BRAIN_APPROVAL_TIMEOUT_S` (Settings field; tests override). Chosen
  against the echo length (≤ 3 sentences ≈ 15 s) plus reaction time with
  soapy hands.
- **Mechanism:** lazy server-side check on every gate touch
  (resolve/approve/patch/new `/ask`) + client countdown on the card. No
  background timer thread.
- **UX on expiry:** gate → `expired`; card grays out ("Expirada"), buttons
  disabled; a system-styled drawer turn notes "⏱ Acción cancelada por
  tiempo"; pill/callState return to `listening`. **No spoken line** — if
  60 s passed silently the user is not attending; TTS nagging would be
  noise.

## 7. Spoken Echo

- The model composes the echo under the existing ≤ 3-sentence answer style:
  state target + what will be sent; for prompts longer than one breath,
  summarize in a few words and point to the screen (the card carries full
  text — no server-side echo truncation machinery).
- The **deterministic closer** is appended server-side whenever a gate
  opens: `answer + " " + "¿Se envía?"` — the question wording never depends
  on the model. The gate stays structural even if the model words its part
  badly.
- System prompt gains one rule: when a send is gated, state the target and
  the text (verbatim if short), and wait — never narrate the send as done.

## 8. Drawer v2 Integration

- **Card placement:** pinned at the TOP of the drawer conversation area
  (below the interim strip, above chat bubbles). It is actionable state,
  not history — exactly one card at a time.
- **Drawer auto-opens** when a gate opens (same behavior as Llamar). If the
  user closes it, the pill shows `confirming` ("Confirmar ▲") and tapping
  reopens — existing pill mechanics, no new surface.
- **Card buttons:** `✓ Enviar` · `✏️ Editar` · `🎙 Re-dictar` · `✕ Cancelar`,
  plus a visible countdown (60 s ring).
- **Coexistence with the agent pending prompt:** two distinct pendings —
  `detect_pending` banner (the AGENT waits for input) vs the brain gate
  (the BRAIN waits for approval). The gate owns the mic and the card; the
  agent pending banner stays visible (drawer PRD G3: never hidden) but
  visually secondary. Both may coexist: answering an agent pending prompt
  routes through `send_to_session`, which gets gated like everything else —
  one consistent write path.
- **Reload recovery:** PWA boot calls `GET /approval/current`; a live gate
  re-enters `confirming` with the card restored.

## 9. Non-goals

- Gating read-only tools (`get_status`, `read_transcript`, `read_screen`).
- Generic approval framework for future tools — the gate is the
  `send_to_session` boundary; refactor only if a second mutating tool ever
  appears.
- Persisting approvals/gates across server restarts.
- Confidence-score gating from STT: acoustic confidence does not predict
  semantic misunderstanding; the gate is unconditional for all sends.
- New SSE events: the gate lives in the request/response cycle of
  `/ask` + approval endpoints; watcher announcements continue independently.

## 10. Planificación original (histórico; piezas implementadas)

- Settings field wiring (`HERDR_BRAIN_APPROVAL_TIMEOUT_S`) and its
  `config.py` default.
- Gate store file/module placement (`approval.py` next to `memory.py`).
- Test matrix: freeze-on-send, approve replays exact args (incl.
  `timeout_ms`), supersede on new ask, expiry lazy check, lexicon matching
  (short-utterance rule + "no sé"), reload recovery, PATCH revision timer
  reset — mirroring existing `test_llm.py` / `test_server.py` injection
  patterns.
- UI copy pass (ES) for card, pill, reprompt and expiry strings.
