"""HerdrClient key transport: the dialect, and the feared failure modes.

`herdr agent send-keys` validates every key BEFORE writing bytes, so an
unknown name is refused with no side effect. The brain validates the same
dialect locally too: a doomed keystroke must never reach a live terminal,
and the CLI must not be spawned for a name we already know it rejects.
"""

from __future__ import annotations

import subprocess

import pytest

from herdr_brain.herdr import HerdrClient, HerdrError, is_valid_key


class FakeRunner:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        result = self.results.pop(0) if self.results else (0, "", "")
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(cmd, result[0], stdout=result[1], stderr=result[2])


def make_client(runner):
    from tests.test_herdr import make_settings

    return HerdrClient(make_settings(), runner=runner)


# -- the dialect ------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["enter", "esc", "left", "right", "up", "down", "ctrl+c", "ctrl+d"]
)
def test_accepts_the_dialect_the_dialogs_need(key):
    assert is_valid_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "arrowleft",      # probed live: herdr answers invalid_key
        "ArrowLeft",
        "leftarrow",
        "control+c",      # the modifier is spelled ctrl
        "ctrl+",          # a modifier with no key
        "",               # nothing is not a key
        "left right",     # two keys are two arguments, never one string
        "f13",            # outside the verified set
    ],
)
def test_refuses_what_is_not_in_the_dialect(key):
    assert is_valid_key(key) is False


# -- send_keys --------------------------------------------------------------


def test_send_keys_builds_the_verified_command():
    runner = FakeRunner(results=[(0, '{"id":"cli:agent:send-keys","result":{"type":"ok"}}', "")])
    client = make_client(runner)

    assert client.send_keys("w1:p2", ["left", "enter"]) is None
    assert runner.calls[0]["cmd"] == ["herdr-fake", "agent", "send-keys", "w1:p2", "left", "enter"]


def test_send_keys_refuses_an_empty_list_without_spawning():
    runner = FakeRunner()
    client = make_client(runner)

    with pytest.raises(HerdrError, match="empty"):
        client.send_keys("w1:p2", [])

    assert runner.calls == []


def test_send_keys_refuses_an_unknown_key_without_spawning():
    runner = FakeRunner()
    client = make_client(runner)

    with pytest.raises(HerdrError, match="arrowleft"):
        client.send_keys("w1:p2", ["arrowleft"])

    assert runner.calls == []


def test_a_refused_key_from_the_cli_surfaces_as_an_error():
    runner = FakeRunner(
        results=[
            (
                1,
                "",
                '{"error":{"code":"invalid_key","message":"unsupported key f13"},'
                '"id":"cli:agent:send-keys"}',
            )
        ]
    )
    client = make_client(runner)

    with pytest.raises(HerdrError, match="invalid_key|unsupported key"):
        client.send_keys("w1:p2", ["enter"])


def test_a_transport_failure_is_not_swallowed():
    runner = FakeRunner(results=[HerdrError("socket gone")])
    client = make_client(runner)

    with pytest.raises(HerdrError, match="socket gone"):
        client.send_keys("w1:p2", ["enter"])


# -- the styled read the signature needs ------------------------------------


def test_ansi_read_asks_for_the_styled_format():
    """The highlight lives in the escape codes: a text read cannot say which
    option is selected, and the guard's signature depends on it."""
    runner = FakeRunner(results=[(0, "\x1b[7mAllow once\x1b[0m", "")])
    client = make_client(runner)

    assert client.read_screen("w1:p2", 20, ansi=True).startswith("\x1b[")
    assert runner.calls[0]["cmd"][-3:] == ["--lines", "20", "--ansi"]
    assert "--format" not in runner.calls[0]["cmd"]


def test_the_plain_read_stays_byte_identical():
    runner = FakeRunner(results=[(0, "plain", "")])
    client = make_client(runner)

    client.read_screen("w1:p2", 20)

    assert runner.calls[0]["cmd"][-4:] == [
        "--lines", "20", "--format", "text",
    ]


def test_the_ansi_read_keeps_the_screen_line_cap():
    runner = FakeRunner(results=[(0, "", "")])
    client = make_client(runner)

    client.read_screen("w1:p2", 500, ansi=True)

    assert runner.calls[0]["cmd"][runner.calls[0]["cmd"].index("--lines") + 1] == "60"
