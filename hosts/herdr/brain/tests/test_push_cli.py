"""The ``herdr-brain push`` terminal: import, list, test (S5).

Exit contract (mirrors the msg CLI):

- 0 — the verb did its job
- 1 — failure (unreadable file, bad keys, corrupt store, push off)
- 2 — usage error

``import`` prints ONLY the public key: the private half never crosses a
terminal transcript. All storage decisions live in the webpush package;
this module is words and exits.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from herdr_brain import push_cli
from herdr_brain.webpush.vapid import (
    VapidKeys,
    generate_keys,
    load_keys,
    save_keys,
)


class FakeTransport:
    def __init__(self, status=201):
        self.status = status
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return type("Response", (), {"status": self.status})()


def run(argv, state_dir, transport=None, env=None):
    out, err = io.StringIO(), io.StringIO()
    code = push_cli.main(
        argv,
        env=env or {},
        state_dir=state_dir,
        transport=transport,
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def write_dotenv(path: Path, prefix="OLDPANEL") -> None:
    keys = generate_keys()
    from herdr_brain.webpush import crypto

    path.write_text(
        f"# an operator's existing export\n"
        f"export {prefix}_VAPID_PUBLIC={crypto.b64url_encode(keys.public)}\n"
        f"{prefix}_VAPID_PRIVATE={crypto.b64url_encode(keys.private.to_bytes(32, 'big'))}\n"
        f"{prefix}_VAPID_SUBJECT=\"mailto:operator@example.invalid\"\n",
        encoding="utf-8",
    )


class TestUsage:
    def test_no_verb_is_a_usage_error(self, tmp_path):
        code, _, err = run([], tmp_path)
        assert code == 2
        assert "usage" in err.lower()

    def test_unknown_verb_is_a_usage_error(self, tmp_path):
        code, _, err = run(["explode"], tmp_path)
        assert code == 2


class TestImport:
    def test_import_persists_the_pair_and_prints_only_the_public_key(
        self, tmp_path
    ):
        dotenv = tmp_path / "keys.env"
        write_dotenv(dotenv)
        state = tmp_path / "state"

        code, out, err = run(["import", str(dotenv)], state)

        assert code == 0, err
        loaded = load_keys(state)
        assert isinstance(loaded, VapidKeys)
        # ONLY the public key on stdout — the private half never lands
        # in a terminal transcript.
        assert out.strip() == loaded.public_b64url + "\n".strip() or (
            out.strip() == loaded.public_b64url
        )
        assert loaded.subject == "mailto:operator@example.invalid"

    def test_import_overrides_an_existing_pair(self, tmp_path):
        dotenv = tmp_path / "keys.env"
        write_dotenv(dotenv)
        state = tmp_path / "state"
        save_keys(state, generate_keys())

        code, out, _ = run(["import", str(dotenv)], state)
        assert code == 0
        loaded = load_keys(state)
        assert out.strip() == loaded.public_b64url

    def test_import_of_a_file_without_a_pair_fails_loudly(self, tmp_path):
        dotenv = tmp_path / "empty.env"
        dotenv.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
        code, _, err = run(["import", str(dotenv)], tmp_path / "state")
        assert code == 1
        assert "VAPID" in err

    def test_import_of_a_missing_file_fails_loudly(self, tmp_path):
        code, _, err = run(["import", str(tmp_path / "nope.env")], tmp_path / "state")
        assert code == 1

    def test_import_without_a_path_is_usage(self, tmp_path):
        assert run(["import"], tmp_path)[0] == 2


class TestList:
    def test_list_prints_each_endpoint_oldest_first(self, tmp_path):
        state = tmp_path / "state"
        from herdr_brain.webpush.service import WebPushService

        service = WebPushService(state)
        for n in ("first", "second"):
            service.subscribe(
                {
                    "endpoint": f"https://push.example.invalid/{n}",
                    "keys": {"p256dh": "A" * 43, "auth": "B" * 22},
                    "user_agent": "test",
                },
                device="phone",
            )

        code, out, err = run(["list"], state)
        assert code == 0, err
        assert out.split() == [
            "https://push.example.invalid/first",
            "https://push.example.invalid/second",
        ]

    def test_list_with_no_subscriptions_says_so(self, tmp_path):
        state = tmp_path / "state"
        save_keys(state, generate_keys())
        code, out, _ = run(["list"], state)
        assert code == 0
        assert "no hay suscripciones" in out

    def test_list_on_a_corrupt_store_fails_loudly_and_resets_nothing(
        self, tmp_path
    ):
        state = tmp_path / "state"
        state.mkdir(parents=True)
        corrupt = b"not json at all"
        (state / "push-subscriptions.json").write_bytes(corrupt)

        code, _, err = run(["list"], state)
        assert code == 1
        assert (state / "push-subscriptions.json").read_bytes() == corrupt


class TestTest:
    def test_test_sends_to_every_subscription(self, tmp_path):
        state = tmp_path / "state"
        keys = generate_keys()
        save_keys(state, keys)
        # A REAL P-256 point: the sender validates before encrypting.
        from herdr_brain.webpush import crypto
        import os

        service_transport = None  # subscriptions are written directly
        from herdr_brain.webpush.service import WebPushService

        seed = WebPushService(state)
        seed.subscribe(
            {
                "endpoint": "https://push.example.invalid/one",
                "keys": {
                    "p256dh": crypto.b64url_encode(keys.public),
                    "auth": crypto.b64url_encode(os.urandom(16)),
                },
            },
            device="phone",
        )

        transport = FakeTransport()
        code, out, err = run(["test"], state, transport=transport)
        assert code == 0, err
        assert len(transport.requests) == 1
        assert transport.requests[0]["url"] == "https://push.example.invalid/one"
        assert "1" in out  # the sent count is printed

    def test_test_without_keys_says_push_is_off(self, tmp_path):
        state = tmp_path / "state"
        transport = FakeTransport()
        code, out, err = run(["test"], state, transport=transport)
        assert code == 1
        assert transport.requests == []
        assert "VAPID" in err or "apagado" in err
