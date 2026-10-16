"""Key pad targeting under out-of-order and delayed /view responses.

Stronger interleavings than the single race in ``test_pane_keys.py``: these
were written by an independent verifier while rechecking the fix for a pad
that could answer a pane other than the one whose permission screen was on
display. The invariant they pin is that the pad's frozen target
(``#pane-reply-keys[data-pane]``) and the painted screen
(``#view-screen``) ALWAYS agree — a late or out-of-order ``/view`` may
repaint older state, but it repaints the screen and the target together.

The verifier's fourth probe (asserting exact prose inside a PRD) is
deliberately NOT committed here: it pins wording rather than behaviour and
would break on any copy edit.
"""

from __future__ import annotations

from playwright.sync_api import expect

A, B = "w1:p2", "w1:p9"


def blocked_view(pane):
    return {
        "status": {"active": True, "pane_id": pane, "agent_status": "blocked"},
        "screen": "PERMISSION FOR " + pane,
        "transcript": None,
        "pending": {"detected": True, "kind": "permission", "excerpt": pane},
    }


def load(karaoke_app):
    """Two blocked panes, A on screen, with /view and /pane/keys held."""
    harness = karaoke_app()
    harness.respond(
        "/herd",
        [
            dict(
                pane_id=pane,
                agent="opencode",
                agent_status="blocked",
                title=pane,
                cwd="/repo",
                session_id="synthetic" + pane,
                focused=pane == A,
                last_turn=None,
            )
            for pane in (A, B)
        ],
    )
    harness.respond("/view", blocked_view(A), query=("pane_id",))
    harness.load()
    expect(harness.page.locator("#pane-keys")).to_be_visible()
    expect(harness.page.locator("#view-screen")).to_have_text("PERMISSION FOR " + A)

    held, sends = [], []
    harness.context.route("**/view*", lambda route: held.append(route))
    harness.context.route("**/pane/keys", lambda route: sends.append(route))
    return harness, held, sends


def select(page, pane):
    page.locator("#pane-picker").click()
    page.locator('.conv-row[data-pane="' + pane + '"]').click()


def aligned(page, pane):
    """The pad's target and the painted screen name the SAME pane."""
    expect(page.locator("#view-screen")).to_have_text("PERMISSION FOR " + pane)
    assert page.evaluate(
        """() => ({
            screen: document.getElementById('view-screen').textContent,
            target: document.getElementById('pane-reply-keys').getAttribute('data-pane')
        })"""
    ) == {"screen": "PERMISSION FOR " + pane, "target": pane}


def tap(page, sends, pane):
    count = len(sends)
    page.locator('#pane-keys [data-key="enter"]').click()
    expect(page.locator('#pane-keys [data-key="enter"]')).to_be_disabled()
    assert len(sends) == count + 1
    assert sends[-1].request.post_data_json == {"pane_id": pane, "keys": ["enter"]}
    return sends[-1]


def settle(page, route):
    route.fulfill(status=502, json={"detail": "synthetic completion"})
    expect(page.locator('#pane-keys [data-key="enter"]')).to_be_enabled()


def test_explicit_switch_before_and_after_the_new_view(karaoke_app):
    """Before it arrives, the tap answers the pane still on screen."""
    harness, views, sends = load(karaoke_app)

    select(harness.page, B)
    assert len(views) == 1
    aligned(harness.page, A)
    settle(harness.page, tap(harness.page, sends, A))

    views[0].fulfill(json=blocked_view(B))
    aligned(harness.page, B)
    settle(harness.page, tap(harness.page, sends, B))


def test_late_old_view_and_herd_repaint_keep_the_same_target(karaoke_app):
    """An older response landing last repaints screen AND target together."""
    harness, views, sends = load(karaoke_app)

    select(harness.page, B)
    select(harness.page, A)
    assert len(views) == 2

    # The newer A request lands first; the older B request lands after it.
    views[1].fulfill(json=blocked_view(A))
    aligned(harness.page, A)
    views[0].fulfill(json=blocked_view(B))
    aligned(harness.page, B)

    # Both callers of renderPending must keep using the painted B payload.
    select(harness.page, B)
    aligned(harness.page, B)
    select(harness.page, A)
    aligned(harness.page, B)
    settle(harness.page, tap(harness.page, sends, B))


def test_a_new_view_cannot_change_an_already_tapped_request(karaoke_app):
    """A request in flight stays bound to the pane it was tapped for."""
    harness, views, sends = load(karaoke_app)

    select(harness.page, B)
    old_send = tap(harness.page, sends, A)
    views[0].fulfill(json=blocked_view(B))
    aligned(harness.page, B)

    assert old_send.request.post_data_json == {"pane_id": A, "keys": ["enter"]}
    settle(harness.page, old_send)
    settle(harness.page, tap(harness.page, sends, B))
