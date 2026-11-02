"""The downloader: every problem becomes a plain SourceError (SPEC F4.11). No real network."""

import httpx
import pytest

from app.sources.base import SourceError
from app.sources.http import HttpFetcher


def fetcher(handler) -> HttpFetcher:
    return HttpFetcher("Test Agent t@mail.test", transport=httpx.MockTransport(handler))


def test_returns_the_body_and_sends_the_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, content=b"hello")

    assert fetcher(handler)("https://feeds.example.com/x") == b"hello"
    assert seen["ua"] == "Test Agent t@mail.test"


@pytest.mark.parametrize(
    ("status", "message"), [(429, "too often"), (404, "doesn't exist"), (503, "error \\(503\\)")]
)
def test_error_answers(status, message):
    with pytest.raises(SourceError, match=message):
        fetcher(lambda r: httpx.Response(status))("https://feeds.example.com/x")


def test_timeout_and_connection_errors():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SourceError, match="in time"):
        fetcher(timeout)("https://feeds.example.com/x")
    with pytest.raises(SourceError, match="ConnectError"):
        fetcher(refused)("https://feeds.example.com/x")


def test_plain_http_is_refused():
    with pytest.raises(SourceError, match="non-HTTPS"):
        fetcher(lambda r: httpx.Response(200))("http://feeds.example.com/x")
