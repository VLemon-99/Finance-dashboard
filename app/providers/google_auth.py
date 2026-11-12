"""Sign in with Google (SPEC F7.6), using Google's OpenID Connect steps with plain httpx.

1. `start()` gives the address that sends the browser to Google, plus a one-time `state`
   (checked when Google sends the browser back, so another site can't fake the return), a
   `nonce` (must come back inside the ID token) and a PKCE `code_verifier` (proves the code
   is exchanged by whoever started the sign-in). The page keeps these three until step 2.
2. `finish()` exchanges the code Google returned for an ID token, directly with Google's
   token endpoint over HTTPS, then checks the token's issuer, audience (our client id),
   expiry, nonce and that Google has verified the email. It returns that email.

The token's signature is not checked, on purpose: it comes straight from Google over
HTTPS in answer to a request carrying our client secret, which Google's own guide says is
enough (DECISIONS 2026-11-12). So no extra library is needed.
"""

import base64
import binascii
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import EMAIL_PATTERN, MAX_EMAIL
from app.timeutils import utc_now

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (a public address)
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
# The values in .env.example: treated as "not set up".
_PLACEHOLDERS = {"your-google-client-id-here", "your-google-client-secret-here"}


class GoogleAuthError(Exception):
    """Sign-in with Google didn't work. The message is plain and safe to show."""


@dataclass(frozen=True)
class SignInStart:
    url: str  # send the browser here
    state: str
    nonce: str
    code_verifier: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token_claims(token: Any) -> dict[str, Any]:
    """The middle part of a JWT (the claims), without checking the signature."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise GoogleAuthError("Google's answer didn't contain a proper sign-in token.")
    payload = token.split(".")[1]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
    except (binascii.Error, ValueError):
        raise GoogleAuthError("Google's sign-in token couldn't be read.") from None
    if not isinstance(claims, dict):
        raise GoogleAuthError("Google's sign-in token couldn't be read.")
    return claims


class GoogleSignIn:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,  # tests only
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.client_id = client_id.strip()
        self._secret = client_secret.strip()
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = clock

    @property
    def is_set_up(self) -> bool:
        return all(v and v not in _PLACEHOLDERS for v in (self.client_id, self._secret))

    def _check_set_up(self) -> None:
        if not self.is_set_up:
            raise GoogleAuthError(
                "Sign in with Google isn't set up: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                "are missing from .env."
            )

    def start(self, redirect_uri: str) -> SignInStart:
        self._check_set_up()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)  # 86 characters (PKCE allows 43-128)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return SignInStart(f"{AUTH_URL}?{query}", state, nonce, verifier)

    def finish(self, code: str, redirect_uri: str, nonce: str, code_verifier: str) -> str:
        """Exchange Google's code and return the verified email (lower case)."""
        self._check_set_up()
        form = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self._secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(TOKEN_URL, data=form)
        except httpx.HTTPError:
            raise GoogleAuthError("Google didn't answer. Please try again.") from None
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if response.status_code != 200 or not isinstance(answer, dict):
            reason = answer.get("error") if isinstance(answer, dict) else None
            detail = f" ({reason})" if isinstance(reason, str) and reason.isascii() else ""
            raise GoogleAuthError(f"Google refused the sign-in{detail[:60]}. Please try again.")
        return self._verified_email(_token_claims(answer.get("id_token")), nonce)

    def _verified_email(self, claims: dict[str, Any], nonce: str) -> str:
        if claims.get("iss") not in ISSUERS:
            raise GoogleAuthError("The sign-in token wasn't issued by Google (wrong issuer).")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if self.client_id not in audiences:
            raise GoogleAuthError("The sign-in token is for another app (wrong audience).")
        expires = claims.get("exp")
        if not isinstance(expires, int | float) or isinstance(expires, bool):
            raise GoogleAuthError("The sign-in token has expired or has no expiry.")
        if datetime.fromtimestamp(expires, UTC) <= self._clock():
            raise GoogleAuthError("The sign-in token has expired. Please try again.")
        if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(
            claims["nonce"], nonce
        ):
            raise GoogleAuthError("The sign-in token doesn't match this sign-in (nonce).")
        if claims.get("email_verified") not in (True, "true"):
            raise GoogleAuthError("Google hasn't verified this account's email address.")
        email = claims.get("email")
        if not isinstance(email, str) or len(email) > MAX_EMAIL or not EMAIL_PATTERN.match(email):
            raise GoogleAuthError("Google didn't send a usable email address.")
        return email.strip().lower()
