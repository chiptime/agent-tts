"""Daemon heartbeat as an ntfy dead man's switch (HT-18 / S2).

The heartbeat keeps a scheduled ntfy message permanently in the future: every
interval the daemon re-publishes the same sequence ID with ``In: <window>``,
so the server keeps moving the delivery forward. Silence means the daemon (or
the host) is gone, and the last scheduled message is delivered by itself.

Everything here is hermetic: the HTTP publish is injected or urllib is
monkeypatched, no socket is opened, and the loop is stopped before the test
ends. Configuration is read from an explicit env mapping where possible so the
tests never depend on the caller's environment.
"""

from __future__ import annotations

import time

import pytest

import agent_tts.heartbeat as heartbeat_mod
from agent_tts.daemon import Daemon
from agent_tts.heartbeat import (
    Heartbeat,
    PublishRejected,
    PublishUnavailable,
    heartbeat_seq_id,
    load_heartbeat_settings,
    publish_heartbeat,
    sanitize_hostname,
)

HEARTBEAT_ENV = (
    "TTS_HEARTBEAT",
    "TTS_HEARTBEAT_INTERVAL_S",
    "TTS_HEARTBEAT_WINDOW_S",
    "TTS_HEARTBEAT_PRIORITY",
    "TTS_HEARTBEAT_SERVER",
    "TTS_HEARTBEAT_TOPIC",
)


@pytest.fixture(autouse=True)
def clean_heartbeat_env(monkeypatch):
    """No test inherits a heartbeat configuration from the developer's shell."""
    for name in HEARTBEAT_ENV:
        monkeypatch.delenv(name, raising=False)


def _enabled_settings(**overrides):
    env = {
        "TTS_HEARTBEAT": "on",
        "TTS_HEARTBEAT_SERVER": "https://ntfy.example",
        "TTS_HEARTBEAT_TOPIC": "alerts",
        "TTS_HEARTBEAT_INTERVAL_S": "60",
        "TTS_HEARTBEAT_WINDOW_S": "300",
        "TTS_HEARTBEAT_PRIORITY": "high",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    hostname = env.pop("hostname", "build-box")
    return load_heartbeat_settings(env, hostname=hostname)


def _config(interval_s: float = 60.0, window_s: float = 300.0):
    settings = _enabled_settings(
        TTS_HEARTBEAT_INTERVAL_S=str(interval_s),
        TTS_HEARTBEAT_WINDOW_S=str(window_s),
    )
    assert settings.config is not None, settings.error
    return settings.config


def _wait_until(predicate, timeout_sec: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Response:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _Recorder:
    """Stand-in for urllib.request.urlopen that records the Request."""

    def __init__(self, outcomes=()):
        self.requests = []
        self.outcomes = list(outcomes)

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0) if self.outcomes else _Response()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- configuration (RF-2, RF-4) ---------------------------------------------


def test_off_by_default_has_no_config():
    settings = load_heartbeat_settings({})
    assert settings.enabled is False
    assert settings.config is None
    assert settings.error is None

    settings = load_heartbeat_settings({"TTS_HEARTBEAT": "off"})
    assert settings.enabled is False
    assert settings.config is None


def test_on_builds_config_from_defaults():
    settings = _enabled_settings()
    config = settings.config
    assert settings.enabled is True and settings.error is None
    assert config is not None
    assert config.interval_s == 60.0
    assert config.window_s == 300.0
    assert config.priority == "high" and config.priority_level == 4
    assert config.seq_id == "heartbeat-build-box"
    assert config.alert_body == (
        "agent-tts daemon on build-box silent for 300s — daemon down or host unreachable"
    )


def test_window_below_three_intervals_refuses_config():
    settings = _enabled_settings(
        TTS_HEARTBEAT_INTERVAL_S="60", TTS_HEARTBEAT_WINDOW_S="120"
    )
    assert settings.enabled is True
    assert settings.config is None
    assert "TTS_HEARTBEAT_WINDOW_S" in settings.error
    assert "TTS_HEARTBEAT_INTERVAL_S" in settings.error


def test_missing_server_or_topic_refuses_config():
    settings = load_heartbeat_settings({"TTS_HEARTBEAT": "on"})
    assert settings.enabled is True
    assert settings.config is None
    assert "TTS_HEARTBEAT_SERVER" in settings.error
    assert "TTS_HEARTBEAT_TOPIC" in settings.error


def test_invalid_interval_and_priority_refuse_config():
    settings = _enabled_settings(TTS_HEARTBEAT_INTERVAL_S="soon")
    assert settings.config is None and "TTS_HEARTBEAT_INTERVAL_S" in settings.error

    settings = _enabled_settings(TTS_HEARTBEAT_PRIORITY="loud")
    assert settings.config is None and "TTS_HEARTBEAT_PRIORITY" in settings.error


def test_invalid_ipv6_server_is_rejected_without_exception():
    settings = load_heartbeat_settings({
        "TTS_HEARTBEAT": "on",
        "TTS_HEARTBEAT_SERVER": "http://[invalid",
        "TTS_HEARTBEAT_TOPIC": "alerts",
    })
    assert settings.config is None
    assert "TTS_HEARTBEAT_SERVER" in settings.error


def test_urgent_priority_maps_to_five():
    settings = _enabled_settings(TTS_HEARTBEAT_PRIORITY="urgent")
    assert settings.config is not None
    assert settings.config.priority == "urgent"
    assert settings.config.priority_level == 5


# --- sequence identity (RF-1) ------------------------------------------------


def test_sanitize_hostname_keeps_the_sequence_charset():
    assert sanitize_hostname("Mi Host!@#/x_y") == "Mi-Host-x_y"
    assert sanitize_hostname("") == ""
    assert sanitize_hostname("---") == ""


def test_sequence_id_is_prefixed_and_bounded():
    sequence = heartbeat_seq_id("h" * 80)
    assert sequence.startswith("heartbeat-")
    assert len(sequence) == 64
    assert all(
        char in "-_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        for char in sequence
    )
    assert heartbeat_seq_id("") == "heartbeat-unknown"


# --- HTTP publish (RF-1, RNF-3) ----------------------------------------------


def test_publish_sends_window_headers_and_alert_body(monkeypatch):
    config = _config(interval_s=60, window_s=300)
    recorder = _Recorder()
    monkeypatch.setattr(heartbeat_mod.urllib.request, "urlopen", recorder)

    publish_heartbeat(config)

    assert len(recorder.requests) == 1
    request, timeout = recorder.requests[0]
    assert request.full_url == "https://ntfy.example/alerts/heartbeat-build-box"
    assert request.get_method() == "POST"
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["in"] == "5m"
    assert headers["priority"] == "4"
    assert request.data.decode("utf-8") == (
        "agent-tts daemon on build-box silent for 300s — daemon down or host unreachable"
    )
    assert timeout is not None


def test_publish_maps_http_errors_and_network_failures(monkeypatch):
    config = _config()
    recorder = _Recorder(
        outcomes=[heartbeat_mod.urllib.error.HTTPError(
            "https://ntfy.example", 404, "not found", {}, None
        )]
    )
    monkeypatch.setattr(heartbeat_mod.urllib.request, "urlopen", recorder)
    with pytest.raises(PublishRejected) as rejected:
        publish_heartbeat(config)
    assert rejected.value.status == 404

    recorder = _Recorder(outcomes=[heartbeat_mod.urllib.error.URLError("down")])
    monkeypatch.setattr(heartbeat_mod.urllib.request, "urlopen", recorder)
    with pytest.raises(PublishUnavailable):
        publish_heartbeat(config)


# --- loop behavior (RF-1, RF-3, RF-5) ----------------------------------------


def test_loop_publishes_first_beat_immediately():
    calls = []
    heartbeat = Heartbeat(_config(interval_s=60, window_s=300), publish=calls.append)
    try:
        heartbeat.start()
        assert _wait_until(lambda: len(calls) == 1)
        assert heartbeat.state == "on"
    finally:
        assert heartbeat.stop(timeout=1.0)
    assert not heartbeat.thread.is_alive()
    assert heartbeat.state == "stopped"


def test_capability_gate_stops_on_server_rejection():
    calls = []

    def reject(config):
        calls.append(config)
        raise PublishRejected(404)

    heartbeat = Heartbeat(_config(), publish=reject)
    heartbeat.start()
    heartbeat.thread.join(timeout=2.0)
    assert not heartbeat.thread.is_alive()
    assert heartbeat.state == "unsupported"
    assert len(calls) == 1  # never enters the repeating loop
    assert heartbeat.status_fields() == "heartbeat=unsupported"


def test_gate_rejects_permanent_error_after_transient_failure():
    calls = []

    def publish(config):
        calls.append(config)
        if len(calls) == 1:
            raise PublishUnavailable("offline")
        raise PublishRejected(404)

    heartbeat = Heartbeat(_config(interval_s=0.02, window_s=0.06), publish=publish)
    try:
        heartbeat.start()
        assert _wait_until(lambda: heartbeat.state == "unsupported")
        heartbeat.thread.join(timeout=1)
        assert not heartbeat.thread.is_alive()
        assert len(calls) == 2
    finally:
        heartbeat.stop(timeout=1)


def test_publish_failure_never_kills_the_loop():
    calls = []

    def flaky(config):
        calls.append(config)
        if len(calls) == 1:
            raise PublishUnavailable("network down")

    heartbeat = Heartbeat(
        _config(interval_s=0.02, window_s=0.06), publish=flaky
    )
    try:
        heartbeat.start()
        assert _wait_until(lambda: len(calls) >= 3)
        assert heartbeat.thread.is_alive()
        assert heartbeat.state == "on"
    finally:
        assert heartbeat.stop(timeout=1.0)
    assert not heartbeat.thread.is_alive()


def test_stop_event_joins_thread_promptly():
    heartbeat = Heartbeat(_config(interval_s=60, window_s=300), publish=lambda cfg: None)
    heartbeat.start()
    assert _wait_until(lambda: heartbeat.state == "on")
    started = time.monotonic()
    assert heartbeat.stop(timeout=1.0)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert not heartbeat.thread.is_alive()
    assert heartbeat.thread.daemon is True  # never blocks process exit


def test_status_fields_report_interval_window_and_next_beat():
    heartbeat = Heartbeat(_config(interval_s=60, window_s=300), publish=lambda cfg: None)
    try:
        heartbeat.start()
        assert _wait_until(lambda: heartbeat.state == "on")
        fields = heartbeat.status_fields()
        assert fields.startswith("heartbeat=on ")
        assert "interval=60s" in fields
        assert "window=300s" in fields
        next_beat = next(t for t in fields.split() if t.startswith("next_beat="))
        assert next_beat.endswith("s")
        assert 0 <= int(next_beat[len("next_beat="):-1]) <= 60
    finally:
        heartbeat.stop(timeout=1.0)


# --- daemon integration (RF-4, RF-6) -----------------------------------------


def test_daemon_off_leaves_no_heartbeat_fields_or_thread():
    daemon = Daemon(socket_path="unused.sock")
    daemon._start_heartbeat_if_enabled()
    try:
        assert daemon.heartbeat is None
        reply = daemon.handle_command("status")
        assert reply.startswith("status=idle ")
        assert "heartbeat=" not in reply
    finally:
        daemon._stop_heartbeat()


def test_daemon_arms_and_status_shows_heartbeat(monkeypatch):
    monkeypatch.setenv("TTS_HEARTBEAT", "on")
    monkeypatch.setenv("TTS_HEARTBEAT_SERVER", "https://ntfy.example")
    monkeypatch.setenv("TTS_HEARTBEAT_TOPIC", "alerts")
    monkeypatch.setenv("TTS_HEARTBEAT_INTERVAL_S", "60")
    monkeypatch.setenv("TTS_HEARTBEAT_WINDOW_S", "300")
    calls = []
    monkeypatch.setattr(
        heartbeat_mod, "publish_heartbeat", lambda config, timeout=None: calls.append(config)
    )

    daemon = Daemon(socket_path="unused.sock")
    daemon._start_heartbeat_if_enabled()
    try:
        assert daemon.heartbeat is not None
        assert _wait_until(lambda: daemon.heartbeat.state == "on")
        reply = daemon.handle_command("status")
        assert "heartbeat=on" in reply
        assert "interval=60s" in reply
        assert "window=300s" in reply
        assert "next_beat=" in reply
    finally:
        daemon._stop_heartbeat()
    assert daemon.heartbeat.thread is not None
    assert not daemon.heartbeat.thread.is_alive()


def test_daemon_invalid_config_logs_and_reports_not_armed(monkeypatch, capsys):
    monkeypatch.setenv("TTS_HEARTBEAT", "on")
    monkeypatch.setenv("TTS_HEARTBEAT_SERVER", "https://ntfy.example")
    monkeypatch.setenv("TTS_HEARTBEAT_TOPIC", "alerts")
    monkeypatch.setenv("TTS_HEARTBEAT_INTERVAL_S", "60")
    monkeypatch.setenv("TTS_HEARTBEAT_WINDOW_S", "120")

    daemon = Daemon(socket_path="unused.sock")
    daemon._start_heartbeat_if_enabled()
    try:
        assert daemon.heartbeat is None
        stderr = capsys.readouterr().err
        assert "heartbeat not armed" in stderr
        assert "TTS_HEARTBEAT_WINDOW_S" in stderr
        assert "heartbeat=not_armed" in daemon.handle_command("status")
    finally:
        daemon._stop_heartbeat()


class _FakeSession:
    state = {"comm_active": None}

    def handle_ipc_command(self, command: str) -> str:
        return "status=playing text=hola"


def test_status_during_playback_includes_heartbeat_fields(monkeypatch):
    monkeypatch.setenv("TTS_HEARTBEAT", "on")
    monkeypatch.setenv("TTS_HEARTBEAT_SERVER", "https://ntfy.example")
    monkeypatch.setenv("TTS_HEARTBEAT_TOPIC", "alerts")
    monkeypatch.setattr(heartbeat_mod, "publish_heartbeat", lambda config, timeout=None: None)

    daemon = Daemon(socket_path="unused.sock")
    daemon._start_heartbeat_if_enabled()
    try:
        assert daemon.heartbeat is not None
        assert _wait_until(lambda: daemon.heartbeat.state == "on")
        daemon.active_session = _FakeSession()
        reply = daemon.handle_command("status")
        head, tail = reply.split(" text=", 1)
        assert "heartbeat=on" in head
        assert tail == "hola"
    finally:
        daemon._stop_heartbeat()
