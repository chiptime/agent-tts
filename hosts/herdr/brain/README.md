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
| `HERDR_TTS_PYTHON` | `~/.local/share/herdr-tts/venv/bin/python` | venv python that hosts the agent-tts engine |
| `HERDR_TTS_ENGINE` | `~/Code/personal/herdr-tts/lib/tts_engine.py` | agent-tts engine script |
| `HERDR_BRAIN_VOICE` / `HERDR_BRAIN_RATE` / `HERDR_BRAIN_MAX_CHARS` | `elvira` / `+0%` / `300` | synthesis knobs |
| `HERDR_BRAIN_TTS_ARGS` | *(empty)* | extra engine flags (e.g. `--piper`, `--tldr`) |
| `HERDR_BRAIN_AUDIO_DIR` | `~/.local/state/herdr-brain/audio` | rendered mp3 directory |
| `HERDR_BRAIN_PROMPT_TIMEOUT_MS` | `180000` | default wait for `send_to_session` |
| `HERDR_BRAIN_MAX_TOOL_ROUNDS` | `4` | LLM tool-loop guard |
| `HERDR_BRAIN_SCREEN_LINES` | `40` | default visible lines for `read_screen` |
| `OPENCODE_DB` / `CLAUDE_PROJECTS_ROOT` | store defaults | transcript store overrides (tests) |

## Run

```bash
# HTTP service (127.0.0.1:8741)
.venv/bin/python -m herdr_brain.server

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
selecting a chip target that agent.

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
