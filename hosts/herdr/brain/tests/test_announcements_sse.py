"""VS1.4: announcements carry a speech_request_id, additively end to end.

Contracts under test:
- the watcher MINTS ``ann-<uuid4>`` inside ``_build_announcement`` while
  every v1 payload key stays untouched;
- the SSE payload construction passes the field through verbatim, and an
  announcement dict LACKING the field serializes EXACTLY like the v1
  payload (no new key — old clients stay byte-compatible);
- the /events stream delivers the field on the wire.

The SSE harness follows tests/test_server.py::TestEvents: starlette's
TestClient buffers whole responses, so a live hub publish cannot be
observed mid-stream from the test thread — the hub double is pre-loaded
with the watcher-minted announcement instead.
"""

from __future__ import annotations

import json
import queue as queue_module
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from herdr_brain.config import Settings
from herdr_brain.herdr import AgentInfo
from herdr_brain.server import announcement_event_payload, create_app
from herdr_brain.watcher import AgentWatcher

# uuid4 str: exactly 36 chars of lowercase hex plus dashes.
SPEECH_REQUEST_ID_RE = re.compile(r"^ann-[0-9a-f-]{36}$")
V1_KEYS = {"type", "pane_id", "agent", "status", "label", "text", "audio_url"}


def make_agent(pane="w1:p1", value="ses_watch000001", status="working", focused=True, cwd="/repo"):
    return AgentInfo(
        pane_id=pane, agent="opencode", status=status, session_kind="id",
        session_value=value, cwd=cwd, title="Watched", focused=focused,
    )


def fake_tts(settings, text, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(b"ID3")
    return out_path


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class PreloadedHub:
    """Hub double from TestEvents: subscribe() yields a pre-loaded queue."""

    def __init__(self, items):
        self._q = queue_module.Queue()
        for item in items:
            self._q.put(item)

    def subscribe(self):
        return 0, self._q

    def unsubscribe(self, sub_id):
        pass


@pytest.fixture
def audio_dir(tmp_path) -> Path:
    return tmp_path / "audio"


def _sse_lines(app, limit=1):
    """Finite /events stream via the test hook; non-empty SSE lines."""
    client = TestClient(app)
    with client.stream("GET", "/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        return [line for line in resp.iter_lines() if line]


def _app_for_hub(cfg, hub, limit=1):
    watcher = AgentWatcher(cfg, tts_renderer=fake_tts)
    watcher.hub = hub
    return create_app(
        settings=cfg, watcher=watcher, sse_heartbeat_s=0.2, sse_stream_limit=limit
    )


class TestWatcherMintsSpeechRequestId:
    def test_payload_carries_speech_request_id(self, settings, make_stub):
        stub = make_stub(agents=[make_agent(status="working")], screen="Todo verde.")
        watcher = AgentWatcher(
            settings, herdr=stub, tts_renderer=fake_tts, clock=FakeClock()
        )

        ann = watcher._build_announcement(make_agent(status="done"))

        assert SPEECH_REQUEST_ID_RE.match(ann["speech_request_id"])
        # Additive only: the v1 key set arrives intact, nothing else new.
        assert set(ann) == V1_KEYS | {"speech_request_id"}
        assert ann["type"] == "transition"
        assert ann["pane_id"] == "w1:p1"
        assert ann["agent"] == "opencode"
        assert ann["status"] == "done"
        assert ann["label"] == "opencode repo"
        assert ann["text"] == "opencode repo terminó: Todo verde."
        assert ann["audio_url"] and ann["audio_url"].startswith("/audio/ann-")
        # The id is MINTED per announcement, not a constant.
        other = watcher._build_announcement(make_agent(status="done"))
        assert other["speech_request_id"] != ann["speech_request_id"]


class TestSSEPayloadV1Compat:
    def test_payload_v1_without_field_stays_valid(self, settings, audio_dir):
        v1 = {
            "type": "transition",
            "pane_id": "w1:p1",
            "agent": "opencode",
            "status": "done",
            "label": "opencode repo",
            "text": "opencode repo terminó: Todo verde",
            "audio_url": "/audio/ann-abc.mp3",
        }

        # Construction seam: verbatim passthrough, no injected key.
        payload = announcement_event_payload(v1)
        assert "speech_request_id" not in payload
        assert set(payload) == V1_KEYS
        assert json.dumps(payload, ensure_ascii=False) == json.dumps(v1, ensure_ascii=False)

        # Wire proof: the /events data line is byte-identical to v1.
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        lines = _sse_lines(_app_for_hub(cfg, PreloadedHub([v1])))
        assert lines == [
            ": connected",
            "data: " + json.dumps(v1, ensure_ascii=False),
        ]


class TestSSEStream:
    def test_sse_stream_includes_field(self, settings, audio_dir, make_stub):
        # Drive the watcher seam for real: baseline poll, then a
        # working→done transition emits one announcement (the exact dict
        # hub.publish received).
        cfg = Settings(**{**settings.__dict__, "audio_dir": str(audio_dir)})
        stub = make_stub(agents=[make_agent(status="working")], screen="Todo verde.")
        watcher = AgentWatcher(
            cfg, herdr=stub, tts_renderer=fake_tts, clock=FakeClock()
        )
        watcher.poll_once()  # baseline
        stub._agents = [make_agent(status="done")]
        produced = watcher.poll_once()
        assert len(produced) == 1
        ann = produced[0]

        lines = _sse_lines(_app_for_hub(cfg, PreloadedHub([ann])))

        assert lines[0] == ": connected"
        assert lines[1].startswith("data: ")
        payload = json.loads(lines[1][len("data: "):])
        # Verbatim pass-through of the minted id, alongside v1 keys.
        assert SPEECH_REQUEST_ID_RE.match(payload["speech_request_id"])
        assert payload["speech_request_id"] == ann["speech_request_id"]
        assert payload["text"] == ann["text"]
        assert set(payload) == V1_KEYS | {"speech_request_id"}
