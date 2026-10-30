# AT-07 — Digest de audio (`--digest`) · Execution pack (compact)

> Prepared by the `prd-loop` skill (planning only — nothing here launches implementation).
> Single canonical execution entrypoint. Every other doc is referenced by exact relative path.

- **Root (canonical):** `/home/bruno/Code/personal/agent-tts` — work lives in `engine/`
- **Entrypoint (this doc):** `odd/tasks/at-07-digest-audio.md`
- **Source PRD:** `docs/prds/AT-07-digest-audio.md` — sha256 `c42c146ba7283d6f2d7e9a8b10fc9665bf34acfca6586975244a485e58b2c21e`
- **`prd` skill dependency (resolved):** `/home/bruno/.config/opencode/skills/prd/SKILL.md`
- **Baseline:** `HEAD=a96b6dbd73dec44b260fe901dc877b04819b9c73`, clean tree on `main`
- **Readiness:** `ready_for_bootstrap` — D1 + A1 confirmed by the maintainer (2026-10-11); see **Gaps** (new files are FUTURE)

---

## Locked decisions (planner, after repo evidence)

- **D1 — Container vs chapters (the real tension).** The engine has **no MP3 encoder** and no tag library; providers emit raw MP3 (edge/openai/elevenlabs) or WAV (kokoro/piper). Therefore:
  - **All selected files are MP3** → output is an MP3 (raw frame join) and **ID3v2 CHAP chapters are embedded** (RF-AT-07-3).
  - **Any WAV present (mixed or all-WAV)** → decode every selected file to PCM (`miniaudio.decode`), concatenate, emit a **canonical WAV** (`_pcm_to_wav`), and **DO NOT embed CHAP** (a WAV cannot carry ID3v2 chapters); print a visible notice and degrade chapters to titles. This satisfies RF-AT-07-2 (true mixed tolerance) at the cost of chapters for WAV days.
  - *Why:* RF-AT-07-2 (tolerate mixed WAV/MP3) and RF-AT-07-3 (ID3v2 CHAP) are **mutually exclusive stdlib-only**. This lock preserves the headline chapters for the common MP3 path (edge is the daily provider) and gives real mixed tolerance otherwise. **Confirmed by the maintainer (2026-10-11, decision delegated to the planner).**
- **D2 — Selection.** No window/selector exists in `audio_store.py`. Add a read-only selector over the partitions (`<audio_dir>/YYYY-MM-DD/<epoch>-<pane><suffix>`; partition regex at `audio_store.py:43`, epoch from the filename) for `--since Nd` / `--date YYYY-MM-DD` (default today). Dedupe by path, order by epoch ascending. Empty window → clear message + non-zero exit (RF-AT-07-1/6). No mutation of source files.
- **D3 — Merge.** Do NOT reuse `merge_chunks_to_audio` blindly: it sniffs only `chunks[0]` (`audio_store.py:132-152`), so an MP3-first mixed list is a raw join that mishandles WAV. Digest merge is container-aware per D1. Insert `--gap-ms` silence (default 400) between turns (RF-AT-07-4): for MP3 join, prepend silence frames is not trivial → insert silence in the WAV path; for the MP3 path, a short silent MP3 segment isn't synthesizable stdlib-only → **assumption A1** (see below).
- **D4 — Chapters.** New stdlib ID3v2 writer (no tag lib exists): 10-byte `ID3` header + `CHAP` frames (element id, start/end ms, byte offsets `0xFFFFFFFF`, nested `TIT2`) + optional `CTOC`. Title = pane id + `HH:MM` from epoch. `--chapters turn|pane|none`, default `turn` (RF-AT-07-3). Only for MP3 output (D1).
- **D5 — Output.** `--output` explicit, or a generated name inside the store; never writes outside the store or `--output` (RNF-AT-07-4).
- **D6 — Podcast.** `--digest --podcast` → `PodcastFeed().add_episode(digest_bytes, title=f"Digest {date}", description=..., duration_sec=...)` (`podcast.py:83-128`); no schema change (RF-AT-07-5). `--podcast-serve` unchanged.
- **D7 — CLI.** Flat parser; add `--digest/--since/--date/--gap-ms/--chapters` beside `--podcast` (`cli.py:1334-1350`); dispatch as an early utility in `main()` (~`cli.py:1623`, before `--play-file`).
- **D8 — Podcast publication is an explicit target (reconciles RNF-AT-07-4).** RNF-AT-07-4 ("zero writes outside the store and `--output`") governs the **digest compilation** only; publication via `--podcast` legitimately persists into the podcast dir (existing `PodcastFeed`, D6). When a digest published via `--podcast` is **not MP3** (the WAV branch), print a visible warning: the feed declares `audio/mpeg` and hard-codes `.mp3` (`podcast.py:94,150`) while the payload is WAV — there is no stdlib transcoder.

## Assumptions (visible)

- **A1.** For the MP3-join path, `--gap-ms` silence cannot be synthesized stdlib-only; **lock:** the gap applies to the WAV path; on the MP3 path the gap is skipped with a visible notice (or use a 0-gap). **Confirmed (2026-10-11) — lock:** the gap applies on the WAV path; on the MP3 path the gap is skipped with a visible notice.
- **A2.** `--date`/`--since` windowing uses the date partition + epoch in the filename; a file whose epoch falls outside the partition date is still selected by its partition date (matches RF-AT-07-1's "ventana" by date+epoch).
- **A3.** Chapter list order == epoch order of selected files (metric: chapter count == selected turn count).

---

## Task registry (single authority)

### T-01 — Audio-store window selector
- Intent: read-only selection of store files for a window. Traces to RF-AT-07-1/2/6.
- Depends: none
- Edit allowlist: `engine/src/agent_tts/audio_store.py`, `engine/tests/test_audio_store.py`
- Contract: add `select_window(*, since_days=None, date=None, now=None) -> list[dict]` returning ordered `{epoch, pane, path, date}` (ascending epoch), deduped; empty → `[]`. Never mutates. Handles already-pruned/absent partitions as empty (RF-AT-07-6).
- Test creator: this task adds cases to `engine/tests/test_audio_store.py` (FUTURE until done)
- Evidence: tests for window boundaries, mixed providers, pruned/absent partitions, ordering, dedupe
- Gate: G-01
- Status: ✅ DONE — 21 new selector cases pass. The suite's single failure `tests/test_daemon.py::test_play_registering_during_shutdown_is_refused_not_orphaned` is a PRE-EXISTING flaky test: passes 3/3 in isolation and failed in the pre-implementation run — unrelated to T-01 (it does not touch `daemon.py`).

### T-02 — Digest compiler + ID3v2 CHAP writer
- Intent: compile the selected turns into one digest file with chapters/gap. Traces to RF-AT-07-2/3/4, RNF-AT-07-1/2/4.
- Depends: T-01
- Edit allowlist: `engine/src/agent_tts/digest.py` (new), `engine/tests/test_digest.py` (new)
- Contract: `compile_digest(files, *, gap_ms=400, chapters="turn", output=None) -> str` implements D1/D3/D4/D5. Container-aware merge; CHAP only on MP3; gap on WAV; zero writes outside store/`--output`.
- Test creator: this task creates `engine/tests/test_digest.py` (FUTURE until done)
- Evidence: tests for MP3-only (CHAP count == turns), WAV/mixed (valid WAV, chapters degraded + notice), gap insertion, no external writes, memory-bounded streaming
- Gate: G-01
- Status: ✅ DONE — 30 focused cases pass (`tests/test_digest.py`). The full suite shows ONE intermittent failure that is a DIFFERENT test each run (`test_daemon.py::test_play_registering_during_shutdown...`, then `test_chain_playback.py::test_chain_honors_stop_flag_mid_file...`); both pass 3/3 in isolation → pre-existing load flakes, unrelated to T-02 (new files only). Follow-up: stabilise the engine suite's playback/daemon timing tests.

### T-03 — CLI wiring
- Intent: expose `--digest` and friends. Traces to RF-AT-07-1/5, RNF-AT-07-4. (D7)
- Depends: T-01, T-02
- Edit allowlist: `engine/src/agent_tts/cli.py`, `engine/tests/test_digest.py`
- Contract: flags `--digest/--since/--date/--gap-ms/--chapters`; `--digest` dispatches before `--play-file`; `--digest --podcast` registers the episode via D6; empty window → clear message + non-zero exit.
- Test creator: existing — `engine/tests/test_digest.py` (created by T-02)
- Evidence: CLI tests for the flag group, empty-window exit, podcast registration, output path rules
- Gate: G-01
- Status: ✅ DONE — 57 focused cases pass; full suite **1223 passed, 0 failed**. Independent verification items 1–5 PASS. Findings resolved: (6) RNF-AT-07-4 reconciled with D6 via D8; (7) a WAV digest published via `--podcast` now emits a visible warning (feed is MP3-only; no stdlib transcoder).

### T-04 — Index/docs
- Intent: reflect AT-07 delivered. 
- Depends: T-03
- Edit allowlist: `docs/prds/README.md`, `docs/ESTADO-Y-PENDIENTES.md`
- Test creator: none (docs)
- Evidence: AT-07 status updated; HT-08 dependency note
- Gate: manual (docs readback)
- Status: ✅ DONE — `docs/prds/README.md` (counts + AT-07 row) and `docs/ESTADO-Y-PENDIENTES.md` (panel + Frente D) updated to reflect AT-07 implemented; HT-08 dependency noted.

---

## Gate registry (single authority)

| Gate | Command (literal) | cwd | Source policy | Exit meaning |
|---|---|---|---|---|
| G-01 | `PYTHONPATH=src ../.venv/bin/python -m pytest tests/ -q` | `/home/bruno/Code/personal/agent-tts/engine` | the engine suite (CI-equivalent: `.github/workflows/ci.yml:51-54` runs `python -m pytest tests/ -q` in `engine/`; here via the repo-root venv + `PYTHONPATH=src`, verified 2026-10-11 by `--collect-only`). | 0 = pass; nonzero = fail |

New test file `engine/tests/test_digest.py` is FUTURE until T-02 completes.

---

## Traceability (FR → task → gate)

| FR / RNF | Task(s) | Gate(s) |
|---|---|---|
| RF-AT-07-1 | T-01, T-03 | G-01 |
| RF-AT-07-2 | T-01, T-02 | G-01 |
| RF-AT-07-3 | T-02 | G-01 |
| RF-AT-07-4 | T-02 | G-01 |
| RF-AT-07-5 | T-03 | G-01 |
| RF-AT-07-6 | T-01 | G-01 |
| RNF-AT-07-1/2 | T-02 | G-01 |
| RNF-AT-07-4 | T-02, T-03 | G-01 |

Counts (verified by grep 2026-10-11): **4 tasks**, **1 machine gate (G-01)** + 1 manual check (T-04 docs readback), **9 requirements (6 RF + 3 RNF)** across **8 traceability rows**.

---

## Candidate binding (baseline; pre-work)

| File | sha256 |
|---|---|
| `docs/prds/AT-07-digest-audio.md` | `c42c146ba7283d6f2d7e9a8b10fc9665bf34acfca6586975244a485e58b2c21e` |
| `engine/src/agent_tts/audio_store.py` | `ef9e6bb71324c747fb9c9bfef29be51e7d4f0a632a80716184fd3709b069aaca` |
| `engine/src/agent_tts/cli.py` | `164e997b56843fffb60004cbe2828ae34ec2727894bf732ad317a2b659023164` |
| `engine/src/agent_tts/podcast.py` | `fb3710c8c48837cb90ac88d9852f3c21d1415caeda342e03d68e5b21acf1f49e` |
| `engine/tests/test_audio_store.py` | `32e614b28e08e9d3386833e07fb3c2e1e2884b93f1b6228221bb84b451812cdf` |
| `engine/tests/test_podcast.py` | `8c8334eb810d17d3dcfaaceccd679b2048f2ae9b50f19572f5ba9211121f99cb` |

`engine/src/agent_tts/digest.py` and `engine/tests/test_digest.py` do not exist yet (FUTURE — created by T-02).

## Checks run vs pending

- **Run:** `git rev-parse HEAD`, `sha256sum` of the bound files, engine `--collect-only` (23 tests collect via the root venv + `PYTHONPATH=src`).
- **Pending:** every gate (no digest code exists).

## Gaps blocking `ready_for_execution`

1. **FUTURE gate on the critical path:** `engine/tests/test_digest.py` (T-02).
2. **RNF-AT-07-3** (reproduce on two mobile podcast apps) is a **manual/physical** check, reported separately — not a machine gate.

Resolved — D1 and A1 confirmed by the maintainer (2026-10-11). The pack is `ready_for_bootstrap`, not yet `ready_for_execution`.

## Resume bookmark (maintained by the future executor)

```markdown
## Resume bookmark (maintained by the future executor)
- Pack identity: odd/tasks/at-07-digest-audio.md — sha256 pending finalization (pack is ready_for_bootstrap)
- Completed: T-01 (selector); T-02 (compiler + CHAP); T-03 (CLI wiring + WAV-podcast warning); T-04 (docs/index)
- Active task: (none) — all four tasks implemented; budget remaining: 2 remediation rounds per milestone
- Next action: close-out. Remaining work is manual: mobile playback validation (RNF-AT-07-3, two apps). HT-08 can now build on `--digest`.
```

## Unresolved choices

- None. D1 and A1 confirmed (2026-10-11).

## Launch request

- Repository root: `/home/bruno/Code/personal/agent-tts` (canonical)
- Pack entrypoint: `odd/tasks/at-07-digest-audio.md`
- Readiness: `draft`
- FRs covered: RF-AT-07-1..6 + RNF-AT-07-1/2/4 (count 9); RNF-AT-07-3 is manual
- Tasks: 4 — Gates: 1
- Non-goals: transcript/subtitles, editing, cloud upload, multi-machine aggregation
- Remediation budget: 2 functional rounds per milestone
- Checks already run: static (hashes, collect-only) — pending: all gates
- Limitations: this pack authorizes nothing; nothing runs until the user explicitly launches it.
