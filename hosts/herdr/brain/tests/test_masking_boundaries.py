"""Secret-masking boundary tests (S14): every route text leaves by.

One seeded synthetic secret per boundary proves it never reaches the
wire while ordinary text stays byte-identical; one ``off``-mode
preservation test per touched boundary restores the exact previous
output. The route harness mirrors tests/test_mirror.py; the watcher
harness mirrors tests/test_announcements_sse.py.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.secrets_mask import ENV_VAR, SPOKEN_PLACEHOLDER
from herdr_brain.view import PendingAction
from tests.test_history_page import StubHerdr, make_agent

SECRET = "sk-ant-api03-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
HINT = "sk-ant-…[masked]"
SESSION = "00000000-1111-2222-3333-444444444444"

# The secret split across styled runs: run 1 paints "sk-ant-" red,
# run 2 carries the rest unstyled.
MIRROR_SCREEN = (
    "\x1b[31mbuild ok\x1b[0m\n"
    "\x1b[31msk-ant-\x1b[0m" + SECRET[len("sk-ant-"):] + " exported\n"
    "plain tail\n"
)
PLAIN_SCREEN = f"build ok\n{SECRET} exported\nplain tail\n"
BLOCKED_SCREEN = f"¿Guardar la clave {SECRET}?\n 1. Sí\n 2. No\n"


def seed_claude_turns(root: Path, session: str, texts) -> None:
    """Minimal claude-projects store with caller-chosen turn texts."""
    project = root / "-repo-"
    project.mkdir(parents=True, exist_ok=True)
    events = []
    for i, text in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        events.append(
            {
                "type": role,
                "message": {"role": role, "content": [{"type": "text", "text": text}]},
                "timestamp": f"2026-01-01T00:00:{i:02d}.000Z",
            }
        )
    (project / f"{session}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


@pytest.fixture
def client_factory(settings, monkeypatch):
    def _make(agents, screens=None):
        import herdr_brain.server as server_module
        from herdr_brain.tools import BrainTools

        stub = StubHerdr(agents, screens)

        class ToolsWithStub(BrainTools):
            def __init__(self, cfg):
                super().__init__(cfg, herdr=stub)

        monkeypatch.setattr(server_module, "BrainTools", ToolsWithStub)
        cfg = Settings(**settings.__dict__)
        return TestClient(server_module.create_app(settings=cfg))

    return _make


@pytest.fixture
def claude_store(tmp_path, monkeypatch):
    seed_claude_turns(tmp_path, SESSION, [
        f"usa la clave {SECRET} por favor",
        "hecho, tests en verde",
    ])
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path))
    return tmp_path


def fake_tts(settings, text, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(b"ID3")
    return out_path


WATCH_SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def make_watcher(settings, status="done", screen=None, tmp_path=None, monkeypatch=None):
    from herdr_brain.watcher import AgentWatcher

    if tmp_path is not None and monkeypatch is not None:
        seed_claude_turns(tmp_path, WATCH_SESSION, [f"listo con {SECRET}"])
        monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(tmp_path))
    stub = StubHerdr([make_agent(status="working")], {"w1:p1": screen} if screen else None)
    watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts)
    watcher_agent = make_agent(status=status, value=WATCH_SESSION)
    return watcher, watcher_agent


# -- /history ---------------------------------------------------------------


class TestHistoryBoundary:
    def test_secret_never_leaves_the_page(self, claude_store, client_factory):
        client = client_factory([make_agent(value=SESSION)])
        body = client.get("/history").json()
        raw = client.get("/history").text
        assert SECRET not in raw
        assert body["turns"][0]["text"] == f"usa la clave {HINT} por favor"
        # Ordinary text is untouched.
        assert body["turns"][1]["text"] == "hecho, tests en verde"

    def test_off_restores_the_exact_previous_output(
        self, claude_store, client_factory, monkeypatch
    ):
        monkeypatch.setenv(ENV_VAR, "off")
        client = client_factory([make_agent(value=SESSION)])
        body = client.get("/history").json()
        assert body["turns"][0]["text"] == f"usa la clave {SECRET} por favor"


# -- /pane/{id}/mirror ------------------------------------------------------


class TestMirrorBoundary:
    def test_secret_split_across_runs_is_masked(self, client_factory):
        client = client_factory([make_agent()], screens={"w1:p1": MIRROR_SCREEN})
        body = client.get("/pane/w1:p1/mirror").json()
        assert SECRET not in json.dumps(body, ensure_ascii=False)
        masked_line = "".join(run["t"] for run in body["runs"][1])
        assert masked_line == f"{HINT} exported"
        # Styles survive on untouched lines.
        assert body["runs"][0][0].get("fg") == "#cd0000"
        assert body["text"].splitlines()[1] == f"{HINT} exported"
        assert body["text"].splitlines()[2] == "plain tail"

    def test_off_restores_exact_runs(self, client_factory, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        client = client_factory([make_agent()], screens={"w1:p1": MIRROR_SCREEN})
        body = client.get("/pane/w1:p1/mirror").json()
        from herdr_brain.view import ansi_runs

        assert body["runs"] == ansi_runs(MIRROR_SCREEN)
        assert body["text"] is not None and SECRET in body["text"]


# -- /view ------------------------------------------------------------------


class TestViewBoundary:
    def test_screen_transcript_and_dialog_are_masked(self, claude_store, client_factory):
        client = client_factory(
            [make_agent(status="blocked", value=SESSION)],
            screens={"w1:p1": BLOCKED_SCREEN},
        )
        raw = client.get("/view").text
        body = json.loads(raw)
        assert SECRET not in raw
        assert HINT in body["dialog"]["question"]
        assert body["screen"] == f"¿Guardar la clave {HINT}?\n 1. Sí\n 2. No\n"
        joined_runs = "".join(r["t"] for r in body["screen_runs"][0])
        assert joined_runs == f"¿Guardar la clave {HINT}?"
        # Transcript tail (clipped to 300 chars) masks before clipping.
        assert HINT in body["transcript"][0]["text"]

    def test_pending_excerpt_is_masked(self, client_factory):
        screen = f"allow? password=hunter2secret\n{SECRET}\n"
        client = client_factory([make_agent()], screens={"w1:p1": screen})
        body = client.get("/view").json()
        assert "hunter2secret" not in json.dumps(body)
        assert body["pending"]["excerpt"] == f"allow? password=…[masked]"

    def test_off_restores_exact_output(self, claude_store, client_factory, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        client = client_factory(
            [make_agent(status="blocked", value=SESSION)],
            screens={"w1:p1": BLOCKED_SCREEN},
        )
        body = client.get("/view").json()
        assert body["screen"] == BLOCKED_SCREEN
        assert SECRET in body["dialog"]["question"]
        assert SECRET in body["transcript"][0]["text"]


# -- /screen ----------------------------------------------------------------


class TestScreenBoundary:
    def test_full_screen_is_masked(self, client_factory):
        client = client_factory([make_agent()], screens={"w1:p1": PLAIN_SCREEN})
        body = client.get("/screen").json()
        assert body["screen"] == f"build ok\n{HINT} exported\nplain tail\n"

    def test_off_restores_exact_output(self, client_factory, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        client = client_factory([make_agent()], screens={"w1:p1": PLAIN_SCREEN})
        body = client.get("/screen").json()
        assert body["screen"] == PLAIN_SCREEN


# -- /conversation ----------------------------------------------------------


class TestConversationBoundary:
    def test_turns_are_masked(self, claude_store, client_factory):
        client = client_factory([make_agent(value=SESSION)])
        raw = client.get("/conversation").text
        body = json.loads(raw)
        assert SECRET not in raw
        assert body["turns"][0]["text"] == f"usa la clave {HINT} por favor"
        assert body["turns"][1]["text"] == "hecho, tests en verde"

    def test_off_restores_exact_output(self, claude_store, client_factory, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        client = client_factory([make_agent(value=SESSION)])
        body = client.get("/conversation").json()
        assert body["turns"][0]["text"] == f"usa la clave {SECRET} por favor"


# -- SSE announcement payload ------------------------------------------------


class TestAnnouncementBoundary:
    def test_done_detail_from_a_turn_is_masked(self, settings, tmp_path, monkeypatch):
        watcher, agent = make_watcher(
            settings, status="done", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        ann = watcher._build_announcement(agent)
        assert SECRET not in json.dumps(ann, ensure_ascii=False)
        assert ann["text"] == f"claude repo terminó: listo con {HINT}"

    def test_blocked_detail_from_a_screen_is_masked(self, settings):
        watcher, agent = make_watcher(
            settings, status="blocked", screen=f"allow? token=abc123def456\n"
        )
        ann = watcher._build_announcement(agent)
        assert "abc123def456" not in json.dumps(ann, ensure_ascii=False)
        assert ann["text"].endswith("token=…[masked]")

    def test_off_restores_exact_text(self, settings, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "off")
        watcher, agent = make_watcher(
            settings, status="done", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        ann = watcher._build_announcement(agent)
        assert ann["text"] == f"claude repo terminó: listo con {SECRET}"


# -- LLM live context --------------------------------------------------------


class TestLiveContextBoundary:
    def _pending(self):
        return PendingAction(
            detected=True, kind="permission", excerpt=f"allow? token={SECRET}"
        )

    def _dialog(self):
        return {
            "question": f"¿Guardar la clave {SECRET}?",
            "options": [{"n": 1, "title": "Sí"}, {"n": 2, "title": "No"}],
            "hint": "",
        }

    def test_pending_and_dialog_lines_are_masked(self, active_agent):
        from herdr_brain.llm import build_live_context

        block = build_live_context(
            active_agent, now=datetime(2026, 10, 28, 10, 0),
            pending=self._pending(), dialog=self._dialog(),
        )
        assert SECRET not in block
        assert "token=…[masked]" in block
        assert f"¿Guardar la clave {HINT}?" in block

    def test_off_restores_exact_block(self, active_agent, monkeypatch):
        from herdr_brain.llm import build_live_context

        monkeypatch.setenv(ENV_VAR, "off")
        block = build_live_context(
            active_agent, now=datetime(2026, 10, 28, 10, 0),
            pending=self._pending(), dialog=self._dialog(),
        )
        assert f"token={SECRET}" in block
        assert f"¿Guardar la clave {SECRET}?" in block


# -- spoken text (TTS) -------------------------------------------------------


class TestSpokenTextBoundary:
    def _runner(self):
        from tests.test_tts import FakeRunner

        return FakeRunner()

    def test_render_replaces_secrets_with_the_spoken_phrase(self, settings, tmp_path):
        from herdr_brain.tts import render_mp3

        runner = self._runner()
        render_mp3(settings, f"la clave {SECRET} caducó", tmp_path / "a.mp3", runner=runner)
        spoken = runner.calls[0]["cmd"][3]
        assert SECRET not in spoken
        assert spoken == f"la clave {SPOKEN_PLACEHOLDER} caducó"

    def test_display_placeholder_replays_naturally(self, settings, tmp_path):
        from herdr_brain.tts import render_mp3

        runner = self._runner()
        render_mp3(settings, f"clave {HINT} aquí", tmp_path / "a.mp3", runner=runner)
        assert runner.calls[0]["cmd"][3] == f"clave {SPOKEN_PLACEHOLDER} aquí"

    def test_ordinary_answer_is_unchanged(self, settings, tmp_path):
        from herdr_brain.tts import render_mp3

        runner = self._runner()
        render_mp3(settings, "todo verde, 80% libre", tmp_path / "a.mp3", runner=runner)
        assert runner.calls[0]["cmd"][3] == "todo verde, 80% libre"

    def test_off_restores_exact_argv(self, settings, tmp_path, monkeypatch):
        from herdr_brain.tts import render_mp3

        monkeypatch.setenv(ENV_VAR, "off")
        runner = self._runner()
        render_mp3(settings, f"la clave {SECRET}", tmp_path / "a.mp3", runner=runner)
        assert runner.calls[0]["cmd"][3] == f"la clave {SECRET}"
