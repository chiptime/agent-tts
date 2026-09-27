# agent-tts IPC Contract v2 — Frozen

> **Frozen at BLOQUE 1.3 close (2026-09-25). Base for herdr-tts HT-03
> (radio mode) and HT-10 (audio orchestration). Post-freeze changes are
> breaking.**
>
> This document is the normative reference for the agent-tts control
> channel as consumed by external integrators (herdr-tts first). It
> describes the wire exactly as implemented at the freeze commit
> (`feat/at-08-cola-y-cadena`, BLOQUE 1.3 / AT-08 close): framing v2
> (`agent_tts/ipc.py`), daemon dispatch (`agent_tts/daemon.py`),
> queue semantics (`agent_tts/queue_manager.py`), session controls
> (`agent_tts/audio.py`), and the CLI client discipline
> (`agent_tts/cli.py`). Physical communication of this freeze to the
> herdr-tts team is the maintainer's action; this file is the artifact
> to communicate.

---

## 1. Transport and framing (v2)

One command, one reply — each is **one length-prefixed frame**,
identical shape in both directions:

```
+----------------+-----------------+--------------------------+
| MAGIC "ATTS"   | VERSION = 0x02  | LENGTH (4B, big-endian)  |
+----------------+-----------------+--------------------------+
| PAYLOAD (LENGTH bytes, UTF-8 command/reply text)              |
+---------------------------------------------------------------+
```

Constants (`agent_tts/ipc.py`):

| Constant | Value | Meaning |
|---|---|---|
| `FRAME_MAGIC` | `b"ATTS"` | 4-byte magic |
| `FRAME_VERSION` | `2` (`0x02`) | framing v2 |
| `FRAME_HEADER_SIZE` | `9` | 4 + 1 + 4 bytes |
| `MAX_PAYLOAD` | `16 MiB` (16,777,216 B) | hard per-frame cap, both directions |
| `READ_CHUNK` | `64 KiB` | body reassembly recv size |
| `IPC_HEADER_TIMEOUT_SEC` | `1.0 s` | server-side header phase deadline |
| `FRAME_BODY_TIMEOUT_SEC` | `30.0 s` | per-recv idle bound while reassembling a body (a sender making progress at least this often always completes) |
| `CLIENT_TIMEOUT_SEC` | `1.0 s` | client connect/read default (except the blocking `play` read, which has none) |
| listen backlog | `128` | concurrent burst enqueue tolerance |

Header rules:

- The header is fully validated (magic, version, `LENGTH` vs
  `MAX_PAYLOAD`) **before any payload byte is read**. An oversize
  frame is rejected from the header alone with a typed
  `ERR: command too large` reply; the announced body is never
  buffered or drained. In-repo clients enforce the cap locally before
  writing (fail-fast, no wire I/O).
- Bad magic or version raises `ProtocolMismatchError` server-side
  (connection closed — nothing parseable could be answered) and, for
  bytes that do arrive in a client, carries an actionable restart
  hint. A stale daemon still speaking the pre-BLOQUE 1.3 line
  protocol never answers a v2 `ping`: the health probe classifies it
  as wedged and the kill-and-respawn machinery replaces it.
- Truncation is impossible by construction: the body is reassembled
  up to exactly the announced length.

Transports: AF_UNIX socket on POSIX (`$TMPDIR/agent-tts-player.sock`
by default); TCP on `127.0.0.1` with an ephemeral port persisted to
the `agent-tts-ipc.port` marker on Windows. The channel is owned,
never stolen: a live transport is left alone; an orphaned one is
reclaimed only by the flock-elected owner.

## 2. Reply schema

Every daemon-level error is ONE shape:

```
ok=false error=<message, always the FINAL field, may contain spaces>
```

Success payloads keep their classic prefixes (`pong ...`,
`status=...`, `sent_idx=...`), and daemon acks carry `ok=true`
(`ok=true shutting_down=true`, `ok=true item=7 queue_len=2`).
Only the transport layer keeps the legacy `ERR:` prefix — framing
violations rejected before dispatch (e.g. `ERR: command too large`).

`--ipc-json` (client side) parses replies into one-line JSON: kv
tokens become keys; the trailing free-text `text=` or `error=` field
keeps its spaces; unknown shapes degrade to `{"raw": reply}`.

## 3. Commands

Payload text is UTF-8; command names are case-insensitive; `<json>`
payloads are single compact JSON objects.

### `ping`

- Reply: `pong version=<version> uptime=<seconds>`

### `play <json>` (blocking)

Queued playback: maps to `enqueue(priority=working, policy=queue)` —
a plain play never rejects with busy; it waits its turn and the
connection blocks until **its own** playback ends. A `no_play`
request owns no audio and runs directly on the connection thread.

Request fields (all optional except the source): `text` (string),
`file` (path), `chain` (list of paths; see §7) — exactly one source —
plus `voice`, `rate`, `volume`, `pitch`, `provider`, `openai_key`,
`openai_base_url`, `openai_model`, `eleven_key`, `eleven_model`,
`piper_model`, `auto_lang`, `podcast`, `podcast_title`, `stream`,
`output_file`, `no_play`, `label`, `highlight`, `autoscroll`,
`bionic`, `zen`, `playback`, `winhost_host`, `winhost_port`,
`auto_rewind_sec`, `persist_name`, `chain_gap_ms`.

Replies:

- `status=done` — playback finished naturally.
- `status=stopped` — playback was cut (stop/preempt/termination).
- `ok=false error=<...>` — typed error (§4), including the synthesis
  failure of the item itself.

### `enqueue <json>` (fire-and-forget)

The play JSON plus queue-control fields; returns immediately,
playback is daemon-owned:

- `priority`: `blocked` | `done` | `working` (default `working`;
  order blocked > done > working, FIFO within a level).
- `policy`: `preempt` | `queue` | `coalesce` (default `queue`).
  `preempt` from a strictly higher priority cuts the active
  announcement; equal or lower priority degrades to `queue`.
  `coalesce` merges same-`event_type` items enqueued inside the
  coalescing window (anchored at the first pending item per event
  type, default 5 s) into ONE synthesized announcement
  (`"N <event_type>: a, b, c and X more"` — up to three identifiers,
  then a count).
- `event_type`: string, the coalescing key (empty = never merges
  with typed windows).
- `identifiers`: list of strings/numbers rendered into the merged
  announcement.

Reply: `ok=true item=<id> queue_len=<pending count>` plus
`coalesced=<n>` when the event merged into an open window (n ≥ 2 is
the merge count). `queue_len` counts pending items only.

### `status`

Idle (no active session):

```
status=idle uptime=<sec> provider=<last used|none> playback=<startup target> queue_len=<n> queue=<snapshot>
```

During playback (the session kv line with daemon fields injected
before the final free-text `text=` field):

```
status=<state> pos=<sec> total=<sec> sent_idx=<i> para_idx=<j> sentence=<text> provider=<p> voice=<v> uptime=<sec> queue_len=<n> queue=<snapshot> text=<sanitized sentence>
```

`queue=` is the `QueueSnapshot` JSON (§5): compact, `ensure_ascii`,
and **every whitespace byte `\uXXXX`-escaped** (e.g. spaces as
`\u0020`), so the whole value is one whitespace-free token for
key=value parsers while any JSON consumer restores the strings with a
plain `json.loads`.

### `shutdown`

- Reply: `ok=true shutting_down=true` — orderly exit (also
  SIGTERM/SIGINT). In-flight plays finalize with their blocking
  replies; new plays are refused with `daemon shutting down`.

### `stop`

- Idle: `status=stopped` (idempotent silence — "be quiet" already holds).
- Active: `status=stopped` — cuts the **active** announcement;
  pending items follow in queue order (full queue-flush is a
  client-level concern).

### Session control commands (require an active session)

Dispatched to the active playback session; replies embed the session
status line (daemon fields injected as in `status`):

| Command | Reply |
|---|---|
| `pause` / `resume` / `toggle-pause` (`toggle_pause`) | `status=<state> ...` (full status line) |
| `seek +10` / `seek -10` (delta seconds) | `status=...` (invalid delta: typed error) |
| `rewind` / `forward` | aliases of `seek -10` / `seek +10` |
| `next-sentence` / `prev-sentence` (also `_`) | `status=<state> pos=<sec> sent_idx=<i> sentence=<text>` or `status=playing at_end=true` / `at_start=true` |
| `next-paragraph` / `prev-paragraph` | same shape with `para_idx=` / `paragraph=` |
| `sentence` (`current-sentence`) | `sent_idx=<i> start=<sec> end=<sec> text=<text>` or `sent_idx=-1 text=` |
| `paragraph` (`current-paragraph`) | `para_idx=<i> start=<sec> end=<sec> text=<truncated 80>` or `para_idx=-1 text=` |
| `highlight` (`current-highlight`) | ANSI-rendered current sentence |
| `scroll-info` (`autoscroll-info`, `autoscroll`) | `pos=.. total=.. pct=.. sent_idx=.. total_sents=.. para_idx=.. total_paras=..` |

With **no active session**, every control command except `status`
(idle line) and `stop` (idempotent) is refused:
`ok=false error=no active playback session`.

Resume after a pause ≥ 2 s auto-rewinds `auto_rewind_sec` (default
2.0 s) — a session behavior, part of the frozen surface.

## 4. Error-text catalogue

Daemon-level typed errors (`ok=false error=...`):

| Error text | When |
|---|---|
| `empty command` | blank command line |
| `no active playback session` | control command against an idle daemon |
| `invalid play payload: <json error>` | `play` argument is not a JSON object |
| `play requires text, file, or chain` | play/enqueue without any source (text changed in T6 from `play requires text or file`) |
| `invalid enqueue payload: <json error>` | `enqueue` argument is not a JSON object |
| `identifiers must be a list of strings` | non-list / non-scalar identifiers |
| `unknown priority label: '<label>'` | priority outside blocked/done/working |
| `unknown policy label: '<label>'` | policy outside preempt/queue/coalesce |
| `enqueue does not support no_play (use play)` | synthesis-only request on `enqueue` |
| `chain must be a list of file paths` | `chain` not a non-empty list of non-blank path strings |
| `play accepts one of text, file, or chain, not a combination` | `chain` combined with `text` or `file` |
| `chain_gap_ms must be a non-negative number of milliseconds: <value>` | negative or non-numeric gap |
| `no_play cannot combine with chain (a chain owns no synthesis)` | `no_play` + `chain` |
| `daemon shutting down` | play/enqueue racing the shutdown snapshot |
| `playback target unavailable (exit <n>)` | speak-path request whose remote target cannot be built (replay `file`/`chain` degrades to local instead, with one stderr warning) |
| `<provider/synthesis error>` | the item's own synthesis/playback failure (free text, e.g. provider exceptions) |

Session-level errors (raised `ERR: ...` in-process, converted to
`ok=false error=...` at the daemon boundary — the daemon never leaks
`ERR:` from dispatch):

| Error text | When |
|---|---|
| `seek requires delta in seconds, e.g. seek +10 or seek -10` | `seek` without an argument |
| `invalid delta <value>` | non-numeric seek delta |
| `unknown command '<action>'` | unrecognized action with a session active |
| `command '<action>' is not supported for <target> playback (supported: status, pause, resume, toggle-pause, stop, highlight, scroll-info, sentence, paragraph)` | navigation on wsl-ps/winhost (see §10) |

Transport-level (`ERR:` prefix, rejected before dispatch):

| Error text | When |
|---|---|
| `ERR: command too large (payload of <n> bytes exceeds the 16777216-byte cap)` | client-side local cap check before any wire write |
| `ERR: command too large (frame announces <n> bytes, over the 16777216-byte cap)` | server-side header rejection |

Client-side transport failure (no daemon reachable / connection
lost): `send_ipc_command` and friends return an opaque `None` (§10,
known limitation); the CLI prints `Error: No active audio playback
session found` (control commands) or the daemon-unavailable message
(speak delegation) on stderr and exits 1.

## 5. Queue snapshot reference

`status`'s `queue=` value is `QueueSnapshot.as_dict()`
(`agent_tts/queue_manager.py`), keys in this order:

- `queue_len`: int — pending item count (excludes the active item).
- `pending`: list in **dispatch order** (blocked → done → working,
  FIFO within a level); each item: `id` (monotonic int), `priority`
  (`blocked`/`done`/`working`), `policy` (`queue`/`preempt`/
  `coalesce`), `event_type` (string), `identifiers` (list of
  strings), `coalesced` (int, ≥ 1; the merge count), `enqueued_at`
  (float, CLOCK_MONOTONIC seconds), `announcement` (string — the
  text that will be spoken; a coalesced owner carries the merged
  announcement).
- `active`: `null` or `{id, priority, policy, event_type, coalesced,
  started_at}`.
- `failed`: bounded history, oldest first, last 20
  (`DEFAULT_FAILED_HISTORY_LIMIT`); each: `{id, priority,
  event_type, error, failed_at, wedged}` — `wedged=true` marks
  watchdog terminations (liveness budget exceeded).
- `last_error`: string or `null` — the most recent failure text.
- `completed_count`, `failed_count`, `interrupted_count`,
  `wedged_count`: lifetime ints (completed / failed / cut by
  stop-or-preempt / cut by watchdog).

Counter semantics: queue-side terminations (stop, preempt,
watchdog) are semantically cuts — a preempted announcement counts as
`interrupted`, never `completed`; natural ends count `completed` (a
wsl-ps natural drain reports runner outcome `stopped` internally but
counts as completed).

## 6. Client-side contract (CLI)

- **stdout/stderr discipline:** any `ok=false` or `ERR:` reply prints
  its message to **stderr** as `Error: <message>` and exits `1`
  (`--ipc-json` prints the JSON error object on stderr instead);
  success payloads print to **stdout** with exit `0`. A host parsing
  stdout never sees error text. (Breaking vs pre-BLOQUE 1.3: idle
  control commands used to print their error text on stdout.)
- **Exit codes:** `0` success; `1` daemon/playback error or
  transport failure; `2` client-side usage validation (unknown
  `--priority`/`--policy` label, `--no-play` with queue flags,
  `--chain-gap` misuse) — validated before the daemon is contacted;
  `130` Ctrl-C during a delegation (after the best-effort stop for a
  blocking play; an enqueued event forwards **no** stop — it owns no
  audio yet).
- **Enqueue ack line:** with `--priority`/`--policy` the CLI switches
  onto fire-and-forget `enqueue` and prints
  `queued: item=<id> position=<n> queue_len=<m> [coalesced=<k>]`
  on stdout when the event waits behind others; silence + exit 0
  when it dispatches immediately.
- **Human status rendering:** plain `--ipc-cmd status` renders the
  snapshot (kv line first, one indented line per pending item:
  `queued[i] priority=… policy=… [coalesced=N]
  text=<truncated announcement>`); `--ipc-json` keeps the raw
  snapshot; a foreign classic owner's reply (no `queue=` token)
  prints unchanged.
- **Default mapping (documented, overridable):** a plain invocation
  is `working`/`queue` — the request queues behind any active
  announcement and blocks until its own playback ends (classic
  semantics, never a busy error). Either flag overrides the mapping
  per invocation. CLI-originated coalesce requests carry no event
  type, so they merge with each other within the window.
- **Health handshake:** speak delegations ensure a healthy daemon
  (200 ms ping gate, transparent auto-start, kill-and-respawn of a
  wedged owner). Control commands never auto-start a daemon.

## 7. Chain fields

`play` and `enqueue` payloads accept:

- `chain`: non-empty JSON list of audio file path strings, played
  back-to-back **in one single audio session** in order. Mutually
  exclusive with `text` and `file`; cannot combine with `no_play`.
- `chain_gap_ms`: non-negative number of milliseconds of silence
  inserted **between** items (default `0` — gapless, nothing
  inserted).

Semantics frozen with the fields:

- The chain enters the queue as **ONE item** with the standard
  priority/policy machinery; the blocking `play` keeps its
  `status=done`/`status=stopped` reply.
- One continuous buffer → one stop flag: a queue-side termination
  (stop/preempt/watchdog) cuts mid-audio; remaining files never play.
- All controls address **chain-global** positions through the
  combined `BoundaryMap`: seek, pause/resume, sentence/paragraph
  navigation span the whole chain (a `next-sentence` hop crosses file
  boundaries; each file without boundary metadata is one navigation
  unit named by its basename).
- All files must share one sample format (rate/channels/width);
  mismatched files are a typed failure at dispatch (visible in
  `status`'s `failed` history and `last_error`). Resampling is out of
  scope.
- The CLI validates paths client-side (missing file → stderr, exit 1,
  before the daemon is contacted).

## 8. Daemon tunables (not wire changes, documented for completeness)

| Knob | Default | Flag / env |
|---|---|---|
| Coalescing window | 5 s | `--coalesce-window SEC` / `AGENT_TTS_COALESCE_WINDOW` |
| Wedged timeout (liveness budget) | 30 s | `--wedged-timeout SEC` / `AGENT_TTS_WEDGED_TIMEOUT` |
| Idle timeout (auto-started daemons) | 1800 s | `--idle-timeout SEC` / `AGENT_TTS_IDLE_TIMEOUT` (`0` disables; explicit starts have none by default) |

Silent non-streaming synthesis emits no progress tokens: size the
wedged timeout above your worst-case synthesis time.

## 9. Daemon stderr trace lines (observability seam)

The daemon prints `agent-tts-queue: ...` lines on stderr
(`CLOCK_MONOTONIC` timestamps):

- `dispatch item=<id> label=<label> prio=<priority> coalesced=<n> t=<sec>`
- `finalize item=<id> outcome=<completed|failed|interrupted|wedged> t=<sec>`
- `chain-item item=<id> index=<i> pos=<sec> t=<sec>` — when a chain's
  playback cursor crosses item i's chain-global start (item 0
  included: chain playback began).

These are the seams the registered measurement harnesses consume
(`scripts/queue_metrics.py`, `scripts/chain_metrics.py`);
`audio-start`/`audio-end` lines come from harness-side play-seam
instrumentation, not from the daemon.

## 10. Known limitations at freeze

1. **winhost v1 preemption:** the Windows host server keeps protocol
   v1 semantics — a new PLAY preempts the current one; there is no
   server-side queue. Queue contract tests and the 0-overlap metric
   are scoped to local + wsl-ps; the winhost v2 decision is a
   deferred open question of AT-08 for the whole package.
2. **wsl-ps / winhost navigation controls:** position jumps (seek,
   next/prev sentence/paragraph) answer their pre-existing
   `command '<action>' is not supported for <target> playback ...`
   error there — PCM already streamed cannot be un-played. Pause/
   resume/stop work. Pre-existing, documented, unchanged by 1.3.
3. **Transient refusal in the bind→listen window (T5.1 finding):**
   a client connecting between a daemon's bind and its listen can see
   a transient `ConnectionRefused` — real clients under load hit this
   ~2× per full-suite run. Client-side bounded retries are the
   accepted workaround (the milestone tests do exactly that);
   enqueue is deliberately never retried (an ambiguous ack cannot be
   distinguished from a lost one).
4. **Opaque `None` on transport failure:** `send_ipc_command` and
   the delegation helpers return `None` for every transport failure
   class (refused, timeout, closed mid-frame) — no typed
   distinction. Registered as a PRODUCT finding, not fixed at
   freeze; callers must treat `None` as "exchange failed, state
   unknown".
5. **kokoro-dependent metrics pending:** the kokoro provider is not
   installed in the measurement environment; the RAM kokoro bound
   (`< 700 MB` warm) and kokoro legs of the latency measurements
   remain pending a real kokoro environment (deliberate: no packages
   were installed for this measurement).
6. **8 h smoke pending:** the full-duration resting smoke
   (`RNF-AT-04-4`) runs after this freeze via
   `PYTHONPATH=src .venv/bin/python scripts/daemon_smoke.py --duration-sec 28800 --interval-sec 60`
   — exit 0 = RSS growth ≤ 5%, every ping answered, always idle;
   results land as a JSON report on stdout to be registered under
   `metrics/` and referenced from the BLOQUE 1.2 notes.

## 11. Changes taken before the freeze (approved exceptions)

- **Framing v2 replaced the newline line-protocol** (BLOQUE 1.3 T1):
  a conscious non-additive transport change, approved as an exception
  to the 1.2 additivity promise because the only consumers (this
  repo's CLI and daemon) ship together. Recovery from a stale v1
  daemon is automatic (wedged classification → kill-and-respawn).
- **`play requires text or file` → `play requires text, file, or
  chain`** (T6): the empty-payload error text changed with the chain
  fields; same `ok=false error=` schema.
- **Idle control commands no longer print error text on stdout**
  (T4): the client error discipline moved them to stderr + exit 1.

---

*Source of truth: the modules named in the header. If this document
and the code disagree, the code is the contract and this document has
a bug — post-freeze, that disagreement is itself a breaking-change
decision (see the freeze statement).*
