"""F7.6 (story F7-S11): the Google sign-in adapter. Google is faked with httpx.MockTransport;
every token here is hand-made fake data. Tests never use the network."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import Settings
from app.providers.google_auth import (
    AUTH_URL,
    TOKEN_URL,
    GoogleAuthError,
    GoogleSignIn,
)

CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
SECRET = "fake-client-secret"  # noqa: S105 (fake test value)
REDIRECT = "https://app.example.com/login/google/callback"
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
NONCE = "fake-nonce"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def id_token(**claims) -> str:
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(NOW.timestamp()) + 600,
        "iat": int(NOW.timestamp()),
        "nonce": NONCE,
        "email": "Alice@Example.com",
        "email_verified": True,
        "sub": "1234567890",
    }
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    return f"{header}.{b64url(json.dumps(payload).encode())}.fake-signature"


class FakeGoogle:
    """Google's token endpoint: answers with `answer` (a dict, bytes or an exception)."""

    def __init__(self, answer=None, status=200):
        self.answer = answer if answer is not None else {"id_token": id_token()}
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        if isinstance(self.answer, bytes):
            return httpx.Response(self.status, content=self.answer)
        return httpx.Response(self.status, json=self.answer)


def adapter(fake=None, client_id=CLIENT_ID, secret=SECRET):
    transport = httpx.MockTransport(fake or FakeGoogle())
    return GoogleSignIn(client_id, secret, transport=transport, clock=lambda: NOW)


def finish(google, nonce=NONCE):
    return google.finish("fake-code", REDIRECT, nonce=nonce, code_verifier="fake-verifier")


# --- starting sign-in ---------------------------------------------------------------


def test_f7_6_sign_in_address_has_everything_google_needs():
    start = adapter().start(REDIRECT)
    parts = urlsplit(start.url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTH_URL
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT
    assert query["response_type"] == "code"
    assert query["scope"] == "openid email"
    assert query["state"] == start.state
    assert query["nonce"] == start.nonce
    assert query["code_challenge_method"] == "S256"
    expected = b64url(hashlib.sha256(start.code_verifier.encode()).digest())
    assert query["code_challenge"] == expected
    assert SECRET not in start.url
    assert 43 <= len(start.code_verifier) <= 128


def test_f7_6_state_nonce_and_verifier_differ_each_time():
    google = adapter()
    starts = [google.start(REDIRECT) for _ in range(5)]
    for field in ("state", "nonce", "code_verifier"):
        values = {getattr(s, field) for s in starts}
        assert len(values) == 5
        assert all(len(v) >= 32 for v in values)


# --- finishing sign-in --------------------------------------------------------------


def test_f7_6_good_answer_gives_the_verified_email_in_lower_case():
    assert finish(adapter()) == "alice@example.com"


def test_f7_6_code_exchange_goes_only_to_googles_token_endpoint():
    fake = FakeGoogle()
    finish(adapter(fake))
    (request,) = fake.requests
    assert str(request.url) == TOKEN_URL
    assert request.url.scheme == "https"
    assert request.method == "POST"
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form == {
        "code": "fake-code",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
        "code_verifier": "fake-verifier",
    }


def test_f7_6_issuer_without_https_is_accepted():
    fake = FakeGoogle({"id_token": id_token(iss="accounts.google.com")})
    assert finish(adapter(fake)) == "alice@example.com"


def test_f7_6_email_verified_as_text_true_is_accepted():
    fake = FakeGoogle({"id_token": id_token(email_verified="true")})
    assert finish(adapter(fake)) == "alice@example.com"


@pytest.mark.parametrize(
    "claims, message",
    [
        ({"iss": "https://evil.example"}, "issuer"),
        ({"aud": "someone-elses-client-id"}, "audience"),
        ({"exp": int(NOW.timestamp()) - 1}, "expired"),
        ({"exp": int(NOW.timestamp())}, "expired"),
        ({"nonce": "other-nonce"}, "nonce"),
        ({"email_verified": False}, "verified"),
        ({"email_verified": "false"}, "verified"),
        ({"email_verified": None}, "verified"),
        ({"email": None}, "email"),
        ({"email": "not-an-email"}, "email"),
    ],
)
def test_f7_6_bad_tokens_are_refused(claims, message):
    fake = FakeGoogle({"id_token": id_token(**claims)})
    with pytest.raises(GoogleAuthError, match=message) as refused:
        finish(adapter(fake))
    assert SECRET not in str(refused.value)


@pytest.mark.parametrize(
    "answer, status",
    [
        ({"error": "invalid_grant", "error_description": "Bad code"}, 400),
        ({"error": "invalid_client"}, 401),
        (b"<html>Server error</html>", 500),
        (b"not json", 200),
        ({"no_token": "here"}, 200),
        ({"id_token": "only-one-part"}, 200),
        ({"id_token": "a.%%%not-base64%%%.c"}, 200),
        ({"id_token": "a." + b64url(b"[1, 2]") + ".c"}, 200),
        ({"id_token": 12345}, 200),
        (httpx.ConnectTimeout("timed out"), 200),
        (httpx.ConnectError("no route"), 200),
    ],
)
def test_f7_6_google_errors_become_plain_messages(answer, status):
    with pytest.raises(GoogleAuthError) as refused:
        finish(adapter(FakeGoogle(answer, status)))
    message = str(refused.value)
    assert message
    assert SECRET not in message
    assert "fake-code" not in message


def test_f7_6_aud_as_a_list_must_contain_our_client_id():
    good = FakeGoogle({"id_token": id_token(aud=["other", CLIENT_ID])})
    assert finish(adapter(good)) == "alice@example.com"
    bad = FakeGoogle({"id_token": id_token(aud=["other"])})
    with pytest.raises(GoogleAuthError, match="audience"):
        finish(adapter(bad))


# --- set up or not ------------------------------------------------------------------


@pytest.mark.parametrize(
    "client_id, secret",
    [
        ("", ""),
        (CLIENT_ID, ""),
        ("", SECRET),
        ("  ", "  "),
        ("your-google-client-id-here", "your-google-client-secret-here"),
    ],
)
def test_f7_6_without_both_keys_google_sign_in_is_off(client_id, secret):
    google = adapter(client_id=client_id, secret=secret)
    assert google.is_set_up is False
    with pytest.raises(GoogleAuthError, match="isn't set up"):
        google.start(REDIRECT)
    with pytest.raises(GoogleAuthError, match="isn't set up"):
        finish(google)


def test_f7_6_with_both_keys_google_sign_in_is_on():
    assert adapter().is_set_up is True


def test_f7_6_settings_have_the_google_keys_empty_by_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    settings = Settings(_env_file=None)
    assert settings.google_client_id == ""
    assert settings.google_client_secret == ""


def test_f7_6_env_example_has_placeholders_only():
    from app.config import PROJECT_ROOT

    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GOOGLE_CLIENT_ID=your-google-client-id-here" in text
    assert "GOOGLE_CLIENT_SECRET=your-google-client-secret-here" in text
