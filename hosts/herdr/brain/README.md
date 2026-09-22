# herdr-brain

Conversational brain service that lets a user talk to AI coding agents
managed by [Herdr](https://herdr.dev). The brain is fully usable over HTTP
and CLI, and ships a thin installable PWA (one-tap call: browser-side speech
recognition → /ask → text + spoken mp3 on the phone).

```
voice (later PWA) ──►  herdr-brain  ──►  herdr CLI  ──►  agent pane (opencode/claude/…)
                          │  ▲
                          │  └── agent-tts transcript stores (SQLite/JSONL, read-only)
                          └───── agent-tts engine (mp3 synthesis, --no-play)
```

The brain is the **only** component allowed to write to Herdr, through a
single tool: `send_to_session`.

## Safety model

- Secrets never enter the repo. LLM access uses the OpenAI-compatible client
  with `GLM_API_KEY` / `GLM_BASE_URL` / `GLM_MODEL` environment variables.
- `send_to_session` (→ `herdr agent prompt --wait`) writes into **real live
  agent sessions owned by the user**. Every unit test mocks subprocess for
  all write paths; only read-only checks run live (`scripts/smoke_readonly.sh`).
- Newlines are stripped from prompt text before sending: a literal `\n` is a
  real Enter in the target pane.
- Screen reads are clamped to 60 lines: reading beyond the viewport scrolls
  the operator's real screen on alt-screen agents.
- Audio is synthesized with `--no-play`; playback happens on the client
  (phone/PC browser), never on PC speakers.

## Setup

```bash
cd ~/Code/personal/herdr-brain
scripts/bootstrap.sh          # creates .venv, installs deps, runs tests
```

### Environment variables

Add your key to `~/.dotfiles/shell/private-env.sh` (symlinked **outside** the
repo — never commit it) and re-source your shell:

```bash
# in ~/.dotfiles/shell/private-env.sh
export GLM_API_KEY="…"
```

| Variable | Default | Purpose |
|---|---|---|
| `GLM_API_KEY` | *(required)* | LLM key (OpenAI-compatible endpoint) |
| `GLM_BASE_URL` | `https://api.z.ai/api/paas/v4/` | LLM base URL |
| `GLM_MODEL` | `glm-5` | LLM model |
| `HERDR_BIN` | `herdr` | herdr CLI path |
| `HERDR_TTS_VENV` | `~/.local/share/herdr-tts/venv` | herdr-tts venv (speech backend contract root; the venv python derives from it) |
| `HERDR_TTS_PYTHON` | `~/.local/share/herdr-tts/venv/bin/python` | venv python that hosts the agent-tts engine |
| `HERDR_TTS_ENGINE` | `~/Code/personal/herdr-tts/lib/tts_engine.py` | agent-tts engine script |
| `HERDR_BRAIN_VOICE` / `HERDR_BRAIN_RATE` / `HERDR_BRAIN_MAX_CHARS` | `elvira` / `+0%` / `300` | synthesis knobs |
| `HERDR_BRAIN_TTS_ARGS` | *(empty)* | extra engine flags (e.g. `--piper`, `--tldr`) |
| `HERDR_BRAIN_AUDIO_DIR` | `~/.local/state/herdr-brain/audio` | rendered mp3 directory |
| `HERDR_BRAIN_PROMPT_TIMEOUT_MS` | `180000` | default wait for `send_to_session` |
| `HERDR_BRAIN_MAX_TOOL_ROUNDS` | `4` | LLM tool-loop guard |
| `HERDR_BRAIN_SCREEN_LINES` | `40` | default visible lines for `read_screen` |
| `OPENCODE_DB` / `CLAUDE_PROJECTS_ROOT` | store defaults | transcript store overrides (tests) |

## Speech backend contract

**herdr-tts is the brain's official speech backend** — a designed
dependency, not an accidental borrow. herdr-tts is the TUI-native voice
layer *and* the engine that renders every answer and announcement the
PWA plays: herdr-brain requires **herdr-tts >= v0.14** installed with its
venv (default `~/.local/share/herdr-tts/venv`, override with
`HERDR_TTS_VENV`; `HERDR_TTS_PYTHON` / `HERDR_TTS_ENGINE` still override
the individual paths).

The contract is verified fail-soft at startup and surfaced in `/health`
as `"tts": "ok" | "missing"` (parity with the `stt` field). When the
backend is missing the server does NOT crash: text answers keep working
(`/ask` degrades to `audio_url: null`, which the PWA already tolerates),
announcements are skipped, and render failures log an explicit
`herdr-tts not found` error naming the contract instead of a bare
subprocess traceback.

## Deployment (the way to run it)

The service runs as a **systemd user unit** — auto-started, restarted on
failure, and with a stable environment that does not depend on any
interactive shell having sourced the dotfiles:

```bash
deploy/install.sh    # idempotent: env file + unit + linger + health gate
```

What the installer does (safe to re-run):

- extracts `GLM_API_KEY` from `~/.dotfiles/shell/private-env.sh` into
  `~/.config/herdr-brain/env` (outside the repo, mode 600; the value is
  never printed and the file is only rewritten when the key changes),
- stops any stray manual instance listening on :8741,
- installs `deploy/herdr-brain.service` as a user unit, enables linger and
  starts it (`Restart=on-failure`, `RestartSec=3`),
- polls `/health` for up to 10s and prints the journal on failure.

```bash
systemctl --user status herdr-brain     # state + recent log lines
journalctl --user -u herdr-brain -f     # follow
systemctl --user restart herdr-brain    # bounces in ~3s, config included
```

### Manual run (debugging ONLY)

```bash
# Requires the key in THIS shell, or /ask will 503:
source ~/.dotfiles/shell/private-env.sh
HERDR_BRAIN_HOST=0.0.0.0 .venv/bin/python -m herdr_brain.server
```

> **Warning:** manual `nohup` restarts from tool shells lose
> `GLM_API_KEY` (it lives in `private-env.sh`, sourced only by interactive
> shells) and `/ask` fails with 503 while `/health` stays green. This is
> the exact incident that made the unit the supported path — after
> debugging, re-run `deploy/install.sh` to hand the port back to systemd.

### CLI ask

```bash
# CLI ask — no mic needed; --no-audio skips TTS entirely
.venv/bin/python -m herdr_brain.ask "dime en que estas trabajando" --no-audio
.venv/bin/python -m herdr_brain.ask "resume what you just did"
```

### HTTP surface

```bash
curl -s localhost:8741/health
curl -s localhost:8741/state
# → {"active":true,"pane_id":"w7:p4","agent":"opencode","agent_status":"working",
#     "title":"OpenCode","cwd":"/repo","session_id":"ses_…"}

curl -s localhost:8741/herd
# → [{"pane_id":"w7:p4","agent":"opencode","agent_status":"working","title":"OpenCode",
#     "cwd":"/repo","session_id":"ses_…","focused":true,
#     "last_turn":{"role":"assistant","text":"…"}}, …]

# live announcements (SSE): transitions into done/blocked
curl -N localhost:8741/events
# → : connected
# → data: {"type":"transition","pane_id":"w1:p2","agent":"opencode","status":"done",
#          "label":"opencode repo","text":"opencode repo terminó: Migración aplicada",
#          "audio_url":"/audio/ann-<hex>.mp3"}


curl -s "localhost:8741/view?pane_id=w1:p2"   # any herd member (default: focused)

curl -s "localhost:8741/conversation?pane_id=w1:p2"
# → {"pane_id":"w1:p2","agent":"opencode","session_id":"ses_…",
#     "turns":[{"role":"user","text":"…full text…"},…],"window":20}
# Last 20 turns, full text (capped at 4000 chars/turn as a token guard).
# The transcript readers have no cursor, so there is no older-page fetch.

curl -s "localhost:8741/screen?pane_id=w1:p2"
# → {"pane_id":"w1:p2","agent":"opencode","screen":"…last ~120 scrollback lines…"}
# → {"status": {…same as /state…},
#     "transcript": [{"role":"user","text":"…"},{"role":"assistant","text":"…"}],
#     "screen": "…last ~12 visible lines…",
#     "pending": {"detected":true,"kind":"permission","excerpt":"…allow? (y/n)"}}

curl -s localhost:8741/ask -H 'content-type: application/json' \
     -d '{"text":"en que estas trabajando?","session_id":"phone-abc"}'
# → {"answer":"…","pane_id":"w7:p4","agent":"opencode","session_id":"phone-abc",
#     "audio_url":"/audio/<hex>.mp3"}

# start a fresh conversation for one session (also: {"reset":true} on /ask)
curl -s localhost:8741/reset -H 'content-type: application/json' \
     -d '{"session_id":"phone-abc"}'

curl -s localhost:8741/tts -H 'content-type: application/json' \
     -d '{"text":"eco local del PWA"}'
curl -sO localhost:8741/audio/<hex>.mp3    # play on the CLIENT
```

### Conversation memory

`/ask` keeps the last 16 user/assistant turns per `session_id` (in process,
each message clipped at 4000 chars). Omitting `session_id` uses the `default`
session, so old callers keep working. The system message is rebuilt on every
request: static identity/policy text plus a **live context block** (active
agent kind and status, terminal title, cwd, pane id, session id, local time)
that is refreshed each turn and never stored in history. When the pending
detector fires, the block ends with an `ATTENTION: …` line. Follow-ups like
"¿y qué más?" resolve against prior turns; `POST /reset` (or `reset: true`
on `/ask`) starts fresh.

### Announcements ("the call tells you when an agent finishes or needs you")

A background watcher polls the agent list every ~4s and announces
**transitions into `done` or `blocked`** over `GET /events` (Server-Sent
Events). Design choices:

- **Digests, not streaming**: template-based text (no LLM call — instant and
  free), at most two short speakable sentences. `done` uses the first
  sentence of the agent's latest transcript turn (or first screen line);
  `blocked` prefers the pending excerpt. Fallback: "sin detalle disponible".
- **Boot silence**: the first poll snapshots state as a baseline —
  pre-existing agents are never announced.
- **Debounce**: the same pane+status is not re-announced within 60 s.
- **Text never blocks on TTS**: the mp3 renders best-effort; on failure the
  event still arrives with `audio_url: null`. Announcement files (`ann-*.mp3`)
  are garbage-collected after ~1 h.
- Transitions into `idle` are intentionally not announced in v1.

On the PWA, the **Call tap** opens the EventSource (the user gesture unlocks
autoplay); announcements queue behind any in-flight audio and play through
the same element with a toast showing the digest. The speaker toggle mutes
announcement audio only (a silent toast still appears); chat answers always
play. Install note: if the phone never plays announcements, tap Call once
after reload — browsers require one gesture per session before autoplay.

### Pending-action detection (heuristic)

`/view.pending` and the LLM hint come from `detect_pending`, which is
**explicitly a heuristic**: it matches the bottom of the visible screen for
permission markers (`y/n`, `allow`, `deny`, `permit`, `approve`,
`confirm`…), error markers (`error`, `failed`, `traceback`) and question
shapes (a line ending in `?`, "press enter"); a `blocked` agent status is a
strong signal by itself. It can false-positive on ordinary output — treat
it as a hint, not ground truth. The brain's prompt enforces the safety
rule that matters: to answer a pending prompt on the user's behalf it must
first `read_screen` and state what would be confirmed — never a blind yes.

The brain also serves its mobile PWA same-origin at `/` (static assets in
`src/herdr_brain/static/`, mounted after the API routes): no CORS anywhere.

## Phone access over Tailscale (PWA)

The brain listens on `127.0.0.1:8741`. To reach it from the phone over your
tailnet (this is documentation, not something the repo runs):

> **IMPORTANT — never use port 443.** The root HTTPS port of this node is
> owned by Collie's bridge (127.0.0.1:8787), a third-party Herdr plugin.
> Serving the brain there stomps Collie for every device on the tailnet.
> The brain gets its own port:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8741
```

Open on the phone (note the port):

```text
https://<machine-name>.<tailnet-name>.ts.net:8443/
```

Install to home screen (Android, Chrome):

1. Open the URL above and allow the microphone permission when prompted.
2. Chrome menu (⋮) → **Add to Home screen** / **Install app**.
3. Launch from the home screen — it runs standalone (no browser chrome).
4. Tap **● Call**, speak; the interim transcript shows live, then the answer
   appears as text and the spoken mp3 plays in the earbuds. **■ Stop audio**
   kills playback anytime.

The header polls `/herd` and `/view` every 5s: a scrollable chip strip shows
every agent in the herd (status-colored, tap to select — persisted across
reloads), and the **agent view panel** shows the selected agent's pending
banner (amber = question, red = error, blue = permission), its last
transcript turns and a dimmed screen tail. Questions spoken or typed after
selecting a chip target that agent. Tapping the panel opens a full-screen
sheet with two tabs — **Conversación** (last 20 turns, full text) and
**Pantalla** (~120 scrollback lines) — with a manual refresh button.

### The call is continuous

One tap starts the call; it stays open. The state machine is
`listening → thinking → speaking → listening`: the mic is forced off while
any audio plays so it never hears its own voice, tapping the state pill
while speaking is barge-in (cuts the audio, back to listening), and a pause
button shuts the mic up without ending the call. Persistent recognition
failure falls back to keyboard input with a notice.

Voice turns are **self-endpointed** (`static/endpointing.js`, pure logic,
unit-tested with `node:test`): Chrome often never finalizes interim
results, so the brain dispatches on `isFinal`, on **1200 ms of interim
silence**, or at a **15 s hard cap** from speech start — whichever comes
first. After dispatch the mic stays off through `/ask` + TTS and restarts
when the answer finishes. All user-facing labels are Spanish; the UI
follows light/dark automatically. Answers are spoken in full — the
renderer's char guard is 4000 chars and digest shortening (`--tldr`) is
never applied to chat answers.

Every async surface has Spanish loading/empty/error states, and the agent
sheet shows a diagnostics line (**Última consulta / Último error**) to make
reporting issues easy.

### Build versioning and cache discipline

`GET /` is served with `Cache-Control: no-cache` and every asset reference
carries a build stamp: `/app.js?v=<git short hash>`. The PWA footer shows
the same hash (`v<hash>`) so the phone's loaded build is always verifiable
at a glance. JS/manifest/icon routes also send `no-cache`; the service
worker is a pure pass-through (it never caches anything). Net effect: the
phone always revalidates against the server and runs the deployed build.
`If-None-Match` revalidation still yields 304s (files carry ETags), which
only skips the body download — freshness is enforced on every load. The
hash is resolved once at server startup: restart the server after pulling
a new build.

### Voice diagnostics overlay

Long-press the state pill (~0.6 s) to open **Diagnóstico de voz**: call
state, listening-since, interim char count and the last raw interim text,
endpointing internals (silence/cap remaining), recognition error count +
last error, `/ask` dispatch count + last dispatch text, and the build hash.
It refreshes every 0.5 s while open — include a screenshot when reporting
that "no te oigo". The pill also self-reports: **5 s listening with zero
captured characters** shows "No te oigo — comprueba el micro", and a
blocked microphone raises a red banner (not a silent fallback). `/ask`
itself has a 30 s timeout so a hung request returns the call to listening
instead of wedging it.

Browsers without the Web Speech API (e.g. Firefox) automatically show a text
input with the same ask flow, which also makes desktop testing trivial.

Tool calls the LLM makes during `/ask` are audit-logged to the
`herdr_brain.tool_calls` logger with name + truncated arguments (never full
payloads).

## Tools exposed to the LLM (fase 1: active pane only)

| Tool | Reads/Writes | Purpose |
|---|---|---|
| `get_status` | read | agent kind, status, pane id, session id, cwd, title |
| `read_transcript(n_turns)` | read | recent user/assistant turns (preferred for Q&A) |
| `read_screen(n_lines)` | read | visible terminal text (fallback) |
| `send_to_session(text, timeout_ms?)` | **write** | forward new work, wait for completion |

Every endpoint and tool is **target-aware**: pass `pane_id` (/view query
param, /ask body field) to operate on any agent in the herd; the default is
the focused pane. The live context block marks the selection explicitly
(`Selected agent: … — focused: yes/no`), and a stale pane_id falls back to
the focused one. Conversation memory stays keyed by the client session id —
selection is per-request.

The system prompt carries the full harness: the collie identity, concrete
capabilities, honesty rules (never invent transcript or screen content; a
failed read becomes one plain sentence plus a retry hint), routing policy
(state/history/summary questions are answered from `read_transcript` or
`read_screen`; new work is forwarded with `send_to_session` and then
reported), and the voice-first style cap of three short, speakable
sentences. Tool descriptions encode the economics: `read_transcript` is
cheap and local (use first), `send_to_session` is slow and waits for real
completion, `get_status` is rarely needed because live context is already
injected every turn.

## Transcripts

`read_transcript` reuses the store contracts of the
[agent-tts](https://github.com/…) connector layer (`agent_tts/sources/`), but
with multi-turn queries (that layer only exposes the last assistant message):

- **OpenCode** (`ses_*` ids, agent `opencode`): read-only SQLite at
  `~/.local/share/opencode/opencode.db`, `message`/`part` JSON filtering.
- **Claude Code** (UUID-like ids, agents `claude`/`codex`/`antigravity`):
  reverse tail scan (8 MB cap) of `~/.claude/projects/<munged>/<uuid>.jsonl`.
- Anything else: connector returns nothing → screen fallback.

## Tests and smoke

```bash
.venv/bin/python -m pytest -q        # unit tests; ALL herdr/agent-tts subprocess mocked
scripts/smoke_readonly.sh           # live READ-ONLY checks only, never sends prompts
```

`smoke_readonly.sh` exercises: `herdr agent list` parsing, active pane
detection, a transcript read for the focused session, and `/health` on a
locally started server.

## Verified herdr CLI flags (from `--help`)

```text
herdr agent list
herdr agent read <TARGET> [--source visible|recent|recent-unwrapped|detection]
                   [--lines N] [--format text|ansi] [--ansi]
herdr agent prompt <TARGET> <TEXT> [--wait]
                    [--until idle|working|blocked|done|unknown] [--timeout MS]
```

`agent prompt` rejects submissions when the agent is blocked
(`agent_blocked`), requires an observed working/blocked state within 5 s of
`--wait` (else `agent_prompt_stalled`), and returns `timeout` when the caller
timeout expires first.

## Known limitations (fase 1)

- Active pane only; multi-pane targeting arrives with the PWA phase.
- Multi-turn transcripts are implemented for OpenCode and Claude-shaped
  stores; `codex`/`antigravity` sessions fall back to `read_screen` unless
  their stores match the Claude JSONL shape.
- TTS defaults follow the engine (voice `elvira`); provider flags can be
  injected via `HERDR_BRAIN_TTS_ARGS`.
