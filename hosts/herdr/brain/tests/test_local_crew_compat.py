"""Activating crew preserves the existing local CLI refusal contract."""

import io

import pytest

from herdr_brain.crew.roster import CrewRoster
from herdr_brain.walky import WalkyService, cli
from tests.test_walky_store_cli import Clock, FakeMux, env, ids, pane


@pytest.mark.parametrize("case", ["pair", "sender", "operator", "hops"])
def test_existing_local_refusal_text_with_crew(tmp_path, case):
    roster = CrewRoster.load(tmp_path)
    roster.init_lead("alpha", "synthetic-secret")
    roster.persist()
    mux = FakeMux([
        pane("w1:p1", "a"),
        pane("w2:p2", "b", status="working"),
    ])
    clock, factory = Clock(), ids()

    def run(args, pid="w1:p1"):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            args, env=env(pid, tmp_path), mux=mux, clock=clock,
            id_factory=factory, stdout=out, stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    if case == "pair":
        for _ in range(6):
            assert run(["send", "w2:p2#b", "hello"])[0] == 0
        result = run(["send", "w2:p2#b", "seventh"])
        expected = (
            "pair rate limit reached: 6 messages per 10 minutes "
            "between these two panes; slow down"
        )
    elif case == "sender":
        mux.panes += [pane(f"w3:p{i}", f"s{i}", status="working") for i in range(31)]
        for i in range(30):
            assert run(["send", f"w3:p{i}#s{i}", "hello"])[0] == 0
        result = run(["send", "w3:p30#s30", "31st"])
        expected = "sender rate limit reached: 30 messages per 10 minutes; wait before sending more"
    elif case == "operator":
        service = WalkyService(tmp_path, mux, clock=clock, id_factory=factory)
        message = service.operator_send("phone", "w2:p2#b", "operator question")
        result = run(["reply", message.id, "answer"], "w2:p2")
        expected = (
            "operator messages cannot be answered from a pane; "
            "the operator reads this pane's output directly"
        )
    else:
        assert run(["send", "w2:p2#b", "start"])[0] == 0
        for i, pid in enumerate(["w2:p2", "w1:p1", "w2:p2", "w1:p1"], 1):
            assert run(["reply", f"bm_{i:010d}", "answer"], pid)[0] == 0
        result = run(["reply", "bm_0000000005", "fifth"], "w2:p2")
        expected = "reply chain limit reached (4 hops); start a fresh send instead"
    assert result == (4, "", f"ERROR: {expected}\n")
