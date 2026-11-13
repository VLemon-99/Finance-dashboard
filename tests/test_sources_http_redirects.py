"""F7.10 (story F7-S19, security review L2): downloads refuse a redirect to a non-HTTPS
address, and stop after too many redirects. The internet is faked with MockTransport."""

import httpx
import pytest

from app.sources.base import SourceError
from app.sources.http import HttpFetcher


class FakeSite:
    """Answers from a map: url -> ("redirect", location) or ("page", body)."""

    def __init__(self, pages):
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)
        kind, value = self.pages.get(url, ("missing", None))
        if kind == "redirect":
            return httpx.Response(302, headers={"location": value})
        if kind == "page":
            return httpx.Response(200, content=value)
        return httpx.Response(404)


def fetcher(site):
    return HttpFetcher("test-agent", transport=httpx.MockTransport(site))


def test_f7_10_redirect_to_http_is_refused_before_it_is_requested():
    site = FakeSite(
        {
            "https://feeds.example.com/rss": ("redirect", "http://feeds.example.com/rss"),
            "http://feeds.example.com/rss": ("page", b"should never be read"),
        }
    )
    with pytest.raises(SourceError, match="non-HTTPS"):
        fetcher(site)("https://feeds.example.com/rss")
    assert site.requested == ["https://feeds.example.com/rss"]


def test_f7_10_redirect_to_an_internal_http_address_is_refused():
    site = FakeSite(
        {"https://blog.example.com/feed": ("redirect", "http://169.254.169.254/computeMetadata")}
    )
    with pytest.raises(SourceError, match="non-HTTPS"):
        fetcher(site)("https://blog.example.com/feed")
    assert all(u.startswith("https://") for u in site.requested)


def test_f7_10_redirect_to_another_https_address_still_works():
    site = FakeSite(
        {
            "https://old.example.com/feed": ("redirect", "https://new.example.com/feed"),
            "https://new.example.com/feed": ("page", b"<rss>ok</rss>"),
        }
    )
    assert fetcher(site)("https://old.example.com/feed") == b"<rss>ok</rss>"


def test_f7_10_five_redirects_are_fine_six_are_refused():
    def chain(length):
        pages = {
            f"https://example.com/{i}": ("redirect", f"https://example.com/{i + 1}")
            for i in range(length)
        }
        pages[f"https://example.com/{length}"] = ("page", b"end")
        return FakeSite(pages)

    assert fetcher(chain(5))("https://example.com/0") == b"end"
    with pytest.raises(SourceError):
        fetcher(chain(6))("https://example.com/0")


def test_f7_10_first_address_must_still_be_https():
    site = FakeSite({})
    with pytest.raises(SourceError, match="non-HTTPS"):
        fetcher(site)("http://example.com/feed")
    assert site.requested == []
