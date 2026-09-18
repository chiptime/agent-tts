"""constants.py tempdir-based defaults and env-override semantics."""

import importlib
import os

import pytest

from agent_tts import constants

ENV_KEYS = ("AGENT_TTS_LOCK_FILE", "AGENT_TTS_PID_FILE", "AGENT_TTS_SOCKET")


@pytest.fixture
def restore_constants(monkeypatch):
    yield
    # Undo monkeypatched state before restoring the module to its real defaults.
    monkeypatch.undo()
    importlib.reload(constants)


def _clear_env():
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    return saved


def _restore_env(saved):
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_defaults_follow_gettempdir(monkeypatch, restore_constants):
    saved = _clear_env()
    try:
        monkeypatch.setattr(constants.tempfile, "gettempdir", lambda: "/mytmp")
        importlib.reload(constants)
        assert constants.LOCK_FILE == os.path.join("/mytmp", "agent-tts-playing.lock")
        assert constants.PID_FILE == os.path.join("/mytmp", "agent-tts-current.pid")
        assert constants.IPC_SOCKET == os.path.join("/mytmp", "agent-tts-player.sock")
        assert constants.IPC_PORT_FILE == os.path.join("/mytmp", "agent-tts-ipc.port")
    finally:
        _restore_env(saved)


def test_env_override_still_wins(monkeypatch, restore_constants):
    saved = _clear_env()
    try:
        monkeypatch.setattr(constants.tempfile, "gettempdir", lambda: "/mytmp")
        os.environ["AGENT_TTS_LOCK_FILE"] = "/custom/lock"
        os.environ["AGENT_TTS_SOCKET"] = "/custom/player.sock"
        importlib.reload(constants)
        assert constants.LOCK_FILE == "/custom/lock"
        assert constants.PID_FILE == os.path.join("/mytmp", "agent-tts-current.pid")
        assert constants.IPC_SOCKET == "/custom/player.sock"
    finally:
        _restore_env(saved)
