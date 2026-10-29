"""VAPID key lifecycle and the ES256 JWT (S5, risk item 1).

Covers: generation, persistence at 0600 under a 0700 state dir, env
override, load order, the operator's dotenv-style import (prefix
agnostic — the file names its own product, never ours to name), and the
signed Authorization JWT whose claims and signature a service can check
back against the public key.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from herdr_brain.webpush import crypto, vapid


PRIV = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721


def _encode_pair(priv: int):
    pub = crypto.public_from_private(priv)
    return crypto.b64url_encode(pub), crypto.b64url_encode(priv.to_bytes(32, "big"))


# ── generation and persistence ───────────────────────────────────────────────


def test_generate_keys_makes_a_valid_pair(tmp_path):
    keys = vapid.generate_keys(subject="mailto:ops@example.net")
    sig = crypto.ecdsa_sign_p256_sha256(keys.private, b"x")
    assert crypto.ecdsa_verify(keys.public, b"x", sig)
    assert keys.subject == "mailto:ops@example.net"


def test_save_keys_writes_private_file_atomically(tmp_path):
    keys = vapid.generate_keys(subject="mailto:ops@example.net")
    path = vapid.save_keys(tmp_path, keys)
    assert path == tmp_path / "vapid.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert not list(tmp_path.glob("*.tmp"))
    raw = json.loads(path.read_text())
    assert set(raw) == {"public", "private", "subject"}


def test_load_order_env_beats_file(tmp_path):
    keys = vapid.generate_keys(subject="mailto:file@example.net")
    vapid.save_keys(tmp_path, keys)
    env_pub, env_priv = _encode_pair(PRIV)
    loaded = vapid.load_keys(
        tmp_path,
        environ={
            "HERDR_BRAIN_VAPID_PUBLIC": env_pub,
            "HERDR_BRAIN_VAPID_PRIVATE": env_priv,
            "HERDR_BRAIN_VAPID_SUBJECT": "mailto:env@example.net",
        },
    )
    assert loaded.private == PRIV
    assert loaded.subject == "mailto:env@example.net"


def test_load_keys_from_file(tmp_path):
    keys = vapid.generate_keys(subject="mailto:file@example.net")
    vapid.save_keys(tmp_path, keys)
    loaded = vapid.load_keys(tmp_path, environ={})
    assert (loaded.public, loaded.private, loaded.subject) == (
        keys.public,
        keys.private,
        keys.subject,
    )


def test_load_keys_without_any_source_returns_none(tmp_path):
    assert vapid.load_keys(tmp_path, environ={}) is None


def test_load_keys_rejects_partial_env(tmp_path):
    env_pub, _ = _encode_pair(PRIV)
    with pytest.raises(vapid.VapidError):
        vapid.load_keys(
            tmp_path,
            environ={
                "HERDR_BRAIN_VAPID_PUBLIC": env_pub,
                "HERDR_BRAIN_VAPID_SUBJECT": "mailto:x@example.net",
            },
        )


# ── dotenv-style import for the operator's existing pair ─────────────────────


def test_import_keys_from_env_file_accepts_any_prefix(tmp_path):
    env_file = tmp_path / "panel.env"
    env_pub, env_priv = _encode_pair(PRIV)
    env_file.write_text(
        "# exported by the old panel\n"
        f"OLDPANEL_VAPID_PUBLIC={env_pub}\n"
        f'OLDPANEL_VAPID_PRIVATE="{env_priv}"\n'
        "OLDPANEL_VAPID_SUBJECT=mailto:ops@oldpanel.example\n"
        "UNRELATED=sk-ant-not-a-real-key\n",
        encoding="utf-8",
    )
    keys = vapid.import_keys_from_env_file(env_file)
    assert keys.private == PRIV
    assert keys.public == crypto.public_from_private(PRIV)
    assert keys.subject == "mailto:ops@oldpanel.example"


def test_import_keys_from_env_file_fixture():
    keys = vapid.import_keys_from_env_file(
        os.path.join(os.path.dirname(__file__), "fixtures", "webpush", "legacy-panel.env")
    )
    assert keys.private == PRIV
    assert keys.subject == "mailto:ops@legacy.example"


def test_import_keys_from_env_file_rejects_half_a_pair(tmp_path):
    env_pub, _ = _encode_pair(PRIV)
    env_file = tmp_path / "panel.env"
    env_file.write_text(f"OLDPANEL_VAPID_PUBLIC={env_pub}\n", encoding="utf-8")
    with pytest.raises(vapid.VapidError):
        vapid.import_keys_from_env_file(env_file)


# ── the VAPID JWT ────────────────────────────────────────────────────────────


def test_vapid_jwt_structure_claims_and_signature():
    keys = vapid.generate_keys(subject="mailto:ops@example.net")
    now = 1_800_000_000.0
    token = vapid.build_vapid_jwt(keys, audience="https://push.example.net", now=now)
    header_b64, claims_b64, sig_b64 = token.split(".")
    header = json.loads(crypto.b64url_decode(header_b64))
    claims = json.loads(crypto.b64url_decode(claims_b64))
    assert header == {"typ": "JWT", "alg": "ES256"}
    assert claims["aud"] == "https://push.example.net"
    assert claims["sub"] == "mailto:ops@example.net"
    assert claims["exp"] == int(now) + 12 * 3600
    sig = crypto.b64url_decode(sig_b64)
    assert len(sig) == 64  # raw r||s, the shape RFC 8292 requires
    signing_input = f"{header_b64}.{claims_b64}".encode()
    assert crypto.ecdsa_verify(keys.public, signing_input, sig)


def test_vapid_jwt_rejects_bad_subject():
    keys = vapid.generate_keys(subject="mailto:ops@example.net")
    keys = vapid.VapidKeys(
        public=keys.public, private=keys.private, subject="ops@example.net"
    )
    with pytest.raises(vapid.VapidError):
        vapid.build_vapid_jwt(keys, audience="https://push.example.net", now=0.0)


# ── load_or_create_keys: the brain owns its own pair ─────────────────────────


def test_first_start_creates_a_private_key_file_once(tmp_path):
    import stat

    created = vapid.load_or_create_keys(tmp_path, environ={})
    path = tmp_path / vapid.VAPID_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = vapid.load_or_create_keys(tmp_path, environ={})
    assert again.public == created.public
    assert vapid.load_keys(tmp_path, environ={}).public == created.public


def test_configured_environment_wins_and_writes_nothing(tmp_path):
    keys = vapid.generate_keys("mailto:ops@example.com")
    env = {
        vapid.ENV_PUBLIC: keys.public_b64url,
        vapid.ENV_PRIVATE: crypto.b64url_encode(keys.private.to_bytes(32, "big")),
        vapid.ENV_SUBJECT: keys.subject,
    }
    loaded = vapid.load_or_create_keys(tmp_path, environ=env)
    assert loaded.public == keys.public
    assert not (tmp_path / vapid.VAPID_FILENAME).exists()


def test_a_broken_key_file_is_never_replaced(tmp_path):
    path = tmp_path / vapid.VAPID_FILENAME
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(vapid.VapidError):
        vapid.load_or_create_keys(tmp_path, environ={})
    assert path.read_text(encoding="utf-8") == "{not json"


def test_concurrent_first_starts_agree_on_one_pair(tmp_path):
    import threading

    results, errors = [], []

    def start():
        try:
            results.append(vapid.load_or_create_keys(tmp_path, environ={}).public)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(set(results)) == 1
    assert vapid.load_keys(tmp_path, environ={}).public == results[0]


def test_separate_processes_create_one_pair_without_overwriting(tmp_path):
    import subprocess
    import sys
    import time

    state = tmp_path / "state"
    gate = tmp_path / "start"
    ready = [tmp_path / f"ready-{i}" for i in range(8)]
    script = """
from pathlib import Path
import sys
import time
from herdr_brain.webpush.vapid import load_or_create_keys

state, ready, gate = map(Path, sys.argv[1:])
ready.touch()
deadline = time.monotonic() + 20
while not gate.exists():
    if time.monotonic() > deadline:
        raise RuntimeError("start barrier timed out")
    time.sleep(0.005)
print(load_or_create_keys(state, environ={}).public_b64url)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(state), str(marker), str(gate)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for marker in ready
    ]
    try:
        deadline = time.monotonic() + 20
        while not all(marker.exists() for marker in ready):
            assert time.monotonic() < deadline, "workers did not reach the start barrier"
            time.sleep(0.005)
        gate.touch()
        public_keys = []
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
            public_keys.append(out.strip())
        assert len(set(public_keys)) == 1
        assert vapid.load_keys(state, environ={}).public_b64url == public_keys[0]
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        assert stat.S_IMODE((state / vapid.VAPID_FILENAME).stat().st_mode) == 0o600
        assert sorted(p.name for p in state.iterdir()) == [vapid.VAPID_FILENAME]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
