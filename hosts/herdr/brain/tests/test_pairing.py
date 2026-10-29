"""Device pairing state module (doc 01 §1).

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
    # expiresAt: None is the S11 addition (the F1 shape never expires);
    # the token hashes still never reach the wire.
    assert wire == [
        {"label": "a", "createdAt": 1, "lastSeenAt": 2, "expiresAt": None, "current": False},
        {"label": "b", "createdAt": 3, "lastSeenAt": 4, "expiresAt": None, "current": True},
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


def test_mint_pins_the_normalized_label(tmp_path: Path):
    """The pin is stored exactly as the claim will read it back, so a
    padded-but-usable label pins its trimmed form."""
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    store.mint(label="  mi móvil\n")
    pending = json.loads((tmp_path / pairing.PENDING_FILENAME).read_text())
    assert pending["label"] == "mi móvil"


def test_mint_rejects_an_unusable_pinned_label(tmp_path: Path):
    """T4 B4: an unusable explicit label is an error at the source, not
    a silent fall-back to the phone's label. Nothing is written."""
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    for bad in ("", "   ", "x" * 49):
        with pytest.raises(ValueError):
            store.mint(label=bad)
    assert not (tmp_path / pairing.PENDING_FILENAME).exists()


def test_write_atomic_tightens_an_existing_loose_state_dir(tmp_path: Path):
    """T4 B5: audio_dir nests under the state dir and can create it
    first with the default umask — mkdir's mode only applies at
    creation, so the first pairing write must retighten 0775 to 0700."""
    state = tmp_path / "state"
    (state / "audio").mkdir(parents=True)
    state.chmod(0o775)

    store = pairing.PairingStore(state, now=FakeClock())
    store.mint()

    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / pairing.PENDING_FILENAME).stat().st_mode) == 0o600


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


def test_corrupt_files_fail_closed_never_authorize_anyone(tmp_path: Path):
    """AC10 (T4 B1): corruption degrades to "no device authorized", and
    the requirement itself only lifts for a WELL-FORMED empty registry
    (the file revoking the last device writes)."""
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path, now=clock)
    (tmp_path / pairing.DEVICES_FILENAME).write_text("{not json", encoding="utf-8")
    assert store.enforced() is True  # unparsable: enforced, nobody authorized
    assert store.resolve("anything") is None
    assert store.registry() == {"devices": []}

    (tmp_path / pairing.DEVICES_FILENAME).write_text("", encoding="utf-8")
    assert store.enforced() is True  # truncated to nothing: same answer

    (tmp_path / pairing.DEVICES_FILENAME).write_text(
        json.dumps({"devices": [{"label": "x", "tokenHash": ""}]}), encoding="utf-8"
    )
    assert store.enforced() is True  # every entry incomplete: same answer
    assert store.resolve("anything") is None

    (tmp_path / pairing.PENDING_FILENAME).write_text("[]", encoding="utf-8")
    assert store.claim("AB23CD45", "phone") == ("fail", "no-pending")

    # The one corrupt shape that lifts the requirement: none. Only the
    # well-formed zero-device registry (post-revoke) does.
    (tmp_path / pairing.DEVICES_FILENAME).write_text(
        json.dumps({"devices": []}), encoding="utf-8"
    )
    assert store.enforced() is False


def test_partial_corruption_keeps_the_valid_devices_enforced(tmp_path: Path):
    """A registry with one valid and one incomplete entry degrades to
    "fewer devices" (doc 01 Riesgos): the valid credential keeps
    working, the incomplete one authorizes nobody."""
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    minted = store.mint()
    _, outcome = store.claim(minted["code"], "phone")
    token = outcome["token"]

    raw = json.dumps({"devices": [
        json.loads((tmp_path / pairing.DEVICES_FILENAME).read_text())["devices"][0],
        {"label": "incomplete"},
    ]})
    (tmp_path / pairing.DEVICES_FILENAME).write_text(raw, encoding="utf-8")

    assert store.enforced() is True
    assert store.resolve(token)["label"] == "phone"
    assert store.resolve("other-token") is None


def test_claim_recovers_a_corrupt_registry(tmp_path: Path):
    """T4 B1: `pair` must still recover a corrupt registry — the claim
    rewrites a VALID registry, so the operator is never locked out of
    their own brain by a truncated file."""
    store = pairing.PairingStore(tmp_path, now=FakeClock())
    minted = store.mint(label="phone")
    (tmp_path / pairing.DEVICES_FILENAME).write_text("{", encoding="utf-8")

    status, outcome = store.claim(minted["code"], "ignored-label")
    assert status == "ok"
    assert outcome["label"] == "phone"  # the pinned label survives
    assert store.enforced() is True
    assert store.resolve(outcome["token"])["label"] == "phone"
    # The corrupt bytes are gone: the file parses again.
    assert json.loads((tmp_path / pairing.DEVICES_FILENAME).read_text()) == {
        "devices": [json.loads(json.dumps({
            "label": "phone",
            "tokenHash": pairing.sha256_hex(outcome["token"]),
            "createdAt": 1_000.0,
            "lastSeenAt": 1_000.0,
        }))]
    }


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


@pytest.mark.parametrize("label", ["", "x" * 49])
def test_cli_pair_rejects_an_invalid_explicit_label(label, tmp_path, monkeypatch, capsys):
    """T4 B4: an explicit but unusable --label exits non-zero with a
    clear stderr message and writes nothing at all — the operator asked
    for a specific revoke handle and must hear that it cannot be pinned
    (a silent replacement would leave a mystery label in `devices
    list`)."""
    state = tmp_path / "state"
    code = run_cli(["pair", "--label", label], monkeypatch, state)
    assert code != 0
    err = capsys.readouterr().err
    assert "--label" in err
    assert not state.exists()  # not even the state dir was created


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
    # argument), so a script can tell "bad label" from "no label".
    code = run_cli(["devices", "revoke", "ghost"], monkeypatch, state)
    assert code == 1
    err = capsys.readouterr().err
    assert "ghost" in err and "phone" in err


def test_cli_devices_requires_a_subverb(tmp_path, monkeypatch):
    assert run_cli(["devices"], monkeypatch, tmp_path / "state") == 2


# ── token expiry (S11): duration parsing, registry shape, resolve ─────────────


@pytest.mark.parametrize(
    "raw,seconds",
    [
        ("30d", 30 * 24 * 3600),
        ("12h", 12 * 3600),
        ("45m", 45 * 60),
        ("90s", 90.0),
        ("2w", 14 * 24 * 3600),
        ("1h30m", None),   # one value, one unit — compound input is a usage error
        ("30", None),      # a bare number is ambiguous, not silently seconds
        ("0d", None),      # a zero/negative expiry is no expiry at all
        ("-5h", None),
        ("bogus", None),
        ("100y", None),    # only s/m/h/d/w exist
        ("", None),
    ],
)
def test_parse_duration_accepts_one_value_and_unit(raw, seconds):
    assert pairing.parse_duration(raw) == seconds


def test_coerce_registry_keeps_a_numeric_expires_at_and_drops_garbage():
    kept = pairing.coerce_registry({"devices": [
        {"label": "a", "tokenHash": "h" * 64, "expiresAt": 2_000.5},
        {"label": "b", "tokenHash": "h" * 64},  # F1 shape: never expires
    ]})
    assert kept["devices"][0]["expiresAt"] == 2_000.5
    assert "expiresAt" not in kept["devices"][1]

    dropped = pairing.coerce_registry({"devices": [
        {"label": "c", "tokenHash": "h" * 64, "expiresAt": "forever"},
        {"label": "d", "tokenHash": "h" * 64, "expiresAt": True},
        {"label": "e", "tokenHash": "h" * 64, "expiresAt": 0},
    ]})
    # Fail-closed: a present-but-unusable expiresAt drops the ENTRY, it
    # never widens into "never expires" (doc 01 AC10 stance).
    assert kept["devices"][0]["label"] == "a"
    assert [d["label"] for d in dropped["devices"]] == []


def test_to_device_wire_carries_the_expiry_for_the_ui():
    wire = pairing.to_device_wire({"devices": [
        {"label": "a", "tokenHash": "x", "createdAt": 1, "lastSeenAt": 2,
         "expiresAt": 3_000.0},
        {"label": "b", "tokenHash": "y", "createdAt": 1, "lastSeenAt": 2},
    ]}, current="a")
    assert wire[0]["expiresAt"] == 3_000.0
    assert wire[1]["expiresAt"] is None


def test_claim_enrols_the_expiry_the_operator_minted(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path / "state", now=clock)
    minted = store.mint(expires_in_s=3600.0)
    assert minted["expiresInS"] == 3600.0

    clock.advance(10.0)  # claimed ten minutes after the mint
    status, outcome = store.claim(minted["code"], "phone")
    assert status == "ok"

    registry = json.loads((store.registry_path).read_text(encoding="utf-8"))
    # The deadline runs from the CLAIM (when the credential is born),
    # not from when the operator typed the command.
    assert registry["devices"][0]["expiresAt"] == 1_010.0 + 3600.0


def test_claim_without_expires_enrols_an_f1_shaped_device(tmp_path: Path):
    store = pairing.PairingStore(tmp_path / "state", now=lambda: 1_000.0)
    minted = store.mint()
    assert "expiresInS" not in minted
    store.claim(minted["code"], "phone")
    registry = json.loads((store.registry_path).read_text(encoding="utf-8"))
    assert "expiresAt" not in registry["devices"][0]


def test_mint_rejects_an_unusable_expiry(tmp_path: Path):
    store = pairing.PairingStore(tmp_path / "state")
    with pytest.raises(ValueError):
        store.mint(expires_in_s=0)
    assert not (tmp_path / "state" / pairing.PENDING_FILENAME).exists()


def test_resolve_status_separates_ok_expired_and_invalid(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path / "state", now=clock)
    minted = store.mint(expires_in_s=600.0)
    _, outcome = store.claim(minted["code"], "phone")
    token = outcome["token"]

    status, device = store.resolve_status(token)
    assert status == "ok"
    assert device["label"] == "phone"

    clock.advance(601.0)
    status, device = store.resolve_status(token)
    assert status == "expired"
    assert device["label"] == "phone"

    assert store.resolve_status("not-a-token") == ("invalid", None)
    assert store.resolve_status(None) == ("invalid", None)


def test_resolve_of_an_expired_token_stops_answering_and_stamps_nothing(tmp_path: Path):
    """The legacy ``resolve`` contract keeps its F1 shape (device or
    None) and an expired credential is simply gone from it; the stamp
    never runs for a device that is being refused."""
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path / "state", now=clock)
    minted = store.mint(expires_in_s=600.0)
    _, outcome = store.claim(minted["code"], "phone")
    token = outcome["token"]

    clock.advance(601.0)
    assert store.resolve(token) is None

    registry = json.loads((store.registry_path).read_text(encoding="utf-8"))
    assert registry["devices"][0]["lastSeenAt"] == registry["devices"][0]["createdAt"]


def test_peek_answers_without_touching_the_registry(tmp_path: Path):
    """B4: the read-only resolution the /pair bootstrap uses for audit
    attribution — no lastSeenAt stamp, no registry write (byte-identical),
    and an expired credential answers None like ``resolve`` does."""
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path / "state", now=clock)
    minted = store.mint()
    _, outcome = store.claim(minted["code"], "phone")
    token = outcome["token"]

    clock.advance(60.0)
    before = store.registry_path.read_bytes()
    device = store.peek(token)
    assert device is not None
    assert device["label"] == "phone"
    assert store.registry_path.read_bytes() == before

    assert store.peek("not-a-token") is None
    assert store.peek(None) is None

    clock.advance(600.0)  # past the claim's lifetime: no expiresAt armed…
    assert store.peek(token) is not None  # …an F1 token never runs out

    store.set_expiry("phone", 1.0)
    clock.advance(2.0)
    stable = store.registry_path.read_bytes()
    assert store.peek(token) is None  # expired answers like resolve: gone
    assert store.registry_path.read_bytes() == stable


def test_set_and_clear_expiry_write_the_registry_atomically(tmp_path: Path):
    clock = FakeClock()
    store = pairing.PairingStore(tmp_path / "state", now=clock)
    minted = store.mint()
    store.claim(minted["code"], "phone")

    assert store.set_expiry("phone", 1800.0) is True
    registry = json.loads((store.registry_path).read_text(encoding="utf-8"))
    assert registry["devices"][0]["expiresAt"] == 1_000.0 + 1800.0

    assert store.clear_expiry("phone") is True
    registry = json.loads((store.registry_path).read_text(encoding="utf-8"))
    assert "expiresAt" not in registry["devices"][0]

    assert store.set_expiry("ghost", 60.0) is False
    assert store.clear_expiry("ghost") is False

    with pytest.raises(ValueError):
        store.set_expiry("phone", -1)


# -- the /pair rate limit (S11): bounded, in-process, per client address -----


def test_rate_limit_verdict_allows_ten_attempts_per_minute():
    decide = pairing.rate_limit_retry_after
    stamps = [1_000.0 + i for i in range(9)]
    assert decide(stamps, now=1_005.0) is None            # 10th fits
    stamps.append(1_009.0)
    assert decide(stamps, now=1_010.0) is not None        # 11th refused
    # Old attempts age out of the window.
    assert decide(stamps, now=1_060.5) is None
    # The retry hint is bounded by the window and never zero.
    assert decide(stamps, now=1_010.0) == 50
    assert decide([1_000.0] * 10, now=1_030.0) == 30


def test_claim_rate_limiter_is_per_client_and_bounded_in_memory():
    clock = FakeClock()
    limiter = pairing.ClaimRateLimiter(now=clock, max_clients=8)

    for _ in range(10):
        assert limiter.check("10.0.0.1") is None
    # All ten attempts share one instant, so the wait is the full window.
    assert limiter.check("10.0.0.1") == 60

    # A different address has its own budget.
    assert limiter.check("10.0.0.2") is None

    # The window slides: after 61s the first address may try again.
    clock.advance(61.0)
    assert limiter.check("10.0.0.1") is None

    # Bounded bookkeeping: one entry per client, capped.
    for i in range(20):
        limiter.check(f"10.1.{i}.1")
    assert len(limiter.clients()) <= 8


# -- CLI (S11): --expires, set-expiry, clear-expiry, expiry in list ----------


def test_cli_pair_expires_writes_the_duration_into_the_pending(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    assert run_cli(["pair", "--expires", "30d"], monkeypatch, state) == 0
    pending = json.loads((state / pairing.PENDING_FILENAME).read_text(encoding="utf-8"))
    assert pending["expiresInS"] == 30 * 24 * 3600
    out = capsys.readouterr().out
    assert "30d" in out


@pytest.mark.parametrize("dur", ["bogus", "30", "0d"])
def test_cli_pair_expires_rejects_an_unusable_duration(dur, tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    assert run_cli(["pair", "--expires", dur], monkeypatch, state) != 0
    err = capsys.readouterr().err
    assert "--expires" in err
    assert not state.exists()


def test_cli_set_and_clear_expiry(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    store = pairing.PairingStore(state)
    minted = store.mint()
    store.claim(minted["code"], "phone")

    assert run_cli(["devices", "set-expiry", "phone", "12h"], monkeypatch, state) == 0
    out = capsys.readouterr().out
    assert "phone" in out
    # The CLI verb arms "12h from now" on its own wall clock — the
    # enrolment's fake clock (if any) is irrelevant to the verb.
    import time as time_module

    registry = json.loads((state / pairing.DEVICES_FILENAME).read_text(encoding="utf-8"))
    assert registry["devices"][0]["expiresAt"] == pytest.approx(
        time_module.time() + 12 * 3600, abs=5
    )

    assert run_cli(["devices", "list"], monkeypatch, state) == 0
    assert "caduca" in capsys.readouterr().out

    assert run_cli(["devices", "clear-expiry", "phone"], monkeypatch, state) == 0
    registry = json.loads((state / pairing.DEVICES_FILENAME).read_text(encoding="utf-8"))
    assert "expiresAt" not in registry["devices"][0]


def test_cli_set_expiry_unknown_label_lists_the_paired_names(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    store = pairing.PairingStore(state, now=lambda: 1_000.0)
    minted = store.mint()
    store.claim(minted["code"], "phone")

    assert run_cli(["devices", "set-expiry", "ghost", "1h"], monkeypatch, state) == 1
    err = capsys.readouterr().err
    assert "ghost" in err and "phone" in err
