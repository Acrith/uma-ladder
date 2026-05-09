"""OAuth2 authorization-code-flow primitives.

Provider-agnostic abstraction backing PR-K1's ``auth_identities``
table and PR-K2's ``/auth/discord/{start,callback}`` routes.

Stdlib-only by design (see auth_roadmap discussion 2026-05-09):
the security primitives required for OAuth (state CSRF, PKCE,
constant-time compare) are all single-line stdlib calls. The
flow itself is ~80 lines per provider; small enough to fully
audit in one pass and keeps the dep footprint flat for future
security-review work.

The HTTP layer is split off as ``HttpTransport`` so tests use a
``FakeHttpTransport`` and never hit the network — same pattern as
``notifications/discord.py``'s webhook transport.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ─── Errors ───────────────────────────────────────────────────────


class OAuthError(Exception):
    """Base for any OAuth-flow failure surfaceable to the user."""


class OAuthExchangeError(OAuthError):
    """Token exchange or profile fetch failed (HTTP error, malformed
    response, missing field)."""


# ─── PKCE + state helpers ────────────────────────────────────────


def generate_state() -> str:
    """One-time CSRF token bound to the user's session.

    Stored in session at /start, popped + constant-time-compared at
    /callback. 32 random bytes → ~43 chars urlsafe; well above the
    OAuth2 RFC's "sufficient entropy" recommendation.
    """
    return secrets.token_urlsafe(32)


def generate_pkce_pair() -> tuple[str, str]:
    """Returns ``(code_verifier, code_challenge)``.

    Defends against authorization-code interception: even if an
    attacker captures the redirect's ``?code=…``, they can't
    redeem it without the verifier (which never leaves our server).

    RFC 7636 method ``S256``: challenge = b64url(sha256(verifier))
    with padding stripped.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


# ─── ProviderProfile DTO ─────────────────────────────────────────


@dataclass(frozen=True)
class ProviderProfile:
    """Minimum viable identity payload from any OAuth provider.

    Provider-specific extras (Discord guild membership, Google
    hosted_domain, etc.) are intentionally absent — we don't need
    them for login. Add a side table if a feature needs them.
    """

    provider: str
    external_id: str
    external_username: str | None = None
    email: str | None = None
    avatar_url: str | None = None


# ─── HTTP transport (injectable for tests) ───────────────────────


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(ABC):
    @abstractmethod
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        ...

    @abstractmethod
    def post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        ...


class UrllibHttpTransport(HttpTransport):
    """Production transport. Stdlib urllib only."""

    _USER_AGENT = "uma-ladder (https://umaladder.moe, 1.0)"

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self._USER_AGENT, **(headers or {})},
            method="GET",
        )
        return _execute(req, timeout=timeout)

    def post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        body = urllib.parse.urlencode(data).encode("ascii")
        merged_headers = {
            "User-Agent": self._USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **(headers or {}),
        }
        req = urllib.request.Request(
            url, data=body, headers=merged_headers, method="POST"
        )
        return _execute(req, timeout=timeout)


def _execute(req: urllib.request.Request, *, timeout: float) -> HttpResponse:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(status_code=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:
        # 4xx/5xx still carry a body that may explain the error
        # (Discord token endpoint returns JSON like
        # `{"error": "invalid_grant"}`). Surface it for diagnosis.
        return HttpResponse(
            status_code=exc.code, body=exc.read() if exc.fp else b""
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OAuthExchangeError(f"network error: {exc}") from exc


@dataclass
class _ScriptedCall:
    method: str
    url_substring: str
    response: HttpResponse


class FakeHttpTransport(HttpTransport):
    """In-memory transport for tests.

    Configure with ``script(...)`` calls in the order requests will
    fire. Each call asserts the request method + a URL substring
    match, then returns the prepared ``HttpResponse``. ``calls``
    records every invocation so tests can assert on bodies/headers.
    """

    def __init__(self) -> None:
        self.calls: list[
            tuple[str, str, dict[str, str] | None, dict[str, str] | None]
        ] = []
        self._scripted: list[_ScriptedCall] = []

    def script(
        self,
        *,
        method: str,
        url_substring: str,
        status_code: int = 200,
        body: bytes | str = b"",
    ) -> None:
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        self._scripted.append(
            _ScriptedCall(
                method=method.upper(),
                url_substring=url_substring,
                response=HttpResponse(
                    status_code=status_code, body=body_bytes
                ),
            )
        )

    def _next(self, method: str, url: str) -> HttpResponse:
        if not self._scripted:
            raise AssertionError(
                f"FakeHttpTransport: unexpected {method} {url} "
                "— no scripted response remaining"
            )
        nxt = self._scripted.pop(0)
        if nxt.method != method or nxt.url_substring not in url:
            raise AssertionError(
                f"FakeHttpTransport: expected {nxt.method} *{nxt.url_substring}*, "
                f"got {method} {url}"
            )
        return nxt.response

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        self.calls.append(("GET", url, None, headers))
        return self._next("GET", url)

    def post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        self.calls.append(("POST", url, dict(data), headers))
        return self._next("POST", url)


# ─── Provider abstraction ────────────────────────────────────────


class OAuthProvider(ABC):
    """Single provider's view of the authorization-code flow.

    Implementations build the authorization URL, exchange a
    callback ``code`` for an access token, fetch the user profile,
    and return a ``ProviderProfile``. The access token is
    intentionally NOT returned — we use OAuth purely for one-time
    identity verification, never for ongoing API access. Discarding
    the token after the profile fetch limits the blast radius if
    our app DB is ever leaked.
    """

    name: str

    @abstractmethod
    def authorization_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        ...

    @abstractmethod
    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ProviderProfile:
        ...


# ─── Discord ─────────────────────────────────────────────────────


@dataclass
class DiscordOAuthProvider(OAuthProvider):
    """Discord OAuth2.

    Scope is ``identify`` only — gives us the snowflake, username,
    and avatar hash. We deliberately do not request ``email``
    today: the app doesn't store user emails (M4 reset flow uses
    admin-issued tokens), and "least scope wins" reduces the
    consent prompt's friction + the privacy footprint.

    No refresh-token plumbing: the flow returns a token, we fetch
    /users/@me once, then drop it. If a future feature needs
    ongoing Discord API access (e.g. read user's joined guilds),
    add a separate "scope upgrade" flow rather than baking it in
    here.
    """

    name: str = field(default="discord", init=False)
    AUTHORIZATION_URL: str = field(
        default="https://discord.com/oauth2/authorize", init=False
    )
    TOKEN_URL: str = field(
        default="https://discord.com/api/oauth2/token", init=False
    )
    PROFILE_URL: str = field(
        default="https://discord.com/api/users/@me", init=False
    )
    SCOPE: str = field(default="identify", init=False)

    client_id: str = ""
    client_secret: str = ""
    transport: HttpTransport = field(default_factory=UrllibHttpTransport)

    def authorization_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.AUTHORIZATION_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ProviderProfile:
        token_resp = self.transport.post_form(
            self.TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        if not 200 <= token_resp.status_code < 300:
            raise OAuthExchangeError(
                f"discord token endpoint returned {token_resp.status_code}: "
                f"{_excerpt(token_resp.body)}"
            )
        token_data = _parse_json(token_resp.body, where="discord token")
        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthExchangeError(
                "discord token response missing access_token"
            )

        profile_resp = self.transport.get(
            self.PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not 200 <= profile_resp.status_code < 300:
            raise OAuthExchangeError(
                f"discord profile endpoint returned {profile_resp.status_code}"
            )
        profile_data = _parse_json(profile_resp.body, where="discord profile")
        external_id = profile_data.get("id")
        if not external_id:
            raise OAuthExchangeError(
                "discord profile response missing id"
            )

        username = (
            profile_data.get("global_name")
            or profile_data.get("username")
            or None
        )
        avatar_hash = profile_data.get("avatar")
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{external_id}/{avatar_hash}.png"
            if avatar_hash
            else None
        )

        return ProviderProfile(
            provider=self.name,
            external_id=str(external_id),
            external_username=username,
            email=profile_data.get("email"),
            avatar_url=avatar_url,
        )


def _parse_json(body: bytes, *, where: str) -> dict[str, Any]:
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise OAuthExchangeError(
            f"{where} response was not valid JSON"
        ) from exc
    if not isinstance(data, dict):
        raise OAuthExchangeError(
            f"{where} response was not a JSON object"
        )
    return data


def _excerpt(body: bytes, limit: int = 200) -> str:
    """Trim binary body for safe inclusion in error messages —
    avoids leaking large response bodies into logs."""
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return f"<{len(body)} bytes>"
    return text if len(text) <= limit else text[:limit] + "…"
