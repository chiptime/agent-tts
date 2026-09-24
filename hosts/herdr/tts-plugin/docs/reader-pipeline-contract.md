# Reader Pipeline Contract — HT-16 popup integration

Implementation-ready contract for the anchored-HTML reader pipeline (HT-15).
Everything below is guaranteed by `scripts/smoke-tests.sh` block 40.

## 1. What exists

- `lib/reader_pipeline.py` (public API via `__all__`):
  - `sanitize(raw) -> str` — ANSI-strip, **fail-closed redaction** (redaction errors
    propagate; no output is written), structure-preserving chrome filtering.
  - `plain_to_markdown(text) -> str` — deterministic (bullets → `- `, `X:` → `### X:`).
  - `markdown_to_html(md, anchors=None) -> str` — GFM subset, total escaping,
    http/https-only links; `anchors` decorates the output (below).
  - `build_anchors(redacted, *, lang="es", max_chars=0) -> tuple[SentenceAnchor, ...]`
    — for already-sanitized text; indices verbatim from the pinned engine.
  - `render(raw, *, lang="es", max_chars=0, pre_extracted=False) -> RenderResult`
    — full pipeline. **Input semantics:** scrollback by default
    (`extract_last_turn` runs — the pane-reading use-case); `pre_extracted=True`
    treats `raw` as the whole document (no extraction).
  - `render_to_files(raw, html_path, map_path=None, **kw) -> int` — exit-code
    contract: `0` success (incl. coverage/empty/malformed), `2` render or write
    failure (fail closed: neither file is written).
  - `mapping_json(result) -> str` — sidecar JSON (§2).
  - Dataclasses: `RenderResult(html, anchors, alignment, normalized, engine)`;
    `SentenceAnchor(sent_idx, para_idx, text, block_ids, fragments, exact, fragment_texts)`.
- `lib/tts_engine.py`: `_render_html_main` — transient `--render-html` bridge.
  The CLI consumes a **file whose content IS the document**: it calls
  `render_to_files(..., pre_extracted=True)` (a leading `# heading` is preserved;
  fixed post-HT-15, smoke 40h). Exit codes: `0` success, `1` usage,
  `2` input unreadable / redaction failure (no output file), `3` agent_tts
  unavailable. Process is transient — no daemon, no socket.

## 2. Sidecar JSON — contract `reader-pipeline/anchors@1`

```json
{
  "version": 1,
  "contract": "reader-pipeline/anchors@1",
  "alignment": "exact | coverage",
  "total_sents": 3,
  "total_paras": 3,
  "engine": {"lang": "es", "max_chars": 0, "summarize": false, "lexicon_fp": "…"},
  "sentences": [
    {
      "sent_idx": 0, "para_idx": 0,
      "text": "raw REDACTED sentence text (primary span)",
      "selector": "#tts-sent-0",
      "block_ids": ["…"],
      "fragments": [[0, 24]],
      "fragment_texts": ["…"],
      "exact": true
    }
  ]
}
```

`fragments` are `[start, end)` **char ranges over the markdown text** produced by
`plain_to_markdown` over the sanitized document — NOT over the raw scrollback.
`alignment`: `exact` when projected blocks match the engine enumeration
whitespace-exactly; `coverage` when a projection gate fails (e.g. tables
collapse) — anchors still tile every sentence, indices stay engine-verbatim.

## 3. HTML anchor rules

- Primary fragment of sentence N: `<span class="tts-sent" data-sent-idx="N"
  data-para-idx="P" id="tts-sent-N">`.
- Continuation fragments (sentence N appearing in later blocks):
  `<span class="tts-sent-cont" data-sent-idx="N">…</span>` — `data-sent-idx` only.
- Container elements (`pre`, `table`, `ul`, `ol`) carry the anchor attributes
  directly on the element when a single sentence covers them (a span cannot
  legally wrap flow content); extra fragments degrade to empty virtual spans.
- A sentence with no renderable fragment degrades to an empty virtual span
  (primary attrs, no text).
- Invariant: `count(.tts-sent) == total_sents` (exactly one primary per sentence).

## 4. Staleness

Anchors are valid ONLY for a speech call whose `(lang, max_chars,
summarize=false, lexicon fingerprint)` equals `sidecar.engine`. Before
highlighting, compare the sidecar tuple against the speech invocation that is
actually playing; on mismatch, re-render (or skip highlighting) — never
highlight against stale indices.

## 5. Popup integration recipe

1. At read time: `render_to_files(raw, html, map, lang=…, max_chars=…,
   pre_extracted=<True for file/document input; default for pane scrollback>)`
   (or in-process `render()`; load the sidecar it produces).
2. Verify §4 staleness tuple against the playing speech call.
3. Drive highlighting from `scroll-info` IPC (`sent_idx`, `total_sents`) onto
   `[data-sent-idx="N"]` — both the primary `.tts-sent` element and any
   `.tts-sent-cont` siblings (multi-fragment sentences light up several
   elements); `total_sents` cross-checks the sidecar.

## 6. Caveats

- `summarize`/`--tldr` rewrites text — unsupported for anchoring. The sidecar
  always records `summarize: false`; a summarized call is stale by definition.
- Leading-content bug (CLI dropped a top `# heading` via scrollback extraction)
  is FIXED: the CLI renders whole documents (`pre_extracted=True`, smoke 40h).
  Pane callers that pass scrollback keep default extraction intentionally.
