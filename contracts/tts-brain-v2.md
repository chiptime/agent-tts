# tts-brain surface contract — v2 (segmented rendering)

> Normative. v1 (`tts-brain-v1.md`) stays INTACT and untouched: everything
> in v1 keeps its exact semantics. v2 is a strictly ADDITIVE extension
> negotiated explicitly. "No private Python imports" brain↔host still holds:
> the brain consumes this surface ONLY through the versioned CLI.

## Negotiation

- `--contract-version` STILL prints exactly `1` (v1 is never rewritten or
  lied about). Consumers gate on it exactly as before.
- NEW `--contract-capabilities`: prints strict JSON
  `{"supported_protocols":[1,2]}` followed by one newline, exit 0. Nothing
  else on stdout. Unknown flags are NOT capabilities: a host without this
  flag is a v1-only host.
- Brain-side selection: parse `--contract-capabilities`; use the protocol
  list; flag missing/unparseable ⇒ v1 legacy path. Protocol versions the
  brain does not know are IGNORED (fail-soft): the brain activates only
  protocols it supports, ≤ its own maximum. v1-only host ⇒ legacy path and
  a VISIBLE degraded marker `speech.degraded: "segmented-unavailable"` on
  identified turns (the textual answer is NEVER blocked).

## Protocol 2: `--render-text-segmented`

```
herdr-tts --render-text-segmented <out-dir> <input-text-file> \
          --speech-request-id <ID> [--voice <voice>] [--rate <rate>]
```

- The TEXT travels by FILE (argv carries only server-generated paths and
  the opaque request id) — the reader discipline of v1 (`tts.py` reader
  path), extended to speech.
- `--speech-request-id` is an EXPLICIT CLI INPUT: it rides into the manifest
  verbatim; the producer never invents one.
- Production uses the ENGINE's sentence-group splitting
  (`agent_tts.split_sentence_groups` via the `lib/` bridge pattern); the
  host does not reimplement segmentation.
- Each segment is ONE COMPLETE atomic MP3 `seg-<seq, 04d>.mp3` (tmp file +
  rename); partial merges do not exist anywhere in the protocol. The v1
  "partial never persists" rule extends: a cancelled or failed producer
  leaves NO merged artifact, only already-published segments.

### `manifest.json` (published IN `out-dir`, per segment)

After EVERY completed segment the manifest is re-published atomically
(tmp + `os.replace`) with a monotonically increasing `revision`:

```json
{"speech_request_id": "...", "voice": "...", "rate": "...",
 "revision": 3, "is_complete": false, "cancelled": false, "error": null,
 "segments": [{"seq": 0, "file": "seg-0000.mp3", "bytes": 12345}]}
```

The TERMINAL manifest (`is_complete: true`, or `cancelled: true`, or
`"error": "..."`) is published at the end WITHOUT hiding the sequence
metadata already published. On cancel/error the producer deletes its own
tmp files and leaves published segments untouched (accounting only).

### Exit codes

- `0` — all groups rendered, terminal manifest complete.
- `3` — cancelled by the producer (stop checker); terminal manifest
  `cancelled: true` already published.
- any other non-zero — failure; terminal manifest carries `"error"`.
- no manifest at all — early failure (nothing produced).

## v1 surface (unchanged)

`--render-text <out.mp3> <text>...` keeps its exact argv and semantics
(PRD 02 FR-07): full-file rendering, text in argv, one blocking call.
