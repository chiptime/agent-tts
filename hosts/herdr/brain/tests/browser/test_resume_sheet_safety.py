"""Browser proof for the new/resume sheet (parity T8, specs S4/S6/S8).

Two contracts meet in one isolated harness (route-only KaraokeHarness,
synthetic data, websockets blocked, no real brain service, no user
sessions, no remote network).

S8 — the sheet-navigation race: opening the launch sheet from the
conversation sheet closed it again a tick later. The #conv-new tap ran
closeConvSheet()'s history.back() and openNewSheet()'s pushState inside
the same gesture; the pending back() traversal fired its popstate after
the pushState and the popstate handler tore the just-opened sheet down.
The fixed cycle hands the conversation sheet's own history entry over
(replaceState, the same shape the pending sheet uses when it takes over
Ajustes) so no traversal is ever pending. These tests pin that handover
and the neighbouring paths it must not break: Escape, backdrop, Android
back, agent selection and the conversation sheet's own open/close cycle.

S4 — the resume operation: /session/resume is doubled at the browser
with the S2 receipt semantics the PWA rides. A request id the mock has
never seen launches exactly ONCE (counted) before its outcome script
runs; a repeat of that same id replays the stored success WITHOUT a
second launch. A "lost" first outcome aborts the connection AFTER
registering — the launch already happened, the response never reaches
the page — which is exactly the case a same-operation retry answers
with the SAME request id. A "held" outcome answers later, so a re-tap
while the first POST is still in flight must share its id.

Runs from hosts/herdr/brain, separate from the unit/API suites:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
        -m pytest -p no:cacheprovider tests/browser/test_resume_sheet_safety.py
"""

import json

import pytest
from playwright.sync_api import Route

ROW = {
    "session_id": "abc-123",
    "title": "Fix login",
    "cwd": "/srv/work/api",
    "last_ts": 1,
}
ROOTS = {
    "roots": [{"path": "/srv/work", "label": "Work"}],
    "recents": [],
    "harnesses": ["claude", "opencode", "codex", "pi"],
    "spaces": [
        {"workspace_id": "w1", "label": "main", "cwd": "/srv/work", "inside_roots": True},
        {"workspace_id": "w2", "label": "lab", "cwd": "/srv/elsewhere", "inside_roots": False},
    ],
}
RECENT = {
    "harnesses": ["claude", "opencode", "codex", "pi"],
    "sessions": [ROW],
}
RESUME_FAILED_ES = "No se pudo reanudar la sesión — prueba otra vez"
RESUMED_ES = "Sesión reanudada"


class ResumeBoundary:
    """POST /session/resume doubled at the browser.

    Registered AFTER the harness's wildcard route, so Playwright's
    newest-first matching hands every /session/resume request here while
    the fail-closed wildcard still owns everything else.
    """

    def __init__(self, harness, first_outcome: str = "ok"):
        self.page = harness.page
        self.requests: list = []
        self.launches = 0
        self.receipts: dict = {}
        self.first_outcome = first_outcome
        self._held: list = []
        harness.page.context.route("**/session/resume", self._handle)

    def _handle(self, route: Route) -> None:
        body = json.loads(route.request.post_data or "{}")
        request_id = body.get("request_id")
        self.requests.append({
            "request_id": request_id,
            "workspace_id": body.get("workspace_id"),
            "session_id": body.get("session_id"),
            "harness": body.get("harness"),
        })
        if request_id in self.receipts:
            # Replay: the SAME id answered from its receipt, NO second launch.
            route.fulfill(status=200, json=self.receipts[request_id])
            return
        self.launches += 1
        self.receipts[request_id] = {
            "ok": True,
            "result": (
                f"Panel reanudado: tab=w2:t9 pane=w2:p9 {body.get('harness')} "
                f"sesión {body.get('session_id')} en /srv/work/api."
            ),
            "pane_id": "w2:p9",
        }
        outcome = self.first_outcome
        self.first_outcome = "ok"  # the script covers the FIRST launch only
        if outcome == "held":
            self._held.append(route)  # answered later via release_held()
            return
        if outcome == "lost":
            route.abort("connectionreset")  # response lost AFTER registering
            return
        route.fulfill(status=200, json=self.receipts[request_id])

    def release_held(self) -> None:
        pending, self._held = self._held, []
        for route in pending:
            body = json.loads(route.request.post_data or "{}")
            route.fulfill(status=200, json=self.receipts[body["request_id"]])


def open_conv_sheet(page) -> None:
    """Real UI path: the header picker opens the conversation sheet."""
    page.click("#pane-picker")
    page.wait_for_function(
        "() => document.getElementById('conv-sheet').classList.contains('open')"
    )


def open_new_sheet_via_conversations(page) -> None:
    """Real UI path: picker -> conversation sheet -> 'Nueva conversación'.

    The settle wait belongs to the contract under test: a pending
    history traversal resolves within a few frames, and the broken
    build closed the sheet inside exactly that window.
    """
    open_conv_sheet(page)
    page.click("#conv-new")
    page.wait_for_function(
        "() => document.getElementById('new-sheet').classList.contains('open')"
    )
    page.wait_for_timeout(400)  # let any pending traversal + popstate land


def sheet_open(page) -> bool:
    return page.evaluate(
        "() => document.getElementById('new-sheet').classList.contains('open')"
    )


def conv_open(page) -> bool:
    return page.evaluate(
        "() => document.getElementById('conv-sheet').classList.contains('open')"
    )


def focused_id(page):
    return page.evaluate(
        "() => (document.activeElement && document.activeElement.id) || null"
    )


def tap_resume_row(page) -> None:
    page.click("#new-resume-list button.ns-resume[data-session='abc-123']")


def wait_resume_row(page) -> None:
    page.wait_for_function(
        "() => !!document.querySelector("
        "\"#new-resume-list button.ns-resume[data-session='abc-123']\")"
    )


def assert_control(page) -> None:
    """The open sheet is actually interactive: pick the agent, see rows."""
    page.select_option("#new-resume-harness", "claude")
    wait_resume_row(page)


def wait_for_toast(page, text: str) -> None:
    page.wait_for_function(
        """text => {
            const el = document.getElementById('toast');
            return el && (el.textContent || '').includes(text);
        }""",
        arg=text,
    )


@pytest.fixture
def sheet_app(karaoke_app):
    """A booted harness with the sheet's endpoints answering."""
    harness = karaoke_app()  # NOT loaded yet: configure first, then load()
    harness.respond("/session/roots", ROOTS)
    harness.respond("/sessions/recent", RECENT, query=("harness",))
    harness.load()
    return harness


def test_new_sheet_from_conversations_stays_open_and_interactive(sheet_app):
    """S8: the sheet opened from the conversation sheet SURVIVES the
    pending history traversal and stays interactive."""
    harness = sheet_app
    page = harness.page

    open_conv_sheet(page)
    page.click("#conv-new")  # the tap that used to kill the sheet
    # Settle: the pending history traversal + its popstate land within a
    # few frames. The broken build closed the sheet inside exactly this
    # window; the fixed cycle has no traversal pending at all.
    page.wait_for_timeout(400)
    assert sheet_open(page), (
        "the sheet opened from 'Nueva conversación' must stay open "
        "after its opening tap settles"
    )
    # One entry, ours: the conversation sheet handed its history entry
    # over instead of leaving a popped entry racing the sheet's own.
    assert page.evaluate("() => !!window.history.state.herdrNewSheet")
    assert page.evaluate(
        "() => __karaokeHarness.fetchCalls.includes('/session/roots')"
    )
    assert_control(page)


def test_escape_backdrop_and_android_back_still_close_the_sheets(sheet_app):
    """S8 containment: every closer of the new sheet and the
    conversation sheet keeps working after the entry-handover fix, and
    the back stack never sprouts an orphan entry."""
    page = sheet_app.page

    # Escape closes the new sheet; focus returns to the picker trigger.
    open_new_sheet_via_conversations(page)
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => !document.getElementById('new-sheet').classList.contains('open')"
    )
    assert focused_id(page) == "pane-picker"
    assert not conv_open(page)  # one sheet band: never stacked

    # The conversation sheet's own cycle still closes on Escape and pops
    # its own entry.
    open_conv_sheet(page)
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => !document.getElementById('conv-sheet').classList.contains('open')"
    )
    assert focused_id(page) == "pane-picker"

    # Backdrop closes the new sheet.
    open_new_sheet_via_conversations(page)
    page.click("#new-backdrop", position={"x": 360, "y": 30})
    page.wait_for_function(
        "() => !document.getElementById('new-sheet').classList.contains('open')"
    )
    assert focused_id(page) == "pane-picker"

    # Android back closes the new sheet with ONE traversal (the sheet's
    # entry replaced the conversation entry in place: no orphan entry
    # below it) and never reopens the conversation sheet.
    open_new_sheet_via_conversations(page)
    page.evaluate("() => history.back()")
    page.wait_for_function(
        "() => !document.getElementById('new-sheet').classList.contains('open')"
    )
    assert focused_id(page) == "pane-picker"
    assert not conv_open(page)


def test_lost_resume_response_retries_on_one_id_with_one_launch(sheet_app):
    """S4: a lost response (launch already registered) is answered by
    the SAME request id on retry — ONE launch, then a visible success."""
    harness = sheet_app
    mock = ResumeBoundary(harness, first_outcome="lost")
    page = harness.page

    open_new_sheet_via_conversations(page)
    assert_control(page)
    tap_resume_row(page)
    # The response was lost AFTER the mock registered the launch.
    wait_for_toast(page, RESUME_FAILED_ES)
    assert mock.launches == 1
    assert len(mock.requests) == 1

    tap_resume_row(page)  # the SAME operation retried after the loss
    wait_for_toast(page, RESUMED_ES)
    page.wait_for_function(
        "() => !document.getElementById('new-sheet').classList.contains('open')"
    )
    assert mock.launches == 1, "the retry must replay the receipt, never launch again"
    assert len(mock.requests) == 2
    assert mock.requests[0]["request_id"] == mock.requests[1]["request_id"]

    # A NEW explicit operation mints a FRESH id (and its own launch).
    open_new_sheet_via_conversations(page)
    assert sheet_open(page)
    wait_resume_row(page)  # the remembered agent selection re-renders
    tap_resume_row(page)
    wait_for_toast(page, RESUMED_ES)
    assert mock.launches == 2
    assert len(mock.requests) == 3
    assert mock.requests[2]["request_id"] != mock.requests[0]["request_id"]
    # Every request rode the same session and the same agent; no space
    # was picked, so the workspace stayed null (current-workspace
    # destination, exactly as before the S4 wiring).
    for entry in mock.requests:
        assert entry["session_id"] == "abc-123"
        assert entry["harness"] == "claude"
        assert entry["workspace_id"] is None


def test_inflight_retap_shares_one_id_and_one_launch(sheet_app):
    """S4: a second tap while the first resume is still unanswered is
    the SAME operation — one request id, ONE launch, visible success."""
    harness = sheet_app
    mock = ResumeBoundary(harness, first_outcome="held")
    page = harness.page

    open_new_sheet_via_conversations(page)
    assert_control(page)
    tap_resume_row(page)  # arrives at the mock and is HELD
    assert len(mock.requests) == 1
    # The first route is held: no success can have shown yet.
    toast_now = page.evaluate(
        "() => (document.getElementById('toast')||{textContent:''}).textContent || ''"
    )
    assert RESUMED_ES not in toast_now
    tap_resume_row(page)  # re-tap while the first is still in flight
    wait_for_toast(page, RESUMED_ES)
    assert len(mock.requests) == 2
    assert mock.requests[0]["request_id"] == mock.requests[1]["request_id"]
    assert mock.launches == 1
    mock.release_held()  # the first route answers too (same receipt)


def test_picked_workspace_rides_the_resume_body(sheet_app):
    """S4: the space picked in the sheet targets the resumed tab — its
    workspace_id rides the POST body (server-side validation stays)."""
    harness = sheet_app
    mock = ResumeBoundary(harness)
    page = harness.page

    open_new_sheet_via_conversations(page)
    assert_control(page)
    page.click("#new-folders button.ns-folder[data-space='w1']")
    tap_resume_row(page)
    wait_for_toast(page, RESUMED_ES)
    assert mock.launches == 1
    assert mock.requests[0]["workspace_id"] == "w1"
