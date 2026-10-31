"""AT-05 content-aware prosody: classification, plans and orchestration."""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import pytest

from agent_tts import prosody
from agent_tts.boundaries import BoundaryMap, SynthesisResult
from agent_tts.cli import _speak_pipelined, use_pipelined_stream

from test_stream_drain import FakeRemoteSession, make_decoded


# ── classification (golden, deterministic) ─────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ('Traceback (most recent call last): File "app.py", line 3, in main', "error"),
        ("ValueError: invalid literal for int()", "error"),
        ("3 tests FAILED in the auth module.", "error"),
        ("ERROR: could not connect to the database.", "error"),
        ("The error handling in this module is now clearer.", "normal"),
        ("WARNING: the cache is stale.", "warning"),
        ("This flag is DEPRECATED and will be removed.", "warning"),
        ("The agent is BLOCKED waiting for approval.", "warning"),
        ("[bloque de código omitido]", "code"),
        ("comando: npm test", "code"),
        ("Do you want me to run the migration?", "question"),
        ("¿Quieres que siga con la migración?", "question"),
        ("All 42 tests passed.", "normal"),
        ("ERROR: build failed. Should I retry?", "error"),
    ],
)
def test_classify_group(text, expected):
    assert prosody.classify(text) == expected


def test_classification_is_deterministic():
    text = "WARNING: disk almost full. Continue?"
    assert {prosody.classify(text) for _ in range(20)} == {"warning"}


# ── value arithmetic ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "base, delta, expected",
    [("+0%", -8, "-8%"), ("+20%", -10, "+10%"), ("-5%", 0, "-5%"), ("1.2", -10, "1.08"), ("", 5, "+5%")],
)
def test_shift_rate(base, delta, expected):
    assert prosody.shift_rate(base, delta) == expected


@pytest.mark.parametrize(
    "base, delta, expected",
    [("+0Hz", -3, "-3Hz"), ("+5Hz", 4, "+9Hz"), ("-2Hz", 0, "-2Hz"), ("", 2, "+2Hz")],
)
def test_shift_pitch(base, delta, expected):
    assert prosody.shift_pitch(base, delta) == expected


# ── plans ──────────────────────────────────────────────────────────────────


GROUPS = [
    "All 42 tests passed.",
    "ERROR: could not connect to the database.",
    "[bloque de código omitido]",
    "Do you want me to retry?",
]


def test_off_has_no_plan():
    assert prosody.plan_groups(GROUPS, "off", "+0%", "+0Hz", config_path="/nonexistent") is None


def test_subtle_plan_shapes_each_group():
    plan = prosody.plan_groups(GROUPS, "subtle", "+0%", "+0Hz", config_path="/nonexistent")
    assert [g.kind for g in plan] == ["normal", "error", "code", "question"]
    assert (plan[0].rate, plan[0].pitch, plan[0].pause_ms) == ("+0%", "+0Hz", 0)
    assert (plan[1].rate, plan[1].pitch, plan[1].pause_ms) == ("-8%", "-3Hz", 300)
    assert (plan[2].rate, plan[2].pause_ms) == ("-5%", 150)
    # Leaving code for prose pauses too (RF-AT-05-4).
    assert (plan[3].pitch, plan[3].pause_ms) == ("+4Hz", 150)


def test_on_is_stronger_than_subtle():
    subtle = prosody.plan_groups(GROUPS, "subtle", "+0%", "+0Hz", config_path="/nonexistent")
    on = prosody.plan_groups(GROUPS, "on", "+0%", "+0Hz", config_path="/nonexistent")
    assert prosody.parse_signed(on[1].rate) < prosody.parse_signed(subtle[1].rate)
    assert prosody.parse_signed(on[3].pitch) > prosody.parse_signed(subtle[3].pitch)


def test_first_group_never_pauses():
    plan = prosody.plan_groups(["ERROR: boom."], "on", "+0%", "+0Hz", config_path="/nonexistent")
    assert plan[0].pause_ms == 0


def test_config_file_overrides_one_class(tmp_path):
    path = tmp_path / "prosody.json"
    path.write_text(json.dumps({"subtle": {"error": {"rate": -20, "pause_ms": 500}}}))
    plan = prosody.plan_groups(GROUPS, "subtle", "+0%", "+0Hz", config_path=str(path))
    assert (plan[1].rate, plan[1].pitch, plan[1].pause_ms) == ("-20%", "-3Hz", 500)
    assert plan[3].pitch == "+4Hz"  # untouched classes keep their defaults


def test_broken_config_warns_and_keeps_defaults(tmp_path, capsys):
    path = tmp_path / "prosody.json"
    path.write_text("{not json")
    plan = prosody.plan_groups(GROUPS, "subtle", "+0%", "+0Hz", config_path=str(path))
    assert plan[1].rate == "-8%"
    assert "prosody config" in capsys.readouterr().err


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        prosody.plan_groups(GROUPS, "loud", "+0%", "+0Hz", config_path="/nonexistent")


# ── stream routing ─────────────────────────────────────────────────────────


def test_prosody_makes_short_auto_texts_pipelined():
    args = dict(provider="edge", stream="auto", no_play=False, output_file=None, podcast=False, text_len=50)
    assert use_pipelined_stream(**args) is False
    assert use_pipelined_stream(**args, prosody="subtle") is True
    assert use_pipelined_stream(**args, prosody="off") is False


def test_prosody_never_overrides_explicit_off_or_podcast():
    base = dict(provider="edge", output_file=None, text_len=50, prosody="on")
    assert use_pipelined_stream(stream="off", no_play=False, podcast=False, **base) is False
    assert use_pipelined_stream(stream="auto", no_play=False, podcast=True, **base) is False
    assert use_pipelined_stream(stream="auto", no_play=True, podcast=False, **base) is False


# ── orchestration ──────────────────────────────────────────────────────────


TEXT = "All 42 tests passed. ERROR: could not connect to the database. Do you want me to retry?"


class TestPipelinedProsody(unittest.TestCase):
    def setUp(self):
        store = tempfile.mkdtemp(prefix="agent-tts-prosody-audio-")
        patcher = mock.patch.dict(
            os.environ,
            {"AGENT_TTS_AUDIO_DIR": store, "AGENT_TTS_PROSODY_CONFIG": "/nonexistent"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, mode, max_chars=40):
        calls = []

        async def fake_synthesize(**kwargs):
            calls.append((kwargs["text"], kwargs["rate"], kwargs["pitch"]))
            return SynthesisResult(b"fake-mp3-bytes", BoundaryMap())

        session = FakeRemoteSession()
        from agent_tts.text import split_sentence_groups as real_split

        with mock.patch("agent_tts.cli.synthesize", fake_synthesize), mock.patch(
            "agent_tts.cli.miniaudio.decode", lambda data: make_decoded(2400)
        ), mock.patch(
            "agent_tts.cli.split_sentence_groups", lambda text: real_split(text, max_chars=max_chars)
        ):
            asyncio.run(
                _speak_pipelined(
                    session=session,
                    text=TEXT,
                    check_stop=lambda: bool(session.state.get("stop")),
                    voice="en-US-AriaNeural",
                    rate="+10%",
                    volume="+0%",
                    pitch="+0Hz",
                    provider="edge",
                    openai_key=None,
                    openai_base_url=None,
                    openai_model=None,
                    eleven_key=None,
                    eleven_model=None,
                    piper_model=None,
                    auto_lang=False,
                    prosody_mode=mode,
                )
            )
        return calls, session

    def test_off_sends_the_global_rate_and_pitch_to_every_group(self):
        calls, session = self._run("off")
        assert len(calls) == 3
        assert {(rate, pitch) for _, rate, pitch in calls} == {("+10%", "+0Hz")}
        assert len(session.appended) == 2  # no silence segments

    def test_subtle_sends_per_group_values_and_inserts_pauses(self):
        calls, session = self._run("subtle")
        assert [(rate, pitch) for _, rate, pitch in calls] == [
            ("+10%", "+0Hz"),
            ("+2%", "-3Hz"),
            ("+10%", "+4Hz"),
        ]
        # Spoken text is never altered (RF-AT-05-6).
        assert " ".join(text for text, _, _ in calls) == TEXT
        # One 300 ms silence before the error group, then two speech groups.
        silences = [d for d in session.appended if not any(d.samples) and len(d.samples) == 7200]
        assert len(silences) == 1
        assert len(session.appended) == 3
        # The karaoke timeline accounts for the pause.
        starts = [s.start_sec for s in session.boundaries.sentences]
        assert starts[1] == pytest.approx(0.1 + 0.3, abs=1e-6)
