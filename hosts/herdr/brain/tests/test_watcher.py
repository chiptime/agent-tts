"""Watcher unit tests: transitions, baseline silence, debounce, digests.

Subprocess is never real; TTS is a fake renderer writing dummy bytes.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.watcher import (
    ANNOUNCEMENT_PREFIX,
    FALLBACK_DETAIL,
    AgentWatcher,
    AnnouncementHub,
    agent_label,
    first_sentence,
)


def make_agent(pane="w1:p1", value="ses_watch000001", status="working", focused=True, cwd="/repo"):
    return AgentInfo(
        pane_id=pane, agent="opencode", status=status, session_kind="id",
        session_value=value, cwd=cwd, title="Watched", focused=focused,
    )


def fake_tts(fail=False):
    def _render(settings, text, out_path):
        if fail:
            raise RuntimeError("no tts")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"ID3")
        return out_path

    return _render


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestHub:
    def test_subscribe_publish_receive(self):
        hub = AnnouncementHub()
        _, q = hub.subscribe()
        hub.publish({"text": "hi"})
        assert q.get_nowait() == {"text": "hi"}

    def test_unsubscribe_stops_delivery(self):
        hub = AnnouncementHub()
        sub_id, q = hub.subscribe()
        hub.unsubscribe(sub_id)
        hub.publish({"text": "hi"})
        assert q.empty()

    def test_full_queue_drops_oldest(self):
        hub = AnnouncementHub(max_queue=2)
        _, q = hub.subscribe()
        for i in range(4):
            hub.publish({"n": i})
        assert q.get_nowait()["n"] == 2
        assert q.get_nowait()["n"] == 3


class TestDigestText:
    def test_first_sentence(self):
        assert first_sentence("Dos cosas. Y una más.") == "Dos cosas"
        assert first_sentence("larga " * 60).endswith("...")

    def test_label_uses_cwd_basename(self):
        agent = make_agent(cwd="/home/bruno/Code/personal/compare-prices")
        assert agent_label(agent) == "opencode compare-prices"

    def test_done_announcement_with_transcript_detail(
        self, settings, make_stub, active_agent, monkeypatch, tmp_path
    ):
        import sqlite3

        db = tmp_path / "opencode.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INT, data TEXT)")
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?, 1, ?)",
            (active_agent.session_value, json_dump_role("assistant")),
        )
        conn.execute(
            "INSERT INTO part VALUES ('m1-p1', 'm1', ?, 1, ?)",
            (active_agent.session_value, json_dump_text("Migración aplicada. Quedan pruebas.")),
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("OPENCODE_DB", str(db))

        agent = make_agent(value=active_agent.session_value, status="working")
        stub = make_stub(agents=[agent])
        clock = FakeClock()
        spoken = []

        def recording_tts(settings, text, out_path):
            spoken.append(text)
            return fake_tts()(settings, text, out_path)

        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=recording_tts, clock=clock)
        watcher.poll_once()  # baseline

        stub._agents = [make_agent(value=active_agent.session_value, status="done")]
        produced = watcher.poll_once()
        assert len(produced) == 1
        ann = produced[0]
        assert ann["type"] == "transition"
        assert ann["status"] == "done"
        assert ann["label"] == "opencode repo"
        assert ann["text"] == "opencode repo terminó: Migración aplicada"
        assert ann["audio_url"] and ann["audio_url"].startswith("/audio/ann-")
        # Live audio is the SHORT aviso; the digest stays text-only.
        assert spoken == ["opencode repo ha terminado"]

    def test_blocked_announcement_uses_pending_excerpt(self, settings, make_stub):
        stub = make_stub(agents=[make_agent(status="working")], screen="Do you want to allow this? (y/n)")
        spoken = []

        def recording_tts(settings, text, out_path):
            spoken.append(text)
            return fake_tts()(settings, text, out_path)

        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=recording_tts, clock=FakeClock())
        watcher.poll_once()
        stub._agents = [make_agent(status="blocked")]
        produced = watcher.poll_once()
        assert produced[0]["text"] == (
            "opencode repo necesita tu atención: Do you want to allow this? (y/n)"
        )
        assert spoken == ["opencode repo necesita tu atención"]

    def test_fallback_detail_when_all_reads_fail(self, settings, make_stub):
        stub = make_stub(agents=[make_agent(status="working")], fail_screen=True)
        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts(), clock=FakeClock())
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        produced = watcher.poll_once()
        assert produced[0]["text"].endswith(FALLBACK_DETAIL)
        assert produced[0]["audio_url"] is None or produced[0]["audio_url"].startswith("/audio/")

    def test_tts_failure_keeps_text_event(self, settings, make_stub):
        stub = make_stub(agents=[make_agent(status="working")], screen="allow? (y/n)")
        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts(fail=True), clock=FakeClock())
        watcher.poll_once()
        stub._agents = [make_agent(status="blocked")]
        produced = watcher.poll_once()
        assert produced[0]["audio_url"] is None
        assert "(y/n)" in produced[0]["text"]


def json_dump_role(role):
    import json

    return json.dumps({"role": role})


def json_dump_text(text):
    import json

    return json.dumps({"type": "text", "text": text})


class TestTransitions:
    def test_baseline_is_silent_even_if_already_done(self, settings, make_stub):
        stub = make_stub(agents=[make_agent(status="done")])
        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts(), clock=FakeClock())
        assert watcher.poll_once() == []

    def test_working_to_done_announces_once(self, settings, make_stub):
        agent = make_agent(status="working")
        stub = make_stub(agents=[agent])
        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts(), clock=FakeClock())
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1
        # Same status again: no new announcement.
        assert watcher.poll_once() == []

    def test_idle_transitions_are_skipped(self, settings, make_stub):
        agent = make_agent(status="working")
        stub = make_stub(agents=[agent])
        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts(), clock=FakeClock())
        watcher.poll_once()
        stub._agents = [make_agent(status="idle")]
        assert watcher.poll_once() == []

    def test_debounce_blocks_reannouncement_within_window(self, settings, make_stub):
        clock = FakeClock()
        agent = make_agent(status="working")
        stub = make_stub(agents=[agent])
        watcher = AgentWatcher(
            settings, herdr=stub, tts_renderer=fake_tts(), clock=clock
        )
        watcher.poll_once()

        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1

        # Flap back to working, then to done again within the window.
        clock.advance(10)
        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        assert watcher.poll_once() == []

        # After the debounce window the same transition announces again.
        clock.advance(61)
        stub._agents = [make_agent(status="working")]
        watcher.poll_once()
        stub._agents = [make_agent(status="done")]
        assert len(watcher.poll_once()) == 1

    def test_herdr_failure_is_silent(self, settings, make_stub):
        stub = make_stub(agents=[])

        def boom():
            raise RuntimeError("list down")

        stub.list_agents = boom
        watcher = AgentWatcher(settings, herdr=stub, tts_renderer=fake_tts(), clock=FakeClock())
        assert watcher.poll_once() == []


class TestCleanup:
    def test_old_announcement_mp3s_removed(self, settings, make_stub, tmp_path):
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        settings = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        old = audio_dir / f"{ANNOUNCEMENT_PREFIX}old.mp3"
        old.write_bytes(b"x")
        aged = time.time() - 7200
        import os

        os.utime(old, (aged, aged))
        chat = audio_dir / "chatanswer.mp3"
        chat.write_bytes(b"x")
        os.utime(chat, (aged, aged))

        watcher = AgentWatcher(
            settings, herdr=make_stub(agents=[make_agent(status="done")]),
            tts_renderer=fake_tts(), clock=FakeClock(),
        )
        watcher.poll_once()
        watcher.poll_once()

        assert not old.exists()
        assert chat.exists()  # chat audio is never touched
