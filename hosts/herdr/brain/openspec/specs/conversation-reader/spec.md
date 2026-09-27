# conversation-reader Specification

## Purpose

Define on-demand formatted conversation rendering for the herdr-brain PWA reading surface (`#glance-turns`): a rendered-conversation endpoint returning per-turn anchored HTML plus the upstream `reader-pipeline/anchors@1` sidecar inline, content-addressed caching with bounded rendering, strict fail-soft degradation to plain text, safe client presentation with turn-scoped sentence selection, and the security and testing obligations that make this progressive enhancement releasable. Reader failures MUST never break text answers or speech.

### Contract Anchors (upstream, read-only reference)

- **Renderer invocation**: `herdr-tts --render-html INPUT OUTPUT --map MAP`, a transient process (no daemon, no socket). The CLI consumes a file whose content IS the whole document (leading headings preserved).
- **Exit codes**: `0` success (including coverage/empty/malformed input); `1` usage; `2` input unreadable / redaction failure (neither output file written); `3` agent_tts unavailable.
- **Sidecar** (`reader-pipeline/anchors@1`): JSON with `version` (1), `contract` (`reader-pipeline/anchors@1`), `alignment` (`exact` | `coverage`), `total_sents`, `total_paras`, `engine` `{lang, max_chars, summarize, lexicon_fp}`, and `sentences[]` (`sent_idx`, `para_idx`, `text`, `selector`, `block_ids`, `fragments`, `fragment_texts`, `exact`).
- **Anchored HTML**: primary fragments are `<span class="tts-sent" data-sent-idx="N" data-para-idx="P" id="tts-sent-N">`; continuation fragments are `<span class="tts-sent-cont" data-sent-idx="N">` (`data-sent-idx` only). Primary anchors may sit directly on container elements (`pre`, `table`, `ul`, `ol`); sentences without a renderable fragment degrade to empty virtual spans. Invariant: `count(.tts-sent) == total_sents`.
- **Staleness tuple**: anchor indices are valid ONLY for a speech call whose `(lang, max_chars, summarize=false, lexicon_fp)` equals `sidecar.engine`. Anchors carry no timing data.

## Requirements

## ADDED Requirements

### Requirement: Rendered Conversation Endpoint

The system SHALL expose `GET /conversation/{pane_id}/rendered` accepting a URL-encoded pane identifier with no query parameters in v1. For a resolved conversation it MUST return HTTP 200 with a JSON body of the shape `{pane_id, session_id, turns: [...]}`, where each turn is `{turn_id, role, text, html, map}`. Turns MUST preserve conversation source order. `text` MUST always be present as the fallback source. `html` and `map` are nullable and MUST be `null` together. An empty conversation MUST return `turns: []`. Requests for an unknown pane MUST follow the existing conversation-resolution error conventions (404) rather than being converted into reader-specific failures. The response MUST NOT include a `map_url` field or reference any separate sidecar endpoint.

#### Scenario: Successful rendered response

- GIVEN a resolved pane whose conversation has at least one turn and a working renderer
- WHEN the client requests `GET /conversation/{pane_id}/rendered` with the URL-encoded pane id
- THEN the response is HTTP 200 with `application/json` containing `pane_id`, `session_id`, and `turns` in source order
- AND each turn has non-null `turn_id`, `role`, and `text`
- AND turns that rendered successfully carry non-null `html` and a complete inline `map`

#### Scenario: Empty conversation

- GIVEN a resolved pane whose conversation has zero turns
- WHEN the client requests the rendered endpoint
- THEN the response is HTTP 200 with `turns: []`

#### Scenario: Unknown pane

- GIVEN a pane identifier that does not resolve to a conversation
- WHEN the client requests the rendered endpoint
- THEN the response follows the existing conversation-resolution error convention (HTTP 404)
- AND the failure is not converted into a rendered-turns fallback body

#### Scenario: No separate sidecar channel

- GIVEN any successful rendered response
- WHEN the client inspects the JSON body
- THEN no `map_url` field is present
- AND no separate sidecar endpoint is required to obtain anchor metadata

### Requirement: Turn Identity Within a Snapshot

The system MUST derive each `turn_id` deterministically from the resolved session, snapshot position, role, and content. `turn_id` identifies a turn within the returned conversation snapshot, not a durable transcript ID. Duplicate turn text MUST NOT produce colliding `turn_id` values within one snapshot.

#### Scenario: Duplicate text does not collide

- GIVEN a conversation snapshot containing two turns with identical role and identical text
- WHEN the rendered endpoint responds
- THEN the two turns carry distinct `turn_id` values

#### Scenario: Deterministic across repeated identical requests

- GIVEN an unchanged conversation snapshot
- WHEN the rendered endpoint is requested twice
- THEN both responses assign the same `turn_id` to each snapshot position

### Requirement: Inline Anchored Map Payload and Staleness Posture

On success, `map` MUST be the complete upstream sidecar object carried inline as JSON, including `version`, `contract: "reader-pipeline/anchors@1"`, `alignment` (`exact` or `coverage`), `total_sents`, `total_paras`, and `engine` metadata (`lang`, `max_chars`, `summarize`, `lexicon_fp`). The server MUST validate the sidecar's contract fields and engine metadata before publishing a rendered pair; an invalid or mismatched sidecar MUST be treated as a rendered failure (see Fail-Soft Rendered Fields). The system MUST NOT claim speech-index equivalence: anchor indices are valid only for speech invocations whose staleness tuple `(lang, max_chars, summarize=false, lexicon_fp)` equals `sidecar.engine`. v1 provides manual sentence selection only and MUST NOT implement or imply audio synchronization.

#### Scenario: Full sidecar travels inline

- GIVEN a turn rendered successfully by the renderer
- WHEN the rendered endpoint responds
- THEN `map` for that turn is the complete sidecar object inline in the same response, with `contract` equal to `reader-pipeline/anchors@1` and populated `engine` metadata

#### Scenario: Contract-mismatched sidecar is rejected

- GIVEN the renderer produced output whose sidecar has a `contract` other than `reader-pipeline/anchors@1`, a missing `engine` object, or `summarize` not equal to `false`
- WHEN the server validates the sidecar
- THEN the turn's `html` and `map` are both `null`
- AND `text` remains present and the request still returns HTTP 200

#### Scenario: Selection implies no synchronization

- GIVEN a mounted rendered turn with selectable sentence anchors
- WHEN the user selects a sentence
- THEN only a visual selection state within the reading surface is produced
- AND no playback, host player IPC, or synchronization call is made

### Requirement: Content-Addressed Render Cache

The system MUST cache rendered turn artifacts under a SHA-256 content-hash key computed over an unambiguous serialization that includes the turn text and the language (`es` in v1), namespaced by reader profile and contract version (`max_chars=0`, `summarize=false`, `reader-pipeline/anchors@1`). Caching MUST use a bounded in-memory LRU (128 entries) plus paired HTML/map disk artifacts under `settings.audio_dir.parent / "reader_cache"`. A cache hit MUST NOT spawn a renderer subprocess. Changed turn text MUST produce a new key and a new render. Missing or corrupt disk artifacts MUST be treated as cache misses, not application failures. Complete HTML/map pairs MUST be published atomically. Disk retention and concurrently executing renders MUST be bounded. A renderer upgrade MUST invalidate the cache namespace. An unwritable cache location MUST degrade fail-soft without failing the request.

#### Scenario: Warm hit spawns no subprocess

- GIVEN a turn previously rendered and cached for its exact text and language
- WHEN the rendered endpoint is requested again for the same unchanged turn
- THEN the response is served from cache
- AND no renderer subprocess is spawned

#### Scenario: Changed text misses the cache

- GIVEN a cached render for a turn's previous text
- WHEN the turn text changes and the rendered endpoint is requested
- THEN a new cache key is computed and a new render is performed

#### Scenario: Corrupt disk artifact is a miss

- GIVEN a disk cache artifact that is truncated, corrupt, or unparseable
- WHEN the rendered endpoint requests that turn
- THEN the artifact is treated as a cache miss and rendering is attempted again
- AND the request outcome still follows the endpoint contract (no application error caused by the cache)

#### Scenario: Concurrent cold misses stay bounded

- GIVEN multiple distinct uncached turns requested concurrently
- WHEN rendering proceeds
- THEN the number of concurrently executing renderer subprocesses stays within the configured bound

#### Scenario: Renderer upgrade invalidates namespace

- GIVEN cached artifacts produced by a previous renderer version or reader profile
- WHEN the renderer or the reader profile/contract version changes
- THEN previously cached entries are not served and the namespace is invalidated

#### Scenario: Unwritable cache degrades fail-soft

- GIVEN the cache directory cannot be created or written
- WHEN the rendered endpoint is requested
- THEN the request still returns HTTP 200 with the endpoint contract
- AND rendered fields follow the fail-soft rules instead of surfacing a cache error

### Requirement: Bounded Renderer Invocation

The renderer wrapper MUST invoke the existing CLI surface `herdr-tts --render-html INPUT OUTPUT --map MAP` as a transient process. It MUST pass the complete available turn text as a whole UTF-8 document (preserving leading headings), MUST enforce an execution timeout, MUST clean up temporary input files on every outcome (success, failure, or timeout), and MUST NOT import upstream private Python modules or invoke the upstream daemon or player.

#### Scenario: Whole-document input preserves leading heading

- GIVEN a turn whose text begins with a Markdown heading
- WHEN the wrapper invokes the renderer
- THEN the temporary input document contains the full text including the leading heading

#### Scenario: Timeout is bounded

- GIVEN a renderer invocation that exceeds the configured timeout
- WHEN the wrapper enforces the bound
- THEN the process is terminated
- AND the turn degrades per the fail-soft rules

#### Scenario: Temporary inputs cleaned on every outcome

- GIVEN a renderer invocation that succeeds, fails, or times out
- WHEN the wrapper completes
- THEN no temporary input files remain on disk

### Requirement: Fail-Soft Rendered Fields

For any turn, renderer absence, a non-zero renderer exit code (`1` usage, `2` input unreadable/redaction failure, `3` agent_tts unavailable), a timeout, a cache-validation failure, or a sidecar parse/validation failure MUST result in `html: null` and `map: null` together for that turn, with `text` preserved. The request MUST still return HTTP 200 with the plain turns. The system MUST log a warning naming the failure class without including transcript payloads. Speech synthesis and text answers MUST be unaffected by any reader failure.

#### Scenario: Renderer binary absent

- GIVEN the `herdr-tts` CLI is not installed or not found
- WHEN the rendered endpoint is requested
- THEN every turn returns `html: null` and `map: null` with `text` preserved
- AND the response is HTTP 200 with the plain turns and a warning names the missing renderer

#### Scenario: Non-zero exit codes handled per contract

- GIVEN the renderer stub exits `1` (usage), `2` (input unreadable/redaction failure, no output written), or `3` (agent_tts unavailable)
- WHEN the rendered endpoint is requested for each of those exit codes
- THEN each affected turn returns `html: null` and `map: null` together with `text` preserved
- AND the response is HTTP 200 with the plain turns

#### Scenario: Renderer timeout

- GIVEN a renderer invocation that exceeds the execution timeout
- WHEN the rendered endpoint is requested
- THEN the affected turn returns `html: null` and `map: null` with `text` preserved and the request returns HTTP 200

#### Scenario: Malformed sidecar JSON

- GIVEN the renderer produced HTML but a sidecar that fails to parse or validate
- WHEN the rendered endpoint is requested
- THEN the affected turn returns `html: null` and `map: null` together with `text` preserved
- AND the response is HTTP 200 with the plain turns

### Requirement: On-Demand Loading and Snapshot Discipline

The client MUST request rendered content for the viewed conversation's turns when the reading surface loads and on each subsequent refresh of that surface, so that formatted rendering is the default presentation. The client MUST NOT require an expansion gesture ("ver más") to trigger rendering; that gesture MUST remain purely a visual truncation control for very long turns. The client MUST display existing text immediately for any turn whose rendered content has not yet arrived, and MUST replace it with the formatted HTML once the response arrives (progressive cold fill across polls). The client MUST replace the reader snapshot as a unit and MUST discard stale rendered responses after a pane or session change rather than joining independent responses by array position.

#### Scenario: All turns formatted without expansion

- GIVEN a conversation whose turns are viewable in the reading surface
- WHEN the reading surface loads
- THEN every turn is mounted for formatted rendering without any expansion gesture
- AND turns whose rendered content is cached display formatted immediately

#### Scenario: Text first, upgrade later

- GIVEN a turn whose rendered content has not yet arrived (cold, over budget, or renderer failed)
- WHEN the turn is displayed
- THEN the existing text is shown immediately
- AND the formatted HTML replaces it only once the rendered response arrives

#### Scenario: Expansion no longer gates rendering

- GIVEN a turn that is visually truncated by the "ver más" control
- WHEN the reading surface loads or refreshes
- THEN the rendered content for that turn is requested and mounted regardless of the truncation state

#### Scenario: Progressive cold fill across polls

- GIVEN more cold turns than the per-request render budget
- WHEN the surface refreshes on its existing poll cycle
- THEN each poll renders up to the budget of still-cold turns
- AND every turn eventually displays formatted content without any expansion gesture

#### Scenario: Stale response discarded on surface change

- GIVEN a rendered-endpoint response in flight for pane A
- WHEN the user switches the viewing surface to pane B before the response arrives
- THEN the response for pane A is discarded and never merged into pane B's reader snapshot


### Requirement: Reader Mounting and Scoped HTML Insertion

The client MUST mount formatted content via a dependency-free UMD `reader.js` module inside dedicated reader containers within `#glance-turns`. Assignment of `innerHTML` MUST be restricted to dedicated `.reader-content` descendants of `#glance-turns` and MUST only ever receive server-rendered reader payload. Raw transcript text and raw Markdown MUST NOT be inserted as HTML; text rendering MUST use `textContent` assignments. `reader.js` MUST be loaded as a versioned static asset under the existing no-cache discipline.

#### Scenario: Formatted turn mounts inside reader container

- GIVEN a rendered response with non-null `html` for the viewed turn
- WHEN the reader mounts the turn
- THEN the HTML is inserted only into a `.reader-content` descendant of `#glance-turns`
- AND headings, emphasis, code, lists, and tables are rendered by the scoped reader styles

#### Scenario: Raw transcript never becomes HTML

- GIVEN any turn (rendered or not)
- WHEN the client renders it
- THEN transcript text is never assigned to `innerHTML`
- AND text-only rendering goes through `textContent` assignments

#### Scenario: Versioned asset with no-cache

- GIVEN the PWA index loads the reader module
- WHEN the asset request is made
- THEN `reader.js` is fetched as a versioned asset under the existing no-cache policy

### Requirement: Strict Plain-Text Fallback in the Client

The client MUST fall back to strict `textContent` rendering whenever `html` is `null`, the rendered request fails at the network level, or client-side insertion fails. The user MUST never be shown an empty message because of a reader failure.

#### Scenario: Null html falls back to text

- GIVEN a rendered response whose turn has `html: null`
- WHEN the client mounts the turn
- THEN the turn displays the original `text` via `textContent`
- AND no reader container is left empty

#### Scenario: Network failure falls back to text

- GIVEN the rendered-endpoint request fails (network or server error)
- WHEN the client handles the failure
- THEN the turn continues to display the existing text via `textContent`

#### Scenario: Insertion failure falls back to text

- GIVEN a mounted HTML insertion that throws or fails client-side
- WHEN the failure is caught
- THEN the turn falls back to `textContent` rendering instead of staying empty

### Requirement: Turn-Scoped Sentence Selection

The client MUST support manual sentence selection through `[data-sent-idx]` lookups scoped to the owning turn's reader container, because upstream anchor IDs restart at `tts-sent-0` per document. Selection MUST include `.tts-sent-cont` continuation fragments of the selected sentence. Selection MUST handle primary anchors located on container elements (`pre`, `table`, `ul`, `ol`), empty virtual spans, and both `exact` and `coverage` alignment modes. Selection is manual only and MUST never imply playback synchronization.

#### Scenario: Selection lights primary and continuations

- GIVEN a rendered turn whose sentence 3 has a primary `.tts-sent` fragment and additional `.tts-sent-cont` fragments in later blocks
- WHEN the user selects sentence 3
- THEN the primary fragment and every continuation fragment with `data-sent-idx="3"` within that turn are visually selected

#### Scenario: Selection is scoped to the owning turn

- GIVEN two rendered turns in the reader surface, each containing `data-sent-idx="0"`
- WHEN the user selects sentence 0 in the second turn
- THEN only the second turn's sentence-0 elements change selection state
- AND the first turn's elements are untouched

#### Scenario: Container-anchored sentence is selectable

- GIVEN a sentence whose primary anchor sits on a container element (`pre`, `table`, `ul`, or `ol`)
- WHEN the user selects that sentence
- THEN the container element receives the selection state

#### Scenario: Coverage alignment remains selectable

- GIVEN a sidecar and HTML whose alignment is `coverage` (anchors still tile every sentence)
- WHEN the user selects sentences in the rendered turn
- THEN selection works through the coverage-mode anchors without error

### Requirement: Hardened Reader Links

Every link inserted into reader content MUST be limited to `http` and `https` schemes and MUST be hardened with `target="_blank"` and `rel="noopener noreferrer"` before insertion. Links with other schemes MUST NOT be inserted as anchors.

#### Scenario: Inserted links are hardened

- GIVEN rendered HTML containing one or more `http(s)` links
- WHEN the reader inserts the HTML into a `.reader-content` container
- THEN every inserted anchor carries `target="_blank"` and `rel="noopener noreferrer"`

#### Scenario: Non-HTTP schemes do not survive as anchors

- GIVEN input text containing a link with a non-HTTP(S) scheme
- WHEN the rendered output is inserted
- THEN that content appears as plain text, not as an anchor

### Requirement: Content Security Policy on the PWA Index

The PWA index response MUST carry a Content-Security-Policy header with the directive set `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'`. Existing self-hosted scripts, versioned assets, audio streaming, SSE connections, and PWA loading MUST remain functional under the policy. The CSP is defense in depth and MUST NOT be treated as a substitute for upstream escaping and safe insertion.

#### Scenario: Index response carries the pinned CSP

- GIVEN the PWA index is requested
- WHEN the response headers are inspected
- THEN the Content-Security-Policy header is present with exactly the pinned directive set

#### Scenario: Existing PWA behavior remains functional

- GIVEN the pinned CSP is active
- WHEN the PWA loads its self-hosted scripts and versioned assets, streams MP3 audio (`media-src 'self' blob:`), and consumes SSE (`connect-src 'self'`)
- THEN all of these mechanisms remain permitted and functional

### Requirement: Non-Executable Metadata and No Inline Handlers

Anchor metadata (`map`) MUST be served only as JSON data in the API response, never embedded in an inline `<script>` or any executable context. Reader client code and assets MUST NOT introduce inline event handlers (for example `on*` attributes); event wiring MUST use programmatic listeners.

#### Scenario: Map is data only

- GIVEN a successful rendered response
- WHEN the response is inspected
- THEN `map` appears only inside the `application/json` body
- AND no page markup embeds the map inside an inline script

#### Scenario: No inline event handlers introduced

- GIVEN the reader surface and its versioned assets
- WHEN the document is inspected
- THEN no reader-introduced `on*` inline handler attributes exist
- AND all reader interactions are wired with programmatic event listeners

### Requirement: Test Suite Obligations

The Python suite (pytest with all subprocesses mocked) MUST extend the existing CLI stub to support `--render-html INPUT OUTPUT --map MAP` and MUST cover: the rendered-endpoint contract, cache-hit behavior proving no renderer subprocess is spawned on hits, and the fail-soft matrix (absent binary, exit codes 1/2/3, timeout, malformed sidecar, unwritable cache). The Node suite (`node --test tests/js/`) MUST independently cover `reader.js`: mounting into scoped containers, strict plain-text fallback, sentence selection including `.tts-sent-cont` continuations, and link hardening. Both suites MUST include a regression case proving malicious Markdown cannot execute scripts at the insertion boundary. Both suites are release-blocking per `openspec/config.yaml`, and the change MUST introduce no new runtime or development dependencies.

#### Scenario: Python suite covers endpoint, cache, and fail-soft matrix

- GIVEN the extended herdr-tts CLI stub supporting `--render-html`
- WHEN `.venv/bin/python -m pytest -q` runs
- THEN the suite asserts the endpoint contract, cache hits spawning no subprocesses, and the complete fail-soft matrix

#### Scenario: Node suite covers reader client behavior

- GIVEN `tests/js/reader.test.js` against the UMD `reader.js` module
- WHEN `node --test tests/js/` runs
- THEN the suite asserts mounting, textContent fallback, sentence selection including continuations, and link hardening

#### Scenario: Malicious Markdown regression

- GIVEN turn text crafted to inject scripts or event-handler attributes
- WHEN it is rendered and inserted through the reader path
- THEN no script execution vector is introduced at the insertion boundary
- AND the regression case fails the suite if the guard regresses

#### Scenario: Both suites gate release

- GIVEN the change is ready for release
- WHEN the test gates run per `openspec/config.yaml`
- THEN both `.venv/bin/python -m pytest -q` and `node --test tests/js/` MUST pass
- AND no new runtime or development dependency was added

## Out of Scope (Non-Goals — notes, not requirements)

- Live audio synchronization, audio-driven highlighting, or timestamp estimation: the sidecar carries no timing data; revisit only when upstream provides timing metadata.
- Host player IPC proxying, playback seeking, or estimated sentence timing.
- Markdown editing, a new full-screen reader, or changes to the call drawer and conversation switcher.
- New runtime or development dependencies, and any changes to the upstream `herdr-tts` repository.

### Requirement: Formatted Agent-Status Toast

The client toast that announces agent status or responses MUST render the announced text as reader HTML when a rendered snapshot for that exact text is available (content-keyed lookup, cache hit, no additional renderer invocation), and MUST fall back to plain `textContent` when it is not (watcher template avisos, renderer unavailable, any fail-soft condition). Formatted toast content MUST be excerpted by CSS clipping only — the client MUST NOT truncate HTML text by hand. Toast insertion MUST follow the scoped-insertion rules (pipeline output only, hardened links) and MUST NOT weaken any fail-soft, security, or CSP requirement of this capability.

#### Scenario: Formatted toast when HTML is available

- GIVEN an agent response whose text has a rendered snapshot in the reader cache
- WHEN the toast announces it
- THEN the toast shows the formatted HTML (e.g. bold and inline code rendered)
- AND no new renderer subprocess is spawned for the announcement

#### Scenario: Plain fallback for template avisos and failures

- GIVEN a watcher template aviso (never rendered) or a renderer failure
- WHEN the toast announces it
- THEN the toast shows the plain text via textContent, exactly as before this delta

#### Scenario: CSS excerpt only

- GIVEN a formatted toast whose HTML exceeds the toast height
- WHEN the toast is displayed
- THEN the content is clipped by CSS (max-height/overflow)
- AND no HTML manipulation (string cutting, tag stripping by regex) is performed on the payload

#### Scenario: Security rules inherited

- GIVEN any formatted toast insertion
- WHEN the payload is mounted
- THEN links are hardened (target=_blank, rel=noopener noreferrer) and insertion remains scoped to the toast container
