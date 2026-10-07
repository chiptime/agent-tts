# agent-tts STT Engine Contract v1

> Normative reference for the engine-owned speech-to-text subsystem
> (`agent_tts.stt`, console entry `agent-tts-stt`). Independent of the
> frozen playback IPC v2 — that channel, its socket and its locks are
> never touched.

## Quick path

1. `pip install 'agent-tts[stt]'` (optional extra; base TTS never imports it)
2. `agent-tts-stt pull` — the ONLY model download operation
3. `agent-tts-stt serve` — resident worker; auto-warms on start (no manual warmup)
4. `agent-tts-stt status` / `agent-tts-stt transcribe --file clip.webm`

## Verified-by map

| Contract section | Enforced by |
|---|---|
| Model policy (offline, snapshot, pull-only) | `test_stt_transcriber.py` (`TestResolveOfflineAssets`, `TestRuntimeAssetSnapshot`, `TestRealLoaderOfflineContract`, `TestModelIsCached`, `TestPull`) |
| Health states / auto-warmup / recovery / hung startup | `test_stt_worker.py` (`TestWorkerProtocol`, `TestLifecycleBounds`), `test_stt_cli.py` (`TestServeSubprocessLifecycle`) |
| Framing, ops, typed errors | `test_stt_worker.py` (`TestFraming`, `TestWorkerProtocol`, `TestAudioBoundary`, `TestLifecycleBounds`) |
| Endpoint security | `test_stt_worker.py` (`TestRuntimeDir`, `TestSocketOwnership`, `TestClientTargetValidation`) |
| CLI surface / exit codes | `test_stt_cli.py` (`TestHelpSmoke`, `TestStatus`, `TestTranscribe`, `TestPull`, `TestServe`, `TestServeSubprocessLifecycle`, `TestPackagingMetadata`) |

## Model policy

| Rule | Behavior |
|---|---|
| No auto-download | Import, boot, probe, warmup and requests never fetch. `pull` is the sole download path (`pull_model`, tests always mock it). Any cache/probe race ends in a typed `model_unavailable`, never a fetch. |
| Local-only loading (runtime snapshot) | The constructor NEVER receives the mutable cache directory. `resolve_local_model` verifies the source assets (absolute dir, `config.json`+`model.bin`+`tokenizer.json`; regular-file paths are typed `invalid_config`; incomplete dirs are refused), then `snapshot_for_runtime` hardlinks every entry into a private 0700 runtime snapshot dir (byte-copy fallback only for cross-filesystem caches — a documented one-time weight copy) and THAT absolute path is retained until close. Dependency evidence (faster-whisper 1.2.1 transcribe.py:689-708): the tokenizer branch is selected by file presence in the model dir AFTER native construction, and its fallback `Tokenizer.from_pretrained("openai/whisper-tiny")` fetches while ignoring `local_files_only` — file presence in an owner-only dir we control makes the offline `from_file` branch structural, so concurrent source-cache deletion cannot flip it (hardlinked inodes stay alive). CT2's `files=` in-memory API was evaluated and REJECTED: per ctranslate2 4.8.2 docs it turns `model_path` into an identifier requiring ALL files (incl. weights) in memory — gratuitous weight copies. A source vanishing mid-snapshot raises typed `model_unavailable`. `local_files_only=True` is still passed as defense-in-depth on the dependency's non-directory branch; it is NOT what closes the tokenizer fallback. |
| Missing model | Typed `model_unavailable` naming `agent-tts-stt pull`. |
| Missing extra | Typed `missing_extra` naming `pip install 'agent-tts[stt]'` — including through the worker protocol and the CLI (exit 6). A missing hub library is reported as a missing extra, not as "run pull". |
| Defaults | model `small`, device `auto`, compute `auto` (reuse the verified brain defaults); env overrides `AGENT_TTS_STT_MODEL/_DEVICE/_COMPUTE`; invalid values → typed `invalid_config`. |
| Cache probe | Offline only: `config.json` + `model.bin` + `tokenizer.json` + a vocabulary file; hub unavailability during probe raises typed `missing_extra`; any other hub failure means absent. |

## Health model

`serve` starts a warmup driver automatically; production code never calls
`warmup()` by hand. States: `loading` → `ready` (successful load) or
`unavailable` (absent model / missing extra / failed load; `status`
includes the actionable `error` string). A load retry that succeeds
after a failure refreshes health to `ready` and clears the stale error
(recovery is reflected in `status`). `busy` reflects the inference slot
only. The warmup task is REGISTERED in shutdown accounting: a hung
startup counts in `hung` and makes the exit non-clean — the process
still exits bounded (daemon task; no cancellation of the native call is
claimed or attempted). Snapshot root: env `AGENT_TTS_STT_SNAPSHOT_DIR`,
default `<tempdir>/agent-tts-stt-models-<uid>` (0700, owner-verified).

## Residual, honestly-stated limits

- Cross-filesystem caches fall back to byte-copying snapshot entries
  (weights included) — one explicit copy per model, never a re-read of
  the mutable cache; same-filesystem snapshots are hardlinks (no copy).
- The snapshot/runtime dirs are protected against CROSS-USER access
  (owner-only 0700); a malicious SAME-UID process (or one able to write
  inside the owner dirs) is out of scope, including racing the
  non-atomic check-then-unlink socket teardown window and tampering with
  existing snapshot entries.
- Weights are hardlinked, not copied: they are immutable for the runtime
  as long as our link exists, but a same-uid actor replacing entries
  inside the private snapshot dir is not defended (same exclusion).
- A hung native load or inference is reported (`loading` state, `hung`
  exit accounting); it is never cancelled and the daemon threads die
  with the process.

## Transport and framing (STT-v1)

POSIX AF_UNIX only. Native Windows is a typed `transport_unsupported`
error (WSL2 is the supported Windows deployment).

```
+----------------+---------------+--------------------------+
| MAGIC "ASTT"   | VERSION = 0x01| LENGTH (4B, big-endian)  |
+----------------+---------------+--------------------------+
| PAYLOAD (LENGTH bytes, UTF-8 JSON object)                    |
+---------------------------------------------------------------+
```

| Constant (`agent_tts/stt/worker.py`) | Value |
|---|---|
| `FRAME_MAGIC` / `FRAME_VERSION` | `b"ASTT"` / `1` (integer; booleans rejected) |
| `MAX_PAYLOAD` | 48 MiB per frame — reserves base64 inflation (4/3 × 24 MiB = 32 MiB) plus JSON envelope overhead, so a maximum-legal audio request always fits (verified end-to-end at the exact cap) |
| `MAX_AUDIO_BYTES` | 24 MiB decoded audio per request |
| `HEADER_TIMEOUT_SEC` / `BODY_TIMEOUT_SEC` | 2 s server header deadline / 30 s per-recv body idle bound |
| `CLIENT_CONNECT_TIMEOUT_SEC` / `DEFAULT_READ_TIMEOUT_SEC` | 2 s connect / 120 s default client read |
| `MAX_CONNECTIONS` / inference concurrency / backlog | 8 connection threads (registry prunes finished threads) / 1 inference (model not thread-safe; busy reply otherwise) / 16 |

## Operations

One request frame, one reply frame per connection. Request envelope
`{"v": 1, "op": ...}` (client injects `v` automatically). Version is a
strict integer (bool rejected); version and op are validated before any
payload field; malformed field shapes (e.g. unhashable suffix) stay
typed `invalid_request`, never internal errors.

| Op | Request fields | Reply |
|---|---|---|
| `status` | — | `{"ok":true,"v":1,"state":...,"model":...,"busy":bool,"pid":...}` (+`error` when unavailable) — answered concurrently with any in-flight transcription |
| `transcribe` | `audio_b64` (strict base64), `suffix` optional (`.webm` default; whitelist `.webm .mp3 .wav .m4a .ogg .flac`) | `{"ok":true,"text":"..."}`; empty or >24 MiB audio → `invalid_request` |
| `shutdown` | — | `{"ok":true,"shutting_down":true}`; only the owning worker's state is honored |

Error replies are always `{"ok":false,"error":{"kind":...,"message":...}}`
with kinds `busy`, `model_unavailable`, `missing_extra`, `invalid_config`,
`invalid_request`, `worker_unavailable`. Messages never contain host
tracebacks or audio content.

## Endpoint security — actual guarantees

- Default runtime dir: a validated `XDG_RUNTIME_DIR` (real dir, owned by
  the current uid, not group/world-writable) → `$XDG_RUNTIME_DIR/agent-tts-stt`;
  otherwise a private uid-specific `<tempdir>/agent-tts-stt-<uid>` (mode
  0700). `AGENT_TTS_STT_RUNTIME_DIR` overrides; created dirs are exactly
  0700; symlinked/foreign/loose existing dirs are typed refusals.
- Explicit `--socket` / `AGENT_TTS_STT_SOCKET` endpoints additionally
  require an owner-only parent directory (owned by this uid, not
  group/world-writable) — world-writable parents (sticky `/tmp`) allow
  cross-user socket interposition and are rejected. Derived endpoints
  get this for free from the 0700 runtime dir.
- The socket is created 0600 via a umask-guarded bind (no name-based
  chmod window) and verified after bind (socket, no symlink, mode 0600,
  this uid); its `(st_dev, st_ino)` identity is recorded.
- A single owner is elected per endpoint by flock on `<socket>.lock`
  (the file itself persists; the kernel drops the lock on death). This
  coordinates cooperating peers.
- Startup never unlinks: a live OR stale occupied path is a typed
  `address_in_use` with exact remediation. A failed start (unsafe
  endpoint, occupied address, bind error such as an overlong AF_UNIX
  path) releases every resource it acquired — no phantom owner.
- Teardown unlinks ONLY when the path currently holds the exact socket
  inode this process bound; symlinks and non-sockets are never unlinked.
  Stated limits: the check-then-unlink pair is not atomic on Linux, so a
  SAME-UID actor racing that window could still swap the entry —
  cross-user interposition is what the owner-only parent prevents, and
  same-uid malicious interposition is explicitly out of scope. No
  automatic takeover of stale sockets, ever.
- Clients reject symlinked and non-socket targets before connecting; a
  missing socket is an actionable `worker_unavailable` naming
  `agent-tts-stt serve`. All client timeouts are finite.

## CLI surface (`agent-tts-stt`, `agent_tts.stt.cli:main`)

`pull [--model]` · `serve [--model --device --compute --socket --runtime-dir]`
· `status [--socket --timeout]` · `transcribe --file [--socket --suffix --timeout]`.
`status`/`transcribe` print ONE machine-readable JSON line on stdout
(errors included, plus a human hint on stderr). No client autostart; the
existing `agent-tts` positional-speech CLI is untouched (separate script).

| Exit code | Meaning |
|---|---|
| 0 | ok |
| 1 | generic error (incl. `endpoint_unsafe`, `address_in_use`, `invalid_request`, `internal_error`) |
| 2 | usage (argparse) |
| 3 | busy |
| 4 | model_unavailable |
| 5 | worker_unavailable |
| 6 | missing_extra |
| 7 | transport_unsupported |

Shutdown honesty: the serve exit is bounded; if a native inference call
is still running at the end of the grace window, the worker reports
`{"clean": false, "hung": N}` and exits 1 — it never claims cancellation
of the native call.
