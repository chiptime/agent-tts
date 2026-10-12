"""Local ducking contracts; no test needs an installed audio server or pactl."""

import os
import subprocess

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
