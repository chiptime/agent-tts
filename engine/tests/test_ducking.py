"""Local ducking contracts; no test needs an installed audio server or pactl."""

import os
import subprocess
from array import array
from types import SimpleNamespace

import pytest

from agent_tts import ducking


INACTIVE = {"comm_active": False, "capture_active": False}


def stream(kind="Sink Input", index=1, role="music", corked="no", name="Player", pid=999999):
    return (
        f"{kind} #{index}\n"
        "\tDriver: protocol-native.c\n"
        f"\tCorked: {corked}\n"
        "\tMute: no\n"
        "\tProperties:\n"
        f'\t\tapplication.name = "{name}"\n'
        f'\t\tapplication.process.id = "{pid}"\n'
        '\t\tapplication.process.binary = "python3"\n'
        f'\t\tmedia.role = "{role}"\n'
    )


@pytest.fixture(autouse=True)
def reset_warnings(monkeypatch):
    monkeypatch.setattr(ducking, "_warned", set())


@pytest.fixture
def pactl(monkeypatch):
    """Install a strict read-only subprocess fake and return its call log."""
    def install(sinks="", captures="", failure=None, fail_on="sink-inputs"):
        calls = []

        def run(args, **kwargs):
            assert args in (
                ["pactl", "list", "sink-inputs"],
                ["pactl", "list", "source-outputs"],
            )
            assert kwargs["capture_output"] is True
            assert kwargs["text"] is True
            assert kwargs["check"] is True
            assert 0 < kwargs["timeout"] <= 1.0
            assert kwargs["env"]["LC_ALL"] == "C"
            calls.append(args)
            if failure is not None and args[-1] == fail_on:
                raise failure
            output = sinks if args[-1] == "sink-inputs" else captures
            return subprocess.CompletedProcess(args, 0, output, "")

        monkeypatch.setattr(ducking.subprocess, "run", run)
        return calls

    return install


@pytest.mark.parametrize("target", ["local", "winhost", "wsl-ps", "windows", "auto"])
@pytest.mark.parametrize("policy", ["duck", "pause", "ignore"])
def test_policy_global_and_per_target_override(target, policy):
    assert ducking.resolve_policy(target, {"AGENT_TTS_DUCK_POLICY": policy}) == policy
    env = {
        "AGENT_TTS_DUCK_POLICY": "ignore",
        f"AGENT_TTS_DUCK_POLICY_{target.upper()}": policy,
    }
    assert ducking.resolve_policy(target, env) == policy


def test_policy_default_and_explicit_empty_environment(monkeypatch):
    monkeypatch.setenv("AGENT_TTS_DUCK_POLICY", "pause")
    assert ducking.resolve_policy("local") == "pause"
    assert ducking.resolve_policy("local", {}) == "ignore"
    assert ducking.resolve_policy("LOCAL", {"AGENT_TTS_DUCK_POLICY_LOCAL": "duck"}) == "duck"


@pytest.mark.parametrize("invalid", ["invalid", "", "DUCK", " duck "])
@pytest.mark.parametrize("key", ["AGENT_TTS_DUCK_POLICY", "AGENT_TTS_DUCK_POLICY_LOCAL"])
def test_invalid_policy_ignores_with_one_warning(invalid, key, capsys):
    env = {"AGENT_TTS_DUCK_POLICY": "pause", key: invalid}
    assert ducking.resolve_policy("local", env) == "ignore"
    assert ducking.resolve_policy("local", env) == "ignore"
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "warning" in lines[0].lower()
    assert key in lines[0]


def test_override_for_another_target_does_not_apply():
    env = {"AGENT_TTS_DUCK_POLICY": "duck", "AGENT_TTS_DUCK_POLICY_WINHOST": "pause"}
    assert ducking.resolve_policy("local", env) == "duck"


@pytest.mark.parametrize("db,expected", [(0, 1.0), (-12, 0.251188643150958), (12, 3.9810717055349722)])
def test_gain_linear_exact_formula(db, expected):
    assert ducking.gain_linear(db) == 10 ** (db / 20)
    assert ducking.gain_linear(db) == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize("raw,expected", [("0", 0.0), ("12", 12.0), ("6.5", 6.5)])
def test_duck_db_valid(raw, expected):
    assert ducking.duck_db({"AGENT_TTS_DUCK_DB": raw}) == expected


def test_duck_db_default_and_environment(monkeypatch):
    monkeypatch.setenv("AGENT_TTS_DUCK_DB", "9")
    assert ducking.duck_db() == 9.0
    assert ducking.duck_db({}) == 12.0


@pytest.mark.parametrize("raw", ["bad", "", "-12", "nan", "inf", "-inf"])
def test_duck_db_invalid_falls_back_with_one_warning(raw, capsys):
    assert ducking.duck_db({"AGENT_TTS_DUCK_DB": raw}) == 12.0
    assert ducking.duck_db({"AGENT_TTS_DUCK_DB": raw}) == 12.0
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "AGENT_TTS_DUCK_DB" in lines[0]


@pytest.mark.parametrize("comm", [False, True])
@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("policy", ["duck", "pause", "ignore"])
def test_decide_truth_table(comm, capture, policy):
    expected = policy if (comm or capture) else "ignore"
    assert ducking.decide({"comm_active": comm, "capture_active": capture}, policy) == expected


def test_decide_missing_flags_and_unknown_policy_fail_open():
    assert ducking.decide({}, "duck") == "ignore"
    assert ducking.decide({"comm_active": True}, "unknown") == "ignore"


@pytest.mark.parametrize(
    "sinks,captures,expected",
    [
        (stream(role="communication"), "", {"comm_active": True, "capture_active": False}),
        (stream(), "", INACTIVE),
        (stream() + stream(index=2, role="communication"), "", {"comm_active": True, "capture_active": False}),
        ("", stream(kind="Source Output"), {"comm_active": False, "capture_active": True}),
        (stream(role="communication"), stream(kind="Source Output"), {"comm_active": True, "capture_active": True}),
        (stream(role="communication", corked="yes"), stream(kind="Source Output", corked="yes"), INACTIVE),
        ("", "", INACTIVE),
    ],
    ids=["communication", "media", "mixed", "capture", "communication-and-capture", "corked", "empty"],
)
def test_detect_local_activity_fixtures(pactl, capsys, sinks, captures, expected):
    calls = pactl(sinks, captures)
    assert ducking.detect_local() == expected
    assert len(calls) == 2
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("pactl absent"),
        subprocess.CalledProcessError(1, ["pactl"], stderr="bus unavailable"),
        subprocess.TimeoutExpired(["pactl"], 0.2),
        PermissionError("pactl denied"),
        UnicodeError("invalid output encoding"),
    ],
    ids=["absent", "server-failure", "timeout", "permission", "encoding"],
)
@pytest.mark.parametrize("fail_on", ["sink-inputs", "source-outputs"])
def test_detect_local_failures_open_with_one_warning(pactl, capsys, failure, fail_on):
    pactl(stream(role="communication"), failure=failure, fail_on=fail_on)
    assert ducking.detect_local() == INACTIVE
    assert ducking.detect_local() == INACTIVE
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "warning" in lines[0].lower()


@pytest.mark.parametrize(
    "malformed",
    [
        "not pactl output",
        "Sink Input #broken\n\tCorked: no\n",
        "Sink Input #1\n\tProperties:\n\t\tmedia.role = \"communication\"\n",
        stream(role="communication", corked="unknown"),
        stream(role="communication").replace('media.role = "communication"', 'media.role = "communication'),
        stream(role="communication") + "\ncorrupt trailing output\n",
    ],
    ids=["garbage", "bad-header", "missing-corked", "bad-corked", "bad-property", "partial-output"],
)
def test_detect_local_malformed_output_fails_open(pactl, capsys, malformed):
    pactl(malformed)
    assert ducking.detect_local() == INACTIVE
    assert ducking.detect_local() == INACTIVE
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_malformed_capture_discards_partial_communication(pactl, capsys):
    pactl(stream(role="communication"), "Source Output #1\n\tCorked: unknown\n")
    assert ducking.detect_local() == INACTIVE
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_detect_local_accepts_format_fields_with_equals(pactl, capsys):
    sink = stream(role="communication").replace(
        "\tProperties:\n", '\tFormat: pcm, format.rate = "24000"\n\tProperties:\n'
    )
    pactl(sink)
    assert ducking.detect_local() == {"comm_active": True, "capture_active": False}
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "own",
    [
        stream(role="communication", pid=os.getpid()),
        stream(role="communication", name="agent-tts"),
        stream(role="communication", name="agent_tts"),
        stream(role="communication").replace('binary = "python3"', 'binary = "agent-tts"'),
    ],
    ids=["current-python-pid", "application-name", "module-name", "process-binary"],
)
def test_detect_local_excludes_own_stream(pactl, own):
    pactl(own, own.replace("Sink Input", "Source Output"))
    assert ducking.detect_local() == INACTIVE


def test_other_python_process_and_external_stream_still_detected(pactl):
    own = stream(role="communication", pid=os.getpid())
    external = stream(index=2, role="communication")
    pactl(own + external, stream(kind="Source Output"))
    assert ducking.detect_local() == {"comm_active": True, "capture_active": True}


def test_detection_recovers_without_resetting_warning_suppression(pactl, capsys):
    pactl(failure=FileNotFoundError())
    assert ducking.detect_local() == INACTIVE
    pactl(stream(role="communication"))
    assert ducking.detect_local()["comm_active"] is True
    pactl(failure=PermissionError())
    assert ducking.detect_local() == INACTIVE
    assert len(capsys.readouterr().err.splitlines()) == 1


def pcm(samples=None, sample_format=None, channels=1):
    import miniaudio

    return SimpleNamespace(
        samples=array("h", samples if samples is not None else [12000, -12000] * 8),
        sample_rate=4,
        nchannels=channels,
        sample_width=2,
        sample_format=sample_format or miniaudio.SampleFormat.SIGNED16,
    )


def test_session_gain_applied_and_restored_exactly():
    from agent_tts.audio import AudioSession

    session = AudioSession()
    decoded = pcm()
    session.prepare_pcm(decoded)
    session.state["status"] = "playing"
    generator = session._stream_generator()
    next(generator)
    gain = ducking.gain_linear(-12)
    session.set_gain(gain)
    assert generator.send(2) == array("h", [int(12000 * gain), int(-12000 * gain)]).tobytes()
    assert session.raw_bytes == decoded.samples.tobytes()
    session.set_gain(1.0)
    assert session.gain == 1.0
    assert generator.send(2) == decoded.samples[:2].tobytes()


@pytest.fixture
def local_playback(monkeypatch):
    from agent_tts import audio, cli

    clock = [0.0]
    monkeypatch.setattr(audio.time, "monotonic", lambda: clock[0])
    monkeypatch.delenv("AGENT_TTS_DUCK_POLICY", raising=False)
    monkeypatch.delenv("AGENT_TTS_DUCK_POLICY_LOCAL", raising=False)
    states = [{"comm_active": True, "capture_active": False}]
    checks = []
    core_detect = ducking.detect_local

    def detect():
        checks.append(clock[0])
        return states[-1].copy()

    monkeypatch.setattr(ducking, "detect_local", detect)

    def build(policy="duck", db="12"):
        return cli._build_playback_session(
            "local", "Ducking test",
            env={"AGENT_TTS_DUCK_POLICY_LOCAL": policy, "AGENT_TTS_DUCK_DB": db},
        )

    return SimpleNamespace(
        audio=audio, cli=cli, clock=clock, states=states, checks=checks,
        build=build, core_detect=core_detect,
    )


def test_local_preplay_detection_precedes_construction(local_playback, monkeypatch):
    events = []
    original = local_playback.cli.AudioSession

    def detect():
        events.append("detect")
        return {"comm_active": True, "capture_active": False}

    def construct(**kwargs):
        events.append("construct")
        return original(**kwargs)

    monkeypatch.setattr(ducking, "detect_local", detect)
    monkeypatch.setattr(local_playback.cli, "AudioSession", construct)
    session = local_playback.build(db="6")
    assert events == ["detect", "construct"]
    assert session.gain == ducking.gain_linear(-6)
    assert "comm_active=true" in session.handle_ipc_command("status")


def test_ignore_is_byte_identical_and_never_detects(local_playback, monkeypatch):
    session = local_playback.build("ignore")
    assert local_playback.checks == []
    decoded = pcm()
    session.prepare_pcm(decoded)
    session.state["status"] = "playing"
    session._playback_live = True
    monkeypatch.setattr(session, "_attenuate_pcm", lambda *args: pytest.fail("unity must bypass gain conversion"))
    generator = session._stream_generator()
    next(generator)
    for now in [0, 2, 4]:
        local_playback.clock[0] = now
        session._check_ducking()
        assert generator.send(2) == decoded.samples[:2].tobytes()
    assert session.gain == 1.0
    assert session.state["status"] == "playing"
    assert local_playback.checks == []


@pytest.mark.parametrize("target", ["winhost", "windows", "wsl-ps"])
def test_nonlocal_targets_bypass_ducking(local_playback, monkeypatch, target):
    sentinel = object()
    monkeypatch.setattr(local_playback.cli, "RemoteAudioSession", lambda **kwargs: sentinel)
    monkeypatch.setattr(local_playback.cli, "PowershellSession", lambda **kwargs: sentinel)
    monkeypatch.setattr(local_playback.cli, "is_wsl_ps_available", lambda: True)
    assert local_playback.cli._build_playback_session(
        target, "Remote", env={"AGENT_TTS_DUCK_POLICY": "pause"}
    ) is sentinel
    assert local_playback.checks == []


def test_poll_cadence_release_and_zero_checks_at_rest(local_playback):
    session = local_playback.build()
    for now in [0, 2, 4]:
        local_playback.clock[0] = now
        session._check_ducking()
    assert local_playback.checks == [0.0]
    session.state["status"] = "playing"
    session._playback_live = True
    local_playback.states.append(INACTIVE)
    session._check_ducking()
    assert session.gain == 1.0
    assert "comm_active=false" in session.handle_ipc_command("status")
    for now in [4.1, 5, 5.9, 6]:
        local_playback.clock[0] = now
        session._check_ducking()
    assert local_playback.checks == [0, 4, 6]
    session.stop()
    local_playback.clock[0] = 20
    session._check_ducking()
    assert local_playback.checks == [0, 4, 6]


def test_pause_release_reuses_ipc_and_smart_rewind(local_playback, monkeypatch):
    session = local_playback.build("pause")
    session.prepare_pcm(pcm([100] * 32))
    session.current_frame = 20
    session.state["status"] = "playing"
    session._playback_live = True
    commands = []
    original = session.handle_ipc_command

    def handle(cmd, **kwargs):
        commands.append(cmd)
        return original(cmd, **kwargs)

    monkeypatch.setattr(session, "handle_ipc_command", handle)
    monkeypatch.setattr(local_playback.audio.time, "time", lambda: 100 + local_playback.clock[0])
    local_playback.clock[0] = 2
    session._check_ducking()
    assert session.state["status"] == "paused"
    assert session.paused_at == 102
    assert session.gain == 1.0
    local_playback.states.append(INACTIVE)
    local_playback.clock[0] = 4
    session._check_ducking()
    assert session.state["status"] == "playing"
    assert session.current_frame == 12  # existing 2-second rewind, 4 Hz
    assert "pause" in commands and "resume" in commands


def test_manual_pause_is_not_resumed_on_source_release(local_playback):
    session = local_playback.build("pause")
    session.state["status"] = "playing"
    session._playback_live = True
    local_playback.clock[0] = 2
    session._check_ducking()
    assert session.state["status"] == "paused"
    session.handle_ipc_command("pause")  # explicitly retain pause even if automatic
    local_playback.states.append(INACTIVE)
    local_playback.clock[0] = 4
    session._check_ducking()
    assert session.state["status"] == "paused"
    assert local_playback.checks == [0, 2]


@pytest.mark.parametrize("policy", ["duck", "pause"])
def test_live_detection_failure_releases_gain_or_pause(local_playback, pactl, monkeypatch, capsys, policy):
    session = local_playback.build(policy)
    session.state["status"] = "playing"
    session._playback_live = True
    local_playback.clock[0] = 2
    session._check_ducking()
    # Exercise T-01's real fail-open path, not merely a false mock snapshot.
    pactl(failure=FileNotFoundError())
    monkeypatch.setattr(ducking, "detect_local", local_playback.core_detect)
    local_playback.clock[0] = 4
    session._check_ducking()
    local_playback.clock[0] = 6
    session._check_ducking()
    assert session.gain == 1.0
    assert session.state["status"] == "playing"
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_daemon_and_session_status_include_comm_active(local_playback):
    import json
    from agent_tts.daemon import Daemon
    from agent_tts.ipc import ipc_reply_json

    daemon = Daemon()
    assert json.loads(ipc_reply_json(daemon.handle_command("status")))["comm_active"] == "false"
    session = local_playback.build()
    daemon.active_session = session
    reply = daemon.handle_command("status")
    assert reply.count("comm_active=") == 1
    assert json.loads(ipc_reply_json(reply))["comm_active"] == "true"
    assert reply.index("comm_active=") < reply.index(" text=")
    assert local_playback.checks == [0]


def test_queue_builder_inherits_local_ducking(local_playback, monkeypatch):
    from agent_tts.daemon import Daemon

    monkeypatch.setenv("AGENT_TTS_DUCK_POLICY_LOCAL", "duck")
    daemon = Daemon()
    monkeypatch.setattr(daemon, "_ensure_cli", lambda: local_playback.cli)
    session = daemon._build_queue_session({"text": "Hello", "playback": "local"})
    assert session.gain == ducking.gain_linear(-12)
    assert local_playback.checks == [0]


@pytest.mark.parametrize("policy", ["duck", "pause", "ignore"])
def test_play_applies_policy_before_first_frame_and_restores_on_finish(local_playback, monkeypatch, policy):
    session = local_playback.build(policy)
    chunks = []
    events = []
    generator = [None]

    class Device:
        def __init__(self, **kwargs):
            pass

        def start(self, stream):
            generator[0] = stream
            events.append(session.state["status"])
            chunks.append(stream.send(2))

        def stop(self):
            events.append("stop")

        def close(self):
            events.append("close")

    def sleep(_):
        local_playback.clock[0] += 0.5
        if local_playback.clock[0] >= 2:
            local_playback.states.append(INACTIVE)
        chunks.append(generator[0].send(2))

    monkeypatch.setattr(local_playback.audio.miniaudio, "PlaybackDevice", Device)
    monkeypatch.setattr(local_playback.audio.time, "sleep", sleep)
    decoded = pcm()
    session.play(decoded)
    if policy == "pause":
        assert events[0] == "paused"
        assert chunks[0] == bytes(4)
    elif policy == "duck":
        gain = ducking.gain_linear(-12)
        assert chunks[0] == array("h", [int(12000 * gain), int(-12000 * gain)]).tobytes()
    else:
        assert chunks[0] == decoded.samples[:2].tobytes()
        assert local_playback.checks == []
    assert decoded.samples[:2].tobytes() in chunks
    assert session.gain == 1.0
    assert session.state["status"] == "stopped"
    assert events[-2:] == ["stop", "close"]
    checks = len(local_playback.checks)
    local_playback.clock[0] = 100
    session._check_ducking()
    assert len(local_playback.checks) == checks


@pytest.mark.parametrize("fmt,code,values,expected", [
    ("UNSIGNED8", "B", [0, 128, 255], [64, 128, 191]),
    ("SIGNED16", "h", [-32768, 0, 32767], [-16384, 0, 16383]),
    ("SIGNED32", "i", [-2147483648, 0, 2147483647], [-1073741824, 0, 1073741823]),
    ("FLOAT32", "f", [-1.0, 0.0, 0.5], [-0.5, 0.0, 0.25]),
])
def test_gain_supports_pcm_formats(fmt, code, values, expected):
    import miniaudio
    from agent_tts.audio import AudioSession

    source = array(code, values)
    decoded = SimpleNamespace(
        samples=source, sample_rate=4, nchannels=1, sample_width=source.itemsize,
        sample_format=getattr(miniaudio.SampleFormat, fmt),
    )
    session = AudioSession()
    session.prepare_pcm(decoded)
    session.state["status"] = "playing"
    session.set_gain(0.5)
    generator = session._stream_generator()
    next(generator)
    assert generator.send(3) == array(code, expected).tobytes()
    assert session.raw_bytes == source.tobytes()


def test_gain_supports_packed_signed24_and_stereo():
    import sys
    import miniaudio
    from agent_tts.audio import AudioSession

    values = [-8388608, 8388607, -4000, 4000]
    source = b"".join(value.to_bytes(3, sys.byteorder, signed=True) for value in values)
    decoded = SimpleNamespace(
        samples=array("B", source), sample_rate=4, nchannels=2, sample_width=3,
        sample_format=miniaudio.SampleFormat.SIGNED24,
    )
    session = AudioSession()
    session.prepare_pcm(decoded)
    session.state["status"] = "playing"
    session.set_gain(0.5)
    generator = session._stream_generator()
    next(generator)
    expected = b"".join(int(value * 0.5).to_bytes(3, sys.byteorder, signed=True) for value in values)
    assert generator.send(2) == expected
    assert session.current_frame == 2


def test_callback_does_not_detect_even_after_snapshot_expires(local_playback):
    session = local_playback.build()
    session.prepare_pcm(pcm())
    session.state["status"] = "playing"
    session._playback_live = True
    local_playback.clock[0] = 20
    generator = session._stream_generator()
    next(generator)
    for _ in range(8):
        generator.send(2)
    assert local_playback.checks == [0]


def test_stale_preplay_snapshot_refreshed_before_first_frame(local_playback, monkeypatch):
    local_playback.states.append(INACTIVE)
    session = local_playback.build()
    assert session.gain == 1.0
    local_playback.clock[0] = 10
    local_playback.states.append({"comm_active": False, "capture_active": True})
    chunks = []

    class Device:
        def __init__(self, **kwargs):
            pass

        def start(self, stream):
            chunks.append(stream.send(16))

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(local_playback.audio.miniaudio, "PlaybackDevice", Device)
    session.play(pcm())
    gain = ducking.gain_linear(-12)
    assert chunks[0] == array("h", [int(12000 * gain), int(-12000 * gain)] * 8).tobytes()
    assert local_playback.checks == [0, 10]


def test_stop_during_detection_cannot_reactivate_session(local_playback, monkeypatch):
    session = local_playback.build("pause")
    session.state["status"] = "playing"
    session._playback_live = True
    local_playback.clock[0] = 2

    def detect():
        session.handle_ipc_command("stop")
        return {"comm_active": True, "capture_active": True}

    monkeypatch.setattr(ducking, "detect_local", detect)
    session._check_ducking()
    assert session.state["status"] == "stopped"
    assert session.gain == 1.0
    assert session.state["comm_active"] is False


@pytest.mark.parametrize("stage", ["construct", "start"])
def test_device_failure_disarms_ducking(local_playback, monkeypatch, stage):
    session = local_playback.build()

    class Device:
        def __init__(self, **kwargs):
            if stage == "construct":
                raise RuntimeError("device unavailable")

        def start(self, stream):
            raise RuntimeError("device unavailable")

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(local_playback.audio.miniaudio, "PlaybackDevice", Device)
    with pytest.raises(RuntimeError, match="device unavailable"):
        session.play(pcm())
    assert session.gain == 1.0
    assert session.state["status"] == "stopped"
    local_playback.clock[0] = 100
    session._check_ducking()
    assert local_playback.checks == [0]
