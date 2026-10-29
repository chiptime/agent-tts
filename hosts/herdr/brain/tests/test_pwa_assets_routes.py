"""Server side of the offline/i18n PWA unit: versioned assets and /build.

The PWA polls ``GET /build`` to notice a new deploy, and every new static
module is served with the same no-cache headers and ``?v=`` cache-busting
as the older ones, so a deploy can never leave a stale dictionary behind.
"""

from __future__ import annotations

import pytest

from tests.test_cutover import client  # noqa: F401  (fixture)

NEW_ASSETS = ("i18n.js", "i18n/es.js", "i18n/en.js", "settings.js", "updates.js", "dock.js")


def test_build_reports_the_stamp_every_asset_ref_carries(client):  # noqa: F811
    build = client.get("/build").json()["build"]
    assert build
    index = client.get("/").text
    for name in NEW_ASSETS:
        assert f'src="/{name}?v={build}"' in index


@pytest.mark.parametrize("name", NEW_ASSETS)
def test_new_modules_are_served_without_caching(client, name):  # noqa: F811
    response = client.get("/" + name)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "no-cache" in response.headers.get("cache-control", "")


def test_build_is_a_read_route():
    from herdr_brain import access

    assert access.classify("GET", "/build") == "read"
