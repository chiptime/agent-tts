"""Read-only karaoke IPC family over remote playback sessions.

PowershellSession (wsl-ps) and RemoteAudioSession (winhost/windows) must
serve highlight/scroll-info/sentence/paragraph byte-compatibly with the
local AudioSession replies, keep ERR for navigation commands, and track
position from the bytes handed to the pipe. No real PowerShell is needed:
sessions are constructed directly with a synthetic BoundaryMap, and the
persistent process is faked for the pipe-handoff ramp tests.
"""

import io
import struct
import subprocess

import pytest

import agent_tts.powershell_playback as psp
from agent_tts.audio import AudioSession
from agent_tts.boundaries import BoundaryMap, Sentence, Word
from agent_tts.winhost_client import RemoteAudioSession


# -- synthetic boundaries -------------------------------------------------------

SENTENCES = [
    Sentence(index=0, start_sec=0.0, duration_sec=2.0, text="First sentence here."),
    Sentence(index=1, start_sec=2.0, duration_sec=3.0, text="Second sentence is longer."),
    Sentence(index=2, start_sec=5.0, duration_sec=1.5, text="Third sentence ends."),
]

WORDS = [
    Word(index=0, sentence_index=0, start_sec=0.0, duration_sec=0.6, text="First"),
    Word(index=1, sentence_index=0, start_sec=0.6, duration_sec=0.7, text="sentence"),
    Word(index=2, sentence_index=0, start_sec=1.3, duration_sec=0.7, text="here."),
    Word(index=3, sentence_index=1, start_sec=2.0, duration_sec=0.7, text="Second"),
    Word(index=4, sentence_index=1, start_sec=2.7, duration_sec=0.8, text="sentence"),
    Word(index=5, sentence_index=1, start_sec=3.5, duration_sec=0.5, text="is"),
    Word(index=6, sentence_index=1, start_sec=4.0, duration_sec=1.0, text="longer."),
    Word(index=7, sentence_index=2, start_sec=5.0, duration_sec=0.5, text="Third"),
    Word(index=8, sentence_index=2, start_sec=5.5, duration_sec=0.5, text="sentence"),
    Word(index=9, sentence_index=2, start_sec=6.0, duration_sec=0.5, text="ends."),
]


def make_bmap():
    return BoundaryMap(sentences=list(SENTENCES), words=list(WORDS))


def make_long_bmap():
    """One paragraph whose joined text exceeds the 80-char IPC truncation."""
    long_text = "Esta es una oracion deliberadamente larga para desbordar el limite de ochenta caracteres del payload."
    return BoundaryMap(
        sentences=[Sentence(index=0, start_sec=0.0, duration_sec=4.0, text=long_text)]
    )


TOTAL_SEC = 6.5
RATE = 24000


def local_session_at(bmap, pos_sec):
    session = AudioSession(boundaries=bmap)
    session.sample_rate = RATE
    session.current_frame = int(pos_sec * RATE)
    session.total_frames = int(TOTAL_SEC * RATE)
    return session


def ps_session_at(bmap, pos_sec):
    session = psp.PowershellSession(boundaries=bmap)
    session.sample_rate = RATE
    session.current_frame = int(pos_sec * RATE)
    session.total_frames = int(TOTAL_SEC * RATE)
    return session


def remote_session_at(bmap, pos_sec, target="winhost"):
    session = RemoteAudioSession(boundaries=bmap, target=target)
    session.sample_rate = RATE
    session.current_frame = int(pos_sec * RATE)
    session.total_frames = int(TOTAL_SEC * RATE)
    return session


READ_ONLY_COMMANDS = [
    "highlight",
    "current-highlight",
    "scroll-info",
    "autoscroll-info",
    "autoscroll",
    "sentence",
    "current-sentence",
    "current_sentence",
    "paragraph",
    "current-paragraph",
    "current_paragraph",
]


# -- byte-compatibility with the local AudioSession replies ---------------------


@pytest.mark.parametrize("pos", [0.0, 2.5, 5.7])
@pytest.mark.parametrize("command", READ_ONLY_COMMANDS)
def test_powershell_reply_matches_local_byte_for_byte(command, pos):
    bmap = make_bmap()
    local = local_session_at(bmap, pos)
    remote = ps_session_at(make_bmap(), pos)
    assert remote.handle_ipc_command(command) == local.handle_ipc_command(command)


@pytest.mark.parametrize("pos", [0.0, 2.5, 5.7])
@pytest.mark.parametrize("command", READ_ONLY_COMMANDS)
def test_winhost_reply_matches_local_byte_for_byte(command, pos):
    bmap = make_bmap()
    local = local_session_at(bmap, pos)
    remote = remote_session_at(make_bmap(), pos)
    assert remote.handle_ipc_command(command) == local.handle_ipc_command(command)


@pytest.mark.parametrize("pos", [0.0, 2.5])
@pytest.mark.parametrize("factory", [ps_session_at, remote_session_at])
def test_paragraph_truncation_matches_local(factory, pos):
    bmap = make_long_bmap()
    local = local_session_at(bmap, pos)
    remote = factory(make_long_bmap(), pos)
    assert remote.handle_ipc_command("paragraph") == local.handle_ipc_command("paragraph")
    assert local.handle_ipc_command("paragraph").endswith("...")


def test_highlight_empty_when_no_sentences():
    assert ps_session_at(BoundaryMap(), 0.0).handle_ipc_command("highlight") == ""
    assert remote_session_at(BoundaryMap(), 0.0).handle_ipc_command("highlight") == ""
    assert ps_session_at(BoundaryMap(), 0.0).handle_ipc_command("sentence") == "sent_idx=-1 text="
    assert remote_session_at(BoundaryMap(), 0.0).handle_ipc_command("paragraph") == "para_idx=-1 text="


def test_highlight_carries_ansi_karaoke_sgr():
    reply = ps_session_at(make_bmap(), 3.0).handle_ipc_command("highlight")
    assert "\x1b[1;33;4m" in reply  # current word: bold yellow underline
    assert "\x1b[2m" in reply  # past words: dimmed


def test_scroll_info_exact_format():
    remote = ps_session_at(make_bmap(), 2.5)
    assert remote.handle_ipc_command("scroll-info") == (
        "pos=2.50 total=6.50 pct=38.5 sent_idx=1 total_sents=3 para_idx=0 total_paras=1"
    )


def test_status_reply_unchanged():
    # The daemon vía única seeds provider/voice into every session kind's
    # status metadata (engine defaults apply when the caller passes none),
    # so the karaoke status reply now carries that suffix.
    remote = ps_session_at(make_bmap(), 2.5)
    remote.state["status"] = "playing"
    assert remote.handle_ipc_command("status") == (
        "status=playing pos=2.50 total=6.50 sent_idx=1 para_idx=0 sentence=Second sentence is longer."
        " provider=edge voice=es-ES-ElviraNeural"
    )
    outer = remote_session_at(make_bmap(), 2.5)
    outer.state["status"] = "playing"
    assert outer.handle_ipc_command("status") == (
        "status=playing pos=2.50 total=6.50 sent_idx=1 para_idx=0 sentence=Second sentence is longer."
        " provider=edge voice=es-ES-ElviraNeural"
    )


# -- ERR fallback: navigation stays unsupported -------------------------------


NAVIGATION_COMMANDS = [
    "seek +10",
    "seek",
    "rewind",
    "forward",
    "next-sentence",
    "prev-sentence",
    "next-paragraph",
    "prev-paragraph",
    "jump",
]


@pytest.mark.parametrize("command", NAVIGATION_COMMANDS)
def test_powershell_navigation_stays_err(command):
    reply = ps_session_at(make_bmap(), 2.5).handle_ipc_command(command)
    assert reply.startswith("ERR: command ")
    assert "is not supported for wsl-ps playback" in reply


@pytest.mark.parametrize("command", NAVIGATION_COMMANDS)
def test_winhost_navigation_stays_err(command):
    reply = remote_session_at(make_bmap(), 2.5).handle_ipc_command(command)
    assert reply.startswith("ERR: command ")
    assert "is not supported for winhost playback" in reply


def test_windows_target_navigation_err_names_target():
    reply = remote_session_at(make_bmap(), 2.5, target="windows").handle_ipc_command("seek +10")
    assert "is not supported for windows playback" in reply


def test_empty_command_still_err():
    assert ps_session_at(make_bmap(), 0.0).handle_ipc_command("   ").startswith("ERR:")
    assert remote_session_at(make_bmap(), 0.0).handle_ipc_command("   ").startswith("ERR:")


# -- fallback carriers answer with their live clock ----------------------------


def test_ps_fallback_carrier_answers_read_only():
    outer = remote_session_at(make_bmap(), 99.0)  # outer clock deliberately wrong
    carrier = ps_session_at(make_bmap(), 2.5)
    outer._ps_session = carrier
    assert outer.handle_ipc_command("highlight") == carrier.handle_ipc_command("highlight")
    assert outer.handle_ipc_command("scroll-info").startswith("pos=2.50 total=6.50")
    assert "pos=2.50" in outer.handle_ipc_command("status")


def test_local_fallback_carrier_answers_read_only():
    bmap = make_bmap()
    outer = remote_session_at(bmap, 99.0, target="windows")
    carrier = local_session_at(bmap, 2.5)
    outer._local_session = carrier
    assert outer.handle_ipc_command("scroll-info") == carrier.handle_ipc_command("scroll-info")
    assert "pos=2.50" in outer.handle_ipc_command("status")


# -- position ramp: pos derives from the bytes handed to the pipe ---------------


class FakeStdin(io.BytesIO):
    def write(self, data):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return super().write(data)


class FakePopen:
    def __init__(self, argv, stdin=None, stdout=None, stderr=None):
        self.argv = argv
        self.stdin = FakeStdin()
        self.stdout = stdout
        self.stderr = io.BytesIO(b"")
        self.exit_code = None

    def poll(self):
        return self.exit_code

    def wait(self, timeout=None):
        if self.exit_code is None:
            raise subprocess.TimeoutExpired(cmd=self.argv, timeout=timeout)
        return self.exit_code

    def kill(self):
        self.exit_code = -9


@pytest.fixture
def fake_popen(monkeypatch):
    monkeypatch.setattr(psp.subprocess, "Popen", FakePopen)
    return FakePopen


def make_decoded(frames, sample_rate=24000, nchannels=1, sample_width=2):
    import array

    class FakeDecoded:
        pass

    decoded = FakeDecoded()
    decoded.sample_rate = sample_rate
    decoded.nchannels = nchannels
    decoded.sample_width = sample_width
    decoded.samples = array.array("h", [((i * 37) % 20000) - 10000 for i in range(frames * nchannels)])
    return decoded


@pytest.fixture
def frozen_clock(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(psp.time, "monotonic", lambda: clock["now"])
    return clock


def test_pos_ramps_after_pipe_handoff(fake_popen, frozen_clock):
    session = psp.PowershellSession(boundaries=make_bmap())
    session.state["status"] = "playing"
    session.prepare_pcm(make_decoded(48000))  # one 2.0s group handed to the pipe
    assert session.pos_frames() == 0  # ramp anchored at the group's start
    frozen_clock["now"] += 0.5
    assert session.pos_frames() == 12000  # 0.5s * 24000Hz
    frozen_clock["now"] += 5.0
    assert session.pos_frames() == 48000  # capped at the bytes handed to the pipe


def test_pos_second_group_resyncs_ramp(fake_popen, frozen_clock):
    session = psp.PowershellSession(boundaries=make_bmap())
    session.state["status"] = "playing"
    session.prepare_pcm(make_decoded(48000))  # group 1: frames 0..48000
    frozen_clock["now"] += 1.0
    session.append_pcm(make_decoded(24000))  # group 2 handed off at pos=1.0s
    assert session.pos_frames() == 48000  # resynced to the new group's start
    frozen_clock["now"] += 0.25
    assert session.pos_frames() == 54000  # 48000 + 0.25s * 24000Hz


def test_pause_freezes_pos_and_resume_rearms(fake_popen, frozen_clock):
    session = psp.PowershellSession(boundaries=make_bmap())
    session.state["status"] = "playing"
    session.prepare_pcm(make_decoded(48000))
    frozen_clock["now"] += 0.5
    session.pause()
    frozen_clock["now"] += 2.0
    assert session.pos_frames() == 12000  # frozen at the pause estimate
    session.resume()
    frozen_clock["now"] += 0.25
    assert session.pos_frames() == 18000  # re-armed from the frozen base


def test_hard_stop_freezes_pos(fake_popen, frozen_clock):
    session = psp.PowershellSession(boundaries=make_bmap())
    session.state["status"] = "playing"
    session.prepare_pcm(make_decoded(48000))
    frozen_clock["now"] += 0.5
    session._hard_stop()
    frozen_clock["now"] += 5.0
    assert session.pos_frames() == 12000  # the cut freezes the estimate


def test_failed_group_write_does_not_advance_pos(fake_popen, frozen_clock):
    session = psp.PowershellSession(boundaries=make_bmap())
    session.state["status"] = "playing"
    session.prepare_pcm(make_decoded(48000))
    # Reach the process the first group spawned, then kill it under us.
    assert isinstance(session._proc, FakePopen)
    session._proc.exit_code = 1  # process died after the first group
    with pytest.raises(RuntimeError):
        session.append_pcm(make_decoded(24000))
    assert session.pos_frames() == 0  # dead write advanced nothing
    assert session._sent_frames == 48000  # cap still counts only group 1
