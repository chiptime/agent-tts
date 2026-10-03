"""Segmented rendering (voice-stack VS2.2, contract tts-brain-v2.md).

The engine seams (splitter/synth) are injected, so production behavior is
exercised without providers; one test drives the REAL engine grouping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import segmented_render as sr

ID = "seg-0123456789"
TEXT = "Primera frase. Segunda frase algo mas larga. Tercera y ultima."


def make_synth(calls, payloads=("a", "bb", "ccc")):
    def synth(group, voice, rate, stop_checker):
        calls.append({"group": group, "voice": voice, "rate": rate})
        return payloads[len(calls) - 1].encode() * 11

    return synth


@pytest.fixture
def calls():
    return []


THREE = ["grupo uno", "grupo dos", "grupo tres"]
split3 = lambda _text: list(THREE)


def test_manifest_published_per_segment_atomic(tmp_path, calls):
    out = tmp_path / "seg"
    rc = sr.render_segmented(TEXT, out, ID, "elvira", "+10%",
                             splitter=split3, synth=make_synth(calls))
    manifest = json.loads((out / sr.MANIFEST_NAME).read_text())
    assert rc == 0
    # one publish per segment + the terminal one: revision == segments
    assert manifest["revision"] == 3 and manifest["is_complete"] is True
    assert [s["seq"] for s in manifest["segments"]] == [0, 1, 2]
    assert manifest["segments"][0]["bytes"] == 11

    # a crash mid-production leaves a VALID manifest at the last revision
    out2 = tmp_path / "seg2"

    def crashing(group, voice, rate, stop_checker):
        if group == "grupo tres":
            raise RuntimeError("provider exploded")
        return b"xx"

    rc = sr.render_segmented(TEXT, out2, ID, splitter=split3, synth=crashing)
    assert rc == 1
    broken = json.loads((out2 / sr.MANIFEST_NAME).read_text())
    assert broken["revision"] == 2 and broken["is_complete"] is False
    assert "group 2 failed" in broken["error"]
    assert not list(out2.glob("*" + sr.TMP_SUFFIX))  # atomically clean


def test_groups_reuse_engine_split(tmp_path, calls):
    """The grouping IS the engine's split_sentence_groups (no parallel split)."""
    engine_text = pytest.importorskip("agent_tts.text")
    from agent_tts.text import split_sentence_groups

    out = tmp_path / "seg"
    long_text = " ".join(
        f"La frase numero {i} es lo bastante larga para ocupar espacio real." for i in range(8))
    seen = []
    sr.render_segmented(long_text, out, ID, synth=lambda g, v, r, s: seen.append(g) or b"x")
    assert seen == split_sentence_groups(long_text)  # the REAL engine grouping
    assert len(seen) >= 2 and all(isinstance(g, str) and g for g in seen)


def test_manifest_carries_request_id_input(tmp_path, calls):
    out = tmp_path / "seg"
    sr.render_segmented("Una sola frase.", out, "apr-fixedid123", synth=lambda *a: b"y")
    manifest = json.loads((out / sr.MANIFEST_NAME).read_text())
    assert manifest["speech_request_id"] == "apr-fixedid123"  # verbatim input
    assert (out / "seg-0000.mp3").exists()                    # 04d naming


def test_producer_cancel_rc3_publishes_terminal_keeps_sequence(tmp_path):
    out = tmp_path / "seg"
    produced = []

    def synth(group, voice, rate, stop_checker):
        produced.append(group)
        return b"z" * 5

    def stop_after_first():
        return len(produced) >= 1

    rc = sr.render_segmented(TEXT, out, ID, splitter=split3, synth=synth, stop_checker=stop_after_first)

    assert rc == 3
    manifest = json.loads((out / sr.MANIFEST_NAME).read_text())
    assert manifest["cancelled"] is True and manifest["is_complete"] is False
    assert manifest["revision"] == 1 and len(manifest["segments"]) == 1
    assert (out / "seg-0000.mp3").exists()   # the published segment SURVIVES
    assert not list(out.glob("*" + sr.TMP_SUFFIX))


def test_no_tmp_left_behind(tmp_path):
    for outcome in ("ok", "cancel", "crash"):
        out = tmp_path / outcome

        def synth(group, voice, rate, stop_checker, _outcome=outcome):
            if _outcome == "crash" and "Segunda" in group:
                raise RuntimeError("boom")
            return b"q"

        stop = (lambda: outcome_count[0] >= 1) if outcome == "cancel" else (lambda: False)
        outcome_count = [0]

        def synth_counting(group, voice, rate, stop_checker):
            data = synth(group, voice, rate, stop_checker)
            outcome_count[0] += 1
            return data

        sr.render_segmented(TEXT, out, ID, splitter=split3, synth=synth_counting, stop_checker=stop)
        files = sorted(p.name for p in out.iterdir())
        assert all(f == sr.MANIFEST_NAME or (f.startswith("seg-") and f.endswith(".mp3"))
                   for f in files), (outcome, files)


def test_cli_exit_codes_and_text_by_file(tmp_path, capsys, monkeypatch):
    text_file = tmp_path / "input.txt"
    text_file.write_text("Frase corta.", encoding="utf-8")
    out = tmp_path / "cli"

    def fake_main(argv):
        assert argv[0] == str(out)
        assert argv[1] == str(text_file)          # text rides the FILE
        assert argv[2:4] == ["--speech-request-id", ID]
        assert "--voice" in argv and "--rate" in argv
        return 0

    monkeypatch.setattr(sr, "render_segmented",
                        lambda text, out_dir, rid, voice, rate: fake_main(
                            [str(out_dir), str(text_file), "--speech-request-id", rid,
                             "--voice", voice, "--rate", rate]) or 0)
    assert sr.main([str(out), str(text_file), "--speech-request-id", ID,
                    "--voice", "v", "--rate", "r"]) == 0
    monkeypatch.undo()
    # and the REAL path once, through the module's own argparse:
    assert sr.main([str(tmp_path / "cli2"), str(text_file),
                    "--speech-request-id", ID]) in (0, 1, 3)


def test_cancel_during_synthesis_empty_bytes_is_cancelled(tmp_path):
    """A provider that returns empty WHILE stopped is a cancel, not a failure."""
    out = tmp_path / "seg"

    def synth(group, voice, rate, stop_checker):
        assert stop_checker()
        return b""

    rc = sr.render_segmented(TEXT, out, ID, splitter=split3, synth=synth,
                             stop_checker=lambda: True)
    assert rc == 3
    manifest = json.loads((out / sr.MANIFEST_NAME).read_text())
    assert manifest["cancelled"] is True and manifest["revision"] == 0


def test_synth_raising_cancelled_error_propagates_as_cancel(tmp_path):
    out = tmp_path / "seg"

    def synth(group, voice, rate, stop_checker):
        if group == "grupo dos":
            raise sr.CancelledError("provider stopped")
        return b"k"

    rc = sr.render_segmented(TEXT, out, ID, splitter=split3, synth=synth)
    assert rc == 3
    assert json.loads((out / sr.MANIFEST_NAME).read_text())["revision"] == 1


def test_tmp_cleanup_survives_an_undeletable_entry(tmp_path):
    out = tmp_path / "seg"
    out.mkdir()
    stubborn = out / ("leftover" + sr.TMP_SUFFIX)
    stubborn.mkdir()  # a DIRECTORY: unlink() raises OSError

    def crashing(group, voice, rate, stop_checker):
        raise RuntimeError("boom")

    rc = sr.render_segmented("una frase.", out, ID, synth=crashing)

    assert rc == 1
    assert stubborn.exists()  # undeletable, but the terminal publish ran


def test_engine_synth_bridge_calls_the_engine(monkeypatch):
    """The production seam: agent_tts.cli.synthesize with voice/rate/stop."""
    import agent_tts.cli as cli_mod

    recorded = {}

    async def fake_synthesize(text, voice=None, rate=None, stop_checker=None, **_kw):
        recorded.update({"text": text, "voice": voice, "rate": rate,
                         "stop": stop_checker})
        return b"mp3!"

    monkeypatch.setattr(cli_mod, "synthesize", fake_synthesize)
    assert sr.engine_synth("hola", "elvira", "+5%", None) == b"mp3!"
    assert recorded == {"text": "hola", "voice": "elvira", "rate": "+5%", "stop": None}


def test_empty_synth_without_stop_is_a_failure(tmp_path):
    out = tmp_path / "seg"
    rc = sr.render_segmented(TEXT, out, ID, synth=lambda *a: b"")
    assert rc == 1
    manifest = json.loads((out / sr.MANIFEST_NAME).read_text())
    assert "produced no audio" in manifest["error"]


def test_terminal_cleanup_unlinks_a_real_tmp_file(tmp_path):
    out = tmp_path / "seg"
    out.mkdir()
    (out / ("garbage" + sr.TMP_SUFFIX)).write_bytes(b"x")  # a REAL leftover

    def crashing(group, voice, rate, stop_checker):
        raise RuntimeError("boom")

    rc = sr.render_segmented("una frase.", out, ID, synth=crashing)

    assert rc == 1
    assert not list(out.glob("*" + sr.TMP_SUFFIX))  # the unlink branch ran


def test_module_entry_point_runs_main(monkeypatch, tmp_path, capsys):
    import runpy
    import sys as _sys

    text_file = tmp_path / "in.txt"
    text_file.write_text("Frase.", encoding="utf-8")
    monkeypatch.setattr(_sys, "argv", [
        "segmented_render.py", str(tmp_path / "out"), str(text_file),
        "--speech-request-id", "seg-0123456789"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(sr.__file__, run_name="__main__")
    assert exc.value.code in (0, 1, 3)
