"""B2-only recheck: masked dispatch intent and direct successful regressions."""
import json
from tests.test_walky_federation import fleet, sender


def audits(service):
    path = service.gateway._state_dir / "audit.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_lost_response_has_one_masked_dispatch_without_resend(fleet):
    services, _, faults, calls, _, _ = fleet
    secret = "sk-" + "A" * 48
    faults["gamma"] = "lost"
    message = services["beta"].send(
        sender(services["beta"]), "gamma:w1:p1#gamma-session", f"api_key={secret}"
    )
    assert message.state == "uncertain"
    assert services["beta"]._outbox.get(message.id).state == "uncertain"
    assert services["gamma"]._store.get(message.id).state == "held"
    for service in services.values():
        rows = audits(service)
        assert len(rows) == 1
        assert secret not in json.dumps(rows)
    relay = audits(services["alpha"])[0]
    assert relay["action"] == "msg.relay.send"
    assert relay["detail"]["phase"] == "dispatch"
    assert relay["detail"]["id"] == message.id
    assert relay["detail"]["origin"] == "beta"
    assert relay["detail"]["host"] == "gamma"
    assert "masked" in relay["detail"]["preview"]
    assert "success" not in json.dumps(relay).lower()
    sends = [row for row in calls if row[2].endswith("/send")]
    assert [(a, b) for a, b, _, _ in sends] == [("beta", "alpha"), ("alpha", "gamma")]
    for service in services.values():
        service.tick()
        service.tick()
    assert [row for row in calls if row[2].endswith("/send")] == sends
    assert services["gamma"]._store.get(message.id).state == "held"
    assert services["beta"]._outbox.get(message.id).state == "uncertain"
    assert all(len(audits(service)) == 1 for service in services.values())


def test_success_send_cancel_each_audited_once_per_host(fleet):
    services, _, _, calls, _, _ = fleet
    secret = "sk-" + "B" * 48
    message = services["beta"].send(
        sender(services["beta"]), "gamma:w1:p1#gamma-session", f"api_key={secret}"
    )
    assert message.state == "held"
    assert all(len(audits(service)) == 1 for service in services.values())
    assert services["beta"].cancel("w1:p1", message.id) == "canceled (held; never typed)"
    assert services["gamma"]._store.get(message.id).state == "canceled"
    assert services["gamma"].inbox("w1:p1") == []
    for host, service in services.items():
        rows = audits(service)
        assert len(rows) == 2
        assert secret not in json.dumps(rows)
        expected = ["msg.relay.send", "msg.relay.cancel"] if host == "alpha" else ["msg.send", "msg.cancel"]
        assert [row["action"] for row in rows] == expected
        if host == "alpha":
            assert all(row["detail"]["phase"] == "dispatch" for row in rows)
    assert len([row for row in calls if row[2].endswith("/send")]) == 2
    assert len([row for row in calls if row[2].endswith("/cancel")]) == 2
