"""Every response carries the browser safety headers (improvement IMP-6).

Other websites can't show the app inside a frame to trick a click ("clickjacking"), browsers
don't guess file types, and the app's own addresses aren't passed on to other sites.
"""

import pytest

EXPECTED = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
}


def assert_safe(response):
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value, (name, response.request.url)
    policy = response.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in policy
    # Only framing is restricted: no script or style rules that could block the pages.
    assert policy.strip().rstrip(";") == "frame-ancestors 'none'"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/lookup",
        "/reports",
        "/portfolio",
        "/calendar",
        "/macro",
        "/watchlists/999",  # the app's 404 page
        "/no-such-page",  # FastAPI's own 404
        "/static/style.css",
        "/static/htmx.min.js",
    ],
)
def test_pages_static_files_and_404s_have_the_headers(client, path):
    assert_safe(client.get(path))


def test_partials_redirects_and_refusals_have_the_headers(client):
    assert_safe(
        client.get("/lookup/result", params={"ticker": "AAPL"}, headers={"HX-Request": "true"})
    )
    redirect = client.post("/watchlists", data={"name": "Tech"}, follow_redirects=False)
    assert redirect.status_code == 303
    assert_safe(redirect)
    refused = client.post(
        "/watchlists", data={"name": "Evil"}, headers={"Origin": "https://evil.example"}
    )
    assert refused.status_code == 403  # IMP-1's cross-site check
    assert_safe(refused)
