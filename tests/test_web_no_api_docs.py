"""F7.10 (story F7-S20, security review L3): FastAPI's automatic API description pages are
off, so the app doesn't publish a map of all its addresses."""

import pytest


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_f7_10_api_description_pages_are_off(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "swagger" not in response.text.lower()
    assert "openapi" not in response.text.lower()


def test_f7_10_pages_still_work(client):
    for path in ("/", "/portfolio", "/reports", "/login"):
        assert client.get(path).status_code == 200, path
