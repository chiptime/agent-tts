"""Remote playback for --play-file replays (play_mp3_file target resolution).

Replays honor AGENT_TTS_PLAYBACK exactly like the speak path: remote targets
feed the same remote session classes through play(), under the same lock/pid
protocol as play_mp3_data, and any remote failure warns once and falls back
to the local path.
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from agent_tts import audio
from agent_tts.audio import play_mp3_file
from agent_tts.playback_target import InvalidPlaybackTarget


@contextlib.contextmanager
def _env(**overrides):
    """Pins AGENT_TTS_PLAYBACK (None removes it), restoring on exit."""
    with mock.patch.dict(os.environ):
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield


class FakeRemoteSession:
    """Records the replay feed; duck-types the session surface play_mp3_file uses."""

    instances = []
    # Class attribute: set before play_mp3_file runs to make play() raise.
    play_error = None

    def __init__(self, label="Audio", auto_rewind_sec=2.0, boundaries=None, highlight=False,
                 autoscroll=False, bionic=False, zen=False, target="", env=None):
        self.label = label
        self.target = target
        self.env = env
        self.ipc_started = False
        self.stopped = False
        self.played = []
        self.locks_at_play = None
        FakeRemoteSession.instances.append(self)

    def start_ipc(self):
        self.ipc_started = True

    def play(self, decoded):
        lock = getattr(audio, "LOCK_FILE", "")
        pid = getattr(audio, "PID_FILE", "")
        pid_content = ""
        try:
            with open(pid, "r", encoding="utf-8") as f:
                pid_content = f.read()
        except OSError:
            pass
        self.locks_at_play = (os.path.exists(lock), os.path.exists(pid), pid_content)
        if self.play_error is not None:
            raise self.play_error
        self.played.append(decoded)

    def stop(self):
        self.stopped = True


@contextlib.contextmanager
def _isolated_locks():
    """Redirects the transient lock/pid/socket files into a temp dir."""
    tmp = tempfile.mkdtemp(prefix="agent-tts-playfile-")
    lock = os.path.join(tmp, "playing.lock")
    pid = os.path.join(tmp, "current.pid")
    sock = os.path.join(tmp, "player.sock")
    try:
        with mock.patch.object(audio, "LOCK_FILE", lock), \
                mock.patch.object(audio, "PID_FILE", pid), \
                mock.patch.object(audio, "IPC_SOCKET", sock):
            yield {"lock": lock, "pid": pid, "sock": sock, "dir": tmp}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class PlayFileTargetTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-tts-playfile-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.mp3_path = os.path.join(self.tmp, "stored.mp3")
        with open(self.mp3_path, "wb") as f:
            f.write(b"fake-mp3-bytes")
        self.decoded = object()  # sentinel returned by the patched decoder

    def _patch_decoder(self):
        return mock.patch.object(audio.miniaudio, "decode", return_value=self.decoded)

    def _patch_remote(self):
        """Patches both remote session classes and resets the instance log."""
        FakeRemoteSession.instances = []
        ps_patch = mock.patch("agent_tts.powershell_playback.PowershellSession", FakeRemoteSession)
        win_patch = mock.patch("agent_tts.winhost_client.RemoteAudioSession", FakeRemoteSession)
        return ps_patch, win_patch

    def _patch_local(self):
        return mock.patch.object(audio, "play_mp3_data")


class TestRemoteReplay(PlayFileTargetTestBase):
    def test_winhost_env_feeds_remote_session_with_lock_protocol(self):
        ps_patch, win_patch = self._patch_remote()
        with _env(AGENT_TTS_PLAYBACK="winhost"), \
                _isolated_locks() as locks, \
                ps_patch, win_patch, \
                self._patch_decoder(), \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path)
        self.assertEqual(len(FakeRemoteSession.instances), 1)
        session = FakeRemoteSession.instances[0]
        self.assertEqual(session.target, "winhost")
        self.assertTrue(session.ipc_started)
        self.assertEqual(session.played, [self.decoded])
        self.assertTrue(session.stopped)
        # Same mutex contract as play_mp3_data: lock+pid present while playing.
        self.assertEqual(session.locks_at_play[0], True)
        self.assertEqual(session.locks_at_play[1], True)
        self.assertEqual(session.locks_at_play[2], str(os.getpid()))
        # Cleaned up afterwards.
        self.assertFalse(os.path.exists(locks["lock"]))
        self.assertFalse(os.path.exists(locks["pid"]))
        # The local path was never used.
        local.assert_not_called()

    def test_wsl_ps_env_feeds_powershell_session(self):
        ps_patch, win_patch = self._patch_remote()
        with _env(AGENT_TTS_PLAYBACK="wsl-ps"), \
                _isolated_locks(), \
                ps_patch, win_patch, \
                mock.patch("agent_tts.powershell_playback.is_wsl_ps_available", return_value=True), \
                self._patch_decoder(), \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path, label="replay-label")
        self.assertEqual(len(FakeRemoteSession.instances), 1)
        session = FakeRemoteSession.instances[0]
        self.assertEqual(session.label, "replay-label")
        self.assertEqual(session.played, [self.decoded])
        self.assertTrue(session.stopped)
        local.assert_not_called()

    def test_wsl_ps_unavailable_warns_and_falls_back_to_local(self):
        ps_patch, win_patch = self._patch_remote()
        stderr = io.StringIO()
        with _env(AGENT_TTS_PLAYBACK="wsl-ps"), \
                _isolated_locks(), \
                ps_patch, win_patch, \
                mock.patch("agent_tts.powershell_playback.is_wsl_ps_available", return_value=False), \
                mock.patch("sys.stderr", new=stderr), \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path)
        self.assertEqual(FakeRemoteSession.instances, [])
        self.assertIn("falling back to local playback", stderr.getvalue())
        local.assert_called_once()
        self.assertEqual(local.call_args[0][0], b"fake-mp3-bytes")
        # Default label parity: "Audio" is truthy, so the basename fallback
        # only applies for the empty label (cli.py passes the basename itself).
        self.assertEqual(local.call_args[1]["label"], "Audio")

    def test_remote_play_failure_warns_once_and_falls_back_to_local(self):
        ps_patch, win_patch = self._patch_remote()
        stderr = io.StringIO()
        with _env(AGENT_TTS_PLAYBACK="winhost"), \
                _isolated_locks() as locks, \
                ps_patch, win_patch, \
                self._patch_decoder(), \
                mock.patch("sys.stderr", new=stderr), \
                self._patch_local() as local:
            FakeRemoteSession.play_error = RuntimeError("winhost unreachable")
            try:
                play_mp3_file(self.mp3_path)
            finally:
                FakeRemoteSession.play_error = None
        self.assertEqual(len(FakeRemoteSession.instances), 1)
        session = FakeRemoteSession.instances[0]
        self.assertTrue(session.stopped)
        self.assertIn("falling back to local playback", stderr.getvalue())
        # Exactly ONE warning (the failure), not two.
        self.assertEqual(stderr.getvalue().count("falling back to local playback"), 1)
        local.assert_called_once()
        self.assertFalse(os.path.exists(locks["lock"]))
        self.assertFalse(os.path.exists(locks["pid"]))


class TestLocalReplay(PlayFileTargetTestBase):
    def test_default_local_target_uses_play_mp3_data_exactly_as_before(self):
        ps_patch, win_patch = self._patch_remote()
        stderr = io.StringIO()
        with _env(AGENT_TTS_PLAYBACK=None), \
                _isolated_locks(), \
                ps_patch, win_patch, \
                mock.patch("sys.stderr", new=stderr), \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path, highlight=True, bionic=True)
        self.assertEqual(FakeRemoteSession.instances, [])
        self.assertEqual(stderr.getvalue(), "")
        local.assert_called_once_with(
            b"fake-mp3-bytes",
            label="Audio",
            auto_rewind_sec=2.0,
            boundaries=None,
            highlight=True,
            autoscroll=False,
            bionic=True,
            zen=False,
            provider="",
            voice="",
        )

    def test_explicit_local_env_stays_local(self):
        ps_patch, win_patch = self._patch_remote()
        with _env(AGENT_TTS_PLAYBACK="local"), \
                _isolated_locks(), \
                ps_patch, win_patch, \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path)
        self.assertEqual(FakeRemoteSession.instances, [])
        local.assert_called_once()

    def test_missing_file_is_a_no_op(self):
        with self._patch_local() as local:
            play_mp3_file(os.path.join(self.tmp, "missing.mp3"))
        local.assert_not_called()


class TestFailOpenTargets(PlayFileTargetTestBase):
    def test_invalid_env_value_warns_and_falls_back_to_local(self):
        ps_patch, win_patch = self._patch_remote()
        stderr = io.StringIO()
        with _env(AGENT_TTS_PLAYBACK="bogus-target"), \
                _isolated_locks(), \
                ps_patch, win_patch, \
                mock.patch("sys.stderr", new=stderr), \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path)
        self.assertEqual(FakeRemoteSession.instances, [])
        self.assertIn("Playback target invalid", stderr.getvalue())
        self.assertIn("falling back to local playback", stderr.getvalue())
        local.assert_called_once()

    def test_unknown_future_target_warns_and_falls_back_to_local(self):
        """A target playback_target may add later that audio.py cannot build."""
        ps_patch, win_patch = self._patch_remote()
        stderr = io.StringIO()
        with _isolated_locks(), \
                ps_patch, win_patch, \
                mock.patch.object(audio, "resolve_target", return_value="future-target"), \
                mock.patch("sys.stderr", new=stderr), \
                self._patch_local() as local:
            play_mp3_file(self.mp3_path)
        self.assertEqual(FakeRemoteSession.instances, [])
        self.assertIn("Playback target 'future-target' unavailable", stderr.getvalue())
        local.assert_called_once()

    def test_invalid_target_raises_invalid_playback_target_type(self):
        """Documents the exception the fail-open path swallows."""
        with _env(AGENT_TTS_PLAYBACK="bogus-target"):
            with self.assertRaises(InvalidPlaybackTarget):
                audio.resolve_target(None, os.environ)


if __name__ == "__main__":
    unittest.main()
