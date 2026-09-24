"""Shared fixtures for herdr-brain tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo, HerdrError, sanitize_prompt_text

# Stub speech-backend SURFACE: the brain consumes herdr-tts only through
# its CLI contract, so tests materialize a stub bin/herdr-tts that answers
# --contract-version and writes a fake MP3 for --render-text.
TTS_STUB_ROOT = Path("/tmp/herdr-brain-test-tts")

SETTINGS_KWARGS = dict(
    herdr_bin="herdr-fake",
    glm_api_key="test-key",
    glm_base_url="https://example.invalid/",
    glm_model="glm-5",
    tts_home=str(TTS_STUB_ROOT),
    tts_bin=str(TTS_STUB_ROOT / "bin/herdr-tts"),
    tts_voice="elvira",
    tts_rate="+0%",
    tts_timeout_s=10,
    audio_dir="/tmp/herdr-brain-test-audio",
    prompt_timeout_ms=5_000,
    max_tool_rounds=4,
    approval_timeout_s=60,
    screen_lines=40,
    stt_model="small",
    stt_device="auto",
    stt_compute="auto",
    stt_warmup=False,  # tests: never start the warmup thread / touch the model
)

_STUB_BIN = """#!/bin/sh
# herdr-tts surface stub (session fixture) — contract v1.
if [ "${1:-}" = "--contract-version" ]; then
  echo 1
  exit 0
fi
if [ "${1:-}" = "--render-text" ]; then
  out="$2"
  printf 'ID3-stub-mp3' > "$out"
  exit 0
fi
# --render-html INPUT OUTPUT --map MAP (reader-pipeline/anchors@1).
# Env switches keep one stub covering the whole reader failure matrix:
#   STUB_RENDER_EXIT — exit N before writing any output (exit 2 per the
#                     upstream contract writes NEITHER output file)
#   STUB_RENDER_HANG — sleep N seconds (timeout probe)
#   STUB_BAD_MAP=1  — write an unparseable sidecar
if [ "${1:-}" = "--render-html" ]; then
  in="$2"; out="$3"; map="$5"
  if [ -n "${STUB_RENDER_EXIT:-}" ]; then exit "$STUB_RENDER_EXIT"; fi
  if [ -n "${STUB_RENDER_HANG:-}" ]; then sleep "$STUB_RENDER_HANG"; fi
  printf '<p><span class="tts-sent" data-sent-idx="0" data-para-idx="0" id="tts-sent-0">%s</span></p>' "$(cat "$in")" > "$out"
  if [ "${STUB_BAD_MAP:-}" = "1" ]; then
    printf 'not json{' > "$map"
  else
    printf '{"version":1,"contract":"reader-pipeline/anchors@1","alignment":"exact","total_sents":1,"total_paras":1,"engine":{"lang":"es","max_chars":0,"summarize":false,"lexicon_fp":"stub"},"sentences":[]}' > "$map"
  fi
  exit 0
fi
exit 2
"""


@pytest.fixture(autouse=True, scope="session")
def _tts_backend_stubs():
    """Materializes the stub herdr-tts surface CLI once per session."""
    bin_dir = TTS_STUB_ROOT / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "herdr-tts"
    stub.write_text(_STUB_BIN)
    stub.chmod(0o755)
    yield
    # Left in /tmp on purpose: harmless and reused across sessions.

ACTIVE_AGENT = AgentInfo(
    pane_id="w1:p9",
    agent="opencode",
    status="working",
    session_kind="id",
    session_value="ses_test0000session",
    cwd="/repo",
    title="OpenCode",
    focused=True,
)


class StubHerdr:
    """HerdrClient test double: canned agents, screen and prompt results."""

    def __init__(self, agents=None, screen="screen body", prompt=None, fail_screen=False):
        self._agents = agents or []
        self._screen = screen
        self._prompt = prompt or {"ok": True, "status": "done", "output": "did it"}
        self._fail_screen = fail_screen
        self.screen_calls: list = []
        self.prompt_calls: list = []

    def list_agents(self):
        return list(self._agents)

    def active_agent(self):
        from herdr_brain.herdr import pick_active

        return pick_active(self._agents)

    def read_screen(self, pane_id, n_lines=None, source="visible"):
        self.screen_calls.append(
            {"pane_id": pane_id, "n_lines": n_lines, "source": source}
        )
        if self._fail_screen:
            raise HerdrError("read failed")
        return self._screen

    def send_prompt(self, pane_id, text, timeout_ms=None):
        # Mirrors the real HerdrClient contract: newlines never reach the pane.
        self.prompt_calls.append(
            {"pane_id": pane_id, "text": sanitize_prompt_text(text), "timeout_ms": timeout_ms}
        )
        return self._prompt


@pytest.fixture
def settings() -> Settings:
    return Settings(**SETTINGS_KWARGS)


@pytest.fixture
def active_agent() -> AgentInfo:
    return ACTIVE_AGENT


@pytest.fixture
def make_stub():
    """Factory for StubHerdr doubles."""

    def _make(**kwargs):
        kwargs.setdefault("agents", [ACTIVE_AGENT])
        return StubHerdr(**kwargs)

    return _make


@pytest.fixture(autouse=True)
def hermetic_stores(tmp_path, monkeypatch):
    """Never let tests touch the real transcript stores on this machine."""
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "nonexistent-opencode.db"))
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path / "nonexistent-claude"))
