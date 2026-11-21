"""The crew /herd contract in the PWA (parity T1, S1).

The lead's /herd answers ``{"panes": [...], "peers": [{"name", "state"}]}``
when a crew exists (aggregate.merge_herds) and the plain list when solo.
The PWA used to pass that object straight into renderHerd, which accepts
only arrays — every crewed install then rendered ZERO panes on the phone
(the audit's blocker). This file reproduces the failure against the real
app and pins the corrected contract:

* local and remote panes appear (remote rows carry their host chip and
  are addresed by the QUALIFIED ``<host>:<pane>`` id),
* an unreachable peer appears explicitly instead of vanishing,
* a remote row tap SELECTS the pane like any other row: the existing
  REST routes proxy ``/view``/``/conversation``/writes to the owner, so
  remote selection keeps working end to end (no new restriction),
* a solo payload keeps rendering rows exactly as before.
"""

from __future__ import annotations

from playwright.sync_api import expect

LOCAL_PANE = {
    "pane_id": "w1:p9", "agent": "opencode", "agent_status": "working",
    "title": "OpenCode", "cwd": "/repo", "focused": True,
}
REMOTE_PANE = {
    "pane_id": "w1:p1", "agent": "claude", "agent_status": "blocked",
    "title": "Claude", "cwd": "/repo", "host": "host-b",
}
CREW_HERD = {
    "panes": [LOCAL_PANE, REMOTE_PANE],
    "peers": [
        {"name": "host-b", "state": "fresh"},
        {"name": "host-dead", "state": "unreachable"},
    ],
}
# A REMOTE pane sharing the LOCAL pane's bare id (two hosts, same id):
# the identity collision that must never let one stand in for the other.
REMOTE_TWIN = {
    "pane_id": "w1:p9", "agent": "claude", "agent_status": "blocked",
    "title": "Claude", "cwd": "/repo", "host": "host-b",
}
CREW_HERD_TWIN = {
    "panes": [LOCAL_PANE, REMOTE_TWIN],
    "peers": [{"name": "host-b", "state": "fresh"}],
}


def latest_view_ids(harness):
    return [
        r.url.split("pane_id=", 1)[1] for r in harness.requests
        if "/view?" in r.url and "pane_id=" in r.url
    ]


def wait_for_condition(page, predicate, timeout_ms=10000):
    """Python-side poll: browser-side waits can't inspect harness state."""
    for _ in range(int(timeout_ms / 500)):
        if predicate():
            return True
        page.wait_for_timeout(500)
    return False


def _load(harness):
    harness.respond(
        "/view", {"status": {"active": True, "pane_id": "w1:p9"},
                  "screen": "", "transcript": None,
                  "pending": {"detected": False, "kind": None, "excerpt": None}},
        query=("pane_id",),
    )
    # The permission box rides the same 5s beat; the boot default leaves
    # it undeclared, so every test here declares its quiet payload.
    harness.respond("/pane/permission", {"pending": None}, query=("pane_id",))
    return harness.load().page


def test_crew_herd_renders_local_remote_and_unreachable_peers(karaoke_app):
    harness = karaoke_app()
    harness.respond("/herd", CREW_HERD)
    page = _load(harness)

    local_row = page.locator('.conv-row[data-pane="w1:p9"]')
    remote_row = page.locator('.conv-row[data-pane="host-b:w1:p1"]')
    expect(local_row).to_have_count(1)
    expect(remote_row).to_have_count(1)
    expect(local_row.locator(".cr-host")).to_be_hidden()
    expect(remote_row.locator(".cr-host")).to_have_text("host-b")

    # The unreachable peer is explicit, never vanished: one informational
    # row naming it. A reachable peer needs no extra chip (its panes are
    # already visible with their host tag).
    peer_row = page.locator('[data-head="peer:host-dead"]')
    expect(peer_row).to_have_count(1)
    expect(peer_row).to_contain_text("host-dead")
    expect(page.locator('[data-head="peer:host-b"]')).to_have_count(0)

    # The local selection is untouched by remote rows.
    expect(page.locator("#pane-picker")).to_contain_text("OpenCode")


def test_remote_row_tap_selects_and_detargets_existing_routes(karaoke_app):
    """The tap reuses the ordinary selection flow: the qualified id rides
    the SAME fetches a local pane uses, which the server already proxies
    to the owning peer. Nothing is blocked and nothing is mis-addressed.
    """
    harness = karaoke_app()
    harness.respond("/herd", CREW_HERD)
    page = _load(harness)

    page.locator("#pane-picker").click()  # open the conversations sheet
    page.locator('.conv-row[data-pane="host-b:w1:p1"]').click()

    # The picker now names the REMOTE pane (selection changed, like any
    # other row tap) and the reading fetches carry the qualified id.
    expect(page.locator("#pane-picker")).to_contain_text("Claude")
    viewed = [
        r for r in harness.requests
        if "/view?" in r.url and "pane_id=host-b%3Aw1%3Ap1" in r.url
    ]
    assert viewed, "selecting a remote pane must address the proxied /view"

    # One selection, not two: no second tap happened, and no fetch ever
    # addressed the remote pane through a STRIPPED local id (that would
    # hit the wrong machine's same-named pane).
    stripped = [
        r for r in harness.requests
        if "pane_id=w1%3Ap1" in r.url or "pane_id=w1:p1" in r.url
    ]
    assert stripped == [], "the host prefix must never be stripped away"


def test_solo_herd_payload_still_renders_rows(karaoke_app):
    # A solo /herd is the plain list and NEVER carries host keys nor peer
    # rows — the pre-crew rendering byte-for-byte.
    second_local = {k: v for k, v in REMOTE_PANE.items() if k != "host"}
    harness = karaoke_app()
    harness.respond("/herd", [LOCAL_PANE, second_local])
    page = _load(harness)
    expect(page.locator('.conv-row[data-pane="w1:p9"]')).to_have_count(1)
    expect(page.locator('.conv-row[data-pane="w1:p1"]')).to_have_count(1)
    expect(page.locator(".cr-host").first).to_be_hidden()
    assert await_no_visible_host_chip(page)
    expect(page.locator('[data-head^="peer:"]')).to_have_count(0)


def test_local_selection_identity_when_bare_ids_collide(karaoke_app):
    """Selection identity (S1 regression pin): local and remote rows that
    share a bare pane id keep SEPARATE identities — the local click
    addresses the bare id, the remote click its qualified form, and the
    highlighted row always matches the fetch."""
    harness = karaoke_app()
    harness.respond("/herd", CREW_HERD_TWIN)
    page = _load(harness)

    page.locator("#pane-picker").click()  # open the conversations sheet
    page.locator('.conv-row[data-pane="host-b:w1:p9"]').click()

    assert wait_for_condition(
        page, lambda: "host-b%3Aw1%3Ap9" in "".join(latest_view_ids(harness))
    ), "the remote click must address the qualified id"
    remote_current = page.locator(
        '.conv-row[data-pane="host-b:w1:p9"][aria-current="true"]'
    )
    assert remote_current.count() == 1

    page.locator("#pane-picker").click()  # reopen the sheet
    page.locator('.conv-row[data-pane="w1:p9"]').click()
    assert wait_for_condition(
        page, lambda: latest_view_ids(harness)[-1:] == ["w1%3Ap9"]
    ), "the local click must address the bare id again"
    assert (
        page.locator('.conv-row[data-pane="w1:p9"][aria-current="true"]').count()
        == 1
    )


def test_local_disappearance_never_substitutes_the_remote_bare_twin(
    karaoke_app,
):
    """S1 blocker (verifier): when the selected LOCAL pane vanishes while
    a remote pane shares its bare id, the matcher must not accept the
    remote row as the vanished local identity. The app runs its ordinary
    fallback: the note shows and the selection moves to the REMOTE row's
    real qualified address (proxied routes), never to the bare impostor.
    """
    harness = karaoke_app()
    harness.respond("/herd", CREW_HERD_TWIN)
    page = _load(harness)
    expect(page.locator("#pane-picker")).to_contain_text("OpenCode")

    # The local pane is explicitly SELECTED (a real row tap), so the
    # held identity is the bare local id.
    page.locator("#pane-picker").click()  # open the conversations sheet
    page.locator('.conv-row[data-pane="w1:p9"]').click()

    # The local pane disappears; only the same-bare-id remote remains.
    harness.respond("/herd", {
        "panes": [REMOTE_TWIN],
        "peers": [{"name": "host-b", "state": "fresh"}],
    })
    # The change lands with the next 5s poll: wait past one full beat.
    expect(page.locator("#pane-picker")).to_contain_text("Claude", timeout=11000)
    expect(page.locator("#herd-note")).to_be_visible()
    assert wait_for_condition(
        page, lambda: "host-b%3Aw1%3Ap9" in "".join(latest_view_ids(harness))
    ), "the fallback must address the remote pane by its qualified id"


def test_local_disappearance_with_no_panes_degrades_safely(karaoke_app):
    """With NO pane left at all, selection degrades to the no-agent state
    and the unreachable peer row still renders — no retained identity, no
    crash, no stray address."""
    harness = karaoke_app()
    harness.respond("/herd", CREW_HERD_TWIN)
    page = _load(harness)
    expect(page.locator("#pane-picker")).to_contain_text("OpenCode")

    harness.respond("/herd", {
        "panes": [],
        "peers": [{"name": "host-dead", "state": "unreachable"}],
    })
    expect(page.locator("#pane-picker")).to_contain_text(
        "sin agente", timeout=11000
    )
    expect(page.locator('[data-head="peer:host-dead"]')).to_have_count(1)
    assert not harness.page_errors


def await_no_visible_host_chip(page):
    """Every host chip stays hidden when no row carries a host."""
    chips = page.locator(".cr-host")
    for i in range(chips.count()):
        assert chips.nth(i).is_hidden(), "a solo pane shows no host chip"
    return True
