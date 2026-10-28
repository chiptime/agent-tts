"""Device pairing state module (Panel parity F1, doc 01 §1).

Pure decisions and the on-disk store under HERDR_BRAIN_STATE_DIR:
hash-only persistence, TTL/attempt-bounded codes, defensive coercion,
atomic owner-only writes, mtime-based re-read so a CLI revocation in
another process applies without a restart.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from herdr_brain import pairing


class FakeClock:
    def __init__(self, start=1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_code_generation_uses_the_confusable_free_alphabet():
    for _ in range(20):
        code = pairing.generate_code()
        assert len(code) == pairing.CODE_LENGTH == 8
        assert all(ch in pairing.CODE_ALPHABET for ch in code)


def test_normalize_code_uppercases_and_strips_out_of_alphabet_characters():
    assert pairing.normalize_code(" ab2-cd3 ") == "AB2CD3"
    assert pairing.normalize_code("o0i1l") == ""  # confusables are not mapped


def test_new_pending_stores_only_the_hash_with_ttl_and_attempts():
    clock = FakeClock(1_000)
    pending = pairing.new_pending("AB23CD45", clock(), ttl_s=pairing.CODE_TTL_S)
    assert pending == {
        "codeHash": pairing.sha256_hex("AB23CD45"),
        "expiresAt": 1_600.0,
        "attemptsLeft": pairing.CODE_ATTEMPTS,
    }
    assert pairing.CODE_TTL_S == 600  # 10 minutes, doc 01
    assert pairing.CODE_ATTEMPTS == 5


def test_hashes_equal_is_constant_time_safe_on_length_mismatch():
    assert pairing.hashes_equal("a" * 64, "a" * 64)
    assert not pairing.hashes_equal("a" * 64, "b" * 64)
    # A corrupted short hash degrades to False, never a raise.
    assert not pairing.hashes_equal("a" * 63, "a" * 64)


def test_normalize_label_bounds_and_flattens():
    assert pairing.normalize_label("  mi\nmóvil\t") == "mi móvil"
    assert pairing.normalize_label("") is None
    assert pairing.normalize_label("x" * 49) is None
    assert pairing.normalize_label(None) is None
    assert pairing.normalize_label(7) is None


def test_generate_token_is_256_bit_base64url():
    token = pairing.generate_token()
    import base64

    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(raw) == 32


# ── check_claim: TTL and the 5-attempt budget (doc 01, AC4) ─────────────────


def _pending(clock):
    return pairing.new_pending("AB23CD45", clock())


def test_check_claim_accepts_the_code_within_ttl():
    clock = FakeClock()
    verdict = pairing.check_claim(_pending(clock), "ab23cd45", clock())
    assert verdict[0] == "ok"


def test_check_claim_expires_after_ten_minutes():
    clock = FakeClock()
    pending = _pending(clock)
    clock.advance(601)
    reason, to_persist = pairing.check_claim(pending, "AB23CD45", clock())
    assert reason == "expired"
    assert to_persist is None  # destroyed, not left guessable


def test_check_claim_destroys_the_pending_after_five_wrong_codes():
    clock = FakeClock()
    pending = _pending(clock)
    for i in range(4):
        reason, pending = pairing.check_claim(pending, "WRONGWRONG", clock())
        assert reason == "bad-code"
        assert pending["attemptsLeft"] == 4 - i
    reason, to_persist = pairing.check_claim(pending, "WRONGWRONG", clock())
    assert reason == "exhausted"
    assert to_persist is None


def test_check_claim_without_pending_is_no_pending():
    assert pairing.check_claim(None, "AB23CD45", 0) == ("no-pending", None)


# ── registry coercion: corruption never authorizes (doc 01, AC10) ───────────


def test_coerce_registry_drops_incomplete_or_corrupt_entries():
    raw = {
        "devices": [
            {"label": "phone", "tokenHash": pairing.sha256_hex("t1"),
             "createdAt": 1, "lastSeenAt": 2},
            {"label": "", "tokenHash": "x" * 64},            # blank label
            {"label": "no-hash", "tokenHash": ""},           # empty hash
            {"label": "short-hash", "tokenHash": "abc"},     # not a digest
            {"tokenHash": "y" * 64},                         # no label
            {"label": "dupe", "tokenHash": pairing.sha256_hex("t2")},
            {"label": "dupe", "tokenHash": pairing.sha256_hex("t3")},
            "not-an-object",
        ]
    }
    registry = pairing.coerce_registry(raw)
    labels = [d["label"] for d in registry["devices"]]
    assert labels == ["phone", "dupe"]


def test_coerce_registry_tolerates_garbage_shapes():
    assert pairing.coerce_registry(None) == {"devices": []}
    assert pairing.coerce_registry("junk") == {"devices": []}
    assert pairing.coerce_registry([1, 2]) == {"devices": []}
    assert pairing.coerce_registry({"devices": "junk"}) == {"devices": []}
    assert pairing.coerce_registry({}) == {"devices": []}


def test_coerce_pending_rejects_incomplete_records():
    good = pairing.new_pending("AB23CD45", 0)
    assert pairing.coerce_pending(good) is not None
    assert pairing.coerce_pending({"codeHash": "", "expiresAt": 1, "attemptsLeft": 1}) is None
    assert pairing.coerce_pending({"codeHash": "z" * 64, "expiresAt": "soon", "attemptsLeft": 1}) is None
    assert pairing.coerce_pending(None) is None
    assert pairing.coerce_pending("junk") is None


# ── token lookup / registry edits ────────────────────────────────────────────


def test_find_by_token_matches_the_hashed_bearer():
    registry = pairing.coerce_registry({"devices": [
        {"label": "phone", "tokenHash": pairing.sha256_hex("secret-1"),
         "createdAt": 1, "lastSeenAt": 1},
    ]})
    assert pairing.find_by_token(registry, "secret-1")["label"] == "phone"
    assert pairing.find_by_token(registry, "secret-2") is None
    assert pairing.find_by_token(registry, None) is None
    assert pairing.find_by_token(registry, "") is None


def test_add_and_remove_device_roundtrip():
    registry = pairing.coerce_registry(None)
    registry = pairing.add_device(registry, {"label": "a", "tokenHash": "h" * 64, "now": 5})
    assert registry["devices"][0] == {
        "label": "a", "tokenHash": "h" * 64, "createdAt": 5, "lastSeenAt": 5,
    }
    # Labels are the revoke handle: duplicates are refused.
    assert pairing.add_device(registry, {"label": "a", "tokenHash": "i" * 64, "now": 6}) is None
    assert pairing.remove_device(registry, "a") == {"devices": []}
    assert pairing.remove_device(registry, "ghost") is None


def test_touch_device_throttles_last_seen_stamps():
    registry = pairing.coerce_registry({"devices": [
        {"label": "a", "tokenHash": "h" * 64, "createdAt": 0, "lastSeenAt": 100},
    ]})
    assert pairing.touch_device(registry, "a", 130) is None  # under the throttle
    bumped = pairing.touch_device(registry, "a", 160 + pairing.SEEN_THROTTLE_S)
    assert bumped["devices"][0]["lastSeenAt"] == 160 + pairing.SEEN_THROTTLE_S
    assert pairing.touch_device(registry, "ghost", 999) is None


def test_to_device_wire_never_leaks_token_hashes():
    registry = pairing.coerce_registry({"devices": [
        {"label": "a", "tokenHash": "h" * 64, "createdAt": 1, "lastSeenAt": 2},
        {"label": "b", "tokenHash": "i" * 64, "createdAt": 3, "lastSeenAt": 4},
    ]})
    wire = pairing.to_device_wire(registry, current="b")
    assert wire == [
        {"label": "a", "createdAt": 1, "lastSeenAt": 2, "current": False},
        {"label": "b", "createdAt": 3, "lastSeenAt": 4, "current": True},
    ]
    assert "tokenHash" not in json.dumps(wire)


def test_bearer_token_parses_the_authorization_header():
    get = {"authorization": "Bearer abc"}.get
    assert pairing.bearer_token({"authorization": "Bearer abc"}.get) == "abc"
    assert pairing.bearer_token({"authorization": "bearer   abc  "}.get) == "abc"
    assert pairing.bearer_token({"authorization": "Basic zzz"}.get) is None
    assert pairing.bearer_token({}.get) is None


# ── the file store ───────────────────────────────────────────────────────────


def test_mint_writes_only_the_hash_with_owner_modes(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path, now=clock)
    minted = store.mint()
    pending_path = tmp_path / pairing.PENDING_FILENAME
    raw = pending_path.read_text(encoding="utf-8")
    assert minted["code"] not in raw  # the code itself never touches disk
    assert json.loads(raw)["codeHash"] == pairing.sha256_hex(minted["code"])
    assert stat.S_IMODE(pending_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_mint_can_pin_the_device_label_for_the_claim(tmp_path: Path):
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    store.mint(label="móvil")
    pending = json.loads((tmp_path / pairing.PENDING_FILENAME).read_text())
    assert pending["label"] == "móvil"


def test_claim_returns_the_token_once_and_destroys_the_pending(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path, now=clock)
    minted = store.mint()
    status, outcome = store.claim(minted["code"], "phone")
    assert status == "ok"
    assert outcome["label"] == "phone"
    token = outcome["token"]
    assert token and pairing.find_by_token(store.registry(), token)["label"] == "phone"
    # Only the hash reached disk.
    assert token not in (tmp_path / pairing.DEVICES_FILENAME).read_text()
    assert stat.S_IMODE((tmp_path / pairing.DEVICES_FILENAME).stat().st_mode) == 0o600
    # The pending is gone: a second claim of the same code fails.
    assert not (tmp_path / pairing.PENDING_FILENAME).exists()
    assert store.claim(minted["code"], "phone") == ("fail", "no-pending")


def test_claim_uses_the_pinned_label_over_the_body_label(tmp_path: Path):
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    minted = store.mint(label="móvil")
    status, outcome = store.claim(minted["code"], "whatever-the-phone-typed")
    assert status == "ok"
    assert outcome["label"] == "móvil"


def test_claim_failure_reasons_map_to_the_pending_verdict(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path, now=clock)

    assert store.claim("AB23CD45", "phone") == ("fail", "no-pending")

    minted = store.mint()
    assert store.claim("WRONGWRONG", "phone") == ("fail", "bad-code")
    assert store.claim("WRONGWRONG", "phone") == ("fail", "bad-code")
    assert store.claim("WRONGWRONG", "phone") == ("fail", "bad-code")
    assert store.claim("WRONGWRONG", "phone") == ("fail", "bad-code")
    assert store.claim("WRONGWRONG", "phone") == ("fail", "exhausted")
    assert not (tmp_path / pairing.PENDING_FILENAME).exists()

    minted = store.mint()
    clock.advance(pairing.CODE_TTL_S + 1)
    assert store.claim(minted["code"], "phone") == ("fail", "expired")


def test_claim_duplicate_label_keeps_the_pending_alive(tmp_path: Path):
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    first = store.mint()
    store.claim(first["code"], "phone")
    second = store.mint()
    assert store.claim(second["code"], "phone") == ("fail", "duplicate-label")
    assert (tmp_path / pairing.PENDING_FILENAME).exists()  # retry with a new name


def test_enforced_is_exactly_a_non_empty_registry(tmp_path: Path):
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    assert store.enforced() is False  # no registry file at all
    minted = store.mint()
    assert store.enforced() is False  # a pending pairing arms nothing
    _, outcome = store.claim(minted["code"], "phone")
    assert store.enforced() is True
    assert store.revoke("phone") is True
    assert store.enforced() is False


def test_resolve_stamps_last_seen_throttled(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path, now=clock)
    minted = store.mint()
    _, outcome = store.claim(minted["code"], "phone")
    token = outcome["token"]

    store.resolve(token)
    assert store.registry()["devices"][0]["lastSeenAt"] == clock.now  # first stamp
    clock.advance(5)
    store.resolve(token)
    assert store.registry()["devices"][0]["lastSeenAt"] == clock.now - 5  # throttled
    clock.advance(pairing.SEEN_THROTTLE_S + 1)
    store.resolve(token)
    assert store.registry()["devices"][0]["lastSeenAt"] == clock.now


def test_revocation_from_another_store_applies_without_restart(tmp_path: Path):
    """The CLI revokes from its own process; the server store must see it."""
    server = pairing.PairingStore(tmp_path, now=FakeClock())
    minted = server.mint()
    _, outcome = server.claim(minted["code"], "phone")
    token = outcome["token"]
    assert server.resolve(token) is not None

    cli = pairing.PairingStore(tmp_path, now=FakeClock(2_000))
    assert cli.revoke("phone") is True

    assert server.resolve(token) is None


def test_resolve_stamp_never_resurrects_a_revoked_device(tmp_path: Path):
    clock = FakeClock()
    server = pairing.PairingStore(tmp_path, now=clock)
    minted = server.mint()
    _, outcome = server.claim(minted["code"], "phone")
    token = outcome["token"]
    server.resolve(token)  # stamps lastSeenAt = 1000

    other = pairing.PairingStore(tmp_path, now=FakeClock(1_000))
    other.revoke("phone")  # CLI revokes while a stamp could still be pending

    clock.advance(pairing.SEEN_THROTTLE_S + 1)
    assert server.resolve(token) is None
    assert server.registry()["devices"] == []


def test_corrupt_files_degrade_to_fewer_devices_never_empty_credentials(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path, now=clock)
    (tmp_path / pairing.DEVICES_FILENAME).write_text("{not json", encoding="utf-8")
    assert store.enforced() is False
    assert store.resolve("anything") is None

    (tmp_path / pairing.DEVICES_FILENAME).write_text(
        json.dumps({"devices": [{"label": "x", "tokenHash": ""}]}), encoding="utf-8"
    )
    assert store.enforced() is False  # the incomplete entry is dropped
    (tmp_path / pairing.PENDING_FILENAME).write_text("[]", encoding="utf-8")
    assert store.claim("AB23CD45", "phone") == ("fail", "no-pending")


def test_default_state_dir_is_the_documented_one():
    assert pairing.default_state_dir() == Path.home() / ".local/state/herdr-brain"


# ── CLI (doc 01 §7, AC5): pair / devices list / devices revoke ───────────────


def run_cli(argv, monkeypatch, state_dir):
    from herdr_brain import pairing_cli

    monkeypatch.setenv("HERDR_BRAIN_STATE_DIR", str(state_dir))
    return pairing_cli.main(argv)


def test_cli_pair_prints_the_code_and_persists_only_its_hash(
    tmp_path, monkeypatch, capsys
):
    state = tmp_path / "state"
    code = run_cli(["pair"], monkeypatch, state)
    assert code == 0
    out = capsys.readouterr().out
    code_line = out.splitlines()[0]
    assert len(code_line) == pairing.CODE_LENGTH
    assert all(ch in pairing.CODE_ALPHABET for ch in code_line)
    pending_path = state / pairing.PENDING_FILENAME
    assert code_line not in pending_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(pending_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_cli_pair_supports_pinning_the_label(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    assert run_cli(["pair", "--label", "móvil"], monkeypatch, state) == 0
    pending = json.loads((state / pairing.PENDING_FILENAME).read_text(encoding="utf-8"))
    assert pending["label"] == "móvil"


def test_cli_devices_list_and_revoke_without_restart(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    # Enrol one device through the store the CLI and server share.
    store = pairing.PairingStore(state, now=lambda: 1_000.0)
    minted = store.mint()
    _, outcome = store.claim(minted["code"], "phone")

    assert run_cli(["devices", "list"], monkeypatch, state) == 0
    out = capsys.readouterr().out
    assert "phone" in out
    assert "created" in out and "last seen" in out

    # Revoke from the CLI; the already-built server store observes it.
    assert run_cli(["devices", "revoke", "phone"], monkeypatch, state) == 0
    assert "revoked" in capsys.readouterr().out
    assert store.resolve(outcome["token"]) is None

    assert run_cli(["devices", "list"], monkeypatch, state) == 0
    assert "phone" not in capsys.readouterr().out


def test_cli_devices_revoke_unknown_label_fails_with_the_paired_names(
    tmp_path, monkeypatch, capsys
):
    state = tmp_path / "state"
    store = pairing.PairingStore(state, now=lambda: 1_000.0)
    minted = store.mint()
    store.claim(minted["code"], "phone")

    # Unknown label is a plain failure (USAGE is reserved for a missing
    # argument), matching Panel's devices revoke.
    code = run_cli(["devices", "revoke", "ghost"], monkeypatch, state)
    assert code == 1
    err = capsys.readouterr().err
    assert "ghost" in err and "phone" in err


def test_cli_devices_requires_a_subverb(tmp_path, monkeypatch):
    assert run_cli(["devices"], monkeypatch, tmp_path / "state") == 2
