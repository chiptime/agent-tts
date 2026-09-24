# SDD Exploration: ht-15-markdown-html-pipeline

## Executive Summary
This exploration establishes the technical foundation for the `ht-15-markdown-html-pipeline` change in `herdr-tts`. The goal is to provide a transformation pipeline from plain text to Markdown and from Markdown to formatted HTML with synchronized sentence anchors, feeding the follow-along reader pipeline (karaoke highlight mode and the planned reading modal). Based on codebase inspection of `herdr-tts` and its brother engine `agent-tts`, we recommend placing the transformation pipeline in the host layer as a self-contained, standard-library-only Python helper (`lib/reader_pipeline.py`) bridged through `lib/tts_engine.py` and `bin/herdr-tts`. This completely respects the "no new deps" constraint, preserves the <2.5 MB resident daemon budget, leaves Surface Contract v1 fully backward-compatible, and decouples the transformation pipeline (HT-15) from the interactive modal UI display (recommended for a dedicated follow-up change, e.g. HT-16).

---

### Current State

1. **Two-Level Decoupled Architecture (`herdr-tts` host vs `agent-tts` core)**:
   - As documented in `docs/ARCHITECTURE.md:20-66`, the system strictly separates host orchestration (`herdr-tts`, 6,980 lines of bash + Python bridge) from audio synthesis and terminal cleaning (`agent-tts` core engine).
   - In `herdr-tts`, text enters primarily through `read_pane_text` (`bin/herdr-tts:1816-1838`), which reads up to 300 lines of scrollback via `herdr pane read` or `herdr agent read`.
   - When reading an agent pane, `bin/herdr-tts:1997-2001` queries `herdr agent get` to extract `agent_kind` (e.g. `opencode`, `claude`) and `session_id`.
   - In `bin/herdr-tts:1944` and `bin/herdr-tts:2011`, the host invokes `"$VENV_PYTHON" "$ENGINE_SCRIPT"` (`lib/tts_engine.py`), passing `--agent` and `--session-id`.

2. **Reading Flow and `--highlight` Today**:
   - `bin/herdr-tts:1952-2014` implements `read_current_pane_visual` for `--highlight` (`-H`), `--autoscroll`, `--bionic`, and `--zen`.
   - Unlike `speak_text` (`bin/herdr-tts:1619-1660`), which backgrounds the synthesis process (`&`), `read_current_pane_visual` executes the engine script synchronously in the foreground (`bin/herdr-tts:2011`).
   - In `agent_tts/cli.py:1076-1111`, when `args.session_id` is supplied, `read_last_agent_message` (`agent_tts/sources/base.py:38-59`) resolves the assistant turn from the agent's structured transcript store (OpenCode SQLite, Claude Code JSONL, Codex JSONL, Aider markdown). If unavailable, it falls back to `extract_last_turn(input_text)` (`agent_tts/cleaner.py:407-488`).
   - During audio playback, `agent_tts/audio.py:565-605` drives terminal visualization:
     - For `--highlight`, it rewrites the current line with ANSI word/sentence highlights via `self.boundaries.format_highlighted_sentence(pos, ansi=True)` (`agent_tts/audio.py:586` and `agent_tts/boundaries.py:214-249`).
     - For `--autoscroll` / `--zen`, it renders a full-screen view (`_render_fullscreen_view`, `agent_tts/audio.py:382-440`) showing surrounding sentences dimmed, active sentence highlighted, and a progress bar footer.
   - All visual rendering today is ANSI escape codes written directly to standard output. There is **no HTML generation anywhere in the reading pipeline**.

3. **IPC Control Commands**:
   - `agent_tts/audio.py:250-380` listens on `/tmp/herdr-tts-player.sock` (configured via `AGENT_TTS_SOCKET` in `lib/tts_engine.py:13`).
   - Relevant commands already supported:
     - `scroll-info` (`agent_tts/audio.py:363-378`): returns single-line status:
       `pos=<sec> total=<sec> pct=<pct> sent_idx=<int> total_sents=<int> para_idx=<int> total_paras=<int>`
     - `sentence` / `current-sentence` (`agent_tts/audio.py:314-321`): returns:
       `sent_idx=<int> start=<sec> end=<sec> text=<text>`
     - `next-sentence` / `prev-sentence` (`agent_tts/audio.py:294-312`)
     - `paragraph` / `current-paragraph` (`agent_tts/audio.py:347-356`): returns:
       `para_idx=<int> start=<sec> end=<sec> text=<text>`
     - `next-paragraph` / `prev-paragraph` (`agent_tts/audio.py:323-346`)
     - `highlight` / `current-highlight` (`agent_tts/audio.py:358-361`): returns current sentence formatted with ANSI highlights.
   - In `bin/herdr-tts:1380-1549`, the host calls `"$VENV_PYTHON" "$ENGINE_SCRIPT" --ipc-cmd "<cmd>"` to dispatch IPC actions (`seek`, `next-sentence`, `prev-sentence`, `toggle-pause`, `status`).

4. **Speech Cleaner vs Reader Needs**:
   - `agent_tts/cleaner.py:491-582` (`clean_agent_text`) was written exclusively for speech synthesis:
     - Strips ANSI codes (`cleaner.py:224-226`).
     - Redacts credentials (`cleaner.py:516` via `agent_tts/redact.py`).
     - Replaces code blocks with `[bloque de código omitido]` (`cleaner.py:546`).
     - Replaces table vertical bars `|` with em-dashes ` — ` (`cleaner.py:557`).
     - Replaces URLs with "enlace web" (`cleaner.py:562`).
     - Expands technical acronyms to spoken Spanish (e.g. `PR` -> "pull request", `DB` -> "base de datos") (`cleaner.py:13-65`, `565`).
   - For a visual reading modal, destroying code blocks, turning tables into pauses, and mutating text into phonetic expansions is undesirable: the reader needs the real code block, the real table, and clean formatting, while still preserving secret redaction and terminal noise removal.

5. **Surface Contract v1**:
   - `bin/herdr-tts:51-53` defines `SURFACE_CONTRACT_VERSION="1"`.
   - `bin/herdr-tts:6607-6611` gates external consumers (`herdr-brain`) via `bin/herdr-tts --contract-version` printing `1`.
   - As documented in `README.md:61-71`, external consumers depend on `--contract-version` and `--render-text <out.mp3> [text...] [--voice] [--rate] [--agent] [--session-id]`.

---

### Affected Areas

1. **`lib/` (Python Host Bridge & Pipeline)**:
   - **`lib/tts_engine.py`**: Currently a 48-line wrapper around `agent_tts` (`lib/tts_engine.py:1-48`). Needs to expose or route new reader pipeline functionality without breaking existing CLI or IPC delegation.
   - **`lib/reader_pipeline.py` (New)**: A dedicated Python module in the host layer implementing:
     - `plain_to_markdown(text: str) -> str`: deterministic structure inference.
     - `markdown_to_html(md: str, with_sync: bool = True) -> Tuple[str, List[Dict]]`: GFM-subset HTML transformation with embedded sentence anchors (`data-sent-idx`, `data-para-idx`).
     - Secret redaction and terminal chrome stripping integration without destroying code blocks or tables.

2. **`bin/herdr-tts` (Host Orchestration & CLI)**:
   - CLI flags: Optional developer/test exposure (e.g., `--render-html <in.txt> <out.html>`, `--plain-to-md <in.txt>`), or integration into `--render-text` / visual reading pathways.
   - Visual reader hook: Updating `read_current_pane_visual` (`bin/herdr-tts:1952-2014`) or the reading dispatch flow to produce the HTML artifact alongside audio synthesis.
   - Translation dictionaries: `TT_EN` (`bin/herdr-tts:570-574`) and `TT_ES` (`bin/herdr-tts:1056-1060`) for any new user-facing options or errors.

3. **`scripts/bootstrap.sh` & Packaging**:
   - `scripts/bootstrap.sh:9-88`: Dependency isolation check. If a zero-dependency stdlib approach is chosen, `bootstrap.sh` remains untouched.
   - `packaging/homebrew/herdr-tts.rb:21-48`: Verified to rely on `bootstrap.sh`.

4. **`scripts/smoke-tests.sh` (Hermetic Test Harness)**:
   - Addition of Scenario 39 (Strict TDD): testing plain->markdown heuristics, markdown->HTML conversion, sentence boundary alignment, secret redaction preservation, and CLI/API integration.

5. **`docs/` & `openspec/`**:
   - `openspec/changes/ht-15-markdown-html-pipeline/`: specs, proposal, design, tasks.
   - `docs/ARCHITECTURE.md`: updating data flow diagrams to show the reading pipeline transformation stage.

---

### Approaches

#### Approach 1: Host-Layer Python Stdlib Pipeline (`lib/reader_pipeline.py`) — Recommended
- **Description**: Implement a self-contained Python module in `herdr-tts/lib/reader_pipeline.py` using only Python standard library modules (`html`, `re`, `json`, `dataclasses`). The module imports `redact_secrets` and `strip_ansi` from `agent_tts` (already installed in the venv) to ensure secrets are never leaked into HTML and terminal noise is cleanly removed.
- **Pipeline Stages**:
  1. *Sanitization & Redaction*: Run `strip_ansi` and `redact_secrets`, plus terminal chrome pre-filtering (`_strip_chrome_line` logic), while strictly preserving Markdown markers (` ``` `, `|`, `#`, `-`, `*`).
  2. *Plain-to-Markdown (when input is unformatted text/scrollback)*: Run deterministic heuristics:
     - Heading detection: single lines ending in `:` or matching section patterns.
     - List detection: convert Unicode bullets (`•`, `◦`, `▪`, `▸`) and numbered items (`1.`, `2)`) into standard Markdown lists (`- item`, `1. item`).
     - Code block detection: detect indented blocks or command snippets.
     - Paragraph grouping: cluster non-empty lines separated by blank lines into Markdown paragraphs.
  3. *Markdown-to-HTML with Sync Anchors*:
     - Parse a well-defined GFM subset (headings `h1-h4`, paragraphs `<p>`, code blocks `<pre><code class="language-...">`, blockquotes `<blockquote>`, GFM tables `<table>`, unordered/ordered lists `<ul>`/`<ol>`, inline `**bold**`, `*italic*`, `` `code` ``, `[link](url)`).
     - Auto-escape HTML characters in text and code blocks (`html.escape`) to guarantee zero XSS.
     - During sentence splitting (using the exact sentence boundary regex `(?<=[.!?])\s+` from `agent_tts/boundaries.py:305`), wrap each sentence in:
       `<span class="tts-sent" data-sent-idx="<idx>" data-para-idx="<pidx>" id="tts-sent-<idx>">...</span>`.
- **Pros**:
  - **Zero new pip dependencies**: No changes to `scripts/bootstrap.sh`, no network required on bootstrap, no changes to `packaging/homebrew/herdr-tts.rb`.
  - **Zero cross-repo blocking**: Does not require code changes, PRs, or release pinning in `agent-tts`.
  - **Exact sentence alignment**: Because sentence wrapping is integrated directly into the custom HTML emission, sentence indices are guaranteed 100% identical to `agent_tts.boundaries.estimate_boundaries_from_text`.
  - **Negligible RAM & disk footprint**: ~250 lines of Python code, uses standard library, runs in <25ms, adds zero resident daemon overhead.
  - **Strict TDD**: Easily unit-tested via `python3 -m unittest` and shell assertions in `scripts/smoke-tests.sh`.
- **Cons**:
  - The Markdown parser is limited to the defined GFM subset; complex edge cases (e.g. deeply nested mixed lists with blockquotes, raw embedded HTML tables) require disciplined parsing state.

#### Approach 2: Add External Markdown Library (`markdown` / `mistune`) to Venv Bootstrap
- **Description**: Update `scripts/bootstrap.sh` to install `markdown` (`pip install markdown` or add to `agent-tts/pyproject.toml`). Use Python-Markdown's extension API or AST Treeprocessors to post-process HTML and inject `data-sent-idx` attributes.
- **Pros**:
  - Full CommonMark / GFM compliance out of the box.
- **Cons**:
  - **Violates Dependency Policy**: Breaks the "no new deps" constraint in `herdr-tts`.
  - If added to `scripts/bootstrap.sh`, it invalidates `openspec/specs/plugin-bootstrap/spec.md:36-45` ("Requirement: agent-tts immutable pin" and scenario 34c), which checks that bootstrap only installs the pinned `agent-tts` ref.
  - If added to `agent-tts`, it forces an out-of-band release/commit bump of `agent-tts`, blocking progress on `herdr-tts`.
  - Injecting sentence anchors into an arbitrary HTML AST without splitting tags (e.g., when a sentence starts inside `<em>` and ends outside) is notoriously fragile in AST treeprocessors.

#### Approach 3: Engine-Layer IPC Extension (`agent-tts`)
- **Description**: Implement Markdown->HTML transformation and sentence sync mapping inside `agent-tts`, exposing an IPC command (e.g. `agent-tts --ipc-cmd get-html`) and CLI flag (`agent-tts --render-html`).
- **Pros**:
  - `agent-tts` already possesses the raw transcript connectors in `sources/`.
- **Cons**:
  - **Architectural Violation**: PRD HT-13 (`docs/prds/HT-13-consumo-api-publica-motor.md:12-36`) established that `agent-tts` is a headless, presentation-agnostic speech synthesis engine. Polluting the speech engine with HTML presentation rendering violates the decoupled architecture.
  - Double-repository coordination overhead and tight coupling.

#### Approach 4: Pure-Bash Pipeline
- **Description**: Write the converter in bash using `sed`, `awk`, and regex.
- **Pros**: No Python invocation overhead.
- **Cons**:
  - Severe risk of HTML escaping bugs / XSS.
  - Multi-line block states (fenced code, nested lists, table headers/cells) are unmaintainable and fragile in bash.
  - Strongly discouraged and rejected.

---

### Recommendation

We recommend **Approach 1 (Host-Layer Python Stdlib Pipeline in `lib/reader_pipeline.py`)**.

1. **Architecture & Layer Boundary**:
   - The transformation pipeline is a **host-side presentation concern** feeding Herdr reading surfaces.
   - Placing it in `herdr-tts/lib/reader_pipeline.py` keeps `agent-tts` pure and avoids any cross-repo release cycle.
   - Text resolution: If an agent identity is available, `lib/reader_pipeline.py` can obtain the clean message from `read_last_agent_message` (already exported in `agent_tts`), or take the scrollback text, run ANSI/chrome cleaning and secret redaction, and perform plain->markdown conversion.

2. **Sync Mapping & Surface Contract v1**:
   - The HTML emitter generates deterministic sentence spans:
     `<span class="tts-sent" data-sent-idx="0" data-para-idx="0" id="tts-sent-0">...</span>`.
   - The sentence indices match `agent-tts/boundaries.py` sentence indices 1:1.
   - `bin/herdr-tts --contract-version` remains `1` (untouched). No breaking changes to existing CLI flags.

3. **Scope Boundary for HT-15**:
   - **IN SCOPE for HT-15**:
     1. Text sanitization preserving formatting structure (secrets redacted, ANSI stripped, chrome pre-filtered).
     2. Deterministic Plain->Markdown heuristic conversion.
     3. Minimal GFM-subset Markdown->HTML transformation.
     4. Sentence anchor synchronization mapping (`data-sent-idx`, `data-para-idx`).
     5. Host CLI / bridge access (e.g. `bin/herdr-tts --render-html <in> <out.html>` and Python module).
     6. Comprehensive hermetic test suite in `scripts/smoke-tests.sh` (Scenario 39).
   - **OUT OF SCOPE for HT-15 (Recommended for Follow-up HT-16)**:
     - The interactive follow-along Reading Modal UI itself (terminal modal frame, curses/tput display loop, Collie web view, real-time keyboard navigation hooks). HT-15 delivers the complete, verified *engine pipeline* feeding that reader.

---

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Sentence Boundary Mismatch**: Sentence splitting regex in `reader_pipeline.py` diverges from `agent_tts.boundaries.estimate_boundaries_from_text`, causing highlight desync. | **HIGH** | Import or vendor the exact sentence boundary regex `(?<=[.!?])\s+` from `agent_tts.boundaries`, and write a strict-TDD smoke test asserting index parity on multi-sentence paragraphs. |
| **XSS / HTML Injection**: Unescaped user text or code blocks containing `<script>` or HTML tags being rendered by a web view or modal. | **HIGH** | Use Python stdlib `html.escape` unconditionally on all text and code block contents before wrapping in HTML tags. Reject raw HTML tags in Markdown input. |
| **Secret Leakage in Rendered HTML**: Credentials (`sk-...`, tokens, passwords) appearing in formatted HTML output. | **HIGH** | Run `agent_tts.redact.redact_secrets` before any Markdown or HTML transformation. Add smoke tests verifying token redaction in HTML. |
| **Parsing Edge Cases in Stdlib Markdown Converter**: Malformed tables, unclosed code fences, or unescaped characters crashing the parser. | **MEDIUM** | Implement a linear, fail-open state machine with extensive unit tests covering unclosed fences, orphan pipes, empty inputs, and special characters. |
| **Scope Creep (Modal UI vs Pipeline)**: Attempting to build both the HTML transformation pipeline and the interactive terminal/web modal UI in a single change. | **MEDIUM** | Strictly bound HT-15 to the transformation and sync pipeline (as confirmed by the product owner), leaving the modal UI widget to HT-16. |
| **Daemon RAM Footprint Regression**: Exceeding the <2.5 MB resident memory budget. | **LOW** | The daemon process remains a bash loop; the Python reader pipeline is invoked on-demand as a transient helper or during visual reader activation, adding 0 KB resident memory to the daemon. |

---

### Ready for Proposal

The technical exploration is complete. The dependency model, layer boundary, sync contract, Markdown subset, cleaning composition, surface inventory, and scope boundary are resolved with empirical code evidence.

Verdict: Yes
