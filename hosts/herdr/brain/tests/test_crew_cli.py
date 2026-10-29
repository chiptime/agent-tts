"""The operator verbs (S7): ``crew init|join-token|join|list|remove|
promote|rotate-secret``. Two invariants tested here: a crew secret
typed on the command line is REFUSED (stdin ``-`` or ``@file`` only —
argv is world-readable in /proc), and ``init`` shows a minted secret
exactly once while only its hash lands on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from herdr_brain.crew import cli
from herdr_brain.crew.cli import main, read_secret_source
from herdr_brain.crew.roster import CREW_FILENAME, CrewRoster
from herdr_brain.pairing import sha256_hex

FIXTURES = Path(__file__).parent / "fixtures" / "crew"
PEER_PEM = (FIXTURES / "peer.pem").read_text(encoding="utf-8")
LEAD_PEM = (FIXTURES / "lead.pem").read_text(encoding="utf-8")


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def stdin(monkeypatch):
    holder = {"text": ""}

    class FakeStdin:
        def readline(self) -> str:
            return holder["text"]

    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    return holder


# ── secrets never travel as arguments ────────────────────────────────────────


def test_literal_secret_argument_is_refused() -> None:
    with pytest.raises(cli.SecretArgumentError) as exc:
        read_secret_source("super-secret-on-argv")
    message = str(exc.value)
    assert "stdin" in message and "@" in message  # the two allowed shapes


def test_dash_reads_secret_from_stdin(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", type("S", (), {"readline": lambda self: "from-stdin\n"})())
    assert read_secret_source("-") == "from-stdin"


def test_at_file_reads_secret_from_a_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "crew.secret"
    secret_file.write_text("from-file\n", encoding="utf-8")
    assert read_secret_source(f"@{secret_file}") == "from-file"


# ── init ─────────────────────────────────────────────────────────────────────


def test_init_mints_secret_prints_once_stores_hash(
    state_dir: Path, capsys
) -> None:
    code = main(["init", "--name", "host-a"], state_dir=state_dir)

    assert code == 0
    out = capsys.readouterr().out
    roster = CrewRoster.load(state_dir)
    assert roster.role == "lead" and roster.self_name == "host-a"

    secret = cli.secret_from_line(out)  # the one place it exists
    assert secret and len(secret) >= 20
    assert roster.secret_matches(secret) is True
    raw = (state_dir / CREW_FILENAME).read_text(encoding="utf-8")
    assert secret not in raw
    assert out.count(secret) == 1  # printed EXACTLY once


def test_init_with_secret_from_stdin(state_dir: Path, capsys, stdin) -> None:
    stdin["text"] = "operator-chosen-secret\n"
    code = main(["init", "--name", "host-a", "--secret", "-"], state_dir=state_dir)
    assert code == 0
    roster = CrewRoster.load(state_dir)
    assert roster.secret_matches("operator-chosen-secret")
    assert "operator-chosen-secret" not in capsys.readouterr().out


def test_init_refuses_secret_on_argv(state_dir: Path, capsys) -> None:
    code = main(["init", "--secret", "on-the-argv"], state_dir=state_dir)
    assert code == 2  # usage error
    assert "stdin" in capsys.readouterr().err


# ── join-token ───────────────────────────────────────────────────────────────


def test_join_token_prints_once_and_only_for_a_lead(
    state_dir: Path, capsys
) -> None:
    assert main(["join-token"], state_dir=state_dir) != 0  # solo: refuse

    main(["init", "--name", "host-a"], state_dir=state_dir)
    capsys.readouterr()
    assert main(["join-token"], state_dir=state_dir) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert len(token) >= 20
    # the token file carries a hash, not the token
    raw = (state_dir / "crew-join-token.json").read_text(encoding="utf-8")
    assert token not in raw


# ── join ─────────────────────────────────────────────────────────────────────


class RecordingTransport:
    """Stands in for the network: captures the enrolment POST."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def request(self, method, url, *, headers, json_body=None, timeout_s=None):
        from herdr_brain.crew.client import CrewHttpResponse

        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers), "body": json_body}
        )
        return CrewHttpResponse(200, {"member": json_body["name"]})


def test_join_presents_token_then_records_lead(state_dir: Path, stdin) -> None:
    stdin["text"] = "crew-secret-1\n"
    transport = RecordingTransport()
    code = main(
        [
            "join",
            "--name", "host-b",
            "--lead-name", "host-a",
            "--lead-url", "https://a.example:8747",
            "--lead-cert", f"@{FIXTURES / 'lead.pem'}",
            "--url", "https://b.example:8747",
            "--cert", f"@{FIXTURES / 'peer.pem'}",
            "--secret", "-",
            "--token", "the-join-token",
        ],
        state_dir=state_dir,
        transport=transport,
    )
    assert code == 0

    # the enrolment went to the LEAD over the crew surface
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/crew/v1/enrol")
    assert request["headers"]["authorization"] == "Bearer the-join-token"
    assert request["body"]["name"] == "host-b"

    roster = CrewRoster.load(state_dir)
    assert roster.role == "peer"
    assert roster.lead_name == "host-a"
    assert roster.peers["host-a"]["url"] == "https://a.example:8747"
    assert roster.secret_matches("crew-secret-1")


def test_join_refused_by_the_lead_writes_nothing(state_dir: Path, stdin) -> None:
    stdin["text"] = "crew-secret-1\n"

    class RefusedTransport(RecordingTransport):
        def request(self, method, url, **kw):
            from herdr_brain.crew.client import CrewHttpResponse

            self.requests.append({"method": method, "url": url})
            return CrewHttpResponse(401, {"detail": "unauthorized"})

    code = main(
        [
            "join", "--name", "host-b", "--lead-name", "host-a",
            "--lead-url", "https://a.example:8747",
            "--lead-cert", f"@{FIXTURES / 'lead.pem'}",
            "--url", "https://b.example:8747",
            "--cert", f"@{FIXTURES / 'peer.pem'}",
            "--secret", "-", "--token", "bad-token",
        ],
        state_dir=state_dir,
        transport=RefusedTransport(),
    )
    assert code != 0
    assert not (state_dir / CREW_FILENAME).exists()  # nothing was joined


# ── list / remove / promote / rotate ─────────────────────────────────────────


def seeded_lead(state_dir: Path) -> None:
    main(["init", "--name", "host-a"], state_dir=state_dir)
    roster = CrewRoster.load(state_dir)
    roster.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1.0)
    roster.persist()


def test_list_shows_members_and_pins(state_dir: Path, capsys) -> None:
    seeded_lead(state_dir)
    assert main(["list"], state_dir=state_dir) == 0
    out = capsys.readouterr().out
    assert "host-a" in out and "lead" in out
    assert "host-b" in out and "https://b.example:8747" in out


def test_remove_drops_a_peer(state_dir: Path, capsys) -> None:
    seeded_lead(state_dir)
    assert main(["remove", "host-b"], state_dir=state_dir) == 0
    roster = CrewRoster.load(state_dir)
    assert "host-b" not in roster.peers


def test_remove_self_leaves_the_crew(state_dir: Path) -> None:
    seeded_lead(state_dir)
    assert main(["remove", "host-a"], state_dir=state_dir) == 0
    roster = CrewRoster.load(state_dir)
    assert roster.role == "solo" and roster.peers == {}


def test_promote_on_a_lead_is_an_explicit_no_op(state_dir: Path) -> None:
    main(["init", "--name", "host-a"], state_dir=state_dir)
    assert main(["promote"], state_dir=state_dir) == 0
    roster = CrewRoster.load(state_dir)
    assert roster.role == "lead" and roster.lead_name == "host-a"


def test_promote_defaults_to_this_host(tmp_path: Path) -> None:
    peer_dir = tmp_path / "peer-state"
    roster = CrewRoster.load(peer_dir)
    roster.join_as_peer(
        "host-b", "host-a", "https://a.example:8747", LEAD_PEM, "crew-secret-1", now=1.0
    )
    roster.persist()

    assert main(["promote"], state_dir=peer_dir) == 0
    promoted = CrewRoster.load(peer_dir)
    assert promoted.role == "lead"
    assert promoted.lead_name == "host-b"  # this host took over, explicitly
    assert set(promoted.peers) == {"host-a"}  # the old lead stays a member


def test_promote_names_another_member_demoting_self(tmp_path: Path) -> None:
    lead_dir = tmp_path / "lead-state"
    roster = CrewRoster.load(lead_dir)
    roster.init_lead("host-a", "crew-secret-1", now=1.0)
    roster.record_peer("host-b", "https://b.example:8747", PEER_PEM, now=1.0)
    roster.persist()

    assert main(["promote", "host-b"], state_dir=lead_dir) == 0
    after = CrewRoster.load(lead_dir)
    assert after.role == "peer"  # demoted explicitly, nothing automatic
    assert after.lead_name == "host-b"


def test_rotate_secret_keeps_old_for_grace(state_dir: Path, stdin, capsys) -> None:
    main(["init", "--name", "host-a"], state_dir=state_dir)
    old_secret = cli.secret_from_line(capsys.readouterr().out)

    stdin["text"] = "rotated-secret-2\n"
    assert main(["rotate-secret", "--secret", "-"], state_dir=state_dir) == 0
    roster = CrewRoster.load(state_dir)
    assert roster.secret_matches("rotated-secret-2")
    assert roster.secret_matches(old_secret)  # grace window open
    raw = (state_dir / CREW_FILENAME).read_text(encoding="utf-8")
    assert "rotated-secret-2" not in raw
