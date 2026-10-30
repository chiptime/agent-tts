"""An accepted owner write stays audited when its relay response is lost."""

import json

from tests.test_walky_federation import fleet, sender


def test_accepted_send_lost_receipt_is_audited_on_all_hosts(fleet):
    services, _, faults, _, _, _ = fleet
    faults["gamma"] = "lost"
    message = services["beta"].send(
        sender(services["beta"]), "gamma:w1:p1#gamma-session", "synthetic audit probe"
    )
    assert message.state == "uncertain"
    assert services["gamma"]._store.get(message.id).state == "held"
    for host in ("beta", "gamma", "alpha"):
        path = services[host].gateway._state_dir / "audit.log"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert len(rows) == 1, (host, rows)
