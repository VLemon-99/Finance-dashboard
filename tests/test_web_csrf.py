"""Other websites can't submit the app's forms (improvement IMP-1, cross-site request forgery).

A page on another site could otherwise make the owner's browser post to
http://localhost:8000 and, say, delete a watchlist. Changes are refused when the request
says it comes from another site (Origin, or Referer when there's no Origin); the app's own
pages, HTMX requests and scripts without either header work as before.
"""

import pytest
from sqlmodel import Session, select

from app.db.models import Watchlist

EVIL = "https://evil.example"


def make_list(client, name="Tech"):
    response = client.post("/watchlists", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def list_names(engine):
    with Session(engine) as session:
        return [w.name for w in session.exec(select(Watchlist))]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": EVIL},
        {"Referer": f"{EVIL}/page"},
        {"Origin": "null"},  # sandboxed frames and local files
        {"Origin": "http://testserver.evil.example"},
        {"Origin": "http://localhost:8000"},  # not the host this request was sent to
    ],
)
def test_cross_site_delete_is_refused_and_nothing_changes(client, engine, headers):
    url = make_list(client)
    response = client.post(f"{url}/delete", headers=headers, follow_redirects=False)
    assert response.status_code == 403
    assert "another website" in response.text
    assert list_names(engine) == ["Tech"]


def test_cross_site_create_and_trade_delete_are_refused(client, engine):
    response = client.post("/watchlists", data={"name": "Evil"}, headers={"Origin": EVIL})
    assert response.status_code == 403
    assert list_names(engine) == []
    response = client.post("/portfolio/trades/1/delete", headers={"Origin": EVIL})
    assert response.status_code == 403  # refused before it even looks for the trade


def test_origin_wins_over_referer(client, engine):
    url = make_list(client)
    response = client.post(
        f"{url}/delete",
        headers={"Origin": EVIL, "Referer": "http://testserver/watchlists"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert list_names(engine) == ["Tech"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://testserver"},
        {"Referer": "http://testserver/watchlists/1"},
        {"Origin": "http://testserver", "HX-Request": "true"},  # HTMX
        {},  # scripts and command-line tools send neither
    ],
)
def test_same_site_or_no_header_works(client, engine, headers):
    url = make_list(client)
    response = client.post(f"{url}/delete", headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert list_names(engine) == []


def test_matches_whatever_host_the_app_is_served_at(client, engine):
    """Compared with the request's own host, so it keeps working once hosted (F7.3)."""
    url = make_list(client)
    headers = {"Host": "localhost:8000", "Origin": "http://localhost:8000"}
    response = client.post(f"{url}/delete", headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert list_names(engine) == []


def test_get_requests_are_never_blocked(client):
    assert client.get("/", headers={"Origin": EVIL}).status_code == 200
    assert client.get("/reports", headers={"Referer": f"{EVIL}/x"}).status_code == 200
