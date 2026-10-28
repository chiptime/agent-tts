# herdr-brain

> [!IMPORTANT]
> **Repository Moved to Monorepo**  
> `herdr-brain` has moved to the unified ecosystem monorepo at [`chiptime/agent-tts`](https://github.com/chiptime/agent-tts).  
> All active development, issues, and PRs continue in [`hosts/herdr/brain/`](https://github.com/chiptime/agent-tts/tree/main/hosts/herdr/brain).  
> This standalone repository is now archived and read-only.

Conversational brain service that lets a user talk to AI coding agents
managed by [Herdr](https://herdr.dev). The brain is fully usable over HTTP
and CLI, and ships a thin installable PWA (one-tap call: browser-side speech
recognition → /ask → text + spoken mp3 on the phone).

```
voice (later PWA) ──►  herdr-brain  ──►  herdr CLI  ──►  agent pane (opencode/claude/…)
                          │  ▲
                          │  └── transcript stores (SQLite/JSONL, read-only)
                          └───── herdr-tts CLI surface (mp3 synthesis, contract v1)
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
cd <repo>/hosts/herdr/brain    # <repo> = your agent-tts checkout
scripts/bootstrap.sh          # creates .venv, installs deps, runs tests
```

### Environment variables

Put your key in `~/.config/herdr-brain/env` (outside the repo, mode 600 —
never commit it). `bin/herdr-brain` reads it from there when `GLM_API_KEY`
is not already exported:

```bash
mkdir -p ~/.config/herdr-brain && chmod 700 ~/.config/herdr-brain
printf 'GLM_API_KEY=%s\n' "<your key>" > ~/.config/herdr-brain/env
chmod 600 ~/.config/herdr-brain/env
```

Interactive shell dotfiles are never scraped: systemd and plugin-launched
daemons do not source them, so the env file is the single stable contract.

| Variable | Default | Purpose |
|---|---|---|
| `GLM_API_KEY` | *(required)* | LLM key (OpenAI-compatible endpoint) |
| `GLM_BASE_URL` | `https://api.z.ai/api/paas/v4/` | LLM base URL |
| `GLM_MODEL` | `glm-5` | LLM model |
| `HERDR_BIN` | *(discovered)* | herdr CLI path; discovery order: `HERDR_BIN` → `herdr` on `PATH` → `$HOMEBREW_PREFIX/bin/herdr` → `brew --prefix`/bin/herdr → `~/.local/bin/herdr` → bare `herdr` |
| `HERDR_TTS_HOME` | sibling `../tts-plugin` of the brain root | herdr-tts root (speech backend; the surface CLI `<home>/bin/herdr-tts` derives from it). An exported, existing directory wins over the sibling default |
| `HERDR_BRAIN_PORT` | `8741` | listen port; order: env → `~/.config/herdr-brain/config.env` → default |
| `HERDR_BRAIN_REMOTE_URL` | *(unset)* | optional remote URL printed by `bin/herdr-brain url`; env or `config.env` only, never inferred |
| `HERDR_BRAIN_VOICE` / `HERDR_BRAIN_RATE` / `HERDR_BRAIN_MAX_CHARS` | `elvira` / `+0%` / `300` | synthesis knobs |
| `HERDR_BRAIN_TTS_ARGS` | *(empty)* | extra engine flags (e.g. `--piper`, `--tldr`) |
| `HERDR_BRAIN_AUDIO_DIR` | `~/.local/state/herdr-brain/audio` | rendered mp3 directory |
| `HERDR_BRAIN_PROMPT_TIMEOUT_MS` | `180000` | default wait for `send_to_session` |
| `HERDR_BRAIN_MAX_TOOL_ROUNDS` | `4` | LLM tool-loop guard |
| `HERDR_BRAIN_SCREEN_LINES` | `40` | default visible lines for `read_screen` |
| `OPENCODE_DB` / `CLAUDE_PROJECTS_ROOT` / `ANTIGRAVITY_ROOT` | store defaults | transcript store overrides (tests) |
| `CODEX_ROOT` | `~/.codex/sessions` | codex rollout transcripts root |
| `GROK_ROOT` | `~/.grok/sessions` | grok Build chat-history root |
| `PI_ROOTS` | `~/.pi/agent/sessions:~/.omp/agent/sessions` | pi/omp session roots (colon-separated list; first root holding the session wins) |
| `HERMES_ROOT` | `~/.hermes` | hermes root (`state.db` plus `profiles/*/state.db`) |
| `HERDR_BRAIN_OPENCODE_URL` | `http://127.0.0.1:4096` | local opencode server; the PWA answers an agent's approval dialog through its permission API |
| `HERDR_BRAIN_APPROVAL_TOKEN` | *(empty = no auth)* | shared secret for `POST /approval/{id}/action` (ntfy action buttons), as `Authorization: Bearer` or `?token=`. Empty leaves that route as open as the PWA routes |
| `HERDR_BRAIN_STATE_DIR` | `~/.local/state/herdr-brain` | device-pairing state and the audit log (`pairing-pending.json`, `paired-devices.json`, `audit.log`; dir 0700, files 0600, only SHA-256 hashes of credentials on disk) |
| `HERDR_BRAIN_ALLOWED_HOSTS` | *(empty)* | extra allowed `Host` headers beyond loopback and `*.ts.net` (anti DNS-rebinding; see [Device pairing and the access gate](#device-pairing-and-the-access-gate)) |
| `HERDR_BRAIN_ALLOWED_ORIGINS` | *(empty)* | extra allowed `Origin` values (each one's host also joins the Host allowlist) |
| `HERDR_BRAIN_ALLOW_ANY_HOST` | *(empty)* | `1` turns Host validation OFF — the DNS-rebinding hole; warned opt-out for exotic proxies |
| `HERDR_BRAIN_TRUSTED_USER` | *(empty)* | under `tailscale serve`, require a matching `Tailscale-User-Login`; a missing header is rejected too (fail-closed). `HERDR_BRAIN_TRUSTED_USER_OPTIONAL=1` tolerates the missing header |
| `HERDR_BRAIN_DEVICE_HEADER` / `HERDR_BRAIN_DEVICE_ALLOWLIST` | *(empty)* | reverse-proxy device gate: when the header name is set, writes require an allowlisted value in it (absent header ⇒ read-only) |
| `HERDR_BRAIN_AUDIT_CONTENT` | `preview` | audit-log content mode: `preview` keeps a 120-char single-line preview of reply text; `none` redacts every content string to the constant `⟨redacted⟩` (metadata such as outcomes, keys and revisions survives) — see [Audit log](#audit-log) |
| `HERDR_BRAIN_SECRET_MASKING` | *(on)* | secret masking on outgoing text (S14): provider API keys, JWTs, PEM private-key blocks, `Authorization: Bearer` tokens, URL credentials and secret-named `KEY=value` assignments are replaced with hint placeholders (`sk-ant-…[masked]`, `[masked:jwt]`; spoken as "clave oculta") in the pane mirror, history pages, reading views, SSE announcements, the LLM live context and speech renders. Set to `off` (also `0`/`false`/`no`) to serve text verbatim; stored data is never rewritten |

### Empty agent reader

If **Vista del agente** has no conversation, it shows an explanation and points
to **Pantalla** for the terminal snapshot. A missing session identifier is
reported separately from a session with no conversation yet. Terminal output
does not hide this notice, and the notice disappears when messages arrive.
The notice does not repair the agent integration or infer a session identity.

## Speech backend contract (surface v1)

**herdr-tts is the brain's official speech backend** — a designed
dependency, not an accidental borrow. herdr-tts is the TUI-native voice
layer *and* the engine that renders every answer and announcement the PWA
plays. herdr-brain requires **herdr-tts with surface contract v1** and
consumes ONLY its versioned CLI surface — everything underneath (engine,
venv, provider flags) is herdr-tts's private detail:

```bash
<HERDR_TTS_HOME>/bin/herdr-tts --contract-version   # must print >= 1
<HERDR_TTS_HOME>/bin/herdr-tts --render-text OUT.mp3 TEXT [--voice V] [--rate R]
```

`HERDR_TTS_HOME` names the herdr-tts repo
root; the CLI is self-sufficient and bootstraps its own environment. The
default is DERIVED from the installation location (`config.py`
`_default_tts_home`): the brain ascends from its own package directory
— up to 6 levels — and takes the first `hosts/herdr/tts-plugin`
sibling directory it finds, so no machine path is embedded. An exported,
existing `HERDR_TTS_HOME` always wins; when the sibling cannot be
derived (a standalone brain install), startup fails loudly with an
actionable error naming `HERDR_TTS_HOME`. The contract is verified once
at boot and surfaced in `/health` as
`"tts": "ok" | "degraded" | "missing"` (`degraded` = contract present but
the herdr-tts daemon is down — PC-speaker channel dead, phone channel
alive). When the surface is unmet the server does NOT crash: text answers
keep working (`/ask` degrades to `audio_url: null`, which the PWA already
tolerates), announcements are skipped, and failures log an explicit error
naming the required surface version.

> The retired `HERDR_TTS_VENV` / `HERDR_TTS_PYTHON` / `HERDR_TTS_ENGINE`
> overrides no longer exist: the brain never touches the venv or the
> engine anymore.

## Deployment (the way to run it)

### Option 1: Native Herdr Plugin (Recommended, cross-platform)

Herdr can manage `herdr-brain` natively as a workflow plugin with supervised lifecycle (Linux & macOS):

```bash
# Link the plugin in Herdr
herdr plugin link .

# When Herdr starts, herdr-brain starts automatically via [[startup]].
# Control and inspect it via plugin actions:
herdr plugin action invoke status --plugin herdr.brain
herdr plugin action invoke restart --plugin herdr.brain
herdr plugin action invoke url --plugin herdr.brain
herdr plugin action invoke call --plugin herdr.brain

# Or directly via the CLI launcher:
bin/herdr-brain status
bin/herdr-brain url
bin/herdr-brain call
bin/herdr-brain restart
bin/herdr-brain stop
```

### Desktop voice call (`herdr brain call`)


`bin/herdr-brain call` opens the voice-call UI — the SAME PWA the
phone uses — in your desktop browser. It is another interface to one
call, not a second voice stack: desktop and mobile share the same
conversation, the same agent inventory and the same pending approvals,
so you can hand over mid-call between phone and laptop.


| Aspect | Behavior |
|---|---|
| Service gate | The command health-checks `localhost:$HERDR_BRAIN_PORT` first; if the service is down it errors honestly and never opens a dead page (and never starts/restarts the service for you). |
| Browser opener | `HERDR_BRAIN_BROWSER` wins if set; otherwise `xdg-open`, then `open` (macOS). A missing or failing opener is an error, not a silent no-op. |
| Microphone consent | The browser's own permission prompt IS the consent gate. The command never bypasses it; a blocked mic falls back to the in-page keyboard. |
| Shared context | Every client adopts the shared call identity from `GET /call/context` at boot AND before every voice utterance routes, and publishes its explicit agent selection via `POST /call/context` (last writer wins, monotonic revision — a stale snapshot can never overwrite a newer selection). The same routing barrier recovers a gate the other device opened, and the server guards the `/ask` supersession boundary: a spoken gate decision («adelante», «no»…) re-explains a pending gate instead of discarding it, while deliberate new work supersedes as always. A failed live-context check drops the utterance with a visible notice rather than sending it to a stale target. |

### Option 2: systemd user unit (optional, Linux daemon mode)

The service can alternatively run as a persistent **systemd user unit** — independent of Herdr lifecycle. This route is optional; the plugin startup route above needs no systemd.

```bash id=install-brain-systemd
export GLM_API_KEY="…"        # optional here; or add it to ~/.config/herdr-brain/env
deploy/install.sh              # idempotent: env file + generated unit + linger + health gate
deploy/install.sh --generate-only   # only render the unit; no systemctl, no env file
```

Run `bin/herdr-brain doctor` for a read-only diagnosis (CLI, audio backend, credentials, daemon, surface contract, STT cache); `bin/herdr-brain doctor --fix-credentials` re-runs only the credential capture.

The unit is **generated**, never committed: the installer discovers the
install location, the venv python, the `herdr` binary
(`HERDR_BIN` → `PATH` → `$HOMEBREW_PREFIX/bin/herdr` → `brew --prefix` →
`~/.local/bin/herdr`; an unresolved binary is a hard failure), the
tts-plugin home, the port and the env file, substitutes them into
`deploy/herdr-brain.service.tmpl`, and writes
`~/.config/systemd/user/herdr-brain.service`. Any placeholder left
unsubstituted aborts the install before anything is changed. `deploy/*.service`
is git-ignored.

What the installer does (safe to re-run):

- reads `GLM_API_KEY` from the installer's own environment (never from
  dotfiles) and merges it into `~/.config/herdr-brain/env` (outside the
  repo, mode 600; the value is never printed; other lines are preserved),
- stops a stray manual herdr-brain instance on the port only when it is
  provably ours (pidfile or the unit's `MainPID`); a foreign listener aborts
  the install untouched (see *Port conflicts* below),
- on upgrade replaces only the unit it generated (stop → regenerate →
  reload → restart); a unit file it did not generate is never overwritten,
- enables linger, starts the unit (`Restart=on-failure`, `RestartSec=3`),
  polls `/health` for up to 10s and prints the journal on failure.

```bash
systemctl --user status herdr-brain     # state + recent log lines
journalctl --user -u herdr-brain -f     # follow
systemctl --user restart herdr-brain    # bounces in ~3s, config included
```

### Port conflicts

The launcher and the installer never signal a process they do not own. If
the port (default `8741`) is held by anything else, they exit with code `98`
and print the PID, the process name, the port and two remedies: pick another
port (`export HERDR_BRAIN_PORT=<free port>`, or persist it in
`~/.config/herdr-brain/config.env`) or stop that process yourself. Our own
previous instance (pidfile) is stopped normally, and a unit-owned listener
is managed through `systemctl --user`, never `kill`.

### Manual run (debugging ONLY)

```bash
# Requires the key in THIS shell, or /ask will 503:
set -a; . ~/.config/herdr-brain/env; set +a
HERDR_BRAIN_HOST=0.0.0.0 .venv/bin/python -m herdr_brain.server
```

> **Warning:** manual `nohup` restarts from tool shells lose
> `GLM_API_KEY` (a key exported only in an interactive shell never reaches
> them) and `/ask` fails with 503 while `/health` stays green. Use
> `bin/herdr-brain` or `deploy/install.sh` to run the server with proper environment discovery.

### Upgrading the herdr-tts renderer (reader cache release step)

Upgrading `herdr-tts` (or changing the reader profile/contract) REQUIRES
bumping `READER_PROFILE_VERSION` in `src/herdr_brain/reader.py`. The
binary's mtime/size is a **false signal** — `bin/herdr-tts` is a thin
entry script whose mtime does not move when the engine underneath
changes — so the version bump is deliberate and is the only cache
invalidation trigger. Without it, the reader serves HTML/anchor maps
produced by the OLD renderer against the NEW speech indices (stale
anchors). Bumping the constant orphans the old
`reader_cache/<old-version>/` tree, which the disk sweep reclaims;
deleting `reader_cache/` by hand is always safe too (it is disposable
and never touches `audio_dir` or transcript stores).

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

curl -s "localhost:8741/history?pane_id=w1:p2&limit=60"
# → {"pane_id":"w1:p2","agent":"opencode","session_id":"ses_…","title":"…",
#     "turns":[{"role":"user","text":"…UNCLIPPED…","created_at":"…"},…],
#     "reason":null,"cursor":"<opaque>","has_more":true,"limit":60}
# The WHOLE transcript, one page at a time: newest-first windows walking
# older via the opaque cursor (each page is chronological inside). Turns
# arrive unclipped; limit is bounded to [1,200]; a malformed cursor
# degrades to the newest page. An agent with no readable history answers
# {"turns":[],"reason":"no-session"} — never a 500.

curl -s "localhost:8741/pane/w1:p2/mirror?lines=40"
# → {"pane_id":"w1:p2","agent":"opencode","status":"working",
#     "runs":[[{"t":"OK","b":true,"fg":"#00cd00"},…],…],"text":"OK build passed…"}
# The visible window as faithful ANSI runs (same parser as the blocked
# dialog path) plus escape-stripped text. No terminal emulation: herdr
# already rendered the grid. lines clamps to [1,60]; an unknown pane is
# a 404; a failed read degrades to empty runs.

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
- **Adaptive cadence**: one loop, one decision site. The baseline poll
  runs every ~4 s; while an SSE subscriber was seen inside the 10 s
  attention window AND some pane is blocked, the next wait shortens to
  ~1.5 s (a dialog worth catching within seconds). Attention decays by
  itself — no per-client timer exists.
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

### Typed reply to the waiting agent

When the pane you are viewing is showing a prompt, the PWA offers a text
input that delivers **your own words** straight to that exact pane
(`POST /pane/reply` with `{pane_id, text}`): no LLM turn and no approval
gate, because the text comes from the keyboard rather than from speech or
the model. The target must exist — an unknown `pane_id` is a `404` and is
never redirected to the focused pane (a reply silently landing in the wrong
agent would be hard to undo), and any herdr failure is a `502`. `text` is
required, must not be blank, and is capped at 8000 chars.

The input is **not** offered for a pane with `blocked` status, and a `409`
says why: `herdr agent prompt` refuses an agent already waiting at an
approval or question dialog (`agent_blocked`) *before writing any input*, so
text sent there would never arrive. An opencode dialog is answered with the
buttons below instead.

### Destructive-command confirmation (typed replies)

A typed reply goes straight into a real terminal, so the PWA runs a
destructive-command guard before sending it: `static/destructive.js`
(a pure module, unit-tested with `node:test`) flags a small, reviewed set
of **7 patterns from a guard design proven in practice** — recursive `rm`, force-push,
`sudo`, `--force`, `dd if=`, `mkfs`, and a redirect onto a system path.
When the typed text matches, the send requires a **second tap** through a
confirm dialog that names the pattern before `POST /pane/reply` leaves the
phone; the key pad (`/pane/keys`) is unaffected, and a pattern that does
not match sends on the first tap as always. The guard is deliberately
conservative (word-boundary anchored, no false positives on look-alikes
such as "assume"): a false positive costs one extra tap, a false negative
costs a shell.

### Key pad: answering a blocked agent's dialog

That dialog is answered with **keys** instead, through `POST /pane/keys`
with `{pane_id, keys}` — the one surface herdr documents for it
(`herdr agent send-keys`, which validates every key before writing a byte).
When the viewed pane is `blocked`, the PWA swaps the text input for a key
pad: `←` `→` `↑` `↓` `Tab` `Esc` `Enter`, one deliberate key per tap. The
pad never auto-selects an option, so choosing and confirming stay your
decision — it will not answer a permission prompt for you.

The key pad answers nothing blind: while the viewed pane is `blocked`, `/view`
also carries **`screen_runs`** — the same screen WITH its colour and weight,
read through `herdr agent read --format ansi` and emitted as plain JSON runs
(`{t, fg?, bg?, b?}`). "Which option of the dialog is selected" exists ONLY in
that styling, so reading the plain text threw the signal away and left you
pressing keys without knowing what they would confirm. The PWA paints the runs
with `createElement` + `textContent`: a faithful mirror that interprets
nothing, and that cannot let terminal output inject markup.

`keys` is a **closed set** (`up`, `down`, `left`, `right`, `enter`, `esc`,
`tab`); anything else, an empty list, or more than 8 keys is a `422` before
any CLI work. An unknown `pane_id` is a `404` and is never redirected —
answering the *wrong* agent's permission dialog would be silent and hard to
undo. Any herdr failure is a `502`.

### Answering an opencode approval dialog

A blocked pane whose agent is opencode gets **three buttons** instead of the
key pad: `Permitir una vez`, `Permitir siempre` and `Rechazar`. They map
one-to-one onto opencode's own permission contract — `once`, `always`,
`reject` — and travel through its local API:

```text
GET  /pane/permission?pane_id=…   -> {pending: {request_id, permission, patterns} | null}
POST /pane/permission/reply       -> {"request_id", "reply", "message"?} -> {ok}
```

The pane is matched to its request by the opencode session id herdr already
reports for that pane; an unknown pane is a `404` and is never redirected to
the focused one. A request already gone (answered elsewhere, or expired) is a
`404`, and an unreachable opencode server is a `502` — never a silent
"nothing pending", which would read as "there is nothing to answer". `reply`
is validated at the edge, so an invented answer is a `422` and never reaches
opencode as a guess.

These two routes are **PWA-only and deliberately absent from the LLM tool
schema**: the same capability in the model's hands would let an agent approve
its own request, which is exactly what the dialog exists to prevent.

`Permitir siempre` writes a persisted rule — `GET /api/permission/saved` on
the opencode server lists them as `{id, projectID, action, resource}` — so it
is the least prominent of the three. The PWA cannot yet list or remove those
rules.

A blocked pane whose dialog is **not** opencode's — claude, codex, agy —
keeps the key pad above: those dialogs are answered with deliberate key
presses, one tap at a time, never auto-answered.

## Device pairing and the access gate

Every route runs behind a fail-closed access gate (device pairing, F1), and
once at least one device is paired, **every write AND every read** requires
that device's bearer token (S11) — except the surfaces that must answer
before any credential can exist: `/health`, the PWA shell and its static
assets (`/`, `*.js`, `*.css`, icons, the manifest, the service worker),
`POST /pair` (the bootstrap itself), `POST /approval/{id}/action` (keeps
`HERDR_BRAIN_APPROVAL_TOKEN`, because ntfy cannot attach a device bearer)
and the `/speech/{id}/*` transport (job-token bound, unchanged):

- **Host allowlist (always on)** — the request's `Host` must be a loopback
  form, any `*.ts.net` name (so the phone's
  `https://<machine>.<tailnet>.ts.net:8443` access keeps working with no
  new configuration), a `HERDR_BRAIN_ALLOWED_HOSTS` entry, or the host of a
  `HERDR_BRAIN_ALLOWED_ORIGINS` origin. Anything else is a 403 before
  any other logic (anti DNS-rebinding). `HERDR_BRAIN_ALLOW_ANY_HOST=1` is
  the warned opt-out.
- **Origin/CSRF** — an `Origin`, when present, must be same-origin with
  the Host (or explicitly allowed); a write — or `POST /pair`, which
  mints a write credential — with no `Origin` from a non-loopback Host
  is rejected (browsers always send `Origin` on POSTs, so the PWA is
  unaffected). Reads never require `Origin`.
- **Optional identity/device gates** — `HERDR_BRAIN_TRUSTED_USER`
  (Tailscale-User-Login under `tailscale serve`, fail-closed) and
  `HERDR_BRAIN_DEVICE_HEADER`/`HERDR_BRAIN_DEVICE_ALLOWLIST` for reverse
  proxies.
- **Device pairing** — `bin/herdr-brain pair` mints a one-time 8-character
  code (10 min TTL, 5 attempts, only its hash on disk). The phone spends
  it in **Ajustes → Emparejar** and receives a 256-bit bearer token shown
  exactly once; the PWA stores it in `localStorage` and attaches
  `Authorization: Bearer` on every fetch (reads included). `bin/herdr-brain
  devices list` / `devices revoke <label>` manage the registry; a
  revocation or an expiry change applies on the device's next request, no
  restart.
- **`/pair` rate limit** — at most 10 claim attempts per client address
  per minute, then a 429 with a `Retry-After`; a refused attempt consumes
  nothing (in-process budget, the pairing files stay untouched).
  The service disables proxy-header rewriting so a caller cannot change
  its limiter identity with `X-Forwarded-For`. Connections from a
  co-located reverse proxy share that proxy's transport-peer budget.
- **Token expiry (optional, per device)** — `bin/herdr-brain pair
  --expires <dur>` enrols the claiming device with a deadline (counted
  from pairing; `30d`, `12h`, `45m`, `90s` — one value, one unit),
  `devices set-expiry <label> <dur>` arms or re-arms it and
  `devices clear-expiry <label>` removes it (`devices list` shows the
  deadline). An expired token answers 403 `dispositivo caducado` and the
  PWA offers re-pairing; a registry entry from before the feature (no
  `expiresAt`) never expires.

**With no device paired, every route behaves exactly as before** — the
requirement turns on with the first pairing. **Migration note:** pairing
the first device removes access from every unpaired client (the PWA
offers the pairing flow on the "not paired" 403 — on reads too).
`POST /approval/{id}/action` keeps `HERDR_BRAIN_APPROVAL_TOKEN` alone,
because ntfy cannot attach a device bearer (and sends no `Origin`);
write-gated routes are `/pane/reply`,
`/pane/keys`, `/pane/permission/reply`, `/approval/{id}/approve|reject|resolve`,
`PATCH /approval/{id}`, `/ask`, `/transcribe`, `/reset` and
`/devices/revoke`.

**EventSource and `<audio src>` cannot send headers**, so exactly
`GET /events` and `GET /audio/{name}` also accept the credential as a
`?device_token=` query parameter; the PWA appends it on those two
transports only, and the gate ignores the parameter everywhere else —
never on a write. The parameter never reaches the logs this project
controls: the uvicorn access log runs through a redaction filter that
rewrites any `device_token=` value out of the line (`log_config=None`
keeps uvicorn from installing its own unfiltered access handler).
Encoded parameter names and repeated token parameters are redacted too.
After pairing or revocation, the PWA replaces its single event stream
with one using the current credential; queued media uses the current
credential when playback actually begins.

The requirement fails CLOSED: a corrupt, truncated or entry-incomplete
`paired-devices.json` keeps every write refused with no device
authorized — only an absent file or a well-formed zero-device registry
(the file revoking the last device writes) leaves writes open, and
`herdr-brain pair` always recovers the state (a claim rewrites a valid
registry). A present-but-unusable `expiresAt` drops the entry (it never
widens into "never expires"). `pair --label` refuses an unusable label
(empty after trimming, or over 48 characters) instead of silently letting
the phone choose one; the state directory is retightened to `0700`
(files `0600`) even when the nested audio directory created it first
with the default umask.

Routes on a blocked device answer 403 `dispositivo no emparejado` (or
`dispositivo caducado`); a revoked or expired device loses reads and
writes alike — the recovery is the pairing flow.

Both permission routes above are PWA-only and carry the device bearer like
every other write. `HERDR_BRAIN_APPROVAL_TOKEN` protects only the ntfy
action route, because ntfy is a third party that cannot send headers.

The brain also serves its mobile PWA same-origin at `/` (static assets in
`src/herdr_brain/static/`, mounted after the API routes): no CORS anywhere.

## Audit log

Every write the brain accepts can type into a real terminal or resolve a
frozen gate, so each one leaves **one JSONL line** in
`<HERDR_BRAIN_STATE_DIR>/audit.log` (default
`~/.local/state/herdr-brain/audit.log`). The file is created `0600` inside
the `0700` state dir — it may echo reply text — and a line looks like:

```json
{"ts":"2026-10-28T09:16:58+00:00","action":"pane.reply","pane_id":"w1:p2","device":"phone","detail":{"outcome":"delivered","text":"deploy the fix"}}
```

- **What is audited**: `pane.reply`, `pane.keys`,
  `pane.permission.reply`, `approval.approve|reject|resolve|patch` (with
  `gate_id`, `tool` and the revision the decision applied to; a voice
  approval via spoken proof carries `spoken: true`), the ntfy
  `approval.action` plus its `approval.replay` outcome (approve and its
  execution are decoupled in time), `pair`, `device.revoke`, and one
  `ask` line per turn with `chars` (the utterance **length**, never the
  transcript). The `device` field is the paired-device label from the
  access gate and is omitted on channels that carry no identity (ntfy).
  Reads, `/tts` (the PWA's local echo), the speech transport and SSE
  announcements produce no lines.
- **Content modes** (`HERDR_BRAIN_AUDIT_CONTENT`): `preview` (default)
  keeps a bounded, single-line 120-char preview of content fields;
  `none` redacts every content string inside `detail` to the constant
  `⟨redacted⟩` — never a length — while an allowlist of action metadata
  (outcomes, key names, revisions, gate ids, decisions, replies,
  request ids, utterance lengths) survives. The default direction is
  the feature: a detail field added later redacts until it is
  deliberately allowlisted. Choose `none` if replies may contain
  secrets typed by the operator; the trace still answers who-what-when.
- **Rotation**: the live file is capped at 5 MB (fixed, no knob) with
  exactly one rotated generation — at the cap `audit.log` becomes
  `audit.log.1`, replacing any previous one. A failed rotation still
  appends: an oversized trail beats a missing line.
- **Failure isolation**: the audit write is fire-and-forget — a full
  disk or an unwritable path logs a warning and never changes the
  status or body of the action it records.

## Phone access over Tailscale (PWA)

The brain listens on `127.0.0.1:8741`. To reach it from the phone over your
tailnet (this is documentation, not something the repo runs):

> **IMPORTANT — never use port 443.** The root HTTPS port of this node may
> be owned by another service (an existing Herdr web plugin already serving
> there). Serving the brain on the same port stomps that service for every
> device on the tailnet. The brain gets its own port:

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

The system prompt carries the full harness: the assistant identity, concrete
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

Harness routing lives in a registry (`transcript_readers/registry.py`):
the agent's own name (or a declared alias — `omp → pi`, `oa →
opencode`, `agy → antigravity`) resolves to one connector; a
session-id shape sniff is the fallback. An agent with no connector is
an ordinary "no session", never an error. Every connector is read-only
and reads whole turns:

- **OpenCode** (`ses_*` ids, agent `opencode`): SQLite at
  `~/.local/share/opencode/opencode.db`, `message`/`part` JSON filtering.
- **Claude Code** (UUID-like ids, agent `claude`): reverse tail scan
  (8 MB cap) of `~/.claude/projects/<munged>/<uuid>.jsonl`.
- **Antigravity** (agent `antigravity`): step JSONL under
  `~/.gemini/antigravity-cli`.
- **Codex** (agent `codex`): rollout JSONL under `~/.codex/sessions`
  (`response_item` rows only — the log double-books every turn).
- **Grok Build** (agent `grok`): `chat_history.jsonl` session folders
  under `~/.grok/sessions` (only real `<user_query>` prompts render).
- **pi / Oh My Pi** (agents `pi`, `omp`): session JSONL under
  `~/.pi/agent/sessions` and `~/.omp/agent/sessions` (first root that
  holds the session wins).
- **Hermes** (agent `hermes`): per-profile SQLite `state.db` under
  `~/.hermes` (root db first, then every `profiles/*/state.db`).

Anything else: connector returns nothing → screen fallback.

## Tests and smoke

```bash
.venv/bin/python -m pytest -q        # unit tests; ALL herdr/herdr-tts subprocess mocked
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
- TTS defaults follow the engine (voice `elvira`); provider flags can be
  injected via `HERDR_BRAIN_TTS_ARGS`.
