"""Edge behavior of the announcement hub and watcher (voice-stack VS1.8).

The watcher gained the speech_request_id field in VS1.4, which makes the whole
module subject to the D4 per-module coverage floor. These tests pin the
previously unexercised edges: a saturated subscriber queue, detail clipping,
label truncation, the poll thread that must survive failures, the done/blocked
detail fallbacks and the housekeeping that must never kill the loop.
"""

from __future__ import annotations

import dataclasses
import os
import queue
import threading
import time
import types
from pathlib import Path

import pytest

from herdr_brain import watcher as watcher_mod
from herdr_brain.config import Settings
from herdr_brain.watcher import (
    ANNOUNCEMENT_MAX_AGE_S,
    FALLBACK_DETAIL,
    LABEL_MAX_CHARS,
    AgentWatcher,
    AnnouncementHub,
    agent_label,
    clip_detail,
)
from tests.conftest import ACTIVE_AGENT, SETTINGS_KWARGS, StubHerdr


def make_watcher(tmp_path: Path, herdr=None, **kwargs) -> AgentWatcher:
    cfg = Settings(**{**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "audio")})
    return AgentWatcher(cfg, herdr=herdr or StubHerdr(), tts_renderer=lambda *a, **k: None, **kwargs)


# -- hub ---------------------------------------------------------------------


def test_full_subscriber_queue_keeps_the_newest_announcement():
    hub = AnnouncementHub(max_queue=1)
    _sub_id, mailbox = hub.subscribe()
    hub.publish({"n": 1})
    hub.publish({"n": 2})  # full: the oldest is dropped, the newest survives
    assert mailbox.get_nowait() == {"n": 2}


def test_publish_survives_a_subscriber_that_stays_stuck():
    class Stuck:
        def put_nowait(self, _item):
            raise queue.Full

        def get_nowait(self):
            raise queue.Empty

    hub = AnnouncementHub()
    hub._subscribers[99] = Stuck()
    hub.publish({"n": 1})  # the stuck subscriber must not raise into the watcher


# -- pure helpers --------------------------------------------------------------


def test_clip_detail_cuts_at_a_trailing_period_with_no_space_inside():
    assert clip_detail("abcdefghi." + "z" * 20, 10) == "abcdefghi."


def test_agent_label_truncates_an_overlong_label():
    agent = dataclasses.replace(ACTIVE_AGENT, cwd="/tmp/" + "p" * 100)
    label = agent_label(agent)
    assert len(label) == LABEL_MAX_CHARS and label.endswith("...")


# -- poll thread ------------------------------------------------------------------


def test_poll_thread_survives_failures_starts_once_and_stops(tmp_path):
    polled = threading.Event()

    class Exploding(StubHerdr):
        def list_agents(self):
            polled.set()
            raise RuntimeError("herdr went away")

    watcher = make_watcher(tmp_path, herdr=Exploding(), poll_interval_s=0.01)
    watcher.start()
    first_thread = watcher._thread
    watcher.start()  # a second start is a no-op
    assert watcher._thread is first_thread
    assert polled.wait(timeout=5), "the poll loop never ran"
    time.sleep(0.05)  # several failing polls: the loop must still be alive
    assert first_thread.is_alive()
    watcher.stop()
    first_thread.join(timeout=5)
    assert not first_thread.is_alive()


# -- done / blocked detail ---------------------------------------------------------


def test_done_detail_falls_back_to_the_screen_when_transcripts_fail(tmp_path, monkeypatch):
    def broken_turns(*_args, **_kwargs):
        raise RuntimeError("transcript unreadable")

    monkeypatch.setattr(watcher_mod, "read_turns", broken_turns)
    watcher = make_watcher(tmp_path, herdr=StubHerdr(screen="Listo. Todo verde.\nmas"))
    assert watcher._done_detail(ACTIVE_AGENT) == "Listo. Todo verde."


def test_blocked_detail_uses_the_first_nonblank_line_when_nothing_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(
        watcher_mod, "detect_pending",
        lambda _screen, _status: types.SimpleNamespace(detected=False, excerpt=None),
    )
    watcher = make_watcher(tmp_path, herdr=StubHerdr(screen="\n   \n  esperando decision  \nresto"))
    blocked = dataclasses.replace(ACTIVE_AGENT, status="blocked")
    assert watcher._blocked_detail(blocked) == "esperando decision"


def test_blocked_detail_falls_back_when_the_screen_is_blank_or_unreadable(tmp_path):
    blocked = dataclasses.replace(ACTIVE_AGENT, status="blocked")
    blank = make_watcher(tmp_path, herdr=StubHerdr(screen="  \n \n"))
    assert blank._blocked_detail(blocked) == FALLBACK_DETAIL
    unreadable = make_watcher(tmp_path, herdr=StubHerdr(fail_screen=True))
    assert unreadable._blocked_detail(blocked) == FALLBACK_DETAIL


# -- housekeeping --------------------------------------------------------------------


def test_cleanup_without_an_audio_dir_is_a_noop(tmp_path):
    make_watcher(tmp_path)._cleanup_old_audio()  # audio dir does not exist: no error


def test_cleanup_skips_entries_it_cannot_stat_and_keeps_fresh_audio(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    old = audio / "ann-old.mp3"
    old.write_bytes(b"x")
    stale = time.time() - ANNOUNCEMENT_MAX_AGE_S - 60
    os.utime(old, (stale, stale))
    fresh = audio / "ann-fresh.mp3"
    fresh.write_bytes(b"x")
    chat = audio / "chat-keep.mp3"  # chat audio is never touched
    chat.write_bytes(b"x")
    os.symlink(audio / "nowhere", audio / "ann-dangling.mp3")  # stat() raises OSError

    make_watcher(tmp_path)._cleanup_old_audio()

    assert not old.exists()
    assert fresh.exists() and chat.exists()


def test_cleanup_never_raises_even_when_settings_blow_up(tmp_path):
    watcher = make_watcher(tmp_path)

    class Broken:
        @property
        def audio_dir(self):
            raise RuntimeError("settings exploded")

    watcher._settings = Broken()
    watcher._cleanup_old_audio()  # housekeeping must never kill the loop
