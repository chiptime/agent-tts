# Archive Report: PM-01 — One-Line Install & Packaging

**Change**: pm-01-install-packaging
**Archived**: 2026-09-22 (hybrid store: this repo tree + Engram mirror)
**Archived to**: `openspec/changes/archive/2026-09-22-pm-01-install-packaging/`
**Final verdict**: PASS (verify-report.md, including its 2026-09-22 Addendum)

This is the terminal record of the cycle. It describes the state of the change AT CLOSE, per the Final-State Authority hierarchy: persisted tasks.md > orchestrator final-state facts > intermediate snapshots (apply-progress, verify-report body).

## Final State at Close

| Fact | Value | Source |
|------|-------|--------|
| Tasks complete | **13/13 `[x]`** (incl. manual 2.3 and 5.3, completed post-verify 2026-09-22) | tasks.md (persisted, authoritative) |
| Verification verdict | **PASS** — original PASS-WITH-WARNINGS upgraded by the Addendum after both CONDITIONAL rows closed | verify-report.md + Addendum |
| CRITICAL findings | 0 | verify-report.md |
| Suite | **624 passed / 0 failed** (exit 0) | verify-report.md execution + apply record |
| Requirements / scenarios | 13/13 / 20/20 compliant | verify-report.md |
| Work-unit commits on main | 4, **not pushed**: `6c7fb75` U1 bootstrap+s33 · `b38251e` U2 manifest 0.16.0 · `07a2933` U3 install.sh+s34 · `f6a23df` U4 README | orchestrator final-state facts, corroborated by apply record (obs 9300) |
| Committed changed lines | 572 vs ~500 ceiling — `size:exception` maintainer-granted | verify-report.md WARNING #4 |
| `bin/herdr-tts` | Untouched by PM-01 commits (task 1.4 hash-identical) | apply record + verify-report.md |
| Tag `v0.16.0` | **Cut locally (annotated) on `f6a23df`**; push deferred to maintainer's out-of-hours window | tasks.md 2.3 note + orchestrator facts |
| agent-tts pin | Full 40-char SHA `19ad6b469751ddca4cab9c7ebb471ab01ee732ce` (upstream repo has no tags — decision (d) fallback) | apply record + verify-report.md |
| Decision (f) live uv test | **PASSED** (uv 0.12.17): pip-less uv venv installed via `uv pip` indirection (rc=0), dev checkout ignored without flag, pinned-SHA install + `import agent_tts` OK, upgrade rc=0 | verify-report.md Addendum transcript |

## Superseded Snapshot Claims (do not trust as current)

Per the Final-State Authority, the following intermediate claims are valid history only and are superseded by higher-ranked sources:

- **apply-progress (Engram obs 9300): "11/13, 2.3 and 5.3 UNCHECKED"** — true at apply time (2026-09-22 14:37). Superseded by tasks.md: both items completed after verify (2.3 tag cut on `f6a23df`; 5.3 live-uv PASS), checkboxes marked with dated completion notes.
- **verify-report body (Engram obs 9306): "PASS WITH WARNINGS; warnings 1–2 open; in-2/in-7/pb-1 CONDITIONAL"** — true at verification time (2026-09-22 14:55). Superseded by the Addendum in the same file: warning 1 (tag) and warning 2 (uv live test) resolved with live evidence; verdict upgraded to **PASS**. Warnings 3, 4, 5 remain as recorded below.

No unrankable contradictions were found: every snapshot-vs-final delta is resolved by the tasks artifact plus the Addendum plus orchestrator facts, and repository evidence (commit list, tasks.md notes, Addendum transcript) corroborates each final claim.

## Warnings Standing at Close (non-blocking)

1. **`openspec/` untracked** — the entire planning tree (including this archive) is untracked by maintainer choice; optional commit logged below.
2. **572 committed changed lines** vs ~500 ceiling — `size:exception` granted upstream.
3. **`gentle-ai sdd-verify-validate` not shipped in CLI 3.4.0** — report persisted without formal validator admission; tooling gap, out of change scope.

## Maintainer Items After Archive

- Push the 4 commits **and** tag `v0.16.0` in the out-of-hours window (tag is local-only until then).
- Optionally commit the `openspec/` planning tree.
- Logged out-of-scope follow-ups: npm bin-stub wrapper; Homebrew tap; startup-hook contract migration (`_daemon-supervised` vs one-shot `[[startup]]`); `install.sh --help`/`--dry-run`; inline comment on s16g's intentional Spanish-title assertion (smoke-tests.sh:912).

## Spec Sync Record

Both domains are NEW capabilities (`openspec/specs/` held only `.gitkeep`); each delta spec is a full spec, copied mechanically via shell (`mktemp` → `cp` → `diff -r` readback → `mv`), never through model Read/Write:

| Domain | Action | Readback |
|--------|--------|----------|
| `plugin-bootstrap` (6 requirements / 8 scenarios) | Created `openspec/specs/plugin-bootstrap/spec.md` | `diff -r` empty ✅ |
| `installer` (7 requirements / 12 scenarios) | Created `openspec/specs/installer/spec.md` | `diff -r` empty ✅ |

Archive move: `openspec/changes/pm-01-install-packaging/` → `openspec/changes/archive/2026-09-22-pm-01-install-packaging/` via guarded `git mv` → plain-`mv` fallback (tree untracked), pre-move recursive snapshot compared against the archived tree; `diff -r` empty ✅. This archive-report file is additive-only and excluded from that comparison (it did not exist in the source folder). No commits, no pushes (maintainer rule).

`rules.archive` check: "Warn before merging destructive deltas" — N/A; no existing main specs were merged into, nothing was removed.

## Artifact Traceability

**Filesystem (all read in full at archive time)**: proposal.md, specs/plugin-bootstrap/spec.md, specs/installer/spec.md, design.md, tasks.md, verify-report.md (incl. Addendum), openspec/config.yaml. Additional folder contents archived without phase reads: explore.md, research.md.

**Engram (project `herdr-tts`, observation IDs actually read)**:
- `sdd/pm-01/proposal` — obs **9281** (via search)
- `sdd/pm-01/tasks` — obs **9291** (via search)
- `sdd/pm-01/apply-progress` — obs **9300** (retrieved in full)
- `sdd/pm-01/verify-report` — obs **9306** (retrieved in full)
- No dedicated Engram observations exist for design/spec topics; those artifacts live as repo files (read above).

**Engram mirror of this report**: `sdd/pm-01/archive-report` (type architecture, capture_prompt false).

## SDD Cycle Complete

Planned → implemented (4 work units, strict TDD, RED 14+22 → GREEN 624/0) → verified (PASS) → archived. The change is closed.

---

## Addendum — Ecosystem cross-analysis (2026-09-22, post-archive)

Maintainer supplied five real Herdr-ecosystem install docs (herdr-auto-title, herdr-radar, tsk, herdr-automatic-rename, roamgate) to validate the follow-up list. Findings:

**Validated by the ecosystem (no action needed):**
- **Tag-pinned installer + ref escape hatch** — roamgate ships `releases/latest/download/install-roamgate.sh` with a `ROAMGATE_VERSION` pin env: the exact pattern of our `v0.16.0` default + `HERDR_TTS_REF`/`@main` hatch.
- **Rejected pattern confirmed weak** — herdr-automatic-rename installs via raw `@main` pipe-bash and merely lists `jq` as a requirement; our tag pin + jq preflight guard are strictly stronger.
- **Self-configuring first start** — herdr-radar "finishes the rest itself" on first daemon start; our `daemon_keymap_autostart` precedent (silent no-op, never auto-inits) is the same contract.

**Collision found and refined — startup-hook contract follow-up (#3):**
Both herdr-auto-title and herdr-radar solve "plugins start only at server start" with a **plugin action invoked post-install** (`plugin action invoke <id>.restart` / `.state-start`), avoiding `herdr server stop` (which kills every pane). Our manifest already declares 20 actions but **none start/restart the daemon**. Refined approach (replaces the vague "migration"): add a `daemon-restart` action (the internal `--restart-daemon` already exists), document the post-install invoke, and keep `[[startup]]` as the boot-time auto-start. Contract-mismatch risk becomes tolerated coexistence — no supervisor rewrite.

**New follow-ups raised from the examples:**
1. **`plugin_action` key bindings** — herdr-radar binds keys with `type = "plugin_action"` (no shell spawn per keystroke); our managed keymap block writes `type = "shell"`. Migrate when convenient.
2. **Agent skill distribution** (tsk pattern: `tsk setup` installs skills into claude/codex/opencode/pi dirs) — `herdr-tts skill install <agent>` shipping a SKILL.md so coding agents can operate the plugin (mute/snooze/status/replay).
3. **"Or hand it to an agent" README block** (herdr-radar pattern) — paste-ready agent install prompt, including the do-NOT-`herdr server stop` warning.
4. **Honest tested-platforms line** (herdr-radar: "Tested on Windows 11 and macOS; Linux not yet") — connects to open question #9 (macOS `/proc` degradation in daemon liveness paths).

**Relative priority shifts:** Homebrew tap (#2) rises slightly — tsk demonstrates a user-tap works for non-core tools. npm wrapper (#1) unchanged (no ecosystem precedent observed).
